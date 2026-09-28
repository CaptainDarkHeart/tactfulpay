"""LLM-based message composition for each escalation phase.

Loads the appropriate phase system prompt, injects invoice/contact context,
and calls the pinned OpenRouter model (see src.strategist.llm_client) to
generate the outbound message.

Also implements the "reply to sent" follow-up technique (Phase 1 follow-ups
are framed as forwarding your own sent email).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from decimal import Decimal

from src.billing.statutory_interest import calculate_statutory_charges
from src.config import settings
from src.db.models import InvoicePhase
from src.strategist.constraints import PHASE_1_BANNED_WORDS, PHASE_MAX_WORDS, DiscountOffer
from src.strategist.llm_client import chat_completion

PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass
class MessageContext:
    """All context needed to generate an outbound message."""

    agent_name: str
    sme_name: str
    invoice_number: str
    debtor_company: str
    contact_name: str
    contact_email: str
    amount: str
    currency: str
    days_overdue: int
    due_date: str
    phase: InvoicePhase
    interaction_count_in_phase: int
    previous_messages: list[str] | None = None
    discount_authorised: bool = False
    max_discount_percent: float = 0.0
    payment_link_url: str | None = None


@dataclass
class GeneratedMessage:
    """The output of message generation."""

    subject: str
    body: str
    is_reply_to_sent: bool = False


def _check_discounts(body: str, ctx: MessageContext) -> None:
    """Check if the generated message offers unauthorized discounts."""
    percentages = re.findall(r"(\d+(?:\.\d+)?)%", body)
    for p in percentages:
        val = float(p)
        phase_num = int(ctx.phase.value) if ctx.phase.value.isdigit() else 0
        offer = DiscountOffer(
            percentage=val,
            payment_window_hours=24,
            phase=phase_num,
            sme_authorised=ctx.discount_authorised,
        )
        if not offer.is_valid():
            raise ValueError(
                f"Constraint Violation: Unauthorized discount of {val}% offered in phase {ctx.phase.value}"
            )


def generate_message(ctx: MessageContext) -> GeneratedMessage:
    """Generate an outbound email for the given phase and context.

    For Phase 1 follow-ups (interaction_count > 0), uses the "reply to sent"
    technique: forwards the original sent email with a casual bump.
    """
    # Phase 1 follow-up: "reply to sent" technique
    if ctx.phase == InvoicePhase.PHASE_1 and ctx.interaction_count_in_phase > 0:
        return _generate_reply_to_sent(ctx)

    system_prompt = _load_phase_prompt(ctx)
    user_prompt = _build_user_prompt(ctx)

    max_retries = 3
    last_error = None

    for attempt in range(max_retries):
        raw = chat_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            models=settings.openrouter_message_models,
            max_tokens=500,
        )
        subject, body = _parse_email(raw, ctx)

        try:
            # Post generation guardrail (subject line included, the LLM can
            # leak a raw hyphenated invoice number into it same as the body)
            subject = _enforce_banned_words(subject, ctx.phase)
            body = _enforce_banned_words(body, ctx.phase)
            _check_discounts(body, ctx)
            return GeneratedMessage(subject=subject, body=body)
        except ValueError as e:
            last_error = e
            # add the violation feedback back to the prompt
            user_prompt += (
                f"\n\nYour previous attempt failed with constraint violation: {e}. "
                "If this was a dash or hyphen, you likely used one for a pause or an aside. "
                "Rewrite using a comma or a new sentence instead, not any kind of dash."
            )

    raise RuntimeError(
        f"Failed to generate valid message after {max_retries} attempts. Last error: {last_error}"
    )


def _load_phase_prompt(ctx: MessageContext) -> str:
    """Load and fill the system prompt template for the current phase."""
    phase_num = ctx.phase.value
    prompt_file = PROMPTS_DIR / f"phase_{phase_num}.txt"

    if not prompt_file.exists():
        raise FileNotFoundError(f"No prompt template for phase {phase_num}")

    template = prompt_file.read_text()
    return template.format(
        agent_name=ctx.agent_name,
        sme_name=ctx.sme_name,
        invoice_number=ctx.invoice_number,
    )


def _build_user_prompt(ctx: MessageContext) -> str:
    """Build the user-turn prompt with invoice context for the LLM."""
    max_words = PHASE_MAX_WORDS.get(int(ctx.phase.value), 120)

    # Strip hyphens from the invoice number before it ever reaches the model,
    # otherwise it gets echoed back verbatim ("#INV-1042") and trips the
    # no-hyphen guardrail on a value the model had no reason to think it
    # was allowed to reformat.
    display_invoice_number = ctx.invoice_number.replace("-", " ")

    if ctx.days_overdue > 0:
        timing_context = f"which is {ctx.days_overdue} days overdue (due date: {ctx.due_date})"
    else:
        timing_context = f"which is not yet due (due date: {ctx.due_date})"

    prompt = (
        f"Write an email to {ctx.contact_name} at {ctx.debtor_company} "
        f"regarding Invoice #{display_invoice_number} for {ctx.currency} {ctx.amount}, "
        f"{timing_context}.\n\n"
        f"Keep the email under {max_words} words.\n"
        f"Include a subject line on the first line prefixed with 'Subject: '.\n"
        f"Then write the email body.\n"
    )

    if ctx.discount_authorised and ctx.max_discount_percent > 0:
        prompt += (
            f"\nThe client has pre-authorised a maximum discount of "
            f"{ctx.max_discount_percent}% for early payment. "
            f"You may offer this if strategically appropriate for this phase.\n"
        )
    else:
        prompt += "\nDo NOT offer any discounts.\n"

    if ctx.payment_link_url:
        prompt += (
            f"\nA secure payment link is available: {ctx.payment_link_url}\n"
            f"Include this link naturally in the email so the recipient can pay directly.\n"
        )

    phase_num = int(ctx.phase.value)
    if phase_num >= 4:
        charges = calculate_statutory_charges(
            principal=Decimal(ctx.amount),
            days_overdue=ctx.days_overdue,
            boe_base_rate_percent=Decimal(str(settings.boe_base_rate_percent)),
        )
        prompt += (
            "\nStatutory interest context: The invoice has exceeded the payment deadline. "
            f"Accrued statutory interest is {ctx.currency} {charges.accrued_interest} "
            f"({charges.annual_rate_percent}% per annum, 8 percent above the Bank of England base rate). "
            f"The statutory compensation fee is {ctx.currency} {charges.compensation_fee}. "
            f"The new total now due is {ctx.currency} {charges.total_due}. "
            "State these exact figures plainly and mention the account is queued for trade credit "
            "reporting if unresolved, as instructed in your system prompt.\n"
        )
    elif phase_num >= 3:
        prompt += (
            "\nStatutory interest context: The invoice is past the due date. "
            "Inform the debtor that statutory interest at 8 percent above the Bank of England base rate "
            "has been automatically added to the balance under UK law and is no longer negotiable.\n"
        )
    elif phase_num >= 2:
        prompt += (
            "\nStatutory interest context: The payment deadline is approaching. "
            "Mention that under the Late Payment of Commercial Debts Regulations, "
            "statutory interest at 8 percent above the Bank of England base rate automatically applies "
            "once payment is past the due date. Frame this as a helpful warning about a system process.\n"
        )

    if ctx.previous_messages:
        prompt += "\nPrevious messages in this thread (for context, do not repeat):\n"
        for msg in ctx.previous_messages[-3:]:
            prompt += f"---\n{msg}\n"

    return prompt


def _parse_email(raw: str, ctx: MessageContext) -> tuple[str, str]:
    """Parse the LLM output into subject and body."""
    lines = raw.strip().split("\n")
    subject = ""
    body_start = 0

    for i, line in enumerate(lines):
        if line.lower().startswith("subject:"):
            subject = line[len("subject:") :].strip()
            body_start = i + 1
            break

    if not subject:
        # Fallback subject
        subject = f"Quick check on invoice #{ctx.invoice_number}"
        body_start = 0

    body = "\n".join(lines[body_start:]).strip()
    return subject, body


def _generate_reply_to_sent(ctx: MessageContext) -> GeneratedMessage:
    """Generate a 'reply to sent' follow-up for Phase 1.

    This is Stewart's technique: forward your own sent email with a casual
    bump like 'Sending this again in case it didn't arrive.' This bypasses
    spam filters and removes blame from the recipient.
    """
    follow_up_num = ctx.interaction_count_in_phase

    if follow_up_num == 1:
        body = (
            f"Hi {ctx.contact_name},\n\n"
            f"Sending this again in case it got buried - I know how hectic inboxes get. "
            f"Just wanted to make sure Invoice #{ctx.invoice_number} landed okay on your end.\n\n"
            f"Best,\n{ctx.agent_name}\n{ctx.sme_name}"
        )
    else:
        body = (
            f"Hi {ctx.contact_name},\n\n"
            f"Just bumping this up one more time - I want to make sure we haven't "
            f"hit a delivery issue with Invoice #{ctx.invoice_number}. "
            f"Happy to re-send a fresh copy if that helps.\n\n"
            f"Best,\n{ctx.agent_name}\n{ctx.sme_name}"
        )

    return GeneratedMessage(
        subject=f"Re: Quick check on invoice #{ctx.invoice_number}",
        body=body,
        is_reply_to_sent=True,
    )


def _enforce_banned_words(body: str, phase: InvoicePhase) -> str:
    """Post generation check replace banned words and prohibit characters"""
    if phase in (InvoicePhase.PHASE_0, InvoicePhase.PHASE_1):
        replacements = {
            "overdue": "outstanding",
            "late": "pending",
            "debt": "balance",
            "owed": "outstanding",
            "collections": "accounts",
        }
        lower_body = body.lower()
        for banned in PHASE_1_BANNED_WORDS:
            if banned in lower_body:
                replacement = replacements.get(banned, "outstanding")
                body = re.sub(
                    r"\b" + re.escape(banned) + r"\b", replacement, body, flags=re.IGNORECASE
                )

    # Reject semicolons and hyphens completely, including en/em dashes
    # (the LLM substitutes "–"/"—" for a plain hyphen about as often as not)
    if re.search(r"[;\-–—]", body):
        raise ValueError("Constraint Violation: Semicolons and hyphens are forbidden")

    return body
