from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.workos_auth import RequestMeta, TimedSessionRevoker, WorkOSAuthService


class TestAuthService:
    def __init__(self) -> None:
        self.revoked_session_ids: list[str | None] = []

    def revoke_session_id(self, session_id: str | None) -> None:
        self.revoked_session_ids.append(session_id)


class TimedSessionRevokerTests(unittest.TestCase):
    def test_revoked_session_ids_are_pruned_after_retention_window(self) -> None:
        now = 1000.0
        auth_service = TestAuthService()
        revoker = TimedSessionRevoker(auth_service, delay_seconds=10, clock=lambda: now)

        revoker.mark_revoked("session_1")
        self.assertTrue(revoker.is_revoked("session_1"))

        now = 1011.0
        self.assertFalse(revoker.is_revoked("session_1"))
        self.assertEqual(revoker._revoked_session_ids, {})

    def test_revoke_marks_and_calls_workos(self) -> None:
        now = 1000.0
        auth_service = TestAuthService()
        revoker = TimedSessionRevoker(auth_service, delay_seconds=10, clock=lambda: now)

        revoker.revoke("session_1")

        self.assertTrue(revoker.is_revoked("session_1"))
        self.assertEqual(auth_service.revoked_session_ids, ["session_1"])


class SignupFlowTests(unittest.TestCase):
    def test_signup_does_not_delete_user_on_transient_auth_failure(self) -> None:
        created_users: list[dict[str, object]] = []
        service = WorkOSAuthService.__new__(WorkOSAuthService)

        class TestUserManagement:
            def create_user(self, **kwargs: object) -> SimpleNamespace:
                created_users.append(kwargs)
                return SimpleNamespace(id="user_123")

            def delete_user(self, user_id: str) -> None:
                raise AssertionError(f"delete_user should not be called for {user_id}")

        service.client = SimpleNamespace(user_management=TestUserManagement())

        def fail_authentication(**kwargs: object) -> None:
            raise RuntimeError("authentication failed")

        service.authenticate_with_password = fail_authentication

        with self.assertRaises(RuntimeError):
            service.signup_with_password(
                email="user@example.com",
                password="long-enough-password",
                first_name="User",
                last_name="Example",
                meta=RequestMeta(ip_address="127.0.0.1", user_agent="test"),
            )

        self.assertEqual(len(created_users), 1)
        self.assertEqual(created_users[0]["email"], "user@example.com")
