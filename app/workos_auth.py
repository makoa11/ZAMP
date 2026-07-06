from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import jwt
from workos import WorkOSClient
from workos._errors import APIError, EmailVerificationRequiredError, WorkOSError
from workos.session import (
    AuthenticateWithSessionCookieErrorResponse,
    AuthenticateWithSessionCookieSuccessResponse,
    RefreshWithSessionCookieErrorResponse,
    RefreshWithSessionCookieSuccessResponse,
    seal_session_from_auth_response,
)
from workos.user_management import PasswordPlaintext
from workos.user_management.models import AuthenticateResponse

from .config import AppConfig


@dataclass(frozen=True)
class RequestMeta:
    ip_address: str | None
    user_agent: str | None


class WorkOSAuthService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.client = WorkOSClient(
            api_key=config.workos_api_key,
            client_id=config.workos_client_id,
        )

    def authenticate_with_password(
        self,
        *,
        email: str,
        password: str,
        meta: RequestMeta,
    ) -> AuthenticateResponse:
        return self.client.user_management.authenticate_with_password(
            email=email,
            password=password,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
        )

    def signup_with_password(
        self,
        *,
        email: str,
        password: str,
        first_name: str | None,
        last_name: str | None,
        meta: RequestMeta,
    ) -> AuthenticateResponse:
        self.client.user_management.create_user(
            email=email,
            first_name=first_name or None,
            last_name=last_name or None,
            password=PasswordPlaintext(password=password),
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
        )
        # If auth fails, the user still exists and can recover through /login.
        return self.authenticate_with_password(email=email, password=password, meta=meta)

    def send_email_otp(self, *, email: str, meta: RequestMeta) -> None:
        self.client.user_management.create_magic_auth(
            email=email,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
        )

    def authenticate_with_email_otp(
        self,
        *,
        email: str,
        code: str,
        meta: RequestMeta,
    ) -> AuthenticateResponse:
        return self.client.user_management.authenticate_with_magic_auth(
            email=email,
            code=code,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
        )

    def authenticate_with_email_verification(
        self,
        *,
        pending_authentication_token: str,
        code: str,
        meta: RequestMeta,
    ) -> AuthenticateResponse:
        return self.client.user_management.authenticate_with_email_verification(
            pending_authentication_token=pending_authentication_token,
            code=code,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
        )

    def seal_auth_response(self, response: AuthenticateResponse) -> str:
        return seal_session_from_auth_response(
            access_token=response.access_token,
            refresh_token=response.refresh_token,
            user=response.user.to_dict(),
            impersonator=response.impersonator.to_dict() if response.impersonator else None,
            cookie_password=self.config.workos_cookie_password,
        )

    def session_id_from_access_token(self, access_token: str) -> str | None:
        try:
            decoded = jwt.decode(
                access_token,
                options={"verify_signature": False, "verify_aud": False},
            )
        except Exception:
            return None
        session_id = decoded.get("sid")
        return session_id if isinstance(session_id, str) else None

    def authenticate_session(
        self,
        session_cookie: str | None,
    ) -> AuthenticateWithSessionCookieSuccessResponse | AuthenticateWithSessionCookieErrorResponse:
        return self.client.user_management.authenticate_with_session_cookie(
            session_data=session_cookie or "",
            cookie_password=self.config.workos_cookie_password,
        )

    def refresh_session(
        self,
        session_cookie: str,
    ) -> RefreshWithSessionCookieSuccessResponse | RefreshWithSessionCookieErrorResponse:
        session = self.client.user_management.load_sealed_session(
            session_data=session_cookie,
            cookie_password=self.config.workos_cookie_password,
        )
        return session.refresh()

    def revoke_session_cookie(self, session_cookie: str | None) -> str | None:
        if not session_cookie:
            return None

        try:
            auth_result = self.authenticate_session(session_cookie)
            if isinstance(auth_result, AuthenticateWithSessionCookieSuccessResponse):
                self.client.user_management.revoke_session(session_id=auth_result.session_id)
                return auth_result.session_id
        except Exception:
            pass

        try:
            refresh_result = self.refresh_session(session_cookie)
            if isinstance(refresh_result, RefreshWithSessionCookieSuccessResponse):
                self.client.user_management.revoke_session(session_id=refresh_result.session_id)
                return refresh_result.session_id
        except Exception:
            return None
        return None

    def revoke_session_id(self, session_id: str | None) -> None:
        if not session_id:
            return
        try:
            self.client.user_management.revoke_session(session_id=session_id)
        except Exception:
            return


class TimedSessionRevoker:
    def __init__(
        self,
        auth_service: WorkOSAuthService,
        delay_seconds: int,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.auth_service = auth_service
        self.delay_seconds = delay_seconds
        self._revoked_session_ids: dict[str, float] = {}
        self._clock = clock
        self._lock = threading.Lock()

    def schedule(self, session_id: str | None) -> None:
        if not session_id:
            return
        timer = threading.Timer(
            self.delay_seconds,
            self.revoke,
            args=(session_id,),
        )
        timer.daemon = True
        timer.start()

    def revoke(self, session_id: str | None) -> None:
        if not session_id:
            return
        self.mark_revoked(session_id)
        self.auth_service.revoke_session_id(session_id)

    def mark_revoked(self, session_id: str | None) -> None:
        if not session_id:
            return
        now = self._clock()
        with self._lock:
            self._purge_expired_locked(now)
            self._revoked_session_ids[session_id] = now + self.delay_seconds

    def is_revoked(self, session_id: str | None) -> bool:
        if not session_id:
            return False
        now = self._clock()
        with self._lock:
            self._purge_expired_locked(now)
            return session_id in self._revoked_session_ids

    def _purge_expired_locked(self, now: float) -> None:
        expired = [
            session_id
            for session_id, expires_at in self._revoked_session_ids.items()
            if expires_at <= now
        ]
        for session_id in expired:
            del self._revoked_session_ids[session_id]


def public_error_message(exc: Exception) -> str:
    if isinstance(exc, APIError):
        if exc.status_code in {400, 401, 403, 404, 422}:
            return "We could not sign you in with those details."
        if exc.status_code == 409:
            return "An account with that email already exists."
        if exc.status_code == 429:
            return "Too many attempts. Try again shortly."
        return "WorkOS is not available right now. Try again shortly."
    if isinstance(exc, WorkOSError):
        return "WorkOS is not available right now. Try again shortly."
    return "Something went wrong. Try again."


def public_signup_error_message(exc: Exception) -> str:
    if isinstance(exc, EmailVerificationRequiredError):
        return "Account created. Enter the verification code from your email."
    if isinstance(exc, APIError):
        if exc.status_code == 409:
            return "An account with that email already exists."
        if exc.status_code in {400, 401, 403, 404, 422}:
            return "We could not create the account with those details."
        if exc.status_code == 429:
            return "Too many attempts. Try again shortly."
        return "WorkOS is not available right now. Try again shortly."
    if isinstance(exc, WorkOSError):
        return "WorkOS is not available right now. Try again shortly."
    return "Something went wrong. Try again."


def public_signup_message_kind(exc: Exception) -> str:
    if isinstance(exc, EmailVerificationRequiredError):
        return "success"
    return "error"


def public_verification_error_message(exc: Exception) -> str:
    if isinstance(exc, APIError):
        if exc.status_code in {400, 401, 403, 404, 422}:
            return "We could not verify that code."
        if exc.status_code == 429:
            return "Too many attempts. Try again shortly."
        return "WorkOS is not available right now. Try again shortly."
    if isinstance(exc, WorkOSError):
        return "WorkOS is not available right now. Try again shortly."
    return "Something went wrong. Try again."


def user_payload(user: dict[str, Any] | None) -> dict[str, Any]:
    if not user:
        return {}
    return {
        "id": user.get("id"),
        "email": user.get("email"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "email_verified": user.get("email_verified"),
        "profile_picture_url": user.get("profile_picture_url"),
    }
