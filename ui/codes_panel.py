"""Codebook panel: shows the project's levels and labels as a Treeview and
owns label CRUD plus codebook import/export. Depends on `app` for shared
state (store, callbacks into the text panel) but keeps that dependency
narrow and explicit via the constructor arguments.

Two views of the same codebook:
- "Tree": one section per independent / top level; labels nest under
  their parent labels across hierarchical levels (Tema › Subtema › ...).
- "By level": one flat section per level, each label showing its parent.
"""
from __future__ import annotations

import tkinter as tk
from collections import Counter
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import theme
from .scaling import px
from .. import codebook_io
from ..colors import DEFAULT_CODE_COLOR
from . import codebook_dialogs, dialogs, excel_actions, widgets

LEVEL_PREFIX = "level::"
ORPHANS_PREFIX = "orphans::"
TREE_VIEW = "tree"
LEVEL_VIEW = "levels"


class CodesPanel:
    def __init__(self, parent, app):
        """
        app must provide:
          - app.store: ProjectStore
          - app.require_project() -> bool (shows warning + returns False if none)
          - app.on_codebook_changed() -> re-render everything showing labels
        """
        self.app = app
        self.code_images: dict[str, object] = {}

        self.frame = tk.Frame(parent)

        tk.Label(self.frame, text="Codebook", font=theme.H2, anchor="w").pack(fill="x", pady=(0, 2))

        button_frame = tk.Frame(self.frame)
        button_frame.pack(fill="x", pady=2)
        theme.primary(tk.Button(button_frame, text="New Label", command=self.new_label)).pack(
            side="left", fill="x", expand=True, padx=(0, 2)
        )
        tk.Button(button_frame, text="Levels…", command=self.manage_levels).pack(
            side="left", fill="x", expand=True, padx=2
        )
        excel_button = tk.Menubutton(button_frame, text="Excel ▾")
        excel_menu = tk.Menu(excel_button, tearoff=0)
        excel_menu.add_command(label="Export Fragments to Excel…", command=lambda: excel_actions.export_fragments(app))
        excel_menu.add_command(
            label="Update Fragments from Excel (by Fragment ID)…", command=lambda: excel_actions.import_fragments(app),
        )
        excel_menu.add_command(
            label="Append New Fragments from Excel (rows without ID)…",
            command=lambda: excel_actions.append_fragments(app),
        )
        excel_menu.add_separator()
        excel_menu.add_command(label="Export Codebook to Excel…", command=self.export_codebook)
        excel_button.config(menu=excel_menu)
        excel_button.pack(side="left", fill="x", expand=True, padx=(2, 0))

        self.structure_label = tk.Label(self.frame, text="", font=("Segoe UI", 8), fg=theme.MUTED, wraplength=px(220))
        self.structure_label.pack(fill="x")
        self.structure_label.bind("<Configure>", lambda e: self.structure_label.config(wraplength=max(e.width - 4, 80)))

        view_frame = tk.Frame(self.frame)
        view_frame.pack(fill="x", pady=(4, 2))
        self.view_var = tk.StringVar(value=TREE_VIEW)
        for value, text in ((TREE_VIEW, "Tree"), (LEVEL_VIEW, "By level")):
            theme.segmented(tk.Radiobutton(
                view_frame, text=text, value=value, variable=self.view_var, indicatoron=False,
                command=self.populate, padx=8,
            )).pack(side="left", fill="x", expand=True)

        filter_frame = tk.Frame(self.frame)
        filter_frame.pack(fill="x", pady=(2, 2))
        tk.Label(filter_frame, text="Filter:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self.populate())
        tk.Entry(filter_frame, textvariable=self.filter_var).pack(side="left", fill="x", expand=True, padx=(4, 0))

        codes_frame = tk.Frame(self.frame)
        codes_frame.pack(fill="both", expand=True, pady=2)

        # Ctrl/Shift-click selects several labels; the right-click menu and
        # the Delete key then act on all of them.
        self.tree = ttk.Treeview(
            codes_frame, height=10, columns=("info", "uses"), show="tree headings", selectmode="extended",
        )
        self.tree.heading("#0", text="Label")
        self.tree.heading("uses", text="#")
        self.tree.column("uses", width=px(40), anchor="center", stretch=False)
        widgets.add_scrollbars(codes_frame, self.tree, horizontal=True)

        style = ttk.Style()
        style.configure("Treeview", font=("Segoe UI", 10))
        self.tree.tag_configure("level", font=theme.H3, background=theme.SURFACE_ALT)
        self.tree.tag_configure("group", font=("Segoe UI", 9, "italic"), foreground=theme.MUTED)

        self.tree.bind("<Button-3>", self.on_right_click)
        self.tree.bind("<Double-Button-1>", self.on_double_click)
        self.tree.bind("<Delete>", self.delete_selected)

    def grid(self, **kwargs):
        self.frame.grid(**kwargs)

    # -- rendering -----------------------------------------------------
    def create_color_image(self, color_hex, size=12):
        size = px(size)
        try:
            from PIL import Image, ImageDraw, ImageTk

            img = Image.new("RGB", (size, size), color_hex)
            draw = ImageDraw.Draw(img)
            draw.rectangle([0, 0, size - 1, size - 1], outline="black", width=1)
            return ImageTk.PhotoImage(img)
        except ImportError:
            return None

    def _image(self, label: dict):
        color = label.get("color") or DEFAULT_CODE_COLOR
        if color not in self.code_images:
            self.code_images[color] = self.create_color_image(color)
        return self.code_images[color]

    def clear(self):
        self.tree.delete(*self.tree.get_children())

    def structure_text(self) -> str:
        """e.g. "Tema › Subtema  ·  Sentimento" -- hierarchies joined by ›,
        independent blocks separated by ·."""
        store = self.app.store
        blocks: list[list[str]] = []
        for level, depth in store.ordered_levels():
            if depth == 0:
                blocks.append([])
            blocks[-1].append(level["name"])
        return "  ·  ".join(" › ".join(block) for block in blocks)

    def populate(self):
        selected = self.tree.selection()  # kept across the redraw, for the next action
        self._populate()
        self.tree.selection_set([iid for iid in selected if self.tree.exists(iid)])

    def _populate(self):
        self.clear()
        store = self.app.store
        if not store.path:
            self.structure_label.config(text="")
            return
        self.structure_label.config(text=self.structure_text() or "No levels yet — click Levels…")
        self._usage = Counter(link["code_id"] for link in store.links)
        term = self.filter_var.get().strip().lower()
        self._visible = self._visible_label_ids(term) if term else None

        if self.view_var.get() == TREE_VIEW:
            self.tree.heading("info", text="Level")
            self._populate_tree()
        else:
            self.tree.heading("info", text="Parent")
            self._populate_by_level()
        widgets.autosize_treeview_columns(self.tree, min_width=40)

    def _visible_label_ids(self, term: str) -> set[str]:
        """Labels matching the filter, plus their ancestors so a match deep
        in the tree stays reachable."""
        store = self.app.store
        visible = set()
        for label in store.codes:
            if term in label["name"].lower():
                visible.add(label["id"])
                visible.update(a["id"] for a in store.label_ancestors(label))
        return visible

    def _shown(self, label: dict) -> bool:
        return self._visible is None or label["id"] in self._visible

    def _sorted(self, labels: list[dict]) -> list[dict]:
        order = {lv["id"]: i for i, (lv, _) in enumerate(self.app.store.ordered_levels())}
        return sorted(labels, key=lambda c: (order.get(c.get("level_id"), 99), c["name"].lower()))

    def _insert_label(self, parent_iid: str, label: dict, info: str, recurse: bool):
        store = self.app.store
        self.tree.insert(
            parent_iid, "end", iid=label["id"], text="  " + label["name"], image=self._image(label),
            values=(info, self._usage.get(label["id"], 0)), open=True,
        )
        if recurse:
            for child in self._sorted(store.child_codes_of(label)):
                if self._shown(child):
                    level = store.level(child.get("level_id"))
                    self._insert_label(label["id"], child, level["name"] if level else "", recurse=True)

    def _insert_level_header(self, level: dict, info: str):
        self.tree.insert(
            "", "end", iid=LEVEL_PREFIX + level["id"], text=level["name"].upper(),
            values=(info, ""), tags=("level",), open=True,
        )

    def _populate_tree(self):
        store = self.app.store
        known_ids = {c["id"] for c in store.codes}
        ordered = store.ordered_levels()
        for root, depth in ordered:
            if depth:
                continue
            header = LEVEL_PREFIX + root["id"]
            self._insert_level_header(root, "")
            for label in self._sorted(store.labels_of_level(root["id"])):
                if self._shown(label):
                    self._insert_label(header, label, "", recurse=True)
            # Sub-level labels with no (or a missing) parent label would
            # otherwise be invisible in this view.
            for level in [lv for lv, _ in ordered if root in store.level_ancestors(lv)]:
                orphans = [
                    c for c in store.labels_of_level(level["id"])
                    if c.get("parent_id") not in known_ids and self._shown(c)
                ]
                if not orphans:
                    continue
                group = ORPHANS_PREFIX + level["id"]
                self.tree.insert(header, "end", iid=group, text=f"{level['name']} — no parent", tags=("group",), open=True)
                for label in self._sorted(orphans):
                    self._insert_label(group, label, level["name"], recurse=True)

    def _populate_by_level(self):
        store = self.app.store
        for level, _ in store.ordered_levels():
            parent_level = store.level(level.get("parent_level_id"))
            self._insert_level_header(level, f"↳ {parent_level['name']}" if parent_level else "independent")
            for label in sorted(store.labels_of_level(level["id"]), key=lambda c: c["name"].lower()):
                if self._shown(label):
                    parent = store.label(label.get("parent_id"))
                    self._insert_label(
                        LEVEL_PREFIX + level["id"], label, store.label_path(parent) if parent else "", recurse=False,
                    )

    def selection(self):
        return self.tree.selection()

    # -- levels ------------------------------------------------------------
    def manage_levels(self):
        if not self.app.require_project():
            return

        def on_change():
            self.app.store.save_codebook()
            self.app.on_codebook_changed()

        codebook_dialogs.manage_levels_dialog(self.frame, self.app.store, on_change)

    def rename_level(self, level: dict):
        name = simpledialog.askstring("Rename Level", "Level name:", initialvalue=level["name"], parent=self.frame)
        if not name:
            return
        try:
            self.app.store.rename_level(level, name)
        except ValueError as exc:
            messagebox.showerror("Can't rename", str(exc))
            return
        self.app.store.save_levels()
        self.app.on_codebook_changed()

    # -- label CRUD -----------------------------------------------------
    def _label_display(self, label: dict) -> str:
        return self.app.store.label_path(label)

    def choose_level(self, prompt: str) -> dict | None:
        levels = [lv for lv, _ in self.app.store.ordered_levels()]
        if not levels:
            messagebox.showinfo("No levels", "Create a level first (Levels…).")
            return None
        if len(levels) == 1:
            return levels[0]
        ok, level = codebook_dialogs.choose_item(self.frame, levels, "Choose Level", prompt)
        return level if ok else None

    def new_label(self, level: dict | None = None, parent_label: dict | None = None) -> dict | None:
        """Create a label, asking for whatever isn't given: level, name,
        color and -- on a hierarchical level -- its parent label. Returns
        the new label, or None if cancelled."""
        if not self.app.require_project():
            return None
        store = self.app.store
        level = level or self.choose_level("Which level is the new label on?")
        if level is None:
            return None

        name = simpledialog.askstring("New Label", f"New label on '{level['name']}':", parent=self.frame)
        if not name or not name.strip():
            return None
        name = name.strip()

        parent_level = store.level(level.get("parent_level_id"))
        if parent_label is None and parent_level is not None:
            candidates = store.labels_of_level(parent_level["id"])
            if candidates:
                ok, parent_label = codebook_dialogs.choose_item(
                    self.frame, candidates, "Parent Label",
                    f"Which {parent_level['name']} does '{name}' belong under?",
                    none_text="(no parent)", display=self._label_display,
                )
                if not ok:
                    return None
        parent_id = parent_label["id"] if parent_label else None

        if store.find_label(name, level["id"], parent_id):
            messagebox.showerror("Duplicate", f"'{name}' already exists there on '{level['name']}'.")
            return None

        color = dialogs.choose_color(self.frame)
        label = store.add_label(name, level["id"], parent_id, color=color)
        store.save_codes()
        self.app.on_codebook_changed()
        return label

    # Kept for the existing "Create New Code..." entry point.
    new_code = new_label

    def _context_item(self, iid: str):
        """(kind, object) for a tree row: ("level", level), ("label", label)
        or (None, None)."""
        store = self.app.store
        for prefix in (LEVEL_PREFIX, ORPHANS_PREFIX):
            if iid.startswith(prefix):
                return "level", store.level(iid[len(prefix):])
        label = store.label(iid)
        return ("label", label) if label else (None, None)

    def on_double_click(self, event):
        kind, obj = self._context_item(self.tree.identify("item", event.x, event.y) or "")
        if kind == "label":
            self.edit_code(obj)
            return "break"

    def selected_labels(self) -> list[dict]:
        """The labels selected in the tree (level header rows ignored)."""
        store = self.app.store
        return [label for label in (store.label(iid) for iid in self.tree.selection()) if label is not None]

    def on_right_click(self, event):
        if not self.app.store.path:
            return
        store = self.app.store
        item = self.tree.identify("item", event.x, event.y)
        menu = tk.Menu(self.frame, tearoff=0)
        kind, obj = self._context_item(item) if item else (None, None)
        # Right-clicking inside a multiple selection acts on all of it;
        # anywhere else starts a new selection.
        if item and item not in self.tree.selection():
            self.tree.selection_set(item)
        labels = self.selected_labels()

        if kind == "label" and len(labels) > 1:
            self._add_multi_label_items(menu, labels)
        elif kind == "level":
            menu.add_command(label=f"New label on {obj['name']}…", command=lambda: self.new_label(obj))
            menu.add_command(label="Rename level…", command=lambda: self.rename_level(obj))
            menu.add_separator()
            menu.add_command(label="Manage levels…", command=self.manage_levels)
        elif kind == "label":
            label = obj
            level = store.level(label.get("level_id"))
            for child_level in store.child_levels(level):
                menu.add_command(
                    label=f"New {child_level['name']} under '{label['name']}'…",
                    command=lambda lv=child_level: self.new_label(lv, label),
                )
            if store.child_levels(level):
                menu.add_separator()
            menu.add_command(label="Rename…", command=lambda: self.edit_code(label))
            menu.add_command(label="Change color…", command=lambda: self.change_color([label]))
            if level and level.get("parent_level_id"):
                menu.add_command(label="Move under…", command=lambda: self.assign_to_category([label]))
                if label.get("parent_id"):
                    menu.add_command(label="Remove parent", command=lambda: self.remove_from_category([label]))
            menu.add_separator()
            menu.add_command(label="Delete", command=lambda: self.delete_code([label]))
        else:
            menu.add_command(label="New label…", command=self.new_label)
            menu.add_command(label="Manage levels…", command=self.manage_levels)

        menu.tk_popup(event.x_root, event.y_root)

    def _add_multi_label_items(self, menu: tk.Menu, labels: list[dict]):
        store = self.app.store
        menu.add_command(label=f"{len(labels)} labels selected", state="disabled")
        menu.add_separator()
        menu.add_command(label="Change color…", command=lambda: self.change_color(labels))
        if self._common_parent_level(labels) is not None:
            menu.add_command(label="Move all under…", command=lambda: self.assign_to_category(labels))
        if any(label.get("parent_id") for label in labels):
            menu.add_command(label="Remove parent", command=lambda: self.remove_from_category(labels))
        if len({label.get("level_id") for label in labels}) == 1:
            menu.add_command(label="Merge into one…", command=lambda: self.merge_labels(labels))
        else:
            menu.add_command(label="Merge into one… (labels must be on the same level)", state="disabled")
        menu.add_separator()
        menu.add_command(label=f"Delete {len(labels)} labels", command=lambda: self.delete_code(labels))

    def _common_parent_level(self, labels: list[dict]) -> dict | None:
        """The parent level all `labels`' levels depend on, or None if they
        differ or a level is independent."""
        store = self.app.store
        parent_ids = {(store.level(label.get("level_id")) or {}).get("parent_level_id") for label in labels}
        if len(parent_ids) != 1:
            return None
        return store.level(parent_ids.pop())

    def _names(self, labels: list[dict]) -> str:
        names = [f"'{label['name']}'" for label in labels[:5]]
        return ", ".join(names) + (f" and {len(labels) - 5} more" if len(labels) > 5 else "")

    def assign_to_category(self, labels: list[dict]):
        """Move labels under a (different) label of their parent level."""
        store = self.app.store
        parent_level = self._common_parent_level(labels)
        if parent_level is None:
            return
        candidates = [c for c in store.labels_of_level(parent_level["id"]) if c not in labels]
        if not candidates:
            messagebox.showinfo("No parent labels", f"Create a label on '{parent_level['name']}' first.")
            return
        ok, new_parent = codebook_dialogs.choose_item(
            self.frame, candidates, "Move Label", f"Move {self._names(labels)} under which {parent_level['name']}?",
            none_text="(no parent)", display=self._label_display,
        )
        if not ok:
            return
        for label in labels:
            store.move_label(label, new_parent["id"] if new_parent else None)
        store.save_codes()
        store.save_links()
        self.app.on_codebook_changed()

    def remove_from_category(self, labels: list[dict]):
        store = self.app.store
        for label in labels:
            store.move_label(label, None)
        store.save_codes()
        store.save_links()
        self.app.on_codebook_changed()

    def edit_code(self, label):
        new_name = simpledialog.askstring("Rename Label", "Label name:", initialvalue=label["name"], parent=self.frame)
        if new_name and new_name.strip() and new_name.strip() != label["name"]:
            label["name"] = new_name.strip()
            self.app.store.save_codes()
            self.app.on_codebook_changed()

    def change_color(self, labels: list[dict]):
        color = dialogs.choose_color(self.frame, cancel_value=None)
        if color is None:
            return
        for label in labels:
            label["color"] = color
        self.app.store.save_codes()
        self.app.on_codebook_changed()

    def merge_labels(self, labels: list[dict]):
        """Keep one of `labels` (all on one level) and fold the others into
        it: their fragments get the kept label, their sub-labels move
        under it, and they are deleted."""
        store = self.app.store
        ok, keep = codebook_dialogs.choose_item(
            self.frame, labels, "Merge Labels", "Keep which label? The others are merged into it and deleted.",
            display=self._label_display,
        )
        if not ok or keep is None:
            return
        others = [label for label in labels if label["id"] != keep["id"]]
        uses = sum(store.label_usage(label["id"]) for label in others)
        if not messagebox.askyesno(
            "Merge Labels",
            f"Merge {self._names(others)} into '{store.label_path(keep)}'?\n\n"
            f"{uses} fragment link(s) move to '{keep['name']}', sub-labels move under it, "
            f"and the {len(others)} other label(s) are deleted.",
        ):
            return
        store.merge_labels(keep, others)
        store.save_codes()
        store.save_links()
        self.app.on_codebook_changed()

    def delete_selected(self, event=None):
        labels = self.selected_labels()
        if labels:
            self.delete_code(labels)

    def delete_code(self, labels: list[dict]):
        store = self.app.store
        ids = {label["id"] for label in labels}
        blocked = [label for label in labels if any(c["id"] not in ids for c in store.child_codes_of(label))]
        if blocked:
            messagebox.showwarning(
                "Cannot Delete",
                f"{self._names(blocked)} {'has' if len(blocked) == 1 else 'have'} sub-labels that aren't selected. "
                "Delete or move them first (or select them too).",
            )
            return
        uses = sum(store.label_usage(label["id"]) for label in labels)
        what = f"label '{labels[0]['name']}'" if len(labels) == 1 else f"{len(labels)} labels ({self._names(labels)})"
        if not messagebox.askyesno(
            "Delete", f"Delete {what}?" + (f"\nThey are used on {uses} fragment link(s)." if uses else "")
        ):
            return
        for label in labels:
            store.delete_code(label)
        store.save_links()
        store.save_codes()
        self.app.on_codebook_changed()

    # -- Excel ----------------------------------------------------------------
    def export_codebook(self):
        if not self.app.require_project():
            return
        path = filedialog.asksaveasfilename(
            title="Export Codebook", defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")],
            initialfile="codebook.xlsx",
        )
        if not path:
            return
        try:
            codebook_io.export_codebook(self.app.store, Path(path))
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        messagebox.showinfo("Exported", f"Codebook exported to:\n{path}")
