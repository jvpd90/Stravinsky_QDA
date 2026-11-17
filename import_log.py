"""Import log: a plain-text record, kept in the project folder, of what
each Excel import changed.

The import takes a snapshot() of the store before it changes anything and
describe() compares it with the store afterwards, so every change is
logged whichever import path made it, grouped document -> fragment.
append() adds the entry to <project>/import_log.txt once it's saved.
"""
from __future__ import annotations

from pathlib import Path

from .models import now
from .storage import ProjectStore

LOG_FILENAME = "import_log.txt"
PREVIEW_CHARS = 90


def log_path(store: ProjectStore) -> Path | None:
    return store.path / LOG_FILENAME if store.path is not None else None


def preview(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS - 1] + "…"


def snapshot(store: ProjectStore) -> dict:
    """Everything describe() needs about the store as it is now -- texts
    and label paths included, since deleted fragments can't be looked up
    afterwards."""
    texts = {d["id"]: store.read_document_text(d) for d in store.documents}
    fragments = {}
    for frag in store.fragments:
        text = texts.get(frag["document_id"], "")
        if frag.get("start") is not None and frag.get("end") is not None:
            text = text[frag["start"]:frag["end"]].strip()
        fragments[frag["id"]] = {
            "document_id": frag["document_id"],
            "start": frag.get("start") or 0,
            "text": text,
            "attributes": dict(frag.get("extra_attributes") or {}),
            "memo": (store.memo_for(frag["id"]) or {}).get("content") or "",
            "labels": {c["id"]: store.label_path(c) for c in store.codes_for_fragment(frag["id"])},
        }
    return {
        "fragments": fragments,
        "documents": {d["id"]: (d["title"], len(texts[d["id"]])) for d in store.documents},
        "code_ids": {c["id"] for c in store.codes},
        "level_names": [lv["name"] for lv in store.levels],
        # level id -> (name, parent level name); label id -> (level name, name, parent label path)
        "level_parents": {
            lv["id"]: (lv["name"], (store.level(lv.get("parent_level_id")) or {}).get("name", ""))
            for lv in store.levels
        },
        "label_parents": {
            c["id"]: (
                (store.level(c.get("level_id")) or {}).get("name", ""), c["name"],
                store.label_path(store.label(c["parent_id"])) if store.label(c.get("parent_id")) else "",
            )
            for c in store.codes
        },
        "schema": list(store.fragment_attributes.get("schema", [])),
    }


def describe(store: ProjectStore, before: dict, workbook: Path, mode: str, summary: list[str],
             notes: dict[str, list[str]] | None = None, skipped: list[str] | None = None) -> list[str]:
    """Log lines for one import. `notes` adds lines of its own under a
    fragment (e.g. ignored text edits); `skipped` lists workbook rows that
    weren't applied."""
    notes = notes or {}
    after = snapshot(store)
    new_code_ids = after["code_ids"] - before["code_ids"]

    lines = [
        "=" * 78,
        f"Import:   {now()}",
        f"Workbook: {workbook}",
        f"Mode:     {mode}",
        "Summary:  " + "; ".join(summary),
    ]
    new_levels = [n for n in after["level_names"] if n not in before["level_names"]]
    if new_levels:
        lines.append("New levels: " + ", ".join(new_levels))
    new_columns = [n for n in after["schema"] if n not in before["schema"]]
    if new_columns:
        lines.append("New attribute columns: " + ", ".join(new_columns))

    codebook = []
    for level_id, (name, parent) in after["level_parents"].items():
        old = before["level_parents"].get(level_id)
        if old is not None and old[1] != parent:
            codebook.append(f'  level {name}: parent level "{old[1] or "none"}" -> "{parent or "none"}"')
    for label_id, (level, name, parent) in after["label_parents"].items():
        old = before["label_parents"].get(label_id)
        if old is not None and old[2] != parent:
            codebook.append(f'  label {name} ({level}): parent "{old[2] or "none"}" -> "{parent or "none"}"')
        elif old is None:
            codebook.append(f"  label created: {name} ({level})" + (f' under "{parent}"' if parent else ""))
    if codebook:
        lines.append("")
        lines.append("Codebook changes:")
        lines += codebook

    # fragment id -> change lines, plus the info to place and title it
    entries: dict[str, list[str]] = {}
    for frag_id in before["fragments"].keys() | after["fragments"].keys():
        old, new = before["fragments"].get(frag_id), after["fragments"].get(frag_id)
        events = []
        if new is None:
            codes = ", ".join(sorted(old["labels"].values())) or "none"
            events.append(f"x fragment deleted (had codes: {codes})")
        else:
            if old is None:
                events.append("+ fragment created")
                old = {"labels": {}, "attributes": {}, "memo": ""}
            for cid in sorted(new["labels"].keys() - old["labels"].keys(), key=new["labels"].get):
                events.append(f"+ code linked: {new['labels'][cid]}" + (" (new label)" if cid in new_code_ids else ""))
            for cid in sorted(old["labels"].keys() - new["labels"].keys(), key=old["labels"].get):
                events.append(f"- code unlinked: {old['labels'][cid]}")
            for key in sorted(old["attributes"].keys() | new["attributes"].keys()):
                was, now_value = old["attributes"].get(key), new["attributes"].get(key)
                if was != now_value:
                    events.append(f'* attribute {key}: "{was or ""}" -> "{now_value or ""}"')
            if old["memo"] != new["memo"]:
                events.append(f'* memo: "{preview(old["memo"])}" -> "{preview(new["memo"])}"')
        events += notes.get(frag_id, [])
        if events:
            entries[frag_id] = events

    by_document: dict[str, list[str]] = {}
    for frag_id in entries:
        info = after["fragments"].get(frag_id) or before["fragments"][frag_id]
        by_document.setdefault(info["document_id"], []).append(frag_id)

    # Documents created or extended (merge mode) even without a fragment change.
    for doc_id, (_, length) in after["documents"].items():
        if doc_id not in before["documents"] or length != before["documents"][doc_id][1]:
            by_document.setdefault(doc_id, [])

    if not by_document:
        lines.append("")
        lines.append("No changes to documents or fragments.")
    for doc_id, frag_ids in by_document.items():
        title = (after["documents"].get(doc_id) or before["documents"].get(doc_id) or ("Unknown",))[0]
        lines.append("")
        lines.append(f"Document: {title}")
        if doc_id not in before["documents"]:
            lines.append("  + document created from the workbook")
        elif doc_id in after["documents"] and after["documents"][doc_id][1] != before["documents"][doc_id][1]:
            lines.append("  ! document text extended with workbook text")
        frag_ids.sort(key=lambda fid: (after["fragments"].get(fid) or before["fragments"][fid])["start"])
        for frag_id in frag_ids:
            info = after["fragments"].get(frag_id) or before["fragments"][frag_id]
            lines.append(f'  Fragment {frag_id}  "{preview(info["text"])}"')
            lines += [f"    {event}" for event in entries[frag_id]]

    if skipped:
        lines.append("")
        lines.append("Rows not applied:")
        lines += [f"  {row}" for row in skipped]
    lines.append("")
    return lines


def append(store: ProjectStore, lines: list[str]) -> None:
    path = log_path(store)
    if path is None or not lines:
        return
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
