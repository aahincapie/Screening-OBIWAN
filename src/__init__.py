"""Screening-OBIWAN analysis modules.

Layering, outermost first::

    app.py            Streamlit orchestration and state
    src/ui/           widgets, charts, maps  (presentation only)
    src/pipeline.py   phase sequencing
    src/aoi.py        AOI ingestion
    src/hansen.py     forest-change transitions   (Earth Engine)
    src/gedi.py       biomass sampling            (Earth Engine)
    src/estimators.py design-based statistics     (pure)
    src/carbon_curve.py accumulation modelling    (pure)
    src/vm0047.py     quantification engine       (pure)
    src/exports.py    CSV / XLSX / methods note
    config/           user-adjustable parameters

Everything from ``estimators`` downward is pure Python — no Earth Engine, no
Streamlit — so it is directly testable without credentials or a browser.
"""

__version__ = "1.0.0"
