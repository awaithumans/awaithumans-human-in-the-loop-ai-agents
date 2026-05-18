"""Channel capability matrix for form primitives.

Each primitive declares whether it renders natively in each channel or
must fall back to a "Complete in dashboard" link-out. If *any* field in
a form forces link-out in a given channel, the whole form falls back in
that channel — the developer's typed-response contract is preserved
regardless of which path the human took.

This is the single source of truth. Channel renderers consult it before
building their output.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from awaithumans.forms.definition import FormDefinition


Channel = Literal["dashboard", "slack", "email_interactive", "email_plain"]


class ChannelSupport(str, enum.Enum):
    NATIVE = "native"
    LINK_OUT = "link_out"


N = ChannelSupport.NATIVE
L = ChannelSupport.LINK_OUT


CAPABILITIES: dict[str, dict[Channel, ChannelSupport]] = {
    # Text
    "display_text": {"dashboard": N, "slack": N, "email_interactive": N, "email_plain": N},
    "short_text": {"dashboard": N, "slack": N, "email_interactive": L, "email_plain": L},
    "long_text": {"dashboard": N, "slack": N, "email_interactive": L, "email_plain": L},
    "rich_text": {"dashboard": N, "slack": L, "email_interactive": L, "email_plain": L},
    # Selection
    "switch": {"dashboard": N, "slack": N, "email_interactive": N, "email_plain": L},
    "single_select": {"dashboard": N, "slack": N, "email_interactive": N, "email_plain": L},
    "multi_select": {"dashboard": N, "slack": N, "email_interactive": N, "email_plain": L},
    "picture_choice": {"dashboard": N, "slack": N, "email_interactive": N, "email_plain": L},
    # Numeric
    "slider": {"dashboard": N, "slack": N, "email_interactive": L, "email_plain": L},
    "star_rating": {"dashboard": N, "slack": N, "email_interactive": N, "email_plain": L},
    "opinion_scale": {"dashboard": N, "slack": N, "email_interactive": N, "email_plain": L},
    "ranking": {"dashboard": N, "slack": L, "email_interactive": L, "email_plain": L},
    # Date/time
    "date": {"dashboard": N, "slack": N, "email_interactive": L, "email_plain": L},
    "datetime": {"dashboard": N, "slack": N, "email_interactive": L, "email_plain": L},
    "date_range": {"dashboard": N, "slack": L, "email_interactive": L, "email_plain": L},
    "time": {"dashboard": N, "slack": N, "email_interactive": L, "email_plain": L},
    # Media input
    "file_upload": {"dashboard": N, "slack": N, "email_interactive": L, "email_plain": L},
    "signature": {"dashboard": N, "slack": L, "email_interactive": L, "email_plain": L},
    # Media display
    "image": {"dashboard": N, "slack": N, "email_interactive": N, "email_plain": L},
    "video": {"dashboard": N, "slack": L, "email_interactive": L, "email_plain": L},
    "pdf_viewer": {"dashboard": N, "slack": L, "email_interactive": L, "email_plain": L},
    "html": {"dashboard": N, "slack": L, "email_interactive": N, "email_plain": L},
    # Layout
    "section": {"dashboard": N, "slack": N, "email_interactive": N, "email_plain": N},
    "divider": {"dashboard": N, "slack": N, "email_interactive": N, "email_plain": N},
    "section_collapse": {"dashboard": N, "slack": L, "email_interactive": N, "email_plain": N},
    # Complex
    "table": {"dashboard": N, "slack": L, "email_interactive": L, "email_plain": L},
    "subform": {"dashboard": N, "slack": L, "email_interactive": L, "email_plain": L},
}


def field_renders_in(kind: str, channel: Channel) -> bool:
    """True iff the primitive kind renders natively in the channel."""
    return CAPABILITIES[kind][channel] == ChannelSupport.NATIVE


def form_renders_in(form: FormDefinition, channel: Channel) -> bool:
    """True iff every field in the form (including recursively nested) renders natively."""
    return not unsupported_fields(form, channel)


def unsupported_fields(form: FormDefinition, channel: Channel) -> list[str]:
    """Names of fields that can't render natively in the channel.

    Recurses into SectionCollapse and Subform children. For layout/display
    elements without a meaningful name, returns their kind.
    """
    # Lazy import to avoid circular dep — capabilities.py is imported by renderers.
    from awaithumans.forms.fields.complex import Subform
    from awaithumans.forms.fields.layout import SectionCollapse

    offenders: list[str] = []

    def walk(fields: list[object]) -> None:
        for f in fields:
            kind = getattr(f, "kind", None)
            if kind is None or kind not in CAPABILITIES:
                continue
            if CAPABILITIES[kind][channel] == ChannelSupport.LINK_OUT:
                identifier = getattr(f, "name", None) or getattr(f, "label", None) or kind
                offenders.append(identifier)
            if isinstance(f, SectionCollapse | Subform):
                walk(list(f.fields))

    walk(list(form.fields))
    return offenders
