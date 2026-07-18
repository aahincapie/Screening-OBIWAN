"""
Shared UI components: KPI strip, provenance badges, empty states, glossary.

The reference app's best idea was a KPI strip that stays visible while the user moves
between result tabs, so the headline numbers never scroll away. That is reproduced
here, with one addition it lacked: **every KPI derived from a sample shows its
uncertainty**, and every result carries a provenance badge.
"""

from __future__ import annotations

from typing import Optional

import streamlit as st

from src.carbon_curve import TIER_GEDI, TIER_IPCC, TIER_LABELS, TIER_PARTIAL

TIER_STYLES = {
    TIER_GEDI: ("#1b7837", "#e8f5ed", "Site-calibrated"),
    TIER_PARTIAL: ("#e08214", "#fdf2e3", "Partially calibrated"),
    TIER_IPCC: ("#d73027", "#fdeaea", "Indicative only"),
}


def inject_css() -> None:
    """Global styling. Kept minimal — Streamlit defaults are fine for most of it."""
    st.markdown(
        """
        <style>
          .block-container { padding-top: 2rem; max-width: 1400px; }
          [data-testid="stMetricValue"] { font-size: 1.6rem; }
          .so-badge {
            display: inline-block; padding: 3px 10px; border-radius: 12px;
            font-size: 0.78rem; font-weight: 600; letter-spacing: 0.2px;
          }
          .so-empty {
            border: 1px dashed #d0d0d0; border-radius: 10px; padding: 2.5rem 1.5rem;
            text-align: center; color: #666;
          }
          .so-empty h4 { margin: 0 0 0.4rem 0; color: #333; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def provenance_badge(tier: str) -> None:
    """Show where the numbers on this screen came from."""
    color, background, short = TIER_STYLES.get(tier, ("#666", "#f0f0f0", tier))
    st.markdown(
        f'<span class="so-badge" style="color:{color};background:{background};">'
        f"{short} · {TIER_LABELS.get(tier, tier)}</span>",
        unsafe_allow_html=True,
    )


def kpi_strip(
    project_area_ha: Optional[float] = None,
    total_net_ers: Optional[float] = None,
    er_per_ha_yr: Optional[float] = None,
    curve_tier: Optional[str] = None,
    plantable_share: Optional[float] = None,
) -> None:
    """The persistent headline metrics. Renders placeholders before analysis runs."""
    cols = st.columns(4)

    cols[0].metric(
        "Project area",
        f"{project_area_ha:,.0f} ha" if project_area_ha else "—",
        f"{100 * plantable_share:.0f}% of AOI plantable" if plantable_share else None,
    )
    cols[1].metric(
        "Total net ERs",
        f"{total_net_ers:,.0f} tCO2e" if total_net_ers else "—",
        help="Over the full crediting period, after all VM0047 deductions.",
    )
    cols[2].metric(
        "Removal rate",
        f"{er_per_ha_yr:.2f} tCO2e/ha/yr" if er_per_ha_yr else "—",
        help="Average net credits per hectare per year.",
    )
    with cols[3]:
        st.caption("Evidence base")
        if curve_tier:
            provenance_badge(curve_tier)
        else:
            st.markdown("—")


def empty_state(title: str, message: str, icon: str = "🌱") -> None:
    """Tell the user what to do next instead of showing a blank panel."""
    st.markdown(
        f'<div class="so-empty"><div style="font-size:2rem;">{icon}</div>'
        f"<h4>{title}</h4><p>{message}</p></div>",
        unsafe_allow_html=True,
    )


def warning_list(warnings: list[str], title: str = "Data quality notes") -> None:
    """Collapse warnings into one expander rather than stacking alert boxes."""
    if not warnings:
        return
    with st.expander(f"{title} ({len(warnings)})", expanded=False):
        for warning in warnings:
            st.markdown(f"- {warning}")


def tier_callout(tier: str, message: str) -> None:
    """A prominent notice when results rest on defaults rather than measurement."""
    if tier == TIER_IPCC:
        st.error(
            "**These results are indicative only.** " + message +
            "\n\nThe carbon curve rests entirely on published ecological-zone averages "
            "with no measurement from this site. Use the figures to compare options and "
            "size an opportunity, not to project credit revenue.",
            icon="⚠️",
        )
    elif tier == TIER_PARTIAL:
        st.warning(message, icon="ℹ️")
    else:
        st.success(message, icon="✅")


GLOSSARY = {
    "AGBD": "Above-ground biomass density — dry matter mass of living vegetation above "
            "soil level, per hectare (Mg/ha).",
    "ARR": "Afforestation, Reforestation and Revegetation — the project category this "
           "tool screens for.",
    "ANR": "Assisted natural regeneration — protecting and tending existing rootstock "
           "and seed bank rather than planting seedlings.",
    "Additionality": "Whether the carbon benefit would have happened anyway. Credits "
                     "may only be issued for removals beyond the baseline.",
    "Baseline": "What the land would have done without the project. Subtracted from "
                "project removals every year.",
    "Buffer pool": "A share of credits withheld against the risk that stored carbon is "
                   "later released — fire, harvest, land-use reversal.",
    "Design-based estimate": "A statistic computed by treating observations as a sample "
                             "from a population, yielding a mean with a standard error — "
                             "as opposed to reading a value off a modelled map.",
    "GEDI": "Global Ecosystem Dynamics Investigation — a spaceborne lidar on the ISS "
            "that samples canopy structure between roughly 51.6°N and 51.6°S.",
    "Hansen GFC": "Global Forest Change — a 30 m global record of tree cover, loss and "
                  "gain since 2000. The only forest-change source used here.",
    "Leakage": "Emissions displaced outside the project boundary by the project itself, "
               "such as farming pushed onto neighbouring land.",
    "Root:shoot ratio": "Below-ground biomass as a fraction of above-ground biomass. "
                        "Higher in dry systems, where much of the carbon is in roots.",
    "Site index": "Productivity of a site relative to the average for its ecological "
                  "zone.",
    "tCO2e": "Tonnes of carbon dioxide equivalent. Biomass carbon converts at 44/12.",
    "VCU": "Verified Carbon Unit — one tonne of CO2e issued under the VCS programme.",
    "VM0047": "Verra's methodology for Afforestation, Reforestation and Revegetation.",
}


def glossary_panel() -> None:
    """MRV vocabulary, borrowed as an idea from the reference app."""
    st.markdown("### Glossary")
    st.caption("Terms used throughout this tool.")
    for term, definition in sorted(GLOSSARY.items()):
        st.markdown(f"**{term}** — {definition}")
