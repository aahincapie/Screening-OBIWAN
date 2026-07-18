"""
Per-user Earth Engine authentication.

Each user signs in with their **own** Google account and supplies their **own** Earth
Engine Cloud project, so quota and billing sit with them and nothing is hardcoded.
(The source notebook hardcoded ``PROJECT_ID = "ee-geocaptain"`` in two cells; that is
deliberately not carried over.)

Three entry paths, tried in order:

1. **Stored credentials** — if the machine already ran ``earthengine authenticate``
   (or a previous session completed step 3), ``ee.Initialize`` just works. This is the
   normal path for local use.
2. **Environment / secrets service account** — if ``EE_SERVICE_ACCOUNT_JSON`` is set,
   use it. Present for CI and headless deployments only; not the default.
3. **Interactive OAuth** — the app renders a Google consent URL, the user pastes back
   the authorization code, and the resulting refresh token is written to the standard
   Earth Engine credentials location for reuse.

The OAuth helpers in ``ee.oauth`` have shifted across ``earthengine-api`` releases, so
every call here is defensive and degrades to an actionable error message rather than a
traceback.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional, Tuple

import ee

logger = logging.getLogger(__name__)

HIGH_VOLUME_ENDPOINT = "https://earthengine-highvolume.googleapis.com"


class EEAuthError(RuntimeError):
    """Raised when Earth Engine cannot be initialised, with a user-facing message."""


@dataclass
class AuthState:
    """Result of an initialisation attempt."""

    initialized: bool
    project_id: str = ""
    method: str = ""          # "stored" | "service_account" | "oauth"
    message: str = ""


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def _initialize(project_id: str, high_volume: bool = True) -> None:
    """Raw ``ee.Initialize`` plus a round-trip ping to prove the session is live.

    ``ee.Initialize`` is lazy — it can succeed against a project the user has no
    access to and only fail on first real call. The ``ee.Number(1).getInfo()`` ping
    forces that failure to surface here, where we can explain it.
    """
    kwargs = {"project": project_id} if project_id else {}
    if high_volume:
        kwargs["opt_url"] = HIGH_VOLUME_ENDPOINT
    ee.Initialize(**kwargs)
    ee.Number(1).getInfo()


def try_stored_credentials(project_id: str) -> AuthState:
    """Attempt initialisation with whatever credentials already exist on this machine."""
    if not project_id:
        return AuthState(False, message="An Earth Engine Cloud project ID is required.")

    try:
        _initialize(project_id)
        return AuthState(True, project_id, "stored", "Signed in with stored credentials.")
    except Exception as exc:  # noqa: BLE001 — surfaced verbatim to the user
        logger.info("Stored-credential init failed for %s: %s", project_id, exc)
        return AuthState(False, project_id, "stored", str(exc))


def try_service_account(project_id: str) -> AuthState:
    """Initialise from ``EE_SERVICE_ACCOUNT_JSON`` if present (headless/CI only)."""
    raw = os.environ.get("EE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return AuthState(False, message="No service account configured.")

    try:
        info = json.loads(raw)
        creds = ee.ServiceAccountCredentials(info["client_email"], key_data=raw)
        ee.Initialize(creds, project=project_id or info.get("project_id", ""),
                      opt_url=HIGH_VOLUME_ENDPOINT)
        ee.Number(1).getInfo()
        return AuthState(True, project_id or info.get("project_id", ""),
                         "service_account", "Signed in with a service account.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Service-account init failed: %s", exc)
        return AuthState(False, project_id, "service_account", str(exc))


def initialize(project_id: str, allow_service_account: bool = True) -> AuthState:
    """Best-effort initialisation. Returns state; never raises.

    Order: stored credentials, then service account. Interactive OAuth is *not*
    attempted automatically — the caller drives it, because it needs UI.
    """
    state = try_stored_credentials(project_id)
    if state.initialized:
        return state

    if allow_service_account:
        sa_state = try_service_account(project_id)
        if sa_state.initialized:
            return sa_state

    return state


# ---------------------------------------------------------------------------
# Interactive OAuth (step 3)
# ---------------------------------------------------------------------------

def build_authorization_url() -> Tuple[str, str]:
    """Return ``(consent_url, code_verifier)`` for the manual OAuth flow.

    The user opens ``consent_url``, approves, and copies the authorization code back
    into the app. ``code_verifier`` must be held (in session state) until then.

    Raises
    ------
    EEAuthError
        If the installed ``earthengine-api`` does not expose the OAuth helpers this
        flow needs, with instructions for the CLI fallback.
    """
    try:
        from ee import oauth  # noqa: PLC0415 — import here so import errors are catchable

        verifier = oauth.create_code_verifier()
        url = oauth.get_authorization_url(verifier)
        return url, verifier
    except Exception as exc:  # noqa: BLE001
        raise EEAuthError(
            "This build of earthengine-api does not expose the interactive OAuth "
            "helpers. Authenticate once from a terminal instead:\n\n"
            "    earthengine authenticate --project YOUR_PROJECT_ID\n\n"
            "then reload this page — the app will pick up the stored credentials.\n\n"
            f"Underlying error: {exc}"
        ) from exc


def complete_authorization(code: str, code_verifier: str, project_id: str) -> AuthState:
    """Exchange an authorization code for a refresh token and initialise.

    The token is written to the standard Earth Engine credentials path, so subsequent
    sessions take the ``stored`` path and never see this flow again.
    """
    code = (code or "").strip()
    if not code:
        return AuthState(False, project_id, "oauth", "No authorization code supplied.")

    try:
        from ee import oauth  # noqa: PLC0415

        token = oauth.request_token(code, code_verifier)
        oauth.write_private_key(token) if hasattr(oauth, "write_private_key") \
            else oauth.write_token(token)
    except AttributeError:
        # Older/newer API surface: fall back to whatever token writer exists.
        try:
            from ee import oauth  # noqa: PLC0415

            oauth.write_token(oauth.request_token(code, code_verifier))
        except Exception as exc:  # noqa: BLE001
            return AuthState(False, project_id, "oauth",
                             f"Could not persist the Earth Engine token: {exc}")
    except Exception as exc:  # noqa: BLE001
        return AuthState(False, project_id, "oauth",
                         f"Authorization failed. The code may have expired — "
                         f"generate a new link and retry. ({exc})")

    state = try_stored_credentials(project_id)
    if state.initialized:
        state.method = "oauth"
        state.message = "Signed in. Credentials saved for future sessions."
    return state


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def is_initialized() -> bool:
    """True if the current process has a live Earth Engine session."""
    try:
        ee.Number(1).getInfo()
        return True
    except Exception:  # noqa: BLE001
        return False


def describe_failure(exc_message: str) -> str:
    """Turn a raw Earth Engine error into an actionable hint."""
    msg = exc_message.lower()

    if "not signed up" in msg or "not registered" in msg:
        return (
            "This Google account is not registered for Earth Engine. Sign up at "
            "https://earthengine.google.com/signup/ (free for research and "
            "non-commercial use), then retry."
        )
    if "permission" in msg or "403" in msg:
        return (
            "The account is registered but lacks access to this Cloud project. Check "
            "the project ID, and that the Earth Engine API is enabled on it."
        )
    if "quota" in msg or "429" in msg:
        return (
            "Earth Engine quota exceeded. Reduce the AOI size, coarsen the analysis "
            "scale, or wait for the quota window to reset."
        )
    if "credentials" in msg or "refresh" in msg:
        return (
            "Stored credentials are missing or expired. Use the sign-in flow below, "
            "or run `earthengine authenticate` from a terminal."
        )
    return exc_message
