"""
Phase sequencing.

One function runs the whole analysis and returns one object. Keeping orchestration
here rather than in ``app.py`` means the pipeline can be driven from a script, a
notebook or a test without Streamlit — and ``app.py`` stays a thin presentation layer.

Phases
------
1. Hansen transitions and per-class areas
2. GEDI footprint extraction and stratification  (skipped outside coverage)
3. Design-based annual estimates per stratum
4. Change, additionality and stock tables
5. Historical scenario projection
6. Carbon curve resolution (calibrated or IPCC-tier)
7. VM0047 ex-ante quantification and sensitivity scenarios
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import pandas as pd

from config.defaults import AppConfig
from config.ecological_zones import infer_zone
from src import carbon_curve, estimators, gedi, hansen, vm0047
from src.aoi import AOI
from src.carbon_curve import ResolvedCurve
from src.gedi import GEDIResult
from src.hansen import TransitionResult
from src.vm0047 import EngineResults

logger = logging.getLogger(__name__)

ProgressFn = Optional[Callable[[str, float], None]]


@dataclass
class AnalysisResult:
    """Everything the UI needs from one run."""

    aoi: AOI
    config: AppConfig

    transitions: TransitionResult
    gedi_result: GEDIResult

    trend: pd.DataFrame = field(default_factory=pd.DataFrame)
    change: pd.DataFrame = field(default_factory=pd.DataFrame)
    additionality: pd.DataFrame = field(default_factory=pd.DataFrame)
    stock: pd.DataFrame = field(default_factory=pd.DataFrame)
    projection: pd.DataFrame = field(default_factory=pd.DataFrame)

    curve: Optional[ResolvedCurve] = None
    engine: Optional[EngineResults] = None
    scenarios: Dict[str, EngineResults] = field(default_factory=dict)

    baseline_rate: float = 0.0
    baseline_note: str = ""
    suggested_zone: str = ""
    warnings: List[str] = field(default_factory=list)

    @property
    def has_quantification(self) -> bool:
        return self.engine is not None

    @property
    def tier(self) -> str:
        return self.curve.tier if self.curve else carbon_curve.TIER_IPCC

    def tables(self) -> Dict[str, pd.DataFrame]:
        """Named tables for the CSV bundle."""
        out = {
            "land_cover_transitions": pd.DataFrame(self.transitions.summary_rows()),
            "gedi_annual_trend": self.trend,
            "biomass_change": self.change,
            "historical_additionality": self.additionality,
            "carbon_stock": self.stock,
            "scenario_projection": self.projection,
        }
        if self.curve:
            out["carbon_curve"] = self.curve.to_frame(
                self.config.project.crediting_period_years
            )
        if self.engine:
            out["er_projections"] = self.engine.annual
        if self.scenarios:
            out["scenario_comparison"] = vm0047.scenario_comparison(self.scenarios)
        return {k: v for k, v in out.items() if v is not None and not v.empty}


def run(config: AppConfig, aoi: AOI, progress: ProgressFn = None) -> AnalysisResult:
    """Execute every phase. Individual phases degrade rather than abort.

    A thin GEDI record or an out-of-coverage AOI weakens the evidence tier; it does
    not stop the run. The only hard failure is Hansen returning nothing, which means
    the AOI itself is unusable.
    """
    def step(label: str, fraction: float) -> None:
        logger.info("[%3.0f%%] %s", 100 * fraction, label)
        if progress:
            progress(label, fraction)

    warnings: List[str] = []

    # --- Phase 1: Hansen ---------------------------------------------------
    step("Reading Hansen forest-change transitions…", 0.10)
    transitions = hansen.analyze(aoi.geometry, config.hansen)
    warnings.extend(transitions.warnings)

    suggested_zone = infer_zone(aoi.latitude, transitions.mean_treecover_pct)

    # --- Phase 2: GEDI -----------------------------------------------------
    step("Sampling GEDI biomass footprints…", 0.25)

    def gedi_progress(year: int, index: int, total: int) -> None:
        step(f"Sampling GEDI {year}…", 0.25 + 0.30 * (index / max(total, 1)))

    gedi_result = gedi.extract(
        aoi.geometry,
        transitions.image,
        config.gedi,
        lat_bounds=(aoi.bounds[1], aoi.bounds[3]),
        progress_callback=gedi_progress,
    )
    warnings.extend(gedi_result.warnings)

    result = AnalysisResult(
        aoi=aoi, config=config,
        transitions=transitions, gedi_result=gedi_result,
        suggested_zone=suggested_zone, warnings=warnings,
    )

    # --- Phases 3-5: design-based statistics -------------------------------
    if gedi_result.available and not gedi_result.footprints.empty:
        step("Computing design-based estimates…", 0.60)
        result.trend = estimators.annual_stratum_table(
            gedi_result.footprints, config.curve.carbon_fraction
        )
        result.change = estimators.change_table(
            gedi_result.footprints, config.trend.change_years, config.curve.carbon_fraction
        )
        result.additionality = estimators.additionality_table(
            gedi_result.footprints, config.trend, config.curve.carbon_fraction
        )
        result.stock = estimators.carbon_stock_table(
            result.trend, transitions.areas_ha, config.gedi.end_year
        )

    # --- Phase 6: carbon curve ---------------------------------------------
    step("Building the carbon accumulation curve…", 0.72)
    reliable = {
        code for code in gedi_result.counts_by_stratum
        if gedi_result.is_reliable(code)
    }
    curve = carbon_curve.build_curve(
        config.curve, config.gedi,
        result.trend if not result.trend.empty else None,
        reference_year=config.gedi.end_year,
        reliable_strata=reliable or None,
    )
    result.curve = curve

    if curve.calibration and curve.calibration.message:
        logger.info(curve.calibration.message)

    # --- Phase 5b: historical scenarios ------------------------------------
    project_class = config.project.project_class
    if not result.trend.empty and project_class in result.trend["stratum"].values:
        step("Projecting historical scenarios…", 0.80)
        result.projection = carbon_curve.scenario_projection(
            result.trend,
            stratum=project_class,
            area_ha=transitions.area_of(project_class),
            start_year=config.gedi.end_year,
            horizon=min(30, config.project.crediting_period_years),
            curve=curve,
            trend_method=config.trend.trend_method,
            carbon_fraction=config.curve.carbon_fraction,
        )

    # --- Phase 7: VM0047 ---------------------------------------------------
    step("Running VM0047 quantification…", 0.88)

    baseline_rate, baseline_note = vm0047.derive_baseline_rate(
        result.trend if not result.trend.empty else None,
        project_class,
        config.gedi.end_year,
        config.trend.trend_method,
        config.curve.carbon_fraction,
        config.project.baseline_rate_override,
    )
    result.baseline_rate = baseline_rate
    result.baseline_note = baseline_note

    setup = vm0047.build_setup(
        curve=curve,
        project=config.project,
        pools=config.pools,
        deductions=config.deductions,
        stratum_area_ha=transitions.area_of(project_class),
        baseline_rate_tco2e_ha_yr=baseline_rate,
        n_footprints=gedi_result.counts_by_stratum.get(project_class, 0),
        min_footprints=config.gedi.min_footprints_per_stratum,
    )

    if setup.area_ha <= 0:
        warnings.append(
            f"The project stratum ({config.project.project_class}) has no area in this "
            "AOI, so no quantification was run. Choose the other reforestation class, "
            "or check the canopy threshold."
        )
        result.warnings = warnings
        step("Done.", 1.0)
        return result

    result.engine = vm0047.VM0047Engine(setup).run()

    step("Running sensitivity scenarios…", 0.95)
    result.scenarios = vm0047.run_scenarios(setup)

    result.warnings = warnings
    step("Done.", 1.0)
    return result
