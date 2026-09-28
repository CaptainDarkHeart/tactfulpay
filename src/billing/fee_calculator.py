"""Fee calculation: 10% of invoice value or GBP 500 flat.

Business rules:
- For invoices over GBP 5,000 (FEE_PERCENTAGE_THRESHOLD): 10% of recovered amount
- For invoices at or below GBP 5,000, OR stalled invoices 60+ days: GBP 500 flat fee
- If nothing is recovered, no fee is charged
- VAT is added on top at settings.vat_rate, but only once settings.vat_registered is
  true. Debt collection fees are excluded from the financial services VAT exemption
  (Sch 9 Group 5, VATA 1994), so the fee is standard rated once registered.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from src.config import settings
from src.db.models import Fee, FeeStatus, FeeType

STALLED_DAYS_THRESHOLD = 60


def calculate_fee(
    invoice_amount: Decimal,
    sme_id: UUID | str,
    invoice_id: UUID | str,
    days_overdue: int = 0,
) -> Fee:
    """Calculate the recovery fee for a successfully collected invoice.

    Args:
        invoice_amount: The amount that was recovered.
        sme_id: The SME client's ID.
        invoice_id: The invoice ID.
        days_overdue: Number of days the invoice was overdue when recovered.
            If 60 or more, triggers the flat fee regardless of amount.

    Returns:
        A Fee object (not yet persisted) with the calculated amount.
    """
    if invoice_amount <= 0:
        raise ValueError(f"Invoice amount must be positive, got {invoice_amount}")

    threshold = Decimal(str(settings.fee_percentage_threshold))
    percentage = Decimal(str(settings.fee_percentage))
    flat_amount = Decimal(str(settings.fee_flat_amount))

    # Stalled invoices (60+ days overdue) always use flat fee
    if days_overdue >= STALLED_DAYS_THRESHOLD:
        fee_amount = flat_amount
        fee_type = FeeType.FLAT
    elif invoice_amount > threshold:
        fee_amount = (invoice_amount * percentage / 100).quantize(Decimal("0.01"))
        fee_type = FeeType.PERCENTAGE
    else:
        fee_amount = flat_amount
        fee_type = FeeType.FLAT

    vat_amount = Decimal("0")
    if settings.vat_registered:
        vat_rate = Decimal(str(settings.vat_rate))
        vat_amount = (fee_amount * vat_rate / 100).quantize(Decimal("0.01"))

    return Fee(
        invoice_id=UUID(str(invoice_id)),
        sme_id=UUID(str(sme_id)),
        fee_type=fee_type,
        fee_amount=fee_amount,
        vat_amount=vat_amount,
        invoice_amount_recovered=invoice_amount,
        status=FeeStatus.PENDING,
    )
