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
3. **Interactive OAuth** — the app renders a Google consent URL and the user pastes
   back the authorization code. Locally the resulting refresh token is written to the
   standard Earth Engine credentials location for reuse; **on a shared host it is
   never written to disk** (see below).

The OAuth helpers in ``ee.oauth`` have shifted across ``earthengine-api`` releases, so
every call here is defensive and degrades to an actionable error message rather than a
traceback.

.. warning::
   **Multi-user hosting and Earth Engine's global state**

   ``earthengine-api`` stores its session in *process-global* state: ``ee.Initialize``
   sets a module-level default used by every subsequent call in that process. Streamlit
   Community Cloud runs one process serving all visitors concurrently.

   Two consequences, both handled here:

   1. **Never persist an OAuth token on a shared host.** Writing a refresh token to the
      container's credentials file would let the next visitor initialise as the
      previous one and spend their quota. :func:`is_hosted` detects the environment and
      :func:`complete_authorization` holds credentials in memory instead, for the
      caller to keep in per-session state.
   2. **Re-initialise before every operation.** :func:`activate` re-binds the global
      session to the current visitor's credentials immediately before their analysis
      runs, so a second visitor signing in cannot silently redirect the first
      visitor's in-flight requests.

   Step 2 narrows the window but does not close it. Two visitors running analyses at
   the same instant can still interleave between ``activate`` and the Earth Engine
   calls that follow, because the shared global cannot represent two identities at
   once. **A public multi-user deployment should use a service account**
   (``EE_SERVICE_ACCOUNT_JSON``), where one identity is the correct model. Per-user
   OAuth is safe for local use and for a private, single-operator deployment.
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
    credentials: object = None
    """In-memory credentials when running hosted, where nothing is written to disk.
    The caller keeps this in per-session state and passes it back to :func:`activate`
    before each operation. ``None`` locally, where the token is persisted normally."""


# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------

def is_hosted() -> bool:
    """True when running on a shared multi-user host rather than a local machine.

    Persisting an OAuth refresh token is safe locally (one operator, one machine) and
    unsafe on a shared host (one process, many visitors). Detection is heuristic and
    deliberately **fails safe**: an unrecognised container is treated as hosted, so the
    worst case is a user re-authenticating rather than a leaked credential.
    """
    if os.environ.get("SCREENING_OBIWAN_FORCE_LOCAL", "").lower() in ("1", "true", "yes"):
        return False

    hosted_markers = (
        "STREAMLIT_SHARING_MODE",     # Streamlit Community Cloud
        "STREAMLIT_SERVER_HEADLESS",  # generic headless deployment
        "SPACE_ID",                   # Hugging Face Spaces
        "K_SERVICE",                  # Google Cloud Run
        "DYNO",                       # Heroku
        "RENDER",                     # Render
        "RAILWAY_ENVIRONMENT",        # Railway
        "WEBSITE_INSTANCE_ID",        # Azure App Service
    )
    if any(os.environ.get(marker) for marker in hosted_markers):
        return True

    # Containerised without a recognised marker — assume shared.
    return os.path.exists("/.dockerenv")


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


def _credentials_from_refresh_token(refresh_token: str):
    """Build in-memory google-auth credentials from an Earth Engine refresh token.

    Used on shared hosts, where writing the token to the container's credentials file
    would expose it to every other visitor of the same process.
    """
    from ee import oauth  # noqa: PLC0415
    from google.oauth2.credentials import Credentials  # noqa: PLC0415

    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=getattr(oauth, "TOKEN_URI", "https://oauth2.googleapis.com/token"),
        client_id=oauth.CLIENT_ID,
        client_secret=oauth.CLIENT_SECRET,
        scopes=list(getattr(oauth, "SCOPES", [])) or None,
    )


def _persist_token(oauth_module, token) -> None:
    """Write a token using whichever writer this earthengine-api release exposes."""
    for writer in ("write_private_key", "write_token"):
        fn = getattr(oauth_module, writer, None)
        if callable(fn):
            fn(token)
            return
    raise EEAuthError("No token writer found in ee.oauth.")


def complete_authorization(
    code: str,
    code_verifier: str,
    project_id: str,
    persist: Optional[bool] = None,
) -> AuthState:
    """Exchange an authorization code for credentials and initialise.

    Parameters
    ----------
    persist
        Whether to write the refresh token to the standard Earth Engine credentials
        path. ``None`` (the default) decides from :func:`is_hosted`: persist locally,
        never on a shared host. Passing an explicit value is intended for tests.

    When the token is not persisted, the credentials are returned on
    :attr:`AuthState.credentials` for the caller to hold in per-session state and
    replay through :func:`activate` before each operation.
    """
    code = (code or "").strip()
    if not code:
        return AuthState(False, project_id, "oauth", "No authorization code supplied.")

    if persist is None:
        persist = not is_hosted()

    try:
        from ee import oauth  # noqa: PLC0415

        token = oauth.request_token(code, code_verifier)
    except Exception as exc:  # noqa: BLE001
        return AuthState(
            False, project_id, "oauth",
            "Authorization failed. The code may have expired — generate a new link "
            f"and retry. ({exc})",
        )

    if persist:
        try:
            _persist_token(oauth, token)
        except Exception as exc:  # noqa: BLE001
            return AuthState(False, project_id, "oauth",
                             f"Could not persist the Earth Engine token: {exc}")

        state = try_stored_credentials(project_id)
        if state.initialized:
            state.method = "oauth"
            state.message = "Signed in. Credentials saved for future sessions."
        return state

    # Hosted: keep the credentials in memory only.
    try:
        refresh_token = token if isinstance(token, str) else token.get("refresh_token")
        credentials = _credentials_from_refresh_token(refresh_token)
        _initialize_with(credentials, project_id)
    except Exception as exc:  # noqa: BLE001
        return AuthState(False, project_id, "oauth",
                         f"Signed in, but Earth Engine rejected the session: {exc}")

    return AuthState(
        True, project_id, "oauth",
        "Signed in for this session only. Because this app is shared, your Earth "
        "Engine token is held in memory and never written to the server — you will "
        "sign in again next visit.",
        credentials=credentials,
    )


def _initialize_with(credentials, project_id: str) -> None:
    """Bind the Earth Engine global session to specific credentials, and verify it."""
    ee.Initialize(credentials, project=project_id, opt_url=HIGH_VOLUME_ENDPOINT)
    ee.Number(1).getInfo()


def activate(state: AuthState) -> bool:
    """Re-bind the global Earth Engine session to this visitor's credentials.

    Call immediately before any Earth Engine work. On a single-user local run this is
    a cheap no-op reassertion. On a shared host it is what stops visitor B's sign-in
    from silently redirecting visitor A's in-flight analysis — see the module warning
    for the residual concurrency caveat this cannot fix.
    """
    if not state or not state.initialized:
        return False

    try:
        if state.credentials is not None:
            _initialize_with(state.credentials, state.project_id)
        else:
            _initialize(state.project_id)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not re-activate the Earth Engine session: %s", exc)
        return False


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
