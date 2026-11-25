"""
UI Theme Specification & Styling Module.

Implements Light and Dark visual palettes per Facebook_Automation_UI_UX_v1.docx
Section 07 specification and Canva 12-screen design system.
"""

from __future__ import annotations

from typing import Any, Dict

THEMES: Dict[str, Dict[str, str]] = {
    "light": {
        "name": "light",
        "bg": "#F4F6FA",
        "card_bg": "#FFFFFF",
        "input_bg": "#F1F4F9",
        "text_primary": "#19283D",
        "text_secondary": "#596A80",
        "text_muted": "#8A99AD",
        "border": "#DCE3EE",
        "border_light": "#EDF1F7",
        "accent": "#467AF4",
        "accent_hover": "#3169DC",
        "accent_soft": "#EBF2FF",
        "accent_soft_text": "#1A56DB",
        "success": "#2E7D32",
        "success_bg": "#E8F5E9",
        "warning": "#B76E00",
        "warning_bg": "#FFF3E0",
        "danger": "#C62828",
        "danger_bg": "#FFEBEE",
        "console_bg": "#1e1e1e",
        "console_fg": "#d4d4d4",
    },
    "dark": {
        "name": "dark",
        "bg": "#0B1019",
        "card_bg": "#131C2B",
        "input_bg": "#192437",
        "text_primary": "#F3F6FC",
        "text_secondary": "#A1B0C7",
        "text_muted": "#64748B",
        "border": "#29374E",
        "border_light": "#1E2A3C",
        "accent": "#3169DC",
        "accent_hover": "#6996FF",
        "accent_soft": "#192843",
        "accent_soft_text": "#82ACFF",
        "success": "#4CAF50",
        "success_bg": "#142E1F",
        "warning": "#FFB74D",
        "warning_bg": "#332611",
        "danger": "#EF5350",
        "danger_bg": "#381717",
        "console_bg": "#0d131f",
        "console_fg": "#e2e8f0",
    },
}


def get_theme(theme_name: str = "light") -> Dict[str, str]:
    """Returns color dictionary for selected theme."""
    return THEMES.get(theme_name.lower(), THEMES["light"])


def apply_theme_to_styles(style: Any, theme: Dict[str, str]) -> None:
    """Configures ttk styles to match theme palette."""
    if not style:
        return

    try:
        style.theme_use("clam")
    except Exception:
        pass

    bg = theme["bg"]
    card_bg = theme["card_bg"]
    text_p = theme["text_primary"]
    text_s = theme["text_secondary"]
    accent = theme["accent"]
    border = theme["border"]

    style.configure(".", background=bg, foreground=text_p, font=("SF Pro", 10))

    # Notebook tabs
    style.configure(
        "TNotebook",
        background=bg,
        borderwidth=0,
    )
    style.configure(
        "TNotebook.Tab",
        background=bg,
        foreground=text_s,
        padding=[20, 10],
        font=("SF Pro", 11, "bold"),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", theme["accent_soft"]), ("active", theme["accent_soft"])],
        foreground=[("selected", theme["accent_soft_text"]), ("active", accent)],
    )

    # Frame & Labelframe
    style.configure("TFrame", background=bg)
    style.configure("Card.TFrame", background=card_bg, relief="flat")
    style.configure("Accent.TFrame", background=theme["accent_soft"], relief="flat")

    style.configure(
        "TLabelframe",
        background=card_bg,
        foreground=text_p,
        bordercolor=border,
    )
    style.configure("TLabelframe.Label", background=card_bg, foreground=accent, font=("SF Pro", 10, "bold"))

    # Labels
    style.configure("TLabel", background=card_bg, foreground=text_p)
    style.configure("Header.TLabel", background=card_bg, foreground=text_p, font=("SF Pro", 16, "bold"))
    style.configure("SubHeader.TLabel", background=card_bg, foreground=text_s, font=("SF Pro", 10))
    style.configure("Status.TLabel", background=card_bg, foreground=accent, font=("SF Pro", 10, "bold"))

    # Buttons
    style.configure(
        "TButton",
        background=theme["card_bg"],
        foreground=text_p,
        bordercolor=border,
        padding=[12, 6],
        font=("SF Pro", 10),
    )
    style.map("TButton", background=[("active", theme["input_bg"])])

    style.configure(
        "Action.TButton",
        background=accent,
        foreground="#ffffff",
        padding=[14, 8],
        font=("SF Pro", 10, "bold"),
    )
    style.map("Action.TButton", background=[("active", theme["accent_hover"])])

    style.configure(
        "Danger.TButton",
        background=theme["danger_bg"],
        foreground=theme["danger"],
        bordercolor=theme["danger"],
        padding=[12, 6],
        font=("SF Pro", 10, "bold"),
    )

    # Treeview
    style.configure(
        "Treeview",
        background=card_bg,
        fieldbackground=card_bg,
        foreground=text_p,
        bordercolor=border,
        rowheight=32,
    )
    style.map("Treeview", background=[("selected", theme["accent_soft"])], foreground=[("selected", theme["accent_soft_text"])])
    style.configure(
        "Treeview.Heading",
        background=theme["input_bg"],
        foreground=text_s,
        font=("SF Pro", 9, "bold"),
        padding=[8, 6],
    )

    # Entry
    style.configure("TEntry", fieldbackground=theme["input_bg"], foreground=text_p)
