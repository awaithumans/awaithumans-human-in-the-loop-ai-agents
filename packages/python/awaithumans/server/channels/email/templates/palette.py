"""Email chrome — colors, fonts, and button shapes.

These values are email-specific on purpose:

- Email clients (Gmail, Outlook) strip `<style>` blocks, so colors have
  to appear as inline CSS strings at render time. No `var(--foo)`.
- The gray palette is chosen for email readability on both light and
  dark-mode clients; the dashboard's Tailwind theme is a separate
  concern and would be distracting noise here.
- Brand tokens (`_BRAND`, `_BG_DARK`, `_TEXT_LIGHT`) duplicate the
  dashboard's `@theme` tokens in `packages/dashboard/app/globals.css`.
  Keep them in sync **by convention**, not by code-sharing — the
  consumption patterns (inline CSS strings here vs CSS custom props
  there) are different enough that sharing would be awkward.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

# ─── Brand tokens (mirror dashboard globals.css) ────────────────────────

_BRAND = "#00E676"
_BG_DARK = "#0A0A0A"
_TEXT_LIGHT = "#F5F5F5"

# ─── Light-surface palette (notification email) ─────────────────────────

LIGHT_PALETTE = {
    "bg_page": "#F3F4F6",
    "bg_card": "#FFFFFF",
    "text_strong": _BG_DARK,
    "text_muted": "#6B7280",
    "text_link": "#059669",
}

# ─── Dark-surface palette (confirm / completed pages) ───────────────────

DARK_PALETTE = {
    "bg_dark": _BG_DARK,
    "bg_card_dark": "#111111",
    "border_dark": "#222",
    "text_light": _TEXT_LIGHT,
    "text_muted_dark": "#9CA3AF",
    "bg_primary": _BRAND,
    "text_on_primary": _BG_DARK,
    "bg_cancel": "#1F1F1F",
    "text_cancel": "#E5E7EB",
    "border_cancel": "#333",
}

# ─── Typography ─────────────────────────────────────────────────────────

FONT_STACK = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif"

# ─── Button spec + renderer ─────────────────────────────────────────────

_BUTTON_STYLES = {
    "primary": (_BRAND, _BG_DARK),  # bg, fg
    "danger": ("#F87171", _BG_DARK),
    "neutral": ("#E5E7EB", _BG_DARK),
}


@dataclass
class ButtonSpec:
    label: str
    url: str
    # "primary" | "danger" | "neutral" — drives button color.
    style: str = "neutral"


def render_button(button: ButtonSpec) -> str:
    """Render a single button as inline-styled <a>. Email-safe HTML."""
    bg, fg = _BUTTON_STYLES.get(button.style, _BUTTON_STYLES["neutral"])
    return (
        f'<a href="{escape(button.url, quote=True)}" '
        f'style="display:inline-block;padding:12px 22px;background:{bg};'
        f"color:{fg};text-decoration:none;border-radius:6px;"
        f'font-weight:600;font-size:14px;margin:0 8px 8px 0;">'
        f"{escape(button.label)}</a>"
    )
