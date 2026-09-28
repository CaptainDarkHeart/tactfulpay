"""Tests for payment attribution, fee creation on external payments, and anti-exploit measures.

Covers:
- Fee creation when invoices are paid externally (Codat + OAuth)
- Fee calculated on original invoice amount (partial payment protection)
- first_contacted_at attribution tracking
- Disconnect alerts when SME removes accounting integration
- OAuth payment detection for Xero and QuickBooks
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.db.models import (
    ConnectionStatus,
    FeeType,
    InvoicePhase,
    InvoiceStatus,
)
from src.sentry.codat_client import CodatInvoice
from src.sentry.invoice_sync import (
    _check_disconnects,
    check_paid_externally_oauth,
    run_invoice_sync,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sme(codat_id=None, platform="xero"):
    return {
        "id": str(uuid4()),
        "company_name": "Test SME",
        "contact_email": "sme@example.com",
        "codat_company_id": codat_id,
        "accounting_platform": platform,
        "status": "active",
    }


def _make_invoice_dict(
    invoice_id=None,
    sme_id=None,
    number="INV-001",
    amount="7500.00",
    first_contacted_at=None,
    external_id=None,
    due_date=None,
):
    return {
        "id": str(invoice_id or uuid4()),
        "sme_id": str(sme_id or uuid4()),
        "invoice_number": number,
        "debtor_company": "Debtor Co",
        "amount": amount,
        "currency": "GBP",
        "status": "active",
        "current_phase": "1",
        "first_contacted_at": first_contacted_at,
        "external_id": external_id,
        # Well under the 60-day stalled-invoice flat fee threshold by default,
        # so callers testing the percentage-vs-flat-by-amount split don't
        # accidentally trip the flat-by-days-overdue rule too.
        "due_date": due_date or (date.today() - timedelta(days=10)).isoformat(),
    }


def _make_codat_invoice(number="INV-001", amount=5000.0, status="Submitted", paid_on_date=None):
    return CodatInvoice(
        codat_invoice_id=f"codat-{number}",
        invoice_number=number,
        customer_name="Debtor Co",
        customer_email="ap@debtor.com",
        contact_name="Jane Smith",
        amount_due=amount,
        total_amount=amount,
        currency="GBP",
        issue_date="2025-01-01",
        due_date="2025-01-15",
        status=status,
        paid_on_date=paid_on_date,
    )


def _make_connection(platform="xero", status="active", expired=False):
    if expired:
        expires_at = datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(hours=1)
    else:
        expires_at = datetime.now(tz=UTC).replace(tzinfo=None) + timedelta(hours=1)

    return {
        "id": str(uuid4()),
        "sme_id": str(uuid4()),
        "platform": platform,
        "access_token": "enc-access",
        "refresh_token": "enc-refresh",
        "token_expires_at": expires_at,
        "tenant_id": "tenant-123",
        "status": status,
    }


# ---------------------------------------------------------------------------
# Fee creation on external payment (Codat)
# ---------------------------------------------------------------------------


class TestExternalPaymentFeeCreation:
    """When Codat detects an invoice was paid externally, a fee should be
    created if we contacted the debtor."""

    @patch("src.sentry.invoice_sync.slack_webhook")
    def test_codat_paid_externally_creates_fee_when_contacted(self, mock_slack):
        """If first_contacted_at is set, detecting external payment should create a fee."""
        db = MagicMock()
        codat = MagicMock()

        sme = _make_sme(codat_id="codat-comp-1", platform="xero")
        sme_id = sme["id"]
        db.list_active_smes.return_value = [sme]

        invoice_id = uuid4()
        existing = [
            _make_invoice_dict(
                invoice_id=invoice_id,
                sme_id=sme_id,
                number="INV-001",
                amount="7500.00",
                first_contacted_at="2026-03-15T10:00:00",
            ),
        ]
        db.list_active_invoices.return_value = existing
        db.get_fee_by_invoice.return_value = None  # no existing fee

        codat.get_overdue_invoices.return_value = []
        paid = _make_codat_invoice("INV-001", 7500, status="Paid", paid_on_date="2026-03-25")
        codat.get_invoices.return_value = [paid]

        run_invoice_sync(db=db, codat=codat)

        # Invoice should be resolved
        update_call = db.update_invoice.call_args
        assert update_call[0][1]["status"] == InvoiceStatus.PAID
        assert update_call[0][1]["current_phase"] == InvoicePhase.RESOLVED

        # Fee should be created (10% of 7500 = 750)
        db.create_fee.assert_called_once()
        fee = db.create_fee.call_args[0][0]
        assert fee.fee_type == FeeType.PERCENTAGE
        assert fee.fee_amount == Decimal("750.00")

    @patch("src.sentry.invoice_sync.slack_webhook")
    def test_codat_paid_externally_no_fee_when_not_contacted(self, mock_slack):
        """If first_contacted_at is None, no fee should be created."""
        db = MagicMock()
        codat = MagicMock()

        sme = _make_sme(codat_id="codat-comp-1", platform="xero")
        db.list_active_smes.return_value = [sme]

        existing = [
            _make_invoice_dict(number="INV-002", first_contacted_at=None),
        ]
        db.list_active_invoices.return_value = existing

        codat.get_overdue_invoices.return_value = []
        paid = _make_codat_invoice("INV-002", 3000, status="Paid")
        codat.get_invoices.return_value = [paid]

        run_invoice_sync(db=db, codat=codat)

        # Invoice resolved but no fee
        db.update_invoice.assert_called_once()
        db.create_fee.assert_not_called()

    @patch("src.sentry.invoice_sync.slack_webhook")
    def test_no_duplicate_fee_on_re_sync(self, mock_slack):
        """If a fee already exists for the invoice, don't create another."""
        db = MagicMock()
        codat = MagicMock()

        sme = _make_sme(codat_id="codat-comp-1", platform="xero")
        db.list_active_smes.return_value = [sme]

        existing = [
            _make_invoice_dict(
                number="INV-003",
                first_contacted_at="2026-03-10T10:00:00",
            ),
        ]
        db.list_active_invoices.return_value = existing
        db.get_fee_by_invoice.return_value = {"id": str(uuid4())}  # fee already exists

        codat.get_overdue_invoices.return_value = []
        paid = _make_codat_invoice("INV-003", 6000, status="Paid")
        codat.get_invoices.return_value = [paid]

        run_invoice_sync(db=db, codat=codat)

        db.create_fee.assert_not_called()


# ---------------------------------------------------------------------------
# Partial payment protection
# ---------------------------------------------------------------------------


class TestPartialPaymentProtection:
    """Fee must be calculated on original invoice amount, not Stripe payment."""

    @patch("src.sentry.write_back.write_back_payment")
    @patch("src.sentry.webhook_handler.slack_webhook")
    @patch("src.sentry.webhook_handler.Database")
    @patch.dict("os.environ", {"SUPABASE_URL": "https://fake.supabase.co"})
    def test_fee_based_on_original_amount_not_stripe_amount(
        self, mock_db_cls, mock_slack, mock_write_back
    ):
        """Debtor pays £4999 via Stripe on a £50,000 invoice.
        Fee should be 10% of £50,000 = £5,000, not £500 flat."""
        from src.sentry.webhook_handler import _handle_debtor_payment

        invoice_id = uuid4()
        sme_id = uuid4()

        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        mock_db.get_invoice.return_value = {
            "id": str(invoice_id),
            "sme_id": str(sme_id),
            "invoice_number": "INV-EXPLOIT",
            "amount": "50000.00",  # original invoice amount
            "debtor_company": "Exploit Corp",
            "due_date": (date.today() - timedelta(days=10)).isoformat(),
        }

        event = {
            "data": {
                "object": {
                    "amount_total": 499900,  # £4999 — just under threshold
                    "metadata": {
                        "payment_type": "debtor_payment",
                        "invoice_id": str(invoice_id),
                        "invoice_number": "INV-EXPLOIT",
                        "debtor_company": "Exploit Corp",
                    },
                }
            }
        }

        _handle_debtor_payment(event)

        fee = mock_db.create_fee.call_args[0][0]
        # Should be percentage based on £50,000 original, not flat based on £4,999
        assert fee.fee_type == FeeType.PERCENTAGE
        assert fee.fee_amount == Decimal("5000.0")

    @patch("src.sentry.write_back.write_back_payment")
    @patch("src.sentry.webhook_handler.slack_webhook")
    @patch("src.sentry.webhook_handler.Database")
    @patch.dict("os.environ", {"SUPABASE_URL": "https://fake.supabase.co"})
    def test_small_original_invoice_still_gets_flat_fee(
        self, mock_db_cls, mock_slack, mock_write_back
    ):
        """If original invoice is under threshold, flat fee applies."""
        from src.sentry.webhook_handler import _handle_debtor_payment

        invoice_id = uuid4()
        sme_id = uuid4()

        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        mock_db.get_invoice.return_value = {
            "id": str(invoice_id),
            "sme_id": str(sme_id),
            "invoice_number": "INV-SMALL",
            "amount": "3000.00",
            "debtor_company": "Small Co",
            "due_date": (date.today() - timedelta(days=10)).isoformat(),
        }

        event = {
            "data": {
                "object": {
                    "amount_total": 300000,
                    "metadata": {
                        "payment_type": "debtor_payment",
                        "invoice_id": str(invoice_id),
                        "invoice_number": "INV-SMALL",
                        "debtor_company": "Small Co",
                    },
                }
            }
        }

        _handle_debtor_payment(event)

        fee = mock_db.create_fee.call_args[0][0]
        assert fee.fee_type == FeeType.FLAT
        assert fee.fee_amount == Decimal("500.0")


# ---------------------------------------------------------------------------
# OAuth payment detection
# ---------------------------------------------------------------------------


class TestOAuthPaymentDetection:
    """Detect paid invoices via Xero/QuickBooks OAuth connections."""

    @patch("src.sentry.invoice_sync.slack_webhook")
    @patch("src.sentry.invoice_sync.XeroClient")
    @patch("src.sentry.invoice_sync.decrypt_token")
    def test_xero_paid_invoice_detected_and_resolved(
        self, mock_decrypt, mock_xero_cls, mock_slack
    ):
        mock_decrypt.return_value = "decrypted-access"
        mock_client = MagicMock()
        mock_client.get_invoice_status.return_value = "PAID"
        mock_xero_cls.return_value = mock_client

        db = MagicMock()
        sme_id = uuid4()
        invoice = _make_invoice_dict(
            sme_id=sme_id,
            external_id="xero-inv-123",
            first_contacted_at="2026-03-15T10:00:00",
        )
        db.list_active_invoices.return_value = [invoice]
        db.get_fee_by_invoice.return_value = None

        conn = _make_connection(platform="xero")

        paid_count = check_paid_externally_oauth(db, sme_id, conn)

        assert paid_count == 1
        # Invoice should be marked resolved
        update_call = db.update_invoice.call_args
        assert update_call[0][1]["status"] == InvoiceStatus.PAID
        assert update_call[0][1]["current_phase"] == InvoicePhase.RESOLVED
        # Fee should be created
        db.create_fee.assert_called_once()

    @patch("src.sentry.invoice_sync.slack_webhook")
    @patch("src.sentry.invoice_sync.QuickBooksClient")
    @patch("src.sentry.invoice_sync.decrypt_token")
    @patch("src.sentry.invoice_sync.settings")
    def test_quickbooks_paid_invoice_detected(
        self, mock_settings, mock_decrypt, mock_qb_cls, mock_slack
    ):
        mock_settings.quickbooks_sandbox = True
        mock_decrypt.return_value = "decrypted-access"
        mock_client = MagicMock()
        mock_client.get_invoice_status.return_value = ("123", True)  # (id, is_paid)
        mock_qb_cls.return_value = mock_client

        db = MagicMock()
        sme_id = uuid4()
        invoice = _make_invoice_dict(
            sme_id=sme_id,
            external_id="qb-inv-456",
            first_contacted_at="2026-03-10T10:00:00",
        )
        db.list_active_invoices.return_value = [invoice]
        db.get_fee_by_invoice.return_value = None

        conn = _make_connection(platform="quickbooks")

        paid_count = check_paid_externally_oauth(db, sme_id, conn)

        assert paid_count == 1
        db.update_invoice.assert_called_once()
        db.create_fee.assert_called_once()

    @patch("src.sentry.invoice_sync.decrypt_token")
    def test_skips_invoices_without_external_id(self, mock_decrypt):
        mock_decrypt.return_value = "decrypted-access"
        db = MagicMock()
        sme_id = uuid4()
        # Invoice has no external_id — can't check platform
        invoice = _make_invoice_dict(sme_id=sme_id, external_id=None)
        db.list_active_invoices.return_value = [invoice]

        conn = _make_connection(platform="xero")

        paid_count = check_paid_externally_oauth(db, sme_id, conn)

        assert paid_count == 0

    @patch("src.sentry.invoice_sync.decrypt_token")
    def test_skips_when_token_expired(self, mock_decrypt):
        db = MagicMock()
        sme_id = uuid4()
        invoice = _make_invoice_dict(sme_id=sme_id, external_id="xero-123")
        db.list_active_invoices.return_value = [invoice]

        conn = _make_connection(platform="xero", expired=True)

        paid_count = check_paid_externally_oauth(db, sme_id, conn)

        assert paid_count == 0
        mock_decrypt.assert_not_called()

    @patch("src.sentry.invoice_sync.slack_webhook")
    @patch("src.sentry.invoice_sync.XeroClient")
    @patch("src.sentry.invoice_sync.decrypt_token")
    def test_xero_unpaid_invoice_not_resolved(
        self, mock_decrypt, mock_xero_cls, mock_slack
    ):
        mock_decrypt.return_value = "decrypted-access"
        mock_client = MagicMock()
        mock_client.get_invoice_status.return_value = "AUTHORISED"  # still unpaid
        mock_xero_cls.return_value = mock_client

        db = MagicMock()
        sme_id = uuid4()
        invoice = _make_invoice_dict(sme_id=sme_id, external_id="xero-789")
        db.list_active_invoices.return_value = [invoice]

        conn = _make_connection(platform="xero")

        paid_count = check_paid_externally_oauth(db, sme_id, conn)

        assert paid_count == 0
        db.update_invoice.assert_not_called()


# ---------------------------------------------------------------------------
# Disconnect alerts
# ---------------------------------------------------------------------------


class TestDisconnectAlerts:
    """Alert when SME disconnects accounting with active contacted invoices."""

    @patch("src.sentry.invoice_sync.slack_webhook")
    def test_alerts_when_no_active_connection_and_contacted_invoices(self, mock_slack):
        db = MagicMock()
        sme_id = uuid4()

        db.list_connections.return_value = [
            {"status": ConnectionStatus.REVOKED.value},
        ]
        db.list_active_invoices.return_value = [
            _make_invoice_dict(
                sme_id=sme_id,
                number="INV-001",
                first_contacted_at="2026-03-15T10:00:00",
            ),
        ]

        _check_disconnects(db, sme_id, "Dodgy Ltd")

        mock_slack.send_alert.assert_called_once()
        call_kwargs = mock_slack.send_alert.call_args[1]
        assert call_kwargs["severity"] == "warning"
        assert "Dodgy Ltd" in call_kwargs["message"]

    @patch("src.sentry.invoice_sync.slack_webhook")
    def test_no_alert_when_active_connection_exists(self, mock_slack):
        db = MagicMock()
        sme_id = uuid4()

        db.list_connections.return_value = [
            {"status": ConnectionStatus.ACTIVE.value},
        ]

        _check_disconnects(db, sme_id, "Good Co")

        mock_slack.send_alert.assert_not_called()

    @patch("src.sentry.invoice_sync.slack_webhook")
    def test_no_alert_when_no_contacted_invoices(self, mock_slack):
        db = MagicMock()
        sme_id = uuid4()

        db.list_connections.return_value = []
        db.list_active_invoices.return_value = [
            _make_invoice_dict(sme_id=sme_id, first_contacted_at=None),
        ]

        _check_disconnects(db, sme_id, "New Co")

        mock_slack.send_alert.assert_not_called()


# ---------------------------------------------------------------------------
# first_contacted_at tracking
# ---------------------------------------------------------------------------


class TestFirstContactedAtTracking:
    """Verify first_contacted_at is set on first outbound send."""

    @patch("src.main.settings")
    @patch("src.main.generate_message")
    @patch("src.main.send_collection_email")
    @patch("src.main.schedule_next_send")
    def test_first_contact_sets_timestamp(
        self, mock_schedule, mock_send, mock_gen, mock_settings
    ):
        from src.executor.email_sender import EmailResult
        from src.main import _process_invoice
        from src.strategist.message_generator import GeneratedMessage

        mock_settings.stripe_secret_key = ""
        mock_settings.agent_default_name = "Alex"
        mock_schedule.return_value = datetime.now(tz=UTC) - timedelta(hours=1)
        mock_gen.return_value = GeneratedMessage(subject="Pay up", body="Please pay")
        mock_send.return_value = EmailResult(success=True, message_id="msg-1")

        invoice_id = uuid4()
        invoice = {
            "id": str(invoice_id),
            "sme_id": str(uuid4()),
            "invoice_number": "INV-FIRST",
            "debtor_company": "First Co",
            "amount": "5000.00",
            "currency": "GBP",
            "due_date": "2026-03-01",
            "current_phase": "1",
            "status": "active",
            "created_at": "2026-03-01T00:00:00",
            "first_contacted_at": None,  # not yet contacted
        }

        db = MagicMock()
        db.get_primary_contact.return_value = {
            "id": str(uuid4()),
            "name": "Jane",
            "email": "jane@debtor.com",
        }
        db.get_latest_outbound.return_value = None
        db.list_interactions.return_value = []
        db.get_email_domain_by_sme.return_value = None

        sme = {
            "id": str(uuid4()),
            "company_name": "Test SME",
            "contact_email": "sme@test.com",
            "discount_authorised": False,
            "max_discount_percent": 0,
        }


        result = _process_invoice(db, MagicMock(), MagicMock(), sme, invoice)

        assert result is True

        # Check that first_contacted_at was set
        update_calls = db.update_invoice.call_args_list
        contact_updates = [
            c for c in update_calls
            if "first_contacted_at" in c[0][1]
        ]
        assert len(contact_updates) == 1
        assert contact_updates[0][0][0] == invoice_id

    @patch("src.main.settings")
    @patch("src.main.generate_message")
    @patch("src.main.send_collection_email")
    @patch("src.main.schedule_next_send")
    def test_second_contact_does_not_overwrite_timestamp(
        self, mock_schedule, mock_send, mock_gen, mock_settings
    ):
        from src.executor.email_sender import EmailResult
        from src.main import _process_invoice
        from src.strategist.message_generator import GeneratedMessage

        mock_settings.stripe_secret_key = ""
        mock_settings.agent_default_name = "Alex"
        mock_schedule.return_value = datetime.now(tz=UTC) - timedelta(hours=1)
        mock_gen.return_value = GeneratedMessage(subject="Follow up", body="Still owed")
        mock_send.return_value = EmailResult(success=True, message_id="msg-2")

        invoice = {
            "id": str(uuid4()),
            "sme_id": str(uuid4()),
            "invoice_number": "INV-SECOND",
            "debtor_company": "Second Co",
            "amount": "5000.00",
            "currency": "GBP",
            "due_date": "2026-03-01",
            "current_phase": "1",
            "status": "active",
            "created_at": "2026-03-01T00:00:00",
            "first_contacted_at": "2026-03-10T10:00:00",  # already set
        }

        db = MagicMock()
        db.get_primary_contact.return_value = {
            "id": str(uuid4()),
            "name": "Jane",
            "email": "jane@debtor.com",
        }
        db.get_latest_outbound.return_value = None
        db.list_interactions.return_value = []
        db.get_email_domain_by_sme.return_value = None

        sme = {
            "id": str(uuid4()),
            "company_name": "Test SME",
            "contact_email": "sme@test.com",
            "discount_authorised": False,
            "max_discount_percent": 0,
        }

        result = _process_invoice(db, MagicMock(), MagicMock(), sme, invoice)

        assert result is True

        # first_contacted_at should NOT be updated again
        update_calls = db.update_invoice.call_args_list
        contact_updates = [
            c for c in update_calls
            if "first_contacted_at" in c[0][1]
        ]
        assert len(contact_updates) == 0
