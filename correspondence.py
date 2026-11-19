"""Correspondence analysis (CA) and multiple correspondence analysis (MCA),
computed directly by singular value decomposition, with supplementary
elements.

Pure numpy/pandas, no knowledge of the project: callers pass a contingency
table (CA) or a table of categorical variables (MCA). Conventions follow
FactoMineR / Le Roux & Rouanet:

* coordinates are *principal* coordinates (scaled by the singular value);
  CA also exposes *standard* coordinates for asymmetric maps;
* supplementary points are projected with the transition formulas, so they
  take no part in building the axes;
* v-tests (valeurs-test) are approximately N(0, 1) under the hypothesis that
  a category's members are a random draw: |v| > 1.96 is significant at 5%;
* in MCA, "passive" categories of active variables are excluded from the
  cloud while keeping the row weights and centring of the full analysis
  (specific MCA) -- the standard way to stop rare categories dominating
  the axes. They are then projected like supplementary categories.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

_EPS = 1e-12
CATEGORY_SEP = ": "


class CorrespondenceError(ValueError):
    """The data can't support the analysis (too few categories, no inertia...)."""


@dataclass
class Points:
    """One set of points (rows, columns, categories...), one column per dimension."""
    coords: pd.DataFrame                 # principal coordinates
    cos2: pd.DataFrame                   # quality of representation per dimension
    count: pd.Series                     # observations behind each point
    vtest: pd.DataFrame | None = None
    mass: pd.Series | None = None        # active points only
    contrib: pd.DataFrame | None = None  # active points only (fractions, sum to 1)
    inertia: pd.Series | None = None     # active points only (share of total inertia)
    std: pd.DataFrame | None = None      # standard coordinates (CA active points)
    variable: pd.Series | None = None    # MCA: the variable each category belongs to

    def __len__(self) -> int:
        return len(self.coords)


def _frame(values: np.ndarray, index) -> pd.DataFrame:
    return pd.DataFrame(values, index=index, columns=range(values.shape[1]))


def _vtest(coords: np.ndarray, counts: np.ndarray, n: float) -> np.ndarray:
    counts = np.asarray(counts, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        factor = np.where(counts < n, np.sqrt((n - 1) * counts / (n - counts)), np.nan)
    return coords * factor[:, None]


def _safe_div(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(b > _EPS, a / np.where(b > _EPS, b, 1.0), np.nan)


def _svd(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Non-trivial singular triplets, signs fixed so the largest |loading| of
    each column-side vector is positive (results don't flip between runs)."""
    u, s, vt = np.linalg.svd(matrix, full_matrices=False)
    keep = s ** 2 > _EPS * max(1.0, float((s ** 2).sum()))
    u, s, v = u[:, keep], s[keep], vt[keep].T
    signs = np.sign(v[np.abs(v).argmax(axis=0), range(v.shape[1])])
    signs[signs == 0] = 1
    return u * signs, s, v * signs


# -- CA -------------------------------------------------------------------------

@dataclass
class CAResult:
    eigenvalues: np.ndarray
    rows: Points
    cols: Points
    sup_rows: Points | None
    sup_cols: Points | None
    total: float                  # grand total of the active table
    table: pd.DataFrame           # the active table actually analysed

    @property
    def n_dims(self) -> int:
        return len(self.eigenvalues)


def ca(table: pd.DataFrame, sup_rows: pd.DataFrame | None = None,
       sup_cols: pd.DataFrame | None = None) -> CAResult:
    """CA of a contingency table. ``sup_rows`` has the table's columns (extra
    ones are ignored, missing ones count 0); ``sup_cols`` has its rows."""
    table = table.astype(float)
    table = table.loc[table.sum(axis=1) > 0, table.sum(axis=0) > 0]
    if table.shape[0] < 2 or table.shape[1] < 2:
        raise CorrespondenceError("A tabela precisa de pelo menos 2 linhas e 2 colunas com observações.")

    counts = table.to_numpy()
    n = counts.sum()
    p = counts / n
    r, c = p.sum(axis=1), p.sum(axis=0)
    expected = np.outer(r, c)
    u, s, v = _svd((p - expected) / np.sqrt(expected))
    if not len(s):
        raise CorrespondenceError("As variáveis são independentes nesta tabela: não há inércia para analisar.")
    lam = s ** 2

    row_std, col_std = u / np.sqrt(r)[:, None], v / np.sqrt(c)[:, None]
    f, g = row_std * s, col_std * s
    row_d2 = (((p / r[:, None]) - c) ** 2 / c).sum(axis=1)
    col_d2 = (((p / c[None, :]) - r[:, None]) ** 2 / r[:, None]).sum(axis=0)
    total_inertia = lam.sum()

    def active(coords, std, mass, d2, totals, index) -> Points:
        return Points(
            coords=_frame(coords, index), std=_frame(std, index),
            cos2=_frame(_safe_div(coords ** 2, d2[:, None]), index),
            count=pd.Series(totals, index=index), mass=pd.Series(mass, index=index),
            contrib=_frame(mass[:, None] * coords ** 2 / lam, index),
            inertia=pd.Series(mass * d2 / total_inertia, index=index),
            vtest=_frame(_vtest(coords, totals, n), index),
        )

    def project(extra: pd.DataFrame | None, centroid: np.ndarray, std: np.ndarray) -> Points | None:
        """Supplementary profiles projected onto the other side's standard coordinates."""
        if extra is None or extra.empty:
            return None
        extra = extra.astype(float)
        extra = extra.loc[extra.sum(axis=1) > 0]
        if extra.empty:
            return None
        h = extra.to_numpy()
        totals = h.sum(axis=1)
        profiles = h / totals[:, None]
        coords = profiles @ std
        d2 = ((profiles - centroid) ** 2 / centroid).sum(axis=1)
        return Points(
            coords=_frame(coords, extra.index), cos2=_frame(_safe_div(coords ** 2, d2[:, None]), extra.index),
            count=pd.Series(totals, index=extra.index), vtest=_frame(_vtest(coords, totals, n), extra.index),
        )

    sup_r = project(None if sup_rows is None else sup_rows.reindex(columns=table.columns, fill_value=0), c, col_std)
    sup_c = project(None if sup_cols is None else sup_cols.reindex(index=table.index, fill_value=0).T, r, row_std)

    return CAResult(
        eigenvalues=lam,
        rows=active(f, row_std, r, row_d2, counts.sum(axis=1), table.index),
        cols=active(g, col_std, c, col_d2, counts.sum(axis=0), table.columns),
        sup_rows=sup_r, sup_cols=sup_c, total=n, table=table,
    )


# -- MCA ------------------------------------------------------------------------

def category_label(variable: str, category) -> str:
    return f"{variable}{CATEGORY_SEP}{category}"


def indicator(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Complete disjunctive table of ``df`` (one 0/1 column per category,
    labelled "variable: category") and the variable of each column."""
    blocks, variables = [], []
    for column in df.columns:
        values = df[column].astype(str)
        categories = sorted(values.unique(), key=str.lower)
        block = pd.DataFrame(
            {category_label(column, cat): (values == cat).to_numpy(dtype=float) for cat in categories},
            index=df.index,
        )
        blocks.append(block)
        variables += [column] * len(categories)
    z = pd.concat(blocks, axis=1) if blocks else pd.DataFrame(index=df.index)
    return z, pd.Series(variables, index=z.columns)


@dataclass
class MCAResult:
    eigenvalues: np.ndarray
    n_rows: int
    n_vars: int                   # K, active variables
    n_active_categories: int      # J, categories building the axes
    categories: Points            # active categories
    passive: Points | None        # passive (rare) categories of active variables
    sup: Points | None            # categories of supplementary variables
    row_coords: pd.DataFrame      # principal coordinates of the rows
    eta2: pd.DataFrame            # correlation ratio, index = variable, cols = dims
    eta2_kind: pd.Series          # "Ativa" / "Suplementar" per variable

    @property
    def n_dims(self) -> int:
        return len(self.eigenvalues)

    def adjusted_inertia(self) -> tuple[np.ndarray, np.ndarray]:
        """Benzécri-adjusted eigenvalues and Greenacre's percentages."""
        k, lam = self.n_vars, self.eigenvalues
        if k < 2:
            return lam.copy(), 100 * lam / lam.sum()
        adjusted = np.where(lam > 1 / k, ((k / (k - 1)) * (lam - 1 / k)) ** 2, 0.0)
        j = self.n_active_categories
        greenacre_total = (k / (k - 1)) * ((lam ** 2).sum() - (j - k) / k ** 2)
        greenacre = 100 * adjusted / greenacre_total if greenacre_total > _EPS else np.zeros_like(lam)
        return adjusted, greenacre


def mca(active: pd.DataFrame, passive: Iterable[str] = (), sup: pd.DataFrame | None = None,
        sup_variable: pd.Series | None = None) -> MCAResult:
    """MCA of the categorical variables in ``active`` (one row per observation).

    passive: category labels ("variable: category") of active variables to
        leave out of the cloud (specific MCA); projected as supplementary.
    sup: 0/1 membership of each row in each supplementary category (labels
        "variable: category"), with ``sup_variable`` giving each column's
        variable. A row may belong to several categories of one
        supplementary variable, or to none.
    """
    if active.shape[1] < 2:
        raise CorrespondenceError("Selecione pelo menos 2 variáveis ativas.")
    if len(active) < 2:
        raise CorrespondenceError("Sem dados suficientes para essas variáveis.")

    z, variable = indicator(active)
    n, k = len(active), active.shape[1]
    zm = z.to_numpy()
    p = zm.sum(axis=0) / n
    passive = set(passive)
    is_active = np.array([label not in passive for label in z.columns])
    if is_active.sum() < 2:
        raise CorrespondenceError("Restam menos de 2 categorias ativas (verifique o limiar de categorias raras).")

    weights = p[is_active] / k
    x = zm[:, is_active] / p[is_active] - 1
    u, s, _ = _svd(x * np.sqrt(weights) / np.sqrt(n))
    if not len(s):
        raise CorrespondenceError("Não há inércia para analisar com estas variáveis.")
    lam = s ** 2
    f = np.sqrt(n) * u * s   # row principal coordinates

    def project(members: np.ndarray, labels, var: pd.Series) -> Points:
        counts = members.sum(axis=0)
        means = _safe_div(members.T @ f, counts[:, None])
        coords = means / s
        d2 = _safe_div(n - counts, counts)
        return Points(
            coords=_frame(coords, labels), cos2=_frame(_safe_div(coords ** 2, d2[:, None]), labels),
            count=pd.Series(counts, index=labels), vtest=_frame(_vtest(coords, counts, n), labels),
            variable=var.loc[labels].reset_index(drop=True).set_axis(labels),
        )

    labels = z.columns
    categories = project(zm[:, is_active], labels[is_active], variable)
    mass = p[is_active] / k
    categories.mass = pd.Series(mass, index=labels[is_active])
    categories.contrib = _frame(mass[:, None] * categories.coords.to_numpy() ** 2 / lam, labels[is_active])
    categories.inertia = pd.Series(((1 - p[is_active]) / k) / ((1 - p[is_active]) / k).sum(), index=labels[is_active])

    passive_points = project(zm[:, ~is_active], labels[~is_active], variable) if (~is_active).any() else None

    sup_points = None
    if sup is not None and not sup.empty:
        sup = sup.loc[:, sup.to_numpy().sum(axis=0) > 0]
        if not sup.empty:
            sup_points = project(sup.to_numpy(dtype=float), sup.columns, sup_variable)

    # Correlation ratio: share of each dimension's variance explained by the
    # variable's categories (sum over categories of p_k * coord_k^2).
    eta_rows, kinds = {}, {}
    for points, kind in ((categories, "Ativa"), (passive_points, "Ativa"), (sup_points, "Suplementar")):
        if points is None:
            continue
        share = (points.count / n).to_numpy()[:, None] * points.coords.to_numpy() ** 2
        for var, part in pd.DataFrame(share, index=points.variable.to_numpy()).groupby(level=0):
            eta_rows[var] = eta_rows.get(var, 0) + part.sum(axis=0).to_numpy()
            kinds[var] = kind
    order = list(active.columns) + [v for v in eta_rows if v not in active.columns]
    eta2 = pd.DataFrame([eta_rows[v] for v in order], index=order, columns=range(len(lam)))

    return MCAResult(
        eigenvalues=lam, n_rows=n, n_vars=k, n_active_categories=int(is_active.sum()),
        categories=categories, passive=passive_points, sup=sup_points,
        row_coords=_frame(f, active.index), eta2=eta2, eta2_kind=pd.Series(kinds).reindex(order),
    )
