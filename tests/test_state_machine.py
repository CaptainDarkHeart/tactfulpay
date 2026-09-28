"""Tests for the phase progression state machine."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

from src.db.models import Classification, InvoicePhase, InvoiceStatus
from src.strategist.state_machine import (
    get_next_followup_day,
    handle_classification,
    initial_phase_for_due_date,
    should_escalate,
)


def _mock_db():
    db = MagicMock()
    db.update_invoice = MagicMock()
    return db


class TestHandleClassification:
    def test_promise_to_pay_monitors(self):
        db = _mock_db()
        result = handle_classification(
            Classification.PROMISE_TO_PAY, InvoicePhase.PHASE_1, uuid4(), db
        )
        assert result.action == "monitor"

    def test_dispute_pauses_immediately(self):
        db = _mock_db()
        invoice_id = uuid4()
        result = handle_classification(Classification.DISPUTE, InvoicePhase.PHASE_2, invoice_id, db)
        assert result.action == "pause"
        assert result.new_phase == InvoicePhase.DISPUTED
        assert result.new_status == InvoiceStatus.DISPUTED
        db.update_invoice.assert_called_once()

    def test_hostile_pauses_immediately(self):
        db = _mock_db()
        invoice_id = uuid4()
        result = handle_classification(Classification.HOSTILE, InvoicePhase.PHASE_3, invoice_id, db)
        assert result.action == "pause"
        assert result.new_phase == InvoicePhase.HUMAN_REVIEW
        assert result.new_status == InvoiceStatus.PAUSED
        db.update_invoice.assert_called_once()

    def test_redirect_starts_phase_1_with_new_contact(self):
        db = _mock_db()
        result = handle_classification(Classification.REDIRECT, InvoicePhase.PHASE_2, uuid4(), db)
        assert result.action == "redirect"
        assert result.new_phase == InvoicePhase.PHASE_1

    def test_stall_accelerates_cadence(self):
        db = _mock_db()
        result = handle_classification(Classification.STALL, InvoicePhase.PHASE_1, uuid4(), db)
        assert result.action == "send_message"
        assert result.accelerated is True

    def test_no_response_escalates_phase_1_to_2(self):
        db = _mock_db()
        result = handle_classification(
            Classification.NO_RESPONSE, InvoicePhase.PHASE_1, uuid4(), db
        )
        assert result.action == "escalate_phase"
        assert result.new_phase == InvoicePhase.PHASE_2

    def test_no_response_escalates_phase_2_to_3(self):
        db = _mock_db()
        result = handle_classification(
            Classification.NO_RESPONSE, InvoicePhase.PHASE_2, uuid4(), db
        )
        assert result.action == "escalate_phase"
        assert result.new_phase == InvoicePhase.PHASE_3

    def test_no_response_phase_4_goes_to_human_review(self):
        db = _mock_db()
        result = handle_classification(
            Classification.NO_RESPONSE, InvoicePhase.PHASE_4, uuid4(), db
        )
        assert result.action == "human_review"
        assert result.new_phase == InvoicePhase.HUMAN_REVIEW

    def test_payment_pending_sends_followup(self):
        db = _mock_db()
        result = handle_classification(
            Classification.PAYMENT_PENDING, InvoicePhase.PHASE_1, uuid4(), db
        )
        assert result.action == "send_message"

    def test_check_or_transfer_initiated_sends_followup(self):
        db = _mock_db()
        result = handle_classification(
            Classification.CHECK_OR_TRANSFER_INITIATED, InvoicePhase.PHASE_1, uuid4(), db
        )
        assert result.action == "send_message"

    def test_inability_to_pay_offers_payment_plan(self):
        db = _mock_db()
        result = handle_classification(
            Classification.INABILITY_TO_PAY, InvoicePhase.PHASE_2, uuid4(), db
        )
        assert result.action == "send_message"
        assert result.accelerated is False

    def test_no_response_escalates_phase_0_to_1(self):
        db = _mock_db()
        result = handle_classification(
            Classification.NO_RESPONSE, InvoicePhase.PHASE_0, uuid4(), db
        )
        assert result.action == "escalate_phase"
        assert result.new_phase == InvoicePhase.PHASE_1


class TestInitialPhaseForDueDate:
    def test_due_within_window_starts_phase_0(self):
        today = date(2026, 9, 28)
        assert initial_phase_for_due_date(date(2026, 9, 28), today) == InvoicePhase.PHASE_0
        assert initial_phase_for_due_date(date(2026, 10, 1), today) == InvoicePhase.PHASE_0

    def test_already_overdue_starts_phase_1(self):
        today = date(2026, 9, 28)
        assert initial_phase_for_due_date(date(2026, 9, 20), today) == InvoicePhase.PHASE_1

    def test_far_in_future_starts_phase_1(self):
        today = date(2026, 9, 28)
        assert initial_phase_for_due_date(date(2026, 10, 10), today) == InvoicePhase.PHASE_1


class TestShouldEscalate:
    def test_no_last_outbound_should_escalate(self):
        assert should_escalate(InvoicePhase.PHASE_1, None) is True

    def test_recent_outbound_should_not_escalate(self):
        last = datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(days=1)
        assert should_escalate(InvoicePhase.PHASE_1, last) is False

    def test_old_outbound_should_escalate(self):
        last = datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(days=6)
        assert should_escalate(InvoicePhase.PHASE_1, last) is True

    def test_accelerated_reduces_duration(self):
        # Phase 1 duration is 5 days, accelerated reduces by 2 → 3 days
        last = datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(days=3)
        assert should_escalate(InvoicePhase.PHASE_1, last, accelerated=False) is False
        assert should_escalate(InvoicePhase.PHASE_1, last, accelerated=True) is True

    def test_non_active_phase_returns_false(self):
        assert should_escalate(InvoicePhase.HUMAN_REVIEW, None) is False


class TestGetNextFollowupDay:
    def test_first_followup(self):
        assert get_next_followup_day(InvoicePhase.PHASE_1, 0) == 0

    def test_second_followup(self):
        assert get_next_followup_day(InvoicePhase.PHASE_1, 1) == 2

    def test_third_followup(self):
        assert get_next_followup_day(InvoicePhase.PHASE_1, 2) == 4

    def test_exhausted_followups(self):
        assert get_next_followup_day(InvoicePhase.PHASE_1, 3) is None

    def test_phase_2_followups(self):
        assert get_next_followup_day(InvoicePhase.PHASE_2, 0) == 0
        assert get_next_followup_day(InvoicePhase.PHASE_2, 1) == 3
        assert get_next_followup_day(InvoicePhase.PHASE_2, 2) is None
