"""Employee login and registration shown before database connection."""

from __future__ import annotations

import customtkinter as ctk

from desktop.constants import APP_NAME
from desktop.design_system import font, ghost_button, primary_button, text_input
from desktop.employee_store import EmployeeSession, authenticate_employee, register_employee
from desktop.theme import (
    ThemeManager,
    clear_last_password,
    load_last_password,
    load_last_username,
    load_remember_password,
    load_saved_theme,
    save_last_credentials,
    save_last_username,
)

ENTRY_HEIGHT = 40
BODY_PAD = 24
FORM_WIDTH = 400
_MODE_SIZES = {
    "login": (480, 560),
    "register": (480, 700),
    "success": (480, 420),
}


class AuthDialog(ctk.CTk):
    """Login and registration gate before the main application."""

    def __init__(self) -> None:
        super().__init__()
        self.session: EmployeeSession | None = None
        self._mode = "login"
        self._prefill_username = ""
        self.theme = ThemeManager(load_saved_theme())
        self._remember_var = ctk.BooleanVar(value=True)

        self.title(f"{APP_NAME} — Login")
        self.configure(fg_color=self.theme.c("bg_app"))
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_shell()
        self._show_login()
        self.after(20, self._fit_window)

    def _fit_window(self) -> None:
        if not self.winfo_exists():
            return
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width, height = _MODE_SIZES.get(self._mode, (480, 560))
        width = min(width, screen_w - 40)
        height = min(height, screen_h - 60)
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.update_idletasks()

    def _build_shell(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        outer = ctk.CTkFrame(self, fg_color=self.theme.c("bg_app"), corner_radius=0)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(0, weight=1)

        self.card = ctk.CTkFrame(
            outer,
            fg_color=self.theme.c("bg_card"),
            corner_radius=16,
            border_width=1,
            border_color=self.theme.c("border"),
            width=FORM_WIDTH + BODY_PAD * 2,
        )
        self.card.grid(row=0, column=0)
        self.card.grid_propagate(True)

        ctk.CTkFrame(
            self.card, height=4, corner_radius=0, fg_color=self.theme.c("primary")
        ).pack(fill="x")

        self.form_frame = ctk.CTkFrame(self.card, fg_color="transparent")
        self.form_frame.pack(fill="both", expand=True, padx=BODY_PAD, pady=BODY_PAD)

        self.status_label = ctk.CTkLabel(
            self.card,
            text="",
            font=font(self.theme, 12),
            text_color=self.theme.c("text_muted"),
            wraplength=FORM_WIDTH,
            justify="left",
            anchor="w",
        )
        self.status_label.pack(fill="x", padx=BODY_PAD, pady=(0, BODY_PAD))

    def _clear_form(self) -> None:
        for child in list(self.form_frame.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass

    def _add_back_button(self) -> None:
        """← Back control used on Register / Success to return to Login."""
        back = ctk.CTkButton(
            self.form_frame,
            text="←  Back to Login",
            anchor="w",
            height=32,
            width=FORM_WIDTH,
            fg_color="transparent",
            hover_color=self.theme.c("primary_muted"),
            text_color=self.theme.c("primary"),
            font=font(self.theme, 13, weight="bold"),
            command=self._show_login,
        )
        back.pack(fill="x", pady=(0, 12))

    def _header(self, title: str, subtitle: str) -> None:
        ctk.CTkLabel(
            self.form_frame,
            text=title,
            font=font(self.theme, 22, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
            wraplength=FORM_WIDTH,
            justify="left",
        ).pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(
            self.form_frame,
            text=subtitle,
            font=font(self.theme, 13),
            text_color=self.theme.c("text_muted"),
            anchor="w",
            wraplength=FORM_WIDTH,
            justify="left",
        ).pack(fill="x", pady=(0, 14))

    def _labeled_entry(self, label: str, *, placeholder: str) -> ctk.CTkEntry:
        ctk.CTkLabel(
            self.form_frame,
            text=label,
            font=font(self.theme, 13, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).pack(fill="x", pady=(6, 3))
        entry = text_input(
            self.form_frame,
            self.theme,
            placeholder=placeholder,
            height=ENTRY_HEIGHT,
            width=FORM_WIDTH,
        )
        entry.pack(fill="x", pady=(0, 2))
        return entry

    def _labeled_password_entry(
        self, label: str, *, placeholder: str
    ) -> tuple[ctk.CTkEntry, ctk.CTkButton]:
        ctk.CTkLabel(
            self.form_frame,
            text=label,
            font=font(self.theme, 13, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).pack(fill="x", pady=(6, 3))

        row = ctk.CTkFrame(self.form_frame, fg_color="transparent")
        row.pack(fill="x", pady=(0, 2))

        entry = text_input(
            row,
            self.theme,
            placeholder=placeholder,
            height=ENTRY_HEIGHT,
            show="•",
            width=FORM_WIDTH - 72,
        )
        entry.pack(side="left", fill="x", expand=True)

        toggle = ctk.CTkButton(
            row,
            text="Show",
            width=64,
            height=ENTRY_HEIGHT,
            fg_color=self.theme.c("bg_list"),
            hover_color=self.theme.c("bg_card_hover"),
            text_color=self.theme.c("text_secondary"),
            border_width=1,
            border_color=self.theme.c("border"),
            font=font(self.theme, 11),
            corner_radius=self.theme.radius("md"),
        )
        toggle.configure(command=lambda: self._toggle_password_visibility(entry, toggle))
        toggle.pack(side="left", padx=(8, 0))
        return entry, toggle

    def _toggle_password_visibility(self, entry: ctk.CTkEntry, button: ctk.CTkButton) -> None:
        visible = entry.cget("show") == ""
        entry.configure(show="" if not visible else "•")
        button.configure(text="Hide" if not visible else "Show")

    def _set_status(self, message: str, level: str = "info") -> None:
        colors = {
            "info": self.theme.c("text_muted"),
            "error": self.theme.c("error"),
            "success": self.theme.c("success"),
            "warning": self.theme.c("warning"),
        }
        self.status_label.configure(
            text=message,
            text_color=colors.get(level, self.theme.c("text_muted")),
        )

    def _show_login(self, *, prefill_username: str | None = None) -> None:
        self._mode = "login"
        self.title(f"{APP_NAME} — Login")
        self._clear_form()
        self._set_status("")
        self._header(APP_NAME, "Sign in to manage remote database automation")

        self.login_user = self._labeled_entry("Username", placeholder="Username")
        self.login_pass, login_pass_toggle = self._labeled_password_entry(
            "Password", placeholder="Password"
        )

        username = (prefill_username or self._prefill_username or load_last_username()).strip()
        if username:
            self.login_user.insert(0, username)
            self._prefill_username = ""

        saved_password = load_last_password()
        last_user = load_last_username().strip()
        if saved_password and username and last_user.lower() == username.lower():
            self.login_pass.delete(0, "end")
            self.login_pass.insert(0, saved_password)
        # Always start masked; user can click Show if needed.
        self.login_pass.configure(show="•")
        login_pass_toggle.configure(text="Show")

        self._remember_var = ctk.BooleanVar(value=load_remember_password())
        ctk.CTkCheckBox(
            self.form_frame,
            text="Remember password",
            variable=self._remember_var,
            font=font(self.theme, 12),
            text_color=self.theme.c("text_secondary"),
            fg_color=self.theme.c("primary"),
            hover_color=self.theme.c("primary_hover"),
            border_color=self.theme.c("border"),
            command=self._on_remember_toggled,
        ).pack(anchor="w", pady=(10, 0))

        self.login_user.bind("<KeyRelease>", lambda _e: self._persist_login_draft())
        self.login_pass.bind("<KeyRelease>", lambda _e: self._persist_login_draft())
        self.login_pass.bind("<FocusOut>", lambda _e: self._persist_login_draft())

        self.login_btn = primary_button(
            self.form_frame,
            self.theme,
            text="Login",
            command=self._on_login,
            height=44,
            width=FORM_WIDTH,
        )
        self.login_btn.pack(fill="x", pady=(16, 8))

        ghost_button(
            self.form_frame,
            self.theme,
            text="New here? Create an account",
            command=self._show_register,
        ).pack(fill="x", pady=(2, 0))

        self.bind("<Return>", lambda _e: self._on_login())
        if username:
            self.login_pass.focus_set()
        else:
            self.login_user.focus_set()
        self.after(20, self._fit_window)

    def _on_remember_toggled(self) -> None:
        if self._remember_var.get():
            self._persist_login_draft()
        else:
            clear_last_password()

    def _persist_login_draft(self) -> None:
        if not self._remember_var.get():
            return
        username = self.login_user.get().strip()
        password = self.login_pass.get()
        if username and password:
            save_last_credentials(username, password)

    def _show_register(self) -> None:
        self._mode = "register"
        self.title(f"{APP_NAME} — Register")
        self._clear_form()
        self._set_status("")

        self._add_back_button()
        self._header("Create account", "Register as an employee to access Database Automation Pro")

        self.reg_name = self._labeled_entry("Full name", placeholder="Your name")
        self.reg_user = self._labeled_entry("Username", placeholder="Choose a username")
        self.reg_email = self._labeled_entry("Email", placeholder="name@company.com (optional)")
        self.reg_pass, _ = self._labeled_password_entry("Password", placeholder="At least 6 characters")
        self.reg_confirm, _ = self._labeled_password_entry(
            "Confirm password", placeholder="Re-enter password"
        )

        primary_button(
            self.form_frame,
            self.theme,
            text="Register",
            command=self._on_register,
            height=44,
            width=FORM_WIDTH,
        ).pack(fill="x", pady=(16, 8))

        ghost_button(
            self.form_frame,
            self.theme,
            text="←  Back to Login",
            command=self._show_login,
        ).pack(fill="x", pady=(2, 4))

        self.bind("<Return>", lambda _e: self._on_register())
        self.reg_name.focus_set()
        self.after(20, self._fit_window)

    def _show_registration_success(self, full_name: str, username: str) -> None:
        self._mode = "success"
        self.title(f"{APP_NAME} — Registration Successful")
        self._clear_form()
        self._set_status("")

        self._add_back_button()
        ctk.CTkLabel(
            self.form_frame,
            text="✓",
            font=font(self.theme, 48, weight="bold"),
            text_color=self.theme.c("success"),
        ).pack(pady=(8, 12))
        ctk.CTkLabel(
            self.form_frame,
            text="Registration Successful!",
            font=font(self.theme, 22, weight="bold"),
            text_color=self.theme.c("text_primary"),
        ).pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            self.form_frame,
            text=(
                f"Welcome, {full_name}!\n\n"
                f"Your account ({username}) is ready.\n"
                "Sign in to continue."
            ),
            font=font(self.theme, 13),
            text_color=self.theme.c("text_muted"),
            justify="center",
            wraplength=FORM_WIDTH,
        ).pack(fill="x", pady=(0, 20))

        primary_button(
            self.form_frame,
            self.theme,
            text="Back to Login",
            command=lambda: self._show_login(prefill_username=username),
            height=44,
            width=FORM_WIDTH,
        ).pack(fill="x")

        self.unbind("<Return>")
        self.after(20, self._fit_window)

    def _on_login(self) -> None:
        if getattr(self, "_login_busy", False):
            return
        username = self.login_user.get().strip()
        password = self.login_pass.get()
        if not username:
            self._set_status("Username is required.", "error")
            self.login_user.focus_set()
            return
        if not password:
            self._set_status("Password is required.", "error")
            self.login_pass.focus_set()
            return

        self._login_busy = True
        self._set_status("Signing in…", "info")
        try:
            self.login_btn.configure(state="disabled")
        except Exception:
            pass

        import threading

        def task() -> None:
            error: str | None = None
            session: EmployeeSession | None = None
            try:
                session = authenticate_employee(username, password)
            except Exception as exc:
                error = f"Login failed: {exc}"

            def finish(
                sess: EmployeeSession | None = session,
                err: str | None = error,
                pwd: str = password,
            ) -> None:
                self._login_busy = False
                try:
                    self.login_btn.configure(state="normal")
                except Exception:
                    pass
                if err:
                    self._set_status(err, "error")
                    return
                if not sess:
                    self._set_status("Invalid username or password.", "error")
                    self.login_pass.delete(0, "end")
                    self.login_pass.focus_set()
                    return
                if self._remember_var.get():
                    save_last_credentials(sess.username, pwd)
                else:
                    clear_last_password()
                    save_last_username(sess.username)
                self.session = sess
                self.destroy()

            self.after(0, finish)

        threading.Thread(target=task, daemon=True).start()

    def _on_register(self) -> None:
        full_name = self.reg_name.get().strip()
        username = self.reg_user.get().strip()
        email = self.reg_email.get().strip()
        password = self.reg_pass.get()
        confirm = self.reg_confirm.get()

        if not full_name:
            self._set_status("Full name is required.", "error")
            self.reg_name.focus_set()
            return
        if not username:
            self._set_status("Username is required.", "error")
            self.reg_user.focus_set()
            return
        if len(password) < 6:
            self._set_status("Password must be at least 6 characters.", "error")
            self.reg_pass.focus_set()
            return
        if password != confirm:
            self._set_status("Passwords do not match.", "error")
            self.reg_confirm.focus_set()
            return

        try:
            register_employee(
                username=username,
                password=password,
                full_name=full_name,
                email=email,
            )
        except ValueError as exc:
            msg = str(exc)
            if "already taken" in msg.lower():
                save_last_credentials(username, password)
                self._set_status(
                    f"'{username}' already exists. Going back to Login…",
                    "warning",
                )
                self.after(200, lambda: self._show_login(prefill_username=username))
                return
            self._set_status(msg, "error")
            return
        except Exception as exc:
            self._set_status(f"Registration failed: {exc}", "error")
            return

        save_last_credentials(username, password)
        self._show_registration_success(full_name, username)

    def _on_close(self) -> None:
        self.session = None
        self.destroy()

    @staticmethod
    def show() -> EmployeeSession | None:
        dialog = AuthDialog()
        dialog.update_idletasks()
        dialog._fit_window()
        dialog.mainloop()
        return dialog.session
