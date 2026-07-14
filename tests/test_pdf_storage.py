from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.mail_store import (
    PdfStorage,
    PdfStorageNotFoundError,
    PdfStoragePathError,
    PdfStorageUnavailableError,
    S3PdfStorage,
)


class FakeS3Error(RuntimeError):
    def __init__(self, code: str, message: str = "provider detail") -> None:
        super().__init__(message)
        self.response = {"Error": {"Code": code, "Message": message}}


def _s3_storage(client: MagicMock, *, prefix: str = "mail-pdfs/") -> S3PdfStorage:
    return S3PdfStorage(
        bucket="private-invoices",
        endpoint="https://storage.example.test",
        access_key_id="access-id",
        secret_access_key="secret-key",
        region="auto",
        addressing_style="virtual",
        prefix=prefix,
        client=client,
    )


class LocalPdfStorageTests(unittest.TestCase):
    def test_save_and_read_round_trip_is_content_addressed_and_idempotent(self) -> None:
        content = b"%PDF-1.4\nlocal invoice"
        digest = hashlib.sha256(content).hexdigest()

        with tempfile.TemporaryDirectory() as tmp:
            storage = PdfStorage(tmp)
            first = storage.save_pdf(content)
            second = storage.save_pdf(content)
            loaded = storage.read_pdf(first.relative_path)

            self.assertEqual(first.relative_path, f"{digest}.pdf")
            self.assertEqual(second, first)
            self.assertEqual(loaded, content)
            self.assertEqual(Path(tmp, first.relative_path).read_bytes(), content)

    def test_missing_local_pdf_has_explicit_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PdfStorageNotFoundError):
                PdfStorage(tmp).read_pdf("missing.pdf")

    def test_local_storage_rejects_absolute_and_traversal_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = PdfStorage(tmp)
            for storage_path in (
                "/tmp/invoice.pdf",
                "../invoice.pdf",
                "nested/../../invoice.pdf",
                r"C:\invoices\invoice.pdf",
                r"..\invoice.pdf",
                ".",
                "invoice\x00.pdf",
            ):
                with self.subTest(storage_path=storage_path):
                    with self.assertRaises(PdfStoragePathError):
                        storage.read_pdf(storage_path)


class S3PdfStorageTests(unittest.TestCase):
    def test_save_uploads_private_pdf_under_prefix(self) -> None:
        content = b"%PDF-1.4\ns3 invoice"
        digest = hashlib.sha256(content).hexdigest()
        client = MagicMock()
        storage = _s3_storage(client, prefix="production/mail-pdfs")

        stored = storage.save_pdf(content)

        self.assertEqual(stored.relative_path, f"{digest}.pdf")
        client.put_object.assert_called_once_with(
            Bucket="private-invoices",
            Key=f"production/mail-pdfs/{digest}.pdf",
            Body=content,
            ContentType="application/pdf",
        )

    def test_config_factory_builds_s3_backend_with_configured_client(self) -> None:
        client = MagicMock()
        config = SimpleNamespace(
            mail_storage_backend="s3",
            mail_pdf_storage_dir="unused",
            mail_s3_bucket="private-invoices",
            mail_s3_endpoint="https://storage.example.test",
            mail_s3_access_key_id="access-id",
            mail_s3_secret_access_key="secret-key",
            mail_s3_region="auto",
            mail_s3_addressing_style="virtual",
            mail_s3_prefix="production/",
        )

        with patch("app.mail_store._new_s3_client", return_value=client) as create_client:
            storage = PdfStorage.from_config(config)  # type: ignore[arg-type]

        self.assertIsInstance(storage, S3PdfStorage)
        self.assertEqual(storage.backend, "s3")
        self.assertEqual(storage.prefix, "production/")
        create_client.assert_called_once_with(
            endpoint="https://storage.example.test",
            access_key_id="access-id",
            secret_access_key="secret-key",
            region="auto",
            addressing_style="virtual",
        )

    def test_duplicate_save_replaces_or_repairs_same_logical_object(self) -> None:
        content = b"%PDF-1.4\nsame content"
        client = MagicMock()
        storage = _s3_storage(client)

        first = storage.save_pdf(content)
        second = storage.save_pdf(content)

        self.assertEqual(second, first)
        self.assertEqual(client.put_object.call_count, 2)
        self.assertEqual(
            client.put_object.call_args_list[0].kwargs["Key"],
            client.put_object.call_args_list[1].kwargs["Key"],
        )

    def test_read_downloads_pdf_bytes_from_prefixed_key(self) -> None:
        client = MagicMock()
        client.get_object.return_value = {"Body": io.BytesIO(b"%PDF-1.4\nfrom s3")}
        storage = _s3_storage(client)

        content = storage.read_pdf("abc123.pdf")

        self.assertEqual(content, b"%PDF-1.4\nfrom s3")
        client.get_object.assert_called_once_with(
            Bucket="private-invoices",
            Key="mail-pdfs/abc123.pdf",
        )

    def test_missing_s3_object_has_explicit_error(self) -> None:
        client = MagicMock()
        client.get_object.side_effect = FakeS3Error("NoSuchKey", "sensitive provider detail")

        with self.assertRaises(PdfStorageNotFoundError) as error:
            _s3_storage(client).read_pdf("missing.pdf")

        self.assertNotIn("sensitive", str(error.exception))

    def test_transient_s3_failures_are_sanitized(self) -> None:
        for operation in ("put", "get"):
            with self.subTest(operation=operation):
                client = MagicMock()
                provider_error = FakeS3Error("SlowDown", "endpoint and credential detail")
                if operation == "put":
                    client.put_object.side_effect = provider_error
                    invoke = lambda: _s3_storage(client).save_pdf(b"%PDF-1.4")
                else:
                    client.get_object.side_effect = provider_error
                    invoke = lambda: _s3_storage(client).read_pdf("invoice.pdf")

                with self.assertRaises(PdfStorageUnavailableError) as error:
                    invoke()

                self.assertEqual(str(error.exception), "PDF storage is temporarily unavailable.")

    def test_s3_storage_rejects_absolute_and_traversal_keys_before_request(self) -> None:
        client = MagicMock()
        storage = _s3_storage(client)

        for storage_path in ("/invoice.pdf", "../invoice.pdf", r"..\invoice.pdf"):
            with self.subTest(storage_path=storage_path):
                with self.assertRaises(PdfStoragePathError):
                    storage.read_pdf(storage_path)

        client.get_object.assert_not_called()

    def test_s3_storage_rejects_unsafe_prefix(self) -> None:
        with self.assertRaises(PdfStoragePathError):
            _s3_storage(MagicMock(), prefix="../mail-pdfs/")


if __name__ == "__main__":
    unittest.main()
