"""
GEDI L4A footprint extraction and stratification.

GEDI is a *sample* of lidar footprints, not a wall-to-wall map. That distinction is
the whole point of the OBIWAN approach and of ``src/estimators.py``: footprints inside
a stratum are treated as a statistical sample, yielding a mean AGBD **with a standard
error**, rather than a pixel-differenced map with no defensible uncertainty.

Differences from the source notebook
------------------------------------
- **Stratification is server-side.** The notebook downloaded footprints, then sampled
  a locally-exported GeoTIFF with ``rasterio`` to tag each one. Here the Hansen
  transition image is sampled inside Earth Engine at the same time as the biomass
  bands, so there is no intermediate raster export, no local file dependency, and no
  CRS round-trip.
- **Coverage is checked before extraction.** GEDI's ISS orbit bounds it to roughly
  +/- 51.6 degrees; the notebook would have silently returned zero footprints outside
  that band. Here it is a first-class, reported condition that routes the carbon curve
  to the IPCC tier.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import ee
import pandas as pd

from config.defaults import ALL_CLASSES, CLASS_LABELS, GEDI_LAT_LIMIT, GEDIConfig

logger = logging.getLogger(__name__)


@dataclass
class GEDIResult:
    """Quality-screened, stratified GEDI footprints for the AOI."""

    footprints: pd.DataFrame
    """Columns: agbd, agbd_se, year, stratum, stratum_label. One row per footprint."""

    available: bool
    """False when GEDI cannot be used at all (out of coverage, disabled, or empty)."""

    reason: str = ""
    """Why GEDI is unavailable, when it is. Shown to the user verbatim."""

    counts_by_stratum: Dict[int, int] = field(default_factory=dict)
    thin_strata: List[int] = field(default_factory=list)
    """Strata below ``min_footprints_per_stratum`` — unreliable, IPCC fallback applies."""

    warnings: List[str] = field(default_factory=list)

    @property
    def n_total(self) -> int:
        return len(self.footprints)

    def for_stratum(self, code: int) -> pd.DataFrame:
        if self.footprints.empty:
            return self.footprints
        return self.footprints[self.footprints["stratum"] == code]

    def is_reliable(self, code: int) -> bool:
        return self.available and code not in self.thin_strata and \
            self.counts_by_stratum.get(code, 0) > 0

    def coverage_table(self) -> pd.DataFrame:
        """Footprint counts by stratum and year, for the QA panel."""
        if self.footprints.empty:
            return pd.DataFrame()
        return self.footprints.pivot_table(
            index="stratum_label", columns="year", values="agbd",
            aggfunc="count", fill_value=0,
        )


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

def check_coverage(min_lat: float, max_lat: float) -> tuple[bool, str]:
    """Is this AOI inside GEDI's orbital footprint?"""
    if abs(min_lat) <= GEDI_LAT_LIMIT and abs(max_lat) <= GEDI_LAT_LIMIT:
        return True, ""

    return False, (
        f"This AOI spans {min_lat:.1f} to {max_lat:.1f} degrees latitude, outside "
        f"GEDI's coverage of +/-{GEDI_LAT_LIMIT} degrees. GEDI flies on the ISS, whose "
        "orbital inclination bounds it to the tropics and mid-latitudes. The carbon "
        "curve will use IPCC ecological-zone defaults instead, and every result "
        "derived from it is labelled accordingly."
    )


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def quality_mask(image: "ee.Image", cfg: GEDIConfig) -> "ee.Image":
    """Standard GEDI L4A screening.

    ``l4_quality_flag == 1`` keeps footprints whose biomass model is in range;
    ``degrade_flag == 0`` drops degraded-pointing shots; ``sensitivity`` is the
    fraction of canopy the beam could penetrate, and 0.95 is the conventional
    threshold for closed canopy. In open or dry systems, 0.95 can discard nearly
    everything — relax it to ~0.90 there and say so in the report.
    """
    mask = image.select("sensitivity").gte(cfg.sensitivity_min)
    if cfg.quality_flag_required:
        mask = mask.And(image.select("l4_quality_flag").eq(1))
    if cfg.degrade_flag_excluded:
        mask = mask.And(image.select("degrade_flag").eq(0))
    return image.updateMask(mask)


def _year_sample(
    aoi_geometry: "ee.Geometry",
    transition: "ee.Image",
    year: int,
    cfg: GEDIConfig,
) -> "ee.FeatureCollection":
    """One year of footprints, already tagged with their transition stratum."""
    start = ee.Date.fromYMD(year, 1, 1)
    end = ee.Date.fromYMD(year, 12, 31).advance(1, "day")

    collection = (
        ee.ImageCollection(cfg.asset)
        .filterBounds(aoi_geometry)
        .filterDate(start, end)
        .map(lambda img: quality_mask(img, cfg))
    )

    # Mosaic the year: each valid 25 m cell is one footprint observation.
    # Stacking the transition band means stratum comes back with the sample, so no
    # separate spatial join is needed.
    stacked = collection.select(["agbd", "agbd_se"]).mosaic().addBands(
        transition.rename("stratum")
    )

    sample = stacked.sample(
        region=aoi_geometry,
        scale=cfg.scale_m,
        projection="EPSG:4326",
        geometries=False,       # coordinates are not needed downstream; omitting them
        dropNulls=True,         # keeps the payload well under EE's response limit
        tileScale=4,
    )
    return sample.map(lambda f: f.set("year", year))


def _to_dataframe(collection: "ee.FeatureCollection") -> pd.DataFrame:
    """Pull an EE FeatureCollection into pandas."""
    info = collection.getInfo()
    rows = [
        {
            "agbd": f["properties"].get("agbd"),
            "agbd_se": f["properties"].get("agbd_se"),
            "year": f["properties"].get("year"),
            "stratum": f["properties"].get("stratum"),
        }
        for f in info.get("features", [])
    ]
    return pd.DataFrame(rows)


def extract(
    aoi_geometry: "ee.Geometry",
    transition: "ee.Image",
    cfg: GEDIConfig,
    lat_bounds: tuple[float, float],
    progress_callback=None,
) -> GEDIResult:
    """Extract and stratify GEDI footprints across the configured years.

    Parameters
    ----------
    aoi_geometry
        Working region.
    transition
        Hansen transition image from :func:`src.hansen.build_transition_image`.
    cfg
        GEDI settings.
    lat_bounds
        ``(min_lat, max_lat)`` of the AOI, for the coverage check.
    progress_callback
        Optional ``fn(year, index, total)`` for UI progress reporting.
    """
    empty = pd.DataFrame(columns=["agbd", "agbd_se", "year", "stratum", "stratum_label"])

    if not cfg.enabled:
        return GEDIResult(empty, False, "GEDI sampling is switched off in the sidebar.")

    covered, reason = check_coverage(*lat_bounds)
    if not covered:
        return GEDIResult(empty, False, reason)

    frames: List[pd.DataFrame] = []
    warnings: List[str] = []
    years = cfg.years

    for i, year in enumerate(years):
        if progress_callback:
            progress_callback(year, i, len(years))
        try:
            frame = _to_dataframe(_year_sample(aoi_geometry, transition, year, cfg))
            if not frame.empty:
                frames.append(frame)
            logger.info("GEDI %d: %d footprints", year, len(frame))
        except Exception as exc:  # noqa: BLE001 — one bad year must not kill the run
            logger.warning("GEDI extraction failed for %d: %s", year, exc)
            warnings.append(f"Year {year} could not be retrieved ({type(exc).__name__}).")

    if not frames:
        return GEDIResult(
            empty, False,
            "No GEDI footprints passed quality screening in this AOI. The area may be "
            f"too small for the orbit track spacing, or the sensitivity threshold "
            f"({cfg.sensitivity_min}) may be too strict for this canopy type. Try "
            "lowering it to 0.90, or widen the year range.",
            warnings=warnings,
        )

    footprints = pd.concat(frames, ignore_index=True)
    footprints = footprints.dropna(subset=["agbd", "stratum"])
    footprints["stratum"] = footprints["stratum"].astype(int)
    footprints = footprints[footprints["stratum"].isin(ALL_CLASSES)].copy()
    footprints["stratum_label"] = footprints["stratum"].map(CLASS_LABELS)

    if footprints.empty:
        return GEDIResult(
            empty, False,
            "GEDI footprints were found but none fell inside a mapped Hansen "
            "transition class.",
            warnings=warnings,
        )

    counts = footprints.groupby("stratum").size().to_dict()
    thin = [c for c, n in counts.items() if n < cfg.min_footprints_per_stratum]

    for code in thin:
        warnings.append(
            f"{CLASS_LABELS.get(code, code)}: only {counts[code]} footprints "
            f"(below the {cfg.min_footprints_per_stratum} threshold). Its estimate "
            "carries wide confidence intervals and will not drive curve calibration."
        )

    if len(footprints) > cfg.max_footprints:
        warnings.append(
            f"{len(footprints):,} footprints retrieved, above the "
            f"{cfg.max_footprints:,} guard. Results are valid but the app will be "
            "slow — consider a smaller AOI."
        )

    return GEDIResult(
        footprints=footprints,
        available=True,
        counts_by_stratum={int(k): int(v) for k, v in counts.items()},
        thin_strata=thin,
        warnings=warnings,
    )
