"""Main application window. Owns the ProjectStore instance and the
current-document selection, and wires the three panels + Excel tab
together. UI panels call back into methods here rather than reaching
into each other directly.
"""
from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk, filedialog, messagebox, simpledialog

from .. import import_log
from ..storage import ProjectStore
from .codes_panel import CodesPanel
from .documents_panel import DocumentsPanel
from .coding_panel import CodingPanel
from . import excel_actions
from .documents_validation_tab import DocumentsValidationTab
from .analysis_tab import AnalysisTab

# Bundled next to the package (see QDA_Claude.spec datas).
HELP_DIR = Path(__file__).resolve().parent.parent / "help"
# Window size and divider positions, remembered per user (not per project).
LAYOUT_FILE = Path.home() / ".qda_planapp" / "layout.json"


class QDAApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("QDA - PLANAPP")
        self.geometry("1200x700")

        self.store = ProjectStore(path=None)  # type: ignore[arg-type]
        self.current_doc: dict | None = None

        self._enlarge_default_fonts()
        self.build_ui()
        self.build_menu()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._restore_layout()

    # -------------------------
    # Window layout (size + divider positions), kept per user
    # -------------------------
    def _split_panes(self) -> dict[str, tk.PanedWindow]:
        return {"coding": self.coding_split, "documents": self.documents_panel.split}

    def _restore_layout(self):
        try:
            layout = json.loads(LAYOUT_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(layout.get("geometry"), str):
            self.geometry(layout["geometry"])

        def place_sashes():
            for name, pane in self._split_panes().items():
                for index, coord in enumerate(layout.get("sashes", {}).get(name, [])):
                    try:
                        x, y = pane.sash_coord(index)
                        if pane.cget("orient") == "horizontal":
                            x = int(coord)
                        else:
                            y = int(coord)
                        pane.sash_place(index, x, y)
                    except (tk.TclError, TypeError, ValueError):
                        pass

        # Sashes can only be placed once the panes have their real size.
        self.after(200, place_sashes)

    def _save_layout(self):
        sashes = {}
        for name, pane in self._split_panes().items():
            axis = 0 if pane.cget("orient") == "horizontal" else 1
            sashes[name] = [pane.sash_coord(i)[axis] for i in range(len(pane.panes()) - 1)]
        try:
            LAYOUT_FILE.parent.mkdir(parents=True, exist_ok=True)
            LAYOUT_FILE.write_text(json.dumps({"geometry": self.geometry(), "sashes": sashes}), encoding="utf-8")
        except OSError:
            pass  # a layout that isn't remembered is no reason to block closing

    def close(self):
        self._save_layout()
        self.destroy()

    # -------------------------
    # Fonts
    # -------------------------
    def _enlarge_default_fonts(self):
        """Bump the default Tk fonts by 2pt so buttons and notebook tabs
        (which don't set an explicit font, and so inherit these) read
        larger. Widgets with their own explicit font= are unaffected."""
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            f = tkfont.nametofont(name)
            f.configure(size=f.cget("size") + 2)

        style = ttk.Style()
        style.configure("TNotebook.Tab", font=tkfont.nametofont("TkDefaultFont"))

    # -------------------------
    # UI Layout
    # -------------------------
    def build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.main_tabs = ttk.Notebook(self)
        self.main_tabs.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

        self.coding_tab = tk.Frame(self.main_tabs)
        self.documents_validation_tab_frame = tk.Frame(self.main_tabs)
        self.analysis_tab_frame = tk.Frame(self.main_tabs)

        self.main_tabs.add(self.coding_tab, text="Codificação")
        self.main_tabs.add(self.documents_validation_tab_frame, text="Leitura e validação de documentos")
        self.main_tabs.add(self.analysis_tab_frame, text="Análise")

        # Three columns split by draggable dividers. Extra width from
        # resizing the window goes to the text, so the side columns keep
        # the width they were dragged to.
        self.coding_tab.columnconfigure(0, weight=1)
        self.coding_tab.rowconfigure(0, weight=1)
        self.coding_split = tk.PanedWindow(self.coding_tab, orient="horizontal", sashwidth=6, sashrelief="raised", bd=0)
        self.coding_split.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

        self.documents_panel = DocumentsPanel(self.coding_split, self)
        self.coding_panel = CodingPanel(self.coding_split, self)
        self.codes_panel = CodesPanel(self.coding_split, self)
        self.coding_split.add(self.documents_panel.frame, width=300, minsize=150, stretch="never", padx=3)
        self.coding_split.add(self.coding_panel.frame, width=560, minsize=300, stretch="always", padx=3)
        self.coding_split.add(self.codes_panel.frame, width=300, minsize=150, stretch="never", padx=3)

        self.documents_validation_tab = DocumentsValidationTab(self.documents_validation_tab_frame, self)
        self.documents_validation_tab_frame.columnconfigure(0, weight=1)
        self.documents_validation_tab_frame.rowconfigure(0, weight=1)
        self.documents_validation_tab.grid(row=0, column=0, sticky="nsew")

        self.analysis_tab = AnalysisTab(self.analysis_tab_frame, self)
        self.analysis_tab_frame.columnconfigure(0, weight=1)
        self.analysis_tab_frame.rowconfigure(0, weight=1)
        self.analysis_tab.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

        self._tab_frames = {
            self.documents_validation_tab: self.documents_validation_tab_frame,
            self.analysis_tab: self.analysis_tab_frame,
        }
        self._stale_tabs: set = set()
        self.main_tabs.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def build_menu(self):
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="New Project", command=self.create_project)
        file_menu.add_command(label="Open Project", command=self.open_project_menu)
        file_menu.add_separator()
        file_menu.add_command(label="Save", command=self.save_project)
        file_menu.add_command(label="Export Fragments to Excel", command=lambda: excel_actions.export_fragments(self))
        file_menu.add_command(label="Update Fragments from Excel (by Fragment ID)", command=lambda: excel_actions.import_fragments(self))
        file_menu.add_command(label="Append New Fragments from Excel (rows without ID)", command=lambda: excel_actions.append_fragments(self))
        file_menu.add_command(label="Import Document Attributes", command=self.documents_panel.import_document_attributes)
        file_menu.add_command(label="Export Document Attributes", command=self.documents_panel.export_document_attributes)
        file_menu.add_command(label="Export Codebook to Excel", command=self.codes_panel.export_codebook)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.close)

        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="Excel Import Rules", command=self.show_import_rules)
        help_menu.add_command(label="Open Import Log", command=self.show_import_log)

    def show_import_rules(self):
        self.show_text_window("Excel Import Rules", HELP_DIR / "excel_import_rules.txt")

    def show_import_log(self):
        if not self.require_project():
            return
        path = import_log.log_path(self.store)
        if not path.exists():
            messagebox.showinfo("Import Log", "No imports have been logged for this project yet.")
            return
        self.show_text_window(f"Import Log - {path}", path, scroll_to_end=True)

    def show_text_window(self, title: str, path: Path, scroll_to_end: bool = False):
        """Read-only viewer for a help or log text file."""
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            messagebox.showerror(title, f"Could not open {path}:\n{e}")
            return
        window = tk.Toplevel(self)
        window.title(title)
        window.geometry("820x640")
        text = tk.Text(window, wrap="word", font=("Consolas", 10))
        scrollbar = ttk.Scrollbar(window, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        text.insert("1.0", content)
        text.configure(state="disabled")
        if scroll_to_end:
            text.see("end")

    # -------------------------
    # Shared helpers used by panels
    # -------------------------
    def require_project(self) -> bool:
        if not self.store.path:
            messagebox.showwarning("No Project", "Open or create a project first")
            return False
        return True

    def set_current_doc(self, doc: dict | None):
        self.current_doc = doc

    def refresh_tab(self, tab):
        """Redraw a whole-project tab (documents board, analysis) now if
        it's showing, else when it's next opened. Both are slow to redraw
        -- the analysis reruns on every fragment change -- and most
        changes happen while the Codificação tab is in front."""
        if self.main_tabs.select() == str(self._tab_frames[tab]):
            tab.refresh()
        else:
            self._stale_tabs.add(tab)

    def _on_tab_changed(self, event=None):
        for tab in list(self._stale_tabs):
            if self.main_tabs.select() == str(self._tab_frames[tab]):
                self._stale_tabs.discard(tab)
                tab.refresh()

    def on_document_validation_changed(self, doc: dict):
        """Called by DocumentsValidationTab whenever a document's status
        changes. Refreshes the Codificação Documents list (which only shows
        validated documents) and, if the document that just left "validated"
        status was open there, redirects the same way deleting a document
        does rather than leaving a stale/invisible document loaded."""
        self.documents_panel.populate()
        self.documents_panel.refresh_documents_window()

        if self.current_doc and self.current_doc["id"] == doc["id"] and self.store.document_validation_status_of(doc) != "validated":
            remaining = self.store.validated_documents()
            self.current_doc = remaining[0] if remaining else None
            if self.current_doc:
                self.load_document()
            else:
                self.clear_text_view()

    # -------------------------
    # Project lifecycle
    # -------------------------
    def create_project(self):
        path = filedialog.askdirectory(title="Select or create project folder")
        if not path:
            return
        self.store = ProjectStore(path)
        self.store.create()
        self.refresh_all()
        messagebox.showinfo("Project Created", f"Project created at:\n{path}")

    def open_project_menu(self):
        path = filedialog.askdirectory(title="Open QDA Project Folder")
        if not path:
            return
        self.store = ProjectStore(path)
        self.store.load()
        self.refresh_all()

    def save_project(self):
        if not self.require_project():
            return
        self.store.save_all()
        messagebox.showinfo("Saved", "Project data saved successfully")

    def refresh_all(self):
        """Re-render every panel from current store state. Call after
        opening/creating a project, or any bulk mutation (e.g. import)."""
        self.documents_panel.populate()
        self.documents_panel.clear_fragments()
        self.coding_panel.clear()
        self.codes_panel.populate()
        self.refresh_tab(self.documents_validation_tab)
        self.refresh_tab(self.analysis_tab)

        docs = self.store.validated_documents()
        if docs:
            self.current_doc = docs[0]
            self.load_document()
        else:
            self.current_doc = None

    # -------------------------
    # Document loading / fragments
    # -------------------------
    def load_document(self):
        if not self.current_doc:
            return
        content = self.store.read_document_text(self.current_doc)
        self.coding_panel.load_text(content)
        self.render_fragments()

    def clear_text_view(self):
        self.coding_panel.clear()
        self.documents_panel.clear_fragments()

    def render_fragments(self):
        rows = self.coding_panel.render_fragments()
        self.documents_panel.set_fragment_rows(rows)
        self.refresh_tab(self.analysis_tab)

    def on_fragment_created(self):
        """Called by CodingPanel after a fragment+link is created."""
        self.render_fragments()

    def uncode_fragment(self, fragment: dict) -> bool:
        """Delete a coded fragment (its code links and memo go with it)
        after confirmation. Returns True if it was deleted."""
        code_names = self.store.fragment_label_text(fragment["id"])
        preview = self.coding_panel.get_fragment_text(fragment)[:80]
        if not messagebox.askyesno(
            "Uncode Fragment",
            f"Remove coding '{code_names}' from:\n\n“{preview}…”\n\n"
            "The fragment's memo and attributes will also be deleted.",
        ):
            return False

        self.store.delete_fragment(fragment["id"])
        self.store.save_fragments()
        self.store.save_links()
        self.store.save_memos()
        self.coding_panel.reset_hover()
        self.render_fragments()
        return True

    def on_select_fragment(self):
        frag_id = self.documents_panel.selected_fragment_id()
        if not frag_id:
            return
        fragment = next((f for f in self.store.fragments if f["id"] == frag_id), None)
        if fragment:
            self.coding_panel.show_fragment_details(fragment)

    # -------------------------
    # Codebook
    # -------------------------
    def new_label(self, level: dict | None = None, parent_label: dict | None = None) -> dict | None:
        return self.codes_panel.new_label(level, parent_label)

    def on_codebook_changed(self):
        """Called after any change to levels/labels (or to which labels a
        fragment carries outside the coding panel): re-render everything
        that shows label names, colors or level fields."""
        self.codes_panel.populate()
        self.render_fragments()

    # -------------------------
    # Code creation triggered from the text panel's context menu
    # -------------------------
    def create_code_from_menu(self):
        if not self.require_project():
            return
        code = self.codes_panel.new_label()
        if code:
            self.coding_panel.assign_code(lambda: code)
