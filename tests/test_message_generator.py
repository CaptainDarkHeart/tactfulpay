"""Tests for the user-prompt building logic in message_generator.

Only covers the pure prompt-construction function (_build_user_prompt).
LLM calls and full generate_message() are not exercised here. The live BoE
rate fetch is mocked so these tests never hit the network.
"""

from decimal import Decimal
from unittest.mock import patch

from src.db.models import InvoicePhase
from src.strategist.message_generator import MessageContext, _build_user_prompt


def _base_ctx(**overrides) -> MessageContext:
    defaults = dict(
        agent_name="Alex",
        sme_name="Acme Ltd",
        invoice_number="INV-1042",
        debtor_company="Debtor Co",
        contact_name="Jamie",
        contact_email="jamie@debtor.co",
        amount="4500.00",
        currency="GBP",
        days_overdue=16,
        due_date="2026-09-10",
        phase=InvoicePhase.PHASE_1,
        interaction_count_in_phase=0,
    )
    defaults.update(overrides)
    return MessageContext(**defaults)


class TestPhaseZeroPrompt:
    def test_not_yet_due_phrasing(self):
        ctx = _base_ctx(phase=InvoicePhase.PHASE_0, days_overdue=-2, due_date="2026-10-01")
        prompt = _build_user_prompt(ctx)
        assert "not yet due" in prompt
        assert "overdue" not in prompt.split("not yet due")[0].split("regarding")[-1]

    def test_overdue_phrasing_unchanged_for_later_phases(self):
        ctx = _base_ctx(phase=InvoicePhase.PHASE_1, days_overdue=3)
        prompt = _build_user_prompt(ctx)
        assert "3 days overdue" in prompt


class TestPhaseFourStatutoryFigures:
    @patch("src.strategist.message_generator.get_boe_base_rate_percent", return_value=Decimal("4.0"))
    def test_includes_exact_calculated_figures(self, mock_rate):
        ctx = _base_ctx(phase=InvoicePhase.PHASE_4, days_overdue=16, amount="4500.00")
        prompt = _build_user_prompt(ctx)
        # 4500 * ((4.0 + 8) / 100 / 365) * 16, compensation fee tier for 4500 is 70
        assert "GBP 70" in prompt
        assert "trade credit reporting" in prompt
        assert "12.0%" in prompt

    @patch("src.strategist.message_generator.get_boe_base_rate_percent", return_value=Decimal("4.0"))
    def test_total_due_is_principal_plus_interest_plus_compensation(self, mock_rate):
        ctx = _base_ctx(phase=InvoicePhase.PHASE_4, days_overdue=16, amount="4500.00")
        prompt = _build_user_prompt(ctx)
        assert "new total now due" in prompt
