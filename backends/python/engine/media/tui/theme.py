"""terminal-navy theme tokens (dotfiles design/tokens.css), shared by every
Textual surface in this project: the media remote and the drive-sync folder
picker. One definition; the app registers TERMINAL_NAVY, the picker too."""

from textual.theme import Theme

# terminal-navy tokens (dotfiles design/tokens.css)
BG = "#0d1420"
SURFACE = "#121b2a"
SURFACE_2 = "#182333"
HAIRLINE = "#273141"  # --border rgba(148,163,184,.16) flattened onto --surface
GRID = "#1c2739"
INK = "#dbe4f0"
INK_2 = "#9fb0c3"
MUTED = "#7d8b9e"
GREEN = "#2ea043"
GREEN_BRIGHT = "#56d364"
AMBER = "#b8860b"
AMBER_BRIGHT = "#e3b341"
RED = "#f87171"

TERMINAL_NAVY = Theme(
    name="terminal-navy",
    primary=GREEN,
    secondary=AMBER,
    accent=GREEN_BRIGHT,
    warning=AMBER_BRIGHT,
    error=RED,
    success=GREEN,
    foreground=INK,
    background=BG,
    surface=SURFACE,
    panel=SURFACE_2,
    dark=True,
    variables={
        "border": GREEN,
        "border-blurred": HAIRLINE,
        "footer-key-foreground": GREEN_BRIGHT,
        "block-cursor-foreground": INK,
        "block-cursor-background": GRID,
        "block-cursor-blurred-foreground": INK_2,
        "block-cursor-blurred-background": SURFACE_2,
        "block-hover-background": SURFACE_2,
        "input-selection-background": f"{GREEN} 35%",
    },
)
