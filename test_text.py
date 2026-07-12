import pdfplumber

with pdfplumber.open("/home/darshan/Desktop/ZAMP/mail-invoice-1-overlay.pdf") as pdf:
    for page in pdf.pages:
        text = page.extract_text(x_tolerance=1.5, y_tolerance=3) or ""
        print(f"Page {page.page_number}: text len = {len(text)}")
