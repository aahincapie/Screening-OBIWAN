"""
Intervention presets and species-mix templates.

Two related registries:

``INTERVENTION_PRESETS``
    How the *activity* shapes the curve. Active planting starts from bare ground with
    a known stem density and an establishment lag; ANR starts from existing rootstock
    and seed bank, so it has a head start but a gentler slope and a lower practical
    ceiling on degraded sites.

``SPECIES_TEMPLATES``
    Allometric and growth parameters for common reforestation cohorts. These populate
    the species-mix editor; every field remains editable in the UI.

Same provenance caveat as ``config/ecological_zones.py``: these are screening
defaults, not verified Tier 2 parameters. Replace them with measured or
literature-sourced values for the project region before relying on the numbers.
"""

from __future__ import annotations

from typing import Dict, List, TypedDict

from config.defaults import SpeciesMixEntry


class InterventionPreset(TypedDict):
    label: str
    description: str
    growth_model: str
    establishment_lag_years: float
    advancement_years: float
    growth_multiplier: float
    ceiling_multiplier: float
    default_planting_density: int
    mortality_rate_y1: float
    annual_mortality: float
    cost_signal: str


INTERVENTION_PRESETS: Dict[str, InterventionPreset] = {
    "active_planting": {
        "label": "Active planting",
        "description": (
            "Nursery-raised seedlings planted at a defined density on land with no "
            "residual woody rootstock. Fastest early accrual and the most controllable "
            "species composition, at the highest establishment cost and mortality risk."
        ),
        "growth_model": "logistic",
        "establishment_lag_years": 1.0,
        "advancement_years": 0.0,
        "growth_multiplier": 1.00,
        "ceiling_multiplier": 1.00,
        "default_planting_density": 1100,
        "mortality_rate_y1": 0.15,
        "annual_mortality": 0.02,
        "cost_signal": "high",
    },
    "anr": {
        "label": "Assisted natural regeneration (ANR)",
        "description": (
            "Protection and tending of existing rootstock, coppice and seed bank — "
            "fire breaks, grazing exclusion, liberation cutting. No planting. Slower "
            "to close canopy but starts from standing biomass, so year-0 stock is "
            "non-zero and the curve carries an advancement head start."
        ),
        "growth_model": "recovery_curve",
        "establishment_lag_years": 0.0,
        "advancement_years": 4.0,
        "growth_multiplier": 0.80,
        "ceiling_multiplier": 0.95,
        "default_planting_density": 0,
        "mortality_rate_y1": 0.05,
        "annual_mortality": 0.01,
        "cost_signal": "low",
    },
    "mixed": {
        "label": "Mixed / enrichment planting",
        "description": (
            "ANR across the matrix with enrichment planting in gaps. Blends both "
            "curves weighted by the ANR area fraction. Common where residual "
            "regeneration is patchy."
        ),
        "growth_model": "logistic",
        "establishment_lag_years": 0.5,
        "advancement_years": 2.0,
        "growth_multiplier": 0.90,
        "ceiling_multiplier": 1.00,
        "default_planting_density": 600,
        "mortality_rate_y1": 0.10,
        "annual_mortality": 0.015,
        "cost_signal": "medium",
    },
}

DEFAULT_INTERVENTION = "active_planting"


# ---------------------------------------------------------------------------
# Species templates
# ---------------------------------------------------------------------------

class SpeciesTemplate(TypedDict):
    label: str
    growth_model: str
    agb_max: float
    k_growth: float
    age_inflection: float
    wood_density: float
    root_shoot_ratio: float
    planting_density: int
    mortality_rate_y1: float
    annual_mortality: float
    notes: str


SPECIES_TEMPLATES: Dict[str, SpeciesTemplate] = {
    "native_mixed_broadleaf": {
        "label": "Native mixed broadleaf",
        "growth_model": "logistic",
        "agb_max": 200.0,
        "k_growth": 0.18,
        "age_inflection": 12.0,
        "wood_density": 0.55,
        "root_shoot_ratio": 0.27,
        "planting_density": 1100,
        "mortality_rate_y1": 0.15,
        "annual_mortality": 0.02,
        "notes": "Default multi-species native restoration cohort.",
    },
    "fast_growing_pioneer": {
        "label": "Fast-growing pioneer",
        "growth_model": "logistic",
        "agb_max": 140.0,
        "k_growth": 0.30,
        "age_inflection": 7.0,
        "wood_density": 0.38,
        "root_shoot_ratio": 0.24,
        "planting_density": 1600,
        "mortality_rate_y1": 0.12,
        "annual_mortality": 0.03,
        "notes": "Fast early credits, lower ceiling, shorter-lived. Nurse crop role.",
    },
    "eucalyptus_plantation": {
        "label": "Eucalyptus (short rotation)",
        "growth_model": "MAI",
        "agb_max": 180.0,
        "k_growth": 0.35,
        "age_inflection": 6.0,
        "wood_density": 0.55,
        "root_shoot_ratio": 0.24,
        "planting_density": 1400,
        "mortality_rate_y1": 0.08,
        "annual_mortality": 0.01,
        "notes": "Set is_harvested and a harvest cycle. Check ARR eligibility rules.",
    },
    "teak_hardwood": {
        "label": "Teak / long-rotation hardwood",
        "growth_model": "logistic",
        "agb_max": 230.0,
        "k_growth": 0.14,
        "age_inflection": 16.0,
        "wood_density": 0.60,
        "root_shoot_ratio": 0.27,
        "planting_density": 800,
        "mortality_rate_y1": 0.12,
        "annual_mortality": 0.015,
        "notes": "Slow start, high ceiling, long crediting horizon.",
    },
    "agroforestry_fruit": {
        "label": "Agroforestry / fruit trees",
        "growth_model": "logistic",
        "agb_max": 90.0,
        "k_growth": 0.22,
        "age_inflection": 9.0,
        "wood_density": 0.50,
        "root_shoot_ratio": 0.27,
        "planting_density": 400,
        "mortality_rate_y1": 0.10,
        "annual_mortality": 0.02,
        "notes": "Low density and low ceiling; livelihood co-benefits dominate.",
    },
    "mangrove": {
        "label": "Mangrove",
        "growth_model": "logistic",
        "agb_max": 250.0,
        "k_growth": 0.20,
        "age_inflection": 11.0,
        "wood_density": 0.70,
        "root_shoot_ratio": 0.49,
        "planting_density": 2500,
        "mortality_rate_y1": 0.25,
        "annual_mortality": 0.03,
        "notes": "High root:shoot and large SOC pool — enable SOC. High early mortality.",
    },
    "natural_regeneration": {
        "label": "Natural regeneration (ANR cohort)",
        "growth_model": "recovery_curve",
        "agb_max": 200.0,
        "k_growth": 0.05,
        "age_inflection": 20.0,
        "wood_density": 0.55,
        "root_shoot_ratio": 0.27,
        "planting_density": 0,
        "mortality_rate_y1": 0.05,
        "annual_mortality": 0.01,
        "notes": "Use with the ANR intervention. Rate is GEDI-calibrated where possible.",
    },
}

DEFAULT_SPECIES = "native_mixed_broadleaf"


def species_choices() -> Dict[str, str]:
    """Map template key -> display label, for UI select boxes."""
    return {k: v["label"] for k, v in SPECIES_TEMPLATES.items()}


def intervention_choices() -> Dict[str, str]:
    return {k: v["label"] for k, v in INTERVENTION_PRESETS.items()}


def make_species_entry(template_key: str, area_fraction: float = 1.0) -> SpeciesMixEntry:
    """Instantiate a :class:`SpeciesMixEntry` from a template."""
    t = SPECIES_TEMPLATES.get(template_key, SPECIES_TEMPLATES[DEFAULT_SPECIES])
    return SpeciesMixEntry(
        name=t["label"],
        area_fraction=area_fraction,
        growth_model=t["growth_model"],
        agb_max=t["agb_max"],
        k_growth=t["k_growth"],
        age_inflection=t["age_inflection"],
        wood_density=t["wood_density"],
        root_shoot_ratio=t["root_shoot_ratio"],
        planting_density=t["planting_density"],
        mortality_rate_y1=t["mortality_rate_y1"],
        annual_mortality=t["annual_mortality"],
    )


def default_mix_for(intervention: str) -> List[SpeciesMixEntry]:
    """A sensible starting species mix for an intervention type."""
    if intervention == "anr":
        return [make_species_entry("natural_regeneration", 1.0)]
    if intervention == "mixed":
        return [
            make_species_entry("natural_regeneration", 0.6),
            make_species_entry("native_mixed_broadleaf", 0.4),
        ]
    return [
        make_species_entry("native_mixed_broadleaf", 0.7),
        make_species_entry("fast_growing_pioneer", 0.3),
    ]
