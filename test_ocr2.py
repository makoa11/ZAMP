import sys
sys.path.append("/home/darshan/Desktop/ZAMP")
from app.invoice_parser import parse_invoice_pdf

with open("/home/darshan/Desktop/ZAMP/mail-invoice-1-overlay.pdf", "rb") as f:
    content = f.read()
    
result = parse_invoice_pdf(content)
print("status:", result.get("status"))
print("warnings:", result.get("warnings"))
print("ocr_summary:", result.get("ocr_summary"))
import json
print("fields:", json.dumps(result.get("fields", {}), indent=2))
