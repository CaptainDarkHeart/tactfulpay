"""Tests for Codat and Stripe webhook handlers."""

import hashlib
import hmac
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from src.dashboard.app import app
from src.db.models import FeeType, InvoicePhase, InvoiceStatus

client = TestClient(app, headers={"Authorization": "Basic dXNlcjp0ZXN0"})

CODAT_TEST_SECRET = "test-codat-secret"


def _codat_signature(body: bytes, secret: str = CODAT_TEST_SECRET) -> str:
    """Compute the HMAC signature the route expects, matching
    src.sentry.webhook_handler._verify_codat_signature."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post_codat_webhook(payload: dict, secret: str = CODAT_TEST_SECRET):
    """POST to /webhooks/codat with a real HMAC signature over the exact body
    bytes sent. Uses content= rather than json= so the bytes we sign are the
    bytes the server verifies, not a re-serialisation of the same dict."""
    body = json.dumps(payload).encode()
    return client.post(
        "/webhooks/codat",
        content=body,
        headers={
            "X-Codat-Signature": _codat_signature(body, secret),
            "Content-Type": "application/json",
        },
    )


class TestCodatWebhook:
    @patch("src.sentry.webhook_handler.settings")
    @patch("src.sentry.webhook_handler.Database")
    def test_codat_webhook_accepts_valid_payload(self, mock_db_cls, mock_settings):
        mock_settings.codat_webhook_secret = CODAT_TEST_SECRET
        mock_settings.supabase_url = "https://fake.supabase.co"
        mock_db = MagicMock()
        mock_db.try_mark_event_processed.return_value = True
        mock_db_cls.return_value = mock_db
        resp = _post_codat_webhook(
            {
                "AlertType": "DataSyncCompleted",
                "CompanyId": "comp-123",
                "DataType": "customers",
            }
        )
        assert resp.status_code == 200
        assert resp.json() == {"received": True}

    @patch("src.sentry.webhook_handler.settings")
    @patch("src.sentry.webhook_handler.Database")
    def test_codat_webhook_unknown_type(self, mock_db_cls, mock_settings):
        mock_settings.codat_webhook_secret = CODAT_TEST_SECRET
        mock_settings.supabase_url = "https://fake.supabase.co"
        mock_db = MagicMock()
        mock_db.try_mark_event_processed.return_value = True
        mock_db_cls.return_value = mock_db
        resp = _post_codat_webhook(
            {
                "AlertType": "SomethingElse",
                "CompanyId": "comp-123",
            }
        )
        assert resp.status_code == 200

    @patch("src.sentry.webhook_handler.settings")
    @patch("src.sentry.webhook_handler.Database")
    @patch("src.sentry.invoice_sync.run_invoice_sync")
    def test_codat_sync_complete_triggers_sync(self, mock_sync, mock_db_cls, mock_settings):
        mock_settings.codat_webhook_secret = CODAT_TEST_SECRET
        mock_settings.supabase_url = "https://fake.supabase.co"
        mock_db = MagicMock()
        mock_db.try_mark_event_processed.return_value = True
        mock_db_cls.return_value = mock_db
        resp = _post_codat_webhook(
            {
                "AlertType": "invoices.dataSync.completed",
                "CompanyId": "comp-123",
                "DataType": "invoices",
            }
        )
        assert resp.status_code == 200
        mock_sync.assert_called_once()


class TestStripeWebhook:
    @patch("src.sentry.webhook_handler.StripeBilling")
    def test_stripe_webhook_invalid_signature(self, mock_billing_cls):
        import stripe

        mock_billing = MagicMock()
        mock_billing.verify_webhook_signature.side_effect = stripe.SignatureVerificationError(
            "bad sig", "sig_header"
        )
        mock_billing_cls.return_value = mock_billing

        resp = client.post(
            "/webhooks/stripe",
            content=b'{"type": "checkout.session.completed"}',
            headers={
                "Stripe-Signature": "bad_sig",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 401


class TestDebtorPayment:
    """Tests for _handle_debtor_payment via the Stripe webhook."""

    @patch("src.sentry.write_back.write_back_payment")
    @patch("src.sentry.webhook_handler.slack_webhook")
    @patch("src.sentry.webhook_handler.Database")
    @patch.dict("os.environ", {"SUPABASE_URL": "https://fake.supabase.co"})
    def test_debtor_payment_marks_invoice_paid_percentage_fee(
        self, mock_db_cls, mock_slack, mock_write_back
    ):
        """A debtor payment over the threshold should create a percentage fee."""
        from src.sentry.webhook_handler import _handle_debtor_payment

        invoice_id = uuid4()
        sme_id = uuid4()

        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        mock_db.get_invoice.return_value = {
            "id": str(invoice_id),
            "sme_id": str(sme_id),
            "invoice_number": "INV-100",
            "amount": "7500.00",
            "debtor_company": "Big Corp",
            "due_date": (date.today() - timedelta(days=10)).isoformat(),
        }

        event = {
            "data": {
                "object": {
                    "amount_total": 750000,  # 7500.00 GBP in pence
                    "metadata": {
                        "payment_type": "debtor_payment",
                        "invoice_id": str(invoice_id),
                        "invoice_number": "INV-100",
                        "debtor_company": "Big Corp",
                    },
                }
            }
        }

        _handle_debtor_payment(event)

        # Invoice should be marked PAID + RESOLVED
        update_call = mock_db.update_invoice.call_args
        assert update_call[0][0] == invoice_id
        updates = update_call[0][1]
        assert updates["status"] == InvoiceStatus.PAID
        assert updates["current_phase"] == InvoicePhase.RESOLVED
        assert "resolved_at" in updates

        # Fee should be 10% of 7500 = 750
        fee_call = mock_db.create_fee.call_args[0][0]
        assert fee_call.fee_type == FeeType.PERCENTAGE
        assert fee_call.fee_amount == Decimal("750.0")
        assert fee_call.invoice_amount_recovered == Decimal("7500.00")
        assert fee_call.sme_id == sme_id

        # Slack notification sent
        mock_slack.send_alert.assert_called_once()

    @patch("src.sentry.write_back.write_back_payment")
    @patch("src.sentry.webhook_handler.slack_webhook")
    @patch("src.sentry.webhook_handler.Database")
    @patch.dict("os.environ", {"SUPABASE_URL": "https://fake.supabase.co"})
    def test_debtor_payment_flat_fee_for_small_invoice(
        self, mock_db_cls, mock_slack, mock_write_back
    ):
        """A debtor payment at or below the threshold should use the flat fee."""
        from src.sentry.webhook_handler import _handle_debtor_payment

        invoice_id = uuid4()
        sme_id = uuid4()

        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        mock_db.get_invoice.return_value = {
            "id": str(invoice_id),
            "sme_id": str(sme_id),
            "invoice_number": "INV-200",
            "amount": "3000.00",
            "debtor_company": "Small Co",
            "due_date": (date.today() - timedelta(days=10)).isoformat(),
        }

        event = {
            "data": {
                "object": {
                    "amount_total": 300000,  # 3000.00 GBP
                    "metadata": {
                        "payment_type": "debtor_payment",
                        "invoice_id": str(invoice_id),
                        "invoice_number": "INV-200",
                        "debtor_company": "Small Co",
                    },
                }
            }
        }

        _handle_debtor_payment(event)

        fee_call = mock_db.create_fee.call_args[0][0]
        assert fee_call.fee_type == FeeType.FLAT
        assert fee_call.fee_amount == Decimal("500.0")

    @patch("src.sentry.webhook_handler.slack_webhook")
    def test_debtor_payment_missing_invoice_id_does_nothing(self, mock_slack):
        """If invoice_id is missing from metadata, nothing should happen."""
        from src.sentry.webhook_handler import _handle_debtor_payment

        event = {
            "data": {
                "object": {
                    "amount_total": 100000,
                    "metadata": {
                        "payment_type": "debtor_payment",
                        # no invoice_id
                    },
                }
            }
        }

        _handle_debtor_payment(event)

        mock_slack.send_alert.assert_not_called()

    @patch("src.sentry.webhook_handler.slack_webhook")
    def test_debtor_payment_demo_mode_skips_db(self, mock_slack):
        """In DEMO_MODE (no SUPABASE_URL), the handler should skip DB ops."""
        from src.sentry.webhook_handler import _handle_debtor_payment

        invoice_id = uuid4()

        event = {
            "data": {
                "object": {
                    "amount_total": 500000,
                    "metadata": {
                        "payment_type": "debtor_payment",
                        "invoice_id": str(invoice_id),
                        "invoice_number": "INV-DEMO",
                        "debtor_company": "Demo Co",
                    },
                }
            }
        }

        # With no SUPABASE_URL, DEMO_MODE should be True
        with patch.dict("os.environ", {"SUPABASE_URL": ""}, clear=False):
            _handle_debtor_payment(event)

        # In demo mode, no slack alert should fire (returns before that)
        mock_slack.send_alert.assert_not_called()

    @patch("src.sentry.write_back.write_back_payment")
    @patch("src.sentry.webhook_handler.slack_webhook")
    @patch("src.sentry.webhook_handler.Database")
    @patch.dict("os.environ", {"SUPABASE_URL": "https://fake.supabase.co"})
    def test_debtor_payment_calls_write_back(
        self, mock_db_cls, mock_slack, mock_write_back
    ):
        """After recording the debtor payment, write_back_payment should be called."""
        from src.sentry.webhook_handler import _handle_debtor_payment

        invoice_id = uuid4()
        sme_id = uuid4()

        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        mock_db.get_invoice.return_value = {
            "id": str(invoice_id),
            "sme_id": str(sme_id),
            "invoice_number": "INV-WB",
            "amount": "6000.00",
            "debtor_company": "Writeback Ltd",
            "due_date": (date.today() - timedelta(days=10)).isoformat(),
        }

        event = {
            "data": {
                "object": {
                    "amount_total": 600000,
                    "metadata": {
                        "payment_type": "debtor_payment",
                        "invoice_id": str(invoice_id),
                        "invoice_number": "INV-WB",
                        "debtor_company": "Writeback Ltd",
                    },
                }
            }
        }

        with patch(
            "src.sentry.write_back.write_back_payment"
        ) as mock_wb:
            _handle_debtor_payment(event)
            mock_wb.assert_called_once_with(mock_db, invoice_id)


class TestWebhookIdempotency:
    """Tests for webhook deduplication."""

    @patch("src.sentry.webhook_handler.settings")
    @patch("src.sentry.webhook_handler.Database")
    def test_codat_duplicate_event_ignored(self, mock_db_cls, mock_settings):
        """A duplicate Codat webhook returns duplicate=True and is not reprocessed."""
        mock_settings.supabase_url = "https://fake.supabase.co"
        mock_settings.codat_webhook_secret = CODAT_TEST_SECRET

        mock_db = MagicMock()
        # try_mark_event_processed returns False when the insert hit a
        # conflict, i.e. this event id was already recorded (duplicate).
        mock_db.try_mark_event_processed.return_value = False
        mock_db_cls.return_value = mock_db

        resp = _post_codat_webhook(
            {
                "AlertId": "alert-abc-123",
                "AlertType": "DataSyncCompleted",
                "CompanyId": "comp-123",
                "DataType": "invoices",
            }
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["duplicate"] is True
        mock_db.try_mark_event_processed.assert_called_once_with(
            "alert-abc-123", "codat", "DataSyncCompleted"
        )

    @patch("src.sentry.webhook_handler.settings")
    @patch("src.sentry.webhook_handler.Database")
    def test_codat_new_event_processed_and_recorded(self, mock_db_cls, mock_settings):
        """A new Codat webhook is processed and marked as such."""
        mock_settings.supabase_url = "https://fake.supabase.co"
        mock_settings.codat_webhook_secret = CODAT_TEST_SECRET

        mock_db = MagicMock()
        # True — the insert succeeded, this is a new event.
        mock_db.try_mark_event_processed.return_value = True
        mock_db_cls.return_value = mock_db

        resp = _post_codat_webhook(
            {
                "AlertId": "alert-new-456",
                "AlertType": "SomethingElse",
                "CompanyId": "comp-789",
            }
        )
        assert resp.status_code == 200
        assert resp.json() == {"received": True}
        mock_db.try_mark_event_processed.assert_called_once_with(
            "alert-new-456", "codat", "SomethingElse"
        )

    @patch("src.sentry.webhook_handler.StripeBilling")
    @patch("src.sentry.webhook_handler.settings")
    @patch("src.sentry.webhook_handler.Database")
    def test_stripe_duplicate_event_ignored(self, mock_db_cls, mock_settings, mock_billing_cls):
        """A duplicate Stripe webhook returns duplicate=True."""
        mock_settings.supabase_url = "https://fake.supabase.co"

        mock_billing = MagicMock()
        mock_billing.verify_webhook_signature.return_value = {
            "id": "evt_duplicate_123",
            "type": "checkout.session.completed",
        }
        mock_billing_cls.return_value = mock_billing

        mock_db = MagicMock()
        # False — the insert hit a conflict, this event id was already recorded.
        mock_db.try_mark_event_processed.return_value = False
        mock_db_cls.return_value = mock_db

        resp = client.post(
            "/webhooks/stripe",
            content=b'{"type": "checkout.session.completed"}',
            headers={
                "Stripe-Signature": "valid_sig",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["duplicate"] is True
        mock_db.try_mark_event_processed.assert_called_once_with(
            "evt_duplicate_123", "stripe", "checkout.session.completed"
        )
