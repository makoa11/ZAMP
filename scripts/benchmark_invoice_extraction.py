from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.invoice_parser import parse_invoice_pdf  # noqa: E402


def benchmark_corpus(
    *,
    input_dir: Path,
    limit: int | None = None,
    enable_ocr: bool = True,
) -> dict[str, Any]:
    paths = sorted(input_dir.glob("*.pdf"))
    if limit is not None:
        paths = paths[:limit]
    field_hits: Counter[str] = Counter()
    field_totals: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    route_counts: Counter[str] = Counter()
    tag_scores: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    durations: list[float] = []
    excluded_multi_document = 0
    line_item_true_positives = 0
    line_item_predicted = 0
    line_item_expected = 0
    files: list[dict[str, Any]] = []

    for path in paths:
        manifest_path = path.with_suffix(".manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        documents = manifest.get("documents") if isinstance(manifest.get("documents"), list) else []
        started = time.perf_counter()
        result = parse_invoice_pdf(path.read_bytes(), source_id=str(path), enable_ocr=enable_ocr)
        duration_ms = (time.perf_counter() - started) * 1000
        durations.append(duration_ms)
        status_counts[str(result.get("status") or "unknown")] += 1
        pipeline = result.get("pipeline") if isinstance(result.get("pipeline"), dict) else {}
        route_counts[str(pipeline.get("route") or "unknown")] += 1

        file_result: dict[str, Any] = {
            "pdf": str(path),
            "status": result.get("status"),
            "route": pipeline.get("route"),
            "duration_ms": round(duration_ms, 2),
        }
        if len(documents) != 1:
            excluded_multi_document += 1
            file_result["scored"] = False
            file_result["reason"] = "manifest_does_not_contain_exactly_one_document"
            files.append(file_result)
            continue

        expected = documents[0]
        comparisons = _field_comparisons(result.get("fields"), expected)
        item_tp, item_predicted, item_expected = _line_item_counts(
            result.get("fields"),
            expected,
        )
        line_item_true_positives += item_tp
        line_item_predicted += item_predicted
        line_item_expected += item_expected
        file_result["scored"] = True
        file_result["field_matches"] = comparisons
        tags = [str(tag) for tag in manifest.get("challenge_tags") or []]
        for field_name, matched in comparisons.items():
            field_totals[field_name] += 1
            field_hits[field_name] += int(matched)
            for tag in tags:
                tag_scores[tag][0] += int(matched)
                tag_scores[tag][1] += 1
        files.append(file_result)

    return {
        "schema_version": 1,
        "input_dir": str(input_dir),
        "total": len(paths),
        "scored": len(paths) - excluded_multi_document,
        "excluded_multi_document": excluded_multi_document,
        "ocr_enabled": enable_ocr,
        "statuses": dict(status_counts),
        "routes": dict(route_counts),
        "field_accuracy": {
            field_name: {
                "matched": field_hits[field_name],
                "total": field_totals[field_name],
                "accuracy": round(field_hits[field_name] / field_totals[field_name], 4)
                if field_totals[field_name]
                else None,
            }
            for field_name in sorted(field_totals)
        },
        "challenge_accuracy": {
            tag: {
                "matched": score[0],
                "total": score[1],
                "accuracy": round(score[0] / score[1], 4) if score[1] else None,
            }
            for tag, score in sorted(tag_scores.items())
        },
        "line_item_metrics": _line_item_metrics(
            line_item_true_positives,
            line_item_predicted,
            line_item_expected,
        ),
        "latency_ms": _latency_summary(durations),
        "files": files,
    }


def _field_comparisons(raw_fields: Any, expected: dict[str, Any]) -> dict[str, bool]:
    fields = raw_fields if isinstance(raw_fields, dict) else {}
    expected_values = {
        "invoice_number": expected.get("invoice_number"),
        "issue_date": _nested(expected.get("issue_date"), "value"),
        "due_date": _nested(expected.get("due_date"), "value"),
        "currency": expected.get("currency"),
        "seller": _nested(expected.get("seller"), "name"),
        "buyer": _nested(expected.get("buyer"), "name"),
        "balance_due": _nested(_nested_dict(expected.get("amounts"), "balance_due"), "value"),
    }
    comparisons: dict[str, bool] = {}
    for field_name, expected_value in expected_values.items():
        field = fields.get(field_name)
        if field_name == "balance_due":
            actual = field.get("amount") if isinstance(field, dict) else None
        else:
            actual = field.get("value") if isinstance(field, dict) else None
        if field_name in {"seller", "buyer"} and isinstance(actual, str):
            actual = actual.splitlines()[0]
        comparisons[field_name] = (
            _money_equal(actual, expected_value)
            if field_name == "balance_due"
            else _canonical(actual) == _canonical(expected_value)
        )
    comparisons["line_items"] = _line_item_match(fields.get("line_items"), expected)
    return comparisons


def _line_item_match(actual: Any, expected_document: Any) -> bool:
    expected = expected_document.get("line_items") if isinstance(expected_document, dict) else None
    if not isinstance(actual, list) or not isinstance(expected, list):
        return False
    table = expected_document.get("table") if isinstance(expected_document.get("table"), dict) else {}
    show_description = bool(table.get("show_description", True))
    actual_rows = [
        (
            _canonical(_nested(_nested_dict(item, "description"), "value")),
            _nested(_nested_dict(item, "quantity"), "value"),
            _nested(_nested_dict(item, "unit_price"), "amount"),
            _nested(_nested_dict(item, "amount"), "amount"),
        )
        for item in actual
        if isinstance(item, dict)
    ]
    expected_rows = [
        (
            _canonical(
                " - ".join(
                    part
                    for part in (
                        str(item.get("name") or ""),
                        str(item.get("description") or "") if show_description else "",
                    )
                    if part
                )
            ),
            item.get("quantity"),
            _nested(_nested_dict(item, "unit_price"), "visible_value"),
            _nested(_nested_dict(item, "amount"), "visible_value"),
        )
        for item in expected
        if isinstance(item, dict)
    ]
    if len(actual_rows) != len(expected_rows):
        return False
    return all(
        actual[0] == expected[0]
        and _money_equal(actual[1], expected[1])
        and _money_equal(actual[2], expected[2])
        and _money_equal(actual[3], expected[3])
        for actual, expected in zip(actual_rows, expected_rows)
    )


def _line_item_counts(raw_fields: Any, expected_document: dict[str, Any]) -> tuple[int, int, int]:
    fields = raw_fields if isinstance(raw_fields, dict) else {}
    actual = fields.get("line_items") if isinstance(fields.get("line_items"), list) else []
    expected = expected_document.get("line_items") if isinstance(expected_document.get("line_items"), list) else []
    table = expected_document.get("table") if isinstance(expected_document.get("table"), dict) else {}
    show_description = bool(table.get("show_description", True))
    actual_keys = Counter(
        (
            _canonical(_nested(_nested_dict(item, "description"), "value")),
            _money_key(_nested(_nested_dict(item, "amount"), "amount")),
        )
        for item in actual
        if isinstance(item, dict)
    )
    expected_keys = Counter(
        (
            _canonical(
                " - ".join(
                    part
                    for part in (
                        str(item.get("name") or ""),
                        str(item.get("description") or "") if show_description else "",
                    )
                    if part
                )
            ),
            _money_key(_nested(_nested_dict(item, "amount"), "visible_value")),
        )
        for item in expected
        if isinstance(item, dict)
    )
    matched = sum((actual_keys & expected_keys).values())
    return matched, sum(actual_keys.values()), sum(expected_keys.values())


def _line_item_metrics(true_positives: int, predicted: int, expected: int) -> dict[str, Any]:
    precision = true_positives / predicted if predicted else 0.0
    recall = true_positives / expected if expected else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "matched_rows": true_positives,
        "predicted_rows": predicted,
        "expected_rows": expected,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def _latency_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "max": None}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, max(0, round(0.95 * len(ordered) + 0.5) - 1))
    return {
        "p50": round(statistics.median(ordered), 2),
        "p95": round(ordered[p95_index], 2),
        "max": round(ordered[-1], 2),
    }


def _canonical(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _money_equal(first: Any, second: Any) -> bool:
    try:
        return Decimal(str(first)).quantize(Decimal("0.01")) == Decimal(str(second)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return False


def _money_key(value: Any) -> str:
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    except (InvalidOperation, TypeError, ValueError):
        return ""


def _nested(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def _nested_dict(value: Any, key: str) -> dict[str, Any]:
    nested = value.get(key) if isinstance(value, dict) else None
    return nested if isinstance(nested, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark invoice extraction against generated manifests.")
    parser.add_argument("--input-dir", type=Path, default=Path("storage/test_pdfs"))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--disable-ocr", action="store_true")
    args = parser.parse_args()
    summary = benchmark_corpus(
        input_dir=args.input_dir,
        limit=args.limit,
        enable_ocr=not args.disable_ocr,
    )
    payload = json.dumps(summary, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
