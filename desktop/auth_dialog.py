"""Employee login and registration shown before database connection."""

from __future__ import annotations

import customtkinter as ctk

from desktop.constants import APP_NAME
from desktop.employee_store import EmployeeSession, authenticate_employee, register_employee
from desktop.theme import load_last_username, save_last_username

# Match connection dialog / screenshot styling
COLOR_BG = "#1e293b"
COLOR_CARD = "#ffffff"
COLOR_CARD_BORDER = "#dde3ec"
COLOR_TEXT = "#0f172a"
COLOR_MUTED = "#64748b"
COLOR_PRIMARY = "#2563eb"
COLOR_PRIMARY_HOVER = "#1d4ed8"
COLOR_ERROR = "#dc2626"
COLOR_SUCCESS = "#059669"

ENTRY_HEIGHT = 40
CARD_WIDTH = 520


class AuthDialog(ctk.CTk):
    """Login and registration gate before the connection screen."""

    def __init__(self) -> None:
        super().__init__()
        self.session: EmployeeSession | None = None
        self._mode = "login"

        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        self.title(f"{APP_NAME} — Login")
        self.configure(fg_color=COLOR_BG)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_shell()
        self._show_login()
        self._center_window(560, 480)

    def _center_window(self, width: int, height: int) -> None:
        self.update_idletasks()
        x = (self.winfo_screenwidth() - width) // 2
        y = (self.winfo_screenheight() - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build_shell(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.card = ctk.CTkFrame(
            self,
            width=CARD_WIDTH,
            fg_color=COLOR_CARD,
            corner_radius=12,
            border_width=1,
            border_color=COLOR_CARD_BORDER,
        )
        self.card.grid(row=0, column=0, padx=24, pady=24)
        self.card.grid_columnconfigure(1, weight=1)

        self.body = ctk.CTkFrame(self.card, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=36, pady=32)
        self.body.grid_columnconfigure(1, weight=1)

        self.form_frame = ctk.CTkFrame(self.body, fg_color="transparent")
        self.form_frame.pack(fill="both", expand=True)
        self.form_frame.grid_columnconfigure(1, weight=1)

        self.status_label = ctk.CTkLabel(
            self.body,
            text="",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_MUTED,
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
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=COLOR_TEXT,
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))

        ctk.CTkLabel(
            self.form_frame,
            text=subtitle,
            font=ctk.CTkFont(size=13),
            text_color=COLOR_MUTED,
            anchor="w",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 24))

    def _labeled_entry(
        self,
        row: int,
        label: str,
        *,
        placeholder: str,
    ) -> ctk.CTkEntry:
        ctk.CTkLabel(
            self.form_frame,
            text=label,
            font=ctk.CTkFont(size=13),
            text_color=COLOR_TEXT,
            anchor="w",
        ).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=8)

        entry = ctk.CTkEntry(
            self.form_frame,
            height=ENTRY_HEIGHT,
            placeholder_text=placeholder,
        )
        entry.grid(row=row, column=1, sticky="ew", pady=8)
        return entry

    def _labeled_password_entry(
        self,
        row: int,
        label: str,
        *,
        placeholder: str,
    ) -> ctk.CTkEntry:
        ctk.CTkLabel(
            self.form_frame,
            text=label,
            font=ctk.CTkFont(size=13),
            text_color=COLOR_TEXT,
            anchor="w",
        ).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=8)

        field_row = ctk.CTkFrame(self.form_frame, fg_color="transparent")
        field_row.grid(row=row, column=1, sticky="ew", pady=8)
        field_row.grid_columnconfigure(0, weight=1)

        entry = ctk.CTkEntry(
            field_row,
            height=ENTRY_HEIGHT,
            placeholder_text=placeholder,
            show="•",
        )
        entry.grid(row=0, column=0, sticky="ew")

        toggle = ctk.CTkButton(
            field_row,
            text="👁",
            width=40,
            height=ENTRY_HEIGHT,
            fg_color="#f1f5f9",
            hover_color="#e2e8f0",
            text_color=COLOR_TEXT,
            border_width=1,
            border_color=COLOR_CARD_BORDER,
            font=ctk.CTkFont(size=16),
        )
        toggle.configure(command=lambda: self._toggle_password_visibility(entry, toggle))
        toggle.grid(row=0, column=1, padx=(8, 0))
        return entry

    def _toggle_password_visibility(
        self, entry: ctk.CTkEntry, button: ctk.CTkButton
    ) -> None:
        visible = entry.cget("show") == ""
        entry.configure(show="" if not visible else "•")
        button.configure(text="🙈" if not visible else "👁")

    def _set_status(self, message: str, level: str = "info") -> None:
        colors = {
            "info": COLOR_MUTED,
            "error": COLOR_ERROR,
            "success": COLOR_SUCCESS,
        }
        self.status_label.configure(
            text=message,
            text_color=colors.get(level, COLOR_MUTED),
        )

    def _show_login(self) -> None:
        self._mode = "login"
        self.title(f"{APP_NAME} — Login")
        self._clear_form()
        self._set_status("")
        self._header(
            APP_NAME,
            "Sign in to manage remote database automation",
        )

        self.login_user = self._labeled_entry(2, "Username", placeholder="Username")
        self.login_pass = self._labeled_password_entry(3, "Password", placeholder="Password")

        last_user = load_last_username()
        if last_user:
            self.login_user.insert(0, last_user)

        login_btn = ctk.CTkButton(
            self.form_frame,
            text="Login",
            height=44,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_PRIMARY_HOVER,
            command=self._on_login,
        )
        login_btn.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(20, 8))

        switch = ctk.CTkButton(
            self.form_frame,
            text="Don't have an account? Register",
            height=32,
            fg_color="transparent",
            hover_color="#eff6ff",
            text_color=COLOR_PRIMARY,
            font=ctk.CTkFont(size=13),
            command=self._show_register,
        )
        switch.grid(row=5, column=0, columnspan=2, sticky="ew")

        self.bind("<Return>", lambda _e: self._on_login())
        if last_user:
            self.login_pass.focus_set()
        else:
            self.login_user.focus_set()
        self._center_window(560, 440)

    def _show_register(self) -> None:
        self._mode = "register"
        self.title(f"{APP_NAME} — Register")
        self._clear_form()
        self._set_status("")
        self._header(
            "Create account",
            "Register as an employee to access Database Automation Pro",
        )

        self.reg_name = self._labeled_entry(2, "Full name", placeholder="Your name")
        self.reg_user = self._labeled_entry(3, "Username", placeholder="Choose a username")
        self.reg_email = self._labeled_entry(4, "Email", placeholder="name@company.com (optional)")
        self.reg_pass = self._labeled_password_entry(
            5, "Password", placeholder="At least 6 characters"
        )
        self.reg_confirm = self._labeled_password_entry(
            6, "Confirm", placeholder="Re-enter password"
        )

        register_btn = ctk.CTkButton(
            self.form_frame,
            text="Register",
            height=44,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_PRIMARY_HOVER,
            command=self._on_register,
        )
        register_btn.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(20, 8))

        switch = ctk.CTkButton(
            self.form_frame,
            text="Already have an account? Sign in",
            height=32,
            fg_color="transparent",
            hover_color="#eff6ff",
            text_color=COLOR_PRIMARY,
            font=ctk.CTkFont(size=13),
            command=self._show_login,
        )
        switch.grid(row=8, column=0, columnspan=2, sticky="ew")

        self.bind("<Return>", lambda _e: self._on_register())
        self.reg_name.focus_set()
        self._center_window(560, 580)

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
            session = authenticate_employee(username, password)
        except ValueError as exc:
            self._set_status(str(exc), "error")
            return
        except Exception as exc:
            self._set_status(f"Registration failed: {exc}", "error")
            return

        if session:
            save_last_username(session.username)
            self.session = session
            self.destroy()
            return

        self._set_status("Account created. Please sign in.", "success")
        save_last_username(username)
        self.after(300, self._show_login)

    def _on_close(self) -> None:
        self.session = None
        self.destroy()

    @staticmethod
    def show() -> EmployeeSession | None:
        dialog = AuthDialog()
        dialog.mainloop()
        return dialog.session
