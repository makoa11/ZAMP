from __future__ import annotations

import unittest
from types import SimpleNamespace

from cryptography.fernet import Fernet

from app.mail_providers import pkce_code_challenge
from app.mail_service import MailIntegration, _require_granted_scopes
from app.mail_store import MailIntegrationError, TokenCipher


class FakeRepo:
    def __init__(self) -> None:
        self.encrypted_code_verifier: str | None = None

    def create_oauth_state(
        self,
        *,
        provider: str,
        owner_user_id: str,
        redirect_after: str | None,
        encrypted_code_verifier: str | None = None,
    ) -> str:
        self.encrypted_code_verifier = encrypted_code_verifier
        return "state-123"

    def consume_oauth_state(
        self,
        *,
        provider: str,
        state: str,
        owner_user_id: str,
    ) -> dict[str, object] | None:
        if owner_user_id != "user-123":
            return None
        return {
            "owner_user_id": "user-123",
            "encrypted_code_verifier": self.encrypted_code_verifier,
        }


class FakeGmail:
    def __init__(self) -> None:
        self.authorization_request: dict[str, str | None] = {}

    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        code_challenge: str | None = None,
    ) -> str:
        self.authorization_request = {
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
        }
        return "https://accounts.google.example/auth"


class FakeOutlook:
    def authorization_url(self, *, redirect_uri: str, state: str) -> str:
        return "https://login.microsoft.example/auth"


class CapturingMailIntegration(MailIntegration):
    def __init__(self) -> None:
        super().__init__(
            SimpleNamespace(
                app_url="https://app.example",
                mail_frontend_redirect_url="https://front.example/mail",
            )
        )
        self._ready = True
        self._repo = FakeRepo()  # type: ignore[assignment]
        self._cipher = TokenCipher(Fernet.generate_key().decode("ascii"))  # type: ignore[assignment]
        self._gmail = FakeGmail()  # type: ignore[assignment]
        self._outlook = FakeOutlook()  # type: ignore[assignment]
        self.completed_gmail: dict[str, str | None] = {}

    def _complete_gmail_oauth(self, *, owner_user_id: str, code: str, code_verifier: str | None = None) -> None:
        self.completed_gmail = {
            "owner_user_id": owner_user_id,
            "code": code,
            "code_verifier": code_verifier,
        }


class MailIntegrationPkceTests(unittest.TestCase):
    def test_start_gmail_oauth_stores_verifier_and_sends_code_challenge(self) -> None:
        integration = CapturingMailIntegration()

        result = integration.start_oauth(provider="gmail", owner_user_id="user-123")

        repo = integration.repo
        gmail = integration.gmail
        self.assertEqual(result["state"], "state-123")
        self.assertIsInstance(repo.encrypted_code_verifier, str)
        code_verifier = integration.cipher.decrypt(repo.encrypted_code_verifier)
        self.assertIsInstance(code_verifier, str)
        self.assertNotEqual(repo.encrypted_code_verifier, code_verifier)
        self.assertGreaterEqual(len(code_verifier or ""), 43)
        self.assertLessEqual(len(code_verifier or ""), 128)
        self.assertEqual(gmail.authorization_request["state"], "state-123")
        self.assertEqual(
            gmail.authorization_request["code_challenge"],
            pkce_code_challenge(str(code_verifier)),
        )

    def test_complete_gmail_oauth_passes_stored_verifier_to_exchange(self) -> None:
        integration = CapturingMailIntegration()
        integration.repo.encrypted_code_verifier = integration.cipher.encrypt("verifier-123")

        location = integration.complete_oauth(
            provider="gmail",
            state="state-123",
            code="code-123",
            owner_user_id="user-123",
        )

        self.assertEqual(location, "https://front.example/mail?mail_connected=gmail")
        self.assertEqual(
            integration.completed_gmail,
            {
                "owner_user_id": "user-123",
                "code": "code-123",
                "code_verifier": "verifier-123",
            },
        )

    def test_complete_gmail_oauth_rejects_state_for_other_user(self) -> None:
        integration = CapturingMailIntegration()
        integration.repo.encrypted_code_verifier = integration.cipher.encrypt("verifier-123")

        location = integration.complete_oauth(
            provider="gmail",
            state="state-123",
            code="code-123",
            owner_user_id="other-user",
        )

        self.assertEqual(location, "https://front.example/mail?mail_error=invalid_oauth_state")
        self.assertEqual(integration.completed_gmail, {})


class ScopeValidationTests(unittest.TestCase):
    def test_required_scope_validation_rejects_missing_gmail_readonly(self) -> None:
        with self.assertRaises(MailIntegrationError):
            _require_granted_scopes(
                {"scope": "openid email"},
                ["https://www.googleapis.com/auth/gmail.readonly"],
                "Google",
            )

    def test_required_scope_validation_accepts_granted_gmail_readonly(self) -> None:
        _require_granted_scopes(
            {"scope": "openid email https://www.googleapis.com/auth/gmail.readonly"},
            ["https://www.googleapis.com/auth/gmail.readonly"],
            "Google",
        )
