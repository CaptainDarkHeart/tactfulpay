"""LLM-based reply classification using the pinned OpenRouter model.

Classifies inbound email replies into one of seven categories:
    PROMISE_TO_PAY, PAYMENT_PENDING, DISPUTE, REDIRECT,
    STALL, HOSTILE, NO_RESPONSE

The classifier prompt is loaded from src/strategist/prompts/classifier.txt.
"""

from __future__ import annotations

from pathlib import Path

from src.config import settings
from src.db.models import Classification
from src.strategist.llm_client import chat_completion

PROMPT_PATH = Path(__file__).parent / "prompts" / "classifier.txt"

# Valid classification values for parsing
VALID_CLASSIFICATIONS = {c.value.upper(): c for c in Classification}


def classify_response(reply_text: str) -> tuple[Classification, str]:
    """Classify an inbound email reply using the pinned OpenRouter model.

    Args:
        reply_text: The raw text of the email reply.

    Returns:
        Tuple of (Classification enum, one-sentence justification).
    """
    prompt_template = PROMPT_PATH.read_text()
    prompt = prompt_template.replace("{reply_text}", reply_text)

    try:
        raw = chat_completion(
            user_prompt=prompt,
            models=settings.openrouter_classifier_models,
            max_tokens=150,
        )
    except RuntimeError:
        return Classification.STALL, "Empty response from classifier LLM"

    return _parse_classification(raw)


def _parse_classification(raw: str) -> tuple[Classification, str]:
    """Parse the LLM's response into a Classification enum + justification.

    Expected format: "CATEGORY_NAME - justification sentence"
    Falls back to STALL if parsing fails (safe default — doesn't pause or escalate).
    """
    # Try splitting on common delimiters
    for delimiter in [" - ", " — ", ": ", "\n"]:
        if delimiter in raw:
            parts = raw.split(delimiter, 1)
            category = parts[0].strip().upper()
            justification = parts[1].strip() if len(parts) > 1 else ""

            if category in VALID_CLASSIFICATIONS:
                return VALID_CLASSIFICATIONS[category], justification

    # Try matching just the first word/line
    first_token = raw.split()[0].strip().upper() if raw else ""
    if first_token in VALID_CLASSIFICATIONS:
        justification = raw[len(first_token) :].strip().lstrip("-:—").strip()
        return VALID_CLASSIFICATIONS[first_token], justification

    # Safe fallback
    return Classification.STALL, f"Could not parse classification from: {raw!r}"
