from __future__ import annotations

import json
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from workos._errors import EmailVerificationRequiredError
from workos.session import (
    AuthenticateWithSessionCookieSuccessResponse,
    RefreshWithSessionCookieSuccessResponse,
)

from .config import AppConfig
from .cookies import build_cookie, clear_cookie, parse_cookie_header
from .security import generate_csrf_token, sign_value, unsign_value, valid_signed_pair
from .templates import dashboard_page, error_page, login_page, signup_page
from .workos_auth import (
    RequestMeta,
    WorkOSAuthService,
    TimedSessionRevoker,
    public_error_message,
    public_signup_error_message,
    public_signup_message_kind,
    public_verification_error_message,
    user_payload,
)


STATIC_CSS = Path(__file__).parent / "static" / "styles.css"


class ZampRequestHandler(BaseHTTPRequestHandler):
    config: AppConfig
    auth: WorkOSAuthService
    timed_revoker: TimedSessionRevoker

    server_version = "ZAMPAuth/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            session, _, clear_cookies = self._session()
            self._redirect("/dashboard" if session else "/login", cookies=clear_cookies)
            return
        if parsed.path == "/login":
            self._handle_login_get(parsed.query)
            return
        if parsed.path == "/signup":
            self._handle_signup_get(parsed.query)
            return
        if parsed.path == "/dashboard":
            self._handle_dashboard_get()
            return
        if parsed.path == "/api/session":
            self._handle_api_session()
            return
        if parsed.path == "/logout":
            self._send_method_not_allowed("POST")
            return
        if parsed.path == "/static/styles.css":
            self._send_static_css()
            return
        self._send_html(HTTPStatus.NOT_FOUND, error_page(404, "Page not found."))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/login":
            self._handle_login_post()
            return
        if parsed.path == "/signup":
            self._handle_signup_post()
            return
        if parsed.path == "/logout":
            self._handle_logout_post()
            return
        self._send_html(HTTPStatus.NOT_FOUND, error_page(404, "Page not found."))

    def _handle_login_post(self) -> None:
        form = self._form()
        action = form.get("action", "")
        if action == "password":
            self._handle_password_login(form)
            return
        if action == "otp_send":
            self._handle_otp_start(form)
            return
        if action == "otp_verify":
            self._handle_otp_verify(form)
            return
        if action == "email_verification_verify":
            self._handle_login_email_verification_verify(form)
            return
        self._send_html(HTTPStatus.BAD_REQUEST, error_page(400, "Unsupported login action."))

    def _handle_signup_post(self) -> None:
        form = self._form()
        action = form.get("action", "")
        if action == "password":
            self._handle_password_signup(form)
            return
        if action == "otp_send":
            self._handle_signup_otp_start(form)
            return
        if action == "otp_verify":
            self._handle_signup_otp_verify(form)
            return
        if action == "email_verification_verify":
            self._handle_signup_email_verification_verify(form)
            return
        self._send_html(HTTPStatus.BAD_REQUEST, error_page(400, "Unsupported signup action."))

    def _handle_login_get(self, query: str) -> None:
        cookies = self._cookies()
        csrf, csrf_cookie = self._csrf_cookie(cookies)
        params = parse_qs(query)
        mode = params.get("mode", ["password"])[0]
        otp_email = unsign_value(
            cookies.get(self.config.otp_email_cookie_name),
            self.config.otp_email_cookie_secret,
            max_age_seconds=600,
        )
        message = None
        kind = "error"
        if params.get("sent") == ["1"] and otp_email:
            message = "Code sent. Check your email."
            kind = "success"
            mode = "otp"
        self._send_html(
            HTTPStatus.OK,
            login_page(
                csrf_token=csrf,
                mode=mode,
                message=message,
                message_kind=kind,
                otp_email=otp_email,
            ),
            cookies=[csrf_cookie] if csrf_cookie else None,
        )

    def _handle_signup_get(self, query: str) -> None:
        cookies = self._cookies()
        csrf, csrf_cookie = self._csrf_cookie(cookies)
        params = parse_qs(query)
        mode = params.get("mode", ["password"])[0]
        otp_email = unsign_value(
            cookies.get(self.config.otp_email_cookie_name),
            self.config.otp_email_cookie_secret,
            max_age_seconds=600,
        )
        message = None
        kind = "error"
        if params.get("sent") == ["1"] and otp_email:
            message = "Code sent. Check your email."
            kind = "success"
            mode = "otp"
        self._send_html(
            HTTPStatus.OK,
            signup_page(
                csrf_token=csrf,
                mode=mode,
                message=message,
                message_kind=kind,
                otp_email=otp_email,
            ),
            cookies=[csrf_cookie] if csrf_cookie else None,
        )

    def _handle_dashboard_get(self) -> None:
        session, set_session_cookie, clear_cookies = self._session()
        if not session:
            self._redirect("/login", cookies=clear_cookies)
            return
        cookies = self._cookies()
        csrf, csrf_cookie = self._csrf_cookie(cookies)
        response_cookies = [cookie for cookie in [set_session_cookie, csrf_cookie] if cookie]
        self._send_html(
            HTTPStatus.OK,
            dashboard_page(csrf_token=csrf, session=self._session_payload(session)),
            cookies=response_cookies,
        )

    def _handle_api_session(self) -> None:
        session, set_session_cookie, clear_cookies = self._session()
        if not session:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"authenticated": False}, cookies=clear_cookies)
            return
        self._send_json(
            HTTPStatus.OK,
            {"authenticated": True, **self._session_payload(session)},
            cookies=[set_session_cookie] if set_session_cookie else None,
        )

    def _handle_password_login(self, form: dict[str, str] | None = None) -> None:
        if form is None:
            form = self._form()
        if not self._valid_csrf(form):
            self._send_html(HTTPStatus.FORBIDDEN, error_page(403, "Invalid request token."))
            return

        email = form.get("email", "").strip().lower()
        password = form.get("password", "")
        if not email or not password:
            self._render_login_error("Email and password are required.", mode="password")
            return

        try:
            auth_response = self.auth.authenticate_with_password(
                email=email,
                password=password,
                meta=self._request_meta(),
            )
            sealed_session = self.auth.seal_auth_response(auth_response)
        except EmailVerificationRequiredError as exc:
            pending_token = exc.pending_authentication_token
            if not pending_token:
                self._render_login_error(public_error_message(exc), mode="password")
                return
            verification_email = exc.email or email
            self._render_login_notice(
                "Enter the verification code from your email.",
                mode="password",
                message_kind="success",
                email_verification_email=verification_email,
                extra_cookies=[
                    self._email_verification_cookie(
                        email=verification_email,
                        pending_authentication_token=pending_token,
                    )
                ],
            )
            return
        except Exception as exc:
            self._render_login_error(public_error_message(exc), mode="password")
            return

        self._redirect(
            "/dashboard",
            cookies=self._auth_success_cookies(auth_response, sealed_session),
        )

    def _handle_password_signup(self, form: dict[str, str] | None = None) -> None:
        if form is None:
            form = self._form()
        if not self._valid_csrf(form):
            self._send_html(HTTPStatus.FORBIDDEN, error_page(403, "Invalid request token."))
            return

        email = form.get("email", "").strip().lower()
        password = form.get("password", "")
        password_confirm = form.get("password_confirm", "")
        first_name = form.get("first_name", "").strip()
        last_name = form.get("last_name", "").strip()

        if not email or not password or not password_confirm:
            self._render_signup_error("Email and password are required.", mode="password")
            return
        if password != password_confirm:
            self._render_signup_error("Passwords do not match.", mode="password")
            return
        if len(password) < 10:
            self._render_signup_error("Password must be at least 10 characters.", mode="password")
            return

        try:
            auth_response = self.auth.signup_with_password(
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                meta=self._request_meta(),
            )
            sealed_session = self.auth.seal_auth_response(auth_response)
        except EmailVerificationRequiredError as exc:
            pending_token = exc.pending_authentication_token
            if not pending_token:
                self._render_signup_error(public_signup_error_message(exc), mode="password")
                return
            verification_email = exc.email or email
            self._render_signup_notice(
                public_signup_error_message(exc),
                mode="password",
                message_kind="success",
                email_verification_email=verification_email,
                extra_cookies=[
                    self._email_verification_cookie(
                        email=verification_email,
                        pending_authentication_token=pending_token,
                    )
                ],
            )
            return
        except Exception as exc:
            self._render_signup_notice(
                public_signup_error_message(exc),
                mode="password",
                message_kind=public_signup_message_kind(exc),
            )
            return

        self._redirect(
            "/dashboard",
            cookies=self._auth_success_cookies(auth_response, sealed_session),
        )

    def _handle_login_email_verification_verify(self, form: dict[str, str] | None = None) -> None:
        if form is None:
            form = self._form()
        if not self._valid_csrf(form):
            self._send_html(HTTPStatus.FORBIDDEN, error_page(403, "Invalid request token."))
            return

        pending = self._pending_email_verification()
        email = form.get("email", "").strip().lower() or pending.get("email", "")
        code = form.get("code", "").strip().replace(" ", "")
        pending_token = pending.get("pending_authentication_token", "")

        if not email or not code:
            self._render_login_notice(
                "Email and verification code are required.",
                mode="password",
                message_kind="error",
                email_verification_email=email,
            )
            return
        if not pending_token:
            self._render_login_notice(
                "Verification session expired. Request a new code.",
                mode="password",
                message_kind="error",
                email_verification_email=email,
            )
            return

        try:
            auth_response = self.auth.authenticate_with_email_verification(
                pending_authentication_token=pending_token,
                code=code,
                meta=self._request_meta(),
            )
            sealed_session = self.auth.seal_auth_response(auth_response)
        except Exception as exc:
            self._render_login_notice(
                public_verification_error_message(exc),
                mode="password",
                message_kind="error",
                email_verification_email=email,
            )
            return

        self._redirect(
            "/dashboard",
            cookies=self._auth_success_cookies(auth_response, sealed_session),
        )

    def _handle_otp_start(self, form: dict[str, str] | None = None) -> None:
        if form is None:
            form = self._form()
        if not self._valid_csrf(form):
            self._send_html(HTTPStatus.FORBIDDEN, error_page(403, "Invalid request token."))
            return

        email = form.get("email", "").strip().lower()
        if not email:
            self._render_login_error("Email is required.", mode="otp")
            return

        try:
            self.auth.send_email_otp(email=email, meta=self._request_meta())
        except Exception as exc:
            self._render_login_error(public_error_message(exc), mode="otp", otp_email=email)
            return

        self._redirect(
            "/login?mode=otp&sent=1",
            cookies=[
                build_cookie(
                    self.config.otp_email_cookie_name,
                    sign_value(email, self.config.otp_email_cookie_secret),
                    max_age=600,
                    secure=self.config.cookie_secure,
                )
            ],
        )

    def _handle_signup_otp_start(self, form: dict[str, str] | None = None) -> None:
        if form is None:
            form = self._form()
        if not self._valid_csrf(form):
            self._send_html(HTTPStatus.FORBIDDEN, error_page(403, "Invalid request token."))
            return

        email = form.get("email", "").strip().lower()
        if not email:
            self._render_signup_error("Email is required.", mode="otp")
            return

        try:
            self.auth.send_email_otp(email=email, meta=self._request_meta())
        except Exception as exc:
            self._render_signup_error(public_signup_error_message(exc), mode="otp", otp_email=email)
            return

        self._redirect(
            "/signup?mode=otp&sent=1",
            cookies=[
                build_cookie(
                    self.config.otp_email_cookie_name,
                    sign_value(email, self.config.otp_email_cookie_secret),
                    max_age=600,
                    secure=self.config.cookie_secure,
                )
            ],
        )

    def _handle_otp_verify(self, form: dict[str, str] | None = None) -> None:
        if form is None:
            form = self._form()
        if not self._valid_csrf(form):
            self._send_html(HTTPStatus.FORBIDDEN, error_page(403, "Invalid request token."))
            return

        cookies = self._cookies()
        email = form.get("email", "").strip().lower() or (
            unsign_value(
                cookies.get(self.config.otp_email_cookie_name),
                self.config.otp_email_cookie_secret,
                max_age_seconds=600,
            )
            or ""
        )
        code = form.get("code", "").strip().replace(" ", "")
        if not email or not code:
            self._render_login_error("Email and code are required.", mode="otp", otp_email=email)
            return

        try:
            auth_response = self.auth.authenticate_with_email_otp(
                email=email,
                code=code,
                meta=self._request_meta(),
            )
            sealed_session = self.auth.seal_auth_response(auth_response)
        except Exception as exc:
            self._render_login_error(public_error_message(exc), mode="otp", otp_email=email)
            return

        self._redirect(
            "/dashboard",
            cookies=self._auth_success_cookies(auth_response, sealed_session),
        )

    def _handle_signup_email_verification_verify(self, form: dict[str, str] | None = None) -> None:
        if form is None:
            form = self._form()
        if not self._valid_csrf(form):
            self._send_html(HTTPStatus.FORBIDDEN, error_page(403, "Invalid request token."))
            return

        pending = self._pending_email_verification()
        email = form.get("email", "").strip().lower() or pending.get("email", "")
        code = form.get("code", "").strip().replace(" ", "")
        pending_token = pending.get("pending_authentication_token", "")

        if not email or not code:
            self._render_signup_notice(
                "Email and verification code are required.",
                mode="password",
                message_kind="error",
                email_verification_email=email,
            )
            return
        if not pending_token:
            self._render_signup_notice(
                "Verification session expired. Request a new code.",
                mode="password",
                message_kind="error",
                email_verification_email=email,
            )
            return

        try:
            auth_response = self.auth.authenticate_with_email_verification(
                pending_authentication_token=pending_token,
                code=code,
                meta=self._request_meta(),
            )
            sealed_session = self.auth.seal_auth_response(auth_response)
        except Exception as exc:
            self._render_signup_notice(
                public_verification_error_message(exc),
                mode="password",
                message_kind="error",
                email_verification_email=email,
            )
            return

        self._redirect(
            "/dashboard",
            cookies=self._auth_success_cookies(auth_response, sealed_session),
        )

    def _handle_signup_otp_verify(self, form: dict[str, str] | None = None) -> None:
        if form is None:
            form = self._form()
        if not self._valid_csrf(form):
            self._send_html(HTTPStatus.FORBIDDEN, error_page(403, "Invalid request token."))
            return

        cookies = self._cookies()
        email = form.get("email", "").strip().lower() or (
            unsign_value(
                cookies.get(self.config.otp_email_cookie_name),
                self.config.otp_email_cookie_secret,
                max_age_seconds=600,
            )
            or ""
        )
        code = form.get("code", "").strip().replace(" ", "")
        if not email or not code:
            self._render_signup_error("Email and code are required.", mode="otp", otp_email=email)
            return

        try:
            auth_response = self.auth.authenticate_with_email_otp(
                email=email,
                code=code,
                meta=self._request_meta(),
            )
            sealed_session = self.auth.seal_auth_response(auth_response)
        except Exception as exc:
            self._render_signup_error(public_signup_error_message(exc), mode="otp", otp_email=email)
            return

        self._redirect(
            "/dashboard",
            cookies=self._auth_success_cookies(auth_response, sealed_session),
        )

    def _handle_logout_post(self) -> None:
        form = self._form()
        if not self._valid_csrf(form):
            self._send_html(HTTPStatus.FORBIDDEN, error_page(403, "Invalid request token."))
            return

        session_cookie = self._cookies().get(self.config.session_cookie_name)
        session_id = self.auth.revoke_session_cookie(session_cookie)
        self.timed_revoker.mark_revoked(session_id)
        self._redirect("/login", cookies=self._clear_auth_cookies())

    def _render_login_error(
        self,
        message: str,
        *,
        mode: str,
        otp_email: str | None = None,
    ) -> None:
        self._render_login_notice(
            message,
            mode=mode,
            message_kind="error",
            otp_email=otp_email,
        )

    def _render_login_notice(
        self,
        message: str,
        *,
        mode: str,
        message_kind: str,
        otp_email: str | None = None,
        email_verification_email: str | None = None,
        extra_cookies: list[str] | None = None,
    ) -> None:
        request_cookies = self._cookies()
        csrf, csrf_cookie = self._csrf_cookie(request_cookies)
        status = HTTPStatus.OK if message_kind == "success" else HTTPStatus.BAD_REQUEST
        response_cookies = [cookie for cookie in [csrf_cookie, *(extra_cookies or [])] if cookie]
        self._send_html(
            status,
            login_page(
                csrf_token=csrf,
                mode=mode,
                message=message,
                message_kind=message_kind,
                otp_email=otp_email,
                email_verification_email=email_verification_email,
            ),
            cookies=response_cookies,
        )

    def _render_signup_notice(
        self,
        message: str,
        *,
        mode: str,
        message_kind: str,
        otp_email: str | None = None,
        email_verification_email: str | None = None,
        extra_cookies: list[str] | None = None,
    ) -> None:
        request_cookies = self._cookies()
        csrf, csrf_cookie = self._csrf_cookie(request_cookies)
        status = HTTPStatus.OK if message_kind == "success" else HTTPStatus.BAD_REQUEST
        response_cookies = [cookie for cookie in [csrf_cookie, *(extra_cookies or [])] if cookie]
        self._send_html(
            status,
            signup_page(
                csrf_token=csrf,
                mode=mode,
                message=message,
                message_kind=message_kind,
                otp_email=otp_email,
                email_verification_email=email_verification_email,
            ),
            cookies=response_cookies,
        )

    def _render_signup_error(
        self,
        message: str,
        *,
        mode: str,
        otp_email: str | None = None,
    ) -> None:
        self._render_signup_notice(
            message,
            mode=mode,
            message_kind="error",
            otp_email=otp_email,
        )

    def _session(
        self,
    ) -> tuple[
        AuthenticateWithSessionCookieSuccessResponse | RefreshWithSessionCookieSuccessResponse | None,
        str | None,
        list[str],
    ]:
        session_cookie = self._cookies().get(self.config.session_cookie_name)
        if not session_cookie:
            return None, None, []

        try:
            auth_result = self.auth.authenticate_session(session_cookie)
        except Exception:
            return None, None, self._clear_auth_cookies()
        if isinstance(auth_result, AuthenticateWithSessionCookieSuccessResponse):
            expired_cookies = self._expired_session_cookies(auth_result.session_id)
            if expired_cookies:
                return None, None, expired_cookies
            return auth_result, None, []

        try:
            refresh_result = self.auth.refresh_session(session_cookie)
        except Exception:
            return None, None, self._clear_auth_cookies()
        if isinstance(refresh_result, RefreshWithSessionCookieSuccessResponse) and refresh_result.authenticated:
            expired_cookies = self._expired_session_cookies(refresh_result.session_id)
            if expired_cookies:
                return None, None, expired_cookies
            return refresh_result, self._session_cookie(refresh_result.sealed_session), []
        return None, None, self._clear_auth_cookies()

    def _session_payload(
        self,
        session: AuthenticateWithSessionCookieSuccessResponse | RefreshWithSessionCookieSuccessResponse,
    ) -> dict[str, Any]:
        expires_at = None
        metadata = self._session_metadata()
        created_at = metadata.get("created_at")
        if metadata.get("session_id") == session.session_id and isinstance(created_at, int):
            expires_at = created_at + self.config.session_max_age_seconds

        return {
            "session_id": session.session_id,
            "expires_at": expires_at,
            "organization_id": session.organization_id,
            "role": session.role,
            "roles": list(session.roles or []),
            "permissions": list(session.permissions or []),
            "entitlements": list(session.entitlements or []),
            "feature_flags": list(session.feature_flags or []),
            "user": user_payload(session.user),
            "impersonator": session.impersonator,
        }

    def _csrf_cookie(self, cookie_values: dict[str, str]) -> tuple[str, str | None]:
        existing = cookie_values.get(self.config.csrf_cookie_name)
        if unsign_value(existing, self.config.csrf_secret, max_age_seconds=3600):
            return existing or "", None
        token = generate_csrf_token(self.config.csrf_secret)
        return token, build_cookie(
            self.config.csrf_cookie_name,
            token,
            max_age=3600,
            secure=self.config.cookie_secure,
        )

    def _valid_csrf(self, form: dict[str, str]) -> bool:
        return valid_signed_pair(
            form.get("_csrf"),
            self._cookies().get(self.config.csrf_cookie_name),
            self.config.csrf_secret,
        )

    def _request_meta(self) -> RequestMeta:
        forwarded_for = self.headers.get("X-Forwarded-For")
        ip_address = forwarded_for.split(",", 1)[0].strip() if forwarded_for else self.client_address[0]
        return RequestMeta(
            ip_address=ip_address,
            user_agent=self.headers.get("User-Agent"),
        )

    def _form(self) -> dict[str, str]:
        try:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            return {}
        if content_length > 64 * 1024:
            return {}
        raw = self.rfile.read(content_length).decode("utf-8")
        return {key: values[-1] for key, values in parse_qs(raw, keep_blank_values=True).items()}

    def _cookies(self) -> dict[str, str]:
        return parse_cookie_header(self.headers.get("Cookie"))

    def _session_cookie(self, value: str) -> str:
        return build_cookie(
            self.config.session_cookie_name,
            value,
            max_age=self.config.session_max_age_seconds,
            secure=self.config.cookie_secure,
        )

    def _session_metadata_cookie(self, session_id: str, created_at: int | None = None) -> str:
        payload = json.dumps(
            {
                "session_id": session_id,
                "created_at": created_at or int(time.time()),
            },
            separators=(",", ":"),
        )
        return build_cookie(
            self.config.session_metadata_cookie_name,
            sign_value(payload, self.config.session_metadata_cookie_secret),
            max_age=self.config.session_max_age_seconds,
            secure=self.config.cookie_secure,
        )

    def _auth_success_cookies(self, auth_response: Any, sealed_session: str) -> list[str]:
        cookies = [self._session_cookie(sealed_session)]
        session_id = self.auth.session_id_from_access_token(auth_response.access_token)
        if session_id:
            self.timed_revoker.schedule(session_id)
            cookies.append(self._session_metadata_cookie(session_id))
        cookies.extend(
            [
                clear_cookie(self.config.otp_email_cookie_name, secure=self.config.cookie_secure),
                clear_cookie(self.config.email_verification_cookie_name, secure=self.config.cookie_secure),
            ]
        )
        return cookies

    def _session_metadata(self) -> dict[str, Any]:
        raw = unsign_value(
            self._cookies().get(self.config.session_metadata_cookie_name),
            self.config.session_metadata_cookie_secret,
        )
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _expired_session_cookies(self, session_id: str) -> list[str]:
        if self.timed_revoker.is_revoked(session_id):
            return self._clear_auth_cookies()

        metadata = self._session_metadata()
        created_at = metadata.get("created_at")
        metadata_session_id = metadata.get("session_id")

        if not isinstance(created_at, int) or metadata_session_id != session_id:
            self.timed_revoker.revoke(session_id)
            return self._clear_auth_cookies()

        if int(time.time()) - created_at >= self.config.session_max_age_seconds:
            self.timed_revoker.revoke(session_id)
            return self._clear_auth_cookies()

        return []

    def _email_verification_cookie(
        self,
        *,
        email: str,
        pending_authentication_token: str,
    ) -> str:
        payload = json.dumps(
            {
                "email": email,
                "pending_authentication_token": pending_authentication_token,
            },
            separators=(",", ":"),
        )
        return build_cookie(
            self.config.email_verification_cookie_name,
            sign_value(payload, self.config.email_verification_cookie_secret),
            max_age=600,
            secure=self.config.cookie_secure,
        )

    def _pending_email_verification(self) -> dict[str, str]:
        raw = unsign_value(
            self._cookies().get(self.config.email_verification_cookie_name),
            self.config.email_verification_cookie_secret,
            max_age_seconds=600,
        )
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        email = data.get("email")
        pending_token = data.get("pending_authentication_token")
        if not isinstance(email, str) or not isinstance(pending_token, str):
            return {}
        return {
            "email": email,
            "pending_authentication_token": pending_token,
        }

    def _clear_auth_cookies(self) -> list[str]:
        return [
            clear_cookie(self.config.session_cookie_name, secure=self.config.cookie_secure),
            clear_cookie(self.config.session_metadata_cookie_name, secure=self.config.cookie_secure),
            clear_cookie(self.config.otp_email_cookie_name, secure=self.config.cookie_secure),
            clear_cookie(self.config.email_verification_cookie_name, secure=self.config.cookie_secure),
        ]

    def _send_static_css(self) -> None:
        with open(STATIC_CSS, "rb") as css_file:
            content = css_file.read()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/css; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_html(
        self,
        status: HTTPStatus,
        content: str,
        *,
        cookies: list[str] | None = None,
    ) -> None:
        encoded = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(
        self,
        status: HTTPStatus,
        content: dict[str, Any],
        *,
        cookies: list[str] | None = None,
    ) -> None:
        encoded = json.dumps(content, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(encoded)

    def _send_method_not_allowed(self, allowed_methods: str) -> None:
        content = error_page(405, "Method not allowed.").encode("utf-8")
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.send_header("Allow", allowed_methods)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _redirect(self, location: str, *, cookies: list[str] | None = None) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()


def create_server(config: AppConfig) -> ThreadingHTTPServer:
    auth = WorkOSAuthService(config)
    timed_revoker = TimedSessionRevoker(auth, config.session_max_age_seconds)

    class Handler(ZampRequestHandler):
        pass

    Handler.config = config
    Handler.auth = auth
    Handler.timed_revoker = timed_revoker
    return ThreadingHTTPServer((config.host, config.port), Handler)
