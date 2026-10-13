"""Ask the operator to type a host fingerprint before it is stored."""

from __future__ import annotations

import customtkinter as ctk

from backend.services.ssh_host_keys import SshHostKeyError, record_verified_host_key
from desktop.window_icon import schedule_window_icon


def maybe_offer_host_key_trust(master, error: BaseException) -> None:
    """Open the trust dialog only for an unknown host that still has its key."""
    if isinstance(error, SshHostKeyError) and error.reason == "unknown" and error.key is not None:
        offer_host_key_trust(master, error)


def offer_host_key_trust(master, error: SshHostKeyError) -> bool:
    """Return True only after the typed fingerprint matches the presented key.

    The fingerprint shown in the dialog is not written by itself. The operator
    must enter the fingerprint they verified on the server.
    """
    if error.reason != "unknown" or error.key is None:
        return False

    theme = getattr(master, "theme", None)
    dialog = ctk.CTkToplevel(master)
    dialog.title("Verify SSH host key")
    dialog.geometry("560x360")
    dialog.resizable(False, False)
    dialog.transient(master)
    schedule_window_icon(dialog)
    saved = {"ok": False}

    if theme is not None:
        dialog.configure(fg_color=theme.c("surface"))

    text_color = theme.c("text_primary") if theme is not None else None
    muted = theme.c("text_muted") if theme is not None else None

    ctk.CTkLabel(
        dialog,
        text=f"Untrusted SSH host {error.host}:{error.port}",
        text_color=text_color,
        anchor="w",
    ).pack(fill="x", padx=20, pady=(16, 6))
    ctk.CTkLabel(
        dialog,
        text=(
            "The server presented this fingerprint. Do not save it from this window alone. "
            "Read the host key fingerprint on the server console, then type that value below."
        ),
        text_color=muted,
        wraplength=520,
        justify="left",
        anchor="w",
    ).pack(fill="x", padx=20, pady=(0, 8))
    presented = ctk.CTkEntry(dialog)
    presented.insert(0, error.fingerprint)
    presented.configure(state="disabled")
    presented.pack(fill="x", padx=20, pady=(0, 10))

    ctk.CTkLabel(
        dialog,
        text="Fingerprint verified on the server",
        text_color=text_color,
        anchor="w",
    ).pack(fill="x", padx=20)
    typed = ctk.CTkEntry(dialog, placeholder_text="SHA256:...")
    typed.pack(fill="x", padx=20, pady=(4, 8))
    status = ctk.CTkLabel(dialog, text="", text_color=muted, wraplength=520, justify="left")
    status.pack(fill="x", padx=20, pady=(0, 8))

    def save() -> None:
        try:
            record_verified_host_key(
                host=error.host,
                port=error.port,
                key=error.key,
                verified_fingerprint=typed.get(),
            )
        except SshHostKeyError as exc:
            status.configure(text=str(exc))
            return
        saved["ok"] = True
        dialog.destroy()

    buttons = ctk.CTkFrame(dialog, fg_color="transparent")
    buttons.pack(fill="x", padx=20, pady=(4, 16))
    ctk.CTkButton(buttons, text="Cancel", command=dialog.destroy, width=100).pack(side="right", padx=(8, 0))
    ctk.CTkButton(buttons, text="Save verified key", command=save, width=160).pack(side="right")

    dialog.grab_set()
    typed.focus_set()
    dialog.wait_window()
    return bool(saved["ok"])
