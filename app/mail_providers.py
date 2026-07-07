from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

from .config import AppConfig
from .mail_http import JsonHttpClient
from .mail_store import MailIntegrationError, require_mail_value, utc_now


GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

GMAIL_SCOPES = [
    "openid",
    "email",
    GMAIL_READONLY_SCOPE,
]

OUTLOOK_SCOPES = [
    "offline_access",
    "User.Read",
    "Mail.Read",
]


def new_pkce_code_verifier() -> str:
    return secrets.token_urlsafe(64)


def pkce_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def token_expires_at(token_response: dict[str, Any]) -> datetime:
    expires_in = int(token_response.get("expires_in") or 3600)
    return utc_now() + timedelta(seconds=max(60, expires_in))


def parse_rfc3339(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def gmail_expiration_ms(value: str | int | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, UTC)
    except (TypeError, ValueError, OSError):
        return None


class GmailClient:
    def __init__(self, config: AppConfig, http: JsonHttpClient | None = None) -> None:
        self.config = config
        self.http = http or JsonHttpClient()

    def authorization_url(self, *, redirect_uri: str, state: str, code_challenge: str | None = None) -> str:
        client_id = require_mail_value(self.config.google_oauth_client_id, "GOOGLE_OAUTH_CLIENT_ID")
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(GMAIL_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "include_granted_scopes": "true",
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)

    def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str | None = None) -> dict[str, Any]:
        payload = {
            "client_id": require_mail_value(self.config.google_oauth_client_id, "GOOGLE_OAUTH_CLIENT_ID"),
            "client_secret": require_mail_value(
                self.config.google_oauth_client_secret,
                "GOOGLE_OAUTH_CLIENT_SECRET",
            ),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
        if code_verifier:
            payload["code_verifier"] = code_verifier
        return self.http.post_form(
            "https://oauth2.googleapis.com/token",
            payload=payload,
        )

    def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        return self.http.post_form(
            "https://oauth2.googleapis.com/token",
            payload={
                "client_id": require_mail_value(self.config.google_oauth_client_id, "GOOGLE_OAUTH_CLIENT_ID"),
                "client_secret": require_mail_value(
                    self.config.google_oauth_client_secret,
                    "GOOGLE_OAUTH_CLIENT_SECRET",
                ),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )

    def profile(self, access_token: str) -> dict[str, Any]:
        return self.http.get_json(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            access_token=access_token,
        )

    def watch(self, *, access_token: str) -> dict[str, Any]:
        topic_name = require_mail_value(self.config.gmail_pubsub_topic, "GMAIL_PUBSUB_TOPIC")
        return self.http.post_json(
            "https://gmail.googleapis.com/gmail/v1/users/me/watch",
            access_token=access_token,
            payload={
                "topicName": topic_name,
                "labelIds": ["INBOX"],
                "labelFilterBehavior": "include",
            },
        )

    def stop_watch(self, *, access_token: str) -> None:
        self.http.post_json(
            "https://gmail.googleapis.com/gmail/v1/users/me/stop",
            access_token=access_token,
            payload={},
        )

    def history_list(
        self,
        *,
        access_token: str,
        start_history_id: str,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        params = {
            "startHistoryId": start_history_id,
            "historyTypes": "messageAdded",
            "labelId": "INBOX",
        }
        if page_token:
            params["pageToken"] = page_token
        return self.http.get_json(
            "https://gmail.googleapis.com/gmail/v1/users/me/history?" + urlencode(params),
            access_token=access_token,
        )

    def message(self, *, access_token: str, message_id: str) -> dict[str, Any]:
        params = {"format": "full"}
        return self.http.get_json(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{quote(message_id)}?"
            + urlencode(params),
            access_token=access_token,
        )

    def attachment(self, *, access_token: str, message_id: str, attachment_id: str) -> dict[str, Any]:
        return self.http.get_json(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/"
            f"{quote(message_id)}/attachments/{quote(attachment_id)}",
            access_token=access_token,
        )

    def list_messages(
        self,
        *,
        access_token: str,
        query: str,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        params = {"q": query}
        if page_token:
            params["pageToken"] = page_token
        return self.http.get_json(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages?" + urlencode(params),
            access_token=access_token,
        )


class OutlookClient:
    def __init__(self, config: AppConfig, http: JsonHttpClient | None = None) -> None:
        self.config = config
        self.http = http or JsonHttpClient()

    @property
    def tenant_id(self) -> str:
        return self.config.microsoft_tenant_id or "common"

    @property
    def token_endpoint(self) -> str:
        return f"https://login.microsoftonline.com/{quote(self.tenant_id)}/oauth2/v2.0/token"

    def authorization_url(self, *, redirect_uri: str, state: str) -> str:
        client_id = require_mail_value(self.config.microsoft_client_id, "MICROSOFT_CLIENT_ID")
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "response_mode": "query",
            "scope": " ".join(OUTLOOK_SCOPES),
            "state": state,
        }
        return (
            f"https://login.microsoftonline.com/{quote(self.tenant_id)}/oauth2/v2.0/authorize?"
            + urlencode(params)
        )

    def exchange_code(self, *, code: str, redirect_uri: str) -> dict[str, Any]:
        return self.http.post_form(
            self.token_endpoint,
            payload={
                "client_id": require_mail_value(self.config.microsoft_client_id, "MICROSOFT_CLIENT_ID"),
                "client_secret": require_mail_value(
                    self.config.microsoft_client_secret,
                    "MICROSOFT_CLIENT_SECRET",
                ),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "scope": " ".join(OUTLOOK_SCOPES),
            },
        )

    def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        return self.http.post_form(
            self.token_endpoint,
            payload={
                "client_id": require_mail_value(self.config.microsoft_client_id, "MICROSOFT_CLIENT_ID"),
                "client_secret": require_mail_value(
                    self.config.microsoft_client_secret,
                    "MICROSOFT_CLIENT_SECRET",
                ),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": " ".join(OUTLOOK_SCOPES),
            },
        )

    def me(self, access_token: str) -> dict[str, Any]:
        return self.http.get_json(
            "https://graph.microsoft.com/v1.0/me?$select=id,mail,userPrincipalName",
            access_token=access_token,
        )

    def create_subscription(
        self,
        *,
        access_token: str,
        notification_url: str,
        client_state: str,
    ) -> dict[str, Any]:
        expiration = utc_now() + timedelta(days=2)
        return self.http.post_json(
            "https://graph.microsoft.com/v1.0/subscriptions",
            access_token=access_token,
            payload={
                "changeType": "created",
                "notificationUrl": notification_url,
                "resource": "me/mailFolders('Inbox')/messages",
                "expirationDateTime": expiration.isoformat().replace("+00:00", "Z"),
                "clientState": client_state,
            },
        )

    def renew_subscription(
        self,
        *,
        access_token: str,
        subscription_id: str,
    ) -> dict[str, Any]:
        expiration = utc_now() + timedelta(days=2)
        return self.http.patch_json(
            f"https://graph.microsoft.com/v1.0/subscriptions/{quote(subscription_id)}",
            access_token=access_token,
            payload={"expirationDateTime": expiration.isoformat().replace("+00:00", "Z")},
        )

    def message(self, *, access_token: str, message_id: str) -> dict[str, Any]:
        params = {
            "$select": "id,subject,from,sender,receivedDateTime,hasAttachments,conversationId,internetMessageId"
        }
        return self.http.get_json(
            f"https://graph.microsoft.com/v1.0/me/messages/{quote(message_id)}?" + urlencode(params),
            access_token=access_token,
        )

    def attachments(self, *, access_token: str, message_id: str) -> list[dict[str, Any]]:
        response = self.http.get_json(
            f"https://graph.microsoft.com/v1.0/me/messages/{quote(message_id)}/attachments",
            access_token=access_token,
        )
        value = response.get("value") if isinstance(response, dict) else None
        if not isinstance(value, list):
            raise MailIntegrationError("Outlook attachments response was not a list.")
        return [item for item in value if isinstance(item, dict)]

    def delta_messages(self, *, access_token: str, delta_link: str | None = None) -> dict[str, Any]:
        url = delta_link or (
            "https://graph.microsoft.com/v1.0/me/mailFolders('Inbox')/messages/delta?"
            + urlencode(
                {
                    "$select": "id,subject,from,receivedDateTime,hasAttachments,conversationId",
                }
            )
        )
        return self.http.get_json(url, access_token=access_token)
