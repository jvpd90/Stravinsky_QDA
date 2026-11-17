"""'Leitura e validação de documentos' tab: a Kanban board -- same layout
and interactions as ValidationTab (validation_tab.py) -- for reviewing
documents brought in via bulk import before they become usable elsewhere.

A document sits in "Por validar" until a human either drags it to
"Validados" (at which point it starts showing up in the Codificação tab's
Documents list) or to "Descartados" (where it's kept, but stays hidden
from Codificação). Documents imported one at a time via the existing
"Add File" button in the Codificação tab skip this board entirely --
they're marked "validated" immediately, same as before this feature
existed.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

from ..extraction import extract_text_for
from . import dialogs, widgets

COLUMNS = [
    ("pending", "Por validar"),
    ("validated", "Validados"),
    ("discarded", "Descartados"),
]
COLUMN_BG = "#f2f2f2"
HOVER_BG = "#dbe9ff"
STRIPE_COLOR = "#8899aa"
DRAG_THRESHOLD = 4  # pixels of motion before a press counts as a drag, not a click
PREVIEW_WORDS = 24
IMPORT_FILETYPES = [
    ("Supported documents", "*.txt *.docx *.pdf"),
    ("Text files", "*.txt"),
    ("Word documents", "*.docx"),
    ("PDF files", "*.pdf"),
]
IMPORT_EXTENSIONS = (".txt", ".docx", ".pdf")


class DocumentsValidationTab:
    def __init__(self, parent, app):
        """
        app must provide:
          - app.store: ProjectStore
          - app.require_project() -> bool
          - app.on_document_validation_changed(doc)
        """
        self.app = app
        self._previews: dict[tuple[str, int], str] = {}  # (text file, mtime) -> preview
        self._drag: dict | None = None
        self._hover_column: tk.Canvas | None = None
        self._editing: str | None = None  # document_id mid-edit, while its title field is an Entry

        self.frame = tk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)

        toolbar = tk.Frame(self.frame, padx=10, pady=8)
        toolbar.grid(row=0, column=0, sticky="ew")
        tk.Label(toolbar, text="Leitura e Validação de Documentos", font=("Arial", 14, "bold")).pack(side="left")
        self.import_folder_btn = tk.Button(toolbar, text="Importar Pasta", command=self.bulk_import_folder)
        self.import_folder_btn.pack(side="right")
        self.import_btn = tk.Button(toolbar, text="Importar Arquivos em Lote", command=self.bulk_import_files)
        self.import_btn.pack(side="right", padx=(0, 4))

        self.board = tk.Frame(self.frame)
        self.board.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        for i in range(len(COLUMNS)):
            self.board.columnconfigure(i, weight=1, uniform="col")
        self.board.rowconfigure(0, weight=1)

        self.column_headers: dict[str, tk.Label] = {}
        self.column_bodies: dict[str, tk.Frame] = {}
        self.column_canvases: dict[str, tk.Canvas] = {}
        for i, (status, title) in enumerate(COLUMNS):
            self._build_column(i, status, title)

        self.refresh()

    def grid(self, **kwargs):
        self.frame.grid(**kwargs)

    # -- column scaffolding ----------------------------------------------------
    def _build_column(self, col_index: int, status: str, title: str):
        outer = tk.Frame(self.board, bd=1, relief="solid")
        outer.grid(row=0, column=col_index, sticky="nsew", padx=6)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        header = tk.Label(outer, text=title, font=("Arial", 11, "bold"), bg="#e0e0e0", pady=6)
        header.grid(row=0, column=0, sticky="ew")
        self.column_headers[status] = header

        canvas_frame = tk.Frame(outer)
        canvas_frame.grid(row=1, column=0, sticky="nsew")
        canvas_frame.columnconfigure(0, weight=1)
        canvas_frame.rowconfigure(0, weight=1)

        canvas = tk.Canvas(canvas_frame, highlightthickness=0, bg=COLUMN_BG)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        body = tk.Frame(canvas, bg=COLUMN_BG)
        window = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e, c=canvas: c.configure(scrollregion=c.bbox("all")))
        canvas.bind("<Configure>", lambda e, c=canvas, w=window: c.itemconfig(w, width=e.width))

        # Scope mousewheel scrolling to whichever column the pointer is
        # over -- three independent canvases would otherwise all scroll
        # together if bound globally for the tab's lifetime.
        canvas.bind("<Enter>", lambda e, c=canvas: c.bind_all(
            "<MouseWheel>", lambda ev, c=c: c.yview_scroll(int(-ev.delta / 120), "units")
        ))
        canvas.bind("<Leave>", lambda e, c=canvas: c.unbind_all("<MouseWheel>"))

        self.column_bodies[status] = body
        self.column_canvases[status] = canvas

    # -- rendering ---------------------------------------------------------
    def refresh(self):
        """Full board redraw from current store state."""
        for body in self.column_bodies.values():
            for child in body.winfo_children():
                child.destroy()

        has_project = bool(self.app.store.path)
        self.import_btn.config(state="normal" if has_project else "disabled")
        self.import_folder_btn.config(state="normal" if has_project else "disabled")

        if not has_project:
            for status, title in COLUMNS:
                self.column_headers[status].config(text=title)
                tk.Label(
                    self.column_bodies[status],
                    text="Abra ou crie um projeto para importar documentos.",
                    bg=COLUMN_BG, fg="gray", font=("Arial", 9), wraplength=200, justify="center", pady=16,
                ).pack(fill="x")
            return

        buckets: dict[str, list[dict]] = {status: [] for status, _ in COLUMNS}
        for doc in self.app.store.documents:
            buckets[self.app.store.document_validation_status_of(doc)].append(doc)

        for status, title in COLUMNS:
            docs = buckets[status]
            self.column_headers[status].config(text=f"{title} ({len(docs)})")
            body = self.column_bodies[status]
            if not docs:
                tk.Label(
                    body, text="(vazio)", bg=COLUMN_BG, fg="gray", font=("Arial", 9, "italic"), pady=10,
                ).pack(fill="x")
                continue
            for doc in docs:
                self._build_card(body, doc)

    def _preview_text(self, doc: dict) -> str:
        # Cached per file version: rereading every document on each redraw
        # was most of the board's cost (and slow on a synced folder).
        path = self.app.store.document_text_path(doc)
        try:
            key = (str(path), path.stat().st_mtime_ns)
        except OSError:
            return ""
        if key not in self._previews:
            words = self.app.store.read_document_text(doc).split()
            preview = " ".join(words[:PREVIEW_WORDS])
            if len(words) > PREVIEW_WORDS:
                preview += "…"
            self._previews[key] = preview
        return self._previews[key]

    def _subtitle(self, doc: dict) -> str:
        parts = [p for p in (doc.get("source_format", "").lstrip("."), doc.get("created_at", "")) if p]
        return " · ".join(parts)

    def _build_card(self, parent, doc: dict):
        doc_id = doc["id"]
        title = doc.get("title", "Untitled")

        card = tk.Frame(parent, bg="white", bd=1, relief="solid")
        card.pack(fill="x", padx=6, pady=4)

        stripe = tk.Frame(card, bg=STRIPE_COLOR, width=5)
        stripe.pack(side="left", fill="y")

        body = tk.Frame(card, bg="white", padx=8, pady=6)
        body.pack(side="left", fill="both", expand=True)

        self._build_editable_title(body, doc, title)

        subtitle = self._subtitle(doc)
        if subtitle:
            tk.Label(
                body, text=subtitle, font=("Arial", 8), fg="#555555", bg="white", anchor="w",
            ).pack(fill="x", pady=(2, 0))

        preview = self._preview_text(doc)
        preview_label = tk.Label(
            body, text=preview or "(documento vazio)", font=("Arial", 9), bg="white",
            anchor="w", justify="left", wraplength=220,
        )
        preview_label.pack(fill="x", pady=(2, 0))

        # Bind drag-to-move / click-to-open-popup on the card chrome and the
        # read-only preview/subtitle text. The title field is excluded --
        # it gets its own double-click-to-edit binding, which would
        # conflict with this single-click drag/popup handling.
        for widget in (card, stripe, body, preview_label):
            widget.bind("<ButtonPress-1>", lambda e, d=doc, c=card: self._on_card_press(e, d, c))
            widget.bind("<B1-Motion>", self._on_card_motion)
            widget.bind("<ButtonRelease-1>", self._on_card_release)

    # -- inline title editing -------------------------------------------------
    def _build_editable_title(self, parent, doc: dict, title: str):
        doc_id = doc["id"]
        if self._editing == doc_id:
            entry = tk.Entry(parent, font=("Arial", 10, "bold"))
            entry.insert(0, title)
            entry.select_range(0, "end")
            entry.pack(fill="x", pady=(2, 0))
            entry.focus_set()

            done = [False]

            def finish(new_value):
                if done[0]:
                    return
                done[0] = True
                self._editing = None
                if new_value is not None and new_value.strip() and new_value.strip() != title:
                    self._commit_title_edit(doc, new_value.strip())
                else:
                    self.refresh()

            entry.bind("<Return>", lambda e: finish(entry.get()))
            entry.bind("<FocusOut>", lambda e: finish(entry.get()) if not done[0] else None)
            entry.bind("<Escape>", lambda e: finish(None))
            return entry

        label = tk.Label(
            parent, text=title, font=("Arial", 10, "bold"), bg="white", anchor="w", justify="left", wraplength=220,
        )
        label.pack(fill="x", pady=(2, 0))
        label.bind("<Double-Button-1>", lambda e: self._start_edit(doc_id))
        return label

    def _start_edit(self, doc_id: str):
        self._editing = doc_id
        self.refresh()

    def _commit_title_edit(self, doc: dict, new_title: str):
        doc["title"] = new_title
        self.app.store.save_documents()
        self.refresh()
        self.app.documents_panel.populate()
        self.app.documents_panel.refresh_documents_window()

    # -- drag and drop -------------------------------------------------------
    def _on_card_press(self, event, doc: dict, card: tk.Frame):
        self._drag = {
            "doc": doc,
            "card": card,
            "start_x": event.x_root,
            "start_y": event.y_root,
            "dragging": False,
            "ghost": None,
        }

    def _on_card_motion(self, event):
        drag = self._drag
        if not drag:
            return
        dx = event.x_root - drag["start_x"]
        dy = event.y_root - drag["start_y"]

        if not drag["dragging"] and (abs(dx) > DRAG_THRESHOLD or abs(dy) > DRAG_THRESHOLD):
            drag["dragging"] = True
            drag["ghost"] = self._make_ghost(drag["doc"])

        if drag["dragging"] and drag["ghost"] is not None:
            drag["ghost"].geometry(f"+{event.x_root + 12}+{event.y_root + 12}")
            self._update_hover_column(event.x_root, event.y_root)

    def _on_card_release(self, event):
        drag = self._drag
        self._drag = None
        if not drag:
            return

        if drag["ghost"] is not None:
            drag["ghost"].destroy()
        self._clear_hover_column()

        if not drag["dragging"]:
            self.show_document_popup(drag["doc"])
            return

        target_status = self._column_at(event.x_root, event.y_root)
        current_status = self.app.store.document_validation_status_of(drag["doc"])
        if target_status and target_status != current_status:
            doc = drag["doc"]
            self.app.store.set_document_validation_status(doc["id"], target_status)
            self.app.store.save_documents()
            self.refresh()
            self.app.on_document_validation_changed(doc)

    def _make_ghost(self, doc: dict) -> tk.Toplevel:
        ghost = tk.Toplevel(self.frame)
        ghost.overrideredirect(True)
        try:
            ghost.attributes("-alpha", 0.85)
        except tk.TclError:
            pass  # -alpha isn't supported on every platform/WM
        tk.Label(
            ghost, text=doc.get("title", "Untitled"), bg="#fffbe0", bd=1, relief="solid",
            font=("Arial", 9), justify="left", padx=6, pady=4,
        ).pack()
        return ghost

    def _column_at(self, x_root: int, y_root: int) -> str | None:
        """Which column (if any) contains the widget under x_root/y_root.
        Walks up the widget's ancestors since the point may land on a
        child label rather than the column body/canvas itself."""
        widget = self.frame.winfo_containing(x_root, y_root)
        while widget is not None:
            for status, body in self.column_bodies.items():
                if widget is body or widget is self.column_canvases[status]:
                    return status
            widget = widget.master
        return None

    def _update_hover_column(self, x_root: int, y_root: int):
        status = self._column_at(x_root, y_root)
        target = self.column_canvases.get(status) if status else None
        if target is self._hover_column:
            return
        self._clear_hover_column()
        if target is not None:
            target.configure(bg=HOVER_BG)
            self.column_bodies[status].configure(bg=HOVER_BG)
            for child in self.column_bodies[status].winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(bg=HOVER_BG)
            self._hover_column = target

    def _clear_hover_column(self):
        if self._hover_column is None:
            return
        for status, canvas in self.column_canvases.items():
            if canvas is self._hover_column:
                canvas.configure(bg=COLUMN_BG)
                self.column_bodies[status].configure(bg=COLUMN_BG)
                for child in self.column_bodies[status].winfo_children():
                    if isinstance(child, tk.Label):
                        child.configure(bg=COLUMN_BG)
        self._hover_column = None

    # -- document detail popup -----------------------------------------------
    def show_document_popup(self, doc: dict):
        store = self.app.store
        full_text = store.read_document_text(doc)

        status_labels = dict(COLUMNS)
        status_text = status_labels[store.document_validation_status_of(doc)]

        dialog = tk.Toplevel(self.frame)
        dialog.title(doc.get("title", "Untitled"))
        dialog.transient(self.frame)
        dialog.grab_set()

        tk.Label(dialog, text=doc.get("title", "Untitled"), font=("Arial", 12, "bold"), wraplength=560, justify="left").pack(anchor="w", padx=12, pady=(10, 2))
        subtitle = self._subtitle(doc)
        if subtitle:
            tk.Label(dialog, text=subtitle, font=("Arial", 9), fg="#555555").pack(anchor="w", padx=12)
        if doc.get("imported_from"):
            tk.Label(dialog, text=f"Origem: {doc['imported_from']}", font=("Arial", 8), fg="gray", wraplength=560, justify="left").pack(anchor="w", padx=12)
        tk.Label(dialog, text=f"Status: {status_text}", font=("Arial", 10)).pack(anchor="w", padx=12, pady=(4, 8))

        tk.Label(dialog, text="Conteúdo:", font=("Arial", 10, "bold")).pack(anchor="w", padx=12)

        search_frame = tk.Frame(dialog)
        search_frame.pack(fill="x", padx=12, pady=(2, 0))
        tk.Label(search_frame, text="Buscar:").pack(side="left")
        search_var = tk.StringVar()
        search_entry = tk.Entry(search_frame, textvariable=search_var, width=30)
        search_entry.pack(side="left", fill="x", expand=True, padx=5)
        prev_btn = tk.Button(search_frame, text="Anterior")
        prev_btn.pack(side="left", padx=2)
        next_btn = tk.Button(search_frame, text="Próxima")
        next_btn.pack(side="left", padx=2)
        clear_btn = tk.Button(search_frame, text="Limpar")
        clear_btn.pack(side="left", padx=2)
        search_count_var = tk.StringVar(value="")
        tk.Label(search_frame, textvariable=search_count_var, font=("Arial", 8), fg="#555555").pack(side="left", padx=5)

        text_frame = tk.Frame(dialog)
        text_frame.pack(fill="both", expand=True, padx=12, pady=(2, 8))
        text_widget = tk.Text(text_frame, wrap="word", font=("Arial", 10))
        text_widget.insert("1.0", full_text)
        text_widget.config(state="disabled")
        text_widget.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(text_frame, command=text_widget.yview)
        scrollbar.pack(side="right", fill="y")
        text_widget.configure(yscrollcommand=scrollbar.set)

        text_widget.tag_config("search", background="yellow", foreground="black")
        text_widget.tag_config("search_current", background="orange", foreground="black")
        search_state = {"positions": [], "index": -1}

        def highlight_search(*_args):
            text_widget.tag_remove("search", "1.0", tk.END)
            text_widget.tag_remove("search_current", "1.0", tk.END)
            search_state["positions"] = []
            search_state["index"] = -1

            term = search_var.get()
            if not term:
                search_count_var.set("")
                return

            start_pos = "1.0"
            while True:
                pos = text_widget.search(term, start_pos, nocase=True, stopindex=tk.END)
                if not pos:
                    break
                end_pos = f"{pos}+{len(term)}c"
                text_widget.tag_add("search", pos, end_pos)
                search_state["positions"].append((pos, end_pos))
                start_pos = end_pos

            if search_state["positions"]:
                goto_search_result(0)
            else:
                search_count_var.set("0/0")

        def goto_search_result(index):
            positions = search_state["positions"]
            if not positions:
                return
            index = index % len(positions)
            search_state["index"] = index
            pos, end_pos = positions[index]
            text_widget.tag_remove("search_current", "1.0", tk.END)
            text_widget.tag_add("search_current", pos, end_pos)
            text_widget.see(pos)
            search_count_var.set(f"{index + 1}/{len(positions)}")

        def next_search():
            if search_state["positions"]:
                goto_search_result(search_state["index"] + 1)

        def previous_search():
            if search_state["positions"]:
                goto_search_result(search_state["index"] - 1)

        def clear_search():
            search_var.set("")
            search_entry.focus_set()
            search_count_var.set("")

        search_var.trace("w", highlight_search)
        prev_btn.config(command=previous_search)
        next_btn.config(command=next_search)
        clear_btn.config(command=clear_search)
        search_entry.bind("<Return>", lambda e: next_search())

        tk.Label(
            dialog,
            text="Arraste o card para “Validados” ou “Descartados” para atualizar o status.",
            font=("Arial", 8, "italic"), fg="gray",
        ).pack(anchor="w", padx=12)

        tk.Button(dialog, text="Fechar", command=dialog.destroy).pack(pady=(6, 12))

        widgets.center_dialog(dialog, self.frame, width=640, height=520)

    # -- bulk import -----------------------------------------------------------
    def bulk_import_files(self):
        """Multi-select file picker. Relies on the OS dialog's native
        Ctrl/Shift-click multi-select -- on some Windows setups (managed
        machines with a Group Policy restricting common-dialog behavior, or
        just an unfamiliar interaction) that doesn't work, so
        bulk_import_folder() below is the reliable alternative: pick one
        folder, get every supported file in it."""
        if not self.app.require_project():
            return

        file_paths = filedialog.askopenfilenames(
            title="Importar Arquivos em Lote", filetypes=IMPORT_FILETYPES,
        )
        if not file_paths:
            return
        self._import_paths([Path(p) for p in file_paths])

    def bulk_import_folder(self):
        """Pick a single folder and import every supported file found in it
        (including subfolders) -- a one-click alternative to multi-select
        that doesn't depend on the OS file dialog supporting Ctrl/Shift-click."""
        if not self.app.require_project():
            return

        folder = filedialog.askdirectory(title="Importar Pasta")
        if not folder:
            return

        paths = sorted(
            p for p in Path(folder).rglob("*") if p.suffix.lower() in IMPORT_EXTENSIONS
        )
        if not paths:
            messagebox.showinfo(
                "Importar Pasta", "Nenhum arquivo .txt, .docx ou .pdf encontrado nessa pasta."
            )
            return
        self._import_paths(paths)

    def _import_paths(self, paths: list[Path]):
        imported = 0
        duplicates = 0
        errors: list[str] = []

        for path in paths:
            busy = dialogs.show_busy(self.frame, f"Extraindo texto de {path.name}...")
            try:
                text = extract_text_for(path)
            except Exception as e:
                errors.append(f"{path.name}: {e}")
                continue
            finally:
                busy.destroy()

            if not text.strip():
                errors.append(f"{path.name}: nenhum texto extraível (pode ser um PDF digitalizado)")
                continue

            doc, created = self.app.store.add_document_from_text(
                title=path.stem,
                text=text,
                source_format=path.suffix.lower(),
                imported_from=str(path),
                validation_status="pending",
            )
            if created:
                imported += 1
            else:
                duplicates += 1

        self.app.store.save_documents()
        self.refresh()

        summary = f"Documentos importados para validação: {imported}"
        if duplicates:
            summary += f"\nJá existentes (ignorados): {duplicates}"
        if errors:
            summary += "\n\nFalhas:\n" + "\n".join(errors)
        messagebox.showinfo("Importação em Lote", summary)
