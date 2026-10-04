# -*- coding: utf-8 -*-
"""Lyrio - Spotify lyrics floating on your screen.

Detecta la cancion de Spotify (via Windows, sin API de pago), busca la letra
sincronizada en internet, sincroniza sola las que no traen tiempos, y la
muestra en un overlay transparente + una ventana con la letra completa.
"""
import bisect
import logging
import os
import sys
import threading
import time

if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--self-test":
    from diagnostics import run_self_test
    sys.exit(run_self_test(sys.argv[2]))

from overlay import set_dpi_aware
set_dpi_aware()   # antes de crear cualquier ventana

import customtkinter as ctk

import i18n
from appconfig import (Config, set_autostart, CONFIG_DIR, APP_VERSION,
                       acquire_single_instance, watch_show_event)
from audiocal import AudioCalibrator
from autosyncai import AutoSyncAI
from hotkeys import GlobalHotkeys
from i18n import t
from lyrics import (fetch_lyrics, clear_cache_entry, save_offset, load_offset,
                    save_user_sync, save_ai_sync, apply_anchor,
                    publish_to_lrclib, clean_video_query)
from overlay import LyricsOverlay
from player import SpotifyWatcher
from recognize import identify_song
from tray import Tray
from ui import MainWindow, BG
from display_lyrics import prepare_display_lyrics
from timing import word_progress
from updater import AutoUpdater, UpdateError, UpdateCancelled

os.makedirs(CONFIG_DIR, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(CONFIG_DIR, "lyrio.log"),
    level=logging.INFO,
    format="%(asctime)s pid=%(process)d %(levelname)s %(message)s",
)
log = logging.getLogger("lyrio")

TICK_MS = 150
ESTIMATED_NOTICE_SECONDS = 8
FAILED_RETRY_SECONDS = 15       # reintentar busqueda fallida (microcortes de red)


def _is_ad(state):
    # solo Spotify inserta anuncios asi; en otros reproductores (YouTube etc.)
    # es normal que no haya campo de artista
    if "spotify" not in (state.source_app or "").lower():
        return False
    return not state.artist or state.title.lower() in ("advertisement", "spotify")


class LyrioApp:
    def __init__(self):
        self.cfg = Config()
        i18n.set_language(self.cfg.get("language", "en"))
        ctk.set_appearance_mode(self.cfg.get("appearance", "system"))

        self._lock = threading.Lock()
        self._lyrics = None
        self._lyrics_key = None
        self._fetching_key = None
        self._failed = {}              # track_key -> monotonic del fallo
        self._loaded_at = 0.0
        self._offsets = {}             # track_key -> ajuste manual
        self._current_key = None
        self._stop_event = threading.Event()
        self._display_cache = []
        self.updater = AutoUpdater(APP_VERSION)
        self._update_state = "idle" if self.updater.enabled else "unsupported"
        self._update_detail = {}
        self._update_wake = threading.Event()
        self._update_requested = False
        self._quitting = False

        self.root = ctk.CTk()
        self.root.configure(fg_color=BG)
        self.watcher = SpotifyWatcher(
            on_track_change=self._on_track_change,
            source_mode=self.cfg.get("source_mode", "spotify"))
        self.window = MainWindow(self, self.root)
        self.overlay = LyricsOverlay(self.root, self.cfg, controller=self)
        self.tray = Tray(self)

        self.audiocal = AudioCalibrator(self._on_audio_anchor)
        self._cal_state = None
        self.overlay2 = None           # segunda letra flotante (modo dual)
        self._lyrics2 = None
        self._key2 = None
        self._fetching2 = None
        self._id_tried = set()         # tracks ya identificados por audio
        self._identifying = False
        self.autosync_ai = AutoSyncAI(
            self._on_ai_sync,
            model_getter=lambda: self.cfg.get("ai_model", "base"))
        self._ai_tried = set()
        self._radio = None             # modo radio: sin sesion SMTC
        self._radio_last_try = 0.0
        self._last_playing_mono = time.monotonic()
        from translate import Translator
        self.translator = Translator()
        self.party = None              # modo fiesta (pantalla completa)
        self._now = {}                 # estado publico (companero movil)
        from phone import PhoneServer
        self.phone = PhoneServer(lambda: dict(self._now))
        if self.cfg.get("phone_server"):
            self.phone.start()
        threading.Thread(target=self._check_update, daemon=True,
                         name="update-check").start()
        self.hotkeys = GlobalHotkeys(
            lambda action: self.ui_call(self._hotkey, action))
        if self.cfg.get("hotkeys", True):
            self.hotkeys.start()

        self.watcher.start()
        self.tray.start()
        watch_show_event(lambda: self.ui_call(self.show_window),
                         self._stop_event)
        self.root.after(TICK_MS, self._tick)

        if "--hidden" in sys.argv:
            self.root.withdraw()
        if "--settings" in sys.argv:   # solo para depuracion visual
            self.window.show_page("settings")

    # ================================================= helpers de datos

    def current_lyrics(self):
        with self._lock:
            return self._lyrics if self._lyrics_key == self._current_key else None

    def current_offset(self):
        key = self._current_key
        base = float(self.cfg.get("offset_global", 0.0))
        with self._lock:
            return base + self._offsets.get(key, 0.0)

    def display_lyrics(self, lyrics):
        """Keep display text separate from the source used to edit/publish sync."""
        if lyrics is None:
            return None
        options = (self.cfg.get("romanize", True),
                   self.cfg.get("romanization_language", "auto"))
        for source, opts, display in self._display_cache:
            if source is lyrics and opts == options:
                return display
        display = prepare_display_lyrics(lyrics, enabled=options[0],
                                         language=options[1])
        self._display_cache.append((lyrics, options, display))
        self._display_cache = self._display_cache[-4:]
        return display

    @staticmethod
    def line_index(lyrics, pos):
        times = [s for s, _ in lyrics.lines]
        return bisect.bisect_right(times, pos) - 1

    # ================================================= marshaling de hilos

    def ui_call(self, fn, *a):
        """Ejecuta fn en el hilo de la UI (seguro desde bandeja/watcher/workers)."""
        try:
            self.root.after(0, lambda: fn(*a))
        except Exception:
            pass

    # ================================================= controller (UI/overlay)

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide_window(self):
        self.root.withdraw()
        if not self.cfg.get("tray_notice_shown"):
            self.cfg.set("tray_notice_shown", True)
            try:
                self.tray.notify(t("still_running"))
            except Exception:
                pass

    def toggle_overlay(self):
        self.set_overlay_visible(not self.cfg.overlay.get("visible", True))

    def set_overlay_visible(self, visible):
        self.overlay.set_visible(visible)
        self.window.sync_overlay_switch(visible)
        self.tray.refresh()

    def set_ghost(self, on):
        self.overlay.set_ghost(on)

    def on_ghost_changed(self, on):
        self.window.sync_ghost_switch(on)
        if on:
            self.window.flash_status(t("ghost_hint"), ms=7000)

    def recenter_overlay(self):
        self.overlay.recenter()

    def set_overlay_theme(self, name):
        from overlay import apply_theme
        apply_theme(self.cfg.overlay, name)
        self.cfg.save()
        self.overlay.apply_style()

    def set_sweep_mode(self, mode):
        self.cfg.overlay["sweep_mode"] = mode
        self.cfg.save()
        self.overlay.apply_style()

    def set_overlay_font(self, size):
        self.cfg.overlay["font_size"] = int(size)
        self.cfg.save()
        self.overlay.apply_style()

    def set_overlay_color(self, hexcolor):
        self.cfg.overlay["color"] = hexcolor
        self.cfg.save()
        self.overlay.apply_style()

    def set_show_next(self, on):
        self.cfg.overlay["show_next"] = bool(on)
        self.cfg.save()
        self.overlay.apply_style()

    def set_language(self, lang):
        self.cfg.set("language", lang)
        i18n.set_language(lang)
        self.window.retranslate()
        self.tray.refresh()

    def set_appearance(self, mode):
        self.cfg.set("appearance", mode)
        ctk.set_appearance_mode(mode)
        self.window.apply_text_theme()

    def set_autostart(self, enabled):
        ok = set_autostart(enabled)
        if ok:
            self.cfg.set("autostart", enabled)
        return ok

    def set_source_mode(self, mode):
        self.cfg.set("source_mode", mode)
        self.watcher.set_source_mode(mode)

    # ------------------------------------- multi-fuente / doble letra

    def pick_source(self, aumid):
        """El usuario elige que app manda (chips de fuentes)."""
        self.watcher.set_pinned(aumid)
        if self.overlay2 and aumid == getattr(self, "_dual_aumid", None):
            self.toggle_dual()     # la fuente dual paso a ser la principal

    def toggle_dual(self, other_aumid=None):
        """Activa/desactiva la segunda letra flotante."""
        from overlay import LyricsOverlay
        if self.overlay2:
            self.audio2 = None
            self.watcher.set_secondary(None)
            self.overlay2.destroy()
            self.overlay2 = None
            self._lyrics2 = None
            self._key2 = None
            return
        if not other_aumid:
            cur = self.watcher.get_state().source_app
            for aumid, _t, _a, _p in self.watcher.get_sessions_info():
                if aumid != cur:
                    other_aumid = aumid
                    break
        if not other_aumid:
            return
        self._dual_aumid = other_aumid
        self.watcher.set_secondary(other_aumid)

        class _Shim:
            show_window = self.show_window
            on_quit = self.on_quit
            reload_lyrics = self.reload_lyrics2
            adjust_offset = self.adjust_offset2
            on_ghost_changed = staticmethod(lambda on: None)

        self.overlay2 = LyricsOverlay(self.root, self.cfg, controller=_Shim(),
                                      section="overlay2")

    def _tick_secondary(self):
        if not self.overlay2:
            return
        st = self.watcher.get_state2()
        if not st.connected or not st.title:
            self.overlay2.render("", "", "…")
            return
        key = st.track_key
        if key != self._key2:
            self._key2 = key
            self._lyrics2 = None
            if self._fetching2 != key:
                self._fetching2 = key

                def work():
                    a, ti = clean_video_query(st.artist, st.title,
                                              st.source_app)
                    try:
                        lyr = fetch_lyrics(a, ti, st.album, st.duration)
                    except Exception:
                        lyr = None
                    with self._lock:
                        if self._fetching2 == key:
                            self._fetching2 = None
                            if self._key2 == key:
                                self._lyrics2 = lyr

                threading.Thread(target=work, daemon=True,
                                 name="fetch-2").start()
        lyr = self.display_lyrics(self._lyrics2)
        if not lyr:
            self.overlay2.render("", f"{st.artist} - {st.title}"
                                 if st.artist else st.title,
                                 t("searching") if self._fetching2 == key
                                 else t("no_lyrics"))
            return
        pos = st.position_now() + getattr(self, "_offset2", 0.0)
        idx = self.line_index(lyr, pos)
        current = lyr.lines[idx][1] if idx >= 0 else "…"
        nxt = ""
        for j in range(idx + 1, len(lyr.lines)):
            if lyr.lines[j][1]:
                nxt = lyr.lines[j][1]
                break
        self._render_timed_overlay(self.overlay2, lyr, idx, pos, st.duration,
                                   current or "…", nxt,
                                   "" if st.playing else t("paused"))

    # ------------------------------------- fiesta / movil / updates

    def toggle_party(self):
        from party import PartyWindow
        if self.party:
            self.party.close()
            return
        self.party = PartyWindow(self.root, self.cfg,
                                 on_close=self._party_closed)

    def _party_closed(self):
        self.party = None

    def set_phone_server(self, on):
        ok = self.phone.start() if on else (self.phone.stop() or True)
        if ok:
            self.cfg.set("phone_server", bool(on))
        return ok and (self.phone.running == bool(on))

    def phone_url(self):
        return self.phone.url if self.phone.running else ""

    def set_translate(self, on):
        self.cfg.set("translate", bool(on))

    def set_romanize(self, on):
        self.cfg.set("romanize", bool(on))
        self._display_cache.clear()

    def set_romanization_language(self, language):
        if language in ("auto", "ja", "zh", "ko"):
            self.cfg.set("romanization_language", language)
            self._display_cache.clear()

    def set_ai_model(self, name):
        self.cfg.set("ai_model", name)

    def adjust_offset2(self, delta):
        self._offset2 = 0.0 if delta is None else \
            round(getattr(self, "_offset2", 0.0) + delta, 2)

    def reload_lyrics2(self):
        st = self.watcher.get_state2()
        if st.title:
            clear_cache_entry(st.artist, st.title, st.duration)
        self._key2 = None
        self._lyrics2 = None

    def set_auto_update(self, on):
        self.cfg.set("auto_update", bool(on))
        self.updater.close()
        self.updater = AutoUpdater(APP_VERSION)
        if self._update_state != "ready":
            self._update_state = ("idle" if on else "disabled") if self.updater.enabled else "unsupported"
            self._update_detail = {}
        self.window.refresh_update_status()
        self._update_wake.set()

    def check_update_now(self):
        self._update_requested = True
        self._update_wake.set()

    def _update_status(self, updater, state, detail):
        if updater is not self.updater or self._stop_event.is_set():
            return
        was_ready = self._update_state == "ready"
        self._update_state, self._update_detail = state, detail
        self.window.refresh_update_status()
        if state == "ready" and not was_ready:
            self.tray.notify(t("update_ready", v=detail.get("version", "")))

    def _check_update(self):
        """One worker: download at launch and every six hours; UI stays free."""
        self._update_wake.wait(12)
        while not self._stop_event.is_set():
            self._update_wake.clear()
            requested = self._update_requested
            self._update_requested = False
            updater = self.updater
            if self.cfg.get("auto_update", True) or requested:
                try:
                    updater.check_and_download(on_status=lambda state, detail, u=updater:
                        self.ui_call(self._update_status, u, state, detail))
                except UpdateCancelled:
                    pass
                except Exception:
                    log.exception("No se pudo preparar la actualizacion")
                    self.ui_call(self._update_status, updater, "error", {})
            elif updater.enabled:
                pending = updater.pending_update()
                if pending:
                    self.ui_call(self._update_status, updater, "ready",
                                 {"version": pending.version})
            self._update_wake.wait(6 * 60 * 60)

    def restart_for_update(self):
        self.on_quit(restart=True)

    # ------------------------------------- identificacion por audio (Shazam)

    def identify_current(self, auto=False):
        """Identifica la cancion REAL que suena (el titulo del video puede
        ser cualquier cosa). Ajusta letra y sincronizacion a lo identificado."""
        st = self.watcher.get_state()
        if not st.connected or not st.title or self._identifying:
            return
        if not st.playing and auto:
            return
        key = st.track_key
        self._id_tried.add(key)
        self._identifying = True
        if not auto:
            self.window.flash_status(t("identifying"), ms=20000)

        def on_result(res):
            self._identifying = False
            if not res or not res.get("title"):
                if not auto:
                    self.ui_call(self.window.flash_status, t("identify_fail"))
                return
            now = self.watcher.get_state()
            if now.track_key != key:
                return
            # sincronia real: posicion dentro de la cancion vs posicion SMTC
            if res.get("song_pos") is not None:
                song_now = res["song_pos"] + (time.monotonic()
                                              - res["at_mono"])
                delta = round(song_now - now.position_now(), 2)
                if 0.7 < abs(delta) < max(now.duration, 600):
                    with self._lock:
                        self._offsets[key] = delta
                    save_offset(key[0], key[1], key[2], delta)
            lyr = fetch_lyrics(res["artist"], res["title"], "", now.duration,
                               save_key=(now.artist, now.title, now.duration))
            if lyr:
                # en modo auto solo pisar si lo de metadatos era nada/estimado
                with self._lock:
                    cur = (self._lyrics
                           if self._lyrics_key == key else None)
                if not auto or cur is None or cur.estimated or lyr.synced:
                    self.ui_call(self.apply_manual_result, key, lyr)
            self.ui_call(self.window.flash_status,
                         t("identified", a=res["artist"], b=res["title"]))

        identify_song(on_result)

    def set_hotkeys(self, enabled):
        self.cfg.set("hotkeys", bool(enabled))
        if enabled:
            self.hotkeys.start()
        else:
            self.hotkeys.stop()

    def _hotkey(self, action):
        if action == "toggle_overlay":
            self.toggle_overlay()
        elif action == "offset_plus":
            self.adjust_offset(+0.25)
        elif action == "offset_minus":
            self.adjust_offset(-0.25)
        elif action == "font_bigger":
            self.set_overlay_font(min(64, self.cfg.overlay["font_size"] + 3))
        elif action == "font_smaller":
            self.set_overlay_font(max(14, self.cfg.overlay["font_size"] - 3))

    # ------------------------------------- calibracion por audio

    def _maybe_calibrate(self, state):
        """Si la letra es estimada y la cancion va empezando, escuchar el
        audio para encontrar el arranque real de la musica."""
        key = state.track_key
        with self._lock:
            if self._offsets.get(key, 0.0):
                return
        self._cal_state = state

        def pos_getter():
            st = self.watcher.get_state()
            return st.position_now(), (st.playing and st.track_key == key)

        self.audiocal.start(key, pos_getter)

    def _maybe_ai_sync(self, state):
        """Whisper local escucha la cancion y la sincroniza el solo."""
        key = state.track_key
        if key in self._ai_tried or self.autosync_ai.busy:
            return
        st = self.watcher.get_state()
        if not st.playing or st.position_now() > 40:
            return
        with self._lock:
            lyr = self._lyrics if self._lyrics_key == key else None
            if self._offsets.get(key, 0.0):
                return  # do not apply automatic and user timing corrections twice
        if not lyr or lyr.source == "user":
            return
        self._ai_tried.add(key)

        def pos_getter():
            now = self.watcher.get_state()
            return now.position_now(), (now.playing and now.track_key == key)

        self.autosync_ai.start(key, pos_getter, lyr.lines, st.duration,
                              preserve_line_times=lyr.synced and not lyr.estimated)

    def _on_ai_sync(self, key, lines, word_times=None):
        """La IA termino de alinear (hilo propio)."""
        st = self._cal_state if (self._cal_state and
                                 self._cal_state.track_key == key) else None
        now = self.watcher.get_state()
        ref = st or (now if now.track_key == key else None)
        if ref is None:
            return
        with self._lock:
            if self._offsets.get(key, 0.0) or (
                    self._lyrics_key == key and self._lyrics and self._lyrics.source == "user"):
                return
            lyr = save_ai_sync(ref.artist, ref.title, ref.duration, lines,
                               words=word_times)
            log.info("IA sincronizo: %s (%d lineas)", ref.title, len(lines))
            if self._lyrics_key == key or self._current_key == key:
                self._lyrics = lyr
                self._lyrics_key = key
        self.ui_call(self.window.flash_status, t("ai_synced"))

    def _on_audio_anchor(self, key, anchor):
        """Llamado desde el hilo de captura al detectar el arranque."""
        st = self._cal_state
        if not st or st.track_key != key:
            return
        with self._lock:
            if self._offsets.get(key, 0.0) or (
                    self._lyrics_key == key and self._lyrics and self._lyrics.source == "user"):
                return
            lyr = apply_anchor(st.artist, st.title, st.duration, anchor)
            if not lyr:
                return
            log.info("Audio-calibracion: %s ancla=%.1fs", st.title, anchor)
            if self._lyrics_key == key:
                self._lyrics = lyr
        self.ui_call(self.window.flash_status, t("calibrated_notice"))

    # ------------------------------------- publicar a lrclib

    def publish_current(self):
        """Publica la sincronizacion 'user' actual en lrclib (en un hilo)."""
        st = self.watcher.get_state()
        with self._lock:
            lyr = self._lyrics if self._lyrics_key == st.track_key else None
        if not lyr or lyr.source != "user" or not st.title:
            return
        author = (self.cfg.get("sync_author") or "").strip()
        self.window.flash_status(t("publishing"), ms=60000)

        def work():
            ok, msg = publish_to_lrclib(st.artist, st.title, st.album,
                                        st.duration, lyr.lines, author)
            log.info("Publicacion lrclib: ok=%s msg=%s", ok, msg)
            self.ui_call(self.window.flash_status,
                         t("publish_ok") if ok else t("publish_fail", err=msg))

        threading.Thread(target=work, daemon=True, name="publish").start()

    def overlay_style_changed(self):
        """Ajustes del overlay (ancho/lineas/alineacion/fondo) cambiaron."""
        self.cfg.save()
        self.overlay.apply_style()

    def adjust_offset(self, delta):
        key = self._current_key
        if key is None:
            return
        with self._lock:
            if delta is None:
                self._offsets.pop(key, None)
                new = 0.0
            else:
                new = round(self._offsets.get(key, 0.0) + delta, 2)
                self._offsets[key] = new
        save_offset(key[0], key[1], key[2], new)

    def adjust_global_offset(self, delta):
        cur = float(self.cfg.get("offset_global", 0.0))
        new = 0.0 if delta is None else round(cur + delta, 2)
        self.cfg.set("offset_global", new)

    def seek(self, seconds):
        # el clic va a la linea: restar el offset para caer donde canta
        self.watcher.request_seek(max(0.0, seconds - self.current_offset()))

    def reload_lyrics(self):
        st = self.watcher.get_state()
        if not st.title:
            return
        self.autosync_ai.cancel()
        self.audiocal.cancel()
        self._ai_tried.discard(st.track_key)
        clear_cache_entry(st.artist, st.title, st.duration)
        with self._lock:
            self._lyrics = None
            self._lyrics_key = None
            self._fetching_key = None      # invalida cualquier fetch en vuelo
            self._failed.pop(st.track_key, None)
        self._start_fetch(st, use_cache=False)

    def fetch_manual(self, artist, title, st):
        """Busqueda con terminos corregidos. Bloqueante (el dialogo la llama en
        un hilo). Solo busca y cachea bajo la clave original; no aplica UI."""
        return fetch_lyrics(artist, title, "", st.duration, use_cache=False,
                            save_key=(st.artist, st.title, st.duration))

    def apply_manual_result(self, key, lyr):
        """Aplica el resultado de la busqueda manual (hilo UI)."""
        self.autosync_ai.cancel()
        self.audiocal.cancel()
        with self._lock:
            self._fetching_key = None      # descarta fetch automatico en vuelo
            self._failed.pop(key, None)
            if key == self._current_key:
                self._lyrics = lyr
                self._lyrics_key = key
                self._loaded_at = time.monotonic()

    def apply_user_sync(self, track, stamped_lines):
        self.autosync_ai.cancel()
        self.audiocal.cancel()
        artist, title, duration = track
        key = (artist, title, round(duration))
        with self._lock:
            lyr = save_user_sync(artist, title, duration, stamped_lines,
                                 author=(self.cfg.get("sync_author") or "").strip())
            self._fetching_key = None      # que ningun fetch viejo la pise
            self._failed.pop(key, None)
            self._offsets.pop(key, None)
            if key == self._current_key:
                self._lyrics = lyr
                self._lyrics_key = key
                self._loaded_at = time.monotonic()
        save_offset(artist, title, duration, 0.0)
        self.window.flash_status(t("sync_saved"))

    def on_quit(self, restart=False):
        if self._quitting:
            return
        try:
            installed = self.updater.install_pending_on_exit(restart=restart)
            if restart and not installed:
                self.window.flash_status(t("update_error"), 10000)
                return
        except (UpdateError, OSError):
            log.exception("No se pudo aplicar la actualizacion al salir")
            if restart:
                self.window.flash_status(t("update_error"), 10000)
                return
        self._quitting = True
        log.info("=== Lyrio cerrando ===")
        self._stop_event.set()
        self._update_wake.set()
        self.updater.close()
        self.autosync_ai.cancel()
        self.audiocal.cancel()
        try:
            self.phone.stop()
            self.translator.stop()
            if self.party:
                self.party.close()
        except Exception:
            pass
        try:
            self.watcher.stop()
            self.tray.stop()
        finally:
            try:
                self.root.destroy()
            except Exception:
                pass
            os._exit(0)   # pystray/winrt dejan hilos vivos; salida limpia y firme

    # ================================================= busqueda de letra

    def _on_track_change(self, state):
        """Llamado desde el hilo del watcher."""
        self.audiocal.cancel()
        self.autosync_ai.cancel()
        self._ai_tried.clear()
        self._radio = None
        self.translator.reset()
        if _is_ad(state) or state.duration > 1200:
            return
        self._start_fetch(state)
        # videos: identificar por audio EN PARALELO con los metadatos (el
        # titulo del video no es fiable); gana el que de mejor resultado
        if "spotify" not in (state.source_app or "").lower() and \
                state.track_key not in self._id_tried:
            self.ui_call(self.identify_current, True)

    def _start_fetch(self, state, use_cache=True):
        key = state.track_key
        with self._lock:
            if key in (self._lyrics_key, self._fetching_key):
                return
            self._fetching_key = key

        def stale():
            return self._current_key is not None and self._current_key != key

        def work():
            log.info("Buscando letra: %s - %s (%ss)",
                     state.artist, state.title, round(state.duration))
            try:
                # YouTube y navegadores traen titulos sucios: limpiarlos
                s_artist, s_title = clean_video_query(
                    state.artist, state.title, state.source_app)
                lyr = fetch_lyrics(s_artist, s_title, state.album,
                                   state.duration, use_cache=use_cache,
                                   is_stale=stale,
                                   save_key=(state.artist, state.title,
                                             state.duration))
            except Exception:
                log.exception("fallo buscando letra")
                lyr = None
            saved_offset = load_offset(state.artist, state.title,
                                       state.duration)
            with self._lock:
                if self._fetching_key == key:
                    self._fetching_key = None
                    if lyr:
                        self._lyrics = lyr
                        self._lyrics_key = key
                        self._loaded_at = time.monotonic()
                        self._failed.pop(key, None)
                        if saved_offset:
                            self._offsets[key] = saved_offset
                        log.info("Letra: fuente=%s synced=%s estimada=%s "
                                 "lineas=%d", lyr.source, lyr.synced,
                                 lyr.estimated, len(lyr.lines))
                    elif not stale():
                        self._failed[key] = time.monotonic()
                        log.info("Sin letra para %s - %s",
                                 state.artist, state.title)
            # letra estimada: calibrar rapido con el audio Y lanzar la
            # sincronizacion por IA (escucha la cancion entera y la alinea)
            if lyr is not None:
                if lyr.estimated:
                    self._maybe_calibrate(state)
                if lyr.estimated or (not lyr.words and lyr.source != "user"):
                    self._maybe_ai_sync(state)

        threading.Thread(target=work, daemon=True, name="fetch-lyrics").start()

    # ================================================= bucle de la UI

    def _render_radio(self):
        """Sin sesion multimedia: escuchar igual (modo radio). Reconoce la
        cancion por el audio del sistema y muestra su letra sincronizada
        usando el offset de Shazam como reloj."""
        if self._radio_render():
            return
        self.overlay.render("", "", t("waiting_spotify"))
        self._radio_try()

    def _radio_render(self):
        """Pinta la letra del modo radio si esta activa. True si pinto."""
        r = self._radio
        if not (r and r.get("lyr")):
            return False
        pos = r["song_pos"] + (time.monotonic() - r["at_mono"])
        lyr = self.display_lyrics(r["lyr"])
        last = lyr.lines[-1][0] if lyr.lines else 0
        if pos > last + 20:
            self._radio = None      # la cancion ya debio terminar
            return False
        idx = self.line_index(lyr, pos)
        cur = lyr.lines[idx][1] if idx >= 0 else "…"
        nxt = next((x for _t, x in lyr.lines[idx + 1:] if x), "")
        self._render_timed_overlay(self.overlay, lyr, idx, pos, 0,
                                   cur or "…", nxt,
                                   f'♪ {r["artist"]} - {r["title"]}'
                                   if pos - (lyr.lines[0][0] if lyr.lines
                                             else 0) < 6 else "")
        self._now = {"title": r["title"], "artist": r["artist"], "prev": "",
                     "current": cur or "…", "next": nxt, "extra": ""}
        if self.party:
            self.party.render(r["title"], r["artist"], "", cur or "…", nxt)
        return True

    def _radio_try(self):
        """Reconocimiento ambiental cada 35 s (modo Cualquier reproductor)."""
        if self.cfg.get("source_mode") != "any" or self._identifying:
            return
        if time.monotonic() - self._radio_last_try < 35:
            return
        self._radio_last_try = time.monotonic()
        self._identifying = True

        def on_result(res):
            self._identifying = False
            if not res or not res.get("title") or res.get("song_pos") is None:
                return
            lyr = fetch_lyrics(res["artist"], res["title"], "", 0.0)
            if lyr:
                self._radio = {"artist": res["artist"], "title": res["title"],
                               "song_pos": res["song_pos"],
                               "at_mono": res["at_mono"], "lyr": lyr}

        identify_song(on_result)

    def _extra_line(self, current):
        """Translation uses the original script, independent of display mode."""
        if not current or current == "…":
            return ""
        if self.cfg.get("translate", False):
            return self.translator.get(current,
                                       self.cfg.get("language", "en"))
        return ""

    @staticmethod
    def _line_frac(lyr, idx, pos, duration):
        """Elapsed line time for the underline; never invent word onsets."""
        t0 = lyr.lines[idx][0]
        t1 = (lyr.lines[idx + 1][0] if idx + 1 < len(lyr.lines)
              else max(duration, t0 + 5))
        return max(0.0, min(1.0, (pos - t0) / max(t1 - t0, 0.5)))

    def _render_timed_overlay(self, overlay, lyrics, idx, pos, duration,
                              current, nxt, status="", prev="", extra=""):
        progress, chars = 0.0, None
        if 0 <= idx < len(lyrics.lines):
            progress = self._line_frac(lyrics, idx, pos, duration)
            chars = word_progress(lyrics.lines[idx][1], lyrics.words.get(idx),
                                  pos, overlay.ov.get("sweep_mode", "word"))
        overlay.render(current, nxt, status, prev=prev, extra=extra,
                       timed=chars is not None)
        overlay.update_progress(progress, chars)

    def _tick(self):
        try:
            self._render()
            self._tick_secondary()
        except Exception:
            log.exception("error en tick")
        self.root.after(TICK_MS, self._tick)

    def _render(self):
        st = self.watcher.get_state()

        if not st.connected:
            self._current_key = None
            self._render_radio()
        elif _is_ad(st) or st.duration > 1200:
            # anuncios y medios largos (peliculas, podcasts): no son canciones
            self._current_key = None
            self.overlay.render("", "", "")
        else:
            key = st.track_key
            self._current_key = key
            if st.playing:
                self._last_playing_mono = time.monotonic()
            with self._lock:
                lyr = self._lyrics if self._lyrics_key == key else None
                fetching = self._fetching_key == key
                failed_at = self._failed.get(key)

            if lyr is None:
                # reintentar tras un fallo (microcortes de red) cada N segundos
                retry = (failed_at is not None and
                         time.monotonic() - failed_at > FAILED_RETRY_SECONDS)
                if not fetching and (failed_at is None or retry):
                    if retry:
                        with self._lock:
                            self._failed.pop(key, None)
                    self._start_fetch(st)
                    fetching = True
                label = f"{st.artist} - {st.title}"
                status = t("searching") if fetching else t("no_lyrics")
                self.overlay.render("", label, status)
                # video sin letra por metadatos: identificar la cancion REAL
                if not fetching and key not in self._id_tried and \
                        "spotify" not in (st.source_app or "").lower():
                    self.identify_current(auto=True)
            else:
                source_lyrics = lyr
                if lyr.estimated or (not lyr.words and lyr.source != "user"):
                    self._maybe_ai_sync(st)
                lyr = self.display_lyrics(lyr)
                pos = st.position_now() + self.current_offset()
                idx = self.line_index(lyr, pos)
                current = lyr.lines[idx][1] if idx >= 0 else ""
                if not current:
                    current = "…"
                nxt = ""
                for j in range(idx + 1, len(lyr.lines)):
                    if lyr.lines[j][1]:
                        nxt = lyr.lines[j][1]
                        break
                prev = ""
                for j in range(idx - 1, -1, -1):
                    if lyr.lines[j][1]:
                        prev = lyr.lines[j][1]
                        break
                status = ""
                if lyr.estimated and time.monotonic() - self._loaded_at < \
                        ESTIMATED_NOTICE_SECONDS:
                    status = t("estimated_notice")
                # creditos del sincronizador al final de la cancion
                if lyr.author and st.duration and \
                        pos > st.duration - 9:
                    status = t("synced_by", name=lyr.author)
                if not self.cfg.get("overlay_hint_shown") and \
                        time.monotonic() - self._loaded_at < 12:
                    status = t("overlay_first_hint")
                elif not self.cfg.get("overlay_hint_shown"):
                    self.cfg.set("overlay_hint_shown", True)
                if not st.playing:
                    status = t("paused")
                    # sesion pausada mucho rato pero suena OTRA cosa: radio
                    if time.monotonic() - self._last_playing_mono > 30 and \
                            self.cfg.get("source_mode") == "any":
                        if self._radio_render():
                            return
                        self._radio_try()
                original = source_lyrics.lines[idx][1] if idx >= 0 else ""
                extra = self._extra_line(original)
                self._render_timed_overlay(self.overlay, lyr, idx, pos,
                                           st.duration, current, nxt, status,
                                           prev=prev, extra=extra)
                self._now = {"title": st.title, "artist": st.artist,
                             "prev": prev, "current": current, "next": nxt,
                             "extra": extra}
                if self.party:
                    self.party.render(st.title, st.artist, prev, current, nxt)

        # ventana (solo si esta visible)
        try:
            if self.root.state() != "withdrawn":
                with self._lock:
                    lyr = (self._lyrics
                           if self._lyrics_key == self._current_key else None)
                    fetching = self._fetching_key == self._current_key
                    failed = self._current_key in self._failed
                self.window.update_now_playing(st, self.display_lyrics(lyr), fetching, failed,
                                               self.current_offset())
        except Exception:
            log.exception("error refrescando ventana")


def main():
    if not acquire_single_instance():
        # ya hay una instancia: le pedimos que se muestre y salimos
        return
    log.info("=== Lyrio %s iniciando ===", APP_VERSION)
    app = LyrioApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()
