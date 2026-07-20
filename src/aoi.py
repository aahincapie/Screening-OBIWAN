"""
Area-of-interest ingestion.

Primary input is a **KML file up to 1 MB** (constraint 1). KMZ, GeoJSON and zipped
shapefiles are also accepted because users rarely have exactly the format a tool asks
for, and rejecting them adds friction for no analytical benefit.

Everything is normalised to EPSG:4326, validated, optionally dissolved and buffered,
and returned as both a GeoDataFrame (for mapping and area maths) and an
``ee.Geometry`` (for the Earth Engine pipeline).

Area is computed in an **equal-area projection**, not with the notebook's
``111320 * cos(lat)`` approximation, which drifts badly at high latitude and would
have made a globally-applicable tool quietly wrong outside the tropics.
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import ee
import geopandas as gpd
from shapely.geometry import mapping
from shapely.ops import unary_union

from config.defaults import AOIConfig

logger = logging.getLogger(__name__)

# World Cylindrical Equal Area — global, area-true, adequate for hectare accounting
# at project scale. For sub-hectare precision, swap to a local equal-area CRS.
EQUAL_AREA_CRS = "ESRI:54034"

SUPPORTED_SUFFIXES = {".kml", ".kmz", ".geojson", ".json", ".zip", ".shp", ".gpkg"}


class AOIError(ValueError):
    """Raised for any AOI the app cannot or should not process."""


@dataclass
class AOI:
    """A validated area of interest, ready for analysis."""

    gdf: gpd.GeoDataFrame
    """All features, EPSG:4326, cleaned."""

    geometry: "ee.Geometry"
    """The working region: dissolved or single-feature, buffered."""

    area_ha: float
    centroid: Tuple[float, float]
    """(longitude, latitude) of the working region centroid."""

    feature_count: int
    source_name: str
    buffered_by_m: float = 0.0

    @property
    def latitude(self) -> float:
        return self.centroid[1]

    @property
    def bounds(self) -> Tuple[float, float, float, float]:
        """(minx, miny, maxx, maxy) in EPSG:4326."""
        return tuple(self.gdf.total_bounds)

    def within_gedi_coverage(self, lat_limit: float) -> bool:
        """GEDI's orbit bounds it to +/- ~51.6 degrees latitude."""
        _, miny, _, maxy = self.bounds
        return abs(miny) <= lat_limit and abs(maxy) <= lat_limit


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _enable_kml_driver() -> None:
    """Fiona/GDAL ships KML support read-only and sometimes disabled by default."""
    try:
        import fiona

        for driver in ("KML", "LIBKML"):
            if driver in fiona.drvsupport.supported_drivers:
                fiona.drvsupport.supported_drivers[driver] = "rw"
            else:
                fiona.drvsupport.supported_drivers[driver] = "rw"
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not adjust Fiona KML drivers: %s", exc)


def _read_kmz(data: bytes) -> gpd.GeoDataFrame:
    """A KMZ is a zip containing one or more KML documents."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
        if not kml_names:
            raise AOIError("This KMZ contains no KML document.")
        frames = []
        for name in kml_names:
            try:
                frames.append(gpd.read_file(io.BytesIO(zf.read(name)), driver="KML"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipped %s inside KMZ: %s", name, exc)
        if not frames:
            raise AOIError("None of the KML documents inside this KMZ could be read.")
        import pandas as pd

        return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=frames[0].crs)


def _read_kml(data: bytes) -> gpd.GeoDataFrame:
    """Read a KML, merging every layer — Google Earth exports often use folders."""
    buf = io.BytesIO(data)
    try:
        import fiona
        import pandas as pd

        layers = fiona.listlayers(buf)
        frames = []
        for layer in layers:
            buf.seek(0)
            try:
                frame = gpd.read_file(buf, driver="KML", layer=layer)
                if not frame.empty:
                    frames.append(frame)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipped KML layer %r: %s", layer, exc)
        if frames:
            return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=frames[0].crs)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Layer enumeration failed, falling back to single read: %s", exc)

    buf.seek(0)
    return gpd.read_file(buf, driver="KML")


def read_aoi_bytes(data: bytes, filename: str, max_mb: float = 1.0) -> gpd.GeoDataFrame:
    """Parse uploaded bytes into a cleaned EPSG:4326 GeoDataFrame.

    Parameters
    ----------
    data
        Raw file bytes.
    filename
        Original name — the suffix selects the driver.
    max_mb
        Size cap. Enforced before parsing, since the guard exists to bound
        Earth Engine payload size, not just memory.
    """
    size_mb = len(data) / (1024 * 1024)
    if size_mb > max_mb:
        raise AOIError(
            f"'{filename}' is {size_mb:.2f} MB, over the {max_mb:.0f} MB limit. "
            "Simplify the boundary (fewer vertices) or split it into separate runs — "
            "very detailed geometries also slow Earth Engine down considerably."
        )
    if not data:
        raise AOIError(f"'{filename}' is empty.")

    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise AOIError(
            f"Unsupported file type '{suffix}'. Upload KML, KMZ, GeoJSON, "
            "GeoPackage, or a zipped shapefile."
        )

    _enable_kml_driver()

    try:
        if suffix == ".kmz":
            gdf = _read_kmz(data)
        elif suffix == ".kml":
            gdf = _read_kml(data)
        elif suffix == ".zip":
            gdf = gpd.read_file(io.BytesIO(data))
        else:
            gdf = gpd.read_file(io.BytesIO(data))
    except AOIError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AOIError(
            f"Could not read '{filename}'. Confirm it is a valid {suffix[1:].upper()} "
            f"file containing polygon geometry.\n\nDetail: {exc}"
        ) from exc

    return clean_geodataframe(gdf, filename)


def clean_geodataframe(gdf: gpd.GeoDataFrame, source_name: str = "AOI") -> gpd.GeoDataFrame:
    """Drop empties, repair invalid rings, keep polygons, reproject to EPSG:4326."""
    if gdf is None or gdf.empty:
        raise AOIError(f"'{source_name}' contains no features.")

    gdf = gdf[~gdf.geometry.isna()].copy()
    gdf = gdf[~gdf.geometry.is_empty].copy()
    if gdf.empty:
        raise AOIError(f"'{source_name}' contains only empty geometries.")

    if gdf.crs is None:
        logger.warning("%s has no CRS; assuming EPSG:4326.", source_name)
        gdf = gdf.set_crs(epsg=4326)
    gdf = gdf.to_crs(epsg=4326)

    # buffer(0) is the standard trick for self-intersecting rings, which are common
    # in hand-drawn Google Earth polygons.
    invalid = ~gdf.geometry.is_valid
    if invalid.any():
        logger.info("Repairing %d invalid geometries in %s.", int(invalid.sum()), source_name)
        gdf.loc[invalid, "geometry"] = gdf.loc[invalid, "geometry"].buffer(0)

    polygons = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    if polygons.empty:
        kinds = ", ".join(sorted(gdf.geometry.geom_type.unique()))
        raise AOIError(
            f"'{source_name}' contains {kinds} but no polygons. An AOI must be a "
            "closed area — in Google Earth, use 'Add Polygon', not a path or placemark."
        )

    return polygons.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Geometry resolution
# ---------------------------------------------------------------------------

def compute_area_ha(geom, crs: str = "EPSG:4326") -> float:
    """Area in hectares via an equal-area projection."""
    series = gpd.GeoSeries([geom], crs=crs)
    return float(series.to_crs(EQUAL_AREA_CRS).area.iloc[0] / 10_000.0)


def build_aoi(gdf: gpd.GeoDataFrame, cfg: AOIConfig, source_name: str = "AOI") -> AOI:
    """Resolve the working region from a cleaned GeoDataFrame and AOI settings.

    Applies, in order: feature selection or dissolve, simplification, buffering.
    Buffering happens in an equal-area CRS so the metre distance means the same
    thing at every latitude.
    """
    if cfg.dissolve_features:
        working = unary_union(gdf.geometry.values)
    else:
        idx = cfg.target_feature_index
        if idx is None:
            working = gdf.geometry.iloc[0]
        elif 0 <= idx < len(gdf):
            working = gdf.geometry.iloc[idx]
        else:
            raise AOIError(
                f"Feature index {idx} is out of range — the file has {len(gdf)} "
                f"feature(s), indexed 0 to {len(gdf) - 1}."
            )

    if cfg.simplify_tolerance_m > 0:
        projected = gpd.GeoSeries([working], crs="EPSG:4326").to_crs(EQUAL_AREA_CRS)
        working = projected.simplify(cfg.simplify_tolerance_m).to_crs("EPSG:4326").iloc[0]

    if cfg.buffer_m != 0:
        projected = gpd.GeoSeries([working], crs="EPSG:4326").to_crs(EQUAL_AREA_CRS)
        buffered = projected.buffer(cfg.buffer_m)
        if buffered.iloc[0].is_empty:
            raise AOIError(
                f"A negative buffer of {cfg.buffer_m:.0f} m erases this AOI entirely. "
                "Use a smaller shrink distance."
            )
        working = buffered.to_crs("EPSG:4326").iloc[0]

    if not working.is_valid:
        working = working.buffer(0)
    if working.is_empty:
        raise AOIError("The resolved AOI geometry is empty.")

    area_ha = compute_area_ha(working)
    if area_ha <= 0:
        raise AOIError("The resolved AOI has zero area.")
    if area_ha > cfg.max_area_ha:
        raise AOIError(
            f"AOI is {area_ha:,.0f} ha, above the {cfg.max_area_ha:,.0f} ha limit. "
            "Earth Engine requests at this size routinely time out. Split the area "
            "into separate runs, or raise the limit in the sidebar if you accept "
            "longer processing times."
        )

    centroid = working.centroid
    ee_geom = ee.Geometry(mapping(working), proj="EPSG:4326", geodesic=False)

    return AOI(
        gdf=gdf,
        geometry=ee_geom,
        area_ha=area_ha,
        centroid=(float(centroid.x), float(centroid.y)),
        feature_count=len(gdf),
        source_name=source_name,
        buffered_by_m=cfg.buffer_m,
    )


def load_aoi(data: bytes, filename: str, cfg: AOIConfig) -> AOI:
    """Full ingestion: bytes -> validated, buffered, EE-ready AOI."""
    gdf = read_aoi_bytes(data, filename, max_mb=cfg.max_upload_mb)
    return build_aoi(gdf, cfg, source_name=filename)


# Bundled demo boundary, resolved relative to this module so it works from any
# working directory and on Streamlit Cloud (which clones the whole repo).
DEMO_AOI_PATH = Path(__file__).resolve().parent.parent / "tests" / "ARG_envelope.geojson"

# Extent of the bundled file, inlined so the demo still works if the file is absent
# from a partial deploy. Keep in sync with tests/ARG_envelope.geojson.
_DEMO_FALLBACK_COORDS = [
    (-64.669534, -23.632389), (-64.503881, -23.632389),
    (-64.503881, -23.484167), (-64.669534, -23.484167), (-64.669534, -23.632389),
]
_DEMO_NAME = "Demo AOI — Salta/Jujuy, Argentina"


def demo_aoi(cfg: Optional[AOIConfig] = None) -> AOI:
    """A small built-in AOI so the app is explorable before anyone uploads a file.

    A ~28,000 ha envelope in the subtropical dry forest of Salta/Jujuy, northern
    Argentina (~23.6 degrees S, 64.6 degrees W). It sits inside GEDI coverage and over
    real Hansen forest change, so every analysis phase returns something.

    Loads the bundled ``tests/ARG_envelope.geojson`` when present, and falls back to an
    inline polygon of the same extent if the file is missing — so a partial deploy
    cannot break the demo button.
    """
    cfg = cfg or AOIConfig()

    if DEMO_AOI_PATH.exists():
        try:
            return load_aoi(DEMO_AOI_PATH.read_bytes(), DEMO_AOI_PATH.name, cfg)
        except AOIError as exc:  # malformed file: fall back rather than dead-end
            logger.warning("Bundled demo AOI unreadable (%s); using inline fallback.", exc)

    from shapely.geometry import Polygon

    gdf = gpd.GeoDataFrame(
        {"name": [_DEMO_NAME]},
        geometry=[Polygon(_DEMO_FALLBACK_COORDS)], crs="EPSG:4326",
    )
    return build_aoi(gdf, cfg, source_name="ARG_envelope.geojson")
