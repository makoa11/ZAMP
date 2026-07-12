from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from cryptography.fernet import Fernet

from .invoice_ocr import OCR_MAX_DOCUMENT_PAGES, OCR_MAX_REGIONS


class ConfigError(RuntimeError):
    """Raised when required application configuration is missing or invalid."""


def _load_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def _env(name: str, env_file_values: Dict[str, str], default: str | None = None) -> str | None:
    return os.environ.get(name) or env_file_values.get(name) or default


def _bool_env(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, env_file_values: Dict[str, str], default: int) -> int:
    value = _env(name, env_file_values, str(default))
    try:
        parsed = int(str(value))
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer.") from exc
    if parsed <= 0:
        raise ConfigError(f"{name} must be greater than 0.")
    return parsed


def _optional_int_env(
    name: str,
    env_file_values: Dict[str, str],
    default: int | None = None,
) -> int | None:
    value = _env(name, env_file_values)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer.") from exc
    if parsed <= 0:
        raise ConfigError(f"{name} must be greater than 0.")
    return parsed


def _derive_signing_secret(secret: str, purpose: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        f"zamp:{purpose}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


@dataclass(frozen=True)
class AppConfig:
    workos_api_key: str
    workos_client_id: str
    workos_cookie_password: str
    csrf_secret: str
    otp_email_cookie_secret: str
    session_metadata_cookie_secret: str
    email_verification_cookie_secret: str
    host: str
    port: int
    app_url: str
    session_cookie_name: str
    session_metadata_cookie_name: str
    session_max_age_seconds: int
    otp_email_cookie_name: str
    email_verification_cookie_name: str
    csrf_cookie_name: str
    cookie_secure: bool
    database_url: str | None
    mail_db_pool_min_size: int
    mail_db_pool_max_size: int
    mail_token_encryption_key: str | None
    mail_pdf_storage_dir: str
    mail_frontend_redirect_url: str
    google_oauth_client_id: str | None
    google_oauth_client_secret: str | None
    gmail_pubsub_topic: str | None
    gmail_pubsub_subscription: str | None
    gmail_pubsub_oidc_audience: str | None
    gmail_pubsub_oidc_service_account_email: str | None
    gmail_webhook_secret: str | None
    microsoft_client_id: str | None
    microsoft_client_secret: str | None
    microsoft_tenant_id: str
    mail_parse_ocr_max_regions: int
    mail_parse_ocr_max_document_pages: int | None

    @property
    def logout_return_url(self) -> str:
        return f"{self.app_url.rstrip('/')}/login"


def load_config(root: Path | None = None) -> AppConfig:
    project_root = root or Path.cwd()
    env_file_values = _load_env_file(project_root / ".env")

    api_key = _env("WORKOS_API_KEY", env_file_values)
    client_id = _env("WORKOS_CLIENT_ID", env_file_values)
    cookie_password = _env("WORKOS_COOKIE_PASSWORD", env_file_values)
    app_signing_secret = _env("APP_SIGNING_SECRET", env_file_values)

    missing = [
        name
        for name, value in {
            "WORKOS_API_KEY": api_key,
            "WORKOS_CLIENT_ID": client_id,
            "WORKOS_COOKIE_PASSWORD": cookie_password,
            "APP_SIGNING_SECRET": app_signing_secret,
        }.items()
        if not value
    ]
    if missing:
        raise ConfigError(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". See .env.example."
        )

    if len(str(app_signing_secret)) < 32:
        raise ConfigError(
            "APP_SIGNING_SECRET must be at least 32 characters. Generate one with: "
            ".venv/bin/python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )

    try:
        Fernet(str(cookie_password).encode("utf-8"))
    except Exception as exc:
        raise ConfigError(
            "WORKOS_COOKIE_PASSWORD must be a Fernet key. Generate one with: "
            ".venv/bin/python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        ) from exc

    host = _env("HOST", env_file_values, "127.0.0.1") or "127.0.0.1"
    port_raw = _env("PORT", env_file_values, "8000") or "8000"
    app_url = _env("APP_URL", env_file_values, f"http://{host}:{port_raw}") or f"http://{host}:{port_raw}"
    secure_default = app_url.startswith("https://")
    mail_db_pool_min_size = _int_env("MAIL_DB_POOL_MIN_SIZE", env_file_values, 1)
    mail_db_pool_max_size = _int_env("MAIL_DB_POOL_MAX_SIZE", env_file_values, 10)
    if mail_db_pool_max_size < mail_db_pool_min_size:
        raise ConfigError("MAIL_DB_POOL_MAX_SIZE must be greater than or equal to MAIL_DB_POOL_MIN_SIZE.")
    mail_parse_ocr_max_regions = _int_env("MAIL_PARSE_OCR_MAX_REGIONS", env_file_values, OCR_MAX_REGIONS)
    mail_parse_ocr_max_document_pages = _optional_int_env(
        "MAIL_PARSE_OCR_MAX_DOCUMENT_PAGES",
        env_file_values,
        OCR_MAX_DOCUMENT_PAGES,
    )
    mail_token_encryption_key = _env("MAIL_TOKEN_ENCRYPTION_KEY", env_file_values)
    if mail_token_encryption_key:
        try:
            Fernet(str(mail_token_encryption_key).encode("utf-8"))
        except Exception as exc:
            raise ConfigError(
                "MAIL_TOKEN_ENCRYPTION_KEY must be a Fernet key. Generate one with: "
                ".venv/bin/python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            ) from exc

    return AppConfig(
        workos_api_key=str(api_key),
        workos_client_id=str(client_id),
        workos_cookie_password=str(cookie_password),
        csrf_secret=_derive_signing_secret(str(app_signing_secret), "csrf"),
        otp_email_cookie_secret=_derive_signing_secret(str(app_signing_secret), "otp-email-cookie"),
        session_metadata_cookie_secret=_derive_signing_secret(
            str(app_signing_secret),
            "session-metadata-cookie",
        ),
        email_verification_cookie_secret=_derive_signing_secret(
            str(app_signing_secret),
            "email-verification-cookie",
        ),
        host=host,
        port=int(port_raw),
        app_url=app_url.rstrip("/"),
        session_cookie_name=_env("SESSION_COOKIE_NAME", env_file_values, "zamp_session") or "zamp_session",
        session_metadata_cookie_name=_env(
            "SESSION_METADATA_COOKIE_NAME",
            env_file_values,
            "zamp_session_meta",
        )
        or "zamp_session_meta",
        session_max_age_seconds=_int_env("SESSION_MAX_AGE_SECONDS", env_file_values, 60 * 60 * 24 * 7),
        otp_email_cookie_name=_env("OTP_EMAIL_COOKIE_NAME", env_file_values, "zamp_otp_email") or "zamp_otp_email",
        email_verification_cookie_name=_env(
            "EMAIL_VERIFICATION_COOKIE_NAME",
            env_file_values,
            "zamp_email_verification",
        )
        or "zamp_email_verification",
        csrf_cookie_name=_env("CSRF_COOKIE_NAME", env_file_values, "zamp_csrf") or "zamp_csrf",
        cookie_secure=_bool_env(_env("COOKIE_SECURE", env_file_values), secure_default),
        database_url=_env("DATABASE_URL", env_file_values),
        mail_db_pool_min_size=mail_db_pool_min_size,
        mail_db_pool_max_size=mail_db_pool_max_size,
        mail_token_encryption_key=mail_token_encryption_key,
        mail_pdf_storage_dir=_env("MAIL_PDF_STORAGE_DIR", env_file_values, "./storage/mail_pdfs")
        or "./storage/mail_pdfs",
        mail_frontend_redirect_url=_env(
            "MAIL_FRONTEND_REDIRECT_URL",
            env_file_values,
            f"{app_url.rstrip('/')}/dashboard",
        )
        or f"{app_url.rstrip('/')}/dashboard",
        google_oauth_client_id=_env("GOOGLE_OAUTH_CLIENT_ID", env_file_values),
        google_oauth_client_secret=_env("GOOGLE_OAUTH_CLIENT_SECRET", env_file_values),
        gmail_pubsub_topic=_env("GMAIL_PUBSUB_TOPIC", env_file_values),
        gmail_pubsub_subscription=_env("GMAIL_PUBSUB_SUBSCRIPTION", env_file_values),
        gmail_pubsub_oidc_audience=_env("GMAIL_PUBSUB_OIDC_AUDIENCE", env_file_values),
        gmail_pubsub_oidc_service_account_email=_env(
            "GMAIL_PUBSUB_OIDC_SERVICE_ACCOUNT_EMAIL",
            env_file_values,
        ),
        gmail_webhook_secret=_env("GMAIL_WEBHOOK_SECRET", env_file_values),
        microsoft_client_id=_env("MICROSOFT_CLIENT_ID", env_file_values),
        microsoft_client_secret=_env("MICROSOFT_CLIENT_SECRET", env_file_values),
        microsoft_tenant_id=_env("MICROSOFT_TENANT_ID", env_file_values, "common") or "common",
        mail_parse_ocr_max_regions=mail_parse_ocr_max_regions,
        mail_parse_ocr_max_document_pages=mail_parse_ocr_max_document_pages,
    )
