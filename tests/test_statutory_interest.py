"""Tests for statutory interest and compensation calculation."""

from decimal import Decimal

import pytest

from src.billing.statutory_interest import (
    calculate_statutory_charges,
    compensation_fee_for_principal,
)


class TestCompensationFeeForPrincipal:
    def test_under_1000_is_40(self):
        assert compensation_fee_for_principal(Decimal("999.99")) == Decimal("40")

    def test_1000_to_under_10000_is_70(self):
        assert compensation_fee_for_principal(Decimal("1000")) == Decimal("70")
        assert compensation_fee_for_principal(Decimal("9999.99")) == Decimal("70")

    def test_10000_and_above_is_100(self):
        assert compensation_fee_for_principal(Decimal("10000")) == Decimal("100")
        assert compensation_fee_for_principal(Decimal("50000")) == Decimal("100")


class TestCalculateStatutoryCharges:
    def test_not_yet_overdue_has_no_charges(self):
        charges = calculate_statutory_charges(
            principal=Decimal("4500.00"), days_overdue=0, boe_base_rate_percent=Decimal("4.0")
        )
        assert charges.accrued_interest == Decimal("0.00")
        assert charges.compensation_fee == Decimal("0.00")
        assert charges.total_due == Decimal("4500.00")

    def test_negative_days_overdue_has_no_charges(self):
        charges = calculate_statutory_charges(
            principal=Decimal("4500.00"), days_overdue=-2, boe_base_rate_percent=Decimal("4.0")
        )
        assert charges.accrued_interest == Decimal("0.00")

    def test_annual_rate_is_base_plus_8(self):
        charges = calculate_statutory_charges(
            principal=Decimal("4500.00"), days_overdue=16, boe_base_rate_percent=Decimal("4.0")
        )
        assert charges.annual_rate_percent == Decimal("12.0")

    def test_interest_accrues_daily(self):
        # 4500 * (12% / 365) * 16 days = 23.67
        charges = calculate_statutory_charges(
            principal=Decimal("4500.00"), days_overdue=16, boe_base_rate_percent=Decimal("4.0")
        )
        assert charges.accrued_interest == Decimal("23.67")

    def test_compensation_fee_included_once_overdue(self):
        charges = calculate_statutory_charges(
            principal=Decimal("4500.00"), days_overdue=16, boe_base_rate_percent=Decimal("4.0")
        )
        assert charges.compensation_fee == Decimal("70")

    def test_total_due_sums_principal_interest_and_compensation(self):
        charges = calculate_statutory_charges(
            principal=Decimal("4500.00"), days_overdue=16, boe_base_rate_percent=Decimal("4.0")
        )
        expected = charges.principal + charges.accrued_interest + charges.compensation_fee
        assert charges.total_due == expected

    def test_non_positive_principal_raises(self):
        with pytest.raises(ValueError):
            calculate_statutory_charges(
                principal=Decimal("0"), days_overdue=10, boe_base_rate_percent=Decimal("4.0")
            )
