"""Small reusable Toplevel dialogs: color picker, document attribute
editor/schema manager. (Codebook pickers live in codebook_dialogs.py.)

Each function/class takes the parent window and whatever data it needs,
and returns a plain result -- no direct writes to project files here,
callers decide what to persist.
"""
from __future__ import annotations

import tkinter as tk
from datetime import date
from tkinter import messagebox, ttk

from .. import attribute_formulas
from ..colors import COLOR_PALETTE, DEFAULT_CODE_COLOR
from . import widgets

ATTRIBUTE_TYPES = ["string", "integer", "date", "computed"]
IMPORTABLE_TYPES = [t for t in ATTRIBUTE_TYPES if t != "computed"]


def show_busy(parent, message: str) -> tk.Toplevel:
    """Show a small non-interactive Toplevel with a message, e.g. while a
    slow blocking operation (like text extraction) runs. Caller must call
    dialog.destroy() when the operation finishes."""
    dialog = tk.Toplevel(parent)
    dialog.title("Please wait")
    dialog.transient(parent)
    dialog.resizable(False, False)
    tk.Label(
        dialog, text=message, font=("Arial", 10), wraplength=420, justify="center",
    ).pack(expand=True, padx=20, pady=20)

    # Size to the label's natural (possibly wrapped) footprint rather than a
    # fixed box, so a long message/file name is never clipped.
    dialog.update_idletasks()
    width = max(300, min(dialog.winfo_reqwidth(), 460))
    height = dialog.winfo_reqheight()
    widgets.center_dialog(dialog, parent, width=width, height=height)

    dialog.update()
    return dialog


def choose_color(parent, cancel_value: str | None = DEFAULT_CODE_COLOR) -> str | None:
    """Show a modal color-swatch picker. Returns the chosen hex color, or
    `cancel_value` if the dialog is closed without a selection (the
    default color, unless the caller passes None to detect cancelling)."""
    dialog = tk.Toplevel(parent)
    dialog.title("Choose Color")
    dialog.transient(parent)
    dialog.grab_set()

    selected_color = [cancel_value]

    tk.Label(dialog, text="Select a color for this code:", font=("Arial", 10, "bold")).pack(pady=10)

    color_frame = tk.Frame(dialog)
    color_frame.pack(fill="both", expand=True, padx=10, pady=10)

    def select_color(hex_color):
        selected_color[0] = hex_color
        dialog.destroy()

    colors_per_row = 4
    row_frame = None
    for i, (hex_color, color_name) in enumerate(COLOR_PALETTE):
        if i % colors_per_row == 0:
            row_frame = tk.Frame(color_frame)
            row_frame.pack(fill="x", pady=5)

        color_button = tk.Button(
            row_frame,
            bg=hex_color,
            width=4,
            height=2,
            relief="raised",
            bd=2,
            command=lambda c=hex_color: select_color(c),
        )
        color_button.pack(side="left", padx=5, pady=5)
        color_button.bind("<Enter>", lambda e, name=color_name: e.widget.config(relief="sunken"))
        color_button.bind("<Leave>", lambda e: e.widget.config(relief="raised"))

    widgets.center_dialog(dialog, parent)
    dialog.wait_window()
    return selected_color[0]


def show_document_attributes_dialog(
    parent, doc: dict, schema: list[dict], on_save, on_manage_schema, resolve_value=None
) -> None:
    """Edit a document's attribute values against the current schema.

    on_save(doc, values: dict[str, str]) is called when the user saves.
    on_manage_schema() is called if the user opens the schema manager
    from within this dialog. resolve_value(name) -> str, when given, is
    used to display a computed attribute's current value -- computed
    attributes have no Entry of their own, since there's nothing to
    directly edit on them.
    """
    dialog = tk.Toplevel(parent)
    dialog.title(f"Attributes for {doc['title']}")
    dialog.transient(parent)
    dialog.grab_set()

    tk.Label(dialog, text=f"Document: {doc['title']}", font=("Arial", 10, "bold")).pack(pady=8)
    tk.Label(dialog, text="Attributes:", font=("Arial", 9)).pack(anchor="w", padx=12)

    entries_frame = tk.Frame(dialog)
    entries_frame.pack(fill="both", expand=True, padx=12, pady=4)

    entries: dict[str, tuple[tk.Entry, str]] = {}
    for attr_entry in schema:
        name = attr_entry["name"]
        attr_type = attr_entry.get("type", "string")

        frame = tk.Frame(entries_frame)
        frame.pack(fill="x", pady=4)
        tk.Label(frame, text=f"{name} ({attr_type}):", anchor="w").pack(side="left")

        if attr_type == "computed":
            value = resolve_value(name) if resolve_value else ""
            tk.Label(frame, text=value or "—", fg="gray", anchor="w").pack(
                side="left", fill="x", expand=True, padx=4
            )
        else:
            value = doc.get("attributes", {}).get(name, "")
            entry = tk.Entry(frame)
            entry.insert(0, value)
            entry.pack(side="left", fill="x", expand=True, padx=4)
            entries[name] = (entry, attr_type)

    def save_attrs():
        values = {}
        for name, (entry, attr_type) in entries.items():
            raw = entry.get().strip()
            if raw and attr_type == "integer":
                try:
                    int(raw)
                except ValueError:
                    messagebox.showerror("Invalid value", f"'{name}' must be a whole number.")
                    return
            elif raw and attr_type == "date":
                try:
                    date.fromisoformat(raw)
                except ValueError:
                    messagebox.showerror("Invalid value", f"'{name}' must be a date in YYYY-MM-DD format.")
                    return
            values[name] = raw
        on_save(doc, values)
        dialog.destroy()

    button_frame = tk.Frame(dialog)
    button_frame.pack(fill="x", pady=10)
    tk.Button(button_frame, text="Save", command=save_attrs, width=10).pack(side="left", padx=4)
    tk.Button(button_frame, text="Manage Schema", command=on_manage_schema, width=15).pack(side="left", padx=4)
    tk.Button(button_frame, text="Close", command=dialog.destroy, width=10).pack(side="left", padx=4)

    widgets.center_dialog(dialog, parent)


def manage_document_schema_dialog(
    parent, schema: list[dict], on_add, on_remove, on_import=None, on_export=None
) -> None:
    """Add/remove attributes from the document schema.

    on_add(entry) / on_remove(name) are called immediately on each action
    (mirrors original behavior of saving as you go) and are responsible
    for persisting + refreshing any dependent views. `schema` is the
    live list (not a copy): add_attribute_dialog reads it to offer
    existing attributes as operands for a new computed attribute.
    on_import(dialog), when given, adds an "Import from file..." button;
    it may append many entries to `schema`, so the list is re-rendered
    from it afterwards. on_export(dialog), when given, adds an
    "Export to file..." button.
    """
    dialog = tk.Toplevel(parent)
    dialog.title("Manage Document Attributes")
    dialog.transient(parent)
    dialog.grab_set()

    tk.Label(dialog, text="Document Attribute Schema:", font=("Arial", 10, "bold")).pack(pady=8)

    listbox_frame = tk.Frame(dialog)
    listbox_frame.pack(fill="both", expand=True, padx=12, pady=4)

    # A long attribute name should make this dialog taller, not wider --
    # WrappingList wraps each row within a fixed width instead of a
    # Listbox's one-line-per-item (which would either hide the overflow
    # or force the whole window wider to show it).
    listbox = widgets.WrappingList(listbox_frame, wraplength=340)
    listbox.frame.pack(fill="both", expand=True)

    def row_label(entry: dict) -> str:
        return f"{entry['name']}  ·  {entry.get('type', 'string')}"

    for entry in schema:
        listbox.insert("end", row_label(entry))

    def add_attr():
        new_entry = add_attribute_dialog(dialog, schema)
        if new_entry:
            listbox.insert("end", row_label(new_entry))
            widgets.center_dialog(dialog, parent)
            on_add(new_entry)

    def remove_attr():
        sel = listbox.curselection()
        if sel:
            idx = sel[0]
            removed_name = schema[idx]["name"]
            listbox.delete(idx)
            widgets.center_dialog(dialog, parent)
            on_remove(removed_name)

    def import_attrs():
        on_import(dialog)
        if not dialog.winfo_exists():
            return
        listbox.clear()
        for entry in schema:
            listbox.insert("end", row_label(entry))
        widgets.center_dialog(dialog, parent)

    button_frame = tk.Frame(dialog)
    button_frame.pack(fill="x", pady=(10, 4))
    tk.Button(button_frame, text="Add", command=add_attr, width=10).pack(side="left", padx=4)
    tk.Button(button_frame, text="Remove", command=remove_attr, width=10).pack(side="left", padx=4)
    tk.Button(button_frame, text="Close", command=dialog.destroy, width=10).pack(side="left", padx=4)

    if on_import is not None or on_export is not None:
        io_frame = tk.Frame(dialog)
        io_frame.pack(fill="x", pady=(0, 10))
        if on_import is not None:
            tk.Button(io_frame, text="Import from file...", command=import_attrs, width=16).pack(side="left", padx=4)
        if on_export is not None:
            tk.Button(io_frame, text="Export to file...", command=lambda: on_export(dialog), width=16).pack(
                side="left", padx=4
            )

    widgets.center_dialog(dialog, parent)


def add_attribute_dialog(parent, schema: list[dict]) -> dict | None:
    """Guided builder for one new document-attribute schema entry.

    Returns a plain typed entry ({"name", "type"}) for string/integer/
    date, or a computed entry ({"name", "type": "computed", "formula"})
    built from one operation over one or two operands -- each operand is
    either an existing attribute of the matching type, or a typed
    literal. Returns None if the user cancels.

    There's no free-text formula box on purpose: attribute_formulas.
    OPERATIONS is a small fixed whitelist, so this dialog can only ever
    build something evaluate() already knows how to run safely.
    """
    dialog = tk.Toplevel(parent)
    dialog.title("Add Attribute")
    dialog.transient(parent)
    dialog.grab_set()

    result: dict | None = None

    top_frame = tk.Frame(dialog)
    top_frame.pack(fill="x", padx=12, pady=(12, 4))
    top_frame.columnconfigure(1, weight=1)

    tk.Label(top_frame, text="Name:").grid(row=0, column=0, sticky="w")
    name_var = tk.StringVar()
    tk.Entry(top_frame, textvariable=name_var, width=32).grid(row=0, column=1, sticky="ew", padx=(6, 0))

    tk.Label(top_frame, text="Type:").grid(row=1, column=0, sticky="w", pady=(6, 0))
    type_var = tk.StringVar(value="string")
    type_combo = ttk.Combobox(
        top_frame, textvariable=type_var, state="readonly", values=ATTRIBUTE_TYPES, width=29,
    )
    type_combo.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(6, 0))

    formula_frame = tk.Frame(dialog)
    formula_frame.pack(fill="x", padx=12, pady=(4, 4))

    op_keys = list(attribute_formulas.OPERATIONS.keys())
    op_var = tk.StringVar(value=op_keys[0])
    left_var = tk.StringVar()
    right_var = tk.StringVar()
    right_literal_var = tk.StringVar()
    use_constant_var = tk.BooleanVar(value=False)

    def current_op() -> attribute_formulas.Operation:
        return attribute_formulas.OPERATIONS[op_var.get()]

    def eligible_attrs(domain: str) -> list[str]:
        return [e["name"] for e in schema if e.get("type") == domain]

    def rebuild_operands(*_args) -> None:
        for child in formula_frame.winfo_children():
            child.destroy()
        if type_var.get() != "computed":
            widgets.center_dialog(dialog, parent)
            return

        formula_frame.columnconfigure(1, weight=1)
        op = current_op()

        tk.Label(formula_frame, text="Operation:").grid(row=0, column=0, sticky="w", pady=2)
        op_labels = [attribute_formulas.OPERATIONS[k].label for k in op_keys]
        op_combo = ttk.Combobox(formula_frame, state="readonly", values=op_labels, width=29)
        op_combo.current(op_keys.index(op_var.get()))
        op_combo.grid(row=0, column=1, columnspan=2, sticky="ew", pady=2)

        def on_op_change(event=None):
            op_var.set(op_keys[op_combo.current()])
            rebuild_operands()

        op_combo.bind("<<ComboboxSelected>>", on_op_change)

        choices = eligible_attrs(op.domain)
        tk.Label(formula_frame, text="Value:").grid(row=1, column=0, sticky="w", pady=2)
        left_combo = ttk.Combobox(formula_frame, textvariable=left_var, state="readonly", values=choices, width=29)
        left_combo.grid(row=1, column=1, columnspan=2, sticky="ew", pady=2)
        if choices and left_var.get() not in choices:
            left_var.set(choices[0])

        if op.arity == 2:
            tk.Checkbutton(
                formula_frame, text="Use a constant instead of an attribute",
                variable=use_constant_var, command=rebuild_operands,
            ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

            tk.Label(formula_frame, text="With:").grid(row=3, column=0, sticky="w", pady=2)
            if use_constant_var.get():
                tk.Entry(formula_frame, textvariable=right_literal_var, width=32).grid(
                    row=3, column=1, columnspan=2, sticky="ew", pady=2
                )
            else:
                right_combo = ttk.Combobox(
                    formula_frame, textvariable=right_var, state="readonly", values=choices, width=29,
                )
                right_combo.grid(row=3, column=1, columnspan=2, sticky="ew", pady=2)
                if choices and right_var.get() not in choices:
                    right_var.set(choices[0])

        widgets.center_dialog(dialog, parent)

    type_combo.bind("<<ComboboxSelected>>", rebuild_operands)
    rebuild_operands()

    def confirm():
        nonlocal result
        name = name_var.get().strip()
        if not name:
            messagebox.showerror("Missing name", "Enter a name for the attribute.")
            return
        if any(e["name"] == name for e in schema):
            messagebox.showerror("Duplicate name", f"An attribute named '{name}' already exists.")
            return

        attr_type = type_var.get()
        if attr_type != "computed":
            result = {"name": name, "type": attr_type}
            dialog.destroy()
            return

        op = current_op()
        if not left_var.get():
            messagebox.showerror("Missing value", "Choose a value for the operation.")
            return
        formula = {"op": op.key, "left": {"kind": "attr", "value": left_var.get()}}

        if op.arity == 2:
            if use_constant_var.get():
                literal = right_literal_var.get().strip()
                if not literal:
                    messagebox.showerror("Missing value", "Enter a constant value.")
                    return
                formula["right"] = {"kind": "literal", "value": literal}
            else:
                if not right_var.get():
                    messagebox.showerror("Missing value", "Choose a second value for the operation.")
                    return
                formula["right"] = {"kind": "attr", "value": right_var.get()}

        result = {"name": name, "type": "computed", "formula": formula}
        dialog.destroy()

    button_frame = tk.Frame(dialog)
    button_frame.pack(fill="x", pady=10, padx=12)
    tk.Button(button_frame, text="OK", command=confirm, width=10).pack(side="left", padx=4)
    tk.Button(button_frame, text="Cancel", command=dialog.destroy, width=10).pack(side="left", padx=4)

    widgets.center_dialog(dialog, parent)
    dialog.wait_window()
    return result


def import_attributes_dialog(parent, file_name: str, headers: list[str], build_plan, count_matches):
    """Preview and adjust a document-attribute import before it runs.

    The user picks which column identifies the document, which of the
    other columns to import, and (for new fields) the type each one gets.
    build_plan(key_index) -> AttributeImportPlan and
    count_matches(key_index) -> (matched_rows, total_rows) are supplied
    by the caller so this dialog stays free of store access. Returns the
    adjusted plan, or None if the user cancels.
    """
    dialog = tk.Toplevel(parent)
    dialog.title("Import Document Attributes")
    dialog.transient(parent)
    dialog.grab_set()

    result = {"plan": None}
    state = {"plan": build_plan(None)}

    tk.Label(dialog, text=f"File: {file_name}", font=("Arial", 10, "bold"), wraplength=520, justify="left").pack(
        anchor="w", padx=12, pady=(10, 4)
    )

    key_frame = tk.Frame(dialog)
    key_frame.pack(fill="x", padx=12, pady=4)
    tk.Label(key_frame, text="Document column:").pack(side="left")
    key_combo = ttk.Combobox(key_frame, state="readonly", values=headers, width=30)
    key_combo.current(state["plan"].key_index)
    key_combo.pack(side="left", padx=6)

    match_label = tk.Label(dialog, anchor="w", font=("Arial", 9))
    match_label.pack(fill="x", padx=12)

    tk.Label(
        dialog, text="Columns to import as attributes:", font=("Arial", 9, "bold"), anchor="w",
    ).pack(fill="x", padx=12, pady=(8, 2))

    # Scrollable grid of columns: a sheet can have dozens of them.
    list_outer = tk.Frame(dialog, bd=1, relief="sunken")
    list_outer.pack(fill="both", expand=True, padx=12, pady=4)
    canvas = tk.Canvas(list_outer, highlightthickness=0, height=280, width=500)
    scrollbar = ttk.Scrollbar(list_outer, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    rows_frame = tk.Frame(canvas)
    canvas.create_window((0, 0), window=rows_frame, anchor="nw")
    rows_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

    def on_wheel(event):
        canvas.yview_scroll(int(-event.delta / 120), "units")

    dialog.bind("<MouseWheel>", on_wheel)

    row_vars: list[tuple[tk.BooleanVar, tk.StringVar]] = []

    def render_columns():
        for child in rows_frame.winfo_children():
            child.destroy()
        row_vars.clear()

        tk.Label(rows_frame, text="Import", font=("Arial", 9, "bold")).grid(row=0, column=0, padx=4, sticky="w")
        tk.Label(rows_frame, text="Column / field", font=("Arial", 9, "bold")).grid(row=0, column=1, padx=4, sticky="w")
        tk.Label(rows_frame, text="Type", font=("Arial", 9, "bold")).grid(row=0, column=2, padx=4, sticky="w")

        for i, col in enumerate(state["plan"].columns, start=1):
            include_var = tk.BooleanVar(value=col.include)
            type_var = tk.StringVar(value=col.type)
            row_vars.append((include_var, type_var))

            check = tk.Checkbutton(rows_frame, variable=include_var)
            check.grid(row=i, column=0, padx=4)
            tk.Label(rows_frame, text=col.header, anchor="w", wraplength=280, justify="left").grid(
                row=i, column=1, padx=4, sticky="w"
            )
            if col.existing:
                note = f"{col.type} (existing)"
                if col.type == "computed":
                    note = "computed (can't import)"
                    check.config(state="disabled")
                tk.Label(rows_frame, text=note, fg="gray", anchor="w").grid(row=i, column=2, padx=4, sticky="w")
            elif col.type == "computed":
                tk.Label(rows_frame, text="computed (formula from file)", fg="gray", anchor="w").grid(
                    row=i, column=2, padx=4, sticky="w"
                )
            else:
                ttk.Combobox(
                    rows_frame, textvariable=type_var, state="readonly", values=IMPORTABLE_TYPES, width=10,
                ).grid(row=i, column=2, padx=4, pady=1, sticky="w")

        matched, total = count_matches(state["plan"].key_index)
        color = "dark green" if matched == total else ("dark orange" if matched else "red")
        match_label.config(text=f"{matched} of {total} rows match a document in this project.", fg=color)
        canvas.yview_moveto(0)

    def on_key_change(event=None):
        state["plan"] = build_plan(key_combo.current())
        render_columns()

    key_combo.bind("<<ComboboxSelected>>", on_key_change)

    def locked(col) -> bool:
        # An existing computed field has no stored values to import into.
        return col.existing and col.type == "computed"

    def set_all(value: bool):
        for (include_var, _), col in zip(row_vars, state["plan"].columns):
            if not locked(col):
                include_var.set(value)

    def confirm():
        plan = state["plan"]
        for (include_var, type_var), col in zip(row_vars, plan.columns):
            col.include = include_var.get() and not locked(col)
            if not col.existing and col.type != "computed":
                col.type = type_var.get()
        if not any(col.include for col in plan.columns):
            messagebox.showerror("Nothing to import", "Select at least one column to import.", parent=dialog)
            return
        result["plan"] = plan
        dialog.destroy()

    button_frame = tk.Frame(dialog)
    button_frame.pack(fill="x", pady=10, padx=12)
    tk.Button(button_frame, text="Import", command=confirm, width=10).pack(side="left", padx=4)
    tk.Button(button_frame, text="Cancel", command=dialog.destroy, width=10).pack(side="left", padx=4)
    tk.Button(button_frame, text="Select none", command=lambda: set_all(False), width=11).pack(side="right", padx=4)
    tk.Button(button_frame, text="Select all", command=lambda: set_all(True), width=11).pack(side="right", padx=4)

    render_columns()
    widgets.center_dialog(dialog, parent)
    dialog.wait_window()
    return result["plan"]
