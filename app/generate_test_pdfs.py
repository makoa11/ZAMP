from __future__ import annotations

import argparse
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .invoice_generator import (
    BASE_TEMPLATES,
    PAPER_FORMATS,
    generate_invoice,
)
from .invoice_fixtures import (
    InvoicePdfFixture,
    generate_invoice_stress_fixtures,
    write_invoice_manifest,
)
from .invoice_pdf import render_invoice_pdf


DEFAULT_OUTPUT_DIR = Path("storage/test_pdfs")
DEFAULT_STANDARD_PDF_COUNT = 150
DEFAULT_STRESS_PDF_COUNT = 100
DEFAULT_PDF_COUNT = DEFAULT_STANDARD_PDF_COUNT + DEFAULT_STRESS_PDF_COUNT
DEFAULT_SEED = 1000
STRESS_FIXTURE_TYPE_COUNT = 3
PAPER_TEMPLATE_COMBINATION_COUNT = len(PAPER_FORMATS) * len(BASE_TEMPLATES)
DATE_SPREAD_DAYS = 366


@dataclass(frozen=True)
class InvoiceCorpusEntry:
    pdf_index: int
    pdf_filename: str
    suite: str
    fixture_slug: str | None
    samples: list[dict[str, Any]]
    generation_params: dict[str, Any]


def iter_invoice_corpus(
    *,
    pdf_count: int = DEFAULT_PDF_COUNT,
    seed: int = DEFAULT_SEED,
    today: date | None = None,
) -> Iterator[InvoiceCorpusEntry]:
    """Yield the deterministic invoice corpus without writing PDFs."""
    if pdf_count < 2:
        raise ValueError("pdf-count must be at least 2.")
    if seed < 1:
        raise ValueError("seed must be greater than 0.")

    invoice_date = today or date.today()
    standard_pdf_count, stress_pdf_count = _corpus_counts(pdf_count)
    sequence_width = max(4, len(str(pdf_count)))

    for pdf_index in range(standard_pdf_count):
        sample_config = _standard_sample_config(
            pdf_index=pdf_index,
            seed=seed,
            invoice_date=invoice_date,
        )
        sample = _diverse_standard_sample_from_config(sample_config)
        yield InvoiceCorpusEntry(
            pdf_index=pdf_index,
            pdf_filename=_standard_pdf_filename(sample, pdf_index, sequence_width),
            suite="standard",
            fixture_slug=None,
            samples=[sample],
            generation_params={
                "schema_version": 1,
                "pdf_count": pdf_count,
                "seed": seed,
                "date": invoice_date.isoformat(),
                "sequence_width": sequence_width,
                "standard_pdf_count": standard_pdf_count,
                "stress_pdf_count": stress_pdf_count,
                "suite": "standard",
                "pdf_index": pdf_index,
                **sample_config,
                "sample_date": sample_config["sample_date"].isoformat(),
            },
        )

    for stress_index in range(stress_pdf_count):
        fixture_config = _stress_fixture_config(
            stress_index=stress_index,
            seed=seed,
            invoice_date=invoice_date,
        )
        fixture = _stress_fixture_from_config(fixture_config)
        yield InvoiceCorpusEntry(
            pdf_index=standard_pdf_count + stress_index,
            pdf_filename=_stress_pdf_filename(fixture.slug, stress_index, sequence_width),
            suite="stress",
            fixture_slug=fixture.slug,
            samples=fixture.samples,
            generation_params={
                "schema_version": 1,
                "pdf_count": pdf_count,
                "seed": seed,
                "date": invoice_date.isoformat(),
                "sequence_width": sequence_width,
                "standard_pdf_count": standard_pdf_count,
                "stress_pdf_count": stress_pdf_count,
                "suite": "stress",
                "pdf_index": standard_pdf_count + stress_index,
                "stress_index": stress_index,
                **fixture_config,
                "fixture_date": fixture_config["fixture_date"].isoformat(),
                "fixture_slug": fixture.slug,
            },
        )


def generate_test_pdfs(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    pdf_count: int = DEFAULT_PDF_COUNT,
    seed: int = DEFAULT_SEED,
    today: date | None = None,
) -> list[Path]:
    """Write a diverse invoice parser corpus and return the PDF paths.

    The default corpus is intentionally broad: 150 standard PDFs and 100 stress
    PDFs. Standard PDFs cover every paper/template pairing with shifted capture
    profiles and varied invoice dates. Stress PDFs cycle through the parser stress
    fixture families with different seeds and dates. A manifest is written beside
    every PDF so parser runs have expected output to compare against.
    """
    if pdf_count < 2:
        raise ValueError("pdf-count must be at least 2.")
    if seed < 1:
        raise ValueError("seed must be greater than 0.")

    output_dir.mkdir(parents=True, exist_ok=True)
    written_paths: list[Path] = []

    for entry in iter_invoice_corpus(
        pdf_count=pdf_count,
        seed=seed,
        today=today,
    ):
        path = output_dir / entry.pdf_filename
        path.write_bytes(render_invoice_pdf(entry.samples))
        write_invoice_manifest(
            path,
            entry.samples,
            suite=entry.suite,
            fixture_slug=entry.fixture_slug,
        )
        written_paths.append(path)

    return written_paths


def _corpus_counts(pdf_count: int) -> tuple[int, int]:
    stress_pdf_count = round(
        pdf_count * (DEFAULT_STRESS_PDF_COUNT / DEFAULT_PDF_COUNT)
    )
    stress_pdf_count = max(1, min(pdf_count - 1, stress_pdf_count))
    return pdf_count - stress_pdf_count, stress_pdf_count


def _diverse_standard_sample(
    *,
    pdf_index: int,
    seed: int,
    invoice_date: date,
) -> dict[str, Any]:
    return _diverse_standard_sample_from_config(
        _standard_sample_config(
            pdf_index=pdf_index,
            seed=seed,
            invoice_date=invoice_date,
        )
    )


def _standard_sample_config(
    *,
    pdf_index: int,
    seed: int,
    invoice_date: date,
) -> dict[str, Any]:
    paper = PAPER_FORMATS[pdf_index % len(PAPER_FORMATS)]
    template = BASE_TEMPLATES[(pdf_index // len(PAPER_FORMATS)) % len(BASE_TEMPLATES)]
    round_index = pdf_index // PAPER_TEMPLATE_COMBINATION_COUNT
    variation_index = pdf_index + round_index
    sample_seed = seed + (pdf_index * 1009) + (round_index * 37)
    sample_date = invoice_date - timedelta(days=(pdf_index * 11) % DATE_SPREAD_DAYS)
    return {
        "standard_index": pdf_index,
        "paper_slug": paper.slug,
        "template_slug": template.slug,
        "round_index": round_index,
        "variation_index": variation_index,
        "sample_seed": sample_seed,
        "sample_date": sample_date,
    }


def _diverse_standard_sample_from_config(config: dict[str, Any]) -> dict[str, Any]:
    return generate_invoice(
        template_slug=str(config["template_slug"]),
        paper_slug=str(config["paper_slug"]),
        seed=int(config["sample_seed"]),
        variation_index=int(config["variation_index"]),
        today=config["sample_date"],
    )


def _standard_pdf_path(
    output_dir: Path,
    sample: dict[str, Any],
    pdf_index: int,
    sequence_width: int,
) -> Path:
    return output_dir / _standard_pdf_filename(sample, pdf_index, sequence_width)


def _standard_pdf_filename(
    sample: dict[str, Any],
    pdf_index: int,
    sequence_width: int,
) -> str:
    paper = sample["paper"] if isinstance(sample.get("paper"), dict) else {}
    template = sample["template"] if isinstance(sample.get("template"), dict) else {}
    paper_slug = str(paper.get("slug") or "paper")
    template_slug = str(template.get("slug") or "template")
    return (
        f"invoice-sample-{pdf_index + 1:0{sequence_width}d}-{paper_slug}-{template_slug}.pdf"
    )


def _stress_fixture(
    *,
    stress_index: int,
    seed: int,
    invoice_date: date,
) -> InvoicePdfFixture:
    return _stress_fixture_from_config(
        _stress_fixture_config(
            stress_index=stress_index,
            seed=seed,
            invoice_date=invoice_date,
        )
    )


def _stress_fixture_config(
    *,
    stress_index: int,
    seed: int,
    invoice_date: date,
) -> dict[str, Any]:
    fixture_round = stress_index // STRESS_FIXTURE_TYPE_COUNT
    fixture_index = stress_index % STRESS_FIXTURE_TYPE_COUNT
    fixture_seed = seed + 50_000 + (fixture_round * 1009) + (fixture_index * 173)
    fixture_date = invoice_date - timedelta(days=(stress_index * 17) % DATE_SPREAD_DAYS)
    return {
        "fixture_round": fixture_round,
        "fixture_index": fixture_index,
        "fixture_seed": fixture_seed,
        "fixture_date": fixture_date,
    }


def _stress_fixture_from_config(config: dict[str, Any]) -> InvoicePdfFixture:
    return generate_invoice_stress_fixtures(
        seed=int(config["fixture_seed"]),
        today=config["fixture_date"],
    )[int(config["fixture_index"])]


def _stress_pdf_path(
    output_dir: Path,
    fixture_slug: str,
    stress_index: int,
    sequence_width: int,
) -> Path:
    return output_dir / _stress_pdf_filename(fixture_slug, stress_index, sequence_width)


def _stress_pdf_filename(
    fixture_slug: str,
    stress_index: int,
    sequence_width: int,
) -> str:
    return f"invoice-stress-{stress_index + 1:0{sequence_width}d}-{fixture_slug}.pdf"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the default diverse invoice PDF parser corpus."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where generated PDFs are written.",
    )
    parser.add_argument(
        "--pdf-count",
        type=int,
        default=DEFAULT_PDF_COUNT,
        help=(
            "Total PDFs to write. Defaults to 250: 150 standard and 100 stress PDFs."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="Base seed for deterministic samples.",
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="Anchor date in YYYY-MM-DD format. Defaults to today.",
    )
    args = parser.parse_args()

    try:
        paths = generate_test_pdfs(
            output_dir=args.output_dir,
            seed=args.seed,
            today=args.date,
            pdf_count=args.pdf_count,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    for path in paths:
        print(path)
    print(f"Wrote {len(paths)} PDFs and {len(paths)} manifests to {args.output_dir}.")


if __name__ == "__main__":
    main()
