import pdfplumber

with pdfplumber.open("/home/darshan/Desktop/ZAMP/mail-invoice-1-overlay.pdf") as pdf:
    for page in pdf.pages:
        print(f"Page {page.page_number}: width={page.width}, height={page.height}")
