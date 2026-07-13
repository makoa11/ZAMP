from __future__ import annotations

import base64
import json
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .config import AppConfig
from .invoice_overlay import render_invoice_parse_overlay_pdf
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

    def list_invoice_review_items(self, *, owner_user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.repo.list_invoice_review_items(owner_user_id=owner_user_id, limit=limit)
        return [_public_invoice_review_item(row) for row in rows]

    def get_invoice_pdf_file(self, *, owner_user_id: str, pdf_file_id: int) -> dict[str, Any] | None:
        row = self.repo.get_pdf_file_for_owner(owner_user_id=owner_user_id, pdf_file_id=pdf_file_id)
        if not row:
            return None
        storage_path = str(row.get("storage_path") or "")
        return {
            "pdf_file_id": int(row["pdf_file_id"]),
            "filename": str(row.get("filename") or f"invoice-{pdf_file_id}.pdf"),
            "sha256": str(row.get("sha256") or ""),
            "byte_size": int(row.get("byte_size") or 0),
            "path": _storage_pdf_path(self.storage.root, storage_path),
        }

    def get_invoice_match_patterns(self, *, owner_user_id: str) -> list[str]:
        return self.repo.get_invoice_match_patterns(owner_user_id=owner_user_id)

    def update_invoice_match_patterns(self, *, owner_user_id: str, patterns: list[str]) -> list[str]:
        normalized = normalize_invoice_match_patterns(patterns)
        stored = self.repo.set_invoice_match_patterns(owner_user_id=owner_user_id, patterns=normalized)
        self._enqueue_owner_fallbacks(owner_user_id=owner_user_id, patterns=stored)
        return stored

    def get_extraction_settings(self, *, owner_user_id: str) -> dict[str, bool]:
        return {
            "use_ai": self.repo.get_ai_extraction_enabled(owner_user_id=owner_user_id),
        }

    def update_extraction_settings(self, *, owner_user_id: str, use_ai: bool) -> dict[str, bool]:
        was_enabled = self.repo.get_ai_extraction_enabled(owner_user_id=owner_user_id)
        enabled = self.repo.set_ai_extraction_enabled(
            owner_user_id=owner_user_id,
            enabled=use_ai,
        )
        if enabled and not was_enabled:
            enqueue = getattr(self.repo, "enqueue_owner_ai_fallback_jobs", None)
            if callable(enqueue):
                enqueue(
                    owner_user_id=owner_user_id,
                    reprocess_key=secrets.token_hex(8),
                )
        return {"use_ai": enabled}

    def suggest_invoice_match_pattern(self, *, filename: str) -> str:
        return suggest_invoice_match_pattern_from_filename(filename)

    def list_invoices(self, *, owner_user_id: str, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        rows = self.repo.list_mail_invoices(
            owner_user_id=owner_user_id,
            limit=limit,
            offset=offset,
        )
        return [_public_invoice_queue_row(row) for row in rows]

    def count_invoices(self, *, owner_user_id: str) -> int:
        return self.repo.count_mail_invoices(owner_user_id=owner_user_id)

    def get_invoice(self, *, owner_user_id: str, pdf_file_id: int) -> dict[str, Any] | None:
        row = self.repo.get_mail_invoice_detail(
            owner_user_id=owner_user_id,
            pdf_file_id=pdf_file_id,
        )
        return _public_invoice_detail(row) if row else None

    def invoice_overlay_pdf(
        self,
        *,
        owner_user_id: str,
        pdf_file_id: int,
        box_mode: str = "parsed",
    ) -> bytes | None:
        source = self.repo.get_mail_invoice_overlay_source(
            owner_user_id=owner_user_id,
            pdf_file_id=pdf_file_id,
        )
        if not source:
            return None
        storage_path = source.get("storage_path")
        if not isinstance(storage_path, str):
            return None
        pdf_path = _storage_pdf_path(self.storage.root, storage_path)
        return render_invoice_parse_overlay_pdf(
            pdf_path.read_bytes(),
            source.get("raw_parse_result") if isinstance(source.get("raw_parse_result"), dict) else {},
            box_mode=box_mode,
        )

    def disconnect_account(self, *, owner_user_id: str, account_id: int) -> bool:
        account = self.repo.get_account(account_id)
        if account and account.get("owner_user_id") == owner_user_id and account.get("status") == "active":
            try:
                access_token = self.token_manager.access_token_for(account_id)
                if account.get("provider") == "gmail":
                    self.gmail.stop_watch(access_token=access_token)
                elif account.get("provider") == "outlook" and account.get(
                    "outlook_subscription_id"
                ):
                    self.outlook.delete_subscription(
                        access_token=access_token,
                        subscription_id=str(account["outlook_subscription_id"]),
                    )
            except Exception:
                pass
        return self.repo.disconnect_account(account_id=account_id, owner_user_id=owner_user_id)

    def handle_gmail_pubsub(self, *, payload: dict[str, Any], subscription: str | None = None) -> dict[str, Any]:
        expected_subscription = self.config.gmail_pubsub_subscription
        if expected_subscription and subscription != expected_subscription:
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
        existing = self.repo.get_account_by_provider_email(
            owner_user_id=owner_user_id,
            provider="gmail",
            email=email,
        )
        if not refresh_token and existing:
            refresh_token = self.cipher.decrypt(existing.get("encrypted_refresh_token"))
        if not refresh_token:
            raise MailIntegrationError("Google did not return a refresh token; retry consent.")

        watch = self.gmail.watch(access_token=access_token)
        try:
            account = self.repo.upsert_account(
                owner_user_id=owner_user_id,
                provider="gmail",
                email=email,
                encrypted_access_token=self.cipher.encrypt(access_token),
                encrypted_refresh_token=self.cipher.encrypt(str(refresh_token)) or "",
                token_expires_at=token_expires_at(token_response),
                scope=(
                    token_response.get("scope")
                    if isinstance(token_response.get("scope"), str)
                    else None
                ),
                gmail_history_id=(
                    str(watch.get("historyId")) if watch.get("historyId") is not None else None
                ),
                gmail_watch_expiration=gmail_expiration_ms(watch.get("expiration")),
            )
        except Exception:
            if not existing:
                try:
                    self.gmail.stop_watch(access_token=access_token)
                except Exception:
                    pass
            raise
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
        subscription = self.outlook.create_subscription(
            access_token=access_token,
            notification_url=self._outlook_notification_url(),
            client_state=client_state,
        )
        subscription_id = _required_token(subscription, "id")
        try:
            account = self.repo.upsert_account(
                owner_user_id=owner_user_id,
                provider="outlook",
                email=email,
                encrypted_access_token=self.cipher.encrypt(access_token),
                encrypted_refresh_token=self.cipher.encrypt(refresh_token) or "",
                token_expires_at=token_expires_at(token_response),
                scope=(
                    token_response.get("scope")
                    if isinstance(token_response.get("scope"), str)
                    else None
                ),
                webhook_client_state=client_state,
                outlook_subscription_id=subscription_id,
                outlook_subscription_expiration=parse_rfc3339(
                    subscription.get("expirationDateTime")
                ),
            )
        except Exception:
            try:
                self.outlook.delete_subscription(
                    access_token=access_token,
                    subscription_id=subscription_id,
                )
            except Exception:
                pass
            raise
        self.repo.enqueue_job(
            job_type="outlook_delta_sync",
            payload={"account_id": int(account["id"])},
            unique_key=f"outlook-delta:{account['id']}:initial",
        )

    def _outlook_notification_url(self) -> str:
        return f"{self.config.app_url.rstrip('/')}/webhooks/outlook"

    def _enqueue_owner_fallbacks(self, *, owner_user_id: str, patterns: list[str]) -> None:
        reprocess_key = secrets.token_hex(8)
        for account in self.repo.list_accounts(owner_user_id=owner_user_id):
            if account.get("status") != "active":
                continue
            account_id = int(account["id"])
            if account.get("provider") == "gmail":
                self.repo.enqueue_job(
                    job_type="gmail_fallback_sync",
                    payload={"account_id": account_id, "reprocess_key": reprocess_key},
                    unique_key=f"gmail-fallback:{account_id}:settings:{reprocess_key}",
                )
            elif account.get("provider") == "outlook":
                self.repo.enqueue_job(
                    job_type="outlook_delta_sync",
                    payload={"account_id": account_id, "reprocess_key": reprocess_key},
                    unique_key=f"outlook-delta:{account_id}:settings:{reprocess_key}",
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
    return {key: _serialize_public_value(value) for key, value in row.items()}


def _public_invoice_review_item(row: dict[str, Any]) -> dict[str, Any]:
    parse_result = _json_dict(row.get("parse_result"))
    fields = _json_dict(parse_result.get("fields"))
    invoice_number = row.get("invoice_number") or _scalar_field_value(fields.get("invoice_number"))
    vendor = row.get("vendor") or _party_display(fields.get("seller"))
    amount = _amount_display(row.get("amount_due"), row.get("currency")) or _money_display(fields.get("balance_due"))
    pdf_file_id = int(row["pdf_file_id"])
    attachment_id = row.get("attachment_id")

    return {
        "attachment_id": int(attachment_id) if attachment_id is not None else None,
        "pdf_file_id": pdf_file_id,
        "filename": str(row.get("filename") or ""),
        "invoice_number": invoice_number or str(row.get("filename") or ""),
        "vendor": vendor,
        "amount": amount,
        "subject": row.get("subject"),
        "sender": row.get("sender"),
        "received_at": _serialize_public_value(row.get("received_at")),
        "provider": row.get("provider"),
        "account_email": row.get("account_email"),
        "candidate_reason": row.get("candidate_reason"),
        "parse_status": row.get("parse_status") or "pending",
        "parsed_at": _serialize_public_value(row.get("parsed_at")),
        "warnings": _json_list(row.get("parse_warnings")),
        "decision": row.get("decision"),
        "confidence": row.get("decision_confidence"),
        "next_action": row.get("next_action"),
        "pdf_url": f"/api/mail/invoices/{pdf_file_id}/overlay.pdf?boxes=all",
    }


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return []
        return decoded if isinstance(decoded, list) else []
    return []


def _scalar_field_value(field: Any) -> str | None:
    if not isinstance(field, dict):
        return None
    value = field.get("value")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _party_display(field: Any) -> str | None:
    if not isinstance(field, dict):
        return None
    raw = field.get("raw")
    if not isinstance(raw, str):
        return None
    for line in raw.splitlines():
        text = line.strip()
        if text:
            return text
    return None


def _money_display(field: Any) -> str | None:
    if not isinstance(field, dict) or field.get("amount") is None:
        return None
    return _amount_display(field.get("amount"), field.get("currency"))


def _amount_display(amount_value: Any, currency_value: Any = None) -> str | None:
    if amount_value is None:
        return None
    try:
        amount = float(amount_value)
    except (TypeError, ValueError):
        return None
    currency = str(currency_value or "").strip()
    if currency:
        return f"{currency} {amount:,.2f}"
    return f"{amount:,.2f}"



def _public_invoice_queue_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "pdf_file_id": row.get("pdf_file_id"),
        "attachment_id": row.get("attachment_id"),
        "vendor": row.get("vendor"),
        "invoice_number": row.get("invoice_number"),
        "purchase_order": row.get("purchase_order"),
        "amount_due": row.get("amount_due"),
        "currency": row.get("currency"),
        "received_date": _serialize_public_value(row.get("received_at")),
        "parse_status": row.get("parse_status"),
        "parser_method": row.get("parser_method"),
        "parse_confidence": row.get("parse_confidence"),
        "decision": row.get("decision"),
        "confidence": row.get("decision_confidence"),
        "next_action": row.get("next_action"),
        "filename": row.get("filename"),
        "sender": row.get("sender"),
        "subject": row.get("subject"),
    }


def _public_invoice_detail(row: dict[str, Any]) -> dict[str, Any]:
    normalized_invoice = row.get("normalized_invoice") if isinstance(row.get("normalized_invoice"), dict) else {}
    checks = row.get("decision_checks") if isinstance(row.get("decision_checks"), list) else []
    audit = row.get("decision_audit") if isinstance(row.get("decision_audit"), dict) else {}
    decision = None
    if row.get("decision"):
        decision = {
            "decision": row.get("decision"),
            "confidence": row.get("decision_confidence"),
            "summary": row.get("decision_summary"),
            "next_action": row.get("decision_next_action"),
        }
    return {
        "pdf_file_id": row.get("pdf_file_id"),
        "owner_user_id": row.get("owner_user_id"),
        "message": {
            "provider": row.get("provider"),
            "provider_message_id": row.get("provider_message_id"),
            "thread_id": row.get("thread_id"),
            "conversation_id": row.get("conversation_id"),
            "sender": row.get("sender"),
            "subject": row.get("subject"),
            "received_at": _serialize_public_value(row.get("received_at")),
            "metadata": row.get("message_metadata") if isinstance(row.get("message_metadata"), dict) else {},
        },
        "attachment": {
            "id": row.get("attachment_id"),
            "filename": row.get("filename"),
            "mime_type": row.get("mime_type"),
            "candidate_reason": row.get("candidate_reason"),
            "metadata": row.get("attachment_metadata")
            if isinstance(row.get("attachment_metadata"), dict)
            else {},
        },
        "pdf": {
            "sha256": row.get("sha256"),
            "byte_size": row.get("byte_size"),
        },
        "raw_parse": _raw_parse_summary(row),
        "normalized_invoice": normalized_invoice,
        "decision": decision,
        "checks": checks,
        "audit": audit,
        "ap_context": audit.get("ap_context_summary") if isinstance(audit.get("ap_context_summary"), dict) else None,
        "parser_warnings": row.get("raw_parse_warnings") if isinstance(row.get("raw_parse_warnings"), list) else [],
    }


def _raw_parse_summary(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("raw_parse_result") if isinstance(row.get("raw_parse_result"), dict) else {}
    pages = raw.get("pages") if isinstance(raw.get("pages"), list) else []
    fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
    return {
        "status": row.get("raw_parse_status") or row.get("parse_status"),
        "parser_version": row.get("raw_parser_version"),
        "parser_method": row.get("parser_method"),
        "page_count": len(pages),
        "field_keys": sorted(fields.keys()),
        "warnings": row.get("raw_parse_warnings") if isinstance(row.get("raw_parse_warnings"), list) else [],
    }


def _storage_pdf_path(root: str | Path, storage_path: str) -> Path:
    relative_path = Path(storage_path)
    if not storage_path or relative_path.is_absolute() or ".." in relative_path.parts:
        raise MailIntegrationError("PDF storage path must be relative to MAIL_PDF_STORAGE_DIR.")
    return Path(root) / relative_path


def _serialize_public_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value
