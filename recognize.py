# -*- coding: utf-8 -*-
"""Identificacion de la cancion REAL por audio (Shazam, sin ffmpeg).

Para videos de YouTube y similares el titulo casi nunca es el nombre real de
la cancion. Este modulo captura ~7 s de la salida del sistema (loopback
WASAPI), genera la firma de Shazam en Python puro (shazamio 0.4) y devuelve
titulo, artista y el OFFSET exacto dentro de la cancion (lo que ademas permite
sincronizar la letra aunque el video tenga intro).
"""
import array
import audioop
import os
import tempfile
import threading
import time
import wave

CAPTURE_SECONDS = 4.5      # intento rapido; si falla se reintenta con 7 s


def _capture_wav(path, seconds=CAPTURE_SECONDS):
    import pyaudiowpatch as pa
    p = pa.PyAudio()
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
            return False
        rate = int(loop["defaultSampleRate"])
        ch = max(1, int(loop["maxInputChannels"]))
        frames = int(rate * 0.1)
        stream = p.open(format=pa.paInt16, channels=ch, rate=rate, input=True,
                        input_device_index=loop["index"],
                        frames_per_buffer=frames)
        chunks = []
        try:
            for _ in range(int(seconds / 0.1)):
                chunks.append(stream.read(frames,
                                          exception_on_overflow=False))
        finally:
            stream.stop_stream()
            stream.close()
        with wave.open(path, "wb") as w:
            w.setnchannels(ch)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"".join(chunks))
        return True
    finally:
        p.terminate()


def _wav_to_16k_mono(path):
    with wave.open(path, "rb") as w:
        ch, sw, fr = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raw = audioop.lin2lin(raw, sw, 2)
    if ch == 2:
        raw = audioop.tomono(raw, 2, 0.5, 0.5)
    if fr != 16000:
        raw, _ = audioop.ratecv(raw, 2, 1, fr, 16000, None)
    return array.array("h", raw)


async def _recognize_wav(path):
    from shazamio import Shazam
    from shazamio.algorithm import SignatureGenerator
    sh = Shazam()
    samples = _wav_to_16k_mono(path)
    gen = SignatureGenerator()
    gen.feed_input(samples)
    gen.MAX_TIME_SECONDS = 12
    sig = gen.get_next_signature()
    if len(gen.input_pending_processing) < 128 and not sig:
        return {"matches": []}
    while not sig:
        sig = gen.get_next_signature()
    return await sh.send_recognize_request(sig)


def identify_song(on_result):
    """Captura + reconoce en un hilo. on_result(dict|None) desde ese hilo.

    dict: {title, artist, song_pos: posicion REAL dentro de la cancion en el
    instante `at_mono` (time.monotonic)}.
    """

    def work():
        import asyncio
        res = None
        wav = os.path.join(tempfile.gettempdir(), "lyrio_id.wav")
        try:
            # intento rapido (4.5 s) y reintento largo (7 s) si no hubo match
            for seconds in (CAPTURE_SECONDS, 7.0):
                t0 = time.monotonic()
                if not _capture_wav(wav, seconds):
                    break
                out = asyncio.run(_recognize_wav(wav))
                track = out.get("track") or {}
                matches = out.get("matches") or []
                if track.get("title"):
                    song_pos = None
                    if matches and matches[0].get("offset") is not None:
                        # offset = posicion al INICIO de la captura
                        song_pos = float(matches[0]["offset"]) + \
                            (time.monotonic() - t0)
                    res = {"title": track.get("title", ""),
                           "artist": track.get("subtitle", ""),
                           "song_pos": song_pos,
                           "at_mono": time.monotonic()}
                    break
        except Exception:
            res = None
        finally:
            try:
                os.remove(wav)
            except OSError:
                pass
        on_result(res)

    threading.Thread(target=work, daemon=True, name="shazam-id").start()
