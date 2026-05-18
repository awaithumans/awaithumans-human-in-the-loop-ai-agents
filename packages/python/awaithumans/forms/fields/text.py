"""Text form field primitives.

- DisplayText: read-only context shown to the human (not an input).
- ShortText: single-line input. Subtypes: plain/email/url/phone/currency/number/password.
- LongText: multi-line textarea.
- RichText: formatted text with inline styling (bold, italic, lists, links).

Numeric single-line inputs (e.g. amounts) use ShortText with subtype="number"
or subtype="currency". Slider/rating/scale live in fields/numeric.py.
"""

from __future__ import annotations

from typing import Literal

from awaithumans.forms.base import FormFieldBase

# ─── Classes ─────────────────────────────────────────────────────────────


class DisplayText(FormFieldBase):
    """Read-only block of text shown to the human. Not an input."""

    kind: Literal["display_text"] = "display_text"
    text: str
    markdown: bool = False
    required: bool = False


ShortTextSubtype = Literal["plain", "email", "url", "phone", "currency", "number", "password"]


class ShortText(FormFieldBase):
    """Single-line text input. Use subtype to pick validation + keyboard."""

    kind: Literal["short_text"] = "short_text"
    subtype: ShortTextSubtype = "plain"
    placeholder: str | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None
    currency_code: str | None = None


class LongText(FormFieldBase):
    """Multi-line text input (plain text, no formatting)."""

    kind: Literal["long_text"] = "long_text"
    placeholder: str | None = None
    min_length: int | None = None
    max_length: int | None = None
    rows: int | None = None


class RichText(FormFieldBase):
    """Formatted text input with bold/italic/lists/links.

    Dashboard-only native rendering. Slack/email degrade to link-out.
    """

    kind: Literal["rich_text"] = "rich_text"
    placeholder: str | None = None
    max_length: int | None = None


# ─── DSL helpers ─────────────────────────────────────────────────────────


def display_text(
    text: str,
    *,
    label: str | None = None,
    markdown: bool = False,
) -> DisplayText:
    return DisplayText(text=text, label=label, markdown=markdown)


def short_text(
    *,
    label: str | None = None,
    hint: str | None = None,
    placeholder: str | None = None,
    subtype: ShortTextSubtype = "plain",
    min_length: int | None = None,
    max_length: int | None = None,
    pattern: str | None = None,
    currency_code: str | None = None,
) -> ShortText:
    return ShortText(
        label=label,
        hint=hint,
        placeholder=placeholder,
        subtype=subtype,
        min_length=min_length,
        max_length=max_length,
        pattern=pattern,
        currency_code=currency_code,
    )


def email(
    *,
    label: str | None = None,
    hint: str | None = None,
    placeholder: str | None = None,
) -> ShortText:
    return ShortText(label=label, hint=hint, placeholder=placeholder, subtype="email")


def url(
    *,
    label: str | None = None,
    hint: str | None = None,
    placeholder: str | None = None,
) -> ShortText:
    return ShortText(label=label, hint=hint, placeholder=placeholder, subtype="url")


def phone(
    *,
    label: str | None = None,
    hint: str | None = None,
    placeholder: str | None = None,
) -> ShortText:
    return ShortText(label=label, hint=hint, placeholder=placeholder, subtype="phone")


def currency(
    *,
    currency_code: str,
    label: str | None = None,
    hint: str | None = None,
    placeholder: str | None = None,
) -> ShortText:
    return ShortText(
        label=label,
        hint=hint,
        placeholder=placeholder,
        subtype="currency",
        currency_code=currency_code,
    )


def password(
    *,
    label: str | None = None,
    hint: str | None = None,
    min_length: int | None = None,
) -> ShortText:
    return ShortText(
        label=label,
        hint=hint,
        subtype="password",
        min_length=min_length,
    )


def long_text(
    *,
    label: str | None = None,
    hint: str | None = None,
    placeholder: str | None = None,
    min_length: int | None = None,
    max_length: int | None = None,
    rows: int | None = None,
) -> LongText:
    return LongText(
        label=label,
        hint=hint,
        placeholder=placeholder,
        min_length=min_length,
        max_length=max_length,
        rows=rows,
    )


def rich_text(
    *,
    label: str | None = None,
    hint: str | None = None,
    placeholder: str | None = None,
    max_length: int | None = None,
) -> RichText:
    return RichText(
        label=label,
        hint=hint,
        placeholder=placeholder,
        max_length=max_length,
    )
