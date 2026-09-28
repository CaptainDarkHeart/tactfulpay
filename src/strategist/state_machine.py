"""Phase progression state machine for invoice collection lifecycle.

Manages the escalation sequence and handles response-driven transitions
per the action matrix defined in the spec.

Phase timeline:
    Phase 0 (Days -3 to 0): Email only, pre-due admin verification
    Phase 1 (Days 1-5):  Email only, friendly check-in
    Phase 2 (Days 7-10): Email + voice, internal advocate
    Phase 3 (Days 14-17): Email + voice, loss aversion
    Phase 4 (Day 21+):   Formal email + LinkedIn

Transitions triggered by:
    - Time elapsed with NO_RESPONSE → escalate to next phase
    - Classification of inbound replies → action per matrix
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from src.db.models import (
    Classification,
    Database,
    InvoicePhase,
    InvoiceStatus,
)

# Phase cadence: maps phase to (days into sequence, phase duration in days)
PHASE_SCHEDULE = {
    InvoicePhase.PHASE_0: {"start_day": -3, "duration": 3},
    InvoicePhase.PHASE_1: {"start_day": 1, "duration": 5},
    InvoicePhase.PHASE_2: {"start_day": 7, "duration": 4},
    InvoicePhase.PHASE_3: {"start_day": 14, "duration": 4},
    InvoicePhase.PHASE_4: {"start_day": 21, "duration": 7},
}

# Follow-up schedule within each phase (days after phase start)
PHASE_FOLLOWUPS = {
    InvoicePhase.PHASE_0: [0],  # Single pre-due admin check
    InvoicePhase.PHASE_1: [0, 2, 4],  # Day 1, 3, 5
    InvoicePhase.PHASE_2: [0, 3],  # Day 7, 10
    InvoicePhase.PHASE_3: [0, 3],  # Day 14, 17
    InvoicePhase.PHASE_4: [0, 2],  # Day 21, 23
}

# How many days before its due date an invoice should start in Phase 0
# (pre-due admin verification) instead of Phase 1.
PRE_DUE_WINDOW_DAYS = 3


def initial_phase_for_due_date(due_date: date, today: date | None = None) -> InvoicePhase:
    """Decide which phase a newly tracked invoice should start in.

    Invoices synced or imported within PRE_DUE_WINDOW_DAYS of their due date
    start in Phase 0, catching PO mismatches or vendor onboarding issues
    before the invoice is even overdue. Anything already overdue, or due
    further out than the window, starts at Phase 1 as before.
    """
    today = today or date.today()
    days_until_due = (due_date - today).days
    if 0 <= days_until_due <= PRE_DUE_WINDOW_DAYS:
        return InvoicePhase.PHASE_0
    return InvoicePhase.PHASE_1


@dataclass
class TransitionResult:
    """Result of processing a state transition."""

    action: str  # send_message, pause, escalate_phase, redirect, monitor, human_review
    new_phase: InvoicePhase | None = None
    new_status: InvoiceStatus | None = None
    message: str = ""
    accelerated: bool = False  # True if cadence is accelerated due to STALL


def handle_classification(
    classification: Classification,
    current_phase: InvoicePhase,
    invoice_id: UUID,
    db: Database,
) -> TransitionResult:
    """Process an inbound reply classification and determine the next action.

    Implements the action matrix from the spec.
    """
    if classification == Classification.PROMISE_TO_PAY:
        db.update_invoice(
            invoice_id,
            {
                "status": InvoiceStatus.ACTIVE.value,
            },
        )
        return TransitionResult(
            action="monitor",
            message="Send thank-you + calendar reminder for promised date. "
            "Re-engage with Phase 2 tone if payment not received by date + 3 days.",
        )

    if classification == Classification.PAYMENT_PENDING:
        return TransitionResult(
            action="send_message",
            message="Acknowledge the payment is in progress with a light touch. "
            "Follow up in 2 days if no reference has come through by then.",
        )

    if classification == Classification.CHECK_OR_TRANSFER_INITIATED:
        return TransitionResult(
            action="send_message",
            message="Request payment reference, transaction trace, or check number "
            "for verification. Follow up in 3 business days.",
        )

    if classification == Classification.INABILITY_TO_PAY:
        return TransitionResult(
            action="send_message",
            message="Do not escalate tone. Offer a structured two-stage payment "
            "arrangement (e.g. 50% now, 50% in 14 days) or a split invoice option "
            "to secure partial liquidity immediately.",
        )

    if classification == Classification.DISPUTE:
        db.update_invoice(
            invoice_id,
            {
                "current_phase": InvoicePhase.DISPUTED.value,
                "status": InvoiceStatus.DISPUTED.value,
            },
        )
        return TransitionResult(
            action="pause",
            new_phase=InvoicePhase.DISPUTED,
            new_status=InvoiceStatus.DISPUTED,
            message="DISPUTED — agent paused. Human intervention required.",
        )

    if classification == Classification.REDIRECT:
        return TransitionResult(
            action="redirect",
            new_phase=InvoicePhase.PHASE_1,
            message="Add new contact to sequence at Phase 1.",
        )

    if classification == Classification.STALL:
        return TransitionResult(
            action="send_message",
            accelerated=True,
            message="Acknowledge stall, continue current phase on accelerated timeline (-2 days).",
        )

    if classification == Classification.HOSTILE:
        db.update_invoice(
            invoice_id,
            {
                "current_phase": InvoicePhase.HUMAN_REVIEW.value,
                "status": InvoiceStatus.PAUSED.value,
            },
        )
        return TransitionResult(
            action="pause",
            new_phase=InvoicePhase.HUMAN_REVIEW,
            new_status=InvoiceStatus.PAUSED,
            message="HOSTILE — agent paused. Do NOT respond. Human review required.",
        )

    if classification == Classification.WRITE_OFF_CLAIMED:
        # Preserve current phase so we can resume from the right place if debtor lied
        db.update_invoice(
            invoice_id,
            {
                "current_phase": InvoicePhase.WRITE_OFF_CLAIMED.value,
                "status": InvoiceStatus.PAUSED.value,
                "write_off_claimed_at": datetime.now(UTC).replace(tzinfo=None),
                "pre_write_off_phase": current_phase.value,
            },
        )
        return TransitionResult(
            action="pause",
            new_phase=InvoicePhase.WRITE_OFF_CLAIMED,
            new_status=InvoiceStatus.PAUSED,
            message=(
                "WRITE-OFF CLAIMED — agent paused. "
                "SME must confirm or deny before agent re-engages."
            ),
        )

    if classification == Classification.NO_RESPONSE:
        return _escalate_phase(current_phase, invoice_id, db)

    return TransitionResult(action="monitor", message="Unknown classification — monitoring.")


def _escalate_phase(
    current_phase: InvoicePhase,
    invoice_id: UUID,
    db: Database,
) -> TransitionResult:
    """Move to the next phase on NO_RESPONSE."""
    phase_order = [
        InvoicePhase.PHASE_0,
        InvoicePhase.PHASE_1,
        InvoicePhase.PHASE_2,
        InvoicePhase.PHASE_3,
        InvoicePhase.PHASE_4,
    ]

    if current_phase not in phase_order:
        return TransitionResult(action="monitor", message="Invoice not in active phase sequence.")

    idx = phase_order.index(current_phase)

    if idx >= len(phase_order) - 1:
        # End of Phase 4 — flag for human review
        db.update_invoice(
            invoice_id,
            {
                "current_phase": InvoicePhase.HUMAN_REVIEW.value,
                "status": InvoiceStatus.PAUSED.value,
            },
        )
        return TransitionResult(
            action="human_review",
            new_phase=InvoicePhase.HUMAN_REVIEW,
            new_status=InvoiceStatus.PAUSED,
            message="Phase 4 exhausted with no response. Flagged for human review.",
        )

    next_phase = phase_order[idx + 1]
    db.update_invoice(invoice_id, {"current_phase": next_phase.value})

    return TransitionResult(
        action="escalate_phase",
        new_phase=next_phase,
        message=f"No response — escalating from {current_phase.value} to Phase {next_phase.value}.",
    )


def should_escalate(
    current_phase: InvoicePhase,
    phase_start_date: datetime | date | None,
    accelerated: bool = False,
) -> bool:
    """Evaluate proper timing parameters checking elapsed duration against phase initiation date to safely control progression"""
    if current_phase not in PHASE_SCHEDULE:
        return False

    if phase_start_date is None:
        return True

    schedule = PHASE_SCHEDULE[current_phase]
    duration = schedule["duration"]
    if accelerated:
        duration = max(1, duration - 2)

    if hasattr(phase_start_date, "date"):
        start_date = phase_start_date.date()
    else:
        start_date = phase_start_date

    now_date = datetime.now(UTC).date()
    elapsed = (now_date - start_date).days
    return elapsed >= duration


def get_next_followup_day(
    current_phase: InvoicePhase,
    interactions_in_phase: int,
) -> int | None:
    """Get the next follow-up day offset within the current phase.

    Returns None if all follow-ups for this phase have been sent.
    """
    followups = PHASE_FOLLOWUPS.get(current_phase, [])
    if interactions_in_phase >= len(followups):
        return None
    return followups[interactions_in_phase]
