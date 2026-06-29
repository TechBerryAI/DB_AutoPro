"""Enterprise sidebar for DB Automation Pro."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from desktop.design_system import destructive_button, font, secondary_button
from desktop.nav_icons import NAV_ICONS
from desktop.theme import ThemeManager

NAV_ITEMS = [
    ("dashboard", "Dashboard"),
    ("servers", "Servers"),
    ("databases", "Databases"),
    ("backup", "Backup"),
    ("restore", "Restore"),
    ("logs", "Logs"),
    ("reports", "Reports"),
    ("settings", "Settings"),
]


class EnterpriseSidebar(ctk.CTkFrame):
    """Advanced dark-sidebar navigation shell."""

    WIDTH = 300

    def __init__(
        self,
        master,
        theme: ThemeManager,
        *,
        employee_name: str,
        employee_role: str,
        initials: str,
        on_navigate: Callable[[str], None],
        on_theme_toggle: Callable[[], None],
        on_sign_out: Callable[[], None],
        on_log: Callable[[str, str], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            width=self.WIDTH,
            corner_radius=0,
            fg_color=theme.sc("bg_sidebar"),
            border_width=0,
            **kwargs,
        )
        self.theme = theme
        self._on_navigate = on_navigate
        self._on_theme_toggle = on_theme_toggle
        self._on_sign_out = on_sign_out
        self._on_log = on_log or (lambda _m, _l: None)
        self._emp_name = employee_name
        self._emp_role = employee_role
        self._initials = initials
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self._nav_indicators: dict[str, ctk.CTkFrame] = {}
        self._build()

    def _section_label(self, parent, text: str) -> None:
        ctk.CTkLabel(
            parent,
            text=text.upper(),
            font=font(self.theme, 9, weight="bold"),
            text_color=self.theme.sc("text_muted"),
            anchor="w",
        ).pack(anchor="w", padx=14, pady=(16, 8))

    def _build(self) -> None:
        self.grid_propagate(False)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkFrame(self, height=3, corner_radius=0, fg_color=self.theme.c("primary")).grid(row=0, column=0, sticky="ew")
        ctk.CTkFrame(self, height=1, corner_radius=0, fg_color=self.theme.c("accent")).grid(row=0, column=0, sticky="s")

        scroll = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            scrollbar_button_color=self.theme.sc("bg_card"),
            scrollbar_button_hover_color=self.theme.c("primary"),
        )
        scroll.grid(row=1, column=0, sticky="nsew", padx=10, pady=(10, 6))

        brand = ctk.CTkFrame(
            scroll,
            corner_radius=self.theme.radius("lg"),
            fg_color=self.theme.sc("bg_card"),
            border_width=1,
            border_color=self.theme.sc("border"),
        )
        brand.pack(fill="x", padx=2, pady=(2, 12))
        brand_inner = ctk.CTkFrame(brand, fg_color="transparent")
        brand_inner.pack(fill="x", padx=14, pady=14)
        logo = ctk.CTkFrame(brand_inner, width=42, height=42, corner_radius=12, fg_color=self.theme.c("primary"))
        logo.pack(side="left")
        logo.pack_propagate(False)
        ctk.CTkLabel(
            logo,
            text="DB",
            font=font(self.theme, 14, weight="bold"),
            text_color=self.theme.c("text_on_primary"),
        ).place(relx=0.5, rely=0.5, anchor="center")
        txt = ctk.CTkFrame(brand_inner, fg_color="transparent")
        txt.pack(side="left", padx=(12, 0))
        ctk.CTkLabel(
            txt,
            text="DB Automation Pro",
            font=font(self.theme, 14, weight="bold"),
            text_color=self.theme.sc("text_primary"),
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            txt,
            text="Enterprise DBA Platform",
            font=font(self.theme, 10),
            text_color=self.theme.sc("text_muted"),
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        profile = ctk.CTkFrame(
            scroll,
            corner_radius=self.theme.radius("lg"),
            fg_color=self.theme.sc("bg_card"),
            border_width=1,
            border_color=self.theme.sc("border"),
        )
        profile.pack(fill="x", padx=2, pady=(0, 12))
        prow = ctk.CTkFrame(profile, fg_color="transparent")
        prow.pack(fill="x", padx=14, pady=14)
        av = ctk.CTkFrame(prow, width=44, height=44, corner_radius=22, fg_color=self.theme.c("accent"))
        av.pack(side="left")
        av.pack_propagate(False)
        self._avatar_initials = ctk.CTkLabel(
            av,
            text=self._initials,
            font=font(self.theme, 13, weight="bold"),
            text_color=self.theme.c("text_on_primary"),
        )
        self._avatar_initials.place(relx=0.5, rely=0.5, anchor="center")
        info = ctk.CTkFrame(prow, fg_color="transparent")
        info.pack(side="left", padx=(12, 0))
        self._name_label = ctk.CTkLabel(
            info,
            text=self._emp_name,
            font=font(self.theme, 13, weight="bold"),
            text_color=self.theme.sc("text_primary"),
            anchor="w",
        )
        self._name_label.pack(anchor="w")
        self._role_label = ctk.CTkLabel(
            info,
            text=self._emp_role,
            font=font(self.theme, 10),
            text_color=self.theme.sc("text_muted"),
            anchor="w",
        )
        self._role_label.pack(anchor="w", pady=(2, 0))

        self._section_label(scroll, "Navigation")
        for key, label in NAV_ITEMS:
            icon = NAV_ICONS.get(key, "•")
            row = ctk.CTkFrame(scroll, fg_color="transparent")
            row.pack(fill="x", pady=2, padx=2)
            indicator = ctk.CTkFrame(row, width=4, height=38, corner_radius=3, fg_color="transparent")
            indicator.pack(side="left", padx=(0, 6))
            self._nav_indicators[key] = indicator
            btn = ctk.CTkButton(
                row,
                text=f"  {icon}   {label}",
                anchor="w",
                height=40,
                corner_radius=self.theme.radius("md"),
                fg_color="transparent",
                text_color=self.theme.sc("text_secondary"),
                hover_color=self.theme.sc("nav_hover"),
                font=font(self.theme, 12),
                command=lambda k=key: self._on_navigate(k),
            )
            btn.pack(side="left", fill="x", expand=True)
            self.nav_buttons[key] = btn

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 14))
        ctk.CTkFrame(footer, height=1, fg_color=self.theme.sc("border")).pack(fill="x", pady=(0, 12))
        self._theme_btn = secondary_button(footer, self.theme, text="☀  Theme", command=self._on_theme_toggle, height=38)
        self._theme_btn.pack(fill="x", pady=(0, 8))
        destructive_button(footer, self.theme, text="Sign Out", command=self._on_sign_out, height=38).pack(fill="x")

    def set_profile(self, name: str, role: str, initials: str) -> None:
        self._name_label.configure(text=name)
        self._role_label.configure(text=role)
        self._avatar_initials.configure(text=initials)

    def style_navigation(self, key: str) -> None:
        t = self.theme
        for k, btn in self.nav_buttons.items():
            indicator = self._nav_indicators.get(k)
            if k == key:
                btn.configure(
                    fg_color=t.sc("nav_active"),
                    text_color=t.c("nav_active_text"),
                    border_width=1,
                    border_color=t.c("primary"),
                    font=font(t, 12, weight="bold"),
                )
                if indicator:
                    indicator.configure(fg_color=t.c("nav_indicator"))
            else:
                btn.configure(
                    fg_color="transparent",
                    text_color=t.sc("text_secondary"),
                    border_width=0,
                    font=font(t, 12),
                )
                if indicator:
                    indicator.configure(fg_color="transparent")

    def set_theme_button_label(self, label: str) -> None:
        self._theme_btn.configure(text=f"{label}  Theme")

    def apply_theme(self, theme: ThemeManager) -> None:
        self.theme = theme
        self.configure(fg_color=theme.sc("bg_sidebar"))
