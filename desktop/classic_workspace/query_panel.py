"""Bottom SQL / Mongo command editor with Results and Messages tabs."""

from __future__ import annotations

import csv
import re
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable

import customtkinter as ctk

from backend.models.schemas import DatabaseType
from desktop.classic_workspace.chrome import cx
from desktop.classic_workspace.query_runner import QueryOutcome, is_probably_write_sql, run_query
from desktop.connection import ConnectionResult
from desktop.theme import ThemeManager

_HISTORY: list[dict] = []


class QueryWorkspace(ctk.CTkFrame):
    """Classic query editor + results/messages/history/statistics."""

    def __init__(
        self,
        master,
        theme: ThemeManager,
        *,
        get_active_connection: Callable[[], ConnectionResult | None] | None = None,
        on_status: Callable[[str], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=cx(theme, "bg_card"), corner_radius=0, **kwargs)
        self.theme = theme
        self.get_active_connection = get_active_connection or (lambda: None)
        self.on_status = on_status
        self._busy = False
        self._stop_flag = False
        self._last_rows: list[dict] = []
        self._last_columns: list[str] = []
        self._output_tab = "Results"
        self._pending_outcome: tuple[str, QueryOutcome] | None = None
        self._build()
        self.after(100, self._drain_pending)

    def _build(self) -> None:
        toolbar = ctk.CTkFrame(self, fg_color=cx(self.theme, "bg_elevated"), corner_radius=0, height=30)
        toolbar.pack(fill="x")
        toolbar.pack_propagate(False)
        self._lang = ctk.CTkLabel(
            toolbar,
            text="SQL Editor",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color=cx(self.theme, "text_primary"),
        )
        self._lang.pack(side="left", padx=8)
        self._conn_meta = ctk.CTkLabel(
            toolbar,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=cx(self.theme, "text_muted"),
        )
        self._conn_meta.pack(side="left", padx=(4, 8))
        self._exec_btn = self._btn(toolbar, "Execute", self.execute, primary=True)
        self._exec_btn.pack(side="left", padx=2)
        self._stop_btn = self._btn(toolbar, "Stop", self.stop)
        self._stop_btn.pack(side="left", padx=2)
        self._btn(toolbar, "Clear", self.clear_editor).pack(side="left", padx=2)
        self._elapsed = ctk.CTkLabel(
            toolbar,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=cx(self.theme, "text_muted"),
        )
        self._elapsed.pack(side="right", padx=8)

        editor_host = tk.Frame(self, bg=cx(self.theme, "bg_input"), height=130)
        editor_host.pack(fill="both", expand=True)
        self._gutter = tk.Text(
            editor_host,
            width=4,
            height=6,
            wrap="none",
            bg=cx(self.theme, "gutter"),
            fg=cx(self.theme, "gutter_text"),
            font=("Consolas", 11),
            relief="flat",
            borderwidth=0,
            padx=4,
            pady=6,
            state="disabled",
            takefocus=0,
            cursor="arrow",
        )
        self._gutter.pack(side="left", fill="y")
        self.editor = tk.Text(
            editor_host,
            height=6,
            wrap="none",
            bg=cx(self.theme, "bg_input"),
            fg=cx(self.theme, "text_primary"),
            insertbackground=cx(self.theme, "accent"),
            font=("Consolas", 11),
            relief="flat",
            borderwidth=0,
            padx=8,
            pady=6,
            undo=True,
            tabs="4c",
        )
        self._esb = ttk.Scrollbar(editor_host, orient="vertical", command=self._scroll_editor)
        self.editor.configure(yscrollcommand=self._editor_yscroll)
        self.editor.pack(side="left", fill="both", expand=True)
        self._esb.pack(side="right", fill="y")
        self.editor.bind("<KeyRelease>", self._on_editor_key)
        self.editor.bind("<ButtonRelease-1>", self._on_editor_key)
        self.editor.bind("<F5>", lambda _e: self.execute())
        self.editor.tag_configure("kw", foreground=cx(self.theme, "accent"))
        self.editor.tag_configure("str", foreground=cx(self.theme, "metric_amber"))
        self.editor.tag_configure("cmt", foreground=cx(self.theme, "text_muted"))
        self.editor.tag_configure("cur", background=cx(self.theme, "bg_current_line"))
        self._sync_gutter()

        out_tabs = ctk.CTkFrame(self, fg_color=cx(self.theme, "bg_elevated"), corner_radius=0, height=28)
        out_tabs.pack(fill="x")
        out_tabs.pack_propagate(False)
        self._out_btns: dict[str, ctk.CTkButton] = {}
        for name in ("Results", "Messages", "Execution History", "Query Statistics"):
            btn = ctk.CTkButton(
                out_tabs,
                text=name,
                width=118,
                height=26,
                corner_radius=0,
                fg_color=cx(self.theme, "bg_card") if name == "Results" else cx(self.theme, "bg_elevated"),
                hover_color=cx(self.theme, "bg_card"),
                text_color=cx(self.theme, "text_primary"),
                font=ctk.CTkFont(family="Segoe UI", size=10),
                command=lambda n=name: self._show_output(n),
            )
            btn.pack(side="left", padx=(0, 1))
            self._out_btns[name] = btn
        self._result_count = ctk.CTkLabel(
            out_tabs,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=cx(self.theme, "text_muted"),
        )
        self._result_count.pack(side="right", padx=8)
        self._btn(out_tabs, "Export CSV", self.export_csv, width=90).pack(side="right", padx=6, pady=2)

        self._out_host = ctk.CTkFrame(self, fg_color=cx(self.theme, "bg_input"), corner_radius=0)
        self._out_host.pack(fill="both", expand=True)

        self._results_frame = tk.Frame(self._out_host, bg=cx(self.theme, "bg_input"))
        style = ttk.Style()
        style.configure(
            "Results.Treeview",
            background=cx(self.theme, "bg_input"),
            foreground=cx(self.theme, "text_primary"),
            fieldbackground=cx(self.theme, "bg_input"),
            rowheight=20,
            font=("Segoe UI", 9),
            borderwidth=0,
        )
        style.configure(
            "Results.Treeview.Heading",
            background=cx(self.theme, "bg_elevated"),
            foreground=cx(self.theme, "text_secondary"),
            font=("Segoe UI", 9, "bold"),
            relief="flat",
        )
        style.map(
            "Results.Treeview",
            background=[("selected", cx(self.theme, "bg_selection"))],
            foreground=[("selected", cx(self.theme, "text_primary"))],
        )
        self.results = ttk.Treeview(self._results_frame, show="headings", style="Results.Treeview")
        self.results.tag_configure("odd", background=cx(self.theme, "bg_zebra"))
        self.results.tag_configure("even", background=cx(self.theme, "bg_input"))
        rvsb = ttk.Scrollbar(self._results_frame, orient="vertical", command=self.results.yview)
        rhsb = ttk.Scrollbar(self._results_frame, orient="horizontal", command=self.results.xview)
        self.results.configure(yscrollcommand=rvsb.set, xscrollcommand=rhsb.set)
        self.results.grid(row=0, column=0, sticky="nsew")
        rvsb.grid(row=0, column=1, sticky="ns")
        rhsb.grid(row=1, column=0, sticky="ew")
        self._results_frame.grid_rowconfigure(0, weight=1)
        self._results_frame.grid_columnconfigure(0, weight=1)
        self.results.bind("<Control-c>", self._copy_selection)

        self.messages = tk.Text(
            self._out_host,
            wrap="word",
            bg=cx(self.theme, "bg_input"),
            fg=cx(self.theme, "text_primary"),
            font=("Consolas", 10),
            relief="flat",
            state="disabled",
        )
        self.history = tk.Text(
            self._out_host,
            wrap="word",
            bg=cx(self.theme, "bg_input"),
            fg=cx(self.theme, "text_secondary"),
            font=("Consolas", 10),
            relief="flat",
            state="disabled",
        )
        self.stats = tk.Text(
            self._out_host,
            wrap="word",
            bg=cx(self.theme, "bg_input"),
            fg=cx(self.theme, "text_primary"),
            font=("Consolas", 10),
            relief="flat",
            state="disabled",
        )
        self._show_output("Results")
        self.sync_language()

    def _btn(self, parent, text, command, *, primary: bool = False, width: int = 72) -> ctk.CTkButton:
        if primary:
            return ctk.CTkButton(
                parent,
                text=text,
                width=width,
                height=22,
                corner_radius=2,
                fg_color=cx(self.theme, "primary"),
                hover_color=cx(self.theme, "primary_hover"),
                text_color=cx(self.theme, "text_on_primary"),
                font=ctk.CTkFont(family="Segoe UI", size=10),
                command=command,
            )
        return ctk.CTkButton(
            parent,
            text=text,
            width=width,
            height=22,
            corner_radius=2,
            fg_color=cx(self.theme, "bg_card"),
            hover_color=cx(self.theme, "bg_card_hover"),
            border_width=1,
            border_color=cx(self.theme, "border"),
            text_color=cx(self.theme, "text_primary"),
            font=ctk.CTkFont(family="Segoe UI", size=10),
            command=command,
        )

    def sync_language(self) -> None:
        conn = self.get_active_connection()
        if conn is None:
            self._lang.configure(text="SQL Editor")
            self._conn_meta.configure(text="● Disconnected")
            return
        name = conn.saved_name or conn.name or "session"
        db = conn.database or "—"
        if conn.db_type == DatabaseType.MONGODB:
            self._lang.configure(text="MongoDB Shell")
            if not self.editor.get("1.0", "end").strip():
                self.editor.insert("1.0", "show collections\n")
                self._sync_gutter()
        else:
            label = {
                DatabaseType.POSTGRESQL: "PostgreSQL SQL",
                DatabaseType.MYSQL: "MySQL SQL",
                DatabaseType.ORACLE: "Oracle SQL",
                DatabaseType.MSSQL: "T-SQL",
            }.get(conn.db_type, "SQL Editor")
            self._lang.configure(text=label)
        self._conn_meta.configure(text=f"● Connected  ·  {name}  ·  {db}")

    def _show_output(self, name: str) -> None:
        self._output_tab = name
        for key, btn in self._out_btns.items():
            active = key == name
            btn.configure(
                fg_color=cx(self.theme, "bg_card") if active else cx(self.theme, "bg_elevated"),
                text_color=cx(self.theme, "text_primary") if active else cx(self.theme, "text_secondary"),
            )
        for widget in (self._results_frame, self.messages, self.history, self.stats):
            widget.pack_forget()
        if name == "Results":
            self._results_frame.pack(fill="both", expand=True)
        elif name == "Messages":
            self.messages.pack(fill="both", expand=True)
        elif name == "Execution History":
            self.history.pack(fill="both", expand=True)
            self._render_history()
        else:
            self.stats.pack(fill="both", expand=True)

    def clear_editor(self) -> None:
        self.editor.delete("1.0", "end")
        self._sync_gutter()

    def focus_editor(self) -> None:
        self.editor.focus_set()

    def stop(self) -> None:
        self._stop_flag = True
        self._append_message("Stop requested (in-flight driver call may still finish).")

    def execute(self) -> None:
        if self._busy:
            return
        conn = self.get_active_connection()
        if conn is None:
            self._append_message("Connect to a database before executing.")
            self._show_output("Messages")
            return
        try:
            text = self.editor.get("sel.first", "sel.last")
        except tk.TclError:
            text = self.editor.get("1.0", "end")
        text = text.strip()
        if not text:
            self._append_message("Nothing selected to execute.")
            return
        if conn.db_type != DatabaseType.MONGODB and is_probably_write_sql(text):
            self._append_message("Executing data-changing SQL explicitly requested by user.")

        self._busy = True
        self._stop_flag = False
        self._elapsed.configure(text="Running…")
        if self.on_status:
            self.on_status("Executing…")

        def worker() -> None:
            outcome = run_query(conn, text)
            self._pending_outcome = (text, outcome)

        threading.Thread(target=worker, daemon=True).start()

    def _drain_pending(self) -> None:
        if not self.winfo_exists():
            return
        pending = self._pending_outcome
        if pending is not None:
            self._pending_outcome = None
            self._finish(*pending)
        self.after(100, self._drain_pending)

    def _finish(self, text: str, outcome: QueryOutcome) -> None:
        self._busy = False
        self._elapsed.configure(text=f"{outcome.elapsed_ms:.1f} ms")
        _HISTORY.append(
            {
                "time": datetime.now().strftime("%H:%M:%S"),
                "ok": outcome.ok,
                "ms": outcome.elapsed_ms,
                "text": text[:200],
                "message": outcome.message,
                "rows": len(outcome.rows),
            }
        )
        if outcome.ok:
            self._append_message(f"OK ({outcome.elapsed_ms:.1f} ms): {outcome.message}")
            self._set_results(outcome.columns, outcome.rows, outcome.elapsed_ms)
            self._set_stats(outcome)
            self._show_output("Results" if outcome.rows or outcome.columns else "Messages")
            if self.on_status:
                self.on_status(f"Query OK · {len(outcome.rows)} rows · {outcome.elapsed_ms:.1f} ms")
        else:
            self._append_message(f"ERROR ({outcome.elapsed_ms:.1f} ms): {outcome.message}")
            self._result_count.configure(text=f"Error · {outcome.elapsed_ms:.1f} ms")
            self._show_output("Messages")
            if self.on_status:
                self.on_status("Query failed")

    def _set_results(self, columns: list[str], rows: list[dict], elapsed_ms: float = 0.0) -> None:
        self._last_columns = columns
        self._last_rows = rows
        display = ["#", *columns]
        self.results.delete(*self.results.get_children())
        self.results["columns"] = display
        self.results.heading("#", text="#")
        self.results.column("#", width=44, stretch=False, anchor="e")
        for col in columns:
            self.results.heading(col, text=col, command=lambda c=col: self._sort_by(c))
            self.results.column(col, width=120, stretch=True)
        for index, row in enumerate(rows[:1000], start=1):
            values = [str(index)] + ["" if row.get(c) is None else str(row.get(c)) for c in columns]
            tag = "odd" if index % 2 else "even"
            self.results.insert("", "end", values=values, tags=(tag,))
        self._result_count.configure(text=f"{len(rows)} row(s) · {elapsed_ms:.1f} ms")

    def _sort_by(self, column: str) -> None:
        if not self._last_rows:
            return
        try:
            ordered = sorted(self._last_rows, key=lambda r: str(r.get(column) or ""))
        except Exception:
            return
        self._set_results(self._last_columns, ordered)

    def _set_stats(self, outcome: QueryOutcome) -> None:
        self.stats.configure(state="normal")
        self.stats.delete("1.0", "end")
        self.stats.insert(
            "end",
            (
                f"Language: {outcome.language}\n"
                f"Elapsed: {outcome.elapsed_ms:.1f} ms\n"
                f"Rows returned: {len(outcome.rows)}\n"
                f"Columns: {', '.join(outcome.columns) if outcome.columns else '—'}\n"
                f"Message: {outcome.message}\n"
            ),
        )
        self.stats.configure(state="disabled")

    def _append_message(self, text: str) -> None:
        self.messages.configure(state="normal")
        self.messages.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {text}\n")
        self.messages.see("end")
        self.messages.configure(state="disabled")

    def _render_history(self) -> None:
        self.history.configure(state="normal")
        self.history.delete("1.0", "end")
        for item in reversed(_HISTORY[-100:]):
            flag = "OK" if item["ok"] else "ERR"
            self.history.insert(
                "end",
                f"{item['time']}  {flag}  {item['ms']:.1f}ms  rows={item['rows']}  {item['text']}\n",
            )
        self.history.configure(state="disabled")

    def export_csv(self) -> None:
        if not self._last_columns:
            self._append_message("No result set to export.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile="query_results.csv",
        )
        if not path:
            return
        with Path(path).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self._last_columns, extrasaction="ignore")
            writer.writeheader()
            for row in self._last_rows:
                writer.writerow(row)
        self._append_message(f"Exported {len(self._last_rows)} row(s) to {path}")

    def _copy_selection(self, _event=None):
        sel = self.results.selection()
        if not sel:
            return "break"
        lines = []
        for item in sel:
            values = list(self.results.item(item, "values"))
            if values:
                values = values[1:]  # skip row number
            lines.append("\t".join(str(v) for v in values))
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        return "break"

    def _on_editor_key(self, _event=None) -> None:
        self._sync_gutter()
        self._highlight()

    def _scroll_editor(self, *args) -> None:
        self.editor.yview(*args)
        self._gutter.yview_moveto(self.editor.yview()[0])

    def _editor_yscroll(self, first, last) -> None:
        self._gutter.yview_moveto(first)
        self._esb.set(first, last)

    def _sync_gutter(self) -> None:
        lines = int(self.editor.index("end-1c").split(".")[0])
        text = "\n".join(f"{i:>3}" for i in range(1, lines + 1))
        self._gutter.configure(state="normal")
        self._gutter.delete("1.0", "end")
        self._gutter.insert("1.0", text)
        self._gutter.configure(state="disabled")
        self._gutter.yview_moveto(self.editor.yview()[0])

    def _highlight(self, _event=None) -> None:
        content = self.editor.get("1.0", "end")
        for tag in ("kw", "str", "cmt", "cur"):
            self.editor.tag_remove(tag, "1.0", "end")
        try:
            line = self.editor.index("insert").split(".")[0]
            self.editor.tag_add("cur", f"{line}.0", f"{line}.0 lineend+1c")
        except Exception:
            pass
        keywords = (
            "select", "from", "where", "insert", "update", "delete", "create", "drop",
            "alter", "table", "view", "index", "join", "left", "right", "inner", "outer",
            "group", "order", "by", "limit", "offset", "and", "or", "not", "null", "as",
            "into", "values", "set", "on", "with", "union", "case", "when", "then", "else",
            "end", "show", "db", "find", "aggregate", "count", "use",
        )
        for match in re.finditer(r"\b(" + "|".join(keywords) + r")\b", content, re.I):
            self.editor.tag_add("kw", f"1.0+{match.start()}c", f"1.0+{match.end()}c")
        for match in re.finditer(r"('([^']|'')*'|\"([^\"\\]|\\.)*\")", content):
            self.editor.tag_add("str", f"1.0+{match.start()}c", f"1.0+{match.end()}c")
        for match in re.finditer(r"(--.*?$|//.*?$)", content, re.M):
            self.editor.tag_add("cmt", f"1.0+{match.start()}c", f"1.0+{match.end()}c")
