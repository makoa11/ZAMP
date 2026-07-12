import fitz
doc = fitz.open("/home/darshan/Desktop/ZAMP/mail-invoice-1-overlay.pdf")
print("PyMuPDF pages:", doc.page_count)
import pdfplumber
with pdfplumber.open("/home/darshan/Desktop/ZAMP/mail-invoice-1-overlay.pdf") as pdf:
    print("pdfplumber pages:", len(pdf.pages))
