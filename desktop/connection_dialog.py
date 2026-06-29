"""Enterprise-style startup connection dialog."""

from __future__ import annotations

import sys
import threading

import customtkinter as ctk

from backend.models.schemas import DatabaseType
from desktop.connection import ConnectionResult, DB_DEFAULTS, test_connection
from desktop.connection_audit import log_connection_attempt
from desktop.connection_store import SavedServer, load_saved_servers, remove_server, upsert_server
from desktop.theme import ThemeManager
from desktop.constants import APP_NAME
from desktop.db_icons import DB_ICON_META

# Layout tokens (defaults; overridden per theme in __init__)
SIDEBAR_WIDTH = 300
ENTRY_HEIGHT = 38
ICON_TILE = 88


class ConnectionDialog(ctk.CTkToplevel):
    """Database connection manager shown inside the main application session."""

    def __init__(self, parent: ctk.CTk | None = None, owner_username: str | None = None) -> None:
        super().__init__(parent)
        self._parent = parent
        self._owner_username = owner_username
        if parent is not None and hasattr(parent, "theme_manager"):
            self._theme = parent.theme_manager
        else:
            self._theme = ThemeManager()
        self._bind_dialog_colors()
        self.connection_result: ConnectionResult | None = None
        self._db_type_var = ctk.StringVar(value=DatabaseType.POSTGRESQL.value)
        self._connecting = False
        self._testing = False
        self._selected_saved: str | None = None
        self._saved_list_buttons: dict[str, ctk.CTkButton] = {}
        self._db_type_tiles: dict[str, ctk.CTkFrame] = {}
        self._password_visible = False
        self._linux_password_visible = False

        ctk.set_appearance_mode(self._theme.colors["appearance_mode"])
        ctk.set_default_color_theme(self._theme.colors["self.COLOR_theme"])

        self.title(f"{APP_NAME} — Connect")
        self.configure(fg_color=self.COLOR_BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if parent is not None:
            self._apply_embedded(parent)
        else:
            self.title(APP_NAME)
            self._apply_fullscreen()
        self._build_ui()
        self._refresh_saved_list()
        self._select_db_type(DatabaseType.POSTGRESQL)

    def _bind_dialog_colors(self) -> None:
        t = self._theme
        self.COLOR_BG = t.c("bg_content")
        self.COLOR_SIDEBAR = t.sc("bg_sidebar")
        self.COLOR_SIDEBAR_HOVER = t.sc("bg_hover")
        self.COLOR_SIDEBAR_ACTIVE = t.sc("nav_active")
        self.COLOR_CARD = t.c("bg_card")
        self.COLOR_CARD_BORDER = t.c("border")
        self.COLOR_TEXT = t.c("text_primary")
        self.COLOR_MUTED = t.c("text_muted")
        self.COLOR_PRIMARY = t.c("primary")
        self.COLOR_PRIMARY_HOVER = t.c("primary_hover")
        self.COLOR_SUCCESS = t.c("success")
        self.COLOR_ERROR = t.c("error")
        self.COLOR_WARNING = t.c("warning")

    def _apply_fullscreen(self) -> None:
        self.update_idletasks()
        if sys.platform == "win32":
            self.state("zoomed")
        elif sys.platform == "darwin":
            self.attributes("-zoomed", True)
        else:
            try:
                self.attributes("-zoomed", True)
            except Exception:
                w, h = self.winfo_screenwidth(), self.winfo_screenheight()
                self.geometry(f"{w}x{h}+0+0")

    def _apply_embedded(self, parent: ctk.CTk) -> None:
        """Open as a modal overlay within the main application window."""
        self.transient(parent)
        self.update_idletasks()
        parent.update_idletasks()
        width = max(parent.winfo_width(), 1024)
        height = max(parent.winfo_height(), 680)
        x = parent.winfo_rootx()
        y = parent.winfo_rooty()
        self.geometry(f"{width}x{height}+{x}+{y}")
        if sys.platform == "win32":
            self.state("zoomed")
        self.minsize(1024, 680)

    def _build_ui(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self._build_header()
        self._build_sidebar()
        self._build_main_panel()

        self.bind("<Return>", lambda _e: self._on_connect())

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, height=64, corner_radius=0, fg_color=self.COLOR_CARD, border_width=1, border_color=self.COLOR_CARD_BORDER)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.grid_propagate(False)

        ctk.CTkLabel(
            header,
            text=APP_NAME,
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=self.COLOR_TEXT,
        ).pack(side="left", padx=24, pady=16)

        ctk.CTkLabel(
            header,
            text="Configure and connect to your database",
            font=ctk.CTkFont(size=13),
            text_color=self.COLOR_MUTED,
        ).pack(side="left", padx=(4, 0), pady=16)

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, width=SIDEBAR_WIDTH, corner_radius=0, fg_color=self.COLOR_SIDEBAR)
        sidebar.grid(row=1, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        # Top: label + new connection
        top = ctk.CTkFrame(sidebar, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(20, 0))

        ctk.CTkLabel(
            top,
            text="SAVED CONNECTIONS",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#94a3b8",
        ).pack(anchor="w", pady=(0, 10))

        ctk.CTkButton(
            top,
            text="+  New Connection",
            height=36,
            fg_color=self.COLOR_SIDEBAR_HOVER,
            hover_color="#475569",
            anchor="center",
            command=self._new_connection,
        ).pack(fill="x", pady=(0, 12))

        # Middle: scrollable list (expands)
        self.saved_list = ctk.CTkScrollableFrame(sidebar, fg_color="transparent")
        self.saved_list.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        # Bottom: pinned action footer (always visible)
        footer = ctk.CTkFrame(
            sidebar,
            fg_color="#172033",
            corner_radius=0,
            border_width=1,
            border_color="#334155",
        )
        footer.pack(side="bottom", fill="x", padx=0, pady=0)

        footer_inner = ctk.CTkFrame(footer, fg_color="transparent")
        footer_inner.pack(fill="x", padx=16, pady=(14, 22))

        ctk.CTkLabel(
            footer_inner,
            text="CONNECTION ACTIONS",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#94a3b8",
        ).pack(anchor="w", pady=(0, 10))

        ctk.CTkButton(
            footer_inner,
            text="💾  Save Connection",
            height=38,
            corner_radius=8,
            fg_color=self.COLOR_SIDEBAR_HOVER,
            hover_color="#475569",
            anchor="center",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._save_server,
        ).pack(fill="x", pady=(0, 8))

        ctk.CTkButton(
            footer_inner,
            text="🗑  Delete Connection",
            height=38,
            corner_radius=8,
            fg_color="#b91c1c",
            hover_color="#dc2626",
            text_color="#ffffff",
            anchor="center",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._remove_saved,
        ).pack(fill="x")

    def _build_main_panel(self) -> None:
        main = ctk.CTkFrame(self, fg_color=self.COLOR_BG, corner_radius=0)
        main.grid(row=1, column=1, sticky="nsew", padx=24, pady=20)
        main.grid_rowconfigure(0, weight=1)
        main.grid_columnconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(main, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew")

        self._build_database_type_card(scroll)
        self._build_linux_access_card(scroll)
        self._build_details_card(scroll)

        footer = ctk.CTkFrame(main, fg_color="transparent")
        footer.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        footer.grid_columnconfigure(0, weight=1)

        self.status_frame = ctk.CTkFrame(footer, corner_radius=8, fg_color="#f8fafc", border_width=1, border_color=self.COLOR_CARD_BORDER)
        self.status_frame.grid(row=0, column=0, sticky="ew", pady=(0, 14))

        status_inner = ctk.CTkFrame(self.status_frame, fg_color="transparent")
        status_inner.pack(fill="x", padx=14, pady=10)

        self.status_indicator = ctk.CTkLabel(status_inner, text="●", font=ctk.CTkFont(size=14), text_color=self.COLOR_MUTED)
        self.status_indicator.pack(side="left", padx=(0, 8))

        self.status_label = ctk.CTkLabel(
            status_inner,
            text="Enter connection details, then test or connect.",
            font=ctk.CTkFont(size=12),
            text_color=self.COLOR_MUTED,
            anchor="w",
            justify="left",
            wraplength=700,
        )
        self.status_label.pack(side="left", fill="x", expand=True)

        actions = ctk.CTkFrame(footer, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="e")

        self.test_btn = ctk.CTkButton(
            actions,
            text="Test Connection",
            width=150,
            height=40,
            fg_color="transparent",
            border_width=1,
            border_color=self.COLOR_CARD_BORDER,
            text_color=self.COLOR_TEXT,
            hover_color="#e2e8f0",
            command=self._on_test,
        )
        self.test_btn.pack(side="left", padx=(0, 12))

        self.connect_btn = ctk.CTkButton(
            actions,
            text="Connect",
            width=180,
            height=44,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=self.COLOR_PRIMARY,
            hover_color=self.COLOR_PRIMARY_HOVER,
            command=self._on_connect,
        )
        self.connect_btn.pack(side="left")

    def _section_card(self, parent: ctk.CTkFrame, title: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            parent,
            corner_radius=12,
            fg_color=self.COLOR_CARD,
            border_width=1,
            border_color=self.COLOR_CARD_BORDER,
        )
        card.pack(fill="x", pady=(0, 16))

        ctk.CTkLabel(
            card,
            text=title.upper(),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=self.COLOR_MUTED,
        ).pack(anchor="w", padx=20, pady=(16, 12))

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=20, pady=(0, 20))
        return body

    def _create_icon_badge(self, parent: ctk.CTkFrame, db_type: DatabaseType, size: int = 40) -> ctk.CTkFrame:
        meta = DB_ICON_META[db_type]
        badge = ctk.CTkFrame(parent, width=size, height=size, corner_radius=8, fg_color=meta["color"])
        badge.pack_propagate(False)
        ctk.CTkLabel(
            badge,
            text=meta["abbrev"],
            font=ctk.CTkFont(size=max(11, size // 3), weight="bold"),
            text_color="white",
        ).place(relx=0.5, rely=0.5, anchor="center")
        return badge

    def _build_database_type_card(self, parent: ctk.CTkFrame) -> None:
        body = self._section_card(parent, "Database Type")

        tiles = ctk.CTkFrame(body, fg_color="transparent")
        tiles.pack(fill="x")

        for index, db_type in enumerate(DatabaseType):
            meta = DB_ICON_META[db_type]
            tile = ctk.CTkFrame(
                tiles,
                width=ICON_TILE,
                height=ICON_TILE + 20,
                corner_radius=10,
                fg_color="#f8fafc",
                border_width=2,
                border_color=self.COLOR_CARD_BORDER,
            )
            tile.grid(row=0, column=index, padx=(0, 10) if index < len(DatabaseType) - 1 else 0, sticky="n")
            tile.pack_propagate(False)
            tile.bind("<Button-1>", lambda _e, dt=db_type: self._select_db_type(dt))

            inner = ctk.CTkFrame(tile, fg_color="transparent")
            inner.place(relx=0.5, rely=0.5, anchor="center")
            inner.bind("<Button-1>", lambda _e, dt=db_type: self._select_db_type(dt))

            badge = self._create_icon_badge(inner, db_type, 40)
            badge.pack(pady=(0, 6))
            badge.bind("<Button-1>", lambda _e, dt=db_type: self._select_db_type(dt))

            label = ctk.CTkLabel(inner, text=meta["label"], font=ctk.CTkFont(size=11), text_color=self.COLOR_TEXT)
            label.pack()
            label.bind("<Button-1>", lambda _e, dt=db_type: self._select_db_type(dt))

            self._db_type_tiles[db_type.value] = tile

    def _toggle_password_visibility(self) -> None:
        self._password_visible = not self._password_visible
        self.password_entry.configure(show="" if self._password_visible else "*")
        self.password_toggle_btn.configure(text="🙈" if self._password_visible else "👁")

    def _build_details_card(self, parent: ctk.CTkFrame) -> None:
        body = self._section_card(parent, "Connection Details")
        body.grid_columnconfigure(1, weight=1)

        self._labeled_field(body, "Connection name", row=0)
        self.connection_name_entry = ctk.CTkEntry(
            body,
            height=ENTRY_HEIGHT,
            placeholder_text="e.g. Production PostgreSQL (auto-filled if empty)",
        )
        self.connection_name_entry.grid(row=0, column=1, sticky="ew", padx=(12, 0), pady=6)

        self._labeled_field(body, "Host", row=1)
        host_row = ctk.CTkFrame(body, fg_color="transparent")
        host_row.grid(row=1, column=1, sticky="ew", padx=(12, 0), pady=6)
        host_row.grid_columnconfigure(0, weight=1)
        self.host_entry = ctk.CTkEntry(host_row, height=ENTRY_HEIGHT, placeholder_text="localhost")
        self.host_entry.insert(0, "localhost")
        self.host_entry.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(host_row, text="Port", font=ctk.CTkFont(size=12), text_color=self.COLOR_MUTED).grid(row=0, column=1, padx=(12, 6))
        self.port_entry = ctk.CTkEntry(host_row, width=90, height=ENTRY_HEIGHT)
        self.port_entry.insert(0, "5432")
        self.port_entry.grid(row=0, column=2)

        self._labeled_field(body, "DB username", row=2)
        self.user_entry = ctk.CTkEntry(body, height=ENTRY_HEIGHT, placeholder_text="postgres")
        self.user_entry.insert(0, "postgres")
        self.user_entry.grid(row=2, column=1, sticky="ew", padx=(12, 0), pady=6)

        self._labeled_field(body, "DB password", row=3)
        password_row = ctk.CTkFrame(body, fg_color="transparent")
        password_row.grid(row=3, column=1, sticky="ew", padx=(12, 0), pady=6)
        password_row.grid_columnconfigure(0, weight=1)
        self.password_entry = ctk.CTkEntry(password_row, height=ENTRY_HEIGHT, show="*")
        self.password_entry.grid(row=0, column=0, sticky="ew")
        self.password_toggle_btn = ctk.CTkButton(
            password_row,
            text="👁",
            width=40,
            height=ENTRY_HEIGHT,
            fg_color="#f1f5f9",
            hover_color="#e2e8f0",
            text_color=self.COLOR_TEXT,
            border_width=1,
            border_color=self.COLOR_CARD_BORDER,
            font=ctk.CTkFont(size=16),
            command=self._toggle_password_visibility,
        )
        self.password_toggle_btn.grid(row=0, column=1, padx=(8, 0))

        self._labeled_field(body, "Database / Service", row=4)
        self.database_entry = ctk.CTkEntry(body, height=ENTRY_HEIGHT, placeholder_text="Default per engine")
        self.database_entry.grid(row=4, column=1, sticky="ew", padx=(12, 0), pady=6)

        self.auth_db_label = ctk.CTkLabel(
            body,
            text="Auth database",
            font=ctk.CTkFont(size=12),
            text_color=self.COLOR_TEXT,
            width=130,
            anchor="w",
        )
        self.auth_db_label.grid(row=5, column=0, sticky="w", pady=6)
        self.auth_database_entry = ctk.CTkEntry(
            body,
            height=ENTRY_HEIGHT,
            placeholder_text="admin (where MongoDB user was created)",
        )
        self.auth_database_entry.grid(row=5, column=1, sticky="ew", padx=(12, 0), pady=6)

        self.hint_label = ctk.CTkLabel(
            body,
            text="Oracle: use Service name (ORCL, XEPDB1). Other engines use database name defaults.",
            font=ctk.CTkFont(size=11),
            text_color=self.COLOR_MUTED,
            wraplength=560,
            justify="left",
        )
        self.hint_label.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _build_linux_access_card(self, parent: ctk.CTkFrame) -> None:
        body = self._section_card(parent, "Linux Server Access (SSH)")
        body.grid_columnconfigure(1, weight=1)

        self.ssh_enabled_var = ctk.BooleanVar(value=False)
        self.ssh_enabled_cb = ctk.CTkCheckBox(
            body,
            text="Connect through Linux server using SSH tunnel",
            variable=self.ssh_enabled_var,
            command=self._sync_ssh_fields,
        )
        self.ssh_enabled_cb.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        self._labeled_field(body, "Linux host", row=1)
        self.ssh_host_entry = ctk.CTkEntry(
            body,
            height=ENTRY_HEIGHT,
            placeholder_text="Same as database host if left blank",
        )
        self.ssh_host_entry.grid(row=1, column=1, sticky="ew", padx=(12, 0), pady=6)

        self._labeled_field(body, "SSH port", row=2)
        self.ssh_port_entry = ctk.CTkEntry(body, width=90, height=ENTRY_HEIGHT)
        self.ssh_port_entry.insert(0, "22")
        self.ssh_port_entry.grid(row=2, column=1, sticky="w", padx=(12, 0), pady=6)

        self._labeled_field(body, "Linux username", row=3)
        self.linux_user_entry = ctk.CTkEntry(
            body, height=ENTRY_HEIGHT, placeholder_text="e.g. ubuntu, root, techadmin"
        )
        self.linux_user_entry.grid(row=3, column=1, sticky="ew", padx=(12, 0), pady=6)

        self._labeled_field(body, "Linux password", row=4)
        linux_pw_row = ctk.CTkFrame(body, fg_color="transparent")
        linux_pw_row.grid(row=4, column=1, sticky="ew", padx=(12, 0), pady=6)
        linux_pw_row.grid_columnconfigure(0, weight=1)
        self.linux_password_entry = ctk.CTkEntry(linux_pw_row, height=ENTRY_HEIGHT, show="*")
        self.linux_password_entry.grid(row=0, column=0, sticky="ew")
        self.linux_password_toggle_btn = ctk.CTkButton(
            linux_pw_row,
            text="👁",
            width=40,
            height=ENTRY_HEIGHT,
            fg_color="#f1f5f9",
            hover_color="#e2e8f0",
            text_color=self.COLOR_TEXT,
            border_width=1,
            border_color=self.COLOR_CARD_BORDER,
            font=ctk.CTkFont(size=16),
            command=self._toggle_linux_password_visibility,
        )
        self.linux_password_toggle_btn.grid(row=0, column=1, padx=(8, 0))

        ctk.CTkLabel(
            body,
            text=(
                "Use the Linux login for the machine where the database runs. "
                "The app opens an SSH tunnel, then connects with the DB username/password below."
            ),
            font=ctk.CTkFont(size=11),
            text_color=self.COLOR_MUTED,
            wraplength=560,
            justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self._ssh_fields = (
            self.ssh_host_entry,
            self.ssh_port_entry,
            self.linux_user_entry,
            self.linux_password_entry,
            self.linux_password_toggle_btn,
        )
        self._sync_ssh_fields()

    def _toggle_linux_password_visibility(self) -> None:
        self._linux_password_visible = not self._linux_password_visible
        self.linux_password_entry.configure(show="" if self._linux_password_visible else "*")
        self.linux_password_toggle_btn.configure(
            text="🙈" if self._linux_password_visible else "👁"
        )

    def _sync_ssh_fields(self) -> None:
        enabled = self.ssh_enabled_var.get()
        state = "normal" if enabled else "disabled"
        for widget in self._ssh_fields:
            widget.configure(state=state)

    def _with_ssh_fields_enabled(self, callback):
        """Run callback while SSH inputs are editable (disabled entries may not read/write reliably)."""
        was_enabled = self.ssh_enabled_var.get()
        if not was_enabled:
            for widget in self._ssh_fields:
                widget.configure(state="normal")
        try:
            return callback()
        finally:
            if not was_enabled:
                self._sync_ssh_fields()

    def _linux_form_values(self) -> dict:
        def read() -> dict:
            ssh_port_raw = self.ssh_port_entry.get().strip() or "22"
            try:
                ssh_port = int(ssh_port_raw)
            except ValueError:
                ssh_port = 22
            ssh_host = self.ssh_host_entry.get().strip() or None
            return {
                "ssh_enabled": self.ssh_enabled_var.get(),
                "linux_username": self.linux_user_entry.get().strip(),
                "linux_password": self.linux_password_entry.get(),
                "ssh_host": ssh_host,
                "ssh_port": ssh_port,
            }

        return self._with_ssh_fields_enabled(read)

    def _labeled_field(self, parent: ctk.CTkFrame, text: str, row: int) -> None:
        ctk.CTkLabel(
            parent,
            text=text,
            font=ctk.CTkFont(size=12),
            text_color=self.COLOR_TEXT,
            width=130,
            anchor="w",
        ).grid(row=row, column=0, sticky="w", pady=6)

    def _find_saved_for_type(self, db_type: DatabaseType) -> SavedServer | None:
        for server in load_saved_servers(self._owner_username):
            if server.db_type == db_type.value:
                return server
        return None

    def _apply_db_type_tiles(self, db_type: DatabaseType) -> None:
        accent = DB_ICON_META[db_type]["color"]
        for value, tile in self._db_type_tiles.items():
            if value == db_type.value:
                tile.configure(border_color=accent, fg_color="#eff6ff")
            else:
                tile.configure(border_color=self.COLOR_CARD_BORDER, fg_color="#f8fafc")

    def _select_db_type(self, db_type: DatabaseType) -> None:
        self._db_type_var.set(db_type.value)
        self._apply_db_type_tiles(db_type)

        saved = self._find_saved_for_type(db_type)
        if saved:
            self._load_saved_server(saved)
            return

        self._selected_saved = None
        self._highlight_saved_selection()
        self._apply_engine_profile(db_type)
        self._update_mongodb_fields(db_type)
        self._set_status(f"{DB_ICON_META[db_type]['label']} defaults applied.", "info")

    def _apply_engine_profile(self, db_type: DatabaseType) -> None:
        """Reset the form to standard defaults for a database engine."""
        defaults = DB_DEFAULTS[db_type]
        self.connection_name_entry.delete(0, "end")
        self.host_entry.delete(0, "end")
        self.host_entry.insert(0, "localhost")
        self.port_entry.delete(0, "end")
        self.port_entry.insert(0, str(defaults["port"]))
        self.user_entry.delete(0, "end")
        self.user_entry.insert(0, str(defaults["username"]))
        self.password_entry.delete(0, "end")
        self.database_entry.delete(0, "end")
        self.database_entry.insert(0, str(defaults["database"]))
        self.auth_database_entry.delete(0, "end")
        self.ssh_enabled_var.set(False)
        self._sync_ssh_fields()

        def clear_ssh() -> None:
            self.ssh_host_entry.delete(0, "end")
            self.ssh_port_entry.delete(0, "end")
            self.ssh_port_entry.insert(0, "22")
            self.linux_user_entry.delete(0, "end")
            self.linux_password_entry.delete(0, "end")

        self._with_ssh_fields_enabled(clear_ssh)
        self._sync_ssh_fields()

    def _update_mongodb_fields(self, db_type: DatabaseType) -> None:
        is_mongo = db_type == DatabaseType.MONGODB
        if is_mongo:
            self.auth_db_label.grid()
            self.auth_database_entry.grid()
            self.hint_label.configure(
                text=(
                    "MongoDB: Database / Service is the database to use (e.g. db1). "
                    "Auth database is where the user was created — usually 'admin'."
                )
            )
        else:
            self.auth_db_label.grid_remove()
            self.auth_database_entry.grid_remove()
            self.hint_label.configure(
                text="Oracle: use Service name (ORCL, XEPDB1). Other engines use database name defaults."
            )

    def _refresh_saved_list(self) -> None:
        for widget in self.saved_list.winfo_children():
            widget.destroy()
        self._saved_list_buttons.clear()

        servers = load_saved_servers(self._owner_username)
        if not servers:
            ctk.CTkLabel(
                self.saved_list,
                text="No saved connections yet.\nFill the form and click Save.",
                font=ctk.CTkFont(size=12),
                text_color="#94a3b8",
                justify="left",
            ).pack(anchor="w", padx=8, pady=8)
            return

        for server in servers:
            db_type = DatabaseType(server.db_type)
            meta = DB_ICON_META[db_type]
            active = server.name == self._selected_saved

            item = ctk.CTkFrame(
                self.saved_list,
                corner_radius=8,
                fg_color=self.COLOR_SIDEBAR_ACTIVE if active else self.COLOR_SIDEBAR_HOVER,
            )
            item.pack(fill="x", pady=3)

            badge = ctk.CTkFrame(item, width=32, height=32, corner_radius=6, fg_color=meta["color"])
            badge.pack(side="left", padx=(10, 8), pady=10)
            badge.pack_propagate(False)
            badge_label = ctk.CTkLabel(
                badge,
                text=meta["abbrev"],
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color="white",
            )
            badge_label.place(relx=0.5, rely=0.5, anchor="center")

            text_col = ctk.CTkFrame(item, fg_color="transparent")
            text_col.pack(side="left", fill="x", expand=True, pady=10)

            name_label = ctk.CTkLabel(
                text_col,
                text=server.name,
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color="white",
                anchor="w",
            )
            name_label.pack(anchor="w")
            detail_label = ctk.CTkLabel(
                text_col,
                text=f"{meta['label']} · {server.host}",
                font=ctk.CTkFont(size=11),
                text_color="#cbd5e1",
                anchor="w",
            )
            detail_label.pack(anchor="w")

            self._wire_saved_item(item, server, root=True)
            self._saved_list_buttons[server.name] = item

    def _wire_saved_item(self, widget, server: SavedServer, *, root: bool = False) -> None:
        """Make every part of a saved-connection row respond to a single click."""

        def activate(_event=None) -> str:
            self._load_saved_server(server)
            return "break"

        try:
            widget.configure(cursor="hand2")
        except Exception:
            pass

        widget.bind("<Button-1>", activate)

        if root:
            def on_enter(_event=None) -> None:
                if self._selected_saved != server.name:
                    widget.configure(fg_color="#475569")

            def on_leave(_event=None) -> None:
                self._highlight_saved_selection()

            widget.bind("<Enter>", on_enter)
            widget.bind("<Leave>", on_leave)

        for child in widget.winfo_children():
            self._wire_saved_item(child, server, root=False)

    def _highlight_saved_selection(self) -> None:
        for name, item in self._saved_list_buttons.items():
            item.configure(fg_color=self.COLOR_SIDEBAR_ACTIVE if name == self._selected_saved else self.COLOR_SIDEBAR_HOVER)

    def _new_connection(self) -> None:
        self._selected_saved = None
        self._highlight_saved_selection()
        self.connection_name_entry.delete(0, "end")
        self.password_entry.delete(0, "end")
        self.linux_password_entry.delete(0, "end")
        self.linux_user_entry.delete(0, "end")
        self.ssh_host_entry.delete(0, "end")
        self.ssh_enabled_var.set(False)
        self.ssh_port_entry.delete(0, "end")
        self.ssh_port_entry.insert(0, "22")
        self._sync_ssh_fields()
        self._select_db_type(DatabaseType.POSTGRESQL)
        self._set_status("New connection profile.", "info")

    def _load_saved_server(self, server: SavedServer) -> None:
        self._selected_saved = server.name
        self._highlight_saved_selection()
        self.update_idletasks()
        self._fill_form(server)
        self._set_status(f"Loaded '{server.name}'.", "info")

    def _get_connection_name(self) -> str:
        return self.connection_name_entry.get().strip()

    def _get_form_values(self) -> ConnectionResult:
        db_type = DatabaseType(self._db_type_var.get())
        defaults = DB_DEFAULTS[db_type]
        host = self.host_entry.get().strip() or "localhost"
        database = self.database_entry.get().strip() or str(defaults["database"])
        name = self._get_connection_name() or self._selected_saved or f"{host} ({db_type.value})"
        linux = self._linux_form_values()
        return ConnectionResult(
            name=name,
            db_type=db_type,
            host=host,
            port=int(self.port_entry.get().strip() or defaults["port"]),
            username=self.user_entry.get().strip(),
            password=self.password_entry.get(),
            database=database,
            saved_name=self._selected_saved,
            auth_database=self.auth_database_entry.get().strip() or None,
            ssh_enabled=linux["ssh_enabled"],
            linux_username=linux["linux_username"],
            linux_password=linux["linux_password"],
            ssh_host=linux["ssh_host"],
            ssh_port=linux["ssh_port"],
        )

    def _fill_form(self, server: SavedServer) -> None:
        db_type = DatabaseType(server.db_type)
        self._db_type_var.set(db_type.value)
        self._apply_db_type_tiles(db_type)
        self._update_mongodb_fields(db_type)

        self.connection_name_entry.delete(0, "end")
        self.connection_name_entry.insert(0, server.name)
        self.host_entry.delete(0, "end")
        self.host_entry.insert(0, server.host)
        self.port_entry.delete(0, "end")
        self.port_entry.insert(0, str(server.port))
        self.user_entry.delete(0, "end")
        self.user_entry.insert(0, server.username)
        self.password_entry.delete(0, "end")
        self.password_entry.insert(0, server.password)
        self.database_entry.delete(0, "end")
        self.database_entry.insert(0, server.database)
        self.auth_database_entry.delete(0, "end")
        if server.auth_database:
            self.auth_database_entry.insert(0, server.auth_database)
        self.ssh_enabled_var.set(server.ssh_enabled)
        self._sync_ssh_fields()

        def fill_ssh() -> None:
            self.ssh_host_entry.delete(0, "end")
            if server.ssh_host:
                self.ssh_host_entry.insert(0, server.ssh_host)
            self.ssh_port_entry.delete(0, "end")
            self.ssh_port_entry.insert(0, str(server.ssh_port or 22))
            self.linux_user_entry.delete(0, "end")
            self.linux_user_entry.insert(0, server.linux_username)
            self.linux_password_entry.delete(0, "end")
            self.linux_password_entry.insert(0, server.linux_password)

        self._with_ssh_fields_enabled(fill_ssh)
        self._sync_ssh_fields()

    def _persist_current_connection(self) -> bool:
        self._ensure_connection_name()
        name = self._get_connection_name()
        if not name:
            self._set_status("Enter a connection name before saving.", "warning")
            return False

        connection = self._get_form_values()
        server = SavedServer(
            name=name,
            db_type=connection.db_type.value,
            host=connection.host,
            port=connection.port,
            username=connection.username,
            password=connection.password,
            database=connection.database,
            auth_database=connection.auth_database or "",
            ssh_enabled=connection.ssh_enabled,
            linux_username=connection.linux_username,
            linux_password=connection.linux_password,
            ssh_host=connection.ssh_host or "",
            ssh_port=connection.ssh_port,
        )
        upsert_server(server, self._owner_username)
        self._selected_saved = name
        self.connection_name_entry.delete(0, "end")
        self.connection_name_entry.insert(0, name)
        self._refresh_saved_list()
        return True

    def _save_server(self) -> None:
        name = self._get_connection_name()
        if self._persist_current_connection():
            self._set_status(f"Saved connection '{name or self._selected_saved}'.", "success")

    def _remove_saved(self) -> None:
        if not self._selected_saved:
            self._set_status("Select a saved connection to delete.", "warning")
            return
        name = self._selected_saved
        if remove_server(name, self._owner_username):
            self._selected_saved = None
            self._refresh_saved_list()
            self._new_connection()
            self._set_status(f"Deleted '{name}'.", "success")
        else:
            self._set_status(f"Could not delete '{name}'.", "error")

    def _set_status(self, message: str, level: str = "info") -> None:
        colors = {
            "info": (self.COLOR_MUTED, "#f8fafc"),
            "warning": (self.COLOR_WARNING, "#fffbeb"),
            "error": (self.COLOR_ERROR, "#fef2f2"),
            "success": (self.COLOR_SUCCESS, "#ecfdf5"),
        }
        text_color, bg = colors.get(level, colors["info"])
        self.status_indicator.configure(text_color=text_color)
        self.status_label.configure(text=message, text_color=text_color)
        self.status_frame.configure(fg_color=bg)

    def _set_busy(self, *, testing: bool = False, connecting: bool = False) -> None:
        self._testing = testing
        self._connecting = connecting
        busy = testing or connecting
        state = "disabled" if busy else "normal"
        self.test_btn.configure(state=state, text="Testing..." if testing else "Test Connection")
        self.connect_btn.configure(state=state, text="Connecting..." if connecting else "Connect")

    def _ensure_connection_name(self) -> None:
        if self._get_connection_name():
            return
        host = self.host_entry.get().strip() or "localhost"
        database = self.database_entry.get().strip()
        db_type = DatabaseType(self._db_type_var.get())
        auto_name = f"{database}@{host}" if database else f"{db_type.value}@{host}"
        self.connection_name_entry.delete(0, "end")
        self.connection_name_entry.insert(0, auto_name)

    def _validate_form(self) -> ConnectionResult | None:
        host = self.host_entry.get().strip()
        if not host:
            self._set_status("Host is required.", "error")
            self.host_entry.focus_set()
            return None

        username = self.user_entry.get().strip()
        if not username:
            self._set_status("Username is required.", "error")
            self.user_entry.focus_set()
            return None

        database = self.database_entry.get().strip()
        if not database:
            defaults = DB_DEFAULTS[DatabaseType(self._db_type_var.get())]
            database = str(defaults["database"])
            self.database_entry.delete(0, "end")
            self.database_entry.insert(0, database)

        self._ensure_connection_name()

        linux = self._linux_form_values()
        if linux["ssh_enabled"] and not linux["linux_username"]:
            self._set_status("Linux username is required when SSH tunnel is enabled.", "error")
            self.linux_user_entry.focus_set()
            return None

        try:
            return self._get_form_values()
        except ValueError:
            self._set_status("Port must be a valid number.", "error")
            self.port_entry.focus_set()
            return None

    def _run_connection_test(self, connection: ConnectionResult, *, on_success_connect: bool) -> None:
        def task() -> None:
            ok, message = test_connection(connection)
            self.after(0, lambda: self._on_test_complete(ok, message, connection, on_success_connect))

        threading.Thread(target=task, daemon=True).start()

    def _on_test(self) -> None:
        if self._testing or self._connecting:
            return
        connection = self._validate_form()
        if not connection:
            return
        self._set_busy(testing=True)
        self._set_status("Testing connection...", "info")
        self._run_connection_test(connection, on_success_connect=False)

    def _on_connect(self) -> None:
        if self._testing or self._connecting:
            return
        connection = self._validate_form()
        if not connection:
            return
        self._set_busy(connecting=True)
        self._set_status("Connecting...", "info")
        self._run_connection_test(connection, on_success_connect=True)

    def _on_test_complete(
        self,
        ok: bool,
        message: str,
        connection: ConnectionResult,
        on_success_connect: bool,
    ) -> None:
        self._set_busy(testing=False, connecting=False)
        if ok:
            self._set_status(message, "success")
            if on_success_connect:
                self._persist_current_connection()
                connection.saved_name = self._selected_saved
                self.connection_result = connection
                self.after(300, self.destroy)
        else:
            self._set_status(message, "error")
            log_connection_attempt(
                self._owner_username,
                connection,
                success=False,
                error=message,
            )

    def _on_close(self) -> None:
        self.connection_result = None
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    @staticmethod
    def show(
        parent: ctk.CTk | None = None,
        owner_username: str | None = None,
    ) -> ConnectionResult | None:
        dialog = ConnectionDialog(parent, owner_username=owner_username)
        if parent is not None:
            dialog.grab_set()
            dialog.focus_force()
            parent.wait_window(dialog)
        else:
            dialog.mainloop()
        return dialog.connection_result
