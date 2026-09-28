"""Shared OpenRouter chat completion helper for the Strategist brain.

Routes through an explicit, ordered fallback list of pinned models
(src.config.settings.openrouter_models), not OpenRouter's auto-router.
Pinning keeps output tone (message generation) and output format
(reply classification) consistent across calls; the auto-router's
per-prompt model switching would risk drift in both.

If the first model in the list errors, OpenRouter automatically retries
the next one server-side before the request fails.

Reasoning is explicitly disabled on every call. The reasoning-capable
models in our fallback lists (GLM 4.6/4.7-flash) will otherwise spend an
unbounded, unpredictable chunk of max_tokens "thinking" before writing an
answer, confirmed against the real classifier prompt: 150-2000 tokens of
reasoning with no guarantee of leaving room for the actual output, versus
a clean answer in under 30 tokens with reasoning off.
"""

from __future__ import annotations

import openai

from src.config import settings

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_client: openai.OpenAI | None = None


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        _client = openai.OpenAI(
            base_url=_OPENROUTER_BASE_URL,
            api_key=settings.openrouter_api_key,
        )
    return _client


def chat_completion(
    *,
    user_prompt: str,
    models: list[str],
    system_prompt: str | None = None,
    max_tokens: int,
) -> str:
    """Run a chat completion against an ordered OpenRouter model fallback list.

    `models` is task-specific (see src.config.settings): message generation
    and reply classification are pinned to different lists so each can be
    tuned for what it needs, quality/tone for one, cheap/fast for the other,
    without one task's requirements dragging on the other's model choice.

    Returns the raw text of the first choice. Raises openai.APIError (or a
    subclass) if every model in the fallback list fails.
    """
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    completion = _get_client().chat.completions.create(
        model=models[0],
        extra_body={"models": models, "reasoning": {"enabled": False}},
        max_tokens=max_tokens,
        messages=messages,
    )

    if not completion.choices or not completion.choices[0].message.content:
        raise RuntimeError("Empty response from LLM")

    return completion.choices[0].message.content.strip()
