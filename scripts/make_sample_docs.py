"""Create deterministic fictional banking fixtures; existing files are never regenerated."""

import csv
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import ROOT
from app.log import log

FICTION = "Fictional Harbourline Bank demonstration document. These are not real banking terms."
HEADERS = ["Tenure months", "General rate", "Senior rate", "Effective from"]
RATE_ROWS = [
    ["12", "7.10%", "7.60%", "01 Oct 2026"],
    ["24", "7.35%*", "7.85%", "01 Oct 2026"],
    ["36", "6.85%", "7.35%", "01 Oct 2026"],
]
CORPUS = {
    "savings_faq.md": (
        "Savings account FAQ",
        "faq",
        "savings_account",
        """# Savings account FAQ
Fictional Harbourline Bank demonstration terms.
## Opening a savings account
Adults may open a savings account with identity proof, address proof and a recent photograph. A PAN declaration is required.
## Minimum balance
The minimum monthly average balance for the Harbour Savings account is INR 5000. The zero-balance student account has no minimum balance requirement.
## Account nomination
A customer can register one nominee through the branch. Updating the nominee is free.
## Interest credit
Savings interest is calculated on the daily closing balance and credited quarterly.
## Joint accounts
Joint savings accounts support either-or-survivor operation when selected at opening.
""",
    ),
    "digital_faq.md": (
        "Digital banking FAQ",
        "faq",
        "savings_account",
        """# Digital banking FAQ
Fictional Harbourline Bank demonstration terms.
## Reset mobile banking access
Reset a forgotten mobile banking password using the registered device and branch verification. Never share an OTP with anyone.
## UPI transfers
UPI transfers are available around the clock. A pending UPI transfer should be checked using its transaction reference before retrying.
## NEFT timing
NEFT transfers are available all day, including weekends and bank holidays. NEFT charges are shown in the fees schedule.
## IMPS timing
IMPS provides immediate transfers around the clock. Check beneficiary details before confirming.
## Device change
Changing your mobile device requires device registration and a cooling period of 24 hours before adding beneficiaries.
""",
    ),
    "fraud_policy.txt": (
        "Grievance and fraud reporting policy",
        "policy",
        "savings_account",
        """# Grievance and fraud reporting
Fictional Harbourline Bank demonstration policy.
## Lost card or suspected fraud
Immediately block a lost card using mobile banking or contact the branch fraud desk. Do not share your PIN, CVV or OTP.
## Complaint acknowledgement
A complaint receives an acknowledgement within 2 working days and a tracking reference.
## Escalation
Unresolved complaints may be escalated to the branch manager after 7 working days. The nodal officer reviews escalated complaints within 15 working days.
## Transaction disputes
Keep the transaction reference, date and disputed amount. Report an unauthorised transaction promptly.
""",
    ),
}
POLICIES = [
    (
        "Cheque book eligibility",
        "An active savings or current account can request a cheque book. A frozen account cannot submit a request.",
    ),
    (
        "Cheque book leaves",
        "Cheque books are available with 20, 40 or 60 leaves. Delivery normally takes 5 working days.",
    ),
    (
        "Cheque book charges",
        "The first 20 cheque leaves each quarter are free. Additional cheque leaves cost INR 3 per leaf.",
    ),
    (
        "Debit card replacement",
        "A lost debit card must be blocked before replacement. The standard replacement card fee is INR 250 plus applicable taxes.",
    ),
    (
        "Debit card delivery",
        "Normal debit card delivery takes 7 working days. Emergency delivery takes 3 working days where supported.",
    ),
]
LOANS = [
    (
        "Home loan eligibility",
        "Home loan applicants must be aged 21 to 65 years at application. Salaried applicants provide 3 salary slips and 6 months of bank statements.",
    ),
    (
        "Personal loan eligibility",
        "A personal loan requires a minimum monthly net income of INR 25000 and 12 months of employment.",
    ),
    (
        "Credit assessment",
        "Credit decisions consider repayment history, existing obligations and verified income. Approval is never guaranteed.",
    ),
    (
        "Debt obligations",
        "Total monthly debt obligations should normally remain within 50 percent of verified monthly net income.",
    ),
    (
        "Loan prepayment",
        "Floating-rate home loans for individual borrowers have no prepayment charge under these fictional demonstration terms.",
    ),
]


def table_image(rows, *, clipped=False, blur=False):
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    image = Image.new("RGB", (1160, 400), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=25)
    draw.text(
        (20, 15),
        "Harbourline Bank - fictional fixed deposit rates",
        font=font,
        fill="#203448",
    )
    xs = [20, 290, 540, 785, 1140]
    draw.rectangle((20, 65, 1140, 115), fill="#203448")
    for c, header in enumerate(HEADERS):
        draw.text((xs[c] + 8, 78), header, font=font, fill="white")
    for r, row in enumerate(rows):
        y = 115 + r * 58
        draw.rectangle(
            (20, y, 1140, y + 58),
            fill="#edf1f3" if r % 2 else "white",
            outline="#b1bdc5",
        )
        for c, value in enumerate(row):
            draw.text((xs[c] + 8, y + 15), str(value), font=font, fill="#182533")
    draw.text(
        (20, 320),
        "* Special tenure; premature withdrawal conditions apply.",
        font=ImageFont.load_default(size=20),
        fill="#253747",
    )
    if clipped:
        image = image.crop((0, 0, 1160, 115 + (len(rows) - 1) * 58 + 28))
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(18))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def make_samples():
    import openpyxl
    import pymupdf
    from docx import Document
    from pptx import Presentation
    from pptx.util import Inches

    target = ROOT / "data/bank_docs"
    target.mkdir(parents=True, exist_ok=True)
    manifest = []

    def add(
        name,
        title,
        doc_type,
        product,
        *,
        lifecycle="active",
        version="2026.10",
        effective="2026-10-01",
    ):
        manifest.append(
            {
                "filename": name,
                "title": title,
                "doc_type": doc_type,
                "product": product,
                "version": version,
                "effective_date": effective,
                "lifecycle": lifecycle,
            }
        )

    for name, (title, kind, product, text) in CORPUS.items():
        if not (target / name).exists():
            (target / name).write_text(text, encoding="utf-8")
        add(name, title, kind, product)
    if not (target / "loan_eligibility.pdf").exists():
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((45, 40), FICTION, fontsize=8)
        y = 85
        for title, text in LOANS:
            page.insert_text((45, y), title, fontsize=13)
            page.insert_textbox((45, y + 12, 550, y + 75), text, fontsize=11)
            y += 120
        doc.save(target / "loan_eligibility.pdf")
        doc.close()
    add(
        "loan_eligibility.pdf",
        "Loan eligibility and credit rules",
        "policy",
        "home_loan",
    )
    if not (target / "card_cheque_policy.docx").exists():
        document = Document()
        document.add_paragraph(FICTION)
        for title, text in POLICIES:
            document.add_heading(title, level=1)
            document.add_paragraph(text)
        grid = document.add_table(rows=1, cols=2)
        grid.rows[0].cells[0].text, grid.rows[0].cells[1].text = (
            "Delivery type",
            "Working days",
        )
        for label, days in (
            ("Cheque book", "5"),
            ("Normal debit card", "7"),
            ("Emergency debit card", "3"),
        ):
            cells = grid.add_row().cells
            cells[0].text, cells[1].text = label, days
        document.save(target / "card_cheque_policy.docx")
    add(
        "card_cheque_policy.docx",
        "Cheque book and debit card policy",
        "policy",
        "debit_card",
    )

    def workbook(name, title, headers, rows):
        if not (target / name).exists():
            book = openpyxl.Workbook()
            sheet = book.active
            sheet.title = title
            sheet.append([FICTION])
            sheet.append(headers)
            for row in rows:
                sheet.append(row)
            book.save(target / name)
            book.close()

    fees = [
        ["NEFT charges", "general", 0],
        ["IMPS charges", "general", 500],
        ["Debit card replacement", "general", 25000],
        ["Duplicate statement", "general", 10000],
        ["Cheque return", "general", 30000],
    ]
    workbook("fees.xlsx", "Fees", ["service", "segment", "amount_paise"], fees)
    add("fees.xlsx", "Fees and charges schedule", "rate_card", "fees_and_charges")
    rates = [
        ["fixed_deposit", months, segment, rate]
        for months, general, senior in (
            (12, "7.10", "7.60"),
            (24, "7.35", "7.85"),
            (36, "6.85", "7.35"),
        )
        for segment, rate in (("general", general), ("senior_citizen", senior))
    ]
    rates += [
        ["home_loan", 240, "general", "8.50"],
        ["personal_loan", 60, "general", "12.25"],
        ["car_loan", 84, "general", "9.10"],
    ]
    workbook(
        "rates.xlsx",
        "Rates",
        ["product", "tenure_months", "segment", "rate_percent"],
        rates,
    )
    add(
        "rates.xlsx",
        "Deposit and loan interest rate card",
        "rate_card",
        "fixed_deposit",
    )
    workbook(
        "rates_old.xlsx",
        "Older rates",
        ["product", "tenure_months", "segment", "rate_percent"],
        [["fixed_deposit", 12, "general", "6.00"]],
    )
    add(
        "rates_old.xlsx",
        "Superseded deposit rate card",
        "rate_card",
        "fixed_deposit",
        lifecycle="superseded",
        version="2025.10",
        effective="2025-10-01",
    )
    image = table_image(RATE_ROWS)
    if not (target / "rates_image.pdf").exists():
        doc = pymupdf.open()
        page = doc.new_page(width=700, height=360)
        page.insert_text((25, 25), FICTION, fontsize=8)
        page.insert_image(pymupdf.Rect(20, 55, 680, 310), stream=image)
        doc.save(target / "rates_image.pdf")
        doc.close()
    add(
        "rates_image.pdf",
        "Illustrated fixed deposit rate card",
        "rate_card",
        "fixed_deposit",
    )
    if not (target / "offers.pptx").exists():
        deck = Presentation()
        slide = deck.slides.add_slide(deck.slide_layouts[5])
        slide.shapes.title.text = "Harbourline new customer offers"
        box = slide.shapes.add_textbox(Inches(0.5), Inches(1), Inches(9), Inches(0.8))
        box.text = "Fictional offer: new salary accounts receive a debit card issuance fee waiver until 31 December 2026."
        slide.notes_slide.notes_text_frame.text = "Offer eligibility requires the first salary credit within 60 days of account opening."
        slide.shapes.add_picture(
            io.BytesIO(image), Inches(0.4), Inches(2), width=Inches(9)
        )
        slide2 = deck.slides.add_slide(deck.slide_layouts[5])
        slide2.shapes.title.text = "Offer eligibility"
        native = slide2.shapes.add_table(
            3, 2, Inches(1), Inches(2), Inches(7), Inches(2)
        ).table
        for r, row in enumerate(
            [
                ["Offer", "Eligibility"],
                ["Salary debit card waiver", "First salary within 60 days"],
                ["Student account", "Age 18 to 25"],
            ]
        ):
            for c, value in enumerate(row):
                native.cell(r, c).text = value
        deck.save(target / "offers.pptx")
    add("offers.pptx", "New customer offers", "offer", "debit_card")
    if not (target / "eligibility.csv").exists():
        with (target / "eligibility.csv").open(
            "w", newline="", encoding="utf-8"
        ) as file:
            writer = csv.writer(file)
            writer.writerows(
                [
                    ["product", "minimum_age", "maximum_age"],
                    ["home_loan", 21, 65],
                    ["personal_loan", 23, 60],
                ]
            )
    add("eligibility.csv", "Loan eligibility summary", "policy", "home_loan")
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    extraction = ROOT / "data/gate1_images"
    extraction.mkdir(exist_ok=True)
    cases = []
    for index in range(20):
        rows = [
            [
                str(months),
                f"{6.50 + index * 0.01 + r * 0.10:.2f}%",
                f"{7.00 + index * 0.01 + r * 0.10:.2f}%",
                "01 Oct 2026",
            ]
            for r, months in enumerate((12, 24, 36))
        ]
        clipped = index % 5 == 4
        name = f"rates_{index:02}.png"
        if not (extraction / name).exists():
            (extraction / name).write_bytes(table_image(rows, clipped=clipped))
        cases.append(
            {
                "image": name,
                "columns": HEADERS,
                "rows": rows[:-1] + [[None] * 4] if clipped else rows,
                "truncated": clipped,
            }
        )
    (extraction / "truth.json").write_text(
        json.dumps(cases, indent=2), encoding="utf-8"
    )
    log.step(
        "INGEST",
        "sample documents ready",
        count=len(manifest),
        extraction_images=len(cases),
    )
    return manifest


if __name__ == "__main__":
    make_samples()
