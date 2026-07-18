"""
Design-based estimators — the OBIWAN statistical core.

Why design-based rather than pixel subtraction
-----------------------------------------------
Differencing two interpolated AGBD maps confounds *model error* with *real change*
and produces no defensible uncertainty. GEDI samples the landscape with lidar
footprints; the honest treatment is to regard the footprints in a stratum as a
statistical sample, report a mean with a standard error, and express change as a
difference of two means with propagated variance.

The hybrid variance
-------------------
For footprints :math:`i = 1..n` in a stratum-year with AGBD :math:`y_i` (Mg/ha) and
per-footprint model standard error :math:`u_i` (``agbd_se``):

.. math::

    \\bar{y}   &= \\frac{1}{n}\\sum y_i \\\\
    v_{samp}  &= s^2 / n \\quad (s^2 = \\text{sample variance, ddof}=1) \\\\
    v_{pred}  &= \\overline{u_i^2} / n \\\\
    SE        &= \\sqrt{v_{samp} + v_{pred}}

``v_samp`` is the design-based sampling variance of the mean under simple random
sampling. ``v_pred`` carries GEDI's own model prediction error. Their sum is a
transparent hybrid (Patterson et al. 2019; Dubayah L4B ATBD).

.. note::
   The full OBIWAN/L4B estimator additionally folds in the model **coefficient
   covariance** — a systematic term of the form :math:`a' C a` requiring GEDI's
   published model covariance matrix :math:`C`. Here that term is approximated by
   the per-footprint prediction variances. The substitution point is marked in
   :func:`design_based_estimate`; swap it in if you hold :math:`C`.

   Consequence: reported standard errors are **slightly optimistic**. They are not
   suitable as-is for a verification-grade uncertainty deduction — which is exactly
   why ``src/vm0047.py`` applies its own sample-size-aware uncertainty floor rather
   than trusting these SEs directly.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from config.defaults import CARBON_FRACTION_DEFAULT, CLASS_LABELS, CO2_PER_C, TrendConfig

logger = logging.getLogger(__name__)

Z_95 = 1.959963985


def agbd_to_co2e(agbd_mg_ha, carbon_fraction: float = CARBON_FRACTION_DEFAULT):
    """AGBD (Mg d.m./ha) -> tCO2e/ha.

    The transform is linear, so standard errors scale by the identical factor —
    which is why the same function is applied to both means and SEs throughout.
    """
    return agbd_mg_ha * carbon_fraction * CO2_PER_C


# ---------------------------------------------------------------------------
# Core estimator
# ---------------------------------------------------------------------------

@dataclass
class Estimate:
    """A mean AGBD with its hybrid uncertainty."""

    n: int
    mean: float
    se: float
    ci_lo: float
    ci_hi: float
    v_sampling: float
    v_prediction: float

    @property
    def valid(self) -> bool:
        return self.n > 0 and np.isfinite(self.mean)

    @property
    def cv_pct(self) -> float:
        """Coefficient of variation of the mean, as a percentage."""
        if not self.valid or self.mean == 0:
            return float("nan")
        return 100.0 * self.se / abs(self.mean)


def _empty_estimate() -> Estimate:
    nan = float("nan")
    return Estimate(0, nan, nan, nan, nan, nan, nan)


def design_based_estimate(
    y: Sequence[float],
    u: Optional[Sequence[float]] = None,
    z: float = Z_95,
) -> Estimate:
    """Hybrid design-based mean AGBD and standard error for one footprint sample.

    Parameters
    ----------
    y
        Footprint AGBD values (Mg/ha).
    u
        Per-footprint model standard errors (``agbd_se``). None or all-NaN drops the
        prediction-variance term, leaving a pure design-based SE.
    z
        Normal quantile for the confidence interval.
    """
    y_arr = np.asarray(y, dtype=float)
    finite = np.isfinite(y_arr)
    y_arr = y_arr[finite]
    n = int(y_arr.size)

    if n == 0:
        return _empty_estimate()

    mean = float(np.mean(y_arr))
    v_samp = float(np.var(y_arr, ddof=1) / n) if n >= 2 else 0.0

    v_pred = 0.0
    if u is not None:
        u_arr = np.asarray(u, dtype=float)
        if u_arr.shape == finite.shape:
            u_arr = u_arr[finite]
        u_arr = u_arr[np.isfinite(u_arr)]
        if u_arr.size:
            # <-- OBIWAN/L4B exact form: replace with the model-covariance term a'Ca
            v_pred = float(np.mean(u_arr ** 2) / n)

    total_var = v_samp + v_pred
    se = math.sqrt(total_var) if total_var > 0 else 0.0

    return Estimate(
        n=n, mean=mean, se=se,
        ci_lo=mean - z * se, ci_hi=mean + z * se,
        v_sampling=v_samp, v_prediction=v_pred,
    )


# ---------------------------------------------------------------------------
# Per-stratum, per-year table
# ---------------------------------------------------------------------------

def annual_stratum_table(
    footprints: pd.DataFrame,
    carbon_fraction: float = CARBON_FRACTION_DEFAULT,
    z: float = Z_95,
) -> pd.DataFrame:
    """Mean AGBD, SE, CI, n and tCO2e/ha for every (stratum, year) cell."""
    if footprints is None or footprints.empty:
        return pd.DataFrame()

    rows = []
    for (stratum, year), group in footprints.groupby(["stratum", "year"]):
        est = design_based_estimate(
            group["agbd"].values,
            group["agbd_se"].values if "agbd_se" in group else None,
            z=z,
        )
        rows.append({
            "stratum": int(stratum),
            "stratum_label": CLASS_LABELS.get(int(stratum), str(stratum)),
            "year": int(year),
            "n": est.n,
            "agbd_mean": est.mean,
            "agbd_se": est.se,
            "agbd_ci_lo": est.ci_lo,
            "agbd_ci_hi": est.ci_hi,
            "cv_pct": est.cv_pct,
            "co2e_mean": agbd_to_co2e(est.mean, carbon_fraction),
            "co2e_se": agbd_to_co2e(est.se, carbon_fraction),
        })

    return pd.DataFrame(rows).sort_values(["stratum", "year"]).reset_index(drop=True)


def _estimate_for(footprints: pd.DataFrame, stratum: int, year: int, z: float = Z_95) -> Estimate:
    subset = footprints[(footprints["stratum"] == stratum) & (footprints["year"] == year)]
    if subset.empty:
        return _empty_estimate()
    return design_based_estimate(
        subset["agbd"].values,
        subset["agbd_se"].values if "agbd_se" in subset else None,
        z=z,
    )


# ---------------------------------------------------------------------------
# Change and additionality
# ---------------------------------------------------------------------------

def change_table(
    footprints: pd.DataFrame,
    years: Tuple[int, int],
    carbon_fraction: float = CARBON_FRACTION_DEFAULT,
    z: float = Z_95,
) -> pd.DataFrame:
    """Mean AGBD change between two years, per stratum, with propagated CI.

    The two years are independent samples, so ``Var(delta) = Var_0 + Var_1``.
    """
    if footprints is None or footprints.empty:
        return pd.DataFrame()

    y0, y1 = years
    rows = []
    for stratum in sorted(footprints["stratum"].unique()):
        e0 = _estimate_for(footprints, stratum, y0, z)
        e1 = _estimate_for(footprints, stratum, y1, z)

        if not (e0.valid and e1.valid):
            continue

        delta = e1.mean - e0.mean
        se = math.sqrt(e0.se ** 2 + e1.se ** 2)
        significant = abs(delta) > z * se if se > 0 else False

        rows.append({
            "stratum": int(stratum),
            "stratum_label": CLASS_LABELS.get(int(stratum), str(stratum)),
            "year_0": y0, "year_1": y1,
            "n_0": e0.n, "n_1": e1.n,
            "agbd_change": delta,
            "change_se": se,
            "change_ci_lo": delta - z * se,
            "change_ci_hi": delta + z * se,
            "co2e_change": agbd_to_co2e(delta, carbon_fraction),
            "co2e_change_se": agbd_to_co2e(se, carbon_fraction),
            "significant_95": significant,
        })

    return pd.DataFrame(rows)


def _annual_rate(
    footprints: pd.DataFrame, stratum: int, period: Tuple[int, int], z: float = Z_95
) -> Tuple[float, float]:
    """Annualised AGBD rate over a period: ``(m1 - m0)/dt``, ``Var = (V0+V1)/dt^2``."""
    y0, y1 = period
    dt = (y1 - y0) or 1
    e0 = _estimate_for(footprints, stratum, y0, z)
    e1 = _estimate_for(footprints, stratum, y1, z)
    if not (e0.valid and e1.valid):
        return float("nan"), float("nan")
    rate = (e1.mean - e0.mean) / dt
    se = math.sqrt(e0.se ** 2 + e1.se ** 2) / dt
    return rate, se


def additionality_table(
    footprints: pd.DataFrame,
    cfg: TrendConfig,
    carbon_fraction: float = CARBON_FRACTION_DEFAULT,
) -> pd.DataFrame:
    """Project-period rate minus baseline-period rate, per stratum.

    This is *observed historical* additionality within the GEDI record — a sanity
    check on whether the land is already changing, not the ex-ante additionality that
    ``src/vm0047.py`` computes against a modelled baseline.
    """
    if footprints is None or footprints.empty:
        return pd.DataFrame()

    z = Z_95
    rows = []
    for stratum in sorted(footprints["stratum"].unique()):
        rb, sb = _annual_rate(footprints, stratum, cfg.baseline_period, z)
        rp, sp = _annual_rate(footprints, stratum, cfg.project_period, z)
        if not (np.isfinite(rb) and np.isfinite(rp)):
            continue

        add = rp - rb
        se = math.sqrt(sb ** 2 + sp ** 2)
        rows.append({
            "stratum": int(stratum),
            "stratum_label": CLASS_LABELS.get(int(stratum), str(stratum)),
            "baseline_rate": rb, "baseline_se": sb,
            "project_rate": rp, "project_se": sp,
            "additionality": add, "additionality_se": se,
            "add_ci_lo": add - z * se, "add_ci_hi": add + z * se,
            "co2e_additionality": agbd_to_co2e(add, carbon_fraction),
            "significant_95": abs(add) > z * se if se > 0 else False,
        })

    return pd.DataFrame(rows)


def carbon_stock_table(
    trend: pd.DataFrame,
    areas_ha: Dict[int, float],
    year: int,
) -> pd.DataFrame:
    """Total stock per stratum: carbon density (tCO2e/ha) x stratum area (ha)."""
    if trend is None or trend.empty:
        return pd.DataFrame()

    subset = trend[trend["year"] == year]
    rows = []
    for _, r in subset.iterrows():
        area = float(areas_ha.get(int(r["stratum"]), 0.0))
        rows.append({
            "stratum": int(r["stratum"]),
            "stratum_label": r["stratum_label"],
            "year": year,
            "area_ha": area,
            "co2e_per_ha": r["co2e_mean"],
            "co2e_se_per_ha": r["co2e_se"],
            "stock_tco2e": r["co2e_mean"] * area,
            "stock_se_tco2e": r["co2e_se"] * area,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Trend fitting
# ---------------------------------------------------------------------------

@dataclass
class TrendFit:
    """A linear trend ``y ~ level + slope*(year - x0)`` with its covariance."""

    level: float
    slope: float
    var_level: float
    var_slope: float
    cov: float
    n: int
    method: str
    x0: int

    @property
    def slope_se(self) -> float:
        return math.sqrt(self.var_slope) if self.var_slope > 0 else 0.0

    @property
    def level_se(self) -> float:
        return math.sqrt(self.var_level) if np.isfinite(self.var_level) and self.var_level > 0 else 0.0

    def predict(self, t: float) -> float:
        """Fitted value ``t`` years after ``x0``."""
        return self.level + self.slope * t

    def predict_se(self, t: float) -> float:
        """SE of the fitted value: ``Var(a) + t^2 Var(b) + 2t Cov(a,b)``."""
        var = self.var_level + (t ** 2) * self.var_slope + 2 * t * self.cov
        return math.sqrt(var) if np.isfinite(var) and var > 0 else 0.0

    @property
    def significant_95(self) -> bool:
        return abs(self.slope) > Z_95 * self.slope_se if self.slope_se > 0 else False


def _weighted_normal_equations(years: np.ndarray, y: np.ndarray, w: np.ndarray, x0: float):
    """Solve the weighted normal equations. Returns ``(a, b, va, vb, cov, x)``.

    Variance terms are *unscaled* — OLS multiplies them by the residual MSE, WLS
    leaves them as-is because inverse-variance weights already fix the scale at 1.
    """
    x = years - x0
    s_w = w.sum()
    s_x = (w * x).sum()
    s_y = (w * y).sum()
    s_xx = (w * x * x).sum()
    s_xy = (w * x * y).sum()

    determinant = s_w * s_xx - s_x * s_x
    if abs(determinant) < 1e-12:
        # Degenerate (single distinct year): weighted mean, flat slope.
        return s_y / s_w, 0.0, 1.0 / s_w, 0.0, 0.0, x

    slope = (s_w * s_xy - s_x * s_y) / determinant
    level = (s_y - slope * s_x) / s_w
    return level, slope, s_xx / determinant, s_w / determinant, -s_x / determinant, x


def fit_trend(
    years: Sequence[float],
    values: Sequence[float],
    errors: Optional[Sequence[float]],
    x0: int,
    method: str = "wls",
) -> TrendFit:
    """Fit a linear trend to a stratum's full multi-year series.

    Methods
    -------
    ``wls``
        Inverse-variance weighted. Noisy years (large SE) pull the line less. The
        default, because GEDI year-to-year sample sizes vary a lot.
    ``ols``
        Equal weights, variance scaled by the residual MSE. Use when SEs are absent
        or untrustworthy.
    ``theilsen``
        Median of pairwise slopes. Robust to one anomalous year, which GEDI series
        with sparse coverage frequently contain.

    Centring on ``x0`` makes ``level`` the fitted value *at* ``x0``, which is what
    the forecast anchors on. Degenerate cases (n = 0, 1) return graceful fallbacks
    rather than raising, because a thin stratum should weaken a result, not break it.
    """
    years_arr = np.asarray(years, dtype=float)
    y_arr = np.asarray(values, dtype=float)
    se_arr = np.asarray(errors, dtype=float) if errors is not None else np.full_like(y_arr, np.nan)

    finite = np.isfinite(years_arr) & np.isfinite(y_arr)
    years_arr, y_arr, se_arr = years_arr[finite], y_arr[finite], se_arr[finite]
    n = int(years_arr.size)

    if n == 0:
        return TrendFit(float("nan"), 0.0, float("nan"), 0.0, 0.0, 0, method, x0)
    if n == 1:
        var = float(se_arr[0] ** 2) if np.isfinite(se_arr[0]) else 0.0
        return TrendFit(float(y_arr[0]), 0.0, var, 0.0, 0.0, 1, method, x0)

    if method == "theilsen":
        return _fit_theilsen(years_arr, y_arr, x0, n)

    if method == "ols":
        level, slope, va, vb, cov, x = _weighted_normal_equations(
            years_arr, y_arr, np.ones(n), x0
        )
        residual = y_arr - (level + slope * x)
        s2 = float((residual ** 2).sum() / max(n - 2, 1))
        return TrendFit(float(level), float(slope), va * s2, vb * s2, cov * s2, n, "ols", x0)

    # Default: WLS. Missing or non-positive SEs are filled with the median observed
    # SE so one bad year cannot dominate the fit with an infinite weight.
    positive = se_arr[np.isfinite(se_arr) & (se_arr > 0)]
    fill = float(np.median(positive)) if positive.size else 1.0
    se_clean = np.where(np.isfinite(se_arr) & (se_arr > 0), se_arr, fill)

    level, slope, va, vb, cov, _ = _weighted_normal_equations(
        years_arr, y_arr, 1.0 / (se_clean ** 2), x0
    )
    return TrendFit(float(level), float(slope), float(va), float(vb), float(cov), n, "wls", x0)


def _fit_theilsen(years: np.ndarray, y: np.ndarray, x0: int, n: int) -> TrendFit:
    """Robust Theil-Sen fit, with SciPy where available and an IQR fallback."""
    x = years - x0

    try:
        from scipy import stats  # noqa: PLC0415

        slope, intercept, lo, hi = stats.theilslopes(y, x, 0.95)
        slope, level = float(slope), float(intercept)
        var_slope = float(((hi - lo) / (2 * Z_95)) ** 2)
    except Exception as exc:  # noqa: BLE001
        logger.debug("SciPy theilslopes unavailable (%s); using pairwise fallback.", exc)
        pairwise = np.array([
            (y[j] - y[i]) / (x[j] - x[i])
            for i in range(n) for j in range(i + 1, n) if x[j] != x[i]
        ])
        slope = float(np.median(pairwise)) if pairwise.size else 0.0
        level = float(np.median(y - slope * x))
        var_slope = 0.0
        if pairwise.size >= 2:
            iqr = float(np.percentile(pairwise, 75) - np.percentile(pairwise, 25))
            var_slope = float((iqr / 1.349 / math.sqrt(pairwise.size)) ** 2)

    residual = y - (level + slope * x)
    mad = float(np.median(np.abs(residual - np.median(residual))))
    var_level = float((1.4826 * mad) ** 2 / n)  # MAD -> robust sigma

    return TrendFit(level, slope, var_level, var_slope, 0.0, n, "theilsen", x0)


def fit_stratum_trend(trend: pd.DataFrame, stratum: int, x0: int, method: str = "wls") -> TrendFit:
    """Convenience wrapper: fit the trend of one stratum from an annual table."""
    subset = trend[trend["stratum"] == stratum].sort_values("year")
    return fit_trend(
        subset["year"].values,
        subset["agbd_mean"].values,
        subset["agbd_se"].values if "agbd_se" in subset else None,
        x0,
        method,
    )


def stratum_density(trend: pd.DataFrame, stratum: int, year: int) -> float:
    """Mean AGBD for a stratum at a year, falling back to its multi-year mean."""
    if trend is None or trend.empty:
        return float("nan")
    exact = trend[(trend["stratum"] == stratum) & (trend["year"] == year)]
    if len(exact):
        return float(exact["agbd_mean"].iloc[0])
    any_year = trend[trend["stratum"] == stratum]
    return float(any_year["agbd_mean"].mean()) if len(any_year) else float("nan")
