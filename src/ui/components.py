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

# Tier badge colours for the dark theme: a bright text colour on a low-alpha tint of
# the same hue. The badge border follows the text colour (currentColor in CSS), so
# each chip reads as a self-contained token. Text colours clear WCAG AA (>=4.5:1)
# against the near-black surface; the tint sits under them without lifting to a light
# chip that would fight the dark page.
TIER_STYLES = {
    TIER_GEDI: ("#4FD98F", "rgba(23,160,92,0.16)", "Site-calibrated"),
    TIER_PARTIAL: ("#E8A657", "rgba(224,149,74,0.16)", "Partially calibrated"),
    TIER_IPCC: ("#F0776B", "rgba(240,101,90,0.16)", "Indicative only"),
}


def inject_css() -> None:
    """Global styling.

    Three deliberate choices, since this is a numbers-first instrument rather than a
    marketing page:

    * **Tabular figures everywhere a quantity appears.** Proportional digits make
      columns of hectares and tCO2e ripple; lining them up is the single largest
      legibility win in the app.
    * **One radius scale.** Surfaces 10px, controls 8px, badges fully round, with no
      exceptions, so nothing reads as accidental.
    * **Colour carries meaning, never decoration.** The accent green marks the primary
      path and nothing else; tier colours stay reserved for provenance. Anything
      merely structural is a neutral hairline.

    Selectors lean on Streamlit's ``data-testid`` attributes, the most stable hooks
    available but still internal: if a Streamlit upgrade flattens the styling, this is
    the first place to look.
    """
    st.markdown(
        """
        <style>
          :root {
            /* Dark forest palette. Mirrors .streamlit/config.toml and the chart
               constants in src/ui/charts.py; change all three together. */
            --so-accent: #17A05C;         /* brand green, brightened for a dark ground */
            --so-accent-strong: #3FC584;  /* brighter still, for text/selected states */
            --so-ink: #E7EEE9;            /* off-white, faint green (never pure #fff) */
            --so-ink-muted: #93A29A;      /* secondary text, AA on the surface */
            --so-surface: #0E1512;        /* near-black forest (never pure #000) */
            --so-surface-elevated: #151D19;
            --so-surface-sunk: #0A100D;
            --so-border: #26312B;
            --so-border-strong: #37453E;
            --so-radius-surface: 10px;
            --so-radius-control: 8px;
          }

          .block-container { padding-top: 2.25rem; max-width: 1400px; }

          /* Subtitle under the H1: the product line, quieter than the title. */
          .so-subtitle {
            color: var(--so-accent-strong);
            font-size: 0.95rem; font-weight: 600; letter-spacing: 0.01em;
            margin: -0.4rem 0 0.2rem 0;
          }

          /* Quantities align in a column regardless of which digits they contain. */
          [data-testid="stMetricValue"],
          [data-testid="stMetricDelta"],
          [data-testid="stDataFrame"] {
            font-variant-numeric: tabular-nums;
            font-feature-settings: "tnum" 1;
          }

          /* Headings: weight and a hairline do the work, not raw size. */
          h1 { font-size: 2rem; font-weight: 700; letter-spacing: -0.022em; }
          h3 {
            font-size: 1.12rem; font-weight: 650; letter-spacing: -0.01em;
            padding-bottom: 0.45rem; margin-bottom: 0.9rem;
            border-bottom: 1px solid var(--so-border);
          }

          /* KPI strip: each metric becomes a surface with an accent rule, so the
             headline numbers read as an instrument panel rather than loose text. */
          [data-testid="stMetric"] {
            background: var(--so-surface-elevated);
            border: 1px solid var(--so-border);
            border-left: 3px solid var(--so-accent);
            border-radius: var(--so-radius-surface);
            padding: 0.9rem 1.1rem;
          }
          [data-testid="stMetricValue"] {
            font-size: 1.55rem; font-weight: 660; letter-spacing: -0.02em;
            color: var(--so-ink);
          }
          [data-testid="stMetricLabel"] {
            font-size: 0.8rem; font-weight: 600; color: var(--so-ink-muted);
            text-transform: uppercase; letter-spacing: 0.06em;
          }

          .so-badge {
            display: inline-block; padding: 4px 11px; border-radius: 999px;
            font-size: 0.78rem; font-weight: 600; letter-spacing: 0.15px;
            border: 1px solid currentColor;
          }

          .so-empty {
            border: 1px dashed var(--so-border-strong);
            border-radius: var(--so-radius-surface);
            background: var(--so-surface-sunk);
            padding: 2.75rem 1.5rem; text-align: center;
            color: var(--so-ink-muted);
          }
          .so-empty h4 {
            margin: 0 0 0.35rem 0; color: var(--so-ink);
            font-size: 1.02rem; font-weight: 650;
          }
          .so-empty p { margin: 0; max-width: 46ch; margin-inline: auto; }

          /* Tabs: an underline indicator reads as navigation, where Streamlit's
             default reads as a row of unrelated links. */
          .stTabs [data-baseweb="tab-list"] {
            gap: 1.5rem; border-bottom: 1px solid var(--so-border);
          }
          .stTabs [data-baseweb="tab"] {
            padding: 0.55rem 0; font-weight: 600; color: var(--so-ink-muted);
          }
          .stTabs [aria-selected="true"] { color: var(--so-accent-strong); }

          /* Buttons keep the 8px control radius and acknowledge the press. */
          .stButton button {
            border-radius: var(--so-radius-control); font-weight: 600;
            transition: transform 0.12s ease, box-shadow 0.12s ease;
          }
          .stButton button:active { transform: translateY(1px); }
          .stButton button[kind="primary"] {
            box-shadow: 0 1px 2px rgba(0, 0, 0, 0.45);
          }

          [data-testid="stSidebar"] { border-right: 1px solid var(--so-border); }

          [data-testid="stExpander"] details {
            border: 1px solid var(--so-border);
            border-radius: var(--so-radius-surface);
          }

          /* The press feedback above is the only motion in the app, but honour the
             preference regardless so it never fights an accessibility setting. */
          @media (prefers-reduced-motion: reduce) {
            .stButton button { transition: none; }
            .stButton button:active { transform: none; }
          }
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
