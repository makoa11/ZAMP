from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable

from cryptography.fernet import Fernet

from .config import AppConfig, ConfigError


REVIEW_QUEUE_DECISIONS = (
    "needs_review",
    "request_missing_info",
    "flag_possible_duplicate",
    "block_or_escalate",
    "apply_credit_or_route_review",
)


class MailIntegrationError(RuntimeError):
    """Raised when mail ingestion cannot continue with current configuration."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_mail_value(value: str | None, name: str) -> str:
    if not value:
        raise MailIntegrationError(f"{name} is required for mail ingestion.")
    return value


class TokenCipher:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode("utf-8"))

    @classmethod
    def from_config(cls, config: AppConfig) -> "TokenCipher":
        key = require_mail_value(config.mail_token_encryption_key, "MAIL_TOKEN_ENCRYPTION_KEY")
        return cls(key)

    def encrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")


@dataclass(frozen=True)
class StoredPdf:
    sha256: str
    byte_size: int
    relative_path: str


DURABLE_DEDUPE_JOB_TYPES = ("gmail_message_fetch", "outlook_message_fetch", "parse_pdf")
DURABLE_DEDUPE_JOB_TYPE_SET = frozenset(DURABLE_DEDUPE_JOB_TYPES)


def _uses_durable_dedupe(job_type: str) -> bool:
    return job_type in DURABLE_DEDUPE_JOB_TYPE_SET


class PdfStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @classmethod
    def from_config(cls, config: AppConfig) -> "PdfStorage":
        return cls(config.mail_pdf_storage_dir)

    def save_pdf(self, content: bytes) -> StoredPdf:
        digest = hashlib.sha256(content).hexdigest()
        relative_path = f"{digest}.pdf"
        destination = self.root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            return StoredPdf(digest, len(content), relative_path)

        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{digest}.",
            suffix=".tmp",
            dir=str(destination.parent),
        )
        try:
            with os.fdopen(fd, "wb") as tmp_file:
                tmp_file.write(content)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            try:
                os.link(tmp_name, destination)
                os.unlink(tmp_name)
            except FileExistsError:
                os.unlink(tmp_name)
            except OSError:
                os.replace(tmp_name, destination)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

        return StoredPdf(digest, len(content), relative_path)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS oauth_states (
    state TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    redirect_after TEXT,
    encrypted_code_verifier TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL
);

ALTER TABLE oauth_states ADD COLUMN IF NOT EXISTS encrypted_code_verifier TEXT;

CREATE TABLE IF NOT EXISTS mail_accounts (
    id BIGSERIAL PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    provider TEXT NOT NULL CHECK (provider IN ('gmail', 'outlook')),
    email TEXT NOT NULL,
    encrypted_access_token TEXT,
    encrypted_refresh_token TEXT NOT NULL,
    token_expires_at TIMESTAMPTZ,
    scope TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    last_error TEXT,
    gmail_history_id TEXT,
    gmail_watch_expiration TIMESTAMPTZ,
    outlook_subscription_id TEXT UNIQUE,
    outlook_subscription_expiration TIMESTAMPTZ,
    outlook_delta_link TEXT,
    webhook_client_state TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mail_invoice_match_settings (
    owner_user_id TEXT PRIMARY KEY,
    patterns JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mail_messages (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES mail_accounts(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_message_id TEXT NOT NULL,
    thread_id TEXT,
    conversation_id TEXT,
    sender TEXT,
    subject TEXT,
    received_at TIMESTAMPTZ,
    has_attachments BOOLEAN NOT NULL DEFAULT false,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(account_id, provider_message_id)
);

CREATE TABLE IF NOT EXISTS mail_pdf_files (
    id BIGSERIAL PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    byte_size BIGINT NOT NULL,
    storage_path TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mail_pdf_parse_results (
    pdf_file_id BIGINT PRIMARY KEY REFERENCES mail_pdf_files(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('parsed', 'needs_review', 'no_text_layer', 'unsupported', 'failed')),
    parser_version TEXT NOT NULL,
    result JSONB NOT NULL DEFAULT '{}'::jsonb,
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE mail_pdf_parse_results DROP CONSTRAINT IF EXISTS mail_pdf_parse_results_status_check;
ALTER TABLE mail_pdf_parse_results
ADD CONSTRAINT mail_pdf_parse_results_status_check
CHECK (status IN ('parsed', 'needs_review', 'no_text_layer', 'unsupported', 'failed'));

CREATE TABLE IF NOT EXISTS mail_attachments (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES mail_accounts(id) ON DELETE CASCADE,
    message_id BIGINT NOT NULL REFERENCES mail_messages(id) ON DELETE CASCADE,
    provider_attachment_id TEXT,
    filename TEXT NOT NULL,
    mime_type TEXT,
    inline BOOLEAN NOT NULL DEFAULT false,
    pdf_file_id BIGINT NOT NULL REFERENCES mail_pdf_files(id),
    candidate_reason TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(message_id, provider_attachment_id)
);

CREATE TABLE IF NOT EXISTS mail_invoice_extractions (
    owner_user_id TEXT NOT NULL,
    pdf_file_id BIGINT NOT NULL REFERENCES mail_pdf_files(id) ON DELETE CASCADE,
    attachment_id BIGINT REFERENCES mail_attachments(id) ON DELETE SET NULL,
    parse_status TEXT NOT NULL CHECK (parse_status IN ('parsed', 'needs_review', 'no_text_layer', 'unsupported', 'failed')),
    parser_method TEXT NOT NULL DEFAULT 'static_text',
    confidence NUMERIC(5,3),
    vendor_name TEXT,
    normalized_vendor TEXT,
    invoice_number TEXT,
    normalized_invoice_number TEXT,
    purchase_order TEXT,
    normalized_purchase_order TEXT,
    issue_date DATE,
    amount_due NUMERIC(12,2),
    currency TEXT,
    normalized_invoice JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_user_id, pdf_file_id)
);

ALTER TABLE mail_invoice_extractions DROP CONSTRAINT IF EXISTS mail_invoice_extractions_parse_status_check;
ALTER TABLE mail_invoice_extractions
ADD CONSTRAINT mail_invoice_extractions_parse_status_check
CHECK (parse_status IN ('parsed', 'needs_review', 'no_text_layer', 'unsupported', 'failed'));

CREATE TABLE IF NOT EXISTS mail_invoice_decisions (
    owner_user_id TEXT NOT NULL,
    pdf_file_id BIGINT NOT NULL REFERENCES mail_pdf_files(id) ON DELETE CASCADE,
    decision TEXT NOT NULL,
    confidence TEXT NOT NULL,
    summary TEXT NOT NULL,
    next_action TEXT NOT NULL,
    checks JSONB NOT NULL DEFAULT '[]'::jsonb,
    audit JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_user_id, pdf_file_id)
);

CREATE TABLE IF NOT EXISTS ap_context_records (
    id BIGSERIAL PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    source_key TEXT,
    vendor_name TEXT NOT NULL,
    normalized_vendor TEXT NOT NULL,
    purchase_order TEXT,
    normalized_purchase_order TEXT,
    invoice_number TEXT,
    normalized_invoice_number TEXT,
    invoice_total NUMERIC(12,2),
    issue_date DATE,
    scenario TEXT,
    context JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(owner_user_id, source_key)
);

CREATE TABLE IF NOT EXISTS webhook_events (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    account_id BIGINT REFERENCES mail_accounts(id) ON DELETE SET NULL,
    event_key TEXT NOT NULL,
    payload JSONB NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ,
    UNIQUE(provider, account_id, event_key)
);

DELETE FROM webhook_events AS duplicate
USING webhook_events AS keeper
WHERE duplicate.account_id IS NULL
  AND keeper.account_id IS NULL
  AND duplicate.provider = keeper.provider
  AND duplicate.event_key = keeper.event_key
  AND duplicate.id > keeper.id;

CREATE UNIQUE INDEX IF NOT EXISTS idx_webhook_events_unknown_unique
    ON webhook_events(provider, event_key)
    WHERE account_id IS NULL;

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id BIGSERIAL PRIMARY KEY,
    type TEXT NOT NULL,
    payload JSONB NOT NULL,
    unique_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_at TIMESTAMPTZ,
    locked_by TEXT,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingestion_job_dedupe_keys (
    unique_key TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO ingestion_job_dedupe_keys (unique_key, type, first_seen_at, completed_at, updated_at)
SELECT unique_key, type, MIN(created_at), MAX(updated_at), MAX(updated_at)
FROM ingestion_jobs
WHERE status = 'completed'
  AND type IN ('gmail_message_fetch', 'outlook_message_fetch', 'parse_pdf')
GROUP BY unique_key, type
ON CONFLICT (unique_key) DO UPDATE SET
    type = EXCLUDED.type,
    first_seen_at = LEAST(ingestion_job_dedupe_keys.first_seen_at, EXCLUDED.first_seen_at),
    completed_at = COALESCE(ingestion_job_dedupe_keys.completed_at, EXCLUDED.completed_at),
    updated_at = GREATEST(ingestion_job_dedupe_keys.updated_at, EXCLUDED.updated_at);

DELETE FROM ingestion_jobs
WHERE status = 'completed';

CREATE INDEX IF NOT EXISTS idx_mail_accounts_owner ON mail_accounts(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_mail_accounts_provider_email ON mail_accounts(provider, lower(email));
CREATE UNIQUE INDEX IF NOT EXISTS idx_mail_accounts_owner_provider_email_unique
    ON mail_accounts(owner_user_id, provider, lower(email));
CREATE INDEX IF NOT EXISTS idx_mail_messages_account ON mail_messages(account_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_claim ON ingestion_jobs(status, available_at, id);
CREATE INDEX IF NOT EXISTS idx_mail_invoice_extractions_owner_updated
    ON mail_invoice_extractions(owner_user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_mail_invoice_extractions_owner_decision
    ON mail_invoice_extractions(owner_user_id, parse_status, normalized_vendor);
CREATE INDEX IF NOT EXISTS idx_mail_invoice_decisions_owner_decision
    ON mail_invoice_decisions(owner_user_id, decision);
CREATE INDEX IF NOT EXISTS idx_ap_context_records_owner_vendor_po
    ON ap_context_records(owner_user_id, normalized_vendor, normalized_purchase_order);
CREATE INDEX IF NOT EXISTS idx_ap_context_records_owner_vendor_invoice
    ON ap_context_records(owner_user_id, normalized_vendor, normalized_invoice_number);
CREATE INDEX IF NOT EXISTS idx_ap_context_records_owner_vendor_amount_date
    ON ap_context_records(owner_user_id, normalized_vendor, invoice_total, issue_date);
CREATE INDEX IF NOT EXISTS idx_ap_context_records_owner_po
    ON ap_context_records(owner_user_id, normalized_purchase_order);
CREATE INDEX IF NOT EXISTS idx_ap_context_records_owner_invoice
    ON ap_context_records(owner_user_id, normalized_invoice_number);
CREATE INDEX IF NOT EXISTS idx_ap_context_records_owner_amount_date
    ON ap_context_records(owner_user_id, invoice_total, issue_date);
"""


def _json_from_db(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value


def _best_ap_context_candidate(rows: Iterable[Any], normalized_vendor: str | None) -> dict[str, Any] | None:
    candidates = [dict(row) for row in rows]
    if not candidates:
        return None
    vendor = str(normalized_vendor or "")
    if not vendor:
        candidates[0]["_vendor_similarity"] = 0.0
        return candidates[0]
    scored = [
        (
            SequenceMatcher(None, vendor, str(candidate.get("normalized_vendor") or "")).ratio(),
            -index,
            candidate,
        )
        for index, candidate in enumerate(candidates)
    ]
    similarity, _, result = max(scored, key=lambda item: (item[0], item[1]))
    result["_vendor_similarity"] = round(similarity, 3)
    return result


def _ap_match_strategy(
    record: dict[str, Any],
    normalized_vendor: str | None,
    *,
    exact: str,
    fuzzy: str,
    fallback: str,
) -> str:
    if normalized_vendor and record.get("normalized_vendor") == normalized_vendor:
        return exact
    if float(record.get("_vendor_similarity") or 0.0) >= 0.85:
        return fuzzy
    return fallback


def _decimal_from_value(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception:
        return None


def _date_from_value(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _nested_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class MailDatabase:
    def __init__(self, database_url: str, *, min_size: int = 1, max_size: int = 10) -> None:
        self.database_url = database_url
        self.min_size = min_size
        self.max_size = max_size
        self._pool: Any | None = None
        self._pool_lock = threading.Lock()

    @classmethod
    def from_config(cls, config: AppConfig) -> "MailDatabase":
        return cls(
            require_mail_value(config.database_url, "DATABASE_URL"),
            min_size=config.mail_db_pool_min_size,
            max_size=config.mail_db_pool_max_size,
        )

    def connect(self) -> Any:
        return self._connection_pool().connection()

    def _connection_pool(self) -> Any:
        if self._pool is not None:
            return self._pool
        with self._pool_lock:
            if self._pool is not None:
                return self._pool
            try:
                from psycopg.rows import dict_row
                from psycopg_pool import ConnectionPool
            except ImportError as exc:
                raise ConfigError("Install psycopg pooling with: pip install 'psycopg[binary,pool]'") from exc
            self._pool = ConnectionPool(
                self.database_url,
                min_size=self.min_size,
                max_size=self.max_size,
                kwargs={"row_factory": dict_row},
                open=True,
            )
            return self._pool

    def close(self) -> None:
        with self._pool_lock:
            if self._pool is not None:
                self._pool.close()
                self._pool = None

    def initialize_schema(self) -> None:
        with self.connect() as conn:
            for statement in SCHEMA_SQL.split(";"):
                if statement.strip():
                    conn.execute(statement)


class MailRepository:
    def __init__(self, database: MailDatabase) -> None:
        self.database = database

    def initialize_schema(self) -> None:
        self.database.initialize_schema()

    def create_oauth_state(
        self,
        *,
        provider: str,
        owner_user_id: str,
        redirect_after: str | None,
        encrypted_code_verifier: str | None = None,
        ttl_seconds: int = 600,
    ) -> str:
        state = secrets.token_urlsafe(32)
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO oauth_states (
                    state, provider, owner_user_id, redirect_after, encrypted_code_verifier, expires_at
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    state,
                    provider,
                    owner_user_id,
                    redirect_after,
                    encrypted_code_verifier,
                    utc_now() + timedelta(seconds=ttl_seconds),
                ),
            )
        return state

    def consume_oauth_state(
        self,
        *,
        provider: str,
        state: str,
        owner_user_id: str,
    ) -> dict[str, Any] | None:
        with self.database.connect() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    SELECT * FROM oauth_states
                    WHERE state = %s
                      AND provider = %s
                      AND owner_user_id = %s
                      AND consumed_at IS NULL
                      AND expires_at > now()
                    FOR UPDATE
                    """,
                    (state, provider, owner_user_id),
                ).fetchone()
                if not row:
                    return None
                conn.execute(
                    "UPDATE oauth_states SET consumed_at = now() WHERE state = %s",
                    (state,),
                )
                return dict(row)

    def upsert_account(
        self,
        *,
        owner_user_id: str,
        provider: str,
        email: str,
        encrypted_access_token: str | None,
        encrypted_refresh_token: str,
        token_expires_at: datetime | None,
        scope: str | None,
        webhook_client_state: str | None = None,
        gmail_history_id: str | None = None,
        gmail_watch_expiration: datetime | None = None,
        outlook_subscription_id: str | None = None,
        outlook_subscription_expiration: datetime | None = None,
    ) -> dict[str, Any]:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO mail_accounts (
                    owner_user_id, provider, email, encrypted_access_token, encrypted_refresh_token,
                    token_expires_at, scope, status, last_error, webhook_client_state,
                    gmail_history_id, gmail_watch_expiration,
                    outlook_subscription_id, outlook_subscription_expiration
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', NULL, %s, %s, %s, %s, %s)
                ON CONFLICT (owner_user_id, provider, lower(email))
                DO UPDATE SET
                    encrypted_access_token = EXCLUDED.encrypted_access_token,
                    encrypted_refresh_token = EXCLUDED.encrypted_refresh_token,
                    token_expires_at = EXCLUDED.token_expires_at,
                    scope = EXCLUDED.scope,
                    status = 'active',
                    last_error = NULL,
                    webhook_client_state = COALESCE(EXCLUDED.webhook_client_state, mail_accounts.webhook_client_state),
                    gmail_history_id = COALESCE(EXCLUDED.gmail_history_id, mail_accounts.gmail_history_id),
                    gmail_watch_expiration = COALESCE(
                        EXCLUDED.gmail_watch_expiration,
                        mail_accounts.gmail_watch_expiration
                    ),
                    outlook_subscription_id = COALESCE(
                        EXCLUDED.outlook_subscription_id,
                        mail_accounts.outlook_subscription_id
                    ),
                    outlook_subscription_expiration = COALESCE(
                        EXCLUDED.outlook_subscription_expiration,
                        mail_accounts.outlook_subscription_expiration
                    ),
                    updated_at = now()
                RETURNING *
                """,
                (
                    owner_user_id,
                    provider,
                    email.lower(),
                    encrypted_access_token,
                    encrypted_refresh_token,
                    token_expires_at,
                    scope,
                    webhook_client_state,
                    gmail_history_id,
                    gmail_watch_expiration,
                    outlook_subscription_id,
                    outlook_subscription_expiration,
                ),
            ).fetchone()
            return dict(row)

    def list_accounts(self, *, owner_user_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, provider, email, status, last_error, gmail_watch_expiration,
                       outlook_subscription_expiration, created_at, updated_at
                FROM mail_accounts
                WHERE owner_user_id = %s
                ORDER BY created_at DESC
                """,
                (owner_user_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_invoice_match_patterns(self, *, owner_user_id: str) -> list[str]:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT patterns FROM mail_invoice_match_settings WHERE owner_user_id = %s",
                (owner_user_id,),
            ).fetchone()
            if not row:
                return []
            patterns = row["patterns"]
            if isinstance(patterns, str):
                try:
                    patterns = json.loads(patterns)
                except json.JSONDecodeError:
                    return []
            if not isinstance(patterns, list):
                return []
            return [item for item in patterns if isinstance(item, str)]

    def set_invoice_match_patterns(self, *, owner_user_id: str, patterns: list[str]) -> list[str]:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO mail_invoice_match_settings (owner_user_id, patterns)
                VALUES (%s, %s::jsonb)
                ON CONFLICT (owner_user_id)
                DO UPDATE SET patterns = EXCLUDED.patterns, updated_at = now()
                RETURNING patterns
                """,
                (owner_user_id, json.dumps(patterns, separators=(",", ":"))),
            ).fetchone()
            stored = row["patterns"]
            if isinstance(stored, str):
                stored = json.loads(stored)
            return [item for item in stored if isinstance(item, str)]

    def list_active_accounts(self) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM mail_accounts
                WHERE status = 'active'
                ORDER BY id
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def list_accounts_due_for_renewal(self) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM mail_accounts
                WHERE status = 'active'
                  AND (
                    (
                      provider = 'gmail'
                      AND (gmail_watch_expiration IS NULL OR gmail_watch_expiration <= now() + interval '1 day')
                    )
                    OR
                    (
                      provider = 'outlook'
                      AND (
                        outlook_subscription_expiration IS NULL
                        OR outlook_subscription_expiration <= now() + interval '1 day'
                      )
                    )
                  )
                ORDER BY id
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_account(self, account_id: int) -> dict[str, Any] | None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM mail_accounts WHERE id = %s",
                (account_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_account_by_provider_email(
        self,
        *,
        owner_user_id: str,
        provider: str,
        email: str,
    ) -> dict[str, Any] | None:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM mail_accounts
                WHERE owner_user_id = %s
                  AND provider = %s
                  AND lower(email) = lower(%s)
                """,
                (owner_user_id, provider, email),
            ).fetchone()
            return dict(row) if row else None

    def list_accounts_by_provider_email(self, *, provider: str, email: str) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM mail_accounts
                WHERE provider = %s AND lower(email) = lower(%s)
                ORDER BY id
                """,
                (provider, email),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_account_by_outlook_subscription(self, subscription_id: str) -> dict[str, Any] | None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM mail_accounts WHERE outlook_subscription_id = %s",
                (subscription_id,),
            ).fetchone()
            return dict(row) if row else None

    def disconnect_account(self, *, account_id: int, owner_user_id: str) -> bool:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                UPDATE mail_accounts
                SET status = 'disconnected', updated_at = now()
                WHERE id = %s AND owner_user_id = %s
                RETURNING id
                """,
                (account_id, owner_user_id),
            ).fetchone()
            return row is not None

    def refresh_account_token(
        self,
        *,
        account_id: int,
        decrypt: Callable[[str | None], str | None],
        encrypt: Callable[[str | None], str | None],
        refresh: Callable[[str, str], dict[str, Any]],
    ) -> str:
        with self.database.connect() as conn:
            with conn.transaction():
                account = conn.execute(
                    "SELECT * FROM mail_accounts WHERE id = %s FOR UPDATE",
                    (account_id,),
                ).fetchone()
                if not account:
                    raise MailIntegrationError(f"Mail account {account_id} was not found.")
                if account["status"] != "active":
                    raise MailIntegrationError(f"Mail account {account_id} is not active.")

                expires_at = account["token_expires_at"]
                access_token = decrypt(account["encrypted_access_token"])
                if access_token and expires_at and expires_at > utc_now() + timedelta(minutes=5):
                    return access_token

                refresh_token = decrypt(account["encrypted_refresh_token"])
                if not refresh_token:
                    raise MailIntegrationError("Refresh token is missing.")

                try:
                    token_response = refresh(str(account["provider"]), refresh_token)
                except Exception as exc:
                    error_text = str(exc)
                    if "invalid_grant" in error_text:
                        conn.execute(
                            """
                            UPDATE mail_accounts
                            SET status = 'reauthorization_required', last_error = %s, updated_at = now()
                            WHERE id = %s
                            """,
                            (error_text[:1000], account_id),
                        )
                    raise

                new_access_token = require_mail_value(token_response.get("access_token"), "provider access_token")
                rotated_refresh_token = token_response.get("refresh_token") or refresh_token
                expires_in = int(token_response.get("expires_in") or 3600)
                conn.execute(
                    """
                    UPDATE mail_accounts
                    SET encrypted_access_token = %s,
                        encrypted_refresh_token = %s,
                        token_expires_at = %s,
                        status = 'active',
                        last_error = NULL,
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (
                        encrypt(new_access_token),
                        encrypt(str(rotated_refresh_token)),
                        utc_now() + timedelta(seconds=max(60, expires_in)),
                        account_id,
                    ),
                )
                return new_access_token

    def update_gmail_watch(self, *, account_id: int, history_id: str | None, expiration: datetime | None) -> None:
        with self.database.connect() as conn:
            conn.execute(
                """
                UPDATE mail_accounts
                SET gmail_history_id = COALESCE(%s, gmail_history_id),
                    gmail_watch_expiration = %s,
                    last_error = NULL,
                    updated_at = now()
                WHERE id = %s
                """,
                (history_id, expiration, account_id),
            )

    def update_gmail_history(self, *, account_id: int, history_id: str) -> None:
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE mail_accounts SET gmail_history_id = %s, updated_at = now() WHERE id = %s",
                (history_id, account_id),
            )

    def update_outlook_subscription(
        self,
        *,
        account_id: int,
        subscription_id: str,
        expiration: datetime | None,
    ) -> None:
        with self.database.connect() as conn:
            conn.execute(
                """
                UPDATE mail_accounts
                SET outlook_subscription_id = %s,
                    outlook_subscription_expiration = %s,
                    last_error = NULL,
                    updated_at = now()
                WHERE id = %s
                """,
                (subscription_id, expiration, account_id),
            )

    def update_outlook_delta(self, *, account_id: int, delta_link: str) -> None:
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE mail_accounts SET outlook_delta_link = %s, updated_at = now() WHERE id = %s",
                (delta_link, account_id),
            )

    def mark_account_error(self, *, account_id: int, status: str, error: str) -> None:
        with self.database.connect() as conn:
            conn.execute(
                """
                UPDATE mail_accounts
                SET status = %s, last_error = %s, updated_at = now()
                WHERE id = %s
                """,
                (status, error[:1000], account_id),
            )

    def record_active_account_error(self, *, account_id: int, error: str) -> None:
        with self.database.connect() as conn:
            conn.execute(
                """
                UPDATE mail_accounts
                SET last_error = %s, updated_at = now()
                WHERE id = %s AND status = 'active'
                """,
                (error[:1000], account_id),
            )

    def insert_webhook_event(
        self,
        *,
        provider: str,
        event_key: str,
        account_id: int | None,
        payload: dict[str, Any],
    ) -> bool:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO webhook_events (provider, event_key, account_id, payload)
                VALUES (%s, %s, %s, %s::jsonb)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                (provider, event_key, account_id, json.dumps(payload, separators=(",", ":"))),
            ).fetchone()
            return row is not None

    def enqueue_job(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        unique_key: str,
        available_at: datetime | None = None,
    ) -> bool:
        payload_json = json.dumps(payload, separators=(",", ":"))
        with self.database.connect() as conn:
            with conn.transaction():
                if _uses_durable_dedupe(job_type):
                    dedupe_row = conn.execute(
                        """
                        INSERT INTO ingestion_job_dedupe_keys (unique_key, type)
                        VALUES (%s, %s)
                        ON CONFLICT (unique_key) DO NOTHING
                        RETURNING unique_key
                        """,
                        (unique_key, job_type),
                    ).fetchone()
                    if dedupe_row is None:
                        return False

                row = conn.execute(
                    """
                    INSERT INTO ingestion_jobs (type, payload, unique_key, available_at)
                    VALUES (%s, %s::jsonb, %s, COALESCE(%s, now()))
                    ON CONFLICT (unique_key) DO NOTHING
                    RETURNING id
                    """,
                    (
                        job_type,
                        payload_json,
                        unique_key,
                        available_at,
                    ),
                ).fetchone()
                return row is not None

    def enqueue_stale_pdf_parse_jobs(
        self,
        *,
        parser_revision: str,
        limit: int = 100,
    ) -> int:
        """Queue stored PDFs whose last parse used an older parser/config revision."""
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                WITH stale AS (
                    SELECT DISTINCT ON (attachments.id)
                        attachments.id AS attachment_id,
                        attachments.account_id,
                        attachments.pdf_file_id,
                        accounts.owner_user_id,
                        pdf_files.storage_path
                    FROM mail_attachments AS attachments
                    JOIN mail_accounts AS accounts ON accounts.id = attachments.account_id
                    JOIN mail_pdf_files AS pdf_files ON pdf_files.id = attachments.pdf_file_id
                    LEFT JOIN mail_pdf_parse_results AS parse_results
                        ON parse_results.pdf_file_id = attachments.pdf_file_id
                    WHERE parse_results.parser_version IS DISTINCT FROM %s
                      AND NOT EXISTS (
                          SELECT 1
                          FROM ingestion_jobs AS pending
                          WHERE pending.type = 'parse_pdf'
                            AND (pending.payload->>'pdf_file_id')::bigint = attachments.pdf_file_id
                            AND pending.status IN ('pending', 'running', 'retry')
                      )
                    ORDER BY attachments.id, attachments.created_at ASC
                    LIMIT %s
                )
                INSERT INTO ingestion_jobs (type, payload, unique_key, available_at)
                SELECT
                    'parse_pdf',
                    jsonb_build_object(
                        'attachment_id', stale.attachment_id,
                        'pdf_file_id', stale.pdf_file_id,
                        'storage_path', stale.storage_path,
                        'account_id', stale.account_id,
                        'owner_user_id', stale.owner_user_id,
                        'parser_version', %s
                    ),
                    'parse-pdf:' || stale.attachment_id || ':' || %s,
                    now()
                FROM stale
                ON CONFLICT (unique_key) DO NOTHING
                RETURNING id
                """,
                (parser_revision, limit, parser_revision, parser_revision),
            ).fetchall()
            return len(rows)

    def claim_jobs(self, *, worker_id: str, job_types: Iterable[str], limit: int) -> list[dict[str, Any]]:
        job_types = list(job_types)
        if not job_types:
            return []
        with self.database.connect() as conn:
            with conn.transaction():
                rows = conn.execute(
                    """
                    UPDATE ingestion_jobs
                    SET status = 'running',
                        locked_at = now(),
                        locked_by = %s,
                        attempts = attempts + 1,
                        updated_at = now()
                    WHERE id IN (
                        SELECT id
                        FROM ingestion_jobs
                        WHERE status IN ('pending', 'retry')
                          AND available_at <= now()
                          AND type = ANY(%s)
                        ORDER BY available_at ASC, id ASC
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING *
                    """,
                    (worker_id, job_types, limit),
                ).fetchall()
                return [dict(row) for row in rows]

    def complete_job(self, *, job_id: int) -> None:
        with self.database.connect() as conn:
            conn.execute(
                """
                WITH completed AS (
                    DELETE FROM ingestion_jobs
                    WHERE id = %s
                    RETURNING type, unique_key, created_at
                )
                INSERT INTO ingestion_job_dedupe_keys (
                    unique_key, type, first_seen_at, completed_at, updated_at
                )
                SELECT unique_key, type, created_at, now(), now()
                FROM completed
                WHERE type = ANY(%s)
                ON CONFLICT (unique_key) DO UPDATE SET
                    type = EXCLUDED.type,
                    completed_at = COALESCE(
                        ingestion_job_dedupe_keys.completed_at,
                        EXCLUDED.completed_at
                    ),
                    updated_at = now()
                """,
                (job_id, list(DURABLE_DEDUPE_JOB_TYPES)),
            )

    def retry_job(self, *, job_id: int, attempts: int, error: str) -> None:
        delay_seconds = min(3600, 30 * (2 ** max(0, attempts - 1)))
        jitter = secrets.randbelow(30)
        with self.database.connect() as conn:
            conn.execute(
                """
                UPDATE ingestion_jobs
                SET status = CASE WHEN attempts >= 8 THEN 'failed' ELSE 'retry' END,
                    available_at = now() + (%s || ' seconds')::interval,
                    last_error = %s,
                    locked_at = NULL,
                    locked_by = NULL,
                    updated_at = now()
                WHERE id = %s
                """,
                (delay_seconds + jitter, error[:1000], job_id),
            )

    def upsert_message(
        self,
        *,
        account_id: int,
        provider: str,
        provider_message_id: str,
        thread_id: str | None,
        conversation_id: str | None,
        sender: str | None,
        subject: str | None,
        received_at: datetime | None,
        has_attachments: bool,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO mail_messages (
                    account_id, provider, provider_message_id, thread_id, conversation_id,
                    sender, subject, received_at, has_attachments, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (account_id, provider_message_id)
                DO UPDATE SET
                    thread_id = EXCLUDED.thread_id,
                    conversation_id = EXCLUDED.conversation_id,
                    sender = EXCLUDED.sender,
                    subject = EXCLUDED.subject,
                    received_at = EXCLUDED.received_at,
                    has_attachments = EXCLUDED.has_attachments,
                    metadata = EXCLUDED.metadata,
                    updated_at = now()
                RETURNING *
                """,
                (
                    account_id,
                    provider,
                    provider_message_id,
                    thread_id,
                    conversation_id,
                    sender,
                    subject,
                    received_at,
                    has_attachments,
                    json.dumps(metadata, separators=(",", ":")),
                ),
            ).fetchone()
            return dict(row)

    def upsert_pdf_file(self, stored_pdf: StoredPdf) -> dict[str, Any]:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO mail_pdf_files (sha256, byte_size, storage_path)
                VALUES (%s, %s, %s)
                ON CONFLICT (sha256)
                DO UPDATE SET byte_size = EXCLUDED.byte_size
                RETURNING *
                """,
                (stored_pdf.sha256, stored_pdf.byte_size, stored_pdf.relative_path),
            ).fetchone()
            return dict(row)

    def upsert_pdf_parse_result(
        self,
        *,
        pdf_file_id: int,
        status: str,
        parser_version: str,
        result: dict[str, Any],
        warnings: list[str],
    ) -> dict[str, Any]:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO mail_pdf_parse_results (
                    pdf_file_id, status, parser_version, result, warnings
                )
                VALUES (%s, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT (pdf_file_id)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    parser_version = EXCLUDED.parser_version,
                    result = EXCLUDED.result,
                    warnings = EXCLUDED.warnings,
                    updated_at = now()
                RETURNING *
                """,
                (
                    pdf_file_id,
                    status,
                    parser_version,
                    json.dumps(result, separators=(",", ":")),
                    json.dumps(warnings, separators=(",", ":")),
                ),
            ).fetchone()
            return dict(row)

    def upsert_mail_invoice_extraction(
        self,
        *,
        owner_user_id: str,
        pdf_file_id: int,
        attachment_id: int | None,
        normalized_invoice: dict[str, Any],
        parse_status: str,
        parser_method: str = "static_text",
    ) -> dict[str, Any]:
        vendor = _nested_dict(normalized_invoice.get("vendor"))
        invoice_number = _nested_dict(normalized_invoice.get("invoice_number"))
        purchase_order = _nested_dict(normalized_invoice.get("purchase_order"))
        issue_date = _date_from_value(_nested_dict(normalized_invoice.get("issue_date")).get("value"))
        amount_due = _nested_dict(normalized_invoice.get("amount_due"))
        confidence = _nested_dict(normalized_invoice.get("confidence")).get("score")
        confidence_value = None
        try:
            confidence_value = round(float(confidence), 3)
        except (TypeError, ValueError):
            pass

        with self.database.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO mail_invoice_extractions (
                    owner_user_id, pdf_file_id, attachment_id, parse_status, parser_method,
                    confidence, vendor_name, normalized_vendor, invoice_number,
                    normalized_invoice_number, purchase_order, normalized_purchase_order,
                    issue_date, amount_due, currency, normalized_invoice
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb
                )
                ON CONFLICT (owner_user_id, pdf_file_id)
                DO UPDATE SET
                    attachment_id = EXCLUDED.attachment_id,
                    parse_status = EXCLUDED.parse_status,
                    parser_method = EXCLUDED.parser_method,
                    confidence = EXCLUDED.confidence,
                    vendor_name = EXCLUDED.vendor_name,
                    normalized_vendor = EXCLUDED.normalized_vendor,
                    invoice_number = EXCLUDED.invoice_number,
                    normalized_invoice_number = EXCLUDED.normalized_invoice_number,
                    purchase_order = EXCLUDED.purchase_order,
                    normalized_purchase_order = EXCLUDED.normalized_purchase_order,
                    issue_date = EXCLUDED.issue_date,
                    amount_due = EXCLUDED.amount_due,
                    currency = EXCLUDED.currency,
                    normalized_invoice = EXCLUDED.normalized_invoice,
                    updated_at = now()
                RETURNING *
                """,
                (
                    owner_user_id,
                    pdf_file_id,
                    attachment_id,
                    parse_status,
                    parser_method,
                    confidence_value,
                    vendor.get("name"),
                    vendor.get("normalized_name"),
                    invoice_number.get("value"),
                    invoice_number.get("normalized"),
                    purchase_order.get("value"),
                    purchase_order.get("normalized"),
                    issue_date,
                    _decimal_from_value(amount_due.get("amount")),
                    amount_due.get("currency")
                    or _nested_dict(normalized_invoice.get("currency")).get("value"),
                    json.dumps(normalized_invoice, separators=(",", ":")),
                ),
            ).fetchone()
            return dict(row)

    def upsert_mail_invoice_decision(
        self,
        *,
        owner_user_id: str,
        pdf_file_id: int,
        decision_result: dict[str, Any],
    ) -> dict[str, Any]:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO mail_invoice_decisions (
                    owner_user_id, pdf_file_id, decision, confidence, summary,
                    next_action, checks, audit
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT (owner_user_id, pdf_file_id)
                DO UPDATE SET
                    decision = EXCLUDED.decision,
                    confidence = EXCLUDED.confidence,
                    summary = EXCLUDED.summary,
                    next_action = EXCLUDED.next_action,
                    checks = EXCLUDED.checks,
                    audit = EXCLUDED.audit,
                    updated_at = now()
                RETURNING *
                """,
                (
                    owner_user_id,
                    pdf_file_id,
                    str(decision_result.get("decision") or "needs_review"),
                    str(decision_result.get("confidence") or "low"),
                    str(decision_result.get("summary") or ""),
                    str(decision_result.get("next_action") or ""),
                    json.dumps(decision_result.get("checks") or [], separators=(",", ":")),
                    json.dumps(decision_result.get("audit") or {}, separators=(",", ":")),
                ),
            ).fetchone()
            return dict(row)

    def upsert_ap_context_record(
        self,
        *,
        owner_user_id: str,
        source_key: str | None,
        vendor_name: str,
        normalized_vendor: str,
        purchase_order: str | None,
        normalized_purchase_order: str | None,
        invoice_number: str | None,
        normalized_invoice_number: str | None,
        invoice_total: Decimal | str | float | int | None,
        issue_date: date | datetime | str | None,
        scenario: str | None,
        context: dict[str, Any],
        source_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO ap_context_records (
                    owner_user_id, source_key, vendor_name, normalized_vendor,
                    purchase_order, normalized_purchase_order, invoice_number,
                    normalized_invoice_number, invoice_total, issue_date, scenario,
                    context, source_metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT (owner_user_id, source_key)
                DO UPDATE SET
                    vendor_name = EXCLUDED.vendor_name,
                    normalized_vendor = EXCLUDED.normalized_vendor,
                    purchase_order = EXCLUDED.purchase_order,
                    normalized_purchase_order = EXCLUDED.normalized_purchase_order,
                    invoice_number = EXCLUDED.invoice_number,
                    normalized_invoice_number = EXCLUDED.normalized_invoice_number,
                    invoice_total = EXCLUDED.invoice_total,
                    issue_date = EXCLUDED.issue_date,
                    scenario = EXCLUDED.scenario,
                    context = EXCLUDED.context,
                    source_metadata = EXCLUDED.source_metadata,
                    updated_at = now()
                RETURNING *
                """,
                (
                    owner_user_id,
                    source_key,
                    vendor_name,
                    normalized_vendor,
                    purchase_order,
                    normalized_purchase_order,
                    invoice_number,
                    normalized_invoice_number,
                    _decimal_from_value(invoice_total),
                    _date_from_value(issue_date),
                    scenario,
                    json.dumps(context, separators=(",", ":")),
                    json.dumps(source_metadata or {}, separators=(",", ":")),
                ),
            ).fetchone()
            return dict(row)

    def find_ap_context_record(
        self,
        *,
        owner_user_id: str,
        normalized_vendor: str | None,
        normalized_purchase_order: str | None,
        normalized_invoice_number: str | None,
        amount_due: Decimal | str | float | int | None,
        issue_date: date | datetime | str | None,
    ) -> dict[str, Any] | None:
        with self.database.connect() as conn:
            if normalized_purchase_order:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM ap_context_records
                    WHERE owner_user_id = %s
                      AND normalized_purchase_order = %s
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 20
                    """,
                    (owner_user_id, normalized_purchase_order),
                ).fetchall()
                result = _best_ap_context_candidate(rows, normalized_vendor)
                if result:
                    result["_match_strategy"] = _ap_match_strategy(
                        result,
                        normalized_vendor,
                        exact="vendor_po",
                        fuzzy="fuzzy_vendor_po",
                        fallback="po",
                    )
                    return self._hydrate_ap_context_record(result)

            if normalized_invoice_number:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM ap_context_records
                    WHERE owner_user_id = %s
                      AND normalized_invoice_number = %s
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 20
                    """,
                    (owner_user_id, normalized_invoice_number),
                ).fetchall()
                result = _best_ap_context_candidate(rows, normalized_vendor)
                if result:
                    result["_match_strategy"] = _ap_match_strategy(
                        result,
                        normalized_vendor,
                        exact="vendor_invoice_number",
                        fuzzy="fuzzy_vendor_invoice_number",
                        fallback="invoice_number",
                    )
                    return self._hydrate_ap_context_record(result)

            amount = _decimal_from_value(amount_due)
            parsed_date = _date_from_value(issue_date)
            if amount is not None:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM ap_context_records
                    WHERE owner_user_id = %s
                      AND invoice_total = %s
                      AND (%s::date IS NULL OR issue_date = %s::date OR issue_date IS NULL)
                    ORDER BY
                        CASE WHEN issue_date = %s::date THEN 0 ELSE 1 END,
                        updated_at DESC,
                        id DESC
                    LIMIT 20
                    """,
                    (
                        owner_user_id,
                        amount,
                        parsed_date,
                        parsed_date,
                        parsed_date,
                    ),
                ).fetchall()
                result = _best_ap_context_candidate(rows, normalized_vendor)
                if result:
                    result["_match_strategy"] = _ap_match_strategy(
                        result,
                        normalized_vendor,
                        exact="vendor_amount_date",
                        fuzzy="fuzzy_vendor_amount_date",
                        fallback="amount_date",
                    )
                    return self._hydrate_ap_context_record(result)
        return None

    def list_mail_invoices(
        self,
        *,
        owner_user_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    extraction.pdf_file_id,
                    extraction.attachment_id,
                    extraction.vendor_name AS vendor,
                    extraction.invoice_number,
                    extraction.purchase_order,
                    extraction.amount_due::text AS amount_due,
                    extraction.currency,
                    extraction.parse_status,
                    extraction.parser_method,
                    extraction.confidence::float AS parse_confidence,
                    decision.decision,
                    decision.confidence AS decision_confidence,
                    decision.next_action,
                    message.received_at,
                    attachment.filename,
                    message.sender,
                    message.subject
                FROM mail_invoice_extractions extraction
                LEFT JOIN mail_invoice_decisions decision
                    ON decision.owner_user_id = extraction.owner_user_id
                   AND decision.pdf_file_id = extraction.pdf_file_id
                LEFT JOIN mail_attachments attachment
                    ON attachment.id = extraction.attachment_id
                LEFT JOIN mail_messages message
                    ON message.id = attachment.message_id
                WHERE extraction.owner_user_id = %s
                ORDER BY COALESCE(message.received_at, extraction.updated_at) DESC,
                         extraction.updated_at DESC
                LIMIT %s OFFSET %s
                """,
                (owner_user_id, max(1, min(limit, 500)), max(0, offset)),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_mail_invoice_detail(
        self,
        *,
        owner_user_id: str,
        pdf_file_id: int,
    ) -> dict[str, Any] | None:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    extraction.*,
                    pdf.sha256,
                    pdf.byte_size,
                    pdf.storage_path,
                    parse.status AS raw_parse_status,
                    parse.parser_version AS raw_parser_version,
                    parse.result AS raw_parse_result,
                    parse.warnings AS raw_parse_warnings,
                    decision.decision,
                    decision.confidence AS decision_confidence,
                    decision.summary AS decision_summary,
                    decision.next_action AS decision_next_action,
                    decision.checks AS decision_checks,
                    decision.audit AS decision_audit,
                    attachment.filename,
                    attachment.mime_type,
                    attachment.candidate_reason,
                    attachment.metadata AS attachment_metadata,
                    message.provider,
                    message.provider_message_id,
                    message.thread_id,
                    message.conversation_id,
                    message.sender,
                    message.subject,
                    message.received_at,
                    message.metadata AS message_metadata
                FROM mail_invoice_extractions extraction
                JOIN mail_pdf_files pdf
                    ON pdf.id = extraction.pdf_file_id
                LEFT JOIN mail_pdf_parse_results parse
                    ON parse.pdf_file_id = extraction.pdf_file_id
                LEFT JOIN mail_invoice_decisions decision
                    ON decision.owner_user_id = extraction.owner_user_id
                   AND decision.pdf_file_id = extraction.pdf_file_id
                LEFT JOIN mail_attachments attachment
                    ON attachment.id = extraction.attachment_id
                LEFT JOIN mail_messages message
                    ON message.id = attachment.message_id
                WHERE extraction.owner_user_id = %s
                  AND extraction.pdf_file_id = %s
                """,
                (owner_user_id, pdf_file_id),
            ).fetchone()
            if not row:
                return None
            detail = dict(row)
            for key, fallback in {
                "normalized_invoice": {},
                "raw_parse_result": {},
                "raw_parse_warnings": [],
                "decision_checks": [],
                "decision_audit": {},
                "attachment_metadata": {},
                "message_metadata": {},
            }.items():
                detail[key] = _json_from_db(detail.get(key), fallback)
            return detail

    def get_mail_invoice_overlay_source(
        self,
        *,
        owner_user_id: str,
        pdf_file_id: int,
    ) -> dict[str, Any] | None:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT pdf.storage_path, parse.result AS raw_parse_result
                FROM mail_invoice_extractions extraction
                JOIN mail_pdf_files pdf
                    ON pdf.id = extraction.pdf_file_id
                JOIN mail_pdf_parse_results parse
                    ON parse.pdf_file_id = extraction.pdf_file_id
                WHERE extraction.owner_user_id = %s
                  AND extraction.pdf_file_id = %s
                """,
                (owner_user_id, pdf_file_id),
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            result["raw_parse_result"] = _json_from_db(result.get("raw_parse_result"), {})
            return result

    def _hydrate_ap_context_record(self, row: dict[str, Any]) -> dict[str, Any]:
        row["context"] = _json_from_db(row.get("context"), {})
        row["source_metadata"] = _json_from_db(row.get("source_metadata"), {})
        return row

    def upsert_attachment(
        self,
        *,
        account_id: int,
        message_id: int,
        provider_attachment_id: str | None,
        filename: str,
        mime_type: str | None,
        inline: bool,
        pdf_file_id: int,
        candidate_reason: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO mail_attachments (
                    account_id, message_id, provider_attachment_id, filename, mime_type,
                    inline, pdf_file_id, candidate_reason, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (message_id, provider_attachment_id)
                DO UPDATE SET
                    filename = EXCLUDED.filename,
                    mime_type = EXCLUDED.mime_type,
                    inline = EXCLUDED.inline,
                    pdf_file_id = EXCLUDED.pdf_file_id,
                    candidate_reason = EXCLUDED.candidate_reason,
                    metadata = EXCLUDED.metadata
                RETURNING *
                """,
                (
                    account_id,
                    message_id,
                    provider_attachment_id,
                    filename,
                    mime_type,
                    inline,
                    pdf_file_id,
                    candidate_reason,
                    json.dumps(metadata, separators=(",", ":")),
                ),
            ).fetchone()
            return dict(row)

    def list_invoice_review_items(self, *, owner_user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    extraction.attachment_id,
                    attachment.filename,
                    attachment.candidate_reason,
                    attachment.created_at AS attachment_created_at,
                    extraction.pdf_file_id,
                    pdf.sha256,
                    pdf.byte_size,
                    pdf.storage_path,
                    message.id AS message_id,
                    message.sender,
                    message.subject,
                    message.received_at,
                    message.provider,
                    account.email AS account_email,
                    extraction.parse_status,
                    raw_parse.result AS parse_result,
                    raw_parse.warnings AS parse_warnings,
                    raw_parse.updated_at AS parsed_at,
                    extraction.vendor_name AS vendor,
                    extraction.invoice_number,
                    extraction.amount_due::text AS amount_due,
                    extraction.currency,
                    decision.decision,
                    decision.confidence AS decision_confidence,
                    decision.next_action
                FROM mail_invoice_extractions extraction
                JOIN mail_pdf_files pdf
                    ON pdf.id = extraction.pdf_file_id
                LEFT JOIN mail_attachments attachment
                    ON attachment.id = extraction.attachment_id
                LEFT JOIN mail_messages message
                    ON message.id = attachment.message_id
                LEFT JOIN mail_accounts account
                    ON account.id = attachment.account_id
                LEFT JOIN mail_pdf_parse_results raw_parse
                    ON raw_parse.pdf_file_id = extraction.pdf_file_id
                LEFT JOIN mail_invoice_decisions decision
                    ON decision.owner_user_id = extraction.owner_user_id
                   AND decision.pdf_file_id = extraction.pdf_file_id
                WHERE extraction.owner_user_id = %s
                  AND (
                      decision.decision IS NULL
                      OR decision.decision = ANY(%s)
                  )
                ORDER BY
                    COALESCE(message.received_at, extraction.updated_at) DESC,
                    extraction.updated_at DESC,
                    extraction.pdf_file_id DESC
                LIMIT %s
                """,
                (owner_user_id, list(REVIEW_QUEUE_DECISIONS), limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_pdf_file_for_owner(self, *, owner_user_id: str, pdf_file_id: int) -> dict[str, Any] | None:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    pdf_files.id AS pdf_file_id,
                    pdf_files.sha256,
                    pdf_files.byte_size,
                    pdf_files.storage_path,
                    attachments.filename
                FROM mail_pdf_files AS pdf_files
                JOIN mail_attachments AS attachments ON attachments.pdf_file_id = pdf_files.id
                JOIN mail_accounts AS accounts ON accounts.id = attachments.account_id
                WHERE accounts.owner_user_id = %s
                  AND pdf_files.id = %s
                ORDER BY attachments.created_at DESC, attachments.id DESC
                LIMIT 1
                """,
                (owner_user_id, pdf_file_id),
            ).fetchone()
            return dict(row) if row else None
