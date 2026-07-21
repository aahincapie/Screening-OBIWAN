"""
Folium map rendering for the AOI and the Hansen transition layer.

Earth Engine images are added as XYZ tile layers via ``getMapId``, so nothing is
downloaded and the map stays responsive over large AOIs.
"""

from __future__ import annotations

import logging
from typing import Optional

import folium

from config.defaults import ALL_CLASSES, CLASS_COLORS, CLASS_LABELS, REFORESTATION_CLASSES

logger = logging.getLogger(__name__)

# Satellite is the default. Dark stays available for when the transition colours need
# to pop against a plain ground; Terrain and Street add cartographic context.
BASEMAPS = {
    "Satellite": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attr": "Esri World Imagery",
    },
    "Dark": {
        "tiles": "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
        "attr": "CARTO",
    },
    "Terrain": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
        "attr": "Esri World Topo",
    },
    "Street": {"tiles": "OpenStreetMap", "attr": None},
}


def _add_ee_layer(fmap: folium.Map, image, vis_params: dict, name: str,
                  opacity: float = 0.82) -> Optional[str]:
    """Add an Earth Engine image as a tile layer.

    Returns ``None`` on success, or the error message on failure. The caller decides
    how to surface it — previously this swallowed the exception, which left the map
    silently missing its most important layer with no way to tell why.
    """
    try:
        map_id = image.getMapId(vis_params)
        tile_url = _tile_url(map_id)
        folium.raster_layers.TileLayer(
            tiles=tile_url,
            attr="Google Earth Engine",
            name=name,
            overlay=True,
            control=True,
            show=True,
            opacity=opacity,
        ).add_to(fmap)
        return None
    except Exception as exc:  # noqa: BLE001 — reported to the caller, not swallowed
        logger.error("Could not add EE layer %r: %s", name, exc)
        return str(exc)


def _tile_url(map_id) -> str:
    """Extract the XYZ tile URL from a getMapId result.

    The return shape of ``getMapId`` has shifted across earthengine-api releases: newer
    builds expose ``map_id['tile_fetcher'].url_format``, some a bare ``tile_fetcher``
    string, older ones only a ``mapid`` (with optional ``token``) that has to be
    assembled into the tiles endpoint. Handle all three rather than assume one.
    """
    if not isinstance(map_id, dict):
        return getattr(map_id, "url_format", str(map_id))

    fetcher = map_id.get("tile_fetcher")
    if fetcher is not None:
        return getattr(fetcher, "url_format", str(fetcher))

    mapid = map_id.get("mapid", "")
    token = map_id.get("token", "")
    if token:
        return (f"https://earthengine.googleapis.com/map/{mapid}/"
                "{z}/{x}/{y}?token=" + token)
    return f"https://earthengine.googleapis.com/v1/{mapid}/tiles/{{z}}/{{x}}/{{y}}"


def build_map(
    aoi,
    transition_image=None,
    basemap: str = "Satellite",
    show_reforestation_only: bool = False,
    opacity: float = 0.82,
) -> tuple[folium.Map, Optional[str]]:
    """Build the main map and report whether the transition raster loaded.

    Returns ``(map, error)`` where ``error`` is ``None`` on success or the reason the
    Hansen transition layer could not be added. The caller shows the error so an empty
    map is never a silent mystery.

    Parameters
    ----------
    aoi
        A :class:`src.aoi.AOI`.
    transition_image
        Hansen transition ``ee.Image``, or None before analysis has run.
    show_reforestation_only
        Mask out the control classes so only plantable land is coloured. This is the
        view that matters for an ARR screen, so it is worth a one-click toggle.
    """
    lon, lat = aoi.centroid
    base = BASEMAPS.get(basemap, BASEMAPS["Satellite"])

    # tiles=None + an explicit named TileLayer, so LayerControl shows a clean basemap
    # name ("Satellite") instead of the raw tile URL.
    fmap = folium.Map(location=[lat, lon], zoom_start=12, tiles=None, control_scale=True)
    folium.TileLayer(
        tiles=base["tiles"], attr=base["attr"] or basemap, name=basemap,
        control=True, overlay=False,
    ).add_to(fmap)

    error: Optional[str] = None
    if transition_image is not None:
        from src.hansen import reforestation_mask, transition_vis  # noqa: PLC0415

        image = transition_image
        layer_name = "Hansen transitions"
        if show_reforestation_only:
            image = transition_image.updateMask(reforestation_mask(transition_image))
            layer_name = "Reforestation candidates"

        styled, vis = transition_vis(image)
        error = _add_ee_layer(fmap, styled, vis, layer_name, opacity)

    folium.GeoJson(
        aoi.gdf.to_json(),
        name="Area of interest",
        style_function=lambda _: {
            "color": "#ffffff", "weight": 2.5, "fillOpacity": 0.0, "dashArray": "6,4",
        },
    ).add_to(fmap)

    minx, miny, maxx, maxy = aoi.bounds
    fmap.fit_bounds([[miny, minx], [maxy, maxx]])

    # Expanded, so the transition layer toggle is visible at a glance.
    folium.LayerControl(collapsed=False).add_to(fmap)
    return fmap, error


def legend_html(reforestation_only: bool = False) -> str:
    """A compact legend rendered beneath the map.

    Drawn as HTML rather than a Folium overlay so it participates in the page layout
    and stays readable on mobile, where floating map legends get clipped.
    """
    codes = REFORESTATION_CLASSES if reforestation_only else ALL_CLASSES
    items = "".join(
        f'<span style="display:inline-flex;align-items:center;margin-right:18px;'
        f'margin-bottom:4px;font-size:0.85rem;">'
        f'<span style="width:14px;height:14px;background:{CLASS_COLORS[c]};'
        f'border-radius:3px;display:inline-block;margin-right:6px;"></span>'
        f"{CLASS_LABELS[c]}</span>"
        for c in codes
    )
    return f'<div style="padding:8px 2px;line-height:1.8;">{items}</div>'
