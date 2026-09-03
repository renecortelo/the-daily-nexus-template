from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from audiodigest.config import Settings
from audiodigest.gmail_client import GmailTokenStore
from audiodigest.private_store import (
    PrivateStoreError,
    delete_private_value,
    read_private_value,
    write_private_value,
)

WEB_RUNNER_IDENTITY_SCOPES = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
)


class WebRunnerError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WebRunnerIdentity:
    uid: str
    email: str


class WebRunnerTokenStore:
    def __init__(self, settings: Settings):
        self.service = settings.web.token_service
        self.username = settings.web.token_username
        self.token_file_path = settings.web.token_file_path

    def get(self) -> str | None:
        if self.token_file_path is not None:
            try:
                return read_private_value(self.token_file_path)
            except PrivateStoreError as exc:
                raise WebRunnerError(str(exc)) from exc
        try:
            import keyring
        except ImportError as exc:
            raise WebRunnerError(
                "keyring is required to secure the web-runner authorization"
            ) from exc
        return keyring.get_password(self.service, self.username)

    def set(self, value: str) -> None:
        if self.token_file_path is not None:
            try:
                write_private_value(self.token_file_path, value)
            except PrivateStoreError as exc:
                raise WebRunnerError(str(exc)) from exc
            return
        import keyring

        keyring.set_password(self.service, self.username, value)

    def delete(self) -> bool:
        if self.token_file_path is not None:
            try:
                return delete_private_value(self.token_file_path)
            except PrivateStoreError as exc:
                raise WebRunnerError(str(exc)) from exc
        try:
            import keyring
            from keyring.errors import PasswordDeleteError
        except ImportError as exc:
            raise WebRunnerError(
                "keyring is required to remove the web-runner authorization"
            ) from exc
        try:
            keyring.delete_password(self.service, self.username)
        except PasswordDeleteError:
            return False
        return True


def _json_request(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    form: dict[str, str] | None = None,
    bearer: str | None = None,
    method: str | None = None,
    timeout_seconds: int = 30,
    allow_conflict: bool = False,
    allow_not_found: bool = False,
) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(url)
    allowed_hosts = {
        "identitytoolkit.googleapis.com",
        "securetoken.googleapis.com",
        "firestore.googleapis.com",
    }
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise WebRunnerError("web runner refused an unexpected remote host")
    if payload is not None and form is not None:
        raise ValueError("request cannot contain both JSON and form data")
    headers = {
        "Accept": "application/json",
        "User-Agent": "TheDailyNexus/0.7 SecureHomeRunner",
    }
    body: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    elif form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        body = urllib.parse.urlencode(form).encode("ascii")
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    request = urllib.request.Request(  # noqa: S310 - strict host/scheme checked above.
        url,
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(  # noqa: S310 - fixed Google hosts enforced above.
            request,
            timeout=timeout_seconds,
        ) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 409 and allow_conflict:
            return {"_conflict": True}
        if exc.code == 404 and allow_not_found:
            return {"_not_found": True}
        try:
            detail = json.loads(exc.read(16_384)).get("error", {}).get("message", "")
        except (AttributeError, json.JSONDecodeError, UnicodeDecodeError):
            detail = ""
        suffix = f": {detail}" if detail else ""
        raise WebRunnerError(
            f"secure Firebase request returned HTTP {exc.code}{suffix}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise WebRunnerError(
            f"secure Firebase request failed ({type(exc).__name__})"
        ) from exc
    if len(raw) > 2 * 1024 * 1024:
        raise WebRunnerError("secure Firebase response exceeded the safety limit")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise WebRunnerError("secure Firebase response was not valid JSON") from exc
    if not isinstance(value, dict):
        raise WebRunnerError("secure Firebase response was not an object")
    return value


def _firebase_auth_url(settings: Settings, endpoint: str) -> str:
    key = urllib.parse.quote(settings.web.firebase_api_key, safe="")
    return f"https://identitytoolkit.googleapis.com/v1/{endpoint}?key={key}"


def authenticate_web_runner(settings: Settings) -> WebRunnerIdentity:
    if not settings.web.enabled:
        raise WebRunnerError("enable the V4 web runner in config.toml first")
    client_path = settings.web.oauth_client_secret_path
    if not client_path.is_file() or client_path.is_symlink():
        raise WebRunnerError(
            "the dedicated Firebase-project Desktop OAuth client is missing"
        )
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise WebRunnerError(
            "google-auth-oauthlib is required for secure runner pairing"
        ) from exc
    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_path),
        scopes=list(WEB_RUNNER_IDENTITY_SCOPES),
    )
    credentials = flow.run_local_server(
        host="localhost",
        port=0,
        open_browser=True,
        authorization_prompt_message=(
            "Complete the private Daily Nexus runner pairing in your browser."
        ),
        success_message=(
            "The Daily Nexus runner is paired. You may close this browser tab."
        ),
    )
    google_access_token = getattr(credentials, "token", None)
    if not isinstance(google_access_token, str) or not google_access_token:
        raise WebRunnerError("Google did not return the access token required for pairing")
    response = _json_request(
        _firebase_auth_url(settings, "accounts:signInWithIdp"),
        payload={
            "postBody": urllib.parse.urlencode(
                {
                    "access_token": google_access_token,
                    "providerId": "google.com",
                }
            ),
            "requestUri": settings.firebase.base_url,
            "returnIdpCredential": True,
            "returnSecureToken": True,
        },
    )
    uid = str(response.get("localId", "")).strip()
    email = str(response.get("email", "")).strip().casefold()
    refresh_token = response.get("refreshToken")
    if not uid or not email or not isinstance(refresh_token, str) or not refresh_token:
        raise WebRunnerError("Firebase did not return a complete runner identity")
    if settings.web.owner_uid and uid != settings.web.owner_uid:
        raise WebRunnerError("the paired Firebase user is not the configured private owner")
    gmail_email = GmailTokenStore(settings).get_account_email()
    if gmail_email and gmail_email.casefold() != email:
        raise WebRunnerError(
            "the web runner must use the same Google account as the connected Gmail source"
        )
    WebRunnerTokenStore(settings).set(refresh_token)
    return WebRunnerIdentity(uid=uid, email=email)


def unpair_web_runner(settings: Settings) -> bool:
    return WebRunnerTokenStore(settings).delete()


def _decode_firestore_value(value: dict[str, Any]) -> Any:
    if "nullValue" in value:
        return None
    if "stringValue" in value:
        return value["stringValue"]
    if "booleanValue" in value:
        return value["booleanValue"]
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "timestampValue" in value:
        return value["timestampValue"]
    if "arrayValue" in value:
        return [
            _decode_firestore_value(item)
            for item in value["arrayValue"].get("values", [])
        ]
    if "mapValue" in value:
        return _decode_firestore_fields(value["mapValue"].get("fields", {}))
    raise WebRunnerError("Firestore returned an unsupported field type")


def _decode_firestore_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _decode_firestore_value(value)
        for key, value in fields.items()
        if isinstance(value, dict)
    }


def _encode_firestore_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise WebRunnerError("Firestore timestamps must include a timezone")
        return {"timestampValue": value.isoformat().replace("+00:00", "Z")}
    if isinstance(value, list | tuple):
        return {
            "arrayValue": {
                "values": [_encode_firestore_value(item) for item in value]
            }
        }
    if isinstance(value, dict):
        return {"mapValue": {"fields": _encode_firestore_fields(value)}}
    raise WebRunnerError(f"cannot encode Firestore value: {type(value).__name__}")


def _encode_firestore_fields(data: dict[str, Any]) -> dict[str, Any]:
    return {key: _encode_firestore_value(value) for key, value in data.items()}


class FirebaseWebRunnerClient:
    def __init__(self, settings: Settings):
        if not settings.web.enabled:
            raise WebRunnerError("the V4 web runner is disabled")
        self.settings = settings
        self.token_store = WebRunnerTokenStore(settings)
        self.uid = settings.web.owner_uid
        self._id_token = ""

    def authenticate(self) -> str:
        refresh_token = self.token_store.get()
        if not refresh_token:
            raise WebRunnerError(
                "the runner is not paired; run authenticate-web-runner"
            )
        key = urllib.parse.quote(self.settings.web.firebase_api_key, safe="")
        response = _json_request(
            f"https://securetoken.googleapis.com/v1/token?key={key}",
            form={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        uid = str(response.get("user_id", ""))
        if uid != self.uid:
            raise WebRunnerError("runner token does not belong to the configured owner")
        token = response.get("id_token")
        rotated = response.get("refresh_token")
        if not isinstance(token, str) or not token:
            raise WebRunnerError("Firebase did not refresh the runner identity")
        if isinstance(rotated, str) and rotated and rotated != refresh_token:
            self.token_store.set(rotated)
        self._id_token = token
        return token

    @property
    def _documents_root(self) -> str:
        project = urllib.parse.quote(self.settings.firebase.project_id, safe="")
        return (
            "https://firestore.googleapis.com/v1/projects/"
            f"{project}/databases/(default)/documents"
        )

    def _token(self) -> str:
        return self._id_token or self.authenticate()

    def list_private_collection(
        self,
        collection_name: str,
        *,
        field_mask: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if collection_name not in {"schedules", "runRequests", "episodes"}:
            raise WebRunnerError("runner refused an unexpected Firestore collection")
        uid = urllib.parse.quote(self.uid, safe="")
        collection_part = urllib.parse.quote(collection_name, safe="")
        query = "pageSize=100"
        if field_mask:
            query += "&" + "&".join(
                f"mask.fieldPaths={urllib.parse.quote(f, safe='')}"
                for f in field_mask
            )
        response = _json_request(
            f"{self._documents_root}/users/{uid}/{collection_part}?{query}",
            bearer=self._token(),
        )
        result: list[dict[str, Any]] = []
        for document in response.get("documents", []):
            if not isinstance(document, dict):
                continue
            name = str(document.get("name", ""))
            document_id = name.rsplit("/", 1)[-1]
            fields = document.get("fields", {})
            if not document_id or not isinstance(fields, dict):
                continue
            result.append(
                {
                    "document_id": document_id,
                    **_decode_firestore_fields(fields),
                }
            )
        return result

    def set_private_document(
        self,
        collection_name: str,
        document_id: str,
        data: dict[str, Any],
    ) -> None:
        if collection_name not in {"runner", "runRequests", "episodes"}:
            raise WebRunnerError("runner refused an unexpected Firestore write")
        if not document_id or "/" in document_id or len(document_id) > 160:
            raise WebRunnerError("runner refused an invalid Firestore document ID")
        uid = urllib.parse.quote(self.uid, safe="")
        collection_part = urllib.parse.quote(collection_name, safe="")
        document_part = urllib.parse.quote(document_id, safe="")
        _json_request(
            (
                f"{self._documents_root}/users/{uid}/"
                f"{collection_part}/{document_part}"
            ),
            payload={"fields": _encode_firestore_fields(data)},
            bearer=self._token(),
            method="PATCH",
        )

    def patch_private_document(
        self,
        collection_name: str,
        document_id: str,
        data: dict[str, Any],
    ) -> None:
        if collection_name != "runRequests":
            raise WebRunnerError("runner refused an unexpected Firestore patch")
        if not document_id or "/" in document_id or len(document_id) > 160:
            raise WebRunnerError("runner refused an invalid Firestore document ID")
        allowed_fields = {"status", "updatedAt", "startedAt", "finishedAt", "detail"}
        if not data or any(key not in allowed_fields for key in data):
            raise WebRunnerError("runner refused an unexpected Firestore patch field")
        uid = urllib.parse.quote(self.uid, safe="")
        document_part = urllib.parse.quote(document_id, safe="")
        query = urllib.parse.urlencode(
            [("updateMask.fieldPaths", key) for key in data]
        )
        _json_request(
            (
                f"{self._documents_root}/users/{uid}/runRequests/"
                f"{document_part}?{query}"
            ),
            payload={"fields": _encode_firestore_fields(data)},
            bearer=self._token(),
            method="PATCH",
        )

    @staticmethod
    def _execution_document_id(execution_id: str, episode_date: str) -> str:
        value = f"{execution_id}\0{episode_date}".encode()
        return hashlib.sha256(value).hexdigest()

    def claim_private_execution(
        self,
        execution_id: str,
        episode_date: str,
    ) -> bool:
        if (
            not execution_id
            or len(execution_id) > 160
            or not episode_date
            or len(episode_date) != 10
        ):
            raise WebRunnerError("runner refused an invalid execution claim")
        uid = urllib.parse.quote(self.uid, safe="")
        document_id = self._execution_document_id(execution_id, episode_date)
        query = urllib.parse.urlencode({"documentId": document_id})
        now = datetime.now(UTC)
        response = _json_request(
            f"{self._documents_root}/users/{uid}/executions?{query}",
            payload={
                "fields": _encode_firestore_fields(
                    {
                        "executionId": execution_id,
                        "episodeDate": episode_date,
                        "status": "running",
                        "startedAt": now,
                        "updatedAt": now,
                        "schemaVersion": 1,
                    }
                )
            },
            bearer=self._token(),
            method="POST",
            allow_conflict=True,
        )
        if not response.get("_conflict"):
            return True

        # An execution ID is immutable.  This is the circuit breaker for cloud
        # automation: a completed, running, *or failed* task must never be
        # claimed automatically again.  A person who wants another attempt
        # creates a new manual request, which receives a new execution ID and
        # preserves the failed attempt for auditability.
        return False

    def private_execution_status(
        self,
        execution_id: str,
        episode_date: str,
    ) -> str:
        document_id = self._execution_document_id(execution_id, episode_date)
        uid = urllib.parse.quote(self.uid, safe="")
        response = _json_request(
            f"{self._documents_root}/users/{uid}/executions/{document_id}",
            bearer=self._token(),
            allow_not_found=True,
        )
        if response.get("_not_found"):
            return ""
        fields = response.get("fields", {})
        if not isinstance(fields, dict):
            return ""
        decoded = _decode_firestore_fields(fields)
        return str(decoded.get("status", ""))

    def has_private_execution(
        self,
        execution_id: str,
        episode_date: str,
    ) -> bool:
        document_id = self._execution_document_id(execution_id, episode_date)
        uid = urllib.parse.quote(self.uid, safe="")
        response = _json_request(
            f"{self._documents_root}/users/{uid}/executions/{document_id}",
            bearer=self._token(),
            allow_not_found=True,
        )
        return not bool(response.get("_not_found"))

    def finish_private_execution(
        self,
        execution_id: str,
        episode_date: str,
        *,
        status: str,
    ) -> None:
        if status not in {"completed", "failed"}:
            raise WebRunnerError("runner refused an invalid execution status")
        document_id = self._execution_document_id(execution_id, episode_date)
        uid = urllib.parse.quote(self.uid, safe="")
        query = urllib.parse.urlencode(
            [
                ("updateMask.fieldPaths", "status"),
                ("updateMask.fieldPaths", "finishedAt"),
                ("updateMask.fieldPaths", "updatedAt"),
            ]
        )
        now = datetime.now(UTC)
        _json_request(
            (
                f"{self._documents_root}/users/{uid}/executions/"
                f"{document_id}?{query}"
            ),
            payload={
                "fields": _encode_firestore_fields(
                    {
                        "status": status,
                        "finishedAt": now,
                        "updatedAt": now,
                    }
                )
            },
            bearer=self._token(),
            method="PATCH",
        )
