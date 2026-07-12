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
from .invoice_showcase import (
    SHOWCASE_DOCUMENTS,
    render_showcase_page_png,
    render_showcase_pdf,
    showcase_document,
)
from .mail_service import MailIntegration
from .mail_store import MailIntegrationError
from .mail_webhook_auth import WebhookAuthenticationError, verify_google_oidc_token
from .security import generate_csrf_token, sign_value, unsign_value, valid_signed_pair
from .templates import (
    dashboard_page,
    error_page,
    invoice_samples_page,
    invoice_showcase_page,
    login_page,
    signup_page,
)
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
REVIEW_DECISIONS = {
    "needs_review",
    "request_missing_info",
    "flag_possible_duplicate",
    "block_or_escalate",
    "apply_credit_or_route_review",
}


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


def _invoice_needs_review(item: dict[str, Any]) -> bool:
    decision = _display_value(item.get("decision")).lower().replace("-", "_")
    return not decision or decision in REVIEW_DECISIONS


def _invoice_received_date(item: dict[str, Any]) -> date | None:
    value = _display_value(item.get("received_date") or item.get("received_at"))
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _filter_invoice_items(
    items: list[dict[str, Any]],
    *,
    review_filter: str,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    filtered = items
    if review_filter == "needs_review":
        filtered = [item for item in filtered if _invoice_needs_review(item)]
    if date_from is not None or date_to is not None:
        filtered = [
            item
            for item in filtered
            if (received_date := _invoice_received_date(item)) is not None
            and (date_from is None or received_date >= date_from)
            and (date_to is None or received_date <= date_to)
        ]
    return filtered


def _query_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


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
      <a class="app-logo" href="/dashboard" aria-label="Zamp home">
        <span class="app-brand-mark" aria-hidden="true">
          <svg viewBox="0 0 32 32" role="presentation"><path d="M8 9.5h16L10 22.5h14"/><circle cx="23.5" cy="8.5" r="2.5"/></svg>
        </span>
        <span>Zamp</span>
      </a>
      <nav class="nav-links" aria-label="Primary">
        <a href="/dashboard"{review_class}{review_current}>Focus</a>
        <a href="/settings"{settings_class}{settings_current}>Settings</a>
      </nav>
    </div>
    <div class="app-header-actions">
      <form class="logout-form" method="post" action="/logout">
        <input type="hidden" name="_csrf" value="{html_lib.escape(csrf_token)}">
        <button class="icon-button" type="submit" aria-label="Log out" title="Log out">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 5H5v14h5M14 8l4 4-4 4M8 12h10"/></svg>
        </button>
      </form>
    </div>
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
        if parsed.path == "/showcase":
            self._handle_invoice_showcase_get()
            return
        if parsed.path.startswith("/showcase/") and parsed.path.endswith(".pdf"):
            self._handle_invoice_showcase_pdf_get(parsed.path)
            return
        if parsed.path.startswith("/showcase/") and "/pages/" in parsed.path and parsed.path.endswith(".png"):
            self._handle_invoice_showcase_page_image_get(parsed.path)
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
        if parsed.path == "/api/mail/extraction-settings":
            self._handle_mail_extraction_settings_get()
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
        if parsed.path == "/api/mail/extraction-settings":
            self._handle_mail_extraction_settings_post()
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
            all_items = self.mail_integration.list_invoices(
                owner_user_id=owner_user_id,
                limit=500,
                offset=0,
            )
            processed_count = self.mail_integration.count_invoices(owner_user_id=owner_user_id)
        except (ConfigError, MailIntegrationError) as exc:
            all_items = []
            processed_count = 0
            mail_notice = str(exc)
            mail_notice_kind = "error"

        query_values = parse_qs(query)
        review_filter = query_values.get("review", ["needs_review"])[-1]
        if review_filter not in {"needs_review", "all"}:
            review_filter = "needs_review"
        date_from_value = query_values.get("date_from", [""])[-1]
        date_to_value = query_values.get("date_to", [""])[-1]
        date_from = _query_date(date_from_value)
        date_to = _query_date(date_to_value)
        if date_from is None:
            date_from_value = ""
        if date_to is None:
            date_to_value = ""
        review_items = _filter_invoice_items(
            all_items,
            review_filter=review_filter,
            date_from=date_from,
            date_to=date_to,
        )

        selected_pdf_id = query_values.get("pdf_id", [""])[0]
        selected_item = (
            next(
                (
                    item
                    for item in all_items
                    if str(item.get("pdf_file_id") or "") == selected_pdf_id
                ),
                None,
            )
            if selected_pdf_id
            else review_items[0] if review_items else None
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

        filter_parameters = {"review": review_filter}
        if date_from_value:
            filter_parameters["date_from"] = date_from_value
        if date_to_value:
            filter_parameters["date_to"] = date_to_value
        filter_query = urlencode(filter_parameters)
        visible_pdf_ids = {item.get("pdf_file_id") for item in review_items}
        queue_items_html = self._review_queue_html(
            all_items,
            selected_item,
            filter_query,
            visible_pdf_ids,
        )
        navigation_items = review_items
        try:
            selected_index = next(
                index
                for index, item in enumerate(navigation_items)
                if selected_item and item.get("pdf_file_id") == selected_item.get("pdf_file_id")
            )
        except StopIteration:
            navigation_items = all_items
            selected_index = next(
                (
                    index
                    for index, item in enumerate(navigation_items)
                    if selected_item and item.get("pdf_file_id") == selected_item.get("pdf_file_id")
                ),
                -1,
            )

        def navigation_url(index: int) -> str:
            if index < 0 or index >= len(navigation_items):
                return ""
            pdf_id = navigation_items[index].get("pdf_file_id")
            return f"/dashboard?{filter_query}&pdf_id={pdf_id}"

        previous_url = navigation_url(selected_index - 1) if selected_index >= 0 else ""
        next_url = navigation_url(selected_index + 1) if selected_index >= 0 else ""
        evidence_html = self._review_evidence_html(
            selected_item,
            selected_invoice,
            previous_url=previous_url,
            next_url=next_url,
        )
        queue_count = len(review_items)
        processed_label = "invoice" if processed_count == 1 else "invoices"
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
  <meta name="theme-color" content="#f5f3ee">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&amp;family=Manrope:wght@500;600;700&amp;display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body class="app-body">
{_app_header_html(csrf_token=csrf, active="review")}

  <main class="workbench" aria-label="Invoice review workspace">
    <aside class="queue-pane" id="invoice-queue" aria-label="Invoice review queue">
      <div class="queue-pane-header">
        <div class="queue-heading-row">
          <div>
            <h1>Invoices</h1>
          </div>
          <span class="queue-count" aria-label="{processed_count} {processed_label} processed">{processed_count}</span>
        </div>
        <p class="queue-summary"><strong>{processed_count}</strong> processed · <strong data-shown-count>{queue_count}</strong> shown</p>
        <form class="queue-filters" method="get" action="/dashboard">
          <input type="hidden" name="review" value="all">
          <label class="review-filter-toggle">
            <input type="checkbox" name="review" value="needs_review"{' checked' if review_filter == 'needs_review' else ''} data-review-filter>
            <span class="toggle-track" aria-hidden="true"><span></span></span>
            <span>Needs review</span>
          </label>
          <div class="custom-date-filters" role="group" aria-label="Filter by received date">
            <label>
              <span>From</span>
              <input type="date" name="date_from" value="{html_lib.escape(date_from_value)}" data-date-from>
            </label>
            <label>
              <span>To</span>
              <input type="date" name="date_to" value="{html_lib.escape(date_to_value)}" data-date-to>
            </label>
          </div>
          <button class="filter-submit" type="submit">Apply</button>
        </form>
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
  <script>
    (function () {{
      var form = document.querySelector(".queue-filters");
      var reviewFilter = document.querySelector("[data-review-filter]");
      var dateFrom = document.querySelector("[data-date-from]");
      var dateTo = document.querySelector("[data-date-to]");
      var shownCount = document.querySelector("[data-shown-count]");
      var items = Array.prototype.slice.call(document.querySelectorAll("[data-queue-item]"));
      var queuePane = document.querySelector(".queue-pane");
      var queueList = document.querySelector(".queue-list");
      var queueScrollTopStorageKey = "zamp-review-queue-scroll-top";
      var queueScrollLeftStorageKey = "zamp-review-queue-scroll-left";
      if (!form || !reviewFilter || !dateFrom || !dateTo) return;

      function persistQueueScroll() {{
        try {{
          if (queuePane) {{
            window.sessionStorage.setItem(queueScrollTopStorageKey, String(queuePane.scrollTop || 0));
          }}
          if (queueList) {{
            window.sessionStorage.setItem(queueScrollLeftStorageKey, String(queueList.scrollLeft || 0));
          }}
        }} catch (error) {{}}
      }}

      function restoreQueueScroll() {{
        try {{
          var storedTop = window.sessionStorage.getItem(queueScrollTopStorageKey);
          var storedLeft = window.sessionStorage.getItem(queueScrollLeftStorageKey);
          if (queuePane && storedTop !== null) {{
            var scrollTop = Number(storedTop);
            if (!Number.isNaN(scrollTop) && scrollTop >= 0) {{
              queuePane.scrollTop = scrollTop;
            }}
          }}
          if (queueList && storedLeft !== null) {{
            var scrollLeft = Number(storedLeft);
            if (!Number.isNaN(scrollLeft) && scrollLeft >= 0) {{
              queueList.scrollLeft = scrollLeft;
            }}
          }}
        }} catch (error) {{}}
      }}

      function withinDate(received, from, to) {{
        if (!from && !to) return true;
        if (!received) return false;
        var receivedDay = received.slice(0, 10);
        return (!from || receivedDay >= from) && (!to || receivedDay <= to);
      }}

      function applyFilters() {{
        var visible = 0;
        items.forEach(function (item) {{
          var matchesReview = !reviewFilter.checked || item.dataset.needsReview === "true";
          var matchesDate = withinDate(item.dataset.received, dateFrom.value, dateTo.value);
          var matches = matchesReview && matchesDate;
          item.hidden = !matches;
          var itemUrl = new URL(item.href);
          itemUrl.searchParams.set("review", reviewFilter.checked ? "needs_review" : "all");
          if (dateFrom.value) itemUrl.searchParams.set("date_from", dateFrom.value);
          else itemUrl.searchParams.delete("date_from");
          if (dateTo.value) itemUrl.searchParams.set("date_to", dateTo.value);
          else itemUrl.searchParams.delete("date_to");
          item.href = itemUrl.toString();
          if (matches) visible += 1;
        }});
        if (shownCount) shownCount.textContent = String(visible);

        var url = new URL(window.location.href);
        url.searchParams.set("review", reviewFilter.checked ? "needs_review" : "all");
        if (dateFrom.value) url.searchParams.set("date_from", dateFrom.value);
        else url.searchParams.delete("date_from");
        if (dateTo.value) url.searchParams.set("date_to", dateTo.value);
        else url.searchParams.delete("date_to");
        window.history.replaceState(null, "", url.toString());

        var visibleItems = items.filter(function (item) {{ return !item.hidden; }});
        var activeIndex = visibleItems.findIndex(function (item) {{ return item.classList.contains("active"); }});
        updateNavigation(document.querySelector("[data-previous-invoice]"), activeIndex > 0 ? visibleItems[activeIndex - 1] : null);
        updateNavigation(document.querySelector("[data-next-invoice]"), activeIndex >= 0 && activeIndex < visibleItems.length - 1 ? visibleItems[activeIndex + 1] : null);
      }}

      function updateNavigation(link, item) {{
        if (!link) return;
        if (item) {{
          link.href = item.href;
          link.classList.remove("disabled");
          link.setAttribute("aria-disabled", "false");
        }} else {{
          link.removeAttribute("href");
          link.classList.add("disabled");
          link.setAttribute("aria-disabled", "true");
        }}
      }}

      form.addEventListener("submit", function (event) {{
        event.preventDefault();
        applyFilters();
      }});
      reviewFilter.addEventListener("change", applyFilters);
      dateFrom.addEventListener("change", applyFilters);
      dateTo.addEventListener("change", applyFilters);
      if (queuePane) {{
        queuePane.addEventListener("scroll", persistQueueScroll, {{ passive: true }});
      }}
      if (queueList) {{
        queueList.addEventListener("scroll", persistQueueScroll, {{ passive: true }});
      }}
      items.forEach(function (item) {{
        item.addEventListener("click", persistQueueScroll);
      }});
      window.requestAnimationFrame(restoreQueueScroll);
    }})();

    (function () {{
      var button = document.querySelector("[data-sidebar-toggle]");
      if (!button) return;

      function setCollapsed(collapsed) {{
        document.body.classList.toggle("sidebar-collapsed", collapsed);
        button.setAttribute("aria-expanded", collapsed ? "false" : "true");
        button.setAttribute("aria-label", collapsed ? "Expand invoice sidebar" : "Collapse invoice sidebar");
        button.setAttribute("title", collapsed ? "Expand invoice sidebar" : "Collapse invoice sidebar");
        try {{ window.localStorage.setItem("zamp-sidebar-collapsed", collapsed ? "1" : "0"); }} catch (error) {{}}
      }}

      try {{
        setCollapsed(window.localStorage.getItem("zamp-sidebar-collapsed") === "1");
      }} catch (error) {{
        setCollapsed(false);
      }}
      button.addEventListener("click", function () {{
        setCollapsed(!document.body.classList.contains("sidebar-collapsed"));
      }});
    }})();

    (function () {{
      var buttons = Array.prototype.slice.call(document.querySelectorAll("[data-document-mode]"));
      var views = Array.prototype.slice.call(document.querySelectorAll("[data-document-view]"));
      if (!buttons.length || !views.length) return;

      function setDocumentMode(mode) {{
        buttons.forEach(function (button) {{
          var selected = button.dataset.documentMode === mode;
          button.classList.toggle("active", selected);
          button.setAttribute("aria-pressed", selected ? "true" : "false");
        }});
        views.forEach(function (view) {{
          view.hidden = view.dataset.documentView !== mode;
        }});
      }}

      buttons.forEach(function (button) {{
        button.addEventListener("click", function () {{
          setDocumentMode(button.dataset.documentMode);
        }});
      }});
    }})();
  </script>
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
        filter_query: str,
        visible_pdf_ids: set[Any],
    ) -> str:
        if not review_items:
            return '<div class="empty-state">No invoices match these filters.</div>'
        selected_pdf_id = selected_item.get("pdf_file_id") if selected_item else None
        return "\n".join(
            self._review_queue_item_html(
                item,
                active=item.get("pdf_file_id") == selected_pdf_id,
                filter_query=filter_query,
                visible=item.get("pdf_file_id") in visible_pdf_ids,
            )
            for item in review_items
        )

    def _review_queue_item_html(
        self,
        item: dict[str, Any],
        *,
        active: bool,
        filter_query: str,
        visible: bool,
    ) -> str:
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
        needs_review = "true" if _invoice_needs_review(item) else "false"
        received_value = html_lib.escape(
            _display_value(item.get("received_date") or item.get("received_at"))
        )
        hidden_attr = "" if visible else " hidden"
        return f"""
        <a class="queue-item{' active' if active else ''}" href="/dashboard?{html_lib.escape(filter_query)}&amp;pdf_id={pdf_file_id}" data-queue-item data-needs-review="{needs_review}" data-received="{received_value}"{hidden_attr}{active_attr}>
          <div class="queue-header">
            <span class="queue-vendor"><span class="vendor-dot" aria-hidden="true"></span>{html_lib.escape(vendor or "Unknown vendor")}</span>
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
        *,
        previous_url: str = "",
        next_url: str = "",
    ) -> str:
        previous_link = self._invoice_navigation_link(
            direction="previous",
            url=previous_url,
        )
        next_link = self._invoice_navigation_link(
            direction="next",
            url=next_url,
        )
        if not selected_item and not invoice:
            return f"""
      <div class="evidence-header">
        <div class="evidence-navigation-left">
          {self._sidebar_toggle_html()}
          {previous_link}
        </div>
        <div class="evidence-title"><strong>No invoice selected</strong></div>
        <div class="review-actions">{next_link}</div>
      </div>
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
            f"/api/mail/invoices/{pdf_file_id}/overlay.pdf?boxes=parsed"
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
        decision_data_json = html_lib.escape(
            json.dumps(
                {
                    "decision": decision,
                    "checks": checks,
                    "audit": audit,
                    "ap_context": ap_context,
                },
                indent=2,
                sort_keys=True,
                default=str,
            )
        )
        pdf_html = (
            f'<iframe class="pdf-viewer" src="{html_lib.escape(overlay_url)}" '
            f'title="{html_lib.escape(filename or "Invoice overlay")}" '
            f'data-document-view="pdf"></iframe>'
            if overlay_url
            else '<div class="evidence-empty" data-document-view="pdf"><strong>Overlay unavailable</strong></div>'
        )
        return f"""
      <div class="evidence-header">
        <div class="evidence-navigation-left">
          {self._sidebar_toggle_html()}
          {previous_link}
        </div>
        <div class="evidence-title">
          <strong>{html_lib.escape(filename or "Invoice detail")}</strong>
          <span>{html_lib.escape(subject)}</span>
        </div>
        <div class="actions review-actions">
          <span class="decision-badge status-{decision_class}">{html_lib.escape(decision_label)}</span>
          {f'<span class="confidence-badge">{html_lib.escape(confidence)} confidence</span>' if confidence else ''}
          {next_link}
        </div>
      </div>
      <div class="evidence-content">
        <div class="review-detail-panel">
          {decision_html}
          {checks_html}
          <div class="supporting-details">
            <p class="supporting-label">Supporting details</p>
            {normalized_html}
            {audit_html}
            <details class="detail-section disclosure-section">
              <summary>Source</summary>
              <div class="disclosure-body">{meta_html}</div>
            </details>
          </div>
        </div>
        <div class="pdf-stage" data-document-stage>
          <div class="document-mode-switch" role="group" aria-label="Document view">
            <button class="active" type="button" data-document-mode="pdf" aria-pressed="true">PDF</button>
            <button type="button" data-document-mode="json" aria-pressed="false">JSON</button>
          </div>
          {pdf_html}
          <div class="raw-json-view" data-document-view="json" hidden>
            <div class="raw-json-heading">Decision data</div>
            <pre><code>{decision_data_json}</code></pre>
          </div>
        </div>
      </div>"""

    def _sidebar_toggle_html(self) -> str:
        return """
        <button class="sidebar-toggle" type="button" data-sidebar-toggle aria-controls="invoice-queue" aria-expanded="true" aria-label="Collapse invoice sidebar" title="Collapse invoice sidebar">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg>
        </button>"""

    def _invoice_navigation_link(self, *, direction: str, url: str) -> str:
        is_previous = direction == "previous"
        label = "Previous invoice" if is_previous else "Next invoice"
        data_attribute = "data-previous-invoice" if is_previous else "data-next-invoice"
        path = "M15 18l-6-6 6-6" if is_previous else "M9 6l6 6-6 6"
        href = f' href="{html_lib.escape(url)}"' if url else ""
        disabled_class = " disabled" if not url else ""
        disabled = "true" if not url else "false"
        return f"""
        <a class="invoice-nav-button{disabled_class}"{href} {data_attribute} aria-label="{label}" title="{label}" aria-disabled="{disabled}">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="{path}"/></svg>
        </a>"""

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
        decision_label = _humanize_token(decision_value) or "Pending"
        decision_class = html_lib.escape(_badge_class(decision_label))
        normalized_decision = decision_value.lower().replace("-", "_")
        is_clear = normalized_decision.startswith("approve") or normalized_decision == "pass"
        headline = (
            "Ready to move forward"
            if is_clear
            else "Review is getting ready"
            if not normalized_decision
            else "This invoice needs your attention"
        )
        summary_html = f'<p class="decision-summary">{html_lib.escape(summary)}</p>' if summary else ""
        action_html = ""
        if next_action:
            cta_label = "Review issue" if failed_or_review else "View completed checks"
            action_html = f"""
              <div class="next-step-card">
                <span class="next-step-number">01</span>
                <div>
                  <span class="next-step-label">Your next step</span>
                  <strong>{html_lib.escape(next_action)}</strong>
                </div>
                <a class="primary-action" href="#decision-steps">
                  <span>{cta_label}</span>
                  <span aria-hidden="true">→</span>
                </a>
              </div>"""
        return f"""
          <section class="decision-hero status-{decision_class}">
            <div class="decision-hero-copy">
              <div class="decision-overline">
                {f'<span>{failed_or_review} attention check{"s" if failed_or_review != 1 else ""}</span>' if checks else ''}
              </div>
              <h2>{headline}</h2>
              {summary_html}
            </div>
            {action_html}
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
        ocr_parts = {
            _display_value(part)
            for part in raw_parse.get("ocr_parts", [])
            if _display_value(part)
        }
        facts = self._detail_facts_with_source_html(
            [
                ("Vendor", _field_value(normalized_invoice.get("vendor"), "name", "value", "raw") or item.get("vendor"), "vendor"),
                ("Invoice #", _field_value(normalized_invoice.get("invoice_number"), "value") or item.get("invoice_number"), "invoice_number"),
                ("Purchase order", _field_value(normalized_invoice.get("purchase_order"), "value") or item.get("purchase_order"), "purchase_order"),
                ("Issue date", _field_value(normalized_invoice.get("issue_date"), "value"), "issue_date"),
                ("Due date", _field_value(normalized_invoice.get("due_date"), "value"), "due_date"),
                ("Amount due", amount, "amount_due"),
                ("Parse status", _humanize_token(parse_status), None),
                ("Parser", parser_method, None),
                ("Pages", page_count, None),
            ],
            ocr_parts=ocr_parts,
        )
        return f"""
          <details class="detail-section disclosure-section">
            <summary>Normalized Invoice</summary>
            <div class="disclosure-body">{facts}</div>
          </details>"""

    def _decision_checks_html(self, checks: list[Any]) -> str:
        if not checks:
            return """
          <section class="progress-section" id="decision-steps">
            <div class="progress-column done-column">
              <div class="progress-heading">
                <span class="progress-marker">✓</span>
                <div><span>Already done</span><h2>Invoice received</h2></div>
              </div>
              <p class="detail-muted">The invoice is safely in your workspace.</p>
            </div>
            <div class="progress-column attention-column">
              <div class="progress-heading">
                <span class="progress-marker">→</span>
                <div><span>Still to do</span><h2>Decision checks</h2></div>
              </div>
              <p class="detail-muted">Checks are still being prepared.</p>
            </div>
          </section>"""
        completed_rows = []
        attention_rows = []
        for check in checks:
            if not isinstance(check, dict):
                continue
            check_id = _display_value(check.get("id"))
            status = _display_value(check.get("status"))
            summary = _frontend_copy(check.get("summary"))
            status_label = _humanize_token(status) or "Unknown"
            status_class = html_lib.escape(_badge_class(status_label))
            row = (
                f"""
              <li class="check-row">
                <span class="check-icon" aria-hidden="true"></span>
                <div>
                  <strong>{html_lib.escape(_humanize_token(check_id) or check_id)}</strong>
                  <span>{html_lib.escape(summary)}</span>
                </div>
                <span class="decision-badge status-{status_class}">{html_lib.escape(status_label)}</span>
              </li>"""
            )
            if status.lower() in {"fail", "review"}:
                attention_rows.append(row)
            else:
                completed_rows.append(row)
        completed_html = "\n".join(completed_rows) if completed_rows else '<p class="detail-muted">No completed checks yet.</p>'
        attention_html = "\n".join(attention_rows) if attention_rows else '<div class="all-clear"><span aria-hidden="true">✓</span><p><strong>Nothing else is blocking this invoice.</strong><br>All recorded checks are clear.</p></div>'
        return f"""
          <section class="progress-section" id="decision-steps">
            <div class="progress-column done-column">
              <div class="progress-heading">
                <span class="progress-marker">✓</span>
                <div><span>Already done</span><h2>Checks completed</h2></div>
              </div>
              <ul class="check-list">{completed_html}</ul>
            </div>
            <div class="progress-column attention-column">
              <div class="progress-heading">
                <span class="progress-marker">→</span>
                <div><span>Still to do</span><h2>Needs attention</h2></div>
              </div>
              <ul class="check-list">{attention_html}</ul>
            </div>
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
          <details class="detail-section disclosure-section">
            <summary>Audit Reasoning</summary>
            <div class="disclosure-body">{facts}</div>
          </details>"""

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

    def _detail_facts_with_source_html(
        self,
        rows: list[tuple[str, Any, str | None]],
        *,
        ocr_parts: set[str],
    ) -> str:
        items = []
        for label, value, part_key in rows:
            text = _display_value(value)
            if not text:
                continue
            is_ocr = part_key in ocr_parts if part_key else False
            row_class = "detail-fact-row is-ocr" if is_ocr else "detail-fact-row"
            items.append(
                f"""
              <div class="{row_class}">
                <dt>{html_lib.escape(label)}</dt>
                <dd>
                  <div class="detail-fact-value">
                    <span>{html_lib.escape(text)}</span>
                  </div>
                </dd>
              </div>"""
            )
        if not items:
            return '<p class="detail-muted">No data recorded yet.</p>'
        return f"""
            <dl class="detail-facts detail-facts-with-source">
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

    def _handle_invoice_showcase_get(self) -> None:
        documents = [
            {
                "slug": document.slug,
                "title": document.title,
                "group": document.group,
                "description": document.description,
                "page_count": document.page_count,
                "tags": document.tags,
            }
            for document in SHOWCASE_DOCUMENTS
        ]
        self._send_html(
            HTTPStatus.OK,
            invoice_showcase_page(documents=documents),
        )

    def _handle_invoice_showcase_pdf_get(self, path: str) -> None:
        slug = path.removeprefix("/showcase/").removesuffix(".pdf")
        document = showcase_document(slug)
        if document is None:
            self._send_html(HTTPStatus.NOT_FOUND, error_page(404, "PDF not found."))
            return
        content = render_showcase_pdf(slug)
        self._send_binary(
            HTTPStatus.OK,
            content,
            content_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="invoice-showcase-{slug}.pdf"',
                "Cache-Control": "public, max-age=3600",
            },
        )

    def _handle_invoice_showcase_page_image_get(self, path: str) -> None:
        parts = path.strip("/").split("/")
        if len(parts) != 4 or parts[0] != "showcase" or parts[2] != "pages":
            self._send_html(HTTPStatus.NOT_FOUND, error_page(404, "Preview not found."))
            return
        slug = parts[1]
        document = showcase_document(slug)
        try:
            page_number = int(parts[3].removesuffix(".png"))
        except ValueError:
            page_number = 0
        if document is None or page_number < 1 or page_number > document.page_count:
            self._send_html(HTTPStatus.NOT_FOUND, error_page(404, "Preview not found."))
            return
        try:
            content = render_showcase_page_png(slug, page_number)
        except (RuntimeError, ValueError) as exc:
            self._send_html(HTTPStatus.INTERNAL_SERVER_ERROR, error_page(500, str(exc)))
            return
        self._send_binary(
            HTTPStatus.OK,
            content,
            content_type="image/png",
            headers={"Cache-Control": "public, max-age=3600"},
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

    def _handle_mail_extraction_settings_get(self) -> None:
        context = self._authenticated_api_user()
        if not context:
            return
        owner_user_id, cookies = context
        try:
            settings = self.mail_integration.get_extraction_settings(owner_user_id=owner_user_id)
        except (ConfigError, MailIntegrationError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, cookies=cookies)
            return
        self._send_json(HTTPStatus.OK, settings, cookies=cookies)

    def _handle_mail_extraction_settings_post(self) -> None:
        context = self._authenticated_api_user()
        if not context:
            return
        owner_user_id, cookies = context
        try:
            body = self._json_body(max_bytes=4 * 1024)
            if not isinstance(body.get("use_ai"), bool):
                raise ValueError("use_ai must be a boolean.")
            settings = self.mail_integration.update_extraction_settings(
                owner_user_id=owner_user_id,
                use_ai=body["use_ai"],
            )
        except (ConfigError, MailIntegrationError, ValueError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)}, cookies=cookies)
            return
        self._send_json(HTTPStatus.OK, settings, cookies=cookies)

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
        if not self._valid_gmail_pubsub_auth(parsed):
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

    def _valid_gmail_pubsub_auth(self, parsed: Any | None = None) -> bool:
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
        if parsed is not None:
            query_secret = parse_qs(parsed.query).get("secret", [None])[-1]
            if isinstance(query_secret, str):
                candidates.append(query_secret)
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
