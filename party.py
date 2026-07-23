# -*- coding: utf-8 -*-
"""Modo fiesta: letra a pantalla completa para TV o proyector.

Fondo negro, linea actual gigante en el color del overlay, anterior y
siguiente tenues, titulo/artista arriba. Esc o clic para salir.
"""
import tkinter as tk
import tkinter.font as tkfont

from i18n import t


class PartyWindow:
    def __init__(self, master, cfg, on_close=None):
        self.cfg = cfg
        self.on_close = on_close
        self.root = tk.Toplevel(master)
        self.root.configure(bg="#000000")
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self._destroyed = False

        sw = self.root.winfo_screenwidth()
        self.scale = self.root.winfo_fpixels("1i") / 96.0
        big = max(28, int(sw / 26))
        self.f_big = tkfont.Font(family="Segoe UI", size=-int(big * 1.6),
                                 weight="bold")
        self.f_ctx = tkfont.Font(family="Segoe UI", size=-big)
        self.f_head = tkfont.Font(family="Segoe UI",
                                  size=-max(16, int(big * 0.55)),
                                  weight="bold")

        self.head = tk.Label(self.root, text="", bg="#000000", fg="#8b939b",
                             font=self.f_head, anchor="w")
        self.head.pack(fill="x", padx=40, pady=(28, 0))

        wrap = sw - 160
        center = tk.Frame(self.root, bg="#000000")
        center.pack(expand=True, fill="both")
        self.lbl_prev = tk.Label(center, text="", bg="#000000", fg="#4a5056",
                                 font=self.f_ctx, wraplength=wrap)
        self.lbl_prev.pack(expand=True)
        self.lbl_cur = tk.Label(center, text="", bg="#000000",
                                fg=cfg.overlay.get("color", "#1db954"),
                                font=self.f_big, wraplength=wrap)
        self.lbl_cur.pack(expand=True)
        self.lbl_next = tk.Label(center, text="", bg="#000000", fg="#6b7278",
                                 font=self.f_ctx, wraplength=wrap)
        self.lbl_next.pack(expand=True)
        self.hint = tk.Label(self.root, text=t("party_exit_hint"),
                             bg="#000000", fg="#3a4046", font=self.f_head)
        self.hint.pack(pady=(0, 18))
        self.root.after(6000, lambda: self._alive() and
                        self.hint.configure(text=""))

        for seq in ("<Escape>", "<Button-1>"):
            self.root.bind(seq, lambda e: self.close())
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.focus_force()

    def _alive(self):
        return not self._destroyed

    def render(self, title, artist, prev, current, nxt):
        if self._destroyed:
            return
        head = f"{artist} — {title}" if artist else title
        if self.head.cget("text") != head:
            self.head.configure(text=head)
        for lbl, txt in ((self.lbl_prev, prev), (self.lbl_cur, current),
                         (self.lbl_next, nxt)):
            if lbl.cget("text") != txt:
                lbl.configure(text=txt)

    def close(self):
        if self._destroyed:
            return
        self._destroyed = True
        try:
            self.root.destroy()
        except Exception:
            pass
        if self.on_close:
            self.on_close()
