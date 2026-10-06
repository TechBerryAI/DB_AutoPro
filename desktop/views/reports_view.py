"""Enterprise Reports dashboard view."""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from tkinter import filedialog

import customtkinter as ctk

from backend.models.schemas import DatabaseType
from backend.services.report_service import ReportFilters
from desktop.connection_store import load_saved_servers
from desktop.db_catalog import fetch_server_databases
from desktop.date_picker import DatePicker
from desktop.report_export import default_export_stem
from desktop.report_widgets import (
    _font,
    activity_table,
    analytics_row,
    enterprise_kpi_card,
    health_tile,
    metric_chip,
    operation_row,
    report_header_card,
    section_card,
    toolbar_button,
)
from desktop.theme import ThemeManager
from desktop.views.base_view import BaseView
from desktop.views.reports_controller import ReportsController


class ReportsView(BaseView):
    """Premium enterprise reports dashboard with filters and export."""

    def __init__(self, master, theme: ThemeManager, log_callback=None, **kwargs) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self.controller = ReportsController(log_callback=self.log)
        self._view_mode = ctk.StringVar(value="Dashboard View")
        self._kpi_labels: dict[str, ctk.CTkLabel] = {}
        self._kpi_comparisons: dict[str, ctk.CTkLabel] = {}
        self._filter_vars: dict[str, ctk.StringVar] = {}
        self._filter_combos: dict[str, ctk.CTkComboBox] = {}
        self._datetime_label: ctk.CTkLabel | None = None
        self._dashboard_frame: ctk.CTkFrame | None = None
        self._raw_box: ctk.CTkTextbox | None = None
        self._summary_body: ctk.CTkFrame | None = None
        self._ops_body: ctk.CTkFrame | None = None
        self._analytics_body: ctk.CTkFrame | None = None
        self._health_body: ctk.CTkFrame | None = None
        self._activity_body: ctk.CTkFrame | None = None
        self._gen_seq = 0
        self._debounce_after_id: str | None = None
        self._generate_btn: ctk.CTkButton | None = None
        self._status_label: ctk.CTkLabel | None = None
        self._report_busy = False
        self._pending_report = False
        self._updating_filters = False
        self._last_report_at: datetime | None = None
        self.configure(fg_color=theme.c("bg_content"))
        self._build_ui()
        self.after(200, self.generate_report)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        from desktop.ops_widgets import page_header

        title_wrap = ctk.CTkFrame(scroll, fg_color="transparent")
        title_wrap.pack(fill="x", padx=24, pady=(16, 0))
        page_header(
            title_wrap,
            self.theme,
            "Reports",
            subtitle="Operational summaries across backups, restores, and jobs.",
        )

        # Connection status strip (filled when a report is generated)
        self._header_wrap = ctk.CTkFrame(scroll, fg_color="transparent")
        self._header_wrap.pack(fill="x", padx=24, pady=(0, 12))
        self._header_inner = ctk.CTkFrame(self._header_wrap, fg_color="transparent")
        self._header_inner.pack(fill="x")

        # Filters
        filter_card = ctk.CTkFrame(
            scroll,
            corner_radius=self.theme.radius("md"),
            fg_color=self.theme.c("bg_card"),
            border_width=1,
            border_color=self.theme.c("border"),
        )
        filter_card.pack(fill="x", padx=24, pady=(0, 12))
        filter_inner = ctk.CTkFrame(filter_card, fg_color="transparent")
        filter_inner.pack(fill="x", padx=16, pady=14)
        filter_inner.grid_columnconfigure((1, 3, 5, 7, 9), weight=1)

        specs = [
            ("date_range", "Date Range", ["Today", "Monthly", "Yearly", "Custom"], 0),
            ("server", "Server", ["All"], 2),
            ("database", "Database", ["All"], 4),
            ("operation", "Operation Type", ["All", "Backup", "Restore", "Migration", "Patch"], 6),
            ("status", "Status", ["All", "Success", "Failed"], 8),
        ]
        for key, label, values, col in specs:
            ctk.CTkLabel(
                filter_inner, text=label, font=_font(weight="bold"), text_color=self.theme.c("text_muted")
            ).grid(
                row=0, column=col, sticky="w", padx=(0, 6)
            )
            var = ctk.StringVar(value=values[0])
            self._filter_vars[key] = var
            cmd = self._on_date_range_changed if key == "date_range" else None
            cb = ctk.CTkComboBox(
                filter_inner,
                values=values,
                variable=var,
                height=32,
                font=_font(),
                fg_color=self.theme.c("bg_input"),
                border_color=self.theme.c("border"),
                button_color=self.theme.c("bg_elevated"),
                button_hover_color=self.theme.c("bg_card_hover"),
                dropdown_fg_color=self.theme.c("bg_card"),
                dropdown_text_color=self.theme.c("text_primary"),
                text_color=self.theme.c("text_primary"),
                command=cmd,
            )
            cb.grid(row=0, column=col + 1, sticky="ew", padx=(0, 12))
            self._filter_combos[key] = cb

        today = datetime.now().date()
        default_from = (today - timedelta(days=30)).strftime("%Y-%m-%d")
        default_to = today.strftime("%Y-%m-%d")
        self._date_from_var = ctk.StringVar(value=default_from)
        self._date_to_var = ctk.StringVar(value=default_to)

        self._custom_date_row = ctk.CTkFrame(filter_card, fg_color="transparent")
        ctk.CTkLabel(
            self._custom_date_row,
            text="From",
            font=_font(weight="bold"),
            text_color=self.theme.c("text_muted"),
        ).pack(side="left", padx=(0, 6))
        self._date_from_picker = DatePicker(
            self._custom_date_row,
            self._date_from_var,
            theme=self.theme,
            on_change=self._schedule_report,
        )
        self._date_from_picker.pack(side="left", padx=(0, 16))

        ctk.CTkLabel(
            self._custom_date_row,
            text="To",
            font=_font(weight="bold"),
            text_color=self.theme.c("text_muted"),
        ).pack(side="left", padx=(0, 6))
        self._date_to_picker = DatePicker(
            self._custom_date_row,
            self._date_to_var,
            theme=self.theme,
            on_change=self._schedule_report,
        )
        self._date_to_picker.pack(side="left")

        # Toolbar + view toggle
        toolbar = ctk.CTkFrame(scroll, fg_color="transparent")
        toolbar.pack(fill="x", padx=24, pady=(0, 12))

        toggle = ctk.CTkSegmentedButton(
            toolbar,
            values=["Dashboard View", "Raw Report"],
            variable=self._view_mode,
            command=self._on_view_toggle,
            font=_font(weight="bold"),
            height=36,
            fg_color=self.theme.c("bg_elevated"),
            selected_color=self.theme.c("primary"),
            selected_hover_color=self.theme.c("primary_hover"),
            unselected_color=self.theme.c("bg_card"),
            unselected_hover_color=self.theme.c("bg_card_hover"),
            text_color=self.theme.c("text_primary"),
        )
        toggle.pack(side="left")
        self._view_toggle = toggle
        self._view_mode.set("Dashboard View")
        self._style_view_toggle("Dashboard View")

        actions = ctk.CTkFrame(toolbar, fg_color="transparent")
        actions.pack(side="right")

        refresh_btn = toolbar_button(
            actions,
            self.theme,
            "Refresh",
            primary=True,
            command=self.generate_report,
        )
        refresh_btn.pack(side="left", padx=(0, 8))
        self._generate_btn = refresh_btn

        export_menu = ctk.CTkOptionMenu(
            actions,
            values=["Export CSV", "Export PDF"],
            command=self._on_export_action,
            height=36,
            width=140,
            font=_font(weight="bold"),
            fg_color=self.theme.c("bg_card"),
            button_color=self.theme.c("bg_elevated"),
            button_hover_color=self.theme.c("bg_card_hover"),
            dropdown_fg_color=self.theme.c("bg_card"),
            dropdown_text_color=self.theme.c("text_primary"),
            text_color=self.theme.c("text_primary"),
        )
        export_menu.set("Export")
        export_menu.pack(side="left")

        self._status_label = ctk.CTkLabel(
            toolbar,
            text="",
            font=_font(),
            text_color=self.theme.c("text_muted"),
        )
        self._status_label.pack(side="left", padx=(16, 0))

        # Dashboard content
        self._dashboard_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self._dashboard_frame.pack(fill="x", padx=24, pady=(0, 16))
        self._dashboard_frame.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="kpi")

        kpi_specs = [
            ("success", "Successful Operations", "✓", self.theme.c("success")),
            ("failed", "Failed Operations", "!", self.theme.c("error")),
            ("backups", "Total Backups", "⬡", self.theme.c("primary")),
            ("storage", "Storage Used", "▣", self.theme.c("metric_purple")),
        ]
        self._kpi_cards = {}
        for i, (key, title, icon, accent) in enumerate(kpi_specs):
            card, val, cmp_lbl = enterprise_kpi_card(
                self._dashboard_frame,
                self.theme,
                title=title,
                value="—",
                icon=icon,
                accent=accent,
                comparison="—",
                indicator_color=accent,
            )
            card.grid(row=0, column=i, padx=(0 if i == 0 else 8, 0), pady=(0, 12), sticky="nsew")
            self._kpi_labels[key] = val
            self._kpi_comparisons[key] = cmp_lbl
            self._kpi_cards[key] = card

        # Summary
        summary_wrap = ctk.CTkFrame(self._dashboard_frame, fg_color="transparent")
        summary_wrap.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(0, 12))
        self._summary_body = section_card(summary_wrap, self.theme, "Report Summary")
        chips = ctk.CTkFrame(self._summary_body, fg_color="transparent")
        chips.pack(fill="x")
        self._summary_chips: dict[str, ctk.CTkLabel] = {}
        for key, label in [
            ("registered_servers", "Registered Servers"),
            ("connected_databases", "Connected Databases"),
            ("active_jobs", "Active Jobs"),
            ("pending_jobs", "Pending Jobs"),
            ("failed_jobs", "Failed Jobs"),
        ]:
            self._summary_chips[key] = metric_chip(chips, self.theme, label, "0")

        # Two-column row: Operations + Backup Analytics
        mid = ctk.CTkFrame(self._dashboard_frame, fg_color="transparent")
        mid.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(0, 12))
        mid.grid_columnconfigure((0, 1), weight=1)

        ops_wrap = ctk.CTkFrame(mid, fg_color="transparent")
        ops_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._ops_body = section_card(ops_wrap, self.theme, "Operations Overview")

        analytics_wrap = ctk.CTkFrame(mid, fg_color="transparent")
        analytics_wrap.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self._analytics_body = section_card(analytics_wrap, self.theme, "Backup Analytics")
        self._analytics_labels: dict[str, ctk.CTkLabel] = {}
        for label in (
            "Total Backups",
            "Latest Backup",
            "Average Backup Size",
            "Total Storage",
            "Largest Backup",
        ):
            key = label.lower().replace(" ", "_")
            self._analytics_labels[key] = analytics_row(self._analytics_body, self.theme, label, "—")

        # Health + Activity
        bottom = ctk.CTkFrame(self._dashboard_frame, fg_color="transparent")
        bottom.grid(row=3, column=0, columnspan=4, sticky="ew")
        bottom.grid_columnconfigure((0, 1), weight=1)

        health_wrap = ctk.CTkFrame(bottom, fg_color="transparent")
        health_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._health_body = section_card(health_wrap, self.theme, "Database Health")
        self._health_row = ctk.CTkFrame(self._health_body, fg_color="transparent")
        self._health_row.pack(fill="x")

        activity_wrap = ctk.CTkFrame(bottom, fg_color="transparent")
        activity_wrap.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self._activity_body = section_card(activity_wrap, self.theme, "Recent Activity")

        # Raw report (hidden by default)
        self._raw_box = ctk.CTkTextbox(
            scroll,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=self.theme.c("bg_card"),
            border_width=1,
            border_color=self.theme.c("border"),
            text_color=self.theme.c("text_primary"),
            height=480,
        )

    def _style_view_toggle(self, selected: str) -> None:
        """Selected segment uses white text; unselected uses dark text for contrast."""
        toggle = getattr(self, "_view_toggle", None)
        if not toggle:
            return
        for value in ("Dashboard View", "Raw Report"):
            btn = toggle._buttons_dict.get(value)
            if btn:
                btn.configure(
                    text_color=self.theme.c("text_on_primary") if value == selected else self.theme.c("text_primary")
                )

    def _on_view_toggle(self, value: str) -> None:
        self._style_view_toggle(value)
        show_raw = value == "Raw Report"
        if self._dashboard_frame:
            if show_raw:
                self._dashboard_frame.pack_forget()
                self._raw_box.pack(fill="x", padx=24, pady=(0, 16))
                self._raw_box.delete("1.0", "end")
                self._raw_box.insert("1.0", self.controller.raw_text())
            else:
                self._raw_box.pack_forget()
                self._dashboard_frame.pack(fill="x", padx=24, pady=(0, 16))

    def _on_date_range_changed(self, value: str) -> None:
        if value == "Custom":
            self._custom_date_row.pack(fill="x", padx=16, pady=(0, 14))
        else:
            self._custom_date_row.pack_forget()
        # Do not auto-refresh here; user clicks Refresh or picks custom dates.

    def _parse_filter_date(self, raw: str) -> date | None:
        try:
            return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
        except ValueError:
            return None

    def _sync_filters(self) -> bool:
        period_map = {
            "Today": "today",
            "Monthly": "monthly",
            "Yearly": "yearly",
            "Custom": "custom",
        }
        period_label = self._filter_vars["date_range"].get()
        period = period_map.get(period_label, "today")
        date_from: date | None = None
        date_to: date | None = None

        if period == "custom":
            date_from = self._parse_filter_date(self._date_from_var.get())
            date_to = self._parse_filter_date(self._date_to_var.get())
            if not date_from or not date_to:
                self.log("Custom range requires valid From and To dates (YYYY-MM-DD)", "ERROR")
                return False
            if date_from > date_to:
                self.log("Custom range: From date must be on or before To date", "ERROR")
                return False

        self.controller.filters = ReportFilters(
            period=period,
            date_from=date_from,
            date_to=date_to,
            server=self._filter_vars["server"].get(),
            database=self._filter_vars["database"].get(),
            operation_type=self._filter_vars["operation"].get(),
            status=self._filter_vars["status"].get(),
        )
        return True

    def _report_context(self, connection=None) -> dict:
        profile = self.get_active_profile()
        local_dir: str | None = None
        remote_dir = "/mnt/backup"
        list_remote = False
        db_type: DatabaseType | None = None
        connection_name: str | None = None
        database_names: list[str] = []

        servers = load_saved_servers()
        server_names = [s.name for s in servers]

        if connection:
            db_type = connection.db_type
            connection_name = connection.name
            lookup = connection.saved_name or connection.name
            for server in servers:
                if server.name == lookup and server.db_type == db_type.value:
                    if server.local_output_dir:
                        local_dir = server.local_output_dir
                    if server.remote_backup_dir:
                        remote_dir = server.remote_backup_dir
                    break
            if connection.remote_backup_dir:
                remote_dir = connection.remote_backup_dir
            if db_type == DatabaseType.MONGODB and connection.ssh_enabled:
                list_remote = True
            try:
                database_names = [d.get("name", "") for d in fetch_server_databases(connection)]
            except Exception:
                pass

        db_count = len(database_names)

        # Refresh filter dropdowns
        return {
            "profile": profile,
            "db_type": db_type,
            "list_remote": list_remote,
            "remote_dir": remote_dir,
            "local_dir": local_dir,
            "registered_servers": len(servers),
            "database_count": db_count,
            "connection_name": connection_name,
            "server_names": server_names,
            "database_names": database_names,
        }

    def _schedule_report(self) -> None:
        """Debounced refresh after an explicit date pick."""
        if self._updating_filters or self._report_busy:
            return
        if self._debounce_after_id is not None:
            self.after_cancel(self._debounce_after_id)
        self._debounce_after_id = self.after(600, self._run_scheduled_report)

    def _run_scheduled_report(self) -> None:
        self._debounce_after_id = None
        self.generate_report()

    def _set_report_busy(self, busy: bool) -> None:
        if self._status_label:
            self._status_label.configure(text="Generating report…" if busy else "")
        if self._generate_btn:
            self._generate_btn.configure(state="disabled" if busy else "normal")

    def _finish_report_cycle(self) -> None:
        self._report_busy = False
        self._set_report_busy(False)
        if self._pending_report:
            self._pending_report = False
            self.after(100, self.generate_report)

    def _apply_report(self, report: dict, seq: int) -> None:
        if seq != self._gen_seq:
            return
        self._update_filter_options(report)
        self._render_header(report)
        self._render_dashboard(report)
        if self._view_mode.get() == "Raw Report":
            self._raw_box.delete("1.0", "end")
            self._raw_box.insert("1.0", self.controller.raw_text())
        self._last_report_at = datetime.now()
        self.log(f"Report generated ({report['period_label']})", "SUCCESS")
        self._finish_report_cycle()

    def _report_error(self, exc: Exception, seq: int) -> None:
        if seq != self._gen_seq:
            return
        self.log(f"Report generation failed: {exc}", "ERROR")
        if self._status_label:
            self._status_label.configure(text="Report generation failed")
        self._finish_report_cycle()

    def generate_report(self) -> None:
        """Build report on a background thread so the UI stays responsive."""
        if self._report_busy:
            self._pending_report = True
            return
        if not self._sync_filters():
            return
        self._report_busy = True
        self._gen_seq += 1
        seq = self._gen_seq
        self._set_report_busy(True)

        filters = ReportFilters(
            period=self.controller.filters.period,
            date_from=self.controller.filters.date_from,
            date_to=self.controller.filters.date_to,
            server=self.controller.filters.server,
            database=self.controller.filters.database,
            operation_type=self.controller.filters.operation_type,
            status=self.controller.filters.status,
        )
        connection = self.connection

        def task() -> None:
            try:
                ctx = self._report_context(connection)
                report = self.controller.service.build_report(filters, **ctx)
                self.controller.last_report = report
                self.after(0, lambda: self._apply_report(report, seq))
            except Exception as exc:
                self.after(0, lambda: self._report_error(exc, seq))

        threading.Thread(target=task, daemon=True).start()

    def _update_filter_options(self, report: dict) -> None:
        opts = report.get("filter_options", {})
        mapping = {
            "server": opts.get("servers", ["All"]),
            "database": opts.get("databases", ["All"]),
        }
        self._updating_filters = True
        try:
            for key, values in mapping.items():
                cb = self._filter_combos.get(key)
                if cb:
                    cb.configure(values=values)
                var = self._filter_vars.get(key)
                if var and var.get() not in values:
                    var.set(values[0])
        finally:
            self._updating_filters = False

    def _render_header(self, report: dict) -> None:
        for child in self._header_inner.winfo_children():
            child.destroy()
        connected = report.get("connection_status") == "Connected"
        report_header_card(
            self._header_inner,
            self.theme,
            connection_status=report.get("connection_status", "Disconnected"),
            connected=connected,
        ).pack(fill="x")

    def _render_dashboard(self, report: dict) -> None:
        kpi = report["kpi"]
        comparisons = {
            "success": kpi["success_comparison"],
            "failed": kpi["failed_comparison"],
            "backups": kpi["backups_comparison"],
            "storage": kpi["storage_comparison"],
        }
        values = {
            "success": str(kpi["success_operations"]),
            "failed": str(kpi["failed_operations"]),
            "backups": str(kpi["total_backups"]),
            "storage": f"{kpi['storage_used_mb']:.2f} MB",
        }
        for key, lbl in self._kpi_labels.items():
            lbl.configure(text=values[key])
            self._kpi_comparisons[key].configure(text=comparisons[key])

        for key, lbl in self._summary_chips.items():
            lbl.configure(text=str(report["summary"].get(key, 0)))

        for child in self._ops_body.winfo_children():
            child.destroy()
        for op in report["operations_overview"]:
            operation_row(self._ops_body, self.theme, op)

        analytics = report["backup_analytics"]
        analytics_values = {
            "total_backups": str(analytics["total"]),
            "latest_backup": analytics["latest"],
            "average_backup_size": f"{analytics['avg_size_mb']:.2f} MB",
            "total_storage": f"{analytics['total_storage_mb']:.2f} MB",
            "largest_backup": f"{analytics['largest_mb']:.2f} MB ({analytics['largest_name']})",
        }
        for key, lbl in self._analytics_labels.items():
            lbl.configure(text=analytics_values.get(key, "—"))

        for child in self._health_row.winfo_children():
            child.destroy()
        h = report["health"]
        health_tile(self._health_row, self.theme, "Healthy", h["healthy"], self.theme.c("success"), "✔")
        health_tile(self._health_row, self.theme, "Warning", h["warning"], self.theme.c("warning"), "⚠")
        health_tile(self._health_row, self.theme, "Critical", h["critical"], self.theme.c("error"), "✖")

        for child in self._activity_body.winfo_children():
            child.destroy()
        activity_table(self._activity_body, self.theme, report["recent_activity"])

    def _on_export_action(self, choice: str) -> None:
        if choice == "Export CSV":
            self._export_csv()
        elif choice == "Export PDF":
            self._export_pdf()

    def refresh(self, period=None) -> None:
        """Refresh when navigating back, but skip if a report just finished."""
        if self._report_busy:
            return
        if self._last_report_at and (datetime.now() - self._last_report_at).total_seconds() < 30:
            return
        self.generate_report()

    def _export_csv(self) -> None:
        try:
            if not self.controller.last_report:
                self.generate_report()
            stem = default_export_stem(self.controller._export_payload())
            path = filedialog.asksaveasfilename(
                title="Export Report (CSV)",
                defaultextension=".csv",
                initialfile=f"{stem}.csv",
                filetypes=[("CSV files", "*.csv")],
            )
            if path:
                self.controller.export_csv(path)
        except Exception as exc:
            self.log(f"CSV export failed: {exc}", "ERROR")

    def _export_pdf(self) -> None:
        try:
            if not self.controller.last_report:
                self.generate_report()
            stem = default_export_stem(self.controller._export_payload())
            path = filedialog.asksaveasfilename(
                title="Export Report (PDF)",
                defaultextension=".pdf",
                initialfile=f"{stem}.pdf",
                filetypes=[("PDF files", "*.pdf")],
            )
            if path:
                self.controller.export_pdf(path)
        except Exception as exc:
            self.log(f"PDF export failed: {exc}", "ERROR")
