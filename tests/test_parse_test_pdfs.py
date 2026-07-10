from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.parse_test_pdfs import _pdf_paths


class ParseTestPdfsScriptTests(unittest.TestCase):
    def test_pdf_paths_includes_any_pdf_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            expected = [
                root / "custom-client-drop.pdf",
                root / "INVOICE.PDF",
                root / "invoice-sample-0001-a4-ledger-clean.pdf",
            ]
            for path in expected:
                path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            (root / "invoice-sample-0001-a4-ledger-clean.manifest.json").write_text("{}", encoding="utf-8")
            (root / "notes.txt").write_text("not a pdf", encoding="utf-8")

            self.assertEqual(_pdf_paths(root), sorted(expected))


if __name__ == "__main__":
    unittest.main()
