"""
Central parameter registry for Screening-OBIWAN.

Every value here is a *default*. The UI (``src/ui/sidebar.py``) binds each field to a
widget, so nothing in this file is baked into the analysis at runtime — the sidebar
returns a fully-populated :class:`AppConfig` that flows through the whole pipeline.

Design rule: analysis modules never read module-level globals. They take a config
object argument. That is what makes the notebook's ``TARGET_TRANSITION = 22`` style
globals safe to expose to a user.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Fixed scientific constants (IPCC). Exposed in the UI but rarely changed.
# ---------------------------------------------------------------------------

CARBON_FRACTION_DEFAULT = 0.47   # AGB dry matter -> carbon (IPCC 2006 default)
CO2_PER_C = 44.0 / 12.0          # carbon -> CO2 equivalent (stoichiometric, not a default)

# GEDI instrument coverage. The ISS orbit bounds GEDI to this latitude band; outside
# it the carbon curve must fall back to the IPCC tier (see src/carbon_curve.py).
GEDI_LAT_LIMIT = 51.6

# Hansen Global Forest Change — the ONLY forest-transition source in this app, so
# that results are globally consistent and reproducible anywhere on Earth.
HANSEN_ASSET_DEFAULT = "UMD/hansen/global_forest_change_2024_v1_12"
HANSEN_ASSET_CHOICES = [
    "UMD/hansen/global_forest_change_2024_v1_12",
    "UMD/hansen/global_forest_change_2023_v1_11",
    "UMD/hansen/global_forest_change_2022_v1_10",
]

GEDI_L4A_ASSET = "LARSE/GEDI/GEDI04_A_002_MONTHLY"

# ---------------------------------------------------------------------------
# Transition legend
# ---------------------------------------------------------------------------
# Codes match the notebook so exported rasters stay interoperable with the
# upstream Jupyter workflow.

STABLE_FOREST = 11
STABLE_NONFOREST = 22
FOREST_LOSS = 12       # forest -> non-forest
FOREST_GAIN = 21       # non-forest -> forest

CLASS_LABELS: Dict[int, str] = {
    STABLE_FOREST: "Stable forest",
    STABLE_NONFOREST: "Stable non-forest",
    FOREST_LOSS: "Forest loss (deforested)",
    FOREST_GAIN: "Forest gain (regenerating)",
}

CLASS_COLORS: Dict[int, str] = {
    STABLE_FOREST: "#1b7837",
    STABLE_NONFOREST: "#d9b365",
    FOREST_LOSS: "#d73027",
    FOREST_GAIN: "#4575b4",
}

# Reforestation focus (constraint 3): only these two classes are *plantable*.
REFORESTATION_CLASSES: Tuple[int, ...] = (STABLE_NONFOREST, FOREST_LOSS)

# Stable forest is retained solely as a reference ceiling / baseline control, and
# forest gain as the natural-regeneration calibration analogue. Neither is ever
# offered as project area.
CONTROL_CLASSES: Tuple[int, ...] = (STABLE_FOREST, FOREST_GAIN)

ALL_CLASSES: Tuple[int, ...] = REFORESTATION_CLASSES + CONTROL_CLASSES


# ---------------------------------------------------------------------------
# Module 1 — Area of interest
# ---------------------------------------------------------------------------

@dataclass
class AOIConfig:
    """Spatial extent and buffering."""

    buffer_m: float = 0.0
    """Expand (+) or shrink (-) the AOI in metres. 0 disables buffering."""

    dissolve_features: bool = True
    """True = union all features into one AOI. False = analyse a single feature."""

    target_feature_index: Optional[int] = None
    """Row index to select when ``dissolve_features`` is False. None = first feature."""

    simplify_tolerance_m: float = 0.0
    """Douglas-Peucker tolerance applied server-side. 0 = keep full vertex detail."""

    max_upload_mb: float = 1.0
    """Hard cap on uploaded AOI file size (constraint 1)."""

    max_area_ha: float = 5_000_000.0
    """Guard rail: refuse AOIs larger than this to keep Earth Engine within quota."""


# ---------------------------------------------------------------------------
# Module 2 — Hansen forest-change transitions
# ---------------------------------------------------------------------------

@dataclass
class HansenConfig:
    """Hansen Global Forest Change transition parameters."""

    asset: str = HANSEN_ASSET_DEFAULT

    treecover_min_pct: int = 25
    """Canopy cover threshold (%) in the year-2000 baseline that defines 'forest'.
    30% is the FAO convention; 10-25% suits dry forests and many national definitions."""

    t0_year: int = 2024
    """Present epoch — the end of the historical transition window."""

    epoch_gap_years: int = 10
    """Length of the transition window. T_start = t0_year - epoch_gap_years."""

    include_gain: bool = True
    """Map Hansen's 'gain' band to class 21.

    CAVEAT carried from the source notebook: Hansen's gain band covers 2000-2012 only
    and carries no year, so class 21 cannot be restricted to the analysis window. It is
    therefore used ONLY as a regeneration analogue for curve calibration, never for
    crediting. See docs in src/hansen.py."""

    analysis_scale_m: int = 30
    """Native Hansen resolution. Coarsen to 60/90 m for very large AOIs."""

    max_pixels: float = 1e10
    best_effort: bool = True
    tile_scale: int = 4

    @property
    def t_start_year(self) -> int:
        return self.t0_year - self.epoch_gap_years


# ---------------------------------------------------------------------------
# Module 3 — GEDI biomass sampling
# ---------------------------------------------------------------------------

@dataclass
class GEDIConfig:
    """GEDI L4A footprint extraction and quality screening."""

    enabled: bool = True
    """Master switch. Disabling forces the IPCC tier for the carbon curve."""

    asset: str = GEDI_L4A_ASSET

    start_year: int = 2019
    end_year: int = 2024
    """GEDI L4A record. There is no GEDI before 2019 — pre-2019 biomass is never inferred."""

    sensitivity_min: float = 0.95
    """Beam sensitivity threshold. 0.95 is the standard L4A screening for dense canopy;
    relax to 0.90 in open/dry systems to retain footprints."""

    quality_flag_required: bool = True
    degrade_flag_excluded: bool = True

    scale_m: int = 25
    """L4A footprint grid."""

    max_footprints: int = 50_000
    """Client-pull guard. Above this the app warns and recommends coarsening the AOI."""

    min_footprints_per_stratum: int = 30
    """Below this count a stratum's design-based estimate is flagged unreliable and the
    carbon curve degrades to the IPCC tier for that stratum."""

    @property
    def years(self) -> List[int]:
        return list(range(self.start_year, self.end_year + 1))


# ---------------------------------------------------------------------------
# Module 4 — Trend / change / additionality windows
# ---------------------------------------------------------------------------

@dataclass
class TrendConfig:
    """Temporal design for the design-based estimators."""

    change_years: Tuple[int, int] = (2019, 2024)
    """(y0, y1) for the mean-difference change estimate."""

    baseline_period: Tuple[int, int] = (2019, 2021)
    project_period: Tuple[int, int] = (2021, 2024)
    """Additionality = project annual rate - baseline annual rate."""

    trend_method: str = "wls"
    """'wls' (inverse-variance weighted), 'ols', or 'theilsen' (robust to outlier years)."""

    confidence_level: float = 0.95


# ---------------------------------------------------------------------------
# Module 5 — Carbon accumulation curve (Phase 7)
# ---------------------------------------------------------------------------

@dataclass
class SpeciesMixEntry:
    """One component of a planting mix. Area fractions across a mix should sum to 1."""

    name: str = "Native mixed broadleaf"
    area_fraction: float = 1.0
    growth_model: str = "logistic"       # logistic | recovery_curve | MAI | linear_dbh | lookup
    agb_max: float = 200.0               # t d.m./ha asymptote
    k_growth: float = 0.18               # logistic steepness (1/yr)
    age_inflection: float = 12.0         # yr at which growth peaks
    wood_density: float = 0.55           # g/cm3
    root_shoot_ratio: float = 0.27
    planting_density: int = 1100         # stems/ha (Active Planting only)
    mortality_rate_y1: float = 0.15
    annual_mortality: float = 0.02
    is_harvested: bool = False
    harvest_cycle_years: int = 30
    min_harvest_age: int = 15
    pct_harvested: float = 0.0
    mai: float = 0.0                     # mean annual increment, m3/ha/yr (MAI model)


@dataclass
class CarbonCurveConfig:
    """Parameterisation of the carbon accumulation curve — constraint 4.

    The shape and ceiling of the curve are set by five independent levers:
      1. intervention type   -> which growth model and establishment lag apply
      2. ecological zone     -> AGB ceiling, baseline growth rate, root:shoot
      3. site index          -> proportional modifier on growth rate and ceiling
      4. soil quality        -> proportional modifier, plus SOC accrual if enabled
      5. species mix         -> per-cohort allometry, weighted by area fraction

    Modifiers are multiplicative and applied in src/carbon_curve.py::apply_modifiers.
    """

    # --- 1. Intervention ---
    intervention_type: str = "active_planting"
    """'active_planting' | 'anr' | 'mixed'. See config/species.py::INTERVENTION_PRESETS."""

    anr_area_fraction: float = 0.0
    """Share of project area under ANR when intervention_type == 'mixed'."""

    establishment_lag_years: float = 1.0
    """Years before measurable AGB accrual. Planting ~1 yr; ANR ~0 (rootstock present)."""

    advancement_years: float = 0.0
    """ANR head start: existing rootstock/seed bank equivalent to this many years of
    growth at t=0. Higher for recently cleared land with live stumps."""

    # --- 2. Ecological zone ---
    ecological_zone: str = "tropical_moist_deciduous"
    """Key into config/ecological_zones.py::ECOLOGICAL_ZONES."""

    auto_detect_zone: bool = True
    """Infer the zone from AOI latitude + Hansen tree cover as a starting point."""

    # --- 3. Site & climate ---
    site_index: float = 1.0
    """Productivity multiplier, 0.5 (poor) - 1.5 (exceptional). 1.0 = zone average."""

    soil_quality: str = "moderate"
    """'degraded' | 'moderate' | 'good'. Modifies growth rate and the AGB ceiling."""

    water_stress: str = "none"
    """'none' | 'seasonal' | 'severe'. Damps growth rate and lowers the ceiling."""

    # --- 4. Disturbance risk ---
    fire_risk: str = "low"
    """'low' | 'moderate' | 'high'. Raises annual mortality."""

    grazing_pressure: str = "none"
    """'none' | 'moderate' | 'high'. Raises establishment mortality."""

    # --- 5. Species mix ---
    species_mix: List[SpeciesMixEntry] = field(default_factory=lambda: [SpeciesMixEntry()])

    # --- Chemistry ---
    carbon_fraction: float = CARBON_FRACTION_DEFAULT
    root_shoot_override: Optional[float] = None
    """None = use the ecological zone's root:shoot ratio."""

    # --- Calibration ---
    calibrate_from_gedi: bool = True
    """Fit the curve's rate/ceiling to this AOI's own GEDI record where possible."""

    calibration_class: int = FOREST_GAIN
    """Stratum whose measured trend calibrates the recovery rate (regenerating land)."""

    reference_class: int = STABLE_FOREST
    """Stratum whose mean AGBD sets the mature-forest ceiling."""

    conservative_haircut: float = 0.0
    """Extra proportional reduction on the calibrated curve, 0-0.5. VM0047 deductions
    already impose conservativeness, so 0 is the honest default."""


# ---------------------------------------------------------------------------
# Module 6 — VM0047 project & deductions
# ---------------------------------------------------------------------------

@dataclass
class PoolConfig:
    """Carbon pools included, per VM0047 Table 3."""

    ag_woody_biomass: bool = True
    bg_woody_biomass: bool = True
    ag_nonwoody_biomass: bool = False
    bg_nonwoody_biomass: bool = False
    deadwood: bool = False
    litter: bool = False
    soc: bool = False
    hwp: bool = False

    deadwood_factor: float = 0.01     # fraction of AGB
    litter_factor: float = 0.01       # fraction of AGB
    soc_rate: float = 0.0             # tCO2e/ha/yr accrual when soc is enabled


@dataclass
class DeductionConfig:
    """VM0047 Eq. 30 deductions."""

    uncertainty_pct: float = 0.10
    """Ex-ante minimum is 10%. The app raises this automatically when GEDI sample
    sizes are thin — see src/vm0047.py::uncertainty_from_sample."""

    performance_benchmark_pct: float = 0.03
    """Dynamic Performance Benchmark deduction."""

    non_permanence_risk: float = 0.15
    """AFOLU non-permanence buffer contribution."""

    leakage_pct: float = 0.0
    leakage_years: int = 40

    pe_fertilizer: float = 0.0        # tCO2e/ha/yr
    pe_fossil_fuel: float = 0.0
    pe_burning: float = 0.0


@dataclass
class ProjectConfigParams:
    """Project framing for the ex-ante run."""

    project_name: str = "ARR Screening"
    start_year: int = 2025
    crediting_period_years: int = 40

    project_class: int = STABLE_NONFOREST
    """Which reforestation stratum is the project area. Must be in REFORESTATION_CLASSES."""

    area_override_ha: Optional[float] = None
    """Override the Hansen-derived stratum area. None = use the measured area."""

    plantable_fraction: float = 0.85
    """Share of the stratum that is realistically plantable after excluding
    infrastructure, water, rock, and access constraints."""

    phased_planting: bool = False
    planting_years: int = 3
    """Spread establishment over this many years when phased_planting is True."""

    baseline_rate_override: Optional[float] = None
    """tCO2e/ha/yr. None = fit from the project stratum's own GEDI trend, clamped >= 0."""


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------

@dataclass
class AppConfig:
    """The single object threaded through every analysis module."""

    aoi: AOIConfig = field(default_factory=AOIConfig)
    hansen: HansenConfig = field(default_factory=HansenConfig)
    gedi: GEDIConfig = field(default_factory=GEDIConfig)
    trend: TrendConfig = field(default_factory=TrendConfig)
    curve: CarbonCurveConfig = field(default_factory=CarbonCurveConfig)
    pools: PoolConfig = field(default_factory=PoolConfig)
    deductions: DeductionConfig = field(default_factory=DeductionConfig)
    project: ProjectConfigParams = field(default_factory=ProjectConfigParams)

    ee_project_id: str = ""
    """User's Earth Engine Cloud project. Supplied at login, never hardcoded."""

    def to_dict(self) -> dict:
        return asdict(self)

    def validate(self) -> List[str]:
        """Return a list of human-readable problems. Empty list = config is usable."""
        errors: List[str] = []

        if self.project.project_class not in REFORESTATION_CLASSES:
            errors.append(
                f"Project class must be a reforestation class "
                f"({', '.join(CLASS_LABELS[c] for c in REFORESTATION_CLASSES)}), "
                f"not {CLASS_LABELS.get(self.project.project_class, self.project.project_class)}."
            )

        if self.hansen.t_start_year < 2000:
            errors.append(
                f"Transition window starts in {self.hansen.t_start_year}, but Hansen's "
                "baseline is the year 2000. Reduce the epoch gap or raise T0."
            )

        if self.hansen.t0_year < self.hansen.t_start_year:
            errors.append("T0 must be later than the transition window start.")

        if self.gedi.enabled and self.gedi.end_year < self.gedi.start_year:
            errors.append("GEDI end year precedes start year.")

        if self.gedi.enabled and self.gedi.start_year < 2019:
            errors.append("GEDI L4A begins in 2019; earlier years contain no data.")

        y0, y1 = self.trend.change_years
        if y1 <= y0:
            errors.append("Change window end year must be after its start year.")

        mix_total = sum(s.area_fraction for s in self.curve.species_mix)
        if self.curve.species_mix and abs(mix_total - 1.0) > 0.01:
            errors.append(f"Species mix area fractions sum to {mix_total:.2f}, expected 1.00.")

        if not 0.0 <= self.curve.conservative_haircut < 1.0:
            errors.append("Conservative haircut must be in [0, 1).")

        if not 0.0 < self.project.plantable_fraction <= 1.0:
            errors.append("Plantable fraction must be in (0, 1].")

        return errors
