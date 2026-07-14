from __future__ import annotations

import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.invoice_ai import (
    AI_INVOICE_EXTRACTION_PROMPT,
    AiExtractionError,
    GeminiAiExtractionClient,
    promote_ai_extraction,
    should_run_ai_fallback,
    validate_ai_extraction,
)
from app.mail_worker import _run_ai_fallback_if_enabled


def _value(value: object, confidence: float = 0.98) -> dict[str, object]:
    return {"value": value, "confidence": confidence if value is not None else 0}


def valid_extraction() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "document_type": "invoice",
        "fields": {
            "seller": _value("Acme LLC"),
            "buyer": _value("Buyer Inc"),
            "invoice_number": _value("INV-100"),
            "issue_date": _value("2026-07-01"),
            "due_date": _value("2026-07-31"),
            "purchase_order": _value("PO-9"),
            "terms": _value("Net 30"),
            "currency": _value("USD"),
            "subtotal": _value(100.0),
            "discount": _value(0.0),
            "tax": _value(10.0),
            "shipping": _value(0.0),
            "paid": _value(0.0),
            "balance_due": _value(110.0),
            "payment_instructions": _value(None),
            "line_items": [
                {
                    "description": _value("Consulting"),
                    "quantity": _value(1.0),
                    "unit_price": _value(100.0),
                    "amount": _value(100.0),
                }
            ],
        },
        "warnings": [],
    }


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int) -> bytes:
        return self.body[:size]


class InvoiceAiContractTests(unittest.TestCase):
    def test_prompt_defends_schema_and_untrusted_document_text(self) -> None:
        self.assertIn("Return exactly one JSON object", AI_INVOICE_EXTRACTION_PROMPT)
        self.assertIn("untrusted data", AI_INVOICE_EXTRACTION_PROMPT)
        self.assertIn("Do not invent", AI_INVOICE_EXTRACTION_PROMPT)

    def test_validator_accepts_exact_schema(self) -> None:
        self.assertEqual(validate_ai_extraction(valid_extraction())["document_type"], "invoice")

    def test_validator_rejects_extra_properties(self) -> None:
        extraction = valid_extraction()
        extraction["explanation"] = "not allowed"
        with self.assertRaisesRegex(AiExtractionError, "extra=.*explanation"):
            validate_ai_extraction(extraction)

    def test_validator_rejects_confident_null_and_invalid_calendar_date(self) -> None:
        extraction = valid_extraction()
        extraction["fields"]["terms"] = {"value": None, "confidence": 0.8}  # type: ignore[index]
        with self.assertRaisesRegex(AiExtractionError, "must be 0"):
            validate_ai_extraction(extraction)

        extraction = valid_extraction()
        extraction["fields"]["issue_date"] = _value("2026-02-30")  # type: ignore[index]
        with self.assertRaisesRegex(AiExtractionError, "valid YYYY-MM-DD"):
            validate_ai_extraction(extraction)

    def test_gemini_transport_sends_pdf_schema_and_api_key_in_native_format(self) -> None:
        body = json.dumps(
            {
                "candidates": [
                    {"content": {"parts": [{"text": json.dumps(valid_extraction())}]}}
                ],
                "usageMetadata": {"totalTokenCount": 123},
            }
        ).encode()
        client = GeminiAiExtractionClient(
            endpoint=(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "stale-model:generateContent"
            ),
            model="gemini-3.1-pro-preview",
            api_key="gemini-secret",
        )
        with patch("urllib.request.urlopen", return_value=FakeResponse(body)) as urlopen:
            output = client.extract(
                b"%PDF",
                filename="invoice.pdf",
                extracted_text="OCR text",
            )

        self.assertEqual(output["schema_version"], "1.0")
        request = urlopen.call_args.args[0]
        self.assertIn("/models/gemini-3.1-pro-preview:generateContent", request.full_url)
        self.assertEqual(request.get_header("X-goog-api-key"), "gemini-secret")
        self.assertIsNone(request.get_header("Authorization"))
        payload = json.loads(request.data)
        parts = payload["contents"][0]["parts"]
        self.assertIn("OCR text", parts[0]["text"])
        self.assertEqual(parts[1]["inline_data"]["mime_type"], "application/pdf")
        self.assertEqual(parts[1]["inline_data"]["data"], "JVBERg==")
        generation_config = payload["generationConfig"]
        self.assertEqual(generation_config["responseMimeType"], "application/json")
        schema = generation_config["responseJsonSchema"]
        self.assertNotIn("$schema", schema)
        self.assertEqual(schema["properties"]["schema_version"]["enum"], ["1.0"])

    def test_gemini_transport_reports_blocked_or_missing_candidates(self) -> None:
        body = json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}).encode()
        client = GeminiAiExtractionClient(
            endpoint=(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-3.1-pro-preview:generateContent"
            ),
            model="gemini-3.1-pro-preview",
            api_key="gemini-secret",
        )
        with patch("urllib.request.urlopen", return_value=FakeResponse(body)):
            with self.assertRaisesRegex(AiExtractionError, "no candidates.*SAFETY"):
                client.extract(b"%PDF", filename="invoice.pdf")

    def test_promotion_keeps_provenance_and_gates_low_confidence(self) -> None:
        prior = {
            "status": "needs_review",
            "warnings": ["OCR failed"],
            "pipeline": {"route": "local_ocr"},
        }
        promoted = promote_ai_extraction(prior, valid_extraction(), model="model-a")
        self.assertEqual(promoted["status"], "parsed")
        self.assertEqual(promoted["pipeline"]["route"], "ai")
        self.assertEqual(promoted["fields"]["invoice_number"]["method"], "ai:model-a")

        low_confidence = copy.deepcopy(valid_extraction())
        low_confidence["fields"]["invoice_number"]["confidence"] = 0.4  # type: ignore[index]
        reviewed = promote_ai_extraction(prior, low_confidence, model="model-a")
        self.assertEqual(reviewed["status"], "needs_review")
        self.assertEqual(reviewed["review"]["reason"], "low_confidence_or_ambiguous_fields")

    def test_fallback_only_runs_after_an_ocr_review_result(self) -> None:
        self.assertFalse(should_run_ai_fallback({"status": "parsed", "ocr_used": True}))
        self.assertFalse(should_run_ai_fallback({"status": "needs_review"}))
        self.assertTrue(
            should_run_ai_fallback(
                {"status": "needs_review", "ocr_failed_parts": ["invoice_number"]}
            )
        )

    def test_worker_requires_user_opt_in_before_sending_pdf(self) -> None:
        config = SimpleNamespace(
            ai_extraction_endpoint=(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-3.1-pro-preview:generateContent"
            ),
            ai_extraction_model="gemini-3.1-pro-preview",
            ai_extraction_api_key="gemini-secret",
            ai_extraction_timeout_seconds=10,
            ai_extraction_max_pdf_bytes=1024,
        )
        prior = {
            "status": "needs_review",
            "ocr_failed_parts": ["invoice_number"],
            "warnings": [],
            "pages": [],
            "pipeline": {"route": "local_ocr"},
        }
        disabled = SimpleNamespace(
            config=config,
            repo=SimpleNamespace(get_ai_extraction_enabled=lambda **_: False),
        )
        with patch.object(GeminiAiExtractionClient, "extract") as extract:
            unchanged = _run_ai_fallback_if_enabled(
                disabled,
                result=prior,
                content=b"%PDF",
                filename="invoice.pdf",
                owner_user_id="user-1",
            )
        extract.assert_not_called()
        self.assertIs(unchanged, prior)

        enabled = SimpleNamespace(
            config=config,
            repo=SimpleNamespace(get_ai_extraction_enabled=lambda **_: True),
        )
        with patch.object(
            GeminiAiExtractionClient,
            "extract",
            return_value=valid_extraction(),
        ) as extract:
            promoted = _run_ai_fallback_if_enabled(
                enabled,
                result=prior,
                content=b"%PDF",
                filename="invoice.pdf",
                owner_user_id="user-1",
            )
        extract.assert_called_once()
        self.assertEqual(promoted["status"], "parsed")
        self.assertTrue(promoted["ai_used"])

    def test_worker_uses_native_gemini_transport(self) -> None:
        config = SimpleNamespace(
            ai_extraction_endpoint=(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-3.1-pro-preview:generateContent"
            ),
            ai_extraction_model="gemini-3.1-pro-preview",
            ai_extraction_api_key="gemini-secret",
            ai_extraction_timeout_seconds=10,
            ai_extraction_max_pdf_bytes=1024,
        )
        prior = {
            "status": "needs_review",
            "ocr_failed_parts": ["invoice_number"],
            "warnings": [],
            "pages": [],
            "pipeline": {"route": "local_ocr"},
        }
        integration = SimpleNamespace(
            config=config,
            repo=SimpleNamespace(get_ai_extraction_enabled=lambda **_: True),
        )

        with patch.object(
            GeminiAiExtractionClient,
            "extract",
            return_value=valid_extraction(),
        ) as gemini_extract:
            promoted = _run_ai_fallback_if_enabled(
                integration,
                result=prior,
                content=b"%PDF",
                filename="invoice.pdf",
                owner_user_id="user-1",
            )

        gemini_extract.assert_called_once()
        self.assertTrue(promoted["ai_used"])

    def test_worker_rejects_non_gemini_endpoint_without_sending_pdf(self) -> None:
        config = SimpleNamespace(
            ai_extraction_endpoint="https://ai.example/extract",
            ai_extraction_model="model-a",
            ai_extraction_api_key="secret",
            ai_extraction_timeout_seconds=10,
            ai_extraction_max_pdf_bytes=1024,
        )
        prior = {
            "status": "needs_review",
            "ocr_failed_parts": ["invoice_number"],
            "warnings": [],
        }
        integration = SimpleNamespace(
            config=config,
            repo=SimpleNamespace(get_ai_extraction_enabled=lambda **_: True),
        )

        with patch.object(GeminiAiExtractionClient, "extract") as extract:
            result = _run_ai_fallback_if_enabled(
                integration,
                result=prior,
                content=b"%PDF",
                filename="invoice.pdf",
                owner_user_id="user-1",
            )

        extract.assert_not_called()
        self.assertEqual(result["ai"]["status"], "not_configured")
        self.assertFalse(result["ai"]["attempted"])

    def test_worker_reports_missing_gemini_configuration(self) -> None:
        config = SimpleNamespace(
            ai_extraction_endpoint=None,
            ai_extraction_model=None,
            ai_extraction_api_key=None,
            ai_extraction_timeout_seconds=10,
            ai_extraction_max_pdf_bytes=1024,
        )
        prior = {
            "status": "needs_review",
            "ocr_failed_parts": ["invoice_number"],
            "warnings": [],
        }
        integration = SimpleNamespace(
            config=config,
            repo=SimpleNamespace(get_ai_extraction_enabled=lambda **_: True),
        )

        with patch.object(GeminiAiExtractionClient, "extract") as extract:
            result = _run_ai_fallback_if_enabled(
                integration,
                result=prior,
                content=b"%PDF",
                filename="invoice.pdf",
                owner_user_id="user-1",
            )

        extract.assert_not_called()
        self.assertEqual(result["ai"]["status"], "not_configured")
        self.assertFalse(result["ai"]["attempted"])

    def test_worker_sanitizes_known_gemini_failure(self) -> None:
        config = SimpleNamespace(
            ai_extraction_endpoint=(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-3.1-pro-preview:generateContent"
            ),
            ai_extraction_model="gemini-3.1-pro-preview",
            ai_extraction_api_key="gemini-secret",
            ai_extraction_timeout_seconds=10,
            ai_extraction_max_pdf_bytes=1024,
        )
        prior = {
            "status": "needs_review",
            "ocr_failed_parts": ["invoice_number"],
            "warnings": [],
        }
        integration = SimpleNamespace(
            config=config,
            repo=SimpleNamespace(get_ai_extraction_enabled=lambda **_: True),
        )

        with patch.object(
            GeminiAiExtractionClient,
            "extract",
            side_effect=AiExtractionError("provider detail must stay internal"),
        ), patch("app.mail_worker.logger.warning") as warning:
            result = _run_ai_fallback_if_enabled(
                integration,
                result=prior,
                content=b"%PDF",
                filename="invoice.pdf",
                owner_user_id="user-1",
            )

        self.assertEqual(result["ai"]["status"], "failed")
        self.assertNotIn("provider detail must stay internal", " ".join(result["warnings"]))
        warning.assert_called_once()
        self.assertIn("provider detail must stay internal", str(warning.call_args))


if __name__ == "__main__":
    unittest.main()
