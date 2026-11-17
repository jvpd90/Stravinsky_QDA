"""Analysis tab: JASP/jamovi-style layout -- a field-selection panel on
the left (move fields from "available" into role-specific boxes), an
embedded matplotlib chart and a results table on the right. Recomputes
automatically whenever the analysis type or field selection changes.
"""
from __future__ import annotations

import math
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

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


class AnalysisTab:
    def __init__(self, parent, app):
        """
        app must provide:
          - app.store: ProjectStore
        """
        self.app = app
        self.row_combo = None
        self.col_combo = None
        self.selected_list = None

        self.frame = tk.Frame(parent)
        self.frame.columnconfigure(0, weight=0)
        self.frame.columnconfigure(1, weight=1)
        self.frame.rowconfigure(0, weight=1)

        self._build_options_panel()
        self._build_results_panel()
        self._build_role_widgets()

    def grid(self, **kwargs):
        self.frame.grid(**kwargs)

    # -- options panel (left) --------------------------------------------------
    def _build_options_panel(self):
        self.options_frame = tk.Frame(self.frame, width=260)
        self.options_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.options_frame.grid_propagate(False)

        tk.Label(self.options_frame, text="Análise", font=("Arial", 11, "bold")).pack(fill="x", pady=(0, 2))
        self.analysis_var = tk.StringVar(value=ANALYSES[0])
        for name in ANALYSES:
            tk.Radiobutton(
                self.options_frame, text=name, variable=self.analysis_var, value=name,
                command=self._on_analysis_type_changed, anchor="w", justify="left", wraplength=240,
            ).pack(fill="x")

        self._build_filter_section()

        tk.Label(self.options_frame, text="Campos disponíveis", font=("Arial", 10, "bold")).pack(
            fill="x", pady=(10, 2)
        )
        avail_frame = tk.Frame(self.options_frame)
        avail_frame.pack(fill="both", expand=False)
        self.available_list = tk.Listbox(avail_frame, selectmode="extended", height=5, exportselection=False)
        widgets.add_scrollbars(avail_frame, self.available_list, horizontal=True)

        # Packed at the bottom before the role frame, so a short window
        # squeezes the field lists rather than hiding this message.
        self.status_label = tk.Label(
            self.options_frame, text="", fg="#a33333", wraplength=240, justify="left", anchor="w"
        )
        self.status_label.pack(side="bottom", fill="x", pady=(8, 0))

        self.role_frame = tk.Frame(self.options_frame)
        self.role_frame.pack(fill="both", expand=True, pady=(8, 0))

    # -- filters: which fragments enter the analysis ------------------------------
    def _build_filter_section(self):
        """Field = value filters applied before any analysis: different
        fields must all match (AND), several values of one field are
        alternatives (OR)."""
        self.filters: dict[str, list[str]] = {}

        tk.Label(self.options_frame, text="Filtros", font=("Arial", 10, "bold")).pack(fill="x", pady=(10, 2))
        pick = tk.Frame(self.options_frame)
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

        self.filter_list = tk.Listbox(self.options_frame, height=3, exportselection=False)
        self.filter_list.pack(fill="x", pady=(4, 0))
        bottom = tk.Frame(self.options_frame)
        bottom.pack(fill="x")
        tk.Button(bottom, text="Remover filtro", command=self._remove_filter).pack(side="left")
        self.filter_count_label = tk.Label(bottom, text="", fg="#555555", font=("Arial", 9))
        self.filter_count_label.pack(side="right")

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

    def _build_role_widgets(self):
        for widget in self.role_frame.winfo_children():
            widget.destroy()
        self.row_combo = None
        self.col_combo = None
        self.selected_list = None

        if self.analysis_var.get() == CA:
            tk.Label(self.role_frame, text="Variável de linha").pack(fill="x")
            self.row_field_var = tk.StringVar()
            self.row_combo = ttk.Combobox(self.role_frame, textvariable=self.row_field_var, state="readonly")
            self.row_combo.pack(fill="x", pady=(0, 6))
            self.row_combo.bind("<<ComboboxSelected>>", lambda e: self.recompute())

            tk.Label(self.role_frame, text="Variável de coluna").pack(fill="x")
            self.col_field_var = tk.StringVar()
            self.col_combo = ttk.Combobox(self.role_frame, textvariable=self.col_field_var, state="readonly")
            self.col_combo.pack(fill="x")
            self.col_combo.bind("<<ComboboxSelected>>", lambda e: self.recompute())
        else:
            move_frame = tk.Frame(self.role_frame)
            move_frame.pack(fill="x")
            tk.Button(move_frame, text="Adicionar →", command=self._add_selected_fields).pack(
                side="left", padx=(0, 4)
            )
            tk.Button(move_frame, text="← Remover", command=self._remove_selected_fields).pack(side="left")

            tk.Label(self.role_frame, text="Variáveis selecionadas", font=("Arial", 10, "bold")).pack(
                fill="x", pady=(8, 2)
            )
            sel_frame = tk.Frame(self.role_frame)
            sel_frame.pack(fill="both", expand=True)
            self.selected_list = tk.Listbox(sel_frame, selectmode="extended", height=6, exportselection=False)
            widgets.add_scrollbars(sel_frame, self.selected_list, horizontal=True)

        self._refresh_available_fields()

    def _on_analysis_type_changed(self):
        self._build_role_widgets()
        self.recompute()

    def _add_selected_fields(self):
        chosen = [self.available_list.get(i) for i in self.available_list.curselection()]
        existing = set(self.selected_list.get(0, tk.END))
        for field in chosen:
            if field not in existing:
                self.selected_list.insert(tk.END, field)
        self.recompute()

    def _remove_selected_fields(self):
        for index in reversed(self.selected_list.curselection()):
            self.selected_list.delete(index)
        self.recompute()

    def _refresh_available_fields(self):
        fields = analysis.available_fields(self.app.store) if self.app.store.path else []

        self.available_list.delete(0, tk.END)
        for field in fields:
            self.available_list.insert(tk.END, field)
        self._refresh_filters(fields)

        if self.row_combo is not None:
            self.row_combo["values"] = fields
            self.col_combo["values"] = fields
            if self.row_field_var.get() not in fields:
                self.row_field_var.set("")
            if self.col_field_var.get() not in fields:
                self.col_field_var.set("")

        if self.selected_list is not None:
            kept = [f for f in self.selected_list.get(0, tk.END) if f in fields]
            self.selected_list.delete(0, tk.END)
            for field in kept:
                self.selected_list.insert(tk.END, field)

    # -- results panel (right) --------------------------------------------------
    def _build_results_panel(self):
        self.results_frame = tk.Frame(self.frame)
        self.results_frame.grid(row=0, column=1, sticky="nsew")
        self.results_frame.rowconfigure(0, weight=3)
        self.results_frame.rowconfigure(1, weight=0)
        self.results_frame.rowconfigure(2, weight=1)
        self.results_frame.columnconfigure(0, weight=1)

        self.figure = Figure(figsize=(5, 4), dpi=100)
        self.ax = self.figure.add_subplot(111)
        self.ax.axis("off")
        self.canvas = FigureCanvasTkAgg(self.figure, master=self.results_frame)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        toolbar_frame = tk.Frame(self.results_frame)
        toolbar_frame.grid(row=1, column=0, sticky="ew")
        NavigationToolbar2Tk(self.canvas, toolbar_frame).update()

        # Result tables: pick one to read, copy it, or export them all.
        self.tables: dict = {}
        self.tables_context: dict = {}
        table_bar = tk.Frame(self.results_frame)
        table_bar.grid(row=2, column=0, sticky="ew", pady=(6, 2))
        tk.Label(table_bar, text="Tabela:", font=("Arial", 10, "bold")).pack(side="left")
        self.table_var = tk.StringVar()
        self.table_combo = ttk.Combobox(table_bar, textvariable=self.table_var, state="readonly", width=34)
        self.table_combo.pack(side="left", padx=(4, 8))
        self.table_combo.bind("<<ComboboxSelected>>", lambda e: self._show_current_table())
        tk.Button(table_bar, text="Exportar tabelas (Excel)…", command=self._export_tables).pack(side="right")
        tk.Button(table_bar, text="Copiar tabela", command=self._copy_table).pack(side="right", padx=4)

        table_frame = tk.Frame(self.results_frame)
        table_frame.grid(row=3, column=0, sticky="nsew")
        self.results_frame.rowconfigure(3, weight=2)
        self.results_frame.rowconfigure(2, weight=0)
        self.results_table = ttk.Treeview(table_frame, show="headings", height=8)
        widgets.add_scrollbars(table_frame, self.results_table, horizontal=True)

    def _show_tables(self, tables: dict, default: str, variables: list[str]):
        """Make `tables` ({title: DataFrame}) the current results: keep the
        table being read if the new results have one of that title."""
        self.tables = tables
        self.tables_context = {
            "Análise": self.analysis_var.get(),
            "Variáveis": ", ".join(variables),
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
            self.results_table.column(col, width=110, anchor="center")
        for row in df.itertuples(index=False):
            self.results_table.insert("", "end", values=[format_cell(v) for v in row])
        widgets.autosize_treeview_columns(self.results_table)

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
        self.status_label.config(text=f"Tabela '{self.table_var.get()}' copiada.", fg="#2e7d32")

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
        self.ax.clear()
        self.ax.axis("off")
        self.canvas.draw()
        self.status_label.config(text=message)
        self.results_table.delete(*self.results_table.get_children())
        self.results_table["columns"] = ()
        self.tables = {}
        self.table_combo["values"] = ()
        self.table_var.set("")

    # -- recompute ---------------------------------------------------------------
    def recompute(self):
        self.status_label.config(text="", fg="#a33333")
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

        df = analysis.build_field_frame(self.app.store, [row_field, col_field], self._fragments)
        if df.empty:
            self._clear_results("Sem dados suficientes para essas variáveis.")
            return

        table = analysis.contingency_table(df, row_field, col_field)
        result = analysis.run_ca(table)

        self.ax.clear()
        analysis.plot_ca(self.ax, result, row_field, col_field)
        self.canvas.draw()
        self._show_tables(
            {"Frequências": analysis.frequency_table(df, [row_field, col_field]), **analysis.ca_tables(table)},
            default="Teste qui-quadrado", variables=[row_field, col_field],
        )

    def _run_mca(self):
        fields = list(self.selected_list.get(0, tk.END))
        if len(fields) < 2:
            self._clear_results("Selecione pelo menos 2 variáveis.")
            return

        df = analysis.build_field_frame(self.app.store, fields, self._fragments)
        if df.empty:
            self._clear_results("Sem dados suficientes para essas variáveis.")
            return

        result = analysis.run_mca(df, fields)

        self.ax.clear()
        analysis.plot_mca(self.ax, result)
        self.canvas.draw()
        self._show_tables(
            {"Frequências": analysis.frequency_table(df, fields), **analysis.mca_tables(df, fields)},
            default="Valores próprios", variables=fields,
        )

    def _run_network(self):
        fields = list(self.selected_list.get(0, tk.END))
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

        self.ax.clear()
        analysis.plot_network(self.ax, graph)
        self.canvas.draw()
        self._show_tables(
            {"Frequências": analysis.frequency_table(df, fields), **analysis.network_tables(graph, df, fields)},
            default="Nós (centralidade)", variables=fields,
        )

    # -- external refresh hook ----------------------------------------------------
    def refresh(self):
        self._refresh_available_fields()
        self.recompute()
