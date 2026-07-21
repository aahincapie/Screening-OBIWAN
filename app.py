"""
Screening-OBIWAN — ex-ante ARR reforestation screening.

Streamlit entry point. This module orchestrates state and presentation only; every
calculation lives in ``src/`` and every parameter in ``config/``.

Run locally::

    streamlit run app.py
"""

from __future__ import annotations

import json
import logging
import os

import pandas as pd
import streamlit as st

from config.defaults import CLASS_LABELS, GEDI_LAT_LIMIT
from src import ee_auth, exports, pipeline
from src.aoi import AOIError, demo_aoi, load_aoi
from src.ui import charts, components, maps, sidebar

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

st.set_page_config(
    page_title="ARR Due Diligence - OBIWAN",
    page_icon="🌱",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def load_secrets() -> None:
    """Mirror Streamlit secrets into the environment.

    Keeps ``src/ee_auth.py`` free of any Streamlit dependency — it reads plain
    environment variables, and this is the only place that knows about ``st.secrets``.
    Existing environment variables win, so a local shell can always override.

    Recognised keys (all optional):
      ``EE_PROJECT_ID``           pin the Earth Engine project for this deployment
      ``EE_SERVICE_ACCOUNT_JSON`` service-account key, for a shared/pilot deployment
    """
    for key in ("EE_PROJECT_ID", "EE_SERVICE_ACCOUNT_JSON"):
        if os.environ.get(key):
            continue
        try:
            value = st.secrets[key]
        except Exception:  # noqa: BLE001 — no secrets file is the normal local case
            continue
        if value:
            # A TOML table is more forgiving to paste than an escaped JSON string,
            # so accept either shape for the service-account key.
            os.environ[key] = value if isinstance(value, str) else json.dumps(dict(value))


def init_state() -> None:
    defaults = {
        "ee_ready": False,
        "ee_state": None,
        "ee_project_id": "",
        "auth_attempted": False,
        "oauth_verifier": "",
        "aoi": None,
        "result": None,
        "suggested_zone": None,
        "analysis_error": "",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


# ---------------------------------------------------------------------------
# Authentication gate
# ---------------------------------------------------------------------------

def render_auth_gate() -> bool:
    """Sign the user in to their own Earth Engine project. Returns True when ready."""
    if st.session_state.ee_ready:
        return True

    hosted = ee_auth.is_hosted()
    configured_project = ee_auth.default_project_id()

    # Pilot / managed deployment: the operator has pinned a project and supplied
    # service-account credentials, so connect silently instead of asking every
    # visitor to authenticate. Quota lands on the operator's project by design.
    if configured_project and ee_auth.has_service_account() and not st.session_state.auth_attempted:
        st.session_state.auth_attempted = True
        with st.spinner("Connecting to Earth Engine…"):
            state = ee_auth.initialize(configured_project)
        if state.initialized:
            st.session_state.ee_ready = True
            st.session_state.ee_state = state
            st.session_state.ee_project_id = state.project_id
            st.rerun()
        else:
            st.warning(
                "This deployment is configured to use "
                f"`{configured_project}`, but the connection failed:\n\n"
                f"```\n{ee_auth.describe_failure(state.message)}\n```\n\n"
                "Sign in with your own account below to continue.",
                icon="⚠️",
            )

    st.markdown("## Connect to Earth Engine")
    if hosted:
        st.markdown(
            "This tool reads Hansen Global Forest Change and GEDI biomass data through "
            "**your** Earth Engine account, so quota and usage stay with you.\n\n"
            "Because this is a shared deployment, your sign-in lasts **for this session "
            "only** — the token is held in memory and never written to the server. You "
            "will sign in again on your next visit."
        )
    else:
        st.markdown(
            "This tool reads Hansen Global Forest Change and GEDI biomass data through "
            "**your** Earth Engine account, so quota and usage stay with you. Nothing is "
            "shared, and no credentials are stored by this app beyond your machine's "
            "standard Earth Engine credentials file."
        )

    project_id = st.text_input(
        "Earth Engine Cloud project ID",
        value=st.session_state.ee_project_id or configured_project,
        placeholder="my-ee-project",
        help=(
            "Find it at code.earthengine.google.com under the project selector, or in "
            "the Google Cloud console. You need an Earth Engine-enabled project — "
            "registration is free for research and non-commercial use."
        ),
    )

    col_connect, col_help = st.columns([1, 3])

    if col_connect.button("Connect", type="primary", disabled=not project_id):
        st.session_state.ee_project_id = project_id
        with st.spinner("Connecting to Earth Engine…"):
            state = ee_auth.initialize(project_id)
        if state.initialized:
            st.session_state.ee_ready = True
            st.session_state.ee_state = state
            st.success(state.message)
            st.rerun()
        else:
            st.error(ee_auth.describe_failure(state.message))
            st.session_state.analysis_error = state.message

    with st.expander("Sign in with Google", expanded=hosted):
        if hosted:
            st.markdown(
                "Authorise Earth Engine below. Nothing is stored on the server."
            )
        else:
            st.markdown(
                "The quickest fix is usually a one-off terminal command:\n\n"
                "```\nearthengine authenticate\n```\n\n"
                "Reload this page afterwards. If you cannot use a terminal, use the "
                "browser flow below instead."
            )

        if st.button("Generate sign-in link"):
            try:
                url, verifier = ee_auth.build_authorization_url()
                st.session_state.oauth_verifier = verifier
                st.markdown(f"**[Open this link to authorise Earth Engine]({url})**")
                st.caption("Approve access, then copy the code Google shows you.")
            except ee_auth.EEAuthError as exc:
                st.error(str(exc))

        code = st.text_input("Authorization code", type="password")
        if st.button("Complete sign-in", disabled=not code):
            state = ee_auth.complete_authorization(
                code, st.session_state.oauth_verifier, st.session_state.ee_project_id
            )
            if state.initialized:
                st.session_state.ee_ready = True
                st.session_state.ee_state = state
                st.rerun()
            else:
                st.error(state.message)

    return False


# ---------------------------------------------------------------------------
# AOI input
# ---------------------------------------------------------------------------

def render_aoi_input(aoi_cfg) -> None:
    st.markdown("### 1 · Define your project area")

    col_upload, col_demo = st.columns([3, 1])

    upload = col_upload.file_uploader(
        "Upload a boundary file",
        type=["kml", "kmz", "geojson", "json", "zip", "gpkg"],
        help=(
            f"KML up to {aoi_cfg.max_upload_mb:.0f} MB. KMZ, GeoJSON, GeoPackage and "
            "zipped shapefiles also work. The file must contain polygons — a path or "
            "placemark will not do."
        ),
    )

    col_demo.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    if col_demo.button("Try a demo area", use_container_width=True):
        st.session_state.aoi = demo_aoi(aoi_cfg)
        st.session_state.result = None
        st.rerun()

    if upload is not None:
        try:
            st.session_state.aoi = load_aoi(upload.getvalue(), upload.name, aoi_cfg)
            st.session_state.result = None
        except AOIError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not read that file: {exc}")


def render_aoi_summary(aoi) -> None:
    cols = st.columns(4)
    cols[0].metric("AOI area", f"{aoi.area_ha:,.0f} ha")
    cols[1].metric("Features", f"{aoi.feature_count:,}")
    cols[2].metric("Centroid", f"{aoi.latitude:.2f}°, {aoi.centroid[0]:.2f}°")

    in_coverage = aoi.within_gedi_coverage(GEDI_LAT_LIMIT)
    cols[3].metric(
        "GEDI coverage",
        "Available" if in_coverage else "Out of range",
        delta=None if in_coverage else f"beyond ±{GEDI_LAT_LIMIT}°",
        delta_color="off",
    )

    if not in_coverage:
        st.info(
            f"This AOI lies outside GEDI's ±{GEDI_LAT_LIMIT}° latitude band, so no "
            "spaceborne biomass measurements are available here. The carbon curve will "
            "use IPCC ecological-zone defaults, and results will be labelled "
            "**indicative only**. Everything else — Hansen transitions, areas, the "
            "VM0047 accounting — works exactly as normal.",
            icon="🛰️",
        )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

def render_results(result: pipeline.AnalysisResult) -> None:
    curve = result.curve
    engine = result.engine

    components.kpi_strip(
        project_area_ha=engine.setup.area_ha if engine else None,
        total_net_ers=engine.total_net_ers if engine else None,
        er_per_ha_yr=engine.er_per_ha_per_year if engine else None,
        curve_tier=result.tier,
        plantable_share=(
            result.transitions.plantable_ha / result.transitions.total_area_ha
            if result.transitions.total_area_ha else None
        ),
    )

    if curve and curve.calibration:
        components.tier_callout(curve.tier, curve.calibration.message)
    elif curve:
        components.tier_callout(
            curve.tier, "Carbon curve built from ecological-zone defaults."
        )

    components.warning_list(result.warnings)

    tabs = st.tabs([
        "Land cover", "Biomass", "Carbon curve", "Credits", "Sensitivity",
        "Data & exports", "Glossary",
    ])

    with tabs[0]:
        _tab_land_cover(result)
    with tabs[1]:
        _tab_biomass(result)
    with tabs[2]:
        _tab_curve(result)
    with tabs[3]:
        _tab_credits(result)
    with tabs[4]:
        _tab_sensitivity(result)
    with tabs[5]:
        _tab_exports(result)
    with tabs[6]:
        components.glossary_panel()


def _tab_land_cover(result: pipeline.AnalysisResult) -> None:
    transitions = result.transitions
    summary = pd.DataFrame(transitions.summary_rows())

    col_map, col_stats = st.columns([3, 2])

    with col_map:
        controls = st.columns([2, 3])
        reforestation_only = controls[0].toggle(
            "Reforestation only", value=False,
            help="Hides stable forest and regenerating land to isolate plantable area.",
        )
        basemap = controls[1].selectbox(
            "Basemap", list(maps.BASEMAPS.keys()), index=0,
            help="Dark shows the transition colors most clearly; Satellite adds context.",
        )

        from streamlit_folium import st_folium  # noqa: PLC0415

        fmap = maps.build_map(
            result.aoi, transitions.image, basemap, reforestation_only
        )
        st.caption(
            "White dashed outline is your AOI. Colored fill is the Hansen transition "
            "class per pixel (see legend below)."
        )
        st_folium(fmap, height=520, width=None, returned_objects=[])
        st.markdown(maps.legend_html(reforestation_only), unsafe_allow_html=True)

    with col_stats:
        st.metric("Plantable area", f"{transitions.plantable_ha:,.0f} ha",
                  f"{100 * transitions.plantable_ha / transitions.total_area_ha:.1f}% of AOI"
                  if transitions.total_area_ha else None)
        st.plotly_chart(charts.transition_bars(summary), use_container_width=True)
        st.dataframe(summary, use_container_width=True, hide_index=True)
        st.caption(
            f"Hansen `{transitions.asset}` · window {transitions.t_start_year}–"
            f"{transitions.t0_year} · canopy threshold {transitions.treecover_min_pct}%"
        )


def _tab_biomass(result: pipeline.AnalysisResult) -> None:
    if not result.gedi_result.available:
        components.empty_state(
            "No GEDI biomass data",
            result.gedi_result.reason,
            icon="🛰️",
        )
        return

    st.caption(
        f"{result.gedi_result.n_total:,} quality-screened footprints across "
        f"{len(result.config.gedi.years)} years."
    )

    as_carbon = st.toggle("Show as carbon (tCO2e/ha)", value=False)

    st.plotly_chart(charts.annual_trend(result.trend, as_carbon), use_container_width=True)

    if not result.change.empty:
        st.plotly_chart(charts.change_bars(result.change, as_carbon), use_container_width=True)

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**Historical additionality**")
        st.caption(
            "Observed change in the rate of biomass accrual between the baseline and "
            "project periods. This is a sanity check on whether the land is already "
            "changing — not the ex-ante additionality used for crediting."
        )
        if result.additionality.empty:
            st.info("Not enough overlapping years to compute rates.")
        else:
            st.dataframe(
                result.additionality[[
                    "stratum_label", "baseline_rate", "project_rate",
                    "additionality", "significant_95",
                ]].round(3),
                use_container_width=True, hide_index=True,
            )
    with col_b:
        st.markdown("**Footprint coverage**")
        coverage = result.gedi_result.coverage_table()
        if coverage.empty:
            st.info("No coverage table available.")
        else:
            st.dataframe(coverage, use_container_width=True)

    if not result.stock.empty:
        st.markdown("**Current carbon stock by stratum**")
        st.dataframe(result.stock.round(2), use_container_width=True, hide_index=True)


def _tab_curve(result: pipeline.AnalysisResult) -> None:
    curve = result.curve
    if curve is None:
        components.empty_state("No curve yet", "Run an analysis to build the curve.")
        return

    components.provenance_badge(curve.tier)

    frame = curve.to_frame(result.config.project.crediting_period_years)

    observed = None
    calibration = curve.calibration
    if calibration and calibration.observed is not None and not calibration.observed.empty:
        from src.estimators import agbd_to_co2e  # noqa: PLC0415

        obs = calibration.observed.copy()
        obs["age"] = obs["year"] - obs["year"].min() + calibration.advancement_years
        obs["tco2e_ha"] = agbd_to_co2e(
            obs["agbd_mean"], result.config.curve.carbon_fraction
        ) * (1 + curve.effective_root_shoot)
        observed = obs[["age", "tco2e_ha"]]

    ceiling_tco2e = None
    if curve.ceiling_agb:
        from src.estimators import agbd_to_co2e  # noqa: PLC0415

        ceiling_tco2e = float(
            agbd_to_co2e(curve.ceiling_agb, result.config.curve.carbon_fraction)
            * (1 + curve.effective_root_shoot)
        )

    st.plotly_chart(
        charts.carbon_curve(frame, ceiling_tco2e, observed, curve.tier_label),
        use_container_width=True,
    )

    cols = st.columns(4)
    half = curve.time_to_fraction(0.5)
    cols[0].metric("Ceiling", f"{ceiling_tco2e:,.0f} tCO2e/ha" if ceiling_tco2e else "—")
    cols[1].metric("Half of ceiling", f"year {half}" if half else "—")
    cols[2].metric("Stock at year 20", f"{frame.loc[min(20, len(frame) - 1), 'total_tco2e_ha']:,.0f} tCO2e/ha")
    cols[3].metric("Peak increment",
                   f"{frame['annual_increment_tco2e_ha'].max():,.2f} tCO2e/ha/yr")

    st.plotly_chart(charts.annual_increment(frame), use_container_width=True)

    with st.expander("How this curve was parameterised"):
        for line in curve.provenance:
            st.markdown(f"- {line}")
        st.markdown("**Species mix**")
        st.dataframe(
            pd.DataFrame([{
                "Cohort": s.name,
                "Area share": round(s.area_fraction, 3),
                "Growth model": s.growth_model,
                "Max AGB (t/ha)": round(s.agb_max, 1),
                "Stems/ha": s.planting_density,
                "Root:shoot": round(s.root_shoot_ratio, 3),
            } for s in curve.species]),
            use_container_width=True, hide_index=True,
        )

    if not result.projection.empty:
        st.markdown("### Scenario projection for the project stratum")
        st.caption(
            "How this stratum's biomass evolves under four futures. The restoration "
            "path uses the parameterised curve above; the baseline extrapolates the "
            "measured GEDI trend with its confidence band."
        )
        st.plotly_chart(
            charts.scenario_projection(result.projection), use_container_width=True
        )


def _tab_credits(result: pipeline.AnalysisResult) -> None:
    engine = result.engine
    if engine is None:
        components.empty_state(
            "No quantification",
            "The selected project stratum has no area in this AOI. Try the other "
            "reforestation class, or lower the canopy cover threshold.",
            icon="📉",
        )
        return

    summary = engine.summary
    setup = engine.setup

    cols = st.columns(4)
    cols[0].metric("Total net ERs", f"{summary['total_net_ers']:,.0f} tCO2e")
    cols[1].metric("Average annual", f"{summary['avg_annual_ers']:,.0f} tCO2e/yr")
    cols[2].metric("Peak year", f"{summary['peak_year']}",
                   f"{summary['peak_annual_ers']:,.0f} tCO2e")
    cols[3].metric("Total deductions", f"{summary['deduction_pct']:.1f}%")

    st.plotly_chart(charts.deduction_waterfall(summary), use_container_width=True)

    col_a, col_b = st.columns(2)
    col_a.plotly_chart(charts.er_timeline(engine.annual, False), use_container_width=True)
    col_b.plotly_chart(charts.er_timeline(engine.annual, True), use_container_width=True)

    with st.expander("Assumptions behind these numbers", expanded=True):
        st.markdown(f"**Baseline** — {result.baseline_note}")
        st.markdown(f"**Uncertainty** — {setup.uncertainty_note}")
        st.markdown(
            f"**Project area** — {setup.area_ha:,.0f} ha, being "
            f"{100 * result.config.project.plantable_fraction:.0f}% of the "
            f"{result.transitions.area_of(result.config.project.project_class):,.0f} ha "
            f"of {CLASS_LABELS[result.config.project.project_class].lower()} in this AOI."
        )
        if setup.project.phased_planting:
            st.markdown(
                f"**Establishment** — phased over {setup.project.planting_years} years."
            )

    st.markdown("### Annual detail")
    st.dataframe(engine.annual.round(2), use_container_width=True, hide_index=True)


def _tab_sensitivity(result: pipeline.AnalysisResult) -> None:
    if not result.scenarios:
        components.empty_state("No sensitivity run", "Quantification did not complete.")
        return

    st.caption(
        "How far the headline number moves under pessimistic and optimistic parameter "
        "choices. This is a sensitivity band, not a probability distribution — for a "
        "screening exercise, the spread matters more than the point estimate."
    )

    from src.vm0047 import scenario_comparison  # noqa: PLC0415

    st.plotly_chart(charts.scenario_comparison(result.scenarios), use_container_width=True)
    st.dataframe(scenario_comparison(result.scenarios), use_container_width=True, hide_index=True)

    base = result.scenarios.get("Base case")
    conservative = result.scenarios.get("Conservative")
    optimistic = result.scenarios.get("Optimistic")
    if base and conservative and optimistic:
        low = conservative.total_net_ers
        high = optimistic.total_net_ers
        st.info(
            f"Plausible range: **{low:,.0f} – {high:,.0f} tCO2e** over the crediting "
            f"period, against a base case of {base.total_net_ers:,.0f} tCO2e. "
            f"That is a spread of {100 * (high - low) / base.total_net_ers:.0f}% of "
            "the base case — quote the range, not the midpoint.",
            icon="📊",
        )


def _tab_exports(result: pipeline.AnalysisResult) -> None:
    st.markdown("### Downloads")
    st.caption(
        "Every export carries a methods note recording the provenance tier, the "
        "parameters used, and the limitations of the underlying data."
    )

    project_name = result.config.project.project_name

    note = ""
    if result.engine and result.curve:
        note = exports.methods_note(
            curve=result.curve,
            results=result.engine,
            aoi_name=result.aoi.source_name,
            aoi_area_ha=result.aoi.area_ha,
            hansen_asset=result.transitions.asset,
            window=(result.transitions.t_start_year, result.transitions.t0_year),
            gedi_available=result.gedi_result.available,
            gedi_reason=result.gedi_result.reason,
        )

    cols = st.columns(3)

    if result.engine and result.curve:
        try:
            workbook = exports.build_workbook(
                result.engine, result.curve, result.scenarios,
                pd.DataFrame(result.transitions.summary_rows()), result.trend,
            )
            cols[0].download_button(
                "VM0047 workbook (.xlsx)", workbook,
                file_name=exports.workbook_filename(project_name),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        except Exception as exc:  # noqa: BLE001
            cols[0].error(f"Workbook export failed: {exc}")

    cols[1].download_button(
        "All tables (.zip)",
        exports.build_csv_bundle(result.tables(), note),
        file_name=exports.bundle_filename(project_name),
        mime="application/zip",
        use_container_width=True,
    )

    cols[2].download_button(
        "Run configuration (.json)",
        exports.config_json(result.config.to_dict()),
        file_name="screening_config.json",
        mime="application/json",
        use_container_width=True,
        help="Reproduce this exact run later.",
    )

    if note:
        with st.expander("Methods note preview"):
            st.markdown(note)

    st.markdown("### Tables")
    for name, frame in result.tables().items():
        with st.expander(name.replace("_", " ").title()):
            st.dataframe(frame, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    load_secrets()
    init_state()
    components.inject_css()

    st.title("ARR Due Diligence")
    st.markdown(
        '<div class="so-subtitle">OBIWAN · Powered by NASA GEDI biomass data</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Ex-ante screening for reforestation (ARR) carbon projects, anywhere on Earth. "
        "Hansen forest change · GEDI biomass · VM0047 quantification."
    )

    if not render_auth_gate():
        st.divider()
        st.markdown(
            "#### What this tool does\n"
            "1. Reads your project boundary from a KML file\n"
            "2. Classifies forest change with Hansen Global Forest Change — the same "
            "dataset everywhere, so results are comparable across countries\n"
            "3. Identifies **stable non-forest** and **recently cleared** land as "
            "reforestation candidates\n"
            "4. Measures current biomass from GEDI spaceborne lidar, with proper "
            "confidence intervals\n"
            "5. Builds a carbon accumulation curve you parameterise by intervention "
            "type, species mix, ecological zone and site conditions\n"
            "6. Runs VM0047 ex-ante quantification to a credit estimate with a "
            "sensitivity range"
        )
        return

    config = sidebar.render(st.session_state.suggested_zone)

    errors = config.validate()
    if errors:
        for error in errors:
            st.sidebar.error(error)

    render_aoi_input(config.aoi)

    aoi = st.session_state.aoi
    if aoi is None:
        components.empty_state(
            "Upload a project boundary to begin",
            "A KML file up to 1 MB, containing the polygon you want to assess. "
            "Or press “Try a demo area” to explore the tool first.",
            icon="📍",
        )
        return

    render_aoi_summary(aoi)

    st.divider()
    st.markdown("### 2 · Run the analysis")

    col_run, col_status = st.columns([1, 3])
    run_clicked = col_run.button(
        "Run screening", type="primary", disabled=bool(errors), use_container_width=True
    )
    if errors:
        col_status.warning("Fix the configuration errors in the sidebar first.")

    if run_clicked:
        progress_bar = st.progress(0.0, text="Starting…")

        def progress(label: str, fraction: float) -> None:
            progress_bar.progress(min(fraction, 1.0), text=label)

        # Re-bind Earth Engine's process-global session to THIS visitor's credentials
        # before touching the API. On a shared host another visitor may have signed in
        # since this session authenticated, which would otherwise silently redirect
        # these requests onto their quota. See src/ee_auth.py for the residual caveat.
        if not ee_auth.activate(st.session_state.ee_state):
            st.session_state.ee_ready = False
            st.session_state.analysis_error = (
                "Your Earth Engine session expired. Sign in again to continue."
            )
            st.rerun()

        try:
            result = pipeline.run(config, aoi, progress)
            st.session_state.result = result
            st.session_state.suggested_zone = result.suggested_zone
            st.session_state.analysis_error = ""
        except Exception as exc:  # noqa: BLE001
            st.session_state.analysis_error = str(exc)
            logging.exception("Analysis failed")
        finally:
            progress_bar.empty()

    if st.session_state.analysis_error:
        st.error(
            "The analysis did not complete.\n\n"
            f"```\n{st.session_state.analysis_error}\n```\n\n"
            + ee_auth.describe_failure(st.session_state.analysis_error)
        )

    result = st.session_state.result
    if result is None:
        components.empty_state(
            "Ready to run",
            "Adjust the parameters in the sidebar, then press “Run screening”. "
            "Nothing is computed until you do.",
            icon="⚙️",
        )
        return

    st.divider()
    st.markdown("### 3 · Results")
    render_results(result)

    st.divider()
    st.caption(
        "Screening-OBIWAN is an **ex-ante screening tool**. Its outputs are not a "
        "validated VM0047 quantification and do not substitute for a PDD, a field "
        "inventory, or assessment by a validation and verification body. "
        "Data: Hansen Global Forest Change (UMD) · GEDI L4A (NASA/UMD) · "
        "Methodology: Verra VM0047."
    )


if __name__ == "__main__":
    main()
