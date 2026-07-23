# -*- coding: utf-8 -*-
"""Calibracion por audio: detecta donde ARRANCA la musica de verdad.

Muchas canciones traen una conversacion/skit/intro antes de la letra; las
sincronizaciones estimadas fallan ahi. Este modulo escucha la salida del
sistema (loopback WASAPI, funciona con Spotify, YouTube o cualquier app),
detecta el arranque sostenido de energia musical y ancla ahi la letra
estimada. El resultado se guarda por cancion: la primera escucha calibra,
las siguientes salen bien desde el segundo cero.

Solo se activa con letras estimadas y si la cancion va empezando. Consumo
minimo (RMS por tramos de 100 ms, maximo ~75 s).
"""
import threading
import time

FRAME_SECONDS = 0.1
MAX_CAPTURE = 75.0        # seg de escucha maxima
MAX_SONG_POS = 8.0        # solo calibrar si la cancion va empezando
SUSTAIN = 2.5             # seg que debe sostenerse la energia para ser "musica"
RATIO = 3.5               # cuantas veces sobre el ruido base
PREROLL = 0.25            # margen antes del arranque detectado


class AudioCalibrator:
    """on_result(track_key, anchor_seconds) se llama en el hilo de captura."""

    def __init__(self, on_result):
        self._on_result = on_result
        self._thread = None
        self._cancel = threading.Event()
        self._key = None

    def start(self, track_key, song_pos_getter):
        """Empieza a escuchar para track_key. song_pos_getter() -> (pos, playing)."""
        self.cancel()
        self._cancel = threading.Event()
        self._key = track_key
        self._thread = threading.Thread(
            target=self._run, args=(track_key, song_pos_getter, self._cancel),
            daemon=True, name="audio-calibrate")
        self._thread.start()

    def cancel(self):
        self._cancel.set()

    # ------------------------------------------------------------------

    def _run(self, key, pos_getter, cancel):
        try:
            import numpy as np
            import pyaudiowpatch as pa
        except ImportError:
            return

        pos0, playing = pos_getter()
        if not playing or pos0 > MAX_SONG_POS:
            return

        try:
            p = pa.PyAudio()
        except Exception:
            return
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
                return
            rate = int(loop["defaultSampleRate"])
            ch = max(1, int(loop["maxInputChannels"]))
            frames = int(rate * FRAME_SECONDS)
            stream = p.open(format=pa.paInt16, channels=ch, rate=rate,
                            input=True, input_device_index=loop["index"],
                            frames_per_buffer=frames)

            t_start = time.monotonic()
            rms = []            # (tiempo_de_cancion, rms)
            while not cancel.is_set():
                elapsed = time.monotonic() - t_start
                if elapsed > MAX_CAPTURE:
                    break
                data = stream.read(frames, exception_on_overflow=False)
                buf = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                if ch > 1:
                    buf = buf.reshape(-1, ch).mean(axis=1)
                val = float(np.sqrt(np.mean(buf * buf))) if len(buf) else 0.0
                rms.append((pos0 + elapsed, val))

                anchor = self._detect(rms)
                if anchor is not None:
                    if not cancel.is_set():
                        self._on_result(key, max(0.0, anchor - PREROLL))
                    return
                # si pausan la cancion, abortar (el silencio confunde)
                if len(rms) % 20 == 0:
                    _, still = pos_getter()
                    if not still:
                        return
        except Exception:
            pass
        finally:
            try:
                if stream:
                    stream.stop_stream()
                    stream.close()
                p.terminate()
            except Exception:
                pass

    @staticmethod
    def _detect(rms):
        """Primer instante donde la energia salta sobre el ruido base y se
        sostiene SUSTAIN segundos. Devuelve tiempo de cancion o None."""
        if len(rms) < int((3.0 + SUSTAIN) / FRAME_SECONDS):
            return None
        vals = [v for _, v in rms]
        n_base = int(3.0 / FRAME_SECONDS)
        base = sorted(vals[:n_base])[n_base // 5]      # percentil ~20 inicial
        floor = max(base * RATIO, 120.0)               # umbral absoluto minimo
        need = int(SUSTAIN / FRAME_SECONDS)
        run = 0
        for i, v in enumerate(vals):
            if v >= floor:
                run += 1
                if run >= need:
                    return rms[i - need + 1][0]
            else:
                run = 0
        return None
