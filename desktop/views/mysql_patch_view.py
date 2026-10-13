"""MySQL package patch page. Apply runs a dry-run and does not install packages."""

from __future__ import annotations

import threading
from tkinter import filedialog

import customtkinter as ctk

from backend.services.mysql_patch.audit import MysqlPatchAuditStore
from backend.services.mysql_patch.command_runner import SshCommandRunner
from backend.services.mysql_patch.executor import PatchExecutor
from backend.services.mysql_patch.models import (
    MysqlPatchAuditRecord,
    PackagePreviewReport,
    PatchPlan,
    PrecheckReport,
)
from backend.services.mysql_patch.plan_service import PlanService
from backend.services.mysql_patch.precheck import PrecheckService
from backend.services.mysql_patch.preview import PackagePreviewService
from backend.services.mysql_patch.verification import VerificationService
from backend.services.mysql_patch.redaction import redact_text
from desktop.connection_store import SavedServer, load_saved_servers
from desktop.design_system import primary_button, secondary_button
from desktop.mysql_patch_export import export_patch_csv, export_patch_pdf
from desktop.mysql_patch_flow import (
    apply_allowed,
    approve_allowed,
    plan_allowed,
    precheck_allowed,
    refresh_allowed,
)
from desktop.ops_widgets import page_header
from desktop.theme import ThemeManager, themed_scrollable_frame
from desktop.views.base_view import BaseView


class MysqlPatchView(BaseView):
    """Sidebar page for Ubuntu MySQL package patching."""

    def __init__(self, master, theme: ThemeManager, log_callback=None, **kwargs) -> None:
        super().__init__(master, theme, log_callback, **kwargs)
        self._audit = MysqlPatchAuditStore()
        self._prechecks = PrecheckService(jobs=self._audit)
        self._previews = PackagePreviewService(precheck=self._prechecks)
        self._plans = PlanService(preview=self._previews)
        self._executor = PatchExecutor(plans=self._plans)
        self._verification = VerificationService()
        self._audit = MysqlPatchAuditStore()
        self._servers: list[SavedServer] = []
        self._busy = False
        self._precheck: PrecheckReport | None = None
        self._preview: PackagePreviewReport | None = None
        self._plan: PatchPlan | None = None
        self._history: list[MysqlPatchAuditRecord] = []
        self._selected_job = ctk.StringVar(value="")
        self._build()
        self._load_servers()

    def refresh(self, *, force: bool = False) -> None:
        del force
        if self._busy:
            return
        self._load_servers()
        self._load_history()
        self.log("MySQL Patch Management refreshed")

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        scroll = themed_scrollable_frame(self, self.theme)
        scroll.grid(row=0, column=0, sticky="nsew", padx=24, pady=(16, 12))
        scroll.grid_columnconfigure(0, weight=1)
        page_header(
            scroll,
            self.theme,
            "MySQL Patch Management",
            subtitle="Ubuntu MySQL package update. Apply runs a dry-run and does not install packages.",
            pady=(0, 12),
        )

        form = self.card_frame(scroll)
        form.pack(fill="x", pady=(0, 12))
        form.grid_columnconfigure(1, weight=1)
        self._label(form, "Target server").grid(row=0, column=0, padx=16, pady=10, sticky="w")
        self.server_menu = ctk.CTkOptionMenu(form, values=["No MySQL SSH server"], command=self._on_server)
        self.server_menu.grid(row=0, column=1, padx=16, pady=10, sticky="ew")
        self._label(form, "Backup file").grid(row=1, column=0, padx=16, pady=10, sticky="w")
        path_row = ctk.CTkFrame(form, fg_color="transparent")
        path_row.grid(row=1, column=1, padx=16, pady=10, sticky="ew")
        path_row.grid_columnconfigure(0, weight=1)
        self.backup_path = ctk.CTkEntry(path_row, placeholder_text="Readable backup used for the precheck")
        self.backup_path.grid(row=0, column=0, sticky="ew")
        self.backup_path.bind("<KeyRelease>", lambda _event: self._sync_buttons())
        secondary_button(path_row, self.theme, text="Browse", command=self._browse_backup, width=90).grid(
            row=0, column=1, padx=(8, 0)
        )
        self.detected = ctk.CTkLabel(
            form,
            text="OS, package manager, MySQL version, and repository appear after prechecks.",
            justify="left",
            anchor="w",
            text_color=self.theme.c("text_secondary"),
        )
        self.detected.grid(row=2, column=0, columnspan=2, padx=16, pady=(0, 8), sticky="ew")
        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.grid(row=3, column=0, columnspan=2, padx=16, pady=(0, 16), sticky="w")
        self.precheck_button = primary_button(actions, self.theme, text="Run Prechecks", command=self._start_precheck)
        self.precheck_button.pack(side="left")
        self.refresh_button = secondary_button(
            actions, self.theme, text="Refresh Patch Information", command=self._start_preview
        )
        self.refresh_button.pack(side="left", padx=(8, 0))

        self.precheck_box = self._section(scroll, "Precheck results")
        self.version_label = self._section_label(scroll, "Current and candidate versions")
        self.preview_box = self._section(scroll, "Package change preview")
        self.risk_label = self._section_label(scroll, "Backup readiness and downtime")
        plan_actions = ctk.CTkFrame(scroll, fg_color="transparent")
        plan_actions.pack(fill="x", pady=(0, 12))
        self.plan_button = secondary_button(plan_actions, self.theme, text="Generate Patch Plan", command=self._create_plan)
        self.plan_button.pack(side="left")
        self.approve_button = secondary_button(
            plan_actions, self.theme, text="Review and Approve", command=self._open_approval
        )
        self.approve_button.pack(side="left", padx=(8, 0))
        self.apply_button = primary_button(plan_actions, self.theme, text="Apply Patch", command=self._start_apply)
        self.apply_button.pack(side="left", padx=(8, 0))

        self.progress_label = self._section_label(scroll, "Execution progress")
        self.log_box = self._section(scroll, "Sanitized log")
        self.verify_box = self._section(scroll, "Post-patch verification")
        history_head = ctk.CTkFrame(scroll, fg_color="transparent")
        history_head.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(
            history_head,
            text="Patch history",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=self.theme.c("text_primary"),
        ).pack(side="left")
        self.job_menu = ctk.CTkOptionMenu(history_head, values=["No jobs"], variable=self._selected_job, width=280)
        self.job_menu.pack(side="right")
        self.history_box = self._textbox(scroll)
        history_actions = ctk.CTkFrame(scroll, fg_color="transparent")
        history_actions.pack(fill="x", pady=(0, 8))
        secondary_button(history_actions, self.theme, text="View Details", command=self._show_details).pack(side="left")
        secondary_button(history_actions, self.theme, text="Export CSV", command=self._export_csv).pack(
            side="left", padx=(8, 0)
        )
        secondary_button(history_actions, self.theme, text="Export PDF", command=self._export_pdf).pack(
            side="left", padx=(8, 0)
        )
        self._sync_buttons()

    def _label(self, parent, text: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            parent,
            text=text,
            font=ctk.CTkFont(size=12),
            text_color=self.theme.c("text_secondary"),
            anchor="w",
        )

    def _section_label(self, parent, text: str) -> ctk.CTkLabel:
        label = ctk.CTkLabel(
            parent,
            text=text,
            justify="left",
            anchor="w",
            font=ctk.CTkFont(size=12),
            text_color=self.theme.c("text_primary"),
        )
        label.pack(fill="x", pady=(0, 8))
        return label

    def _section(self, parent, title: str) -> ctk.CTkTextbox:
        ctk.CTkLabel(
            parent,
            text=title,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).pack(fill="x", pady=(0, 6))
        return self._textbox(parent)

    def _textbox(self, parent) -> ctk.CTkTextbox:
        box = ctk.CTkTextbox(parent, height=120, activate_scrollbars=True)
        box.pack(fill="x", pady=(0, 12))
        box.configure(state="disabled")
        return box

    def _write(self, box: ctk.CTkTextbox, text: str) -> None:
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("end", text)
        box.configure(state="disabled")

    def _load_servers(self) -> None:
        servers = load_saved_servers(self.owner_username)
        self._servers = [
            server
            for server in servers
            if server.db_type == "mysql" and server.ssh_enabled and server.linux_username.strip()
        ]
        labels = [self._server_label(server) for server in self._servers] or ["No MySQL SSH server"]
        self.server_menu.configure(values=labels)
        self.server_menu.set(labels[0])
        self._sync_buttons()

    def _server_label(self, server: SavedServer) -> str:
        host = server.ssh_host or server.host
        return f"{server.name} ({host}:{server.ssh_port})"

    def _selected_server(self) -> SavedServer | None:
        label = self.server_menu.get()
        for server in self._servers:
            if self._server_label(server) == label:
                return server
        return None

    def _on_server(self, _value: str) -> None:
        self._precheck = None
        self._preview = None
        self._plan = None
        self._sync_buttons()

    def _browse_backup(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Backup files", "*.sql *.gz *.dump"), ("All files", "*.*")])
        if not path:
            return
        self.backup_path.delete(0, "end")
        self.backup_path.insert(0, path)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        server_selected = self._selected_server() is not None
        backup = self.backup_path.get()
        precheck_ready = bool(self._precheck and self._precheck.ready_for_plan)
        preview_accepted = bool(self._preview and self._preview.accepted)
        plan_ready = self._plan is not None
        approved = bool(self._plan and self._plan.approval and self._plan.approval.high_risk_confirmed)
        self.precheck_button.configure(
            state="normal" if precheck_allowed(server_selected=server_selected, backup_path=backup, busy=self._busy) else "disabled"
        )
        self.refresh_button.configure(state="normal" if refresh_allowed(precheck_ready=precheck_ready, busy=self._busy) else "disabled")
        self.plan_button.configure(state="normal" if plan_allowed(preview_accepted=preview_accepted, busy=self._busy) else "disabled")
        self.approve_button.configure(
            state="normal"
            if approve_allowed(plan_ready=plan_ready, already_approved=approved, busy=self._busy)
            else "disabled"
        )
        self.apply_button.configure(
            state="normal"
            if apply_allowed(
                precheck_ready=precheck_ready,
                preview_accepted=preview_accepted,
                approval=None if self._plan is None else self._plan.approval,
                busy=self._busy,
            )
            else "disabled"
        )

    def _runner_for(self, server: SavedServer) -> SshCommandRunner:
        return SshCommandRunner(
            host=server.ssh_host or server.host,
            port=int(server.ssh_port or 22),
            username=server.linux_username,
            password=server.linux_password,
            fallback_host=server.host,
        )

    def _start_precheck(self) -> None:
        server = self._selected_server()
        if server is None:
            return
        backup = self.backup_path.get().strip()
        self._run_async(
            lambda: self._prechecks.precheck(
                self._runner_for(server),
                server_id=server.name,
                backup_path=backup,
            ),
            self._show_precheck,
        )

    def _show_precheck(self, report: PrecheckReport) -> None:
        self._precheck = report
        self._preview = None
        self._plan = None
        discovery = report.discovery
        self.detected.configure(
            text=(
                f"OS: {discovery.os_pretty_name or discovery.os_id or 'unknown'}\n"
                f"Package manager: {discovery.package_manager.value}\n"
                f"MySQL: {discovery.mysql_upstream_version or 'not detected'} "
                f"({discovery.mysql_server_package or 'no package'})\n"
                f"Repository: {discovery.repository_origin.value}"
            )
        )
        self.version_label.configure(
            text=(
                f"Current {discovery.mysql_server_version or '-'}  |  "
                f"Candidate {discovery.candidate_version or '-'}"
            )
        )
        self._write(self.precheck_box, _checks_text(report.checks, report.stop_reason))
        self.risk_label.configure(
            text=(
                f"Backup: {report.backup_state.value}. Restore has not been proven.\n"
                f"Service interruption expected: {'yes' if report.service_interruption_expected else 'no'}."
            )
        )
        self._write(self.preview_box, "Run Refresh Patch Information after prechecks pass.")
        self.log("MySQL patch prechecks finished")

    def _start_preview(self) -> None:
        server = self._selected_server()
        if server is None:
            return
        backup = self.backup_path.get().strip()
        self._run_async(
            lambda: self._previews.preview(
                self._runner_for(server),
                server_id=server.name,
                backup_path=backup,
            ),
            self._show_preview,
        )

    def _show_preview(self, report: PackagePreviewReport) -> None:
        self._preview = report
        self._precheck = report.precheck
        self._plan = None
        lines = [
            f"{item.action}: {item.name} {item.current_version or '-'} -> {item.target_version or '-'}"
            for item in report.changes
        ]
        summary = "Preview accepted." if report.accepted else f"Preview stopped: {report.stop_reason}"
        self._write(self.preview_box, summary + ("\n" + "\n".join(lines) if lines else ""))
        self.log("MySQL patch preview finished")

    def _create_plan(self) -> None:
        if self._preview is None:
            return
        try:
            self._plan = self._plans.create_from_preview(self._preview)
        except Exception as exc:
            self._plan = None
            self.log(f"Patch plan was not created: {redact_text(str(exc))}", "ERROR")
        self._sync_buttons()

    def _open_approval(self) -> None:
        if self._plan is None or self._plan.approval is not None:
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("Approve MySQL patch plan")
        dialog.geometry("560x460")
        dialog.grab_set()
        text = ctk.CTkTextbox(dialog, height=260)
        text.pack(fill="both", expand=True, padx=16, pady=(16, 8))
        text.insert("end", _plan_text(self._plan))
        text.configure(state="disabled")
        confirmed = ctk.BooleanVar(value=False)
        check = ctk.CTkCheckBox(
            dialog,
            text="I confirm the MySQL service may restart and the backup restore is not proven",
            variable=confirmed,
        )
        check.pack(anchor="w", padx=16, pady=8)
        approver = ctk.CTkEntry(dialog, placeholder_text="Your name")
        approver.pack(fill="x", padx=16, pady=(0, 8))
        if self.employee is not None:
            approver.insert(0, self.employee.username)

        def _approve() -> None:
            if self._plan is None or not confirmed.get():
                return
            try:
                self._plan = self._plans.approve(
                    self._plan,
                    approved_by=approver.get(),
                    high_risk_confirmed=True,
                )
            except Exception as exc:
                self.log(f"Approval was not recorded: {redact_text(str(exc))}", "ERROR")
                return
            dialog.destroy()
            self._sync_buttons()
            self.log("MySQL patch plan approved for dry-run")

        primary_button(dialog, self.theme, text="Approve dry-run", command=_approve).pack(pady=(0, 16))

    def _start_apply(self) -> None:
        server = self._selected_server()
        plan = self._plan
        if server is None or plan is None:
            return
        backup = self.backup_path.get().strip()
        actor = self.employee.username if self.employee is not None else "unknown"

        def work():
            execution = self._executor.execute(
                self._runner_for(server),
                plan,
                backup_path=backup,
                mode="dry_run",
            )
            verification = self._verification.verify(self._runner_for(server), plan)
            self._audit.record(execution, plan, initiated_by=actor, verification=verification)
            return execution, verification

        self.progress_label.configure(text="Execution progress: dry-run started")
        self._run_async(work, self._show_apply)

    def _show_apply(self, result) -> None:
        execution, verification = result
        self.progress_label.configure(
            text=f"Execution progress: {execution.stage.value}, outcome {execution.outcome.value}. No packages installed."
        )
        self._write(self.log_box, execution.log_excerpt or execution.stop_reason or "Dry-run finished.")
        steps = "\n".join(verification.recommended_next_steps)
        self._write(
            self.verify_box,
            f"Outcome: {verification.outcome.value}\n{verification.stop_reason or 'Checks passed.'}\n{steps}",
        )
        self._load_history()
        self.log(f"MySQL patch dry-run {execution.outcome.value}")

    def _load_history(self) -> None:
        server = self._selected_server()
        self._history = list(reversed(self._audit.history(None if server is None else server.name)))
        if not self._history:
            self.job_menu.configure(values=["No jobs"])
            self._selected_job.set("No jobs")
            self._write(self.history_box, "No patch jobs stored for this server.")
            return
        labels = [f"{row.job_id[:8]} {row.final_status} {row.target_version}" for row in self._history]
        self.job_menu.configure(values=labels)
        self._selected_job.set(labels[0])
        self._write(
            self.history_box,
            "\n".join(
                f"{row.started_at.isoformat()}  {row.server_id}  {row.final_status}  "
                f"{row.current_version} -> {row.target_version}  installed={row.packages_installed}"
                for row in self._history
            ),
        )

    def _selected_record(self) -> MysqlPatchAuditRecord | None:
        label = self._selected_job.get()
        for row, text in zip(self._history, [f"{row.job_id[:8]} {row.final_status} {row.target_version}" for row in self._history]):
            if text == label:
                return row
        return self._history[0] if self._history else None

    def _show_details(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        lines = [
            f"Job: {record.job_id}",
            f"Server: {record.server_id}",
            f"User: {record.initiated_by}",
            f"Plan: {record.plan_id}",
            f"Digest: {record.plan_digest}",
            f"Approved by: {record.approved_by or '-'}",
            f"Stage: {record.stage}",
            f"Execution: {record.execution_outcome}",
            f"Verification: {record.verification_outcome or '-'}",
            f"Final: {record.final_status}",
            f"Packages installed: {record.packages_installed}",
            f"Error: {record.error_message or '-'}",
        ]
        self._write(self.log_box, "\n".join(lines))

    def _export_csv(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if path:
            export_patch_csv(record, path)
            self.log(f"Patch report exported to {path}")

    def _export_pdf(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        path = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if path:
            export_patch_pdf(record, path)
            self.log(f"Patch report exported to {path}")

    def _run_async(self, work, on_success) -> None:
        if self._busy:
            return
        self._busy = True
        self._sync_buttons()

        def task() -> None:
            try:
                result = work()
            except Exception as exc:
                message = redact_text(str(exc)) or "The patch action failed"
                self.after(0, lambda: self._show_error(message))
                return
            self.after(0, lambda: self._show_success(on_success, result))

        threading.Thread(target=task, daemon=True).start()

    def _show_success(self, on_success, result) -> None:
        self._busy = False
        on_success(result)
        self._sync_buttons()

    def _show_error(self, message: str) -> None:
        self._busy = False
        self.progress_label.configure(text="Execution progress: failed before completion")
        self._write(self.log_box, message)
        self.log(f"MySQL patch action failed: {message}", "ERROR")
        self._sync_buttons()


def _checks_text(checks, stop_reason: str | None) -> str:
    lines = [f"[{item.status.value}] {item.code}: {item.summary}" for item in checks]
    if stop_reason:
        lines.append(f"Stop: {stop_reason}")
    return "\n".join(lines) or "No checks."


def _plan_text(plan: PatchPlan) -> str:
    packages = "\n".join(
        f"- {item.action} {item.name} {item.current_version or '-'} -> {item.target_version or '-'}"
        for item in plan.packages
    )
    risks = "\n".join(f"- {reason}" for reason in plan.high_risk_reasons)
    return (
        f"Server: {plan.server_id}\n"
        f"Current: {plan.current_version}\n"
        f"Target: {plan.target_version}\n"
        f"Repository: {plan.repository_origin.value}\n"
        f"Privileges: {plan.required_privileges}\n"
        f"Backup: {plan.backup_state.value}\n\n"
        f"Packages:\n{packages}\n\n"
        f"Risks:\n{risks}\n"
    )
