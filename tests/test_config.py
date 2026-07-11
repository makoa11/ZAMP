from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from app.config import ConfigError, load_config


class ConfigTests(unittest.TestCase):
    def _write_env(self, root: Path, values: dict[str, str]) -> None:
        root.joinpath(".env").write_text(
            "\n".join(f"{key}={value}" for key, value in values.items()),
            encoding="utf-8",
        )

    def _base_env(self) -> dict[str, str]:
        return {
            "WORKOS_API_KEY": "sk_test_123",
            "WORKOS_CLIENT_ID": "client_123",
            "WORKOS_COOKIE_PASSWORD": Fernet.generate_key().decode("ascii"),
            "APP_SIGNING_SECRET": "test-signing-secret-with-enough-entropy",
        }

    def test_app_signing_secret_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            values = self._base_env()
            values.pop("APP_SIGNING_SECRET")
            self._write_env(root, values)

            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ConfigError) as error:
                    load_config(root)

            self.assertIn("APP_SIGNING_SECRET", str(error.exception))

    def test_app_signing_secret_derives_distinct_purpose_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_env(root, self._base_env())

            with patch.dict(os.environ, {}, clear=True):
                config = load_config(root)

        signing_secrets = {
            config.csrf_secret,
            config.otp_email_cookie_secret,
            config.session_metadata_cookie_secret,
            config.email_verification_cookie_secret,
        }
        self.assertEqual(len(signing_secrets), 4)
        self.assertNotIn(config.workos_cookie_password, signing_secrets)

    def test_mail_db_pool_max_must_not_be_less_than_min(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            values = {
                **self._base_env(),
                "MAIL_DB_POOL_MIN_SIZE": "5",
                "MAIL_DB_POOL_MAX_SIZE": "2",
            }
            self._write_env(root, values)

            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ConfigError) as error:
                    load_config(root)

        self.assertIn("MAIL_DB_POOL_MAX_SIZE", str(error.exception))

    def test_mail_parse_ocr_region_limit_is_loaded_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_env(
                root,
                {
                    **self._base_env(),
                    "MAIL_PARSE_OCR_MAX_REGIONS": "3",
                },
            )

            with patch.dict(os.environ, {}, clear=True):
                config = load_config(root)

        self.assertEqual(config.mail_parse_ocr_max_regions, 3)

    def test_mail_parse_ocr_max_regions_must_be_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_env(
                root,
                {
                    **self._base_env(),
                    "MAIL_PARSE_OCR_MAX_REGIONS": "0",
                },
            )

            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ConfigError) as error:
                    load_config(root)

        self.assertIn("MAIL_PARSE_OCR_MAX_REGIONS", str(error.exception))
