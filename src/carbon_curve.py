"""
Carbon accumulation curve — Phase 7 parameterisation.

This module answers one question: **how much above-ground biomass will a hectare of
this land carry, at every age from 0 to the end of the crediting period?**

The source notebook answered it with a single hard-wired path — a ``recovery_curve``
fit to GEDI stratum 21. That works only where GEDI flies, only for natural
regeneration, and offers the user no way to say "we are planting 1,100 stems/ha of
mixed natives on degraded clay with seasonal drought". This module opens that up.

Five independent levers shape the curve
---------------------------------------
1. **Intervention type** — active planting (logistic, from bare ground, establishment
   lag) versus ANR (recovery curve, head start from existing rootstock), or a
   weighted blend of both.
2. **Ecological zone** — sets the AGB ceiling, the baseline recovery rate, and the
   root:shoot ratio. Auto-suggested from AOI latitude and tree cover; always
   overridable.
3. **Site index** — a productivity multiplier on both rate and ceiling, for stands
   that are better or worse than the zone average.
4. **Soil quality, water stress, fire risk, grazing pressure** — multiplicative
   modifiers on rate and ceiling, additive on mortality.
5. **Species mix** — per-cohort allometry and growth model, weighted by area fraction.

Provenance tiers
----------------
Every resolved curve carries a ``tier`` recording where its numbers came from, and
that label follows the results all the way to the exported workbook:

``gedi_calibrated``
    Rate and ceiling both fit to this AOI's own GEDI record. Strongest evidence.
``gedi_partial``
    One of the two came from GEDI, the other from IPCC defaults.
``ipcc_default``
    No usable GEDI — outside coverage, disabled, or too few footprints. The curve is
    entirely literature-based and should be treated as indicative only.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config.defaults import (
    CO2_PER_C,
    CarbonCurveConfig,
    GEDIConfig,
    SpeciesMixEntry,
)
from config.ecological_zones import (
    FIRE_RISK_MODIFIERS,
    GRAZING_MODIFIERS,
    SOIL_QUALITY_MODIFIERS,
    WATER_STRESS_MODIFIERS,
    get_zone,
)
from config.species import INTERVENTION_PRESETS
from src.estimators import fit_stratum_trend, stratum_density

logger = logging.getLogger(__name__)

TIER_GEDI = "gedi_calibrated"
TIER_PARTIAL = "gedi_partial"
TIER_IPCC = "ipcc_default"

TIER_LABELS = {
    TIER_GEDI: "GEDI-calibrated (site-specific)",
    TIER_PARTIAL: "Partially GEDI-calibrated",
    TIER_IPCC: "IPCC defaults (indicative only)",
}


# ---------------------------------------------------------------------------
# Modifiers
# ---------------------------------------------------------------------------

@dataclass
class Modifiers:
    """Combined effect of site, soil, climate and disturbance settings."""

    growth_multiplier: float = 1.0
    ceiling_multiplier: float = 1.0
    mortality_delta: float = 0.0
    applied: List[str] = field(default_factory=list)


def apply_modifiers(cfg: CarbonCurveConfig) -> Modifiers:
    """Combine every site modifier into one set of multipliers.

    Growth and ceiling multipliers compound (a degraded site with severe drought is
    worse than either alone); mortality deltas add. Multipliers are clamped to a
    sane band so a stack of pessimistic settings cannot drive the curve to zero and
    produce a divide-by-zero downstream.
    """
    mods = Modifiers()

    def combine(table: Dict[str, tuple], key: str, label: str) -> None:
        growth, ceiling, mortality = table.get(key, (1.0, 1.0, 0.0))
        mods.growth_multiplier *= growth
        mods.ceiling_multiplier *= ceiling
        mods.mortality_delta += mortality
        if (growth, ceiling, mortality) != (1.0, 1.0, 0.0):
            mods.applied.append(f"{label}: {key} (growth x{growth:.2f}, ceiling x{ceiling:.2f})")

    combine(SOIL_QUALITY_MODIFIERS, cfg.soil_quality, "Soil")
    combine(WATER_STRESS_MODIFIERS, cfg.water_stress, "Water stress")
    combine(FIRE_RISK_MODIFIERS, cfg.fire_risk, "Fire risk")
    combine(GRAZING_MODIFIERS, cfg.grazing_pressure, "Grazing")

    site = float(np.clip(cfg.site_index, 0.3, 2.0))
    if abs(site - 1.0) > 1e-6:
        mods.growth_multiplier *= site
        mods.ceiling_multiplier *= (1.0 + (site - 1.0) * 0.5)  # ceiling is less elastic than rate
        mods.applied.append(f"Site index: {site:.2f}")

    mods.growth_multiplier = float(np.clip(mods.growth_multiplier, 0.05, 3.0))
    mods.ceiling_multiplier = float(np.clip(mods.ceiling_multiplier, 0.10, 2.0))
    mods.mortality_delta = float(np.clip(mods.mortality_delta, -0.05, 0.20))

    return mods


# ---------------------------------------------------------------------------
# GEDI calibration
# ---------------------------------------------------------------------------

@dataclass
class Calibration:
    """Result of fitting the recovery curve to this AOI's GEDI record."""

    success: bool
    ceiling_agb: float = float("nan")
    """Mature-forest AGB ceiling (t d.m./ha) from the reference stratum."""

    recovery_rate: float = float("nan")
    """Exponential constant k in AGB(t) = ceiling * (1 - exp(-k*(t + advancement)))."""

    advancement_years: float = 0.0
    rmse: float = float("nan")
    n_years: int = 0
    ceiling_from_gedi: bool = False
    rate_from_gedi: bool = False
    observed: Optional[pd.DataFrame] = None
    """Observed (year, agbd, agbd_se) used in the fit, for the diagnostic chart."""

    message: str = ""


def calibrate_from_gedi(
    trend: pd.DataFrame,
    cfg: CarbonCurveConfig,
    gedi_cfg: GEDIConfig,
    reference_year: int,
    reliable_strata: Optional[set] = None,
) -> Calibration:
    """Fit ceiling and recovery rate to the AOI's own GEDI observations.

    The ceiling comes from the reference stratum's mean AGBD (mature forest standing
    in the same landscape — the most defensible local asymptote available). The rate
    comes from a non-linear fit to the calibration stratum's multi-year trend.

    Either half can fail independently, which is why the returned object flags them
    separately and the caller can still reach ``gedi_partial``.
    """
    reliable = reliable_strata if reliable_strata is not None else set()

    if trend is None or trend.empty:
        return Calibration(False, message="No GEDI trend data available.")

    # --- Ceiling from the reference (mature forest) stratum ---
    ceiling = stratum_density(trend, cfg.reference_class, reference_year)
    ceiling_ok = (
        np.isfinite(ceiling)
        and ceiling > 0
        and (not reliable or cfg.reference_class in reliable)
    )

    # --- Rate from the calibration (regenerating) stratum ---
    calib = trend[trend["stratum"] == cfg.calibration_class].sort_values("year")
    rate_ok = False
    rate = float("nan")
    advancement = cfg.advancement_years
    rmse = float("nan")

    zone = get_zone(cfg.ecological_zone)
    fallback_ceiling = float(zone["agb_max_t_ha"])
    fallback_rate = float(zone["recovery_rate"])

    effective_ceiling = ceiling if ceiling_ok else fallback_ceiling

    enough_years = len(calib) >= 3
    stratum_reliable = (not reliable) or (cfg.calibration_class in reliable)

    if enough_years and stratum_reliable and effective_ceiling > 0:
        years = calib["year"].values.astype(float)
        agbd = calib["agbd_mean"].values.astype(float)
        errors = calib["agbd_se"].values.astype(float)
        t_rel = years - years.min()

        def model(t, k, adv):
            return effective_ceiling * (1.0 - np.exp(-k * (t + adv)))

        try:
            from scipy.optimize import curve_fit  # noqa: PLC0415

            positive = errors[np.isfinite(errors) & (errors > 0)]
            fill = float(np.median(positive)) if positive.size else 1.0
            sigma = np.where(np.isfinite(errors) & (errors > 0), errors, fill)

            popt, _ = curve_fit(
                model, t_rel, agbd,
                p0=[fallback_rate, max(advancement, 1.0)],
                bounds=([1e-3, 0.0], [1.0, 60.0]),
                sigma=sigma, maxfev=8000,
            )
            rate, advancement = float(popt[0]), float(popt[1])
            rmse = float(np.sqrt(np.mean((agbd - model(t_rel, rate, advancement)) ** 2)))
            rate_ok = True
        except Exception as exc:  # noqa: BLE001 — a failed fit falls back, it does not raise
            logger.info("Recovery-curve fit failed: %s", exc)

    if not rate_ok:
        rate = fallback_rate

    success = ceiling_ok or rate_ok
    if ceiling_ok and rate_ok:
        message = (
            f"Curve calibrated to this AOI: ceiling {effective_ceiling:.0f} t/ha from "
            f"mature forest, recovery rate {rate:.4f}/yr fit to {len(calib)} years of "
            f"regenerating land (RMSE {rmse:.1f} t/ha)."
        )
    elif ceiling_ok:
        message = (
            f"Ceiling calibrated from local mature forest ({effective_ceiling:.0f} t/ha), "
            f"but the recovery rate could not be fit — using the {zone['label']} "
            f"default ({rate:.4f}/yr)."
        )
    elif rate_ok:
        message = (
            f"Recovery rate fit locally ({rate:.4f}/yr), but no reliable mature-forest "
            f"reference — using the {zone['label']} ceiling ({fallback_ceiling:.0f} t/ha)."
        )
    else:
        message = (
            f"No usable GEDI calibration. Falling back entirely to {zone['label']} "
            "defaults — treat the resulting carbon estimates as indicative."
        )

    return Calibration(
        success=success,
        ceiling_agb=effective_ceiling,
        recovery_rate=rate,
        advancement_years=advancement,
        rmse=rmse,
        n_years=len(calib),
        ceiling_from_gedi=bool(ceiling_ok),
        rate_from_gedi=bool(rate_ok),
        observed=calib[["year", "agbd_mean", "agbd_se"]].copy() if len(calib) else None,
        message=message,
    )


# ---------------------------------------------------------------------------
# Resolved curve
# ---------------------------------------------------------------------------

@dataclass
class ResolvedSpecies:
    """One species cohort with all modifiers already folded in."""

    name: str
    area_fraction: float
    growth_model: str
    agb_max: float
    k_growth: float
    age_inflection: float
    root_shoot_ratio: float
    carbon_fraction: float
    planting_density: int
    mortality_rate_y1: float
    annual_mortality: float
    establishment_lag: float
    advancement_years: float
    recovery_rate: float
    wood_density: float
    mai: float
    is_harvested: bool
    harvest_cycle_years: int
    min_harvest_age: int
    pct_harvested: float

    def agb_at(self, age: float) -> float:
        """Above-ground biomass (t d.m./ha) at a given stand age.

        The establishment lag shifts the whole curve right: a planting with a 1-year
        lag has zero AGB in year 1 and behaves like a year-1 stand in year 2.

        ``recovery_curve`` is deliberately exempt from the zero-at-age-0 rule. ANR
        land already carries standing biomass on day one — the original notebook's
        blanket ``age <= 0 -> 0`` short-circuit produced a spurious first-year spike
        equal to the entire pre-existing stock, which would have been credited.
        """
        if self.growth_model == "recovery_curve":
            effective_age = max(0.0, age) + self.advancement_years
            return self.agb_max * (1.0 - math.exp(-self.recovery_rate * effective_age))

        effective_age = age - self.establishment_lag
        if effective_age <= 0:
            return 0.0

        if self.growth_model == "logistic":
            # Normalised so AGB(0) = 0 exactly; a raw logistic starts at a non-zero
            # value, which would credit biomass that was never established.
            raw = self.agb_max / (1.0 + math.exp(-self.k_growth * (effective_age - self.age_inflection)))
            at_zero = self.agb_max / (1.0 + math.exp(self.k_growth * self.age_inflection))
            return max(0.0, (raw - at_zero) / (1.0 - at_zero / self.agb_max)) \
                if self.agb_max > at_zero else max(0.0, raw - at_zero)

        if self.growth_model == "MAI":
            return min(self.agb_max, self.mai * self.wood_density * effective_age)

        if self.growth_model == "linear_dbh":
            return min(self.agb_max, self.k_growth * self.agb_max * effective_age / 20.0)

        return 0.0

    def survival_fraction(self, age: float) -> float:
        """Establishment survival — the fraction of planted stocking that persists.

        Mortality is applied only up to **canopy closure** (approximated by
        ``age_inflection``), then held constant for the rest of the rotation.

        The reason is that ``agb_max`` is a *stand-level* asymptote in t/ha: it
        already embeds the self-thinning that occurs in a closed stand, where a dying
        stem's growing space is captured by its neighbours and stand biomass does not
        fall. Compounding a per-stem survival decay across the whole rotation on top
        of that double-counts mortality, and — because the logistic saturates while
        the decay does not — drives the curve *downward* in later decades. A carbon
        curve that declines with no harvest event is not physical, and it would show
        up as spurious negative annual increments in the credit schedule.

        Before closure the stand is not yet capturing the site, so seedling losses do
        genuinely reduce stocking; that is the part modelled here.
        """
        if age <= 0 or self.planting_density <= 0:
            return 1.0
        closure_age = max(1.0, self.age_inflection)
        years_of_loss = min(max(0.0, age - 1.0), max(0.0, closure_age - 1.0))
        return (1.0 - self.mortality_rate_y1) * ((1.0 - self.annual_mortality) ** years_of_loss)

    def carbon_at(self, age: float) -> Dict[str, float]:
        """Carbon stocks (tCO2e/ha) at a given age, including harvest cycling."""
        agb = self.agb_at(age)

        if self.is_harvested and age >= self.min_harvest_age:
            cycle_age = (age - self.min_harvest_age) % self.harvest_cycle_years
            if cycle_age == 0 and age > self.min_harvest_age:
                agb *= (1.0 - self.pct_harvested)
            else:
                agb = self.agb_at(cycle_age if cycle_age > 0 else self.harvest_cycle_years)

        if self.planting_density > 0:
            agb *= self.survival_fraction(age)

        agb_c = agb * self.carbon_fraction
        bgb_c = agb_c * self.root_shoot_ratio
        return {
            "agb_dm": agb,
            "agb_co2": agb_c * CO2_PER_C,
            "bgb_co2": bgb_c * CO2_PER_C,
            "total_co2": (agb_c + bgb_c) * CO2_PER_C,
        }


@dataclass
class ResolvedCurve:
    """A fully-parameterised carbon accumulation curve, ready for the VM0047 engine."""

    species: List[ResolvedSpecies]
    tier: str
    provenance: List[str]
    modifiers: Modifiers
    calibration: Optional[Calibration]
    ecological_zone: str
    intervention_type: str
    ceiling_agb: float
    effective_root_shoot: float

    @property
    def tier_label(self) -> str:
        return TIER_LABELS.get(self.tier, self.tier)

    @property
    def is_indicative_only(self) -> bool:
        return self.tier == TIER_IPCC

    def agb_at(self, age: float) -> float:
        """Area-weighted above-ground biomass (t d.m./ha) across the species mix."""
        return sum(s.agb_at(age) * s.area_fraction for s in self.species)

    def carbon_at(self, age: float) -> Dict[str, float]:
        """Area-weighted carbon stocks (tCO2e/ha) across the species mix."""
        totals = {"agb_dm": 0.0, "agb_co2": 0.0, "bgb_co2": 0.0, "total_co2": 0.0}
        for s in self.species:
            stocks = s.carbon_at(age)
            for key in totals:
                totals[key] += stocks[key] * s.area_fraction
        return totals

    def to_frame(self, max_age: int) -> pd.DataFrame:
        """Tabulate the curve from age 0 to ``max_age``, with annual increments."""
        rows = []
        previous = 0.0
        for age in range(0, int(max_age) + 1):
            stocks = self.carbon_at(age)
            total = stocks["total_co2"]
            rows.append({
                "age": age,
                "agb_dm_t_ha": stocks["agb_dm"],
                "agb_tco2e_ha": stocks["agb_co2"],
                "bgb_tco2e_ha": stocks["bgb_co2"],
                "total_tco2e_ha": total,
                "annual_increment_tco2e_ha": total - previous,
            })
            previous = total
        return pd.DataFrame(rows)

    def time_to_fraction(self, fraction: float, max_age: int = 200) -> Optional[int]:
        """First age at which the curve reaches ``fraction`` of its own maximum.

        Useful for a plain-language readout: "reaches half of mature stock in year 14".
        """
        peak = self.carbon_at(max_age)["total_co2"]
        if peak <= 0:
            return None
        target = peak * fraction
        for age in range(0, max_age + 1):
            if self.carbon_at(age)["total_co2"] >= target:
                return age
        return None


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve_curve(
    cfg: CarbonCurveConfig,
    calibration: Optional[Calibration] = None,
) -> ResolvedCurve:
    """Combine intervention, zone, modifiers, species mix and calibration into a curve.

    This is the single place where all five levers meet. Precedence, highest first:

    1. GEDI calibration (ceiling and rate), when available and enabled
    2. Explicit per-species parameters from the mix editor
    3. Ecological zone defaults
    4. Intervention preset multipliers

    Site modifiers are applied *last*, on top of whatever the ceiling and rate ended
    up being, so a user who pastes in a measured ceiling still sees their drought
    setting take effect.
    """
    zone = get_zone(cfg.ecological_zone)
    preset = INTERVENTION_PRESETS.get(cfg.intervention_type, INTERVENTION_PRESETS["active_planting"])
    mods = apply_modifiers(cfg)
    provenance: List[str] = []

    # --- Ceiling ---
    zone_ceiling = float(zone["agb_max_t_ha"])
    ceiling_from_gedi = bool(
        calibration and calibration.ceiling_from_gedi and cfg.calibrate_from_gedi
    )
    base_ceiling = calibration.ceiling_agb if ceiling_from_gedi else zone_ceiling
    provenance.append(
        f"AGB ceiling {base_ceiling:.0f} t/ha from "
        + ("this AOI's mature forest (GEDI)" if ceiling_from_gedi
           else f"{zone['label']} defaults")
    )

    # --- Recovery rate ---
    zone_rate = float(zone["recovery_rate"])
    rate_from_gedi = bool(
        calibration and calibration.rate_from_gedi and cfg.calibrate_from_gedi
    )
    base_rate = calibration.recovery_rate if rate_from_gedi else zone_rate
    provenance.append(
        f"Recovery rate {base_rate:.4f}/yr from "
        + ("a fit to this AOI's regenerating land (GEDI)" if rate_from_gedi
           else f"{zone['label']} defaults")
    )

    # --- Tier ---
    if ceiling_from_gedi and rate_from_gedi:
        tier = TIER_GEDI
    elif ceiling_from_gedi or rate_from_gedi:
        tier = TIER_PARTIAL
    else:
        tier = TIER_IPCC

    # --- Apply modifiers ---
    ceiling = base_ceiling * mods.ceiling_multiplier * preset["ceiling_multiplier"]
    rate = base_rate * mods.growth_multiplier * preset["growth_multiplier"]
    provenance.extend(mods.applied)
    provenance.append(
        f"Intervention '{preset['label']}': growth x{preset['growth_multiplier']:.2f}, "
        f"ceiling x{preset['ceiling_multiplier']:.2f}"
    )

    if cfg.conservative_haircut > 0:
        ceiling *= (1.0 - cfg.conservative_haircut)
        provenance.append(f"Conservative haircut: -{100 * cfg.conservative_haircut:.0f}% on ceiling")

    root_shoot = cfg.root_shoot_override if cfg.root_shoot_override is not None \
        else float(zone["root_shoot"])

    advancement = calibration.advancement_years if (calibration and rate_from_gedi) \
        else cfg.advancement_years or preset["advancement_years"]

    # --- Build the resolved species mix ---
    mix = cfg.species_mix or []
    if not mix:
        from config.species import default_mix_for  # noqa: PLC0415

        mix = default_mix_for(cfg.intervention_type)

    resolved: List[ResolvedSpecies] = []
    for entry in mix:
        resolved.append(_resolve_species(
            entry, cfg, preset, mods, ceiling, rate, root_shoot, advancement
        ))

    return ResolvedCurve(
        species=resolved,
        tier=tier,
        provenance=provenance,
        modifiers=mods,
        calibration=calibration,
        ecological_zone=cfg.ecological_zone,
        intervention_type=cfg.intervention_type,
        ceiling_agb=ceiling,
        effective_root_shoot=root_shoot,
    )


def _resolve_species(
    entry: SpeciesMixEntry,
    cfg: CarbonCurveConfig,
    preset: dict,
    mods: Modifiers,
    ceiling: float,
    rate: float,
    root_shoot: float,
    advancement: float,
) -> ResolvedSpecies:
    """Fold every modifier into one species cohort.

    A species' own ``agb_max`` is treated as its share of the site's potential, scaled
    to the resolved ceiling. This keeps a species mix internally consistent when GEDI
    calibration moves the site ceiling away from the template values — otherwise a
    locally-measured 90 t/ha ceiling would be silently ignored by a template written
    around 200 t/ha.
    """
    zone = get_zone(cfg.ecological_zone)
    template_ceiling = float(zone["agb_max_t_ha"]) or 1.0
    species_scale = entry.agb_max / template_ceiling if template_ceiling > 0 else 1.0
    scaled_agb_max = max(1.0, ceiling * species_scale)

    growth_model = entry.growth_model
    if cfg.intervention_type == "anr" and growth_model == "logistic":
        # ANR has no planting event to drive a logistic establishment curve.
        growth_model = "recovery_curve"

    return ResolvedSpecies(
        name=entry.name,
        area_fraction=entry.area_fraction,
        growth_model=growth_model,
        agb_max=scaled_agb_max,
        k_growth=entry.k_growth * mods.growth_multiplier * preset["growth_multiplier"],
        age_inflection=entry.age_inflection,
        root_shoot_ratio=cfg.root_shoot_override if cfg.root_shoot_override is not None
        else (entry.root_shoot_ratio or root_shoot),
        carbon_fraction=cfg.carbon_fraction,
        planting_density=entry.planting_density,
        mortality_rate_y1=float(np.clip(entry.mortality_rate_y1 + mods.mortality_delta, 0.0, 0.9)),
        annual_mortality=float(np.clip(entry.annual_mortality + mods.mortality_delta / 4, 0.0, 0.3)),
        establishment_lag=cfg.establishment_lag_years if cfg.establishment_lag_years is not None
        else preset["establishment_lag_years"],
        advancement_years=advancement,
        recovery_rate=rate,
        wood_density=entry.wood_density,
        mai=entry.mai,
        is_harvested=entry.is_harvested,
        harvest_cycle_years=entry.harvest_cycle_years,
        min_harvest_age=entry.min_harvest_age,
        pct_harvested=entry.pct_harvested,
    )


def build_curve(
    cfg: CarbonCurveConfig,
    gedi_cfg: GEDIConfig,
    trend: Optional[pd.DataFrame],
    reference_year: int,
    reliable_strata: Optional[set] = None,
) -> ResolvedCurve:
    """Top-level entry point: calibrate if possible, then resolve.

    Returns a curve in every case. When GEDI is unusable the curve is still valid —
    it is simply tier ``ipcc_default``, and every downstream surface says so.
    """
    calibration = None
    if cfg.calibrate_from_gedi and trend is not None and not trend.empty:
        calibration = calibrate_from_gedi(
            trend, cfg, gedi_cfg, reference_year, reliable_strata
        )

    return resolve_curve(cfg, calibration)


# ---------------------------------------------------------------------------
# Historical scenarios (Phase 5 of the notebook)
# ---------------------------------------------------------------------------

def scenario_projection(
    trend: pd.DataFrame,
    stratum: int,
    area_ha: float,
    start_year: int,
    horizon: int,
    curve: ResolvedCurve,
    trend_method: str = "wls",
    carbon_fraction: float = 0.47,
    growth_increment: float = 6.0,
    accel_multiplier: float = 1.5,
) -> pd.DataFrame:
    """Project four capped trajectories for one stratum.

    ==================== ===================================== ==================
    Scenario             Annual AGBD rule                      Bound
    ==================== ===================================== ==================
    Baseline (BAU)       level + slope*t, with 95% CI band     [floor, ceiling]
    Conservation         level + max(slope, g)*t               <= ceiling
    Accelerated loss     level + (slope if <0 else -g)*mult*t  >= floor
    Restoration          the resolved carbon curve             [floor, ceiling]
    ==================== ===================================== ==================

    The restoration scenario is the one real departure from the notebook: instead of
    a generic Chapman-Richards shape, it uses the fully parameterised curve from this
    module, so the intervention type, species mix and site settings actually move it.
    """
    from src.estimators import agbd_to_co2e  # noqa: PLC0415 — avoids a circular import

    fit = fit_stratum_trend(trend, stratum, start_year, trend_method)
    level = fit.level if np.isfinite(fit.level) else stratum_density(trend, stratum, start_year)
    if not np.isfinite(level):
        level = 0.0

    ceiling = max(curve.ceiling_agb, level)
    floor = 0.0
    clamp = lambda v: float(min(ceiling, max(floor, v)))  # noqa: E731

    decline_rate = (fit.slope if fit.slope < 0 else -growth_increment) * accel_multiplier

    years = list(range(start_year, start_year + horizon + 1))
    rows = []
    for i, year in enumerate(years):
        bau = fit.predict(i)
        se = fit.predict_se(i)
        rows.append({
            "year": year,
            "years_elapsed": i,
            "Baseline_agbd": clamp(bau),
            "Baseline_lo_agbd": clamp(bau - 1.96 * se),
            "Baseline_hi_agbd": clamp(bau + 1.96 * se),
            "Conservation_agbd": min(ceiling, level + max(fit.slope, growth_increment) * i),
            "Accelerated_agbd": max(floor, level + decline_rate * i),
            "Restoration_agbd": clamp(level + curve.agb_at(i)),
        })

    df = pd.DataFrame(rows)
    for scenario in ("Baseline", "Conservation", "Accelerated", "Restoration"):
        df[f"{scenario}_tco2e"] = agbd_to_co2e(df[f"{scenario}_agbd"], carbon_fraction) * area_ha
        df[f"{scenario}_cum_tco2e"] = df[f"{scenario}_tco2e"] - df[f"{scenario}_tco2e"].iloc[0]

    df["Baseline_lo_tco2e"] = agbd_to_co2e(df["Baseline_lo_agbd"], carbon_fraction) * area_ha
    df["Baseline_hi_tco2e"] = agbd_to_co2e(df["Baseline_hi_agbd"], carbon_fraction) * area_ha

    df.attrs.update({
        "stratum": stratum, "area_ha": area_ha, "level": level,
        "slope": fit.slope, "slope_se": fit.slope_se, "ceiling": ceiling,
        "n_years": fit.n, "method": fit.method, "tier": curve.tier,
    })
    return df
