"""Embedded SSH remote directory browser for restore source selection."""

from __future__ import annotations

import threading
from typing import Callable

import customtkinter as ctk

from backend.services.ssh_client import list_remote_directory
from desktop.theme import ThemeManager


class RemoteDirectoryBrowser(ctk.CTkFrame):
    """Browse folders and files on a Linux server over SSH."""

    def __init__(
        self,
        master,
        theme: ThemeManager,
        get_ssh: Callable[[], dict],
        initial_path: str = "/mnt/backup",
        on_selection_changed: Callable[[str], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.theme = theme
        self._get_ssh = get_ssh
        self._on_selection_changed = on_selection_changed
        self._current_path = initial_path
        self._selected_path = ""
        self._entries: list[dict] = []
        self._build_ui()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)

        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        nav.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(nav, text="Up", width=50, command=self._go_up).grid(row=0, column=0, padx=(0, 6))
        self.path_entry = ctk.CTkEntry(nav, placeholder_text="/mnt/backup")
        self.path_entry.grid(row=0, column=1, sticky="ew")
        self.path_entry.bind("<Return>", self._on_path_enter)
        ctk.CTkButton(nav, text="Go", width=50, command=self._on_path_enter).grid(row=0, column=2, padx=(6, 0))
        ctk.CTkButton(nav, text="Refresh", width=70, command=self._refresh).grid(row=0, column=3, padx=(6, 0))

        ctk.CTkLabel(
            self,
            text="Double-click a folder to open it. Click a row to select, then use Select.",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(0, 4))

        self.list_frame = ctk.CTkScrollableFrame(self, height=160)
        self.list_frame.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        self.list_frame.grid_columnconfigure(0, weight=1)

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew")
        ctk.CTkButton(
            actions,
            text="Select folder",
            width=110,
            command=self._select_current_folder,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            actions,
            text="Select file",
            width=90,
            command=self._select_highlighted,
        ).pack(side="left")

        self.status_label = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=self.theme.c("text_muted"),
            anchor="w",
        )
        self.status_label.grid(row=4, column=0, sticky="w", pady=(6, 0))

    def set_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget in (self.path_entry,):
            widget.configure(state=state)
        for child in self.list_frame.winfo_children():
            try:
                child.configure(state=state)
            except Exception:
                pass

    def get_selected_path(self) -> str:
        return self._selected_path

    def set_initial_path(self, path: str) -> None:
        if path.strip():
            self.load_directory(path.strip())

    def load_directory(self, path: str) -> None:
        self._current_path = path.rstrip("/") or "/"
        self.path_entry.delete(0, "end")
        self.path_entry.insert(0, self._current_path)
        self.status_label.configure(text="Loading...")
        self._clear_list()

        def task():
            try:
                ssh = self._get_ssh()
                if not ssh:
                    raise RuntimeError("SSH is not configured on this connection.")
                entries = list_remote_directory(ssh, self._current_path)
                self.after(0, lambda: self._show_entries(entries, None))
            except Exception as exc:
                self.after(0, lambda: self._show_entries([], str(exc)))

        threading.Thread(target=task, daemon=True).start()

    def _clear_list(self) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()

    def _show_entries(self, entries: list[dict], error: str | None) -> None:
        self._entries = entries
        self._clear_list()
        if error:
            self.status_label.configure(text=f"Error: {error}")
            return
        if not entries:
            self.status_label.configure(text=f"Empty folder: {self._current_path}")
            return
        self.status_label.configure(text=f"{len(entries)} items in {self._current_path}")
        for idx, entry in enumerate(entries):
            prefix = "[DIR] " if entry["is_dir"] else "[FILE] "
            size = entry.get("size", 0)
            if entry["is_dir"]:
                label = f"{prefix}{entry['name']}/"
            elif size >= 1024 * 1024:
                label = f"{prefix}{entry['name']}  ({size / (1024 * 1024):.1f} MB)"
            elif size > 0:
                label = f"{prefix}{entry['name']}  ({size:,} bytes)"
            else:
                label = f"{prefix}{entry['name']}"
            row = ctk.CTkButton(
                self.list_frame,
                text=label,
                anchor="w",
                fg_color="transparent",
                text_color=self.theme.c("text_primary"),
                hover_color=self.theme.c("surface_hover"),
                command=lambda e=entry: self._on_row_click(e),
            )
            row.grid(row=idx, column=0, sticky="ew", pady=1)
            row.bind("<Double-Button-1>", lambda _ev, e=entry: self._on_row_double_click(e))

    def _on_row_click(self, entry: dict) -> None:
        self._selected_path = entry["path"]
        self.status_label.configure(text=f"Selected: {self._selected_path}")
        if self._on_selection_changed:
            self._on_selection_changed(self._selected_path)

    def _on_row_double_click(self, entry: dict) -> None:
        if entry["is_dir"]:
            self.load_directory(entry["path"])
        else:
            self._on_row_click(entry)

    def _go_up(self) -> None:
        parent = self._current_path.rstrip("/")
        if not parent or parent == "/":
            self.load_directory("/")
            return
        parent = parent.rsplit("/", 1)[0] or "/"
        self.load_directory(parent)

    def _on_path_enter(self, _event=None) -> None:
        path = self.path_entry.get().strip()
        if path:
            self.load_directory(path)

    def _refresh(self) -> None:
        self.load_directory(self._current_path)

    def _select_current_folder(self) -> None:
        self._selected_path = self._current_path
        self.status_label.configure(text=f"Selected folder: {self._selected_path}")
        if self._on_selection_changed:
            self._on_selection_changed(self._selected_path)

    def _select_highlighted(self) -> None:
        if not self._selected_path:
            self.status_label.configure(text="Click a file or folder first.")
            return
        if self._on_selection_changed:
            self._on_selection_changed(self._selected_path)
