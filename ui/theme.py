"""Stravinsky's light theme: one palette, one font family, applied app-wide.

``apply(root)`` must run before any widget is built. It

* restyles every ttk widget (tabs, trees, combo boxes, scrollbars, ...) on top
  of the built-in "clam" theme, and
* sets option-database defaults for the classic Tk widgets (Button, Label,
  Listbox, Entry, Text, ...), so they look the same without per-widget code.
  Values passed explicitly to a widget still win over these defaults.

A few helpers style the exceptions: ``primary()`` for a panel's main action,
``segmented()`` for toggle buttons, ``placeholder()`` for hint text in an entry.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from .scaling import px

# -- palette (shared with the splash screen) ----------------------------------
BG = "#f7f8fa"            # window background
SURFACE = "#ffffff"       # panels, lists, editor
SURFACE_ALT = "#f1f3f6"   # recessed areas (board columns, headings)
BORDER = "#e5e7eb"
BORDER_STRONG = "#d1d5db"
FG = "#111827"
MUTED = "#6b7280"
SUBTLE = "#9ca3af"
ACCENT = "#3b74d9"        # blue
ACCENT_HOVER = "#2f66c9"
ACCENT_PRESSED = "#2556ad"
ACCENT_SOFT = "#e3edfc"   # selection background
ERROR = "#b42318"
SUCCESS = "#027a48"

ACCENT_DISABLED = "#b9cdf2"

# Secondary buttons are a soft grey fill: classic Tk buttons can't draw a
# coloured outline on Windows, so a tonal fill is what separates them.
BUTTON = "#eceef2"
BUTTON_HOVER = "#e2e5ea"
BUTTON_PRESSED = "#d6dae0"
GHOST_HOVER = "#eceef2"

# -- fonts --------------------------------------------------------------------
FAMILY = "Segoe UI"
FAMILY_SEMIBOLD = "Segoe UI Semibold"
H1 = (FAMILY_SEMIBOLD, 14)   # page titles
H2 = (FAMILY_SEMIBOLD, 11)   # panel headers
H3 = (FAMILY_SEMIBOLD, 10)   # sub-section headers

# Charts (also used by analysis.py through matplotlib's colour cycle).
CHART_COLORS = ["#3b74d9", "#e8964a", "#5fa883", "#8e7cc3", "#d9677f", "#9ca3af"]

# Hover colours for flat buttons, keyed by their resting background.
_HOVER = {BUTTON: BUTTON_HOVER, ACCENT: ACCENT_HOVER, BG: GHOST_HOVER}
_UNHOVER = {v: k for k, v in _HOVER.items()}


def apply(root: tk.Tk) -> None:
    _configure_fonts()
    _configure_classic_widgets(root)
    _configure_ttk(root)
    root.configure(background=BG)
    root.bind_all("<Enter>", _on_enter, add="+")
    root.bind_all("<Leave>", _on_leave, add="+")


# -- fonts --------------------------------------------------------------------
def _configure_fonts() -> None:
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                 "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"):
        try:
            tkfont.nametofont(name).configure(family=FAMILY)
        except tk.TclError:
            pass


# -- classic Tk widgets (option database) -------------------------------------
def _configure_classic_widgets(root: tk.Tk) -> None:
    opts = {
        "*Background": BG,
        "*Foreground": FG,
        "*highlightBackground": BG,
        "*disabledForeground": SUBTLE,
        "*insertBackground": FG,
        "*selectBackground": ACCENT_SOFT,
        "*selectForeground": FG,

        "*Button.background": BUTTON,
        "*Button.activeBackground": BUTTON_PRESSED,
        "*Button.relief": "flat",
        "*Button.overRelief": "flat",
        "*Button.borderWidth": 0,
        "*Button.highlightThickness": 0,
        "*Button.padX": px(8),
        "*Button.padY": px(4),
        "*Button.cursor": "hand2",

        "*Menubutton.background": BUTTON,
        "*Menubutton.activeBackground": BUTTON_HOVER,
        "*Menubutton.relief": "flat",
        "*Menubutton.borderWidth": 0,
        "*Menubutton.highlightThickness": 0,
        "*Menubutton.padX": px(8),
        "*Menubutton.padY": px(4),
        "*Menubutton.cursor": "hand2",

        "*Entry.background": SURFACE,
        "*Entry.relief": "flat",
        "*Entry.borderWidth": px(4),
        "*Entry.highlightThickness": 1,
        "*Entry.highlightBackground": BORDER_STRONG,
        "*Entry.highlightColor": ACCENT,
        "*Entry.readonlyBackground": SURFACE_ALT,
        "*Entry.disabledBackground": SURFACE_ALT,

        "*Spinbox.background": SURFACE,
        "*Spinbox.relief": "flat",
        "*Spinbox.borderWidth": px(3),
        "*Spinbox.highlightThickness": 1,
        "*Spinbox.highlightBackground": BORDER_STRONG,
        "*Spinbox.highlightColor": ACCENT,
        "*Spinbox.buttonBackground": BUTTON,
        "*Spinbox.buttonCursor": "hand2",

        "*Listbox.background": SURFACE,
        "*Listbox.relief": "flat",
        "*Listbox.borderWidth": 0,
        "*Listbox.highlightThickness": 1,
        "*Listbox.highlightBackground": BORDER,
        "*Listbox.highlightColor": BORDER_STRONG,
        "*Listbox.activeStyle": "none",

        "*Text.background": SURFACE,
        "*Text.relief": "flat",
        "*Text.borderWidth": 0,
        "*Text.highlightThickness": 1,
        "*Text.highlightBackground": BORDER,
        "*Text.highlightColor": BORDER_STRONG,
        "*Text.padX": px(10),
        "*Text.padY": px(8),
        "*Text.inactiveSelectBackground": "#d3e2fa",

        "*Radiobutton.activeBackground": BG,
        "*Radiobutton.selectColor": SURFACE,
        "*Radiobutton.highlightThickness": 0,
        "*Checkbutton.activeBackground": BG,
        "*Checkbutton.selectColor": SURFACE,
        "*Checkbutton.highlightThickness": 0,

        "*Menu.background": SURFACE,
        "*Menu.activeBackground": ACCENT_SOFT,
        "*Menu.activeForeground": FG,
        "*Menu.relief": "flat",
        "*Menu.activeBorderWidth": 0,

        "*Canvas.highlightThickness": 0,
        "*Panedwindow.sashRelief": "flat",
        "*Panedwindow.borderWidth": 0,

        # Drop-down list of ttk combo boxes.
        "*TCombobox*Listbox.background": SURFACE,
        "*TCombobox*Listbox.selectBackground": ACCENT_SOFT,
        "*TCombobox*Listbox.selectForeground": FG,
        "*TCombobox*Listbox.font": "TkDefaultFont",
        "*TCombobox*Listbox.highlightThickness": 0,
    }
    for pattern, value in opts.items():
        root.option_add(pattern, value)


# -- ttk widgets ----------------------------------------------------------------
def _flat_image(root: tk.Tk, fill: str, underline: str | None, size: int = 12, line: int = 3) -> tk.PhotoImage:
    img = tk.PhotoImage(master=root, width=size, height=size)
    img.put(fill, to=(0, 0, size, size))
    if underline:
        img.put(underline, to=(0, size - line, size, size))
    return img


def _configure_ttk(root: tk.Tk) -> None:
    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(
        ".", background=BG, foreground=FG, font="TkDefaultFont",
        bordercolor=BORDER, lightcolor=BG, darkcolor=BG, troughcolor=BG,
        focuscolor=ACCENT, selectbackground=ACCENT_SOFT, selectforeground=FG,
        fieldbackground=SURFACE, insertcolor=FG,
    )
    style.map(".", foreground=[("disabled", SUBTLE)])

    # Notebook: flat tabs, blue underline on the selected one.
    line = max(2, px(3))
    size = 4 * line
    images = {
        "normal": _flat_image(root, BG, None, size, line),
        "hover": _flat_image(root, BG, BORDER_STRONG, size, line),
        "selected": _flat_image(root, BG, ACCENT, size, line),
    }
    root._theme_images = images  # keep references alive
    style.element_create(
        "Flat.tab", "image", images["normal"],
        ("selected", images["selected"]), ("active", images["hover"]),
        border=(1, 1, 1, line), sticky="nsew",
    )
    style.layout("TNotebook.Tab", [
        ("Flat.tab", {"sticky": "nsew", "children": [
            ("Notebook.padding", {"side": "top", "sticky": "nsew", "children": [
                ("Notebook.label", {"side": "top", "sticky": ""}),
            ]}),
        ]}),
    ])
    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(px(8), px(6), px(8), 0))
    tab_padding = (px(14), px(7), px(14), px(9))
    style.configure("TNotebook.Tab", padding=tab_padding, foreground=MUTED)
    # clam enlarges and re-pads the selected tab, which shifts its label down;
    # keep every tab level.
    style.map("TNotebook.Tab", foreground=[("selected", FG), ("active", FG)],
              expand=[("selected", (0, 0, 0, 0))], padding=[("selected", tab_padding)])

    # Treeview: white, airy, soft-blue selection, flat headings.
    style.configure(
        "Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=FG,
        bordercolor=BORDER, lightcolor=SURFACE, darkcolor=SURFACE, borderwidth=1,
    )
    style.map("Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", FG)])
    style.configure(
        "Treeview.Heading", background=SURFACE_ALT, foreground=MUTED, relief="flat",
        bordercolor=BORDER, lightcolor=SURFACE_ALT, darkcolor=SURFACE_ALT,
        padding=(px(8), px(4)), font="TkDefaultFont",
    )
    style.map("Treeview.Heading", background=[("active", BUTTON_PRESSED)])

    # Inputs.
    field = dict(
        fieldbackground=SURFACE, background=SURFACE, foreground=FG, bordercolor=BORDER_STRONG,
        lightcolor=SURFACE, darkcolor=SURFACE, arrowcolor=MUTED, padding=(px(6), px(3)),
        arrowsize=px(12),
    )
    style.configure("TCombobox", **field)
    style.configure("TEntry", **field)
    focus_map = dict(
        bordercolor=[("focus", ACCENT), ("hover", SUBTLE)],
        lightcolor=[("focus", SURFACE)], darkcolor=[("focus", SURFACE)],
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", SURFACE), ("disabled", SURFACE_ALT)],
        background=[("active", SURFACE), ("pressed", SURFACE)],
        selectbackground=[("readonly", SURFACE)], selectforeground=[("readonly", FG)],
        arrowcolor=[("active", FG)], **focus_map,
    )
    style.map("TEntry", **focus_map)

    # Buttons (ttk ones; most of the app uses classic Tk buttons).
    style.configure(
        "TButton", background=SURFACE, bordercolor=BORDER_STRONG, lightcolor=SURFACE,
        darkcolor=SURFACE, padding=(px(10), px(4)), relief="flat",
    )
    style.map("TButton", background=[("pressed", BUTTON_PRESSED), ("active", BUTTON_HOVER)])

    # Scrollbars: thin, no arrows.
    for orient in ("Vertical", "Horizontal"):
        style.layout(f"{orient}.TScrollbar", [
            (f"{orient}.Scrollbar.trough", {"sticky": "nsew", "children": [
                (f"{orient}.Scrollbar.thumb", {"expand": "1", "sticky": "nsew"}),
            ]}),
        ])
    style.configure(
        "TScrollbar", background=BORDER_STRONG, troughcolor=BG, bordercolor=BG,
        lightcolor=BORDER_STRONG, darkcolor=BORDER_STRONG, arrowsize=px(9), gripcount=0,
    )
    style.map(
        "TScrollbar",
        background=[("pressed", MUTED), ("active", SUBTLE)],
        lightcolor=[("pressed", MUTED), ("active", SUBTLE)],
        darkcolor=[("pressed", MUTED), ("active", SUBTLE)],
    )

    for name in ("TFrame", "TLabel", "TRadiobutton", "TCheckbutton", "TPanedwindow"):
        style.configure(name, background=BG)


# -- hover for flat classic buttons -----------------------------------------------
def _hover_target(event):
    w = event.widget
    if not isinstance(w, (tk.Button, tk.Menubutton)):
        return None
    try:
        if str(w.cget("state")) == "disabled":
            return None
    except tk.TclError:
        return None
    return w


def _on_enter(event):
    w = _hover_target(event)
    if w is not None:
        bg = str(w.cget("background")).lower()
        if bg in _HOVER:
            w.configure(background=_HOVER[bg])


def _on_leave(event):
    w = event.widget
    if isinstance(w, (tk.Button, tk.Menubutton)):
        try:
            bg = str(w.cget("background")).lower()
        except tk.TclError:
            return
        if bg in _UNHOVER:
            w.configure(background=_UNHOVER[bg])


# -- helpers for individual widgets ------------------------------------------------
def primary(button: tk.Button) -> tk.Button:
    """Blue, filled button for the main action of a panel."""
    button.configure(
        background=ACCENT, foreground="#ffffff", activebackground=ACCENT_PRESSED,
        activeforeground="#ffffff", highlightbackground=ACCENT, highlightcolor=ACCENT_PRESSED,
        disabledforeground="#ffffff", font=(FAMILY_SEMIBOLD, tkfont.nametofont("TkDefaultFont").cget("size")),
    )
    button._theme_primary = True
    return button


def set_enabled(button: tk.Button, enabled: bool) -> None:
    """Enable/disable a button; a disabled primary button also fades its fill
    (Tk keeps the background unchanged when a button is disabled)."""
    button.configure(state="normal" if enabled else "disabled")
    if getattr(button, "_theme_primary", False):
        fill = ACCENT if enabled else ACCENT_DISABLED
        button.configure(background=fill, highlightbackground=fill, cursor="hand2" if enabled else "")


def ghost(button: tk.Button) -> tk.Button:
    """Borderless button that only shows a background on hover."""
    button.configure(background=BG, highlightthickness=0, activebackground=BUTTON_PRESSED)
    return button


def segmented(radio: tk.Radiobutton) -> tk.Radiobutton:
    """Style an ``indicatoron=False`` Radiobutton as one segment of a toggle."""
    radio.configure(
        relief="flat", offrelief="flat", borderwidth=0, background=BUTTON,
        selectcolor=ACCENT_SOFT, activebackground=BUTTON_HOVER, highlightthickness=0,
        pady=px(4), cursor="hand2",
    )
    return radio


def header(parent, text: str, font=H2, **pack) -> tk.Label:
    """Left-aligned section header, packed with sensible spacing."""
    label = tk.Label(parent, text=text, font=font, anchor="w")
    label.pack(**({"fill": "x", "pady": (px(6), px(4))} | pack))
    return label


def placeholder(entry: tk.Entry, text: str, variable: tk.Variable | None = None) -> None:
    """Grey hint shown inside an empty, unfocused entry (doesn't touch its value).
    Pass the entry's ``variable`` so the hint also reacts to programmatic changes."""
    hint = tk.Label(entry, text=text, fg=SUBTLE, bg=entry.cget("background"), cursor="xterm")
    hint.bind("<Button-1>", lambda e: entry.focus_set())

    def update(*_):
        if entry.get() or entry.focus_get() is entry:
            hint.place_forget()
        else:
            hint.place(x=0, rely=0.5, anchor="w")

    entry.bind("<FocusIn>", update, add="+")
    entry.bind("<FocusOut>", update, add="+")
    entry.bind("<KeyRelease>", update, add="+")
    if variable is not None:
        variable.trace_add("write", update)
    entry.after_idle(update)


def style_matplotlib() -> None:
    """Matching look for charts: Segoe UI, light axes, blue-first colours."""
    import matplotlib as mpl
    from cycler import cycler

    mpl.rcParams.update({
        "font.family": ["Segoe UI", "DejaVu Sans"],
        "font.size": 9,
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "axes.edgecolor": BORDER_STRONG,
        "axes.labelcolor": MUTED,
        "axes.titlecolor": FG,
        "axes.titleweight": "bold",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.prop_cycle": cycler(color=CHART_COLORS),
        "axes.axisbelow": True,
        "grid.color": "#eef0f4",
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "text.color": FG,
        "legend.frameon": False,
    })
