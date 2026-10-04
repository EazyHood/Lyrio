# -*- coding: utf-8 -*-
"""Ventana principal de Lyrio (customtkinter) - v4.

Detalles: ecualizador animado mientras suena, barra de progreso clicable con
tiempos, letra con scroll suave + boton "Ahora", tooltips, iconos propios,
editor de sincronizacion con modo correccion en vivo, ajustes completos del
overlay (ancho, lineas, alineacion, fondo, fuente), fuente de musica
(Spotify/cualquiera), firma de sincronizacion y publicacion a lrclib.
"""
import io
import threading
import time
import tkinter as tk

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageOps, ImageTk

from i18n import t
from appconfig import APP_NAME, APP_VERSION
from lyrics import cache_size, clear_cache
from player import friendly_app_name

BG = ("#f4f2ec", "#101417")
CARD = ("#fbfaf7", "#171d22")
CARD_2 = ("#ece9e0", "#1f272e")
TEXT = ("#1b1e21", "#e9e7e2")
MUTED = ("#82878d", "#8b939b")
ACCENT = ("#149544", "#1db954")
ACCENT_SOFT = ("#dff0e4", "#16301f")
BORDER = ("#e3dfd4", "#242d35")
AMBER = ("#a06a00", "#e5b567")
AMBER_SOFT = ("#f7ecd4", "#31280f")

SWATCHES = ["#ffffff", "#1db954", "#ffd93b", "#4dd6ff", "#ff7bd5"]
FONT_CHOICES = ["Segoe UI", "Arial", "Bahnschrift", "Georgia",
                "Comic Sans MS", "Impact", "Consolas"]

ART_SIZE = 168
USER_SCROLL_GRACE = 5.0
_SENTINEL = object()


def _font(size, weight="normal"):
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


def _pick(pair):
    if isinstance(pair, str):
        return pair
    return pair[1] if ctk.get_appearance_mode() == "Dark" else pair[0]


def _fmt_time(s):
    s = max(0, int(s))
    return f"{s // 60}:{s % 60:02d}"


def _round_image(img, size, radius=18):
    img = ImageOps.fit(img.convert("RGB"), (size * 2, size * 2))
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0) + img.size,
                                           radius=radius * 2, fill=255)
    img.putalpha(mask)
    return img


def _placeholder_art(size):
    img = Image.new("RGB", (size * 2, size * 2), "#20262c")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size * 2, size * 2), radius=36, fill="#232a31")
    cx, cy = size, size
    d.ellipse((cx - 46, cy + 18, cx - 6, cy + 50), fill="#1db954")
    d.rectangle((cx - 10, cy - 52, cx - 2, cy + 34), fill="#1db954")
    d.polygon((cx - 10, cy - 52, cx + 34, cy - 64, cx + 34, cy - 46,
               cx - 10, cy - 34), fill="#1db954")
    return _round_image(img, size)


class Tooltip:
    """Tooltip minimalista y consciente del tema."""

    def __init__(self, widget, textfn, delay=550):
        self.w = widget
        self.textfn = textfn if callable(textfn) else (lambda: textfn)
        self.delay = delay
        self.tip = None
        self.job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Button>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self.job = self.w.after(self.delay, self._show)

    def _show(self):
        txt = self.textfn()
        if not txt or self.tip:
            return
        x = self.w.winfo_rootx() + 12
        y = self.w.winfo_rooty() + self.w.winfo_height() + 6
        self.tip = tk.Toplevel(self.w)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        lbl = tk.Label(self.tip, text=txt, font=("Segoe UI", 10),
                       bg=_pick(("#2a2f34", "#e8e6e2")),
                       fg=_pick(("#f0efec", "#1b1e21")),
                       padx=10, pady=5, wraplength=280, justify="left")
        lbl.pack()
        self.tip.geometry(f"+{x}+{y}")

    def _hide(self, _e=None):
        self._cancel()
        if self.tip:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None

    def _cancel(self):
        if self.job:
            try:
                self.w.after_cancel(self.job)
            except Exception:
                pass
            self.job = None


class EqBars(tk.Canvas):
    """Ecualizador animado de 3 barras (late mientras suena)."""

    def __init__(self, master, **kw):
        super().__init__(master, width=22, height=16, highlightthickness=0,
                         bd=0, **kw)
        self._phase = 0
        self._playing = False
        self._loop()

    def set_playing(self, on):
        self._playing = on

    def retheme(self):
        self.configure(bg=_pick(CARD))

    def _loop(self):
        try:
            self.delete("all")
            self.configure(bg=_pick(CARD))
            import math
            for i in range(3):
                if self._playing:
                    h = 5 + 9 * abs(math.sin(self._phase / 4.0 + i * 1.1))
                else:
                    h = 4
                x = 3 + i * 7
                self.create_rectangle(x, 16 - h, x + 4, 16,
                                      fill=_pick(ACCENT), outline="")
            self._phase += 1
        except Exception:
            return
        self.after(90, self._loop)


class SeekBar(tk.Canvas):
    """Barra de progreso clicable (seek)."""

    def __init__(self, master, on_seek, **kw):
        super().__init__(master, height=14, highlightthickness=0, bd=0, **kw)
        self.on_seek = on_seek
        self.frac = 0.0
        self.bind("<Button-1>", self._click)
        self.bind("<B1-Motion>", self._click)
        self.bind("<Configure>", lambda e: self._draw())

    def set_frac(self, f):
        f = max(0.0, min(1.0, f))
        if abs(f - self.frac) > 0.002:
            self.frac = f
            self._draw()

    def retheme(self):
        self._draw()

    def _draw(self):
        self.delete("all")
        self.configure(bg=_pick(CARD))
        w = max(1, self.winfo_width())
        y = 7
        self.create_line(4, y, w - 4, y, width=4, capstyle="round",
                         fill=_pick(CARD_2))
        x = 4 + (w - 8) * self.frac
        self.create_line(4, y, x, y, width=4, capstyle="round",
                         fill=_pick(ACCENT))
        self.create_oval(x - 5, y - 5, x + 5, y + 5, fill=_pick(ACCENT),
                         outline="")

    def _click(self, e):
        w = max(1, self.winfo_width())
        self.on_seek(max(0.0, min(1.0, (e.x - 4) / max(1, w - 8))))


class MainWindow:
    def __init__(self, app, root):
        self.app = app
        self.root = root
        self.cfg = app.cfg

        root.title(APP_NAME)
        root.geometry("1040x680")
        root.minsize(900, 580)
        root.configure(fg_color=BG)
        root.protocol("WM_DELETE_WINDOW", self.app.hide_window)

        self._lyric_times = []
        self._hl_vis = -1
        self._hl_lines_idx = -1
        self._track_shown = _SENTINEL
        self._art_key = None
        self._panel_mode = None
        self._lyrics_shown = None
        self._flash_until = 0.0
        self._user_scroll_at = 0.0
        self._scroll_anim = None
        self._tips = []

        try:
            from tray import make_icon_image
            self._icon_img = ImageTk.PhotoImage(make_icon_image(64))
            root.iconphoto(True, self._icon_img)
        except Exception:
            pass

        self._build_header()
        self._build_pages()
        self.show_page("main")
        self.retranslate()

    # ============================================================ estructura

    def _tip(self, widget, key):
        self._tips.append(Tooltip(widget, lambda k=key: t(k)))

    def _build_header(self):
        h = ctk.CTkFrame(self.root, fg_color="transparent", height=62)
        h.pack(fill="x", padx=22, pady=(12, 0))
        h.pack_propagate(False)
        brand = ctk.CTkFrame(h, fg_color="transparent")
        brand.pack(side="left")
        ctk.CTkLabel(brand, text="Lyr", font=_font(26, "bold"),
                     text_color=TEXT).pack(side="left")
        ctk.CTkLabel(brand, text="io", font=_font(26, "bold"),
                     text_color=ACCENT).pack(side="left")

        right = ctk.CTkFrame(h, fg_color="transparent")
        right.pack(side="right")
        self.overlay_switch = ctk.CTkSwitch(
            right, text="", progress_color=ACCENT, width=44,
            command=self._on_overlay_switch, font=_font(13))
        self.overlay_switch.pack(side="left", padx=(0, 14))
        if self.cfg.overlay.get("visible", True):
            self.overlay_switch.select()
        self._tip(self.overlay_switch, "tip_overlay")

        self.settings_btn = ctk.CTkButton(
            right, text="", width=110, height=34, corner_radius=17,
            fg_color=CARD_2, hover_color=BORDER, text_color=TEXT,
            font=_font(13), command=self._toggle_settings)
        self.settings_btn.pack(side="left")

    def _build_pages(self):
        self.container = ctk.CTkFrame(self.root, fg_color="transparent")
        self.container.pack(fill="both", expand=True, padx=22, pady=(10, 8))
        self.page_main = ctk.CTkFrame(self.container, fg_color="transparent")
        self.page_settings = ctk.CTkFrame(self.container,
                                          fg_color="transparent")
        self._build_main_page()
        self._build_settings_page()
        self._build_statusbar()

    def show_page(self, name):
        self.page_main.pack_forget()
        self.page_settings.pack_forget()
        (self.page_main if name == "main" else self.page_settings).pack(
            fill="both", expand=True)
        self._page = name
        self._update_settings_btn()

    def _toggle_settings(self):
        if self._page == "settings":
            self._refresh_cache_label()
        self.show_page("settings" if self._page == "main" else "main")
        if self._page == "settings":
            self._refresh_cache_label()

    def _update_settings_btn(self):
        self.settings_btn.configure(
            text=t("back") if self._page == "settings" else t("settings"))

    # ============================================================ principal

    def _build_main_page(self):
        p = self.page_main
        p.grid_columnconfigure(1, weight=1)
        p.grid_rowconfigure(1, weight=1)

        # barra de fuentes activas (aparece cuando suenan 2+ programas)
        self.sources_frame = ctk.CTkFrame(p, fg_color=CARD_2, corner_radius=12)
        self.sources_frame.grid(row=0, column=0, columnspan=2, sticky="ew",
                                pady=(0, 8))
        self.sources_frame.grid_remove()
        self._sources_sig = None

        left = ctk.CTkFrame(p, fg_color=CARD, corner_radius=16,
                            border_width=1, border_color=BORDER, width=292)
        left.grid(row=1, column=0, sticky="nsw", padx=(0, 14))
        left.grid_propagate(False)

        self.art_label = ctk.CTkLabel(left, text="")
        self.art_label.pack(pady=(16, 8))
        self._set_art(None)

        trow = ctk.CTkFrame(left, fg_color="transparent")
        trow.pack(fill="x", padx=16)
        self.eq = EqBars(trow)
        self.eq.pack(side="left", padx=(0, 6), pady=2)
        self.title_label = ctk.CTkLabel(trow, text="", font=_font(15, "bold"),
                                        text_color=TEXT, wraplength=210,
                                        anchor="w", justify="left")
        self.title_label.pack(side="left", fill="x", expand=True)
        self.artist_label = ctk.CTkLabel(left, text="", font=_font(13),
                                         text_color=MUTED, wraplength=250)
        self.artist_label.pack(padx=16, pady=(0, 4))

        # progreso clicable + tiempos
        prow = ctk.CTkFrame(left, fg_color="transparent")
        prow.pack(fill="x", padx=18, pady=(2, 2))
        self.time_now = ctk.CTkLabel(prow, text="0:00", font=_font(11),
                                     text_color=MUTED, width=34)
        self.time_now.pack(side="left")
        self.seekbar = SeekBar(prow, self._on_seek_frac)
        self.seekbar.pack(side="left", fill="x", expand=True, padx=4)
        self.time_total = ctk.CTkLabel(prow, text="0:00", font=_font(11),
                                       text_color=MUTED, width=34)
        self.time_total.pack(side="left")
        self._tip(self.seekbar, "tip_progress")

        self.source_badge = ctk.CTkLabel(
            left, text="", font=_font(12, "bold"), corner_radius=999,
            fg_color=ACCENT_SOFT, text_color=ACCENT, height=26, padx=6)
        self.source_badge.pack(pady=(4, 2), ipadx=10)

        self.btn_publish = ctk.CTkButton(
            left, text="", height=28, corner_radius=14, font=_font(12, "bold"),
            fg_color=ACCENT_SOFT, hover_color=BORDER, text_color=ACCENT,
            command=self.app.publish_current)
        # (se hace pack solo cuando la sync es del usuario)

        off = ctk.CTkFrame(left, fg_color="transparent")
        off.pack(fill="x", padx=18, pady=(4, 0))
        self.offset_title = ctk.CTkLabel(off, text="", font=_font(12, "bold"),
                                         text_color=MUTED, anchor="w")
        self.offset_title.pack(fill="x")
        row = ctk.CTkFrame(off, fg_color="transparent")
        row.pack(pady=(2, 0))
        mk = dict(width=34, height=28, corner_radius=14, fg_color=CARD_2,
                  hover_color=BORDER, text_color=TEXT, font=_font(15, "bold"))
        b1 = ctk.CTkButton(row, text="−",
                           command=lambda: self.app.adjust_offset(-0.25), **mk)
        b1.pack(side="left")
        self.offset_value = ctk.CTkLabel(row, text="0.00 s", width=80,
                                         font=_font(13, "bold"),
                                         text_color=TEXT)
        self.offset_value.pack(side="left", padx=4)
        b2 = ctk.CTkButton(row, text="＋",
                           command=lambda: self.app.adjust_offset(+0.25), **mk)
        b2.pack(side="left")
        b3 = ctk.CTkButton(row, text="", width=60, height=28, corner_radius=14,
                           fg_color="transparent", hover_color=CARD_2,
                           text_color=MUTED, font=_font(12),
                           command=lambda: self.app.adjust_offset(None))
        b3.pack(side="left", padx=(4, 0))
        self.offset_reset = b3
        self._tip(b1, "tip_offset_minus")
        self._tip(b2, "tip_offset_plus")
        self._tip(b3, "tip_offset_reset")

        gh = ctk.CTkFrame(left, fg_color="transparent")
        gh.pack(fill="x", padx=18, pady=(6, 0))
        self.ghost_switch = ctk.CTkSwitch(
            gh, text="", progress_color=ACCENT, font=_font(13),
            text_color=TEXT, command=self._on_ghost_switch)
        self.ghost_switch.pack(anchor="w")
        if self.cfg.overlay.get("ghost"):
            self.ghost_switch.select()
        self._tip(self.ghost_switch, "tip_ghost")

        act = ctk.CTkFrame(left, fg_color="transparent")
        act.pack(fill="x", padx=18, pady=(6, 12), side="bottom")
        mkb = dict(height=31, corner_radius=15, font=_font(13),
                   fg_color=CARD_2, hover_color=BORDER, text_color=TEXT)
        self.btn_research = ctk.CTkButton(act, text="",
                                          command=self.app.reload_lyrics,
                                          **mkb)
        self.btn_research.pack(fill="x", pady=2)
        self.btn_wrong = ctk.CTkButton(act, text="",
                                       command=self._open_search, **mkb)
        self.btn_wrong.pack(fill="x", pady=2)
        self.btn_identify = ctk.CTkButton(
            act, text="", command=lambda: self.app.identify_current(), **mkb)
        self.btn_identify.pack(fill="x", pady=2)
        self._tip(self.btn_identify, "tip_identify")
        self.btn_sync = ctk.CTkButton(
            act, text="", height=32, corner_radius=16, font=_font(13, "bold"),
            fg_color=ACCENT, hover_color=("#0f7c37", "#25d165"),
            text_color=("#ffffff", "#08130c"), command=self._open_sync_editor)
        self.btn_sync.pack(fill="x", pady=2)
        self._tip(self.btn_research, "tip_research")
        self._tip(self.btn_wrong, "tip_wrong")
        self._tip(self.btn_sync, "tip_sync")

        # --- letra
        right = ctk.CTkFrame(p, fg_color=CARD, corner_radius=16,
                             border_width=1, border_color=BORDER)
        right.grid(row=1, column=1, sticky="nsew")
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self.lyrics_text = tk.Text(
            right, wrap="word", bd=0, highlightthickness=0, relief="flat",
            padx=18, pady=14, spacing1=4, spacing3=4, cursor="arrow",
            state="disabled")
        self.lyrics_text.grid(row=0, column=0, sticky="nsew", padx=(10, 0),
                              pady=10)
        self.lyrics_scrollbar = ctk.CTkScrollbar(right,
                                                 command=self._on_scrollbar)
        self.lyrics_scrollbar.grid(row=0, column=1, sticky="ns",
                                   padx=(0, 6), pady=10)
        self.lyrics_text.configure(yscrollcommand=self.lyrics_scrollbar.set)
        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.lyrics_text.bind(ev, self._on_user_scroll)

        self.now_btn = ctk.CTkButton(
            right, text="", width=76, height=30, corner_radius=15,
            font=_font(12, "bold"), fg_color=ACCENT,
            text_color=("#ffffff", "#08130c"), command=self._jump_now)
        # se coloca con place() cuando hace falta

        self.apply_text_theme()

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self.root, fg_color="transparent", height=28)
        bar.pack(fill="x", padx=26, pady=(0, 10))
        bar.pack_propagate(False)
        self.status_dot = ctk.CTkLabel(bar, text="●", font=_font(12),
                                       text_color=MUTED, width=14)
        self.status_dot.pack(side="left")
        self.status_text = ctk.CTkLabel(bar, text="", font=_font(12),
                                        text_color=MUTED)
        self.status_text.pack(side="left", padx=(4, 0))
        self.hint_text = ctk.CTkLabel(bar, text="", font=_font(12),
                                      text_color=MUTED)
        self.hint_text.pack(side="right")

    # ============================================================ ajustes

    def _build_settings_page(self):
        p = self.page_settings
        wrap = ctk.CTkScrollableFrame(p, fg_color="transparent")
        wrap.pack(fill="both", expand=True)

        def card():
            c = ctk.CTkFrame(wrap, fg_color=CARD, corner_radius=16,
                             border_width=1, border_color=BORDER)
            c.pack(fill="x", pady=(0, 12))
            return c

        def sec(parent, attr):
            lbl = ctk.CTkLabel(parent, text="", font=_font(13, "bold"),
                               text_color=MUTED, anchor="w")
            lbl.pack(fill="x", padx=20, pady=(14, 4))
            setattr(self, attr, lbl)

        seg = dict(font=_font(13), selected_color=ACCENT,
                   selected_hover_color=ACCENT)

        # idioma / tema / fuente de musica
        c1 = card()
        sec(c1, "lbl_language")
        self.lang_seg = ctk.CTkSegmentedButton(
            c1, values=["English", "Español"], command=self._on_lang, **seg)
        self.lang_seg.pack(anchor="w", padx=20, pady=(0, 4))
        self.lang_seg.set("Español" if self.cfg.get("language") == "es"
                          else "English")
        sec(c1, "lbl_theme")
        self.theme_seg = ctk.CTkSegmentedButton(
            c1, values=[t("theme_light"), t("theme_dark"), t("theme_system")],
            command=self._on_theme, **seg)
        self.theme_seg.pack(anchor="w", padx=20, pady=(0, 4))
        sec(c1, "lbl_source")
        self.source_seg = ctk.CTkSegmentedButton(
            c1, values=[t("source_spotify"), t("source_any")],
            command=self._on_source, **seg)
        self.source_seg.pack(anchor="w", padx=20, pady=(0, 16))
        self.source_seg.set(t("source_any")
                            if self.cfg.get("source_mode") == "any"
                            else t("source_spotify"))

        # overlay
        c2 = card()
        sec(c2, "lbl_overlay_style")
        rowt = ctk.CTkFrame(c2, fg_color="transparent")
        rowt.pack(fill="x", padx=20, pady=(0, 6))
        colT = ctk.CTkFrame(rowt, fg_color="transparent")
        colT.pack(side="left", padx=(0, 24))
        self.lbl_theme_style = ctk.CTkLabel(colT, text="", font=_font(13),
                                            text_color=TEXT, anchor="w")
        self.lbl_theme_style.pack(anchor="w")
        self.themestyle_seg = ctk.CTkSegmentedButton(
            colT, values=[t("theme_classic"), t("theme_cartoon"),
                          t("theme_neon"), t("theme_minimal")],
            command=self._on_theme_style, **seg)
        self.themestyle_seg.pack(anchor="w", pady=(2, 0))
        colS = ctk.CTkFrame(rowt, fg_color="transparent")
        colS.pack(side="left")
        self.lbl_sweep = ctk.CTkLabel(colS, text="", font=_font(13),
                                      text_color=TEXT, anchor="w")
        self.lbl_sweep.pack(anchor="w")
        self.sweep_seg = ctk.CTkSegmentedButton(
            colS, values=[t("sweep_word"), t("sweep_char")],
            command=self._on_sweep, **seg)
        self.sweep_seg.pack(anchor="w", pady=(2, 0))

        self.lbl_font_size = ctk.CTkLabel(c2, text="", font=_font(13),
                                          text_color=TEXT, anchor="w")
        self.lbl_font_size.pack(fill="x", padx=20)
        self.size_slider = ctk.CTkSlider(
            c2, from_=16, to=56, number_of_steps=20, progress_color=ACCENT,
            button_color=ACCENT, button_hover_color=ACCENT,
            command=self._on_size_slider)
        self.size_slider.pack(fill="x", padx=20, pady=(2, 6))
        self.size_slider.set(self.cfg.overlay.get("font_size", 30))

        self.lbl_width = ctk.CTkLabel(c2, text="", font=_font(13),
                                      text_color=TEXT, anchor="w")
        self.lbl_width.pack(fill="x", padx=20)
        self.width_slider = ctk.CTkSlider(
            c2, from_=30, to=95, number_of_steps=13, progress_color=ACCENT,
            button_color=ACCENT, button_hover_color=ACCENT,
            command=self._on_width_slider)
        self.width_slider.pack(fill="x", padx=20, pady=(2, 6))
        self.width_slider.set(self.cfg.overlay.get("width_pct", 72))

        row2 = ctk.CTkFrame(c2, fg_color="transparent")
        row2.pack(fill="x", padx=20, pady=(2, 4))
        colL = ctk.CTkFrame(row2, fg_color="transparent")
        colL.pack(side="left", padx=(0, 24))
        self.lbl_lines = ctk.CTkLabel(colL, text="", font=_font(13),
                                      text_color=TEXT, anchor="w")
        self.lbl_lines.pack(anchor="w")
        self.lines_seg = ctk.CTkSegmentedButton(
            colL, values=["1", "2", "3"], command=self._on_lines, **seg)
        self.lines_seg.pack(anchor="w", pady=(2, 0))
        self.lines_seg.set(str(self.cfg.overlay.get("lines_mode", 2)))
        colR = ctk.CTkFrame(row2, fg_color="transparent")
        colR.pack(side="left")
        self.lbl_align = ctk.CTkLabel(colR, text="", font=_font(13),
                                      text_color=TEXT, anchor="w")
        self.lbl_align.pack(anchor="w")
        self.align_seg = ctk.CTkSegmentedButton(
            colR, values=[t("align_left"), t("align_center"),
                          t("align_right")],
            command=self._on_align, **seg)
        self.align_seg.pack(anchor="w", pady=(2, 0))

        row3 = ctk.CTkFrame(c2, fg_color="transparent")
        row3.pack(fill="x", padx=20, pady=(6, 2))
        self.lbl_ovfont = ctk.CTkLabel(row3, text="", font=_font(13),
                                       text_color=TEXT, anchor="w")
        self.lbl_ovfont.pack(anchor="w")
        self.font_menu = ctk.CTkOptionMenu(
            row3, values=FONT_CHOICES, fg_color=CARD_2,
            button_color=CARD_2, button_hover_color=BORDER,
            text_color=TEXT, font=_font(13), command=self._on_font_family)
        self.font_menu.pack(anchor="w", pady=(2, 4))
        self.font_menu.set(self.cfg.overlay.get("font_family", "Segoe UI"))

        self.lbl_text_color = ctk.CTkLabel(c2, text="", font=_font(13),
                                           text_color=TEXT, anchor="w")
        self.lbl_text_color.pack(fill="x", padx=20)
        sw = ctk.CTkFrame(c2, fg_color="transparent")
        sw.pack(anchor="w", padx=20, pady=(4, 6))
        for hexcolor in SWATCHES:
            ctk.CTkButton(sw, text="", width=28, height=28, corner_radius=14,
                          fg_color=hexcolor, hover_color=hexcolor,
                          border_width=2, border_color=BORDER,
                          command=lambda h=hexcolor: self._on_color(h)
                          ).pack(side="left", padx=4)

        self.bg_switch = ctk.CTkSwitch(c2, text="", progress_color=ACCENT,
                                       font=_font(13), text_color=TEXT,
                                       command=self._on_bg_switch)
        self.bg_switch.pack(anchor="w", padx=20, pady=(2, 2))
        if self.cfg.overlay.get("bg_card"):
            self.bg_switch.select()
        self.progress_switch = ctk.CTkSwitch(c2, text="",
                                             progress_color=ACCENT,
                                             font=_font(13), text_color=TEXT,
                                             command=self._on_progress_switch)
        self.progress_switch.pack(anchor="w", padx=20, pady=(2, 2))
        if self.cfg.overlay.get("progress_line", True):
            self.progress_switch.select()

        self.btn_recenter = ctk.CTkButton(
            c2, text="", height=30, corner_radius=15, font=_font(13),
            fg_color=CARD_2, hover_color=BORDER, text_color=TEXT,
            command=self.app.recenter_overlay)
        self.btn_recenter.pack(anchor="w", padx=20, pady=(4, 16))

        # sincronizacion
        c3 = card()
        sec(c3, "lbl_global_offset")
        grow = ctk.CTkFrame(c3, fg_color="transparent")
        grow.pack(anchor="w", padx=20, pady=(0, 8))
        mk = dict(width=34, height=28, corner_radius=14, fg_color=CARD_2,
                  hover_color=BORDER, text_color=TEXT, font=_font(15, "bold"))
        ctk.CTkButton(grow, text="−",
                      command=lambda: self.app.adjust_global_offset(-0.25),
                      **mk).pack(side="left")
        self.global_offset_value = ctk.CTkLabel(grow, text="0.00 s", width=80,
                                                font=_font(13, "bold"),
                                                text_color=TEXT)
        self.global_offset_value.pack(side="left", padx=4)
        ctk.CTkButton(grow, text="＋",
                      command=lambda: self.app.adjust_global_offset(+0.25),
                      **mk).pack(side="left")
        self.global_offset_reset = ctk.CTkButton(
            grow, text="", width=60, height=28, corner_radius=14,
            fg_color="transparent", hover_color=CARD_2, text_color=MUTED,
            font=_font(12),
            command=lambda: self.app.adjust_global_offset(None))
        self.global_offset_reset.pack(side="left", padx=(4, 0))

        self.lbl_author = ctk.CTkLabel(c3, text="", font=_font(13),
                                       text_color=TEXT, anchor="w")
        self.lbl_author.pack(fill="x", padx=20)
        self.author_entry = ctk.CTkEntry(c3, width=260, font=_font(13))
        self.author_entry.pack(anchor="w", padx=20, pady=(2, 16))
        self.author_entry.insert(0, self.cfg.get("sync_author", ""))
        self.author_entry.bind(
            "<FocusOut>", lambda e: self.cfg.set(
                "sync_author", self.author_entry.get().strip()))

        # extras: traduccion, romanizacion, fiesta, movil, modelo IA
        cx = card()
        sec(cx, "lbl_extras")
        self.translate_switch = ctk.CTkSwitch(
            cx, text="", progress_color=ACCENT, font=_font(13),
            text_color=TEXT,
            command=lambda: self.app.set_translate(
                bool(self.translate_switch.get())))
        self.translate_switch.pack(anchor="w", padx=20, pady=(0, 4))
        if self.cfg.get("translate"):
            self.translate_switch.select()
        self.romanize_switch = ctk.CTkSwitch(
            cx, text="", progress_color=ACCENT, font=_font(13),
            text_color=TEXT,
            command=lambda: self.app.set_romanize(
                bool(self.romanize_switch.get())))
        self.romanize_switch.pack(anchor="w", padx=20, pady=(0, 4))
        if self.cfg.get("romanize", True):
            self.romanize_switch.select()
        rrow = ctk.CTkFrame(cx, fg_color="transparent")
        rrow.pack(fill="x", padx=20, pady=(0, 10))
        self.lbl_romanization_language = ctk.CTkLabel(
            rrow, text="", font=_font(12), text_color=MUTED)
        self.lbl_romanization_language.pack(side="left")
        self.romanization_menu = ctk.CTkOptionMenu(
            rrow, values=["auto", "ja", "zh", "ko"],
            command=self._on_romanization_language, width=160, height=28,
            fg_color=CARD_2, button_color=BORDER, text_color=TEXT,
            font=_font(12))
        self.romanization_menu.pack(side="left", padx=12)
        prow = ctk.CTkFrame(cx, fg_color="transparent")
        prow.pack(fill="x", padx=20, pady=(0, 4))
        self.phone_switch = ctk.CTkSwitch(
            prow, text="", progress_color=ACCENT, font=_font(13),
            text_color=TEXT, command=self._on_phone)
        self.phone_switch.pack(side="left")
        if self.cfg.get("phone_server"):
            self.phone_switch.select()
        self.btn_qr = ctk.CTkButton(
            prow, text="", width=110, height=26, corner_radius=13,
            fg_color=CARD_2, hover_color=BORDER, text_color=TEXT,
            font=_font(12), command=self._show_qr)
        self.btn_qr.pack(side="left", padx=12)
        arow = ctk.CTkFrame(cx, fg_color="transparent")
        arow.pack(fill="x", padx=20, pady=(0, 4))
        self.lbl_ai_model = ctk.CTkLabel(arow, text="", font=_font(13),
                                         text_color=TEXT, anchor="w")
        self.lbl_ai_model.pack(side="left")
        self.ai_seg = ctk.CTkSegmentedButton(
            arow, values=["base", "small"],
            command=self.app.set_ai_model, **seg)
        self.ai_seg.pack(side="left", padx=12)
        self.ai_seg.set(self.cfg.get("ai_model", "base"))
        self.btn_party = ctk.CTkButton(
            cx, text="", height=32, corner_radius=16, font=_font(13, "bold"),
            fg_color=ACCENT, hover_color=("#0f7c37", "#25d165"),
            text_color=("#ffffff", "#08130c"),
            command=self.app.toggle_party)
        self.btn_party.pack(anchor="w", padx=20, pady=(6, 14))

        # sistema
        c4 = card()
        sec(c4, "lbl_system")
        self.autostart_switch = ctk.CTkSwitch(
            c4, text="", progress_color=ACCENT, font=_font(13),
            text_color=TEXT, command=self._on_autostart)
        self.autostart_switch.pack(anchor="w", padx=20, pady=(0, 4))
        if self.cfg.get("autostart"):
            self.autostart_switch.select()
        self.hotkeys_switch = ctk.CTkSwitch(
            c4, text="", progress_color=ACCENT, font=_font(13),
            text_color=TEXT, command=self._on_hotkeys)
        self.hotkeys_switch.pack(anchor="w", padx=20, pady=(0, 8))
        if self.cfg.get("hotkeys", True):
            self.hotkeys_switch.select()
        crow = ctk.CTkFrame(c4, fg_color="transparent")
        crow.pack(fill="x", padx=20, pady=(0, 14))
        self.cache_label = ctk.CTkLabel(crow, text="", font=_font(12),
                                        text_color=MUTED, anchor="w")
        self.cache_label.pack(side="left")
        self.btn_clear_cache = ctk.CTkButton(
            crow, text="", width=110, height=26, corner_radius=13,
            fg_color=CARD_2, hover_color=BORDER, text_color=TEXT,
            font=_font(12), command=self._clear_cache)
        self.btn_clear_cache.pack(side="left", padx=12)

        # actualizaciones: persistent progress, never close a song automatically
        cu = card()
        sec(cu, "lbl_updates")
        self.auto_update_switch = ctk.CTkSwitch(
            cu, text="", progress_color=ACCENT, font=_font(13),
            text_color=TEXT, command=lambda: self.app.set_auto_update(
                bool(self.auto_update_switch.get())))
        self.auto_update_switch.pack(anchor="w", padx=20, pady=(0, 8))
        if self.cfg.get("auto_update", True):
            self.auto_update_switch.select()
        self.update_status = ctk.CTkLabel(
            cu, text="", font=_font(12), text_color=MUTED,
            anchor="w", justify="left", wraplength=700)
        self.update_status.pack(fill="x", padx=20, pady=(0, 10))
        urow = ctk.CTkFrame(cu, fg_color="transparent")
        urow.pack(fill="x", padx=20, pady=(0, 16))
        self.btn_check_update = ctk.CTkButton(
            urow, text="", width=180, height=30, corner_radius=15,
            fg_color=CARD_2, hover_color=BORDER, text_color=TEXT,
            font=_font(12), command=self.app.check_update_now)
        self.btn_check_update.pack(side="left")
        self.btn_restart_update = ctk.CTkButton(
            urow, text="", width=180, height=30, corner_radius=15,
            fg_color=ACCENT, text_color=("#ffffff", "#08130c"),
            font=_font(12), command=self.app.restart_for_update)
        self.btn_restart_update.pack(side="left", padx=12)

        # acerca de
        c5 = card()
        sec(c5, "lbl_about")
        self.about_text = ctk.CTkLabel(c5, text="", font=_font(13),
                                       text_color=MUTED, anchor="w",
                                       justify="left", wraplength=760)
        self.about_text.pack(fill="x", padx=20)
        ctk.CTkLabel(c5, text=f"{APP_NAME} {APP_VERSION}",
                     font=_font(12), text_color=MUTED, anchor="w"
                     ).pack(fill="x", padx=20, pady=(4, 14))

    # ============================================================ i18n

    def retranslate(self):
        self._update_settings_btn()
        self.overlay_switch.configure(text=t("overlay_switch"))
        self.offset_title.configure(text=f'{t("offset")} · {t("offset_hint")}')
        self.offset_reset.configure(text=t("reset"))
        self.ghost_switch.configure(text=t("ghost_switch"))
        self.btn_research.configure(text=t("re_search"))
        self.btn_wrong.configure(text=t("wrong_lyrics"))
        self.btn_identify.configure(text=t("identify_btn"))
        self.btn_sync.configure(text=t("sync_editor"))
        self.btn_publish.configure(text=t("publish_btn"))
        self.hint_text.configure(text=t("click_to_jump"))
        self.now_btn.configure(text=t("now_button"))

        self.lbl_language.configure(text=t("language").upper())
        self.lbl_theme.configure(text=t("theme").upper())
        self.theme_seg.configure(values=[t("theme_light"), t("theme_dark"),
                                         t("theme_system")])
        cur = self.cfg.get("appearance", "system")
        self.theme_seg.set({"light": t("theme_light"),
                            "dark": t("theme_dark"),
                            "system": t("theme_system")}[cur])
        self.lbl_source.configure(text=t("source_mode").upper())
        self.source_seg.configure(values=[t("source_spotify"),
                                          t("source_any")])
        self.source_seg.set(t("source_any")
                            if self.cfg.get("source_mode") == "any"
                            else t("source_spotify"))
        self.lbl_overlay_style.configure(text=t("overlay_style").upper())
        self.lbl_theme_style.configure(text=t("overlay_theme"))
        self.themestyle_seg.configure(
            values=[t("theme_classic"), t("theme_cartoon"),
                    t("theme_neon"), t("theme_minimal")])
        self.themestyle_seg.set({"classic": t("theme_classic"),
                                 "cartoon": t("theme_cartoon"),
                                 "neon": t("theme_neon"),
                                 "minimal": t("theme_minimal")}[
            self.cfg.overlay.get("theme", "classic")])
        self.lbl_sweep.configure(text=t("sweep_label"))
        self.sweep_seg.configure(values=[t("sweep_word"), t("sweep_char")])
        self.sweep_seg.set(t("sweep_char")
                           if self.cfg.overlay.get("sweep_mode") == "char"
                           else t("sweep_word"))
        self.lbl_font_size.configure(
            text=f'{t("font_size")}: {int(self.size_slider.get())}')
        self.lbl_width.configure(
            text=f'{t("overlay_width")}: {int(self.width_slider.get())} %')
        self.lbl_lines.configure(text=t("overlay_lines"))
        self.lbl_align.configure(text=t("overlay_align"))
        self.align_seg.configure(values=[t("align_left"), t("align_center"),
                                         t("align_right")])
        self.align_seg.set({"left": t("align_left"),
                            "center": t("align_center"),
                            "right": t("align_right")}[
            self.cfg.overlay.get("align", "center")])
        self.lbl_ovfont.configure(text=t("overlay_font"))
        self.lbl_text_color.configure(text=t("text_color"))
        self.bg_switch.configure(text=t("overlay_bg"))
        self.progress_switch.configure(text=t("overlay_progress"))
        self.btn_recenter.configure(text=t("reset_position"))
        self.lbl_global_offset.configure(text=t("global_offset").upper())
        self.global_offset_reset.configure(text=t("reset"))
        self.lbl_author.configure(text=t("sync_author_label"))
        self.lbl_system.configure(text="WINDOWS")
        self.lbl_extras.configure(text=t("lbl_extras").upper())
        self.translate_switch.configure(text=t("translate_label"))
        self.romanize_switch.configure(text=t("romanize_label"))
        self.lbl_romanization_language.configure(text=t("romanization_language"))
        self.romanization_menu.configure(values=[t("roman_lang_" + k)
                                                for k in ("auto", "ja", "zh", "ko")])
        roman_lang = self.cfg.get("romanization_language", "auto")
        if roman_lang not in ("auto", "ja", "zh", "ko"):
            roman_lang = "auto"
        self.romanization_menu.set(t("roman_lang_" + roman_lang))
        self.phone_switch.configure(text=t("phone_label"))
        self.btn_qr.configure(text=t("phone_qr_btn"))
        self.lbl_ai_model.configure(text=t("ai_model_label"))
        self.btn_party.configure(text=t("party_mode"))
        self.autostart_switch.configure(text=t("autostart"))
        self.hotkeys_switch.configure(text=t("hotkeys_label"))
        self.lbl_updates.configure(text=t("updates_title").upper())
        self.auto_update_switch.configure(text=t("auto_update_label"))
        self.btn_check_update.configure(text=t("update_check"))
        self.btn_restart_update.configure(text=t("update_restart"))
        self.refresh_update_status()
        self.btn_clear_cache.configure(text=t("clear_cache_btn"))
        self._refresh_cache_label()
        self.lbl_about.configure(text=t("about").upper())
        self.about_text.configure(text=t("about_text"))

        if self._track_shown in (None, _SENTINEL):
            self.title_label.configure(text=t("nothing_playing"))
            self.artist_label.configure(text=t("open_spotify_hint"))
        self._panel_mode = None

    def _refresh_cache_label(self):
        try:
            n, size = cache_size()
            self.cache_label.configure(
                text=t("cache_label", n=n, mb=f"{size / 1e6:.1f}"))
        except Exception:
            pass

    def _clear_cache(self):
        clear_cache()
        self._refresh_cache_label()

    def _on_romanization_language(self, label):
        for key in ("auto", "ja", "zh", "ko"):
            if label == t("roman_lang_" + key):
                self.app.set_romanization_language(key)
                break

    def refresh_update_status(self):
        state = self.app._update_state
        detail = self.app._update_detail
        if not self.app.updater.enabled:
            state = "unsupported"
        elif state == "idle" and not self.cfg.get("auto_update", True):
            state = "disabled"
        if state == "downloading":
            total = detail.get("total", 0)
            percent = int(100 * detail.get("downloaded", 0) / total) if total else 0
            message = t("update_downloading", percent=percent)
        else:
            message = t("update_" + state, v=detail.get("version", ""))
        self.update_status.configure(text=message)
        self.btn_check_update.configure(state="disabled" if state in (
            "checking", "downloading", "unsupported") else "normal")
        self.btn_restart_update.configure(state="normal" if state == "ready" else "disabled")

    # ============================================================ en vivo

    def update_now_playing(self, state, lyrics, fetching, failed, offset):
        key = state.track_key if (state.connected and state.title) else None

        self.eq.set_playing(bool(state.playing))
        if state.duration > 0:
            self.seekbar.set_frac(state.position_now() / state.duration)
            self.time_now.configure(text=_fmt_time(state.position_now()))
            self.time_total.configure(text=_fmt_time(state.duration))
        else:
            self.seekbar.set_frac(0.0)
            self.time_now.configure(text="0:00")
            self.time_total.configure(text="0:00")

        if time.time() >= self._flash_until:
            if not state.connected:
                self.status_dot.configure(text_color=MUTED)
                self.status_text.configure(text=t("waiting_spotify"))
            else:
                self.status_dot.configure(text_color=ACCENT)
                app = friendly_app_name(state.source_app)
                txt = (t("listening_to", app=app) if app and app != "Spotify"
                       else t("connected"))
                if not state.playing and state.title:
                    txt += f' · {t("paused")}'
                self.status_text.configure(text=txt)

        self._update_sources_bar(state)

        if key != self._track_shown:
            self._track_shown = key
            self._populate_track(state)

        art_key, art = self.app.watcher.get_thumbnail()
        if art_key == key and art is not None and art_key != self._art_key:
            self._art_key = art_key
            self._set_art(art)

        if key is None:
            mode = "nothing"
        elif lyrics is not None and lyrics.lines:
            mode = "lyrics"
        elif fetching:
            mode = "searching"
        else:
            mode = "no_lyrics"
        if mode != self._panel_mode or lyrics is not self._lyrics_shown:
            self._panel_mode = mode
            self._lyrics_shown = lyrics
            self._hl_lines_idx = -1
            self._render_panel(mode, lyrics)

        self._update_badge(state, lyrics, fetching, failed)
        self.offset_value.configure(text=f"{offset:+.2f} s")
        self.global_offset_value.configure(
            text=f'{float(self.cfg.get("offset_global", 0.0)):+.2f} s')

        if mode == "lyrics":
            pos = state.position_now() + offset
            idx = self.app.line_index(lyrics, pos)
            if idx != self._hl_lines_idx:
                self._highlight(idx, lyrics)
            self._update_now_btn()

    def _update_sources_bar(self, state):
        """Chips de fuentes cuando suenan 2+ programas con musica a la vez."""
        infos = self.app.watcher.get_sessions_info()
        dual_on = self.app.overlay2 is not None
        sig = (tuple((a, t_) for a, t_, _ar, _p in infos),
               state.source_app, dual_on)
        if sig == self._sources_sig:
            return
        self._sources_sig = sig
        for w in self.sources_frame.winfo_children():
            w.destroy()
        if len(infos) < 2:
            self.sources_frame.grid_remove()
            return
        self.sources_frame.grid()
        ctk.CTkLabel(self.sources_frame, text=t("sources_title"),
                     font=_font(12, "bold"), text_color=MUTED
                     ).pack(side="left", padx=(12, 8), pady=6)
        for aumid, title, _artist, playing in infos[:4]:
            name = friendly_app_name(aumid)
            short = title if len(title) <= 26 else title[:24] + "…"
            active = aumid == state.source_app
            ctk.CTkButton(
                self.sources_frame,
                text=f'{"▶ " if playing else ""}{name} · {short}',
                height=28, corner_radius=14, font=_font(12),
                fg_color=ACCENT if active else CARD,
                hover_color=BORDER,
                text_color=(("#ffffff", "#08130c") if active else TEXT),
                command=lambda a=aumid: self.app.pick_source(a)
            ).pack(side="left", padx=4, pady=6)
        ctk.CTkButton(
            self.sources_frame, text=t("both_lyrics"), height=28,
            corner_radius=14, font=_font(12, "bold"),
            fg_color=ACCENT_SOFT if not dual_on else ACCENT,
            hover_color=BORDER,
            text_color=(ACCENT if not dual_on else ("#ffffff", "#08130c")),
            command=self.app.toggle_dual
        ).pack(side="right", padx=8, pady=6)

    def _populate_track(self, state):
        if state.connected and state.title:
            self.title_label.configure(text=state.title)
            self.artist_label.configure(text=state.artist or "—")
        else:
            self.title_label.configure(text=t("nothing_playing"))
            self.artist_label.configure(text=t("open_spotify_hint"))
        self._art_key = None
        self._set_art(None)
        self._panel_mode = None

    def _update_badge(self, state, lyrics, fetching, failed):
        show_publish = False
        if not state.connected or not state.title:
            self.source_badge.configure(text="—", text_color=MUTED,
                                        fg_color=CARD_2)
            self.btn_sync.pack_forget()
        elif fetching:
            self.source_badge.configure(text=t("searching"),
                                        text_color=MUTED, fg_color=CARD_2)
        elif lyrics is None:
            self.source_badge.configure(text=t("no_lyrics"),
                                        text_color=MUTED, fg_color=CARD_2)
        elif lyrics.source == "user":
            self.source_badge.configure(text=t("source_user"),
                                        text_color=ACCENT,
                                        fg_color=ACCENT_SOFT)
            show_publish = True
        elif lyrics.source == "ai":
            self.source_badge.configure(text=t("source_ai"),
                                        text_color=ACCENT,
                                        fg_color=ACCENT_SOFT)
        elif lyrics.estimated:
            self.source_badge.configure(text=t("source_auto"),
                                        text_color=AMBER,
                                        fg_color=AMBER_SOFT)
        else:
            name = {"netease": t("source_netease"),
                    "musixmatch": "Musixmatch · sync",
                    "qq": "QQ Music · sync",
                    "kugou": "Kugou · sync"}.get(
                lyrics.source, t("source_lrclib"))
            self.source_badge.configure(text=name, text_color=ACCENT,
                                        fg_color=ACCENT_SOFT)
        if lyrics is not None and lyrics.lines and state.title:
            self.btn_sync.pack(fill="x", pady=2)
        else:
            self.btn_sync.pack_forget()
        if show_publish:
            self.btn_publish.pack(pady=(0, 2), ipadx=8)
        else:
            self.btn_publish.pack_forget()

    def _set_art(self, data):
        try:
            img = (Image.open(io.BytesIO(data)) if data else None)
            pil = (_round_image(img, ART_SIZE) if img
                   else _placeholder_art(ART_SIZE))
        except Exception:
            pil = _placeholder_art(ART_SIZE)
        self._art_img = ctk.CTkImage(light_image=pil, dark_image=pil,
                                     size=(ART_SIZE, ART_SIZE))
        self.art_label.configure(image=self._art_img)

    # -------- panel de letra

    def apply_text_theme(self):
        self.lyrics_text.configure(
            bg=_pick(CARD), fg=_pick(MUTED),
            selectbackground=_pick(ACCENT_SOFT),
            selectforeground=_pick(TEXT),
            inactiveselectbackground=_pick(ACCENT_SOFT))
        self.lyrics_text.tag_configure("norm", font=("Segoe UI", 12),
                                       foreground=_pick(MUTED))
        self.lyrics_text.tag_configure("cur", font=("Segoe UI", 14, "bold"),
                                       foreground=_pick(ACCENT))
        self.lyrics_text.tag_configure("hover", foreground=_pick(TEXT))
        self.lyrics_text.tag_configure(
            "empty_title", font=("Segoe UI", 15, "bold"),
            foreground=_pick(TEXT), justify="center", spacing1=140)
        self.lyrics_text.tag_configure(
            "empty_hint", font=("Segoe UI", 11), foreground=_pick(MUTED),
            justify="center", spacing1=8)
        self.eq.retheme()
        self.seekbar.retheme()

    def _on_scrollbar(self, *args):
        self._user_scroll_at = time.monotonic()
        self.lyrics_text.yview(*args)

    def _on_user_scroll(self, _e=None):
        self._user_scroll_at = time.monotonic()

    def _render_panel(self, mode, lyrics):
        txt = self.lyrics_text
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        self._lyric_times = []
        self._hl_vis = -1
        self._hl_lines_idx = -1

        if mode == "lyrics":
            for sec, line in lyrics.lines:
                if not line:
                    continue
                i = len(self._lyric_times) + 1
                tag = f"L{i}"
                txt.insert("end", line + "\n", ("norm", tag))
                txt.tag_bind(tag, "<Button-1>",
                             lambda e, s=sec: self.app.seek(s))
                txt.tag_bind(tag, "<Enter>",
                             lambda e, g=tag: txt.tag_add("hover", *txt.tag_ranges(g)[:2]) if txt.tag_ranges(g) else None)
                txt.tag_bind(tag, "<Leave>",
                             lambda e: txt.tag_remove("hover", "1.0", "end"))
                self._lyric_times.append(sec)
        elif mode == "searching":
            txt.insert("end", t("searching") + "\n", "empty_title")
        elif mode == "no_lyrics":
            txt.insert("end", t("no_lyrics") + "\n", "empty_title")
            txt.insert("end", t("no_lyrics_hint") + "\n", "empty_hint")
        else:
            txt.insert("end", t("nothing_playing") + "\n", "empty_title")
            txt.insert("end", t("open_spotify_hint") + "\n", "empty_hint")
        txt.configure(state="disabled")

    def _highlight(self, idx, lyrics):
        txt = self.lyrics_text
        if self._hl_vis > 0:
            txt.tag_remove("cur", f"{self._hl_vis}.0", f"{self._hl_vis}.end")
        vis = -1
        if 0 <= idx < len(lyrics.lines):
            target = lyrics.lines[idx][0]
            for i, s in enumerate(self._lyric_times):
                if s <= target + 1e-6:
                    vis = i + 1
                else:
                    break
        self._hl_lines_idx = idx
        self._hl_vis = vis
        if vis > 0:
            txt.tag_add("cur", f"{vis}.0", f"{vis}.end")
            if time.monotonic() - self._user_scroll_at > USER_SCROLL_GRACE:
                self._smooth_scroll_to(vis)

    def _target_frac(self, vis):
        total = max(1, len(self._lyric_times))
        return max(0.0, (vis - 4) / total)

    def _smooth_scroll_to(self, vis):
        target = self._target_frac(vis)
        try:
            start = self.lyrics_text.yview()[0]
        except Exception:
            return
        if self._scroll_anim:
            try:
                self.root.after_cancel(self._scroll_anim)
            except Exception:
                pass
        steps = [0.25, 0.5, 0.72, 0.88, 1.0]

        def step(i=0):
            if i >= len(steps):
                self._scroll_anim = None
                return
            f = start + (target - start) * steps[i]
            try:
                self.lyrics_text.yview_moveto(f)
            except Exception:
                return
            self._scroll_anim = self.root.after(30, lambda: step(i + 1))

        step()

    def _update_now_btn(self):
        """Boton 'Ahora' visible cuando el usuario esta hojeando la letra."""
        browsing = time.monotonic() - self._user_scroll_at < USER_SCROLL_GRACE
        if browsing and self._hl_vis > 0:
            self.now_btn.place(relx=0.86, rely=0.92)
        else:
            self.now_btn.place_forget()

    def _jump_now(self):
        self._user_scroll_at = 0.0
        if self._hl_vis > 0:
            self._smooth_scroll_to(self._hl_vis)

    # ============================================================ callbacks

    def _on_overlay_switch(self):
        self.app.set_overlay_visible(bool(self.overlay_switch.get()))

    def _on_ghost_switch(self):
        self.app.set_ghost(bool(self.ghost_switch.get()))

    def sync_ghost_switch(self, on):
        (self.ghost_switch.select if on else self.ghost_switch.deselect)()

    def sync_overlay_switch(self, on):
        (self.overlay_switch.select if on else self.overlay_switch.deselect)()

    def _on_lang(self, value):
        self.app.set_language("es" if value == "Español" else "en")

    def _on_theme(self, value):
        mapping = {t("theme_light"): "light", t("theme_dark"): "dark",
                   t("theme_system"): "system"}
        self.app.set_appearance(mapping.get(value, "system"))

    def _on_source(self, value):
        self.app.set_source_mode("any" if value == t("source_any")
                                 else "spotify")

    def _on_theme_style(self, value):
        mapping = {t("theme_classic"): "classic", t("theme_cartoon"): "cartoon",
                   t("theme_neon"): "neon", t("theme_minimal"): "minimal"}
        self.app.set_overlay_theme(mapping.get(value, "classic"))
        self.font_menu.set(self.cfg.overlay.get("font_family", "Segoe UI"))
        (self.bg_switch.select if self.cfg.overlay.get("bg_card")
         else self.bg_switch.deselect)()

    def _on_sweep(self, value):
        self.app.set_sweep_mode("char" if value == t("sweep_char") else "word")

    def _on_size_slider(self, value):
        self.lbl_font_size.configure(text=f'{t("font_size")}: {int(value)}')
        self.app.set_overlay_font(int(value))

    def _on_width_slider(self, value):
        self.lbl_width.configure(
            text=f'{t("overlay_width")}: {int(value)} %')
        self.cfg.overlay["width_pct"] = int(value)
        self.app.overlay_style_changed()

    def _on_lines(self, value):
        self.cfg.overlay["lines_mode"] = int(value)
        self.app.overlay_style_changed()

    def _on_align(self, value):
        mapping = {t("align_left"): "left", t("align_center"): "center",
                   t("align_right"): "right"}
        self.cfg.overlay["align"] = mapping.get(value, "center")
        self.app.overlay_style_changed()

    def _on_font_family(self, value):
        self.cfg.overlay["font_family"] = value
        self.app.overlay_style_changed()

    def _on_color(self, hexcolor):
        self.app.set_overlay_color(hexcolor)

    def _on_bg_switch(self):
        self.cfg.overlay["bg_card"] = bool(self.bg_switch.get())
        self.app.overlay_style_changed()

    def _on_progress_switch(self):
        self.cfg.overlay["progress_line"] = bool(self.progress_switch.get())
        self.app.overlay_style_changed()

    def _on_autostart(self):
        ok = self.app.set_autostart(bool(self.autostart_switch.get()))
        if not ok:
            (self.autostart_switch.deselect if self.autostart_switch.get()
             else self.autostart_switch.select)()

    def _on_hotkeys(self):
        self.app.set_hotkeys(bool(self.hotkeys_switch.get()))

    def _on_phone(self):
        on = bool(self.phone_switch.get())
        if not self.app.set_phone_server(on):
            (self.phone_switch.deselect if on else
             self.phone_switch.select)()

    def _show_qr(self):
        import os
        import tempfile
        from phone import make_qr_png
        if not self.app.phone.running:
            self.phone_switch.select()
            if not self.app.set_phone_server(True):
                self.phone_switch.deselect()
                return
        url = self.app.phone_url()
        png = os.path.join(tempfile.gettempdir(), "lyrio_qr.png")
        dlg = ctk.CTkToplevel(self.root)
        dlg.title(t("phone_qr_title"))
        dlg.geometry("360x460")
        dlg.transient(self.root)
        dlg.configure(fg_color=BG)
        ctk.CTkLabel(dlg, text=t("phone_qr_title"), font=_font(17, "bold"),
                     text_color=TEXT).pack(pady=(18, 6))
        if make_qr_png(url, png):
            try:
                from PIL import Image
                img = Image.open(png)
                self._qr_img = ctk.CTkImage(light_image=img, dark_image=img,
                                            size=(260, 260))
                ctk.CTkLabel(dlg, text="", image=self._qr_img).pack(pady=6)
            except Exception:
                pass
        ctk.CTkLabel(dlg, text=url, font=_font(15, "bold"),
                     text_color=ACCENT).pack(pady=(4, 2))
        ctk.CTkLabel(dlg, text=t("phone_qr_hint"), font=_font(12),
                     text_color=MUTED, wraplength=300).pack(padx=20)

    def _on_seek_frac(self, frac):
        st = self.app.watcher.get_state()
        if st.duration > 0:
            self.app.watcher.request_seek(frac * st.duration)

    def flash_status(self, text, ms=5000):
        self._flash_until = time.time() + ms / 1000.0
        self.status_dot.configure(text_color=ACCENT)
        self.status_text.configure(text=text)

    # ============================================================ dialogos

    def _open_search(self):
        st = self.app.watcher.get_state()
        if not st.title:
            return
        dlg = ctk.CTkToplevel(self.root)
        dlg.title(t("search_title"))
        dlg.geometry("420x270")
        dlg.transient(self.root)
        dlg.configure(fg_color=BG)
        state = {"cancelled": False, "busy": False}

        def close():
            state["cancelled"] = True
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", close)
        ctk.CTkLabel(dlg, text=t("search_title"), font=_font(17, "bold"),
                     text_color=TEXT).pack(pady=(18, 2))
        ctk.CTkLabel(dlg, text=t("search_help"), font=_font(12),
                     text_color=MUTED, wraplength=360).pack(padx=20)
        frm = ctk.CTkFrame(dlg, fg_color="transparent")
        frm.pack(pady=10)
        ctk.CTkLabel(frm, text=t("artist"), font=_font(12), text_color=MUTED,
                     width=60, anchor="w").grid(row=0, column=0, padx=6,
                                                pady=4)
        e_art = ctk.CTkEntry(frm, width=250, font=_font(13))
        e_art.grid(row=0, column=1, pady=4)
        e_art.insert(0, st.artist)
        ctk.CTkLabel(frm, text=t("title"), font=_font(12), text_color=MUTED,
                     width=60, anchor="w").grid(row=1, column=0, padx=6,
                                                pady=4)
        e_tit = ctk.CTkEntry(frm, width=250, font=_font(13))
        e_tit.grid(row=1, column=1, pady=4)
        e_tit.insert(0, st.title)
        msg = ctk.CTkLabel(dlg, text="", font=_font(12), text_color=MUTED)
        msg.pack()
        btns = ctk.CTkFrame(dlg, fg_color="transparent")
        btns.pack(pady=8)

        def do_search():
            if state["busy"]:
                return
            artist, title = e_art.get().strip(), e_tit.get().strip()
            if not (artist or title):
                return
            state["busy"] = True
            msg.configure(text=t("searching"))
            btn_ok.configure(state="disabled")
            key = st.track_key

            def work():
                lyr = self.app.fetch_manual(artist, title, st)

                def done():
                    if state["cancelled"] or not dlg.winfo_exists():
                        return
                    state["busy"] = False
                    if lyr:
                        self.app.apply_manual_result(key, lyr)
                        dlg.destroy()
                    else:
                        msg.configure(text=t("not_found_retry"))
                        btn_ok.configure(state="normal")

                self.app.ui_call(done)

            threading.Thread(target=work, daemon=True).start()

        btn_ok = ctk.CTkButton(btns, text=t("search_btn"), width=120,
                               height=34, corner_radius=17, fg_color=ACCENT,
                               font=_font(13, "bold"), command=do_search)
        btn_ok.pack(side="left", padx=6)
        ctk.CTkButton(btns, text=t("cancel"), width=100, height=34,
                      corner_radius=17, fg_color=CARD_2, hover_color=BORDER,
                      text_color=TEXT, font=_font(13),
                      command=close).pack(side="left", padx=6)
        e_art.focus_set()

    def _open_sync_editor(self):
        lyr = self.app.current_lyrics()
        st = self.app.watcher.get_state()
        if not lyr or not lyr.lines or not st.title:
            return
        pairs = [(sec, txt) for sec, txt in lyr.lines if txt]
        if not pairs:
            return
        track = (st.artist, st.title, st.duration)
        track_key = st.track_key

        dlg = ctk.CTkToplevel(self.root)
        dlg.title(t("sync_title"))
        dlg.geometry("580x660")
        dlg.transient(self.root)
        dlg.configure(fg_color=BG)

        ctk.CTkLabel(dlg, text=t("sync_title"), font=_font(18, "bold"),
                     text_color=TEXT).pack(pady=(14, 2))
        ctk.CTkLabel(dlg, text=t("sync_help"), font=_font(12),
                     text_color=MUTED, wraplength=520,
                     justify="left").pack(padx=20)

        prog = ctk.CTkLabel(dlg, text="", font=_font(13, "bold"),
                            text_color=ACCENT)
        prog.pack(pady=(4, 2))
        scroll = ctk.CTkScrollableFrame(dlg, fg_color=CARD, corner_radius=12)
        scroll.pack(fill="both", expand=True, padx=16, pady=6)

        times = [sec for sec, _ in pairs]
        texts = [txt for _, txt in pairs]
        sel = {"i": 0, "dirty": False, "dead": False}
        labels = []

        def fmt_row(i):
            return f"{times[i]:6.2f}  {texts[i]}"

        def refresh():
            if sel["dead"]:
                prog.configure(text=t("sync_track_changed"),
                               text_color=AMBER)
            else:
                prog.configure(text=t("line_of", a=sel["i"] + 1,
                                      b=len(texts)), text_color=ACCENT)
            for i, lbl in enumerate(labels):
                if i == sel["i"]:
                    lbl.configure(text="▶ " + fmt_row(i), text_color=TEXT,
                                  font=_font(14, "bold"))
                else:
                    lbl.configure(text="   " + fmt_row(i), text_color=MUTED,
                                  font=_font(13))
            frac = max(0.0, (sel["i"] - 4) / max(1, len(labels)))
            try:
                scroll._parent_canvas.yview_moveto(frac)
            except Exception:
                pass
            btn_save.configure(
                state="normal" if (sel["dirty"] and not sel["dead"])
                else "disabled")

        for i, txt_ in enumerate(texts):
            lbl = ctk.CTkLabel(scroll, text="", anchor="w", justify="left",
                               wraplength=470, cursor="hand2")
            lbl.pack(fill="x", padx=10, pady=1)
            lbl.bind("<Button-1>", lambda e, j=i: (sel.update(i=j),
                                                   refresh()))
            labels.append(lbl)

        def guard():
            if not dlg.winfo_exists():
                return
            now = self.app.watcher.get_state()
            if now.track_key != track_key or not now.connected:
                if not sel["dead"]:
                    sel["dead"] = True
                    refresh()
            else:
                dlg.after(500, guard)

        def stamp_now(_e=None):
            """La linea seleccionada empieza AHORA; las siguientes se
            desplazan igual (correccion en vivo)."""
            now = self.app.watcher.get_state()
            if sel["dead"] or now.track_key != track_key:
                return "break"
            if not now.playing:
                prog.configure(text=t("sync_paused_hint"), text_color=AMBER)
                return "break"
            i = sel["i"]
            delta = round(now.position_now() - times[i], 2)
            for j in range(i, len(times)):
                times[j] = round(max(0.0, times[j] + delta), 2)
            # mantener el orden si el desplazamiento cruza la linea previa
            for j in range(i, len(times)):
                if j > 0 and times[j] <= times[j - 1]:
                    times[j] = round(times[j - 1] + 0.1, 2)
            sel["dirty"] = True
            if i + 1 < len(times):
                sel["i"] = i + 1
            refresh()
            return "break"

        def nudge(d):
            if sel["dead"]:
                return
            i = sel["i"]
            times[i] = round(max(0.0, times[i] + d), 2)
            for j in range(i + 1, len(times)):
                if times[j] <= times[j - 1]:
                    times[j] = round(times[j - 1] + 0.1, 2)
            sel["dirty"] = True
            refresh()

        def save():
            if sel["dead"] or not sel["dirty"]:
                return
            self.app.apply_user_sync(track, list(zip(times, texts)))
            dlg.destroy()

        btns = ctk.CTkFrame(dlg, fg_color="transparent")
        btns.pack(pady=(4, 12))
        btn_tap = ctk.CTkButton(btns, text=t("tap"), width=190, height=44,
                                corner_radius=22, fg_color=ACCENT,
                                font=_font(15, "bold"), command=stamp_now)
        btn_tap.pack(side="left", padx=5)
        mkn = dict(width=52, height=44, corner_radius=22, fg_color=CARD_2,
                   hover_color=BORDER, text_color=TEXT,
                   font=_font(14, "bold"))
        ctk.CTkButton(btns, text="−¼s",
                      command=lambda: nudge(-0.25), **mkn).pack(side="left",
                                                                padx=3)
        ctk.CTkButton(btns, text="+¼s",
                      command=lambda: nudge(+0.25), **mkn).pack(side="left",
                                                                padx=3)
        btn_save = ctk.CTkButton(btns, text=t("save"), width=110, height=44,
                                 corner_radius=22, fg_color=CARD_2,
                                 hover_color=BORDER, text_color=TEXT,
                                 font=_font(13, "bold"), command=save)
        btn_save.pack(side="left", padx=5)

        dlg.bind("<space>", stamp_now)
        dlg.bind("<Up>", lambda e: (sel.update(i=max(0, sel["i"] - 1)),
                                    refresh(), "break")[-1])
        dlg.bind("<Down>", lambda e: (sel.update(
            i=min(len(texts) - 1, sel["i"] + 1)), refresh(), "break")[-1])
        dlg.focus_set()
        refresh()
        guard()
