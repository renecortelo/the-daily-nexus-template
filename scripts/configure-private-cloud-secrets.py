from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class SecretSetupError(RuntimeError):
    pass


def _json_file(path: Path, *, label: str) -> dict:
    if not path.is_file() or path.is_symlink():
        raise SecretSetupError(f"{label} is missing")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SecretSetupError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise SecretSetupError(f"{label} must contain a JSON object")
    return value


def _set_secret(gh: str, name: str, value: str) -> None:
    if not value or len(value.encode()) > 48 * 1024:
        raise SecretSetupError(f"{name} is empty or too large")
    completed = subprocess.run(
        [gh, "secret", "set", name],
        cwd=ROOT,
        input=f"{value}\n",
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SecretSetupError(f"GitHub rejected encrypted secret {name}")
    print(f"Encrypted GitHub secret saved: {name}")


def _minimal_gmail_token(raw: str) -> str:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SecretSetupError("local Gmail authorization is not valid JSON") from exc
    if not isinstance(value, dict):
        raise SecretSetupError("local Gmail authorization must be a JSON object")
    required = ("refresh_token", "client_id", "client_secret", "token_uri")
    if any(not isinstance(value.get(key), str) or not value[key] for key in required):
        raise SecretSetupError("local Gmail authorization is missing refresh fields")
    minimal = {key: value[key] for key in required}
    scopes = value.get("scopes")
    if isinstance(scopes, list):
        minimal["scopes"] = scopes
    return json.dumps(minimal, separators=(",", ":"))


def _windows_generic_credential(target: str) -> str:
    if sys.platform != "win32":
        raise SecretSetupError(
            "Antigravity keyring transfer currently requires the authenticated "
            "Windows deployment"
        )

    import ctypes
    from ctypes import wintypes

    class Credential(ctypes.Structure):
        _fields_ = [
            ("flags", wintypes.DWORD),
            ("type", wintypes.DWORD),
            ("target_name", wintypes.LPWSTR),
            ("comment", wintypes.LPWSTR),
            ("last_written", wintypes.FILETIME),
            ("credential_blob_size", wintypes.DWORD),
            ("credential_blob", ctypes.POINTER(ctypes.c_ubyte)),
            ("persist", wintypes.DWORD),
            ("attribute_count", wintypes.DWORD),
            ("attributes", ctypes.c_void_p),
            ("target_alias", wintypes.LPWSTR),
            ("user_name", wintypes.LPWSTR),
        ]

    credential_pointer = ctypes.POINTER(Credential)()
    credential_api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    credential_api.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(Credential)),
    ]
    credential_api.CredReadW.restype = wintypes.BOOL
    credential_api.CredFree.argtypes = [ctypes.c_void_p]
    credential_api.CredFree.restype = None

    if not credential_api.CredReadW(
        target,
        1,  # CRED_TYPE_GENERIC
        0,
        ctypes.byref(credential_pointer),
    ):
        raise SecretSetupError(
            "the Antigravity Windows Credential Manager session is missing"
        )
    try:
        credential = credential_pointer.contents
        raw = ctypes.string_at(
            credential.credential_blob,
            credential.credential_blob_size,
        )
        return raw.decode("utf-8")
    except (UnicodeDecodeError, ValueError) as exc:
        raise SecretSetupError(
            "the Antigravity Windows Credential Manager session is invalid"
        ) from exc
    finally:
        credential_api.CredFree(credential_pointer)


def _antigravity_keyring_credential(raw: str) -> str:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SecretSetupError(
            "the Antigravity keyring session is not valid JSON"
        ) from exc
    token = value.get("token") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("auth_method"), str)
        or not value["auth_method"]
        or not isinstance(value.get("id_token"), str)
        or len(value["id_token"]) < 100
        or not isinstance(token, dict)
    ):
        raise SecretSetupError("the Antigravity keyring session has an invalid structure")
    for field in ("access_token", "refresh_token", "token_type", "expiry"):
        if not isinstance(token.get(field), str) or not token[field]:
            raise SecretSetupError(
                f"the Antigravity keyring session is missing token field: {field}"
            )
    return json.dumps(value, separators=(",", ":"))


def main() -> None:
    from audiodigest.config import load_settings
    from audiodigest.gmail_client import GmailTokenStore
    from audiodigest.web_runner import WebRunnerTokenStore

    portable_gh = ROOT / ".tools" / "bin" / "gh.exe"
    gh = shutil.which("gh") or (
        str(portable_gh) if portable_gh.is_file() else None
    )
    if not gh:
        raise SecretSetupError("GitHub CLI is not installed")
    repository = subprocess.run(
        [gh, "repo", "view", "--json", "visibility,nameWithOwner"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if repository.returncode != 0:
        raise SecretSetupError("GitHub CLI is not authenticated for this repository")
    try:
        repository_data = json.loads(repository.stdout)
    except json.JSONDecodeError as exc:
        raise SecretSetupError("GitHub returned invalid repository metadata") from exc
    if repository_data.get("visibility") != "PRIVATE":
        raise SecretSetupError("production secrets may be added only to a private repository")

    settings = load_settings(ROOT / "config.toml")
    gmail_token = GmailTokenStore(settings).get()
    firebase_refresh = WebRunnerTokenStore(settings).get()
    if not gmail_token:
        raise SecretSetupError("local Gmail read-only authorization is missing")
    if not firebase_refresh:
        raise SecretSetupError("local Firebase web-runner authorization is missing")

    antigravity = _antigravity_keyring_credential(
        _windows_generic_credential("gemini:antigravity")
    )

    firebase_cli_path = (
        Path.home() / ".config" / "configstore" / "firebase-tools.json"
    )
    firebase_cli = _json_file(
        firebase_cli_path,
        label="local Firebase CLI authorization",
    )
    firebase_deploy_token = firebase_cli.get("tokens", {}).get("refresh_token")
    if not isinstance(firebase_deploy_token, str) or not firebase_deploy_token:
        raise SecretSetupError("local Firebase CLI authorization has no refresh token")

    values = {
        "TDN_GMAIL_TOKEN_JSON": _minimal_gmail_token(gmail_token),
        "TDN_FIREBASE_REFRESH_TOKEN": firebase_refresh,
        "TDN_ANTIGRAVITY_KEYRING_JSON": antigravity,
        "TDN_FIREBASE_DEPLOY_TOKEN": firebase_deploy_token,
        "TDN_FIREBASE_PROJECT_ID": settings.firebase.project_id,
        "TDN_FIREBASE_API_KEY": settings.web.firebase_api_key,
        "TDN_FIREBASE_OWNER_UID": settings.web.owner_uid,
        "TDN_FIREBASE_SECRET_PATH": settings.firebase.secret_path,
        "TDN_SPARK_CONFIRMED": "SPARK_NO_BILLING_CONFIRMED",
    }
    for name, value in values.items():
        _set_secret(gh, name, value)
    print(
        "Private cloud secrets are configured. Values were sent through standard "
        "input and were not printed."
    )


if __name__ == "__main__":
    try:
        main()
    except SecretSetupError as exc:
        print(f"Cloud secret setup stopped: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
