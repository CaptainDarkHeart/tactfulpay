"""Tests for the response classifier parsing logic.

Tests the parsing layer (no LLM calls). The LLM integration is tested
separately with real API calls.
"""

from src.db.models import Classification
from src.strategist.response_classifier import _parse_classification


class TestParseClassification:
    def test_standard_format_with_dash(self):
        result, justification = _parse_classification(
            "PROMISE_TO_PAY - The sender commits to paying on Friday"
        )
        assert result == Classification.PROMISE_TO_PAY
        assert "Friday" in justification

    def test_standard_format_with_colon(self):
        result, justification = _parse_classification(
            "DISPUTE: The sender disputes the amount billed"
        )
        assert result == Classification.DISPUTE
        assert "amount" in justification

    def test_standard_format_with_em_dash(self):
        result, justification = _parse_classification("HOSTILE — Sender demands no further contact")
        assert result == Classification.HOSTILE

    def test_category_only(self):
        result, justification = _parse_classification("REDIRECT")
        assert result == Classification.REDIRECT

    def test_newline_separated(self):
        result, justification = _parse_classification(
            "STALL\nThe sender says they are working on it"
        )
        assert result == Classification.STALL

    def test_fallback_on_unparseable(self):
        result, justification = _parse_classification("I don't know what this means")
        assert result == Classification.STALL  # Safe fallback
        assert "Could not parse" in justification

    def test_empty_string_fallback(self):
        result, justification = _parse_classification("")
        assert result == Classification.STALL

    def test_payment_pending(self):
        result, _ = _parse_classification(
            "PAYMENT_PENDING - Check is in the mail but no date given"
        )
        assert result == Classification.PAYMENT_PENDING

    def test_check_or_transfer_initiated(self):
        result, _ = _parse_classification(
            "CHECK_OR_TRANSFER_INITIATED - The sender says the transfer already went out"
        )
        assert result == Classification.CHECK_OR_TRANSFER_INITIATED

    def test_inability_to_pay(self):
        result, _ = _parse_classification(
            "INABILITY_TO_PAY - The sender cites a cash flow shortfall"
        )
        assert result == Classification.INABILITY_TO_PAY

    def test_no_response(self):
        result, _ = _parse_classification("NO_RESPONSE - No reply received within the window")
        assert result == Classification.NO_RESPONSE
