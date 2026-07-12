from __future__ import annotations

import argparse
import json
import socket
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .ap_context import load_db_procurement_context, summarize_procurement_context
from .config import ConfigError, load_config
from .invoice_decision import decide_invoice
from .invoice_normalizer import normalize_invoice_parse
from .invoice_ocr import OCR_MAX_DOCUMENT_PAGES, OCR_REGION_PADDING
from .invoice_pipeline import configuration_fingerprint
from .invoice_parser import PARSER_VERSION, parse_invoice_pdf
from .mail_ingestion import MAIL_JOB_TYPES
from .mail_service import MailIntegration

PDF_PARSE_JOB_TYPES = {"parse_pdf"}
MAIL_FETCH_JOB_TYPES = MAIL_JOB_TYPES - PDF_PARSE_JOB_TYPES


def run_once(*, limit: int = 10, worker_id: str | None = None) -> int:
    config = load_config()
    integration = MailIntegration(config)
    try:
        integration.repo.enqueue_stale_pdf_parse_jobs(
            parser_revision=_parser_revision(integration),
        )
        worker_name = worker_id or f"{socket.gethostname()}:{time.time_ns()}"
        jobs = _claim_prioritized_jobs(integration, worker_id=worker_name, limit=limit)
        for job in jobs:
            _handle_job(integration, job)
        return len(jobs)
    finally:
        integration.close()


def run_forever(
    *,
    limit: int,
    poll_seconds: float,
    fallback_seconds: int,
    worker_id: str | None = None,
) -> None:
    worker_name = worker_id or f"{socket.gethostname()}:{time.time_ns()}"
    config = load_config()
    integration = MailIntegration(config)
    last_fallback_at = 0.0
    last_renewal_at = 0.0
    last_reprocess_scan_at = 0.0
    try:
        while True:
            now = time.time()
            if now - last_reprocess_scan_at >= 3600:
                integration.repo.enqueue_stale_pdf_parse_jobs(
                    parser_revision=_parser_revision(integration),
                )
                last_reprocess_scan_at = now
            if now - last_fallback_at >= fallback_seconds:
                _enqueue_polling_fallbacks(integration)
                last_fallback_at = now
            if now - last_renewal_at >= 3600:
                last_renewal_at = now
                _renew_subscriptions_safely(integration)
            jobs = _claim_prioritized_jobs(integration, worker_id=worker_name, limit=limit)
            for job in jobs:
                _handle_job(integration, job)
            if not jobs:
                time.sleep(poll_seconds)
    finally:
        integration.close()


def _claim_prioritized_jobs(
    integration: MailIntegration,
    *,
    worker_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Keep attachment downloads ahead of expensive PDF/OCR parsing."""
    if limit <= 0:
        return []

    jobs = integration.repo.claim_jobs(
        worker_id=worker_id,
        job_types=MAIL_FETCH_JOB_TYPES,
        limit=limit,
    )
    remaining = limit - len(jobs)
    if remaining <= 0:
        return jobs

    return [
        *jobs,
        *integration.repo.claim_jobs(
            worker_id=worker_id,
            job_types=PDF_PARSE_JOB_TYPES,
            limit=remaining,
        ),
    ]


def _handle_job(integration: MailIntegration, job: dict[str, Any]) -> None:
    payload = job.get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        payload = {}

    try:
        job_type = job["type"]
        if job_type == "gmail_history_sync":
            integration.ingestion.process_gmail_history(
                account_id=int(payload["account_id"]),
                notification_history_id=str(payload.get("history_id") or ""),
            )
        elif job_type == "gmail_message_fetch":
            integration.ingestion.process_gmail_message(
                account_id=int(payload["account_id"]),
                message_id=str(payload["message_id"]),
            )
        elif job_type == "gmail_fallback_sync":
            integration.ingestion.process_gmail_fallback(
                account_id=int(payload["account_id"]),
                reprocess_key=_optional_string(payload.get("reprocess_key")),
            )
        elif job_type == "outlook_message_fetch":
            integration.ingestion.process_outlook_message(
                account_id=int(payload["account_id"]),
                message_id=str(payload["message_id"]),
            )
        elif job_type == "outlook_delta_sync":
            integration.ingestion.process_outlook_delta(
                account_id=int(payload["account_id"]),
                reprocess_key=_optional_string(payload.get("reprocess_key")),
            )
        elif job_type == "renew_mail_subscriptions":
            integration.ingestion.renew_mail_subscriptions()
        elif job_type == "parse_pdf":
            _handle_parse_pdf_job(integration, payload)
        else:
            raise RuntimeError(f"Unsupported job type: {job_type}")
    except Exception as exc:
        integration.repo.retry_job(
            job_id=int(job["id"]),
            attempts=int(job.get("attempts") or 1),
            error=str(exc),
        )
        return

    integration.repo.complete_job(job_id=int(job["id"]))


def _renew_subscriptions_safely(integration: MailIntegration) -> bool:
    try:
        integration.ingestion.renew_mail_subscriptions()
    except Exception:
        return False
    return True


def _handle_parse_pdf_job(integration: MailIntegration, payload: dict[str, Any]) -> None:
    pdf_file_id = int(payload["pdf_file_id"])
    storage_path = str(payload["storage_path"])
    attachment_id = _optional_int(payload.get("attachment_id"))
    pdf_path = _storage_pdf_path(integration.storage.root, storage_path)
    parse_kwargs: dict[str, Any] = {
        "source_id": f"mail_pdf_file:{pdf_file_id}",
        "ocr_max_regions": integration.config.mail_parse_ocr_max_regions,
        "ocr_max_document_pages": getattr(
            integration.config,
            "mail_parse_ocr_max_document_pages",
            OCR_MAX_DOCUMENT_PAGES,
        ),
    }
    for config_name, parser_name in {
        "mail_parse_ocr_render_dpi": "ocr_render_dpi",
        "mail_parse_ocr_refinement_dpi": "ocr_refinement_dpi",
        "mail_parse_ocr_timeout_seconds": "ocr_timeout_seconds",
        "mail_parse_document_timeout_seconds": "document_timeout_seconds",
    }.items():
        if hasattr(integration.config, config_name):
            parse_kwargs[parser_name] = getattr(integration.config, config_name)
    result = parse_invoice_pdf(pdf_path.read_bytes(), **parse_kwargs)
    parser_revision = _parser_revision(integration)
    result["parser_revision"] = parser_revision
    warnings = result.get("warnings")
    parser_method = _parser_method(result)
    integration.repo.upsert_pdf_parse_result(
        pdf_file_id=pdf_file_id,
        status=str(result.get("status") or "failed"),
        parser_version=parser_revision,
        result=result,
        warnings=warnings if isinstance(warnings, list) else [],
    )
    if hasattr(integration.repo, "insert_pdf_parse_run"):
        pipeline = result.get("pipeline") if isinstance(result.get("pipeline"), dict) else {}
        timings = pipeline.get("timings_ms") if isinstance(pipeline.get("timings_ms"), dict) else {}
        integration.repo.insert_pdf_parse_run(
            pdf_file_id=pdf_file_id,
            status=str(result.get("status") or "failed"),
            parser_version=parser_revision,
            parser_method=parser_method,
            configuration_fingerprint=_optional_string(pipeline.get("configuration_fingerprint")),
            duration_ms=_optional_float(timings.get("total")),
            result=result,
            warnings=warnings if isinstance(warnings, list) else [],
            promoted=True,
        )
    owner_user_id = _owner_user_id_for_parse_job(integration, payload)
    if not owner_user_id:
        return

    normalized_invoice = normalize_invoice_parse(result)
    integration.repo.upsert_mail_invoice_extraction(
        owner_user_id=owner_user_id,
        pdf_file_id=pdf_file_id,
        attachment_id=attachment_id,
        normalized_invoice=normalized_invoice,
        parse_status=str(normalized_invoice.get("parser_status") or result.get("status") or "failed"),
        parser_method=parser_method,
    )
    procurement_context = load_db_procurement_context(
        integration.repo,
        owner_user_id=owner_user_id,
        invoice=normalized_invoice,
    )
    decision = decide_invoice(normalized_invoice, procurement_context)
    audit = decision.get("audit") if isinstance(decision.get("audit"), dict) else {}
    decision["audit"] = {
        **audit,
        "ap_context_summary": summarize_procurement_context(procurement_context),
    }
    integration.repo.upsert_mail_invoice_decision(
        owner_user_id=owner_user_id,
        pdf_file_id=pdf_file_id,
        decision_result=decision,
    )


def _storage_pdf_path(root: str | Path, storage_path: str) -> Path:
    relative_path = Path(storage_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise RuntimeError("PDF storage path must be relative to MAIL_PDF_STORAGE_DIR.")
    return Path(root) / relative_path


def _parser_revision(integration: MailIntegration) -> str:
    max_regions = int(integration.config.mail_parse_ocr_max_regions)
    configured_max_pages = getattr(
        integration.config,
        "mail_parse_ocr_max_document_pages",
        OCR_MAX_DOCUMENT_PAGES,
    )
    max_pages = "all" if configured_max_pages is None else str(int(configured_max_pages))
    readable = (
        f"{PARSER_VERSION}:ocr-regions={max_regions}:ocr-pages={max_pages}:"
        f"padding={OCR_REGION_PADDING:.2f}"
    )
    fingerprint = configuration_fingerprint(
        {
            "max_regions": max_regions,
            "max_pages": configured_max_pages,
            "padding": OCR_REGION_PADDING,
            "render_dpi": getattr(integration.config, "mail_parse_ocr_render_dpi", None),
            "refinement_dpi": getattr(integration.config, "mail_parse_ocr_refinement_dpi", None),
            "ocr_timeout": getattr(integration.config, "mail_parse_ocr_timeout_seconds", None),
            "document_timeout": getattr(integration.config, "mail_parse_document_timeout_seconds", None),
        }
    )
    return f"{readable}:config={fingerprint}"


def _parser_method(result: dict[str, Any]) -> str:
    pipeline = result.get("pipeline")
    route = pipeline.get("route") if isinstance(pipeline, dict) else None
    if route in {"native_text", "local_ocr", "hybrid"}:
        return str(route)
    return "local_ocr" if result.get("ocr_used") else "native_text"


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _owner_user_id_for_parse_job(integration: MailIntegration, payload: dict[str, Any]) -> str | None:
    owner_user_id = payload.get("owner_user_id")
    if isinstance(owner_user_id, str) and owner_user_id:
        return owner_user_id

    account_id = _optional_int(payload.get("account_id"))
    if account_id is None:
        return None
    account = integration.repo.get_account(account_id)
    if not account:
        return None
    owner_user_id = account.get("owner_user_id")
    return owner_user_id if isinstance(owner_user_id, str) and owner_user_id else None


def _enqueue_polling_fallbacks(integration: MailIntegration) -> None:
    bucket = datetime.now(UTC).strftime("%Y%m%d%H%M")
    for account in integration.repo.list_active_accounts():
        account_id = int(account["id"])
        owner_user_id = account.get("owner_user_id")
        if not isinstance(owner_user_id, str):
            continue
        provider = account.get("provider")
        if provider == "gmail":
            integration.repo.enqueue_job(
                job_type="gmail_fallback_sync",
                payload={"account_id": account_id},
                unique_key=f"gmail-fallback:{account_id}:{bucket}",
            )
        elif provider == "outlook":
            integration.repo.enqueue_job(
                job_type="outlook_delta_sync",
                payload={"account_id": account_id},
                unique_key=f"outlook-delta:{account_id}:{bucket}",
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ZAMP mail ingestion jobs.")
    parser.add_argument("--once", action="store_true", help="Claim and process one batch, then exit.")
    parser.add_argument("--limit", type=int, default=10, help="Maximum jobs to claim per batch.")
    parser.add_argument("--poll-seconds", type=float, default=10.0, help="Idle sleep between claim attempts.")
    parser.add_argument(
        "--fallback-seconds",
        type=int,
        default=900,
        help="How often to enqueue Gmail/Outlook polling fallback jobs.",
    )
    args = parser.parse_args()

    try:
        if args.once:
            processed = run_once(limit=args.limit)
            print(f"Processed {processed} mail jobs.")
        else:
            run_forever(
                limit=args.limit,
                poll_seconds=args.poll_seconds,
                fallback_seconds=args.fallback_seconds,
            )
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
