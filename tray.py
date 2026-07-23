# -*- coding: utf-8 -*-
"""Icono de Lyrio en la bandeja del sistema (pystray)."""
import pystray
from PIL import Image, ImageDraw

from i18n import t
from appconfig import APP_NAME


def make_icon_image(size=64):
    """Nota musical verde sobre placa redondeada oscura."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, size - 2, size - 2), radius=size // 5,
                        fill=(16, 20, 23, 255))
    s = size / 64.0
    def sc(*v):
        return tuple(int(round(x * s)) for x in v)
    green = (29, 185, 84, 255)
    d.ellipse(sc(14, 40, 30, 52), fill=green)
    d.rectangle(sc(26, 14, 30, 46), fill=green)
    d.polygon([sc(26, 14)[:2], sc(48, 9)[:2], sc(48, 19)[:2], sc(26, 24)[:2]],
              fill=green)
    d.ellipse(sc(36, 34, 50, 45), fill=green)
    d.rectangle(sc(46, 12, 50, 39), fill=green)
    return img


class Tray:
    """app: controlador con show_window(), toggle_overlay(), on_quit()."""

    def __init__(self, app):
        self.app = app
        self.icon = pystray.Icon(
            APP_NAME, make_icon_image(), APP_NAME, menu=self._menu())

    def _menu(self):
        return pystray.Menu(
            pystray.MenuItem(lambda item: t("open_app"),
                             lambda: self.app.ui_call(self.app.show_window),
                             default=True),
            pystray.MenuItem(
                lambda item: (t("hide_lyrics")
                              if self.app.cfg.overlay.get("visible", True)
                              else t("show_lyrics")),
                lambda: self.app.ui_call(self.app.toggle_overlay)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda item: t("quit"),
                             lambda: self.app.ui_call(self.app.on_quit)),
        )

    def start(self):
        self.icon.run_detached()

    def refresh(self):
        try:
            self.icon.update_menu()
        except Exception:
            pass

    def notify(self, message):
        try:
            self.icon.notify(message, APP_NAME)
        except Exception:
            pass

    def stop(self):
        try:
            self.icon.stop()
        except Exception:
            pass
