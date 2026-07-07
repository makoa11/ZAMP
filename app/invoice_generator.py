from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


@dataclass(frozen=True)
class PaperFormat:
    slug: str
    label: str
    width_mm: float
    height_mm: float
    compactness: int


@dataclass(frozen=True)
class TemplateProfile:
    slug: str
    name: str
    industry: str
    layout_family: str
    accent: str
    secondary: str
    ink: str
    logo_shape: str
    table_style: str
    header_style: str
    density: int
    optional_components: tuple[str, ...]


PAPER_FORMATS: tuple[PaperFormat, ...] = (
    PaperFormat("a4", "A4 full page", 210, 297, 0),
    PaperFormat("a4-half-horizontal", "A4 / 2 horizontal", 210, 148.5, 1),
    PaperFormat("a4-third-horizontal", "A4 / 3 horizontal", 210, 99, 2),
)

PAPER_ALIASES = {
    "a4-half-vertical": "a4-half-horizontal",
    "a4-third-vertical": "a4-third-horizontal",
}

MONEY_QUANT = Decimal("0.01")
OVERLAP_RESOLUTION_MAX_ITERATIONS = 200

FONT_STYLES: tuple[str, ...] = (
    "system",
    "serif",
    "slab",
    "mono",
    "condensed",
    "rounded",
    "formal",
    "industrial",
    "humanist",
    "geometric",
    "courier",
    "book",
    "narrow",
    "typewriter",
    "neo",
)


BASE_TEMPLATES: tuple[TemplateProfile, ...] = (
    TemplateProfile(
        "ledger-clean",
        "Ledger Clean",
        "B2B services",
        "classic",
        "#0f766e",
        "#f2c94c",
        "#172026",
        "square",
        "ruled",
        "split",
        5,
        ("payment", "terms", "footer"),
    ),
    TemplateProfile(
        "north-star",
        "North Star",
        "SaaS subscription",
        "top-band",
        "#1d4ed8",
        "#f97316",
        "#111827",
        "circle",
        "soft",
        "banded",
        6,
        ("remittance", "timeline", "footer"),
    ),
    TemplateProfile(
        "studio-block",
        "Studio Block",
        "Creative studio",
        "poster",
        "#7c3aed",
        "#14b8a6",
        "#171321",
        "blob",
        "contrast",
        "centered",
        4,
        ("signature", "terms", "watermark"),
    ),
    TemplateProfile(
        "harbor-rail",
        "Harbor Rail",
        "Freight and logistics",
        "side-rail",
        "#0e7490",
        "#84cc16",
        "#0f172a",
        "hex",
        "dense",
        "rail",
        7,
        ("remittance", "stamp", "footer"),
    ),
    TemplateProfile(
        "apex-grid",
        "Apex Grid",
        "Consulting",
        "grid",
        "#be123c",
        "#0891b2",
        "#111827",
        "diamond",
        "boxed",
        "boxed",
        5,
        ("payment", "approver", "terms"),
    ),
    TemplateProfile(
        "civic-classic",
        "Civic Classic",
        "Municipal vendor",
        "classic",
        "#365314",
        "#ca8a04",
        "#1f2937",
        "seal",
        "ruled",
        "centered-no-line",
        6,
        ("tax-summary", "terms", "footer"),
    ),
    TemplateProfile(
        "pulse-care",
        "Pulse Care",
        "Healthcare services",
        "split-header",
        "#047857",
        "#2563eb",
        "#172026",
        "pill",
        "soft",
        "split-no-line",
        4,
        ("insurance", "terms", "footer"),
    ),
    TemplateProfile(
        "market-slip",
        "Market Slip",
        "Retail wholesale",
        "receipt",
        "#ea580c",
        "#0d9488",
        "#18181b",
        "ticket",
        "dense",
        "receipt",
        8,
        ("barcode", "stamp", "footer"),
    ),
    TemplateProfile(
        "forge-sheet",
        "Forge Sheet",
        "Manufacturing",
        "side-rail",
        "#334155",
        "#dc2626",
        "#0f172a",
        "bolt",
        "boxed",
        "industrial",
        7,
        ("packing", "quality", "footer"),
    ),
    TemplateProfile(
        "terra-simple",
        "Terra Simple",
        "Field services",
        "split-header",
        "#15803d",
        "#a16207",
        "#1c1917",
        "leaf",
        "ruled",
        "soft-band",
        5,
        ("work-order", "signature", "terms"),
    ),
    TemplateProfile(
        "orbit-minimal",
        "Orbit Minimal",
        "Professional services",
        "minimal",
        "#111827",
        "#06b6d4",
        "#111827",
        "orbit",
        "soft",
        "minimal-no-line",
        4,
        ("payment", "terms", "footer"),
    ),
    TemplateProfile(
        "ribbon-pro",
        "Ribbon Pro",
        "Events",
        "top-band",
        "#a21caf",
        "#f59e0b",
        "#1f1024",
        "ribbon",
        "contrast",
        "centered",
        5,
        ("schedule", "deposit", "footer"),
    ),
    TemplateProfile(
        "atlas-voucher",
        "Atlas Voucher",
        "Travel operations",
        "grid",
        "#0369a1",
        "#65a30d",
        "#0c1b2a",
        "pin",
        "boxed",
        "boxed",
        6,
        ("itinerary", "tax-summary", "footer"),
    ),
    TemplateProfile(
        "mono-archive",
        "Mono Archive",
        "Legal and accounting",
        "minimal",
        "#27272a",
        "#b45309",
        "#18181b",
        "monogram",
        "ruled",
        "minimal-no-line",
        5,
        ("approver", "terms", "footer"),
    ),
    TemplateProfile(
        "signal-card",
        "Signal Card",
        "IT maintenance",
        "poster",
        "#4338ca",
        "#16a34a",
        "#111827",
        "signal",
        "soft",
        "banded",
        6,
        ("sla", "payment", "footer"),
    ),
)


SELLERS: tuple[dict[str, str], ...] = (
    {
        "name": "Tandem Ledger Co.",
        "line1": "88 Market Street",
        "city": "San Francisco, CA 94105",
        "email": "billing@tandemledger.example",
        "tax_id": "US-EIN 82-4119083",
    },
    {
        "name": "Blue Harbor Supply",
        "line1": "410 Dockyard Road",
        "city": "Seattle, WA 98121",
        "email": "ar@blueharbor.example",
        "tax_id": "US-EIN 47-2190041",
    },
    {
        "name": "Aster Works Studio",
        "line1": "22 Grand Avenue",
        "city": "Brooklyn, NY 11238",
        "email": "accounts@asterworks.example",
        "tax_id": "US-EIN 66-1822075",
    },
    {
        "name": "Northline Systems",
        "line1": "700 Technology Parkway",
        "city": "Austin, TX 78701",
        "email": "finance@northline.example",
        "tax_id": "US-EIN 31-7648012",
    },
)


BUYERS: tuple[dict[str, str], ...] = (
    {
        "name": "Marin & Holt Partners",
        "line1": "19 Battery Place",
        "city": "New York, NY 10004",
        "email": "payables@marinholt.example",
    },
    {
        "name": "Greenridge Foods LLC",
        "line1": "1160 West Lake Road",
        "city": "Chicago, IL 60606",
        "email": "ap@greenridge.example",
    },
    {
        "name": "Orchid Hotel Group",
        "line1": "555 Harbor View",
        "city": "Miami, FL 33131",
        "email": "invoices@orchidhotels.example",
    },
    {
        "name": "Kestrel Bio Labs",
        "line1": "72 Discovery Lane",
        "city": "Cambridge, MA 02139",
        "email": "procurement@kestrelbio.example",
    },
)


ITEM_CATALOG: tuple[tuple[str, str, float], ...] = (
    ("Implementation workshop", "Discovery, planning, and stakeholder alignment", 1250.0),
    ("Monthly platform license", "Usage tier with reporting and support", 780.0),
    ("On-site service call", "Technician visit with diagnostics", 340.0),
    ("Freight handling", "Inbound pallet handling and carrier coordination", 210.0),
    ("Design production", "Campaign asset preparation and revisions", 640.0),
    ("Data migration batch", "Validated import, cleanup, and reconciliation", 920.0),
    ("Preventive maintenance", "Inspection, calibration, and test report", 455.0),
    ("Custom materials", "Special order parts and finishing supplies", 188.0),
    ("Training seats", "Instructor-led training per participant", 145.0),
    ("Expedited fulfillment", "Priority handling and same-day dispatch", 96.0),
    ("Compliance review", "Documentation audit and exception report", 510.0),
    ("Support retainer", "Reserved engineering response hours", 875.0),
)


CAPTURE_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "name": "numeric-dmy-usd",
        "date_pattern": "DDMMYYYY",
        "invoice_number_style": "prefix-year-month",
        "currency": "USD",
        "money_style": "symbol-prefix-2dp",
        "decimals": 2,
        "table_variant": "standard-desc",
        "total_in_table": True,
        "show_description": True,
        "columns": [
            {"key": "item", "label": "Item"},
            {"key": "quantity", "label": "Qty", "numeric": True},
            {"key": "unit_price", "label": "Rate", "numeric": True},
            {"key": "amount", "label": "Amount", "numeric": True},
        ],
        "labels": {
            "document_title": "Invoice",
            "seller": "From",
            "buyer": "Bill To",
            "invoice_number": "Invoice No.",
            "purchase_order": "PO",
            "status": "Status",
            "issue_date": "Invoice Date",
            "due_date": "Due Date",
            "terms": "Terms",
            "payment": "Payment",
            "subtotal": "Subtotal",
            "discount": "Discount",
            "tax": "Tax",
            "shipping": "Shipping",
            "paid": "Paid",
            "balance_due": "Balance Due",
        },
    },
    {
        "name": "short-dmy-inr-compact",
        "date_pattern": "DDMMYY",
        "invoice_number_style": "compact",
        "currency": "INR",
        "money_style": "symbol-prefix-0dp",
        "decimals": 0,
        "table_variant": "compact-total",
        "total_in_table": True,
        "show_description": False,
        "columns": [
            {"key": "item_plain", "label": "Particulars"},
            {"key": "quantity_unit", "label": "Units", "numeric": True},
            {"key": "amount", "label": "Billed Amt", "numeric": True},
        ],
        "labels": {
            "document_title": "Tax Invoice",
            "seller": "Supplier",
            "buyer": "Customer",
            "invoice_number": "Bill ID",
            "purchase_order": "Order Ref",
            "status": "State",
            "issue_date": "Bill Dt",
            "due_date": "Pay By",
            "terms": "Pay Terms",
            "payment": "Bank Details",
            "subtotal": "Goods Value",
            "discount": "Less Disc.",
            "tax": "GST",
            "shipping": "Freight",
            "paid": "Received",
            "balance_due": "Left Balance",
        },
    },
    {
        "name": "numeric-mdy-usd-no-decimal",
        "date_pattern": "MMDDYYYY",
        "invoice_number_style": "hash-short-year",
        "currency": "USD",
        "money_style": "symbol-prefix-0dp",
        "decimals": 0,
        "table_variant": "sku-ledger",
        "total_in_table": False,
        "show_description": False,
        "columns": [
            {"key": "sku", "label": "SKU"},
            {"key": "item_plain", "label": "Description"},
            {"key": "quantity", "label": "Count", "numeric": True},
            {"key": "amount", "label": "Line Total", "numeric": True},
        ],
        "labels": {
            "document_title": "Statement",
            "seller": "Seller",
            "buyer": "Billed To",
            "invoice_number": "Doc #",
            "purchase_order": "Buyer Ref",
            "status": "Stage",
            "issue_date": "Date",
            "due_date": "Payment Before",
            "terms": "Agreement",
            "payment": "Settlement",
            "subtotal": "Billed Amount",
            "discount": "Allowance",
            "tax": "Sales Tax",
            "shipping": "Handling",
            "paid": "Advance",
            "balance_due": "Remaining Payment",
        },
    },
    {
        "name": "slash-dmy-eur-comma",
        "date_pattern": "DD/MM/YYYY",
        "invoice_number_style": "slash-year",
        "currency": "EUR",
        "money_style": "symbol-prefix-comma-2dp",
        "decimals": 2,
        "table_variant": "service-hours",
        "total_in_table": True,
        "show_description": True,
        "columns": [
            {"key": "service_date", "label": "Service Date"},
            {"key": "item", "label": "Work"},
            {"key": "quantity_unit", "label": "Hours", "numeric": True},
            {"key": "unit_price", "label": "Unit Fee", "numeric": True},
            {"key": "amount", "label": "Net", "numeric": True},
        ],
        "labels": {
            "document_title": "Invoice",
            "seller": "Provider",
            "buyer": "Client",
            "invoice_number": "Reference",
            "purchase_order": "Client Order",
            "status": "Payment State",
            "issue_date": "Raised On",
            "due_date": "Settle By",
            "terms": "Credit",
            "payment": "Remittance",
            "subtotal": "Net Services",
            "discount": "Rebate",
            "tax": "VAT",
            "shipping": "Expenses",
            "paid": "Settled",
            "balance_due": "Open Amount",
        },
    },
    {
        "name": "iso-gbp-suffix",
        "date_pattern": "YYYYMMDD",
        "invoice_number_style": "fiscal",
        "currency": "GBP",
        "money_style": "symbol-prefix-2dp",
        "decimals": 2,
        "table_variant": "hsn-taxable",
        "total_in_table": False,
        "show_description": False,
        "columns": [
            {"key": "hsn", "label": "Code"},
            {"key": "item_plain", "label": "Narration"},
            {"key": "quantity", "label": "Qty", "numeric": True},
            {"key": "taxable", "label": "Taxable", "numeric": True},
        ],
        "labels": {
            "document_title": "Commercial Invoice",
            "seller": "Remit From",
            "buyer": "Account",
            "invoice_number": "Voucher No",
            "purchase_order": "Contract",
            "status": "Ledger",
            "issue_date": "Posting Date",
            "due_date": "Collection Date",
            "terms": "Settlement",
            "payment": "Payable To",
            "subtotal": "Taxable Value",
            "discount": "Deduction",
            "tax": "Tax Charged",
            "shipping": "Carriage",
            "paid": "Credit",
            "balance_due": "Amount Open",
        },
    },
    {
        "name": "dash-dmy-aed",
        "date_pattern": "DD-MM-YYYY",
        "invoice_number_style": "bill-dash",
        "currency": "AED",
        "money_style": "code-prefix-2dp",
        "decimals": 2,
        "table_variant": "quantity-first",
        "total_in_table": True,
        "show_description": True,
        "columns": [
            {"key": "quantity_unit", "label": "Qty"},
            {"key": "item", "label": "Charge"},
            {"key": "unit_price", "label": "Price", "numeric": True},
            {"key": "amount", "label": "Payable", "numeric": True},
        ],
        "labels": {
            "document_title": "Bill",
            "seller": "Issuer",
            "buyer": "To",
            "invoice_number": "Bill No.",
            "purchase_order": "Job No.",
            "status": "Approval",
            "issue_date": "Dated",
            "due_date": "Last Date",
            "terms": "Credit Days",
            "payment": "Payment Route",
            "subtotal": "Charge Total",
            "discount": "Adjustment",
            "tax": "VAT",
            "shipping": "Delivery",
            "paid": "Already Paid",
            "balance_due": "Left To Pay",
        },
    },
    {
        "name": "spoken-month-sgd",
        "date_pattern": "DD Mon YYYY",
        "invoice_number_style": "region",
        "currency": "SGD",
        "money_style": "plain-code-2dp",
        "decimals": 2,
        "table_variant": "date-ledger",
        "total_in_table": False,
        "show_description": False,
        "columns": [
            {"key": "service_date", "label": "Txn Date"},
            {"key": "item_plain", "label": "Memo"},
            {"key": "amount", "label": "Debit", "numeric": True},
        ],
        "labels": {
            "document_title": "Debit Note",
            "seller": "Prepared By",
            "buyer": "Charged To",
            "invoice_number": "Note Ref",
            "purchase_order": "Auth Ref",
            "status": "Queue",
            "issue_date": "Created",
            "due_date": "Clear By",
            "terms": "Window",
            "payment": "Transfer Info",
            "subtotal": "Debit Total",
            "discount": "Credit Adj.",
            "tax": "GST",
            "shipping": "Other Fees",
            "paid": "Applied",
            "balance_due": "Outstanding",
        },
    },
    {
        "name": "month-first-cad",
        "date_pattern": "Mon DD YYYY",
        "invoice_number_style": "bare-year-sequence",
        "currency": "CAD",
        "money_style": "symbol-prefix-2dp",
        "decimals": 2,
        "table_variant": "receipt-lines",
        "total_in_table": True,
        "show_description": False,
        "columns": [
            {"key": "line", "label": "#", "numeric": True},
            {"key": "item_plain", "label": "Line"},
            {"key": "amount", "label": "Ext.", "numeric": True},
        ],
        "labels": {
            "document_title": "Receipt",
            "seller": "Merchant",
            "buyer": "Payer",
            "invoice_number": "Receipt ID",
            "purchase_order": "Ref Code",
            "status": "Paid State",
            "issue_date": "Printed",
            "due_date": "Balance On",
            "terms": "Policy",
            "payment": "Tender",
            "subtotal": "Items Total",
            "discount": "Promo",
            "tax": "Tax",
            "shipping": "Service",
            "paid": "Tendered",
            "balance_due": "Amount Left",
        },
    },
    {
        "name": "dot-date-aud",
        "date_pattern": "YYYY.MM.DD",
        "invoice_number_style": "dot-sequence",
        "currency": "AUD",
        "money_style": "code-prefix-space-0dp",
        "decimals": 0,
        "table_variant": "description-only",
        "total_in_table": False,
        "show_description": True,
        "columns": [
            {"key": "item", "label": "Description"},
            {"key": "amount", "label": "Value", "numeric": True},
        ],
        "labels": {
            "document_title": "Account",
            "seller": "Origin",
            "buyer": "Destination",
            "invoice_number": "Account Ref",
            "purchase_order": "Work Ref",
            "status": "Review",
            "issue_date": "Account Date",
            "due_date": "Payment Target",
            "terms": "Basis",
            "payment": "Funds",
            "subtotal": "Value Before Tax",
            "discount": "Offset",
            "tax": "GST",
            "shipping": "Logistics",
            "paid": "Deposit",
            "balance_due": "Net Due",
        },
    },
    {
        "name": "hyphen-short-year-inr",
        "date_pattern": "DD-MM-YY",
        "invoice_number_style": "short-prefix",
        "currency": "INR",
        "money_style": "symbol-prefix-2dp",
        "decimals": 2,
        "table_variant": "hsn-rate",
        "total_in_table": True,
        "show_description": False,
        "columns": [
            {"key": "hsn", "label": "HSN/SAC"},
            {"key": "item_plain", "label": "Supply"},
            {"key": "quantity", "label": "Nos", "numeric": True},
            {"key": "unit_price", "label": "Basic", "numeric": True},
            {"key": "amount", "label": "Tax Inv Val", "numeric": True},
        ],
        "labels": {
            "document_title": "GST Bill",
            "seller": "Vendor",
            "buyer": "Recipient",
            "invoice_number": "Inv Ref",
            "purchase_order": "PO Ref",
            "status": "E-way",
            "issue_date": "Inv Date",
            "due_date": "Collection",
            "terms": "Terms",
            "payment": "UPI / Bank",
            "subtotal": "Assessable",
            "discount": "Scheme Disc",
            "tax": "CGST/SGST",
            "shipping": "Transport",
            "paid": "Advance",
            "balance_due": "Receivable",
        },
    },
    {
        "name": "slash-mdy-usd",
        "date_pattern": "MM/DD/YYYY",
        "invoice_number_style": "us-slash",
        "currency": "USD",
        "money_style": "symbol-prefix-2dp",
        "decimals": 2,
        "table_variant": "amount-left",
        "total_in_table": False,
        "show_description": True,
        "columns": [
            {"key": "item", "label": "Billing Description"},
            {"key": "quantity", "label": "Qty", "numeric": True},
            {"key": "amount", "label": "Charge", "numeric": True},
        ],
        "labels": {
            "document_title": "Pay Request",
            "seller": "Payee",
            "buyer": "Requester",
            "invoice_number": "Request No.",
            "purchase_order": "Budget Ref",
            "status": "Internal State",
            "issue_date": "Request Date",
            "due_date": "Needed By",
            "terms": "Payment Rule",
            "payment": "Payment Instructions",
            "subtotal": "Requested Amount",
            "discount": "Withheld",
            "tax": "Tax Add-on",
            "shipping": "Pass-through",
            "paid": "Released",
            "balance_due": "Unreleased",
        },
    },
    {
        "name": "compact-yyyy-cny",
        "date_pattern": "YYYYMMDD",
        "invoice_number_style": "numeric-only",
        "currency": "CNY",
        "money_style": "symbol-prefix-0dp",
        "decimals": 0,
        "table_variant": "code-amount",
        "total_in_table": True,
        "show_description": False,
        "columns": [
            {"key": "sku", "label": "Material"},
            {"key": "quantity_unit", "label": "Pack"},
            {"key": "amount", "label": "RMB Amt", "numeric": True},
        ],
        "labels": {
            "document_title": "Charge Sheet",
            "seller": "Entity",
            "buyer": "Counterparty",
            "invoice_number": "Serial",
            "purchase_order": "Batch",
            "status": "Flag",
            "issue_date": "Doc Date",
            "due_date": "Cash Date",
            "terms": "Cycle",
            "payment": "Bank",
            "subtotal": "Sheet Total",
            "discount": "Less",
            "tax": "Tax",
            "shipping": "Move Cost",
            "paid": "Cleared",
            "balance_due": "Not Cleared",
        },
    },
    {
        "name": "no-separator-dmy-zar",
        "date_pattern": "DDMMYYYY",
        "invoice_number_style": "job-ticket",
        "currency": "ZAR",
        "money_style": "code-prefix-2dp",
        "decimals": 2,
        "table_variant": "job-card",
        "total_in_table": False,
        "show_description": True,
        "columns": [
            {"key": "sku", "label": "Job"},
            {"key": "item", "label": "Task"},
            {"key": "quantity_unit", "label": "Time"},
            {"key": "amount", "label": "Due Amt", "numeric": True},
        ],
        "labels": {
            "document_title": "Job Invoice",
            "seller": "Contractor",
            "buyer": "Site",
            "invoice_number": "Ticket",
            "purchase_order": "Site Order",
            "status": "Signoff",
            "issue_date": "Work Date",
            "due_date": "Payment Cutoff",
            "terms": "Contract Term",
            "payment": "EFT Detail",
            "subtotal": "Work Value",
            "discount": "Retention",
            "tax": "VAT",
            "shipping": "Travel",
            "paid": "Progress Pay",
            "balance_due": "Final Claim",
        },
    },
    {
        "name": "abbrev-month-jpy",
        "date_pattern": "DD Mon YYYY",
        "invoice_number_style": "alpha-batch",
        "currency": "JPY",
        "money_style": "symbol-prefix-0dp",
        "decimals": 0,
        "table_variant": "minimal-lines",
        "total_in_table": True,
        "show_description": False,
        "columns": [
            {"key": "line", "label": "Ln", "numeric": True},
            {"key": "item_plain", "label": "Details"},
            {"key": "quantity", "label": "Qty", "numeric": True},
            {"key": "amount", "label": "JPY", "numeric": True},
        ],
        "labels": {
            "document_title": "Payment Note",
            "seller": "Source",
            "buyer": "Payor",
            "invoice_number": "Batch ID",
            "purchase_order": "Control",
            "status": "Run",
            "issue_date": "Batch Date",
            "due_date": "Release Date",
            "terms": "Run Terms",
            "payment": "Clearing",
            "subtotal": "Batch Sum",
            "discount": "Holdback",
            "tax": "Consumption Tax",
            "shipping": "Admin",
            "paid": "Released",
            "balance_due": "Pending Release",
        },
    },
    {
        "name": "wordy-date-mxn",
        "date_pattern": "Mon DD YYYY",
        "invoice_number_style": "colon-ref",
        "currency": "MXN",
        "money_style": "symbol-prefix-comma-2dp",
        "decimals": 2,
        "table_variant": "extended",
        "total_in_table": False,
        "show_description": True,
        "columns": [
            {"key": "service_date", "label": "Applied"},
            {"key": "sku", "label": "Ref"},
            {"key": "item", "label": "Concept"},
            {"key": "quantity", "label": "Units", "numeric": True},
            {"key": "unit_price", "label": "Each", "numeric": True},
            {"key": "amount", "label": "Importe", "numeric": True},
        ],
        "labels": {
            "document_title": "Fiscal Invoice",
            "seller": "Emisor",
            "buyer": "Receptor",
            "invoice_number": "Folio",
            "purchase_order": "Orden",
            "status": "Estado",
            "issue_date": "Fecha",
            "due_date": "Limite Pago",
            "terms": "Condiciones",
            "payment": "Forma de Pago",
            "subtotal": "Subtotal",
            "discount": "Descuento",
            "tax": "IVA",
            "shipping": "Envio",
            "paid": "Abonado",
            "balance_due": "Saldo",
        },
    },
)


def paper_options() -> list[dict[str, Any]]:
    return [
        {
            "slug": paper.slug,
            "label": paper.label,
            "width_mm": paper.width_mm,
            "height_mm": paper.height_mm,
        }
        for paper in PAPER_FORMATS
    ]


def template_options() -> list[dict[str, str]]:
    return [
        {
            "slug": template.slug,
            "name": template.name,
            "industry": template.industry,
            "layout_family": template.layout_family,
        }
        for template in BASE_TEMPLATES
    ]


def generate_invoice_samples(
    *,
    paper_slug: str = "a4",
    count: int = 15,
    seed: int = 1000,
    today: date | None = None,
) -> list[dict[str, Any]]:
    bounded_count = max(1, min(count, 60))
    return [
        generate_invoice(
            template_slug=BASE_TEMPLATES[index % len(BASE_TEMPLATES)].slug,
            paper_slug=paper_slug,
            seed=seed + (index * 97),
            variation_index=index,
            today=today,
        )
        for index in range(bounded_count)
    ]


def generate_invoice(
    *,
    template_slug: str | None = None,
    paper_slug: str = "a4",
    seed: int = 1000,
    variation_index: int = 0,
    today: date | None = None,
) -> dict[str, Any]:
    paper = _paper(paper_slug)
    template = _template(template_slug, seed)
    rng = random.Random(f"{seed}:{variation_index}:{paper.slug}:{template.slug}")
    invoice_date = today or date.today()
    data = _invoice_data(template, paper, rng, invoice_date, variation_index=variation_index)
    components = _layout_components(template, paper, data, rng)
    components, data = _optimize_for_paper(template, paper, components, data)
    font_style = FONT_STYLES[(_template_index(template) + variation_index) % len(FONT_STYLES)]
    return {
        "id": f"{template.slug}-{paper.slug}-{seed}-{variation_index}",
        "paper": {
            "slug": paper.slug,
            "label": paper.label,
            "width_mm": paper.width_mm,
            "height_mm": paper.height_mm,
        },
        "template": {
            "slug": template.slug,
            "name": template.name,
            "industry": template.industry,
            "layout_family": template.layout_family,
            "accent": template.accent,
            "secondary": template.secondary,
            "ink": template.ink,
            "logo_shape": template.logo_shape,
            "table_style": template.table_style,
            "header_style": template.header_style,
            "font_style": font_style,
        },
        "data": data,
        "components": [component.copy() for component in components],
        "layout_score": _layout_score(paper, components, data),
    }


def _paper(slug: str) -> PaperFormat:
    slug = PAPER_ALIASES.get(slug, slug)
    for paper in PAPER_FORMATS:
        if paper.slug == slug:
            return paper
    raise ValueError(f"Unsupported paper size: {slug}")


def _template(slug: str | None, seed: int) -> TemplateProfile:
    if slug:
        for template in BASE_TEMPLATES:
            if template.slug == slug:
                return template
        raise ValueError(f"Unsupported invoice template: {slug}")
    return BASE_TEMPLATES[seed % len(BASE_TEMPLATES)]


def _template_index(template: TemplateProfile) -> int:
    for index, candidate in enumerate(BASE_TEMPLATES):
        if candidate.slug == template.slug:
            return index
    return 0


def _capture_profile(template: TemplateProfile, variation_index: int) -> dict[str, Any]:
    profile = CAPTURE_PROFILES[(_template_index(template) + variation_index) % len(CAPTURE_PROFILES)]
    return {
        **profile,
        "labels": dict(profile["labels"]),
        "columns": [dict(column) for column in profile["columns"]],
    }


def _invoice_data(
    template: TemplateProfile,
    paper: PaperFormat,
    rng: random.Random,
    today: date,
    variation_index: int,
) -> dict[str, Any]:
    seller = dict(rng.choice(SELLERS))
    buyer = dict(rng.choice(BUYERS))
    profile = _capture_profile(template, variation_index)
    issue_date = today - timedelta(days=rng.randint(2, 42))
    due_date = issue_date + timedelta(days=rng.choice([7, 14, 21, 30, 45]))
    item_limit = 6 if paper.compactness == 0 else 4 if paper.compactness == 1 else 3
    minimum_items = 3 if paper.compactness < 2 else 2
    item_count = max(minimum_items, min(item_limit, max(3, template.density + rng.randint(-1, 2))))
    items = _line_items(rng, item_count, issue_date=issue_date, profile=profile)
    subtotal_amount = _round_money(sum(_decimal_money(item["amount"]) for item in items))
    discount_amount = _round_money(
        subtotal_amount * _decimal_money(rng.choice([0, 0, 0.025, 0.05]))
    )
    taxable_amount = _round_money(subtotal_amount - discount_amount)
    tax_rate_amount = _decimal_money(rng.choice([0.0, 0.0625, 0.0725, 0.0825]))
    tax_amount = _round_money(taxable_amount * tax_rate_amount)
    shipping_amount = _round_money(rng.choice([0, 0, 0, 48, 75, 120]))
    total_amount = _round_money(taxable_amount + tax_amount + shipping_amount)
    paid_amount = _round_money(total_amount * _decimal_money(rng.choice([0, 0, 0.25, 0.5])))
    balance_due_amount = _round_money(total_amount - paid_amount)
    subtotal = _money_float(subtotal_amount)
    discount = _money_float(discount_amount)
    tax_rate = float(tax_rate_amount)
    tax = _money_float(tax_amount)
    shipping = _money_float(shipping_amount)
    total = _money_float(total_amount)
    paid = _money_float(paid_amount)
    balance_due = _money_float(balance_due_amount)
    invoice_number = _invoice_number(
        seller["name"],
        issue_date,
        rng,
        style=str(profile["invoice_number_style"]),
    )
    currency = str(profile["currency"])
    return {
        "invoice_number": invoice_number,
        "invoice_number_style": profile["invoice_number_style"],
        "purchase_order": f"PO-{rng.randint(2000, 9800)}-{rng.choice(['A', 'B', 'C', 'R'])}",
        "issue_date": issue_date.isoformat(),
        "issue_date_display": _format_date(issue_date, str(profile["date_pattern"])),
        "due_date": due_date.isoformat(),
        "due_date_display": _format_date(due_date, str(profile["date_pattern"])),
        "terms": rng.choice(["Net 15", "Net 30", "Due on receipt", "2% 10 Net 30"]),
        "status": rng.choice(["Open", "Pending approval", "Partial payment", "Due soon"]),
        "seller": seller,
        "buyer": buyer,
        "items": items,
        "currency": currency,
        "labels": profile["labels"],
        "formatting": {
            "date_pattern": profile["date_pattern"],
            "money_style": profile["money_style"],
            "decimals": profile["decimals"],
        },
        "table": {
            "variant": profile["table_variant"],
            "columns": profile["columns"],
            "show_description": profile["show_description"],
            "total_in_table": bool(profile.get("total_in_table")),
        },
        "capture_profile": profile["name"],
        "subtotal": subtotal,
        "discount": discount,
        "tax_rate": tax_rate,
        "tax": tax,
        "shipping": shipping,
        "total": total,
        "paid": paid,
        "balance_due": balance_due,
        "total_quantity": sum(int(item["quantity"]) for item in items),
        "payment": _payment_details(seller, rng, labels=profile["labels"]),
        "notes": _notes(template, rng),
    }


def _line_items(
    rng: random.Random,
    count: int,
    *,
    issue_date: date,
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    picks = list(ITEM_CATALOG)
    rng.shuffle(picks)
    items: list[dict[str, Any]] = []
    unit_labels = ["ea", "hrs", "pcs", "days", "kg", "sets"]
    for index, (name, description, base_price) in enumerate(picks[:count], start=1):
        quantity = rng.choice([1, 1, 2, 3, 4, 6, 8])
        unit_label = rng.choice(unit_labels)
        price_factor = Decimal(rng.randint(8800, 11800)) / Decimal("10000")
        unit_price_amount = _round_money(
            _decimal_money(base_price) * price_factor
        )
        amount_value = _round_money(quantity * unit_price_amount)
        unit_price = _money_float(unit_price_amount)
        amount = _money_float(amount_value)
        service_date = issue_date - timedelta(days=rng.randint(0, 12))
        items.append(
            {
                "line": index,
                "sku": f"{rng.choice(['SV', 'PR', 'LN', 'MT'])}-{rng.randint(100, 999)}",
                "hsn": str(rng.choice([998313, 998314, 852380, 491110, 847130, 940360])),
                "name": name,
                "description": description,
                "quantity": quantity,
                "quantity_display": f"{quantity} {unit_label}",
                "unit_price": unit_price,
                "amount": amount,
                "taxable_amount": amount,
                "service_date": service_date.isoformat(),
                "service_date_display": _format_date(service_date, str(profile["date_pattern"])),
            }
        )
    return items


def _format_date(value: date, pattern: str) -> str:
    formats = {
        "DDMMYYYY": "%d%m%Y",
        "DDMMYY": "%d%m%y",
        "MMDDYYYY": "%m%d%Y",
        "DD/MM/YYYY": "%d/%m/%Y",
        "MM/DD/YYYY": "%m/%d/%Y",
        "DD-MM-YYYY": "%d-%m-%Y",
        "DD-MM-YY": "%d-%m-%y",
        "YYYYMMDD": "%Y%m%d",
        "YYYY.MM.DD": "%Y.%m.%d",
        "DD Mon YYYY": "%d %b %Y",
        "Mon DD YYYY": "%b %d %Y",
    }
    return value.strftime(formats.get(pattern, "%Y-%m-%d"))


def _decimal_money(value: object) -> Decimal:
    return Decimal(str(value))


def _round_money(value: object) -> Decimal:
    return _decimal_money(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _money_float(value: Decimal) -> float:
    return float(_round_money(value))


def _invoice_number(seller_name: str, issue_date: date, rng: random.Random, *, style: str) -> str:
    letters = "".join(part[0] for part in seller_name.replace("&", " ").split() if part[:1]).upper()
    prefix = (letters + "INV")[:4]
    sequence = rng.randint(1000, 9999)
    short_sequence = rng.randint(100, 999)
    styles = {
        "prefix-year-month": f"{prefix}-{issue_date:%Y%m}-{sequence}",
        "compact": f"{prefix}{issue_date:%y%m}{sequence}",
        "hash-short-year": f"#{sequence}-{issue_date:%y}",
        "slash-year": f"INV/{issue_date:%Y}/{sequence}",
        "fiscal": f"FY{issue_date:%y}-{(issue_date.year + 1) % 100:02d}/{prefix}/{short_sequence}",
        "bill-dash": f"BILL-{issue_date:%m%d}-{sequence}",
        "region": f"US-{prefix}-{issue_date:%y}-{sequence}",
        "bare-year-sequence": f"{issue_date:%Y}{sequence}",
        "dot-sequence": f"{prefix}.{issue_date:%Y}.{short_sequence}",
        "short-prefix": f"{prefix}-{short_sequence}",
        "us-slash": f"{issue_date:%m}/{sequence}/{issue_date:%y}",
        "numeric-only": f"{issue_date:%y%m%d}{sequence}",
        "job-ticket": f"JOB-{sequence}-{prefix}",
        "alpha-batch": f"{prefix}{rng.choice(['A', 'B', 'C'])}{sequence}",
        "colon-ref": f"{prefix}:{issue_date:%m}:{sequence}",
    }
    return styles.get(style, styles["prefix-year-month"])


def _payment_details(seller: dict[str, str], rng: random.Random, *, labels: dict[str, str]) -> dict[str, str]:
    bank_suffix = rng.randint(1000, 9999)
    invoice_ref_label = labels.get("invoice_number", "invoice number")
    return {
        "method": rng.choice(["ACH transfer", "Card on file", "Wire transfer", "Check"]),
        "account": f"**** {bank_suffix}",
        "reference": rng.choice(
            [
                f"Quote {invoice_ref_label} with the transfer.",
                "Match payment to the document reference.",
                "Send remittance advice after settlement.",
                "Include account and document code in memo.",
            ]
        ),
        "remit_to": seller["email"],
    }


def _notes(template: TemplateProfile, rng: random.Random) -> str:
    options = [
        "Thank you for your business.",
        "Please notify accounts receivable of any billing questions within seven days.",
        "Late balances may be subject to fees defined in the service agreement.",
        f"{template.industry} charges are grouped for audit-friendly reconciliation.",
    ]
    return rng.choice(options)


def _layout_components(
    template: TemplateProfile,
    paper: PaperFormat,
    data: dict[str, Any],
    rng: random.Random,
) -> list[dict[str, Any]]:
    if paper.compactness:
        return _with_company_header(template, paper, _horizontal_slip_components(template, paper, data))
    if template.layout_family == "side-rail":
        return _with_company_header(template, paper, _side_rail_components(template, paper, data))
    if template.layout_family == "top-band":
        return _with_company_header(template, paper, _top_band_components(template, paper, data))
    if template.layout_family == "grid":
        return _with_company_header(template, paper, _grid_components(template, paper, data))
    if template.layout_family == "poster":
        return _with_company_header(template, paper, _poster_components(template, paper, data))
    if template.layout_family == "split-header":
        return _with_company_header(template, paper, _split_header_components(template, paper, data))
    if template.layout_family == "receipt":
        return _with_company_header(template, paper, _receipt_components(template, paper, data))
    if template.layout_family == "minimal":
        return _with_company_header(template, paper, _minimal_components(template, paper, data))
    return _with_company_header(template, paper, _classic_components(template, paper, data, rng))


def _component(
    kind: str,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    priority: int = 5,
    optional: bool = False,
    variant: str | None = None,
) -> dict[str, Any]:
    component = {
        "kind": kind,
        "x_mm": round(x, 2),
        "y_mm": round(y, 2),
        "width_mm": round(width, 2),
        "height_mm": round(height, 2),
        "priority": priority,
        "optional": optional,
    }
    if variant:
        component["variant"] = variant
    return component


def _with_company_header(
    template: TemplateProfile,
    paper: PaperFormat,
    components: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    variant = template.header_style
    height = _company_header_height(paper, variant)
    full_page_shift = max(0.0, height - 11) if paper.compactness == 0 else 0.0
    decorative = [
        component
        for component in components
        if component["kind"] in {"accent-band", "accent-rail", "watermark"}
    ]
    content = []
    for component in components:
        if component["kind"] in {"accent-band", "accent-rail", "watermark"}:
            continue
        shifted = component.copy()
        shifted["y_mm"] = round(float(shifted["y_mm"]) + full_page_shift, 2)
        content.append(shifted)
    return [
        *decorative,
        _component("company-header", 0, 0, paper.width_mm, height, priority=1, variant=variant),
        *content,
    ]


def _company_header_height(paper: PaperFormat, variant: str) -> float:
    if paper.compactness == 2:
        return {
            "centered": 15,
            "centered-no-line": 15,
            "banded": 13,
            "soft-band": 13,
            "boxed": 13,
            "receipt": 12,
            "industrial": 12,
            "rail": 12,
            "minimal-no-line": 10,
            "split-no-line": 11,
        }.get(variant, 12)
    if paper.compactness == 1:
        return {
            "centered": 18,
            "centered-no-line": 18,
            "banded": 16,
            "soft-band": 16,
            "boxed": 17,
            "receipt": 15,
            "industrial": 15,
            "rail": 15,
            "minimal-no-line": 12,
            "split-no-line": 14,
        }.get(variant, 15)
    return {
        "centered": 16,
        "centered-no-line": 16,
        "banded": 14,
        "soft-band": 14,
        "boxed": 15,
        "receipt": 13,
        "industrial": 13,
        "rail": 13,
        "minimal-no-line": 11,
        "split-no-line": 11,
    }.get(variant, 11)


def _classic_components(
    template: TemplateProfile,
    paper: PaperFormat,
    data: dict[str, Any],
    rng: random.Random,
) -> list[dict[str, Any]]:
    margin = 14
    width = paper.width_mm - (margin * 2)
    table_height = 84 + (len(data["items"]) * 4)
    return [
        _component("logo", margin, 15, 24, 20, priority=1),
        _component("title", 106, 15, 90, 24, priority=1),
        _component("seller", margin, 42, 72, 35, priority=2),
        _component("invoice-meta", 126, 42, 70, 35, priority=2),
        _component("buyer", margin, 85, 86, 36, priority=2),
        _component("dates", 112, 85, 84, 36, priority=2),
        _component("items-table", margin, 130, width, table_height, priority=1),
        _component("totals", 118, 218, 78, 36, priority=1),
        _component("payment", margin, 220, 74, 34, priority=4, optional=True),
        _component("terms", margin, 260, width, 15, priority=6, optional=True),
        _component("footer", margin, 281, width, 8, priority=8, optional=True),
    ]


def _side_rail_components(
    template: TemplateProfile,
    paper: PaperFormat,
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    margin = 12
    rail = 43
    content_x = margin + rail + 9
    content_w = paper.width_mm - content_x - margin
    return [
        _component("accent-rail", 0, 0, rail + 8, paper.height_mm, priority=7, optional=True),
        _component("logo", margin, 18, 24, 22, priority=1),
        _component("seller", margin, 49, rail - 2, 72, priority=2),
        _component("title", content_x, 18, 72, 24, priority=1),
        _component("invoice-meta", content_x + 76, 18, content_w - 76, 42, priority=2),
        _component("buyer", content_x, 70, content_w * 0.52, 35, priority=2),
        _component("dates", content_x + content_w * 0.58, 70, content_w * 0.42, 35, priority=2),
        _component("items-table", content_x, 115, content_w, 92, priority=1),
        _component("payment", content_x, 216, content_w * 0.46, 36, priority=4, optional=True),
        _component("totals", content_x + content_w * 0.54, 216, content_w * 0.46, 36, priority=1),
        _component("stamp", margin, 228, 32, 18, priority=6, optional=True),
        _component("footer", content_x, 278, content_w, 9, priority=8, optional=True),
    ]


def _top_band_components(
    template: TemplateProfile,
    paper: PaperFormat,
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    margin = 13
    width = paper.width_mm - (margin * 2)
    return [
        _component("accent-band", 0, 0, paper.width_mm, 34, priority=7, optional=True),
        _component("logo", margin, 15, 22, 20, priority=1),
        _component("title", 118, 15, 78, 22, priority=1),
        _component("seller", margin, 45, 75, 34, priority=2),
        _component("invoice-meta", 123, 45, 73, 34, priority=2),
        _component("buyer", margin, 88, 86, 34, priority=2),
        _component("dates", 112, 88, 84, 34, priority=2),
        _component("items-table", margin, 132, width, 82 + len(data["items"]) * 3, priority=1),
        _component("timeline", margin, 221, 72, 28, priority=5, optional=True),
        _component("totals", 116, 219, 80, 38, priority=1),
        _component("remittance", margin, 260, width, 17, priority=5, optional=True),
        _component("footer", margin, 282, width, 8, priority=8, optional=True),
    ]


def _grid_components(
    template: TemplateProfile,
    paper: PaperFormat,
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    margin = 14
    gap = 8
    width = paper.width_mm - (margin * 2)
    column = (width - gap) / 2
    return [
        _component("logo", margin, 16, 24, 20, priority=1),
        _component("title", margin + 92, 16, 90, 24, priority=1),
        _component("seller", margin, 48, column, 42, priority=2),
        _component("invoice-meta", margin + column + gap, 48, column, 42, priority=2),
        _component("buyer", margin, 98, column, 40, priority=2),
        _component("dates", margin + column + gap, 98, column, 40, priority=2),
        _component("items-table", margin, 148, width, 76 + len(data["items"]) * 4, priority=1),
        _component("payment", margin, 230, column, 30, priority=4, optional=True),
        _component("totals", margin + column + gap, 228, column, 38, priority=1),
        _component("tax-summary", margin, 267, column, 16, priority=5, optional=True),
        _component("terms", margin + column + gap, 271, column, 12, priority=6, optional=True),
    ]


def _poster_components(
    template: TemplateProfile,
    paper: PaperFormat,
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    margin = 13
    width = paper.width_mm - (margin * 2)
    return [
        _component("watermark", 122, 38, 64, 64, priority=9, optional=True),
        _component("logo", margin, 17, 25, 23, priority=1),
        _component("title", margin, 49, 100, 28, priority=1),
        _component("invoice-meta", 119, 17, 78, 54, priority=2),
        _component("seller", margin, 86, 78, 34, priority=2),
        _component("buyer", 104, 86, 92, 34, priority=2),
        _component("dates", margin, 128, width, 22, priority=2),
        _component("items-table", margin, 160, width, 72 + len(data["items"]) * 4, priority=1),
        _component("signature", margin, 241, 72, 28, priority=5, optional=True),
        _component("totals", 119, 238, 78, 38, priority=1),
        _component("footer", margin, 282, width, 8, priority=8, optional=True),
    ]


def _split_header_components(
    template: TemplateProfile,
    paper: PaperFormat,
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    margin = 14
    width = paper.width_mm - (margin * 2)
    return [
        _component("logo", margin, 16, 24, 22, priority=1),
        _component("seller", margin, 46, 72, 40, priority=2),
        _component("title", 112, 16, 84, 24, priority=1),
        _component("invoice-meta", 112, 46, 84, 40, priority=2),
        _component("buyer", margin, 96, 88, 35, priority=2),
        _component("dates", 112, 96, 84, 35, priority=2),
        _component("items-table", margin, 142, width, 82 + len(data["items"]) * 4, priority=1),
        _component("work-order", margin, 232, 76, 25, priority=5, optional=True),
        _component("totals", 118, 229, 78, 38, priority=1),
        _component("terms", margin, 268, width, 14, priority=6, optional=True),
    ]


def _receipt_components(
    template: TemplateProfile,
    paper: PaperFormat,
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    margin = 15
    width = paper.width_mm - (margin * 2)
    return [
        _component("logo", margin, 15, 22, 20, priority=1),
        _component("title", 111, 15, 84, 24, priority=1),
        _component("seller", margin, 45, 76, 30, priority=2),
        _component("invoice-meta", 112, 45, 83, 30, priority=2),
        _component("buyer", margin, 84, width, 28, priority=2),
        _component("dates", margin, 118, width, 20, priority=2),
        _component("items-table", margin, 146, width, 92 + len(data["items"]) * 3, priority=1),
        _component("barcode", margin, 247, 72, 18, priority=5, optional=True),
        _component("totals", 116, 239, 79, 38, priority=1),
        _component("footer", margin, 282, width, 8, priority=8, optional=True),
    ]


def _minimal_components(
    template: TemplateProfile,
    paper: PaperFormat,
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    margin = 16
    width = paper.width_mm - (margin * 2)
    return [
        _component("logo", margin, 18, 20, 18, priority=1),
        _component("title", 126, 18, 68, 22, priority=1),
        _component("seller", margin, 52, 76, 34, priority=2),
        _component("invoice-meta", 121, 52, 73, 34, priority=2),
        _component("buyer", margin, 98, 84, 34, priority=2),
        _component("dates", 112, 98, 82, 34, priority=2),
        _component("items-table", margin, 148, width, 76 + len(data["items"]) * 4, priority=1),
        _component("totals", 116, 231, 78, 37, priority=1),
        _component("payment", margin, 235, 72, 28, priority=4, optional=True),
        _component("terms", margin, 273, width, 12, priority=6, optional=True),
    ]


def _horizontal_slip_components(
    template: TemplateProfile,
    paper: PaperFormat,
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    margin = 8 if paper.compactness == 1 else 7
    width = paper.width_mm - (margin * 2)
    if paper.compactness == 1:
        table_y = 82
        table_h = 38
        return [
            _component("logo", margin, 22, 54, 13, priority=1),
            _component("title", 118, 22, 84, 13, priority=1),
            _component("seller", margin, 40, 53, 20, priority=2),
            _component("buyer", 67, 40, 58, 20, priority=2),
            _component("invoice-meta", 130, 40, 72, 20, priority=2),
            _component("dates", margin, 65, width, 11, priority=2),
            _component("items-table", margin, table_y, 126, table_h, priority=1),
            _component("totals", 138, table_y, 64, table_h, priority=1),
            _component("payment", margin, 124, 88, 13, priority=4, optional=True),
            _component("footer", 102, 132, 100, 8, priority=8, optional=True),
        ]
    return [
        _component("logo", margin, 17, 56, 10, priority=1),
        _component("title", 112, 17, 84, 10, priority=1),
        _component("seller", margin, 31, 49, 14, priority=2),
        _component("buyer", 61, 31, 57, 14, priority=2),
        _component("invoice-meta", 123, 31, 80, 14, priority=2),
        _component("dates", margin, 48, width, 7, priority=2, variant="two-row"),
        _component("items-table", margin, 58, 126, 26, priority=1),
        _component("totals", 138, 58, 65, 26, priority=1),
        _component("payment", margin, 87, width, 7, priority=4, optional=True),
    ]


def _optimize_for_paper(
    template: TemplateProfile,
    paper: PaperFormat,
    components: list[dict[str, Any]],
    data: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    optimized_data = dict(data)
    optimized_data["items"] = list(data["items"])
    if paper.compactness == 2:
        optimized_data["items"] = optimized_data["items"][:3]
        keep_optional = {"payment"}
    elif paper.compactness == 1:
        optimized_data["items"] = optimized_data["items"][:4]
        keep_optional = {"payment", "footer"}
    else:
        keep_optional = set(template.optional_components)

    table = optimized_data.get("table") if isinstance(optimized_data.get("table"), dict) else {}
    optimized_table = dict(table)
    optimized_table["total_in_table"] = bool(table.get("total_in_table")) and paper.compactness == 0
    optimized_data["table"] = optimized_table
    total_in_table = bool(optimized_table.get("total_in_table"))
    filtered = [
        component
        for component in components
        if component["kind"] != "totals" or not total_in_table
    ]
    filtered = [
        component
        for component in filtered
        if not component["optional"] or component["kind"] in keep_optional
    ]
    filtered = _size_table_for_data(paper, filtered, optimized_data)
    filtered = _resolve_non_decorative_overlaps(paper, filtered)
    max_bottom = max((component["y_mm"] + component["height_mm"]) for component in filtered)
    if max_bottom > paper.height_mm - 6:
        filtered = [
            component
            for component in filtered
            if not component["optional"] or component["priority"] <= 4
        ]
        filtered = _resolve_non_decorative_overlaps(paper, filtered)
    return filtered, optimized_data


def _size_table_for_data(
    paper: PaperFormat,
    components: list[dict[str, Any]],
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    sized = [component.copy() for component in components]
    for component in sized:
        if component["kind"] == "items-table":
            component["height_mm"] = round(_estimated_table_height(paper, data), 2)
            break
    return sized


def _estimated_table_height(paper: PaperFormat, data: dict[str, Any]) -> float:
    rows = len(data["items"])
    table = data.get("table") if isinstance(data.get("table"), dict) else {}
    columns = table.get("columns") if isinstance(table.get("columns"), list) else []
    show_description = bool(table.get("show_description", True))
    column_count = max(1, len(columns))
    total_row = bool(table.get("total_in_table")) and paper.compactness == 0
    if total_row:
        rows += 1
    if paper.compactness == 2:
        return 11 + (rows * (5.8 if show_description else 4.9))
    if paper.compactness == 1:
        return 12 + (rows * (6.8 if show_description else 5.6))
    row_height = 7.4
    if show_description:
        row_height += 2.6
    if column_count >= 5:
        row_height += 1.0
    if total_row:
        row_height += 0.4
    return min(104, 12 + (rows * row_height))


def _resolve_non_decorative_overlaps(
    paper: PaperFormat,
    components: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    gap = 3.0 if paper.compactness else 4.5
    updated = [component.copy() for component in components]
    placed: list[tuple[int, dict[str, Any]]] = []
    ordered = sorted(
        [
            (index, component.copy())
            for index, component in enumerate(updated)
            if not _is_decorative_component(component)
        ],
        key=lambda item: (float(item[1]["y_mm"]), float(item[1]["x_mm"]), item[0]),
    )
    for index, component in ordered:
        for _ in range(OVERLAP_RESOLUTION_MAX_ITERATIONS):
            collision = next(
                (
                    previous
                    for _, previous in placed
                    if _rectangles_overlap(component, previous)
                ),
                None,
            )
            if collision is None:
                break
            component["y_mm"] = round(collision["y_mm"] + collision["height_mm"] + gap, 2)
        else:
            component["y_mm"] = _fallback_overlap_y(component, placed, gap)
        updated[index] = component
        placed.append((index, component))
    return updated


def _fallback_overlap_y(
    component: dict[str, Any],
    placed: list[tuple[int, dict[str, Any]]],
    gap: float,
) -> float:
    horizontally_overlapping = [
        previous
        for _, previous in placed
        if not (
            component["x_mm"] + component["width_mm"] <= previous["x_mm"]
            or previous["x_mm"] + previous["width_mm"] <= component["x_mm"]
        )
    ]
    if not horizontally_overlapping:
        return component["y_mm"]
    return round(
        max(previous["y_mm"] + previous["height_mm"] for previous in horizontally_overlapping) + gap,
        2,
    )


def _is_decorative_component(component: dict[str, Any]) -> bool:
    return component["kind"] in {"accent-band", "accent-rail", "watermark"}


def _rectangles_overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return not (
        first["x_mm"] + first["width_mm"] <= second["x_mm"]
        or second["x_mm"] + second["width_mm"] <= first["x_mm"]
        or first["y_mm"] + first["height_mm"] <= second["y_mm"]
        or second["y_mm"] + second["height_mm"] <= first["y_mm"]
    )


def _layout_score(
    paper: PaperFormat,
    components: list[dict[str, Any]],
    data: dict[str, Any],
) -> dict[str, Any]:
    area = paper.width_mm * paper.height_mm
    used_area = sum(component["width_mm"] * component["height_mm"] for component in components)
    bottom = max((component["y_mm"] + component["height_mm"]) for component in components)
    overflow_mm = max(0.0, bottom - paper.height_mm)
    density = used_area / area if area else 0
    required = {
        "company-header",
        "logo",
        "title",
        "invoice-meta",
        "buyer",
        "dates",
        "items-table",
        "totals",
    }
    present = {component["kind"] for component in components}
    return {
        "density": round(density, 3),
        "bottom_mm": round(bottom, 2),
        "overflow_mm": round(overflow_mm, 2),
        "required_components_present": sorted(required.intersection(present)),
        "line_item_count": len(data["items"]),
    }
