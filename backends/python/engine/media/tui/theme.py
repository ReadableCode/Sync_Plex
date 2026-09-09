"""The readablecode terminal-navy design system for every Textual surface in
this project, from the one definition every TUI shares
(readable_utils.design_tokens; the spec is ReadableCode/style-terminal-navy).
This module only re-exports, so the screens import tokens from one place."""

from readable_utils.design_tokens import (  # noqa: F401 — re-exported
    AMBER,
    AMBER_BRIGHT,
    BG,
    GREEN,
    GREEN_BRIGHT,
    GRID,
    HAIRLINE,
    INK,
    INK_2,
    MUTED,
    RED,
    SURFACE,
    SURFACE_2,
    terminal_navy_textual_theme,
)

TERMINAL_NAVY = terminal_navy_textual_theme()
