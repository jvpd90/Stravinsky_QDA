"""Center coding panel: the text widget itself, search, right-click
coding menu, fragment highlighting/hover tooltips, and memo editing.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from . import theme
from .scaling import px
from . import codebook_dialogs, widgets


class CodingPanel:
    def __init__(self, parent, app):
        """
        app must provide:
          - app.store: ProjectStore
          - app.current_doc
          - app.require_project() -> bool
          - app.on_fragment_created() -> refresh fragment list / excel preview
          - app.new_label(level, parent_label) -> create a label (codes panel)
        """
        self.app = app
        self.search_positions: list[tuple[str, str]] = []
        self.search_index = -1
        self.tooltip: tk.Toplevel | None = None
        self._hover_frag_ids: frozenset = frozenset()

        self.frame = tk.Frame(parent)
        self.frame.rowconfigure(1, weight=1)

        search_frame = tk.Frame(self.frame)
        search_frame.pack(fill="x", pady=(0, 5))
        self.search_var = tk.StringVar()
        self.search_var.trace("w", self.highlight_search)
        self.search_entry = tk.Entry(search_frame, textvariable=self.search_var, width=30)
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        theme.placeholder(self.search_entry, "Search…   Ctrl+F", self.search_var)
        tk.Button(search_frame, text="Prev", command=self.previous_search).pack(side="left", padx=2)
        tk.Button(search_frame, text="Next", command=self.next_search).pack(side="left", padx=2)
        tk.Button(search_frame, text="Clear", command=self.clear_search).pack(side="left", padx=2)
        self.search_count_var = tk.StringVar(value="")
        tk.Label(search_frame, textvariable=self.search_count_var, font=("Segoe UI", 8), fg=theme.MUTED).pack(side="left", padx=5)

        self.text = tk.Text(self.frame, wrap="word", font=("Segoe UI", 10))
        scrollbar = ttk.Scrollbar(self.frame, command=self.text.yview)
        self.text.config(yscrollcommand=scrollbar.set)
        self.text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.text.tag_config("hover_frag", background="#d0e0ff")
        self.text.bind("<Control-f>", self.focus_search)
        self.text.bind("<Button-3>", self.show_context_menu)
        self.text.bind("<Motion>", self.on_text_motion)
        self.text.bind("<Leave>", self.on_text_leave)
        self.text.bind("<Double-Button-1>", self.on_text_double_click)

        self.context_menu = tk.Menu(self.frame, tearoff=0)

    def grid(self, **kwargs):
        self.frame.grid(**kwargs)

    # -- document loading -----------------------------------------------------
    def load_text(self, content: str):
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", content)

    def clear(self):
        self.text.delete("1.0", tk.END)

    # -- fragment rendering ----------------------------------------------------
    def _fragment_color(self, fragment: dict) -> str:
        return self.app.store.fragment_color(fragment["id"])

    @staticmethod
    def _mix_colors(hex_colors: list[str]) -> str:
        """Average several #rrggbb colors so overlapping fragments get a
        blended background instead of one fragment's color silently
        hiding the other's."""
        r = g = b = 0
        for hexc in hex_colors:
            hexc = hexc.lstrip("#")
            r += int(hexc[0:2], 16)
            g += int(hexc[2:4], 16)
            b += int(hexc[4:6], 16)
        n = len(hex_colors)
        return f"#{r // n:02x}{g // n:02x}{b // n:02x}"

    def fragments_at_char(self, char_index: int) -> list[dict]:
        """All fragments of the current document covering a character
        offset. Used for hover/click hit-testing instead of Tk tag
        bindings, which fire ambiguously once fragments overlap."""
        if not self.app.current_doc:
            return []
        doc_id = self.app.current_doc["id"]
        return [
            f for f in self.app.store.fragments
            if f["document_id"] == doc_id and f["start"] <= char_index < f["end"]
        ]

    def fragments_in_range(self, start: int, end: int) -> list[dict]:
        """All fragments of the current document overlapping [start, end)."""
        if not self.app.current_doc:
            return []
        doc_id = self.app.current_doc["id"]
        return [
            f for f in self.app.store.fragments
            if f["document_id"] == doc_id and f["start"] < end and f["end"] > start
        ]

    def _fragment_label(self, fragment: dict) -> str:
        return f"{self.app.store.fragment_label_text(fragment['id'])}: {self.get_fragment_text(fragment)[:40]}"

    def _labels_by_level_text(self, fragment: dict, sep: str = "\n") -> str:
        """One "Level: label, label" line per level the fragment is coded on."""
        store = self.app.store
        by_level = store.labels_by_level(fragment["id"])
        lines = [
            f"{level['name']}: {', '.join(c['name'] for c in by_level[level['id']])}"
            for level, _ in store.ordered_levels() if level["id"] in by_level
        ]
        return sep.join(lines) if lines else "No code"

    def render_fragments(self) -> list[str]:
        """Re-tag the text widget for the current document's fragments.
        Returns preview rows for the fragments listbox.

        Fragments may overlap. Tk Text can only show one background color
        per character, so instead of tagging each fragment independently
        (where the last-added tag would simply hide the others), this
        splits the document into contiguous runs bounded by fragment
        start/end points and colors each run by blending the colors of
        every fragment covering it, underlining runs covered by more than
        one fragment as a visual cue that multiple codes apply there."""
        for tag in self.text.tag_names():
            if tag.startswith("run_"):
                self.text.tag_delete(tag)

        rows = []
        if not self.app.current_doc:
            return rows

        doc_fragments = [
            f for f in self.app.store.fragments if f["document_id"] == self.app.current_doc["id"]
        ]

        breakpoints = sorted({f["start"] for f in doc_fragments} | {f["end"] for f in doc_fragments})
        for b1, b2 in zip(breakpoints, breakpoints[1:]):
            covering = [f for f in doc_fragments if f["start"] <= b1 and f["end"] >= b2]
            if not covering:
                continue

            colors = [self._fragment_color(f) for f in covering]
            bg = colors[0] if len(colors) == 1 else self._mix_colors(colors)

            tag = f"run_{b1}_{b2}"
            self.text.tag_config(tag, background=bg, underline=len(covering) > 1)
            s = self.text.index(f"1.0+{b1}c")
            e = self.text.index(f"1.0+{b2}c")
            self.text.tag_add(tag, s, e)

        for f in doc_fragments:
            s = self.text.index(f"1.0+{f['start']}c")
            e = self.text.index(f"1.0+{f['end']}c")
            preview = self.text.get(s, e).strip().replace("\n", " ")[:50]
            rows.append(f"{f['id']} | {preview}")

        # run_* tags are (re)created above, which makes them more recently
        # created -- and so higher priority -- than hover_frag; without this
        # the hover highlight would be hidden behind fragment colors.
        self.text.tag_raise("hover_frag")

        return rows

    def get_fragment_text(self, fragment: dict) -> str:
        return self.text.get(f"1.0+{fragment['start']}c", f"1.0+{fragment['end']}c").strip()

    # -- coding -------------------------------------------------------------
    def selected_char_range(self) -> tuple[int, int] | None:
        sel = self.text.tag_ranges(tk.SEL)
        if not sel:
            return None
        # Text.count() returns None (not (0,)) when the count is exactly
        # zero, e.g. index1 == "1.0" -- guard against that instead of
        # assuming a truthy result.
        start_result = self.text.count("1.0", sel[0], "chars")
        end_result = self.text.count("1.0", sel[1], "chars")
        start = start_result[0] if start_result else 0
        end = end_result[0] if end_result else 0
        return start, end

    def assign_code(self, code_selection_fn):
        """code_selection_fn() should return a chosen code dict or None."""
        char_range = self.selected_char_range()
        if not char_range:
            messagebox.showwarning("Select", "Select text first")
            return

        code = code_selection_fn()
        if not code:
            return

        start, end = char_range
        frag = self.app.store.add_fragment(self.app.current_doc["id"], start, end)
        self.app.store.save_fragments()
        self.app.store.link_code(frag["id"], code["id"])
        self.app.store.save_links()
        self.app.on_fragment_created()

    def assign_code_id(self, code_id: str):
        code = self.app.store.label(code_id)
        if code:
            self.assign_code(lambda: code)
            messagebox.showinfo("Assigned", f"'{self.app.store.label_path(code)}' assigned to selection")

    def code_selection_with_levels(self):
        """Code the selection with one label per level, via the per-level
        dialog. Creates a single fragment carrying all chosen labels."""
        char_range = self.selected_char_range()
        if not char_range:
            messagebox.showwarning("Select", "Select text first")
            return
        label_ids = codebook_dialogs.assign_labels_dialog(
            self.frame, self.app.store, "Code Selection", [], self.app.new_label,
        )
        if label_ids is None:
            return
        if not label_ids:
            messagebox.showwarning("No label", "Choose a label on at least one level.")
            return
        start, end = char_range
        frag = self.app.store.add_fragment(self.app.current_doc["id"], start, end)
        self.app.store.save_fragments()
        self.app.store.set_fragment_labels(frag["id"], label_ids)
        self.app.store.save_links()
        self.app.on_fragment_created()

    def edit_fragment_labels(self, fragment: dict):
        current = [c["id"] for c in self.app.store.codes_for_fragment(fragment["id"])]
        label_ids = codebook_dialogs.assign_labels_dialog(
            self.frame, self.app.store, "Edit Fragment Labels", current, self.app.new_label,
        )
        if label_ids is None:
            return
        if not label_ids:
            messagebox.showwarning("No label", "Choose a label on at least one level, or use Uncode Fragment.")
            return
        self.app.store.set_fragment_labels(fragment["id"], label_ids)
        self.app.store.save_links()
        self.app.on_fragment_created()

    def _add_quick_assign_menus(self, menu: tk.Menu):
        """One cascade per level listing its labels (sub-levels' labels
        shown with their parent path). Picking one codes the selection
        with that label and its ancestors."""
        store = self.app.store
        for level, depth in store.ordered_levels():
            labels = store.labels_of_level(level["id"])
            submenu = tk.Menu(menu, tearoff=0)
            for label in sorted(labels, key=lambda c: store.label_path(c).lower()):
                submenu.add_command(
                    label=store.label_path(label),
                    command=lambda code_id=label["id"]: self.assign_code_id(code_id),
                )
            if not labels:
                submenu.add_command(label="(no labels)", state="disabled")
            menu.add_cascade(label=("    " * depth) + level["name"], menu=submenu)

    # -- search ----------------------------------------------------------------
    def focus_search(self, event=None):
        self.search_entry.focus()
        self.search_entry.select_range(0, tk.END)
        return "break"

    def highlight_search(self, *args):
        self.text.tag_remove("search", "1.0", tk.END)
        self.text.tag_remove("search_current", "1.0", tk.END)
        self.search_positions = []
        self.search_index = -1

        term = self.search_var.get()
        if not term:
            self.search_count_var.set("")
            return

        self.text.tag_config("search", background="yellow", foreground="black")
        self.text.tag_config("search_current", background="orange", foreground="black")

        start_pos = "1.0"
        while True:
            pos = self.text.search(term, start_pos, nocase=True, stopindex=tk.END)
            if not pos:
                break
            end_pos = f"{pos}+{len(term)}c"
            self.text.tag_add("search", pos, end_pos)
            self.search_positions.append((pos, end_pos))
            start_pos = end_pos

        if self.search_positions:
            self.search_index = 0
            self.goto_search_result(self.search_index)
        else:
            self.search_count_var.set("0/0")

    def goto_search_result(self, index):
        if not self.search_positions:
            return
        index = index % len(self.search_positions)
        self.search_index = index
        pos, end_pos = self.search_positions[index]
        self.text.tag_remove("search_current", "1.0", tk.END)
        self.text.tag_add("search_current", pos, end_pos)
        self.text.see(pos)
        self.search_count_var.set(f"{index + 1}/{len(self.search_positions)}")

    def next_search(self):
        if not self.search_positions:
            return
        self.goto_search_result((self.search_index + 1) % len(self.search_positions))

    def previous_search(self):
        if not self.search_positions:
            return
        self.goto_search_result((self.search_index - 1) % len(self.search_positions))

    def clear_search(self):
        self.search_var.set("")
        self.text.tag_remove("search", "1.0", tk.END)
        self.text.tag_remove("search_current", "1.0", tk.END)
        self.search_positions = []
        self.search_index = -1
        self.search_count_var.set("")

    # -- right-click coding menu ---------------------------------------------------
    def show_context_menu(self, event):
        if not self.app.store.path:
            return

        try:
            has_selection = bool(self.text.tag_ranges(tk.SEL))
        except Exception:
            has_selection = False

        # Fragments that can be uncoded from here: those overlapping the
        # selection, or -- with no selection -- those under the cursor.
        if has_selection:
            start, end = self.selected_char_range()
            uncodable = self.fragments_in_range(start, end)
        else:
            index = self.text.index(f"@{event.x},{event.y}")
            uncodable = self.fragments_at_char(self._char_offset(index))

        if not has_selection and not uncodable:
            return

        self.context_menu.delete(0, tk.END)
        if has_selection:
            self.context_menu.add_command(label="Code Selection (all levels)…", command=self.code_selection_with_levels)
            self.context_menu.add_separator()
            self.context_menu.add_command(label="Quick assign one label:", state="disabled")
            self._add_quick_assign_menus(self.context_menu)

            self.context_menu.add_separator()
            self.context_menu.add_command(label="Edit Memo for Selection", command=self.edit_memo_for_selection)
            self.context_menu.add_command(label="Create New Label...", command=self.app.create_code_from_menu)

        if uncodable:
            if has_selection:
                self.context_menu.add_separator()
            self.context_menu.add_command(label="Edit Fragment Labels", state="disabled")
            for f in uncodable:
                self.context_menu.add_command(
                    label=f"  {self._fragment_label(f)}",
                    command=lambda frag=f: self.edit_fragment_labels(frag),
                )
            self.context_menu.add_separator()
            self.context_menu.add_command(label="Uncode Fragment", state="disabled")
            for f in uncodable:
                self.context_menu.add_command(
                    label=f"  {self._fragment_label(f)}",
                    command=lambda frag=f: self.app.uncode_fragment(frag),
                )

        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    # -- hover tooltips -------------------------------------------------------------
    def _char_offset(self, index) -> int:
        result = self.text.count("1.0", index, "chars")
        return result[0] if result else 0

    def on_text_motion(self, event):
        index = self.text.index(f"@{event.x},{event.y}")
        char = self._char_offset(index)
        frags = self.fragments_at_char(char)
        frag_ids = frozenset(f["id"] for f in frags)

        if frag_ids == self._hover_frag_ids:
            return
        self._hover_frag_ids = frag_ids

        self.text.tag_remove("hover_frag", "1.0", tk.END)
        if not frags:
            self.hide_tooltip()
            return

        for f in frags:
            s = self.text.index(f"1.0+{f['start']}c")
            e = self.text.index(f"1.0+{f['end']}c")
            self.text.tag_add("hover_frag", s, e)

        blocks = []
        for f in frags:
            memo = self.app.store.memo_for(f["id"])
            memo_text = memo["content"] if memo and memo.get("content", "").strip() else "No memo for this fragment"
            blocks.append(f"{self._labels_by_level_text(f)}\nMemo: {memo_text}")
        self.show_tooltip(event.x_root, event.y_root, "\n---\n".join(blocks))

    def on_text_leave(self, event):
        self.reset_hover()

    def reset_hover(self):
        """Clear hover highlight/tooltip, e.g. after the hovered fragment
        was uncoded and no longer exists."""
        self._hover_frag_ids = frozenset()
        self.text.tag_remove("hover_frag", "1.0", tk.END)
        self.hide_tooltip()

    def show_tooltip(self, x, y, text):
        self.hide_tooltip()
        self.tooltip = tk.Toplevel(self.frame)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.wm_geometry(f"+{x + 10}+{y + 10}")
        tk.Label(
            self.tooltip, text=text, background=theme.SURFACE, relief="flat", highlightthickness=1, highlightbackground=theme.BORDER_STRONG,
            borderwidth=1, font=("Segoe UI", 9), wraplength=px(300), justify="left", padx=5, pady=3,
        ).pack()
        self.tooltip.after(3000, self.hide_tooltip)

    def hide_tooltip(self):
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None

    # -- memos ------------------------------------------------------------------

    def on_text_double_click(self, event):
        """Primary path: double-click directly on coded (highlighted) text
        to edit its memo. Looks up fragments by character offset rather
        than by Tk tag, so it works whether zero, one, or several
        fragments overlap at that point."""
        index = self.text.index(f"@{event.x},{event.y}")
        char = self._char_offset(index)
        frags = self.fragments_at_char(char)
        if not frags:
            return
        if len(frags) == 1:
            self.edit_memo_for_fragment(frags[0])
        else:
            self.choose_fragment_for_memo(frags)

    def choose_fragment_for_memo(self, frags: list[dict]):
        """Several fragments overlap at the clicked point -- let the user
        pick which one to edit a memo for."""
        menu = tk.Menu(self.frame, tearoff=0)
        for f in frags:
            menu.add_command(
                label=self._fragment_label(f),
                command=lambda frag=f: self.edit_memo_for_fragment(frag),
            )
        x = self.frame.winfo_pointerx()
        y = self.frame.winfo_pointery()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def edit_memo_for_fragment(self, fragment: dict):
        existing = self.app.store.memo_for(fragment["id"])
        current_text = existing["content"] if existing else ""

        memo_text = simpledialog.askstring(
            "Edit Memo", "Enter memo for this fragment:", initialvalue=current_text, parent=self.frame
        )
        if memo_text is not None:
            self.app.store.set_memo(fragment["id"], scope="fragment", content=memo_text)
            self.app.store.save_memos()
            messagebox.showinfo("Saved", "Memo saved successfully")

    def edit_memo_for_selection(self):
        """Fallback path from the right-click menu, for when the user has
        text selected rather than clicking directly on a highlighted
        fragment. Since a double-click on highlighted text is now the
        recommended way to edit a memo (see on_text_double_click), this is
        mainly useful right after coding a new selection, before the
        highlight has been re-rendered, or for older projects where the
        selection may not perfectly match the stored fragment bounds.
        """
        char_range = self.selected_char_range()
        if not char_range:
            messagebox.showwarning("No Selection", "Please select text first")
            return
        start, end = char_range

        exact = self.app.store.find_fragment(self.app.current_doc["id"], start, end)
        fragment = exact or self.app.store.find_fragment_at(self.app.current_doc["id"], start, end)
        if not fragment:
            messagebox.showwarning("No Fragment", "This text is not coded yet. Code it first to add a memo.")
            return

        if not exact:
            frag_preview = self.get_fragment_text(fragment)[:60]
            proceed = messagebox.askyesno(
                "Approximate Match",
                "Your selection doesn't exactly match a coded fragment.\n\n"
                f"Closest coded fragment: \u201c{frag_preview}\u2026\u201d\n\n"
                "Add the memo to this fragment?",
            )
            if not proceed:
                return

        self.edit_memo_for_fragment(fragment)

    def show_fragment_details(self, fragment: dict):
        dialog = tk.Toplevel(self.frame)
        dialog.title(f"Fragment {fragment['id']}")
        dialog.transient(self.frame)
        dialog.grab_set()

        codes_text = self._labels_by_level_text(fragment, sep="  |  ")

        memo = self.app.store.memo_for(fragment["id"])
        memo_text = memo["content"].strip() if memo and memo.get("content") else "No memo"

        doc_title = self.app.current_doc["title"] if self.app.current_doc else "Unknown"

        tk.Label(dialog, text=f"Fragment ID: {fragment['id']}", font=theme.H2).pack(anchor="w", padx=12, pady=(10, 2))
        tk.Label(dialog, text=f"Document: {doc_title}", font=("Segoe UI", 10), wraplength=px(500), justify="left").pack(anchor="w", padx=12)
        tk.Label(dialog, text=f"Labels: {codes_text}", font=("Segoe UI", 10), wraplength=px(500), justify="left").pack(anchor="w", padx=12, pady=(4, 8))
        tk.Label(dialog, text=f"Memo: {memo_text}", font=("Segoe UI", 10), wraplength=px(500), justify="left").pack(anchor="w", padx=12, pady=(0, 8))

        tk.Label(dialog, text="Full Fragment Text:", font=theme.H3).pack(anchor="w", padx=12)
        text_frame = tk.Frame(dialog)
        text_frame.pack(fill="both", expand=True, padx=12, pady=(4, 8))

        fragment_text_widget = tk.Text(text_frame, wrap="word", font=("Segoe UI", 10), height=12)
        fragment_text_widget.insert("1.0", self.get_fragment_text(fragment))
        fragment_text_widget.config(state="disabled")
        fragment_text_widget.pack(side="left", fill="both", expand=True)

        from tkinter import ttk
        scrollbar = ttk.Scrollbar(text_frame, command=fragment_text_widget.yview)
        scrollbar.pack(side="right", fill="y")
        fragment_text_widget.config(yscrollcommand=scrollbar.set)

        tk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=(0, 12))

        widgets.center_dialog(dialog, self.frame, width=520, height=420)
