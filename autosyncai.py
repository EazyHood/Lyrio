# -*- coding: utf-8 -*-
"""Sincronizacion por IA: el programa ESCUCHA al cantante y alinea la letra.

Cuando una cancion solo tiene letra estimada (sin tiempos reales en ninguna
fuente), este modulo graba la salida del sistema mientras suena, transcribe
con Whisper local (faster-whisper, CPU int8, con timestamps por palabra) y
alinea la transcripcion con la letra conocida (SequenceMatcher sobre palabras
normalizadas). Resultado: tiempos reales por linea sin ayuda humana, guardados
en cache como fuente "ai". Todo en segundo plano, con puerta de calidad
(minimo 40 % de lineas ancladas y tiempos monotonos) para no empeorar nada.
"""
import re
import threading
import time
import unicodedata
import wave
from difflib import SequenceMatcher

CHUNK = 0.1
MAX_SECONDS = 210          # graba hasta 3.5 min de la cancion
MIN_MATCH_RATIO = 0.4      # % minimo de lineas ancladas para aceptar


def _norm_word(w):
    w = unicodedata.normalize("NFKD", w.lower())
    w = "".join(c for c in w if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9ñ]+", "", w)


class AutoSyncAI:
    """start() graba+transcribe+alinea en un hilo; on_result(lines|None)."""

    def __init__(self, on_result, model_getter=None):
        self._on_result = on_result
        self._model_getter = model_getter or (lambda: "base")
        self._cancel = threading.Event()
        self._busy = False

    @property
    def busy(self):
        return self._busy

    def cancel(self):
        self._cancel.set()

    def start(self, track_key, pos_getter, plain_lines, duration):
        """pos_getter() -> (song_pos, sigue_siendo_esta_cancion_y_sonando)."""
        if self._busy:
            return
        self._cancel = threading.Event()
        self._busy = True
        threading.Thread(
            target=self._run,
            args=(track_key, pos_getter, list(plain_lines), duration,
                  self._cancel),
            daemon=True, name="autosync-ai").start()

    # ------------------------------------------------------------------

    def _run(self, key, pos_getter, lines, duration, cancel):
        import os
        import tempfile
        wav = os.path.join(tempfile.gettempdir(), "lyrio_ai.wav")
        try:
            pos0 = self._capture(wav, pos_getter, cancel, duration)
            if pos0 is None or cancel.is_set():
                return
            words = self._transcribe(wav, self._model_getter())
            if not words or cancel.is_set():
                return
            result = self._align(lines, words, pos0)
            if result:
                synced, word_times = result
                self._on_result(key, synced, word_times)
        except Exception:
            pass
        finally:
            self._busy = False
            try:
                os.remove(wav)
            except OSError:
                pass

    @staticmethod
    def _capture(wav_path, pos_getter, cancel, duration):
        """Graba loopback hasta el final de la cancion (o 3.5 min).
        Devuelve la posicion de la cancion al iniciar la grabacion."""
        try:
            import pyaudiowpatch as pa
        except ImportError:
            return None
        p = pa.PyAudio()
        stream = None
        try:
            wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
            out = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
            loop = None
            for d in p.get_loopback_device_info_generator():
                if out["name"] in d["name"]:
                    loop = d
                    break
                loop = loop or d
            if not loop:
                return None
            rate = int(loop["defaultSampleRate"])
            ch = max(1, int(loop["maxInputChannels"]))
            frames = int(rate * CHUNK)
            stream = p.open(format=pa.paInt16, channels=ch, rate=rate,
                            input=True, input_device_index=loop["index"],
                            frames_per_buffer=frames)
            pos0, ok = pos_getter()
            if not ok:
                return None
            chunks = []
            limit = min(MAX_SECONDS,
                        (duration - pos0 + 2) if duration else MAX_SECONDS)
            t0 = time.monotonic()
            while time.monotonic() - t0 < limit and not cancel.is_set():
                chunks.append(stream.read(frames,
                                          exception_on_overflow=False))
                if len(chunks) % 50 == 0:
                    _p, ok = pos_getter()
                    if not ok:
                        break       # cambio de cancion/pausa: alinear lo que hay
            with wave.open(wav_path, "wb") as w:
                w.setnchannels(ch)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(b"".join(chunks))
            return pos0
        except Exception:
            return None
        finally:
            try:
                if stream:
                    stream.stop_stream()
                    stream.close()
                p.terminate()
            except Exception:
                pass

    @staticmethod
    def _transcribe(wav_path, model_name="base"):
        """[(t_rel, palabra)] con Whisper local (CPU int8, portable)."""
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            return None
        model = WhisperModel(model_name if model_name in ("base", "small")
                             else "base", device="cpu", compute_type="int8")
        segs, _info = model.transcribe(wav_path, word_timestamps=True,
                                       vad_filter=True, beam_size=1)
        out = []
        for seg in segs:
            for w in (seg.words or []):
                nw = _norm_word(w.word)
                if nw:
                    out.append((float(w.start), nw))
        return out

    @staticmethod
    def _align(lines, words, pos0):
        """Alinea la letra conocida con la transcripcion (palabra a palabra)."""
        line_tokens = []      # (indice_linea, char_fin_en_linea, token)
        for i, (_t, text) in enumerate(lines):
            for m in re.finditer(r"\S+", text):
                nt = _norm_word(m.group())
                if nt:
                    line_tokens.append((i, m.end(), nt))
        if not line_tokens or not words:
            return None
        a = [tok for _i, _e, tok in line_tokens]
        b = [tok for _t, tok in words]
        sm = SequenceMatcher(None, a, b, autojunk=False)
        anchors = {}          # linea -> tiempo de su primera palabra anclada
        word_times = {}       # linea -> [(t_cancion, char_fin)] palabra a palabra
        for blk in sm.get_matching_blocks():
            for k in range(blk.size):
                li, cend, _tok = line_tokens[blk.a + k]
                t_song = pos0 + words[blk.b + k][0]
                if li not in anchors or t_song < anchors[li]:
                    anchors[li] = t_song
                word_times.setdefault(li, []).append((round(t_song, 2), cend))
        if len(anchors) < max(3, MIN_MATCH_RATIO * len(lines)):
            return None
        # limpiar tiempos de palabra: orden y monotonia dentro de cada linea
        for li, wl in word_times.items():
            wl.sort(key=lambda p: p[1])
            clean = []
            for t, ce in wl:
                if not clean or (t > clean[-1][0] and ce > clean[-1][1]):
                    clean.append((t, ce))
            word_times[li] = clean
        # construir tiempos: anclas + interpolacion en huecos, monotonia forzada
        n = len(lines)
        times = [None] * n
        for i, t in anchors.items():
            times[i] = t
        known = [i for i in range(n) if times[i] is not None]
        for i in range(n):
            if times[i] is None:
                prev = max((k for k in known if k < i), default=None)
                nxt = min((k for k in known if k > i), default=None)
                if prev is None:
                    times[i] = max(0.0, times[nxt] - (nxt - i) * 3.0)
                elif nxt is None:
                    times[i] = times[prev] + (i - prev) * 3.0
                else:
                    f = (i - prev) / (nxt - prev)
                    times[i] = times[prev] + f * (times[nxt] - times[prev])
        for i in range(1, n):
            if times[i] <= times[i - 1]:
                times[i] = times[i - 1] + 0.3
        synced = [(round(times[i], 2), lines[i][1]) for i in range(n)]
        return synced, word_times
