# -*- coding: utf-8 -*-
"""Busqueda de letras en internet + parseo LRC + sincronizacion automatica.

Cadena de fuentes (se detiene en la primera letra sincronizada valida):
  1. Cache local (%LOCALAPPDATA%/Lyrio/cache)
  2. lrclib.net /api/get  (exacta, con y sin album)
  3. lrclib.net /api/search (coincidencia estricta de artista + titulo/duracion)
  4. NetEase Cloud Music (solo con artista y duracion compatibles)
  5. lyrics.ovh (solo letra plana)
Si solo se consigue letra plana, se sincroniza automaticamente distribuyendo
las lineas a lo largo de la duracion, ponderadas por su longitud.

Los ajustes manuales de tiempo por cancion viven en offsets.json (aparte de la
cache, para que 'Buscar de nuevo' no los borre).
"""
import hashlib
import json
import math
import os
import re
import unicodedata
from itertools import chain

import requests
from timing import normalize_word_timings

UA = {"User-Agent": "Lyrio/2.0"}
BASE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "."), "Lyrio")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
OFFSETS_PATH = os.path.join(BASE_DIR, "offsets.json")

DURATION_TOLERANCE = 7          # segundos para aceptar una letra sincronizada

TS_ANY = re.compile(r"\[\d+:\d{1,2}(?::\d{1,2})?(?:\.\d{1,3})?\]")
OFFSET_TAG = re.compile(r"\[offset:\s*([+-]?\d+)\s*\]", re.IGNORECASE)
BY_TAG = re.compile(r"\[by:\s*([^\]]+)\]", re.IGNORECASE)
WORD_TS_RE = re.compile(r"<(\d+):(\d{1,2})(?:[.:](\d{1,3}))?>")
PAREN_NOISE_RE = re.compile(
    r"\s*[\(\[\-–—]\s*(feat\.?|ft\.?|with|con)\s+[^)\]]*[\)\]]?\s*$", re.IGNORECASE)
SUFFIX_NOISE_RE = re.compile(
    r"\s*-\s*(remaster(ed)?|remix|live|acustic[oa]?|acoustic|version|edit|"
    r"deluxe|bonus|radio)[^-]*$", re.IGNORECASE)


class Lyrics:
    """Resultado: lineas [(segundos, texto)], fuente y si es sync real o estimada."""

    def __init__(self, lines, source, synced, estimated=False, author="",
                 words=None):
        self.lines = lines            # list[tuple[float, str]] ordenada por tiempo
        self.source = source          # "lrclib", "musixmatch", "netease", "user"...
        self.synced = synced          # True si trae tiempos reales
        self.estimated = estimated    # True si los tiempos son estimados
        self.author = author          # firma [by:] de quien la sincronizo
        # Canonical timing: {line: [(start, end, char_start, char_end), ...]}.
        # Old cache pairs remain readable as onset-only events.
        self.words = {}
        if isinstance(words, dict):
            for key, entries in words.items():
                try:
                    index = int(key)
                except (TypeError, ValueError, OverflowError):
                    continue
                if not 0 <= index < len(self.lines):
                    continue
                clean = normalize_word_timings(self.lines[index][1], entries)
                if clean:
                    self.words[index] = clean

    def to_dict(self):
        d = {"lines": self.lines, "source": self.source,
             "synced": self.synced, "estimated": self.estimated,
             "author": self.author, "timing_version": 2}
        if self.words:
            d["words"] = {str(k): v for k, v in self.words.items()}
        return d

    @staticmethod
    def from_dict(d):
        lines = [(float(t), str(x)) for t, x in d.get("lines", [])]
        if any(not math.isfinite(t) or t < 0 for t, _ in lines):
            raise ValueError("Invalid lyric timestamp")
        if any(a[0] > b[0] for a, b in zip(lines, lines[1:])):
            raise ValueError("Unordered lyric timestamps")
        words = d.get("words") or {}
        # Legacy AI caches may contain interpolated lines with no vocal
        # anchors. Keep them readable but do not claim they are real sync.
        try:
            timing_version = int(d.get("timing_version", 0))
        except (TypeError, ValueError, OverflowError):
            timing_version = 0
        legacy_ai = d.get("source") == "ai" and timing_version < 2
        return Lyrics(lines, str(d.get("source", "cache")),
                      bool(d.get("synced")) and not legacy_ai,
                      bool(d.get("estimated")) or legacy_ai,
                      str(d.get("author", "")), words)


# ---------------------------------------------------------------- utilidades

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


_VIDEO_NOISE = re.compile(
    r"[\(\[（【][^)\]）】]*(official|oficial|video|lyric|letra|audio|visuali|"
    r"live|en vivo|hd|4k|mv|m/v|sub\b|subtitul|remaster|prod\.?|explicit)"
    r"[^)\]）】]*[\)\]）】]", re.IGNORECASE)


def clean_video_query(artist, title, source_app=""):
    """Limpia titulos de YouTube/navegadores para poder buscar la letra.
    'BadBunnyVEVO' + 'Bad Bunny - Monaco (Official Video) [4K]' ->
    ('Bad Bunny', 'Monaco')."""
    if "spotify" in (source_app or "").lower():
        return artist, title
    t = _VIDEO_NOISE.sub(" ", title or "")
    t = re.sub(r"\s*\|[^|]*$", " ", t)             # '| Movie Soundtrack' etc
    t = re.sub(r"[\"“”«»]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–—·")
    a = (artist or "").strip()
    if a.endswith(" - Topic"):
        a = a[:-8].strip()                          # canal auto de YouTube
    elif re.search(r"vevo$", a, re.IGNORECASE):
        a = re.sub(r"vevo$", "", a, flags=re.IGNORECASE)
        a = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", a).strip()
    # 'Artista - Cancion' dentro del titulo del video
    if " - " in t:
        left, right = t.split(" - ", 1)
        if not a or _norm(a) in _norm(left) or _norm(left) in _norm(a) or \
                len(a) < 2:
            a, t = left.strip(), right.strip()
    return a or artist, t or title


def _clean_title(title: str) -> str:
    t = PAREN_NOISE_RE.sub("", title or "")
    t = SUFFIX_NOISE_RE.sub("", t)
    return t.strip() or (title or "")


def _artists_match(a: str, b: str) -> bool:
    """Igualdad normalizada o contencion razonable (evita 'Ana' ~ 'Santana')."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    return shorter in longer and len(shorter) >= max(3, 0.5 * len(longer))


def _duration_ok(candidate, duration, tol=DURATION_TOLERANCE):
    if not duration:
        return True         # sin referencia no podemos filtrar
    try:
        return abs(float(candidate) - float(duration)) <= tol
    except (TypeError, ValueError):
        return False


def _key_hash(artist, title, duration):
    key = f"{_norm(artist)}|{_norm(title)}|{round(duration or 0)}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _cache_path(artist, title, duration):
    return os.path.join(CACHE_DIR, f"{_key_hash(artist, title, duration)}.json")


def _atomic_write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------- parseo LRC

def lrc_author(text: str) -> str:
    """Extrae la firma [by:...] de un LRC (creditos del sincronizador)."""
    m = BY_TAG.search(text or "")
    return m.group(1).strip() if m else ""


def parse_lrc(text: str):
    """Return sorted line timestamps; use parse_lrc_timing for word data."""
    return parse_lrc_timing(text)[0]


def parse_lrc_timing(text: str):
    """Return (lines, word intervals), preserving Enhanced LRC timestamps.

    Soporta [mm:ss], [mm:ss.frac], [h:mm:ss(.frac)], varias marcas por linea,
    y [offset:±ms]. Inline times are absolute; repeated line tags shift their
    word times by the same delta. A missing last word end stays onset-only.
    """
    text = text or ""
    offset = 0.0
    m = OFFSET_TAG.search(text)
    if m:
        try:
            offset = int(m.group(1)) / 1000.0   # positivo = adelantar (estandar)
        except ValueError:
            pass

    out = []

    def word_stamp(match):
        mm, ss, frac = match.groups()
        return (int(mm) * 60 + int(ss)
                + int((frac or "0").ljust(3, "0")) / 1000.0)

    for raw in text.splitlines():
        times = []
        for tag in TS_ANY.finditer(raw):
            parts = tag.group()[1:-1].split(":")
            seconds = float(parts[-1])
            minutes = int(parts[-2])
            hours = int(parts[0]) if len(parts) == 3 else 0
            times.append(hours * 3600 + minutes * 60 + seconds)
        if not times:
            continue
        body = TS_ANY.sub("", raw)
        tags = list(WORD_TS_RE.finditer(body))
        untrimmed = WORD_TS_RE.sub("", body)
        trim_left = len(untrimmed) - len(untrimmed.lstrip())
        line = untrimmed.strip()
        inline = []
        cursor = len(body[:tags[0].start()]) if tags else 0
        for index, tag in enumerate(tags):
            next_tag = tags[index + 1] if index + 1 < len(tags) else None
            segment = body[tag.end():next_tag.start() if next_tag else len(body)]

            start = word_stamp(tag)
            end = word_stamp(next_tag) if next_tag else start
            cs = cursor + len(segment) - len(segment.lstrip()) - trim_left
            ce = cursor + len(segment.rstrip()) - trim_left
            if cs < ce:
                inline.append((start, end, cs, ce))
            cursor += len(segment)
        for t in times:
            delta = t - times[0] - offset
            words = [(max(0.0, start + delta), max(0.0, end + delta), cs, ce)
                     for start, end, cs, ce in inline]
            out.append((max(0.0, t - offset), line,
                        normalize_word_timings(line, words)))
    out.sort(key=lambda p: p[0])
    return ([(t, line) for t, line, _words in out],
            {index: words for index, (_t, _line, words) in enumerate(out) if words})


def _lrc_result(text, source):
    lines, words = parse_lrc_timing(text)
    return Lyrics(lines, source, True, author=lrc_author(text), words=words)


def autosync(plain: str, duration: float):
    """Sincronizacion estimada: reparte las lineas por la duracion segun longitud."""
    lines = [ln.strip() for ln in (plain or "").splitlines()]
    compact = []
    for ln in lines:
        if ln == "" and (not compact or compact[-1] == ""):
            continue
        compact.append(ln)
    while compact and compact[-1] == "":
        compact.pop()
    if not compact or duration <= 0:
        return []

    start = min(10.0, duration * 0.06)
    end = duration * 0.97
    span = max(end - start, 1.0)

    weights = [(40.0 if ln == "" else 14.0 + len(ln)) for ln in compact]
    total = sum(weights)

    out = []
    t = start
    for ln, w in zip(compact, weights):
        if ln != "":
            out.append((round(t, 2), ln))
        t += span * (w / total)
    return out


# ---------------------------------------------------------------- fuentes

def _lrclib_get(artist, title, album, duration):
    for params in (
        {"artist_name": artist, "track_name": title,
         "album_name": album, "duration": round(duration)},
        {"artist_name": artist, "track_name": title},
        {"artist_name": artist, "track_name": _clean_title(title)},
    ):
        if not params.get("album_name"):
            params.pop("album_name", None)
        try:
            r = requests.get("https://lrclib.net/api/get", params=params,
                             headers=UA, timeout=10)
            if r.status_code == 200:
                yield r.json()
        except (requests.RequestException, ValueError):
            continue


def _lrclib_search(artist, title, duration):
    q = f"{artist} {_clean_title(title)}"
    try:
        r = requests.get("https://lrclib.net/api/search", params={"q": q},
                         headers=UA, timeout=10)
        if not r.ok:
            return
        results = r.json()
    except (requests.RequestException, ValueError):
        return
    nt = _norm(_clean_title(title))

    def acceptable(rec):
        amatch = _artists_match(artist, rec.get("artistName", ""))
        tmatch = nt and nt in _norm(rec.get("trackName", ""))
        dmatch = duration and _duration_ok(rec.get("duration"), duration, 6)
        if not artist:
            # video sin artista fiable: exigir titulo Y duracion compatibles
            return tmatch and dmatch
        return amatch and (tmatch or dmatch)

    good = [rec for rec in (results or []) if acceptable(rec)]
    good.sort(key=lambda rec: (not rec.get("syncedLyrics"),
                               abs((rec.get("duration") or 0) - (duration or 0))))
    for rec in good[:2]:
        yield rec


def _musixmatch(artist, title, duration):
    """Musixmatch (catalogo mas grande del mundo) via syncedlyrics.
    Validado contra la duracion para no aceptar otra version."""
    try:
        import syncedlyrics
    except ImportError:
        return None
    try:
        lrc = syncedlyrics.search(f"{artist} {_clean_title(title)}",
                                  providers=["Musixmatch"], enhanced=True)
    except Exception:
        return None
    if not lrc:
        return None
    result = _lrc_result(lrc, "musixmatch")
    parsed = result.lines
    if sum(1 for _, ln in parsed if ln) < 6:
        return None
    if duration:
        last = parsed[-1][0]
        if last > duration + 15 or last < duration * 0.3:
            return None       # tiempos de otra grabacion
    return result


def _netease(artist, title, duration):
    hdr = {**UA, "Referer": "https://music.163.com"}
    try:
        r = requests.get("https://music.163.com/api/search/get",
                         params={"s": f"{artist} {_clean_title(title)}",
                                 "type": 1, "limit": 8},
                         headers=hdr, timeout=10)
        songs = (r.json().get("result") or {}).get("songs") or []
    except (requests.RequestException, ValueError):
        return None

    def ok(song):
        artists = [a.get("name", "") for a in song.get("artists", [])]
        if not any(_artists_match(artist, name) for name in artists if name):
            return False
        dur_ms = song.get("duration")
        return _duration_ok((dur_ms or 0) / 1000.0, duration)

    for song in [s for s in songs if ok(s)][:3]:
        try:
            r2 = requests.get("https://music.163.com/api/song/lyric",
                              params={"id": song["id"], "lv": 1, "kv": 1,
                                      "tv": -1},
                              headers=hdr, timeout=10)
            lrc = ((r2.json().get("lrc") or {}).get("lyric")) or ""
        except (requests.RequestException, ValueError, KeyError):
            continue
        result = _lrc_result(lrc, "netease")
        if sum(1 for _, ln in result.lines if ln) >= 8:
            return result
    return None


def _lyrics_ovh(artist, title):
    try:
        r = requests.get(
            "https://api.lyrics.ovh/v1/"
            f"{requests.utils.quote(artist, safe='')}/"
            f"{requests.utils.quote(_clean_title(title), safe='')}",
            headers=UA, timeout=8)
        if r.ok:
            return (r.json().get("lyrics") or "").strip() or None
    except (requests.RequestException, ValueError):
        pass
    return None


# ---------------------------------------------------------------- API publica

def fetch_lyrics(artist, title, album="", duration=0.0, use_cache=True,
                 save_key=None, is_stale=None):
    """Busca la letra. Devuelve Lyrics o None. Bloqueante (llamar en hilo).

    save_key: (artist, title, duration) bajo el que guardar en cache (permite
    buscar con terminos corregidos y recordarlo para la cancion original).
    is_stale: callable() -> bool; si devuelve True entre fuentes, se aborta
    (la cancion ya cambio y el resultado se descartaria).
    """
    if not artist and not title:
        return None

    def stale():
        return bool(is_stale and is_stale())

    sk = save_key or (artist, title, duration)
    cpath = _cache_path(*sk)
    if use_cache and os.path.exists(cpath):
        try:
            with open(cpath, "r", encoding="utf-8") as f:
                return Lyrics.from_dict(json.load(f))
        except Exception:
            # cache corrupta o de esquema viejo: eliminar y buscar de nuevo
            try:
                os.remove(cpath)
            except OSError:
                pass

    plain_fallback = None   # (texto_plano, fuente)

    # lrclib get + search, perezoso: corta en el primer resultado valido
    for rec in chain(_lrclib_get(artist, title, album, duration),
                     _lrclib_search(artist, title, duration)):
        if stale():
            return None
        synced = rec.get("syncedLyrics") or ""
        plain = rec.get("plainLyrics") or ""
        rec_dur = rec.get("duration")
        if synced and (rec_dur is None or _duration_ok(rec_dur, duration)):
            result = _lrc_result(synced, "lrclib")
            if result.lines:
                return _save(cpath, result)
        if plain and not plain_fallback:
            plain_fallback = (plain, "lrclib")

    if stale():
        return None

    # Musixmatch + QQ + Kugou EN PARALELO (antes eran secuenciales: ~3x mas
    # rapido). Se respeta la prioridad: musixmatch > qq > kugou.
    q = f"{artist} {_clean_title(title)}"

    def _try_qq_kugou(fn, name):
        try:
            import sources_extra
            res = getattr(sources_extra, fn)(q)
        except Exception:
            return None
        if not res or not res.get("synced"):
            return None
        if res.get("artist") and not _artists_match(artist, res["artist"]):
            return None
        result = _lrc_result(res["lyrics"], name)
        parsed = result.lines
        good = sum(1 for _, ln in parsed if ln) >= 6
        if good and duration and parsed:
            last = parsed[-1][0]
            good = last <= duration + 15 and last >= duration * 0.3
        if good:
            return result
        return None

    def _try_mxm():
        return _musixmatch(artist, title, duration)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    jobs = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        jobs[ex.submit(_try_mxm)] = 0
        jobs[ex.submit(_try_qq_kugou, "qq_music", "qq")] = 1
        jobs[ex.submit(_try_qq_kugou, "kugou", "kugou")] = 2
        best = None
        best_prio = 99
        try:
            for fut in as_completed(jobs, timeout=18):
                if stale():
                    break
                try:
                    lyr = fut.result()
                except Exception:
                    lyr = None
                if lyr and jobs[fut] < best_prio:
                    best, best_prio = lyr, jobs[fut]
                if best_prio == 0:
                    break       # llego la de maxima prioridad: no esperar mas
        except Exception:
            pass
    if best:
        return _save(cpath, best)

    if stale():
        return None

    # NetEase (sincronizada, con artista y duracion compatibles)
    result = _netease(artist, title, duration)
    if result:
        return _save(cpath, result)

    if stale():
        return None

    # planas: letras.com (la mejor en espanol) -> Genius (proxy) -> lyrics.ovh
    if not plain_fallback:
        try:
            import sources_extra
            for fn, name in ((sources_extra.letras_com, "letras.com"),
                             (sources_extra.genius_dumb, "genius")):
                if stale():
                    return None
                try:
                    res = fn(q)
                except Exception:
                    res = None
                if res and res.get("lyrics") and (
                        not res.get("artist") or
                        _artists_match(artist, res["artist"])):
                    plain_fallback = (res["lyrics"], name)
                    break
        except ImportError:
            pass
    if not plain_fallback:
        plain = _lyrics_ovh(artist, title)
        if plain:
            plain_fallback = (plain, "lyrics.ovh")

    if plain_fallback and not stale():
        text, source = plain_fallback
        lines = autosync(text, duration)
        if lines:
            return _save(cpath, Lyrics(lines, source, False, estimated=True))

    return None


CACHE_MAX_FILES = 400     # poda LRU: la cache nunca engorda sin limite


def _prune_cache():
    try:
        files = [os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR)
                 if f.endswith(".json")]
        if len(files) <= CACHE_MAX_FILES:
            return
        files.sort(key=lambda p: os.path.getmtime(p))
        for p in files[:len(files) - CACHE_MAX_FILES]:
            try:
                os.remove(p)
            except OSError:
                pass
    except OSError:
        pass


def cache_size():
    """(n_archivos, bytes) de la cache local."""
    n = total = 0
    try:
        for f in os.listdir(CACHE_DIR):
            p = os.path.join(CACHE_DIR, f)
            try:
                total += os.path.getsize(p)
                n += 1
            except OSError:
                pass
    except OSError:
        pass
    return n, total


def clear_cache():
    try:
        for f in os.listdir(CACHE_DIR):
            try:
                os.remove(os.path.join(CACHE_DIR, f))
            except OSError:
                pass
    except OSError:
        pass


def _save(path, lyr: Lyrics):
    try:
        _atomic_write(path, lyr.to_dict())
        _prune_cache()
    except OSError:
        pass
    return lyr


def clear_cache_entry(artist, title, duration):
    try:
        os.remove(_cache_path(artist, title, duration))
    except OSError:
        pass


def save_ai_sync(artist, title, duration, lines, words=None):
    """Guarda la sincronizacion hecha por la IA escuchando la cancion."""
    ordered = sorted(enumerate(lines), key=lambda item: float(item[1][0]))
    word_map = {new: (words or {}).get(old, [])
                for new, (old, _line) in enumerate(ordered)}
    lines = [(float(t), str(text)) for _old, (t, text) in ordered]
    lyr = Lyrics(lines, "ai", True, estimated=False,
                 author="Lyrio AI", words=word_map)
    return _save(_cache_path(artist, title, duration), lyr)


def save_user_sync(artist, title, duration, lines, author=""):
    """Guarda la sincronizacion hecha a mano por el usuario (maxima prioridad)."""
    lines = sorted(((round(float(t), 2), str(x)) for t, x in lines),
                   key=lambda p: p[0])
    lyr = Lyrics([list(p) for p in lines], "user", True, estimated=False,
                 author=author)
    return _save(_cache_path(artist, title, duration), lyr)


def apply_anchor(artist, title, duration, anchor):
    """Re-ancla una letra ESTIMADA para que la primera linea caiga en `anchor`
    (arranque real de la musica detectado por audio). Devuelve Lyrics o None."""
    path = _cache_path(artist, title, duration)
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    if not d.get("estimated"):
        return None
    lines = sorted(((float(t), str(x)) for t, x in d.get("lines", [])),
                   key=lambda p: p[0])
    if not lines:
        return None
    first, last = lines[0][0], lines[-1][0]
    end = max(anchor + 5.0, duration * 0.97) if duration else last
    scale = 1.0 if last <= first else max(0.4, min(1.25,
                                                   (end - anchor) / (last - first)))
    d["lines"] = [[round(anchor + (t - first) * scale, 2), x]
                  for t, x in lines]
    d["calibrated"] = True
    try:
        _atomic_write(path, d)
    except OSError:
        pass
    return Lyrics.from_dict(d)


# ------------------------------------------------- publicar a lrclib

def _fmt_lrc(lines, author=""):
    out = []
    if author:
        out.append(f"[by:{author}]")
    for tsec, text in lines:
        mm = int(tsec) // 60
        ss = tsec - mm * 60
        out.append(f"[{mm:02d}:{ss:05.2f}] {text}")
    return "\n".join(out)


def publish_to_lrclib(artist, title, album, duration, lines, author="",
                      max_seconds=120):
    """Publica una sincronizacion en lrclib.net (anonimo, con firma [by:]).

    Resuelve el reto proof-of-work de lrclib. Devuelve (ok, mensaje).
    Bloqueante: llamar en un hilo.
    """
    import time as _time
    try:
        r = requests.post("https://lrclib.net/api/request-challenge",
                          headers=UA, timeout=10)
        r.raise_for_status()
        ch = r.json()
        prefix, target_hex = ch["prefix"], ch["target"]
    except Exception as e:
        return False, f"challenge: {type(e).__name__}"

    target = bytes.fromhex(target_hex)
    nonce = 0
    t0 = _time.monotonic()
    while True:
        h = hashlib.sha256(f"{prefix}{nonce}".encode()).digest()
        if h <= target:
            break
        nonce += 1
        if nonce % 200000 == 0 and _time.monotonic() - t0 > max_seconds:
            return False, "timeout resolviendo el reto"

    synced = _fmt_lrc(lines, author)
    plain = "\n".join(text for _, text in lines)
    try:
        r = requests.post(
            "https://lrclib.net/api/publish",
            json={"trackName": title, "artistName": artist,
                  "albumName": album or title, "duration": round(duration),
                  "plainLyrics": plain, "syncedLyrics": synced},
            headers={**UA, "X-Publish-Token": f"{prefix}:{nonce}"},
            timeout=15)
        if r.status_code in (200, 201):
            return True, "ok"
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, type(e).__name__


# ------------------------------------------------- offsets por cancion

def _load_offsets():
    try:
        with open(OFFSETS_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def load_offset(artist, title, duration):
    try:
        return float(_load_offsets().get(_key_hash(artist, title, duration), 0.0))
    except (TypeError, ValueError):
        return 0.0


def save_offset(artist, title, duration, offset):
    """Persiste el ajuste manual (sobrevive a 'Buscar de nuevo' y reinicios)."""
    d = _load_offsets()
    h = _key_hash(artist, title, duration)
    if abs(float(offset)) < 0.005:
        d.pop(h, None)
    else:
        d[h] = round(float(offset), 2)
    try:
        _atomic_write(OFFSETS_PATH, d)
    except OSError:
        pass
