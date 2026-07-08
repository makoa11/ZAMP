from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .invoice_generator import (
    BASE_TEMPLATES,
    PAPER_FORMATS,
    generate_invoice,
    generate_invoice_samples,
)
from .invoice_fixtures import generate_invoice_stress_fixtures, write_invoice_manifest
from .invoice_pdf import render_invoice_pdf


DEFAULT_OUTPUT_DIR = Path("storage/test_pdfs")


def generate_test_pdfs(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    count: int = 15,
    seed: int = 1000,
    today: date | None = None,
    pdf_count: int | None = None,
    samples_per_pdf: int = 1,
    write_manifests: bool = False,
    stress_cases: bool = False,
) -> list[Path]:
    if count < 1 or count > 60:
        raise ValueError("count must be between 1 and 60.")
    if seed < 1:
        raise ValueError("seed must be greater than 0.")
    if pdf_count is not None and pdf_count < 1:
        raise ValueError("pdf-count must be greater than 0.")
    if samples_per_pdf < 1 or samples_per_pdf > 60:
        raise ValueError("samples-per-pdf must be between 1 and 60.")
    if stress_cases and pdf_count is not None:
        raise ValueError("--stress-cases cannot be combined with --pdf-count.")

    output_dir.mkdir(parents=True, exist_ok=True)
    written_paths: list[Path] = []

    if stress_cases:
        for fixture in generate_invoice_stress_fixtures(seed=seed, today=today):
            path = output_dir / fixture.filename
            path.write_bytes(render_invoice_pdf(fixture.samples))
            if write_manifests:
                write_invoice_manifest(
                    path,
                    fixture.samples,
                    suite="stress",
                    fixture_slug=fixture.slug,
                )
            written_paths.append(path)
        return written_paths

    if pdf_count is not None:
        sequence_width = max(4, len(str(pdf_count)))
        for pdf_index in range(pdf_count):
            paper = PAPER_FORMATS[pdf_index % len(PAPER_FORMATS)]
            first_sample_index = pdf_index * samples_per_pdf
            samples = [
                generate_invoice(
                    template_slug=BASE_TEMPLATES[
                        (first_sample_index + sample_index) % len(BASE_TEMPLATES)
                    ].slug,
                    paper_slug=paper.slug,
                    seed=seed + ((first_sample_index + sample_index) * 97),
                    variation_index=first_sample_index + sample_index,
                    today=today,
                )
                for sample_index in range(samples_per_pdf)
            ]
            path = output_dir / f"invoice-sample-{pdf_index + 1:0{sequence_width}d}-{paper.slug}.pdf"
            path.write_bytes(render_invoice_pdf(samples))
            if write_manifests:
                write_invoice_manifest(path, samples)
            written_paths.append(path)
        return written_paths

    for paper in PAPER_FORMATS:
        samples = generate_invoice_samples(
            paper_slug=paper.slug,
            count=count,
            seed=seed,
            today=today,
        )
        path = output_dir / f"invoice-samples-{paper.slug}.pdf"
        path.write_bytes(render_invoice_pdf(samples))
        if write_manifests:
            write_invoice_manifest(path, samples)
        written_paths.append(path)

    return written_paths


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate invoice sample PDFs for all supported paper variations."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where generated PDFs are written.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=15,
        help="Samples per bundled variation PDF when --pdf-count is not used.",
    )
    parser.add_argument(
        "--pdf-count",
        type=int,
        default=None,
        help="Total number of individual PDF files to save, rotated across paper variations.",
    )
    parser.add_argument(
        "--samples-per-pdf",
        type=int,
        default=1,
        help="Samples/pages per PDF when --pdf-count is used.",
    )
    parser.add_argument(
        "--write-manifests",
        action="store_true",
        help="Write expected-output JSON manifests beside generated PDFs.",
    )
    parser.add_argument(
        "--stress-cases",
        action="store_true",
        help="Write deterministic parser stress PDFs for multi-page, ambiguous label, and currency cases.",
    )
    parser.add_argument("--seed", type=int, default=1000, help="Seed for deterministic samples.")
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="Invoice date in YYYY-MM-DD format. Defaults to today.",
    )
    args = parser.parse_args()

    try:
        paths = generate_test_pdfs(
            output_dir=args.output_dir,
            count=args.count,
            seed=args.seed,
            today=args.date,
            pdf_count=args.pdf_count,
            samples_per_pdf=args.samples_per_pdf,
            write_manifests=args.write_manifests,
            stress_cases=args.stress_cases,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    for path in paths:
        print(path)
        if args.write_manifests:
            print(path.with_suffix(".manifest.json"))


if __name__ == "__main__":
    main()
