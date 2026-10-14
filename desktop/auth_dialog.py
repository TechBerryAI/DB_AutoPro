"""Employee login and registration shown before the main application."""

from __future__ import annotations

import customtkinter as ctk

from desktop.constants import APP_NAME
from desktop.design_system import font, ghost_button, primary_button, secondary_button
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
from desktop.window_icon import prepare_ctk_shutdown, schedule_window_icon

ENTRY_HEIGHT = 36
BODY_PAD_X = 28
BODY_PAD_Y = 24
FORM_WIDTH = 360
_MODE_SIZES = {
    "login": (420, 520),
    "register": (420, 680),
    "success": (420, 400),
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

        self.title(f"{APP_NAME} — Sign in")
        self.configure(fg_color=self.theme.c("bg_app"))
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        schedule_window_icon(self)

        self._build_shell()
        self._show_login()
        self.after(20, self._fit_window)

    def _fit_window(self) -> None:
        if not self.winfo_exists():
            return
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width, height = _MODE_SIZES.get(self._mode, (420, 520))
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

        # Surface card — 1px border, sm radius; no decorative accent strip.
        self.card = ctk.CTkFrame(
            outer,
            fg_color=self.theme.c("surface"),
            corner_radius=self.theme.radius("sm"),
            border_width=1,
            border_color=self.theme.c("border"),
            width=FORM_WIDTH + BODY_PAD_X * 2,
        )
        self.card.grid(row=0, column=0, padx=28, pady=28)
        self.card.grid_propagate(True)

        self.form_frame = ctk.CTkFrame(self.card, fg_color="transparent")
        self.form_frame.pack(fill="both", expand=True, padx=BODY_PAD_X, pady=(BODY_PAD_Y, 8))

        self.status_label = ctk.CTkLabel(
            self.card,
            text="",
            font=font(self.theme, 12),
            text_color=self.theme.c("text_muted"),
            wraplength=FORM_WIDTH,
            justify="left",
            anchor="w",
        )
        self.status_label.pack(fill="x", padx=BODY_PAD_X, pady=(0, BODY_PAD_Y))

    def _clear_form(self) -> None:
        for child in list(self.form_frame.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass

    def _brand_mark(self) -> None:
        """Compact product mark — matches slim sidebar brand."""
        ctk.CTkLabel(
            self.form_frame,
            text="DB Automation Pro",
            font=font(self.theme, 12, weight="bold"),
            text_color=self.theme.c("primary"),
            anchor="w",
        ).pack(fill="x", pady=(0, 16))

    def _header(self, title: str, subtitle: str) -> None:
        ctk.CTkLabel(
            self.form_frame,
            text=title,
            font=font(self.theme, 20, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
            wraplength=FORM_WIDTH,
            justify="left",
        ).pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(
            self.form_frame,
            text=subtitle,
            font=font(self.theme, 12),
            text_color=self.theme.c("text_muted"),
            anchor="w",
            wraplength=FORM_WIDTH,
            justify="left",
        ).pack(fill="x", pady=(0, 18))

    def _field_label(self, text: str) -> None:
        ctk.CTkLabel(
            self.form_frame,
            text=text,
            font=font(self.theme, 11),
            text_color=self.theme.c("text_secondary"),
            anchor="w",
        ).pack(fill="x", pady=(8, 4))

    def _entry(self, *, placeholder: str, show: str | None = None) -> ctk.CTkEntry:
        kwargs: dict = {
            "width": FORM_WIDTH,
            "height": ENTRY_HEIGHT,
            "placeholder_text": placeholder,
            "corner_radius": self.theme.radius("sm"),
            "fg_color": self.theme.c("bg_input"),
            "border_color": self.theme.c("border"),
            "border_width": 1,
            "text_color": self.theme.c("text_primary"),
            "placeholder_text_color": self.theme.c("text_muted"),
            "font": font(self.theme, 13),
        }
        if show is not None:
            kwargs["show"] = show
        entry = ctk.CTkEntry(self.form_frame, **kwargs)
        entry.pack(fill="x")
        return entry

    def _labeled_entry(self, label: str, *, placeholder: str) -> ctk.CTkEntry:
        self._field_label(label)
        return self._entry(placeholder=placeholder)

    def _labeled_password_entry(
        self, label: str, *, placeholder: str
    ) -> tuple[ctk.CTkEntry, ctk.CTkButton]:
        self._field_label(label)

        row = ctk.CTkFrame(self.form_frame, fg_color="transparent")
        row.pack(fill="x")

        kwargs: dict = {
            "height": ENTRY_HEIGHT,
            "placeholder_text": placeholder,
            "corner_radius": self.theme.radius("sm"),
            "fg_color": self.theme.c("bg_input"),
            "border_color": self.theme.c("border"),
            "border_width": 1,
            "text_color": self.theme.c("text_primary"),
            "placeholder_text_color": self.theme.c("text_muted"),
            "font": font(self.theme, 13),
            "show": "•",
        }
        entry = ctk.CTkEntry(row, **kwargs)
        entry.pack(side="left", fill="x", expand=True)

        toggle = secondary_button(
            row,
            self.theme,
            text="Show",
            height=ENTRY_HEIGHT,
            width=64,
            command=lambda: self._toggle_password_visibility(entry, toggle),
        )
        toggle.configure(
            corner_radius=self.theme.radius("sm"),
            font=font(self.theme, 11),
            text_color=self.theme.c("text_secondary"),
        )
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

    def _add_back_row(self) -> None:
        back = ghost_button(
            self.form_frame,
            self.theme,
            text="←  Back to sign in",
            command=self._show_login,
            height=28,
        )
        back.configure(
            text_color=self.theme.c("text_secondary"),
            hover_color=self.theme.c("bg_card_hover"),
            font=font(self.theme, 12),
            anchor="w",
        )
        back.pack(fill="x", pady=(0, 8))

    def _show_login(self, *, prefill_username: str | None = None) -> None:
        self._mode = "login"
        self.title(f"{APP_NAME} — Sign in")
        self._clear_form()
        self._set_status("")

        self._brand_mark()
        self._header("Sign in", "Use your employee account to open the operations console.")

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
        self.login_pass.configure(show="•")
        login_pass_toggle.configure(text="Show")

        self._remember_var = ctk.BooleanVar(value=load_remember_password())
        ctk.CTkCheckBox(
            self.form_frame,
            text="Remember password on this workstation",
            variable=self._remember_var,
            font=font(self.theme, 12),
            text_color=self.theme.c("text_secondary"),
            fg_color=self.theme.c("primary"),
            hover_color=self.theme.c("primary_hover"),
            border_color=self.theme.c("border"),
            checkmark_color=self.theme.c("text_on_primary"),
            corner_radius=4,
            command=self._on_remember_toggled,
        ).pack(anchor="w", pady=(14, 0))

        self.login_user.bind("<KeyRelease>", lambda _e: self._persist_login_draft())
        self.login_pass.bind("<KeyRelease>", lambda _e: self._persist_login_draft())
        self.login_pass.bind("<FocusOut>", lambda _e: self._persist_login_draft())

        self.login_btn = primary_button(
            self.form_frame,
            self.theme,
            text="Sign in",
            command=self._on_login,
            height=36,
            width=FORM_WIDTH,
        )
        self.login_btn.configure(corner_radius=self.theme.radius("sm"))
        self.login_btn.pack(fill="x", pady=(20, 8))

        create = ghost_button(
            self.form_frame,
            self.theme,
            text="New here? Create an account",
            command=self._show_register,
            height=32,
        )
        create.configure(
            text_color=self.theme.c("text_secondary"),
            hover_color=self.theme.c("bg_card_hover"),
            font=font(self.theme, 12),
        )
        create.pack(fill="x", pady=(2, 0))

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

        self._add_back_row()
        self._brand_mark()
        self._header(
            "Create account",
            "Register an employee account for this workstation.",
        )

        self.reg_name = self._labeled_entry("Full name", placeholder="Your name")
        self.reg_user = self._labeled_entry("Username", placeholder="Choose a username")
        self.reg_email = self._labeled_entry("Email", placeholder="name@company.com (optional)")
        self.reg_pass, _ = self._labeled_password_entry(
            "Password", placeholder="At least 6 characters"
        )
        self.reg_confirm, _ = self._labeled_password_entry(
            "Confirm password", placeholder="Re-enter password"
        )

        register_btn = primary_button(
            self.form_frame,
            self.theme,
            text="Create account",
            command=self._on_register,
            height=36,
            width=FORM_WIDTH,
        )
        register_btn.configure(corner_radius=self.theme.radius("sm"))
        register_btn.pack(fill="x", pady=(18, 8))

        back = ghost_button(
            self.form_frame,
            self.theme,
            text="←  Back to sign in",
            command=self._show_login,
            height=32,
        )
        back.configure(
            text_color=self.theme.c("text_secondary"),
            hover_color=self.theme.c("bg_card_hover"),
            font=font(self.theme, 12),
        )
        back.pack(fill="x", pady=(2, 4))

        self.bind("<Return>", lambda _e: self._on_register())
        self.reg_name.focus_set()
        self.after(20, self._fit_window)

    def _show_registration_success(self, full_name: str, username: str) -> None:
        self._mode = "success"
        self.title(f"{APP_NAME} — Registration Successful")
        self._clear_form()
        self._set_status("")

        self._brand_mark()

        badge = ctk.CTkFrame(
            self.form_frame,
            fg_color=self.theme.c("success_muted"),
            corner_radius=self.theme.radius("sm"),
            border_width=1,
            border_color=self.theme.c("border"),
        )
        badge.pack(fill="x", pady=(4, 16))
        ctk.CTkLabel(
            badge,
            text="●  Account created",
            font=font(self.theme, 12, weight="bold"),
            text_color=self.theme.c("success"),
            anchor="w",
        ).pack(anchor="w", padx=12, pady=10)

        ctk.CTkLabel(
            self.form_frame,
            text="You're ready to sign in",
            font=font(self.theme, 18, weight="bold"),
            text_color=self.theme.c("text_primary"),
            anchor="w",
        ).pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(
            self.form_frame,
            text=(
                f"{full_name} ({username}) is registered on this workstation.\n"
                "Sign in to open the operations console."
            ),
            font=font(self.theme, 12),
            text_color=self.theme.c("text_muted"),
            justify="left",
            wraplength=FORM_WIDTH,
            anchor="w",
        ).pack(fill="x", pady=(0, 20))

        done = primary_button(
            self.form_frame,
            self.theme,
            text="Back to sign in",
            command=lambda: self._show_login(prefill_username=username),
            height=36,
            width=FORM_WIDTH,
        )
        done.configure(corner_radius=self.theme.radius("sm"))
        done.pack(fill="x")

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
                error = f"Sign in failed: {exc}"

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
                    f"'{username}' already exists. Returning to sign in…",
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

    def destroy(self) -> None:
        prepare_ctk_shutdown(self)
        super().destroy()

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
