"""
Smoke tests for the pure-Python core.

Everything from ``src/estimators.py`` downward runs without Earth Engine credentials
or a browser, so it can be tested directly. That separation is deliberate: the parts
most likely to be wrong in a carbon tool are the statistics and the accounting, and
those must be testable without a network round-trip.

Run with::

    python -m pytest tests/ -v
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from config.defaults import (
    CarbonCurveConfig,
    DeductionConfig,
    GEDIConfig,
    PoolConfig,
    ProjectConfigParams,
    STABLE_FOREST,
    STABLE_NONFOREST,
    FOREST_GAIN,
    AppConfig,
)
from config.species import default_mix_for
from src import carbon_curve, estimators, vm0047


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def synthetic_footprints() -> pd.DataFrame:
    """Three strata over six years, with a known regeneration signal in class 21."""
    rng = np.random.default_rng(42)
    rows = []
    for year in range(2019, 2025):
        elapsed = year - 2019
        for stratum, base, slope in [
            (STABLE_FOREST, 180.0, 0.5),
            (STABLE_NONFOREST, 20.0, 0.0),
            (FOREST_GAIN, 40.0, 6.0),
        ]:
            mean = base + slope * elapsed
            values = rng.normal(mean, 25.0, size=120)
            for value in values:
                rows.append({
                    "agbd": max(0.0, value),
                    "agbd_se": 12.0,
                    "year": year,
                    "stratum": stratum,
                })
    return pd.DataFrame(rows)


@pytest.fixture
def trend(synthetic_footprints) -> pd.DataFrame:
    return estimators.annual_stratum_table(synthetic_footprints)


# ---------------------------------------------------------------------------
# Estimators
# ---------------------------------------------------------------------------

def test_design_based_estimate_recovers_the_mean():
    rng = np.random.default_rng(7)
    values = rng.normal(100.0, 20.0, size=500)
    est = estimators.design_based_estimate(values, np.full(500, 10.0))

    assert est.n == 500
    assert est.mean == pytest.approx(100.0, abs=3.0)
    # Hybrid SE exceeds the pure sampling SE, because the model term is added.
    assert est.se > math.sqrt(np.var(values, ddof=1) / 500) * 0.99
    assert est.ci_lo < est.mean < est.ci_hi


def test_design_based_estimate_handles_empty_and_singleton():
    assert estimators.design_based_estimate([]).n == 0
    single = estimators.design_based_estimate([50.0], [5.0])
    assert single.n == 1
    assert single.mean == 50.0
    assert single.v_sampling == 0.0  # no sampling variance from one observation


def test_prediction_variance_is_dropped_without_errors():
    values = [10.0, 12.0, 11.0, 13.0]
    with_se = estimators.design_based_estimate(values, [3.0] * 4)
    without_se = estimators.design_based_estimate(values, None)
    assert with_se.se > without_se.se
    assert without_se.v_prediction == 0.0


def test_annual_table_shape(trend):
    assert not trend.empty
    assert set(trend["stratum"]) == {STABLE_FOREST, STABLE_NONFOREST, FOREST_GAIN}
    assert len(trend) == 3 * 6
    assert (trend["co2e_mean"] > trend["agbd_mean"]).all()  # 0.47 * 44/12 > 1


def test_change_detects_the_planted_signal(synthetic_footprints):
    change = estimators.change_table(synthetic_footprints, (2019, 2024))
    gain = change[change["stratum"] == FOREST_GAIN].iloc[0]
    stable = change[change["stratum"] == STABLE_NONFOREST].iloc[0]

    assert gain["agbd_change"] == pytest.approx(30.0, abs=8.0)  # 6 Mg/ha/yr * 5 yr
    assert bool(gain["significant_95"])
    assert not bool(stable["significant_95"])  # flat stratum, no real change


@pytest.mark.parametrize("method", ["wls", "ols", "theilsen"])
def test_trend_methods_agree_on_a_clean_signal(trend, method):
    fit = estimators.fit_stratum_trend(trend, FOREST_GAIN, 2024, method)
    assert fit.slope == pytest.approx(6.0, abs=2.0)
    assert fit.n == 6


def test_trend_fit_degenerate_cases():
    empty = estimators.fit_trend([], [], None, 2024)
    assert empty.n == 0 and empty.slope == 0.0

    single = estimators.fit_trend([2024], [100.0], [5.0], 2024)
    assert single.n == 1 and single.level == 100.0 and single.slope == 0.0


def test_predict_se_grows_with_extrapolation(trend):
    fit = estimators.fit_stratum_trend(trend, FOREST_GAIN, 2024, "wls")
    assert fit.predict_se(20) > fit.predict_se(1)


# ---------------------------------------------------------------------------
# Carbon curve
# ---------------------------------------------------------------------------

def test_curve_starts_at_zero_for_planting():
    cfg = CarbonCurveConfig(
        intervention_type="active_planting",
        calibrate_from_gedi=False,
        species_mix=default_mix_for("active_planting"),
    )
    curve = carbon_curve.resolve_curve(cfg)
    assert curve.carbon_at(0)["total_co2"] == pytest.approx(0.0, abs=1e-6)
    assert curve.carbon_at(20)["total_co2"] > 0


def test_anr_starts_above_zero():
    """ANR land carries standing biomass on day one — the notebook's spike bug."""
    cfg = CarbonCurveConfig(
        intervention_type="anr",
        advancement_years=5.0,
        calibrate_from_gedi=False,
        species_mix=default_mix_for("anr"),
    )
    curve = carbon_curve.resolve_curve(cfg)
    assert curve.carbon_at(0)["total_co2"] > 0


def test_curve_is_monotonic_and_bounded():
    cfg = CarbonCurveConfig(calibrate_from_gedi=False,
                            species_mix=default_mix_for("active_planting"))
    curve = carbon_curve.resolve_curve(cfg)
    frame = curve.to_frame(60)

    diffs = frame["total_tco2e_ha"].diff().dropna()
    assert (diffs >= -1e-6).all(), "curve must not decrease without harvest"

    ceiling_co2e = curve.ceiling_agb * cfg.carbon_fraction * (44 / 12) * (1 + curve.effective_root_shoot)
    assert frame["total_tco2e_ha"].max() <= ceiling_co2e * 1.05


def test_site_modifiers_move_the_curve():
    base = carbon_curve.resolve_curve(CarbonCurveConfig(calibrate_from_gedi=False))
    poor = carbon_curve.resolve_curve(CarbonCurveConfig(
        calibrate_from_gedi=False, soil_quality="degraded",
        water_stress="severe", site_index=0.6,
    ))
    good = carbon_curve.resolve_curve(CarbonCurveConfig(
        calibrate_from_gedi=False, soil_quality="good", site_index=1.4,
    ))

    assert poor.carbon_at(20)["total_co2"] < base.carbon_at(20)["total_co2"]
    assert good.carbon_at(20)["total_co2"] > base.carbon_at(20)["total_co2"]


def test_intervention_type_changes_the_shape():
    planting = carbon_curve.resolve_curve(CarbonCurveConfig(
        intervention_type="active_planting", calibrate_from_gedi=False,
        species_mix=default_mix_for("active_planting"),
    ))
    anr = carbon_curve.resolve_curve(CarbonCurveConfig(
        intervention_type="anr", calibrate_from_gedi=False,
        species_mix=default_mix_for("anr"),
    ))
    # ANR leads early (head start) but planting overtakes it at maturity.
    assert anr.carbon_at(1)["total_co2"] > planting.carbon_at(1)["total_co2"]


def test_calibration_prefers_local_data(trend):
    cfg = CarbonCurveConfig(calibrate_from_gedi=True)
    calibration = carbon_curve.calibrate_from_gedi(trend, cfg, GEDIConfig(), 2024)

    assert calibration.success
    assert calibration.ceiling_from_gedi
    # The reference stratum's mean AGBD, not the zone default.
    assert calibration.ceiling_agb == pytest.approx(182.5, abs=15.0)

    curve = carbon_curve.resolve_curve(cfg, calibration)
    assert curve.tier in (carbon_curve.TIER_GEDI, carbon_curve.TIER_PARTIAL)


def test_tier_falls_back_without_data():
    curve = carbon_curve.resolve_curve(CarbonCurveConfig(calibrate_from_gedi=False))
    assert curve.tier == carbon_curve.TIER_IPCC
    assert curve.is_indicative_only


# ---------------------------------------------------------------------------
# VM0047 engine
# ---------------------------------------------------------------------------

def _setup(**overrides) -> vm0047.ProjectSetup:
    curve = carbon_curve.resolve_curve(CarbonCurveConfig(
        calibrate_from_gedi=False, species_mix=default_mix_for("active_planting"),
    ))
    defaults = dict(
        curve=curve,
        project=ProjectConfigParams(crediting_period_years=30),
        pools=PoolConfig(),
        deductions=DeductionConfig(),
        stratum_area_ha=1000.0,
        baseline_rate_tco2e_ha_yr=0.0,
        n_footprints=0,
    )
    defaults.update(overrides)
    return vm0047.build_setup(**defaults)


def test_engine_produces_positive_credits():
    results = vm0047.VM0047Engine(_setup()).run()
    assert results.total_net_ers > 0
    assert len(results.annual) == 31
    assert results.annual["net_ers_tco2e"].min() >= 0  # credits are never negative


def test_deduction_chain_is_ordered():
    results = vm0047.VM0047Engine(_setup()).run()
    row = results.annual.iloc[10]
    assert row["gross_removals_tco2e"] >= row["net_before_buffer_tco2e"]
    assert row["net_before_buffer_tco2e"] >= row["net_ers_tco2e"]


def test_baseline_removals_reduce_credits():
    without = vm0047.VM0047Engine(_setup(baseline_rate_tco2e_ha_yr=0.0)).run()
    with_baseline = vm0047.VM0047Engine(_setup(baseline_rate_tco2e_ha_yr=2.0)).run()
    assert with_baseline.total_net_ers < without.total_net_ers


def test_plantable_fraction_scales_area():
    setup = _setup(project=ProjectConfigParams(plantable_fraction=0.5))
    assert setup.area_ha == pytest.approx(500.0)


def test_uncertainty_escalates_without_evidence():
    ipcc, _ = vm0047.uncertainty_from_evidence(0.10, carbon_curve.TIER_IPCC, 0)
    partial, _ = vm0047.uncertainty_from_evidence(0.10, carbon_curve.TIER_PARTIAL, 50)
    calibrated, _ = vm0047.uncertainty_from_evidence(0.10, carbon_curve.TIER_GEDI, 500)

    assert ipcc == 0.30
    assert partial == 0.20
    assert calibrated == 0.10
    assert ipcc > partial > calibrated


def test_user_uncertainty_is_never_lowered():
    """A user asking for 35% must not be silently reduced to the tier default."""
    applied, _ = vm0047.uncertainty_from_evidence(0.35, carbon_curve.TIER_GEDI, 10_000)
    assert applied == 0.35


def test_phased_planting_staggers_accrual():
    phased = _setup(project=ProjectConfigParams(
        phased_planting=True, planting_years=3, crediting_period_years=30,
    ))
    assert phased.area_at(0) == pytest.approx(phased.area_ha / 3)
    assert phased.area_at(2) == pytest.approx(phased.area_ha)


def test_sensitivity_scenarios_bracket_the_base_case():
    scenarios = vm0047.run_scenarios(_setup())
    base = scenarios["Base case"].total_net_ers
    assert scenarios["Conservative"].total_net_ers < base
    assert scenarios["Optimistic"].total_net_ers > base


def test_baseline_rate_is_clamped_non_negative(trend):
    """A declining stratum must yield a zero baseline, never a credit-boosting one."""
    rate, note = vm0047.derive_baseline_rate(
        trend, STABLE_NONFOREST, 2024, "wls", 0.47, None
    )
    assert rate >= 0.0
    assert note


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

def test_stable_forest_is_rejected_as_project_area():
    config = AppConfig()
    config.project.project_class = STABLE_FOREST
    errors = config.validate()
    assert any("reforestation class" in e for e in errors)


def test_window_before_hansen_baseline_is_rejected():
    config = AppConfig()
    config.hansen.t0_year = 2005
    config.hansen.epoch_gap_years = 10
    assert any("year 2000" in e for e in config.validate())


def test_default_config_is_valid():
    assert AppConfig().validate() == []


# ---------------------------------------------------------------------------
# Hosted-environment detection
# ---------------------------------------------------------------------------
# These guard a security property, not a feature: on a shared host an OAuth refresh
# token must never reach disk, because earthengine-api's session is process-global and
# the next visitor would inherit it.

def test_hosted_detection_local(monkeypatch):
    from src import ee_auth

    for marker in ("STREAMLIT_SHARING_MODE", "SPACE_ID", "K_SERVICE", "DYNO",
                   "RENDER", "RAILWAY_ENVIRONMENT", "WEBSITE_INSTANCE_ID",
                   "STREAMLIT_SERVER_HEADLESS"):
        monkeypatch.delenv(marker, raising=False)
    monkeypatch.setattr("os.path.exists", lambda p: False)
    assert ee_auth.is_hosted() is False


@pytest.mark.parametrize("marker", [
    "STREAMLIT_SHARING_MODE", "SPACE_ID", "K_SERVICE", "DYNO", "RENDER",
])
def test_hosted_detection_recognises_platforms(monkeypatch, marker):
    from src import ee_auth

    monkeypatch.setenv(marker, "1")
    assert ee_auth.is_hosted() is True


def test_hosted_detection_fails_safe_in_containers(monkeypatch):
    """An unrecognised container must be treated as shared, not as local."""
    from src import ee_auth

    for m in ("STREAMLIT_SHARING_MODE", "SPACE_ID", "K_SERVICE", "DYNO", "RENDER",
              "RAILWAY_ENVIRONMENT", "WEBSITE_INSTANCE_ID", "STREAMLIT_SERVER_HEADLESS"):
        monkeypatch.delenv(m, raising=False)
    monkeypatch.setattr("os.path.exists", lambda p: p == "/.dockerenv")
    assert ee_auth.is_hosted() is True


def test_force_local_override(monkeypatch):
    from src import ee_auth

    monkeypatch.setenv("STREAMLIT_SHARING_MODE", "1")
    monkeypatch.setenv("SCREENING_OBIWAN_FORCE_LOCAL", "true")
    assert ee_auth.is_hosted() is False


def test_empty_authorization_code_is_rejected():
    from src import ee_auth

    state = ee_auth.complete_authorization("", "verifier", "proj")
    assert not state.initialized
    assert state.credentials is None


def test_activate_is_false_without_a_session():
    from src import ee_auth

    assert ee_auth.activate(None) is False
    assert ee_auth.activate(ee_auth.AuthState(initialized=False)) is False


def test_authorization_url_is_generated():
    """Regression guard: ee.oauth's PKCE surface changed across releases.

    An earlier version called ee.oauth.create_code_verifier(), which does not exist in
    earthengine-api 1.x — the sign-in link failed at runtime with an AttributeError
    that only appeared once a user clicked the button.
    """
    from src import ee_auth

    url, verifier = ee_auth.build_authorization_url()
    assert url.startswith("https://")
    assert "earthengine" in url or "google.com" in url
    assert verifier and len(verifier) >= 16


def test_service_account_and_project_read_from_env(monkeypatch):
    from src import ee_auth

    monkeypatch.delenv("EE_PROJECT_ID", raising=False)
    monkeypatch.delenv("EE_SERVICE_ACCOUNT_JSON", raising=False)
    assert ee_auth.default_project_id() == ""
    assert ee_auth.has_service_account() is False

    monkeypatch.setenv("EE_PROJECT_ID", "  ee-geocaptain  ")
    monkeypatch.setenv("EE_SERVICE_ACCOUNT_JSON", '{"client_email": "x@y.iam"}')
    assert ee_auth.default_project_id() == "ee-geocaptain"
    assert ee_auth.has_service_account() is True


def test_describe_failure_maps_service_usage_403():
    """The Service Usage 403 is the top service-account failure and must name the
    right role — an earlier version pointed every 403 at 'Resource Viewer', which is
    necessary but insufficient and left users stuck."""
    from src import ee_auth

    real_error = (
        "Caller does not have required permission to use project ee-geocaptain. "
        "Grant the caller the roles/serviceusage.serviceUsageConsumer role"
    )
    hint = ee_auth.describe_failure(real_error)
    assert "Service Usage Consumer" in hint
    assert "serviceUsageConsumer" in hint

    generic = ee_auth.describe_failure("403 Forbidden: permission denied")
    assert "Service Usage Consumer" in generic and "Resource Viewer" in generic


def test_describe_failure_still_maps_registration():
    from src import ee_auth

    hint = ee_auth.describe_failure("Service account not registered for Earth Engine")
    assert "signup.earthengine" in hint or "not registered" in hint.lower()
