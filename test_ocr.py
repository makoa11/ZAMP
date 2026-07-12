import sys
sys.path.append("/home/darshan/Desktop/ZAMP")
from app.invoice_parser import parse_invoice_pdf

with open("/home/darshan/Desktop/ZAMP/mail-invoice-1-overlay.pdf", "rb") as f:
    content = f.read()
    
result = parse_invoice_pdf(content)
print("Pages in result:", [p["page"] for p in result.get("pages", [])])
print("OCR summary:", result.get("ocr_summary", {}))
