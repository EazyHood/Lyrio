# -*- coding: utf-8 -*-
"""Lector/controlador de la sesion multimedia de Spotify via Windows SMTC.

Corre en su propio hilo con un event loop asyncio. Publica el estado en
self._state (protegido por lock). Ademas:
- baja la caratula del album (bytes) con reintentos
- acepta ordenes de seek con proteccion anti-rebote (SMTC tarda en reflejarlo)
- ancla la posicion a time.monotonic (inmune a cambios del reloj del sistema)
- espera a que el cambio de pista se estabilice antes de anunciarlo
"""
import asyncio
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
)
from winrt.windows.storage.streams import Buffer, DataReader, InputStreamOptions

PLAYING = 4

POLL_SECONDS = 0.6
TICKS_PER_SECOND = 10_000_000   # TimeSpan de WinRT: 100 ns por tick
WINRT_TIMEOUT = 4.0             # segundos por await WinRT
SEEK_HOLD_SECONDS = 2.5         # ignorar lecturas SMTC rancias tras un seek
THUMB_RETRIES = 6               # reintentos de caratula (1 por poll)


_APP_NAMES = (
    ("spotify", "Spotify"), ("ytmusic", "YouTube Music"),
    ("youtube", "YouTube"), ("applemusic", "Apple Music"),
    ("apple", "Apple Music"), ("amazon", "Amazon Music"),
    ("deezer", "Deezer"), ("tidal", "TIDAL"), ("vlc", "VLC"),
    ("chrome", "Chrome"), ("msedge", "Edge"), ("edge", "Edge"),
    ("firefox", "Firefox"), ("opera", "Opera"), ("brave", "Brave"),
    ("zune", "Media Player"), ("wmplayer", "Media Player"),
    ("foobar", "foobar2000"), ("musicbee", "MusicBee"), ("aimp", "AIMP"),
)


def friendly_app_name(aumid):
    low = (aumid or "").lower()
    for token, name in _APP_NAMES:
        if token in low:
            return name
    return (aumid or "").split("!")[0].split(".")[-1] or "Player"


@dataclass
class PlayerState:
    connected: bool = False          # hay sesion multimedia
    source_app: str = ""             # AUMID de la app (spotify, chrome, ...)
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: float = 0.0            # segundos
    playing: bool = False
    # snapshot de posicion: pos_base segundos validos en el instante mono_base
    pos_base: float = 0.0
    mono_base: float = field(default_factory=time.monotonic)

    def position_now(self) -> float:
        pos = self.pos_base
        if self.playing:
            pos += time.monotonic() - self.mono_base
        if self.duration > 0:
            pos = min(pos, self.duration)
        return max(0.0, pos)

    @property
    def track_key(self):
        return (self.artist, self.title, round(self.duration))


async def _wr(awaitable, timeout=WINRT_TIMEOUT):
    """Await WinRT con timeout para que un SMTC colgado no congele el loop."""
    return await asyncio.wait_for(awaitable, timeout)


class SpotifyWatcher:
    """Hilo que sondea SMTC, baja caratulas y ejecuta seeks."""

    def __init__(self, on_track_change=None, source_mode="spotify"):
        self._lock = threading.Lock()
        self._source_mode = source_mode      # "spotify" | "any"
        self._pinned = None                  # AUMID elegido por el usuario
        self._secondary = None               # AUMID del overlay secundario
        self._state2 = PlayerState()         # estado de la sesion secundaria
        self._sessions_info = []             # [(aumid, title, artist, playing)]
        self._state = PlayerState()
        self._thumb = (None, None)       # (track_key, bytes)
        self._on_track_change = on_track_change
        self._cmds = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="SpotifyWatcher")
        # ancla de posicion (solo la toca el hilo del watcher)
        self._tl_sig = None              # (tl_pos, tl_last_updated) del ultimo poll
        self._anchor_pos = 0.0
        self._anchor_mono = 0.0
        # anti-rebote de seek
        self._seek_target = None
        self._seek_mono = 0.0
        # debounce de cambio de pista
        self._candidate_key = None
        self._announced_key = None
        # caratula
        self._thumb_tries = 0
        self._thumb_key = None

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def get_state(self) -> PlayerState:
        with self._lock:
            return PlayerState(**vars(self._state))

    def get_thumbnail(self):
        """(track_key, bytes|None) de la caratula mas reciente."""
        with self._lock:
            return self._thumb

    def request_seek(self, seconds):
        """Salta a ese segundo en el reproductor. Optimista para la UI."""
        seconds = max(0.0, float(seconds))
        with self._lock:
            if self._state.connected:
                self._state.pos_base = seconds
                self._state.mono_base = time.monotonic()
            self._cmds.put(("seek", seconds))

    def set_source_mode(self, mode):
        """'spotify' (solo Spotify) o 'any' (cualquier reproductor SMTC)."""
        with self._lock:
            self._source_mode = mode

    def get_sessions_info(self):
        """[(aumid, title, artist, playing)] de TODAS las sesiones con musica."""
        with self._lock:
            return list(self._sessions_info)

    def set_pinned(self, aumid):
        """Fija la sesion principal a esa app (None = automatico)."""
        with self._lock:
            self._pinned = aumid

    def set_secondary(self, aumid):
        """Activa/desactiva el seguimiento de una segunda sesion."""
        with self._lock:
            self._secondary = aumid
            if not aumid:
                self._state2 = PlayerState()

    def get_state2(self) -> PlayerState:
        with self._lock:
            return PlayerState(**vars(self._state2))

    # ---- hilo interno ----

    def _run(self):
        asyncio.run(self._loop())

    async def _loop(self):
        mgr = None
        while not self._stop.is_set():
            try:
                if mgr is None:
                    mgr = await _wr(SessionManager.request_async())
                session, sess2 = await self._scan_sessions(mgr)
                if session is None:
                    self._reset_disconnected()
                else:
                    await self._drain_cmds(session)
                    st, thumb_ref = await self._read_session(session)
                    self._publish(st)
                    await self._track_change_and_thumb(st, thumb_ref)
                if sess2 is not None:
                    try:
                        st2 = await self._read_session_simple(sess2)
                    except Exception:
                        st2 = PlayerState()
                    with self._lock:
                        self._state2 = st2
                elif self._secondary:
                    with self._lock:
                        self._state2 = PlayerState()
            except Exception:
                # manager invalido (servicio reiniciado, Spotify cerrado): rehacer
                mgr = None
                self._reset_disconnected()
            await asyncio.sleep(POLL_SECONDS)

    def _reset_disconnected(self):
        self._publish(PlayerState())
        self._tl_sig = None
        self._candidate_key = None
        self._announced_key = None
        self._seek_target = None
        with self._lock:
            self._thumb = (None, None)

    async def _drain_cmds(self, session):
        while True:
            try:
                cmd, arg = self._cmds.get_nowait()
            except queue.Empty:
                return
            try:
                if cmd == "seek":
                    ticks = int(arg * TICKS_PER_SECOND)
                    await _wr(session.try_change_playback_position_async(ticks))
                    # proteger la posicion optimista de lecturas SMTC rancias
                    self._seek_target = arg
                    self._seek_mono = time.monotonic()
                    self._tl_sig = None
            except Exception:
                pass

    async def _scan_sessions(self, mgr):
        """Enumera TODAS las sesiones con musica, publica la lista para la UI
        y elige (principal, secundaria). Prioridad principal: fijada por el
        usuario > Spotify sonando > otra sonando (modo any) > Spotify pausado
        > otra (modo any)."""
        with self._lock:
            any_mode = self._source_mode == "any"
            pinned = self._pinned
            second_id = self._secondary
        infos, entries = [], []
        try:
            sessions = list(mgr.get_sessions())
        except Exception:
            sessions = []
        for s in sessions[:6]:
            aumid = s.source_app_user_model_id or ""
            try:
                info = await _wr(s.try_get_media_properties_async())
            except Exception:
                continue
            title = info.title or ""
            if not title:
                continue
            try:
                playing = int(s.get_playback_info().playback_status) == PLAYING
            except Exception:
                playing = False
            infos.append((aumid, title, info.artist or "", playing))
            entries.append((s, aumid, playing))
        with self._lock:
            self._sessions_info = infos

        def pick_auto():
            spotify_p = other_p = spotify_x = other_x = None
            for s, aumid, playing in entries:
                is_sp = "spotify" in aumid.lower()
                if is_sp and playing and not spotify_p:
                    spotify_p = s
                elif is_sp and not spotify_x:
                    spotify_x = s
                elif playing and not other_p:
                    other_p = s
                elif not other_x:
                    other_x = s
            if any_mode:
                return spotify_p or other_p or spotify_x or other_x
            return spotify_p or spotify_x

        primary = None
        if pinned:
            for s, aumid, _p in entries:
                if aumid == pinned:
                    primary = s
                    break
        primary = primary or pick_auto()
        secondary = None
        if second_id and primary is not None:
            prim_id = primary.source_app_user_model_id or ""
            for s, aumid, _p in entries:
                if aumid == second_id and aumid != prim_id:
                    secondary = s
                    break
        return primary, secondary

    async def _read_session_simple(self, session):
        """Lectura ligera para la sesion secundaria (sin ancla compartida)."""
        info = await _wr(session.try_get_media_properties_async())
        tl = session.get_timeline_properties()
        pb = session.get_playback_info()
        playing = int(pb.playback_status) == PLAYING
        pos = tl.position.total_seconds() if tl.position else 0.0
        dur = tl.end_time.total_seconds() if tl.end_time else 0.0
        if playing and tl.last_updated_time is not None:
            try:
                lag = (datetime.now(timezone.utc)
                       - tl.last_updated_time).total_seconds()
                if 0 <= lag < 3600:
                    pos += lag
            except Exception:
                pass
        return PlayerState(
            connected=True,
            source_app=session.source_app_user_model_id or "",
            title=info.title or "", artist=info.artist or "",
            album=info.album_title or "", duration=dur, playing=playing,
            pos_base=max(0.0, min(pos, dur) if dur else pos),
            mono_base=time.monotonic())

    async def _read_session(self, session):
        info = await _wr(session.try_get_media_properties_async())
        tl = session.get_timeline_properties()
        pb = session.get_playback_info()

        playing = int(pb.playback_status) == PLAYING
        mono_now = time.monotonic()

        tl_pos = tl.position.total_seconds() if tl.position else 0.0
        dur = tl.end_time.total_seconds() if tl.end_time else 0.0
        tl_upd = tl.last_updated_time

        # Ancla monotonica: cuando el timeline cambia (play/pausa/seek/cambio de
        # pista) fijamos pos+monotonic de ese instante; entre eventos extrapolamos
        # SOLO con monotonic (inmune a NTP/suspension y sin tope de 1 hora).
        sig = (round(tl_pos, 3), str(tl_upd))
        if sig != self._tl_sig:
            self._tl_sig = sig
            base = tl_pos
            if playing and tl_upd is not None:
                try:
                    lag = (datetime.now(timezone.utc) - tl_upd).total_seconds()
                    if 0 <= lag < 30:      # solo el desfase corto de propagacion
                        base += lag
                except Exception:
                    pass
            self._anchor_pos = base
            self._anchor_mono = mono_now

        pos = self._anchor_pos
        if playing:
            pos += mono_now - self._anchor_mono

        # anti-rebote: tras un seek, SMTC tarda en reflejar la nueva posicion;
        # mientras la lectura siga cerca de la posicion vieja, mantener el destino
        if self._seek_target is not None:
            since = mono_now - self._seek_mono
            if since > SEEK_HOLD_SECONDS or abs(pos - self._seek_target) < 3.0:
                self._seek_target = None
            else:
                pos = self._seek_target + (since if playing else 0.0)
                self._anchor_pos = pos
                self._anchor_mono = mono_now

        if dur > 0:
            pos = min(pos, dur)

        st = PlayerState(
            connected=True,
            source_app=session.source_app_user_model_id or "",
            title=info.title or "",
            artist=info.artist or "",
            album=info.album_title or "",
            duration=dur,
            playing=playing,
            pos_base=max(0.0, pos),
            mono_base=mono_now,
        )
        return st, info.thumbnail

    async def _track_change_and_thumb(self, st, thumb_ref):
        key = st.track_key
        if not st.title:
            return

        # debounce: anunciar el cambio solo cuando el key se repite en dos
        # sondeos seguidos con duracion valida (SMTC actualiza por partes)
        if key != self._announced_key:
            if key == self._candidate_key and st.duration > 0:
                self._announced_key = key
                self._thumb_key = key
                self._thumb_tries = 0
                with self._lock:
                    self._thumb = (key, None)
                if self._on_track_change:
                    try:
                        self._on_track_change(st)
                    except Exception:
                        pass
            else:
                self._candidate_key = key
                return

        # caratula: reintentar mientras no haya datos (el arte tarda en llegar
        # al cambiar de pista y el primer stream puede ser el del tema anterior)
        if key == self._thumb_key and self._thumb_tries < THUMB_RETRIES:
            with self._lock:
                have = self._thumb[0] == key and self._thumb[1]
            if not have:
                self._thumb_tries += 1
                data = await self._read_thumbnail(thumb_ref)
                if data:
                    with self._lock:
                        self._thumb = (key, data)

    @staticmethod
    async def _read_thumbnail(thumb_ref):
        if thumb_ref is None:
            return None
        try:
            stream = await _wr(thumb_ref.open_read_async(), 5.0)
            try:
                size = int(stream.size)
                if not (0 < size < 5_000_000):
                    return None
                buf = Buffer(size)
                await _wr(stream.read_async(buf, size,
                                            InputStreamOptions.READ_AHEAD), 5.0)
                try:
                    return bytes(buf)      # IBuffer soporta protocolo buffer
                except TypeError:
                    reader = DataReader.from_buffer(buf)
                    return bytes(bytearray(
                        reader.read_byte() for _ in range(buf.length)))
            finally:
                stream.close()
        except Exception:
            return None

    def _publish(self, st: PlayerState):
        with self._lock:
            self._state = st
