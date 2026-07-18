"""
VM0047 ex-ante quantification engine (Area-based approach).

Ported from the source notebook's ``VM0047Engine`` with three substantive changes:

1. **The carbon curve is injected, not hardcoded.** The engine takes a
   :class:`~src.carbon_curve.ResolvedCurve`, so intervention type, species mix,
   ecological zone and site conditions all reach the numbers. The notebook allowed
   exactly one curve shape.
2. **Uncertainty responds to the evidence.** VM0047 sets a 10% ex-ante floor; this
   engine raises it when the underlying GEDI sample is thin or absent, because
   claiming 10% uncertainty on an IPCC-default curve with no local measurement is not
   defensible. See :func:`uncertainty_from_evidence`.
3. **Phased planting is supported.** Cohorts age independently, so a three-year
   establishment schedule produces the staggered accrual it should.

Deduction chain (VM0047 Eq. 30 and related)
-------------------------------------------
::

    delta_C_WP        stock change with project, this year
  - baseline removals what the land would have done anyway
  = gross removals    (floored at zero)
  - project emissions fertiliser + fossil fuel + burning, per ha
  - leakage           activity shifting
  - PB deduction      dynamic performance benchmark
  - uncertainty       UNC_t
  = net before buffer (floored at zero)
  - buffer            non-permanence risk contribution
  = NET ERs / VCUs
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from config.defaults import (
    DeductionConfig,
    PoolConfig,
    ProjectConfigParams,
)
from src.carbon_curve import TIER_GEDI, TIER_IPCC, TIER_PARTIAL, ResolvedCurve

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Evidence-aware uncertainty
# ---------------------------------------------------------------------------

def uncertainty_from_evidence(
    base_pct: float,
    tier: str,
    n_footprints: int = 0,
    min_footprints: int = 30,
) -> tuple[float, str]:
    """Raise the uncertainty deduction when the evidence is thin.

    VM0047 sets 10% as an ex-ante *minimum*, not a default to apply regardless of
    what the estimate rests on. A curve built entirely from IPCC zone averages, with
    no site measurement, carries far more than 10% uncertainty, and quoting 10%
    would overstate the credit volume a screening exercise should promise.

    The escalation is deliberately conservative and transparent rather than derived
    from a formal error propagation — a screening tool should be honest that it is
    applying a judgement-based penalty, not pretend to a rigour it does not have.

    Returns
    -------
    (uncertainty_fraction, explanation)
    """
    if tier == TIER_GEDI and n_footprints >= min_footprints * 3:
        return base_pct, (
            f"{100 * base_pct:.0f}% — the VM0047 ex-ante minimum. The curve is "
            f"calibrated to {n_footprints:,} local GEDI footprints."
        )

    if tier == TIER_GEDI:
        adjusted = max(base_pct, 0.15)
        return adjusted, (
            f"{100 * adjusted:.0f}% — raised from {100 * base_pct:.0f}% because the "
            f"GEDI sample is small ({n_footprints:,} footprints)."
        )

    if tier == TIER_PARTIAL:
        adjusted = max(base_pct, 0.20)
        return adjusted, (
            f"{100 * adjusted:.0f}% — raised from {100 * base_pct:.0f}% because only "
            "part of the curve is calibrated to local measurement."
        )

    adjusted = max(base_pct, 0.30)
    return adjusted, (
        f"{100 * adjusted:.0f}% — raised from {100 * base_pct:.0f}% because the curve "
        "rests entirely on IPCC ecological-zone defaults with no site measurement. "
        "Field inventory or local allometry would reduce this substantially."
    )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

@dataclass
class ProjectSetup:
    """Everything the engine needs, assembled from the UI config and the curve."""

    curve: ResolvedCurve
    project: ProjectConfigParams
    pools: PoolConfig
    deductions: DeductionConfig
    area_ha: float
    baseline_rate_tco2e_ha_yr: float
    planting_schedule: Dict[int, float] = field(default_factory=dict)
    """``{year_offset: hectares established}``. Ages are tracked per cohort."""

    uncertainty_pct: float = 0.10
    uncertainty_note: str = ""

    def __post_init__(self) -> None:
        if not self.planting_schedule:
            self.planting_schedule = self._default_schedule()

    def _default_schedule(self) -> Dict[int, float]:
        if not self.project.phased_planting or self.project.planting_years <= 1:
            return {0: self.area_ha}
        n = int(self.project.planting_years)
        per_year = self.area_ha / n
        return {i: per_year for i in range(n)}

    def area_at(self, year_offset: int) -> float:
        """Cumulative hectares established by a given project year."""
        return sum(ha for yr, ha in self.planting_schedule.items() if yr <= year_offset)


@dataclass
class EngineResults:
    """Annual results plus a headline summary."""

    annual: pd.DataFrame
    summary: Dict[str, float]
    setup: ProjectSetup
    cohorts: List[dict] = field(default_factory=list)

    @property
    def total_net_ers(self) -> float:
        return float(self.summary.get("total_net_ers", 0.0))

    @property
    def er_per_ha_per_year(self) -> float:
        return float(self.summary.get("er_per_ha_per_yr", 0.0))


class VM0047Engine:
    """Area-based ex-ante quantification for ARR projects."""

    def __init__(self, setup: ProjectSetup) -> None:
        self.setup = setup

    def _stock_per_ha(self, age: int) -> Dict[str, float]:
        """Carbon stock (tCO2e/ha) at a stand age, across all enabled pools."""
        stocks = self.setup.curve.carbon_at(age)
        pools = self.setup.pools

        agb = stocks["agb_co2"] if pools.ag_woody_biomass else 0.0
        bgb = stocks["bgb_co2"] if pools.bg_woody_biomass else 0.0
        deadwood = agb * pools.deadwood_factor if pools.deadwood else 0.0
        litter = agb * pools.litter_factor if pools.litter else 0.0
        soc = pools.soc_rate * age if pools.soc else 0.0

        return {
            "agb_co2": agb, "bgb_co2": bgb,
            "deadwood_co2": deadwood, "litter_co2": litter, "soc_co2": soc,
            "total": agb + bgb + deadwood + litter + soc,
        }

    def run(self) -> EngineResults:
        setup = self.setup
        deductions = setup.deductions
        project = setup.project

        rows: List[dict] = []
        cohort_log: List[dict] = []
        previous_stock = 0.0

        for t in range(project.crediting_period_years + 1):
            calendar_year = project.start_year + t
            total_stock = 0.0

            for planting_offset, hectares in sorted(setup.planting_schedule.items()):
                age = t - planting_offset
                # age < 0 means not yet established. age == 0 IS included, so ANR land
                # carrying pre-existing biomass is counted in the stock baseline —
                # but because credits come from the *year-on-year delta*, that stock
                # is never itself credited.
                if age < 0 or hectares <= 0:
                    continue

                per_ha = self._stock_per_ha(age)
                cohort_stock = per_ha["total"] * hectares
                total_stock += cohort_stock

                if t in (0, 1, 5, 10, 20, project.crediting_period_years):
                    cohort_log.append({
                        "project_year": t, "calendar_year": calendar_year,
                        "cohort": planting_offset, "age": age, "area_ha": hectares,
                        "stock_per_ha": per_ha["total"], "cohort_total_tco2e": cohort_stock,
                    })

            delta_c_wp = total_stock - previous_stock if t > 0 else 0.0
            previous_stock = total_stock

            cumulative_area = setup.area_at(t)
            baseline_removals = setup.baseline_rate_tco2e_ha_yr * cumulative_area
            gross_removals = max(0.0, delta_c_wp - baseline_removals)

            project_emissions = (
                deductions.pe_fertilizer + deductions.pe_fossil_fuel + deductions.pe_burning
            ) * cumulative_area

            leakage = (
                gross_removals * deductions.leakage_pct
                if deductions.leakage_years == 0 or t <= deductions.leakage_years
                else 0.0
            )

            pb_deduction = gross_removals * deductions.performance_benchmark_pct
            uncertainty_deduction = gross_removals * setup.uncertainty_pct

            net_before_buffer = max(0.0, (
                gross_removals - project_emissions - leakage
                - pb_deduction - uncertainty_deduction
            ))
            buffer = net_before_buffer * deductions.non_permanence_risk
            net_ers = net_before_buffer - buffer

            rows.append({
                "project_year": t,
                "calendar_year": calendar_year,
                "cumulative_area_ha": cumulative_area,
                "total_stock_tco2e": total_stock,
                "stock_per_ha_tco2e": total_stock / cumulative_area if cumulative_area else 0.0,
                "delta_c_wp_tco2e": delta_c_wp,
                "baseline_removals_tco2e": baseline_removals,
                "gross_removals_tco2e": gross_removals,
                "project_emissions_tco2e": project_emissions,
                "leakage_tco2e": leakage,
                "pb_deduction_tco2e": pb_deduction,
                "uncertainty_deduction_tco2e": uncertainty_deduction,
                "net_before_buffer_tco2e": net_before_buffer,
                "buffer_tco2e": buffer,
                "net_ers_tco2e": net_ers,
                "er_per_ha_yr": net_ers / cumulative_area if cumulative_area else 0.0,
            })

        annual = pd.DataFrame(rows)
        annual["cumulative_net_ers_tco2e"] = annual["net_ers_tco2e"].cumsum()

        return EngineResults(annual, self._summarize(annual), setup, cohort_log)

    def _summarize(self, annual: pd.DataFrame) -> Dict[str, float]:
        project = self.setup.project
        total_gross = float(annual["gross_removals_tco2e"].sum())
        total_net = float(annual["net_ers_tco2e"].sum())
        max_area = float(annual["cumulative_area_ha"].max())
        active_years = int((annual["net_ers_tco2e"] > 0).sum())

        return {
            "project_name": project.project_name,
            "total_area_ha": max_area,
            "crediting_period": project.crediting_period_years,
            "total_gross_removals": total_gross,
            "total_net_ers": total_net,
            "total_project_emissions": float(annual["project_emissions_tco2e"].sum()),
            "total_leakage": float(annual["leakage_tco2e"].sum()),
            "total_uncertainty_ded": float(annual["uncertainty_deduction_tco2e"].sum()),
            "total_pb_ded": float(annual["pb_deduction_tco2e"].sum()),
            "total_buffer": float(annual["buffer_tco2e"].sum()),
            "avg_annual_ers": total_net / active_years if active_years else 0.0,
            "er_per_ha_per_yr": (
                total_net / (max_area * project.crediting_period_years)
                if max_area > 0 and project.crediting_period_years > 0 else 0.0
            ),
            "deduction_pct": (1 - total_net / total_gross) * 100 if total_gross > 0 else 0.0,
            "peak_annual_ers": float(annual["net_ers_tco2e"].max()),
            "peak_year": int(annual.loc[annual["net_ers_tco2e"].idxmax(), "calendar_year"])
            if len(annual) else 0,
        }


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def build_setup(
    curve: ResolvedCurve,
    project: ProjectConfigParams,
    pools: PoolConfig,
    deductions: DeductionConfig,
    stratum_area_ha: float,
    baseline_rate_tco2e_ha_yr: float = 0.0,
    n_footprints: int = 0,
    min_footprints: int = 30,
) -> ProjectSetup:
    """Assemble a :class:`ProjectSetup`, applying the plantable fraction and the
    evidence-aware uncertainty escalation."""
    area = project.area_override_ha if project.area_override_ha is not None else stratum_area_ha
    area = max(0.0, area * project.plantable_fraction)

    uncertainty, note = uncertainty_from_evidence(
        deductions.uncertainty_pct, curve.tier, n_footprints, min_footprints
    )

    return ProjectSetup(
        curve=curve,
        project=project,
        pools=pools,
        deductions=deductions,
        area_ha=area,
        baseline_rate_tco2e_ha_yr=max(0.0, baseline_rate_tco2e_ha_yr),
        uncertainty_pct=uncertainty,
        uncertainty_note=note,
    )


def derive_baseline_rate(
    trend: Optional[pd.DataFrame],
    stratum: int,
    start_year: int,
    trend_method: str,
    carbon_fraction: float,
    override: Optional[float] = None,
) -> tuple[float, str]:
    """Baseline removal rate (tCO2e/ha/yr) for the project stratum.

    Fit to the stratum's *own* historical GEDI trend and clamped at >= 0: if the
    "stable non-forest" land is already gaining biomass through woody encroachment,
    that gain is not additional and must be netted off the project's gross removals
    every year. Clamping at zero means the baseline never *adds* credits, only
    subtracts — the conservative direction, as VM0047 requires.
    """
    if override is not None:
        return max(0.0, override), f"Baseline rate set manually to {override:.3f} tCO2e/ha/yr."

    if trend is None or trend.empty:
        return 0.0, (
            "No GEDI trend for the project stratum — baseline removals assumed zero. "
            "This is the least conservative assumption available; verify with field "
            "data or historical imagery before relying on the credit volume."
        )

    from src.estimators import agbd_to_co2e, fit_stratum_trend  # noqa: PLC0415

    fit = fit_stratum_trend(trend, stratum, start_year, trend_method)
    if not np.isfinite(fit.slope) or fit.n < 2:
        return 0.0, "Insufficient GEDI years to fit a baseline trend; assumed zero."

    rate = float(agbd_to_co2e(fit.slope, carbon_fraction))
    if rate <= 0:
        return 0.0, (
            f"The project stratum shows a flat or declining biomass trend "
            f"({fit.slope:+.3f} Mg/ha/yr over {fit.n} years), so baseline removals are "
            "zero. All project accrual counts as additional."
        )

    return rate, (
        f"Baseline removals of {rate:.3f} tCO2e/ha/yr, from the project stratum's own "
        f"{fit.method.upper()} trend ({fit.slope:+.3f} Mg/ha/yr over {fit.n} years). "
        "This pre-existing gain is deducted annually as non-additional."
    )


def run_scenarios(setup: ProjectSetup) -> Dict[str, EngineResults]:
    """Base / Conservative / Optimistic sensitivity runs.

    The spread is a sensitivity band, not a probability distribution — it shows how
    much the headline number moves under plausible parameter choices, which for a
    screening tool matters more than a single point estimate.
    """
    import copy  # noqa: PLC0415

    results: Dict[str, EngineResults] = {"Base case": VM0047Engine(setup).run()}

    conservative = copy.deepcopy(setup)
    conservative.uncertainty_pct = min(0.40, conservative.uncertainty_pct * 1.5)
    conservative.deductions.non_permanence_risk = min(0.35, conservative.deductions.non_permanence_risk * 1.3)
    conservative.deductions.leakage_pct = max(conservative.deductions.leakage_pct, 0.05)
    for species in conservative.curve.species:
        species.recovery_rate *= 0.75
        species.k_growth *= 0.75
        species.agb_max *= 0.90
    results["Conservative"] = VM0047Engine(conservative).run()

    optimistic = copy.deepcopy(setup)
    optimistic.uncertainty_pct = max(0.05, optimistic.uncertainty_pct * 0.6)
    optimistic.deductions.non_permanence_risk *= 0.7
    optimistic.deductions.leakage_pct = 0.0
    for species in optimistic.curve.species:
        species.recovery_rate *= 1.15
        species.k_growth *= 1.15
    results["Optimistic"] = VM0047Engine(optimistic).run()

    return results


def scenario_comparison(scenarios: Dict[str, EngineResults]) -> pd.DataFrame:
    """Side-by-side metrics for the scenario table."""
    metrics = [
        ("Total area (ha)", "total_area_ha", 0),
        ("Total gross removals (tCO2e)", "total_gross_removals", 0),
        ("Total net ERs (tCO2e)", "total_net_ers", 0),
        ("Average annual ERs (tCO2e/yr)", "avg_annual_ers", 0),
        ("ER per ha per year", "er_per_ha_per_yr", 3),
        ("Total deduction rate (%)", "deduction_pct", 1),
    ]
    rows = []
    for label, key, places in metrics:
        row = {"Metric": label}
        for name, result in scenarios.items():
            row[name] = round(float(result.summary.get(key, 0.0)), places)
        rows.append(row)
    return pd.DataFrame(rows)
