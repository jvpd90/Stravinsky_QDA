"""Codebook (levels + labels) export to an Excel workbook, plus the
Levels sheet helpers the fragments export/import shares.

The exported workbook has a "Levels" sheet (Level | Parent Level) and a
"Labels" sheet with one row per label (Level | Label | Parent Label |
Color | Description). There is no codebook import: codebooks change in
the app, or through the fragments workbook (see export_excel).
"""
from __future__ import annotations

import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import PatternFill

from .excel_style import style_body_cells, style_header_row
from .storage import ProjectStore

LEVELS_SHEET = "Levels"
LABELS_SHEET = "Labels"

LEVELS_HEADERS = ["Level", "Parent Level"]
LABELS_HEADERS = ["Level", "Label", "Parent Label", "Color", "Description"]

HEX_COLOR_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")


def _cell(value) -> str:
    return "" if value is None else str(value).strip()


def _rows(ws) -> list[list[str]]:
    return [[_cell(v) for v in row] for row in ws.iter_rows(values_only=True)]


def _header_index(header: list[str], name: str) -> int | None:
    lowered = [h.lower() for h in header]
    return lowered.index(name.lower()) if name.lower() in lowered else None


def _read_levels_sheet(rows: list[list[str]]) -> list[tuple[str, str]]:
    if not rows:
        return []
    header = rows[0]
    li = _header_index(header, "Level")
    pi = _header_index(header, "Parent Level")
    if li is None:
        raise ValueError("The Levels sheet needs a 'Level' column.")
    levels = []
    seen = set()
    for row in rows[1:]:
        name = row[li] if li < len(row) else ""
        if not name or name.lower() in seen:
            continue
        parent = row[pi] if pi is not None and pi < len(row) else ""
        levels.append((name, parent))
        seen.add(name.lower())
    return levels


def write_levels_sheet(wb, store: ProjectStore) -> None:
    """Add a Levels sheet (Level | Parent Level) to `wb` -- also used by
    the fragments export, so its level columns can be re-imported with
    their hierarchy into a project that lacks those levels."""
    ws = wb.create_sheet(LEVELS_SHEET)
    ws.append(LEVELS_HEADERS)
    for level, _ in store.ordered_levels():
        parent = store.level(level.get("parent_level_id"))
        ws.append([level["name"], parent["name"] if parent else ""])
    style_header_row(ws)
    style_body_cells(ws)
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 28


def levels_declared_in_workbook(wb) -> list[tuple[str, str]]:
    """(level, parent level or "") pairs from a workbook's Levels sheet,
    or [] when it has none."""
    name = next((n for n in wb.sheetnames if n.lower() == LEVELS_SHEET.lower()), None)
    return _read_levels_sheet(_rows(wb[name])) if name is not None else []


def ensure_levels_from_workbook(store: ProjectStore, wb) -> list[str]:
    """Create the levels a workbook's Levels sheet declares that the store
    doesn't have yet (with their parent level, when that exists). Returns
    the names created; existing levels are left as they are."""
    created = []
    for level_name, parent_name in levels_declared_in_workbook(wb):
        if store.level_by_name(level_name):
            continue
        parent = store.level_by_name(parent_name) if parent_name else None
        store.add_level(level_name, parent["id"] if parent else None)
        created.append(level_name)
    return created


def export_codebook(store: ProjectStore, file_path: Path) -> None:
    """Write the codebook as Levels + Labels sheets."""
    wb = Workbook()
    wb.remove(wb.active)
    write_levels_sheet(wb, store)
    ordered = store.ordered_levels()

    ws_labels = wb.create_sheet(LABELS_SHEET)
    ws_labels.append(LABELS_HEADERS)
    for level, _ in ordered:
        labels = store.labels_of_level(level["id"])
        labels.sort(key=lambda c: (store.label_path(c).lower()))
        for label in labels:
            parent = store.label(label.get("parent_id"))
            ws_labels.append([
                level["name"], label["name"], parent["name"] if parent else "",
                label.get("color", ""), label.get("description", ""),
            ])
    style_header_row(ws_labels)
    style_body_cells(ws_labels)
    for row in ws_labels.iter_rows(min_row=2, min_col=4, max_col=4):
        cell = row[0]
        if cell.value and HEX_COLOR_RE.match(str(cell.value)):
            hex_value = str(cell.value).lstrip("#")
            cell.fill = PatternFill(start_color=hex_value, end_color=hex_value, fill_type="solid")
    for col, width in zip("ABCDE", (22, 32, 32, 12, 40)):
        ws_labels.column_dimensions[col].width = width
    ws_labels.auto_filter.ref = ws_labels.dimensions

    wb.save(file_path)
