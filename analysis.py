"""Statistical analysis over fragments and document attributes: network
(code co-occurrence), correspondence analysis (CA), and multiple
correspondence analysis (MCA).

No tkinter dependency. Functions take a ProjectStore and plain field
names, return pandas/networkx objects; the UI layer (ui/analysis_tab.py)
is responsible for embedding matplotlib figures and displaying errors.
"""
from __future__ import annotations

import itertools

import networkx as nx
import numpy as np
import pandas as pd
import prince
from scipy import stats as scipy_stats

from .storage import ProjectStore


class AnalysisError(Exception):
    """Raised for conditions the UI should show as a message, not a traceback."""


def available_fields(store: ProjectStore) -> list[str]:
    """Fields a user can pick from: one per codebook level (the fragment's
    label on that level), plus every document attribute defined in the
    project's schema. A document attribute named like a level is hidden
    behind the level."""
    level_names = [level["name"] for level, _ in store.ordered_levels()]
    schema = store.doc_attributes.get("schema", [])
    return level_names + [entry["name"] for entry in schema if entry["name"] not in level_names]


def field_values(store: ProjectStore, field: str) -> list[str]:
    """The values a field takes in the project, sorted: label names for a
    codebook level, distinct non-empty values for a document attribute."""
    level = store.level_by_name(field)
    if level is not None:
        values = {c["name"] for c in store.labels_of_level(level["id"])}
    else:
        values = {str(store.attribute_value(d, field)) for d in store.documents} - {"", "None"}
    return sorted(values, key=str.lower)


def filter_fragments(store: ProjectStore, filters: dict[str, list[str]]) -> list[dict]:
    """The fragments matching every filter (field -> accepted values): a
    fragment matches a level filter if it has a label of one of those
    names on that level, a document-attribute filter if its document has
    one of those values. No filters: every fragment."""
    if not filters:
        return list(store.fragments)
    documents = {d["id"]: d for d in store.documents}
    labels = {c["id"]: c for c in store.codes}
    names_by_fragment: dict[str, set[tuple[str, str]]] = {}  # fragment -> {(level_id, name)}
    for link in store.links:
        label = labels.get(link["code_id"])
        if label is not None:
            names_by_fragment.setdefault(link["fragment_id"], set()).add((label.get("level_id"), label["name"]))

    checks = []
    for field, values in filters.items():
        accepted = {str(v) for v in values}
        level = store.level_by_name(field)
        if level is not None:
            checks.append(lambda frag, doc, lid=level["id"], ok=accepted: any(
                (lid, name) in names_by_fragment.get(frag["id"], ()) for name in ok
            ))
        else:
            checks.append(lambda frag, doc, f=field, ok=accepted: doc is not None and str(store.attribute_value(doc, f)) in ok)
    return [
        frag for frag in store.fragments
        if all(check(frag, documents.get(frag["document_id"])) for check in checks)
    ]


def _consistent(ancestors: dict[str, list[dict]], combo: dict[str, dict]) -> bool:
    """For hierarchical levels, a row may only pair a label with its own
    ancestor -- e.g. a fragment coded "A › A1" and "B › B1" must give rows
    (A, A1) and (B, B1), never the cross pairs (A, B1) / (B, A1).
    `ancestors` maps label id -> store.label_ancestors(label)."""
    for label in combo.values():
        for ancestor in ancestors[label["id"]]:
            chosen = combo.get(ancestor.get("level_id"))
            if chosen is not None and chosen["id"] != ancestor["id"]:
                return False
    return True


def build_field_frame(store: ProjectStore, fields: list[str], fragments: list[dict] | None = None) -> pd.DataFrame:
    """One row per fragment (all of them, or just `fragments`, e.g. from
    filter_fragments) for each requested field's value, joining in the
    parent document's attributes.

    A fragment can carry more than one label on a level, so when level
    fields are requested a multi-labelled fragment is exploded into one
    row per combination of its labels on those levels -- the standard way
    to treat a multiple-response categorical variable in correspondence
    analysis, and it's also what lets two labels on the same fragment show
    up as co-occurring in the network graph. Combinations pairing a label
    with another hierarchy's ancestor are skipped (see _consistent). Rows
    missing a value for any requested field are dropped, since CA/MCA
    need complete cases -- including fragments with no label on a
    requested level.
    """
    level_ids = {level["name"]: level["id"] for level in store.levels}
    label_fields = [f for f in fields if f in level_ids]
    attr_fields = [f for f in fields if f not in level_ids]

    # Lookups built once: per-fragment scans of documents and links made
    # this quadratic in the size of the project.
    documents = {d["id"]: d for d in store.documents}
    labels = {c["id"]: c for c in store.codes}
    ancestors = {c["id"]: store.label_ancestors(c) for c in store.codes}
    labels_by_fragment: dict[str, dict[str, list[dict]]] = {}
    for link in store.links:
        label = labels.get(link["code_id"])
        if label is not None:
            by_level = labels_by_fragment.setdefault(link["fragment_id"], {})
            if label not in by_level.setdefault(label.get("level_id"), []):
                by_level[label.get("level_id")].append(label)
    code_order = {c["id"]: i for i, c in enumerate(store.codes)}  # store.labels_by_level's order
    for by_level in labels_by_fragment.values():
        for level_labels in by_level.values():
            level_labels.sort(key=lambda c: code_order[c["id"]])

    rows = []
    for frag in store.fragments if fragments is None else fragments:
        doc = documents.get(frag["document_id"])
        if doc is None:
            continue

        base = {}
        complete = True
        for field in attr_fields:
            value = store.attribute_value(doc, field)
            if value in ("", None):
                complete = False
                break
            base[field] = value
        if not complete:
            continue

        if not label_fields:
            rows.append({**base, "fragment_id": frag["id"]})
            continue

        by_level = labels_by_fragment.get(frag["id"], {})
        choices = [by_level.get(level_ids[f], []) for f in label_fields]
        if any(not c for c in choices):
            continue

        seen = set()
        for combo in itertools.product(*choices):
            by_level_id = {label["level_id"]: label for label in combo}
            if not _consistent(ancestors, by_level_id):
                continue
            names = tuple(label["name"] for label in combo)
            if names in seen:
                continue
            seen.add(names)
            row = dict(base)
            row.update(zip(label_fields, names))
            row["fragment_id"] = frag["id"]
            rows.append(row)

    return pd.DataFrame(rows, columns=fields + ["fragment_id"])


def contingency_table(df: pd.DataFrame, row_field: str, col_field: str) -> pd.DataFrame:
    return pd.crosstab(df[row_field], df[col_field])


def _ensure_min_components(coords: pd.DataFrame, n: int) -> pd.DataFrame:
    """A small/sparse contingency table can have rank below n_components
    (e.g. a 2x2 table only has 1 dimension), and prince then silently
    returns fewer coordinate columns than requested. Pad any missing
    columns with 0 so the plotting code can always assume n columns are
    present -- points just collapse onto that axis instead of crashing."""
    coords = coords.copy()
    for i in range(n):
        if i not in coords.columns:
            coords[i] = 0.0
    return coords


def run_ca(table: pd.DataFrame, n_components: int = 2) -> dict:
    if table.shape[0] < 2 or table.shape[1] < 2:
        raise AnalysisError("Selecione variáveis com pelo menos 2 categorias cada.")
    ca = prince.CA(n_components=n_components, random_state=42).fit(table)
    return {
        "row_coords": _ensure_min_components(ca.row_coordinates(table), n_components),
        "col_coords": _ensure_min_components(ca.column_coordinates(table), n_components),
        "summary": ca.eigenvalues_summary,
    }


def run_mca(df: pd.DataFrame, fields: list[str], n_components: int = 2) -> dict:
    subset = df[fields]
    if len(subset) < 2:
        raise AnalysisError("Sem dados suficientes para essas variáveis.")
    mca = prince.MCA(n_components=n_components, random_state=42).fit(subset)
    return {
        "row_coords": _ensure_min_components(mca.row_coordinates(subset), n_components),
        "col_coords": _ensure_min_components(mca.column_coordinates(subset), n_components),
        "summary": mca.eigenvalues_summary,
    }


def build_cooccurrence_graph(df: pd.DataFrame, fields: list[str]) -> nx.Graph:
    """Nodes are "field: value" pairs; an edge connects two values that
    appear together on the same fragment (e.g. two codes on a doubly
    coded fragment, or a code and the document attribute it co-occurs
    with), weighted by how often that pair co-occurs."""
    # Plain-Python grouping: pandas groupby + unique() per fragment was
    # most of the time spent here.
    nodes_by_fragment: dict[str, set[str]] = {}
    for record in df[fields + ["fragment_id"]].itertuples(index=False):
        nodes_here = nodes_by_fragment.setdefault(record[-1], set())
        for field, value in zip(fields, record[:-1]):
            nodes_here.add(f"{field}: {value}")

    graph = nx.Graph()
    for fragment_id in sorted(nodes_by_fragment):
        nodes_list = sorted(nodes_by_fragment[fragment_id])
        for node in nodes_list:
            graph.add_node(node)
        for i in range(len(nodes_list)):
            for j in range(i + 1, len(nodes_list)):
                a, b = nodes_list[i], nodes_list[j]
                if graph.has_edge(a, b):
                    graph[a][b]["weight"] += 1
                else:
                    graph.add_edge(a, b, weight=1)
    return graph


def network_summary(graph: nx.Graph) -> pd.DataFrame:
    centrality = nx.degree_centrality(graph)
    rows = [
        {"Nó": node, "Centralidade de Grau": round(value, 3), "Conexões": graph.degree(node)}
        for node, value in sorted(centrality.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return pd.DataFrame(rows)


# -- result tables ---------------------------------------------------------------
# The standard output tables of each analysis (as in SPSS / FactoMineR /
# jamovi), returned as {title: DataFrame} in display order. Values are
# kept unrounded; the UI rounds for display.

def frequency_table(df: pd.DataFrame, fields: list[str]) -> pd.DataFrame:
    """Fragments per category of each variable, and their share of the
    fragments in the analysis. Multi-coded fragments count once per
    category, so a variable's shares can add up to more than 100%."""
    total = df["fragment_id"].nunique()
    rows = []
    for field in fields:
        counts = df[["fragment_id", field]].drop_duplicates()[field].value_counts()
        for value, count in counts.items():
            rows.append({"Variável": field, "Categoria": value, "Fragmentos": int(count), "%": 100 * count / total})
    return pd.DataFrame(rows, columns=["Variável", "Categoria", "Fragmentos", "%"])


def _all_eigenvalues(matrix: np.ndarray) -> np.ndarray:
    """Every principal inertia of a correspondence analysis of `matrix`
    (a contingency or indicator table): squared singular values of the
    standardized residuals."""
    p = matrix / matrix.sum()
    r, c = p.sum(axis=1), p.sum(axis=0)
    expected = np.outer(r, c)
    residuals = (p - expected) / np.sqrt(expected)
    values = np.linalg.svd(residuals, compute_uv=False) ** 2
    return values[values > 1e-12]


def _eigenvalue_table(eigenvalues: np.ndarray) -> pd.DataFrame:
    total = eigenvalues.sum()
    return pd.DataFrame({
        "Dimensão": [f"Dim {i + 1}" for i in range(len(eigenvalues))],
        "Valor singular": np.sqrt(eigenvalues),
        "Valor próprio (inércia)": eigenvalues,
        "% inércia": 100 * eigenvalues / total,
        "% acumulada": 100 * np.cumsum(eigenvalues) / total,
    })


def _points_table(coords, contributions, cos2, masses, inertia_pct, name: str) -> pd.DataFrame:
    """Coordinates, contributions (%) and cos² on the two plotted
    dimensions, with the point's mass, inertia and quality (cos² summed
    over the plotted dimensions)."""
    table = pd.DataFrame({name: [str(i) for i in coords.index]})
    table["Massa"] = np.asarray(masses, dtype=float)
    if inertia_pct is not None:
        table["Inércia (%)"] = np.asarray(inertia_pct, dtype=float)
    for d in (0, 1):
        table[f"Coord. Dim {d + 1}"] = coords[d].to_numpy() if d in coords else 0.0
    for d in (0, 1):
        table[f"Contrib. Dim {d + 1} (%)"] = 100 * contributions[d].to_numpy() if d in contributions else 0.0
    for d in (0, 1):
        table[f"cos² Dim {d + 1}"] = cos2[d].to_numpy() if d in cos2 else 0.0
    table["Qualidade"] = table["cos² Dim 1"] + table["cos² Dim 2"]
    return table


def ca_tables(table: pd.DataFrame, n_components: int = 2) -> dict[str, pd.DataFrame]:
    observed = table.to_numpy(dtype=float)
    n = observed.sum()
    chi2, p_value, dof, expected = scipy_stats.chi2_contingency(observed, correction=False)
    rows_n, cols_n = observed.shape
    cramers_v = np.sqrt(chi2 / (n * (min(rows_n, cols_n) - 1)))
    low_expected = 100 * (expected < 5).sum() / expected.size

    with_totals = table.copy()
    with_totals["Total"] = with_totals.sum(axis=1)
    with_totals.loc["Total"] = with_totals.sum(axis=0)

    row_share, col_share = observed.sum(axis=1, keepdims=True) / n, observed.sum(axis=0, keepdims=True) / n
    adjusted = (observed - expected) / np.sqrt(expected * (1 - row_share) * (1 - col_share))

    ca = prince.CA(n_components=n_components, random_state=42).fit(table)
    p = observed / n
    r, c = p.sum(axis=1), p.sum(axis=0)
    row_inertia = r * (((p / r[:, None] - c) ** 2) / c).sum(axis=1)
    col_inertia = c * (((p / c[None, :] - r[:, None]) ** 2) / r[:, None]).sum(axis=0)
    total_inertia = chi2 / n

    test = pd.DataFrame([
        ("Qui-quadrado (χ²)", chi2),
        ("Graus de liberdade", dof),
        ("p-valor", p_value),
        ("N (observações)", n),
        ("V de Cramér", cramers_v),
        ("Inércia total (χ²/N)", total_inertia),
        ("Células com freq. esperada < 5 (%)", low_expected),
    ], columns=["Estatística", "Valor"])

    return {
        "Tabela de contingência": with_totals.reset_index(),
        "Teste qui-quadrado": test,
        "Resíduos padronizados ajustados": pd.DataFrame(adjusted, index=table.index, columns=table.columns).reset_index(),
        "Valores próprios": _eigenvalue_table(_all_eigenvalues(observed)),
        "Pontos-linha": _points_table(
            ca.row_coordinates(table), ca.row_contributions_, ca.row_cosine_similarities(table),
            r, 100 * row_inertia / total_inertia, table.index.name or "Linha",
        ),
        "Pontos-coluna": _points_table(
            ca.column_coordinates(table), ca.column_contributions_, ca.column_cosine_similarities(table),
            c, 100 * col_inertia / total_inertia, table.columns.name or "Coluna",
        ),
    }


def mca_tables(df: pd.DataFrame, fields: list[str], n_components: int = 2) -> dict[str, pd.DataFrame]:
    subset = df[fields]
    k = len(fields)
    mca = prince.MCA(n_components=n_components, random_state=42).fit(subset)

    indicator = pd.get_dummies(subset.astype(str)).to_numpy(dtype=float)
    eigenvalues = _all_eigenvalues(indicator)
    eig_table = _eigenvalue_table(eigenvalues)
    # Benzécri: only dimensions with λ > 1/K carry information beyond the
    # indicator coding; their adjusted inertias give realistic percentages.
    adjusted = np.where(eigenvalues > 1 / k, ((k / (k - 1)) * (eigenvalues - 1 / k)) ** 2, 0.0) if k > 1 else eigenvalues
    eig_table["% corrigida (Benzécri)"] = 100 * adjusted / adjusted.sum() if adjusted.sum() else 0.0

    # Discrimination measures: correlation ratio η² between each variable
    # and the fragments' scores on each dimension.
    scores = mca.row_coordinates(subset)
    discrimination = []
    for field in fields:
        row = {"Variável": field}
        for d in (0, 1):
            if d not in scores:
                row[f"η² Dim {d + 1}"] = 0.0
                continue
            s = scores[d].to_numpy()
            groups = pd.Series(s).groupby(subset[field].to_numpy())
            between = sum(len(g) * (g.mean() - s.mean()) ** 2 for _, g in groups)
            total = ((s - s.mean()) ** 2).sum()
            row[f"η² Dim {d + 1}"] = between / total if total else 0.0
        discrimination.append(row)

    coords = mca.column_coordinates(subset)
    labels = [str(i).replace("__", ": ", 1) for i in coords.index]
    counts = {f"{f}: {v}": n for f in fields for v, n in subset[f].astype(str).value_counts().items()}
    categories = _points_table(
        coords.set_axis(labels), mca.column_contributions_.set_axis(labels),
        mca.column_cosine_similarities(subset).set_axis(labels),
        [counts.get(label, 0) / (len(subset) * k) for label in labels], None, "Categoria",
    )
    categories.insert(1, "Frequência", [counts.get(label, 0) for label in labels])

    return {
        "Valores próprios": eig_table,
        "Medidas de discriminação (η²)": pd.DataFrame(discrimination),
        "Categorias": categories,
    }


def export_tables(path, tables: dict[str, pd.DataFrame], context: dict[str, object]) -> None:
    """One workbook: an "Análise" sheet describing the run (`context`:
    analysis, variables, filters, ...) and a sheet per table, values
    unrounded."""
    used: set[str] = set()

    def sheet_name(title: str) -> str:
        base = "".join("-" if ch in '[]:*?/\\' else ch for ch in title)[:31]
        name, n = base, 2
        while name.lower() in used:
            name = f"{base[:28]} {n}"
            n += 1
        used.add(name.lower())
        return name

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        info = pd.DataFrame(list(context.items()), columns=["Parâmetro", "Valor"])
        info.to_excel(writer, sheet_name=sheet_name("Análise"), index=False)
        for title, df in tables.items():
            df.to_excel(writer, sheet_name=sheet_name(title), index=False)
        for ws in writer.book.worksheets:
            for column in ws.columns:
                width = max(len(str(cell.value)) if cell.value is not None else 0 for cell in column)
                ws.column_dimensions[column[0].column_letter].width = min(max(10, width + 2), 60)


def network_tables(graph: nx.Graph, df: pd.DataFrame, fields: list[str]) -> dict[str, pd.DataFrame]:
    frequency = {
        f"{f}: {v}": n for f in fields
        for v, n in df[["fragment_id", f]].drop_duplicates()[f].value_counts().items()
    }
    degree_c = nx.degree_centrality(graph)
    betweenness = nx.betweenness_centrality(graph)
    closeness = nx.closeness_centrality(graph)
    try:
        eigenvector = nx.eigenvector_centrality_numpy(graph, weight="weight")
    except (nx.NetworkXException, ValueError, TypeError):
        eigenvector = {node: float("nan") for node in graph}

    nodes = pd.DataFrame([
        {
            "Nó": node, "Variável": node.split(": ", 1)[0], "Categoria": node.split(": ", 1)[-1],
            "Fragmentos": frequency.get(node, 0), "Grau": graph.degree(node),
            "Grau ponderado (força)": graph.degree(node, weight="weight"),
            "Centralidade de grau": degree_c[node], "Intermediação": betweenness[node],
            "Proximidade": closeness[node], "Vetor próprio": eigenvector[node],
        }
        for node in graph
    ]).sort_values(["Grau ponderado (força)", "Grau"], ascending=False)

    edges = pd.DataFrame([
        {
            "Nó A": a, "Nó B": b, "Coocorrências": w,
            # Jaccard: co-occurrences over fragments with either category.
            "Jaccard": w / (frequency.get(a, 0) + frequency.get(b, 0) - w) if frequency.get(a, 0) + frequency.get(b, 0) - w else 0.0,
        }
        for a, b, w in graph.edges(data="weight")
    ], columns=["Nó A", "Nó B", "Coocorrências", "Jaccard"]).sort_values("Coocorrências", ascending=False)

    components = list(nx.connected_components(graph))
    summary = pd.DataFrame([
        ("Nós", graph.number_of_nodes()),
        ("Arestas", graph.number_of_edges()),
        ("Densidade", nx.density(graph)),
        ("Componentes conexas", len(components)),
        ("Nós na maior componente", max((len(c) for c in components), default=0)),
        ("Grau médio", 2 * graph.number_of_edges() / graph.number_of_nodes() if graph.number_of_nodes() else 0),
        ("Força média (coocorrências por nó)", sum(w for *_, w in graph.edges(data="weight")) * 2 / graph.number_of_nodes() if graph.number_of_nodes() else 0),
        ("Coeficiente de clustering médio", nx.average_clustering(graph)),
    ], columns=["Medida", "Valor"])

    order = list(nodes["Nó"])
    matrix = nx.to_pandas_adjacency(graph, nodelist=order, weight="weight", dtype=int)

    return {
        "Resumo da rede": summary,
        "Nós (centralidade)": nodes.reset_index(drop=True),
        "Arestas (coocorrências)": edges.reset_index(drop=True),
        "Matriz de coocorrência": matrix.rename_axis("Nó").reset_index(),
    }


# -- plotting (matplotlib axes passed in by the UI layer) ---------------------

def plot_ca(ax, result: dict, row_field: str, col_field: str) -> None:
    row_coords = result["row_coords"]
    col_coords = result["col_coords"]

    ax.axhline(0, color="#999999", linewidth=0.8)
    ax.axvline(0, color="#999999", linewidth=0.8)

    ax.scatter(row_coords[0], row_coords[1], color="#4472C4", label=row_field)
    for label, x, y in zip(row_coords.index, row_coords[0], row_coords[1]):
        ax.annotate(str(label), (x, y), color="#4472C4", fontsize=8, xytext=(3, 3), textcoords="offset points")

    ax.scatter(col_coords[0], col_coords[1], color="#ED7D31", label=col_field)
    for label, x, y in zip(col_coords.index, col_coords[0], col_coords[1]):
        ax.annotate(str(label), (x, y), color="#ED7D31", fontsize=8, xytext=(3, 3), textcoords="offset points")

    ax.set_xlabel("Dimensão 1")
    ax.set_ylabel("Dimensão 2")
    ax.legend(loc="best", fontsize=8)


def plot_mca(ax, result: dict) -> None:
    """Plots category points only (not individual fragment rows) -- with
    fragments exploded per code, a per-row scatter would be both
    cluttered and harder to read than a category map."""
    col_coords = result["col_coords"]

    ax.axhline(0, color="#999999", linewidth=0.8)
    ax.axvline(0, color="#999999", linewidth=0.8)

    ax.scatter(col_coords[0], col_coords[1], color="#4472C4")
    for label, x, y in zip(col_coords.index, col_coords[0], col_coords[1]):
        ax.annotate(str(label), (x, y), fontsize=8, xytext=(3, 3), textcoords="offset points")

    ax.set_xlabel("Dimensão 1")
    ax.set_ylabel("Dimensão 2")


def plot_network(ax, graph: nx.Graph) -> None:
    pos = nx.spring_layout(graph, seed=42, weight="weight")
    weights = [graph[u][v]["weight"] for u, v in graph.edges()]
    max_weight = max(weights) if weights else 1

    nx.draw_networkx_edges(
        graph, pos, ax=ax, width=[1 + 3 * w / max_weight for w in weights], edge_color="#aaaaaa"
    )
    nx.draw_networkx_nodes(graph, pos, ax=ax, node_color="#8ecae6", node_size=600)
    nx.draw_networkx_labels(graph, pos, ax=ax, font_size=8)
    ax.axis("off")
