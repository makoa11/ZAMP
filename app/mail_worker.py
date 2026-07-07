from __future__ import annotations

import argparse
import json
import socket
import time
from datetime import UTC, datetime
from typing import Any

from .config import ConfigError, load_config
from .mail_ingestion import MAIL_JOB_TYPES
from .mail_service import MailIntegration


def run_once(*, limit: int = 10, worker_id: str | None = None) -> int:
    config = load_config()
    integration = MailIntegration(config)
    worker_name = worker_id or f"{socket.gethostname()}:{time.time_ns()}"
    jobs = integration.repo.claim_jobs(
        worker_id=worker_name,
        job_types=MAIL_JOB_TYPES,
        limit=limit,
    )
    for job in jobs:
        _handle_job(integration, job)
    return len(jobs)


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
    while True:
        now = time.time()
        if now - last_fallback_at >= fallback_seconds:
            _enqueue_polling_fallbacks(integration)
            last_fallback_at = now
        if now - last_renewal_at >= 3600:
            integration.ingestion.renew_mail_subscriptions()
            last_renewal_at = now
        jobs = integration.repo.claim_jobs(
            worker_id=worker_name,
            job_types=MAIL_JOB_TYPES,
            limit=limit,
        )
        for job in jobs:
            _handle_job(integration, job)
        if not jobs:
            time.sleep(poll_seconds)


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
            integration.ingestion.process_gmail_fallback(account_id=int(payload["account_id"]))
        elif job_type == "outlook_message_fetch":
            integration.ingestion.process_outlook_message(
                account_id=int(payload["account_id"]),
                message_id=str(payload["message_id"]),
            )
        elif job_type == "outlook_delta_sync":
            integration.ingestion.process_outlook_delta(account_id=int(payload["account_id"]))
        elif job_type == "renew_mail_subscriptions":
            integration.ingestion.renew_mail_subscriptions()
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


def _enqueue_polling_fallbacks(integration: MailIntegration) -> None:
    bucket = datetime.now(UTC).strftime("%Y%m%d%H%M")
    for account in integration.repo.list_active_accounts():
        account_id = int(account["id"])
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
