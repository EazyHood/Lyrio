# -*- coding: utf-8 -*-
"""Traduccion en vivo (Ollama local) y romanizacion de letras.

- Traduccion: linea a linea contra el Ollama local (qwen2.5:3b u otro),
  con cache en memoria por cancion y peticiones en un hilo unico. Si Ollama
  no responde, la funcion queda muda (sin errores para el usuario).
- Romanizacion: japones (pykakasi), chino (pypinyin), coreano
  (korean_romanizer), 100% offline. Se activa sola si la linea es CJK.
"""
import json
import re
import threading
import unicodedata

import requests

OLLAMA = "http://localhost:11434"
MODEL = "qwen2.5:3b"

_JA = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f]")  # kana
_ZH = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")  # han
_KO = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]")  # hangul
_CJK_RUN = re.compile(
    r"[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f"
    r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3005\u3006]+"
    r"|[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]+")
_LATIN = re.compile(r"[a-zA-ZÀ-ɏ]")

_kakasi = None
_kakasi_lock = threading.Lock()
_LANG_NAMES = {"es": "Spanish", "en": "English"}


def needs_romanization(text):
    return bool(_JA.search(text) or _ZH.search(text) or _KO.search(text))


def infer_romanization_language(texts, language=None):
    """Infer the song's script context, not the UI/translation language.

    Han characters alone cannot distinguish Chinese from Japanese. Kana on
    other lines establishes Japanese context for kanji-only lines. An explicit
    override handles songs written entirely in kanji; without that evidence,
    Han-only songs retain the existing Chinese behavior.
    """
    if language in ("ja", "zh", "ko"):
        return language
    if isinstance(texts, str):
        texts = [texts]
    kana = hangul = han = 0
    for text in texts:
        kana += len(_JA.findall(text))
        hangul += len(_KO.findall(text))
        han += len(_ZH.findall(text))
    if kana and kana >= hangul:
        return "ja"
    if hangul:
        return "ko"
    return "zh" if han else None


def _romanize_run(text, language):
    """Convert only CJK runs so Latin words and punctuation survive verbatim."""
    global _kakasi
    try:
        if _KO.search(text):
            from korean_romanizer.romanizer import Romanizer
            return Romanizer(text).romanize()
        if _JA.search(text) or language == "ja":
            import pykakasi
            # Its dictionaries are lazily initialized; workers and UI may both
            # request a conversion while a song is being loaded.
            with _kakasi_lock:
                if _kakasi is None:
                    _kakasi = pykakasi.kakasi()
                items = _kakasi.convert(unicodedata.normalize("NFKC", text))
            return " ".join(item["hepburn"] or item["orig"]
                            for item in items).strip()
        if _ZH.search(text) and language != "ko":
            from pypinyin import lazy_pinyin
            return " ".join(lazy_pinyin(text))
    except Exception:
        # A missing dictionary must never blank a lyric line. Keep the source
        # run; other supported scripts in the same line can still be converted.
        pass
    return text


def romanize(text, language=None):
    """Romanize with a stable song context; return "" if nothing changed.

    ``language`` is auto/ja/zh/ko and is independent of the translation target.
    Latin portions retain their casing, spelling, punctuation, and spacing.
    """
    if not text or not needs_romanization(text):
        return ""
    language = infer_romanization_language(text, language)
    parts = []
    end = 0
    changed = False
    for match in _CJK_RUN.finditer(text):
        prefix = text[end:match.start()]
        original = match.group()
        converted = _romanize_run(original, language)
        is_changed = converted != original
        # Insert separators only where a converted run touches a Latin word.
        if prefix:
            parts.append(prefix)
        if is_changed and parts and parts[-1][-1:].isalnum():
            parts.append(" ")
        parts.append(converted)
        if (is_changed and match.end() < len(text)
                and text[match.end()].isalnum()):
            parts.append(" ")
        changed = changed or is_changed
        end = match.end()
    parts.append(text[end:])
    return "".join(parts) if changed else ""


class Translator:
    """Traduce lineas en segundo plano con cache. on_update() avisa a la UI."""

    def __init__(self, on_update=None):
        self._cache = {}          # texto -> traduccion
        self._pending = []
        self._lock = threading.Lock()
        self._on_update = on_update
        self._alive = None        # None = sin probar, True/False tras probar
        self._wake = threading.Event()
        self._stop = threading.Event()
        threading.Thread(target=self._worker, daemon=True,
                         name="translator").start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def reset(self):
        with self._lock:
            self._cache.clear()
            self._pending.clear()

    def get(self, text, target_lang):
        """Traduccion cacheada de la linea, o "" si aun no esta (la encola)."""
        if not text or len(text) < 2:
            return ""
        if not _LATIN.search(text) and not needs_romanization(text):
            return ""
        key = (text, target_lang)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            if key not in self._pending:
                self._pending.append(key)
                if len(self._pending) > 30:
                    self._pending.pop(0)
        self._wake.set()
        return ""

    # ------------------------------------------------------------------

    def _check_alive(self):
        if self._alive is not None:
            return self._alive
        try:
            requests.get(OLLAMA + "/api/tags", timeout=2)
            self._alive = True
        except requests.RequestException:
            self._alive = False
        return self._alive

    def _worker(self):
        while not self._stop.is_set():
            self._wake.wait(timeout=2.0)
            self._wake.clear()
            while True:
                with self._lock:
                    if not self._pending:
                        break
                    key = self._pending.pop(0)
                if key in self._cache:
                    continue
                if not self._check_alive():
                    with self._lock:
                        self._pending.clear()
                    break
                text, lang = key
                out = self._translate(text, lang)
                with self._lock:
                    self._cache[key] = out
                if out and self._on_update:
                    try:
                        self._on_update()
                    except Exception:
                        pass

    def _translate(self, text, target_lang):
        name = _LANG_NAMES.get(target_lang, "Spanish")
        try:
            r = requests.post(
                OLLAMA + "/api/generate",
                json={"model": MODEL, "stream": False,
                      "options": {"temperature": 0.1, "num_predict": 60},
                      "prompt": (f"Translate this song lyric line to {name}. "
                                 "Reply ONLY with the translation, nothing "
                                 f"else.\nLine: {text}\nTranslation:")},
                timeout=20)
            out = (r.json().get("response") or "").strip()
            out = out.strip('"').splitlines()[0].strip() if out else ""
            # si el modelo devolvio lo mismo o basura larga, descartar
            if out.lower() == text.lower() or len(out) > 3 * len(text) + 20:
                return ""
            return out
        except Exception:
            self._alive = None      # reprobar mas tarde
            return ""
