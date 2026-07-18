"""
Ecological zone defaults for the carbon accumulation curve.

.. warning::
   **These are editable starting points, not verified Tier 1 values.**

   The numbers below are representative, order-of-magnitude figures for the FAO
   global ecological zones, intended to give a sensible curve shape before the user
   supplies project-specific parameters. They are *screening* defaults.

   Before any value here is used in a VCS submission, PDD, or investment decision,
   replace it with the figure from the current source table for your zone and
   continent:

     - IPCC 2019 Refinement to the 2006 Guidelines, Vol. 4, Ch. 4
       Table 4.9  — AGB growth rates in naturally regenerating forest
       Table 4.10 — AGB growth rates in plantations
       Table 4.4  — root-to-shoot ratios
       Table 4.7  — above-ground biomass in natural forest

   Every field is exposed in the UI, so a user with authoritative numbers can enter
   them directly without touching this file. The app labels any output derived from
   these defaults as ``tier="ipcc_default"`` so provenance is never lost.

Fields
------
agb_max_t_ha
    Mature above-ground biomass ceiling (t dry matter/ha). Asymptote of the curve.
growth_young_t_ha_yr
    Mean AGB increment for stands <= 20 years (t d.m./ha/yr). Sets the early slope.
growth_mature_t_ha_yr
    Mean AGB increment for stands > 20 years.
root_shoot
    Below-ground : above-ground biomass ratio.
recovery_rate
    Exponential recovery constant k in AGB(t) = AGB_max * (1 - exp(-k*t)), derived
    so the curve reaches ~63% of the ceiling in 1/k years. Consistent with the
    young/mature increments above.
soc_rate_tco2_ha_yr
    Indicative soil organic carbon accrual under reforestation, used only when the
    SOC pool is explicitly enabled.
"""

from __future__ import annotations

from typing import Dict, Optional, TypedDict


class EcologicalZone(TypedDict):
    label: str
    agb_max_t_ha: float
    growth_young_t_ha_yr: float
    growth_mature_t_ha_yr: float
    root_shoot: float
    recovery_rate: float
    soc_rate_tco2_ha_yr: float
    notes: str


ECOLOGICAL_ZONES: Dict[str, EcologicalZone] = {
    "tropical_rainforest": {
        "label": "Tropical rainforest",
        "agb_max_t_ha": 300.0,
        "growth_young_t_ha_yr": 7.0,
        "growth_mature_t_ha_yr": 3.1,
        "root_shoot": 0.37,
        "recovery_rate": 0.045,
        "soc_rate_tco2_ha_yr": 0.9,
        "notes": "Humid evergreen, >2000 mm/yr, no pronounced dry season.",
    },
    "tropical_moist_deciduous": {
        "label": "Tropical moist deciduous",
        "agb_max_t_ha": 220.0,
        "growth_young_t_ha_yr": 5.0,
        "growth_mature_t_ha_yr": 2.4,
        "root_shoot": 0.24,
        "recovery_rate": 0.038,
        "soc_rate_tco2_ha_yr": 0.8,
        "notes": "Semi-deciduous, 1000-2000 mm/yr, 3-5 month dry season.",
    },
    "tropical_dry": {
        "label": "Tropical dry forest",
        "agb_max_t_ha": 120.0,
        "growth_young_t_ha_yr": 2.4,
        "growth_mature_t_ha_yr": 1.2,
        "root_shoot": 0.56,
        "recovery_rate": 0.030,
        "soc_rate_tco2_ha_yr": 0.5,
        "notes": "High root:shoot — a large share of carbon is below ground.",
    },
    "tropical_shrubland": {
        "label": "Tropical shrubland",
        "agb_max_t_ha": 70.0,
        "growth_young_t_ha_yr": 1.5,
        "growth_mature_t_ha_yr": 0.7,
        "root_shoot": 0.40,
        "recovery_rate": 0.028,
        "soc_rate_tco2_ha_yr": 0.4,
        "notes": "Low ceiling. Check that ARR is the right activity here.",
    },
    "tropical_mountain": {
        "label": "Tropical mountain system",
        "agb_max_t_ha": 180.0,
        "growth_young_t_ha_yr": 3.5,
        "growth_mature_t_ha_yr": 1.6,
        "root_shoot": 0.27,
        "recovery_rate": 0.033,
        "soc_rate_tco2_ha_yr": 1.0,
        "notes": "Cooler, slower growth; often high SOC potential.",
    },
    "subtropical_humid": {
        "label": "Subtropical humid forest",
        "agb_max_t_ha": 250.0,
        "growth_young_t_ha_yr": 5.5,
        "growth_mature_t_ha_yr": 2.6,
        "root_shoot": 0.28,
        "recovery_rate": 0.040,
        "soc_rate_tco2_ha_yr": 0.8,
        "notes": "",
    },
    "subtropical_dry": {
        "label": "Subtropical dry forest / steppe",
        "agb_max_t_ha": 110.0,
        "growth_young_t_ha_yr": 2.0,
        "growth_mature_t_ha_yr": 1.0,
        "root_shoot": 0.32,
        "recovery_rate": 0.028,
        "soc_rate_tco2_ha_yr": 0.4,
        "notes": "",
    },
    "temperate_oceanic": {
        "label": "Temperate oceanic forest",
        "agb_max_t_ha": 280.0,
        "growth_young_t_ha_yr": 4.0,
        "growth_mature_t_ha_yr": 2.6,
        "root_shoot": 0.23,
        "recovery_rate": 0.032,
        "soc_rate_tco2_ha_yr": 0.7,
        "notes": "",
    },
    "temperate_continental": {
        "label": "Temperate continental forest",
        "agb_max_t_ha": 220.0,
        "growth_young_t_ha_yr": 3.0,
        "growth_mature_t_ha_yr": 2.0,
        "root_shoot": 0.24,
        "recovery_rate": 0.028,
        "soc_rate_tco2_ha_yr": 0.7,
        "notes": "",
    },
    "boreal_coniferous": {
        "label": "Boreal coniferous forest",
        "agb_max_t_ha": 120.0,
        "growth_young_t_ha_yr": 1.5,
        "growth_mature_t_ha_yr": 1.0,
        "root_shoot": 0.39,
        "recovery_rate": 0.018,
        "soc_rate_tco2_ha_yr": 0.5,
        "notes": "Outside GEDI coverage — always IPCC-tier. Slow, long horizons.",
    },
}

DEFAULT_ZONE = "tropical_moist_deciduous"


# ---------------------------------------------------------------------------
# Multiplicative modifiers
# ---------------------------------------------------------------------------
# Applied as (growth_multiplier, ceiling_multiplier, mortality_delta) in
# src/carbon_curve.py::apply_modifiers.

SOIL_QUALITY_MODIFIERS: Dict[str, tuple] = {
    "degraded": (0.70, 0.75, 0.010),
    "moderate": (1.00, 1.00, 0.000),
    "good": (1.20, 1.10, -0.005),
}

WATER_STRESS_MODIFIERS: Dict[str, tuple] = {
    "none": (1.00, 1.00, 0.000),
    "seasonal": (0.85, 0.90, 0.005),
    "severe": (0.60, 0.70, 0.020),
}

FIRE_RISK_MODIFIERS: Dict[str, tuple] = {
    "low": (1.00, 1.00, 0.000),
    "moderate": (0.95, 0.95, 0.010),
    "high": (0.85, 0.85, 0.030),
}

GRAZING_MODIFIERS: Dict[str, tuple] = {
    "none": (1.00, 1.00, 0.000),
    "moderate": (0.90, 0.95, 0.015),
    "high": (0.70, 0.85, 0.040),
}


def zone_choices() -> Dict[str, str]:
    """Map zone key -> display label, for UI select boxes."""
    return {k: v["label"] for k, v in ECOLOGICAL_ZONES.items()}


def get_zone(key: str) -> EcologicalZone:
    """Look up a zone, falling back to the default rather than raising."""
    return ECOLOGICAL_ZONES.get(key, ECOLOGICAL_ZONES[DEFAULT_ZONE])


def infer_zone(latitude: float, mean_treecover_pct: Optional[float] = None) -> str:
    """Suggest an ecological zone from AOI latitude and mean Hansen tree cover.

    This is a *starting point* for the UI select box, deliberately crude: it uses
    latitude bands for the thermal regime and tree cover as a moisture proxy. Real
    zone assignment needs the FAO GEZ layer or local knowledge, and the user can
    always override the suggestion.

    Parameters
    ----------
    latitude
        AOI centroid latitude in degrees.
    mean_treecover_pct
        Mean Hansen ``treecover2000`` over the AOI, 0-100. Used to separate humid
        from dry systems within a latitude band. None skips the moisture split.
    """
    lat = abs(float(latitude))
    tc = mean_treecover_pct

    if lat < 23.5:
        if tc is None:
            return "tropical_moist_deciduous"
        if tc >= 60:
            return "tropical_rainforest"
        if tc >= 30:
            return "tropical_moist_deciduous"
        if tc >= 10:
            return "tropical_dry"
        return "tropical_shrubland"

    if lat < 35.0:
        if tc is not None and tc < 25:
            return "subtropical_dry"
        return "subtropical_humid"

    if lat < 55.0:
        if tc is not None and tc < 20:
            return "temperate_continental"
        return "temperate_oceanic"

    return "boreal_coniferous"
