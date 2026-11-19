"""Analysis tab: JASP/jamovi-style layout -- a scrolling options panel on
the left (filters, variables and their roles, supplementary elements, rare
categories), and on the right a chart with its display options above it and
the result tables below. Recomputes whenever an analysis option changes;
display options only redraw the chart.
"""
from __future__ import annotations

import math
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from . import theme
from .scaling import px, scale
from .. import analysis
from . import widgets


def format_cell(value, decimal: str = ".") -> str:
    """A table value as shown/copied: whole numbers without decimals,
    others to 3 decimals (scientific notation when tiny, e.g. p-values)."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)) or hasattr(value, "dtype"):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if math.isnan(number):
            return ""
        if number.is_integer() and abs(number) < 1e15:
            text = str(int(number))
        elif 0 < abs(number) < 0.001:
            text = f"{number:.2e}"
        else:
            text = f"{number:.3f}"
        return text.replace(".", decimal)
    return str(value)


def system_decimal_separator() -> str:
    """The decimal separator Excel will expect when pasting (Windows
    regional setting), "." elsewhere or if it can't be read."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\International") as key:
            return winreg.QueryValueEx(key, "sDecimal")[0] or "."
    except (ImportError, OSError):
        return "."


NETWORK = "Rede"
CA = "Análise de Correspondência (CA)"
MCA = "Análise de Correspondência Múltipla (MCA)"
ANALYSES = [NETWORK, CA, MCA]

RARE_MODES = {
    "Suplementares (passivas)": analysis.RARE_SUPPLEMENTARY,
    f"Agrupar em '{analysis.OTHER_CATEGORY}'": analysis.RARE_MERGE,
}
CA_MAPS = {
    "Simétrico": analysis.CA_SYMMETRIC,
    "Linhas principais": analysis.CA_ROW_PRINCIPAL,
    "Colunas principais": analysis.CA_COL_PRINCIPAL,
}
LABEL_MODES = {
    "Todos": analysis.LABELS_ALL,
    "Top N (contribuição)": analysis.LABELS_CONTRIB,
    "Top N (cos²)": analysis.LABELS_COS2,
    "Nenhum": analysis.LABELS_NONE,
}
SIZE_MODES = {"Fixo": analysis.SIZE_FIXED, "Massa": analysis.SIZE_MASS, "Contribuição": analysis.SIZE_CONTRIB}
NO_COLOR = "(nenhuma)"
CHART_MAP, CHART_SCREE = "map", "scree"


def _number(var: tk.Variable, default: float, low: float, high: float) -> float:
    try:
        value = float(str(var.get()).replace(",", "."))
    except (TypeError, ValueError, tk.TclError):
        return default
    return min(max(value, low), high)


class FieldList:
    """A titled listbox of field names, filled from the "available" list."""

    def __init__(self, parent, title: str, height: int = 4):
        self.frame = tk.Frame(parent)
        self.frame.pack(fill="x", pady=(px(4), 0))
        tk.Label(self.frame, text=title, fg=theme.MUTED, anchor="w").pack(fill="x")
        box = tk.Frame(self.frame)
        box.pack(fill="x")
        self.listbox = tk.Listbox(box, selectmode="extended", height=height, exportselection=False)
        widgets.add_scrollbars(box, self.listbox)

    def fields(self) -> list[str]:
        return list(self.listbox.get(0, tk.END))

    def add(self, names) -> None:
        existing = set(self.fields())
        for name in names:
            if name not in existing:
                self.listbox.insert(tk.END, name)

    def remove_selected(self) -> None:
        for index in reversed(self.listbox.curselection()):
            self.listbox.delete(index)

    def keep_only(self, allowed) -> None:
        kept = [f for f in self.fields() if f in allowed]
        self.listbox.delete(0, tk.END)
        for name in kept:
            self.listbox.insert(tk.END, name)


class AnalysisTab:
    def __init__(self, parent, app):
        """
        app must provide:
          - app.store: ProjectStore
        """
        self.app = app
        self._analysis = None          # last CAAnalysis / MCAAnalysis / None
        self._fragments: list[dict] = []
        self._fields: list[str] = []
        self.ca_sup_categories = {"rows": set(), "cols": set()}
        self._ca_pair: tuple[str, str] = ("", "")

        self.frame = tk.Frame(parent)
        self.frame.columnconfigure(0, weight=0)
        self.frame.columnconfigure(1, weight=1)
        self.frame.rowconfigure(0, weight=1)

        self._make_variables()
        self._build_options_panel()
        self._build_results_panel()
        self._on_analysis_type_changed(recompute=False)

    def grid(self, **kwargs):
        self.frame.grid(**kwargs)

    # -- state ---------------------------------------------------------------------
    def _make_variables(self):
        self.analysis_var = tk.StringVar(value=ANALYSES[0])
        self.row_field_var = tk.StringVar()
        self.col_field_var = tk.StringVar()
        self.rare_threshold_var = tk.StringVar(value="0")
        self.rare_mode_var = tk.StringVar(value=next(iter(RARE_MODES)))
        self.table_dims_var = tk.StringVar(value="3")

        # Chart display options (kept across recomputes).
        self.chart_var = tk.StringVar(value=CHART_MAP)
        self.x_dim_var = tk.StringVar(value="Dim 1")
        self.y_dim_var = tk.StringVar(value="Dim 2")
        self.ca_map_var = tk.StringVar(value="Simétrico")
        self.show_rows_var = tk.BooleanVar(value=True)
        self.show_cols_var = tk.BooleanVar(value=True)
        self.show_categories_var = tk.BooleanVar(value=True)
        self.show_fragments_var = tk.BooleanVar(value=False)
        self.show_sup_var = tk.BooleanVar(value=True)
        self.color_by_var = tk.StringVar(value=NO_COLOR)
        self.ellipses_var = tk.BooleanVar(value=False)
        self.labels_var = tk.StringVar(value="Todos")
        self.top_n_var = tk.StringVar(value="10")
        self.size_var = tk.StringVar(value="Fixo")

    # -- options panel (left) ----------------------------------------------------------
    def _build_options_panel(self):
        left = tk.Frame(self.frame)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, px(10)))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        scroller = widgets.ScrollableFrame(left, width=px(290))
        scroller.outer.grid(row=0, column=0, sticky="nsew")
        panel = scroller.inner

        # Always visible below the scrolling options, so messages can't scroll away.
        self.status_label = tk.Label(left, text="", fg=theme.ERROR, wraplength=px(280), justify="left", anchor="w")
        self.status_label.grid(row=1, column=0, sticky="ew", pady=(px(6), 0))

        self.sections = widgets.SectionStack(panel)
        self._build_analysis_section(self.sections.add("Análise"))
        self._build_filter_section(self.sections.add("Filtros"))
        self.variables_section = self.sections.add("Variáveis")
        self._build_variables_section(self.variables_section)
        self.sup_section = self.sections.add("Elementos suplementares")
        self._build_sup_section(self.sup_section)
        self.rare_section = self.sections.add("Categorias raras")
        self._build_rare_section(self.rare_section)
        self.output_section = self.sections.add("Resultados", expanded=False)
        self._build_output_section(self.output_section)

    def _build_analysis_section(self, section):
        for name in ANALYSES:
            tk.Radiobutton(
                section.body, text=name, variable=self.analysis_var, value=name,
                command=self._on_analysis_type_changed, anchor="w", justify="left", wraplength=px(260),
            ).pack(fill="x")

    def _build_filter_section(self, section):
        """Field = value filters applied before any analysis: different
        fields must all match (AND), several values of one field are
        alternatives (OR)."""
        self.filters: dict[str, list[str]] = {}
        body = section.body
        pick = tk.Frame(body)
        pick.pack(fill="x")
        pick.columnconfigure(0, weight=1)
        pick.columnconfigure(1, weight=1)
        self.filter_field_var = tk.StringVar()
        self.filter_field_combo = ttk.Combobox(pick, textvariable=self.filter_field_var, state="readonly", width=10)
        self.filter_field_combo.grid(row=0, column=0, sticky="ew", padx=(0, 2))
        self.filter_field_combo.bind("<<ComboboxSelected>>", lambda e: self._on_filter_field_changed())
        self.filter_value_var = tk.StringVar()
        self.filter_value_combo = ttk.Combobox(pick, textvariable=self.filter_value_var, state="readonly", width=10)
        self.filter_value_combo.grid(row=0, column=1, sticky="ew", padx=(0, 2))
        tk.Button(pick, text="+", width=2, command=self._add_filter).grid(row=0, column=2)

        self.filter_list = tk.Listbox(body, height=3, exportselection=False)
        self.filter_list.pack(fill="x", pady=(4, 0))
        bottom = tk.Frame(body)
        bottom.pack(fill="x", pady=(px(4), 0))
        tk.Button(bottom, text="Remover filtro", command=self._remove_filter).pack(side="left")
        self.filter_count_label = tk.Label(bottom, text="", fg=theme.MUTED, font=("Segoe UI", 9))
        self.filter_count_label.pack(side="right")

    def _build_variables_section(self, section):
        body = section.body
        tk.Label(body, text="Campos disponíveis", fg=theme.MUTED, anchor="w").pack(fill="x")
        avail = tk.Frame(body)
        avail.pack(fill="x")
        self.available_list = tk.Listbox(avail, selectmode="extended", height=6, exportselection=False)
        widgets.add_scrollbars(avail, self.available_list)

        # CA roles.
        self.ca_roles = tk.Frame(body)
        for text, var in (("Variável de linha", self.row_field_var), ("Variável de coluna", self.col_field_var)):
            tk.Label(self.ca_roles, text=text, fg=theme.MUTED, anchor="w").pack(fill="x", pady=(px(6), 0))
            combo = ttk.Combobox(self.ca_roles, textvariable=var, state="readonly")
            combo.pack(fill="x")
            combo.bind("<<ComboboxSelected>>", lambda e: self._on_ca_fields_changed())
            setattr(self, "row_combo" if var is self.row_field_var else "col_combo", combo)

        # Network / MCA: list of active variables.
        self.list_roles = tk.Frame(body)
        move = tk.Frame(self.list_roles)
        move.pack(fill="x", pady=(px(6), 0))
        tk.Button(move, text="Adicionar →", command=self._add_active).pack(side="left", padx=(0, 4))
        tk.Button(move, text="← Remover", command=lambda: self._remove_from(self.active_list)).pack(side="left")
        self.active_list = FieldList(self.list_roles, "Variáveis ativas", height=5)

    def _build_sup_section(self, section):
        body = section.body
        tk.Label(body, text="Projetados no mapa sem influenciar os eixos. Selecione campos acima e adicione-os aqui.",
                 fg=theme.MUTED, wraplength=px(270), justify="left", anchor="w").pack(fill="x")

        # MCA: supplementary variables.
        self.mca_sup = tk.Frame(body)
        row = tk.Frame(self.mca_sup)
        row.pack(fill="x", pady=(px(6), 0))
        tk.Button(row, text="Adicionar →", command=lambda: self._add_to(self.mca_sup_list)).pack(side="left", padx=(0, 4))
        tk.Button(row, text="← Remover", command=lambda: self._remove_from(self.mca_sup_list)).pack(side="left")
        self.mca_sup_list = FieldList(self.mca_sup, "Variáveis suplementares", height=4)

        # CA: supplementary rows / columns (other variables) and categories.
        self.ca_sup = tk.Frame(body)
        self.ca_sup_rows = self._sup_list_with_buttons(self.ca_sup, "Linhas suplementares (variáveis)")
        self.ca_sup_cols = self._sup_list_with_buttons(self.ca_sup, "Colunas suplementares (variáveis)")
        cat_row = tk.Frame(self.ca_sup)
        cat_row.pack(fill="x", pady=(px(8), 0))
        tk.Button(cat_row, text="Categorias suplementares…", command=self._choose_ca_categories).pack(side="left")
        self.ca_cat_label = tk.Label(self.ca_sup, text="", fg=theme.MUTED, anchor="w", justify="left",
                                     wraplength=px(270))
        self.ca_cat_label.pack(fill="x")

    def _sup_list_with_buttons(self, parent, title: str) -> FieldList:
        field_list = FieldList(parent, title, height=3)
        row = tk.Frame(field_list.frame)
        row.pack(fill="x", pady=(px(2), 0))
        tk.Button(row, text="Adicionar →", command=lambda: self._add_to(field_list)).pack(side="left", padx=(0, 4))
        tk.Button(row, text="← Remover", command=lambda: self._remove_from(field_list)).pack(side="left")
        return field_list

    def _build_rare_section(self, section):
        body = section.body
        tk.Label(body, text="Categorias presentes em menos linhas do que o limiar deixam de definir os eixos.",
                 fg=theme.MUTED, wraplength=px(270), justify="left", anchor="w").pack(fill="x")
        row = tk.Frame(body)
        row.pack(fill="x", pady=(px(6), 0))
        tk.Label(row, text="Limiar (% das linhas):").pack(side="left")
        spin = tk.Spinbox(row, from_=0, to=50, increment=0.5, width=5, textvariable=self.rare_threshold_var,
                          command=self.recompute)
        spin.pack(side="left", padx=(px(6), 0))
        spin.bind("<Return>", lambda e: self.recompute())
        spin.bind("<FocusOut>", lambda e: self.recompute())
        tk.Label(body, text="Tratamento", fg=theme.MUTED, anchor="w").pack(fill="x", pady=(px(6), 0))
        mode = ttk.Combobox(body, textvariable=self.rare_mode_var, values=list(RARE_MODES), state="readonly")
        mode.pack(fill="x")
        mode.bind("<<ComboboxSelected>>", lambda e: self.recompute())
        tk.Label(body, text="0 = desativado.", fg=theme.SUBTLE, anchor="w").pack(fill="x")

    def _build_output_section(self, section):
        row = tk.Frame(section.body)
        row.pack(fill="x")
        tk.Label(row, text="Dimensões nas tabelas:").pack(side="left")
        spin = tk.Spinbox(row, from_=1, to=20, increment=1, width=4, textvariable=self.table_dims_var,
                          command=self._refresh_tables)
        spin.pack(side="left", padx=(px(6), 0))
        spin.bind("<Return>", lambda e: self._refresh_tables())
        spin.bind("<FocusOut>", lambda e: self._refresh_tables())

    # -- role / list actions ---------------------------------------------------------------
    def _selected_available(self) -> list[str]:
        return [self.available_list.get(i) for i in self.available_list.curselection()]

    def _add_active(self):
        self.active_list.add(self._selected_available())
        self.recompute()

    def _add_to(self, field_list: FieldList):
        field_list.add(self._selected_available())
        self.recompute()

    def _remove_from(self, field_list: FieldList):
        field_list.remove_selected()
        self.recompute()

    def _on_ca_fields_changed(self):
        pair = (self.row_field_var.get(), self.col_field_var.get())
        if pair != self._ca_pair:  # categories chosen for other variables no longer apply
            if pair[0] != self._ca_pair[0]:
                self.ca_sup_categories["rows"] = set()
            if pair[1] != self._ca_pair[1]:
                self.ca_sup_categories["cols"] = set()
            self._ca_pair = pair
        self._show_ca_categories()
        self.recompute()

    def _show_ca_categories(self):
        parts = [f"{name}: {', '.join(sorted(cats, key=str.lower))}"
                 for name, cats in (("Linhas", self.ca_sup_categories["rows"]), ("Colunas", self.ca_sup_categories["cols"]))
                 if cats]
        self.ca_cat_label.config(text="\n".join(parts) or "Nenhuma categoria suplementar escolhida.")

    def _choose_ca_categories(self):
        row_field, col_field = self.row_field_var.get(), self.col_field_var.get()
        if not row_field or not col_field:
            messagebox.showinfo("Categorias suplementares", "Escolha primeiro as variáveis de linha e de coluna.")
            return
        dialog = tk.Toplevel(self.frame)
        dialog.title("Categorias suplementares")
        dialog.transient(self.frame.winfo_toplevel())
        tk.Label(dialog, text="Categorias projetadas no mapa sem definir os eixos:", font=theme.H3, anchor="w").pack(
            fill="x", padx=px(14), pady=(px(12), px(6)))
        columns = tk.Frame(dialog)
        columns.pack(fill="both", expand=True, padx=px(14))
        chosen = {}
        for col, (key, field_name) in enumerate((("rows", row_field), ("cols", col_field))):
            box = tk.Frame(columns)
            box.grid(row=0, column=col, sticky="nw", padx=(0, px(18)))
            tk.Label(box, text=field_name, fg=theme.MUTED, anchor="w").pack(fill="x")
            chosen[key] = {}
            for value in analysis.field_values(self.app.store, field_name):
                var = tk.BooleanVar(value=value in self.ca_sup_categories[key])
                tk.Checkbutton(box, text=value, variable=var, anchor="w").pack(fill="x")
                chosen[key][value] = var

        def apply():
            for key in ("rows", "cols"):
                self.ca_sup_categories[key] = {v for v, var in chosen[key].items() if var.get()}
            dialog.destroy()
            self._show_ca_categories()
            self.recompute()

        buttons = tk.Frame(dialog)
        buttons.pack(fill="x", padx=px(14), pady=px(12))
        tk.Button(buttons, text="Cancelar", command=dialog.destroy).pack(side="right")
        theme.primary(tk.Button(buttons, text="Aplicar", command=apply)).pack(side="right", padx=(0, px(6)))
        widgets.center_dialog(dialog, self.frame)
        dialog.grab_set()

    def _on_analysis_type_changed(self, recompute: bool = True):
        kind = self.analysis_var.get()
        self.ca_roles.pack_forget()
        self.list_roles.pack_forget()
        self.mca_sup.pack_forget()
        self.ca_sup.pack_forget()
        if kind == CA:
            self.ca_roles.pack(fill="x")
            self.ca_sup.pack(fill="x")
        else:
            self.list_roles.pack(fill="x")
            if kind == MCA:
                self.mca_sup.pack(fill="x")
        correspondence = kind in (CA, MCA)
        for section in (self.sup_section, self.rare_section, self.output_section):
            self.sections.set_visible(section, correspondence)
        self._build_plot_bar()
        self._show_ca_categories()
        self._refresh_available_fields()
        if recompute:
            self.recompute()

    # -- filters ---------------------------------------------------------------------------
    def _on_filter_field_changed(self):
        field = self.filter_field_var.get()
        self.filter_value_combo["values"] = analysis.field_values(self.app.store, field) if field else []
        self.filter_value_var.set("")

    def _add_filter(self):
        field, value = self.filter_field_var.get(), self.filter_value_var.get()
        if not field or not value:
            return
        values = self.filters.setdefault(field, [])
        if value not in values:
            values.append(value)
        self._show_filters()
        self.recompute()

    def _remove_filter(self):
        for index in self.filter_list.curselection():
            field = list(self.filters)[index]
            self.filters.pop(field, None)
        self._show_filters()
        self.recompute()

    def _show_filters(self):
        self.filter_list.delete(0, tk.END)
        for field, values in self.filters.items():
            self.filter_list.insert(tk.END, f"{field} = {', '.join(values)}")

    def _refresh_filters(self, fields: list[str]):
        """Keep the filter choices in step with the project: drop filters
        on fields that no longer exist and refresh the value list."""
        self.filter_field_combo["values"] = fields
        if self.filter_field_var.get() not in fields:
            self.filter_field_var.set("")
        for field in [f for f in self.filters if f not in fields]:
            del self.filters[field]
        self._on_filter_field_changed()
        self._show_filters()

    def _filtered_fragments(self) -> list[dict]:
        fragments = analysis.filter_fragments(self.app.store, self.filters)
        total = len(self.app.store.fragments)
        self.filter_count_label.config(
            text=f"{len(fragments)} de {total} fragmentos" if self.filters else f"{total} fragmentos"
        )
        return fragments

    def _refresh_available_fields(self):
        fields = analysis.available_fields(self.app.store) if self.app.store.path else []
        self._fields = fields
        self.available_list.delete(0, tk.END)
        for field in fields:
            self.available_list.insert(tk.END, field)
        self._refresh_filters(fields)

        self.row_combo["values"] = fields
        self.col_combo["values"] = fields
        for var in (self.row_field_var, self.col_field_var):
            if var.get() not in fields:
                var.set("")
        for field_list in (self.active_list, self.mca_sup_list, self.ca_sup_rows, self.ca_sup_cols):
            field_list.keep_only(fields)

    # -- results panel (right) --------------------------------------------------------------
    def _build_results_panel(self):
        self.results_frame = tk.Frame(self.frame)
        self.results_frame.grid(row=0, column=1, sticky="nsew")
        self.results_frame.columnconfigure(0, weight=1)
        self.results_frame.rowconfigure(1, weight=3)
        self.results_frame.rowconfigure(5, weight=2)

        self.plot_bar = tk.Frame(self.results_frame)
        self.plot_bar.grid(row=0, column=0, sticky="ew", pady=(0, px(6)))

        theme.style_matplotlib()
        self.figure = Figure(figsize=(5, 4), dpi=100 * scale())
        self.ax = self.figure.add_subplot(111)
        self.ax.axis("off")
        self.canvas = FigureCanvasTkAgg(self.figure, master=self.results_frame)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew")
        self.canvas.get_tk_widget().configure(highlightthickness=1, highlightbackground=theme.BORDER)

        toolbar_frame = tk.Frame(self.results_frame)
        toolbar_frame.grid(row=2, column=0, sticky="ew")
        toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        toolbar.update()
        for child in toolbar.winfo_children():  # flat icons on the window background
            if isinstance(child, (tk.Button, tk.Checkbutton)):
                child.configure(background=theme.BG, activebackground=theme.BUTTON_PRESSED,
                                relief="flat", overrelief="flat", highlightthickness=0)
            if isinstance(child, tk.Checkbutton):
                child.configure(selectcolor=theme.ACCENT_SOFT, offrelief="flat")

        # Result tables: pick one to read, copy it, or export them all.
        self.tables: dict = {}
        self.tables_context: dict = {}
        table_bar = tk.Frame(self.results_frame)
        table_bar.grid(row=3, column=0, sticky="ew", pady=(6, 2))
        tk.Label(table_bar, text="Tabela:", font=theme.H3).pack(side="left")
        self.table_var = tk.StringVar()
        self.table_combo = ttk.Combobox(table_bar, textvariable=self.table_var, state="readonly", width=34)
        self.table_combo.pack(side="left", padx=(4, 8))
        self.table_combo.bind("<<ComboboxSelected>>", lambda e: self._show_current_table())
        tk.Button(table_bar, text="Exportar tabelas (Excel)…", command=self._export_tables).pack(side="right")
        tk.Button(table_bar, text="Copiar tabela", command=self._copy_table).pack(side="right", padx=4)

        table_frame = tk.Frame(self.results_frame)
        table_frame.grid(row=5, column=0, sticky="nsew")
        self.results_table = ttk.Treeview(table_frame, show="headings", height=8)
        widgets.add_scrollbars(table_frame, self.results_table, horizontal=True)

    # -- chart display options --------------------------------------------------------------
    def _build_plot_bar(self):
        for child in self.plot_bar.winfo_children():
            child.destroy()
        kind = self.analysis_var.get()
        if kind not in (CA, MCA):
            self.plot_bar.grid_remove()
            return
        self.plot_bar.grid()

        def combo(parent, var, values, width):
            box = ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=width)
            box.bind("<<ComboboxSelected>>", lambda e: self._draw_chart())
            return box

        def check(parent, text, var):
            return tk.Checkbutton(parent, text=text, variable=var, command=self._draw_chart)

        def label(parent, text):
            return tk.Label(parent, text=text, fg=theme.MUTED)

        # Three short rows, so the bar never forces the window wider.
        rows = [tk.Frame(self.plot_bar) for _ in range(3 if kind == MCA else 2)]
        for i, row in enumerate(rows):
            row.pack(fill="x", pady=(px(6) if i else 0, 0))
        top, middle = rows[0], rows[1]

        for value, text in ((CHART_MAP, "Mapa"), (CHART_SCREE, "Valores próprios")):
            theme.segmented(tk.Radiobutton(
                top, text=text, value=value, variable=self.chart_var, indicatoron=False,
                command=self._draw_chart, padx=px(10),
            )).pack(side="left")
        label(top, "Eixos").pack(side="left", padx=(px(16), px(4)))
        self.x_combo = combo(top, self.x_dim_var, [], 7)
        self.x_combo.pack(side="left")
        label(top, "×").pack(side="left", padx=px(4))
        self.y_combo = combo(top, self.y_dim_var, [], 7)
        self.y_combo.pack(side="left")
        if kind == CA:
            label(top, "Mapa").pack(side="left", padx=(px(16), px(4)))
            combo(top, self.ca_map_var, list(CA_MAPS), 17).pack(side="left")

        label(middle, "Mostrar").pack(side="left", padx=(0, px(4)))
        if kind == CA:
            check(middle, "Linhas", self.show_rows_var).pack(side="left")
            check(middle, "Colunas", self.show_cols_var).pack(side="left")
        else:
            check(middle, "Categorias", self.show_categories_var).pack(side="left")
            check(middle, "Fragmentos", self.show_fragments_var).pack(side="left")
        check(middle, "Suplementares", self.show_sup_var).pack(side="left")
        label(middle, "Rótulos").pack(side="left", padx=(px(16), px(4)))
        combo(middle, self.labels_var, list(LABEL_MODES), 18).pack(side="left")
        top_n = tk.Spinbox(middle, from_=1, to=200, width=4, textvariable=self.top_n_var, command=self._draw_chart)
        top_n.pack(side="left", padx=(px(4), 0))
        top_n.bind("<Return>", lambda e: self._draw_chart())
        label(middle, "Tamanho").pack(side="left", padx=(px(16), px(4)))
        combo(middle, self.size_var, list(SIZE_MODES), 12).pack(side="left")

        if kind == MCA:
            bottom = rows[2]
            label(bottom, "Colorir fragmentos por").pack(side="left", padx=(0, px(4)))
            self.color_combo = combo(bottom, self.color_by_var, [NO_COLOR], 16)
            self.color_combo.pack(side="left")
            check(bottom, "Elipses de confiança (95%)", self.ellipses_var).pack(side="left", padx=(px(8), 0))

    def _update_plot_choices(self):
        """Dimension and colour choices follow the current results."""
        an = self._analysis
        n_dims = an.result.n_dims if an is not None else 2
        dims = [f"Dim {i + 1}" for i in range(max(n_dims, 1))]
        self.x_combo["values"] = dims
        self.y_combo["values"] = dims
        if self.x_dim_var.get() not in dims:
            self.x_dim_var.set(dims[0])
        if self.y_dim_var.get() not in dims:
            self.y_dim_var.set(dims[1] if len(dims) > 1 else dims[0])
        if self.analysis_var.get() == MCA and isinstance(an, analysis.MCAAnalysis):
            choices = [NO_COLOR] + an.fields + an.sup_fields
            self.color_combo["values"] = choices
            if self.color_by_var.get() not in choices:
                self.color_by_var.set(NO_COLOR)

    def _map_options(self) -> analysis.MapOptions:
        def dim(var):
            try:
                return int(var.get().split()[-1]) - 1
            except (ValueError, IndexError):
                return 0
        color = self.color_by_var.get()
        return analysis.MapOptions(
            dims=(dim(self.x_dim_var), dim(self.y_dim_var)),
            ca_map=CA_MAPS.get(self.ca_map_var.get(), analysis.CA_SYMMETRIC),
            show_rows=self.show_rows_var.get(), show_cols=self.show_cols_var.get(),
            show_categories=self.show_categories_var.get(), show_fragments=self.show_fragments_var.get(),
            show_sup=self.show_sup_var.get(),
            color_by=None if color == NO_COLOR else color, ellipses=self.ellipses_var.get(),
            labels=LABEL_MODES.get(self.labels_var.get(), analysis.LABELS_ALL),
            top_n=int(_number(self.top_n_var, 10, 1, 200)),
            size=SIZE_MODES.get(self.size_var.get(), analysis.SIZE_FIXED),
        )

    def _draw_chart(self):
        an = self._analysis
        if not isinstance(an, (analysis.CAAnalysis, analysis.MCAAnalysis)):
            return
        self.ax.clear()
        lam = an.result.eigenvalues
        if self.chart_var.get() == CHART_SCREE:
            if isinstance(an, analysis.CAAnalysis):
                analysis.plot_scree(self.ax, lam, lam.mean(), "média dos valores próprios")
            else:
                analysis.plot_scree(self.ax, lam, 1 / an.result.n_vars, "1/K (limiar de Benzécri)")
        elif isinstance(an, analysis.CAAnalysis):
            analysis.plot_ca_map(self.ax, an, self._map_options())
        else:
            analysis.plot_mca_map(self.ax, an, self._map_options())
        self._finish_draw()

    def _finish_draw(self):
        try:
            self.figure.tight_layout()
        except (ValueError, RuntimeError):
            pass
        self.canvas.draw_idle()

    # -- tables --------------------------------------------------------------------------------
    def _show_tables(self, tables: dict, default: str, context: dict):
        """Make `tables` ({title: DataFrame}) the current results: keep the
        table being read if the new results have one of that title."""
        self.tables = tables
        self.tables_context = {
            "Análise": self.analysis_var.get(),
            **context,
            "Filtros": "; ".join(f"{f} = {', '.join(v)}" for f, v in self.filters.items()) or "(nenhum)",
            "Fragmentos analisados": len(self._fragments),
        }
        self.table_combo["values"] = list(tables)
        if self.table_var.get() not in tables:
            self.table_var.set(default if default in tables else next(iter(tables)))
        self._show_current_table()

    def _show_current_table(self):
        df = self.tables.get(self.table_var.get())
        self.results_table.delete(*self.results_table.get_children())
        if df is None:
            self.results_table["columns"] = ()
            return
        columns = [str(c) for c in df.columns]
        self.results_table["columns"] = columns
        for col in columns:
            self.results_table.heading(col, text=col)
            self.results_table.column(col, width=px(110), anchor="center")
        for row in df.itertuples(index=False):
            self.results_table.insert("", "end", values=[format_cell(v) for v in row])
        widgets.autosize_treeview_columns(self.results_table, min_width=60, stretch=False)

    def _table_dims(self) -> int:
        return int(_number(self.table_dims_var, 3, 1, 20))

    def _refresh_tables(self):
        an = self._analysis
        if isinstance(an, analysis.CAAnalysis):
            self._show_ca_tables(an)
        elif isinstance(an, analysis.MCAAnalysis):
            self._show_mca_tables(an)

    def _copy_table(self):
        """Copy the table being read, tab-separated, as shown -- pastes as
        a table into Excel or Word. Numbers use the system's decimal
        separator so Excel reads them as numbers."""
        df = self.tables.get(self.table_var.get())
        if df is None:
            return
        decimal = system_decimal_separator()
        lines = ["\t".join(str(c) for c in df.columns)]
        for row in df.itertuples(index=False):
            lines.append("\t".join(format_cell(v, decimal) for v in row))
        self.frame.clipboard_clear()
        self.frame.clipboard_append("\n".join(lines))
        self.status_label.config(text=f"Tabela '{self.table_var.get()}' copiada.", fg=theme.SUCCESS)

    def _export_tables(self):
        """Every table of the current results in one workbook, a sheet
        each (unrounded values), plus a sheet describing the analysis."""
        if not self.tables:
            messagebox.showinfo("Exportar tabelas", "Ainda não há resultados para exportar.")
            return
        path = filedialog.asksaveasfilename(
            title="Exportar tabelas", defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")],
            initialfile="analise_tabelas.xlsx",
        )
        if not path:
            return
        try:
            analysis.export_tables(Path(path), self.tables, self.tables_context)
        except OSError as e:
            messagebox.showerror("Exportar tabelas", f"Não foi possível guardar o ficheiro:\n{e}")
            return
        messagebox.showinfo("Exportar tabelas", f"Tabelas exportadas para:\n{path}")

    def _clear_results(self, message: str = ""):
        self._analysis = None
        self.ax.clear()
        self.ax.axis("off")
        self.canvas.draw_idle()
        self.status_label.config(text=message, fg=theme.ERROR)
        self.results_table.delete(*self.results_table.get_children())
        self.results_table["columns"] = ()
        self.tables = {}
        self.table_combo["values"] = ()
        self.table_var.set("")

    # -- recompute -------------------------------------------------------------------------
    def _rare(self) -> analysis.RareCategories:
        return analysis.RareCategories(
            threshold=_number(self.rare_threshold_var, 0, 0, 100),
            mode=RARE_MODES.get(self.rare_mode_var.get(), analysis.RARE_SUPPLEMENTARY),
        )

    def _rare_context(self) -> str:
        rare = self._rare()
        return f"< {rare.threshold:g}% → {self.rare_mode_var.get()}" if rare.threshold > 0 else "(desativado)"

    def recompute(self):
        self.status_label.config(text="", fg=theme.ERROR)
        if not self.app.store.path:
            self._clear_results("Abra ou crie um projeto para analisar.")
            return
        self._fragments = self._filtered_fragments()
        if self.filters and not self._fragments:
            self._clear_results("Nenhum fragmento corresponde aos filtros.")
            return

        try:
            kind = self.analysis_var.get()
            if kind == NETWORK:
                self._run_network()
            elif kind == CA:
                self._run_ca()
            else:
                self._run_mca()
        except analysis.AnalysisError as e:
            self._clear_results(str(e))

    def _run_ca(self):
        row_field = self.row_field_var.get()
        col_field = self.col_field_var.get()
        if not row_field or not col_field:
            self._clear_results("Selecione a variável de linha e a variável de coluna.")
            return
        if row_field == col_field:
            self._clear_results("Escolha duas variáveis diferentes.")
            return

        an = analysis.run_ca(
            self.app.store, self._fragments, row_field, col_field,
            sup_row_fields=self.ca_sup_rows.fields(), sup_col_fields=self.ca_sup_cols.fields(),
            sup_row_categories=self.ca_sup_categories["rows"], sup_col_categories=self.ca_sup_categories["cols"],
            rare=self._rare(),
        )
        self._analysis = an
        self._update_plot_choices()
        self._draw_chart()
        self._show_ca_tables(an)
        ignored = [f for f in self.ca_sup_rows.fields() + self.ca_sup_cols.fields() if f in (row_field, col_field)]
        if ignored:
            self.status_label.config(text=f"Ignorado (já é variável ativa): {', '.join(ignored)}", fg=theme.MUTED)

    def _show_ca_tables(self, an):
        self._show_tables(
            {"Frequências": analysis.frequency_table(an.df, [an.row_field, an.col_field]),
             **analysis.ca_tables(an, self._table_dims())},
            default="Teste qui-quadrado",
            context={
                "Variável de linha": an.row_field, "Variável de coluna": an.col_field,
                "Linhas suplementares (variáveis)": ", ".join(self.ca_sup_rows.fields()) or "(nenhuma)",
                "Colunas suplementares (variáveis)": ", ".join(self.ca_sup_cols.fields()) or "(nenhuma)",
                "Categorias suplementares": "; ".join(
                    f"{k}: {', '.join(sorted(v))}" for k, v in self.ca_sup_categories.items() if v) or "(nenhuma)",
                "Categorias raras": self._rare_context(),
            },
        )

    def _run_mca(self):
        fields = self.active_list.fields()
        if len(fields) < 2:
            self._clear_results("Selecione pelo menos 2 variáveis ativas.")
            return
        sup_fields = self.mca_sup_list.fields()
        an = analysis.run_mca(self.app.store, self._fragments, fields, sup_fields=sup_fields, rare=self._rare())
        self._analysis = an
        self._update_plot_choices()
        self._draw_chart()
        self._show_mca_tables(an)
        ignored = [f for f in sup_fields if f in fields]
        if ignored:
            self.status_label.config(text=f"Ignorado (já é variável ativa): {', '.join(ignored)}", fg=theme.MUTED)

    def _show_mca_tables(self, an):
        self._show_tables(
            {"Frequências": analysis.frequency_table(an.df, an.fields), **analysis.mca_tables(an, self._table_dims())},
            default="Valores próprios",
            context={
                "Variáveis ativas": ", ".join(an.fields),
                "Variáveis suplementares": ", ".join(an.sup_fields) or "(nenhuma)",
                "Categorias raras": self._rare_context(),
            },
        )

    def _run_network(self):
        fields = self.active_list.fields()
        if len(fields) < 2:
            self._clear_results("Selecione pelo menos 2 variáveis.")
            return

        df = analysis.build_field_frame(self.app.store, fields, self._fragments)
        if df.empty:
            self._clear_results("Sem dados suficientes para essas variáveis.")
            return

        graph = analysis.build_cooccurrence_graph(df, fields)
        if graph.number_of_nodes() < 2:
            self._clear_results("Não há coocorrências suficientes entre essas variáveis.")
            return

        self._analysis = None
        self.ax.clear()
        analysis.plot_network(self.ax, graph)
        self._finish_draw()
        self._show_tables(
            {"Frequências": analysis.frequency_table(df, fields), **analysis.network_tables(graph, df, fields)},
            default="Nós (centralidade)", context={"Variáveis": ", ".join(fields)},
        )

    # -- external refresh hook -------------------------------------------------------------
    def refresh(self):
        self._refresh_available_fields()
        self.recompute()
