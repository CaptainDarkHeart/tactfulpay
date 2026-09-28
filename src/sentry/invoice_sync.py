"""Daily sync job for pulling overdue invoices from connected accounting platforms.

Orchestrates the Sentry brain's main job:
1. For each active SME with a Codat connection, pull overdue invoices
2. For each active SME with a direct OAuth connection (Xero/QuickBooks), pull overdue invoices
3. Upsert new invoices into the database
4. Check if any previously-active invoices have been marked as paid externally
5. Report sync results via Slack/email notifications
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from src.billing.fee_calculator import calculate_fee
from src.config import settings
from src.db.models import (
    AccountingPlatform,
    ConnectionStatus,
    Contact,
    ContactSource,
    Database,
    Invoice,
    InvoicePhase,
    InvoiceStatus,
)
from src.notifications import slack_webhook
from src.sentry.codat_client import CodatClient, CodatInvoice, CodatSyncResult
from src.sentry.normalised_invoice import NormalisedInvoice
from src.sentry.oauth import decrypt_token, encrypt_token, refresh_access_token
from src.sentry.quickbooks_client import QuickBooksClient
from src.sentry.xero_client import XeroClient
from src.strategist.state_machine import initial_phase_for_due_date

logger = logging.getLogger(__name__)

# Map platform enum values to the ContactSource used when creating contacts
_PLATFORM_CONTACT_SOURCE = {
    AccountingPlatform.XERO: ContactSource.XERO_SYNC,
    AccountingPlatform.QUICKBOOKS: ContactSource.QUICKBOOKS_SYNC,
}


# ---------------------------------------------------------------------------
# Existing Codat sync (preserved as-is)
# ---------------------------------------------------------------------------


def run_invoice_sync(
    db: Database | None = None,
    codat: CodatClient | None = None,
) -> CodatSyncResult:
    """Pull overdue invoices from all connected accounting platforms.

    Returns:
        CodatSyncResult with counts and any errors.
    """
    db = db or Database()
    codat = codat or CodatClient()

    result = CodatSyncResult(success=True)

    for sme in db.list_active_smes():
        if not sme.get("codat_company_id"):
            continue  # CSV-only SME, skip

        if AccountingPlatform(sme.get("accounting_platform", "csv")) == AccountingPlatform.CSV:
            continue

        sme_id = UUID(sme["id"])
        company_id = sme["codat_company_id"]
        sme_name = sme["company_name"]

        logger.info("Syncing invoices for %s (Codat: %s)", sme_name, company_id)

        try:
            _sync_sme_invoices(db, codat, sme_id, company_id, sme_name, result)
            result.companies_synced += 1
        except Exception as e:
            error_msg = f"Failed to sync {sme_name}: {e}"
            logger.exception(error_msg)
            result.errors.append(error_msg)

    if result.errors:
        result.success = len(result.errors) < result.companies_synced

    logger.info(
        "Invoice sync complete: %d companies, %d invoices found, %d overdue",
        result.companies_synced,
        result.invoices_found,
        result.overdue_invoices,
    )

    return result


def _sync_sme_invoices(
    db: Database,
    codat: CodatClient,
    sme_id: UUID,
    company_id: str,
    sme_name: str,
    result: CodatSyncResult,
) -> None:
    """Sync invoices for a single SME."""
    overdue = codat.get_overdue_invoices(company_id)
    result.invoices_found += len(overdue)
    result.overdue_invoices += len(overdue)

    # Get existing invoices for this SME to avoid duplicates
    existing = db.list_active_invoices(sme_id=sme_id)
    existing_numbers = {inv["invoice_number"] for inv in existing}

    new_count = 0
    for codat_inv in overdue:
        if codat_inv.invoice_number in existing_numbers:
            continue  # Already tracking this invoice

        if not codat_inv.invoice_number:
            logger.warning("Skipping invoice with no number from %s", sme_name)
            continue

        _create_invoice_from_codat(db, sme_id, codat_inv)
        new_count += 1

    if new_count > 0:
        logger.info("Created %d new invoices for %s", new_count, sme_name)
        slack_webhook.send_alert(
            title="New Overdue Invoices Synced",
            message=f"Synced {new_count} new overdue invoice(s) for {sme_name}",
            severity="info",
        )

    # Check for invoices paid externally
    _check_paid_externally(db, codat, sme_id, company_id, existing)


def _create_invoice_from_codat(
    db: Database,
    sme_id: UUID,
    codat_inv: CodatInvoice,
) -> None:
    """Create an Invoice and Contact from a Codat invoice."""
    due_date = date.fromisoformat(codat_inv.due_date[:10])
    invoice = Invoice(
        sme_id=sme_id,
        invoice_number=codat_inv.invoice_number,
        debtor_company=codat_inv.customer_name,
        amount=Decimal(str(codat_inv.amount_due)),
        currency=codat_inv.currency,
        due_date=due_date,
        current_phase=initial_phase_for_due_date(due_date),
        external_id=codat_inv.codat_invoice_id,
    )
    db.create_invoice(invoice)

    # Create a contact if we have enough info
    contact_name = codat_inv.contact_name or codat_inv.customer_name
    contact_email = codat_inv.customer_email

    if contact_name and contact_email:
        contact = Contact(
            invoice_id=invoice.id,
            name=contact_name,
            email=contact_email,
            source=ContactSource.CODAT_SYNC,
        )
        db.create_contact(contact)
    else:
        logger.warning(
            "Invoice %s has incomplete contact info (name=%r, email=%r) — "
            "will need manual contact entry",
            codat_inv.invoice_number,
            contact_name,
            contact_email,
        )


def _check_paid_externally(
    db: Database,
    codat: CodatClient,
    sme_id: UUID,
    company_id: str,
    existing_invoices: list[dict],
) -> None:
    """Check if any active invoices have been paid in the accounting software.

    When a payment is detected externally, this also creates a Fee record
    if the invoice was contacted by our agent (has first_contacted_at set),
    ensuring we capture revenue regardless of how the debtor paid.
    """
    if not existing_invoices:
        return

    # Pull all invoices (not just overdue) to check paid status
    all_codat = codat.get_invoices(company_id)
    paid_numbers = {
        inv.invoice_number for inv in all_codat if inv.status == "Paid" or inv.paid_on_date
    }

    for inv in existing_invoices:
        if inv["invoice_number"] in paid_numbers:
            invoice_id = UUID(inv["id"])
            logger.info(
                "Invoice %s marked as paid externally — resolving",
                inv["invoice_number"],
            )
            db.update_invoice(
                invoice_id,
                {
                    "status": InvoiceStatus.PAID,
                    "current_phase": InvoicePhase.RESOLVED,
                    "resolved_at": datetime.now(tz=UTC).replace(tzinfo=None),
                },
            )

            # Create fee if we contacted this debtor (attribution)
            _create_fee_if_attributed(db, inv, sme_id, invoice_id)

            slack_webhook.send_alert(
                title="Invoice Paid Externally",
                message=(
                    f"Invoice {inv['invoice_number']} ({inv['debtor_company']}) "
                    f"was paid in the accounting software — resolved"
                ),
                invoice_number=inv["invoice_number"],
                debtor_company=inv["debtor_company"],
                severity="info",
            )


def _create_fee_if_attributed(
    db: Database,
    invoice_data: dict,
    sme_id: UUID,
    invoice_id: UUID,
) -> None:
    """Create a fee record if the invoice was contacted by our agent.

    Attribution rule: if first_contacted_at is set, we contributed to recovery.
    Skips if a fee already exists for this invoice.
    """
    first_contacted = invoice_data.get("first_contacted_at")
    if not first_contacted:
        logger.info(
            "Invoice %s paid externally but never contacted — no fee",
            invoice_data.get("invoice_number", invoice_id),
        )
        return

    # Don't double-create fees
    existing_fee = db.get_fee_by_invoice(invoice_id)
    if existing_fee:
        logger.info(
            "Invoice %s already has a fee record — skipping",
            invoice_data.get("invoice_number", invoice_id),
        )
        return

    # Fee is calculated on the original invoice amount
    original_amount = Decimal(str(invoice_data["amount"]))
    due_date = date.fromisoformat(invoice_data["due_date"])
    days_overdue = (date.today() - due_date).days
    fee = calculate_fee(original_amount, sme_id, invoice_id, days_overdue)
    db.create_fee(fee)

    logger.info(
        "Created %s fee of %s for externally-paid invoice %s",
        fee.fee_type.value,
        fee.fee_amount,
        invoice_data.get("invoice_number", invoice_id),
    )


# ---------------------------------------------------------------------------
# Multi-provider sync (Xero, QuickBooks via direct OAuth connections)
# ---------------------------------------------------------------------------


def sync_from_connection(
    db: Database,
    sme_id: UUID,
    connection: dict,
) -> list[NormalisedInvoice]:
    """Sync overdue invoices from a single OAuth accounting connection.

    Handles token expiry detection and refresh, decrypts tokens, instantiates
    the appropriate platform client, and returns normalised invoices.

    Args:
        db: Database instance.
        sme_id: The SME that owns this connection.
        connection: A dict from the accounting_connections table.

    Returns:
        List of NormalisedInvoice objects fetched from the platform.

    Raises:
        ValueError: If the connection platform is not supported.
    """
    platform = AccountingPlatform(connection["platform"])
    connection_id = UUID(connection["id"])

    # --- Token refresh if expired ---
    expires_at_raw = connection["token_expires_at"]
    if isinstance(expires_at_raw, str):
        token_expires_at = datetime.fromisoformat(expires_at_raw)
    else:
        token_expires_at = expires_at_raw

    # Compare in UTC; strip tzinfo if needed for consistent comparison
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    if token_expires_at.tzinfo is not None:
        token_expires_at = token_expires_at.replace(tzinfo=None)

    if now >= token_expires_at:
        logger.info(
            "Access token expired for connection %s (%s) — refreshing",
            connection_id,
            platform.value,
        )
        decrypted_refresh = decrypt_token(connection["refresh_token"])
        token_response = refresh_access_token(platform, decrypted_refresh)

        new_access = encrypt_token(token_response["access_token"])
        new_refresh = encrypt_token(token_response.get("refresh_token", decrypted_refresh))
        new_expires = datetime.now(tz=UTC).replace(tzinfo=None) + timedelta(
            seconds=token_response.get("expires_in", 1800)
        )

        db.update_connection(
            connection_id,
            access_token=new_access,
            refresh_token=new_refresh,
            token_expires_at=new_expires,
        )

        # Use the freshly obtained token for this request
        access_token = token_response["access_token"]
    else:
        access_token = decrypt_token(connection["access_token"])

    # --- Create platform client and fetch invoices ---
    if platform == AccountingPlatform.XERO:
        tenant_id = connection.get("tenant_id", "")
        client = XeroClient(access_token=access_token, tenant_id=tenant_id)
    elif platform == AccountingPlatform.QUICKBOOKS:
        tenant_id = connection.get("tenant_id", "")
        client = QuickBooksClient(
            access_token=access_token,
            realm_id=tenant_id,
            sandbox=settings.quickbooks_sandbox,
        )
    else:
        raise ValueError(f"Direct sync not supported for platform: {platform.value}")

    invoices = client.get_overdue_invoices()

    # Update last_sync_at
    db.update_connection(
        connection_id,
        last_sync_at=datetime.now(tz=UTC).replace(tzinfo=None),
    )

    logger.info(
        "Fetched %d overdue invoices from %s for SME %s",
        len(invoices),
        platform.value,
        sme_id,
    )
    return invoices


def upsert_normalised_invoices(
    db: Database,
    sme_id: UUID,
    invoices: list[NormalisedInvoice],
) -> int:
    """Upsert normalised invoices into the database, skipping duplicates.

    For each NormalisedInvoice, checks whether an invoice with the same
    invoice_number already exists for this SME. If not, creates the invoice
    and an associated contact (when sufficient contact info is available).

    Args:
        db: Database instance.
        sme_id: The SME that owns these invoices.
        invoices: List of NormalisedInvoice objects from any platform.

    Returns:
        Count of new invoices created.
    """
    existing = db.list_active_invoices(sme_id=sme_id)
    existing_numbers = {inv["invoice_number"] for inv in existing}

    new_count = 0
    for norm_inv in invoices:
        if not norm_inv.invoice_number:
            logger.warning(
                "Skipping normalised invoice with no number (external_id=%s)",
                norm_inv.external_id,
            )
            continue

        if norm_inv.invoice_number in existing_numbers:
            continue  # Already tracking this invoice

        invoice = Invoice(
            sme_id=sme_id,
            invoice_number=norm_inv.invoice_number,
            debtor_company=norm_inv.debtor_company,
            amount=norm_inv.amount_due,
            currency=norm_inv.currency,
            due_date=norm_inv.due_date,
            current_phase=initial_phase_for_due_date(norm_inv.due_date),
            external_id=norm_inv.external_id,
        )
        db.create_invoice(invoice)

        # Create a contact if we have enough info
        contact_name = norm_inv.contact_name or norm_inv.debtor_company
        contact_email = norm_inv.contact_email
        contact_source = _PLATFORM_CONTACT_SOURCE.get(norm_inv.platform, ContactSource.CSV_UPLOAD)

        if contact_name and contact_email:
            contact = Contact(
                invoice_id=invoice.id,
                name=contact_name,
                email=contact_email,
                phone=norm_inv.contact_phone or None,
                source=contact_source,
            )
            db.create_contact(contact)
        else:
            logger.warning(
                "Invoice %s has incomplete contact info (name=%r, email=%r) — "
                "will need manual contact entry",
                norm_inv.invoice_number,
                contact_name,
                contact_email,
            )

        # Track the number so we don't double-create within this batch
        existing_numbers.add(norm_inv.invoice_number)
        new_count += 1

    return new_count


def check_paid_externally_oauth(
    db: Database,
    sme_id: UUID,
    connection: dict,
) -> int:
    """Check if any tracked invoices have been paid via OAuth platform (Xero/QB).

    For each active invoice with an external_id, queries the platform to check
    if it has been paid. If paid, resolves the invoice and creates a fee.

    Returns the number of invoices found to be paid.
    """
    platform = AccountingPlatform(connection["platform"])
    active_invoices = db.list_active_invoices(sme_id=sme_id)

    # Only check invoices that have an external_id (came from this platform)
    trackable = [inv for inv in active_invoices if inv.get("external_id")]
    if not trackable:
        return 0

    # Decrypt token
    expires_at_raw = connection["token_expires_at"]
    if isinstance(expires_at_raw, str):
        token_expires_at = datetime.fromisoformat(expires_at_raw)
    else:
        token_expires_at = expires_at_raw
    if token_expires_at.tzinfo is not None:
        token_expires_at = token_expires_at.replace(tzinfo=None)

    now = datetime.now(tz=UTC).replace(tzinfo=None)
    if now >= token_expires_at:
        # Token expired — skip. The main sync will refresh it next run.
        logger.info("Skipping OAuth paid check — token expired for connection %s", connection["id"])
        return 0

    access_token = decrypt_token(connection["access_token"])
    paid_count = 0

    if platform == AccountingPlatform.XERO:
        tenant_id = connection.get("tenant_id", "")
        client = XeroClient(access_token=access_token, tenant_id=tenant_id)
        for inv in trackable:
            status = client.get_invoice_status(inv["external_id"])
            if status == "PAID":
                _resolve_externally_paid(db, inv, sme_id)
                paid_count += 1

    elif platform == AccountingPlatform.QUICKBOOKS:
        tenant_id = connection.get("tenant_id", "")
        client = QuickBooksClient(
            access_token=access_token,
            realm_id=tenant_id,
            sandbox=settings.quickbooks_sandbox,
        )
        for inv in trackable:
            _, is_paid = client.get_invoice_status(inv["external_id"])
            if is_paid:
                _resolve_externally_paid(db, inv, sme_id)
                paid_count += 1

    if paid_count:
        logger.info(
            "Found %d externally-paid invoices via %s for SME %s",
            paid_count,
            platform.value,
            sme_id,
        )

    return paid_count


def _resolve_externally_paid(db: Database, inv: dict, sme_id: UUID) -> None:
    """Mark an invoice as paid externally and create a fee if attributed."""
    invoice_id = UUID(inv["id"])
    logger.info("Invoice %s paid externally (OAuth) — resolving", inv["invoice_number"])
    db.update_invoice(
        invoice_id,
        {
            "status": InvoiceStatus.PAID,
            "current_phase": InvoicePhase.RESOLVED,
            "resolved_at": datetime.now(tz=UTC).replace(tzinfo=None),
        },
    )
    _create_fee_if_attributed(db, inv, sme_id, invoice_id)
    slack_webhook.send_alert(
        title="Invoice Paid Externally",
        message=(
            f"Invoice {inv['invoice_number']} ({inv['debtor_company']}) "
            f"was paid in the accounting software — resolved"
        ),
        invoice_number=inv["invoice_number"],
        debtor_company=inv.get("debtor_company", ""),
        severity="info",
    )


def _check_disconnects(db: Database, sme_id: UUID, sme_name: str) -> None:
    """Alert if an SME has active invoices but no active accounting connection.

    This catches the exploit where an SME disconnects their integration after
    collection begins, to prevent us from detecting external payment.
    """
    connections = db.list_connections(sme_id)
    has_active_connection = any(
        c.get("status") == ConnectionStatus.ACTIVE.value for c in connections
    )

    if has_active_connection:
        return

    active_invoices = db.list_active_invoices(sme_id=sme_id)
    contacted = [inv for inv in active_invoices if inv.get("first_contacted_at")]

    if contacted:
        logger.warning(
            "SME %s has %d contacted invoices but no active accounting connection",
            sme_name,
            len(contacted),
        )
        invoice_numbers = ", ".join(inv["invoice_number"] for inv in contacted[:5])
        slack_webhook.send_alert(
            title="Accounting Disconnected — Active Invoices",
            message=(
                f"{sme_name} has disconnected their accounting integration "
                f"while {len(contacted)} contacted invoice(s) are still active: "
                f"{invoice_numbers}. Payment detection is impaired."
            ),
            severity="warning",
        )


def run_full_sync(db: Database | None = None) -> dict:
    """Run a complete invoice sync across all providers.

    For each active SME:
    - If the SME has a Codat connection, runs the existing Codat sync.
    - For each active direct OAuth connection (Xero/QuickBooks), fetches
      invoices and upserts them.

    Errors on individual connections are logged and skipped so that one
    failure does not block the rest of the sync.

    Args:
        db: Optional Database instance (created if not provided).

    Returns:
        A summary dict with keys: smes_processed, codat_result,
        connections_synced, invoices_created, errors.
    """
    db = db or Database()

    summary: dict = {
        "smes_processed": 0,
        "codat_result": None,
        "connections_synced": 0,
        "invoices_created": 0,
        "externally_paid": 0,
        "disconnect_warnings": 0,
        "errors": [],
    }

    # --- Run existing Codat sync first ---
    try:
        codat_result = run_invoice_sync(db=db)
        summary["codat_result"] = {
            "success": codat_result.success,
            "companies_synced": codat_result.companies_synced,
            "invoices_found": codat_result.invoices_found,
            "overdue_invoices": codat_result.overdue_invoices,
            "errors": codat_result.errors,
        }
    except Exception as e:
        error_msg = f"Codat sync failed: {e}"
        logger.exception(error_msg)
        summary["errors"].append(error_msg)

    # --- Sync direct OAuth connections (Xero, QuickBooks) ---
    for sme in db.list_active_smes():
        sme_id = UUID(sme["id"])
        sme_name = sme["company_name"]
        summary["smes_processed"] += 1

        try:
            connections = db.list_connections(sme_id)
        except Exception as e:
            error_msg = f"Failed to list connections for {sme_name}: {e}"
            logger.exception(error_msg)
            summary["errors"].append(error_msg)
            continue

        active_connections = [
            c for c in connections if c.get("status") == ConnectionStatus.ACTIVE.value
        ]

        for conn in active_connections:
            platform = conn.get("platform", "unknown")
            conn_id = conn.get("id", "unknown")

            try:
                invoices = sync_from_connection(db, sme_id, conn)
                new_count = upsert_normalised_invoices(db, sme_id, invoices)

                summary["connections_synced"] += 1
                summary["invoices_created"] += new_count

                if new_count > 0:
                    logger.info(
                        "Created %d new invoices for %s via %s",
                        new_count,
                        sme_name,
                        platform,
                    )
                    slack_webhook.send_alert(
                        title="New Overdue Invoices Synced",
                        message=(
                            f"Synced {new_count} new overdue invoice(s) "
                            f"for {sme_name} via {platform}"
                        ),
                        severity="info",
                    )

            except Exception as e:
                error_msg = f"Failed to sync connection {conn_id} ({platform}) for {sme_name}: {e}"
                logger.exception(error_msg)
                summary["errors"].append(error_msg)

        # --- Check for externally paid invoices via OAuth ---
        for conn in active_connections:
            try:
                paid_count = check_paid_externally_oauth(db, sme_id, conn)
                summary["externally_paid"] += paid_count
            except Exception as e:
                logger.warning(
                    "Failed OAuth paid check for %s (%s): %s",
                    sme_name,
                    conn.get("platform", "?"),
                    e,
                )

        # --- Check for disconnected integrations with active invoices ---
        try:
            _check_disconnects(db, sme_id, sme_name)
        except Exception:
            logger.warning(
                "Failed disconnect check for %s",
                sme_name,
                exc_info=True,
            )

    logger.info(
        "Full sync complete: %d SMEs processed, %d connections synced, "
        "%d new invoices created, %d externally paid, %d errors",
        summary["smes_processed"],
        summary["connections_synced"],
        summary["invoices_created"],
        summary["externally_paid"],
        len(summary["errors"]),
    )

    return summary
