#!/usr/bin/env python
"""
Validate Earth Engine secrets without ever printing them.

Checks the TOML parses, the service-account fields are present and well-formed, the
private key survived pasting intact, and — optionally — that Earth Engine actually
accepts the credentials.

Run against a local secrets file::

    python scripts/check_secrets.py
    python scripts/check_secrets.py --file .streamlit/secrets.toml
    python scripts/check_secrets.py --connect          # also test the EE handshake

Or against the environment (CI, container)::

    python scripts/check_secrets.py --from-env

Output is deliberately redacted: key material is reported only as a length and a
fingerprint, never as content. Safe to paste the output when asking for help.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    try:
        import tomli as tomllib  # type: ignore
    except ModuleNotFoundError:
        print("Needs Python 3.11+, or `pip install tomli`.")
        sys.exit(2)

REQUIRED_FIELDS = (
    "type", "project_id", "private_key_id", "private_key",
    "client_email", "token_uri",
)

PASS, FAIL, WARN, INFO = "  [ok]", "  [FAIL]", "  [warn]", "  [--]"

_problems: list[str] = []


def ok(msg: str) -> None:
    print(f"{PASS} {msg}")


def fail(msg: str, remedy: str = "") -> None:
    print(f"{FAIL} {msg}")
    _problems.append(f"{msg}\n         -> {remedy}" if remedy else msg)


def warn(msg: str) -> None:
    print(f"{WARN} {msg}")


def fingerprint(value: str) -> str:
    """A short, non-reversible identifier — safe to share when debugging."""
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def load_from_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        fail(
            f"{path} not found.",
            "Copy .streamlit/secrets.toml.example and fill it in, or use --from-env.",
        )
        sys.exit(1)
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        fail(
            f"{path} is not valid TOML: {exc}",
            "The most common cause is mixing both formats — a "
            "[EE_SERVICE_ACCOUNT_JSON] table header AND a raw JSON object. Use one "
            "or the other, never both.",
        )
        sys.exit(1)


def load_from_env() -> dict[str, Any]:
    return {
        key: os.environ[key]
        for key in ("EE_PROJECT_ID", "EE_SERVICE_ACCOUNT_JSON")
        if key in os.environ
    }


def coerce_service_account(raw: Any) -> tuple[Optional[dict], bool]:
    """Accept either a TOML table or a JSON string, matching app.py::load_secrets.

    Returns ``(account, came_from_json_string)`` — the flag matters because a JSON
    string has already had its ``\\n`` escapes decoded.
    """
    if raw is None:
        return None, False
    if isinstance(raw, dict):
        ok("EE_SERVICE_ACCOUNT_JSON parsed as a TOML table")
        return dict(raw), False
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            fail(
                f"EE_SERVICE_ACCOUNT_JSON is a string but not valid JSON: {exc}",
                "If you pasted the raw .json file, wrap it in TRIPLE SINGLE quotes "
                "('''), not triple double quotes. Double quotes expand the \\n "
                "sequences in private_key into real newlines, which breaks JSON.",
            )
            return None, True
        ok("EE_SERVICE_ACCOUNT_JSON parsed as a JSON string")
        return parsed, True
    fail(f"EE_SERVICE_ACCOUNT_JSON has unexpected type {type(raw).__name__}.")
    return None, False


def check_private_key(key: str, from_json_string: bool = False) -> None:
    """Verify the PEM survived copy-paste, without revealing any of it.

    ``from_json_string`` says the value already went through ``json.loads``, which
    converts ``\\n`` escapes into real newlines. Real newlines are then correct and
    expected, so the escape-preservation check does not apply.
    """
    if not key:
        fail("private_key is empty.")
        return

    if not key.startswith("-----BEGIN"):
        fail("private_key does not start with '-----BEGIN'.",
             "Copy the whole value, including the BEGIN/END armour lines.")
        return
    if "PRIVATE KEY-----" not in key:
        fail("private_key is missing its END armour line.")
        return

    if "..." in key:
        fail("private_key still contains a '...' placeholder.",
             "Replace the template value with the real key from the downloaded JSON.")
        return

    literal = key.count("\\n")
    real = key.count("\n")

    if literal == 0 and real == 0:
        fail("private_key contains no line breaks at all.",
             "The PEM body must be split across lines. Re-copy from the .json file.")
        return

    if from_json_string:
        # json.loads has already turned \n escapes into real newlines.
        ok(f"private_key line breaks intact ({real} lines after JSON decoding)")
    elif real > 0 and literal == 0:
        # In a TOML table the escapes should still be literal. Real newlines here
        # usually mean an editor reformatted the value.
        warn(f"private_key uses {real} real newlines rather than \\n escapes — "
             "valid here, but do not re-wrap it into TOML by hand.")
    else:
        ok(f"private_key line breaks look intact ({literal} \\n escapes)")

    body_len = len(key)
    if body_len < 1000:
        warn(f"private_key is only {body_len} characters — RSA keys are usually "
             "~1,700. It may be truncated.")
    else:
        ok(f"private_key length plausible ({body_len} chars)")

    print(f"{INFO} private_key fingerprint: {fingerprint(key)}  (safe to share)")

    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        load_pem_private_key(key.encode(), password=None)
        ok("private_key is a cryptographically valid PEM")
    except ImportError:
        print(f"{INFO} `pip install cryptography` to verify the key parses")
    except Exception as exc:  # noqa: BLE001
        fail(f"private_key is not a parseable PEM: {type(exc).__name__}",
             "The value was likely altered in transit. Download a fresh key.")


def check_connection(project_id: str, account: dict) -> None:
    try:
        import ee
    except ImportError:
        warn("earthengine-api not installed; skipping the connection test.")
        return

    print("\nTesting the Earth Engine handshake…")
    try:
        credentials = ee.ServiceAccountCredentials(
            account["client_email"], key_data=json.dumps(account)
        )
        ee.Initialize(credentials, project=project_id,
                      opt_url="https://earthengine-highvolume.googleapis.com")
        ee.Number(1).getInfo()
        ok(f"Earth Engine accepted the credentials for {project_id}")
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        remedy = "Check the project ID and that the Earth Engine API is enabled."
        low = message.lower()
        if "not registered" in low or "not signed up" in low:
            remedy = (
                "Register the SERVICE ACCOUNT itself for Earth Engine at "
                "https://signup.earthengine.google.com/#!/service_accounts — creating "
                "it in Cloud IAM is not enough. This is the most commonly missed step."
            )
        elif "serviceusage" in low or "services.use" in low:
            remedy = ("Grant the service account the 'Service Usage Consumer' role "
                      "(roles/serviceusage.serviceUsageConsumer) on this project. It is "
                      "separate from 'Earth Engine Resource Viewer' and BOTH are "
                      "required — the Viewer role alone cannot make API calls.")
        elif "permission" in low or "403" in low:
            remedy = ("Grant the service account BOTH 'Earth Engine Resource Viewer' "
                      "and 'Service Usage Consumer' roles on this project.")
        elif "invalid_grant" in low or "signature" in low:
            remedy = ("The key was rejected — usually a mangled private_key, or a key "
                      "that has been deleted. Download a fresh one.")
        fail(f"Earth Engine rejected the credentials: {message[:160]}", remedy)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", default=".streamlit/secrets.toml")
    parser.add_argument("--from-env", action="store_true",
                        help="Read from environment variables instead of a file.")
    parser.add_argument("--connect", action="store_true",
                        help="Also test the Earth Engine handshake (makes a network call).")
    args = parser.parse_args()

    print("Screening-OBIWAN — secret check")
    print("No secret values are printed by this script.\n")

    secrets = load_from_env() if args.from_env else load_from_file(Path(args.file))
    if not args.from_env:
        ok(f"{args.file} is valid TOML")

    project_id = str(secrets.get("EE_PROJECT_ID", "")).strip()
    if project_id:
        ok(f"EE_PROJECT_ID = {project_id}")
    else:
        warn("EE_PROJECT_ID not set — the app will prompt each visitor for a project.")

    account, from_json = coerce_service_account(secrets.get("EE_SERVICE_ACCOUNT_JSON"))
    if account is None:
        if "EE_SERVICE_ACCOUNT_JSON" not in secrets:
            warn("EE_SERVICE_ACCOUNT_JSON not set — per-user sign-in will be used.")
    else:
        missing = [f for f in REQUIRED_FIELDS if not account.get(f)]
        if missing:
            fail(f"Service account is missing: {', '.join(missing)}",
                 "Copy the downloaded .json file in full — every field is needed.")
        else:
            ok(f"all {len(REQUIRED_FIELDS)} required fields present")

        email = str(account.get("client_email", ""))
        if email.endswith(".iam.gserviceaccount.com"):
            ok(f"client_email = {email}")
        elif email:
            fail(f"client_email does not look like a service account: {email}")

        if project_id and account.get("project_id") and account["project_id"] != project_id:
            warn(f"EE_PROJECT_ID ({project_id}) differs from the key's project_id "
                 f"({account['project_id']}). Valid, but usually a mistake.")

        check_private_key(str(account.get("private_key", "")), from_json_string=from_json)

        if args.connect and not _problems:
            check_connection(project_id or account.get("project_id", ""), account)

    print()
    if _problems:
        print(f"{len(_problems)} problem(s) found:\n")
        for i, problem in enumerate(_problems, 1):
            print(f"  {i}. {problem}")
        return 1

    print("All checks passed.")
    if not args.connect:
        print("Re-run with --connect to test the Earth Engine handshake.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
