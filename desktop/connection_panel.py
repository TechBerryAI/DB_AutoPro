"""Embeddable, theme-aware database connection panel for the Servers view."""

from __future__ import annotations

import threading
from typing import Callable

import customtkinter as ctk

from backend.models.schemas import DatabaseType
from desktop.connection import ConnectionResult, DB_DEFAULTS, test_connection
from desktop.connection_store import SavedServer, load_saved_servers, remove_server, upsert_server
from desktop.db_icons import DB_ICON_META
from desktop.ui_helpers import confirm
from desktop.theme import ThemeManager, themed_scrollable_frame

SIDEBAR_WIDTH = 280
ENTRY_HEIGHT = 36
ICON_TILE = 88
SHOW_SAVED_CONNECTIONS_SIDEBAR = False

# Saved-connection grouping order in the Servers sidebar (3rd panel).
SERVER_SUBCATEGORIES: list[tuple[DatabaseType, str]] = [
    (DatabaseType.POSTGRESQL, "PostgreSQL"),
    (DatabaseType.MYSQL, "MySQL"),
    (DatabaseType.MONGODB, "MongoDB"),
    (DatabaseType.MSSQL, "MSSQL"),
    (DatabaseType.ORACLE, "Oracle"),
]


class ConnectionPanel(ctk.CTkFrame):
    """Inline connection manager — saved targets sidebar + configuration form."""

    def __init__(
        self,
        master,
        theme: ThemeManager,
        *,
        on_connect_success: Callable[[ConnectionResult], None] | None = None,
        disconnect_callback: Callable[[], None] | None = None,
        on_saved_servers_changed: Callable[[str | None], None] | None = None,
        log_callback: Callable[[str, str], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color=theme.c("bg_content"), **kwargs)
        self.theme = theme
        self.on_connect_success = on_connect_success
        self.disconnect_callback = disconnect_callback
        self.on_saved_servers_changed = on_saved_servers_changed
        self.log_callback = log_callback
        self._db_type_var = ctk.StringVar(value=DatabaseType.POSTGRESQL.value)
        self._connecting = False
        self._testing = False
        self._selected_saved: str | None = None
        self._saved_list_buttons: dict[str, ctk.CTkFrame] = {}
        self._db_type_tiles: dict[str, ctk.CTkFrame] = {}
        self._password_visible = False
        self._linux_password_visible = False
        self._servers_filter: str = "all"

        self.grid_rowconfigure(0, weight=1)
        if SHOW_SAVED_CONNECTIONS_SIDEBAR:
            self.grid_columnconfigure(1, weight=1)
            self._build_sidebar()
        else:
            self.grid_columnconfigure(0, weight=1)
            self._init_sidebar_stubs()
        self._build_main_panel()
        self.bind("<Return>", lambda _e: self._on_connect())
        if SHOW_SAVED_CONNECTIONS_SIDEBAR:
            self._refresh_saved_list()
        # Initial blank form — do not notify parent (would clear active saved selection on view rebuild).
        self._select_db_type(DatabaseType.POSTGRESQL, load_saved=False, notify=False)

    def _init_sidebar_stubs(self) -> None:
        """Hidden placeholders so save/load logic keeps working without the sidebar UI."""
        self.saved_list = ctk.CTkFrame(self)
        self._session_frame = ctk.CTkFrame(self)
        self._session_label = ctk.CTkLabel(self._session_frame, text="")
        self._disconnect_btn = ctk.CTkButton(
            self._session_frame,
            text="Disconnect",
            command=self._on_disconnect,
        )
        self._actions_footer = ctk.CTkFrame(self)

    def _sidebar_bg(self) -> str:
        return self.theme.sc("bg_sidebar") if self.theme.is_light else self.theme.c("bg_sidebar")

    def _sidebar_item_bg(self) -> str:
        return self.theme.sc("sidebar_bg_card") if self.theme.is_light else self.theme.c("bg_elevated")

    def _sidebar_muted(self) -> str:
        return self.theme.sc("text_muted") if self.theme.is_light else self.theme.c("text_muted")

    def _sidebar_secondary(self) -> str:
        return self.theme.sc("text_secondary") if self.theme.is_light else self.theme.c("text_secondary")

    def _sidebar_text(self) -> str:
        return self.theme.sc("text_primary") if self.theme.is_light else self.theme.c("text_primary")

    def _sidebar_hover(self) -> str:
        return self.theme.sc("sidebar_bg_hover") if self.theme.is_light else self.theme.c("bg_card_hover")

    def _sidebar_footer_bg(self) -> str:
        return self.theme.sc("bg_list") if self.theme.is_light else self.theme.c("bg_elevated")

    def _sidebar_border(self) -> str:
        return self.theme.sc("border") if self.theme.is_light else self.theme.c("border_subtle")

    def _log(self, message: str, level: str = "INFO") -> None:
        if self.log_callback:
            self.log_callback(message, level)

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, width=SIDEBAR_WIDTH, corner_radius=0, fg_color=self._sidebar_bg())
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        body = ctk.CTkFrame(sidebar, fg_color="transparent")
        body.pack(fill="both", expand=True)

        # Top: label + new connection
        top = ctk.CTkFrame(body, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(20, 0))

        ctk.CTkLabel(
            top,
            text="SAVED CONNECTIONS",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=self._sidebar_muted(),
        ).pack(anchor="w", pady=(0, 10))

        ctk.CTkButton(
            top,
            text="+  New Connection",
            height=36,
            fg_color=self._sidebar_item_bg(),
            hover_color=self._sidebar_hover(),
            anchor="center",
            command=self._new_connection,
        ).pack(fill="x", pady=(0, 12))

        # Server rows — height follows content (scrolls when many servers)
        self.saved_list = themed_scrollable_frame(body, self.theme, height=120)
        self.saved_list.pack(fill="x", padx=12, pady=(0, 4))

        # Active session strip (shown when connected)
        self._session_frame = ctk.CTkFrame(
            body,
            fg_color=self._sidebar_item_bg(),
            corner_radius=8,
            border_width=1,
            border_color=self._sidebar_border(),
        )
        session_inner = ctk.CTkFrame(self._session_frame, fg_color="transparent")
        session_inner.pack(fill="x", padx=12, pady=10)

        self._session_label = ctk.CTkLabel(
            session_inner,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=self._sidebar_secondary(),
            anchor="w",
            justify="left",
            wraplength=SIDEBAR_WIDTH - 48,
        )
        self._session_label.pack(fill="x", pady=(0, 8))

        self._disconnect_btn = ctk.CTkButton(
            session_inner,
            text="Disconnect",
            height=32,
            fg_color="#7f1d1d",
            hover_color="#ef4444",
            text_color="#fecaca",
            command=self._on_disconnect,
        )
        self._disconnect_btn.pack(fill="x")

        # Connection actions directly under server rows
        self._actions_footer = ctk.CTkFrame(
            body,
            fg_color=self._sidebar_footer_bg(),
            corner_radius=0,
            border_width=1,
            border_color=self._sidebar_border(),
        )
        self._actions_footer.pack(fill="x", padx=0, pady=(8, 0))

        footer_inner = ctk.CTkFrame(self._actions_footer, fg_color="transparent")
        footer_inner.pack(fill="x", padx=16, pady=(14, 16))

        ctk.CTkLabel(
            footer_inner,
            text="CONNECTION ACTIONS",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=self._sidebar_muted(),
        ).pack(anchor="w", pady=(0, 10))

        ctk.CTkButton(
            footer_inner,
            text="💾  Save Connection",
            height=38,
            corner_radius=8,
            fg_color=self._sidebar_item_bg(),
            hover_color=self._sidebar_hover(),
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

        ctk.CTkFrame(body, fg_color="transparent").pack(fill="both", expand=True)

    def _build_main_panel(self) -> None:
        main = ctk.CTkFrame(self, fg_color=self.theme.c("bg_content"), corner_radius=0)
        main.grid(
            row=0,
            column=1 if SHOW_SAVED_CONNECTIONS_SIDEBAR else 0,
            sticky="nsew",
            padx=24,
            pady=(12, 0),
        )
        main.grid_columnconfigure(0, weight=1)

        # Pack the action block first so it stays at the bottom. A scrollable
        # frame in the same grid steals the cell and leaves a gap under the buttons.
        footer = ctk.CTkFrame(main, fg_color="transparent")
        footer.pack(side="bottom", fill="x", pady=(6, 0))
        footer.grid_columnconfigure(0, weight=1)

        scroll = themed_scrollable_frame(main, self.theme, height=120)
        scroll.pack(fill="both", expand=True)
        self._form_scroll = scroll

        self._build_database_type_card(scroll)
        self._build_linux_access_card(scroll)
        self._build_details_card(scroll)

        if not SHOW_SAVED_CONNECTIONS_SIDEBAR:
            self._main_session_bar = ctk.CTkFrame(
                footer,
                fg_color=self.theme.c("bg_card_hover"),
                corner_radius=8,
                border_width=1,
                border_color=self.theme.c("border"),
            )
            session_row = ctk.CTkFrame(self._main_session_bar, fg_color="transparent")
            session_row.pack(fill="x", padx=12, pady=4)
            self._main_session_label = ctk.CTkLabel(
                session_row,
                text="",
                font=ctk.CTkFont(size=12),
                text_color=self.theme.c("text_primary"),
                anchor="w",
            )
            self._main_session_label.pack(side="left", fill="x", expand=True)
            self._main_disconnect_btn = ctk.CTkButton(
                session_row,
                text="Disconnect",
                width=110,
                height=28,
                fg_color="#7f1d1d",
                hover_color="#ef4444",
                text_color="#fecaca",
                command=self._on_disconnect,
            )
            self._main_disconnect_btn.pack(side="right")

        self.status_frame = ctk.CTkFrame(footer, corner_radius=8, fg_color=self.theme.c("bg_card_hover"), border_width=1, border_color=self.theme.c("border"))
        self.status_frame.grid(row=1 if not SHOW_SAVED_CONNECTIONS_SIDEBAR else 0, column=0, sticky="ew", pady=(0, 6))

        status_inner = ctk.CTkFrame(self.status_frame, fg_color="transparent")
        status_inner.pack(fill="x", padx=12, pady=4)

        self.status_indicator = ctk.CTkLabel(status_inner, text="●", font=ctk.CTkFont(size=14), text_color=self.theme.c("text_muted"))
        self.status_indicator.pack(side="left", padx=(0, 8))

        self.status_label = ctk.CTkLabel(
            status_inner,
            text="Enter connection details, then test or connect.",
            font=ctk.CTkFont(size=12),
            text_color=self.theme.c("text_muted"),
            anchor="w",
            justify="left",
            wraplength=700,
        )
        self.status_label.pack(side="left", fill="x", expand=True)

        actions = ctk.CTkFrame(footer, fg_color="transparent")
        actions.grid(row=2 if not SHOW_SAVED_CONNECTIONS_SIDEBAR else 1, column=0, sticky="e")

        self.test_btn = ctk.CTkButton(
            actions,
            text="Test Connection",
            width=140,
            height=32,
            fg_color=self.theme.c("bg_card"),
            hover_color=self.theme.c("bg_card_hover"),
            border_width=1,
            border_color=self.theme.c("border"),
            text_color=self.theme.c("text_primary"),
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._on_test,
        )
        self.test_btn.pack(side="left", padx=(0, 12))

        self.save_btn = ctk.CTkButton(
            actions,
            text="Save Connection",
            width=140,
            height=32,
            fg_color=self.theme.c("bg_elevated"),
            hover_color=self.theme.c("bg_card_hover"),
            border_width=1,
            border_color=self.theme.c("border"),
            text_color=self.theme.c("text_primary"),
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._save_server,
        )
        self.save_btn.pack(side="left", padx=(0, 12))

        self.delete_btn = ctk.CTkButton(
            actions,
            text="Delete Connection",
            width=150,
            height=32,
            fg_color="#b91c1c",
            hover_color="#dc2626",
            text_color="#ffffff",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._remove_saved,
        )

        self.connect_btn = ctk.CTkButton(
            actions,
            text="Connect",
            width=140,
            height=32,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=self.theme.c("primary"),
            hover_color=self.theme.c("primary_hover"),
            command=self._on_connect,
        )
        self.connect_btn.pack(side="left")
        self._sync_action_buttons()

    def set_servers_filter(self, db_filter: str = "all") -> None:
        """Open an engine form for +PostgreSQL / +MongoDB (new connection, not saved)."""
        self._servers_filter = db_filter or "all"
        if SHOW_SAVED_CONNECTIONS_SIDEBAR:
            self._refresh_saved_list()
        if self._servers_filter == "all":
            return
        try:
            db_type = DatabaseType(self._servers_filter)
        except ValueError:
            return
        # Engine sub-nav should always start a *new* connection form
        # (Test + Save + Connect). Saved profiles use load_saved_by_name().
        self._select_db_type(db_type, load_saved=False)

    def load_saved_by_name(self, name: str) -> bool:
        """Load a named saved profile. Returns True if found."""
        self._servers_filter = "all"
        for server in load_saved_servers():
            if server.name == name:
                self._load_saved_server(server)
                return True
        self._set_status(f"Saved connection '{name}' not found.", "warning")
        return False

    def load_from_connection(self, connection: ConnectionResult) -> None:
        """Fill the form from an active ConnectionResult (post-connect restore)."""
        server = SavedServer(
            name=connection.saved_name or connection.name,
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
        self._load_saved_server(server)

    def _notify_saved_servers_changed(self, name: str | None = None) -> None:
        if self.on_saved_servers_changed:
            self.on_saved_servers_changed(name)

    def _add_saved_category_header(self, label: str, count: int) -> None:
        row = ctk.CTkFrame(self.saved_list, fg_color="transparent")
        row.pack(fill="x", pady=(10, 4))

        ctk.CTkLabel(
            row,
            text=label.upper(),
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=self._sidebar_muted(),
        ).pack(side="left", padx=4)

        ctk.CTkLabel(
            row,
            text=str(count),
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=self._sidebar_secondary(),
        ).pack(side="right", padx=4)

    def _add_saved_server_row(self, server: SavedServer) -> None:
        db_type = DatabaseType(server.db_type)
        meta = DB_ICON_META[db_type]
        active = server.name == self._selected_saved

        item = ctk.CTkFrame(
            self.saved_list,
            corner_radius=8,
            fg_color=self.theme.c("primary") if active else self._sidebar_item_bg(),
        )
        item.pack(fill="x", pady=3)

        badge = ctk.CTkFrame(item, width=32, height=32, corner_radius=6, fg_color=meta["color"])
        badge.pack(side="left", padx=(10, 8), pady=10)
        badge.pack_propagate(False)
        ctk.CTkLabel(
            badge,
            text=meta["abbrev"],
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=self._sidebar_text(),
        ).place(relx=0.5, rely=0.5, anchor="center")

        text_col = ctk.CTkFrame(item, fg_color="transparent")
        text_col.pack(side="left", fill="x", expand=True, pady=10)

        ctk.CTkLabel(
            text_col,
            text=server.name,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=self._sidebar_text(),
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            text_col,
            text=f"{meta['label']} · {server.host}",
            font=ctk.CTkFont(size=11),
            text_color=self._sidebar_secondary(),
            anchor="w",
        ).pack(anchor="w")

        self._wire_saved_item(item, server, root=True)
        self._saved_list_buttons[server.name] = item

    def _section_card(self, parent: ctk.CTkFrame, title: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            parent,
            corner_radius=12,
            fg_color=self.theme.c("bg_card"),
            border_width=1,
            border_color=self.theme.c("border"),
        )
        card.pack(fill="x", pady=(0, 16))

        from desktop.design_system import font as ds_font

        size, weight = self.theme.type_scale("section")
        ctk.CTkLabel(
            card,
            text=title,
            font=ds_font(self.theme, size, weight=weight),
            text_color=self.theme.c("text_primary"),
        ).pack(anchor="w", padx=20, pady=(16, 12))
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=20, pady=(0, 20))
        return body

    def _bind_click_recursive(self, widget, command) -> None:
        """Bind <Button-1> on ``widget`` and every descendant.

        A composite CTk widget draws its children ON TOP of its own
        background canvas, so a click anywhere inside it lands on whichever
        child is actually drawn there — binding only the outer frame leaves
        every child that covers it dead to clicks.
        """
        try:
            widget.bind("<Button-1>", lambda _e: command())
        except Exception:
            pass
        for child in widget.winfo_children():
            self._bind_click_recursive(child, command)

    def _create_icon_badge(self, parent: ctk.CTkFrame, db_type: DatabaseType, size: int = 40) -> ctk.CTkFrame:
        meta = DB_ICON_META[db_type]
        badge = ctk.CTkFrame(parent, width=size, height=size, corner_radius=8, fg_color=meta["color"])
        badge.pack_propagate(False)
        ctk.CTkLabel(
            badge,
            text=meta["abbrev"],
            font=ctk.CTkFont(size=max(11, size // 3), weight="bold"),
            text_color=self._sidebar_text(),
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
                fg_color=self.theme.c("bg_card_hover"),
                border_width=2,
                border_color=self.theme.c("border"),
            )
            tile.grid(row=0, column=index, padx=(0, 10) if index < len(DatabaseType) - 1 else 0, sticky="n")
            tile.pack_propagate(False)

            inner = ctk.CTkFrame(tile, fg_color="transparent")
            inner.place(relx=0.5, rely=0.5, anchor="center")

            badge = self._create_icon_badge(inner, db_type, 40)
            badge.pack(pady=(0, 6))

            label = ctk.CTkLabel(inner, text=meta["label"], font=ctk.CTkFont(size=11), text_color=self.theme.c("text_primary"))
            label.pack()

            # Binding just the outer `tile` misses clicks on the badge: its
            # child CTkLabel (the "MY"/"OR" text) is drawn on top and
            # intercepts the click before the frame's own binding ever sees
            # it, so the icon square itself was previously dead space. Bind
            # every descendant instead of the four widgets guessed above.
            self._bind_click_recursive(tile, lambda dt=db_type: self._select_db_type(dt))

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
        ctk.CTkLabel(host_row, text="Port", font=ctk.CTkFont(size=12), text_color=self.theme.c("text_muted")).grid(row=0, column=1, padx=(12, 6))
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
            fg_color=self.theme.c("bg_elevated"),
            hover_color=self.theme.c("bg_card_hover"),
            text_color=self.theme.c("text_primary"),
            border_width=1,
            border_color=self.theme.c("border"),
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
            text_color=self.theme.c("text_primary"),
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
            text_color=self.theme.c("text_muted"),
            wraplength=560,
            justify="left",
        )
        self.hint_label.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.after_idle(self._style_inputs)

    def _build_linux_access_card(self, parent: ctk.CTkFrame) -> None:
        body = self._section_card(parent, "Linux Server Access (SSH)")
        body.grid_columnconfigure(0, weight=1)

        self.ssh_enabled_var = ctk.BooleanVar(value=False)
        self.ssh_enabled_cb = ctk.CTkCheckBox(
            body,
            text="Connect through Linux server using SSH tunnel",
            variable=self.ssh_enabled_var,
            command=self._sync_ssh_fields,
            font=ctk.CTkFont(size=13),
            text_color=self.theme.c("text_primary"),
            fg_color=self.theme.c("primary"),
            hover_color=self.theme.c("primary_hover"),
            border_color=self.theme.c("border"),
        )
        self.ssh_enabled_cb.grid(row=0, column=0, sticky="w", pady=(0, 6))

        # All SSH inputs live in this frame so they can be shown/hidden together.
        self._ssh_fields_frame = ctk.CTkFrame(body, fg_color="transparent")
        self._ssh_fields_frame.grid(row=1, column=0, sticky="ew")
        self._ssh_fields_frame.grid_columnconfigure(1, weight=1)

        self._labeled_field(self._ssh_fields_frame, "Linux host", row=0)
        self.ssh_host_entry = ctk.CTkEntry(
            self._ssh_fields_frame,
            height=ENTRY_HEIGHT,
            placeholder_text="Same as database host if left blank",
        )
        self.ssh_host_entry.grid(row=0, column=1, sticky="ew", padx=(12, 0), pady=6)

        self._labeled_field(self._ssh_fields_frame, "SSH port", row=1)
        self.ssh_port_entry = ctk.CTkEntry(self._ssh_fields_frame, width=90, height=ENTRY_HEIGHT)
        self.ssh_port_entry.insert(0, "22")
        self.ssh_port_entry.grid(row=1, column=1, sticky="w", padx=(12, 0), pady=6)

        self._labeled_field(self._ssh_fields_frame, "Linux username", row=2)
        self.linux_user_entry = ctk.CTkEntry(
            self._ssh_fields_frame, height=ENTRY_HEIGHT, placeholder_text="e.g. ubuntu, root, techadmin"
        )
        self.linux_user_entry.grid(row=2, column=1, sticky="ew", padx=(12, 0), pady=6)

        self._labeled_field(self._ssh_fields_frame, "Linux password", row=3)
        linux_pw_row = ctk.CTkFrame(self._ssh_fields_frame, fg_color="transparent")
        linux_pw_row.grid(row=3, column=1, sticky="ew", padx=(12, 0), pady=6)
        linux_pw_row.grid_columnconfigure(0, weight=1)
        self.linux_password_entry = ctk.CTkEntry(linux_pw_row, height=ENTRY_HEIGHT, show="*")
        self.linux_password_entry.grid(row=0, column=0, sticky="ew")
        self.linux_password_toggle_btn = ctk.CTkButton(
            linux_pw_row,
            text="👁",
            width=40,
            height=ENTRY_HEIGHT,
            fg_color=self.theme.c("bg_elevated"),
            hover_color=self.theme.c("bg_card_hover"),
            text_color=self.theme.c("text_primary"),
            border_width=1,
            border_color=self.theme.c("border"),
            font=ctk.CTkFont(size=16),
            command=self._toggle_linux_password_visibility,
        )
        self.linux_password_toggle_btn.grid(row=0, column=1, padx=(8, 0))

        ctk.CTkLabel(
            self._ssh_fields_frame,
            text=(
                "Use the Linux login for the machine where the database runs. "
                "The app opens an SSH tunnel, then connects with the DB username/password below."
            ),
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            wraplength=560,
            justify="left",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

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
        """Show SSH fields only when the tunnel checkbox is checked."""
        enabled = bool(self.ssh_enabled_var.get())
        if not hasattr(self, "_ssh_fields_frame"):
            return
        if enabled:
            if not self._ssh_fields_frame.winfo_manager():
                self._ssh_fields_frame.grid(row=1, column=0, sticky="ew")
            for widget in self._ssh_fields:
                widget.configure(state="normal")
        else:
            self._ssh_fields_frame.grid_remove()
            for widget in self._ssh_fields:
                try:
                    widget.configure(state="disabled")
                except Exception:
                    pass

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
            text_color=self.theme.c("text_primary"),
            width=130,
            anchor="w",
        ).grid(row=row, column=0, sticky="w", pady=6)

    def _find_saved_for_type(self, db_type: DatabaseType) -> SavedServer | None:
        for server in load_saved_servers():
            if server.db_type == db_type.value:
                return server
        return None

    def _apply_db_type_tiles(self, db_type: DatabaseType) -> None:
        """Highlight the active engine tile — every tile stays visible in its
        original position (PostgreSQL, MySQL, MongoDB, MSSQL, Oracle, all in
        one row) so you can switch engines directly from any saved
        connection or "New <Engine>" view. This used to hide every
        non-active tile (grid_remove()) and re-grid only the selected one to
        column 0, which meant the moment you picked or loaded any engine,
        every other tile vanished and you had no way back to them except the
        sidebar's separate "+ Engine" links.
        """
        accent = DB_ICON_META[db_type]["color"]
        for value, tile in self._db_type_tiles.items():
            if value == db_type.value:
                tile.configure(border_color=accent, fg_color=self.theme.c("primary_muted"))
            else:
                tile.configure(
                    border_color=self.theme.c("border"),
                    fg_color=self.theme.c("bg_card_hover"),
                )

    def _select_db_type(
        self,
        db_type: DatabaseType,
        *,
        load_saved: bool = False,
        notify: bool = True,
    ) -> None:
        """Switch engine type. Only load a saved profile when load_saved=True."""
        self._db_type_var.set(db_type.value)
        self._apply_db_type_tiles(db_type)

        if load_saved:
            saved = self._find_saved_for_type(db_type)
            if saved:
                self._load_saved_server(saved)
                return

        self._selected_saved = None
        self._highlight_saved_selection()
        self._apply_engine_profile(db_type)
        self._update_mongodb_fields(db_type)
        self._sync_action_buttons()
        if notify:
            self._notify_saved_servers_changed(None)
        self._set_status(
            f"New {DB_ICON_META[db_type]['label']} connection — test or save when ready.",
            "info",
        )

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
        if not SHOW_SAVED_CONNECTIONS_SIDEBAR:
            return
        for widget in self.saved_list.winfo_children():
            widget.destroy()
        self._saved_list_buttons.clear()

        servers = load_saved_servers()
        if not servers:
            ctk.CTkLabel(
                self.saved_list,
                text="No saved connections yet.\nFill the form and click Save.",
                font=ctk.CTkFont(size=12),
                text_color=self._sidebar_muted(),
                justify="left",
            ).pack(anchor="w", padx=8, pady=8)
            self._adjust_saved_list_height(0, 0)
            return

        grouped: dict[str, list[SavedServer]] = {}
        for server in servers:
            grouped.setdefault(server.db_type, []).append(server)

        visible_rows = 0
        visible_headers = 0
        for db_type, label in SERVER_SUBCATEGORIES:
            if self._servers_filter not in ("all", db_type.value):
                continue
            group = grouped.get(db_type.value, [])
            if not group:
                continue
            group.sort(key=lambda s: s.name.lower())
            self._add_saved_category_header(label, len(group))
            visible_headers += 1
            for server in group:
                self._add_saved_server_row(server)
                visible_rows += 1

        if visible_rows == 0:
            ctk.CTkLabel(
                self.saved_list,
                text="No connections in this category.\nTry another filter or save a new profile.",
                font=ctk.CTkFont(size=12),
                text_color=self._sidebar_muted(),
                justify="left",
            ).pack(anchor="w", padx=8, pady=8)
            self._adjust_saved_list_height(0, 0)
            return

        self._adjust_saved_list_height(visible_rows, visible_headers)

    def _adjust_saved_list_height(self, count: int, header_count: int = 0) -> None:
        """Size the saved-list scroll area to hug server rows (cap height when many)."""
        row_height = 58
        header_height = 28
        max_rows = 6
        if count <= 0:
            height = 72
        else:
            total = count * row_height + header_count * header_height + 8
            height = min(total, max_rows * row_height + header_count * header_height + 8)
        self.saved_list.configure(height=height)

    def update_session(self, connection: ConnectionResult | None) -> None:
        """Show or hide the active-session strip when connected."""
        if not SHOW_SAVED_CONNECTIONS_SIDEBAR:
            bar = getattr(self, "_main_session_bar", None)
            label = getattr(self, "_main_session_label", None)
            btn = getattr(self, "_main_disconnect_btn", None)
            if not bar or not label or not btn:
                return
            if connection:
                label.configure(
                    text=(
                        f"Active session: {connection.name} · "
                        f"{connection.db_type.value.upper()} · "
                        f"{connection.host}:{connection.port}"
                    ),
                )
                bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
                btn.configure(state="normal")
            else:
                bar.grid_remove()
                btn.configure(state="disabled")
            return

        if connection:
            self._session_label.configure(
                text=(
                    f"Active session: {connection.name} · "
                    f"{connection.db_type.value.upper()} · "
                    f"{connection.host}:{connection.port}"
                ),
            )
            if not self._session_frame.winfo_manager():
                self._session_frame.pack(
                    fill="x",
                    padx=16,
                    pady=(6, 0),
                    before=self._actions_footer,
                )
            self._disconnect_btn.configure(state="normal")
        else:
            self._session_frame.pack_forget()
            self._disconnect_btn.configure(state="disabled")

    def _on_disconnect(self) -> None:
        if self.disconnect_callback:
            self.disconnect_callback()
        self._set_status("Disconnected. No active session.", "warning")

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
                    widget.configure(fg_color=self._sidebar_hover())

            def on_leave(_event=None) -> None:
                self._highlight_saved_selection()

            widget.bind("<Enter>", on_enter)
            widget.bind("<Leave>", on_leave)

        for child in widget.winfo_children():
            self._wire_saved_item(child, server, root=False)

    def _highlight_saved_selection(self) -> None:
        for name, item in self._saved_list_buttons.items():
            item.configure(fg_color=self.theme.c("primary") if name == self._selected_saved else self._sidebar_item_bg())

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
        self._select_db_type(DatabaseType.POSTGRESQL, load_saved=False)

    def _load_saved_server(self, server: SavedServer) -> None:
        self._selected_saved = server.name
        self._highlight_saved_selection()
        self.update_idletasks()
        self._fill_form(server)
        self._sync_action_buttons()
        self._set_status(f"Loaded '{server.name}'.", "info")

    def _sync_action_buttons(self) -> None:
        """Saved profile → Connect + Delete; new/engine form → Test + Save + Connect."""
        if not hasattr(self, "test_btn"):
            return
        saved_mode = bool(self._selected_saved)
        if saved_mode:
            self.test_btn.pack_forget()
            self.save_btn.pack_forget()
            self.connect_btn.pack_forget()
            self.delete_btn.pack_forget()
            # Connect first (left), Delete second (right).
            self.connect_btn.pack(side="left", padx=(0, 12))
            self.delete_btn.pack(side="left")
        else:
            self.delete_btn.pack_forget()
            if not self.test_btn.winfo_manager():
                self.test_btn.pack(side="left", padx=(0, 12), before=self.connect_btn)
            if not self.save_btn.winfo_manager():
                self.save_btn.pack(side="left", padx=(0, 12), before=self.connect_btn)
            if not self.connect_btn.winfo_manager():
                self.connect_btn.pack(side="left")

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
        upsert_server(server)
        self._selected_saved = name
        self.connection_name_entry.delete(0, "end")
        self.connection_name_entry.insert(0, name)
        self._refresh_saved_list()
        self._notify_saved_servers_changed(name)
        return True

    def _save_server(self) -> None:
        name = self._get_connection_name()
        if self._persist_current_connection():
            self._sync_action_buttons()
            self._set_status(f"Saved connection '{name or self._selected_saved}'.", "success")

    def _remove_saved(self) -> None:
        if not self._selected_saved:
            self._set_status("Select a saved connection to delete.", "warning")
            return
        name = self._selected_saved
        confirm(
            self,
            self.theme,
            title="Delete connection?",
            message=(
                f"'{name}' will be removed from your saved connections. "
                "This cannot be undone."
            ),
            confirm_text="Delete",
            destructive=True,
            on_confirm=lambda: self._do_remove_saved(name),
        )

    def _do_remove_saved(self, name: str) -> None:
        if remove_server(name):
            self._selected_saved = None
            self._refresh_saved_list()
            self._notify_saved_servers_changed()
            self._new_connection()
            self._set_status(f"Deleted '{name}'.", "success")
        else:
            self._set_status(f"Could not delete '{name}'.", "error")

    def _set_status(self, message: str, level: str = "info") -> None:
        colors = {
            "info": (self.theme.c("text_muted"), self.theme.c("bg_elevated")),
            "warning": (self.theme.c("warning"), self.theme.c("warning_muted")),
            "error": (self.theme.c("error"), self.theme.c("error_muted")),
            "success": (self.theme.c("success"), self.theme.c("success_muted")),
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
        self.save_btn.configure(state=state)
        self.delete_btn.configure(state=state)
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
            from backend.services.ssh_host_keys import SshHostKeyError

            host_key_error = None
            try:
                ok, message = test_connection(connection)
            except SshHostKeyError as exc:
                ok, message, host_key_error = False, str(exc), exc
            self.after(
                0,
                lambda ok=ok, message=message, err=host_key_error: self._on_test_complete(
                    ok, message, connection, on_success_connect, err
                ),
            )

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
        host_key_error: Exception | None = None,
    ) -> None:
        self._set_busy(testing=False, connecting=False)
        if ok:
            self._set_status(message, "success")
            if on_success_connect:
                self._persist_current_connection()
                connection.saved_name = self._selected_saved
                self._log(f"Connected to {connection.name}", "SUCCESS")
                if self.on_connect_success:
                    self.on_connect_success(connection)
        else:
            self._set_status(message, "error")
            if host_key_error is not None:
                from desktop.ssh_host_trust import maybe_offer_host_key_trust

                maybe_offer_host_key_trust(self, host_key_error)

    def _style_inputs(self) -> None:
        for name in (
            "connection_name_entry", "host_entry", "port_entry", "user_entry",
            "password_entry", "database_entry", "auth_database_entry",
            "ssh_host_entry", "ssh_port_entry", "linux_user_entry", "linux_password_entry",
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.configure(
                    fg_color=self.theme.c("bg_input"),
                    border_color=self.theme.c("border"),
                    text_color=self.theme.c("text_primary"),
                )
        # Re-apply SSH show/hide after theme styling so fields stay in sync.
        if hasattr(self, "ssh_enabled_var"):
            self._sync_ssh_fields()

    def apply_theme(self, theme: ThemeManager) -> None:
        """Re-apply theme tokens after light/dark toggle."""
        self.theme = theme
        self.configure(fg_color=theme.c("bg_content"))
        scroll = getattr(self, "_form_scroll", None)
        if scroll is not None:
            try:
                scroll.configure(**theme.scroll_kwargs())
            except Exception:
                pass
        saved = getattr(self, "saved_list", None)
        if saved is not None:
            try:
                saved.configure(**theme.scroll_kwargs())
            except Exception:
                pass
        self._refresh_saved_list()
        self._apply_db_type_tiles(DatabaseType(self._db_type_var.get()))
        self._style_inputs()
