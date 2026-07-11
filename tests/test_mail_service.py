from __future__ import annotations

import unittest
from types import SimpleNamespace

from cryptography.fernet import Fernet

from app.mail_providers import pkce_code_challenge
from app.mail_service import MailIntegration, _require_granted_scopes
from app.mail_store import MailIntegrationError, TokenCipher


class TestRepo:
    def __init__(self) -> None:
        self.encrypted_code_verifier: str | None = None
        self.invoice_patterns: list[str] = []
        self.jobs: list[dict[str, object]] = []

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

    def get_invoice_match_patterns(self, *, owner_user_id: str) -> list[str]:
        return self.invoice_patterns

    def set_invoice_match_patterns(
        self, *, owner_user_id: str, patterns: list[str]
    ) -> list[str]:
        self.invoice_patterns = patterns
        return patterns

    def list_accounts(self, *, owner_user_id: str) -> list[dict[str, object]]:
        return [
            {"id": 1, "provider": "gmail", "status": "active"},
            {"id": 2, "provider": "outlook", "status": "active"},
            {"id": 3, "provider": "gmail", "status": "disconnected"},
        ]

    def enqueue_job(self, **kwargs: object) -> bool:
        if any(job["unique_key"] == kwargs["unique_key"] for job in self.jobs):
            return False
        self.jobs.append(kwargs)
        return True


class TestGmail:
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


class TestOutlook:
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
        self._repo = TestRepo()  # type: ignore[assignment]
        self._cipher = TokenCipher(Fernet.generate_key().decode("ascii"))  # type: ignore[assignment]
        self._gmail = TestGmail()  # type: ignore[assignment]
        self._outlook = TestOutlook()  # type: ignore[assignment]
        self.completed_gmail: dict[str, str | None] = {}

    def _complete_gmail_oauth(
        self, *, owner_user_id: str, code: str, code_verifier: str | None = None
    ) -> None:
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
        integration.repo.encrypted_code_verifier = integration.cipher.encrypt(
            "verifier-123"
        )

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
        integration.repo.encrypted_code_verifier = integration.cipher.encrypt(
            "verifier-123"
        )

        location = integration.complete_oauth(
            provider="gmail",
            state="state-123",
            code="code-123",
            owner_user_id="other-user",
        )

        self.assertEqual(
            location, "https://front.example/mail?mail_error=invalid_oauth_state"
        )
        self.assertEqual(integration.completed_gmail, {})


class MailIntegrationLifecycleTests(unittest.TestCase):
    def test_close_closes_database_and_resets_ready_state(self) -> None:
        class Database:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        database = Database()
        integration = MailIntegration(SimpleNamespace())
        integration._ready = True
        integration._repo = SimpleNamespace(database=database)  # type: ignore[assignment]
        integration._cipher = object()  # type: ignore[assignment]

        integration.close()

        self.assertTrue(database.closed)
        self.assertFalse(integration._ready)
        self.assertIsNone(integration._repo)
        self.assertIsNone(integration._cipher)

    def test_close_resets_state_when_database_close_raises(self) -> None:
        class Database:
            def close(self) -> None:
                raise RuntimeError("close failed")

        integration = MailIntegration(SimpleNamespace())
        integration._ready = True
        integration._repo = SimpleNamespace(database=Database())  # type: ignore[assignment]
        integration._cipher = object()  # type: ignore[assignment]

        with self.assertRaises(RuntimeError):
            integration.close()

        self.assertFalse(integration._ready)
        self.assertIsNone(integration._repo)
        self.assertIsNone(integration._cipher)


class InvoicePatternSettingsTests(unittest.TestCase):
    def test_update_invoice_patterns_saves_patterns_and_enqueues_catchup_jobs(
        self,
    ) -> None:
        integration = CapturingMailIntegration()

        patterns = integration.update_invoice_match_patterns(
            owner_user_id="user-123",
            patterns=[r"\binvoice\b", " ", r"^INV-\d+"],
        )

        self.assertEqual(patterns, [r"\binvoice\b", r"^INV-\d+"])
        self.assertEqual(
            integration.get_invoice_match_patterns(owner_user_id="user-123"), patterns
        )
        self.assertEqual(
            [job["job_type"] for job in integration.repo.jobs],
            ["gmail_fallback_sync", "outlook_delta_sync"],
        )
        self.assertEqual(
            [job["unique_key"] for job in integration.repo.jobs],
            [
                "gmail-fallback:1:settings:d8f1065acdc2bfa2",
                "outlook-delta:2:settings:d8f1065acdc2bfa2",
            ],
        )

        integration.update_invoice_match_patterns(
            owner_user_id="user-123",
            patterns=[r"\binvoice\b", " ", r"^INV-\d+"],
        )

        self.assertEqual(len(integration.repo.jobs), 2)

    def test_update_invoice_patterns_empty_list_enqueues_catchup_jobs(
        self,
    ) -> None:
        integration = CapturingMailIntegration()

        patterns = integration.update_invoice_match_patterns(
            owner_user_id="user-123", patterns=[]
        )

        self.assertEqual(patterns, [])
        self.assertEqual(
            [job["job_type"] for job in integration.repo.jobs],
            ["gmail_fallback_sync", "outlook_delta_sync"],
        )

    def test_update_invoice_patterns_rejects_invalid_regex(self) -> None:
        integration = CapturingMailIntegration()

        with self.assertRaises(MailIntegrationError):
            integration.update_invoice_match_patterns(
                owner_user_id="user-123", patterns=["["]
            )

    def test_suggest_invoice_pattern_from_filename(self) -> None:
        integration = CapturingMailIntegration()

        pattern = integration.suggest_invoice_match_pattern(filename="Bill_889.pdf")

        self.assertEqual(pattern, r"^Bill[\s._:#-]*\d+(?:\.pdf)?$")


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
