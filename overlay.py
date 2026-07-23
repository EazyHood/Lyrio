# -*- coding: utf-8 -*-
"""Overlay transparente: la letra flotando en primer plano.

- Fondo 100% transparente (color-key) o tarjeta oscura opcional.
- Ancho configurable (% de pantalla), 1/2/3 lineas visibles, alineacion
  izquierda/centro/derecha, subrayado de progreso de la linea actual.
- Transicion animada al cambiar de linea (deslizamiento + fundido).
- Consciente de DPI; arrastrable; menu con clic derecho; modo fantasma.
"""
import ctypes
import re
import tkinter as tk
import tkinter.font as tkfont

from i18n import t

TRANSPARENT = "#010203"       # color-key que Windows vuelve transparente
OUTLINE = "#000000"
CARD_BG = "#0e1216"
CARD_EDGE = "#2a333c"
NEXT_COLOR = "#b9b9b9"
PREV_COLOR = "#8a8f94"
STATUS_COLOR = "#c8c8c8"

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20
GA_ROOT = 2

HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
TOPMOST_REASSERT_MS = 2000

ANIM_FRAMES = 9
ANIM_MS = 16

COLOR_KEYS = [
    ("color_white", "#ffffff"),
    ("color_green", "#1db954"),
    ("color_yellow", "#ffd93b"),
    ("color_cyan", "#4dd6ff"),
    ("color_pink", "#ff7bd5"),
]

# temas visuales del overlay (estilo de video lirico)
THEMES = {
    "classic": {"font_family": "Segoe UI", "outline_px": 2, "caps": False,
                "color": "#ffffff", "bg_card": False},
    "cartoon": {"font_family": "Segoe UI Black", "outline_px": 5, "caps": True,
                "color": "#ffffff", "bg_card": False},
    "neon": {"font_family": "Bahnschrift", "outline_px": 3, "caps": False,
             "color": "#4dd6ff", "bg_card": False},
    "minimal": {"font_family": "Segoe UI", "outline_px": 1, "caps": False,
                "color": "#ffffff", "bg_card": True},
}


def apply_theme(ov, name):
    """Aplica un preset de tema al dict de config del overlay."""
    ov["theme"] = name
    for k, v in THEMES.get(name, {}).items():
        ov[k] = v


def set_dpi_aware():
    """Marca el proceso como consciente de DPI. Llamar ANTES de crear Tk."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # SYSTEM_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _lerp_color(a, b, f):
    """Interpola dos colores hex."""
    av = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    bv = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(
        int(x + (y - x) * f) for x, y in zip(av, bv))


class LyricsOverlay:
    """controller: adjust_offset(d), reload_lyrics(), show_window(), on_quit(),
    on_ghost_changed(on)."""

    def __init__(self, master, cfg, controller=None, section="overlay"):
        self.cfg = cfg                # appconfig.Config
        self.ov = cfg.data.get(section, cfg.overlay)
        self.section = section        # overlay | overlay2 (letra secundaria)
        self.controller = controller

        self.root = tk.Toplevel(master)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT)
        self.root.attributes("-transparentcolor", TRANSPARENT)
        self.root.title("Lyrio overlay")

        # factor de escala DPI (1.0 = 96 DPI, 1.5 = 150 %)
        self.scale = self.root.winfo_fpixels("1i") / 96.0
        self._compute_width()

        self.canvas = tk.Canvas(self.root, bg=TRANSPARENT, highlightthickness=0,
                                bd=0, width=self.width, height=10)
        self.canvas.pack(fill="both", expand=True)

        self._prev = ""
        self._current = ""
        self._next = ""
        self._extra = ""              # traduccion / romanizacion de la actual
        self._status = ""
        self._progress = 0.0
        self._progress_geom = None    # (x0, y, maxw) del subrayado
        self._progress_items = None   # (fondo, relleno)
        self._sweep = None            # karaoke: (item, texto, chars_actuales)
        self._drag = None
        self._destroyed = False
        self._anim_job = None
        self._anim_t = 1.0

        self.canvas.bind("<Button-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", lambda e: self._save_pos())
        self.canvas.bind("<Button-3>", self._show_menu)

        self._build_grip()
        self._apply_fonts()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x, y = self._initial_pos(sw, sh)
        self.root.geometry(f"{self.width}x{self._min_height()}+{x}+{y}")
        self.root.update_idletasks()
        if self.ov.get("ghost"):
            self._apply_ghost(True)
        if not self.ov.get("visible", True):
            self.root.withdraw()
        self._redraw()
        self._keep_on_top()

    # ------------------------------------------------------------- helpers

    def px(self, logical):
        return int(round(logical * self.scale))

    def _compute_width(self):
        sw = self.root.winfo_screenwidth()
        pct = max(30, min(95, int(self.ov.get("width_pct", 72))))
        self.width = max(self.px(300), min(int(sw * pct / 100), sw - self.px(16)))

    def _initial_pos(self, sw, sh):
        """Valida la posicion guardada contra el escritorio VIRTUAL completo."""
        x, y = self.ov.get("x"), self.ov.get("y")
        w = self.width
        try:
            u = ctypes.windll.user32
            vx, vy = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
            vw, vh = u.GetSystemMetrics(78), u.GetSystemMetrics(79)
        except Exception:
            vx, vy, vw, vh = 0, 0, sw, sh
        yfrac = 0.08 if self.section == "overlay2" else 0.80
        if x is None or y is None or not (vx - w < x < vx + vw - 40) or \
                not (vy - 20 <= y < vy + vh - 40):
            return (sw - w) // 2, int(sh * yfrac)
        return int(x), int(y)

    # ------------------------------------------------------------- publico

    def render(self, current, nxt, status="", prev="", extra=""):
        if self._destroyed:
            return
        changed_line = current != self._current and current and self._current
        if (prev, current, nxt, status, extra) == (
                self._prev, self._current, self._next, self._status,
                self._extra):
            return
        self._prev, self._current, self._next = prev, current, nxt
        self._status = status
        self._extra = extra
        if changed_line:
            self._animate_line()
        else:
            self._redraw()

    def update_progress(self, frac):
        """Progreso dentro de la linea: barrido karaoke o subrayado (barato)."""
        if self._destroyed:
            return
        frac = max(0.0, min(1.0, frac))
        if abs(frac - self._progress) < 0.01:
            return
        self._progress = frac
        try:
            if self._sweep is not None:
                item, text, shown, words = self._sweep
                n = self._sweep_chars(frac, text, words)
                if n != shown:
                    self.canvas.itemconfigure(item, text=text[:n])
                    self._sweep = (item, text, n, words)
            elif self._progress_items:
                x0, y, maxw = self._progress_geom
                self.canvas.coords(self._progress_items[1],
                                   x0, y, x0 + maxw * frac, y)
        except Exception:
            pass

    def _sweep_chars(self, frac, text, words):
        """Cuantos caracteres 'cantados' mostrar. Modo word: la palabra entera
        se enciende al entrar en ella; modo char: barrido letra a letra."""
        n_raw = frac * len(text)
        if self.ov.get("sweep_mode", "word") != "word" or not words:
            return int(round(n_raw))
        n = 0
        for s, e in words:
            if n_raw >= s + 0.35 * (e - s):   # entro a la palabra
                n = e
            else:
                break
        return n

    def set_visible(self, visible):
        self.ov["visible"] = bool(visible)
        self.cfg.save()
        if self._destroyed:
            return
        if visible:
            self.root.deiconify()
            if self.ov.get("ghost"):
                self._place_grip()
                self.grip.deiconify()
        else:
            self.root.withdraw()
            self.grip.withdraw()

    def set_ghost(self, on):
        self._apply_ghost(on)

    def apply_style(self):
        """Refresca fuente/ancho/alineacion tras cambios desde Ajustes."""
        if self._destroyed:
            return
        old_w = self.width
        self._compute_width()
        self._apply_fonts()
        if self.width != old_w:
            # mantener el centro visual al cambiar el ancho
            cx = self.root.winfo_x() + old_w // 2
            nx = max(0, cx - self.width // 2)
            self.canvas.configure(width=self.width)
            self.root.geometry(f"{self.width}x{self._min_height()}+{nx}"
                               f"+{self.root.winfo_y()}")
            self._save_pos()
        self._redraw()

    def recenter(self):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"+{(sw - self.width) // 2}+{int(sh * 0.80)}")
        self._place_grip()
        self._save_pos()

    def destroy(self):
        self._destroyed = True
        try:
            self.grip.destroy()
            self.root.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------- dibujo

    def _apply_fonts(self):
        fam = self.ov.get("font_family", "Segoe UI")
        size = self.px(int(self.ov.get("font_size", 30)))
        self.font_main = tkfont.Font(family=fam, size=-size, weight="bold")
        self.font_ctx = tkfont.Font(family=fam,
                                    size=-max(self.px(13), int(size * 0.58)),
                                    weight="bold")
        self.font_status = tkfont.Font(family="Segoe UI", size=-self.px(12),
                                       weight="bold")

    def _min_height(self):
        mode = int(self.ov.get("lines_mode", 2))
        h = self.font_main.metrics("linespace") * 1.6
        if mode >= 2:
            h += self.font_ctx.metrics("linespace") * 1.5
        if mode >= 3:
            h += self.font_ctx.metrics("linespace") * 1.5
        h += self.font_status.metrics("linespace") + self.px(26)
        return int(h)

    def _anchor_x(self):
        align = self.ov.get("align", "center")
        pad = self.px(18)
        if align == "left":
            return "nw", pad, "left"
        if align == "right":
            return "ne", self.width - pad, "right"
        return "n", self.width // 2, "center"

    def _outlined_text(self, x, y, text, font, fill, anchor, justify, width):
        kw = dict(text=text, font=font, anchor=anchor, width=width,
                  justify=justify)
        opx = int(self.ov.get("outline_px", 2))
        o = self.px(opx) if font is self.font_main else max(1, self.px(1))
        offs = [(-o, 0), (o, 0), (0, -o), (0, o),
                (-o, -o), (-o, o), (o, -o), (o, o)]
        if o >= self.px(4):      # contorno gordo estilo cartoon: sin huecos
            h = o // 2
            offs += [(-o, -h), (-o, h), (o, -h), (o, h),
                     (-h, -o), (h, -o), (-h, o), (h, o)]
        for dx, dy in offs:
            self.canvas.create_text(x + dx, y + dy, fill=OUTLINE, **kw)
        return self.canvas.create_text(x, y, fill=fill, **kw)

    def _redraw(self, anim_t=1.0):
        c = self.canvas
        c.delete("all")
        self._progress_items = None
        anchor, ax, justify = self._anchor_x()
        wrap = self.width - self.px(36)
        mode = int(self.ov.get("lines_mode", 2))
        color = self.ov.get("color", "#ffffff")
        y = self.px(8)

        if self._status:
            self._outlined_text(ax, y, self._status, self.font_status,
                                STATUS_COLOR, anchor, justify, wrap)
            y = c.bbox("all")[3] + self.px(6)

        if mode >= 3 and self._prev:
            ptxt = self._prev.upper() if self.ov.get("caps") else self._prev
            self._outlined_text(ax, y, ptxt, self.font_ctx, PREV_COLOR,
                                anchor, justify, wrap)
            y = c.bbox("all")[3] + self.px(6)

        cur_top = y
        self._sweep = None
        caps = bool(self.ov.get("caps"))
        cur_text = self._current.upper() if caps else self._current
        if self._current:
            # animacion: entra deslizandose desde abajo y aclarando el color
            dy = int((1.0 - anim_t) * self.px(16))
            one_row = self.font_main.measure(cur_text) <= wrap
            karaoke = (one_row and anim_t >= 1.0 and
                       self.ov.get("progress_line", True))
            # con karaoke: la base va atenuada y el barrido la va "cantando"
            base_fill = (_lerp_color(color, "#6a6a6a", 0.55) if karaoke
                         else (_lerp_color("#777777", color, anim_t)
                               if anim_t < 1 else color))
            self._outlined_text(ax, y + dy, cur_text, self.font_main,
                                base_fill, anchor, justify, wrap)
            if karaoke:
                tw = self.font_main.measure(cur_text)
                if anchor == "nw":
                    x0 = ax
                elif anchor == "ne":
                    x0 = ax - tw
                else:
                    x0 = ax - tw // 2
                # limites de palabra para el barrido por palabra completa
                words = [(m.start(), m.end())
                         for m in re.finditer(r"\S+", cur_text)]
                n0 = self._sweep_chars(self._progress, cur_text, words)
                item = self.canvas.create_text(
                    x0, y, text=cur_text[:n0], font=self.font_main,
                    fill=color, anchor="nw")
                self._sweep = (item, cur_text, n0, words)
            bottom = c.bbox("all")[3]
            # subrayado de progreso (respaldo para lineas envueltas)
            if self.ov.get("progress_line", True) and anim_t >= 1.0 and \
                    not karaoke:
                ly = bottom + self.px(6)
                maxw = min(wrap, self.px(260))
                if anchor == "nw":
                    x0 = ax
                elif anchor == "ne":
                    x0 = ax - maxw
                else:
                    x0 = ax - maxw // 2
                track = c.create_line(x0, ly, x0 + maxw, ly,
                                      fill="#3a3f45", width=self.px(3),
                                      capstyle="round")
                fillbar = c.create_line(x0, ly, x0 + maxw * self._progress, ly,
                                        fill=color, width=self.px(3),
                                        capstyle="round")
                self._progress_geom = (x0, ly, maxw)
                self._progress_items = (track, fillbar)
                bottom = ly
            y = bottom + self.px(8)
        else:
            y += self.font_main.metrics("linespace")

        if self._extra:
            # traduccion / romanizacion de la linea actual
            self._outlined_text(ax, y, self._extra, self.font_ctx, "#9fd6b4",
                                anchor, justify, wrap)
            y = c.bbox("all")[3] + self.px(6)

        if mode >= 2 and self._next:
            ntxt = self._next.upper() if self.ov.get("caps") else self._next
            self._outlined_text(ax, y, ntxt, self.font_ctx, NEXT_COLOR,
                                anchor, justify, wrap)

        # tarjeta de fondo opcional (detras de todo el contenido)
        bbox = c.bbox("all")
        if bbox and self.ov.get("bg_card"):
            p = self.px(14)
            r = self.px(12)
            x0, y0, x1, y1 = (bbox[0] - p, bbox[1] - p,
                              bbox[2] + p, bbox[3] + p)
            card = self._round_rect(x0, y0, x1, y1, r, CARD_BG, CARD_EDGE)
            for item in card:
                c.tag_lower(item)
            bbox = c.bbox("all")

        needed = max(self._min_height(),
                     (bbox[3] + self.px(10)) if bbox else 0)
        if abs(needed - self.root.winfo_height()) > self.px(6):
            self.root.geometry(f"{self.width}x{int(needed)}")

    def _round_rect(self, x0, y0, x1, y1, r, fill, edge):
        """Rectangulo redondeado como poligono suavizado."""
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r,
               x1, y1, x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r,
               x0, y0 + r, x0, y0]
        return [self.canvas.create_polygon(
            pts, smooth=True, fill=fill, outline=edge, width=1)]

    def _animate_line(self):
        if self._anim_job:
            try:
                self.root.after_cancel(self._anim_job)
            except Exception:
                pass
        self._anim_t = 0.0

        def step():
            self._anim_t = min(1.0, self._anim_t + 1.0 / ANIM_FRAMES)
            # easing suave (ease-out cubico)
            e = 1.0 - (1.0 - self._anim_t) ** 3
            self._redraw(anim_t=e)
            if self._anim_t < 1.0 and not self._destroyed:
                self._anim_job = self.root.after(ANIM_MS, step)
            else:
                self._anim_job = None

        step()

    # ------------------------------------------------------------- arrastre

    def _drag_start(self, e):
        self._drag = (e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _drag_move(self, e):
        if not self._drag:
            return
        x0, y0, wx, wy = self._drag
        self.root.geometry(f"+{wx + e.x_root - x0}+{wy + e.y_root - y0}")
        self._place_grip()

    def _save_pos(self):
        self.ov["x"] = self.root.winfo_x()
        self.ov["y"] = self.root.winfo_y()
        self.cfg.save()

    # ------------------------------------------------------------- menu

    def _show_menu(self, e):
        m = tk.Menu(self.root, tearoff=0)
        c = self.controller
        m.add_command(label=t("open_app"),
                      command=lambda: c and c.show_window())
        m.add_separator()
        # la posicion mostrada es pos+offset: offset POSITIVO adelanta la letra
        m.add_command(label=t("forward_half"),
                      command=lambda: c and c.adjust_offset(+0.5))
        m.add_command(label=t("backward_half"),
                      command=lambda: c and c.adjust_offset(-0.5))
        m.add_command(label=t("reset_offset"),
                      command=lambda: c and c.adjust_offset(None))
        m.add_separator()
        m.add_command(label=t("bigger"), command=lambda: self._font_delta(+3))
        m.add_command(label=t("smaller"), command=lambda: self._font_delta(-3))
        colm = tk.Menu(m, tearoff=0)
        for key, hexcolor in COLOR_KEYS:
            colm.add_command(label=t(key),
                             command=lambda h=hexcolor: self._set_color(h))
        m.add_cascade(label=t("color_menu"), menu=colm)
        m.add_separator()
        m.add_command(label=t("ghost_on"),
                      command=lambda: self._apply_ghost(True))
        m.add_command(label=t("reload"),
                      command=lambda: c and c.reload_lyrics())
        m.add_separator()
        m.add_command(label=t("quit"),
                      command=lambda: c and c.on_quit())
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def _font_delta(self, d):
        self.ov["font_size"] = max(14, min(64, int(self.ov.get("font_size", 30)) + d))
        self.cfg.save()
        self.apply_style()

    def _set_color(self, hexcolor):
        self.ov["color"] = hexcolor
        self.cfg.save()
        self._redraw()

    # ------------------------------------------------------------- fantasma

    def _hwnd(self):
        return ctypes.windll.user32.GetAncestor(self.root.winfo_id(), GA_ROOT)

    def _keep_on_top(self):
        if self._destroyed:
            return
        try:
            u = ctypes.windll.user32
            for hwnd in (self._hwnd(),
                         u.GetAncestor(self.grip.winfo_id(), GA_ROOT)):
                if hwnd:
                    # HWND_TOP (0): re-eleva DENTRO de la banda topmost; con
                    # HWND_TOPMOST no sube si otra app topmost se puso encima
                    u.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                                   SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception:
            pass
        self.root.after(TOPMOST_REASSERT_MS, self._keep_on_top)

    def _apply_ghost(self, on):
        hwnd = self._hwnd()
        u = ctypes.windll.user32
        style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style | WS_EX_TRANSPARENT) if on else (style & ~WS_EX_TRANSPARENT)
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        self.ov["ghost"] = bool(on)
        self.cfg.save()
        if on and self.ov.get("visible", True):
            self._place_grip()
            self.grip.deiconify()
        else:
            self.grip.withdraw()
        if self.controller:
            self.controller.on_ghost_changed(bool(on))

    def _build_grip(self):
        s = self.px(14)
        self.grip = tk.Toplevel(self.root)
        self.grip.overrideredirect(True)
        self.grip.attributes("-topmost", True)
        self.grip.configure(bg="#1db954")
        self.grip.geometry(f"{s}x{s}")
        self.grip.withdraw()
        lbl = tk.Label(self.grip, text="", bg="#1db954")
        lbl.pack(fill="both", expand=True)
        for w in (self.grip, lbl):
            w.bind("<Button-1>", self._grip_drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", lambda e: self._save_pos())
            w.bind("<Button-3>", self._grip_menu)
            w.bind("<Double-Button-1>", lambda e: self._apply_ghost(False))

    def _place_grip(self):
        gx = self.root.winfo_x() - self.px(18)
        try:
            vleft = ctypes.windll.user32.GetSystemMetrics(76)
        except Exception:
            vleft = 0
        if gx < vleft:
            gx = self.root.winfo_x() + self.px(4)
        self.grip.geometry(f"+{gx}+{self.root.winfo_y()}")

    def _grip_drag_start(self, e):
        self._drag = (e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _grip_menu(self, e):
        m = tk.Menu(self.grip, tearoff=0)
        m.add_command(label=t("ghost_off"),
                      command=lambda: self._apply_ghost(False))
        m.add_command(label=t("open_app"),
                      command=lambda: self.controller and
                      self.controller.show_window())
        m.add_command(label=t("quit"),
                      command=lambda: self.controller and
                      self.controller.on_quit())
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()
