"""Excel export/import for fragments and document attributes.

No tkinter dependency: functions take a ProjectStore and a file path,
return plain results, and raise ValueError/OSError on failure. The UI
layer is responsible for file dialogs and displaying errors/results.

The rules users see are in help/excel_import_rules.txt -- keep it in step
with the import behavior here.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.utils import get_column_letter

from . import import_log
from .attribute_io import write_attribute_sheets
from .codebook_io import ensure_levels_from_workbook, levels_declared_in_workbook, write_levels_sheet
from .colors import color_for_index
from .excel_style import style_body_cells, style_header_row
from .models import now, uid
from .storage import ProjectStore

# Current layout, one row per fragment:
#   Fragment ID | Document | <one column per codebook level> | Fragment Text | Memo | <fragment attributes>
# A level column is headed by the level's name and lists the fragment's
# labels on that level. Any column that isn't fixed or a level is a
# fragment attribute.
FRAGMENT_ID_HEADER = "Fragment ID"
DOCUMENT_HEADER = "Document"
TEXT_HEADER = "Fragment Text"
MEMO_HEADER = "Memo"
FIXED_HEADERS = [FRAGMENT_ID_HEADER, DOCUMENT_HEADER, TEXT_HEADER, MEMO_HEADER]
# A level whose name clashes with a fixed header is written as
# "Level: <name>"; that prefix also marks a new level on import.
LEVEL_HEADER_PREFIX = "Level: "
# Older exports: "Document | Code(s) | Fragment Text" first, one row per
# fragment-code pair. Still accepted on import, merged add-only.
LEGACY_HEADERS = (["Document", "Code", "Fragment Text"], ["Document", "Codes", "Fragment Text"])
# Columns those exports computed for filtering; never stored as attributes.
DERIVED_HEADERS = ["Fragment ID", "Codes on Fragment", "Other Codes"]
PATH_SEPARATOR = " › "


def excel_safe(value: str) -> str:
    """Strip control characters openpyxl refuses to write.

    PDF-extracted text carries a form feed (\\f) at every page break, so a
    fragment spanning two pages would otherwise abort the whole export.
    Page breaks become line breaks; other control characters are dropped.
    """
    return ILLEGAL_CHARACTERS_RE.sub("", value.replace("\f", "\n"))


def level_header(level: dict) -> str:
    name = level["name"]
    if name.lower() in {h.lower() for h in FIXED_HEADERS}:
        return LEVEL_HEADER_PREFIX + name
    return name


def fragment_text(store: ProjectStore, frag: dict, doc_texts: dict[str, str]) -> str:
    """The fragment's text, "-" if its document or bounds are missing.
    `doc_texts` caches document texts across calls."""
    doc = next((d for d in store.documents if d["id"] == frag["document_id"]), None)
    if doc is None or frag.get("start") is None or frag.get("end") is None:
        return "-"
    if doc["id"] not in doc_texts:
        doc_texts[doc["id"]] = store.read_document_text(doc)
    return doc_texts[doc["id"]][frag["start"]:frag["end"]].strip()


def build_fragment_rows(store: ProjectStore) -> tuple[list[str], list[list[str]]]:
    """(headers, rows) for the Fragments sheet: one row per fragment, in
    document order, with its labels listed per level."""
    levels = [level for level, _ in store.ordered_levels()]
    attributes = [a for a in store.fragment_attributes.get("schema", []) if a not in FIXED_HEADERS]
    headers = [FRAGMENT_ID_HEADER, DOCUMENT_HEADER] + [level_header(lv) for lv in levels] + [
        TEXT_HEADER, MEMO_HEADER,
    ] + attributes

    doc_order = {d["id"]: i for i, d in enumerate(store.documents)}
    doc_titles = {d["id"]: d["title"] for d in store.documents}
    doc_texts: dict[str, str] = {}
    rows = []
    for frag in sorted(store.fragments, key=lambda f: (doc_order.get(f["document_id"], len(doc_order)), f.get("start") or 0)):
        by_level = store.labels_by_level(frag["id"])
        memo = store.memo_for(frag["id"])
        extra = frag.get("extra_attributes") or {}
        rows.append(
            [frag["id"], doc_titles.get(frag["document_id"], "Unknown")]
            # One label per line: names may contain commas (see parse_excel_code_names).
            + ["\n".join(c["name"] for c in by_level.get(lv["id"], [])) for lv in levels]
            + [fragment_text(store, frag, doc_texts), memo["content"] if memo else ""]
            + [str(extra.get(a, "")) for a in attributes]
        )
    return headers, rows


def export_to_excel(store: ProjectStore, file_path: Path) -> None:
    """Write fragments + document attributes to an .xlsx file."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Fragments"

    headers, rows = build_fragment_rows(store)
    ws.append(headers)
    style_header_row(ws)
    widths = {FRAGMENT_ID_HEADER: 16, DOCUMENT_HEADER: 25, TEXT_HEADER: 60, MEMO_HEADER: 40}
    for i, header in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(header, 22)

    for row in rows:
        ws.append([excel_safe(str(v)) for v in row])

    style_body_cells(ws)
    for row_idx in range(2, ws.max_row + 1):
        ws.row_dimensions[row_idx].height = 80
    ws.auto_filter.ref = ws.dimensions

    write_attribute_sheets(wb, store, store.documents)
    write_levels_sheet(wb, store)

    wb.save(file_path)


# Separators a single-line cell may use between existing labels.
LIST_SEPARATOR_RE = re.compile(r"\s*[,;|]\s*|\s+/\s+")


def parse_excel_code_names(raw_value, known_names=()) -> list[str]:
    """Label names in a cell, each trimmed. Label names may themselves
    contain , ; | or " / " ("Diagnosis / attribution of causes"), so:

    - each line of the cell is read on its own (the export writes one
      label per line);
    - a line that is a known name is one label;
    - otherwise it is split at those separators only where a piece is a
      known name, and the text between known names stays whole as one
      (new) name -- never cut into pieces.

    `known_names` are the names the cell's labels may already have
    (case-insensitive). "-" (an uncoded fragment in older exports) is
    skipped."""
    if raw_value is None:
        return []
    known = {str(n).strip().lower() for n in known_names}
    names = []
    for line in str(raw_value).splitlines():
        line = line.strip()
        if line and line != "-":
            names += _split_line(line, known)
    return list(dict.fromkeys(names))


def _split_line(line: str, known: set[str]) -> list[str]:
    if line.lower() in known:
        return [line]
    tokens = LIST_SEPARATOR_RE.split(line)
    separators = LIST_SEPARATOR_RE.findall(line)

    def span(i: int, j: int) -> str:  # tokens i..j with their original separators
        return "".join(tokens[k] + (separators[k] if k < j else "") for k in range(i, j + 1)).strip()

    names, unknown_from, i = [], None, 0
    while i < len(tokens):
        end = next((j for j in range(len(tokens) - 1, i - 1, -1) if span(i, j).lower() in known), None)
        if end is None:
            unknown_from = i if unknown_from is None else unknown_from
            i += 1
            continue
        if unknown_from is not None:
            names.append(span(unknown_from, i - 1))
            unknown_from = None
        names.append(span(i, end))
        i = end + 1
    if unknown_from is not None:
        names.append(span(unknown_from, len(tokens) - 1))
    return [n for n in names if n and n != "-"]


def find_or_create_label(store: ProjectStore, name: str) -> tuple[dict | None, bool]:
    """Resolve a bare label name (from an Excel cell or a validation card)
    to a label on any level; an unknown name becomes a new label on the
    store's default level. Returns (label, created); label is None only
    if the codebook has no level to put it in."""
    if PATH_SEPARATOR in name:
        # A "Tema A › Subtema A1" path, as shown on validation cards.
        path = PATH_SEPARATOR.join(part.strip() for part in name.split(PATH_SEPARATOR)).lower()
        label = next((c for c in store.codes if store.label_path(c).lower() == path), None)
        if label is not None:
            return label, False
        name = name.split(PATH_SEPARATOR)[-1].strip()
    label = store.find_label(name)
    if label is not None:
        return label, False
    level = store.default_level()
    if level is None:
        return None, False
    return store.add_label(name, level["id"], color=color_for_index(len(store.codes))), True


@dataclass
class ImportResult:
    imported_docs: int = 0
    imported_codes: int = 0
    imported_fragments: int = 0
    levels_created: list[str] = field(default_factory=list)
    # Sync mode only (current layout).
    synced: bool = False
    updated_fragments: int = 0
    deleted_fragments: int = 0
    removed_links: int = 0
    memos_updated: int = 0
    levels_reparented: list[str] = field(default_factory=list)
    labels_reparented: int = 0
    skipped_rows: int = 0
    text_edits_ignored: int = 0
    cancelled: bool = False
    # Lines for the project's import log (see import_log); the caller
    # appends them once the store is saved.
    log_lines: list[str] = field(default_factory=list)


@dataclass
class SheetLayout:
    """Where things are in a current-layout Fragments sheet."""
    id_col: int | None = None
    document_col: int | None = None
    text_col: int | None = None
    memo_col: int | None = None
    level_columns: list[tuple[int, str]] = field(default_factory=list)  # (column, level name)
    attribute_columns: list[tuple[int, str]] = field(default_factory=list)  # (column, attribute name)


@dataclass
class SyncPreview:
    """What a sync import would remove or restructure, worked out by
    running it on a copy of the store, so the caller can ask before the
    real one is touched."""
    fragments_deleted: int = 0
    links_removed: int = 0
    memos_cleared: int = 0
    attribute_values_cleared: int = 0
    level_changes: list[str] = field(default_factory=list)  # "Issue: Theme -> (none)"
    labels_reparented: int = 0

    @property
    def needs_confirmation(self) -> bool:
        return bool(
            self.fragments_deleted or self.links_removed or self.memos_cleared
            or self.attribute_values_cleared or self.level_changes or self.labels_reparented
        )


def import_from_excel(
    store: ProjectStore, file_path: Path, confirm: Callable[[SyncPreview], bool] | None = None,
) -> ImportResult:
    """Import a previously exported QDA workbook into the given store.

    A workbook in the current layout (Fragment ID, Document first) is
    synced onto the project by fragment ID (see _sync). For the
    codification it contains, the workbook is the rule: the Levels sheet
    sets the levels' hierarchy and order, and each row sets its
    fragment's labels on every level that has a column, placed under the
    parent labels the row names. Memo and attribute cells replace the
    fragment's values, and a fragment without a row is deleted. The
    workbook can't create fragments or documents or change fragment text.
    The sync is first run on a copy of the store and `confirm` called
    with what it would remove or restructure; returning False cancels the
    import with the store untouched.

    Older layouts (Document, Code(s), Fragment Text first) are merged the
    original way (_import_row): documents matched by title, fragments
    located by text, nothing ever removed or restructured.

    Mutates the store in place and returns counts of what changed. Raises
    ValueError if the workbook doesn't look like a QDA export of this
    project, or its Levels sheet is inconsistent. Caller is responsible
    for calling store.save_all() (or the relevant save_*) afterwards.
    """
    result = ImportResult()
    wb = load_workbook(file_path, read_only=True, data_only=True)
    try:
        if "Fragments" not in wb.sheetnames:
            raise ValueError("The selected workbook is not a QDA export. Expected sheet 'Fragments'.")

        ws = wb["Fragments"]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            raise ValueError("The selected workbook is empty.")

        headers = [str(value).strip() if value is not None else "" for value in rows[0]]
        if [h.lower() for h in headers[:2]] == [FRAGMENT_ID_HEADER.lower(), DOCUMENT_HEADER.lower()]:
            declared_levels = levels_declared_in_workbook(wb)
            layout = _read_layout(store, declared_levels, headers)
            cells_by_fragment, skipped = _merge_rows(store, rows[1:], layout)

            trial = copy.deepcopy(store)
            trial_before = import_log.snapshot(trial)
            _sync(trial, declared_levels, layout, cells_by_fragment, ImportResult(), {})
            preview = _preview(trial_before, import_log.snapshot(trial))
            if confirm is not None and preview.needs_confirmation and not confirm(preview):
                result.cancelled = True
                return result

            before = import_log.snapshot(store)
            notes: dict[str, list[str]] = {}
            result.skipped_rows = len(skipped)
            _sync(store, declared_levels, layout, cells_by_fragment, result, notes)
            result.log_lines = import_log.describe(
                store, before, file_path, "sync by Fragment ID",
                [
                    f"{result.updated_fragments} fragments updated",
                    f"{result.deleted_fragments} deleted",
                    f"{result.removed_links} codes unlinked",
                    f"{result.imported_codes} new codes",
                    f"{result.memos_updated} memos changed",
                    f"{result.skipped_rows} rows skipped",
                    f"{result.text_edits_ignored} text edits ignored",
                ],
                notes,
                [f"row {number}: blank or unknown Fragment ID -- {_describe_row(row, layout)}"
                 for number, row in skipped],
            )
        elif headers[:3] in LEGACY_HEADERS:
            before = import_log.snapshot(store)
            _legacy_merge(store, wb, rows, headers, file_path, result)
            result.log_lines = import_log.describe(
                store, before, file_path, "merge (older layout: Document, Code, Fragment Text)",
                [
                    f"{result.imported_docs} new documents",
                    f"{result.imported_fragments} new fragments",
                    f"{result.imported_codes} new codes",
                ],
            )
        else:
            raise ValueError(
                "The columns do not match the export layout. The first columns must be "
                "'Fragment ID, Document' (or, in older exports, 'Document, Code, Fragment Text')."
            )
    finally:
        wb.close()

    return result


def _read_layout(store: ProjectStore, declared_levels: list[tuple[str, str]], headers: list[str]) -> SheetLayout:
    """Sort the header row into fixed, level and attribute columns. A
    column is a level if the project has a level of that name, the
    workbook's Levels sheet declares it, or it's headed "Level: <name>"."""
    declared = {name.lower() for name, _ in declared_levels}
    layout = SheetLayout()
    for idx, header in enumerate(headers):
        key = header.lower()
        if not header:
            continue
        if key == FRAGMENT_ID_HEADER.lower():
            layout.id_col = idx if layout.id_col is None else layout.id_col
        elif key == DOCUMENT_HEADER.lower():
            layout.document_col = idx if layout.document_col is None else layout.document_col
        elif key == TEXT_HEADER.lower():
            layout.text_col = idx
        elif key == MEMO_HEADER.lower():
            layout.memo_col = idx
        elif header.startswith(LEVEL_HEADER_PREFIX):
            name = header[len(LEVEL_HEADER_PREFIX):].strip()
            if name:
                layout.level_columns.append((idx, name))
        elif store.level_by_name(header) or key in declared:
            layout.level_columns.append((idx, header))
        elif header not in DERIVED_HEADERS:
            layout.attribute_columns.append((idx, header))
    return layout


def _is_blank_row(row) -> bool:
    return not row or all(value is None or str(value).strip() == "" for value in row)


def _describe_row(row, layout: SheetLayout) -> str:
    parts = [
        row[i] for i in (layout.document_col, layout.text_col) if i is not None and i < len(row) and row[i] is not None
    ]
    return " | ".join(import_log.preview(str(v)) for v in parts)


def _merge_rows(
    store: ProjectStore, rows: list, layout: SheetLayout,
) -> tuple[dict[str, dict[int, str]], list[tuple[int, tuple]]]:
    """fragment_id -> column -> cell value, plus the (worksheet row
    number, row) pairs skipped for a blank or unknown fragment ID -- new
    fragments can't be created from Excel, only coded in the app.

    Several rows with the same ID are merged: level cells are combined,
    other cells take the first non-empty value."""
    level_cols = {idx for idx, _ in layout.level_columns}
    known_ids = {f["id"] for f in store.fragments}
    collected: dict[str, dict[int, list[str]]] = {}
    skipped = []
    for row_number, row in enumerate(rows, start=2):
        if _is_blank_row(row):
            continue
        frag_id = str(row[0]).strip() if row[0] is not None else ""
        if frag_id not in known_ids:
            skipped.append((row_number, row))
            continue
        cells = collected.setdefault(frag_id, {})
        for idx, value in enumerate(row):
            if value is not None and str(value).strip():
                cells.setdefault(idx, []).append(str(value).strip())

    if store.fragments and not collected:
        raise ValueError(
            "None of the workbook's fragment IDs belong to this project, so it "
            "was probably exported from another project. Nothing was imported."
        )

    merged = {
        frag_id: {
            # Lines are split into names later, against the level's labels.
            idx: "\n".join(dict.fromkeys(line.strip() for v in values for line in v.splitlines() if line.strip()))
            if idx in level_cols else values[0]
            for idx, values in cells.items()
        }
        for frag_id, cells in collected.items()
    }
    return merged, skipped


def _preview(before: dict, after: dict) -> SyncPreview:
    preview = SyncPreview()
    for frag_id, old in before["fragments"].items():
        new = after["fragments"].get(frag_id)
        if new is None:
            preview.fragments_deleted += 1
            continue
        preview.links_removed += len(old["labels"].keys() - new["labels"].keys())
        preview.memos_cleared += int(bool(old["memo"]) and not new["memo"])
        preview.attribute_values_cleared += sum(1 for k, v in old["attributes"].items() if v and not new["attributes"].get(k))
    for level_id, (name, parent) in before["level_parents"].items():
        if level_id in after["level_parents"] and after["level_parents"][level_id][1] != parent:
            preview.level_changes.append(f"{name}: {parent or '(none)'} -> {after['level_parents'][level_id][1] or '(none)'}")
    preview.labels_reparented = sum(
        1 for label_id, (_, _, parent) in before["label_parents"].items()
        if label_id in after["label_parents"] and after["label_parents"][label_id][2] != parent
    )
    return preview


def _apply_levels(
    store: ProjectStore, declared_levels: list[tuple[str, str]], named_columns: list[tuple[int, str]],
    result: ImportResult,
) -> list[tuple[int, dict]]:
    """Make the project's levels follow the Levels sheet: create the
    missing ones, give every listed level the parent it lists (labels of
    a level whose parent changes lose their parent labels until rows
    place them again) and put the listed levels first, in sheet order.
    Levels the sheet doesn't list are left as they are; a level column
    nobody declares becomes an independent level.

    Returns (column, level) pairs, parents before children, so a row's
    parent labels are known when its child level's cell is read."""
    for name, _ in declared_levels:
        if store.level_by_name(name) is None:
            store.add_level(name)
            result.levels_created.append(name)
    for name, parent_name in declared_levels:
        level = store.level_by_name(name)
        parent = store.level_by_name(parent_name) if parent_name else None
        if parent_name and parent is None:
            raise ValueError(f"Levels sheet: the parent level '{parent_name}' of '{name}' is not a level.")
        parent_id = parent["id"] if parent else None
        if level.get("parent_level_id") != parent_id:
            old_parent = store.level(level.get("parent_level_id"))
            store.set_level_parent(level, parent_id)  # raises on a cycle
            result.levels_reparented.append(
                f"{name}: {old_parent['name'] if old_parent else '(none)'} -> {parent_name or '(none)'}"
            )
    listed = [store.level_by_name(name) for name, _ in declared_levels]
    store.levels = listed + [lv for lv in store.levels if lv not in listed]

    level_columns = []
    for idx, name in named_columns:
        level = store.level_by_name(name)
        if level is None:
            level = store.add_level(name)
            result.levels_created.append(name)
        level_columns.append((idx, level))
    order = {lv["id"]: i for i, (lv, _) in enumerate(store.ordered_levels())}
    level_columns.sort(key=lambda pair: order.get(pair[1]["id"], 0))
    return level_columns


def _resolve_level_columns(
    store: ProjectStore, wb, named_columns: list[tuple[int, str]], result: ImportResult,
) -> list[tuple[int, dict]]:
    """Older layout: only add the levels the workbook brings (Levels sheet
    first, for the hierarchy; any other level column as an independent
    level) and return (column, level) pairs, parents before children."""
    result.levels_created += ensure_levels_from_workbook(store, wb)
    level_columns = []
    for idx, name in named_columns:
        level = store.level_by_name(name)
        if level is None:
            level = store.add_level(name)
            result.levels_created.append(name)
        level_columns.append((idx, level))
    order = {lv["id"]: i for i, (lv, _) in enumerate(store.ordered_levels())}
    level_columns.sort(key=lambda pair: order.get(pair[1]["id"], 0))
    return level_columns


def _add_attribute_columns(store: ProjectStore, names: list[str]) -> None:
    schema = store.fragment_attributes.setdefault("schema", [])
    for name in names:
        if name not in schema:
            schema.append(name)


def _sync(
    store: ProjectStore, declared_levels: list[tuple[str, str]], layout: SheetLayout,
    cells_by_fragment: dict[str, dict[int, str]], result: ImportResult, notes: dict[str, list[str]],
) -> None:
    """Apply a merged workbook to the store. Fragment bounds and document
    text are never changed; an edited Fragment Text cell is only counted,
    and noted in `notes` for the import log."""
    result.synced = True
    level_columns = _apply_levels(store, declared_levels, layout.level_columns, result)
    column_level_ids = {level["id"] for _, level in level_columns}
    _add_attribute_columns(store, [name for _, name in layout.attribute_columns])

    to_delete = [f["id"] for f in store.fragments if f["id"] not in cells_by_fragment]
    for frag_id in to_delete:
        store.delete_fragment(frag_id)
    result.deleted_fragments = len(to_delete)

    doc_texts: dict[str, str] = {}
    for frag_id, cells in cells_by_fragment.items():
        frag = next(f for f in store.fragments if f["id"] == frag_id)
        links_before = {l["code_id"] for l in store.links if l["fragment_id"] == frag_id}
        attributes_before = dict(frag.get("extra_attributes") or {})

        if layout.text_col is not None:
            text = fragment_text(store, frag, doc_texts)
            cell = cells.get(layout.text_col, "")
            if text != "-" and cell != excel_safe(text).strip():
                result.text_edits_ignored += 1
                notes.setdefault(frag_id, []).append(
                    f'! text edit ignored (project text kept): "{import_log.preview(cell)}"'
                )

        # The row decides the labels on every level it has a column for;
        # labels on other levels stay. set_fragment_labels adds ancestors.
        current = store.codes_for_fragment(frag_id)
        labels = [c for c in current if c.get("level_id") not in column_level_ids]
        labels += _resolve_row_labels(store, cells, level_columns, current, result)
        store.set_fragment_labels(frag_id, list(dict.fromkeys(c["id"] for c in labels)))
        links_after = {l["code_id"] for l in store.links if l["fragment_id"] == frag_id}
        result.removed_links += len(links_before - links_after)

        for idx, name in layout.attribute_columns:
            value = cells.get(idx, "")
            if value:
                frag.setdefault("extra_attributes", {})[name] = value
            elif name in (frag.get("extra_attributes") or {}):
                del frag["extra_attributes"][name]

        if layout.memo_col is not None:
            value = cells.get(layout.memo_col, "")
            memo = store.memo_for(frag_id)
            if value and (memo is None or memo["content"] != value):
                store.set_memo(frag_id, scope="fragment", content=value)
                result.memos_updated += 1
            elif not value and memo is not None and memo["content"]:
                store.memos.remove(memo)
                result.memos_updated += 1

        if links_after != links_before or (frag.get("extra_attributes") or {}) != attributes_before:
            result.updated_fragments += 1


def _resolve_row_labels(
    store: ProjectStore, cells: dict[int, str], level_columns: list[tuple[int, dict]],
    current: list[dict], result: ImportResult,
) -> list[dict]:
    """The labels a row names, level by level (parents first). On a
    hierarchical level each name is placed under the parent labels the
    row gives for the parent level (or, if that level has no column, the
    fragment's current ones): see _resolve_label."""
    current_ids = {c["id"] for c in current}
    column_levels = {level["id"] for _, level in level_columns}
    chosen: dict[str, list[dict]] = {}
    for idx, level in level_columns:
        parent_level_id = level.get("parent_level_id")
        if not parent_level_id:
            parents = []
        elif parent_level_id in column_levels:
            parents = chosen.get(parent_level_id, [])
        else:
            parents = [c for c in current if c.get("level_id") == parent_level_id]
        known = [c["name"] for c in store.labels_of_level(level["id"])]
        for name in parse_excel_code_names(cells.get(idx), known):
            chosen.setdefault(level["id"], []).append(
                _resolve_label(store, level, name, parents, current_ids, result)
            )
    return [label for labels in chosen.values() for label in labels]


def _resolve_label(
    store: ProjectStore, level: dict, name: str, parents: list[dict], current_ids: set[str], result: ImportResult,
) -> dict:
    """Find or create the label `name` on `level` under one of `parents`.

    - Independent level: the label of that name, or a new one.
    - Parents given: a label of that name under one of them. Failing
      that, a parentless one of that name is moved under the (single)
      parent; otherwise a new label is created there, copying color and
      description from a namesake on the level. With several parents and
      no match the new label gets no parent.
    - No parents given (parent cell empty): the label of that name on the
      level, so its own parent label is kept -- a child implies its parent.
    Among several matches, a label the fragment already has wins."""
    same = [c for c in store.labels_of_level(level["id"]) if c["name"].lower() == name.lower()]
    same.sort(key=lambda c: c["id"] not in current_ids)

    if not level.get("parent_level_id") or not parents:
        if same:
            return same[0]
        return _new_label(store, name, level, None, None, result)

    parent_ids = [p["id"] for p in parents]
    under = [c for c in same if c.get("parent_id") in parent_ids]
    if under:
        return under[0]
    if len(parents) == 1:
        orphan = next((c for c in same if not c.get("parent_id")), None)
        if orphan is not None:
            orphan["parent_id"] = parents[0]["id"]
            result.labels_reparented += 1
            return orphan
    return _new_label(store, name, level, parents[0] if len(parents) == 1 else None, same[0] if same else None, result)


def _new_label(
    store: ProjectStore, name: str, level: dict, parent: dict | None, like: dict | None, result: ImportResult,
) -> dict:
    result.imported_codes += 1
    return store.add_label(
        name, level["id"], parent["id"] if parent else None,
        color=like["color"] if like else color_for_index(len(store.codes)),
        description=like.get("description", "") if like else "",
    )


def append_from_excel(store: ProjectStore, file_path: Path) -> ImportResult:
    """Add to the project the fragments of a workbook's rows that have no
    Fragment ID (rows with one are already in the project and ignored --
    the sync import updates those).

    Same layout as the export, minus the IDs: Document and Fragment Text
    are required, level, Memo and attribute columns are optional. Each
    row's text is looked up in the document of that title (case and
    surrounding/inner whitespace don't matter) and becomes a fragment
    there; the document text is never changed. A row is skipped, and
    listed in the import log, if its document doesn't exist, its text
    isn't found there, or it is already a fragment.

    Only adds: new levels (from the Levels sheet, or a new level column
    as an independent level), labels, attribute columns. Existing levels'
    hierarchy and other fragments are left alone. Caller saves and
    appends result.log_lines to the import log afterwards."""
    result = ImportResult()
    wb = load_workbook(file_path, read_only=True, data_only=True)
    try:
        if "Fragments" not in wb.sheetnames:
            raise ValueError("The selected workbook has no 'Fragments' sheet.")
        rows = list(wb["Fragments"].iter_rows(values_only=True))
        if not rows:
            raise ValueError("The selected workbook is empty.")
        headers = [str(value).strip() if value is not None else "" for value in rows[0]]
        layout = _read_layout(store, levels_declared_in_workbook(wb), headers)
        if layout.document_col is None or layout.text_col is None:
            raise ValueError(f"The 'Fragments' sheet needs a '{DOCUMENT_HEADER}' and a '{TEXT_HEADER}' column.")

        before = import_log.snapshot(store)
        level_columns = _resolve_level_columns(store, wb, layout.level_columns, result)
        _add_attribute_columns(store, [name for _, name in layout.attribute_columns])
        documents = {d["title"].strip().lower(): d for d in store.documents}
        doc_texts: dict[str, str] = {}
        skipped: list[str] = []
        with_id = 0
        for row_number, row in enumerate(rows[1:], start=2):
            if _is_blank_row(row):
                continue
            cells = {idx: str(v).strip() for idx, v in enumerate(row) if v is not None and str(v).strip()}
            if layout.id_col is not None and cells.get(layout.id_col):
                with_id += 1
                continue
            doc = documents.get(cells.get(layout.document_col, "").lower())
            text = cells.get(layout.text_col, "")
            if doc is None:
                skipped.append(f"row {row_number}: document not found -- {_describe_row(row, layout)}")
                continue
            if not text:
                skipped.append(f"row {row_number}: no fragment text -- {_describe_row(row, layout)}")
                continue
            if doc["id"] not in doc_texts:
                doc_texts[doc["id"]] = store.read_document_text(doc)
            span = _find_span(doc_texts[doc["id"]], text)
            if span is None:
                skipped.append(f"row {row_number}: text not found in the document -- {_describe_row(row, layout)}")
                continue
            if store.find_fragment(doc["id"], *span) is not None:
                skipped.append(f"row {row_number}: already a fragment -- {_describe_row(row, layout)}")
                continue

            frag = store.add_fragment(doc["id"], *span)
            result.imported_fragments += 1
            labels = _resolve_row_labels(store, cells, level_columns, [], result)
            store.set_fragment_labels(frag["id"], list(dict.fromkeys(c["id"] for c in labels)))
            attributes = {name: cells[idx] for idx, name in layout.attribute_columns if cells.get(idx)}
            if attributes:
                frag["extra_attributes"] = attributes
            if layout.memo_col is not None and cells.get(layout.memo_col):
                store.set_memo(frag["id"], scope="fragment", content=cells[layout.memo_col])

        result.skipped_rows = len(skipped)
        if with_id:
            skipped.append(f"{with_id} rows with a Fragment ID ignored (already in the project)")
        result.log_lines = import_log.describe(
            store, before, file_path, "append new fragments (rows without Fragment ID)",
            [
                f"{result.imported_fragments} fragments added",
                f"{result.imported_codes} new codes",
                f"{result.skipped_rows} rows skipped",
            ],
            skipped=skipped,
        )
    finally:
        wb.close()
    return result


def _find_span(document_text: str, text: str) -> tuple[int, int] | None:
    """Where `text` is in the document: an exact match, or else one that
    ignores case and differences in whitespace (Excel turns page breaks
    into line breaks and may collapse spaces). First occurrence wins."""
    start = document_text.find(text)
    if start != -1:
        return start, start + len(text)
    words = text.split()
    if not words:
        return None
    match = re.search(r"\s+".join(re.escape(w) for w in words), document_text, re.IGNORECASE)
    return (match.start(), match.end()) if match else None


def _legacy_merge(
    store: ProjectStore, wb, rows: list, headers: list[str], file_path: Path, result: ImportResult,
) -> None:
    """Older layout: "Level: <name>" columns assign labels (and override
    the Code column, which only repeats them); other columns beyond the
    first three, except the derived ones, are extra attributes."""
    extra_columns = [
        (idx, header)
        for idx, header in enumerate(headers)
        if idx >= 3 and header
        and header not in DERIVED_HEADERS
        and not header.startswith(LEVEL_HEADER_PREFIX)
    ]
    _add_attribute_columns(store, [name for _, name in extra_columns])
    level_columns = _resolve_level_columns(
        store, wb,
        [
            (idx, header[len(LEVEL_HEADER_PREFIX):].strip())
            for idx, header in enumerate(headers)
            if idx >= 3 and header.startswith(LEVEL_HEADER_PREFIX) and header[len(LEVEL_HEADER_PREFIX):].strip()
        ],
        result,
    )
    for row in rows[1:]:
        _import_row(store, row, file_path, result, extra_columns, level_columns)


def _import_row(
    store: ProjectStore, row, file_path: Path, result: ImportResult, extra_columns: list[tuple[int, str]],
    level_columns: list[tuple[int, dict]] = (),
) -> None:
    import hashlib

    if not row or all(value is None or str(value).strip() == "" for value in row[:3]):
        return

    doc_title = str(row[0]).strip() if row[0] is not None else ""
    code_names = parse_excel_code_names(
        row[1] if len(row) > 1 else "", [c["name"] for c in store.codes] + [store.label_path(c) for c in store.codes],
    )
    if level_columns:
        code_names = []
    fragment_text = str(row[2]).strip() if len(row) > 2 and row[2] is not None else ""

    if not doc_title:
        return

    doc = next((d for d in store.documents if d.get("title") == doc_title), None)
    if not doc:
        doc_id = uid("doc")
        filename = f"{doc_id}.txt"
        doc = {
            "id": doc_id,
            "title": doc_title,
            "filename": filename,
            "source_format": "xlsx_import",
            "imported_from": str(file_path),
            "sha256": hashlib.sha256(fragment_text.encode("utf-8")).hexdigest(),
            "created_at": now(),
            "locked": False,
            "attributes": {},
        }
        store.documents.append(doc)
        store.write_document_text(
            doc, fragment_text + ("\n" if fragment_text and not fragment_text.endswith("\n") else "")
        )
        result.imported_docs += 1
    else:
        current_text = store.read_document_text(doc)
        if fragment_text and fragment_text not in current_text:
            if current_text and not current_text.endswith("\n"):
                current_text += "\n\n"
            current_text += fragment_text
            if not current_text.endswith("\n"):
                current_text += "\n"
            store.write_document_text(doc, current_text)

    full_text = store.read_document_text(doc)
    frag_text_value = fragment_text.strip()
    start = full_text.find(frag_text_value)
    if start == -1:
        if full_text and not full_text.endswith("\n"):
            full_text += "\n\n"
        full_text += frag_text_value
        if not full_text.endswith("\n"):
            full_text += "\n"
        store.write_document_text(doc, full_text)
        start = full_text.find(frag_text_value)
    end = start + len(frag_text_value)

    frag = store.find_fragment(doc["id"], start, end)
    if frag is None:
        frag = store.add_fragment(doc["id"], start, end)
        result.imported_fragments += 1

    if extra_columns:
        extra_data = {}
        for idx, header in extra_columns:
            if idx < len(row) and row[idx] is not None:
                value = str(row[idx]).strip()
                if value:
                    extra_data[header] = value
        if extra_data:
            frag.setdefault("extra_attributes", {}).update(extra_data)

    for code_name in code_names:
        code_name = code_name.strip()
        if not code_name:
            continue

        code, created = find_or_create_label(store, code_name)
        if code is None:
            continue
        result.imported_codes += int(created)

        store.link_code(frag["id"], code["id"])

    if level_columns:
        _import_level_labels(store, frag, row, level_columns, result)


def _import_level_labels(
    store: ProjectStore, frag: dict, row, level_columns: list[tuple[int, dict]], result: ImportResult
) -> None:
    """Link the labels named in each level cell (several may be separated
    by , or ;) to the fragment.

    A label the fragment already has on that level is reused. Otherwise,
    on a hierarchical level, a label is looked up under the label given in
    the parent level's cell on the same row; if that cell names exactly one
    label and no such child exists yet, it's created under it. Without a
    usable parent cell, any label of that name on the level is used, or a
    new one without a parent is created."""
    chosen: dict[str, list[dict]] = {}  # level_id -> labels linked from this row
    for idx, level in level_columns:
        names = parse_excel_code_names(
            row[idx] if idx < len(row) else None, [c["name"] for c in store.labels_of_level(level["id"])],
        )
        parent_level_id = level.get("parent_level_id")
        parents = chosen.get(parent_level_id, []) if parent_level_id else []
        parent = parents[0] if len(parents) == 1 else None

        on_fragment = store.labels_by_level(frag["id"]).get(level["id"], [])
        for name in names:
            # Reusing the fragment's own label keeps re-importing an
            # unchanged export from creating a namesake under the row's
            # parent label for one that has no parent.
            label = next((c for c in on_fragment if c["name"].lower() == name.lower()), None)
            if label is None and parent is not None:
                label = store.find_label(name, level["id"], parent["id"])
            elif label is None:
                parent_ids = {p["id"] for p in parents}
                matches = [
                    c for c in store.labels_of_level(level["id"])
                    if c["name"].lower() == name.lower() and (not parent_ids or c.get("parent_id") in parent_ids)
                ]
                label = matches[0] if matches else None
            if label is None:
                label = store.add_label(
                    name, level["id"], parent["id"] if parent else None, color=color_for_index(len(store.codes)),
                )
                result.imported_codes += 1
            store.link_code(frag["id"], label["id"])
            chosen.setdefault(level["id"], []).append(label)
