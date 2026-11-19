"""Shared layout helpers that keep long content (file names, titles,
codes, field names) reachable instead of silently clipped.

There was no single standard for this across the app: some Treeview
columns and Listboxes had hardcoded pixel/char widths with no scrollbar
to reach anything past them, and some dialogs never resized to fit their
own content. These two helpers are meant to be the one place every
panel/dialog goes through for that, so growing/scrolling behaves the
same everywhere.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from tkinter.font import Font

from .scaling import px
from . import theme
from .theme import ACCENT_SOFT

TREE_INDENT_PX = 20


def _tree_font(tree: ttk.Treeview) -> Font:
    style = ttk.Style()
    font_spec = style.lookup("Treeview", "font") or tree.cget("font") or "TkDefaultFont"
    try:
        return Font(font=font_spec)
    except tk.TclError:
        return Font()


def _iter_all_items(tree: ttk.Treeview, parent: str = ""):
    for iid in tree.get_children(parent):
        yield iid
        yield from _iter_all_items(tree, iid)


def _item_depth(tree: ttk.Treeview, iid: str) -> int:
    depth = 0
    parent = tree.parent(iid)
    while parent:
        depth += 1
        parent = tree.parent(parent)
    return depth


def autosize_treeview_columns(tree: ttk.Treeview, min_width: int = 80, max_width: int = 480, padding: int = 28,
                              stretch: bool = True) -> None:
    """Widen every visible column (including the '#0' tree column, when
    shown) to fit its widest current heading/cell text, up to max_width,
    and mark columns stretchable so they keep using extra space when the
    window grows. Call this after populating/refreshing a tree's rows --
    it does not re-run automatically on data changes. With stretch=False
    columns keep their fitted width and a wide table scrolls sideways
    instead of squeezing every column."""
    font = _tree_font(tree)
    try:  # headings use their own (usually larger) font
        heading_font = Font(font=ttk.Style().lookup("Treeview.Heading", "font") or "TkHeadingFont")
    except tk.TclError:
        heading_font = font
    # cget("show") returns a tuple of Tcl objects (not a plain string), so
    # normalize each element to str before checking membership.
    show_flags = {str(flag) for flag in tree.cget("show")}
    has_tree_col = "tree" in show_flags
    columns = (["#0"] if has_tree_col else []) + list(tree.cget("columns") or ())
    items = list(_iter_all_items(tree))

    for col in columns:
        widest = heading_font.measure(str(tree.heading(col, "text"))) + px(8)
        for iid in items:
            if col == "#0":
                text_width = font.measure(str(tree.item(iid, "text")))
                text_width += _item_depth(tree, iid) * px(TREE_INDENT_PX)
            else:
                text_width = font.measure(str(tree.set(iid, col)))
            widest = max(widest, text_width)
        width = max(px(min_width), min(widest + px(padding), px(max_width)))
        tree.column(col, width=width, minwidth=min(width, px(min_width)), stretch=stretch)


def autosize_listbox(listbox: tk.Listbox, min_chars: int = 20, max_chars: int = 70) -> None:
    """Set a Listbox's character width to fit its widest current item, up
    to max_chars, so the box itself is wide enough to show entries without
    needing a horizontal scroll. Call after populating the listbox."""
    items = listbox.get(0, tk.END)
    widest = max((len(str(item)) for item in items), default=min_chars)
    listbox.configure(width=max(min_chars, min(widest + 2, max_chars)))


class WrappingList:
    """A vertically-stacked, click-to-select list of wrapping-text rows.

    A plain tk.Listbox can only grow a dialog *wider* to show a long
    item (or hide it behind a fixed width). This instead wraps each row
    within a fixed width, so a long item grows that row -- and so the
    whole list and its dialog -- *taller* instead. Meant for short lists
    (attribute/category names, not thousands of rows): there's no
    internal scrollbar, so after insert/delete the caller should
    re-center the containing dialog (via `center_dialog`) so it can
    resize to fit.

    Supports just the bit of the Listbox API callers here rely on:
    insert(_, text), delete(index), get(index), curselection(), plus
    clear() to drop every row at once.
    """

    def __init__(self, parent, wraplength: int = 320, font=("Segoe UI", 10)):
        self.frame = tk.Frame(parent)
        self.wraplength = px(wraplength)
        self.font = font
        self._labels: list[tk.Label] = []
        self._items: list[str] = []
        self._selected: int | None = None

    def insert(self, _index, text: str) -> None:
        idx = len(self._items)
        label = tk.Label(
            self.frame, text=text, font=self.font, wraplength=self.wraplength,
            justify="left", anchor="w", bg="white", padx=6, pady=4,
        )
        label.pack(fill="x")
        label.bind("<Button-1>", lambda e, i=idx: self.select(i))
        self._labels.append(label)
        self._items.append(text)

    def select(self, index: int) -> None:
        for i, label in enumerate(self._labels):
            label.configure(bg=ACCENT_SOFT if i == index else "white")
        self._selected = index

    def curselection(self) -> tuple[int, ...]:
        return (self._selected,) if self._selected is not None else ()

    def get(self, index: int) -> str:
        return self._items[index]

    def clear(self) -> None:
        for label in self._labels:
            label.destroy()
        self._labels.clear()
        self._items.clear()
        self._selected = None

    def delete(self, index: int) -> None:
        self._labels.pop(index).destroy()
        self._items.pop(index)
        self._selected = None
        for i, label in enumerate(self._labels):
            label.unbind("<Button-1>")
            label.bind("<Button-1>", lambda e, i=i: self.select(i))


def center_dialog(dialog: tk.Toplevel, parent, width: int | None = None, height: int | None = None) -> None:
    """Center `dialog` over the application's main window.

    Call this last, after every widget has been added to the dialog. With
    no width/height given, the dialog is sized to fit its own content
    (via winfo_reqwidth/reqheight) so nothing already placed in it gets
    clipped by a stale fixed size; pass explicit width/height to keep a
    dialog's existing fixed size (e.g. one built around a scrollable text
    area that's meant to show only a portion of its content) while still
    centering it.
    """
    dialog.update_idletasks()
    width = px(width) if width is not None else None
    height = px(height) if height is not None else None
    if width is None:
        width = dialog.winfo_reqwidth()
    if height is None:
        height = dialog.winfo_reqheight()

    root = parent.winfo_toplevel()
    x = root.winfo_rootx() + (root.winfo_width() - width) // 2
    y = root.winfo_rooty() + (root.winfo_height() - height) // 2
    dialog.geometry(f"{width}x{height}+{x}+{y}")


def autohide_scrollbar(bar: ttk.Scrollbar):
    """Scroll command that hides `bar` while everything fits."""
    def set_(first, last):
        if float(first) <= 0.0 and float(last) >= 1.0:
            bar.grid_remove()
        else:
            bar.grid()
        bar.set(first, last)
    return set_


def add_scrollbars(frame: tk.Widget, widget, vertical: bool = True, horizontal: bool = False) -> None:
    """Grid `widget` into `frame` with vertical and/or horizontal
    scrollbars wired to it, so content wider or taller than the visible
    area stays reachable instead of being cut off with no way to see it.

    `frame` should otherwise be empty -- its own placement in its parent
    (pack/grid) is left to the caller.
    """
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    widget.grid(row=0, column=0, sticky="nsew")

    if vertical:
        vbar = ttk.Scrollbar(frame, orient="vertical", command=widget.yview)
        vbar.grid(row=0, column=1, sticky="ns")
        widget.configure(yscrollcommand=autohide_scrollbar(vbar))

    if horizontal:
        hbar = ttk.Scrollbar(frame, orient="horizontal", command=widget.xview)
        hbar.grid(row=1, column=0, sticky="ew")
        widget.configure(xscrollcommand=autohide_scrollbar(hbar))


class ScrollableFrame:
    """A vertically scrolling column: put widgets in `.inner`, place `.outer`.
    The mouse wheel scrolls it while the pointer is over it (lists and
    tables inside keep scrolling themselves)."""

    def __init__(self, parent, width: int):
        self.outer = tk.Frame(parent)
        self.outer.rowconfigure(0, weight=1)
        self.outer.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self.outer, width=width, highlightthickness=0, background=theme.BG)
        bar = ttk.Scrollbar(self.outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=autohide_scrollbar(bar))
        self.canvas.grid(row=0, column=0, sticky="nsew")
        bar.grid(row=0, column=1, sticky="ns")

        self.inner = tk.Frame(self.canvas)
        window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(window, width=e.width))
        self.outer.bind("<Enter>", lambda e: self.outer.bind_all("<MouseWheel>", self._on_wheel))
        self.outer.bind("<Leave>", lambda e: self.outer.unbind_all("<MouseWheel>"))

    def _on_wheel(self, event):
        if isinstance(event.widget, (tk.Listbox, tk.Text, ttk.Treeview)):
            return
        if self.inner.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")


class Section:
    """A titled block whose body folds away when the title is clicked.
    Visibility of whole sections is managed by `SectionStack`."""

    def __init__(self, parent, title: str, expanded: bool = True):
        self.title = title
        self.frame = tk.Frame(parent)
        self.header = tk.Label(self.frame, font=theme.H3, anchor="w", cursor="hand2")
        self.header.pack(fill="x", pady=(px(10), px(4)))
        self.header.bind("<Button-1>", lambda e: self.toggle())
        self.body = tk.Frame(self.frame)
        self.expanded = not expanded
        self.toggle()

    def toggle(self) -> None:
        self.expanded = not self.expanded
        self.header.configure(text=("▾  " if self.expanded else "▸  ") + self.title)
        if self.expanded:
            self.body.pack(fill="both", expand=True)
        else:
            self.body.pack_forget()


class SectionStack:
    """Sections stacked in a fixed order, any of which can be hidden."""

    def __init__(self, parent):
        self.parent = parent
        self.sections: list[Section] = []
        self.visible: dict[Section, bool] = {}

    def add(self, title: str, expanded: bool = True) -> Section:
        section = Section(self.parent, title, expanded)
        self.sections.append(section)
        self.visible[section] = True
        self._repack()
        return section

    def set_visible(self, section: Section, visible: bool) -> None:
        if self.visible.get(section) != visible:
            self.visible[section] = visible
            self._repack()

    def _repack(self) -> None:
        for section in self.sections:
            section.frame.pack_forget()
        for section in self.sections:
            if self.visible[section]:
                section.frame.pack(fill="x")
