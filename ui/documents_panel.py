"""Documents panel: file import button, document tree, fragments list,
and the standalone Documents datasheet window.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from pathlib import Path

from . import theme
from .scaling import px
from .. import attribute_io
from ..extraction import extract_text_for
from . import dialogs, widgets


class DocumentsPanel:
    def __init__(self, parent, app):
        """
        app must provide:
          - app.store: ProjectStore
          - app.require_project() -> bool
          - app.current_doc, app.set_current_doc(doc)
          - app.load_document()
          - app.render_fragments()
        """
        self.app = app
        self.documents_window = None
        self.documents_window_tree = None

        self.frame = tk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)

        button_frame = tk.Frame(self.frame)
        button_frame.grid(row=0, column=0, sticky="ew", pady=2)
        theme.primary(tk.Button(button_frame, text="Add File", command=self.add_document)).pack(side="left", fill="x", expand=True, padx=(0, 2))
        tk.Button(button_frame, text="Documents", command=self.show_documents_window).pack(side="left", fill="x", expand=True, padx=2)
        tk.Button(button_frame, text="Manage Attributes", command=self.manage_document_schema).pack(side="left", fill="x", expand=True, padx=(2, 0))

        # Documents above Fragments, split by a draggable divider (the
        # position is saved with the window layout, see QDAApp).
        self.split = tk.PanedWindow(self.frame, orient="vertical", sashwidth=px(8), bd=0)
        self.split.grid(row=1, column=0, sticky="nsew")

        doc_section = tk.Frame(self.split)
        tk.Label(doc_section, text="Documents", font=theme.H2, anchor="w").pack(fill="x", pady=(5, 2))
        doc_frame = tk.Frame(doc_section)
        doc_frame.pack(fill="both", expand=True, pady=2)

        self.tree = ttk.Treeview(doc_frame, height=5, show="tree")
        widgets.add_scrollbars(doc_frame, self.tree, horizontal=True)

        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Button-3>", lambda e: self.on_right_click(e, self.tree))

        frag_section = tk.Frame(self.split)
        tk.Label(frag_section, text="Fragments", font=theme.H2, anchor="w").pack(fill="x", pady=(5, 2))
        self.frag_list = tk.Listbox(frag_section, font=("Segoe UI", 9))
        self.frag_list.pack(fill="both", expand=True, pady=2)

        self.split.add(doc_section, minsize=80, stretch="always")
        self.split.add(frag_section, minsize=80, stretch="always")
        self.frag_list.bind("<<ListboxSelect>>", lambda e: self.app.on_select_fragment())
        self.frag_list.bind("<Button-3>", self.on_fragment_right_click)
        self.frag_list.bind("<Delete>", lambda e: self.uncode_selected_fragment())

    def grid(self, **kwargs):
        self.frame.grid(**kwargs)

    # -- document tree -----------------------------------------------------
    def populate(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for doc in self.app.store.validated_documents():
            self.tree.insert("", "end", iid=doc["id"], text=doc.get("title", "Untitled"))
        widgets.autosize_treeview_columns(self.tree)

    def on_select(self, event=None):
        sel = self.tree.selection()
        if sel:
            doc = next((d for d in self.app.store.documents if d["id"] == sel[0]), None)
            if doc:
                self.app.set_current_doc(doc)
                self.app.load_document()

    def on_right_click(self, event, tree):
        item = tree.identify("item", event.x, event.y)
        if not item:
            return
        tree.selection_set(item)
        doc = next((d for d in self.app.store.documents if d["id"] == item), None)
        if doc:
            self.app.set_current_doc(doc)
            self.app.load_document()

        menu = tk.Menu(self.frame, tearoff=0)
        menu.add_command(label="Rename", command=lambda: self.rename_document(doc))
        menu.add_command(label="Edit Attributes", command=lambda: self.edit_document_attributes(doc))
        menu.add_command(label="Delete", command=lambda: self.delete_document(doc))
        menu.tk_popup(event.x_root, event.y_root)

    # -- import ---------------------------------------------------------------
    def add_document(self):
        if not self.app.require_project():
            return

        file_path = filedialog.askopenfilename(
            title="Import Document",
            filetypes=[
                ("Text files", "*.txt"),
                ("Word documents", "*.docx"),
                ("PDF files", "*.pdf"),
            ],
        )
        if not file_path:
            return
        file_path = Path(file_path)

        busy = dialogs.show_busy(self.frame, f"Extracting text from {file_path.name}...")
        try:
            text = extract_text_for(file_path)
        except Exception as e:
            messagebox.showerror("Import error", str(e))
            return
        finally:
            busy.destroy()

        if not text.strip():
            messagebox.showwarning("No text", "No extractable text found. This PDF may be scanned.")
            return

        doc, created = self.app.store.add_document_from_text(
            title=file_path.stem,
            text=text,
            source_format=file_path.suffix.lower(),
            imported_from=str(file_path),
        )
        if not created:
            messagebox.showwarning("Duplicate document", "This document has already been imported.")
            return

        self.app.store.save_documents()
        self.app.refresh_all()
        self.refresh_documents_window()
        messagebox.showinfo("Imported", "Document imported successfully")

    # -- delete / attributes -----------------------------------------------------
    def delete_document(self, doc):
        if not doc:
            return
        if not messagebox.askyesno("Delete", f"Delete '{doc.get('title', 'Untitled')}'?"):
            return

        self.app.store.delete_document(doc)
        self.app.store.save_documents()
        self.app.store.save_fragments()

        if self.app.current_doc and self.app.current_doc["id"] == doc["id"]:
            remaining = self.app.store.validated_documents()
            self.app.set_current_doc(remaining[0] if remaining else None)
            if self.app.current_doc:
                self.app.load_document()
            else:
                self.app.clear_text_view()

        self.populate()
        self.refresh_documents_window()

    def rename_document(self, doc):
        if not doc:
            return
        new_title = simpledialog.askstring(
            "Rename Document", "New name:", initialvalue=doc.get("title", ""), parent=self.frame
        )
        if not new_title or not new_title.strip():
            return
        doc["title"] = new_title.strip()
        self.app.store.save_documents()
        self.populate()
        self.refresh_documents_window()
        self.app.refresh_tab(self.app.analysis_tab)

    def edit_document_attributes(self, doc=None):
        if doc is None:
            sel = self.tree.selection()
            if not sel:
                return
            doc = next((d for d in self.app.store.documents if d["id"] == sel[0]), None)
        if doc:
            self.show_document_attributes_dialog(doc)

    def show_document_attributes_dialog(self, doc):
        schema = self.app.store.doc_attributes.get("schema", [])

        def on_save(doc, values):
            doc.setdefault("attributes", {}).update(values)
            self.app.store.save_documents()
            self.populate()
            self.refresh_documents_window()
            self.app.refresh_tab(self.app.analysis_tab)
            messagebox.showinfo("Saved", "Document attributes saved")

        dialogs.show_document_attributes_dialog(
            self.frame, doc, schema, on_save=on_save, on_manage_schema=self.manage_document_schema,
            resolve_value=lambda name: self.app.store.attribute_value(doc, name),
        )

    def manage_document_schema(self):
        if not self.app.require_project():
            return

        schema = self.app.store.doc_attributes["schema"]

        def on_add(entry):
            schema.append(entry)
            self.app.store.save_attributes()
            self.populate()
            self.refresh_documents_window()
            self.app.refresh_tab(self.app.analysis_tab)

        def on_remove(name):
            schema[:] = [e for e in schema if e["name"] != name]
            self.app.store.save_attributes()
            self.populate()
            self.refresh_documents_window()
            self.app.refresh_tab(self.app.analysis_tab)

        dialogs.manage_document_schema_dialog(
            self.frame, schema, on_add=on_add, on_remove=on_remove,
            on_import=lambda parent: self.import_document_attributes(parent),
            on_export=lambda parent: self.export_document_attributes(parent),
        )

    def export_document_attributes(self, parent=None):
        """Export every (non-discarded) document's attribute values, one
        row per document. The .xlsx also records each field's type, so it
        can be edited and imported back without losing them."""
        if not self.app.require_project():
            return
        parent = parent or self.frame
        store = self.app.store

        if not store.doc_attributes.get("schema"):
            messagebox.showwarning("Nothing to export", "This project has no document attributes yet.", parent=parent)
            return

        file_path = filedialog.asksaveasfilename(
            parent=parent,
            title="Export Document Attributes",
            initialfile=f"{store.path.name}_attributes.xlsx",
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx"), ("CSV (values only)", "*.csv")],
        )
        if not file_path:
            return

        try:
            count = attribute_io.export_attributes(store, Path(file_path))
        except Exception as e:
            messagebox.showerror("Export error", f"Could not export attributes:\n{e}", parent=parent)
            return
        messagebox.showinfo(
            "Attributes exported",
            f"Exported {len(store.doc_attributes['schema'])} attribute(s) for {count} document(s) to:\n{file_path}",
            parent=parent,
        )

    def import_document_attributes(self, parent=None):
        """Import a spreadsheet's columns as document-attribute fields and
        its rows as the values of the documents they name."""
        if not self.app.require_project():
            return
        parent = parent or self.frame

        file_path = filedialog.askopenfilename(
            parent=parent,
            title="Import Document Attributes",
            filetypes=[("Spreadsheets", "*.xlsx *.xlsm *.csv"), ("Excel", "*.xlsx *.xlsm"), ("CSV", "*.csv")],
        )
        if not file_path:
            return
        file_path = Path(file_path)

        store = self.app.store
        try:
            table = attribute_io.read_table(file_path)
        except Exception as e:
            messagebox.showerror("Import error", f"Could not read the file:\n{e}", parent=parent)
            return
        if not table.rows:
            messagebox.showwarning("Nothing to import", "The file has a header row but no data rows.", parent=parent)
            return

        plan = dialogs.import_attributes_dialog(
            parent, file_path.name, table.headers,
            build_plan=lambda key_index: attribute_io.build_plan(table, store, key_index),
            count_matches=lambda key_index: attribute_io.count_matches(table, store, key_index),
        )
        if plan is None:
            return

        result = attribute_io.apply_import(store, table, plan)
        store.save_attributes()
        store.save_documents()
        self.populate()
        self.refresh_documents_window()
        self.app.refresh_tab(self.app.analysis_tab)

        lines = [
            f"New fields: {len(result.fields_created)}",
            f"Documents updated: {result.documents_updated}",
            f"Values set: {result.values_set}",
        ]
        if result.unmatched_keys:
            sample = ", ".join(result.unmatched_keys[:10])
            more = f" (+{len(result.unmatched_keys) - 10} more)" if len(result.unmatched_keys) > 10 else ""
            lines.append(f"\nRows with no matching document ({len(result.unmatched_keys)}): {sample}{more}")
        if result.invalid_values:
            sample = "\n  ".join(result.invalid_values[:10])
            more = f"\n  (+{len(result.invalid_values) - 10} more)" if len(result.invalid_values) > 10 else ""
            lines.append(f"\nValues skipped (wrong type) ({len(result.invalid_values)}):\n  {sample}{more}")
        messagebox.showinfo("Attributes imported", "\n".join(lines), parent=parent)

    # -- fragments list ----------------------------------------------------------
    def clear_fragments(self):
        self.frag_list.delete(0, tk.END)

    def set_fragment_rows(self, rows: list[str]):
        self.frag_list.delete(0, tk.END)
        for row in rows:
            self.frag_list.insert("end", row)

    def selected_fragment_id(self) -> str | None:
        sel = self.frag_list.curselection()
        if not sel:
            return None
        return self.frag_list.get(sel[0]).split(" | ", 1)[0]

    def on_fragment_right_click(self, event):
        index = self.frag_list.nearest(event.y)
        if index < 0:
            return
        # Select the row under the cursor without firing <<ListboxSelect>>,
        # which would open the fragment details dialog.
        self.frag_list.selection_clear(0, tk.END)
        self.frag_list.selection_set(index)

        menu = tk.Menu(self.frame, tearoff=0)
        menu.add_command(label="Uncode Fragment", command=self.uncode_selected_fragment)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def uncode_selected_fragment(self):
        frag_id = self.selected_fragment_id()
        fragment = next((f for f in self.app.store.fragments if f["id"] == frag_id), None)
        if fragment:
            self.app.uncode_fragment(fragment)

    # -- standalone documents window ---------------------------------------------
    def show_documents_window(self):
        if not self.app.require_project():
            return

        if self.documents_window and self.documents_window.winfo_exists():
            self.documents_window.lift()
            self.refresh_documents_window()
            return

        doc_window = tk.Toplevel(self.frame)
        self.documents_window = doc_window
        doc_window.title("Documents")
        doc_window.transient(self.frame)
        doc_window.protocol("WM_DELETE_WINDOW", self.close_documents_window)

        doc_frame = tk.Frame(doc_window)
        doc_frame.pack(fill="both", expand=True, padx=5, pady=5)

        tree = ttk.Treeview(doc_frame)
        self.documents_window_tree = tree
        widgets.add_scrollbars(doc_frame, tree, horizontal=True)

        self._render_documents_window_tree(tree)

        def on_doc_select(event):
            sel = tree.selection()
            if sel:
                doc = next((d for d in self.app.store.documents if d["id"] == sel[0]), None)
                if doc:
                    self.app.set_current_doc(doc)
                    self.app.load_document()

        tree.bind("<<TreeviewSelect>>", on_doc_select)
        tree.bind("<Button-3>", lambda e: self.on_right_click(e, tree))

        widgets.center_dialog(doc_window, self.frame, width=700, height=400)

    def _render_documents_window_tree(self, tree):
        schema = self.app.store.doc_attributes.get("schema", [])
        attr_names = [entry["name"] for entry in schema]
        columns = ["title"] + attr_names
        tree.configure(columns=columns, show="headings")
        for col in columns:
            tree.heading(col, text=col.title())
            tree.column(col, width=100)

        tree.delete(*tree.get_children())
        for doc in self.app.store.validated_documents():
            row_values = [doc.get("title", "")]
            for name in attr_names:
                row_values.append(self.app.store.attribute_value(doc, name))
            tree.insert("", "end", iid=doc["id"], values=row_values)
        widgets.autosize_treeview_columns(tree)

    def close_documents_window(self):
        if self.documents_window:
            try:
                self.documents_window.destroy()
            except Exception:
                pass
        self.documents_window = None
        self.documents_window_tree = None

    def refresh_documents_window(self):
        if not self.documents_window or not self.documents_window.winfo_exists() or not self.documents_window_tree:
            return
        self._render_documents_window_tree(self.documents_window_tree)
