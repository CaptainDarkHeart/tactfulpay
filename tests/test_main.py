"""Integration tests for the main orchestrator (src/main.py).

Tests run_daily_cycle, _process_invoice, and process_inbound_reply
with fully mocked DB, email client, LLM, and notification layers.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.db.models import (
    Channel,
    Classification,
    Direction,
    InvoicePhase,
    InvoiceStatus,
    MessageType,
)
from src.executor.email_sender import EmailResult
from src.main import (
    _get_phase_start_date,
    _process_invoice,
    process_inbound_reply,
    run_daily_cycle,
)
from src.strategist.message_generator import GeneratedMessage
from src.strategist.state_machine import TransitionResult

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC).replace(tzinfo=None)
_SME_ID = str(uuid4())
_INVOICE_ID = str(uuid4())
_CONTACT_ID = str(uuid4())


def _make_sme(**overrides) -> dict:
    base = {
        "id": _SME_ID,
        "company_name": "Acme Digital Ltd",
        "contact_email": "owner@acme.co.uk",
        "discount_authorised": False,
        "max_discount_percent": 0,
        "status": "active",
    }
    base.update(overrides)
    return base


def _make_invoice(**overrides) -> dict:
    base = {
        "id": _INVOICE_ID,
        "sme_id": _SME_ID,
        "invoice_number": "INV-001",
        "debtor_company": "WidgetCorp",
        "amount": "7500.00",
        "currency": "GBP",
        "due_date": (date.today() - timedelta(days=10)).isoformat(),
        "current_phase": InvoicePhase.PHASE_1.value,
        "status": InvoiceStatus.ACTIVE.value,
        "created_at": (_NOW - timedelta(days=10)).isoformat(),
    }
    base.update(overrides)
    return base


def _make_contact(**overrides) -> dict:
    base = {
        "id": _CONTACT_ID,
        "name": "Jane Smith",
        "email": "jane@widgetcorp.com",
        "role": "Finance Manager",
        "phone": None,
        "is_primary": True,
    }
    base.update(overrides)
    return base


def _make_outbound_interaction(phase: str = "1", days_ago: int = 3, **overrides) -> dict:
    base = {
        "id": str(uuid4()),
        "invoice_id": _INVOICE_ID,
        "contact_id": _CONTACT_ID,
        "phase": phase,
        "channel": Channel.EMAIL.value,
        "direction": Direction.OUTBOUND.value,
        "message_type": MessageType.INITIAL.value,
        "content": "Subject: Test\n\nHello",
        "classification": None,
        "sent_at": (_NOW - timedelta(days=days_ago)).isoformat(),
        "delivered": True,
        "opened": False,
        "replied": False,
        "metadata": {"message_id": "msg-123"},
    }
    base.update(overrides)
    return base


def _mock_db(**method_returns) -> MagicMock:
    """Create a mock Database with sensible defaults."""
    db = MagicMock()
    db.list_active_smes.return_value = [_make_sme()]
    db.list_active_invoices.return_value = [_make_invoice()]
    db.get_invoice.return_value = _make_invoice()
    db.get_primary_contact.return_value = _make_contact()
    db.get_latest_outbound.return_value = None
    db.list_interactions.return_value = []
    db.get_sme.return_value = _make_sme()
    db.create_interaction.return_value = None
    db.update_invoice.return_value = None
    for method, retval in method_returns.items():
        getattr(db, method).return_value = retval
    return db


def _mock_email_client(success: bool = True) -> MagicMock:
    client = MagicMock()
    client.send_email.return_value = EmailResult(
        success=success,
        message_id="msg-456" if success else None,
        error=None if success else "Connection refused",
    )
    return client


def _mock_payment_links() -> MagicMock:
    from src.executor.payment_link import PaymentLinkResult

    client = MagicMock()
    client.create_invoice_payment_link.return_value = PaymentLinkResult(
        success=True,
        url="https://pay.stripe.com/test_link",
        payment_link_id="plink_test",
    )
    return client


# ---------------------------------------------------------------------------
# run_daily_cycle
# ---------------------------------------------------------------------------


class TestRunDailyCycle:
    """Tests for the top-level daily processing loop."""

    @patch("src.main.generate_message")
    @patch("src.main.send_collection_email")
    @patch("src.main.schedule_next_send")
    def test_processes_all_smes_and_invoices(self, mock_schedule, mock_send, mock_gen):
        """Cycle iterates over every SME and their invoices."""
        sme_a = _make_sme(id=str(uuid4()), company_name="Alpha Ltd")
        sme_b = _make_sme(id=str(uuid4()), company_name="Beta Ltd")
        inv_a = _make_invoice(id=str(uuid4()), sme_id=sme_a["id"])
        inv_b = _make_invoice(id=str(uuid4()), sme_id=sme_b["id"])

        db = _mock_db()
        db.list_active_smes.return_value = [sme_a, sme_b]
        db.list_active_invoices.side_effect = [[inv_a], [inv_b]]

        mock_schedule.return_value = _NOW - timedelta(hours=1)
        mock_gen.return_value = GeneratedMessage(subject="Hi", body="Hello")
        mock_send.return_value = EmailResult(success=True, message_id="m1")

        run_daily_cycle(db=db, email_client=_mock_email_client(), payment_links=_mock_payment_links())

        assert db.list_active_smes.call_count == 1
        assert db.list_active_invoices.call_count == 2

    @patch("src.main.generate_message")
    @patch("src.main.send_collection_email")
    @patch("src.main.schedule_next_send")
    def test_stops_at_daily_email_limit(self, mock_schedule, mock_send, mock_gen):
        """Daily cycle stops when 30-email limit is reached."""
        invoices = [
            _make_invoice(id=str(uuid4()), invoice_number=f"INV-{i:03d}") for i in range(35)
        ]
        db = _mock_db()
        db.list_active_invoices.return_value = invoices

        mock_schedule.return_value = _NOW - timedelta(hours=1)
        mock_gen.return_value = GeneratedMessage(subject="Hi", body="Hello")
        mock_send.return_value = EmailResult(success=True, message_id="m1")

        run_daily_cycle(db=db, email_client=_mock_email_client(), payment_links=_mock_payment_links())

        # Should have created at most 30 interactions (the limit)
        assert db.create_interaction.call_count <= 30

    def test_empty_sme_list_completes_without_error(self):
        """Cycle handles zero SMEs gracefully."""
        db = _mock_db()
        db.list_active_smes.return_value = []

        run_daily_cycle(db=db, email_client=_mock_email_client(), payment_links=_mock_payment_links())

        db.list_active_invoices.assert_not_called()

    @patch("src.main.generate_message")
    @patch("src.main.send_collection_email")
    @patch("src.main.schedule_next_send")
    def test_total_sent_accumulates_across_smes(self, mock_schedule, mock_send, mock_gen, caplog):
        """The final total must count emails from every SME, not just the last one.

        Regression test: emails_sent_by_inbox used to be reset inside the
        per-SME loop, so the end-of-cycle total only ever reflected the last
        SME processed (and crashed outright with zero SMEs).
        """
        sme_a = _make_sme(id=str(uuid4()), company_name="Alpha Ltd")
        sme_b = _make_sme(id=str(uuid4()), company_name="Beta Ltd")
        inv_a = _make_invoice(id=str(uuid4()), sme_id=sme_a["id"])
        inv_b = _make_invoice(id=str(uuid4()), sme_id=sme_b["id"])

        db = _mock_db()
        db.list_active_smes.return_value = [sme_a, sme_b]
        db.list_active_invoices.side_effect = [[inv_a], [inv_b]]

        mock_schedule.return_value = _NOW - timedelta(hours=1)
        mock_gen.return_value = GeneratedMessage(subject="Hi", body="Hello")
        mock_send.return_value = EmailResult(success=True, message_id="m1")

        with caplog.at_level("INFO"):
            run_daily_cycle(
                db=db, email_client=_mock_email_client(), payment_links=_mock_payment_links()
            )

        assert "Total emails sent: 2" in caplog.text


# ---------------------------------------------------------------------------
# _process_invoice
# ---------------------------------------------------------------------------


class TestProcessInvoice:
    """Tests for single-invoice processing logic."""

    def test_skips_human_review_phase(self):
        """Invoices in HUMAN_REVIEW are not processed."""
        db = _mock_db()
        invoice = _make_invoice(current_phase=InvoicePhase.HUMAN_REVIEW.value)

        result = _process_invoice(db, _mock_email_client(), _mock_payment_links(), _make_sme(), invoice)

        assert result is False
        db.get_primary_contact.assert_not_called()

    def test_skips_resolved_phase(self):
        """Invoices in RESOLVED are not processed."""
        db = _mock_db()
        invoice = _make_invoice(current_phase=InvoicePhase.RESOLVED.value)

        result = _process_invoice(db, _mock_email_client(), _mock_payment_links(), _make_sme(), invoice)

        assert result is False

    def test_skips_disputed_phase(self):
        """Invoices in DISPUTED are not processed."""
        db = _mock_db()
        invoice = _make_invoice(current_phase=InvoicePhase.DISPUTED.value)

        result = _process_invoice(db, _mock_email_client(), _mock_payment_links(), _make_sme(), invoice)

        assert result is False

    def test_skips_when_no_primary_contact(self):
        """Invoices without a primary contact are skipped."""
        db = _mock_db(get_primary_contact=None)

        result = _process_invoice(db, _mock_email_client(), _mock_payment_links(), _make_sme(), _make_invoice())

        assert result is False

    @patch("src.main.can_contact_today", return_value=False)
    def test_skips_when_contact_gap_too_short(self, _mock_gap):
        """Skips if minimum contact gap hasn't elapsed."""
        last_outbound = _make_outbound_interaction(days_ago=0)
        db = _mock_db(get_latest_outbound=last_outbound)

        result = _process_invoice(db, _mock_email_client(), _mock_payment_links(), _make_sme(), _make_invoice())

        assert result is False

    @patch("src.main.schedule_next_send", return_value=None)
    def test_skips_when_followups_exhausted(self, _mock_schedule):
        """Skips if all follow-ups for the phase have been sent."""
        db = _mock_db()

        result = _process_invoice(db, _mock_email_client(), _mock_payment_links(), _make_sme(), _make_invoice())

        assert result is False

    @patch("src.main.schedule_next_send")
    def test_skips_when_next_send_is_future(self, mock_schedule):
        """Skips if scheduled send time is in the future."""
        tomorrow = datetime.combine(date.today() + timedelta(days=1), datetime.min.time())
        mock_schedule.return_value = tomorrow
        db = _mock_db()

        result = _process_invoice(db, _mock_email_client(), _mock_payment_links(), _make_sme(), _make_invoice())

        assert result is False

    @patch("src.main.generate_message")
    @patch("src.main.send_collection_email")
    @patch("src.main.schedule_next_send")
    def test_successful_send_creates_interaction(self, mock_schedule, mock_send, mock_gen):
        """A successful send logs an interaction in the DB."""
        mock_schedule.return_value = _NOW - timedelta(hours=1)
        mock_gen.return_value = GeneratedMessage(subject="Check on INV-001", body="Hi Jane")
        mock_send.return_value = EmailResult(success=True, message_id="msg-789")
        db = _mock_db()

        result = _process_invoice(db, _mock_email_client(), _mock_payment_links(), _make_sme(), _make_invoice())

        assert result is True
        db.create_interaction.assert_called_once()
        interaction = db.create_interaction.call_args[0][0]
        assert interaction.direction == Direction.OUTBOUND
        assert interaction.channel == Channel.EMAIL
        assert interaction.message_type == MessageType.INITIAL
        assert "Check on INV-001" in interaction.content

    @patch("src.main.generate_message")
    @patch("src.main.send_collection_email")
    @patch("src.main.schedule_next_send")
    def test_failed_send_returns_false_no_interaction(self, mock_schedule, mock_send, mock_gen):
        """A failed email send does not log an interaction."""
        mock_schedule.return_value = _NOW - timedelta(hours=1)
        mock_gen.return_value = GeneratedMessage(subject="Hi", body="Hello")
        mock_send.return_value = EmailResult(success=False, error="Timeout")
        db = _mock_db()

        result = _process_invoice(db, _mock_email_client(), _mock_payment_links(), _make_sme(), _make_invoice())

        assert result is False
        db.create_interaction.assert_not_called()

    @patch("src.main.generate_message")
    @patch("src.main.schedule_next_send")
    def test_llm_error_returns_false(self, mock_schedule, mock_gen):
        """If the LLM call fails, the invoice is skipped gracefully."""
        mock_schedule.return_value = _NOW - timedelta(hours=1)
        mock_gen.side_effect = RuntimeError("LLM timeout")
        db = _mock_db()

        result = _process_invoice(db, _mock_email_client(), _mock_payment_links(), _make_sme(), _make_invoice())

        assert result is False
        db.create_interaction.assert_not_called()

    @patch("src.main.generate_message")
    @patch("src.main.send_collection_email")
    @patch("src.main.schedule_next_send")
    def test_follow_up_sets_correct_message_type(self, mock_schedule, mock_send, mock_gen):
        """Second message in a phase is typed as FOLLOW_UP, not INITIAL."""
        # One prior outbound in phase 1
        prior = _make_outbound_interaction(phase="1", days_ago=3)
        db = _mock_db(
            get_latest_outbound=_make_outbound_interaction(phase="1", days_ago=2),
            list_interactions=[prior],
        )

        mock_schedule.return_value = _NOW - timedelta(hours=1)
        mock_gen.return_value = GeneratedMessage(subject="Follow up", body="Bump")
        mock_send.return_value = EmailResult(success=True, message_id="msg-f1")

        result = _process_invoice(db, _mock_email_client(), _mock_payment_links(), _make_sme(), _make_invoice())

        assert result is True
        interaction = db.create_interaction.call_args[0][0]
        assert interaction.message_type == MessageType.FOLLOW_UP

    @patch("src.main._send_notifications")
    @patch("src.main.should_escalate", return_value=True)
    @patch("src.main.handle_classification")
    def test_escalation_to_human_review_sends_notifications(
        self,
        mock_handle,
        mock_esc,
        mock_notify,
    ):
        """When phase 4 exhausts, human_review triggers notifications."""
        mock_handle.return_value = TransitionResult(
            action="human_review",
            new_phase=InvoicePhase.HUMAN_REVIEW,
            message="Phase 4 exhausted.",
        )
        prior = _make_outbound_interaction(phase="4", days_ago=8)
        db = _mock_db(
            get_latest_outbound=prior,
            list_interactions=[prior],
        )
        invoice = _make_invoice(current_phase=InvoicePhase.PHASE_4.value)

        result = _process_invoice(db, _mock_email_client(), _mock_payment_links(), _make_sme(), invoice)

        assert result is False
        mock_notify.assert_called_once()

    @patch("src.main.generate_message")
    @patch("src.main.send_collection_email")
    @patch("src.main.schedule_next_send")
    @patch("src.main.should_escalate", return_value=True)
    @patch("src.main.handle_classification")
    def test_escalation_refreshes_phase(
        self,
        mock_handle,
        mock_esc,
        mock_schedule,
        mock_send,
        mock_gen,
    ):
        """After escalation, the updated phase is used for message generation."""
        mock_handle.return_value = TransitionResult(
            action="escalate_phase",
            new_phase=InvoicePhase.PHASE_2,
            message="Escalated to Phase 2.",
        )
        prior = _make_outbound_interaction(phase="1", days_ago=6)
        updated_invoice = _make_invoice(current_phase=InvoicePhase.PHASE_2.value)
        db = _mock_db(
            get_latest_outbound=prior,
            list_interactions=[prior],
            get_invoice=updated_invoice,
        )

        mock_schedule.return_value = _NOW - timedelta(hours=1)
        mock_gen.return_value = GeneratedMessage(subject="Escalated", body="Phase 2 msg")
        mock_send.return_value = EmailResult(success=True, message_id="msg-e1")

        result = _process_invoice(db, _mock_email_client(), _mock_payment_links(), _make_sme(), _make_invoice())

        assert result is True
        # Verify the message context used Phase 2
        ctx = mock_gen.call_args[0][0]
        assert ctx.phase == InvoicePhase.PHASE_2
        assert ctx.interaction_count_in_phase == 0

    @patch("src.main.generate_message")
    @patch("src.main.send_collection_email")
    @patch("src.main.schedule_next_send")
    def test_reply_to_sent_passes_previous_message_id(self, mock_schedule, mock_send, mock_gen):
        """Reply-to-sent messages thread using the previous message ID."""
        prior = _make_outbound_interaction(phase="1", days_ago=3)
        db = _mock_db(
            get_latest_outbound=prior,
            list_interactions=[prior],
        )

        mock_schedule.return_value = _NOW - timedelta(hours=1)
        mock_gen.return_value = GeneratedMessage(
            subject="Re: Check on INV-001",
            body="Bump",
            is_reply_to_sent=True,
        )
        mock_send.return_value = EmailResult(success=True, message_id="msg-r1")

        _process_invoice(db, _mock_email_client(), _mock_payment_links(), _make_sme(), _make_invoice())

        # send_collection_email should receive the prior message_id
        call_kwargs = mock_send.call_args[1]
        assert call_kwargs["previous_message_id"] == "msg-123"

    @patch("src.main.generate_message")
    @patch("src.main.send_collection_email")
    @patch("src.main.schedule_next_send")
    def test_non_reply_to_sent_omits_previous_message_id(self, mock_schedule, mock_send, mock_gen):
        """Non-reply messages don't thread to previous message."""
        prior = _make_outbound_interaction(phase="1", days_ago=3)
        db = _mock_db(
            get_latest_outbound=prior,
            list_interactions=[prior],
        )

        mock_schedule.return_value = _NOW - timedelta(hours=1)
        mock_gen.return_value = GeneratedMessage(
            subject="New subject",
            body="Hello",
            is_reply_to_sent=False,
        )
        mock_send.return_value = EmailResult(success=True, message_id="msg-n1")

        _process_invoice(db, _mock_email_client(), _mock_payment_links(), _make_sme(), _make_invoice())

        call_kwargs = mock_send.call_args[1]
        assert call_kwargs["previous_message_id"] is None


# ---------------------------------------------------------------------------
# process_inbound_reply
# ---------------------------------------------------------------------------


class TestProcessInboundReply:
    """Tests for inbound reply processing and classification routing."""

    @patch(
        "src.main.classify_response",
        return_value=(Classification.PROMISE_TO_PAY, "Will pay Friday"),
    )
    @patch("src.main.handle_classification")
    @patch("src.main.email_alerts")
    def test_promise_to_pay_logs_and_notifies_sme(self, mock_alerts, mock_handle, mock_classify):
        """PROMISE_TO_PAY logs interaction and sends SME notification."""
        mock_handle.return_value = TransitionResult(action="monitor", message="Monitor payment.")
        db = _mock_db()

        process_inbound_reply(
            db,
            _mock_email_client(),
            invoice_id=uuid4(),
            contact_id=uuid4(),
            reply_text="I'll pay on Friday",
        )

        # Interaction logged
        db.create_interaction.assert_called_once()
        interaction = db.create_interaction.call_args[0][0]
        assert interaction.direction == Direction.INBOUND
        assert interaction.classification == Classification.PROMISE_TO_PAY
        assert interaction.content == "I'll pay on Friday"

        # SME notified
        mock_alerts.alert_promise_to_pay.assert_called_once()

    @patch(
        "src.main.classify_response",
        return_value=(Classification.DISPUTE, "Disputing deliverables"),
    )
    @patch("src.main.handle_classification")
    @patch("src.main.email_alerts")
    @patch("src.main.slack_webhook")
    @patch("src.main._send_notifications")
    def test_dispute_pauses_and_notifies_all_channels(
        self, mock_notify, mock_slack, mock_email, mock_handle, mock_classify
    ):
        """DISPUTE triggers pause, Slack alert, and email alert."""
        mock_handle.return_value = TransitionResult(
            action="pause",
            new_phase=InvoicePhase.DISPUTED,
            message="DISPUTED",
        )
        db = _mock_db()

        process_inbound_reply(
            db,
            _mock_email_client(),
            invoice_id=uuid4(),
            contact_id=uuid4(),
            reply_text="We dispute this invoice",
        )

        mock_notify.assert_called_once()
        mock_slack.notify_dispute.assert_called_once()
        mock_email.alert_dispute.assert_called_once()

    @patch(
        "src.main.classify_response",
        return_value=(Classification.HOSTILE, "Threatening language"),
    )
    @patch("src.main.handle_classification")
    @patch("src.main.email_alerts")
    @patch("src.main.slack_webhook")
    @patch("src.main._send_notifications")
    def test_hostile_pauses_and_notifies_all_channels(
        self, mock_notify, mock_slack, mock_email, mock_handle, mock_classify
    ):
        """HOSTILE triggers pause, Slack alert, and email alert."""
        mock_handle.return_value = TransitionResult(
            action="pause",
            new_phase=InvoicePhase.HUMAN_REVIEW,
            message="HOSTILE",
        )
        db = _mock_db()

        process_inbound_reply(
            db,
            _mock_email_client(),
            invoice_id=uuid4(),
            contact_id=uuid4(),
            reply_text="Do not contact us again!",
        )

        mock_notify.assert_called_once()
        mock_slack.notify_hostile.assert_called_once()
        mock_email.alert_hostile.assert_called_once()

    @patch("src.main.classify_response", return_value=(Classification.REDIRECT, "Talk to accounts"))
    @patch("src.main.handle_classification")
    def test_redirect_logs_interaction(self, mock_handle, mock_classify):
        """REDIRECT logs the interaction (new contact extraction is TODO)."""
        mock_handle.return_value = TransitionResult(
            action="redirect",
            message="Redirect to new contact.",
        )
        db = _mock_db()

        process_inbound_reply(
            db,
            _mock_email_client(),
            invoice_id=uuid4(),
            contact_id=uuid4(),
            reply_text="Please contact accounts@widgetcorp.com",
        )

        db.create_interaction.assert_called_once()

    @patch("src.main.classify_response", return_value=(Classification.STALL, "Vague response"))
    @patch("src.main.handle_classification")
    def test_stall_logs_interaction_no_special_notification(self, mock_handle, mock_classify):
        """STALL logs interaction but doesn't trigger special notifications."""
        mock_handle.return_value = TransitionResult(
            action="send_message",
            accelerated=True,
            message="Continue.",
        )
        db = _mock_db()

        process_inbound_reply(
            db,
            _mock_email_client(),
            invoice_id=uuid4(),
            contact_id=uuid4(),
            reply_text="We're looking into it",
        )

        db.create_interaction.assert_called_once()
        interaction = db.create_interaction.call_args[0][0]
        assert interaction.classification == Classification.STALL

    @patch("src.main.classify_response")
    def test_missing_invoice_returns_early(self, mock_classify):
        """If the invoice doesn't exist, return without processing."""
        db = _mock_db(get_invoice=None)

        process_inbound_reply(
            db,
            _mock_email_client(),
            invoice_id=uuid4(),
            contact_id=uuid4(),
            reply_text="Hello",
        )

        mock_classify.assert_not_called()
        db.create_interaction.assert_not_called()

    @patch("src.main.classify_response", return_value=(Classification.PROMISE_TO_PAY, "Will pay"))
    @patch("src.main.handle_classification")
    def test_missing_sme_returns_after_logging(self, mock_handle, mock_classify):
        """If SME not found, interaction is logged but notifications skipped."""
        mock_handle.return_value = TransitionResult(action="monitor", message="Monitor.")
        db = _mock_db(get_sme=None)

        process_inbound_reply(
            db,
            _mock_email_client(),
            invoice_id=uuid4(),
            contact_id=uuid4(),
            reply_text="I'll pay soon",
        )

        # Interaction still logged
        db.create_interaction.assert_called_once()


# ---------------------------------------------------------------------------
# _get_phase_start_date
# ---------------------------------------------------------------------------


class TestGetPhaseStartDate:
    """Tests for phase start date determination."""

    def test_returns_first_outbound_in_phase(self):
        """Returns the sent_at date of the first outbound in the current phase."""
        interactions = [
            _make_outbound_interaction(phase="1", days_ago=5),
            _make_outbound_interaction(phase="1", days_ago=3),
        ]

        result = _get_phase_start_date(interactions, InvoicePhase.PHASE_1, _make_invoice())

        expected = (_NOW - timedelta(days=5)).date()
        assert result == expected

    def test_ignores_interactions_from_other_phases(self):
        """Only considers interactions matching the current phase."""
        interactions = [
            _make_outbound_interaction(phase="1", days_ago=10),
            _make_outbound_interaction(phase="2", days_ago=5),
        ]
        invoice = _make_invoice(created_at=(_NOW - timedelta(days=6)).isoformat())

        result = _get_phase_start_date(interactions, InvoicePhase.PHASE_2, invoice)

        expected = (_NOW - timedelta(days=5)).date()
        assert result == expected

    def test_falls_back_to_invoice_created_at(self):
        """If no interactions in the current phase, uses invoice creation date."""
        created = _NOW - timedelta(days=12)
        invoice = _make_invoice(created_at=created.isoformat())

        result = _get_phase_start_date([], InvoicePhase.PHASE_1, invoice)

        assert result == created.date()

    def test_ignores_inbound_interactions(self):
        """Inbound interactions don't count as phase start."""
        inbound = _make_outbound_interaction(phase="1", days_ago=5)
        inbound["direction"] = Direction.INBOUND.value
        created = _NOW - timedelta(days=8)
        invoice = _make_invoice(created_at=created.isoformat())

        result = _get_phase_start_date([inbound], InvoicePhase.PHASE_1, invoice)

        # Should fall back to created_at since inbound doesn't count
        assert result == created.date()


# ---------------------------------------------------------------------------
# Payment link caching
# ---------------------------------------------------------------------------


class TestPaymentLinkCaching:
    """Tests for payment link caching on invoice records."""

    @patch("src.main.settings")
    @patch("src.main.generate_message")
    @patch("src.main.send_collection_email")
    @patch("src.main.schedule_next_send")
    def test_existing_payment_link_is_reused(self, mock_schedule, mock_send, mock_gen, mock_settings):
        """If invoice already has a payment_link_url, no Stripe call is made."""
        mock_settings.stripe_secret_key = "sk_test_fake"
        mock_settings.agent_default_name = "Alex"
        mock_schedule.return_value = _NOW - timedelta(hours=1)
        mock_gen.return_value = GeneratedMessage(subject="Hi", body="Pay here")
        mock_send.return_value = EmailResult(success=True, message_id="msg-cache")

        invoice = _make_invoice(payment_link_url="https://pay.stripe.com/cached_link")
        db = _mock_db()
        payment_links = _mock_payment_links()

        result = _process_invoice(db, _mock_email_client(), payment_links, _make_sme(), invoice)

        assert result is True
        # Stripe should NOT have been called
        payment_links.create_invoice_payment_link.assert_not_called()
        # The cached URL should be passed to message context
        ctx = mock_gen.call_args[0][0]
        assert ctx.payment_link_url == "https://pay.stripe.com/cached_link"

    @patch("src.main.settings")
    @patch("src.main.generate_message")
    @patch("src.main.send_collection_email")
    @patch("src.main.schedule_next_send")
    def test_null_payment_link_triggers_creation_and_storage(
        self, mock_schedule, mock_send, mock_gen, mock_settings
    ):
        """If invoice has no payment_link_url, Stripe is called and result is persisted."""
        mock_settings.stripe_secret_key = "sk_test_fake"
        mock_settings.agent_default_name = "Alex"
        mock_schedule.return_value = _NOW - timedelta(hours=1)
        mock_gen.return_value = GeneratedMessage(subject="Hi", body="Pay here")
        mock_send.return_value = EmailResult(success=True, message_id="msg-new")

        invoice = _make_invoice()  # no payment_link_url
        db = _mock_db()
        payment_links = _mock_payment_links()

        result = _process_invoice(db, _mock_email_client(), payment_links, _make_sme(), invoice)

        assert result is True
        payment_links.create_invoice_payment_link.assert_called_once()
        # Verify the link was persisted to DB (first call is payment link, second is first_contacted_at)
        assert db.update_invoice.call_count >= 1
        first_update = db.update_invoice.call_args_list[0]
        assert first_update[0][1]["payment_link_url"] == "https://pay.stripe.com/test_link"
        assert first_update[0][1]["payment_link_id"] == "plink_test"
