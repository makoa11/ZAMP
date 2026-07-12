import sys
sys.path.append("/home/darshan/Desktop/ZAMP")
from app.invoice_parser import _important_ocr_pages, OCR_FALLBACK_FIELDS

pages = [{"page": i, "text": "invoice total" if i == 1 else ""} for i in range(1, 16)]
selected = _important_ocr_pages(pages, target_fields=OCR_FALLBACK_FIELDS, max_pages=3)
print("Selected pages:", selected)
