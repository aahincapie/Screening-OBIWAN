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

# "Dark" is first, so it is the default: against a near-black ground the white AOI
# outline and the colored transition raster both read clearly, where the same layers
# over busy satellite imagery turn muddy. Satellite stays available for context.
BASEMAPS = {
    "Dark": {
        "tiles": "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
        "attr": "CARTO",
    },
    "Satellite": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attr": "Esri World Imagery",
    },
    "Terrain": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
        "attr": "Esri World Topo",
    },
    "Street": {"tiles": "OpenStreetMap", "attr": None},
}


def _add_ee_layer(fmap: folium.Map, image, vis_params: dict, name: str, opacity: float = 0.75) -> None:
    """Add an Earth Engine image as a tile layer."""
    try:
        map_id = image.getMapId(vis_params)
        folium.raster_layers.TileLayer(
            tiles=map_id["tile_fetcher"].url_format,
            attr="Google Earth Engine",
            name=name,
            overlay=True,
            control=True,
            opacity=opacity,
        ).add_to(fmap)
    except Exception as exc:  # noqa: BLE001 — a missing layer must not blank the map
        logger.warning("Could not add EE layer %r: %s", name, exc)


def build_map(
    aoi,
    transition_image=None,
    basemap: str = "Dark",
    show_reforestation_only: bool = False,
    opacity: float = 0.82,
) -> folium.Map:
    """Build the main map: basemap, AOI outline, and the transition raster.

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

    fmap = folium.Map(
        location=[lat, lon],
        zoom_start=12,
        tiles=base["tiles"],
        attr=base["attr"],
        control_scale=True,
    )

    folium.GeoJson(
        aoi.gdf.to_json(),
        name="Area of interest",
        style_function=lambda _: {
            "color": "#ffffff", "weight": 2.5, "fillOpacity": 0.0, "dashArray": "6,4",
        },
    ).add_to(fmap)

    if transition_image is not None:
        from src.hansen import reforestation_mask, visualization_params  # noqa: PLC0415

        image = transition_image
        layer_name = "Hansen transitions"
        if show_reforestation_only:
            image = transition_image.updateMask(reforestation_mask(transition_image))
            layer_name = "Reforestation candidates"

        _add_ee_layer(fmap, image, visualization_params(), layer_name, opacity)

    minx, miny, maxx, maxy = aoi.bounds
    fmap.fit_bounds([[miny, minx], [maxy, maxx]])

    folium.LayerControl(collapsed=True).add_to(fmap)
    return fmap


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
