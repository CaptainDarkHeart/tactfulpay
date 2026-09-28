"""Hard-coded guardrails for the Strategist brain.

These constraints are enforced in code, not just in prompts,
to prevent hallucination of unauthorised offers or language.
"""

from dataclasses import dataclass

# Words the agent must never use in Phase 1
PHASE_1_BANNED_WORDS = frozenset(
    {
        "overdue",
        "late",
        "debt",
        "owed",
        "collections",
        "legal",
        "lawyer",
        "court",
        "solicitor",
    }
)

# The strongest language permitted in Phases 0 to 3
MAX_ESCALATION_LANGUAGE = "external compliance partner"

# Phase 4 only, loosened 2026-09-28 (Dan's sign-off on Stewart's TactfulPay v2
# spec): the agent may additionally state exact statutory interest and
# compensation figures (calculated via src/billing/statutory_interest.py, never
# invented) and reference trade credit reporting. It must still never name a
# specific law firm or threaten court action directly.
PHASE_4_MAX_ESCALATION_LANGUAGE = "trade credit reporting"

# Discount limits by phase
PHASE_DISCOUNT_LIMITS: dict[int, float] = {
    0: 0.0,  # No discounts pre-due
    1: 0.0,  # No discounts in Phase 1
    2: 2.0,  # Max 2% for payment within 48h
    3: 3.0,  # Max 3% for payment within 24h (requires pre-auth)
    4: 0.0,  # No discounts in Phase 4
}

# Maximum email word counts by phase
PHASE_MAX_WORDS: dict[int, int] = {
    0: 80,
    1: 120,
    2: 100,
    3: 110,
    4: 80,
}

# Classifications that require immediate agent pause
PAUSE_CLASSIFICATIONS = frozenset({"DISPUTE", "HOSTILE"})

# Maximum voice message duration in seconds
MAX_VOICE_MESSAGE_SECONDS = 30


@dataclass(frozen=True)
class DiscountOffer:
    """Validated discount offer that has passed all guardrails."""

    percentage: float
    payment_window_hours: int
    phase: int
    sme_authorised: bool

    def is_valid(self) -> bool:
        phase_limit = PHASE_DISCOUNT_LIMITS.get(self.phase, 0.0)
        if self.percentage > phase_limit:
            return False
        if self.percentage > 0 and not self.sme_authorised:
            return False
        return True
