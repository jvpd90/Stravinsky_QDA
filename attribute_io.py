"""Bulk export/import of document attributes as a spreadsheet (.xlsx/.csv).

Export writes one row per document and one column per schema field
("Document" first), plus -- for .xlsx -- an "Attribute Types" sheet
recording each field's type (and a computed field's formula), so the
file can be edited and imported straight back without losing types.

Import turns every column of a sheet (other than the one that
identifies the document) into a field in the document-attribute schema,
using the declared type when the file carries one and inferring it from
the values otherwise. Each row is matched to an existing document by
title (or by the original file name it was imported from), and its
cells become that document's attribute values.

No tkinter dependency: the UI reads the table with read_table(), builds
an AttributeImportPlan (optionally letting the user adjust it), then
calls apply_import(). Caller is responsible for saving afterwards.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook

from .attribute_formulas import OPERATIONS
from .excel_style import style_body_cells, style_header_row
from .storage import ProjectStore

EXPORT_SHEET_NAME = "Document Attributes"
TYPES_SHEET_NAME = "Attribute Types"
TYPES_HEADERS = ["Field", "Type", "Formula"]
DOCUMENT_HEADER = "Document"
# Excel in Portuguese locales splits CSV on ";" -- import sniffs both.
CSV_DELIMITER = ";"

IMPORTABLE_TYPES = ("string", "integer", "date")


@dataclass
class Table:
    headers: list[str]
    rows: list[list]  # raw cell values, each row padded to len(headers)
    # field name -> schema entry, from an exported "Attribute Types" sheet
    declared: dict[str, dict] = field(default_factory=dict)


@dataclass
class ColumnPlan:
    index: int
    header: str
    type: str
    include: bool = True
    existing: bool = False  # already a schema field (its type is kept)
    declared: bool = False  # type comes from the file's "Attribute Types" sheet
    formula: dict | None = None  # computed fields recreated from an export


@dataclass
class AttributeImportPlan:
    key_index: int
    columns: list[ColumnPlan]


@dataclass
class AttributeImportResult:
    fields_created: list[str] = field(default_factory=list)
    documents_updated: int = 0
    values_set: int = 0
    unmatched_keys: list[str] = field(default_factory=list)
    invalid_values: list[str] = field(default_factory=list)  # "Field (row N): value"


# -- reading -------------------------------------------------------------
def read_table(file_path: Path) -> Table:
    """Read the header row + data rows from an .xlsx or .csv file.

    For workbooks, the "Document Attributes" sheet is used when present
    (round-trip with the Excel export), otherwise the first sheet.
    Columns with an empty header are dropped; duplicate headers get a
    " (2)", " (3)"... suffix so each one can still become its own field.
    """
    suffix = file_path.suffix.lower()
    declared: dict[str, dict] = {}
    if suffix == ".csv":
        raw_rows = _read_csv(file_path)
    elif suffix in (".xlsx", ".xlsm"):
        raw_rows, declared = _read_xlsx(file_path)
    else:
        raise ValueError(f"Unsupported file type: {suffix}. Use .xlsx or .csv.")

    if not raw_rows:
        raise ValueError("The selected file is empty.")

    header_row = raw_rows[0]
    kept: list[tuple[int, str]] = []
    seen: dict[str, int] = {}
    for idx, value in enumerate(header_row):
        header = _cell_text(value)
        if not header:
            continue
        seen[header] = seen.get(header, 0) + 1
        if seen[header] > 1:
            header = f"{header} ({seen[header]})"
        kept.append((idx, header))

    if len(kept) < 2:
        raise ValueError(
            "The file needs a header row with at least two columns: "
            "one identifying the document and one or more attributes."
        )

    rows = []
    for raw in raw_rows[1:]:
        row = [raw[idx] if idx < len(raw) else None for idx, _ in kept]
        if all(_cell_text(v) == "" for v in row):
            continue
        rows.append(row)

    return Table(headers=[h for _, h in kept], rows=rows, declared=declared)


def _read_xlsx(file_path: Path) -> tuple[list[tuple], dict[str, dict]]:
    wb = load_workbook(file_path, read_only=True, data_only=True)
    try:
        ws = wb[EXPORT_SHEET_NAME] if EXPORT_SHEET_NAME in wb.sheetnames else wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
        declared = (
            _read_declared_types(wb[TYPES_SHEET_NAME]) if TYPES_SHEET_NAME in wb.sheetnames else {}
        )
        return rows, declared
    finally:
        wb.close()


def _read_declared_types(ws) -> dict[str, dict]:
    """Schema entries from an exported "Attribute Types" sheet. Rows with
    an unknown type, or a computed formula that doesn't parse into a
    known operation, are ignored -- the column then falls back to type
    inference like any other sheet."""
    declared: dict[str, dict] = {}
    for row in list(ws.iter_rows(values_only=True))[1:]:
        name, attr_type, formula_raw = (list(row) + [None, None, None])[:3]
        name, attr_type = _cell_text(name), _cell_text(attr_type).lower()
        if not name:
            continue
        if attr_type in IMPORTABLE_TYPES:
            declared[name] = {"name": name, "type": attr_type}
        elif attr_type == "computed":
            try:
                formula = json.loads(_cell_text(formula_raw))
                if formula.get("op") not in OPERATIONS:
                    continue
            except (ValueError, AttributeError):
                continue
            declared[name] = {"name": name, "type": "computed", "formula": formula}
    return declared


def _read_csv(file_path: Path) -> list[list[str]]:
    # utf-8-sig strips the BOM Excel adds when saving "CSV UTF-8"; fall
    # back to cp1252 for plain "CSV" saved by Excel on Windows.
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = file_path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Could not decode the CSV file (expected UTF-8 or Windows-1252).")

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(text.splitlines(), dialect)]


# -- value normalization / type inference -----------------------------------
def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _as_integer(value) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else None
    text = _cell_text(value)
    try:
        return str(int(text))
    except ValueError:
        return None


def _as_date(value) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _cell_text(value)
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def convert_value(value, attr_type: str) -> str | None:
    """Normalize a raw cell to the string stored for a field of
    `attr_type`. Returns None when the value doesn't fit that type."""
    if attr_type == "integer":
        return _as_integer(value)
    if attr_type == "date":
        return _as_date(value)
    return _cell_text(value)


def infer_type(values: list) -> str:
    """Pick the narrowest type every non-empty value fits: integer, then
    date, falling back to string (also for all-empty columns)."""
    non_empty = [v for v in values if _cell_text(v) != ""]
    if not non_empty:
        return "string"
    if all(_as_integer(v) is not None for v in non_empty):
        return "integer"
    if all(_as_date(v) is not None for v in non_empty):
        return "date"
    return "string"


# -- planning ------------------------------------------------------------------
def guess_key_index(table: Table, store: ProjectStore) -> int:
    """The column whose values match the most documents; ties (including
    no matches at all) go to the leftmost column."""
    index = _document_index(store)
    best_idx, best_hits = 0, -1
    for idx in range(len(table.headers)):
        hits = sum(1 for row in table.rows if _normalize_key(row[idx]) in index)
        if hits > best_hits:
            best_idx, best_hits = idx, hits
    return best_idx


def build_plan(table: Table, store: ProjectStore, key_index: int | None = None) -> AttributeImportPlan:
    if key_index is None:
        key_index = guess_key_index(table, store)

    columns = []
    for idx, header in enumerate(table.headers):
        if idx == key_index:
            continue
        existing = store.schema_entry(header)
        if existing is not None:
            # Computed fields have no stored values to overwrite.
            include = existing.get("type") != "computed"
            columns.append(ColumnPlan(idx, header, existing.get("type", "string"), include, existing=True))
        elif header in table.declared:
            entry = table.declared[header]
            columns.append(ColumnPlan(idx, header, entry["type"], declared=True, formula=entry.get("formula")))
        else:
            values = [row[idx] for row in table.rows]
            columns.append(ColumnPlan(idx, header, infer_type(values)))
    return AttributeImportPlan(key_index=key_index, columns=columns)


def count_matches(table: Table, store: ProjectStore, key_index: int) -> tuple[int, int]:
    """(rows that match at least one document, total rows)."""
    index = _document_index(store)
    matched = sum(1 for row in table.rows if _normalize_key(row[key_index]) in index)
    return matched, len(table.rows)


# -- matching -------------------------------------------------------------------
def _normalize_key(value) -> str:
    return " ".join(_cell_text(value).split()).casefold()


def _document_index(store: ProjectStore) -> dict[str, list[dict]]:
    """Every key a row may use to refer to a document: its title, and
    the file name (with and without extension) it was imported from,
    so a sheet listing "entrevista_01.docx" still finds the document
    titled "entrevista_01"."""
    index: dict[str, list[dict]] = {}

    def add(key: str, doc: dict) -> None:
        key = _normalize_key(key)
        if key and doc not in index.setdefault(key, []):
            index[key].append(doc)

    for doc in store.documents:
        add(doc.get("title", ""), doc)
        source = doc.get("imported_from")
        if source:
            source_path = Path(source)
            add(source_path.name, doc)
            add(source_path.stem, doc)
    return index


# -- applying ----------------------------------------------------------------------
def apply_import(store: ProjectStore, table: Table, plan: AttributeImportPlan) -> AttributeImportResult:
    """Add any new fields to the schema and write each matched row's
    values onto its document(s).

    Empty cells leave a document's existing value untouched, so a
    partially filled sheet never wipes data. Values that don't fit their
    field's type are skipped and reported rather than stored. A new
    computed field is recreated from its formula and takes no values
    (they're re-derived from its operands). Mutates store.doc_attributes
    and store.documents; caller saves.
    """
    result = AttributeImportResult()
    schema = store.doc_attributes.setdefault("schema", [])
    included = [c for c in plan.columns if c.include]

    # Plain fields first, so a recreated computed field's operands exist.
    for col in sorted(included, key=lambda c: c.type == "computed"):
        if store.schema_entry(col.header) is not None:
            continue
        if col.type == "computed":
            schema.append({"name": col.header, "type": "computed", "formula": col.formula})
        else:
            schema.append({"name": col.header, "type": col.type})
        result.fields_created.append(col.header)

    columns = [c for c in included if c.type != "computed"]

    index = _document_index(store)
    updated_ids: set[str] = set()

    for row_number, row in enumerate(table.rows, start=2):
        key_raw = _cell_text(row[plan.key_index])
        if not key_raw:
            continue
        docs = index.get(_normalize_key(key_raw))
        if not docs:
            result.unmatched_keys.append(key_raw)
            continue

        values: dict[str, str] = {}
        for col in columns:
            raw = row[col.index]
            if _cell_text(raw) == "":
                continue
            converted = convert_value(raw, col.type)
            if converted is None:
                result.invalid_values.append(f"{col.header} (row {row_number}): {_cell_text(raw)}")
                continue
            values[col.header] = converted

        for doc in docs:
            if values:
                doc.setdefault("attributes", {}).update(values)
                updated_ids.add(doc["id"])
                result.values_set += len(values)

    result.documents_updated = len(updated_ids)
    return result


# -- exporting ---------------------------------------------------------------------
def exportable_documents(store: ProjectStore) -> list[dict]:
    """Every document except those discarded in the validation board."""
    return [d for d in store.documents if store.document_validation_status_of(d) != "discarded"]


def _attribute_rows(store: ProjectStore, documents: list[dict]) -> tuple[list[str], list[list[str]]]:
    names = [entry["name"] for entry in store.doc_attributes.get("schema", [])]
    rows = [[doc.get("title", "")] + [store.attribute_value(doc, n) for n in names] for doc in documents]
    return [DOCUMENT_HEADER] + names, rows


def write_attribute_sheets(wb: Workbook, store: ProjectStore, documents: list[dict]) -> None:
    """Add the "Document Attributes" and "Attribute Types" sheets to `wb`.
    Shared with export_excel so both exports produce the same layout."""
    headers, rows = _attribute_rows(store, documents)

    ws = wb.create_sheet(title=EXPORT_SHEET_NAME)
    ws.append(headers)
    for row in rows:
        ws.append(row)
    style_header_row(ws)
    style_body_cells(ws)
    ws.column_dimensions["A"].width = 30
    for idx in range(2, len(headers) + 1):
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = 20
    ws.freeze_panes = "B2"

    types_ws = wb.create_sheet(title=TYPES_SHEET_NAME)
    types_ws.append(TYPES_HEADERS)
    for entry in store.doc_attributes.get("schema", []):
        formula = entry.get("formula")
        types_ws.append([
            entry["name"],
            entry.get("type", "string"),
            json.dumps(formula, ensure_ascii=False) if formula else "",
        ])
    style_header_row(types_ws)
    style_body_cells(types_ws)
    types_ws.column_dimensions["A"].width = 30
    types_ws.column_dimensions["B"].width = 12
    types_ws.column_dimensions["C"].width = 60


def export_attributes(store: ProjectStore, file_path: Path, documents: list[dict] | None = None) -> int:
    """Write document attributes to .xlsx (with field types) or .csv
    (values only). Computed fields are exported with their current
    values. Returns the number of documents written."""
    if documents is None:
        documents = exportable_documents(store)

    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        headers, rows = _attribute_rows(store, documents)
        # utf-8-sig so Excel detects the encoding and accents survive.
        with file_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=CSV_DELIMITER)
            writer.writerow(headers)
            writer.writerows(rows)
    elif suffix in (".xlsx", ".xlsm"):
        wb = Workbook()
        wb.remove(wb.active)
        write_attribute_sheets(wb, store, documents)
        wb.save(file_path)
    else:
        raise ValueError(f"Unsupported file type: {suffix}. Use .xlsx or .csv.")
    return len(documents)
