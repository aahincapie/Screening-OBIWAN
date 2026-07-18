"""User-adjustable configuration for Screening-OBIWAN.

Nothing here is read as a global by the analysis modules; the UI builds an
:class:`~config.defaults.AppConfig` and passes it explicitly.
"""

from config.defaults import (
    ALL_CLASSES,
    CLASS_COLORS,
    CLASS_LABELS,
    CO2_PER_C,
    CONTROL_CLASSES,
    FOREST_GAIN,
    FOREST_LOSS,
    GEDI_LAT_LIMIT,
    REFORESTATION_CLASSES,
    STABLE_FOREST,
    STABLE_NONFOREST,
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
from config.ecological_zones import ECOLOGICAL_ZONES, get_zone, infer_zone, zone_choices
from config.species import (
    INTERVENTION_PRESETS,
    SPECIES_TEMPLATES,
    default_mix_for,
    intervention_choices,
    make_species_entry,
    species_choices,
)

__all__ = [
    "AppConfig",
    "AOIConfig",
    "HansenConfig",
    "GEDIConfig",
    "TrendConfig",
    "CarbonCurveConfig",
    "PoolConfig",
    "DeductionConfig",
    "ProjectConfigParams",
    "SpeciesMixEntry",
    "CLASS_LABELS",
    "CLASS_COLORS",
    "ALL_CLASSES",
    "REFORESTATION_CLASSES",
    "CONTROL_CLASSES",
    "STABLE_FOREST",
    "STABLE_NONFOREST",
    "FOREST_LOSS",
    "FOREST_GAIN",
    "CO2_PER_C",
    "GEDI_LAT_LIMIT",
    "ECOLOGICAL_ZONES",
    "get_zone",
    "infer_zone",
    "zone_choices",
    "INTERVENTION_PRESETS",
    "SPECIES_TEMPLATES",
    "intervention_choices",
    "species_choices",
    "make_species_entry",
    "default_mix_for",
]
