"""Employee login and registration shown before database connection."""

from __future__ import annotations

import customtkinter as ctk

from desktop.constants import APP_NAME
from desktop.design_system import font, ghost_button, primary_button, text_input
from desktop.employee_store import EmployeeSession, authenticate_employee, register_employee
from desktop.theme import ThemeManager, load_saved_theme, save_last_username, load_last_username

ENTRY_HEIGHT = 44
BODY_PAD = 32
CARD_MIN_WIDTH = 480
CARD_MAX_WIDTH = 520
_MODE_SIZES = {
    "login": (560, 560),
    "register": (560, 880),
    "success": (560, 520),
}


class AuthDialog(ctk.CTk):
    """Premium login and registration gate before the main application."""

    def __init__(self) -> None:
        super().__init__()
        self.session: EmployeeSession | None = None
        self._mode = "login"
        self._prefill_username: str = ""
        self._wraplength = 440
        self._destroying = False
        self._pending_jobs: list[str] = []
        self.theme = ThemeManager(load_saved_theme())

        self.title(f"{APP_NAME} — Login")
        self.configure(fg_color=self.theme.c("bg_app"))
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_shell()
        self._show_login()
        self.bind("<Map>", self._on_map, add="+")
        self.bind("<Configure>", self._on_resize, add="+")

    def _defer(self, delay_ms: int, callback) -> None:
        if self._destroying:
            return
        try:
            job = self.after(delay_ms, callback)
            self._pending_jobs.append(job)
        except Exception:
            pass

    def _on_map(self, _event=None) -> None:
        self._defer(0, self._fit_window)

    def _on_resize(self, event=None) -> None:
        if self._destroying or event is not None and event.widget is not self:
            return
        self._sync_layout_widths()

    def _fit_window(self) -> None:
        """Size and center the window for the current auth mode."""
        if self._destroying or not self.winfo_exists():
            return
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        mode_w, mode_h = _MODE_SIZES.get(self._mode, (560, 560))
        width = max(mode_w, CARD_MIN_WIDTH + 80)
        width = min(width, int(screen_w * 0.96))
        height = min(mode_h, max(480, screen_h - 40))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(CARD_MIN_WIDTH + 40, 480)
        self.update_idletasks()
        self._sync_layout_widths()

    def _sync_layout_widths(self) -> None:
        if self._destroying or not self.winfo_exists():
            return
        card_w = max(CARD_MIN_WIDTH, min(CARD_MAX_WIDTH, self.winfo_width() - 56))
        self._wraplength = max(320, card_w - (BODY_PAD * 2) - 16)
        try:
            self.card.configure(width=card_w)
        except Exception:
            pass
        self.status_label.configure(wraplength=self._wraplength)
        for widget in self.form_frame.winfo_children():
            if isinstance(widget, ctk.CTkLabel):
                try:
                    widget.configure(wraplength=self._wraplength)
                except Exception:
                    pass

    def _schedule_fit(self) -> None:
        self._defer(0, self._fit_window)
        self._defer(120, self._fit_window)

    def _center_window(self, _width: int, _height: int) -> None:
        self._schedule_fit()

    def _build_shell(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        canvas = ctk.CTkFrame(self, fg_color=self.theme.c("bg_app"), corner_radius=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        canvas.grid_columnconfigure(0, weight=1)
        canvas.grid_columnconfigure(1, weight=0)
        canvas.grid_columnconfigure(2, weight=1)
        canvas.grid_rowconfigure(0, weight=1)
        canvas.grid_rowconfigure(1, weight=0)
        canvas.grid_rowconfigure(2, weight=1)

        glow_left = ctk.CTkFrame(
            canvas, width=240, height=240, corner_radius=120, fg_color=self.theme.c("primary_muted")
        )
        glow_left.place(relx=0.05, rely=0.08, anchor="center")
        glow_right = ctk.CTkFrame(
            canvas, width=180, height=180, corner_radius=90, fg_color=self.theme.c("bg_elevated")
        )
        glow_right.place(relx=0.95, rely=0.92, anchor="center")

        self.card = ctk.CTkFrame(
            canvas,
            fg_color=self.theme.c("bg_glass") if not self.theme.is_light else self.theme.c("bg_card"),
            corner_radius=self.theme.radius("xl"),
            border_width=1,
            border_color=self.theme.c("border"),
        )
        self.card.grid(row=1, column=1, sticky="n", padx=20, pady=20)
        self.card.grid_columnconfigure(0, weight=1)
        self.card.grid_rowconfigure(0, weight=1)

        self.body = ctk.CTkFrame(self.card, fg_color="transparent")
        self.body.grid(row=0, column=0, sticky="nsew", padx=BODY_PAD, pady=BODY_PAD)
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)

        self.form_frame = ctk.CTkFrame(self.body, fg_color="transparent")
        self.form_frame.grid(row=0, column=0, sticky="nsew")
        self.form_frame.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            self.body,
            text="",
            font=font(self.theme, 12),
            text_color=self.theme.c("text_muted"),
            wraplength=self._wraplength,
            justify="left",
        )
        self.status_label.grid(row=1, column=0, sticky="ew", pady=(12, 0))

    def _clear_form(self) -> None:
        for child in self.form_frame.winfo_children():
            child.destroy()

    def _header(self, title: str, subtitle: str) -> None:
        ctk.CTkLabel(
            self.form_frame,
            text=title,
            font=font(self.theme, 24, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
            wraplength=self._wraplength,
            justify="left",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 4))

        ctk.CTkLabel(
            self.form_frame,
            text=subtitle,
            font=font(self.theme, 13),
            text_color=self.theme.c("text_muted"),
            anchor="w",
            wraplength=self._wraplength,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(0, 20))

    def _labeled_entry(self, row: int, label: str, *, placeholder: str) -> ctk.CTkEntry:
        ctk.CTkLabel(
            self.form_frame,
            text=label,
            font=font(self.theme, 13, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).grid(row=row, column=0, sticky="ew", pady=(8, 4))

        entry = text_input(self.form_frame, self.theme, placeholder=placeholder, height=ENTRY_HEIGHT)
        entry.grid(row=row + 1, column=0, sticky="ew", pady=(0, 4))
        return entry

    def _labeled_password_entry(self, row: int, label: str, *, placeholder: str) -> ctk.CTkEntry:
        ctk.CTkLabel(
            self.form_frame,
            text=label,
            font=font(self.theme, 13, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).grid(row=row, column=0, sticky="ew", pady=(8, 4))

        field_row = ctk.CTkFrame(self.form_frame, fg_color="transparent")
        field_row.grid(row=row + 1, column=0, sticky="ew", pady=(0, 4))
        field_row.grid_columnconfigure(0, weight=1)

        entry = text_input(field_row, self.theme, placeholder=placeholder, height=ENTRY_HEIGHT, show="•")
        entry.grid(row=0, column=0, sticky="ew")

        toggle = ctk.CTkButton(
            field_row,
            text="Show",
            width=56,
            height=ENTRY_HEIGHT,
            fg_color=self.theme.c("bg_elevated"),
            hover_color=self.theme.c("bg_card_hover"),
            text_color=self.theme.c("text_secondary"),
            border_width=1,
            border_color=self.theme.c("border"),
            font=font(self.theme, 11),
            corner_radius=self.theme.radius("md"),
        )
        toggle.configure(command=lambda: self._toggle_password_visibility(entry, toggle))
        toggle.grid(row=0, column=1, padx=(8, 0))
        return entry

    def _toggle_password_visibility(self, entry: ctk.CTkEntry, button: ctk.CTkButton) -> None:
        visible = entry.cget("show") == ""
        entry.configure(show="" if not visible else "•")
        button.configure(text="Hide" if not visible else "Show")

    def _set_status(self, message: str, level: str = "info") -> None:
        colors = {
            "info": self.theme.c("text_muted"),
            "error": self.theme.c("error"),
            "success": self.theme.c("success"),
        }
        self.status_label.configure(text=message, text_color=colors.get(level, self.theme.c("text_muted")))

    def _show_login(self, *, prefill_username: str | None = None) -> None:
        self._mode = "login"
        self.title(f"{APP_NAME} — Login")
        self._clear_form()
        self._set_status("")
        self._header(APP_NAME, "Sign in to manage remote database automation")

        self.login_user = self._labeled_entry(2, "Username", placeholder="Username")
        self.login_pass = self._labeled_password_entry(4, "Password", placeholder="Password")

        username = (prefill_username or self._prefill_username or load_last_username()).strip()
        if username:
            self.login_user.insert(0, username)
            self._prefill_username = ""

        primary_button(
            self.form_frame, self.theme, text="Login", command=self._on_login, height=46
        ).grid(row=6, column=0, sticky="ew", pady=(20, 8))

        ghost_button(
            self.form_frame,
            self.theme,
            text="Don't have an account? Register",
            command=self._show_register,
        ).grid(row=7, column=0, sticky="ew", pady=(4, 0))

        self.bind("<Return>", lambda _e: self._on_login())
        if username:
            self.login_pass.focus_set()
        else:
            self.login_user.focus_set()
        self._schedule_fit()

    def _show_registration_success(self, full_name: str, username: str) -> None:
        self._mode = "success"
        self.title(f"{APP_NAME} — Registration Successful")
        self._clear_form()
        self._set_status("")

        ctk.CTkLabel(
            self.form_frame,
            text="✓",
            font=font(self.theme, 48, weight="bold"),
            text_color=self.theme.c("success"),
        ).grid(row=0, column=0, pady=(8, 12))

        ctk.CTkLabel(
            self.form_frame,
            text="Registration Successful!",
            font=font(self.theme, 24, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="center",
        ).grid(row=1, column=0, sticky="ew", pady=(0, 8))

        ctk.CTkLabel(
            self.form_frame,
            text=(
                f"Welcome, {full_name}!\n\n"
                f"Your account ({username}) has been created successfully.\n"
                "Please sign in to continue to the dashboard."
            ),
            font=font(self.theme, 13),
            text_color=self.theme.c("text_muted"),
            justify="center",
            wraplength=self._wraplength,
        ).grid(row=2, column=0, sticky="ew", pady=(0, 24))

        primary_button(
            self.form_frame,
            self.theme,
            text="Go to Login",
            command=lambda: self._show_login(prefill_username=username),
            height=46,
        ).grid(row=3, column=0, sticky="ew", pady=(0, 8))

        self.unbind("<Return>")
        self._schedule_fit()

    def _show_register(self) -> None:
        self._mode = "register"
        self.title(f"{APP_NAME} — Register")
        self._clear_form()
        self._set_status("")
        self._header("Create account", "Register as an employee to access Database Automation Pro")

        self.reg_name = self._labeled_entry(2, "Full name", placeholder="Your name")
        self.reg_user = self._labeled_entry(4, "Username", placeholder="Choose a username")
        self.reg_email = self._labeled_entry(6, "Email", placeholder="name@company.com (optional)")
        self.reg_pass = self._labeled_password_entry(8, "Password", placeholder="At least 6 characters")
        self.reg_confirm = self._labeled_password_entry(10, "Confirm password", placeholder="Re-enter password")

        primary_button(
            self.form_frame, self.theme, text="Register", command=self._on_register, height=46
        ).grid(row=12, column=0, sticky="ew", pady=(20, 8))

        ghost_button(
            self.form_frame,
            self.theme,
            text="Already have an account? Sign in",
            command=self._show_login,
        ).grid(row=13, column=0, sticky="ew", pady=(4, 8))

        self.bind("<Return>", lambda _e: self._on_register())
        self.reg_name.focus_set()
        self._schedule_fit()

    def _on_login(self) -> None:
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

        try:
            session = authenticate_employee(username, password)
        except Exception as exc:
            self._set_status(f"Login failed: {exc}", "error")
            return

        if not session:
            self._set_status("Invalid username or password.", "error")
            self.login_pass.delete(0, "end")
            self.login_pass.focus_set()
            return

        save_last_username(session.username)
        self.session = session
        self._safe_destroy()

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
            self._set_status(str(exc), "error")
            return
        except Exception as exc:
            self._set_status(f"Registration failed: {exc}", "error")
            return

        save_last_username(username)
        self._show_registration_success(full_name, username)

    def _on_close(self) -> None:
        self.session = None
        self._safe_destroy()

    def _safe_destroy(self) -> None:
        if self._destroying:
            return
        self._destroying = True
        for job in self._pending_jobs:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._pending_jobs.clear()
        try:
            self.unbind("<Map>")
            self.unbind("<Configure>")
            self.unbind("<Return>")
        except Exception:
            pass
        try:
            self.after(0, self._finish_destroy)
        except Exception:
            self._finish_destroy()

    def _finish_destroy(self) -> None:
        try:
            super().destroy()
        except Exception:
            pass

    @staticmethod
    def show() -> EmployeeSession | None:
        dialog = AuthDialog()
        dialog.update_idletasks()
        dialog._fit_window()
        dialog.mainloop()
        return dialog.session
