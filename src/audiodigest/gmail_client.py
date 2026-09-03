from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr
from typing import Any

from audiodigest.config import Settings
from audiodigest.content import clean_html, extract_editorial_links_with_stats
from audiodigest.dates import DayWindow
from audiodigest.models import SourceItem
from audiodigest.preferences import validate_gmail_label
from audiodigest.private_store import (
    PrivateStoreError,
    delete_private_value,
    read_private_value,
    write_private_value,
)

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class GmailConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GmailLogoutResult:
    had_authorization: bool
    remote_revoked: bool
    local_deleted: bool
    detail: str = ""


def _decode_body(data: str) -> bytes:
    return base64.urlsafe_b64decode(data.encode("ascii") + b"===")


def _headers(payload: dict[str, Any]) -> dict[str, str]:
    return {
        str(item.get("name", "")).lower(): str(item.get("value", ""))
        for item in payload.get("headers", [])
        if item.get("name")
    }


def _walk_text_parts(payload: dict[str, Any]) -> tuple[str, str]:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    def visit(part: dict[str, Any]) -> None:
        filename = str(part.get("filename", "")).strip()
        if filename:
            return
        mime = str(part.get("mimeType", "")).lower()
        data = part.get("body", {}).get("data")
        if data and mime in {"text/plain", "text/html"}:
            try:
                text = _decode_body(str(data)).decode("utf-8", errors="replace")
            except (ValueError, UnicodeError):
                return
            (html_parts if mime == "text/html" else plain_parts).append(text)
        for child in part.get("parts", []) or []:
            if isinstance(child, dict):
                visit(child)

    visit(payload)
    return "\n".join(plain_parts), "\n".join(html_parts)


def _publication_name(from_header: str) -> tuple[str, str]:
    name, address = parseaddr(from_header)
    publication = name.strip().strip('"') or address.split("@", 1)[0]
    return publication, address.lower()


class GmailTokenStore:
    def __init__(self, settings: Settings):
        self.service = settings.gmail.token_service
        self.username = settings.gmail.token_username
        self.account_username = f"{self.username}-account-email"
        self.token_file_path = settings.gmail.token_file_path
        self.account_email_file_path = settings.gmail.account_email_file_path

    def get(self) -> str | None:
        token_file_path = getattr(self, "token_file_path", None)
        if token_file_path is not None:
            try:
                return read_private_value(token_file_path)
            except PrivateStoreError as exc:
                raise GmailConfigurationError(str(exc)) from exc
        try:
            import keyring
        except ImportError as exc:
            raise GmailConfigurationError(
                "keyring is required to keep the Gmail refresh token out of project files"
            ) from exc
        return keyring.get_password(self.service, self.username)

    def set(self, value: str) -> None:
        token_file_path = getattr(self, "token_file_path", None)
        if token_file_path is not None:
            try:
                write_private_value(token_file_path, value)
            except PrivateStoreError as exc:
                raise GmailConfigurationError(str(exc)) from exc
            return
        import keyring

        keyring.set_password(self.service, self.username, value)

    def get_account_email(self) -> str | None:
        account_email_file_path = getattr(self, "account_email_file_path", None)
        if account_email_file_path is not None:
            try:
                return read_private_value(account_email_file_path)
            except PrivateStoreError as exc:
                raise GmailConfigurationError(str(exc)) from exc
        try:
            import keyring
        except ImportError as exc:
            raise GmailConfigurationError(
                "keyring is required to read the signed-in Gmail account"
            ) from exc
        return keyring.get_password(self.service, self.account_username)

    def set_account_email(self, value: str) -> None:
        account_email_file_path = getattr(self, "account_email_file_path", None)
        if account_email_file_path is not None:
            try:
                write_private_value(account_email_file_path, value)
            except PrivateStoreError as exc:
                raise GmailConfigurationError(str(exc)) from exc
            return
        import keyring

        keyring.set_password(self.service, self.account_username, value)

    def exists(self) -> bool:
        return bool(self.get())

    def delete(self) -> bool:
        token_file_path = getattr(self, "token_file_path", None)
        if token_file_path is not None:
            try:
                return delete_private_value(token_file_path)
            except PrivateStoreError as exc:
                raise GmailConfigurationError(str(exc)) from exc
        try:
            import keyring
            from keyring.errors import PasswordDeleteError
        except ImportError as exc:
            raise GmailConfigurationError(
                "keyring is required to remove the Gmail authorization"
            ) from exc
        try:
            keyring.delete_password(self.service, self.username)
        except PasswordDeleteError:
            return False
        return True

    def delete_account_email(self) -> bool:
        account_email_file_path = getattr(self, "account_email_file_path", None)
        if account_email_file_path is not None:
            try:
                return delete_private_value(account_email_file_path)
            except PrivateStoreError as exc:
                raise GmailConfigurationError(str(exc)) from exc
        try:
            import keyring
            from keyring.errors import PasswordDeleteError
        except ImportError as exc:
            raise GmailConfigurationError(
                "keyring is required to remove the saved Gmail account identity"
            ) from exc
        try:
            keyring.delete_password(self.service, self.account_username)
        except PasswordDeleteError:
            return False
        return True


class GmailClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.token_store = GmailTokenStore(settings)
        self._service = None

    def is_authenticated(self) -> bool:
        return self.token_store.exists()

    def cached_account_email(self) -> str | None:
        return self.token_store.get_account_email()

    def account_email(self) -> str:
        response = self.service.users().getProfile(userId="me").execute()
        email = str(response.get("emailAddress", "")).strip()
        if not email or "@" not in email:
            raise GmailConfigurationError(
                "Google did not return an email address for the signed-in account"
            )
        self.token_store.set_account_email(email)
        return email

    def logout(self, *, timeout_seconds: int = 15) -> GmailLogoutResult:
        token_json = self.token_store.get()
        if not token_json:
            self.token_store.delete_account_email()
            return GmailLogoutResult(
                had_authorization=False,
                remote_revoked=False,
                local_deleted=False,
                detail="Gmail is already signed out.",
            )

        remote_revoked = False
        detail = ""
        try:
            token_data = json.loads(token_json)
            token = token_data.get("refresh_token") or token_data.get("token")
            if not isinstance(token, str) or not token:
                detail = "The saved authorization had no revocable token."
            else:
                body = urllib.parse.urlencode({"token": token}).encode("ascii")
                request = urllib.request.Request(
                    "https://oauth2.googleapis.com/revoke",
                    data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                with urllib.request.urlopen(  # noqa: S310 - fixed Google OAuth endpoint
                    request, timeout=timeout_seconds
                ) as response:
                    remote_revoked = response.status == 200
                if not remote_revoked:
                    detail = "Google did not confirm remote token revocation."
        except (json.JSONDecodeError, OSError, urllib.error.URLError) as exc:
            detail = f"Remote revocation could not be confirmed: {exc}"
        finally:
            local_deleted = self.token_store.delete()
            self.token_store.delete_account_email()
            self._service = None

        return GmailLogoutResult(
            had_authorization=True,
            remote_revoked=remote_revoked,
            local_deleted=local_deleted,
            detail=detail,
        )

    def _credentials(self):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as exc:
            raise GmailConfigurationError(
                "Install the project dependencies before authenticating Gmail"
            ) from exc

        token_json = self.token_store.get()
        credentials = (
            Credentials.from_authorized_user_info(json.loads(token_json), [GMAIL_READONLY_SCOPE])
            if token_json
            else None
        )
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        if not credentials or not credentials.valid:
            if self.settings.gmail.token_file_path is not None:
                raise GmailConfigurationError(
                    "cloud Gmail authorization is invalid or expired; "
                    "replace the encrypted Gmail token secret"
                )
            if not self.settings.gmail.client_secret_path.exists():
                raise GmailConfigurationError(
                    f"Missing Gmail OAuth desktop client file: "
                    f"{self.settings.gmail.client_secret_path}"
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.settings.gmail.client_secret_path), [GMAIL_READONLY_SCOPE]
            )
            credentials = flow.run_local_server(port=0, open_browser=True)
        self.token_store.set(credentials.to_json())
        return credentials

    @property
    def service(self):
        if self._service is None:
            from googleapiclient.discovery import build

            self._service = build(
                "gmail", "v1", credentials=self._credentials(), cache_discovery=False
            )
        return self._service

    def verify_label(self, label_name: str | None = None) -> str:
        expected_name = validate_gmail_label(
            label_name if label_name is not None else self.settings.app.gmail_label
        )
        response = self.service.users().labels().list(userId="me").execute()
        for item in response.get("labels", []):
            if item.get("name") == expected_name:
                return str(item["id"])
        raise GmailConfigurationError(
            f"Gmail label {expected_name!r} was not found in this account. "
            "Create it in Gmail or enter its exact name, including capitalization."
        )

    def fetch_newsletters(self, window: DayWindow) -> list[SourceItem]:
        label_id = self.verify_label()
        # The explicit AudioDigest label is the allowlist. Gmail often places legitimate
        # newsletters in Promotions, so category exclusions would silently lose sources.
        query = f"after:{window.start_epoch} before:{window.end_epoch}"
        messages: list[dict[str, Any]] = []
        page_token = None
        while len(messages) < self.settings.app.max_newsletters:
            response = (
                self.service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    labelIds=[label_id],
                    maxResults=min(100, self.settings.app.max_newsletters - len(messages)),
                    pageToken=page_token,
                )
                .execute()
            )
            messages.extend(response.get("messages", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        results: list[SourceItem] = []
        for summary in messages:
            raw = (
                self.service.users()
                .messages()
                .get(userId="me", id=summary["id"], format="full")
                .execute()
            )
            payload = raw.get("payload", {})
            headers = _headers(payload)
            plain, html = _walk_text_parts(payload)
            body = clean_html(html) if html else plain.strip()
            if not body:
                continue
            publication, sender = _publication_name(headers.get("from", ""))
            received_at = datetime.fromtimestamp(
                int(raw["internalDate"]) / 1000, tz=window.start.tzinfo
            )
            link_result = extract_editorial_links_with_stats(
                html,
                limit=self.settings.app.max_articles_per_newsletter,
            )
            results.append(
                SourceItem(
                    message_id=str(raw["id"]),
                    publication=publication,
                    sender=sender,
                    subject=headers.get("subject", "(no subject)"),
                    received_at=received_at,
                    email_text=body,
                    article_urls=link_result.urls,
                    link_stats=link_result.stats(),
                )
            )
        return results


def fixture_sources(path) -> list[SourceItem]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("fixture source file must contain a JSON list")
    result = []
    for item in raw:
        result.append(
            SourceItem(
                message_id=str(item["message_id"]),
                publication=str(item["publication"]),
                sender=str(item.get("sender", "fixture@example.com")),
                subject=str(item["subject"]),
                received_at=datetime.fromisoformat(item["received_at"]),
                email_text=str(item["email_text"]),
                article_urls=list(item.get("article_urls", [])),
            )
        )
    return result
