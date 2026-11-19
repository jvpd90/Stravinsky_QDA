"""Statistical analysis over fragments and document attributes: network
(code co-occurrence), correspondence analysis (CA), and multiple
correspondence analysis (MCA) with supplementary elements (the
maths is in correspondence.py).

No tkinter dependency. Functions take a ProjectStore and plain field
names, return pandas/networkx objects; the UI layer (ui/analysis_tab.py)
is responsible for embedding matplotlib figures and displaying errors.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import matplotlib as mpl
import networkx as nx
import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from . import correspondence as corr
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


def _eigenvalue_table(eigenvalues: np.ndarray) -> pd.DataFrame:
    total = eigenvalues.sum()
    return pd.DataFrame({
        "Dimensão": [f"Dim {i + 1}" for i in range(len(eigenvalues))],
        "Valor singular": np.sqrt(eigenvalues),
        "Valor próprio (inércia)": eigenvalues,
        "% inércia": 100 * eigenvalues / total,
        "% acumulada": 100 * np.cumsum(eigenvalues) / total,
    })


# -- correspondence analysis (CA / MCA) --------------------------------------------
# The maths lives in correspondence.py; this layer turns the project into the
# tables it needs (supplementary variables, rare categories) and back into
# result tables. Every supplementary element is attached to the rows already
# in the active analysis, so adding one never changes the axes.

OTHER_CATEGORY = "Outros"
RARE_SUPPLEMENTARY = "supplementary"
RARE_MERGE = "merge"
VTEST_LIMIT = 1.96  # |v-test| above this: significant at 5%


@dataclass
class RareCategories:
    """Categories observed in fewer than `threshold` % of the analysed rows:
    left out of the axes and projected as supplementary (default), or merged
    into one "Outros" category per variable. A threshold of 0 disables it."""
    threshold: float = 0.0
    mode: str = RARE_SUPPLEMENTARY

    def find(self, values: pd.Series) -> list[str]:
        if self.threshold <= 0 or values.empty:
            return []
        share = 100 * values.value_counts() / len(values)
        return [str(category) for category, pct in share.items() if pct < self.threshold]


def _rare_rows(field: str, values: pd.Series, rare: list[str], mode: str) -> list[dict]:
    counts = values.value_counts()
    treatment = "Suplementar (passiva)" if mode == RARE_SUPPLEMENTARY else f"Agrupada em '{OTHER_CATEGORY}'"
    return [
        {"Variável": field, "Categoria": c, "Ocorrências": int(counts[c]), "%": 100 * counts[c] / len(values),
         "Tratamento": treatment}
        for c in rare
    ]


def field_memberships(store: ProjectStore, field: str, fragments: list[dict] | None) -> pd.DataFrame:
    """(fragment_id, value) pairs for one field: one pair per label a
    fragment carries on a level, one per fragment for a document attribute."""
    df = build_field_frame(store, [field], fragments)
    return df[["fragment_id", field]].drop_duplicates().rename(columns={field: "value"}).astype({"value": str})


def _correspondence_error(error: corr.CorrespondenceError) -> AnalysisError:
    return AnalysisError(str(error))


@dataclass
class CAAnalysis:
    result: corr.CAResult
    row_field: str
    col_field: str
    df: pd.DataFrame                      # active rows (after rare-category merging)
    sup_row_kind: dict[str, str] = field(default_factory=dict)
    sup_col_kind: dict[str, str] = field(default_factory=dict)
    rare_table: pd.DataFrame | None = None


def run_ca(
    store: ProjectStore, fragments: list[dict] | None, row_field: str, col_field: str, *,
    sup_row_fields=(), sup_col_fields=(), sup_row_categories=(), sup_col_categories=(),
    rare: RareCategories = RareCategories(),
) -> CAAnalysis:
    """CA of row_field x col_field, with supplementary rows/columns made of
    (a) chosen categories of the active variables, (b) rare categories,
    (c) other variables crossed with the active column (resp. row) variable."""
    df = build_field_frame(store, [row_field, col_field], fragments)
    if df.empty:
        raise AnalysisError("Sem dados suficientes para essas variáveis.")
    df = df.astype({row_field: str, col_field: str})

    rare_rows, rare_cols = rare.find(df[row_field]), rare.find(df[col_field])
    rare_info = _rare_rows(row_field, df[row_field], rare_rows, rare.mode) + _rare_rows(col_field, df[col_field], rare_cols, rare.mode)
    if rare.mode == RARE_MERGE:
        df.loc[df[row_field].isin(rare_rows), row_field] = OTHER_CATEGORY
        df.loc[df[col_field].isin(rare_cols), col_field] = OTHER_CATEGORY
        rare_rows, rare_cols = [], []

    full = pd.crosstab(df[row_field], df[col_field])
    chosen_rows, chosen_cols = {str(c) for c in sup_row_categories}, {str(c) for c in sup_col_categories}
    sup_row_cats = [c for c in full.index if c in chosen_rows or c in rare_rows]
    sup_col_cats = [c for c in full.columns if c in chosen_cols or c in rare_cols]
    active = full.drop(index=sup_row_cats, columns=sup_col_cats)
    active_df = df[df[row_field].isin(active.index) & df[col_field].isin(active.columns)]

    row_parts, col_parts = [], []
    row_kind, col_kind = {}, {}
    if sup_row_cats:
        row_parts.append(full.loc[sup_row_cats])
        row_kind.update({c: "Categoria suplementar" if c in chosen_rows else "Rara" for c in sup_row_cats})
    if sup_col_cats:
        col_parts.append(full[sup_col_cats])
        col_kind.update({c: "Categoria suplementar" if c in chosen_cols else "Rara" for c in sup_col_cats})

    def crossed(var: str, against: str) -> pd.DataFrame | None:
        if var in (row_field, col_field):
            return None
        joined = active_df[["fragment_id", against]].merge(field_memberships(store, var, fragments), on="fragment_id")
        if joined.empty:
            return None
        table = pd.crosstab(joined["value"], joined[against])
        return table.set_axis([corr.category_label(var, v) for v in table.index], axis=0)

    for var in sup_row_fields:
        table = crossed(var, col_field)
        if table is not None:
            row_parts.append(table)
            row_kind.update({label: "Variável suplementar" for label in table.index})
    for var in sup_col_fields:
        table = crossed(var, row_field)
        if table is not None:
            col_parts.append(table.T)
            col_kind.update({label: "Variável suplementar" for label in table.index})

    try:
        result = corr.ca(
            active,
            sup_rows=pd.concat(row_parts).fillna(0) if row_parts else None,
            sup_cols=pd.concat(col_parts, axis=1).fillna(0) if col_parts else None,
        )
    except corr.CorrespondenceError as e:
        raise _correspondence_error(e) from None
    return CAAnalysis(
        result=result, row_field=row_field, col_field=col_field, df=active_df,
        sup_row_kind=row_kind, sup_col_kind=col_kind,
        rare_table=pd.DataFrame(rare_info) if rare_info else None,
    )


@dataclass
class MCAAnalysis:
    result: corr.MCAResult
    fields: list[str]
    sup_fields: list[str]
    df: pd.DataFrame                      # active rows (after rare-category merging)
    row_groups: dict[str, pd.Series]      # per-row value of every variable, for colouring
    rare_table: pd.DataFrame | None = None


MULTIPLE = "(vários)"


def run_mca(
    store: ProjectStore, fragments: list[dict] | None, fields: list[str], *,
    sup_fields=(), rare: RareCategories = RareCategories(),
) -> MCAAnalysis:
    df = build_field_frame(store, fields, fragments)
    if df.empty:
        raise AnalysisError("Sem dados suficientes para essas variáveis.")
    data = df[fields].astype(str)

    passive, rare_info = [], []
    for f in fields:
        found = rare.find(data[f])
        rare_info += _rare_rows(f, data[f], found, rare.mode)
        if rare.mode == RARE_MERGE:
            data.loc[data[f].isin(found), f] = OTHER_CATEGORY
        else:
            passive += [corr.category_label(f, c) for c in found]

    row_groups = {f: data[f] for f in fields}
    sup_blocks, sup_vars, used_sup = [], [], []
    rows = df[["fragment_id"]].reset_index()
    for var in sup_fields:
        if var in fields:
            continue
        pairs = rows.merge(field_memberships(store, var, fragments), on="fragment_id")
        if pairs.empty:
            continue
        block = pd.crosstab(pairs["index"], pairs["value"]).clip(upper=1).reindex(df.index, fill_value=0)
        labels = [corr.category_label(var, v) for v in block.columns]
        sup_blocks.append(block.set_axis(labels, axis=1).astype(float))
        sup_vars += [var] * len(labels)
        used_sup.append(var)
        per_row = pairs.groupby("index")["value"].agg(lambda v: v.iloc[0] if len(v) == 1 else MULTIPLE)
        row_groups[var] = per_row.reindex(df.index)

    sup = pd.concat(sup_blocks, axis=1) if sup_blocks else None
    try:
        result = corr.mca(
            data, passive=passive, sup=sup,
            sup_variable=pd.Series(sup_vars, index=sup.columns) if sup is not None else None,
        )
    except corr.CorrespondenceError as e:
        raise _correspondence_error(e) from None
    return MCAAnalysis(
        result=result, fields=list(fields), sup_fields=used_sup, df=df.assign(**{f: data[f] for f in fields}),
        row_groups=row_groups, rare_table=pd.DataFrame(rare_info) if rare_info else None,
    )


# -- CA / MCA result tables ------------------------------------------------------------

def _p_value(v):
    return 2 * scipy_stats.norm.sf(np.abs(v))


def _points_table(points: corr.Points, n_dims: int, name: str, kind: dict[str, str] | None = None,
                  split_variable: bool = False) -> pd.DataFrame:
    """Frequency, mass, inertia and, per dimension, coordinate, contribution,
    cos² and v-test; then quality (cos² summed over the dimensions shown)."""
    labels = [str(i) for i in points.coords.index]
    if split_variable and points.variable is not None:
        table = pd.DataFrame({
            "Variável": points.variable.to_numpy(),
            name: [l.split(corr.CATEGORY_SEP, 1)[-1] for l in labels],
        })
    else:
        table = pd.DataFrame({name: labels})
    if kind:
        table["Tipo"] = [kind.get(l, "") for l in labels]
    table["Frequência"] = points.count.to_numpy()
    if points.mass is not None:
        table["Massa"] = points.mass.to_numpy()
    if points.inertia is not None:
        table["Inércia (%)"] = 100 * points.inertia.to_numpy()
    shown = range(min(n_dims, points.coords.shape[1]))
    for d in shown:
        table[f"Coord. Dim {d + 1}"] = points.coords[d].to_numpy()
        if points.contrib is not None:
            table[f"Contrib. Dim {d + 1} (%)"] = 100 * points.contrib[d].to_numpy()
        table[f"cos² Dim {d + 1}"] = points.cos2[d].to_numpy()
        if points.vtest is not None:
            table[f"v-test Dim {d + 1}"] = points.vtest[d].to_numpy()
    if len(shown):
        table[f"Qualidade (Dim 1–{len(shown)})"] = points.cos2[list(shown)].sum(axis=1).to_numpy()
    return table


def dimension_description(sets, n_dims: int) -> pd.DataFrame:
    """For each dimension, the categories with a significant v-test
    (|v| >= 1.96), positive side first -- what characterises each axis.
    `sets`: (type, Points, variable-of-label function)."""
    rows = []
    for d in range(n_dims):
        for kind, points, variable_of in sets:
            if points is None or points.vtest is None or d not in points.vtest:
                continue
            for label, v in points.vtest[d].items():
                if np.isfinite(v) and abs(v) >= VTEST_LIMIT:
                    variable, category = variable_of(str(label))
                    rows.append({
                        "Dimensão": f"Dim {d + 1}", "Lado": "+" if v > 0 else "−", "Tipo": kind,
                        "Variável": variable, "Categoria": category,
                        "Coord.": points.coords.at[label, d], "v-test": v, "p-valor": _p_value(v), "_d": d,
                    })
    columns = ["Dimensão", "Lado", "Tipo", "Variável", "Categoria", "Coord.", "v-test", "p-valor"]
    if not rows:
        return pd.DataFrame(columns=columns)
    table = pd.DataFrame(rows).sort_values(["_d", "v-test"], ascending=[True, False])
    return table[columns].reset_index(drop=True)


def _split_label(label: str) -> tuple[str, str]:
    variable, _, category = label.partition(corr.CATEGORY_SEP)
    return (variable, category) if category else ("", label)


def ca_tables(an: CAAnalysis, n_dims: int = 3) -> dict[str, pd.DataFrame]:
    res = an.result
    table = res.table
    observed = table.to_numpy(dtype=float)
    n = observed.sum()
    chi2, p_value, dof, expected = scipy_stats.chi2_contingency(observed, correction=False)
    rows_n, cols_n = observed.shape
    cramers_v = np.sqrt(chi2 / (n * (min(rows_n, cols_n) - 1)))
    low_expected = 100 * (expected < 5).sum() / expected.size

    with_totals = table.astype(int)
    with_totals["Total"] = with_totals.sum(axis=1)
    with_totals.loc["Total"] = with_totals.sum(axis=0)
    with_totals = with_totals.rename_axis(index=an.row_field, columns=None)

    row_share, col_share = observed.sum(axis=1, keepdims=True) / n, observed.sum(axis=0, keepdims=True) / n
    adjusted = (observed - expected) / np.sqrt(expected * (1 - row_share) * (1 - col_share))

    test = pd.DataFrame([
        ("Qui-quadrado (χ²)", chi2), ("Graus de liberdade", dof), ("p-valor", p_value),
        ("N (observações)", n), ("V de Cramér", cramers_v), ("Inércia total (χ²/N)", chi2 / n),
        ("Células com freq. esperada < 5 (%)", low_expected),
    ], columns=["Estatística", "Valor"])

    dims = min(n_dims, res.n_dims)
    tables = {
        "Tabela de contingência": with_totals.reset_index(),
        "Teste qui-quadrado": test,
        "Resíduos padronizados ajustados": pd.DataFrame(adjusted, index=table.index, columns=table.columns)
            .rename_axis(index=an.row_field, columns=None).reset_index(),
        "Valores próprios": _eigenvalue_table(res.eigenvalues),
        "Pontos-linha": _points_table(res.rows, dims, an.row_field),
        "Pontos-coluna": _points_table(res.cols, dims, an.col_field),
    }
    if res.sup_rows is not None:
        tables["Linhas suplementares"] = _points_table(res.sup_rows, dims, "Linha", an.sup_row_kind)
    if res.sup_cols is not None:
        tables["Colunas suplementares"] = _points_table(res.sup_cols, dims, "Coluna", an.sup_col_kind)
    if an.rare_table is not None:
        tables["Categorias raras"] = an.rare_table

    def of(field_name):
        return lambda label: (field_name, label)

    def sup_of(field_name, kinds):
        return lambda label: _split_label(label) if kinds.get(label) == "Variável suplementar" else (field_name, label)

    tables["Descrição das dimensões"] = dimension_description([
        ("Linha", res.rows, of(an.row_field)), ("Coluna", res.cols, of(an.col_field)),
        ("Linha suplementar", res.sup_rows, sup_of(an.row_field, an.sup_row_kind)),
        ("Coluna suplementar", res.sup_cols, sup_of(an.col_field, an.sup_col_kind)),
    ], dims)
    return tables


def mca_tables(an: MCAAnalysis, n_dims: int = 3) -> dict[str, pd.DataFrame]:
    res = an.result
    dims = min(n_dims, res.n_dims)
    eig = _eigenvalue_table(res.eigenvalues)
    adjusted, greenacre = res.adjusted_inertia()
    eig["% corrigida (Benzécri)"] = 100 * adjusted / adjusted.sum() if adjusted.sum() else 0.0
    eig["% corrigida (Greenacre)"] = greenacre

    eta = pd.DataFrame({"Variável": res.eta2.index, "Tipo": res.eta2_kind.to_numpy()})
    for d in range(dims):
        eta[f"η² Dim {d + 1}"] = res.eta2[d].to_numpy()

    tables = {
        "Valores próprios": eig,
        "Medidas de discriminação (η²)": eta,
        "Categorias": _points_table(res.categories, dims, "Categoria", split_variable=True),
    }
    if res.passive is not None:
        tables["Categorias raras (passivas)"] = _points_table(res.passive, dims, "Categoria", split_variable=True)
    if res.sup is not None:
        tables["Categorias suplementares"] = _points_table(res.sup, dims, "Categoria", split_variable=True)
    if an.rare_table is not None:
        tables["Categorias raras"] = an.rare_table
    tables["Descrição das dimensões"] = dimension_description([
        ("Ativa", res.categories, _split_label), ("Rara (passiva)", res.passive, _split_label),
        ("Suplementar", res.sup, _split_label),
    ], dims)
    return tables


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
# Colours follow the app theme (ui/theme.py): blue first, then amber.
AXIS_COLOR = "#d1d5db"
SERIES_1, SERIES_1_TEXT = "#3b74d9", "#2556ad"
SERIES_2, SERIES_2_TEXT = "#e8964a", "#b0601a"
NEUTRAL, NEUTRAL_TEXT = "#9ca3af", "#4b5563"
NODE_FILL, NODE_EDGE, EDGE_COLOR = "#dbe7fb", "#3b74d9", "#cfd4dc"

LABELS_ALL, LABELS_CONTRIB, LABELS_COS2, LABELS_NONE = "all", "contrib", "cos2", "none"
SIZE_FIXED, SIZE_MASS, SIZE_CONTRIB = "fixed", "mass", "contrib"
CA_SYMMETRIC, CA_ROW_PRINCIPAL, CA_COL_PRINCIPAL = "symmetric", "rows", "cols"


@dataclass
class MapOptions:
    """What a CA/MCA factor map shows. Dimensions are 0-based."""
    dims: tuple[int, int] = (0, 1)
    ca_map: str = CA_SYMMETRIC
    show_rows: bool = True          # CA: row points
    show_cols: bool = True          # CA: column points
    show_categories: bool = True    # MCA: active (and rare) categories
    show_fragments: bool = False    # MCA: one dot per analysed row
    show_sup: bool = True           # supplementary points (CA and MCA)
    color_by: str | None = None     # MCA: variable colouring the fragments
    ellipses: bool = False          # MCA: 95% confidence ellipses per colour group
    labels: str = LABELS_ALL
    top_n: int = 10
    size: str = SIZE_FIXED


def _palette() -> list[str]:
    colors = mpl.rcParams["axes.prop_cycle"].by_key().get("color", [])
    return colors or [SERIES_1, SERIES_2]


def _xy(coords: pd.DataFrame, dims: tuple[int, int]) -> np.ndarray:
    return np.column_stack([coords[d].to_numpy() if d in coords else np.zeros(len(coords)) for d in dims])


def _plane_score(points: corr.Points, dims, eigenvalues, mode: str) -> np.ndarray:
    """Ranking used to choose which points get a label / bigger marker."""
    if mode == LABELS_CONTRIB and points.contrib is not None:
        lam = [eigenvalues[d] if d < len(eigenvalues) else 0 for d in dims]
        parts = [points.contrib[d].to_numpy() * l if d in points.contrib else 0 for d, l in zip(dims, lam)]
        return (parts[0] + parts[1]) / (sum(lam) or 1)
    return sum(points.cos2[d].to_numpy() if d in points.cos2 else 0 for d in dims)


def _label_mask(scores: np.ndarray, opts: MapOptions) -> np.ndarray:
    if opts.labels == LABELS_NONE:
        return np.zeros(len(scores), dtype=bool)
    if opts.labels == LABELS_ALL or len(scores) <= opts.top_n:
        return np.ones(len(scores), dtype=bool)
    keep = np.zeros(len(scores), dtype=bool)
    keep[np.argsort(-np.nan_to_num(scores, nan=-1))[:opts.top_n]] = True
    return keep


def _sizes(points: corr.Points, dims, eigenvalues, opts: MapOptions) -> np.ndarray | float:
    if opts.size == SIZE_MASS and points.mass is not None:
        mass = points.mass.to_numpy()
        return 15 + 285 * mass / mass.max()
    if opts.size == SIZE_CONTRIB and points.contrib is not None:
        score = _plane_score(points, dims, eigenvalues, LABELS_CONTRIB)
        return 15 + 285 * score / (score.max() or 1)
    return 40.0


def _draw(ax, xy, labels, mask, color, text_color, *, hollow=False, marker="o", size=40.0, italic=False):
    if not len(xy):
        return
    if hollow:
        ax.scatter(xy[:, 0], xy[:, 1], s=size, marker=marker, facecolors="white", edgecolors=color, linewidths=1.4, zorder=3)
    else:
        ax.scatter(xy[:, 0], xy[:, 1], s=size, marker=marker, color=color, zorder=3)
    for (x, y), label, show in zip(xy, labels, mask):
        if show:
            ax.annotate(str(label), (x, y), color=text_color, fontsize=8, xytext=(4, 3), textcoords="offset points",
                        fontstyle="italic" if italic else "normal", zorder=4)


def _frame_axes(ax, eigenvalues, dims):
    total = eigenvalues.sum()
    ax.axhline(0, color=AXIS_COLOR, linewidth=0.8, zorder=1)
    ax.axvline(0, color=AXIS_COLOR, linewidth=0.8, zorder=1)
    names = []
    for d in dims:
        pct = 100 * eigenvalues[d] / total if d < len(eigenvalues) else 0.0
        names.append(f"Dim {d + 1} ({pct:.1f}%)")
    ax.set_xlabel(names[0])
    ax.set_ylabel(names[1])


def _legend(ax, entries):
    from matplotlib.lines import Line2D
    handles = [
        Line2D([], [], linestyle="", marker=marker, markersize=7, markerfacecolor="white" if hollow else color,
               markeredgecolor=color, markeredgewidth=1.4, label=label)
        for label, color, marker, hollow in entries
    ]
    if handles:
        ax.legend(handles=handles, loc="best", fontsize=8)


def plot_ca_map(ax, an: CAAnalysis, opts: MapOptions) -> None:
    res, dims = an.result, opts.dims
    lam = res.eigenvalues
    s = np.sqrt(np.array([lam[d] if d < len(lam) else 1.0 for d in dims]))
    rows_standard = opts.ca_map == CA_COL_PRINCIPAL
    cols_standard = opts.ca_map == CA_ROW_PRINCIPAL

    def coords(points, standard):
        xy = _xy(points.coords, dims)
        return xy / s if standard else xy

    legend = []
    active_sets = []
    if opts.show_rows:
        active_sets.append((res.rows, rows_standard, SERIES_1, SERIES_1_TEXT, an.row_field))
    if opts.show_cols:
        active_sets.append((res.cols, cols_standard, SERIES_2, SERIES_2_TEXT, an.col_field))
    for points, standard, color, text, name in active_sets:
        mask = _label_mask(_plane_score(points, dims, lam, opts.labels), opts)
        _draw(ax, coords(points, standard), points.coords.index, mask, color, text,
              size=_sizes(points, dims, lam, opts))
        legend.append((name, color, "o", False))

    if opts.show_sup:
        for points, standard, color, text, shown in (
            (res.sup_rows, rows_standard, SERIES_1, SERIES_1_TEXT, opts.show_rows),
            (res.sup_cols, cols_standard, SERIES_2, SERIES_2_TEXT, opts.show_cols),
        ):
            if points is None or not shown:
                continue
            mask = _label_mask(_plane_score(points, dims, lam, LABELS_COS2), opts)
            _draw(ax, coords(points, standard), points.coords.index, mask, color, text, hollow=True, marker="^",
                  italic=True)
        if (res.sup_rows is not None and opts.show_rows) or (res.sup_cols is not None and opts.show_cols):
            legend.append(("suplementares", NEUTRAL, "^", True))

    _frame_axes(ax, lam, dims)
    _legend(ax, legend)


def _confidence_ellipse(ax, xy: np.ndarray, color: str) -> None:
    """95% confidence ellipse of the mean of a group of points."""
    from matplotlib.patches import Ellipse
    if len(xy) < 3:
        return
    cov = np.cov(xy.T) / len(xy)
    values, vectors = np.linalg.eigh(cov)
    values = np.clip(values, 0, None)
    radius = np.sqrt(scipy_stats.chi2.ppf(0.95, 2))
    angle = np.degrees(np.arctan2(vectors[1, -1], vectors[0, -1]))
    ax.add_patch(Ellipse(xy.mean(axis=0), 2 * radius * np.sqrt(values[-1]), 2 * radius * np.sqrt(values[0]),
                         angle=angle, facecolor=color, alpha=0.12, edgecolor=color, linewidth=1.2, zorder=2))


def plot_mca_map(ax, an: MCAAnalysis, opts: MapOptions) -> None:
    res, dims = an.result, opts.dims
    lam = res.eigenvalues
    palette = _palette()
    variables = an.fields + an.sup_fields
    var_color = {v: palette[i % len(palette)] for i, v in enumerate(variables)}
    legend = []

    groups = an.row_groups.get(opts.color_by) if opts.color_by else None
    if opts.show_fragments:
        xy = _xy(res.row_coords, dims)
        if groups is None:
            ax.scatter(xy[:, 0], xy[:, 1], s=8, color=NEUTRAL, alpha=0.35, linewidths=0, zorder=2)
        else:
            values = groups.fillna("").astype(str).to_numpy()
            names = [g for g in pd.unique(values) if g not in ("", MULTIPLE)]
            for i, g in enumerate(sorted(names, key=str.lower)):
                color = palette[i % len(palette)]
                pts = xy[values == g]
                ax.scatter(pts[:, 0], pts[:, 1], s=10, color=color, alpha=0.55, linewidths=0, zorder=2)
                if opts.ellipses:
                    _confidence_ellipse(ax, pts, color)
                if i < 12:
                    legend.append((f"{opts.color_by}: {g}", color, "o", False))
            other = xy[(values == "") | (values == MULTIPLE)]
            if len(other):
                ax.scatter(other[:, 0], other[:, 1], s=8, color=NEUTRAL, alpha=0.3, linewidths=0, zorder=2)

    coloured_groups = opts.show_fragments and groups is not None

    def category_color(points, i):
        return NEUTRAL_TEXT if coloured_groups else var_color.get(points.variable.iloc[i], SERIES_1)

    def draw_set(points, hollow, marker, italic, score_mode):
        if points is None:
            return
        xy = _xy(points.coords, dims)
        mask = _label_mask(_plane_score(points, dims, lam, score_mode), opts)
        sizes = _sizes(points, dims, lam, opts) if not hollow else 40.0
        for var in pd.unique(points.variable):
            idx = np.flatnonzero(points.variable.to_numpy() == var)
            color = category_color(points, idx[0])
            size = sizes[idx] if isinstance(sizes, np.ndarray) else sizes
            _draw(ax, xy[idx], points.coords.index[idx], mask[idx], color, color if coloured_groups else _darker(color),
                  hollow=hollow, marker=marker, size=size, italic=italic)

    if opts.show_categories:
        draw_set(res.categories, False, "o", False, opts.labels)
        draw_set(res.passive, True, "s", True, LABELS_COS2)
        if not coloured_groups:
            legend += [(v, var_color[v], "o", False) for v in an.fields[:10]]
        if res.passive is not None:
            legend.append(("raras (passivas)", NEUTRAL, "s", True))
    if opts.show_sup and res.sup is not None:
        draw_set(res.sup, True, "^", True, LABELS_COS2)
        if not coloured_groups:
            legend += [(f"{v} (supl.)", var_color[v], "^", True) for v in an.sup_fields[:6]]

    _frame_axes(ax, lam, dims)
    _legend(ax, legend)


def _darker(color: str, factor: float = 0.72) -> str:
    r, g, b = mpl.colors.to_rgb(color)
    return mpl.colors.to_hex((r * factor, g * factor, b * factor))


def plot_scree(ax, eigenvalues: np.ndarray, reference: float | None = None, reference_label: str = "",
               max_bars: int = 20) -> None:
    """Share of inertia per dimension, with an optional reference level
    (average eigenvalue in CA, 1/K in MCA) drawn as a dashed line."""
    total = eigenvalues.sum()
    pct = 100 * eigenvalues[:max_bars] / total
    positions = np.arange(1, len(pct) + 1)
    ax.bar(positions, pct, color=SERIES_1, width=0.65, zorder=2)
    ax.plot(positions, np.cumsum(pct), color=SERIES_2, marker="o", markersize=3, linewidth=1.2, zorder=3,
            label="% acumulada")
    for x, y in zip(positions, pct):
        ax.annotate(f"{y:.1f}", (x, y), ha="center", va="bottom", fontsize=7, color=NEUTRAL_TEXT,
                    xytext=(0, 2), textcoords="offset points")
    if reference is not None:
        ax.axhline(100 * reference / total, color=NEUTRAL_TEXT, linestyle="--", linewidth=1, zorder=3,
                   label=reference_label)
    ax.set_xticks(positions)
    ax.set_xlabel("Dimensão")
    ax.set_ylabel("% de inércia")
    ax.set_ylim(0, 105)
    ax.grid(axis="y", zorder=0)
    ax.legend(loc="center right", fontsize=8)


def plot_network(ax, graph: nx.Graph) -> None:
    pos = nx.spring_layout(graph, seed=42, weight="weight")
    weights = [graph[u][v]["weight"] for u, v in graph.edges()]
    max_weight = max(weights) if weights else 1

    nx.draw_networkx_edges(
        graph, pos, ax=ax, width=[1 + 3 * w / max_weight for w in weights], edge_color=EDGE_COLOR
    )
    nx.draw_networkx_nodes(graph, pos, ax=ax, node_color=NODE_FILL, edgecolors=NODE_EDGE, linewidths=1.2, node_size=600)
    nx.draw_networkx_labels(graph, pos, ax=ax, font_size=8)
    ax.axis("off")
