"""
Plotly figures.

Two rules run through every chart here:

**Uncertainty is never optional.** Any estimate derived from a sample is drawn with
its confidence band. A carbon number without a CI reads as a measurement, gets quoted
as one, and that is precisely the failure mode this tool exists to avoid. The
reference app showed none anywhere.

**Colour carries meaning.** Sequestration is green, loss is red, baseline is grey,
and the transition classes reuse the map palette so the eye can move between the map
and the charts without relearning anything.
"""

from __future__ import annotations

from typing import Dict, Optional

import pandas as pd
import plotly.graph_objects as go

from config.defaults import CLASS_COLORS, CLASS_LABELS

# Dark-theme chart palette, mirrored with the CSS tokens in src/ui/components.py and
# .streamlit/config.toml — change all three together. On a near-black ground the marks
# are brightened for legibility (a plotted mark needs ~3:1 contrast): GREEN is the
# single brand accent, the rest are semantic signals, not decoration.
GREEN = "#35C285"
RED = "#F0655A"
BLUE = "#5B9BE0"
GREY = "#8A978F"
AMBER = "#E0954A"

INK = "#E7EEE9"          # off-white text (titles, hover, in-chart marks)
INK_MUTED = "#93A29A"    # tick labels, axis titles
GRID = "#26312B"         # recessive grid, barely above the surface
SURFACE_ELEVATED = "#151D19"
BORDER_STRONG = "#37453E"

LAYOUT = dict(
    template="plotly_dark",
    margin=dict(l=60, r=30, t=60, b=50),
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    height=420,
    # Transparent backgrounds let each chart sit directly on the app's dark surface
    # instead of stamping a slightly-different rectangle onto it. A recessive grid and
    # muted ticks push the data forward. These use Plotly's magic-underscore form
    # rather than ``xaxis=dict(...)`` so they cannot collide with the ``xaxis_title``
    # that _apply passes alongside them.
    font=dict(size=12, color=INK_MUTED),
    xaxis_gridcolor=GRID,
    xaxis_zerolinecolor=GRID,
    xaxis_linecolor=GRID,
    yaxis_gridcolor=GRID,
    yaxis_zerolinecolor=GRID,
    yaxis_linecolor=GRID,
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    hoverlabel=dict(bgcolor=SURFACE_ELEVATED, bordercolor=BORDER_STRONG,
                    font=dict(color=INK)),
)

TITLE_FONT = dict(size=15, color=INK)


def _apply(fig: go.Figure, title: str, x_title: str, y_title: str) -> go.Figure:
    # Title text and title styling go in together, because passing ``title`` here and
    # a ``title`` key inside LAYOUT would be a duplicate keyword argument.
    fig.update_layout(
        title=dict(text=title, font=TITLE_FONT),
        xaxis_title=x_title,
        yaxis_title=y_title,
        **LAYOUT,
    )
    return fig


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


# ---------------------------------------------------------------------------
# Land cover
# ---------------------------------------------------------------------------

def transition_bars(summary: pd.DataFrame) -> go.Figure:
    """Area by transition class, coloured by the map palette."""
    fig = go.Figure(go.Bar(
        x=summary["class"],
        y=summary["area_ha"],
        marker_color=[CLASS_COLORS.get(int(c), GREY) for c in summary["code"]],
        text=[f"{v:,.0f} ha<br>{p:.1f}%" for v, p in zip(summary["area_ha"], summary["share_pct"])],
        textposition="outside",
        hovertemplate="%{x}<br>%{y:,.0f} ha<extra></extra>",
    ))
    return _apply(fig, "Land cover transitions", "", "Area (ha)")


# ---------------------------------------------------------------------------
# GEDI trend
# ---------------------------------------------------------------------------

def annual_trend(trend: pd.DataFrame, as_carbon: bool = False) -> go.Figure:
    """Annual mean AGBD (or tCO2e/ha) per stratum, with 95% confidence bands."""
    mean_col = "co2e_mean" if as_carbon else "agbd_mean"
    se_col = "co2e_se" if as_carbon else "agbd_se"
    unit = "tCO2e/ha" if as_carbon else "Mg/ha"

    fig = go.Figure()
    for stratum, group in trend.groupby("stratum"):
        group = group.sort_values("year")
        color = CLASS_COLORS.get(int(stratum), GREY)
        label = CLASS_LABELS.get(int(stratum), str(stratum))

        upper = group[mean_col] + 1.96 * group[se_col]
        lower = group[mean_col] - 1.96 * group[se_col]

        fig.add_trace(go.Scatter(
            x=list(group["year"]) + list(group["year"])[::-1],
            y=list(upper) + list(lower)[::-1],
            fill="toself", fillcolor=_rgba(color, 0.15),
            line=dict(color="rgba(0,0,0,0)"),
            hoverinfo="skip", showlegend=False,
        ))
        fig.add_trace(go.Scatter(
            x=group["year"], y=group[mean_col],
            mode="lines+markers", name=label,
            line=dict(color=color, width=2.5), marker=dict(size=7),
            customdata=group[["n", se_col]].values,
            hovertemplate=(
                f"{label}<br>%{{y:.1f}} {unit}"
                "<br>SE %{customdata[1]:.1f} · n=%{customdata[0]}<extra></extra>"
            ),
        ))

    return _apply(
        fig, f"Annual biomass by transition class ({unit}, shaded = 95% CI)",
        "Year", unit,
    )


def change_bars(change: pd.DataFrame, as_carbon: bool = False) -> go.Figure:
    """Change between two years per stratum, with 95% error bars.

    Bars whose interval crosses zero are drawn hollow — a change that is not
    statistically distinguishable from zero should not look like a finding.
    """
    value_col = "co2e_change" if as_carbon else "agbd_change"
    se_col = "co2e_change_se" if as_carbon else "change_se"
    unit = "tCO2e/ha" if as_carbon else "Mg/ha"

    significant = change["significant_95"] if "significant_95" in change else [True] * len(change)
    colors = [GREEN if v >= 0 else RED for v in change[value_col]]

    fig = go.Figure(go.Bar(
        x=change["stratum_label"], y=change[value_col],
        error_y=dict(type="data", array=1.96 * change[se_col], visible=True, color=INK_MUTED),
        marker=dict(
            color=[c if s else "rgba(0,0,0,0)" for c, s in zip(colors, significant)],
            line=dict(color=colors, width=2),
        ),
        hovertemplate=f"%{{x}}<br>%{{y:+.1f}} {unit}<extra></extra>",
    ))
    fig.add_hline(y=0, line_width=1, line_color=BORDER_STRONG)

    y0, y1 = (change["year_0"].iloc[0], change["year_1"].iloc[0]) if len(change) else ("", "")
    fig = _apply(fig, f"Biomass change {y0}-{y1} (95% CI)", "", unit)
    fig.add_annotation(
        text="Hollow bars: interval crosses zero, not significant at 95%",
        xref="paper", yref="paper", x=0, y=-0.18, showarrow=False,
        font=dict(size=11, color=INK_MUTED),
    )
    return fig


# ---------------------------------------------------------------------------
# Carbon curve
# ---------------------------------------------------------------------------

def carbon_curve(
    curve_frame: pd.DataFrame,
    ceiling_tco2e: Optional[float] = None,
    observed: Optional[pd.DataFrame] = None,
    tier_label: str = "",
) -> go.Figure:
    """The accumulation curve per hectare, with the mature ceiling and any GEDI fit."""
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=curve_frame["age"], y=curve_frame["total_tco2e_ha"],
        mode="lines", name="Total (AGB + BGB)",
        line=dict(color=GREEN, width=3),
        hovertemplate="Age %{x} yr<br>%{y:,.1f} tCO2e/ha<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=curve_frame["age"], y=curve_frame["agb_tco2e_ha"],
        mode="lines", name="Above-ground only",
        line=dict(color=GREEN, width=1.5, dash="dot"),
    ))

    if ceiling_tco2e and ceiling_tco2e > 0:
        fig.add_hline(
            y=ceiling_tco2e, line_dash="dash", line_color=GREY,
            annotation_text="Mature forest ceiling", annotation_position="right",
        )

    if observed is not None and not observed.empty:
        fig.add_trace(go.Scatter(
            x=observed["age"], y=observed["tco2e_ha"],
            mode="markers", name="Observed (GEDI)",
            marker=dict(color=INK, size=9, symbol="circle-open", line=dict(width=2)),
        ))

    title = "Carbon accumulation curve (per hectare)"
    if tier_label:
        title += f" — {tier_label}"
    return _apply(fig, title, "Stand age (years)", "tCO2e / ha")


def annual_increment(curve_frame: pd.DataFrame) -> go.Figure:
    """Annual increment — where the curve is actually generating credits."""
    fig = go.Figure(go.Bar(
        x=curve_frame["age"], y=curve_frame["annual_increment_tco2e_ha"],
        marker_color=GREEN, opacity=0.75,
        hovertemplate="Age %{x} yr<br>%{y:,.2f} tCO2e/ha/yr<extra></extra>",
    ))
    return _apply(fig, "Annual increment", "Stand age (years)", "tCO2e / ha / yr")


# ---------------------------------------------------------------------------
# Scenarios & ERs
# ---------------------------------------------------------------------------

def scenario_projection(projection: pd.DataFrame, as_density: bool = False) -> go.Figure:
    """The four historical scenarios, with the baseline's confidence band."""
    suffix = "_agbd" if as_density else "_tco2e"
    unit = "Mg/ha" if as_density else "tCO2e (AOI)"

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=list(projection["year"]) + list(projection["year"])[::-1],
        y=list(projection[f"Baseline_hi{suffix}"]) + list(projection[f"Baseline_lo{suffix}"])[::-1],
        fill="toself", fillcolor=_rgba(GREY, 0.18),
        line=dict(color="rgba(0,0,0,0)"), name="Baseline 95% CI",
        hoverinfo="skip",
    ))

    for name, color, dash in [
        ("Baseline", GREY, "solid"),
        ("Restoration", BLUE, "dash"),
        ("Conservation", GREEN, "dash"),
        ("Accelerated", RED, "dash"),
    ]:
        fig.add_trace(go.Scatter(
            x=projection["year"], y=projection[f"{name}{suffix}"],
            mode="lines", name=name,
            line=dict(color=color, width=2.5, dash=dash),
        ))

    return _apply(fig, "Scenario projection", "Year", unit)


def er_timeline(annual: pd.DataFrame, cumulative: bool = False) -> go.Figure:
    """Net emission reductions over the crediting period."""
    if cumulative:
        fig = go.Figure(go.Scatter(
            x=annual["calendar_year"], y=annual["cumulative_net_ers_tco2e"],
            mode="lines", fill="tozeroy",
            line=dict(color=GREEN, width=3), fillcolor=_rgba(GREEN, 0.2),
            name="Cumulative net ERs",
            hovertemplate="%{x}<br>%{y:,.0f} tCO2e cumulative<extra></extra>",
        ))
        return _apply(fig, "Cumulative net ERs / VCUs", "Year", "tCO2e")

    fig = go.Figure(go.Bar(
        x=annual["calendar_year"], y=annual["net_ers_tco2e"],
        marker_color=GREEN, name="Net ERs",
        hovertemplate="%{x}<br>%{y:,.0f} tCO2e<extra></extra>",
    ))
    return _apply(fig, "Annual net ERs / VCUs", "Year", "tCO2e / yr")


def deduction_waterfall(summary: Dict[str, float]) -> go.Figure:
    """Where the gross removals go — the single most useful chart for a developer.

    Everyone asks "why is the net so much lower than the gross?". This answers it in
    one glance instead of a table of six deduction lines.
    """
    gross = summary["total_gross_removals"]
    steps = [
        ("Gross removals", gross, "absolute"),
        ("Project emissions", -summary["total_project_emissions"], "relative"),
        ("Leakage", -summary["total_leakage"], "relative"),
        ("Performance benchmark", -summary["total_pb_ded"], "relative"),
        ("Uncertainty", -summary["total_uncertainty_ded"], "relative"),
        ("Buffer", -summary["total_buffer"], "relative"),
        ("Net ERs / VCUs", summary["total_net_ers"], "total"),
    ]

    fig = go.Figure(go.Waterfall(
        orientation="v",
        measure=[m for _, _, m in steps],
        x=[label for label, _, _ in steps],
        y=[value for _, value, _ in steps],
        text=[f"{abs(v):,.0f}" for _, v, _ in steps],
        textposition="outside",
        connector=dict(line=dict(color=BORDER_STRONG)),
        increasing=dict(marker=dict(color=GREEN)),
        decreasing=dict(marker=dict(color=AMBER)),
        totals=dict(marker=dict(color=BLUE)),
    ))
    return _apply(fig, "From gross removals to issued credits", "", "tCO2e")


def scenario_comparison(scenarios: Dict[str, "object"]) -> go.Figure:
    """Cumulative ERs under the sensitivity scenarios."""
    colors = {"Base case": GREEN, "Conservative": RED, "Optimistic": BLUE}
    fig = go.Figure()
    for name, result in scenarios.items():
        annual = result.annual
        fig.add_trace(go.Scatter(
            x=annual["calendar_year"], y=annual["cumulative_net_ers_tco2e"],
            mode="lines", name=name,
            line=dict(color=colors.get(name, GREY), width=2.5),
        ))
    return _apply(fig, "Sensitivity: cumulative net ERs", "Year", "tCO2e")
