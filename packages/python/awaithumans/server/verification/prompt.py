"""Prompt builder for the verifier.

The verifier is asked to do two jobs in one LLM call:

  1. **Quality check** the human's response against the task and payload.
     Did they answer the question? Is their answer internally consistent?
     If the operator gave `instructions`, follow them.

  2. **NL parsing** when the human replied in free text (Slack thread or
     email body) instead of submitting the structured form. Extract a
     value conforming to `response_schema` from `raw_input`.

Returning both via the same structured-output call keeps verification a
single round-trip — no separate parse-then-check.

The prompt is provider-agnostic. Each provider (claude/openai/gemini/
azure) wraps it in its own request shape.
"""

from __future__ import annotations

import json
from typing import Any

from awaithumans.types import VerificationContext

# Output schema the verifier MUST fill. Same shape across providers so
# the runner can parse a single response model.
VERIFIER_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "passed": {
            "type": "boolean",
            "description": (
                "True if the response is acceptable (quality + NL parse "
                "succeeded if needed). False if the human should redo it."
            ),
        },
        "reason": {
            "type": "string",
            "description": (
                "One-sentence explanation shown to the human if rejected. "
                "Be concrete: tell them what to fix, not just that something is wrong."
            ),
        },
        "parsed_response": {
            "description": (
                "Structured response conforming to the task's response schema. "
                "Required when raw_input was an NL string. May be null if "
                "structured response was already submitted directly."
            ),
        },
    },
    "required": ["passed", "reason"],
}


def build_system_prompt(instructions: str) -> str:
    """The system message — explains the verifier's job once, up front.

    Operator's `instructions` are the verification policy; everything
    else here is plumbing."""
    return (
        "You are a verifier for a human-in-the-loop AI agent system. A "
        "human reviewer has just submitted a response to a task. Your "
        "job is to do TWO things in one structured output:\n"
        "\n"
        "1. QUALITY CHECK: Decide whether the response is acceptable. "
        "Apply the operator's policy (provided below as INSTRUCTIONS). "
        "If unacceptable, explain why in one sentence so the human "
        "knows what to fix.\n"
        "\n"
        "2. NL PARSING (only when raw_input is set): The human typed a "
        "natural-language reply instead of filling the structured form. "
        "Extract a value matching the response schema and return it as "
        "`parsed_response`. If you cannot extract a valid value with "
        "high confidence, set `passed=false` with a reason like 'Please "
        "answer with one of: approve, reject'.\n"
        "\n"
        "OPERATOR'S INSTRUCTIONS:\n"
        f"{instructions}\n"
        "\n"
        "Reply with the structured output only — no preamble, no "
        "follow-up questions."
    )


def build_user_prompt(ctx: VerificationContext) -> str:
    """The user message — the actual task, payload, response, schemas.

    JSON is dumped with indent=2 so an LLM reading the prompt sees a
    readable tree. The cost is a few extra tokens per call; the
    benefit is meaningfully better extraction quality."""
    parts: list[str] = []
    parts.append(f"TASK:\n{ctx.task}")
    parts.append(f"\nPAYLOAD (the data the human was reviewing):\n{_dump(ctx.payload)}")
    parts.append(
        f"\nRESPONSE SCHEMA (what `parsed_response` must conform to):\n{_dump(ctx.response_schema)}"
    )

    if ctx.raw_input is not None:
        parts.append(f"\nHUMAN'S NL REPLY (parse this into the response schema):\n{ctx.raw_input}")
    if ctx.response is not None:
        parts.append(
            f"\nHUMAN'S STRUCTURED RESPONSE (already form-submitted):\n{_dump(ctx.response)}"
        )

    if ctx.previous_rejections:
        # Tell the verifier what its own previous rejections said. Keeps
        # the loop coherent — without this it might reject for the same
        # reason twice and never give the human a clean shot.
        rejections = "\n".join(f"- {r}" for r in ctx.previous_rejections)
        parts.append(
            f"\nPREVIOUS REJECTION REASONS (this is attempt {ctx.attempt + 1}):\n{rejections}"
        )

    return "\n".join(parts)


def _dump(value: Any) -> str:
    """JSON-dump for prompt embedding. Falls back to repr for non-JSONable values."""
    try:
        return json.dumps(value, indent=2, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(value)


def to_openai_strict_schema(schema: dict) -> dict:
    """Adapt VERIFIER_OUTPUT_SCHEMA for OpenAI / Azure strict mode.

    The Responses API's `text.format` json_schema (and the older
    `response_format` json_schema before it) require, in strict mode,
    every property be in `required` and the object set
    `additionalProperties: false`. We promote `parsed_response` into
    `required` and widen its type to include `null` so the model can
    emit null when no NL parsing is needed — strict mode allows nulls
    when the type union includes them.

    Lives here (next to VERIFIER_OUTPUT_SCHEMA) rather than in any one
    provider so OpenAI and Azure both consume the same shape without
    cross-provider imports."""
    strict = json.loads(json.dumps(schema))  # deep copy
    strict["additionalProperties"] = False
    strict["required"] = list(strict.get("properties", {}).keys())
    if "parsed_response" in strict.get("properties", {}):
        strict["properties"]["parsed_response"]["type"] = [
            "object",
            "string",
            "number",
            "boolean",
            "array",
            "null",
        ]
    return strict
