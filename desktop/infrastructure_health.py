"""Infrastructure health status panel for the dashboard."""



from __future__ import annotations



import customtkinter as ctk



from desktop.dashboard_widgets import panel_grid

from desktop.design_system import font, glass_surface

from desktop.theme import ThemeManager



BLINK_MS = 750





class InfrastructureHealthPanel(ctk.CTkFrame):

    """Live connection health: servers, databases, backup, replication."""



    ROWS = [

        ("servers", "Saved Server Profiles"),

        ("databases", "Database Connection"),

        ("backup", "Backup Status (Today)"),

        ("replication", "Replication Status"),

    ]



    def __init__(self, master, theme: ThemeManager, **kwargs) -> None:

        super().__init__(master, fg_color="transparent", **kwargs)

        self.theme = theme

        self._health_widgets: dict[str, dict] = {}

        self._blink_jobs: dict[str, str] = {}

        self._blink_on = True

        self._live_rows: set[str] = set()

        self._timers_active = False

        self._build()



    def _build(self) -> None:

        grid = panel_grid(self, self.theme, columns=1)

        shell, content = glass_surface(grid, self.theme, radius="lg")

        shell.pack(fill="x", pady=4)

        ctk.CTkLabel(

            content,

            text="SERVICE STATUS",

            font=font(self.theme, 10, weight="bold"),

            text_color=self.theme.c("text_muted"),

            anchor="w",

        ).pack(anchor="w", padx=20, pady=(16, 8))

        inner = ctk.CTkFrame(content, fg_color="transparent")

        inner.pack(fill="x", padx=20, pady=(0, 16))



        for key, label in self.ROWS:

            row = ctk.CTkFrame(inner, fg_color="transparent")

            row.pack(fill="x", pady=8)



            ctk.CTkLabel(

                row,

                text=label,

                font=font(self.theme, 13),

                text_color=self.theme.c("text_secondary"),

                anchor="w",

            ).pack(side="left")



            value_wrap = ctk.CTkFrame(row, fg_color="transparent")

            value_wrap.pack(side="right")



            dot = ctk.CTkFrame(

                value_wrap,

                width=10,

                height=10,

                corner_radius=5,

                fg_color="transparent",

            )

            dot.pack(side="left", padx=(0, 8))

            dot.pack_propagate(False)



            prefix = ctk.CTkLabel(

                value_wrap,

                text="",

                font=font(self.theme, 13, weight="bold"),

                text_color=self.theme.c("text_secondary"),

            )

            prefix.pack(side="left")



            status = ctk.CTkLabel(

                value_wrap,

                text="—",

                font=font(self.theme, 13, weight="bold"),

                text_color=self.theme.c("text_secondary"),

                anchor="e",

            )

            status.pack(side="left")



            self._health_widgets[key] = {"dot": dot, "prefix": prefix, "status": status}



    def set_active(self, active: bool) -> None:

        """Pause or resume live indicator animation when the dashboard is hidden."""

        self._timers_active = active

        if not active:

            for key in list(self._blink_jobs):

                self._stop_blink(key, hold_color=True)

            return

        for key in self._live_rows:

            self._start_blink(key)



    def destroy(self) -> None:

        self._timers_active = False

        for key in list(self._blink_jobs):

            self._stop_blink(key)

        self._live_rows.clear()

        super().destroy()



    def _stop_blink(self, key: str, *, hold_color: bool = False) -> None:

        job = self._blink_jobs.pop(key, None)

        if job is not None:

            try:

                self.after_cancel(job)

            except Exception:

                pass

        if not hold_color:

            widgets = self._health_widgets.get(key)

            if widgets and widgets["dot"].winfo_exists():

                widgets["dot"].configure(fg_color="transparent")



    def _start_blink(self, key: str, *, tone: str = "success") -> None:

        if not self._timers_active:

            return

        self._stop_blink(key, hold_color=True)

        widgets = self._health_widgets.get(key)

        if not widgets:

            return

        dot = widgets["dot"]

        if tone == "primary":

            bright = self.theme.c("primary")

            dim = self.theme.c("primary_muted")

        else:

            bright = self.theme.c("success")

            dim = self.theme.c("success_muted")



        def tick() -> None:

            if not self.winfo_exists() or not dot.winfo_exists() or not self._timers_active:

                self._blink_jobs.pop(key, None)

                return

            self._blink_on = not self._blink_on

            dot.configure(fg_color=bright if self._blink_on else dim)

            self._blink_jobs[key] = self.after(BLINK_MS, tick)



        self._blink_on = True

        dot.configure(fg_color=bright)

        self._blink_jobs[key] = self.after(BLINK_MS, tick)



    def _mark_live(self, key: str, *, live: bool, tone: str = "success") -> None:

        if live:

            self._live_rows.add(key)

            self._start_blink(key, tone=tone)

        else:

            self._live_rows.discard(key)

            self._stop_blink(key)



    def _health_color(self, token: str) -> str:

        token_l = token.lower()

        if token_l in ("online", "healthy", "ok", "connected", "compliant"):

            return self.theme.c("success")

        if token_l in ("offline", "disconnected"):

            return self.theme.c("error")

        if token_l in ("warning", "failed", "attention"):

            return self.theme.c("warning")

        if token_l in ("critical",):

            return self.theme.c("error")

        if token_l in ("idle", "n/a", "none", "saved"):

            return self.theme.c("text_muted")

        return self.theme.c("text_secondary")



    def _set_health_row(self, key: str, text: str) -> None:

        widgets = self._health_widgets.get(key)

        if not widgets:

            return



        dot = widgets["dot"]

        prefix = widgets["prefix"]

        status = widgets["status"]

        raw = (text or "").strip()

        raw_l = raw.lower()



        if raw_l in ("not connected", "offline", "disconnected", "0 online") or raw.startswith("0 "):

            self._mark_live(key, live=False)

            prefix.configure(text="")

            status.configure(text=raw or "Offline", text_color=self.theme.c("error"))

            dot.configure(fg_color=self.theme.c("error"))

            return



        if raw_l.endswith(" online") or raw_l == "online":

            parts = raw.rsplit(" ", 1)

            if len(parts) == 2 and parts[1].lower() == "online":

                prefix.configure(text=f"{parts[0]} ", text_color=self.theme.c("text_secondary"))

                status.configure(text="Online", text_color=self.theme.c("success"))

            else:

                prefix.configure(text="")

                status.configure(text="Online", text_color=self.theme.c("success"))

            self._mark_live(key, live=True)

            return



        if "connected" in raw_l:

            prefix.configure(text="")

            status.configure(text=raw, text_color=self.theme.c("success"))

            self._mark_live(key, live=True)

            return



        if "saved" in raw_l and "connected" not in raw_l:

            self._mark_live(key, live=False)

            prefix.configure(text="")

            status.configure(text=raw, text_color=self.theme.c("text_secondary"))

            dot.configure(fg_color=self.theme.c("primary"))

            return



        if raw_l in ("primary", "replica set ok") or "replica" in raw_l and "lag" not in raw_l:

            self._mark_live(key, live=True, tone="success")

            prefix.configure(text="")

            status.configure(text=raw or "—", text_color=self.theme.c("success"))

            return



        self._mark_live(key, live=False)

        dot.configure(fg_color="transparent")

        prefix.configure(text="")

        token = raw.split()[0] if raw else ""

        status.configure(text=raw or "—", text_color=self._health_color(token))



    def update_health(self, *, servers: str, databases: str, backup: str, replication: str) -> None:

        self._set_health_row("servers", servers)

        self._set_health_row("databases", databases)

        self._set_health_row("backup", backup)

        self._set_health_row("replication", replication)

