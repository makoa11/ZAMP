from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.invoice_degradation import (  # noqa: E402
    DEFAULT_DEGRADATION_PROFILES,
    degrade_pdf_to_image_pdf,
)


def generate_degraded_corpus(*, input_dir: Path, output_dir: Path, limit: int | None = None) -> int:
    paths = sorted(input_dir.glob("*.pdf"))
    if limit is not None:
        paths = paths[:limit]
    output_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for pdf_index, path in enumerate(paths):
        manifest_path = path.with_suffix(".manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        for profile_index, profile in enumerate(DEFAULT_DEGRADATION_PROFILES):
            output_path = output_dir / f"{path.stem}--{profile.name}.pdf"
            output_path.write_bytes(
                degrade_pdf_to_image_pdf(
                    path.read_bytes(),
                    profile=profile,
                    seed=1000 + pdf_index * 17 + profile_index,
                )
            )
            if manifest:
                degraded_manifest = dict(manifest)
                degraded_manifest["source_pdf"] = path.name
                degraded_manifest["degradation"] = profile.as_dict()
                output_path.with_suffix(".manifest.json").write_text(
                    json.dumps(degraded_manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            written += 1
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic image-only degraded invoice PDFs.")
    parser.add_argument("--input-dir", type=Path, default=Path("storage/test_pdfs"))
    parser.add_argument("--output-dir", type=Path, default=Path("storage/test_pdfs_degraded"))
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    count = generate_degraded_corpus(input_dir=args.input_dir, output_dir=args.output_dir, limit=args.limit)
    print(f"Wrote {count} degraded PDFs to {args.output_dir}.")


if __name__ == "__main__":
    main()
