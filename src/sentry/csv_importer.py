"""CSV upload parser for manual invoice intake.

Expected CSV columns:
    debtor_company, contact_name, contact_email, contact_phone,
    invoice_number, amount, currency, due_date

- currency defaults to GBP if omitted
- contact_phone is optional
- due_date format: YYYY-MM-DD
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID

from src.db.models import Contact, ContactSource, Database, Invoice
from src.strategist.state_machine import initial_phase_for_due_date


@dataclass
class ImportError:
    row: int
    column: str
    message: str


@dataclass
class ImportResult:
    invoices_created: int = 0
    contacts_created: int = 0
    errors: list[ImportError] = field(default_factory=list)
    skipped: int = 0

    @property
    def success(self) -> bool:
        return self.invoices_created > 0


REQUIRED_COLUMNS = {
    "debtor_company",
    "contact_name",
    "contact_email",
    "invoice_number",
    "amount",
    "due_date",
}
OPTIONAL_COLUMNS = {"contact_phone", "currency", "contact_role"}


def parse_csv(file_content: str | bytes) -> tuple[list[dict], list[ImportError]]:
    """Parse and validate CSV content. Returns (rows, errors)."""
    if isinstance(file_content, bytes):
        file_content = file_content.decode("utf-8-sig")

    # Strip BOM if present in string input
    file_content = file_content.lstrip("\ufeff")

    reader = csv.DictReader(io.StringIO(file_content))

    if not reader.fieldnames:
        return [], [ImportError(row=0, column="", message="CSV file is empty or has no header row")]

    # Normalise headers: strip whitespace, lowercase
    normalised = {h.strip().lower().replace(" ", "_"): h for h in reader.fieldnames}
    missing = REQUIRED_COLUMNS - set(normalised.keys())
    if missing:
        return [], [
            ImportError(
                row=0, column="", message=f"Missing required columns: {', '.join(sorted(missing))}"
            )
        ]

    rows: list[dict] = []
    errors: list[ImportError] = []

    for i, raw_row in enumerate(reader, start=2):  # row 2 = first data row
        # Remap to normalised keys
        row = {
            normalised_key: (raw_row.get(original_header) or "").strip()
            for normalised_key, original_header in normalised.items()
        }

        row_errors = _validate_row(i, row)
        if row_errors:
            errors.extend(row_errors)
            continue

        rows.append(row)

    return rows, errors


def _validate_row(row_num: int, row: dict) -> list[ImportError]:
    """Validate a single CSV row."""
    errors: list[ImportError] = []

    for col in REQUIRED_COLUMNS:
        if not row.get(col):
            errors.append(ImportError(row=row_num, column=col, message=f"'{col}' is required"))

    if errors:
        return errors

    # Validate amount
    try:
        amount = Decimal(row["amount"].replace(",", ""))
        if amount <= 0:
            errors.append(
                ImportError(
                    row=row_num,
                    column="amount",
                    message="Amount must be positive",
                )
            )
    except InvalidOperation:
        errors.append(
            ImportError(
                row=row_num,
                column="amount",
                message=f"Invalid amount: {row['amount']}",
            )
        )

    # Validate due_date
    try:
        parsed_date = date.fromisoformat(row["due_date"])
        if parsed_date > date.today():
            errors.append(
                ImportError(
                    row=row_num,
                    column="due_date",
                    message="Due date is in the future — invoice is not overdue",
                )
            )
    except ValueError:
        errors.append(
            ImportError(
                row=row_num,
                column="due_date",
                message=f"Invalid date format: {row['due_date']} (expected YYYY-MM-DD)",
            )
        )

    # Validate email
    email = row["contact_email"]
    if "@" not in email or "." not in email.split("@")[-1]:
        errors.append(
            ImportError(
                row=row_num,
                column="contact_email",
                message=f"Invalid email: {email}",
            )
        )

    return errors


def import_csv(file_content: str | bytes, sme_id: UUID, db: Database) -> ImportResult:
    """Parse a CSV file and insert invoices + contacts into the database.

    Args:
        file_content: Raw CSV content (string or bytes).
        sme_id: The SME this import belongs to.
        db: Database client instance.

    Returns:
        ImportResult with counts and any validation errors.
    """
    rows, parse_errors = parse_csv(file_content)
    result = ImportResult(errors=parse_errors)

    # Query existing invoices for this SME to detect duplicates against DB
    existing = db.list_all_invoices(sme_id=sme_id)
    existing_numbers = {inv["invoice_number"] for inv in existing}

    seen_invoice_numbers: set[str] = set()

    for row in rows:
        inv_num = row["invoice_number"]

        # De-duplicate against existing DB records
        if inv_num in existing_numbers:
            result.skipped += 1
            continue

        # De-duplicate within this upload
        if inv_num in seen_invoice_numbers:
            result.skipped += 1
            continue
        seen_invoice_numbers.add(inv_num)

        amount = Decimal(row["amount"].replace(",", ""))
        currency = row.get("currency", "").upper() or "GBP"
        due_date = date.fromisoformat(row["due_date"])

        invoice = Invoice(
            sme_id=sme_id,
            invoice_number=inv_num,
            debtor_company=row["debtor_company"],
            amount=amount,
            currency=currency,
            due_date=due_date,
            current_phase=initial_phase_for_due_date(due_date),
        )
        db.create_invoice(invoice)
        result.invoices_created += 1

        contact = Contact(
            invoice_id=invoice.id,
            name=row["contact_name"],
            email=row["contact_email"],
            phone=row.get("contact_phone") or None,
            role=row.get("contact_role") or None,
            is_primary=True,
            source=ContactSource.CSV_UPLOAD,
        )
        db.create_contact(contact)
        result.contacts_created += 1

    return result
