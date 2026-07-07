from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet

from app.config import load_config
from app.mail_providers import GmailClient, OutlookClient, pkce_code_challenge


class FakeHttp:
    def __init__(self) -> None:
        self.posted_forms: list[tuple[str, dict[str, str]]] = []

    def post_form(self, url: str, *, payload: dict[str, str]) -> dict[str, object]:
        self.posted_forms.append((url, payload))
        return {"access_token": "access-token"}


class ProviderAuthorizationUrlTests(unittest.TestCase):
    def _write_env(self, root: Path) -> None:
        root.joinpath(".env").write_text(
            "\n".join(
                [
                    "WORKOS_API_KEY=sk_test_123",
                    "WORKOS_CLIENT_ID=client_123",
                    f"WORKOS_COOKIE_PASSWORD={Fernet.generate_key().decode('ascii')}",
                    "APP_SIGNING_SECRET=test-signing-secret-with-enough-entropy",
                    "GOOGLE_OAUTH_CLIENT_ID=google-client",
                    "GOOGLE_OAUTH_CLIENT_SECRET=google-secret",
                    "MICROSOFT_CLIENT_ID=microsoft-client",
                    "MICROSOFT_CLIENT_SECRET=microsoft-secret",
                ]
            ),
            encoding="utf-8",
        )

    def test_pkce_code_challenge_uses_s256(self) -> None:
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"

        self.assertEqual(pkce_code_challenge(verifier), "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")

    def test_gmail_authorization_url_requests_offline_consent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_env(root)
            with patch.dict(os.environ, {}, clear=True):
                config = load_config(root)

        url = GmailClient(config).authorization_url(
            redirect_uri="https://app.example/api/mail/oauth/gmail/callback",
            state="state-123",
            code_challenge="challenge-123",
        )
        params = parse_qs(urlparse(url).query)

        self.assertEqual(params["access_type"], ["offline"])
        self.assertEqual(params["prompt"], ["consent"])
        self.assertEqual(params["code_challenge"], ["challenge-123"])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertIn("https://www.googleapis.com/auth/gmail.readonly", params["scope"][0])

    def test_gmail_exchange_code_sends_pkce_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_env(root)
            with patch.dict(os.environ, {}, clear=True):
                config = load_config(root)

        http = FakeHttp()
        GmailClient(config, http=http).exchange_code(
            code="code-123",
            redirect_uri="https://app.example/api/mail/oauth/gmail/callback",
            code_verifier="verifier-123",
        )

        self.assertEqual(http.posted_forms[0][0], "https://oauth2.googleapis.com/token")
        self.assertEqual(http.posted_forms[0][1]["code_verifier"], "verifier-123")
        self.assertEqual(http.posted_forms[0][1]["client_secret"], "google-secret")

    def test_outlook_authorization_url_requests_mail_read_and_offline_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_env(root)
            with patch.dict(os.environ, {}, clear=True):
                config = load_config(root)

        url = OutlookClient(config).authorization_url(
            redirect_uri="https://app.example/api/mail/oauth/outlook/callback",
            state="state-123",
        )
        params = parse_qs(urlparse(url).query)

        self.assertEqual(params["response_mode"], ["query"])
        self.assertIn("offline_access", params["scope"][0])
        self.assertIn("Mail.Read", params["scope"][0])
