from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from app.mail_store import MailRepository, PdfStorage, SCHEMA_SQL, TokenCipher


class FakeResult:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


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

        raise AssertionError(f"Unexpected SQL: {sql}")


class FakeMailDatabase:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []
        self.dedupe_keys: dict[str, dict[str, Any]] = {}
        self.next_job_id = 1

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


class MailSchemaSqlTests(unittest.TestCase):
    def test_schema_backfills_durable_dedupe_keys_and_prunes_completed_jobs(self) -> None:
        self.assertIn("CREATE TABLE IF NOT EXISTS ingestion_job_dedupe_keys", SCHEMA_SQL)
        self.assertIn(
            "AND type IN ('gmail_message_fetch', 'outlook_message_fetch', 'parse_pdf')",
            SCHEMA_SQL,
        )
        self.assertIn("DELETE FROM ingestion_jobs\nWHERE status = 'completed'", SCHEMA_SQL)
