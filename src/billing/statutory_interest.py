"""Statutory interest and compensation under the Late Payment of Commercial
Debts (Interest) Act 1998 (as amended).

This is the debtor's automatic statutory liability to the SME creditor, not
TactfulPay's own recovery fee (see fee_calculator.py for that). It is used
from Phase 4 onward to state exact figures instead of a qualitative warning.

Business rules:
- Interest accrues daily at the Bank of England base rate plus 8% per annum,
  from the day the invoice became overdue.
- A one-off statutory compensation fee attaches once an invoice is overdue:
    - Under GBP 1,000: GBP 40
    - GBP 1,000 to under GBP 10,000: GBP 70
    - GBP 10,000 and above: GBP 100
- These cannot be contracted out of. If nothing is overdue yet, neither
  interest nor compensation applies.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

STATUTORY_INTEREST_MARGIN_PERCENT = Decimal("8")
DAYS_PER_YEAR = Decimal("365")

COMPENSATION_FEE_LOW = Decimal("40")
COMPENSATION_FEE_MID = Decimal("70")
COMPENSATION_FEE_HIGH = Decimal("100")
COMPENSATION_THRESHOLD_LOW = Decimal("1000")
COMPENSATION_THRESHOLD_HIGH = Decimal("10000")


@dataclass(frozen=True)
class StatutoryCharges:
    """Statutory interest and compensation owed on an overdue invoice."""

    principal: Decimal
    days_overdue: int
    annual_rate_percent: Decimal
    accrued_interest: Decimal
    compensation_fee: Decimal
    total_due: Decimal


def compensation_fee_for_principal(principal: Decimal) -> Decimal:
    """The one-off statutory compensation fee for a given invoice principal."""
    if principal < COMPENSATION_THRESHOLD_LOW:
        return COMPENSATION_FEE_LOW
    if principal < COMPENSATION_THRESHOLD_HIGH:
        return COMPENSATION_FEE_MID
    return COMPENSATION_FEE_HIGH


def calculate_statutory_charges(
    principal: Decimal,
    days_overdue: int,
    boe_base_rate_percent: Decimal,
) -> StatutoryCharges:
    """Calculate accrued statutory interest and compensation for an invoice.

    Args:
        principal: The outstanding invoice amount.
        days_overdue: Days past the due date. If 0 or negative, nothing has
            accrued yet and neither interest nor compensation applies.
        boe_base_rate_percent: Current Bank of England base rate, percent.

    Returns:
        A StatutoryCharges with the accrued interest, compensation fee, and
        total amount now due (principal + interest + compensation).
    """
    if principal <= 0:
        raise ValueError(f"Principal must be positive, got {principal}")

    annual_rate = Decimal(str(boe_base_rate_percent)) + STATUTORY_INTEREST_MARGIN_PERCENT

    if days_overdue <= 0:
        return StatutoryCharges(
            principal=principal,
            days_overdue=0,
            annual_rate_percent=annual_rate,
            accrued_interest=Decimal("0.00"),
            compensation_fee=Decimal("0.00"),
            total_due=principal,
        )

    daily_rate = annual_rate / Decimal("100") / DAYS_PER_YEAR
    accrued_interest = (principal * daily_rate * days_overdue).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    compensation_fee = compensation_fee_for_principal(principal)

    return StatutoryCharges(
        principal=principal,
        days_overdue=days_overdue,
        annual_rate_percent=annual_rate,
        accrued_interest=accrued_interest,
        compensation_fee=compensation_fee,
        total_due=principal + accrued_interest + compensation_fee,
    )
