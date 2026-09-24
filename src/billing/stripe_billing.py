"""Stripe billing for charging recovery fees to SMEs.

When an invoice is successfully collected, we charge the SME our fee:
- 10% for invoices over GBP 5,000
- GBP 500 flat fee otherwise

This module handles:
- Creating Stripe Customers for SMEs (on first charge)
- Creating Checkout Sessions for fee collection
- Processing webhook events for payment confirmation
- Marking fees as charged in the database
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

import stripe

from src.config import settings
from src.utils.retry import with_retry

logger = logging.getLogger(__name__)

CURRENCY_MULTIPLIERS = {
    "GBP": 100,
    "USD": 100,
    "EUR": 100,
}


@dataclass
class ChargeResult:
    """Result of a fee charge attempt."""

    success: bool
    stripe_customer_id: str | None = None
    checkout_url: str | None = None
    payment_intent_id: str | None = None
    error: str | None = None


class StripeBilling:
    """Handles charging recovery fees to SMEs via Stripe."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.stripe_secret_key
        stripe.api_key = self.api_key

    @with_retry(
        max_attempts=3,
        backoff_factor=1.0,
        retryable_exceptions=(stripe.APIConnectionError, stripe.RateLimitError),
    )
    def get_or_create_customer(
        self,
        sme_id: UUID,
        company_name: str,
        email: str,
        existing_stripe_id: str | None = None,
    ) -> str:
        """Get existing or create new Stripe Customer for an SME.

        Returns the Stripe Customer ID.
        """
        if existing_stripe_id:
            try:
                customer = stripe.Customer.retrieve(existing_stripe_id)
                if not customer.get("deleted"):
                    return customer.id
            except stripe.StripeError:
                logger.warning(
                    "Stored Stripe customer %s not found, creating new", existing_stripe_id
                )

        customer = stripe.Customer.create(
            name=company_name,
            email=email,
            metadata={"sme_id": str(sme_id)},
        )
        logger.info("Created Stripe customer %s for SME %s", customer.id, company_name)
        return customer.id

    @with_retry(
        max_attempts=3,
        backoff_factor=1.0,
        retryable_exceptions=(stripe.APIConnectionError, stripe.RateLimitError),
    )
    def create_fee_checkout(
        self,
        sme_id: UUID,
        invoice_id: UUID,
        invoice_number: str,
        fee_amount: Decimal,
        currency: str = "GBP",
        customer_id: str | None = None,
        success_url: str = "https://tactfulpay.app/billing/success",
        cancel_url: str = "https://tactfulpay.app/billing/cancel",
    ) -> ChargeResult:
        """Create a Stripe Checkout Session to collect the recovery fee from the SME.

        Args:
            sme_id: The SME being charged.
            invoice_id: The invoice that was recovered.
            invoice_number: Human-readable invoice number.
            fee_amount: The fee to charge.
            currency: Three-letter currency code.
            customer_id: Stripe Customer ID (if already known).
            success_url: Redirect URL after successful payment.
            cancel_url: Redirect URL if payment is cancelled.

        Returns:
            ChargeResult with the checkout URL or error.
        """
        multiplier = CURRENCY_MULTIPLIERS.get(currency.upper(), 100)
        amount_minor = int(fee_amount * multiplier)

        if amount_minor <= 0:
            return ChargeResult(success=False, error="Fee amount must be positive")

        try:
            session_params: dict = {
                "mode": "payment",
                "line_items": [
                    {
                        "price_data": {
                            "currency": currency.lower(),
                            "unit_amount": amount_minor,
                            "product_data": {
                                "name": f"Recovery fee — Invoice {invoice_number}",
                                "description": (
                                    f"Success fee for recovering payment on invoice "
                                    f"{invoice_number}"
                                ),
                            },
                        },
                        "quantity": 1,
                    }
                ],
                "metadata": {
                    "sme_id": str(sme_id),
                    "invoice_id": str(invoice_id),
                    "invoice_number": invoice_number,
                    "fee_type": "recovery_fee",
                },
                "success_url": success_url,
                "cancel_url": cancel_url,
            }

            if customer_id:
                session_params["customer"] = customer_id

            session = stripe.checkout.Session.create(**session_params)

            logger.info(
                "Created checkout session %s for SME fee on invoice %s (£%.2f)",
                session.id,
                invoice_number,
                fee_amount,
            )

            return ChargeResult(
                success=True,
                stripe_customer_id=customer_id,
                checkout_url=session.url,
            )

        except stripe.StripeError as e:
            logger.error("Stripe error creating fee checkout for %s: %s", invoice_number, e)
            return ChargeResult(success=False, error=str(e))

    def handle_checkout_completed(self, event: dict) -> dict | None:
        """Process a checkout.session.completed webhook event.

        Returns metadata dict if this is one of our fee payments, else None.
        """
        session = event.get("data", {}).get("object", {})
        metadata = session.get("metadata", {})

        if metadata.get("fee_type") != "recovery_fee":
            return None

        payment_intent_id = session.get("payment_intent")
        logger.info(
            "Fee payment completed for invoice %s (PI: %s)",
            metadata.get("invoice_number"),
            payment_intent_id,
        )

        return {
            "sme_id": metadata.get("sme_id"),
            "invoice_id": metadata.get("invoice_id"),
            "invoice_number": metadata.get("invoice_number"),
            "payment_intent_id": payment_intent_id,
            "amount_total": session.get("amount_total"),
            "currency": session.get("currency"),
        }

    def verify_webhook_signature(self, payload: bytes, sig_header: str) -> dict:
        """Verify and parse a Stripe webhook event.

        Raises stripe.SignatureVerificationError if invalid.
        """
        return stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
