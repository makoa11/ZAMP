from __future__ import annotations

import json
import hmac
import html as html_lib
import time
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

from workos._errors import EmailVerificationRequiredError
from workos.session import (
    AuthenticateWithSessionCookieSuccessResponse,
    RefreshWithSessionCookieSuccessResponse,
)

from .config import AppConfig, ConfigError
from .cookies import build_cookie, clear_cookie, parse_cookie_header
from .invoice_generator import (
    generate_invoice,
    generate_invoice_samples,
    paper_options,
    template_options,
)
from .invoice_pdf import render_invoice_pdf
from .mail_service import MailIntegration
from .mail_store import MailIntegrationError
from .mail_webhook_auth import WebhookAuthenticationError, verify_google_oidc_token
from .security import generate_csrf_token, sign_value, unsign_value, valid_signed_pair
from .templates import dashboard_page, error_page, invoice_samples_page, login_page, signup_page
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
SENSITIVE_QUERY_PARAMETERS = {"secret"}


def _redact_query_parameters(target: str) -> str:
    parsed = urlparse(target)
    if not parsed.query:
        return target
    query = [
        (key, "REDACTED" if key.lower() in SENSITIVE_QUERY_PARAMETERS else value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(query, doseq=True),
            parsed.fragment,
        )
    )


def _redact_log_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return " ".join(_redact_query_parameters(part) for part in value.split(" "))


def _display_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _humanize_token(value: Any) -> str:
    text = _display_value(value).replace("_", " ").replace("-", " ")
    return text.capitalize() if text else ""


def _frontend_copy(value: Any) -> str:
    text = _display_value(value)
    if not text:
        return ""
    replacements = (
        (
            "No simulated AP context record matched the parsed vendor, PO, invoice number, amount, or date.",
            "No simulated accounts payable context record matched the parsed vendor, purchase order, invoice number, amount due, or invoice date.",
        ),
        ("AP context", "accounts payable context"),
        ("AP review", "accounts payable review"),
        ("AP tolerance", "accounts payable tolerance"),
        ("AP expected amount", "accounts payable expected amount"),
        (" AP ", " accounts payable "),
    )
    for source, replacement in replacements:
        text = text.replace(source, replacement)
    return text


def _source_type_label(value: Any) -> str:
    source_type = _display_value(value)
    labels = {
        "ap_context_records": "Accounts payable context records",
        "manifest": "Manifest",
    }
    return labels.get(source_type, _humanize_token(source_type))


def _badge_class(value: Any) -> str:
    text = _display_value(value).lower().replace("_", "-").replace(" ", "-")
    return "".join(character for character in text if character.isalnum() or character == "-")


def _format_amount(amount_value: Any, currency_value: Any = None) -> str:
    if amount_value in (None, ""):
        return ""
    try:
        amount = float(amount_value)
    except (TypeError, ValueError):
        return _display_value(amount_value)
    currency = _display_value(currency_value)
    if currency:
        return f"{currency} {amount:,.2f}"
    return f"{amount:,.2f}"


def _nested_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _field_value(field: Any, *keys: str) -> str:
    data = _nested_dict(field)
    for key in keys or ("value",):
        value = _display_value(data.get(key))
        if value:
            return value
    return ""


def _app_header_html(*, csrf_token: str, active: str) -> str:
    review_class = ' class="active"' if active == "review" else ""
    settings_class = ' class="active"' if active == "settings" else ""
    review_current = ' aria-current="page"' if active == "review" else ""
    settings_current = ' aria-current="page"' if active == "settings" else ""
    return f"""
  <header class="app-header">
    <div class="app-brand">
      <div class="brand-mark app-brand-mark" aria-hidden="true">Z</div>
      <nav class="nav-links" aria-label="Primary">
        <a href="/dashboard"{review_class}{review_current}>Review</a>
        <a href="/settings"{settings_class}{settings_current}>Settings</a>
      </nav>
    </div>
    <form class="logout-form" method="post" action="/logout">
      <input type="hidden" name="_csrf" value="{html_lib.escape(csrf_token)}">
      <button class="secondary compact-button" type="submit">Log out</button>
    </form>
  </header>"""


class ZampHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        mail_integration: MailIntegration,
    ) -> None:
        self.mail_integration = mail_integration
        super().__init__(server_address, handler_class)

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            self.mail_integration.close()


class ZampRequestHandler(BaseHTTPRequestHandler):
    config: AppConfig
    auth: WorkOSAuthService
    timed_revoker: TimedSessionRevoker
    mail_integration: MailIntegration

    server_version = "ZAMPAuth/0.1"

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            self.close_connection = True

    def log_message(self, format: str, *args: Any) -> None:
        args = tuple(_redact_log_value(arg) for arg in args)
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
            self._handle_dashboard_get(parsed.query)
            return
        if parsed.path == "/settings":
            self._handle_settings_get(parsed.query)
            return
        if parsed.path == "/invoice-samples":
            self._handle_invoice_samples_get(parsed.query)
            return
        if parsed.path == "/invoice-samples.pdf":
            self._handle_invoice_samples_pdf_get(parsed.query)
            return
        if parsed.path == "/api/session":
            self._handle_api_session()
            return
        if parsed.path == "/api/invoices/samples":
            self._handle_invoice_samples_api_get(parsed.query)
            return
        if parsed.path == "/api/invoices/samples.pdf":
            self._handle_invoice_samples_pdf_get(parsed.query)
            return
        if parsed.path == "/api/mail/accounts":
            self._handle_mail_accounts_get()
            return
        if parsed.path.startswith("/api/mail/pdfs/"):
            self._handle_mail_pdf_get(parsed)
            return
        if parsed.path == "/api/mail/invoices":
            self._handle_mail_invoices_get(parsed.query)
            return
        if parsed.path.startswith("/api/mail/invoices/"):
            self._handle_mail_invoice_detail_get(parsed)
            return
        if parsed.path == "/api/mail/invoice-patterns":
            self._handle_mail_invoice_patterns_get()
            return
        if parsed.path.startswith("/api/mail/oauth/") and parsed.path.endswith("/callback"):
            self._handle_mail_oauth_callback(parsed)
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
        if parsed.path.startswith("/api/mail/oauth/") and parsed.path.endswith("/start"):
            self._handle_mail_oauth_start(parsed)
            return
        if parsed.path == "/api/mail/invoice-patterns/suggest":
            self._handle_mail_invoice_pattern_suggest_post()
            return
        if parsed.path == "/api/mail/invoice-patterns":
            self._handle_mail_invoice_patterns_post()
            return
        if parsed.path == "/webhooks/gmail/pubsub":
            self._handle_gmail_pubsub_webhook(parsed)
            return
        if parsed.path == "/webhooks/outlook":
            self._handle_outlook_webhook(parsed)
            return
        self._send_html(HTTPStatus.NOT_FOUND, error_page(404, "Page not found."))

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/mail/accounts/"):
            self._handle_mail_account_delete(parsed)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Page not found."})

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

    def _handle_dashboard_get(self, query: str) -> None:
        session, set_session_cookie, clear_cookies = self._session()
        if not session:
            self._redirect("/login", cookies=clear_cookies)
            return
        cookies = self._cookies()
        csrf, csrf_cookie = self._csrf_cookie(cookies)
        response_cookies = [cookie for cookie in [set_session_cookie, csrf_cookie] if cookie]

        payload = self._session_payload(session)
        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        owner_user_id = user.get("id")
        if not isinstance(owner_user_id, str) or not owner_user_id:
            self._send_html(
                HTTPStatus.UNAUTHORIZED,
                error_page(401, "Signed-in user could not be resolved."),
                cookies=response_cookies,
            )
            return

        mail_notice = None
        mail_notice_kind = "success"
        try:
            review_items = self.mail_integration.list_invoice_review_items(
                owner_user_id=owner_user_id,
                limit=50,
            )
        except (ConfigError, MailIntegrationError) as exc:
            review_items = []
            mail_notice = str(exc)
            mail_notice_kind = "error"

        selected_pdf_id = parse_qs(query).get("pdf_id", [""])[0]
        selected_item = next(
            (
                item
                for item in review_items
                if str(item.get("pdf_file_id") or "") == selected_pdf_id
            ),
            review_items[0] if review_items else None,
        )

        selected_invoice = None
        selected_pdf_file_id = _safe_int(selected_item.get("pdf_file_id")) if selected_item else _safe_int(selected_pdf_id)
        if selected_pdf_file_id is not None:
            try:
                selected_invoice = self.mail_integration.get_invoice(
                    owner_user_id=owner_user_id,
                    pdf_file_id=selected_pdf_file_id,
                )
            except (ConfigError, MailIntegrationError) as exc:
                mail_notice = str(exc)
                mail_notice_kind = "error"

        queue_items_html = self._review_queue_html(review_items, selected_item)
        evidence_html = self._review_evidence_html(selected_item, selected_invoice)
        queue_count = len(review_items)
        queue_label = "invoice" if queue_count == 1 else "invoices"
        notice_html = (
            f'<div class="queue-notice"><div class="notice notice-{html_lib.escape(mail_notice_kind)}" role="status">'
            f"{html_lib.escape(mail_notice)}</div></div>"
            if mail_notice
            else ""
        )

        html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Dashboard - ZAMP</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body class="app-body">
{_app_header_html(csrf_token=csrf, active="review")}

  <main class="workbench" aria-label="Invoice review workspace">
    <aside class="queue-pane" aria-label="Invoice review queue">
      <div class="queue-pane-header">
        <p class="eyebrow">Review queue</p>
        <h1>Invoices</h1>
        <p>{queue_count} {queue_label} pending</p>
      </div>
      {notice_html}
      <div class="queue-list">
      {queue_items_html}
      </div>
    </aside>

    <section class="evidence-pane" aria-label="Invoice evidence">
      {evidence_html}
    </section>
  </main>
</body>
</html>"""

        self._send_html(
            HTTPStatus.OK,
            html,
            cookies=response_cookies,
        )

    def _review_queue_html(
        self,
        review_items: list[dict[str, Any]],
        selected_item: dict[str, Any] | None,
    ) -> str:
        if not review_items:
            return '<div class="empty-state">No action needed. You are all caught up.</div>'
        selected_pdf_id = selected_item.get("pdf_file_id") if selected_item else None
        return "\n".join(
            self._review_queue_item_html(item, active=item.get("pdf_file_id") == selected_pdf_id)
            for item in review_items
        )

    def _review_queue_item_html(self, item: dict[str, Any], *, active: bool) -> str:
        title = _display_value(item.get("invoice_number") or item.get("filename"))
        amount = _display_value(item.get("amount")) or _format_amount(
            item.get("amount_due"),
            item.get("currency"),
        )
        vendor = _display_value(item.get("vendor") or item.get("sender") or item.get("account_email"))
        subject = _display_value(item.get("subject"))
        received = _display_value(item.get("received_date") or item.get("received_at"))
        decision = _display_value(item.get("decision")) or _display_value(item.get("parse_status"))
        decision_label = _humanize_token(decision) or "Pending"
        confidence = _display_value(item.get("confidence"))
        pdf_file_id = html_lib.escape(str(item.get("pdf_file_id") or ""))
        active_attr = ' aria-current="page"' if active else ""
        amount_label = amount or "Review"
        title_label = title or "Untitled invoice"
        decision_class = html_lib.escape(_badge_class(decision_label))
        confidence_html = (
            f"<span>{html_lib.escape(confidence)} confidence</span>"
            if confidence
            else ""
        )
        return f"""
        <a class="queue-item{' active' if active else ''}" href="/dashboard?pdf_id={pdf_file_id}"{active_attr}>
          <div class="queue-header">
            <span class="queue-vendor">{html_lib.escape(vendor or "Unknown vendor")}</span>
            <span class="queue-amount">{html_lib.escape(amount_label)}</span>
          </div>
          <div class="queue-title">{html_lib.escape(title_label)}</div>
          <div class="queue-subject">{html_lib.escape(subject)}</div>
          <div class="queue-meta">
            <span>{html_lib.escape(received)}</span>
            <span class="decision-badge status-{decision_class}">{html_lib.escape(decision_label)}</span>
            {confidence_html}
          </div>
        </a>"""

    def _review_evidence_html(
        self,
        selected_item: dict[str, Any] | None,
        invoice: dict[str, Any] | None,
    ) -> str:
        if not selected_item and not invoice:
            return """
      <div class="evidence-empty">
        <div>
          <p class="eyebrow">Invoice preview</p>
          <strong>No invoice selected</strong>
        </div>
      </div>"""

        detail = invoice if isinstance(invoice, dict) else {}
        message = _nested_dict(detail.get("message"))
        attachment = _nested_dict(detail.get("attachment"))
        normalized_invoice = _nested_dict(detail.get("normalized_invoice"))
        decision = _nested_dict(detail.get("decision"))
        raw_parse = _nested_dict(detail.get("raw_parse"))
        audit = _nested_dict(detail.get("audit"))
        ap_context = _nested_dict(detail.get("ap_context"))
        checks = detail.get("checks") if isinstance(detail.get("checks"), list) else []

        item = selected_item or {}
        pdf_file_id = _safe_int(detail.get("pdf_file_id")) or _safe_int(item.get("pdf_file_id"))
        filename = _display_value(attachment.get("filename") or item.get("filename"))
        subject = _display_value(message.get("subject") or item.get("subject"))
        sender = _display_value(message.get("sender") or item.get("sender"))
        received = _display_value(message.get("received_at") or item.get("received_date") or item.get("received_at"))
        decision_value = _display_value(decision.get("decision") or item.get("decision"))
        confidence = _display_value(decision.get("confidence") or item.get("confidence"))
        decision_label = _humanize_token(decision_value) or "Pending"
        decision_class = html_lib.escape(_badge_class(decision_label))
        overlay_url = (
            f"/api/mail/invoices/{pdf_file_id}/overlay.pdf?boxes=all"
            if pdf_file_id is not None
            else ""
        )
        meta_html = self._detail_facts_html(
            [
                ("From", sender),
                ("Received", received),
                ("Attachment", filename),
            ]
        )
        normalized_html = self._normalized_invoice_html(normalized_invoice, raw_parse, item)
        decision_html = self._decision_summary_html(decision, checks, item)
        checks_html = self._decision_checks_html(checks)
        audit_html = self._audit_reasoning_html(audit, ap_context)
        pdf_html = (
            f'<iframe class="pdf-viewer" src="{html_lib.escape(overlay_url)}" '
            f'title="{html_lib.escape(filename or "Invoice overlay")}"></iframe>'
            if overlay_url
            else '<div class="evidence-empty"><strong>Overlay unavailable</strong></div>'
        )
        return f"""
      <div class="evidence-header">
        <div class="evidence-title">
          <strong>{html_lib.escape(filename or "Invoice detail")}</strong>
          <span>{html_lib.escape(subject)}</span>
        </div>
        <div class="actions review-actions">
          <span class="decision-badge status-{decision_class}">{html_lib.escape(decision_label)}</span>
          {f'<span class="confidence-badge">{html_lib.escape(confidence)} confidence</span>' if confidence else ''}
        </div>
      </div>
      <div class="evidence-content">
        <div class="review-detail-panel">
          {decision_html}
          {normalized_html}
          {checks_html}
          {audit_html}
          <section class="detail-section">
            <h2>Source</h2>
            {meta_html}
          </section>
        </div>
        <div class="pdf-stage">
          {pdf_html}
        </div>
      </div>"""

    def _decision_summary_html(
        self,
        decision: dict[str, Any],
        checks: list[Any],
        item: dict[str, Any],
    ) -> str:
        decision_value = _display_value(decision.get("decision") or item.get("decision"))
        summary = _frontend_copy(decision.get("summary")) or _frontend_copy(item.get("next_action"))
        next_action = _frontend_copy(decision.get("next_action") or item.get("next_action"))
        failed_or_review = sum(
            1
            for check in checks
            if isinstance(check, dict) and _display_value(check.get("status")).lower() in {"fail", "review"}
        )
        facts = self._detail_facts_html(
            [
                ("Decision", _humanize_token(decision_value) or "Pending"),
                ("Attention checks", str(failed_or_review) if checks else ""),
            ]
        )
        body = facts
        if summary:
            body += f'<p class="detail-summary">{html_lib.escape(summary)}</p>'
        if next_action:
            body += f'<p class="detail-next-action">{html_lib.escape(next_action)}</p>'
        return f"""
          <section class="detail-section decision-section">
            <h2>Decision</h2>
            {body}
          </section>"""

    def _normalized_invoice_html(
        self,
        normalized_invoice: dict[str, Any],
        raw_parse: dict[str, Any],
        item: dict[str, Any],
    ) -> str:
        amount = self._normalized_amount(normalized_invoice) or _format_amount(
            item.get("amount_due"),
            item.get("currency"),
        )
        parse_status = _display_value(raw_parse.get("status") or normalized_invoice.get("parser_status") or item.get("parse_status"))
        parser_method = _display_value(raw_parse.get("parser_method"))
        page_count = _display_value(raw_parse.get("page_count"))
        facts = self._detail_facts_html(
            [
                ("Vendor", _field_value(normalized_invoice.get("vendor"), "name", "value", "raw") or item.get("vendor")),
                ("Invoice #", _field_value(normalized_invoice.get("invoice_number"), "value") or item.get("invoice_number")),
                ("Purchase order", _field_value(normalized_invoice.get("purchase_order"), "value") or item.get("purchase_order")),
                ("Issue date", _field_value(normalized_invoice.get("issue_date"), "value")),
                ("Due date", _field_value(normalized_invoice.get("due_date"), "value")),
                ("Amount due", amount),
                ("Parse status", _humanize_token(parse_status)),
                ("Parser", parser_method),
                ("Pages", page_count),
            ]
        )
        return f"""
          <section class="detail-section">
            <h2>Normalized Invoice</h2>
            {facts}
          </section>"""

    def _decision_checks_html(self, checks: list[Any]) -> str:
        if not checks:
            return """
          <section class="detail-section">
            <h2>Decision Checks</h2>
            <p class="detail-muted">No checks recorded yet.</p>
          </section>"""
        rows = []
        for check in checks:
            if not isinstance(check, dict):
                continue
            check_id = _display_value(check.get("id"))
            status = _display_value(check.get("status"))
            summary = _frontend_copy(check.get("summary"))
            status_label = _humanize_token(status) or "Unknown"
            status_class = html_lib.escape(_badge_class(status_label))
            rows.append(
                f"""
              <li class="check-row">
                <div>
                  <strong>{html_lib.escape(_humanize_token(check_id) or check_id)}</strong>
                  <span>{html_lib.escape(summary)}</span>
                </div>
                <span class="decision-badge status-{status_class}">{html_lib.escape(status_label)}</span>
              </li>"""
            )
        checks_html = "\n".join(rows) if rows else '<p class="detail-muted">No checks recorded yet.</p>'
        return f"""
          <section class="detail-section">
            <h2>Decision Checks</h2>
            <ul class="check-list">
              {checks_html}
            </ul>
          </section>"""

    def _audit_reasoning_html(self, audit: dict[str, Any], ap_context: dict[str, Any]) -> str:
        context_available = ap_context.get("available")
        context_label = (
            "Available"
            if context_available is True
            else "Missing"
            if context_available is False
            else ""
        )
        source = _nested_dict(ap_context.get("source") or audit.get("context_source"))
        facts = self._detail_facts_html(
            [
                ("Accounts payable context", context_label),
                ("Reason", _frontend_copy(ap_context.get("reason"))),
                ("Scenario", ap_context.get("scenario") or audit.get("context_scenario")),
                ("Source", _source_type_label(source.get("type"))),
                ("Record", source.get("record_id") or source.get("source_key")),
                ("Normalized vendor", audit.get("normalized_vendor")),
                ("Normalized invoice #", audit.get("normalized_invoice_number")),
                ("Purchase order", audit.get("purchase_order")),
                ("Audit amount", audit.get("amount_due")),
            ]
        )
        return f"""
          <section class="detail-section">
            <h2>Audit Reasoning</h2>
            {facts}
          </section>"""

    def _detail_facts_html(self, rows: list[tuple[str, Any]]) -> str:
        items = []
        for label, value in rows:
            text = _display_value(value)
            if not text:
                continue
            items.append(
                f"""
              <div>
                <dt>{html_lib.escape(label)}</dt>
                <dd>{html_lib.escape(text)}</dd>
              </div>"""
            )
        if not items:
            return '<p class="detail-muted">No data recorded yet.</p>'
        return f"""
            <dl class="detail-facts">
              {"".join(items)}
            </dl>"""

    def _normalized_amount(self, normalized_invoice: dict[str, Any]) -> str:
        amount_due = _nested_dict(normalized_invoice.get("amount_due"))
        currency = amount_due.get("currency") or _field_value(normalized_invoice.get("currency"), "value")
        return _format_amount(amount_due.get("amount"), currency)

    def _handle_settings_get(self, query: str) -> None:
        session, set_session_cookie, clear_cookies = self._session()
        if not session:
            self._redirect("/login", cookies=clear_cookies)
            return
        cookies = self._cookies()
        csrf, csrf_cookie = self._csrf_cookie(cookies)
        response_cookies = [cookie for cookie in [set_session_cookie, csrf_cookie] if cookie]
        params = parse_qs(query)
        mail_notice = None
        mail_notice_kind = "success"
        if params.get("mail_connected"):
            provider = params.get("mail_connected", ["mail"])[0]
            mail_notice = f"{provider.title()} connected."
        elif params.get("mail_error"):
            mail_notice = params.get("mail_error", ["Mail connection failed."])[0]
            mail_notice_kind = "error"

        original_html = dashboard_page(
            csrf_token=csrf,
            session=self._session_payload(session),
            mail_notice=mail_notice,
            mail_notice_kind=mail_notice_kind,
        )

        import re

        app_header = _app_header_html(csrf_token=csrf, active="settings")

        html = original_html.replace('<body>', '<body class="app-body settings-body">\n' + app_header)
        html = html.replace('<main class="dashboard-shell">', '<main class="dashboard-shell settings-shell">')
        html = re.sub(r'<header class="topbar">.*?</header>', '', html, flags=re.DOTALL)
        html = re.sub(r'<section class="summary">.*?</section>', '', html, flags=re.DOTALL)
        html = re.sub(
            r'<section class="data-panel">\s*<h2>Session data.*?^  </section>',
            '',
            html,
            flags=re.DOTALL | re.MULTILINE,
        )
        html = html.replace('<title>Dashboard - ZAMP</title>', '<title>Settings - ZAMP</title>')

        self._send_html(
            HTTPStatus.OK,
            html,
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

    def _handle_invoice_samples_get(self, query: str) -> None:
        try:
            params = self._invoice_sample_params(query)
            samples = self._invoice_samples_from_params(params)
        except ValueError as exc:
            self._send_html(HTTPStatus.BAD_REQUEST, error_page(400, str(exc)))
            return
        self._send_html(
            HTTPStatus.OK,
            invoice_samples_page(
                samples=samples,
                papers=paper_options(),
                templates=template_options(),
                active_paper=params["paper"],
                active_template=params["template"],
                seed=params["seed"],
                count=params["count"],
            ),
        )

    def _handle_invoice_samples_api_get(self, query: str) -> None:
        try:
            params = self._invoice_sample_params(query)
            samples = self._invoice_samples_from_params(params)
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "samples": samples,
                "paper_options": paper_options(),
                "template_options": template_options(),
            },
        )

    def _handle_invoice_samples_pdf_get(self, query: str) -> None:
        try:
            params = self._invoice_sample_params(query)
            samples = self._invoice_samples_from_params(params)
            content = render_invoice_pdf(samples)
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_binary(
            HTTPStatus.OK,
            content,
            content_type="application/pdf",
            headers={
                "Content-Disposition": 'inline; filename="invoice-samples.pdf"',
            },
        )

    def _invoice_sample_params(self, query: str) -> dict[str, Any]:
        params = parse_qs(query)
        paper = params.get("paper", ["a4"])[0] or "a4"
        template = params.get("template", [""])[0] or None
        try:
            count = int(params.get("count", ["1" if template else "15"])[0])
            seed = int(params.get("seed", ["1000"])[0])
        except ValueError as exc:
            raise ValueError("count and seed must be integers.") from exc
        if count < 1 or count > 60:
            raise ValueError("count must be between 1 and 60.")
        if seed < 1:
            raise ValueError("seed must be greater than 0.")
        return {
            "paper": paper,
            "template": template,
            "count": count,
            "seed": seed,
        }

    def _invoice_samples_from_params(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        template = params["template"]
        if template:
            return [
                generate_invoice(
                    template_slug=template,
                    paper_slug=params["paper"],
                    seed=params["seed"] + (index * 97),
                    variation_index=index,
                )
                for index in range(params["count"])
            ]
        return generate_invoice_samples(
            paper_slug=params["paper"],
            count=params["count"],
            seed=params["seed"],
        )

    def _handle_mail_oauth_start(self, parsed: Any) -> None:
        context = self._authenticated_api_user()
        if not context:
            return
        owner_user_id, cookies = context
        provider = self._provider_from_oauth_path(parsed.path, suffix="start")
        if not provider:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unsupported mail provider."}, cookies=cookies)
            return
        try:
            body = self._json_body()
            redirect_after = body.get("redirect_after") if isinstance(body.get("redirect_after"), str) else None
            result = self.mail_integration.start_oauth(
                provider=provider,
                owner_user_id=owner_user_id,
                redirect_after=redirect_after,
            )
        except (ConfigError, MailIntegrationError, ValueError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, cookies=cookies)
            return
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)}, cookies=cookies)
            return
        self._send_json(HTTPStatus.OK, result, cookies=cookies)

    def _handle_mail_oauth_callback(self, parsed: Any) -> None:
        provider = self._provider_from_oauth_path(parsed.path, suffix="callback")
        if not provider:
            self._send_html(HTTPStatus.NOT_FOUND, error_page(404, "Page not found."))
            return
        params = parse_qs(parsed.query)
        if params.get("error"):
            self._redirect(self._mail_frontend_redirect({"mail_error": "oauth_denied"}))
            return
        state = params.get("state", [""])[0]
        code = params.get("code", [""])[0]
        if not state or not code:
            self._redirect(self._mail_frontend_redirect({"mail_error": "missing_oauth_code"}))
            return
        session, set_session_cookie, clear_cookies = self._session()
        if not session:
            self._redirect(
                self._mail_frontend_redirect({"mail_error": "oauth_session_required"}),
                cookies=clear_cookies,
            )
            return
        payload = self._session_payload(session)
        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        owner_user_id = user.get("id")
        if not isinstance(owner_user_id, str) or not owner_user_id:
            self._redirect(self._mail_frontend_redirect({"mail_error": "oauth_session_required"}))
            return
        try:
            location = self.mail_integration.complete_oauth(
                provider=provider,
                state=state,
                code=code,
                owner_user_id=owner_user_id,
            )
        except Exception as exc:
            self.log_message("Mail OAuth callback failed: %s", exc)
            self._send_html(HTTPStatus.BAD_REQUEST, error_page(400, "Mail OAuth callback failed."))
            return
        self._redirect(location, cookies=[set_session_cookie] if set_session_cookie else None)

    def _handle_mail_accounts_get(self) -> None:
        context = self._authenticated_api_user()
        if not context:
            return
        owner_user_id, cookies = context
        try:
            accounts = self.mail_integration.list_accounts(owner_user_id=owner_user_id)
        except (ConfigError, MailIntegrationError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, cookies=cookies)
            return
        self._send_json(HTTPStatus.OK, {"accounts": accounts}, cookies=cookies)

    def _handle_mail_pdf_get(self, parsed: Any) -> None:
        context = self._authenticated_api_user()
        if not context:
            return
        owner_user_id, cookies = context
        pdf_file_id_raw = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        try:
            pdf_file_id = int(pdf_file_id_raw)
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid PDF file id."}, cookies=cookies)
            return

        try:
            pdf_file = self.mail_integration.get_invoice_pdf_file(
                owner_user_id=owner_user_id,
                pdf_file_id=pdf_file_id,
            )
        except (ConfigError, MailIntegrationError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, cookies=cookies)
            return
        if not pdf_file:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "PDF file not found."}, cookies=cookies)
            return

        pdf_path = pdf_file["path"]
        try:
            content = pdf_path.read_bytes()
        except FileNotFoundError:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "PDF file is missing from storage."}, cookies=cookies)
            return

        filename = str(pdf_file.get("filename") or f"invoice-{pdf_file_id}.pdf").replace('"', "")
        self._send_binary(
            HTTPStatus.OK,
            content,
            content_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
            cookies=cookies,
        )

    def _handle_mail_invoices_get(self, query: str) -> None:
        context = self._authenticated_api_user()
        if not context:
            return
        owner_user_id, cookies = context
        params = parse_qs(query)
        try:
            limit = int(params.get("limit", ["100"])[0])
            offset = int(params.get("offset", ["0"])[0])
            if limit < 1 or limit > 500:
                raise ValueError("limit must be between 1 and 500.")
            if offset < 0:
                raise ValueError("offset must be greater than or equal to 0.")
            invoices = self.mail_integration.list_invoices(
                owner_user_id=owner_user_id,
                limit=limit,
                offset=offset,
            )
        except (ConfigError, MailIntegrationError, ValueError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, cookies=cookies)
            return
        self._send_json(HTTPStatus.OK, {"invoices": invoices}, cookies=cookies)

    def _handle_mail_invoice_detail_get(self, parsed: Any) -> None:
        context = self._authenticated_api_user()
        if not context:
            return
        owner_user_id, cookies = context
        parts = parsed.path.strip("/").split("/")
        if len(parts) not in {4, 5} or parts[:3] != ["api", "mail", "invoices"]:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Page not found."}, cookies=cookies)
            return
        try:
            pdf_file_id = int(parts[3])
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid PDF file id."}, cookies=cookies)
            return

        if len(parts) == 5:
            if parts[4] != "overlay.pdf":
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Page not found."}, cookies=cookies)
                return
            params = parse_qs(parsed.query)
            box_mode = params.get("boxes", ["parsed"])[0] or "parsed"
            try:
                content = self.mail_integration.invoice_overlay_pdf(
                    owner_user_id=owner_user_id,
                    pdf_file_id=pdf_file_id,
                    box_mode=box_mode,
                )
            except (ConfigError, MailIntegrationError, ValueError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, cookies=cookies)
                return
            if content is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Invoice PDF was not found."}, cookies=cookies)
                return
            self._send_binary(
                HTTPStatus.OK,
                content,
                content_type="application/pdf",
                cookies=cookies,
                headers={
                    "Content-Disposition": f'inline; filename="mail-invoice-{pdf_file_id}-overlay.pdf"',
                },
            )
            return

        try:
            invoice = self.mail_integration.get_invoice(
                owner_user_id=owner_user_id,
                pdf_file_id=pdf_file_id,
            )
        except (ConfigError, MailIntegrationError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, cookies=cookies)
            return
        if invoice is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Invoice was not found."}, cookies=cookies)
            return
        self._send_json(HTTPStatus.OK, invoice, cookies=cookies)

    def _handle_mail_invoice_patterns_get(self) -> None:
        context = self._authenticated_api_user()
        if not context:
            return
        owner_user_id, cookies = context
        try:
            patterns = self.mail_integration.get_invoice_match_patterns(owner_user_id=owner_user_id)
        except (ConfigError, MailIntegrationError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, cookies=cookies)
            return
        self._send_json(HTTPStatus.OK, {"patterns": patterns}, cookies=cookies)

    def _handle_mail_invoice_patterns_post(self) -> None:
        context = self._authenticated_api_user()
        if not context:
            return
        owner_user_id, cookies = context
        try:
            body = self._json_body(max_bytes=32 * 1024)
            patterns_value = body.get("patterns")
            if isinstance(patterns_value, str):
                patterns = patterns_value.splitlines()
            elif isinstance(patterns_value, list) and all(isinstance(item, str) for item in patterns_value):
                patterns = patterns_value
            else:
                raise ValueError("patterns must be a string array or newline-separated string.")
            saved = self.mail_integration.update_invoice_match_patterns(
                owner_user_id=owner_user_id,
                patterns=patterns,
            )
        except (ConfigError, MailIntegrationError, ValueError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, cookies=cookies)
            return
        self._send_json(HTTPStatus.OK, {"patterns": saved}, cookies=cookies)

    def _handle_mail_invoice_pattern_suggest_post(self) -> None:
        context = self._authenticated_api_user()
        if not context:
            return
        _, cookies = context
        try:
            body = self._json_body(max_bytes=4 * 1024)
            filename = body.get("filename")
            if not isinstance(filename, str):
                raise ValueError("filename must be a string.")
            pattern = self.mail_integration.suggest_invoice_match_pattern(filename=filename)
        except (ConfigError, MailIntegrationError, ValueError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, cookies=cookies)
            return
        self._send_json(HTTPStatus.OK, {"pattern": pattern}, cookies=cookies)

    def _handle_mail_account_delete(self, parsed: Any) -> None:
        context = self._authenticated_api_user()
        if not context:
            return
        owner_user_id, cookies = context
        account_id_raw = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        try:
            account_id = int(account_id_raw)
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid account id."}, cookies=cookies)
            return
        try:
            disconnected = self.mail_integration.disconnect_account(
                owner_user_id=owner_user_id,
                account_id=account_id,
            )
        except (ConfigError, MailIntegrationError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, cookies=cookies)
            return
        self._send_json(
            HTTPStatus.OK if disconnected else HTTPStatus.NOT_FOUND,
            {"disconnected": disconnected},
            cookies=cookies,
        )

    def _handle_gmail_pubsub_webhook(self, parsed: Any) -> None:
        if not self._valid_gmail_pubsub_auth():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Invalid Gmail webhook authentication."})
            return
        try:
            payload = self._json_body(max_bytes=1024 * 1024)
            result = self.mail_integration.handle_gmail_pubsub(
                payload=payload,
                subscription=payload.get("subscription") if isinstance(payload.get("subscription"), str) else None,
            )
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, result)

    def _handle_outlook_webhook(self, parsed: Any) -> None:
        params = parse_qs(parsed.query)
        validation_token = params.get("validationToken", [""])[0]
        if validation_token:
            self._send_text(HTTPStatus.OK, validation_token, content_type="text/plain; charset=utf-8")
            return
        try:
            payload = self._json_body(max_bytes=1024 * 1024)
            result = self.mail_integration.handle_outlook_notifications(payload=payload)
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.ACCEPTED, result)

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

    def _authenticated_api_user(self) -> tuple[str, list[str]] | None:
        session, set_session_cookie, clear_cookies = self._session()
        if not session:
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"authenticated": False},
                cookies=clear_cookies,
            )
            return None
        payload = self._session_payload(session)
        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        user_id = user.get("id")
        if not isinstance(user_id, str) or not user_id:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"authenticated": False})
            return None
        return user_id, [set_session_cookie] if set_session_cookie else []

    def _provider_from_oauth_path(self, path: str, *, suffix: str) -> str | None:
        parts = path.strip("/").split("/")
        if len(parts) != 5 or parts[:3] != ["api", "mail", "oauth"] or parts[4] != suffix:
            return None
        provider = parts[3]
        return provider if provider in {"gmail", "outlook"} else None

    def _mail_frontend_redirect(self, params: dict[str, str]) -> str:
        separator = "&" if "?" in self.config.mail_frontend_redirect_url else "?"
        return self.config.mail_frontend_redirect_url + separator + urlencode(params)

    def _valid_gmail_pubsub_auth(self) -> bool:
        authorization = self.headers.get("Authorization")
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
            audience = getattr(self.config, "gmail_pubsub_oidc_audience", None) or (
                f"{self.config.app_url.rstrip('/')}/webhooks/gmail/pubsub"
            )
            try:
                verify_google_oidc_token(
                    token,
                    audience=audience,
                    service_account_email=getattr(
                        self.config,
                        "gmail_pubsub_oidc_service_account_email",
                        None,
                    ),
                )
                return True
            except WebhookAuthenticationError:
                return False

        expected_secret = getattr(self.config, "gmail_webhook_secret", None)
        if not expected_secret:
            return False
        candidates = [
            self.headers.get("X-Zamp-Gmail-Webhook-Secret"),
            self.headers.get("X-Zamp-Webhook-Secret"),
        ]
        return any(
            hmac.compare_digest(candidate, expected_secret)
            for candidate in candidates
            if isinstance(candidate, str)
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

    def _json_body(self, *, max_bytes: int = 256 * 1024) -> dict[str, Any]:
        try:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError as exc:
            raise ValueError("Invalid Content-Length.") from exc
        if content_length > max_bytes:
            raise ValueError("Request body is too large.")
        raw = self.rfile.read(content_length)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Request body must be JSON.") from exc
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object.")
        return data

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
        encoded = json.dumps(content, sort_keys=True, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(encoded)

    def _send_binary(
        self,
        status: HTTPStatus,
        content: bytes,
        *,
        content_type: str,
        cookies: list[str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(content)

    def _send_text(
        self,
        status: HTTPStatus,
        content: str,
        *,
        content_type: str = "text/plain; charset=utf-8",
    ) -> None:
        encoded = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
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


def create_server(config: AppConfig) -> ZampHTTPServer:
    auth = WorkOSAuthService(config)
    timed_revoker = TimedSessionRevoker(auth, config.session_max_age_seconds)
    mail_integration = MailIntegration(config)

    class Handler(ZampRequestHandler):
        pass

    Handler.config = config
    Handler.auth = auth
    Handler.timed_revoker = timed_revoker
    Handler.mail_integration = mail_integration
    return ZampHTTPServer((config.host, config.port), Handler, mail_integration)


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
