from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

from cryptography.fernet import Fernet

from .config import AppConfig, ConfigError


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

CREATE INDEX IF NOT EXISTS idx_mail_accounts_owner ON mail_accounts(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_mail_accounts_provider_email ON mail_accounts(provider, lower(email));
CREATE UNIQUE INDEX IF NOT EXISTS idx_mail_accounts_owner_provider_email_unique
    ON mail_accounts(owner_user_id, provider, lower(email));
CREATE INDEX IF NOT EXISTS idx_mail_messages_account ON mail_messages(account_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_claim ON ingestion_jobs(status, available_at, id);
"""


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
    ) -> dict[str, Any]:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO mail_accounts (
                    owner_user_id, provider, email, encrypted_access_token, encrypted_refresh_token,
                    token_expires_at, scope, status, last_error, webhook_client_state
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', NULL, %s)
                ON CONFLICT (owner_user_id, provider, lower(email))
                DO UPDATE SET
                    encrypted_access_token = EXCLUDED.encrypted_access_token,
                    encrypted_refresh_token = EXCLUDED.encrypted_refresh_token,
                    token_expires_at = EXCLUDED.token_expires_at,
                    scope = EXCLUDED.scope,
                    status = 'active',
                    last_error = NULL,
                    webhook_client_state = COALESCE(EXCLUDED.webhook_client_state, mail_accounts.webhook_client_state),
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

    def get_account_by_provider_email(self, *, provider: str, email: str) -> dict[str, Any] | None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM mail_accounts WHERE provider = %s AND lower(email) = lower(%s)",
                (provider, email),
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
                ON CONFLICT (provider, account_id, event_key) DO NOTHING
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
        with self.database.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO ingestion_jobs (type, payload, unique_key, available_at)
                VALUES (%s, %s::jsonb, %s, COALESCE(%s, now()))
                ON CONFLICT (unique_key) DO NOTHING
                RETURNING id
                """,
                (
                    job_type,
                    json.dumps(payload, separators=(",", ":")),
                    unique_key,
                    available_at,
                ),
            ).fetchone()
            return row is not None

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
                "UPDATE ingestion_jobs SET status = 'completed', updated_at = now() WHERE id = %s",
                (job_id,),
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
