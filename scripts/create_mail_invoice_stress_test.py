from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ap_context import (  # noqa: E402
    iter_ap_context_records_from_manifest,
    load_db_procurement_context,
)
from app.invoice_fixtures import (  # noqa: E402
    InvoicePdfFixture,
    generate_invoice_stress_fixtures,
    write_invoice_manifest,
)
from app.invoice_normalizer import normalize_invoice_parse  # noqa: E402
from app.invoice_parser import parse_invoice_pdf  # noqa: E402
from app.invoice_pdf import render_invoice_pdf  # noqa: E402
from app.mail_store import MailDatabase, MailRepository  # noqa: E402


DEFAULT_OUTPUT_DIR = Path("storage/test_invoice_pipeline")
DEFAULT_PDF_COUNT = 10
DEFAULT_SEED = 73_000
DATE_SPREAD_DAYS = 120
FILE_PREFIX = "mail-invoice-stress"
SUMMARY_FILENAME = "mail-invoice-stress-summary.json"


def create_mail_invoice_stress_test(
    *,
    database_url: str,
    owner_user_id: str | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    pdf_count: int = DEFAULT_PDF_COUNT,
    seed: int = DEFAULT_SEED,
    today: date | None = None,
) -> dict[str, Any]:
    """Generate mail-sized stress PDFs, seed AP context, and verify DB matching."""
    database = MailDatabase(database_url)
    repo = MailRepository(database)
    try:
        repo.initialize_schema()
        resolved_owner_user_id = resolve_owner_user_id(database, owner_user_id)
        pdf_paths = generate_mail_invoice_stress_pdfs(
            output_dir=output_dir,
            pdf_count=pdf_count,
            seed=seed,
            today=today,
        )
        seeded_records = seed_mail_invoice_ap_context(
            repo,
            pdf_paths=pdf_paths,
            owner_user_id=resolved_owner_user_id,
        )
        validations = validate_mail_invoice_ap_context(
            repo,
            pdf_paths=pdf_paths,
            owner_user_id=resolved_owner_user_id,
        )
        matched_count = sum(1 for item in validations if item["context_matched"])
        summary = {
            "schema_version": 1,
            "owner_user_id": resolved_owner_user_id,
            "output_dir": str(output_dir),
            "pdf_count": len(pdf_paths),
            "ap_context_record_count": seeded_records,
            "validated_context_match_count": matched_count,
            "seed": seed,
            "date": (today or date.today()).isoformat(),
            "pdfs": validations,
        }
        output_dir.joinpath(SUMMARY_FILENAME).write_text(
            json.dumps(summary, indent=2, default=_json_default) + "\n",
            encoding="utf-8",
        )
        if matched_count != len(pdf_paths):
            failed = [item["pdf_filename"] for item in validations if not item["context_matched"]]
            raise RuntimeError(
                "Generated AP context did not match parsed fields for: " + ", ".join(failed)
            )
        return summary
    finally:
        database.close()


def generate_mail_invoice_stress_pdfs(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    pdf_count: int = DEFAULT_PDF_COUNT,
    seed: int = DEFAULT_SEED,
    today: date | None = None,
) -> list[Path]:
    """Write deterministic stress PDFs containing one invoice document each."""
    if pdf_count < 1:
        raise ValueError("pdf-count must be at least 1.")
    if seed < 1:
        raise ValueError("seed must be greater than 0.")

    output_dir.mkdir(parents=True, exist_ok=True)
    anchor_date = today or date.today()
    sequence_width = max(2, len(str(pdf_count)))
    written_paths: list[Path] = []

    for index in range(pdf_count):
        fixture_seed = seed + (index * 1009)
        fixture_date = anchor_date - timedelta(days=(index * 13) % DATE_SPREAD_DAYS)
        fixtures = generate_invoice_stress_fixtures(seed=fixture_seed, today=fixture_date)
        fixture = fixtures[index % len(fixtures)]
        samples = _one_document_samples(fixture, document_index=index // len(fixtures))
        filename = f"{FILE_PREFIX}-{index + 1:0{sequence_width}d}-{fixture.slug}.pdf"
        pdf_path = output_dir / filename
        pdf_path.write_bytes(render_invoice_pdf(samples))
        write_invoice_manifest(
            pdf_path,
            samples,
            suite="mail_stress",
            fixture_slug=fixture.slug,
        )
        written_paths.append(pdf_path)

    return written_paths


def seed_mail_invoice_ap_context(
    repo: Any,
    *,
    pdf_paths: Iterable[Path],
    owner_user_id: str,
) -> int:
    inserted = 0
    for pdf_path in pdf_paths:
        manifest_path = pdf_path.with_suffix(".manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        records = iter_ap_context_records_from_manifest(
            manifest,
            owner_user_id=owner_user_id,
            manifest_path=manifest_path,
        )
        if len(records) != 1:
            raise RuntimeError(
                f"Expected one AP context record for {pdf_path.name}, found {len(records)}."
            )
        repo.upsert_ap_context_record(**records[0])
        inserted += 1
    return inserted


def validate_mail_invoice_ap_context(
    repo: Any,
    *,
    pdf_paths: Iterable[Path],
    owner_user_id: str,
) -> list[dict[str, Any]]:
    validations: list[dict[str, Any]] = []
    for pdf_path in pdf_paths:
        parse_result = parse_invoice_pdf(pdf_path.read_bytes(), source_id=str(pdf_path))
        invoice = normalize_invoice_parse(parse_result)
        context = load_db_procurement_context(
            repo,
            owner_user_id=owner_user_id,
            invoice=invoice,
        )
        source = context.get("source") if isinstance(context.get("source"), Mapping) else {}
        validations.append(
            {
                "pdf_filename": pdf_path.name,
                "manifest_filename": pdf_path.with_suffix(".manifest.json").name,
                "parse_status": parse_result.get("status"),
                "context_matched": bool(context.get("available")),
                "match_strategy": source.get("match_strategy"),
                "ap_context_record_id": source.get("record_id"),
                "scenario": context.get("scenario"),
            }
        )
    return validations


def resolve_owner_user_id(database: Any, explicit_owner_user_id: str | None) -> str:
    if explicit_owner_user_id and explicit_owner_user_id.strip():
        return explicit_owner_user_id.strip()

    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT owner_user_id
            FROM mail_accounts
            WHERE owner_user_id <> ''
            ORDER BY owner_user_id
            """
        ).fetchall()
    owners = [str(_row_value(row, "owner_user_id")) for row in rows]
    if len(owners) == 1:
        return owners[0]
    if not owners:
        raise RuntimeError(
            "No connected mailbox owner was found. Pass --owner-user-id explicitly."
        )
    raise RuntimeError(
        "Multiple connected mailbox owners were found. Pass --owner-user-id explicitly: "
        + ", ".join(owners)
    )


def resolve_database_url(
    explicit_database_url: str | None,
    *,
    env_path: Path = ROOT / ".env",
) -> str | None:
    return explicit_database_url or os.environ.get("DATABASE_URL") or _load_env_file(env_path).get("DATABASE_URL")


def _one_document_samples(
    fixture: InvoicePdfFixture,
    *,
    document_index: int,
) -> list[dict[str, Any]]:
    document_samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in fixture.samples:
        metadata = sample.get("fixture") if isinstance(sample.get("fixture"), dict) else {}
        document_id = str(metadata.get("document_id") or sample["id"])
        document_samples[document_id].append(sample)
    documents = list(document_samples.values())
    if not documents:
        raise ValueError(f"Stress fixture {fixture.slug!r} contained no invoice documents.")
    return documents[document_index % len(documents)]


def _row_value(row: Any, key: str) -> Any:
    if isinstance(row, Mapping):
        return row[key]
    return row[0]


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _json_default(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create mail-pipeline stress invoice PDFs, seed matching AP context records, "
            "and verify each generated PDF resolves its procurement context."
        )
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument(
        "--owner-user-id",
        default=None,
        help="Mailbox owner ID. Auto-detected when exactly one owner has a connected account.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--pdf-count", type=int, default=DEFAULT_PDF_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--date", type=date.fromisoformat, default=None)
    args = parser.parse_args()

    database_url = resolve_database_url(args.database_url)
    if not database_url:
        raise SystemExit("DATABASE_URL is required. Pass --database-url or set it in the environment/.env.")

    try:
        summary = create_mail_invoice_stress_test(
            database_url=database_url,
            owner_user_id=args.owner_user_id,
            output_dir=args.output_dir,
            pdf_count=args.pdf_count,
            seed=args.seed,
            today=args.date,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    print(
        f"Created {summary['pdf_count']} stress PDFs and seeded "
        f"{summary['ap_context_record_count']} matching AP context records for "
        f"{summary['owner_user_id']}."
    )
    print(
        f"Verified procurement context for {summary['validated_context_match_count']}/"
        f"{summary['pdf_count']} PDFs. Files are in {summary['output_dir']}."
    )


if __name__ == "__main__":
    main()
