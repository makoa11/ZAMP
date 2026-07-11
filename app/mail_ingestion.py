from __future__ import annotations

import base64
import binascii
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Iterable

from .mail_http import HttpClientError
from .mail_providers import GmailClient, OutlookClient, gmail_expiration_ms, parse_rfc3339
from .mail_store import MailIntegrationError, MailRepository, PdfStorage, TokenCipher


MAIL_JOB_TYPES = {
    "gmail_history_sync",
    "gmail_message_fetch",
    "gmail_fallback_sync",
    "outlook_message_fetch",
    "outlook_delta_sync",
    "renew_mail_subscriptions",
    "parse_pdf",
}

GMAIL_PDF_FALLBACK_QUERY = "newer_than:1d has:attachment filename:pdf"
MAX_INVOICE_MATCH_PATTERNS = 25
MAX_INVOICE_MATCH_PATTERN_LENGTH = 500
MAX_INVOICE_SAMPLE_FILENAME_LENGTH = 255
INVOICE_FILENAME_SEPARATOR_PATTERN = r"[\s._:#-]*"


def _b64decode_urlsafe(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _b64decode_standard(value: str) -> bytes:
    return base64.b64decode(value)


def _header(headers: Iterable[dict[str, Any]], name: str) -> str | None:
    wanted = name.lower()
    for header in headers:
        if str(header.get("name", "")).lower() == wanted:
            value = header.get("value")
            return value if isinstance(value, str) else None
    return None


def _gmail_received_at(message: dict[str, Any]) -> datetime | None:
    internal_date = message.get("internalDate")
    if internal_date is not None:
        try:
            return datetime.fromtimestamp(int(str(internal_date)) / 1000, UTC)
        except (TypeError, ValueError, OSError):
            pass
    headers = ((message.get("payload") or {}).get("headers") or [])
    date_header = _header(headers, "Date")
    if not date_header:
        return None
    try:
        parsed = parsedate_to_datetime(date_header)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def normalize_invoice_match_patterns(patterns: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for raw_pattern in patterns:
        pattern = raw_pattern.strip()
        if not pattern:
            continue
        if len(pattern) > MAX_INVOICE_MATCH_PATTERN_LENGTH:
            raise MailIntegrationError("Invoice match regex is too long.")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise MailIntegrationError(f"Invalid invoice match regex {pattern!r}: {exc}") from exc
        normalized.append(pattern)
    if len(normalized) > MAX_INVOICE_MATCH_PATTERNS:
        raise MailIntegrationError(f"Use {MAX_INVOICE_MATCH_PATTERNS} or fewer invoice match regexes.")
    return normalized


def compile_invoice_match_regexes(patterns: Iterable[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern, re.IGNORECASE) for pattern in normalize_invoice_match_patterns(patterns))


def suggest_invoice_match_pattern_from_filename(filename: str) -> str:
    sample = filename.strip().replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not sample:
        raise MailIntegrationError("Filename is required.")
    if len(sample) > MAX_INVOICE_SAMPLE_FILENAME_LENGTH:
        raise MailIntegrationError("Filename is too long.")

    stem, dot, extension = sample.rpartition(".")
    if not stem or "/" in extension or "\\" in extension:
        stem = sample
        extension = ""
        dot = ""

    tokens = re.findall(r"[A-Za-z]+|\d+|[^A-Za-z\d]+", stem)
    parts: list[str] = []
    for token in tokens:
        if token.isalpha():
            parts.append(re.escape(token))
        elif token.isdigit():
            parts.append(r"\d+")
        elif parts and parts[-1] != INVOICE_FILENAME_SEPARATOR_PATTERN:
            parts.append(INVOICE_FILENAME_SEPARATOR_PATTERN)

    while parts and parts[-1] == INVOICE_FILENAME_SEPARATOR_PATTERN:
        parts.pop()
    if not parts:
        parts = [re.escape(stem)]

    extension_pattern = ""
    if dot and extension:
        escaped_extension = re.escape(extension)
        if extension.lower() == "pdf":
            extension_pattern = rf"(?:\.{escaped_extension})?"
        else:
            extension_pattern = rf"\.{escaped_extension}"

    pattern = "^" + "".join(parts) + extension_pattern + "$"
    normalize_invoice_match_patterns([pattern])
    return pattern


def is_invoice_candidate(
    *,
    subject: str | None,
    sender: str | None = None,
    filename: str | None = None,
    body: str | None = None,
    snippet: str | None = None,
    invoice_match_regexes: tuple[re.Pattern[str], ...],
) -> bool:
    for value in [filename, subject, body, snippet]:
        if not value:
            continue
        if any(pattern.search(value) for pattern in invoice_match_regexes):
            return True
    return False


def is_pdf_attachment(*, filename: str | None, mime_type: str | None) -> bool:
    return (mime_type or "").lower() == "application/pdf" or (filename or "").lower().endswith(".pdf")


def candidate_reason(
    *,
    subject: str | None,
    sender: str | None,
    filename: str | None,
    body: str | None = None,
    snippet: str | None = None,
    invoice_match_regexes: tuple[re.Pattern[str], ...],
) -> str:
    if is_invoice_candidate(
        subject=subject,
        sender=sender,
        filename=filename,
        body=body,
        snippet=snippet,
        invoice_match_regexes=invoice_match_regexes,
    ):
        return "invoice_hint"
    return "pdf_attachment"


def gmail_pdf_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(part: dict[str, Any]) -> None:
        filename = part.get("filename")
        mime_type = part.get("mimeType")
        body = part.get("body") if isinstance(part.get("body"), dict) else {}
        if is_pdf_attachment(
            filename=filename if isinstance(filename, str) else None,
            mime_type=mime_type if isinstance(mime_type, str) else None,
        ):
            found.append(part)
        for child in part.get("parts") or []:
            if isinstance(child, dict):
                walk(child)

    walk(payload)
    return found


def gmail_body_text(payload: dict[str, Any]) -> str:
    found: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        filename = part.get("filename")
        mime_type = part.get("mimeType")
        body = part.get("body") if isinstance(part.get("body"), dict) else {}
        raw_data = body.get("data")
        if (
            not filename
            and isinstance(mime_type, str)
            and mime_type.lower().startswith("text/")
            and isinstance(raw_data, str)
        ):
            try:
                found.append(_b64decode_urlsafe(raw_data).decode("utf-8", errors="replace"))
            except (binascii.Error, ValueError):
                pass
        for child in part.get("parts") or []:
            if isinstance(child, dict):
                walk(child)

    walk(payload)
    return " ".join(found)


def _outlook_email_address(value: dict[str, Any] | None) -> str | None:
    email_address = (value or {}).get("emailAddress")
    address = (email_address or {}).get("address") if isinstance(email_address, dict) else None
    return address if isinstance(address, str) else None


class TokenManager:
    def __init__(
        self,
        *,
        repo: MailRepository,
        cipher: TokenCipher,
        gmail: GmailClient,
        outlook: OutlookClient,
    ) -> None:
        self.repo = repo
        self.cipher = cipher
        self.gmail = gmail
        self.outlook = outlook

    def access_token_for(self, account_id: int) -> str:
        return self.repo.refresh_account_token(
            account_id=account_id,
            decrypt=self.cipher.decrypt,
            encrypt=self.cipher.encrypt,
            refresh=self._refresh,
        )

    def _refresh(self, provider: str, refresh_token: str) -> dict[str, Any]:
        if provider == "gmail":
            return self.gmail.refresh_access_token(refresh_token)
        if provider == "outlook":
            return self.outlook.refresh_access_token(refresh_token)
        raise MailIntegrationError(f"Unsupported mail provider: {provider}")


class MailIngestionService:
    def __init__(
        self,
        *,
        repo: MailRepository,
        storage: PdfStorage,
        token_manager: TokenManager,
        gmail: GmailClient,
        outlook: OutlookClient,
    ) -> None:
        self.repo = repo
        self.storage = storage
        self.token_manager = token_manager
        self.gmail = gmail
        self.outlook = outlook

    def process_gmail_history(self, *, account_id: int, notification_history_id: str | None = None) -> None:
        account = self._active_account(account_id, "gmail")
        start_history_id = account.get("gmail_history_id")
        if not start_history_id:
            self.repo.enqueue_job(
                job_type="gmail_fallback_sync",
                payload={"account_id": account_id},
                unique_key=f"gmail-fallback:{account_id}:missing-history",
            )
            if notification_history_id:
                self.repo.update_gmail_history(account_id=account_id, history_id=notification_history_id)
            return

        access_token = self.token_manager.access_token_for(account_id)
        page_token = None
        latest_history_id = notification_history_id
        message_ids: set[str] = set()

        while True:
            try:
                response = self.gmail.history_list(
                    access_token=access_token,
                    start_history_id=str(start_history_id),
                    page_token=page_token,
                )
            except HttpClientError as exc:
                if exc.status_code == 404:
                    self.repo.enqueue_job(
                        job_type="gmail_fallback_sync",
                        payload={"account_id": account_id},
                        unique_key=f"gmail-fallback:{account_id}:stale:{start_history_id}",
                    )
                    return
                raise

            for history in response.get("history") or []:
                if not isinstance(history, dict):
                    continue
                for added in history.get("messagesAdded") or []:
                    message = added.get("message") if isinstance(added, dict) else None
                    message_id = (message or {}).get("id") if isinstance(message, dict) else None
                    if isinstance(message_id, str):
                        message_ids.add(message_id)
                for message in history.get("messages") or []:
                    message_id = message.get("id") if isinstance(message, dict) else None
                    if isinstance(message_id, str):
                        message_ids.add(message_id)

            latest = response.get("historyId")
            if isinstance(latest, str):
                latest_history_id = latest
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        for message_id in sorted(message_ids):
            self.repo.enqueue_job(
                job_type="gmail_message_fetch",
                payload={"account_id": account_id, "message_id": message_id},
                unique_key=f"gmail-message:{account_id}:{message_id}",
            )

        if latest_history_id:
            self.repo.update_gmail_history(account_id=account_id, history_id=str(latest_history_id))

    def process_gmail_fallback(self, *, account_id: int) -> None:
        self._active_account(account_id, "gmail")
        access_token = self.token_manager.access_token_for(account_id)
        page_token = None
        while True:
            response = self.gmail.list_messages(
                access_token=access_token,
                query=GMAIL_PDF_FALLBACK_QUERY,
                page_token=page_token,
            )
            for message in response.get("messages") or []:
                if not isinstance(message, dict):
                    continue
                message_id = message.get("id")
                if isinstance(message_id, str):
                    self.repo.enqueue_job(
                        job_type="gmail_message_fetch",
                        payload={"account_id": account_id, "message_id": message_id},
                        unique_key=f"gmail-message:{account_id}:{message_id}",
                    )
            page_token = response.get("nextPageToken")
            if not page_token:
                break

    def process_gmail_message(self, *, account_id: int, message_id: str) -> None:
        account = self._active_account(account_id, "gmail")
        invoice_match_regexes = self._invoice_match_regexes_for_account(account)
        access_token = self.token_manager.access_token_for(account_id)
        message = self.gmail.message(access_token=access_token, message_id=message_id)
        payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
        headers = payload.get("headers") or []
        subject = _header(headers, "Subject") if isinstance(headers, list) else None
        sender = _header(headers, "From") if isinstance(headers, list) else None
        snippet = message.get("snippet") if isinstance(message.get("snippet"), str) else None
        body_text = gmail_body_text(payload)
        pdf_parts = gmail_pdf_parts(payload)
        matching_pdf_parts = [
            part
            for part in pdf_parts
            if not invoice_match_regexes
            or is_invoice_candidate(
                subject=subject,
                sender=sender,
                filename=part.get("filename") if isinstance(part.get("filename"), str) else None,
                body=body_text,
                snippet=snippet,
                invoice_match_regexes=invoice_match_regexes,
            )
        ]
        if not matching_pdf_parts:
            return

        message_row = self.repo.upsert_message(
            account_id=account_id,
            provider="gmail",
            provider_message_id=str(message.get("id") or message_id),
            thread_id=message.get("threadId") if isinstance(message.get("threadId"), str) else None,
            conversation_id=None,
            sender=sender,
            subject=subject,
            received_at=_gmail_received_at(message),
            has_attachments=bool(pdf_parts),
            metadata={
                "labelIds": message.get("labelIds") or [],
                "snippet": snippet,
                "historyId": message.get("historyId"),
                "source_email": account.get("email"),
            },
        )

        for part in matching_pdf_parts:
            body = part.get("body") if isinstance(part.get("body"), dict) else {}
            attachment_id = body.get("attachmentId")
            raw_data = body.get("data")
            if isinstance(attachment_id, str):
                attachment_response = self.gmail.attachment(
                    access_token=access_token,
                    message_id=message_id,
                    attachment_id=attachment_id,
                )
                raw_data = attachment_response.get("data")
            if not isinstance(raw_data, str):
                continue
            content = _b64decode_urlsafe(raw_data)
            filename = str(part.get("filename") or f"{message_id}.pdf")
            stored = self.storage.save_pdf(content)
            pdf_row = self.repo.upsert_pdf_file(stored)
            provider_attachment_key = attachment_id or f"inline:{filename}:{stored.sha256}"
            attachment_row = self.repo.upsert_attachment(
                account_id=account_id,
                message_id=int(message_row["id"]),
                provider_attachment_id=provider_attachment_key,
                filename=filename,
                mime_type=part.get("mimeType") if isinstance(part.get("mimeType"), str) else None,
                inline=False,
                pdf_file_id=int(pdf_row["id"]),
                candidate_reason=candidate_reason(
                    subject=subject,
                    sender=sender,
                    filename=filename,
                    body=body_text,
                    snippet=snippet,
                    invoice_match_regexes=invoice_match_regexes,
                ),
                metadata={
                    "provider_message_id": message_id,
                    "body_size": body.get("size"),
                },
            )
            self.repo.enqueue_job(
                job_type="parse_pdf",
                payload={
                    "attachment_id": int(attachment_row["id"]),
                    "pdf_file_id": int(pdf_row["id"]),
                    "storage_path": stored.relative_path,
                    "account_id": account_id,
                    "owner_user_id": account.get("owner_user_id"),
                },
                unique_key=f"parse-pdf:{attachment_row['id']}",
            )

    def process_outlook_message(self, *, account_id: int, message_id: str) -> None:
        account = self._active_account(account_id, "outlook")
        invoice_match_regexes = self._invoice_match_regexes_for_account(account)
        access_token = self.token_manager.access_token_for(account_id)
        message = self.outlook.message(access_token=access_token, message_id=message_id)
        subject = message.get("subject") if isinstance(message.get("subject"), str) else None
        sender = _outlook_email_address(message.get("from") if isinstance(message.get("from"), dict) else None)
        body_preview = message.get("bodyPreview") if isinstance(message.get("bodyPreview"), str) else None
        received_at = parse_rfc3339(message.get("receivedDateTime"))

        if not message.get("hasAttachments"):
            return

        matching_attachments: list[dict[str, Any]] = []
        for attachment in self.outlook.attachments(access_token=access_token, message_id=message_id):
            odata_type = attachment.get("@odata.type")
            filename = attachment.get("name")
            mime_type = attachment.get("contentType")
            is_inline = bool(attachment.get("isInline"))
            if odata_type and odata_type != "#microsoft.graph.fileAttachment":
                continue
            if is_inline:
                continue
            if not is_pdf_attachment(
                filename=filename if isinstance(filename, str) else None,
                mime_type=mime_type if isinstance(mime_type, str) else None,
            ):
                continue
            if invoice_match_regexes and not is_invoice_candidate(
                subject=subject,
                sender=sender,
                filename=filename if isinstance(filename, str) else None,
                body=body_preview,
                invoice_match_regexes=invoice_match_regexes,
            ):
                continue
            matching_attachments.append(attachment)

        if not matching_attachments:
            return

        message_row = self.repo.upsert_message(
            account_id=account_id,
            provider="outlook",
            provider_message_id=str(message.get("id") or message_id),
            thread_id=None,
            conversation_id=message.get("conversationId") if isinstance(message.get("conversationId"), str) else None,
            sender=sender,
            subject=subject,
            received_at=received_at,
            has_attachments=bool(message.get("hasAttachments")),
            metadata={
                "internetMessageId": message.get("internetMessageId"),
                "source_email": account.get("email"),
            },
        )

        for attachment in matching_attachments:
            filename = attachment.get("name")
            mime_type = attachment.get("contentType")
            is_inline = bool(attachment.get("isInline"))
            raw_data = attachment.get("contentBytes")
            if not isinstance(raw_data, str):
                continue
            content = _b64decode_standard(raw_data)
            stored = self.storage.save_pdf(content)
            pdf_row = self.repo.upsert_pdf_file(stored)
            provider_attachment_key = (
                attachment.get("id")
                if isinstance(attachment.get("id"), str)
                else f"file:{filename or message_id}:{stored.sha256}"
            )
            attachment_row = self.repo.upsert_attachment(
                account_id=account_id,
                message_id=int(message_row["id"]),
                provider_attachment_id=provider_attachment_key,
                filename=str(filename or f"{message_id}.pdf"),
                mime_type=mime_type if isinstance(mime_type, str) else None,
                inline=is_inline,
                pdf_file_id=int(pdf_row["id"]),
                candidate_reason=candidate_reason(
                    subject=subject,
                    sender=sender,
                    filename=filename if isinstance(filename, str) else None,
                    body=body_preview,
                    invoice_match_regexes=invoice_match_regexes,
                ),
                metadata={"provider_message_id": message_id, "size": attachment.get("size")},
            )
            self.repo.enqueue_job(
                job_type="parse_pdf",
                payload={
                    "attachment_id": int(attachment_row["id"]),
                    "pdf_file_id": int(pdf_row["id"]),
                    "storage_path": stored.relative_path,
                    "account_id": account_id,
                    "owner_user_id": account.get("owner_user_id"),
                },
                unique_key=f"parse-pdf:{attachment_row['id']}",
            )

    def process_outlook_delta(self, *, account_id: int) -> None:
        self._active_account(account_id, "outlook")
        access_token = self.token_manager.access_token_for(account_id)
        delta_link = account.get("outlook_delta_link") if isinstance(account.get("outlook_delta_link"), str) else None

        while True:
            response = self.outlook.delta_messages(access_token=access_token, delta_link=delta_link)
            for message in response.get("value") or []:
                if not isinstance(message, dict) or "@removed" in message:
                    continue
                message_id = message.get("id")
                if isinstance(message_id, str) and message.get("hasAttachments"):
                    self.repo.enqueue_job(
                        job_type="outlook_message_fetch",
                        payload={"account_id": account_id, "message_id": message_id},
                        unique_key=f"outlook-message:{account_id}:{message_id}",
                    )

            next_link = response.get("@odata.nextLink")
            if isinstance(next_link, str):
                delta_link = next_link
                continue
            final_delta = response.get("@odata.deltaLink")
            if isinstance(final_delta, str):
                self.repo.update_outlook_delta(account_id=account_id, delta_link=final_delta)
            break

    def renew_mail_subscriptions(self) -> None:
        for account in self.repo.list_accounts_due_for_renewal():
            account_id = int(account["id"])
            provider = account.get("provider")
            if provider == "gmail":
                access_token = self.token_manager.access_token_for(account_id)
                watch = self.gmail.watch(access_token=access_token)
                self.repo.update_gmail_watch(
                    account_id=account_id,
                    history_id=str(watch.get("historyId")) if watch.get("historyId") is not None else None,
                    expiration=gmail_expiration_ms(watch.get("expiration")),
                )
            elif provider == "outlook" and account.get("outlook_subscription_id"):
                access_token = self.token_manager.access_token_for(account_id)
                subscription = self.outlook.renew_subscription(
                    access_token=access_token,
                    subscription_id=str(account["outlook_subscription_id"]),
                )
                self.repo.update_outlook_subscription(
                    account_id=account_id,
                    subscription_id=str(subscription.get("id") or account["outlook_subscription_id"]),
                    expiration=parse_rfc3339(subscription.get("expirationDateTime")),
                )

    def _active_account(self, account_id: int, provider: str) -> dict[str, Any]:
        account = self.repo.get_account(account_id)
        if not account:
            raise MailIntegrationError(f"Mail account {account_id} was not found.")
        if account.get("provider") != provider:
            raise MailIntegrationError(f"Mail account {account_id} is not a {provider} account.")
        if account.get("status") != "active":
            raise MailIntegrationError(f"Mail account {account_id} is not active.")
        return account

    def _invoice_match_regexes_for_account(self, account: dict[str, Any]) -> tuple[re.Pattern[str], ...]:
        owner_user_id = account.get("owner_user_id")
        if not isinstance(owner_user_id, str) or not owner_user_id:
            return ()
        return compile_invoice_match_regexes(
            self.repo.get_invoice_match_patterns(owner_user_id=owner_user_id)
        )
