"""Checks of the CA/MCA engine against a published example, the `prince`
library (when installed) and the identities the methods must satisfy.

Run from the folder above the package:  python -m unittest Stravinsky_QDA.tests.test_correspondence
(or from the package folder:            python -m unittest tests.test_correspondence)
"""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import correspondence as corr  # noqa: E402

try:
    import prince
except ImportError:  # pragma: no cover
    prince = None

# Greenacre's smoking data (Theory and Applications of CA, 1984; R package `ca`).
SMOKE = pd.DataFrame(
    [[4, 2, 3, 2], [4, 3, 7, 4], [25, 10, 12, 4], [18, 24, 33, 13], [10, 6, 7, 2]],
    index=["SM", "JM", "SE", "JE", "SC"], columns=["none", "light", "medium", "heavy"],
)


def random_categorical(seed=0, n=300):
    rng = np.random.default_rng(seed)
    a = rng.choice(["a1", "a2", "a3"], n, p=[0.5, 0.3, 0.2])
    b = np.where(rng.random(n) < 0.6, np.char.replace(a, "a", "b"), rng.choice(["b1", "b2", "b3", "b4"], n))
    c = rng.choice(["c1", "c2"], n)
    d = np.where(rng.random(n) < 0.5, np.char.replace(b, "b", "d"), rng.choice(["d1", "d2", "d3", "d4"], n))
    return pd.DataFrame({"A": a, "B": b, "C": c, "D": d})


class CATests(unittest.TestCase):
    def test_smoke_published_values(self):
        res = corr.ca(SMOKE)
        np.testing.assert_allclose(res.eigenvalues, [0.074759, 0.010017, 0.000414], rtol=2e-3)
        np.testing.assert_allclose(
            np.abs(res.rows.coords[0]), [0.065768, 0.258958, 0.380595, 0.232952, 0.201089], atol=2e-5,
        )
        self.assertAlmostEqual(res.eigenvalues.sum() * SMOKE.to_numpy().sum(), 16.442, places=2)  # chi-square

    def test_identities(self):
        res = corr.ca(SMOKE)
        for points in (res.rows, res.cols):
            np.testing.assert_allclose(points.contrib.sum(axis=0), 1, atol=1e-10)
            np.testing.assert_allclose(points.cos2.sum(axis=1), 1, atol=1e-10)
            np.testing.assert_allclose(points.inertia.sum(), 1, atol=1e-10)

    def test_active_point_projected_as_supplementary_lands_on_itself(self):
        res = corr.ca(SMOKE, sup_rows=SMOKE.loc[["SE"]], sup_cols=SMOKE[["heavy"]])
        np.testing.assert_allclose(res.sup_rows.coords.loc["SE"], res.rows.coords.loc["SE"], atol=1e-12)
        np.testing.assert_allclose(res.sup_cols.coords.loc["heavy"], res.cols.coords.loc["heavy"], atol=1e-12)
        np.testing.assert_allclose(res.sup_rows.cos2.loc["SE"], res.rows.cos2.loc["SE"], atol=1e-12)

    def test_supplementary_row_ignores_unknown_columns(self):
        sup = pd.DataFrame([[1, 2, 3, 4, 99]], index=["X"], columns=["none", "light", "medium", "heavy", "other"])
        res = corr.ca(SMOKE, sup_rows=sup)
        expected = corr.ca(SMOKE, sup_rows=sup.drop(columns="other"))
        np.testing.assert_allclose(res.sup_rows.coords, expected.sup_rows.coords)

    @unittest.skipIf(prince is None, "prince not installed")
    def test_matches_prince(self):
        rng = np.random.default_rng(1)
        table = pd.DataFrame(rng.integers(1, 40, size=(6, 5)), index=list("abcdef"), columns=list("VWXYZ"))
        res = corr.ca(table)
        ref = prince.CA(n_components=3, random_state=42).fit(table)
        np.testing.assert_allclose(res.eigenvalues[:3], ref.eigenvalues_, rtol=1e-8)
        np.testing.assert_allclose(np.abs(res.rows.coords.iloc[:, :3]), np.abs(ref.row_coordinates(table)), atol=1e-10)
        np.testing.assert_allclose(np.abs(res.cols.coords.iloc[:, :3]), np.abs(ref.column_coordinates(table)), atol=1e-10)
        np.testing.assert_allclose(res.rows.contrib.iloc[:, :3], ref.row_contributions_, atol=1e-10)
        np.testing.assert_allclose(res.rows.cos2.iloc[:, :3], ref.row_cosine_similarities(table), atol=1e-10)

    def test_too_small(self):
        with self.assertRaises(corr.CorrespondenceError):
            corr.ca(SMOKE.iloc[:1])


class MCATests(unittest.TestCase):
    def setUp(self):
        self.df = random_categorical()

    def test_identities(self):
        res = corr.mca(self.df)
        np.testing.assert_allclose(res.categories.contrib.sum(axis=0), 1, atol=1e-10)
        np.testing.assert_allclose(res.row_coords.mean(axis=0), 0, atol=1e-10)
        # Each eigenvalue is the mean correlation ratio of the active variables.
        np.testing.assert_allclose(res.eta2.mean(axis=0), res.eigenvalues, atol=1e-10)
        # Total inertia of the indicator matrix: J/K - 1.
        k, j = self.df.shape[1], res.n_active_categories
        self.assertAlmostEqual(res.eigenvalues.sum(), j / k - 1, places=10)

    def test_supplementary_copy_of_active_variable_lands_on_itself(self):
        z, var = corr.indicator(self.df[["B"]].rename(columns={"B": "B_sup"}))
        res = corr.mca(self.df, sup=z, sup_variable=var)
        active = res.categories.coords.loc[[l for l in res.categories.coords.index if l.startswith("B: ")]]
        sup = res.sup.coords.set_axis([l.replace("B_sup", "B") for l in res.sup.coords.index])
        np.testing.assert_allclose(sup.loc[active.index], active, atol=1e-10)
        self.assertAlmostEqual(res.eta2.loc["B_sup", 0], res.eta2.loc["B", 0], places=10)

    def test_vtest_formula(self):
        res = corr.mca(self.df)
        n = len(self.df)
        nk = res.categories.count.to_numpy()
        expected = res.categories.coords.to_numpy() * np.sqrt((n - 1) * nk / (n - nk))[:, None]
        np.testing.assert_allclose(res.categories.vtest, expected)

    def test_specific_mca(self):
        passive = {"B: b4", "D: d4"}
        res = corr.mca(self.df, passive=passive)
        self.assertEqual(set(res.passive.coords.index), passive)
        self.assertNotIn("B: b4", res.categories.coords.index)
        np.testing.assert_allclose(res.categories.contrib.sum(axis=0), 1, atol=1e-10)
        np.testing.assert_allclose(res.row_coords.mean(axis=0), 0, atol=1e-10)
        # Total inertia = sum over active categories of (1 - p_k) / K.
        p = res.categories.count / len(self.df)
        self.assertAlmostEqual(res.eigenvalues.sum(), ((1 - p) / self.df.shape[1]).sum(), places=10)

    def test_multi_membership_supplementary(self):
        z = pd.DataFrame({"S: x": [1.0] * 150 + [0.0] * 150, "S: y": [1.0] * 200 + [0.0] * 100}, index=self.df.index)
        res = corr.mca(self.df, sup=z, sup_variable=pd.Series(["S", "S"], index=z.columns))
        f = res.row_coords.to_numpy()
        np.testing.assert_allclose(res.sup.coords.loc["S: y"], f[:200].mean(axis=0) / np.sqrt(res.eigenvalues))

    @unittest.skipIf(prince is None, "prince not installed")
    def test_matches_prince(self):
        res = corr.mca(self.df)
        ref = prince.MCA(n_components=3, random_state=42).fit(self.df)
        np.testing.assert_allclose(res.eigenvalues[:3], ref.eigenvalues_, rtol=1e-8)
        ref_cols = ref.column_coordinates(self.df)
        ref_cols.index = [str(i).replace("__", ": ", 1) for i in ref_cols.index]
        mine = res.categories.coords.loc[ref_cols.index].iloc[:, :3]
        np.testing.assert_allclose(np.abs(mine), np.abs(ref_cols), atol=1e-8)
        np.testing.assert_allclose(np.abs(res.row_coords.iloc[:, :3]), np.abs(ref.row_coordinates(self.df)), atol=1e-8)


if __name__ == "__main__":
    unittest.main()
