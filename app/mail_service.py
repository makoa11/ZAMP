from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from .config import AppConfig
from .mail_ingestion import (
    MailIngestionService,
    TokenManager,
    normalize_invoice_match_patterns,
    suggest_invoice_match_pattern_from_filename,
)
from .mail_providers import (
    GmailClient,
    GMAIL_READONLY_SCOPE,
    OutlookClient,
    gmail_expiration_ms,
    new_pkce_code_verifier,
    parse_rfc3339,
    pkce_code_challenge,
    token_expires_at,
)
from .mail_store import MailDatabase, MailIntegrationError, MailRepository, PdfStorage, TokenCipher


class MailIntegration:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._ready = False
        self._repo: MailRepository | None = None
        self._cipher: TokenCipher | None = None
        self._storage: PdfStorage | None = None
        self._gmail: GmailClient | None = None
        self._outlook: OutlookClient | None = None
        self._token_manager: TokenManager | None = None
        self._ingestion: MailIngestionService | None = None

    @property
    def repo(self) -> MailRepository:
        self.ensure_ready()
        assert self._repo is not None
        return self._repo

    @property
    def cipher(self) -> TokenCipher:
        self.ensure_ready()
        assert self._cipher is not None
        return self._cipher

    @property
    def gmail(self) -> GmailClient:
        self.ensure_ready()
        assert self._gmail is not None
        return self._gmail

    @property
    def outlook(self) -> OutlookClient:
        self.ensure_ready()
        assert self._outlook is not None
        return self._outlook

    @property
    def storage(self) -> PdfStorage:
        self.ensure_ready()
        assert self._storage is not None
        return self._storage

    @property
    def token_manager(self) -> TokenManager:
        self.ensure_ready()
        assert self._token_manager is not None
        return self._token_manager

    @property
    def ingestion(self) -> MailIngestionService:
        self.ensure_ready()
        assert self._ingestion is not None
        return self._ingestion

    def ensure_ready(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            database = MailDatabase.from_config(self.config)
            repo = MailRepository(database)
            repo.initialize_schema()
            cipher = TokenCipher.from_config(self.config)
            storage = PdfStorage.from_config(self.config)
            gmail = GmailClient(self.config)
            outlook = OutlookClient(self.config)
            token_manager = TokenManager(repo=repo, cipher=cipher, gmail=gmail, outlook=outlook)
            ingestion = MailIngestionService(
                repo=repo,
                storage=storage,
                token_manager=token_manager,
                gmail=gmail,
                outlook=outlook,
            )
            self._repo = repo
            self._cipher = cipher
            self._storage = storage
            self._gmail = gmail
            self._outlook = outlook
            self._token_manager = token_manager
            self._ingestion = ingestion
            self._ready = True

    def close(self) -> None:
        with self._lock:
            try:
                if self._repo is not None:
                    self._repo.database.close()
            finally:
                self._repo = None
                self._cipher = None
                self._storage = None
                self._gmail = None
                self._outlook = None
                self._token_manager = None
                self._ingestion = None
                self._ready = False

    def start_oauth(
        self,
        *,
        provider: str,
        owner_user_id: str,
        redirect_after: str | None = None,
    ) -> dict[str, str]:
        self._validate_provider(provider)
        code_verifier = new_pkce_code_verifier() if provider == "gmail" else None
        state = self.repo.create_oauth_state(
            provider=provider,
            owner_user_id=owner_user_id,
            redirect_after=redirect_after,
            encrypted_code_verifier=self.cipher.encrypt(code_verifier) if code_verifier else None,
        )
        redirect_uri = self.oauth_redirect_uri(provider)
        if provider == "gmail":
            authorization_url = self.gmail.authorization_url(
                redirect_uri=redirect_uri,
                state=state,
                code_challenge=pkce_code_challenge(code_verifier) if code_verifier else None,
            )
        else:
            authorization_url = self.outlook.authorization_url(redirect_uri=redirect_uri, state=state)
        return {"authorization_url": authorization_url, "state": state}

    def complete_oauth(self, *, provider: str, state: str, code: str, owner_user_id: str) -> str:
        self._validate_provider(provider)
        state_row = self.repo.consume_oauth_state(
            provider=provider,
            state=state,
            owner_user_id=owner_user_id,
        )
        if not state_row:
            return self._frontend_redirect({"mail_error": "invalid_oauth_state"})

        try:
            if provider == "gmail":
                encrypted_code_verifier = state_row.get("encrypted_code_verifier")
                if not isinstance(encrypted_code_verifier, str) or not encrypted_code_verifier:
                    raise MailIntegrationError("OAuth PKCE verifier is missing.")
                self._complete_gmail_oauth(
                    owner_user_id=str(state_row["owner_user_id"]),
                    code=code,
                    code_verifier=self.cipher.decrypt(encrypted_code_verifier),
                )
            else:
                self._complete_outlook_oauth(owner_user_id=str(state_row["owner_user_id"]), code=code)
        except Exception as exc:
            return self._frontend_redirect({"mail_error": str(exc)[:200]})

        return self._frontend_redirect({"mail_connected": provider})

    def list_accounts(self, *, owner_user_id: str) -> list[dict[str, Any]]:
        accounts = self.repo.list_accounts(owner_user_id=owner_user_id)
        return [_public_account(row) for row in accounts]

    def get_invoice_match_patterns(self, *, owner_user_id: str) -> list[str]:
        return self.repo.get_invoice_match_patterns(owner_user_id=owner_user_id)

    def update_invoice_match_patterns(self, *, owner_user_id: str, patterns: list[str]) -> list[str]:
        normalized = normalize_invoice_match_patterns(patterns)
        stored = self.repo.set_invoice_match_patterns(owner_user_id=owner_user_id, patterns=normalized)
        if stored:
            self._enqueue_owner_fallbacks(owner_user_id=owner_user_id, patterns=stored)
        return stored

    def suggest_invoice_match_pattern(self, *, filename: str) -> str:
        return suggest_invoice_match_pattern_from_filename(filename)

    def disconnect_account(self, *, owner_user_id: str, account_id: int) -> bool:
        account = self.repo.get_account(account_id)
        if account and account.get("owner_user_id") == owner_user_id and account.get("status") == "active":
            try:
                access_token = self.token_manager.access_token_for(account_id)
                if account.get("provider") == "gmail":
                    self.gmail.stop_watch(access_token=access_token)
            except Exception:
                pass
        return self.repo.disconnect_account(account_id=account_id, owner_user_id=owner_user_id)

    def handle_gmail_pubsub(self, *, payload: dict[str, Any], subscription: str | None = None) -> dict[str, Any]:
        expected_subscription = self.config.gmail_pubsub_subscription
        if expected_subscription and subscription and subscription != expected_subscription:
            raise MailIntegrationError("Gmail Pub/Sub subscription did not match configured subscription.")

        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        data = message.get("data")
        notification: dict[str, Any] = {}
        if isinstance(data, str):
            notification = json.loads(_base64url_decode(data).decode("utf-8"))
        email = notification.get("emailAddress")
        history_id = notification.get("historyId")
        message_id = message.get("messageId")
        event_key = str(message_id or f"{email}:{history_id}")

        accounts = self.repo.list_accounts_by_provider_email(provider="gmail", email=str(email or ""))
        if not accounts:
            self.repo.insert_webhook_event(
                provider="gmail",
                event_key=event_key,
                account_id=None,
                payload=payload,
            )
            return {"accepted": True, "duplicates": 0, "accounts": 0}

        accepted = 0
        duplicates = 0
        for account in accounts:
            account_id = int(account["id"])
            is_new = self.repo.insert_webhook_event(
                provider="gmail",
                event_key=event_key,
                account_id=account_id,
                payload=payload,
            )
            if not is_new:
                duplicates += 1
                continue
            if account.get("status") == "active" and history_id:
                self.repo.enqueue_job(
                    job_type="gmail_history_sync",
                    payload={"account_id": account_id, "history_id": str(history_id)},
                    unique_key=f"gmail-history:{account_id}:{history_id}",
                )
                accepted += 1
        return {"accepted": True, "duplicates": duplicates, "accounts": accepted}

    def handle_outlook_notifications(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        accepted = 0
        duplicates = 0
        rejected = 0
        for notification in payload.get("value") or []:
            if not isinstance(notification, dict):
                rejected += 1
                continue
            subscription_id = notification.get("subscriptionId")
            if not isinstance(subscription_id, str):
                rejected += 1
                continue
            account = self.repo.get_account_by_outlook_subscription(subscription_id)
            if not account or account.get("status") != "active":
                rejected += 1
                continue
            if notification.get("clientState") != account.get("webhook_client_state"):
                rejected += 1
                continue
            resource_data = notification.get("resourceData")
            message_id = resource_data.get("id") if isinstance(resource_data, dict) else None
            if not isinstance(message_id, str):
                message_id = _message_id_from_resource(notification.get("resource"))
            if not message_id:
                rejected += 1
                continue

            event_key = ":".join(
                [
                    subscription_id,
                    str(notification.get("changeType") or "created"),
                    message_id,
                    str(notification.get("subscriptionExpirationDateTime") or ""),
                ]
            )
            is_new = self.repo.insert_webhook_event(
                provider="outlook",
                event_key=event_key,
                account_id=int(account["id"]),
                payload=notification,
            )
            if not is_new:
                duplicates += 1
                continue
            self.repo.enqueue_job(
                job_type="outlook_message_fetch",
                payload={"account_id": int(account["id"]), "message_id": message_id},
                unique_key=f"outlook-message:{account['id']}:{message_id}",
            )
            accepted += 1
        return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected}

    def oauth_redirect_uri(self, provider: str) -> str:
        return f"{self.config.app_url.rstrip('/')}/api/mail/oauth/{provider}/callback"

    def _complete_gmail_oauth(self, *, owner_user_id: str, code: str, code_verifier: str | None = None) -> None:
        token_response = self.gmail.exchange_code(
            code=code,
            redirect_uri=self.oauth_redirect_uri("gmail"),
            code_verifier=code_verifier,
        )
        _require_granted_scopes(token_response, [GMAIL_READONLY_SCOPE], "Google")
        access_token = _required_token(token_response, "access_token")
        profile = self.gmail.profile(access_token)
        email = _required_token(profile, "emailAddress").lower()
        refresh_token = token_response.get("refresh_token")
        existing = self.repo.get_account_by_provider_email(provider="gmail", email=email)
        if not refresh_token and existing and existing.get("owner_user_id") == owner_user_id:
            refresh_token = self.cipher.decrypt(existing.get("encrypted_refresh_token"))
        if not refresh_token:
            raise MailIntegrationError("Google did not return a refresh token; retry consent.")

        account = self.repo.upsert_account(
            owner_user_id=owner_user_id,
            provider="gmail",
            email=email,
            encrypted_access_token=self.cipher.encrypt(access_token),
            encrypted_refresh_token=self.cipher.encrypt(str(refresh_token)) or "",
            token_expires_at=token_expires_at(token_response),
            scope=token_response.get("scope") if isinstance(token_response.get("scope"), str) else None,
        )
        watch = self.gmail.watch(access_token=access_token)
        self.repo.update_gmail_watch(
            account_id=int(account["id"]),
            history_id=str(watch.get("historyId")) if watch.get("historyId") is not None else None,
            expiration=gmail_expiration_ms(watch.get("expiration")),
        )
        self.repo.enqueue_job(
            job_type="gmail_fallback_sync",
            payload={"account_id": int(account["id"])},
            unique_key=f"gmail-fallback:{account['id']}:initial",
        )

    def _complete_outlook_oauth(self, *, owner_user_id: str, code: str) -> None:
        token_response = self.outlook.exchange_code(code=code, redirect_uri=self.oauth_redirect_uri("outlook"))
        access_token = _required_token(token_response, "access_token")
        refresh_token = _required_token(token_response, "refresh_token")
        me = self.outlook.me(access_token)
        email = str(me.get("mail") or me.get("userPrincipalName") or "").lower()
        if not email:
            raise MailIntegrationError("Microsoft profile did not include an email address.")

        client_state = secrets.token_urlsafe(24)
        account = self.repo.upsert_account(
            owner_user_id=owner_user_id,
            provider="outlook",
            email=email,
            encrypted_access_token=self.cipher.encrypt(access_token),
            encrypted_refresh_token=self.cipher.encrypt(refresh_token) or "",
            token_expires_at=token_expires_at(token_response),
            scope=token_response.get("scope") if isinstance(token_response.get("scope"), str) else None,
            webhook_client_state=client_state,
        )
        subscription = self.outlook.create_subscription(
            access_token=access_token,
            notification_url=self._outlook_notification_url(),
            client_state=client_state,
        )
        self.repo.update_outlook_subscription(
            account_id=int(account["id"]),
            subscription_id=_required_token(subscription, "id"),
            expiration=parse_rfc3339(subscription.get("expirationDateTime")),
        )
        self.repo.enqueue_job(
            job_type="outlook_delta_sync",
            payload={"account_id": int(account["id"])},
            unique_key=f"outlook-delta:{account['id']}:initial",
        )

    def _outlook_notification_url(self) -> str:
        return f"{self.config.app_url.rstrip('/')}/webhooks/outlook"

    def _enqueue_owner_fallbacks(self, *, owner_user_id: str, patterns: list[str]) -> None:
        settings_fingerprint = hashlib.sha256(
            json.dumps(patterns, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        for account in self.repo.list_accounts(owner_user_id=owner_user_id):
            if account.get("status") != "active":
                continue
            account_id = int(account["id"])
            if account.get("provider") == "gmail":
                self.repo.enqueue_job(
                    job_type="gmail_fallback_sync",
                    payload={"account_id": account_id},
                    unique_key=f"gmail-fallback:{account_id}:settings:{settings_fingerprint}",
                )
            elif account.get("provider") == "outlook":
                self.repo.enqueue_job(
                    job_type="outlook_delta_sync",
                    payload={"account_id": account_id},
                    unique_key=f"outlook-delta:{account_id}:settings:{settings_fingerprint}",
                )

    def _frontend_redirect(self, params: dict[str, str]) -> str:
        separator = "&" if "?" in self.config.mail_frontend_redirect_url else "?"
        return self.config.mail_frontend_redirect_url + separator + urlencode(params)

    def _validate_provider(self, provider: str) -> None:
        if provider not in {"gmail", "outlook"}:
            raise MailIntegrationError(f"Unsupported mail provider: {provider}")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _required_token(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise MailIntegrationError(f"Provider response did not include {key}.")
    return value


def _require_granted_scopes(token_response: dict[str, Any], required_scopes: list[str], provider: str) -> None:
    scope = token_response.get("scope")
    granted_scopes = set(scope.split()) if isinstance(scope, str) else set()
    missing = [scope for scope in required_scopes if scope not in granted_scopes]
    if missing:
        raise MailIntegrationError(f"{provider} did not grant required scopes: {', '.join(missing)}.")


def _message_id_from_resource(resource: Any) -> str | None:
    if not isinstance(resource, str):
        return None
    marker = "/messages/"
    if marker not in resource:
        return None
    return resource.rsplit(marker, 1)[-1].strip("'")


def _public_account(row: dict[str, Any]) -> dict[str, Any]:
    def serialize(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    return {key: serialize(value) for key, value in row.items()}
