"""Codebook dialogs: managing levels (names + hierarchical/independent
structure), picking one label per level when coding a fragment, and a
generic single-item picker.

Unlike dialogs.py, the level manager mutates the ProjectStore's levels
directly (through its methods) and calls on_change() after every action
so the caller can persist and refresh -- mirroring how the attribute
schema manager saves as you go. Nothing here writes files itself.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from . import widgets

NONE_CHOICE = "—"


def choose_item(parent, items: list[dict], title: str, prompt: str, none_text: str | None = None, display=None):
    """Modal single-choice list. Returns (confirmed, item): item is None
    when `none_text` (an extra first row, e.g. "No parent") was chosen.
    confirmed is False if the dialog was cancelled -- callers must not
    treat that like choosing "none"."""
    display = display or (lambda item: item["name"])
    items = sorted(items, key=lambda item: display(item).lower())

    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.transient(parent)
    dialog.grab_set()

    tk.Label(dialog, text=prompt, font=("Arial", 10, "bold"), wraplength=380, justify="left").pack(padx=12, pady=8)
    listbox_frame = tk.Frame(dialog)
    listbox_frame.pack(fill="both", expand=True, padx=12, pady=4)
    listbox = tk.Listbox(listbox_frame, selectmode="single", font=("Arial", 10), height=min(15, len(items) + 1))
    widgets.add_scrollbars(listbox_frame, listbox, horizontal=True)
    offset = 0
    if none_text is not None:
        listbox.insert("end", none_text)
        offset = 1
    for item in items:
        listbox.insert("end", display(item))
    widgets.autosize_listbox(listbox)

    result = {"confirmed": False, "item": None}

    def confirm(event=None):
        sel = listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        result["confirmed"] = True
        result["item"] = None if idx < offset else items[idx - offset]
        dialog.destroy()

    listbox.bind("<Double-Button-1>", confirm)
    listbox.bind("<Return>", confirm)
    dialog.bind("<Escape>", lambda e: dialog.destroy())

    button_frame = tk.Frame(dialog)
    button_frame.pack(fill="x", pady=10)
    tk.Button(button_frame, text="OK", width=10, command=confirm).pack(side="left", padx=8)
    tk.Button(button_frame, text="Cancel", width=10, command=dialog.destroy).pack(side="left", padx=8)

    widgets.center_dialog(dialog, parent)
    listbox.focus_set()
    dialog.wait_window()
    return result["confirmed"], result["item"]


# -- levels ----------------------------------------------------------------
def manage_levels_dialog(parent, store, on_change) -> None:
    """Create, rename, reorder and delete codebook levels, and decide for
    each whether it depends on another level (hierarchical) or not
    (independent). Shown as a tree that mirrors the hierarchy."""
    dialog = tk.Toplevel(parent)
    dialog.title("Codebook Levels")
    dialog.transient(parent)
    dialog.grab_set()

    tk.Label(
        dialog, font=("Arial", 9), justify="left", wraplength=520, anchor="w",
        text=(
            "Levels are the dimensions you label a fragment on (e.g. Tema, Subtema, Sentimento). "
            "A level that depends on another is hierarchical: each of its labels sits under a label "
            "of that level. An independent level is coded separately, side by side with the others."
        ),
    ).pack(fill="x", padx=12, pady=(10, 6))

    tree_frame = tk.Frame(dialog)
    tree_frame.pack(fill="both", expand=True, padx=12)
    tree = ttk.Treeview(tree_frame, columns=("kind", "labels"), height=10, selectmode="browse")
    tree.heading("#0", text="Level")
    tree.heading("kind", text="Structure")
    tree.heading("labels", text="Labels")
    tree.column("labels", width=60, anchor="center")
    widgets.add_scrollbars(tree_frame, tree, horizontal=True)

    def render(select_id: str | None = None):
        tree.delete(*tree.get_children())
        for level, _ in store.ordered_levels():
            parent_level = store.level(level.get("parent_level_id"))
            kind = f"depends on {parent_level['name']}" if parent_level else "independent"
            tree.insert(
                parent_level["id"] if parent_level else "", "end", iid=level["id"], text=level["name"],
                values=(kind, len(store.labels_of_level(level["id"]))), open=True,
            )
        widgets.autosize_treeview_columns(tree, min_width=60)
        if select_id and tree.exists(select_id):
            tree.selection_set(select_id)
            tree.see(select_id)

    def selected_level():
        sel = tree.selection()
        level = store.level(sel[0]) if sel else None
        if level is None:
            messagebox.showinfo("Select a level", "Select a level first.", parent=dialog)
        return level

    def changed(select_id=None):
        on_change()
        render(select_id)

    def ask_name(title, initial=""):
        return simpledialog.askstring(title, "Level name:", initialvalue=initial, parent=dialog)

    def add_level(parent_level=None):
        name = ask_name("New Sub-level" if parent_level else "New Level")
        if not name:
            return
        try:
            level = store.add_level(name, parent_level["id"] if parent_level else None)
        except ValueError as exc:
            messagebox.showerror("Can't add level", str(exc), parent=dialog)
            return
        changed(level["id"])

    def add_sublevel():
        level = selected_level()
        if level:
            add_level(level)

    def rename():
        level = selected_level()
        if not level:
            return
        name = ask_name("Rename Level", level["name"])
        if not name:
            return
        try:
            store.rename_level(level, name)
        except ValueError as exc:
            messagebox.showerror("Can't rename", str(exc), parent=dialog)
            return
        changed(level["id"])

    def confirm_detach(levels) -> bool:
        attached = sum(1 for lv in levels for c in store.labels_of_level(lv["id"]) if c.get("parent_id"))
        if not attached:
            return True
        return messagebox.askyesno(
            "Change structure",
            f"{attached} label(s) currently sit under a parent label. Changing which level they depend on "
            "detaches them from their parents (fragments keep their labels).\n\nContinue?",
            parent=dialog,
        )

    def set_parent(level, parent_id):
        try:
            store.set_level_parent(level, parent_id)
        except ValueError as exc:
            messagebox.showerror("Can't change structure", str(exc), parent=dialog)

    def depends_on():
        level = selected_level()
        if not level:
            return
        candidates = [
            lv for lv in store.levels
            if lv is not level and level not in store.level_ancestors(lv)
        ]
        if not candidates:
            messagebox.showinfo("No levels", "Add another level first.", parent=dialog)
            return
        ok, chosen = choose_item(
            dialog, candidates, "Depends on", f"Which level should '{level['name']}' depend on?",
            none_text="(none — independent)",
        )
        if not ok:
            return
        new_parent_id = chosen["id"] if chosen else None
        if new_parent_id == level.get("parent_level_id") or not confirm_detach([level]):
            return
        set_parent(level, new_parent_id)
        changed(level["id"])

    def move(delta):
        level = selected_level()
        if level:
            store.move_level(level, delta)
            changed(level["id"])

    def delete():
        level = selected_level()
        if not level:
            return
        labels = store.labels_of_level(level["id"])
        uses = sum(store.label_usage(c["id"]) for c in labels)
        if not messagebox.askyesno(
            "Delete level",
            f"Delete level '{level['name']}' with its {len(labels)} label(s)?\n"
            f"{uses} fragment coding(s) on this level will be removed.\n"
            "Levels depending on it become independent.",
            parent=dialog,
        ):
            return
        store.delete_level(level)
        changed()

    def preset_hierarchy():
        ordered = [lv for lv, _ in store.ordered_levels()]
        if len(ordered) < 2:
            return
        if not messagebox.askyesno(
            "One hierarchy",
            "Make every level depend on the one above it:\n\n" + " › ".join(lv["name"] for lv in ordered),
            parent=dialog,
        ):
            return
        to_change = [lv for i, lv in enumerate(ordered) if i and lv.get("parent_level_id") != ordered[i - 1]["id"]]
        if not confirm_detach(to_change):
            return
        # Detach the levels that change first, so re-parenting them in chain
        # order never forms a cycle; levels already in place keep their
        # labels' parents.
        store.levels[:] = ordered
        for lv in to_change:
            set_parent(lv, None)
        for i in range(1, len(ordered)):
            set_parent(ordered[i], ordered[i - 1]["id"])
        changed()

    def preset_independent():
        dependent = [lv for lv in store.levels if lv.get("parent_level_id")]
        if not dependent:
            return
        if not messagebox.askyesno(
            "All independent", "Make every level independent (no hierarchy)?", parent=dialog,
        ) or not confirm_detach(dependent):
            return
        ordered = [lv for lv, _ in store.ordered_levels()]
        for lv in dependent:
            set_parent(lv, None)
        store.levels[:] = ordered
        changed()

    row1 = tk.Frame(dialog)
    row1.pack(fill="x", padx=12, pady=(8, 2))
    tk.Button(row1, text="Add level", command=add_level).pack(side="left", padx=2)
    tk.Button(row1, text="Add sub-level", command=add_sublevel).pack(side="left", padx=2)
    tk.Button(row1, text="Rename", command=rename).pack(side="left", padx=2)
    tk.Button(row1, text="Depends on…", command=depends_on).pack(side="left", padx=2)
    tk.Button(row1, text="▲", width=3, command=lambda: move(-1)).pack(side="left", padx=2)
    tk.Button(row1, text="▼", width=3, command=lambda: move(1)).pack(side="left", padx=2)
    tk.Button(row1, text="Delete", command=delete).pack(side="left", padx=2)

    row2 = tk.Frame(dialog)
    row2.pack(fill="x", padx=12, pady=(2, 10))
    tk.Label(row2, text="Structure:").pack(side="left")
    tk.Button(row2, text="One hierarchy (top → bottom)", command=preset_hierarchy).pack(side="left", padx=2)
    tk.Button(row2, text="All independent", command=preset_independent).pack(side="left", padx=2)
    tk.Button(row2, text="Close", width=10, command=dialog.destroy).pack(side="right", padx=2)

    render()
    widgets.center_dialog(dialog, parent)


# -- coding ----------------------------------------------------------------
def assign_labels_dialog(parent, store, title: str, initial_ids: list[str], new_label) -> list[str] | None:
    """One dropdown per level, indented to show the hierarchy. A
    hierarchical level's dropdown only offers labels under the label
    chosen on its parent level (all of them while that's unset); choosing
    a sub-label fills in its ancestors, and changing a parent clears any
    sub-label that no longer fits. "+" creates a new label on that level
    via new_label(level, parent_label) -> label | None.

    Returns the chosen label ids (may be empty), or None if cancelled."""
    ordered = store.ordered_levels()
    selected: dict[str, str | None] = {lv["id"]: None for lv, _ in ordered}
    extra_notes: dict[str, str] = {}
    for label_id in initial_ids:
        label = store.label(label_id)
        if label is None or label.get("level_id") not in selected:
            continue
        if selected[label["level_id"]] is None:
            selected[label["level_id"]] = label_id
        else:
            extra_notes[label["level_id"]] = extra_notes.get(label["level_id"], "") + f" {label['name']};"

    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.transient(parent)
    dialog.grab_set()

    tk.Label(dialog, text="Choose a label on each level (leave “—” to skip a level).", font=("Arial", 9)).pack(
        anchor="w", padx=12, pady=(10, 4)
    )
    grid = tk.Frame(dialog)
    grid.pack(fill="both", expand=True, padx=12)
    grid.columnconfigure(1, weight=1)

    combos: dict[str, tuple[ttk.Combobox, dict[str, str | None]]] = {}

    def descendants_of(level) -> list[dict]:
        return [lv for lv, _ in ordered if level in store.level_ancestors(lv)]

    def candidates(level) -> tuple[list[dict], bool]:
        """(labels, filtered_by_parent)"""
        parent_level = store.level(level.get("parent_level_id"))
        labels = store.labels_of_level(level["id"])
        if parent_level is not None and selected.get(parent_level["id"]):
            return [c for c in labels if c.get("parent_id") == selected[parent_level["id"]]], True
        return labels, False

    def refresh_combos():
        for level, _ in ordered:
            combo, mapping = combos[level["id"]]
            labels, filtered = candidates(level)
            mapping.clear()
            mapping[NONE_CHOICE] = None
            for c in sorted(labels, key=lambda c: c["name"].lower()):
                parent_label = store.label(c.get("parent_id"))
                text = c["name"] if filtered or parent_label is None else f"{c['name']}   ({store.label_path(parent_label)})"
                while text in mapping:
                    text += " "
                mapping[text] = c["id"]
            combo["values"] = list(mapping)
            current = selected[level["id"]]
            combo.set(next((t for t, i in mapping.items() if i == current and i is not None), NONE_CHOICE))

    def choose(level, label_id):
        selected[level["id"]] = label_id
        if label_id:
            label = store.label(label_id)
            for ancestor in store.label_ancestors(label):
                if ancestor.get("level_id") in selected:
                    selected[ancestor["level_id"]] = ancestor["id"]
        for desc in descendants_of(level):
            current = store.label(selected[desc["id"]])
            if current is None:
                continue
            if not label_id or label_id not in {a["id"] for a in store.label_ancestors(current)}:
                selected[desc["id"]] = None
        refresh_combos()

    def on_select(level):
        combo, mapping = combos[level["id"]]
        choose(level, mapping.get(combo.get()))

    def add_new(level):
        parent_level = store.level(level.get("parent_level_id"))
        parent_label = store.label(selected.get(parent_level["id"])) if parent_level else None
        label = new_label(level, parent_label)
        if label:
            refresh_combos()
            choose(level, label["id"])

    previous_depth = None
    for row, (level, depth) in enumerate(ordered):
        if depth == 0 and previous_depth is not None:
            ttk.Separator(grid, orient="horizontal").grid(row=row * 2, column=0, columnspan=3, sticky="ew", pady=4)
        previous_depth = depth
        prefix = "    " * depth + ("↳ " if depth else "")
        tk.Label(grid, text=f"{prefix}{level['name']}:", anchor="w", font=("Arial", 10, "bold" if depth == 0 else "normal")).grid(
            row=row * 2 + 1, column=0, sticky="w", padx=(0, 8), pady=2
        )
        combo = ttk.Combobox(grid, state="readonly", width=40, height=15)
        combo.grid(row=row * 2 + 1, column=1, sticky="ew", pady=2)
        combo.bind("<<ComboboxSelected>>", lambda e, lv=level: on_select(lv))
        combos[level["id"]] = (combo, {})
        tk.Button(grid, text="+", width=2, command=lambda lv=level: add_new(lv)).grid(
            row=row * 2 + 1, column=2, padx=(4, 0), pady=2
        )

    if extra_notes:
        note = "; ".join(
            f"{store.level(lid)['name']}:{names.rstrip(';')}" for lid, names in extra_notes.items()
        )
        tk.Label(
            dialog, fg="#a33333", font=("Arial", 8), wraplength=460, justify="left", anchor="w",
            text=f"This fragment has more than one label on some levels; saving keeps only the one shown ({note}).",
        ).pack(fill="x", padx=12, pady=(4, 0))

    result = {"ids": None}

    def confirm(event=None):
        result["ids"] = [label_id for label_id in selected.values() if label_id]
        dialog.destroy()

    button_frame = tk.Frame(dialog)
    button_frame.pack(fill="x", pady=10, padx=12)
    tk.Button(button_frame, text="OK", width=10, command=confirm).pack(side="left", padx=4)
    tk.Button(button_frame, text="Cancel", width=10, command=dialog.destroy).pack(side="left", padx=4)
    dialog.bind("<Return>", confirm)
    dialog.bind("<Escape>", lambda e: dialog.destroy())

    if not ordered:
        tk.Label(grid, text="The codebook has no levels yet — add one under Levels…", fg="gray").grid(row=0, column=0)

    refresh_combos()
    widgets.center_dialog(dialog, parent)
    dialog.wait_window()
    return result["ids"]
