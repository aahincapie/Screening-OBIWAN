"""
Sidebar — every analysis parameter, bound to a widget.

Constraint 2: all configuration modules are dynamic user inputs. This module is the
single place widgets are declared, and it returns a fully-populated
:class:`~config.defaults.AppConfig`. No analysis module reads Streamlit state.

Organising principle: **progressive disclosure**. Six of the sixty-odd parameters
control the shape of the answer; the rest are refinements. The six live at the top
level, everything else sits behind an expander that is closed by default. A user who
opens the app should be able to get a defensible result without touching an expander,
and a user who needs to justify every number to a validator should be able to reach
every one of them.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import streamlit as st

from config.defaults import (
    CLASS_LABELS,
    HANSEN_ASSET_CHOICES,
    REFORESTATION_CLASSES,
    AOIConfig,
    AppConfig,
    CarbonCurveConfig,
    DeductionConfig,
    GEDIConfig,
    HansenConfig,
    PoolConfig,
    ProjectConfigParams,
    SpeciesMixEntry,
    TrendConfig,
)
from config.ecological_zones import zone_choices
from config.species import (
    INTERVENTION_PRESETS,
    default_mix_for,
    intervention_choices,
    make_species_entry,
    species_choices,
)


def _help(text: str) -> str:
    return text


def render(suggested_zone: Optional[str] = None) -> AppConfig:
    """Draw the whole sidebar and return the resulting configuration.

    Parameters
    ----------
    suggested_zone
        Zone key inferred from the AOI. Pre-selects the ecological zone the first
        time an AOI is loaded, without overriding a choice the user already made.
    """
    st.sidebar.markdown("## Configuration")

    aoi = _aoi_section()
    hansen = _hansen_section()
    project, curve_partial = _project_section()
    curve = _curve_section(suggested_zone, curve_partial)
    gedi, trend = _biomass_section(hansen.t0_year)
    pools, deductions = _accounting_section()

    return AppConfig(
        aoi=aoi,
        hansen=hansen,
        gedi=gedi,
        trend=trend,
        curve=curve,
        pools=pools,
        deductions=deductions,
        project=project,
        ee_project_id=st.session_state.get("ee_project_id", ""),
    )


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _aoi_section() -> AOIConfig:
    st.sidebar.markdown("### Area of interest")

    buffer_m = st.sidebar.slider(
        "Boundary buffer (m)", -500, 2000, 0, step=10,
        help=_help(
            "Expand or shrink the uploaded boundary. Positive values include a margin "
            "around the parcel; negative values pull the edge in, which is useful for "
            "excluding boundary effects along roads and rivers."
        ),
    )

    with st.sidebar.expander("Advanced AOI settings"):
        dissolve = st.checkbox(
            "Merge all features into one AOI", value=True,
            help="Off analyses a single feature — pick which one below.",
        )
        target_index = None
        if not dissolve:
            target_index = st.number_input(
                "Feature index", min_value=0, value=0, step=1,
                help="Zero-based row index in the uploaded file.",
            )
        simplify = st.slider(
            "Simplify tolerance (m)", 0, 500, 0, step=10,
            help=(
                "Reduce vertex count before sending to Earth Engine. Highly detailed "
                "boundaries slow processing considerably; 30-50 m rarely changes the "
                "area by more than a fraction of a percent."
            ),
        )
        max_area = st.number_input(
            "Maximum AOI area (ha)", min_value=1_000.0, value=5_000_000.0, step=100_000.0,
            help="Guard against requests that will time out in Earth Engine.",
        )

    return AOIConfig(
        buffer_m=float(buffer_m),
        dissolve_features=dissolve,
        target_feature_index=int(target_index) if target_index is not None else None,
        simplify_tolerance_m=float(simplify),
        max_area_ha=float(max_area),
    )


def _hansen_section() -> HansenConfig:
    st.sidebar.markdown("### Forest change (Hansen)")

    treecover = st.sidebar.slider(
        "Canopy cover threshold (%)", 5, 75, 25, step=5,
        help=_help(
            "Minimum year-2000 canopy cover for a pixel to count as forest. 30% is the "
            "FAO convention; 10-25% suits dry forests and savannas. A lower threshold "
            "classifies more land as forest, shrinking the plantable area."
        ),
    )

    col_a, col_b = st.sidebar.columns(2)
    t0_year = col_a.number_input("Present year (T0)", 2001, 2030, 2024, step=1)
    epoch_gap = col_b.number_input("Window length (yr)", 1, 24, 10, step=1)

    if t0_year - epoch_gap < 2000:
        st.sidebar.warning(
            f"Window starts in {t0_year - epoch_gap}, before Hansen's year-2000 "
            "baseline. Shorten the window."
        )

    with st.sidebar.expander("Advanced Hansen settings"):
        asset = st.selectbox(
            "Dataset version", HANSEN_ASSET_CHOICES, index=0,
            help="Newer versions extend the loss record but occasionally revise history.",
        )
        include_gain = st.checkbox(
            "Include forest gain class", value=True,
            help=(
                "Hansen's gain band covers 2000-2012 only and carries no year, so the "
                "gain class cannot be restricted to your window. It is used solely to "
                "calibrate the regeneration curve, never as creditable project area."
            ),
        )
        scale = st.select_slider(
            "Analysis scale (m)", options=[30, 60, 90, 120], value=30,
            help="Coarsen for large AOIs. 30 m is Hansen's native resolution.",
        )

    return HansenConfig(
        asset=asset,
        treecover_min_pct=int(treecover),
        t0_year=int(t0_year),
        epoch_gap_years=int(epoch_gap),
        include_gain=include_gain,
        analysis_scale_m=int(scale),
    )


def _project_section() -> Tuple[ProjectConfigParams, dict]:
    st.sidebar.markdown("### Project")

    name = st.sidebar.text_input("Project name", value="ARR Screening")

    class_options = list(REFORESTATION_CLASSES)
    project_class = st.sidebar.selectbox(
        "Project area class", class_options,
        format_func=lambda c: CLASS_LABELS[c],
        help=_help(
            "Which reforestation stratum is the project area. Stable non-forest is "
            "land available for new planting; forest loss is recently cleared land "
            "requiring restoration. Stable forest is never offered — it is not "
            "eligible for ARR."
        ),
    )

    plantable = st.sidebar.slider(
        "Plantable fraction", 0.1, 1.0, 0.85, step=0.05,
        help=_help(
            "Share of the stratum that can realistically be established, after "
            "excluding infrastructure, water bodies, rock, steep ground and areas "
            "without access or landowner agreement."
        ),
    )

    col_a, col_b = st.sidebar.columns(2)
    start_year = col_a.number_input("Start year", 2020, 2050, 2026, step=1)
    crediting = col_b.number_input("Crediting (yr)", 5, 100, 40, step=5)

    with st.sidebar.expander("Advanced project settings"):
        area_override = st.number_input(
            "Override project area (ha)", min_value=0.0, value=0.0, step=10.0,
            help="0 uses the Hansen-measured stratum area.",
        )
        phased = st.checkbox("Phase establishment over several years", value=False)
        planting_years = st.number_input(
            "Establishment years", 1, 10, 3, step=1, disabled=not phased,
        )
        baseline_override = st.number_input(
            "Override baseline rate (tCO2e/ha/yr)", min_value=-1.0, value=-1.0, step=0.05,
            help=(
                "-1 fits the baseline from the project stratum's own GEDI trend, "
                "clamped at zero. Set a value only if you have better evidence."
            ),
        )

    project = ProjectConfigParams(
        project_name=name,
        start_year=int(start_year),
        crediting_period_years=int(crediting),
        project_class=int(project_class),
        area_override_ha=float(area_override) if area_override > 0 else None,
        plantable_fraction=float(plantable),
        phased_planting=phased,
        planting_years=int(planting_years),
        baseline_rate_override=float(baseline_override) if baseline_override >= 0 else None,
    )
    return project, {}


def _curve_section(suggested_zone: Optional[str], _partial: dict) -> CarbonCurveConfig:
    """Phase 7 — the carbon accumulation curve. Constraint 4 lives here."""
    st.sidebar.markdown("### Carbon curve")

    interventions = intervention_choices()
    intervention = st.sidebar.selectbox(
        "Intervention type", list(interventions.keys()),
        format_func=lambda k: interventions[k],
        key="intervention_type",
    )
    st.sidebar.caption(INTERVENTION_PRESETS[intervention]["description"])

    anr_fraction = 0.0
    if intervention == "mixed":
        anr_fraction = st.sidebar.slider(
            "ANR share of project area", 0.0, 1.0, 0.6, step=0.05,
            help="The remainder is enrichment planting.",
        )

    zones = zone_choices()
    zone_keys = list(zones.keys())
    default_index = zone_keys.index(suggested_zone) if suggested_zone in zone_keys else 1
    zone = st.sidebar.selectbox(
        "Ecological zone", zone_keys,
        index=default_index,
        format_func=lambda k: zones[k],
        help=_help(
            "Sets the biomass ceiling, baseline growth rate and root:shoot ratio. "
            "Suggested from your AOI's latitude and tree cover — override it if you "
            "know the zone."
        ),
    )
    if suggested_zone and zone == suggested_zone:
        st.sidebar.caption(f"Suggested from AOI location: {zones[suggested_zone]}")

    site_index = st.sidebar.slider(
        "Site index", 0.5, 1.5, 1.0, step=0.05,
        help=_help(
            "Productivity relative to the zone average. 1.0 is typical; raise it for "
            "deep soils, reliable rainfall and good aspect, lower it for marginal land."
        ),
    )

    with st.sidebar.expander("Site & climate conditions"):
        soil = st.select_slider(
            "Soil quality", options=["degraded", "moderate", "good"], value="moderate",
            help="Degraded soil cuts both growth rate and the achievable ceiling.",
        )
        water = st.select_slider(
            "Water stress", options=["none", "seasonal", "severe"], value="none",
        )
        fire = st.select_slider(
            "Fire risk", options=["low", "moderate", "high"], value="low",
            help="Raises mortality and depresses the long-run ceiling.",
        )
        grazing = st.select_slider(
            "Grazing pressure", options=["none", "moderate", "high"], value="none",
            help="Livestock browsing is a leading cause of ARR establishment failure.",
        )

    with st.sidebar.expander("Growth timing"):
        preset = INTERVENTION_PRESETS[intervention]
        establishment_lag = st.slider(
            "Establishment lag (yr)", 0.0, 5.0, float(preset["establishment_lag_years"]), step=0.5,
            help=(
                "Years before measurable biomass accrues. Planting typically needs a "
                "year for establishment; ANR starts immediately from existing rootstock."
            ),
        )
        advancement = st.slider(
            "ANR advancement (yr)", 0.0, 30.0, float(preset["advancement_years"]), step=1.0,
            help=(
                "Head start from existing rootstock, coppice and seed bank, expressed "
                "as equivalent years of prior growth. Higher on recently cleared land "
                "with live stumps; zero on long-degraded pasture."
            ),
        )

    species_mix = _species_mix_editor(intervention)

    with st.sidebar.expander("Calibration & chemistry"):
        calibrate = st.checkbox(
            "Calibrate the curve from GEDI where possible", value=True,
            help=(
                "Fits the ceiling to local mature forest and the recovery rate to "
                "local regenerating land. Falls back to zone defaults outside GEDI "
                "coverage or where footprints are too few."
            ),
        )
        carbon_fraction = st.number_input(
            "Carbon fraction of dry matter", 0.40, 0.55, 0.47, step=0.01,
            help="IPCC default is 0.47.",
        )
        root_shoot_override = st.number_input(
            "Override root:shoot ratio", min_value=0.0, max_value=1.5, value=0.0, step=0.01,
            help="0 uses the ecological zone default.",
        )
        haircut = st.slider(
            "Conservative haircut", 0.0, 0.5, 0.0, step=0.05,
            help=(
                "Extra proportional reduction on the curve ceiling. The VM0047 "
                "deductions already impose conservativeness, so 0 is the honest "
                "default — use this only if you have a specific reason."
            ),
        )

    return CarbonCurveConfig(
        intervention_type=intervention,
        anr_area_fraction=anr_fraction,
        establishment_lag_years=establishment_lag,
        advancement_years=advancement,
        ecological_zone=zone,
        site_index=site_index,
        soil_quality=soil,
        water_stress=water,
        fire_risk=fire,
        grazing_pressure=grazing,
        species_mix=species_mix,
        carbon_fraction=carbon_fraction,
        root_shoot_override=root_shoot_override if root_shoot_override > 0 else None,
        calibrate_from_gedi=calibrate,
        conservative_haircut=haircut,
    )


def _species_mix_editor(intervention: str) -> List[SpeciesMixEntry]:
    """Species mix editor. Fractions are normalised so they always sum to 1."""
    with st.sidebar.expander("Species mix", expanded=False):
        templates = species_choices()
        template_keys = list(templates.keys())

        defaults = default_mix_for(intervention)
        n_species = st.number_input(
            "Number of cohorts", 1, 5, len(defaults), step=1,
            key=f"n_species_{intervention}",
        )

        entries: List[SpeciesMixEntry] = []
        for i in range(int(n_species)):
            st.markdown(f"**Cohort {i + 1}**")
            default_key = template_keys[0]
            if i < len(defaults):
                for key, label in templates.items():
                    if label == defaults[i].name:
                        default_key = key
                        break

            template = st.selectbox(
                "Species", template_keys,
                index=template_keys.index(default_key),
                format_func=lambda k: templates[k],
                key=f"species_template_{intervention}_{i}",
            )
            entry = make_species_entry(template)

            entry.area_fraction = st.slider(
                "Area share", 0.0, 1.0,
                float(defaults[i].area_fraction) if i < len(defaults) else 1.0 / int(n_species),
                step=0.05, key=f"species_frac_{intervention}_{i}",
            )

            cols = st.columns(2)
            entry.agb_max = cols[0].number_input(
                "Max AGB (t/ha)", 10.0, 600.0, float(entry.agb_max), step=10.0,
                key=f"species_agbmax_{intervention}_{i}",
            )
            entry.planting_density = cols[1].number_input(
                "Stems/ha", 0, 5000, int(entry.planting_density), step=100,
                key=f"species_density_{intervention}_{i}",
                help="0 for natural regeneration cohorts.",
            )

            cols = st.columns(2)
            entry.k_growth = cols[0].number_input(
                "Growth rate k", 0.01, 1.0, float(entry.k_growth), step=0.01,
                key=f"species_k_{intervention}_{i}",
                help="Steepness of the accumulation curve.",
            )
            entry.mortality_rate_y1 = cols[1].number_input(
                "Year-1 mortality", 0.0, 0.9, float(entry.mortality_rate_y1), step=0.05,
                key=f"species_mort_{intervention}_{i}",
            )

            entry.is_harvested = st.checkbox(
                "Harvested", value=False, key=f"species_harvest_{intervention}_{i}",
                help="Harvest resets the cohort's stock on each cycle.",
            )
            if entry.is_harvested:
                cols = st.columns(2)
                entry.harvest_cycle_years = cols[0].number_input(
                    "Cycle (yr)", 5, 60, 30, step=5, key=f"species_cycle_{intervention}_{i}",
                )
                entry.pct_harvested = cols[1].slider(
                    "Removed", 0.0, 1.0, 0.8, step=0.1, key=f"species_pct_{intervention}_{i}",
                )

            entries.append(entry)
            st.divider()

        # Normalise so the mix always sums to 1 — a mix that does not is a config
        # error the user cannot see in the results, so fix it silently and say so.
        total = sum(e.area_fraction for e in entries)
        if total > 0 and abs(total - 1.0) > 0.01:
            st.caption(f"Area shares sum to {total:.2f} — normalised to 1.00.")
            for e in entries:
                e.area_fraction /= total
        elif total == 0:
            for e in entries:
                e.area_fraction = 1.0 / len(entries)

    return entries


def _biomass_section(t0_year: int) -> Tuple[GEDIConfig, TrendConfig]:
    st.sidebar.markdown("### GEDI biomass sampling")

    enabled = st.sidebar.checkbox(
        "Sample GEDI footprints", value=True,
        help=_help(
            "GEDI provides the site-specific biomass measurements that calibrate the "
            "carbon curve. Switching it off forces IPCC ecological-zone defaults and "
            "makes results indicative only — but the run completes much faster, which "
            "is useful while exploring settings."
        ),
    )

    with st.sidebar.expander("Advanced GEDI settings"):
        cols = st.columns(2)
        start_year = cols[0].number_input("First year", 2019, 2030, 2019, step=1)
        end_year = cols[1].number_input("Last year", 2019, 2030, 2024, step=1)

        sensitivity = st.slider(
            "Beam sensitivity minimum", 0.80, 0.99, 0.95, step=0.01,
            help=(
                "Fraction of canopy the beam could penetrate. 0.95 is standard for "
                "closed canopy but discards nearly everything in open or dry systems — "
                "relax to 0.90 there and note it in your report."
            ),
        )
        min_per_stratum = st.number_input(
            "Minimum footprints per stratum", 5, 200, 30, step=5,
            help=(
                "Below this, a stratum's estimate is flagged unreliable and will not "
                "drive curve calibration."
            ),
        )

    st.sidebar.markdown("### Trend windows")
    with st.sidebar.expander("Change & additionality periods"):
        trend_method = st.selectbox(
            "Trend estimator", ["wls", "ols", "theilsen"],
            format_func=lambda m: {
                "wls": "Weighted least squares (recommended)",
                "ols": "Ordinary least squares",
                "theilsen": "Theil-Sen (robust to outlier years)",
            }[m],
            help=(
                "WLS weights each year by 1/SE^2, so sparse years pull the line less. "
                "Theil-Sen resists a single anomalous year, which sparse GEDI coverage "
                "produces regularly."
            ),
        )
        cols = st.columns(2)
        change_start = cols[0].number_input("Change from", 2019, 2030, 2019, step=1)
        change_end = cols[1].number_input("Change to", 2019, 2030, 2024, step=1)

        cols = st.columns(2)
        base_start = cols[0].number_input("Baseline from", 2019, 2030, 2019, step=1)
        base_end = cols[1].number_input("Baseline to", 2019, 2030, 2021, step=1)

        cols = st.columns(2)
        proj_start = cols[0].number_input("Project from", 2019, 2030, 2021, step=1)
        proj_end = cols[1].number_input("Project to", 2019, 2030, 2024, step=1)

    gedi = GEDIConfig(
        enabled=enabled,
        start_year=int(start_year),
        end_year=int(end_year),
        sensitivity_min=float(sensitivity),
        min_footprints_per_stratum=int(min_per_stratum),
    )
    trend = TrendConfig(
        change_years=(int(change_start), int(change_end)),
        baseline_period=(int(base_start), int(base_end)),
        project_period=(int(proj_start), int(proj_end)),
        trend_method=trend_method,
    )
    return gedi, trend


def _accounting_section() -> Tuple[PoolConfig, DeductionConfig]:
    st.sidebar.markdown("### VM0047 accounting")

    with st.sidebar.expander("Carbon pools"):
        st.caption("Woody biomass pools are mandatory under VM0047 Table 3.")
        ag = st.checkbox("Above-ground woody biomass", value=True, disabled=True)
        bg = st.checkbox("Below-ground woody biomass", value=True)
        deadwood = st.checkbox("Dead wood", value=False)
        litter = st.checkbox("Litter", value=False)
        soc = st.checkbox(
            "Soil organic carbon", value=False,
            help=(
                "Including SOC requires soil sampling to verify. Enable only if you "
                "intend to monitor it — it is a common source of validation findings."
            ),
        )
        soc_rate = st.number_input(
            "SOC accrual (tCO2e/ha/yr)", 0.0, 5.0, 0.0, step=0.1, disabled=not soc,
        )

    with st.sidebar.expander("Deductions (VM0047 Eq. 30)"):
        uncertainty = st.slider(
            "Uncertainty (UNC)", 0.05, 0.50, 0.10, step=0.01,
            help=(
                "VM0047 sets 10% as an ex-ante minimum. The app raises this "
                "automatically when the carbon curve is not backed by local "
                "measurement — the applied value is shown in the results."
            ),
        )
        pb = st.slider("Performance benchmark (PB)", 0.0, 0.30, 0.03, step=0.01)
        npr = st.slider(
            "Non-permanence risk buffer", 0.05, 0.50, 0.15, step=0.01,
            help="Contribution to the AFOLU pooled buffer. Set by the VCS risk tool.",
        )
        leakage = st.slider("Leakage", 0.0, 0.40, 0.0, step=0.01)
        leakage_years = st.number_input("Leakage applies for (yr)", 0, 100, 40, step=5)

        st.caption("Project emissions, per hectare per year")
        cols = st.columns(3)
        pe_fert = cols[0].number_input("Fertiliser", 0.0, 5.0, 0.0, step=0.1)
        pe_fuel = cols[1].number_input("Fuel", 0.0, 5.0, 0.0, step=0.1)
        pe_burn = cols[2].number_input("Burning", 0.0, 5.0, 0.0, step=0.1)

    pools = PoolConfig(
        ag_woody_biomass=ag,
        bg_woody_biomass=bg,
        deadwood=deadwood,
        litter=litter,
        soc=soc,
        soc_rate=float(soc_rate),
    )
    deductions = DeductionConfig(
        uncertainty_pct=float(uncertainty),
        performance_benchmark_pct=float(pb),
        non_permanence_risk=float(npr),
        leakage_pct=float(leakage),
        leakage_years=int(leakage_years),
        pe_fertilizer=float(pe_fert),
        pe_fossil_fuel=float(pe_fuel),
        pe_burning=float(pe_burn),
    )
    return pools, deductions
