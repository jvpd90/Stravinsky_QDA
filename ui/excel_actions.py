"""Fragment workbook actions (export, sync import, append new fragments),
reached from the codes panel's Excel menu and the File menu. Dialogs and
messages only; the logic lives in export_excel.py, the user-facing rules
in help/excel_import_rules.txt.
"""
from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox

from .. import export_excel, import_log

EXCEL_FILETYPES = [("Excel files", "*.xlsx"), ("All files", "*.*")]


def export_fragments(app) -> None:
    if not app.require_project():
        return
    if not app.store.fragments:
        messagebox.showinfo("No Fragments", "No fragments to export")
        return
    file_path = filedialog.asksaveasfilename(
        title="Export Fragments to Excel", defaultextension=".xlsx", filetypes=EXCEL_FILETYPES,
    )
    if not file_path:
        return
    try:
        export_excel.export_to_excel(app.store, Path(file_path))
    except Exception as e:
        messagebox.showerror("Export Error", f"Failed to export: {str(e)}")
        return
    messagebox.showinfo("Exported", f"Fragments exported to:\n{file_path}")


def import_fragments(app) -> None:
    """Sync an exported workbook back onto the project by Fragment ID."""
    if not app.require_project():
        return
    file_path = filedialog.askopenfilename(
        title="Update Fragments from Excel", defaultextension=".xlsx", filetypes=EXCEL_FILETYPES,
    )
    if not file_path:
        return

    def confirm(preview):
        level_changes = "".join(f"\n    {change}" for change in preview.level_changes)
        return messagebox.askyesno(
            "Confirm Changes",
            "The workbook is the rule for the codification. Importing it will:\n"
            f"Delete fragments (row removed): {preview.fragments_deleted}\n"
            f"Unlink codes from fragments: {preview.links_removed}\n"
            f"Clear memos: {preview.memos_cleared}\n"
            f"Clear attribute values: {preview.attribute_values_cleared}\n"
            f"Move labels to another parent: {preview.labels_reparented}\n"
            f"Change level hierarchy: {len(preview.level_changes)}{level_changes}\n\n"
            "Continue with the import?",
            icon="warning",
        )

    try:
        result = export_excel.import_from_excel(app.store, Path(file_path), confirm=confirm)
    except Exception as e:
        messagebox.showerror("Import Error", f"Failed to import Excel file:\n{str(e)}")
        return
    if result.cancelled:
        return

    _save(app, result)
    new_levels = f"\nNew levels: {', '.join(result.levels_created)}" if result.levels_created else ""
    if result.synced:
        message = (
            f"Excel export synced onto the project.\n"
            f"Fragments updated: {result.updated_fragments}\n"
            f"Fragments deleted: {result.deleted_fragments}\n"
            f"Codes unlinked: {result.removed_links}\n"
            f"New codes: {result.imported_codes}\n"
            f"Memos changed: {result.memos_updated}"
            + new_levels
            + (f"\nLevel hierarchy changed: {'; '.join(result.levels_reparented)}" if result.levels_reparented else "")
            + (f"\nLabels moved to another parent: {result.labels_reparented}" if result.labels_reparented else "")
            + (
                f"\nRows skipped (blank or unknown Fragment ID): {result.skipped_rows}"
                "\n  To add rows without an ID as new fragments, use Excel > Append New Fragments."
                if result.skipped_rows else ""
            )
            + (f"\nFragment text edits ignored: {result.text_edits_ignored}" if result.text_edits_ignored else "")
        )
    else:
        message = (
            f"Excel export imported successfully.\n"
            f"Documents: {result.imported_docs}\n"
            f"Codes: {result.imported_codes}\n"
            f"Fragments: {result.imported_fragments}"
            + new_levels
        )
    messagebox.showinfo("Imported", message)


def append_fragments(app) -> None:
    """Add the workbook's rows without a Fragment ID as new fragments."""
    if not app.require_project():
        return
    file_path = filedialog.askopenfilename(
        title="Append New Fragments from Excel", defaultextension=".xlsx", filetypes=EXCEL_FILETYPES,
    )
    if not file_path:
        return
    try:
        result = export_excel.append_from_excel(app.store, Path(file_path))
    except Exception as e:
        messagebox.showerror("Import Error", f"Failed to read Excel file:\n{str(e)}")
        return

    _save(app, result)
    messagebox.showinfo(
        "New Fragments",
        f"Fragments added: {result.imported_fragments}\n"
        f"New codes: {result.imported_codes}"
        + (f"\nNew levels: {', '.join(result.levels_created)}" if result.levels_created else "")
        + (
            f"\nRows skipped: {result.skipped_rows} (document or text not found, or already a fragment)"
            "\n  See Help > Open Import Log for each row and its reason."
            if result.skipped_rows else ""
        ),
    )


def _save(app, result) -> None:
    app.store.save_all()
    import_log.append(app.store, result.log_lines)
    app.refresh_all()
