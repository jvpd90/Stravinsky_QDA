# QDA-PLANAPP

Split from a single ~1400-line script into a package. Behavior is
unchanged; this is a structural refactor plus a couple of real bugs
fixed along the way (see below).

## Layout

```
qda_app/
  models.py       # uid/now/read_json/write_json — no tkinter, no business logic
  extraction.py     # extract_txt/docx/pdf, normalize_text
  colors.py       # COLOR_PALETTE
  storage.py       # ProjectStore — owns all project file I/O and core mutations
  codebook_io.py     # codebook (levels + labels) Excel export
  export_excel.py   # export_to_excel / import_from_excel / build_fragment_rows
  analysis.py      # network / CA / MCA over fragments x document attributes
  ui/
    app.py         # QDAApp (Tk root window), wires panels together
    documents_panel.py # left panel: file list, import, attributes, fragments list
    coding_panel.py   # center panel: text widget, search, coding, memos, tooltips
    codes_panel.py    # right panel: codebook (Tree / By level views)
    codebook_dialogs.py # level manager, per-level coding dialog, item picker
    documents_validation_tab.py # second tab: Kanban board for bulk-imported documents
    excel_actions.py   # fragment workbook export / update / append dialogs (codes panel Excel menu)
    analysis_tab.py   # fourth tab: field selector + embedded chart + results table
    dialogs.py      # small reusable Toplevel dialogs (color picker, attributes, etc.)
  main.py         # entry point
```

`models.py`, `extraction.py`, `colors.py`, `storage.py`,
`export_excel.py`, and `analysis.py` have zero tkinter dependency and
can be imported and tested without a display server. The `ui/` package
holds everything that touches Tk.

## Running

```
pip install -r requirements.txt
python -m qda_app.main
```

## Documents validation tab

The second tab ("Leitura e validação de documentos") is a Kanban board
-- same layout and drag/click interactions as the fragment validation
tab below -- for reviewing documents brought in through its two bulk-import
buttons ("Importar Arquivos em Lote" for a multi-select file picker,
"Importar Pasta" to recursively import every supported file in a chosen
folder in one click -- useful since the OS file dialog's native
Ctrl/Shift-click multi-select isn't reliable on every Windows setup),
with three columns: **Por
validar**, **Validados**, and **Descartados**. Each document carries a
`validation_status` field, read and written via
`ProjectStore.document_validation_status_of()` /
`set_document_validation_status()`. Unlike fragments, a missing status
(or an explicit `"validated"` one) counts as validated -- this covers
documents imported the original way, one at a time via the "Add File"
button in the Codificação tab, which still skips this board entirely
and is usable immediately, same as before this feature existed.

Only documents in `store.validated_documents()` (status `"validated"`)
show up in the Codificação tab's Documents list/tree/window or get
picked as the initially-loaded document -- `documents_panel.py` and
`QDAApp.refresh_all()` filter through that method rather than reading
`store.documents` directly. Dragging a document's card to another
column calls `app.on_document_validation_changed(doc)`, which re-renders
that list and, if the document that just left "validated" status was
the one open in the Codificação tab, redirects the same way deleting a
document does.

Each card shows the document's title (double-click to rename inline),
source format/import date, and a short text preview. Clicking a card
(without dragging) opens a popup with the full document text.

## Codebook: levels and labels

The codebook is a set of user-named **levels** (`codes/levels.json`,
e.g. *Tema*, *Subtema*, *Sentimento*) and **labels** belonging to them
(`codes/codes.json`, each with a `level_id`). A level either depends on
a parent level -- hierarchical: each of its labels may sit under a label
of that level (`parent_id`) -- or is independent. Both can be mixed:
*Tema › Subtema* plus an independent *Sentimento*.

- **Levels…** (codes panel) names, adds, reorders, deletes levels and sets
  what each depends on; "One hierarchy" / "All independent" set the whole
  structure at once. Changing a level's parent detaches its labels from
  their old parent labels (fragments keep their labels).
- **Coding**: right-click a selection → *Code Selection (all levels)…*
  opens one dropdown per level. A sub-level's dropdown only offers labels
  under the parent level's choice; picking a sub-label fills in its
  ancestors. *Quick assign* submenus code one label directly, and
  *Edit Fragment Labels* reopens the dialog for existing fragments.
  Linking a label always links its ancestor labels too
  (`ProjectStore.link_code`).
- **Views**: *Tree* nests labels across hierarchical levels (sub-labels
  without a parent are grouped as "— no parent"); *By level* lists each
  level flat with every label's parent. Both show usage counts and can be
  filtered.
- A fragment's highlight color is its most specific label's color.

Projects created before levels existed are migrated on open to
*Categoria › Código* (categories → Categoria, codes → Código, and each
coded fragment also linked to its code's category), and saved once.

### Codebook in Excel

*Excel ▾ → Export Codebook* writes the codebook as a **Levels** sheet
(`Level | Parent Level`) and a **Labels** sheet (`Level | Label | Parent
Label | Color | Description`). It is export-only: a codebook on its own
isn't imported. The codebook changes in the app, or with the fragments
through the fragments workbook below.

### Coding fragments in Excel

The fragments export has one row per fragment -- `Fragment ID | Document
| <one column per level> | Fragment Text | Memo | <attributes>` -- plus a
`Levels` sheet and a read-only `Codings` sheet: the same columns in long
form, one label per cell and one row per codification path (a label with
its own parents, crossed with the independent levels), for filtering and
pivot tables in Excel. Importing it back syncs the project by Fragment ID: level,
memo and attribute cells replace the fragment's values, a deleted row
deletes the fragment, new levels come from the `Levels` sheet, and
fragment text is never changed. *Append New Fragments* adds the rows
without a Fragment ID as new fragments, located by their text in the
document of that title. Every applied import is written to
`import_log.txt` in the project folder. The user-facing rules are in
`help/excel_import_rules.txt` (*Help → Excel Import Rules*).

## Analysis tab

The fourth tab ("Análise") is a JASP/jamovi-style panel: pick an
analysis type, move fields into the role box(es) on the left, and the
chart plus a results table on the right recompute automatically — no
"Run" button. The field pool is the same for all three analyses: the
fragment's label on each codebook level (e.g. `Categoria`, `Código`), plus every document attribute
defined in the project's schema.

- **Rede (Network)**: nodes are `field: value` pairs (e.g. a code, or an
  attribute value); an edge connects two values that co-occur on the
  same fragment, weighted by how often. Uses `networkx`.
- **CA**: pick exactly one row variable and one column variable; builds
  a contingency table and plots the row/column category map. Uses
  `prince.CA`.
- **MCA**: pick two or more variables; plots the category map (not
  individual fragments, which would clutter it). Uses `prince.MCA`.

A fragment can carry more than one code. `analysis.build_field_frame`
explodes a multi-coded fragment into one row per code — the standard
way to treat a multiple-response categorical variable in CA/MCA — which
is also what makes two codes on the same fragment show up as
co-occurring in the network graph.

`prince`'s own `.plot()` renders via Altair (HTML/Vega), which doesn't
embed in Tkinter, so the analysis tab draws its own matplotlib
scatter/graph from `.row_coordinates()` / `.column_coordinates()` /
`.eigenvalues_summary` instead.

## Design notes

- **`ProjectStore`** is the single place that knows the on-disk layout
  (`documents/documents.json`, `coding/fragment_codes.json`, etc.) and
  is the only thing that calls `write_json`/`read_json` on project
  files. UI code mutates `store.documents` / `store.codes` / etc.
  in place and then calls the matching `save_*()` method.
- **Panels talk to `app`, not to each other.** `CodingPanel`,
  `DocumentsPanel`, `CodesPanel`, `DocumentsValidationTab`, and
  `ValidationTab` each take the main `QDAApp` instance and call methods
  on it (`app.render_fragments()`, `app.require_project()`,
  `app.on_document_validation_changed()`, etc.) rather than importing
  one another directly. This keeps the dependency graph a star instead
  of a mesh.
- **Excel export/import logic has no tkinter dependency.** File
  dialogs and message boxes live in `ui/excel_actions.py`; the actual
  workbook reading/writing is in `export_excel.py` and returns plain
  results or raises exceptions.

## Bugs found and fixed during the split

1. **`Text.count()` can return `None` instead of `(0,)`.** CPython's
   tkinter wrapper does `res = self.tk.call(...) or None`, so a
   genuinely-zero count (e.g. a selection starting at `"1.0"`) collapses
   to `None`, and the original code's `self.text.count(...)[0]` pattern
   would then raise `TypeError`. Fixed in `CodingPanel.selected_char_range()`
   with an explicit `if start_result else 0` guard. This affects both
   code assignment and memo editing on selections that start at the very
   beginning of the document.
2. **`ProjectStore(path=None)`** (the "no project open yet" initial
   state) needs explicit handling — `Path(None)` raises `TypeError`.
3. Removed dead code: the old `show_fragment_tooltip` (motion-based
   tooltip lookup) was never bound to anything; `on_fragment_enter`/
   `on_fragment_leave` (tag-based) was the live implementation, so only
   that one was kept.

## Known limitations carried over from the original (not fixed here)

- Excel import locates fragment text via `str.find()`, which can
  mis-locate fragments if the same text occurs more than once in a
  document.
- Memo editing matches fragments by exact `start`/`end`, so a selection
  that doesn't exactly match a previously-created fragment's bounds
  will report "not coded yet."
- No undo for code/category/document deletion beyond a confirm dialog.

See the earlier conversation for the full list of suggested follow-ups.
