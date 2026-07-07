from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from app.mail_store import PdfStorage, TokenCipher


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

            expected_relative_path = f"{digest[:2]}/{digest[2:4]}/{digest}.pdf"
            self.assertEqual(first.sha256, digest)
            self.assertEqual(first.relative_path, expected_relative_path)
            self.assertEqual(second.relative_path, expected_relative_path)
            self.assertEqual(Path(tmp, expected_relative_path).read_bytes(), content)
