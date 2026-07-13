from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from app.mail_store import (
    MailRepository,
    PdfStorage,
    REVIEW_QUEUE_DECISIONS,
    SCHEMA_SQL,
    TokenCipher,
)


class FakeResult:
    def __init__(
        self,
        row: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.row = row
        self.rows = rows or []

    def fetchone(self) -> dict[str, Any] | None:
        return self.row

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class FakeTransaction:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc_info: object) -> None:
        return None


class FakeMailConnection:
    def __init__(self, database: FakeMailDatabase) -> None:
        self.database = database

    def __enter__(self) -> "FakeMailConnection":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> FakeResult:
        if "WITH stale AS" in sql and "parser_version IS DISTINCT FROM" in sql:
            self.database.last_sql = sql
            self.database.last_params = params
            return FakeResult(rows=[{"id": index + 1} for index in range(self.database.stale_enqueue_count)])

        if "WITH eligible AS" in sql and "parse-pdf-ai:" in sql:
            self.database.last_sql = sql
            self.database.last_params = params
            return FakeResult(
                rows=[{"id": index + 1} for index in range(self.database.ai_enqueue_count)]
            )

        if "INSERT INTO ingestion_job_dedupe_keys (unique_key, type)" in sql:
            unique_key, job_type = str(params[0]), str(params[1])
            if unique_key in self.database.dedupe_keys:
                return FakeResult()
            self.database.dedupe_keys[unique_key] = {
                "unique_key": unique_key,
                "type": job_type,
                "completed_at": None,
            }
            return FakeResult({"unique_key": unique_key})

        if "INSERT INTO ingestion_jobs (type, payload, unique_key, available_at)" in sql:
            job_type, payload, unique_key, available_at = params
            if any(job["unique_key"] == unique_key for job in self.database.jobs):
                return FakeResult()
            job = {
                "id": self.database.next_job_id,
                "type": job_type,
                "payload": payload,
                "unique_key": unique_key,
                "available_at": available_at,
                "status": "pending",
            }
            self.database.next_job_id += 1
            self.database.jobs.append(job)
            return FakeResult({"id": job["id"]})

        if "WITH completed AS" in sql and "DELETE FROM ingestion_jobs" in sql:
            job_id = int(params[0])
            durable_types = set(params[1])
            for index, job in enumerate(self.database.jobs):
                if job["id"] != job_id:
                    continue
                completed = self.database.jobs.pop(index)
                if completed["type"] in durable_types:
                    unique_key = str(completed["unique_key"])
                    self.database.dedupe_keys.setdefault(
                        unique_key,
                        {
                            "unique_key": unique_key,
                            "type": completed["type"],
                            "completed_at": None,
                        },
                    )
                    self.database.dedupe_keys[unique_key]["completed_at"] = "completed"
                return FakeResult()
            return FakeResult()

        if "FROM mail_invoice_extractions extraction" in sql:
            self.database.last_sql = sql
            self.database.last_params = params
            return FakeResult(rows=self.database.review_rows)

        if "FROM mail_pdf_files AS pdf_files" in sql:
            self.database.last_sql = sql
            self.database.last_params = params
            return FakeResult(row=self.database.pdf_row)

        if "FROM mail_accounts" in sql and "owner_user_id = %s" in sql:
            self.database.last_sql = sql
            self.database.last_params = params
            return FakeResult(row={"id": 1, "owner_user_id": params[0]})

        raise AssertionError(f"Unexpected SQL: {sql}")


class FakeMailDatabase:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []
        self.dedupe_keys: dict[str, dict[str, Any]] = {}
        self.next_job_id = 1
        self.review_rows: list[dict[str, Any]] = []
        self.pdf_row: dict[str, Any] | None = None
        self.last_sql = ""
        self.last_params: tuple[object, ...] = ()
        self.stale_enqueue_count = 0
        self.ai_enqueue_count = 0

    def connect(self) -> FakeMailConnection:
        return FakeMailConnection(self)


class TokenCipherTests(unittest.TestCase):
    def test_token_cipher_round_trips_tokens(self) -> None:
        cipher = TokenCipher(Fernet.generate_key().decode("ascii"))

        encrypted = cipher.encrypt("refresh-token")

        self.assertIsInstance(encrypted, str)
        self.assertNotEqual(encrypted, "refresh-token")
        self.assertEqual(cipher.decrypt(encrypted), "refresh-token")


class PdfStorageTests(unittest.TestCase):
    def test_pdf_storage_writes_content_addressed_file(self) -> None:
        content = b"%PDF-1.4\ninvoice"
        digest = hashlib.sha256(content).hexdigest()

        with tempfile.TemporaryDirectory() as tmp:
            storage = PdfStorage(tmp)
            first = storage.save_pdf(content)
            second = storage.save_pdf(content)

            expected_relative_path = f"{digest}.pdf"
            self.assertEqual(first.sha256, digest)
            self.assertEqual(first.relative_path, expected_relative_path)
            self.assertEqual(second.relative_path, expected_relative_path)
            self.assertEqual(Path(tmp, expected_relative_path).read_bytes(), content)


class MailRepositoryJobQueueTests(unittest.TestCase):
    def test_stale_parser_revisions_are_reenqueued_with_versioned_keys(self) -> None:
        database = FakeMailDatabase()
        database.stale_enqueue_count = 2
        repo = MailRepository(database)  # type: ignore[arg-type]

        count = repo.enqueue_stale_pdf_parse_jobs(
            parser_revision="static-pdf-v3:ocr-pages=3",
            limit=25,
        )

        self.assertEqual(count, 2)
        self.assertEqual(
            database.last_params,
            (
                "static-pdf-v3:ocr-pages=3",
                25,
                "static-pdf-v3:ocr-pages=3",
                "static-pdf-v3:ocr-pages=3",
            ),
        )
        self.assertIn("parse_results.parser_version IS DISTINCT FROM %s", database.last_sql)
        self.assertIn("'parser_version', %s::text", database.last_sql)
        self.assertIn("'parse-pdf:' || stale.attachment_id || ':' || %s::text", database.last_sql)

    def test_ai_opt_in_requeues_existing_owner_ocr_review_results(self) -> None:
        database = FakeMailDatabase()
        database.ai_enqueue_count = 3
        repo = MailRepository(database)  # type: ignore[arg-type]

        count = repo.enqueue_owner_ai_fallback_jobs(
            owner_user_id="user-123",
            reprocess_key="enable-1",
            limit=25,
        )

        self.assertEqual(count, 3)
        self.assertEqual(database.last_params, ("user-123", 25, "enable-1", "enable-1"))
        self.assertIn("parse_results.status = 'needs_review'", database.last_sql)
        self.assertIn("parse_results.result->>'ocr_used' = 'true'", database.last_sql)
        self.assertIn("'parse-pdf-ai:' || eligible.attachment_id", database.last_sql)

    def test_completed_durable_job_leaves_queue_and_blocks_reenqueue(self) -> None:
        database = FakeMailDatabase()
        repo = MailRepository(database)  # type: ignore[arg-type]

        created = repo.enqueue_job(
            job_type="gmail_message_fetch",
            payload={"account_id": 1, "message_id": "m1"},
            unique_key="gmail-message:1:m1",
        )
        self.assertTrue(created)

        repo.complete_job(job_id=1)

        self.assertEqual(database.jobs, [])
        self.assertEqual(database.dedupe_keys["gmail-message:1:m1"]["completed_at"], "completed")
        self.assertFalse(
            repo.enqueue_job(
                job_type="gmail_message_fetch",
                payload={"account_id": 1, "message_id": "m1"},
                unique_key="gmail-message:1:m1",
            )
        )
        self.assertEqual(database.jobs, [])

    def test_completed_periodic_job_leaves_queue_without_durable_dedupe(self) -> None:
        database = FakeMailDatabase()
        repo = MailRepository(database)  # type: ignore[arg-type]

        self.assertTrue(
            repo.enqueue_job(
                job_type="gmail_fallback_sync",
                payload={"account_id": 1},
                unique_key="gmail-fallback:1:202607091620",
            )
        )

        repo.complete_job(job_id=1)

        self.assertEqual(database.jobs, [])
        self.assertEqual(database.dedupe_keys, {})
        self.assertTrue(
            repo.enqueue_job(
                job_type="gmail_fallback_sync",
                payload={"account_id": 1},
                unique_key="gmail-fallback:1:202607091620",
            )
        )
        self.assertEqual(len(database.jobs), 1)


class MailRepositoryAccountTests(unittest.TestCase):
    def test_provider_email_lookup_is_scoped_to_owner(self) -> None:
        database = FakeMailDatabase()
        repo = MailRepository(database)  # type: ignore[arg-type]

        account = repo.get_account_by_provider_email(
            owner_user_id="user-123",
            provider="gmail",
            email="ap@example.com",
        )

        self.assertEqual(account, {"id": 1, "owner_user_id": "user-123"})
        self.assertIn("owner_user_id = %s", database.last_sql)
        self.assertEqual(database.last_params, ("user-123", "gmail", "ap@example.com"))


class MailRepositoryInvoiceReviewTests(unittest.TestCase):
    def test_list_invoice_review_items_reads_owner_decision_rows(self) -> None:
        database = FakeMailDatabase()
        database.review_rows = [
            {
                "attachment_id": 30,
                "filename": "invoice.pdf",
                "pdf_file_id": 20,
            }
        ]
        repo = MailRepository(database)  # type: ignore[arg-type]

        rows = repo.list_invoice_review_items(owner_user_id="user-123", limit=25)

        self.assertEqual(rows, database.review_rows)
        self.assertEqual(database.last_params, ("user-123", list(REVIEW_QUEUE_DECISIONS), 25))
        self.assertIn("FROM mail_invoice_extractions extraction", database.last_sql)
        self.assertIn("LEFT JOIN mail_invoice_decisions decision", database.last_sql)
        self.assertIn("decision.decision = ANY(%s)", database.last_sql)
        self.assertNotIn("LOWER(parse_results.status)", database.last_sql)
        self.assertNotIn("generate_invoice", database.last_sql)

    def test_get_pdf_file_for_owner_filters_by_owner_and_pdf_id(self) -> None:
        database = FakeMailDatabase()
        database.pdf_row = {
            "pdf_file_id": 20,
            "filename": "invoice.pdf",
            "storage_path": "stored.pdf",
        }
        repo = MailRepository(database)  # type: ignore[arg-type]

        row = repo.get_pdf_file_for_owner(owner_user_id="user-123", pdf_file_id=20)

        self.assertEqual(row, database.pdf_row)
        self.assertEqual(database.last_params, ("user-123", 20))
        self.assertIn("JOIN mail_attachments AS attachments", database.last_sql)
        self.assertIn("accounts.owner_user_id = %s", database.last_sql)


class MailSchemaSqlTests(unittest.TestCase):
    def test_schema_backfills_durable_dedupe_keys_and_prunes_completed_jobs(self) -> None:
        self.assertIn("CREATE TABLE IF NOT EXISTS ingestion_job_dedupe_keys", SCHEMA_SQL)
        self.assertIn(
            "AND type IN ('gmail_message_fetch', 'outlook_message_fetch', 'parse_pdf')",
            SCHEMA_SQL,
        )
        self.assertIn("DELETE FROM ingestion_jobs\nWHERE status = 'completed'", SCHEMA_SQL)

    def test_schema_dedupes_webhook_events_without_known_account(self) -> None:
        self.assertIn("idx_webhook_events_unknown_unique", SCHEMA_SQL)
        self.assertIn("WHERE account_id IS NULL", SCHEMA_SQL)
        self.assertIn("DELETE FROM webhook_events AS duplicate", SCHEMA_SQL)

    def test_schema_includes_invoice_extraction_decision_and_ap_context_tables(self) -> None:
        self.assertIn("CREATE TABLE IF NOT EXISTS mail_invoice_extractions", SCHEMA_SQL)
        self.assertIn("CREATE TABLE IF NOT EXISTS mail_invoice_decisions", SCHEMA_SQL)
        self.assertIn("CREATE TABLE IF NOT EXISTS ap_context_records", SCHEMA_SQL)
        self.assertIn("parser_method TEXT NOT NULL DEFAULT 'static_text'", SCHEMA_SQL)
        self.assertIn("idx_ap_context_records_owner_vendor_po", SCHEMA_SQL)
        self.assertIn("use_ai_extraction BOOLEAN NOT NULL DEFAULT false", SCHEMA_SQL)

    def test_parse_result_status_schema_allows_review_queue(self) -> None:
        self.assertIn("'needs_review'", SCHEMA_SQL)
        self.assertIn("DROP CONSTRAINT IF EXISTS mail_pdf_parse_results_status_check", SCHEMA_SQL)

    def test_invoice_extraction_parse_status_schema_allows_review_queue(self) -> None:
        self.assertIn("DROP CONSTRAINT IF EXISTS mail_invoice_extractions_parse_status_check", SCHEMA_SQL)
        self.assertIn(
            "parse_status IN ('parsed', 'needs_review', 'no_text_layer', 'unsupported', 'failed')",
            SCHEMA_SQL,
        )
