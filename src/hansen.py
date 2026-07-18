"""
Hansen Global Forest Change transitions — the app's only forest-change source.

Restricting to Hansen (constraint 1) buys global applicability and reproducibility:
one 30 m product, one methodology, identical class semantics from Guatemala to
Ghana to Guangxi. No national datasets, no per-region asset wrangling, no silent
regional gaps.

Transition legend (identical codes to the source notebook, so exported rasters stay
interoperable with the upstream Jupyter workflow):

===== ==========================================
Code   Meaning
===== ==========================================
11     Stable forest        (control / ceiling)
22     Stable non-forest    **plantable**
12     Forest -> non-forest **plantable**
21     Non-forest -> forest (regen calibration)
===== ==========================================

Known Hansen limitations, stated plainly because they bound what the results mean
-----------------------------------------------------------------------------------
1. **``gain`` is 2000-2012 only and carries no year.** Class 21 therefore cannot be
   restricted to the analysis window. This app uses class 21 *only* as a natural
   regeneration analogue for calibrating the carbon curve, and never as creditable
   project area. The source notebook flagged this in a comment and then calibrated on
   it regardless; here the restriction is enforced in code.
2. **``treecover2000`` is the only cover baseline.** Forest standing at the window
   start is inferred as "forest in 2000, not yet lost by then". Land that became
   forest between 2000 and the window start via processes Hansen's gain band missed
   is misclassified as non-forest.
3. **Loss is not deforestation.** Hansen's loss means stand-replacing canopy removal,
   which includes plantation harvest and natural disturbance. For ARR screening this
   is acceptable — such land is genuinely a reforestation candidate — but it is not a
   deforestation figure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import ee

from config.defaults import (
    ALL_CLASSES,
    CLASS_LABELS,
    FOREST_GAIN,
    FOREST_LOSS,
    REFORESTATION_CLASSES,
    STABLE_FOREST,
    STABLE_NONFOREST,
    HansenConfig,
)

logger = logging.getLogger(__name__)


@dataclass
class TransitionResult:
    """Hansen transition raster plus its per-class area accounting."""

    image: "ee.Image"
    """Single-band byte image, values in {11, 22, 12, 21}, masked elsewhere."""

    areas_ha: Dict[int, float]
    total_area_ha: float
    mean_treecover_pct: Optional[float]
    t_start_year: int
    t0_year: int
    treecover_min_pct: int
    asset: str
    warnings: List[str] = field(default_factory=list)

    @property
    def plantable_ha(self) -> float:
        """Total area in the two reforestation classes."""
        return sum(self.areas_ha.get(c, 0.0) for c in REFORESTATION_CLASSES)

    def area_of(self, class_code: int) -> float:
        return self.areas_ha.get(class_code, 0.0)

    def share_of(self, class_code: int) -> float:
        return self.areas_ha.get(class_code, 0.0) / self.total_area_ha if self.total_area_ha else 0.0

    def summary_rows(self) -> List[dict]:
        """Tabular form for the UI and CSV export."""
        return [
            {
                "code": code,
                "class": CLASS_LABELS[code],
                "area_ha": round(self.areas_ha.get(code, 0.0), 2),
                "share_pct": round(100 * self.share_of(code), 2),
                "role": (
                    "Plantable" if code in REFORESTATION_CLASSES
                    else "Ceiling reference" if code == STABLE_FOREST
                    else "Regeneration calibration"
                ),
            }
            for code in ALL_CLASSES
        ]


# ---------------------------------------------------------------------------
# Transition construction
# ---------------------------------------------------------------------------

def build_transition_image(aoi_geometry: "ee.Image", cfg: HansenConfig) -> "ee.Image":
    """Build the four-class transition image for the configured window.

    The classification is a strict decision tree evaluated per pixel:

    1. ``forest_2000``   = treecover2000 >= threshold
    2. ``forest_t_start``= forest_2000 AND NOT lost on or before the window start
    3. ``loss_in_window``= loss occurred strictly after the window start, up to T0
    4. ``gain_any``      = Hansen gain flag (2000-2012, undated — see module docstring)

    Then:
      - forest at start, lost in window       -> 12
      - forest at start, not lost             -> 11
      - non-forest at start, gained           -> 21
      - non-forest at start, no gain          -> 22
    """
    gfc = ee.Image(cfg.asset)

    treecover = gfc.select("treecover2000")
    lossyear = gfc.select("lossyear").unmask(0)   # 0 = no loss; y => loss in year 2000+y
    loss = gfc.select("loss").unmask(0)
    gain = gfc.select("gain").unmask(0)

    offset_start = int(cfg.t_start_year) - 2000
    offset_t0 = int(cfg.t0_year) - 2000

    forest_2000 = treecover.gte(cfg.treecover_min_pct)
    lost_before_start = lossyear.gte(1).And(lossyear.lte(offset_start))
    forest_t_start = forest_2000.And(lost_before_start.Not())
    nonforest_t_start = forest_t_start.Not()

    loss_in_window = loss.eq(1).And(lossyear.gt(offset_start)).And(lossyear.lte(offset_t0))
    gain_any = gain.eq(1) if cfg.include_gain else ee.Image(0)

    is_loss = forest_t_start.And(loss_in_window)
    is_gain = nonforest_t_start.And(gain_any)

    transition = (
        ee.Image(0)
        .where(forest_t_start.And(is_loss.Not()), STABLE_FOREST)
        .where(nonforest_t_start.And(is_gain.Not()), STABLE_NONFOREST)
        .where(is_loss, FOREST_LOSS)
        .where(is_gain, FOREST_GAIN)
        .rename("transition")
        .toByte()
    )

    return transition.updateMask(transition.neq(0)).clip(aoi_geometry)


def compute_class_areas(
    transition: "ee.Image",
    aoi_geometry: "ee.Geometry",
    cfg: HansenConfig,
) -> Dict[int, float]:
    """Per-class area in hectares, server-side.

    Uses ``ee.Image.pixelArea()`` with a grouped sum rather than counting pixels and
    multiplying by a nominal cell size. Pixel area varies with latitude in Hansen's
    geographic grid, so the naive count is wrong everywhere except the equator — a
    real problem for a tool that must work globally.
    """
    area_image = ee.Image.pixelArea().addBands(transition)

    grouped = area_image.reduceRegion(
        reducer=ee.Reducer.sum().group(groupField=1, groupName="class"),
        geometry=aoi_geometry,
        scale=cfg.analysis_scale_m,
        maxPixels=cfg.max_pixels,
        bestEffort=cfg.best_effort,
        tileScale=cfg.tile_scale,
    )

    result = grouped.getInfo() or {}
    areas = {code: 0.0 for code in ALL_CLASSES}
    for entry in result.get("groups", []):
        code = int(entry["class"])
        if code in areas:
            areas[code] = float(entry["sum"]) / 10_000.0
    return areas


def compute_mean_treecover(
    aoi_geometry: "ee.Geometry", cfg: HansenConfig
) -> Optional[float]:
    """Mean ``treecover2000`` over the AOI — the moisture proxy for zone inference."""
    try:
        value = (
            ee.Image(cfg.asset)
            .select("treecover2000")
            .reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=aoi_geometry,
                scale=max(cfg.analysis_scale_m, 100),  # coarse is fine for a mean
                maxPixels=cfg.max_pixels,
                bestEffort=True,
                tileScale=cfg.tile_scale,
            )
            .get("treecover2000")
            .getInfo()
        )
        return float(value) if value is not None else None
    except Exception as exc:  # noqa: BLE001 — a missing hint is not a failure
        logger.warning("Mean tree cover unavailable: %s", exc)
        return None


def analyze(aoi_geometry: "ee.Geometry", cfg: HansenConfig) -> TransitionResult:
    """Run the full Hansen phase: transition image, areas, and quality warnings."""
    warnings: List[str] = []

    transition = build_transition_image(aoi_geometry, cfg)
    areas = compute_class_areas(transition, aoi_geometry, cfg)
    mean_tc = compute_mean_treecover(aoi_geometry, cfg)
    total = sum(areas.values())

    if total <= 0:
        warnings.append(
            "Hansen returned no classified pixels for this AOI. Check that the "
            "boundary is in the right place and is not entirely over water."
        )

    plantable = sum(areas.get(c, 0.0) for c in REFORESTATION_CLASSES)
    if total > 0 and plantable / total < 0.05:
        warnings.append(
            f"Only {100 * plantable / total:.1f}% of this AOI is in a reforestation "
            "class — it is almost entirely standing forest. ARR may not be the right "
            "activity here; consider a conservation (REDD+) methodology instead."
        )

    if cfg.include_gain and areas.get(FOREST_GAIN, 0.0) > 0 and cfg.t_start_year > 2012:
        warnings.append(
            f"Forest gain ({areas[FOREST_GAIN]:,.0f} ha) is flagged from Hansen's "
            "2000-2012 gain band, which predates this analysis window "
            f"({cfg.t_start_year}-{cfg.t0_year}) and carries no year. It is used only "
            "to calibrate the regeneration curve, never as project area."
        )

    if areas.get(FOREST_LOSS, 0.0) == 0 and total > 0:
        warnings.append(
            "No forest loss detected in this window. The carbon curve will rely on "
            "stable non-forest land only."
        )

    if cfg.treecover_min_pct < 10:
        warnings.append(
            f"A {cfg.treecover_min_pct}% canopy threshold classifies very sparse "
            "vegetation as forest, which shrinks the plantable area. Most national "
            "forest definitions sit between 10% and 30%."
        )

    return TransitionResult(
        image=transition,
        areas_ha=areas,
        total_area_ha=total,
        mean_treecover_pct=mean_tc,
        t_start_year=cfg.t_start_year,
        t0_year=cfg.t0_year,
        treecover_min_pct=cfg.treecover_min_pct,
        asset=cfg.asset,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Visualisation & export helpers
# ---------------------------------------------------------------------------

def visualization_params() -> dict:
    """Palette for ``ee.Image.getMapId``. Order follows the sorted class codes."""
    from config.defaults import CLASS_COLORS

    codes = sorted(ALL_CLASSES)
    return {
        "min": min(codes),
        "max": max(codes),
        "palette": [CLASS_COLORS[c].lstrip("#") for c in codes],
    }


def reforestation_mask(transition: "ee.Image") -> "ee.Image":
    """Binary mask of plantable land only — classes 22 and 12."""
    return transition.eq(STABLE_NONFOREST).Or(transition.eq(FOREST_LOSS))


def export_to_drive(
    transition: "ee.Image",
    aoi_geometry: "ee.Geometry",
    cfg: HansenConfig,
    description: str,
    folder: str = "Screening_OBIWAN",
) -> "ee.batch.Task":
    """Start a Drive export of the transition raster. Returns the started task.

    Exports are asynchronous; the caller shows the task ID and points the user at the
    Earth Engine Tasks tab rather than blocking the app.
    """
    task = ee.batch.Export.image.toDrive(
        image=transition.toByte(),
        description=description,
        folder=folder,
        fileNamePrefix=description,
        region=aoi_geometry,
        scale=cfg.analysis_scale_m,
        crs="EPSG:4326",
        maxPixels=int(cfg.max_pixels),
        fileFormat="GeoTIFF",
    )
    task.start()
    return task
