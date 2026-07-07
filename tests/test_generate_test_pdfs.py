from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.generate_test_pdfs import generate_test_pdfs


class GenerateTestPdfsTests(unittest.TestCase):
    def test_generate_test_pdfs_writes_all_paper_variations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = generate_test_pdfs(
                output_dir=Path(temp_dir),
                count=1,
                seed=500,
                today=date(2026, 7, 7),
            )

            self.assertEqual(
                [path.name for path in paths],
                [
                    "invoice-samples-a4.pdf",
                    "invoice-samples-a4-half-horizontal.pdf",
                    "invoice-samples-a4-third-horizontal.pdf",
                ],
            )
            for path in paths:
                content = path.read_bytes()
                self.assertTrue(content.startswith(b"%PDF-1.4"))
                self.assertTrue(content.rstrip().endswith(b"%%EOF"))

    def test_generate_test_pdfs_can_write_requested_number_of_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = generate_test_pdfs(
                output_dir=Path(temp_dir),
                pdf_count=5,
                seed=500,
                today=date(2026, 7, 7),
            )

            self.assertEqual(
                [path.name for path in paths],
                [
                    "invoice-sample-0001-a4.pdf",
                    "invoice-sample-0002-a4-half-horizontal.pdf",
                    "invoice-sample-0003-a4-third-horizontal.pdf",
                    "invoice-sample-0004-a4.pdf",
                    "invoice-sample-0005-a4-half-horizontal.pdf",
                ],
            )
            self.assertEqual(len(list(Path(temp_dir).glob("*.pdf"))), 5)
            for path in paths:
                content = path.read_bytes()
                self.assertTrue(content.startswith(b"%PDF-1.4"))
                self.assertIn(b"/Count 1", content)
                self.assertTrue(content.rstrip().endswith(b"%%EOF"))


if __name__ == "__main__":
    unittest.main()
