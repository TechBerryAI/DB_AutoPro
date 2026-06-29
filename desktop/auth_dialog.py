"""Employee login and registration shown before database connection."""

from __future__ import annotations

import customtkinter as ctk

from desktop.constants import APP_NAME
from desktop.design_system import font, primary_button, secondary_button, text_input
from desktop.employee_store import EmployeeSession, authenticate_employee, register_employee
from desktop.theme import ThemeManager, load_saved_theme, save_last_username, load_last_username

ENTRY_HEIGHT = 44
CARD_WIDTH = 520


class AuthDialog(ctk.CTk):
    """Premium login and registration gate before the main application."""

    def __init__(self) -> None:
        super().__init__()
        self.session: EmployeeSession | None = None
        self._mode = "login"
        self._prefill_username: str = ""
        self.theme = ThemeManager(load_saved_theme())

        self.title(f"{APP_NAME} — Login")
        self.configure(fg_color=self.theme.c("bg_app"))
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_shell()
        self._show_login()
        self._center_window(580, 500)

    def _center_window(self, width: int, height: int) -> None:
        self.update_idletasks()
        x = (self.winfo_screenwidth() - width) // 2
        y = (self.winfo_screenheight() - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build_shell(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.grid(row=0, column=0, padx=28, pady=28)

        self.card = ctk.CTkFrame(
            outer,
            width=CARD_WIDTH,
            fg_color=self.theme.c("bg_glass") if not self.theme.is_light else self.theme.c("bg_card"),
            corner_radius=self.theme.radius("lg"),
            border_width=1,
            border_color=self.theme.c("border"),
        )
        self.card.pack()
        self.card.grid_columnconfigure(1, weight=1)

        ctk.CTkFrame(self.card, height=4, corner_radius=0, fg_color=self.theme.c("primary")).pack(fill="x")

        self.body = ctk.CTkFrame(self.card, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=36, pady=32)
        self.body.grid_columnconfigure(1, weight=1)

        self.form_frame = ctk.CTkFrame(self.body, fg_color="transparent")
        self.form_frame.pack(fill="both", expand=True)
        self.form_frame.grid_columnconfigure(1, weight=1)

        self.status_label = ctk.CTkLabel(
            self.body,
            text="",
            font=font(self.theme, 12),
            text_color=self.theme.c("text_muted"),
            wraplength=CARD_WIDTH - 72,
            justify="left",
        )
        self.status_label.pack(anchor="w", pady=(12, 0))

    def _clear_form(self) -> None:
        for child in self.form_frame.winfo_children():
            child.destroy()

    def _header(self, title: str, subtitle: str) -> None:
        ctk.CTkLabel(
            self.form_frame,
            text=title,
            font=font(self.theme, 26, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))

        ctk.CTkLabel(
            self.form_frame,
            text=subtitle,
            font=font(self.theme, 13),
            text_color=self.theme.c("text_muted"),
            anchor="w",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 24))

    def _labeled_entry(self, row: int, label: str, *, placeholder: str) -> ctk.CTkEntry:
        ctk.CTkLabel(
            self.form_frame,
            text=label,
            font=font(self.theme, 13),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=8)

        entry = text_input(self.form_frame, self.theme, placeholder=placeholder, height=ENTRY_HEIGHT)
        entry.grid(row=row, column=1, sticky="ew", pady=8)
        return entry

    def _labeled_password_entry(self, row: int, label: str, *, placeholder: str) -> ctk.CTkEntry:
        ctk.CTkLabel(
            self.form_frame,
            text=label,
            font=font(self.theme, 13),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=8)

        field_row = ctk.CTkFrame(self.form_frame, fg_color="transparent")
        field_row.grid(row=row, column=1, sticky="ew", pady=8)
        field_row.grid_columnconfigure(0, weight=1)

        entry = text_input(field_row, self.theme, placeholder=placeholder, height=ENTRY_HEIGHT, show="•")
        entry.grid(row=0, column=0, sticky="ew")

        toggle = ctk.CTkButton(
            field_row,
            text="Show",
            width=52,
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
        self.login_pass = self._labeled_password_entry(3, "Password", placeholder="Password")

        username = (prefill_username or self._prefill_username or load_last_username()).strip()
        if username:
            self.login_user.insert(0, username)
            self._prefill_username = ""

        login_btn = primary_button(
            self.form_frame, self.theme, text="Login", command=self._on_login, height=46
        )
        login_btn.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(20, 8))

        switch = ctk.CTkButton(
            self.form_frame,
            text="Don't have an account? Register",
            height=34,
            fg_color="transparent",
            hover_color=self.theme.c("primary_muted"),
            text_color=self.theme.c("primary"),
            font=font(self.theme, 13),
            command=self._show_register,
        )
        switch.grid(row=5, column=0, columnspan=2, sticky="ew")

        self.bind("<Return>", lambda _e: self._on_login())
        if username:
            self.login_pass.focus_set()
        else:
            self.login_user.focus_set()
        self._center_window(580, 460)

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
        ).grid(row=0, column=0, columnspan=2, pady=(8, 12))

        ctk.CTkLabel(
            self.form_frame,
            text="Registration Successful!",
            font=font(self.theme, 24, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="center",
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))

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
            wraplength=CARD_WIDTH - 72,
        ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 24))

        primary_button(
            self.form_frame,
            self.theme,
            text="Go to Login",
            command=lambda: self._show_login(prefill_username=username),
            height=46,
        ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        self.unbind("<Return>")
        self._center_window(580, 440)

    def _show_register(self) -> None:
        self._mode = "register"
        self.title(f"{APP_NAME} — Register")
        self._clear_form()
        self._set_status("")
        self._header("Create account", "Register as an employee to access Database Automation Pro")

        self.reg_name = self._labeled_entry(2, "Full name", placeholder="Your name")
        self.reg_user = self._labeled_entry(3, "Username", placeholder="Choose a username")
        self.reg_email = self._labeled_entry(4, "Email", placeholder="name@company.com (optional)")
        self.reg_pass = self._labeled_password_entry(5, "Password", placeholder="At least 6 characters")
        self.reg_confirm = self._labeled_password_entry(6, "Confirm", placeholder="Re-enter password")

        primary_button(
            self.form_frame, self.theme, text="Register", command=self._on_register, height=46
        ).grid(row=7, column=0, columnspan=2, sticky="ew", pady=(20, 8))

        ctk.CTkButton(
            self.form_frame,
            text="Already have an account? Sign in",
            height=34,
            fg_color="transparent",
            hover_color=self.theme.c("primary_muted"),
            text_color=self.theme.c("primary"),
            font=font(self.theme, 13),
            command=self._show_login,
        ).grid(row=8, column=0, columnspan=2, sticky="ew")

        self.bind("<Return>", lambda _e: self._on_register())
        self.reg_name.focus_set()
        self._center_window(580, 600)

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
        self.destroy()

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
        self.destroy()

    @staticmethod
    def show() -> EmployeeSession | None:
        dialog = AuthDialog()
        dialog.mainloop()
        return dialog.session
