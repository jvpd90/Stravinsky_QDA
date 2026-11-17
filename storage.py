"""Project persistence: all JSON file I/O for a QDA project lives here.

No tkinter dependency. UI code should never call write_json/read_json
directly on project paths -- it should go through a ProjectStore
instance so the on-disk layout is defined in exactly one place.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from . import attribute_formulas
from .colors import DEFAULT_CODE_COLOR
from .models import now, read_json, uid, write_json

PROJECT_SUBDIRS = ("documents", "codes", "fragments", "coding", "memos")


def _normalize_schema(schema: list) -> list[dict]:
    """Document-attribute schema entries used to be plain name strings;
    normalize each to {"name": ..., "type": "string"} so every caller can
    rely on the dict shape regardless of which format a project was last
    saved with."""
    return [{"name": entry, "type": "string"} if isinstance(entry, str) else entry for entry in schema]


def default_levels() -> list[dict]:
    """The two-level "Categoria > Código" codebook every project had before
    levels became configurable -- used for new projects and as the target
    of migrating an old project's categories/codes."""
    top = {"id": uid("level"), "name": "Categoria", "parent_level_id": None}
    child = {"id": uid("level"), "name": "Código", "parent_level_id": top["id"]}
    return [top, child]


class ProjectStore:
    """Owns the on-disk layout and in-memory state for one QDA project.

    Usage:
        store = ProjectStore(path)
        store.create()        # new project
        # or
        store.load()          # existing project

        store.documents        # list[dict], mutate in place
        store.save_documents()   # persist just that collection
        store.save_all()       # persist everything
    """

    def __init__(self, path: Path | None):
        self.path = Path(path) if path is not None else None
        self.documents: list[dict] = []
        self.codes: list[dict] = []
        # Codebook levels ("dimensions" a fragment can be labelled on), each
        # {"id", "name", "parent_level_id"}. A level with a parent level is
        # hierarchical: each of its labels may hang under a label of the
        # parent level. A level without one is independent. Every entry in
        # `codes` is a label belonging to exactly one level (`level_id`).
        self.levels: list[dict] = []
        self.fragments: list[dict] = []
        self.links: list[dict] = []
        self.memos: list[dict] = []
        self.doc_attributes: dict = {"schema": []}
        self.fragment_attributes: dict = {"schema": []}

    # -- paths -----------------------------------------------------
    @property
    def documents_json(self) -> Path:
        return self.path / "documents/documents.json"

    @property
    def codes_json(self) -> Path:
        return self.path / "codes/codes.json"

    @property
    def levels_json(self) -> Path:
        return self.path / "codes/levels.json"

    @property
    def fragments_json(self) -> Path:
        return self.path / "fragments/fragments.json"

    @property
    def links_json(self) -> Path:
        return self.path / "coding/fragment_codes.json"

    @property
    def memos_json(self) -> Path:
        return self.path / "memos/memos.json"

    @property
    def attributes_json(self) -> Path:
        return self.path / "documents/attributes.json"

    @property
    def fragment_attributes_json(self) -> Path:
        return self.path / "fragments/attributes.json"

    def document_text_path(self, doc: dict) -> Path:
        return self.path / "documents" / doc["filename"]

    # -- lifecycle ---------------------------------------------------
    def create(self) -> None:
        """Initialize a brand-new project directory structure."""
        for sub in PROJECT_SUBDIRS:
            (self.path / sub).mkdir(parents=True, exist_ok=True)

        self.documents = []
        self.codes = []
        self.levels = default_levels()
        self.fragments = []
        self.links = []
        self.memos = []
        self.doc_attributes = {"schema": []}
        self.fragment_attributes = {"schema": []}
        self.save_all()

    def load(self) -> None:
        """Load an existing project directory into memory."""
        self.documents = read_json(self.documents_json, [])
        self.codes = read_json(self.codes_json, [])
        self.fragments = read_json(self.fragments_json, [])
        self.links = read_json(self.links_json, [])
        self.memos = read_json(self.memos_json, [])
        self.doc_attributes = read_json(self.attributes_json, {"schema": []})
        self.doc_attributes["schema"] = _normalize_schema(self.doc_attributes.get("schema", []))
        self.fragment_attributes = read_json(self.fragment_attributes_json, {"schema": []})

        levels = read_json(self.levels_json, None)
        if levels is None:
            self._migrate_to_levels()
        else:
            self.levels = levels

    def _migrate_to_levels(self) -> None:
        """Projects saved before configurable levels stored categories and
        codes in one flat list told apart by `type`, and only linked codes
        (not their categories) to fragments. Map that onto the default
        "Categoria > Código" levels, link each coded fragment to its code's
        category too (so the per-level coding dialog shows both), and
        persist right away so the migration only ever runs once."""
        self.levels = default_levels()
        category_level, code_level = self.levels
        category_ids = {c["id"] for c in self.codes if c.get("type") == "category"}
        for code in self.codes:
            is_category = code.pop("type", None) == "category"
            code["level_id"] = category_level["id"] if is_category else code_level["id"]
            if is_category or code.get("parent_id") not in category_ids:
                code["parent_id"] = None
        for link in list(self.links):
            self.link_code(link["fragment_id"], link["code_id"])
        self.save_codebook()

    # -- save (granular + bulk) ---------------------------------------
    def save_documents(self) -> None:
        write_json(self.documents_json, self.documents)

    def save_codes(self) -> None:
        write_json(self.codes_json, self.codes)

    def save_levels(self) -> None:
        write_json(self.levels_json, self.levels)

    def save_codebook(self) -> None:
        """Levels, labels and fragment links change together whenever the
        codebook's structure does (e.g. deleting a level drops its labels'
        links), so they're saved together."""
        self.save_levels()
        self.save_codes()
        self.save_links()

    def save_fragments(self) -> None:
        write_json(self.fragments_json, self.fragments)

    def save_links(self) -> None:
        write_json(self.links_json, self.links)

    def save_memos(self) -> None:
        write_json(self.memos_json, self.memos)

    def save_attributes(self) -> None:
        write_json(self.attributes_json, self.doc_attributes)

    def save_fragment_attributes(self) -> None:
        write_json(self.fragment_attributes_json, self.fragment_attributes)

    def save_all(self) -> None:
        self.save_documents()
        self.save_codes()
        self.save_levels()
        self.save_fragments()
        self.save_links()
        self.save_memos()
        self.save_attributes()
        self.save_fragment_attributes()

    # -- document text -------------------------------------------------
    def read_document_text(self, doc: dict) -> str:
        path = self.document_text_path(doc)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def write_document_text(self, doc: dict, text: str) -> None:
        self.document_text_path(doc).write_text(text, encoding="utf-8")

    # -- higher-level mutations -----------------------------------------
    # These centralize logic that was previously duplicated across UI
    # handlers (add_document, import_from_excel) so there's one place
    # that defines what "add a document" means.

    def add_document_from_text(
        self,
        title: str,
        text: str,
        source_format: str,
        imported_from: str = "",
        validation_status: str = "validated",
    ) -> tuple[dict | None, bool]:
        """Add a document from already-extracted text.

        Returns (doc, created). If a document with the same content hash
        already exists, returns (existing_doc, False) without writing.

        `validation_status` defaults to "validated" so the single-file
        "Add File" import keeps showing up immediately in the Codificação
        Documents list like it always has. Bulk import (the "Leitura e
        validação de documentos" tab) passes "pending" instead, so new
        documents sit in that board for review before they're usable
        elsewhere.
        """
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        existing = next((d for d in self.documents if d.get("sha256") == text_hash), None)
        if existing:
            return existing, False

        doc_id = uid("doc")
        filename = f"{doc_id}.txt"
        doc = {
            "id": doc_id,
            "title": title,
            "filename": filename,
            "source_format": source_format,
            "imported_from": imported_from,
            "sha256": text_hash,
            "created_at": now(),
            "locked": False,
            "attributes": {},
            "validation_status": validation_status,
        }
        self.write_document_text(doc, text)
        self.documents.append(doc)
        return doc, True

    def document_validation_status_of(self, doc: dict) -> str:
        """Documents created before this feature existed (or imported via
        the single-file "Add File" button) have no stored status, or an
        explicit "validated" one -- both are treated as validated so
        nothing already in a project silently disappears from the
        Codificação Documents list."""
        return doc.get("validation_status") or "validated"

    def set_document_validation_status(self, doc_id: str, status: str) -> None:
        if status not in ("pending", "validated", "discarded"):
            raise ValueError(f"Unknown validation status: {status!r}")
        doc = next((d for d in self.documents if d["id"] == doc_id), None)
        if doc is not None:
            doc["validation_status"] = status

    def validated_documents(self) -> list[dict]:
        """Documents that should be visible/usable in the Codificação tab
        -- i.e. not sitting in the bulk-import validation board as pending
        or discarded."""
        return [d for d in self.documents if self.document_validation_status_of(d) == "validated"]

    def schema_entry(self, name: str) -> dict | None:
        return next((e for e in self.doc_attributes.get("schema", []) if e["name"] == name), None)

    def attribute_value(self, doc: dict, field_name: str) -> str:
        """Resolve a document's value for a schema field by name.

        Computed fields are never stored on the document -- their value is
        re-derived from the formula every call, so it's always current
        with whatever its source attributes hold right now.
        """
        entry = self.schema_entry(field_name)
        if entry and entry.get("type") == "computed":
            schema_by_name = {e["name"]: e for e in self.doc_attributes.get("schema", [])}
            return attribute_formulas.evaluate(entry["formula"], doc, schema_by_name)
        return doc.get("attributes", {}).get(field_name, "")

    def delete_document(self, doc: dict) -> None:
        self.fragments = [f for f in self.fragments if f["document_id"] != doc["id"]]
        self.documents = [d for d in self.documents if d["id"] != doc["id"]]

    def add_fragment(self, document_id: str, start: int, end: int) -> dict:
        frag = {
            "id": uid("frag"),
            "document_id": document_id,
            "start": start,
            "end": end,
            "created_at": now(),
            "validation_status": "pending",
        }
        self.fragments.append(frag)
        return frag

    def delete_fragment(self, fragment_id: str) -> None:
        """"Uncode" a fragment: remove it along with its code links and its
        memo, so nothing is left pointing at a fragment that no longer
        exists. The document text itself is untouched."""
        self.fragments = [f for f in self.fragments if f["id"] != fragment_id]
        self.links = [l for l in self.links if l["fragment_id"] != fragment_id]
        self.memos = [m for m in self.memos if m["target_id"] != fragment_id]

    def validation_status_of(self, fragment: dict) -> str:
        """Fragments created before the validation feature existed have no
        stored status -- treat them as still needing review rather than
        silently marking them validated."""
        return fragment.get("validation_status") or "pending"

    def set_validation_status(self, fragment_id: str, status: str) -> None:
        if status not in ("pending", "validated", "discarded"):
            raise ValueError(f"Unknown validation status: {status!r}")
        fragment = next((f for f in self.fragments if f["id"] == fragment_id), None)
        if fragment is not None:
            fragment["validation_status"] = status

    def find_fragment(self, document_id: str, start: int, end: int) -> dict | None:
        """Exact-bounds fragment lookup. Prefer find_fragment_at() for UI
        interactions where the user's current selection may not line up
        character-for-character with a previously created fragment."""
        return next(
            (
                f for f in self.fragments
                if f["document_id"] == document_id and f["start"] == start and f["end"] == end
            ),
            None,
        )

    def find_fragment_at(self, document_id: str, start: int, end: int) -> dict | None:
        """Find the fragment for a document that best matches the given
        character range, tolerating small selection drift.

        Exact match wins. Failing that, prefer a fragment that fully
        contains [start, end) or is fully contained by it, breaking ties
        by the fragment whose bounds are closest to the given range.
        This is meant for interactive use (e.g. re-selecting text to
        edit a memo) where the user's selection may be off by a
        character or two from the original coding pass, not for
        creating new fragments.
        """
        exact = self.find_fragment(document_id, start, end)
        if exact is not None:
            return exact

        candidates = [f for f in self.fragments if f["document_id"] == document_id]
        overlapping = [
            f for f in candidates
            if f["start"] < end and f["end"] > start  # ranges overlap at all
        ]
        if not overlapping:
            return None

        def distance(f):
            return abs(f["start"] - start) + abs(f["end"] - end)

        overlapping.sort(key=distance)
        return overlapping[0]
   
    def memo_for(self, target_id: str) -> dict | None:
        return next((m for m in self.memos if m["target_id"] == target_id), None)

    def set_memo(self, target_id: str, scope: str, content: str) -> None:
        memo = self.memo_for(target_id)
        if memo:
            memo["content"] = content
        else:
            self.memos.append({
                "id": uid("memo"),
                "scope": scope,
                "target_id": target_id,
                "content": content,
                "created_at": now(),
            })

    # -- codebook: levels ------------------------------------------------
    # A label (an entry in `codes`) belongs to one level. Labels of a
    # hierarchical level may name a parent label (`parent_id`) from the
    # parent level; labels of an independent level never do.

    def level(self, level_id: str | None) -> dict | None:
        return next((lv for lv in self.levels if lv["id"] == level_id), None)

    def level_by_name(self, name: str) -> dict | None:
        key = name.strip().lower()
        return next((lv for lv in self.levels if lv["name"].lower() == key), None)

    def child_levels(self, level: dict | None) -> list[dict]:
        """Levels that depend on `level` (or, for None, the independent /
        top-of-hierarchy levels), in stored order."""
        parent_id = level["id"] if level else None
        return [lv for lv in self.levels if lv.get("parent_level_id") == parent_id]

    def ordered_levels(self) -> list[tuple[dict, int]]:
        """(level, depth) pairs, depth-first: each level comes right after
        its parent level, so dialogs and exports can show a hierarchy as
        an indented block and independent levels as their own rows."""
        result = []

        def walk(parent, depth):
            for lv in self.child_levels(parent):
                result.append((lv, depth))
                walk(lv, depth + 1)

        walk(None, 0)
        return result

    def level_ancestors(self, level: dict) -> list[dict]:
        """Parent level, grandparent level, ... (nearest first)."""
        chain = []
        current = self.level(level.get("parent_level_id"))
        while current is not None and current not in chain:
            chain.append(current)
            current = self.level(current.get("parent_level_id"))
        return chain

    def default_level(self) -> dict | None:
        """Level a label goes into when nothing says which (e.g. a bare code
        name typed on a validation card or found in an Excel export): the
        deepest level of the first hierarchy, i.e. the old "Código"."""
        ordered = self.ordered_levels()
        if not ordered:
            return None
        first_root_block = []
        for lv, depth in ordered:
            if depth == 0 and first_root_block:
                break
            first_root_block.append((lv, depth))
        return max(first_root_block, key=lambda pair: pair[1])[0]

    def add_level(self, name: str, parent_level_id: str | None = None) -> dict:
        name = name.strip()
        if not name:
            raise ValueError("A level needs a name.")
        if self.level_by_name(name):
            raise ValueError(f"A level named '{name}' already exists.")
        level = {"id": uid("level"), "name": name, "parent_level_id": parent_level_id}
        self.levels.append(level)
        return level

    def rename_level(self, level: dict, new_name: str) -> None:
        new_name = new_name.strip()
        if not new_name:
            raise ValueError("A level needs a name.")
        clash = self.level_by_name(new_name)
        if clash is not None and clash is not level:
            raise ValueError(f"A level named '{new_name}' already exists.")
        level["name"] = new_name

    def set_level_parent(self, level: dict, parent_level_id: str | None) -> None:
        """Make `level` depend on another level (hierarchical) or on none
        (independent). Its labels' parent labels belonged to the old
        parent level, so they are detached; fragment links are kept."""
        if parent_level_id == level.get("parent_level_id"):
            return
        if parent_level_id is not None:
            new_parent = self.level(parent_level_id)
            if new_parent is None:
                raise ValueError("Unknown parent level.")
            if new_parent is level or level in self.level_ancestors(new_parent):
                raise ValueError("A level can't depend on itself or on one of its own sub-levels.")
        level["parent_level_id"] = parent_level_id
        for label in self.labels_of_level(level["id"]):
            label["parent_id"] = None

    def move_level(self, level: dict, delta: int) -> None:
        """Move a level up/down among its siblings (same parent level),
        which is the order it's shown and exported in."""
        siblings = [lv for lv in self.levels if lv.get("parent_level_id") == level.get("parent_level_id")]
        pos = siblings.index(level)
        target = pos + delta
        if not 0 <= target < len(siblings):
            return
        a, b = self.levels.index(level), self.levels.index(siblings[target])
        self.levels[a], self.levels[b] = self.levels[b], self.levels[a]

    def delete_level(self, level: dict) -> None:
        """Delete a level with all its labels (and their fragment links).
        Levels that depended on it become independent."""
        label_ids = {c["id"] for c in self.labels_of_level(level["id"])}
        self.codes = [c for c in self.codes if c["id"] not in label_ids]
        self.links = [l for l in self.links if l["code_id"] not in label_ids]
        for child in self.child_levels(level):
            self.set_level_parent(child, None)
        self.levels.remove(level)

    # -- codebook: labels ----------------------------------------------------
    def label(self, label_id: str | None) -> dict | None:
        return next((c for c in self.codes if c["id"] == label_id), None)

    def labels_of_level(self, level_id: str) -> list[dict]:
        return [c for c in self.codes if c.get("level_id") == level_id]

    def find_label(self, name: str, level_id: str | None = None, parent_id: str | None = ...) -> dict | None:
        """Case-insensitive name lookup, optionally restricted to a level
        and (when parent_id is passed, even as None) to a parent label."""
        key = name.strip().lower()
        for c in self.codes:
            if c["name"].lower() != key:
                continue
            if level_id is not None and c.get("level_id") != level_id:
                continue
            if parent_id is not ... and c.get("parent_id") != parent_id:
                continue
            return c
        return None

    def add_label(
        self, name: str, level_id: str, parent_id: str | None = None,
        color: str = DEFAULT_CODE_COLOR, description: str = "",
    ) -> dict:
        label = {
            "id": uid("code"), "name": name.strip(), "description": description,
            "parent_id": parent_id, "color": color, "level_id": level_id,
        }
        self.codes.append(label)
        return label

    def label_ancestors(self, label: dict) -> list[dict]:
        """Parent label, grandparent label, ... (nearest first)."""
        chain = []
        current = self.label(label.get("parent_id"))
        while current is not None and current not in chain:
            chain.append(current)
            current = self.label(current.get("parent_id"))
        return chain

    def label_path(self, label: dict, sep: str = " › ") -> str:
        return sep.join(c["name"] for c in reversed([label] + self.label_ancestors(label)))

    def child_codes_of(self, label: dict) -> list[dict]:
        return [c for c in self.codes if c.get("parent_id") == label["id"]]

    def label_usage(self, label_id: str) -> int:
        return sum(1 for l in self.links if l["code_id"] == label_id)

    def delete_code(self, code: dict) -> None:
        self.codes.remove(code)
        self.links = [l for l in self.links if l["code_id"] != code["id"]]

    def label_descendants(self, label: dict) -> list[dict]:
        result, stack = [], [label]
        while stack:
            for child in self.child_codes_of(stack.pop()):
                if child not in result:
                    result.append(child)
                    stack.append(child)
        return result

    def _relabel_fragments(self, fragment_ids: set[str], drop_ids: set[str], add_id: str | None = None) -> None:
        """For each fragment: drop the labels in `drop_ids`, add `add_id`,
        and re-derive ancestors from what remains -- so a dropped ancestor
        comes back only if another of the fragment's labels still sits
        under it."""
        for fragment_id in fragment_ids:
            keep = [l["code_id"] for l in self.links if l["fragment_id"] == fragment_id and l["code_id"] not in drop_ids]
            self.set_fragment_labels(fragment_id, keep + ([add_id] if add_id else []))

    def move_label(self, label: dict, parent_id: str | None) -> None:
        """Put `label` under another parent label (None: no parent). The
        hierarchy is the rule for the fragments: every fragment coded with
        the label or one of its sub-labels gets the new ancestors and loses
        the old ones, unless another of its labels still implies them."""
        old_ancestor_ids = {a["id"] for a in self.label_ancestors(label)}
        label["parent_id"] = parent_id
        moved_ids = {label["id"]} | {c["id"] for c in self.label_descendants(label)}
        fragment_ids = {l["fragment_id"] for l in self.links if l["code_id"] in moved_ids}
        self._relabel_fragments(fragment_ids, old_ancestor_ids)

    def merge_labels(self, keep: dict, others: list[dict]) -> None:
        """Fold `others` into `keep` and delete them: their sub-labels move
        under `keep`, and their fragments are coded with `keep` instead --
        with `keep`'s ancestors in place of theirs, as in move_label."""
        for other in others:
            for child in self.child_codes_of(other):
                self.move_label(child, keep["id"])
            drop_ids = {other["id"]} | {a["id"] for a in self.label_ancestors(other)}
            fragment_ids = {l["fragment_id"] for l in self.links if l["code_id"] == other["id"]}
            self._relabel_fragments(fragment_ids, drop_ids, keep["id"])
            self.delete_code(other)

    # -- fragment <-> label links ------------------------------------------
    def link_code(self, fragment_id: str, code_id: str) -> None:
        """Link a label to a fragment, together with its ancestor labels:
        coding something as "Subtema A1" also codes it as its "Tema A"."""
        label = self.label(code_id)
        ids = [code_id] + ([c["id"] for c in self.label_ancestors(label)] if label else [])
        for cid in ids:
            if not any(link["fragment_id"] == fragment_id and link["code_id"] == cid for link in self.links):
                self.links.append({"fragment_id": fragment_id, "code_id": cid})

    def set_fragment_labels(self, fragment_id: str, label_ids: list[str]) -> None:
        """Replace every label on a fragment (ancestors are added back by
        link_code)."""
        self.links = [l for l in self.links if l["fragment_id"] != fragment_id]
        for label_id in label_ids:
            self.link_code(fragment_id, label_id)

    def codes_for_fragment(self, fragment_id: str) -> list[dict]:
        code_ids = {link["code_id"] for link in self.links if link["fragment_id"] == fragment_id}
        return [c for c in self.codes if c["id"] in code_ids]

    def labels_by_level(self, fragment_id: str) -> dict[str, list[dict]]:
        """level_id -> the fragment's labels on that level."""
        result: dict[str, list[dict]] = {}
        for label in self.codes_for_fragment(fragment_id):
            result.setdefault(label.get("level_id"), []).append(label)
        return result

    def leaf_labels_for_fragment(self, fragment_id: str) -> list[dict]:
        """The fragment's most specific labels: those that aren't just the
        ancestor of another of its labels. For a fragment coded "Tema A ›
        Subtema A1" plus "Positivo", that's [Subtema A1, Positivo]."""
        labels = self.codes_for_fragment(fragment_id)
        ancestor_ids = {a["id"] for c in labels for a in self.label_ancestors(c)}
        return [c for c in labels if c["id"] not in ancestor_ids]

    def fragment_label_text(self, fragment_id: str, empty: str = "No code") -> str:
        leaves = self.leaf_labels_for_fragment(fragment_id)
        return "; ".join(self.label_path(c) for c in leaves) if leaves else empty

    def fragment_color(self, fragment_id: str) -> str:
        """Highlight color: the first most-specific label's color, so a
        fragment coded on a sub-level shows that sub-label's color rather
        than its (often uncolored) parent's."""
        order = {lv["id"]: i for i, (lv, _) in enumerate(self.ordered_levels())}
        leaves = sorted(self.leaf_labels_for_fragment(fragment_id), key=lambda c: order.get(c.get("level_id"), 99))
        return leaves[0].get("color") or DEFAULT_CODE_COLOR if leaves else DEFAULT_CODE_COLOR
