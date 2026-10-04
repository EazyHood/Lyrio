# -*- coding: utf-8 -*-
"""Listen locally and align lyrics using Whisper word starts AND ends.

Existing line timing is corrected only by nearby, corroborated vocal onsets.
Unsupported lines retain their original timestamps. Estimated lyrics are
replaced only when every sung line has a reliable onset; missing timings are
never filled by interpolation and labelled as real synchronization.
"""
import importlib.util
import math
import re
import threading
import unicodedata
import wave
from collections import Counter
from difflib import SequenceMatcher

CHUNK = 0.1
MAX_SECONDS = 210          # graba hasta 3.5 min de la cancion
MIN_MATCH_RATIO = 0.7      # fraction of tokens supported in each accepted line
CLOCK_TOLERANCE = 0.75     # allow coarse media-session clock updates, not seeks
MAX_LINE_SHIFT = 4.0       # trusted LRC may lag or lead the actual phrase
MAX_NEIGHBOR_DRIFT = 2.0   # local timing corrections must corroborate each other
_TOKEN_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]|"
                       r"[^\s\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+")


def _norm_word(w):
    # NFKC preserves Japanese dakuten and all non-Latin scripts. Drop Latin
    # accents only, so "corazón" still matches "corazon" without erasing 歌.
    out = []
    for char in unicodedata.normalize("NFKC", str(w)).casefold():
        if "LATIN" in unicodedata.name(char, ""):
            char = "".join(c for c in unicodedata.normalize("NFKD", char)
                           if not unicodedata.combining(c))
        out.extend(c for c in char if unicodedata.category(c)[0] in "LNM")
    return "".join(out)


def _token_spans(text):
    for match in _TOKEN_RE.finditer(text):
        normalized = _norm_word(match.group())
        if normalized:
            yield match.start(), match.end(), normalized


def _clock_continuous(start, current, captured_seconds):
    """A recording offset is valid only while audio and song clocks agree."""
    return (all(math.isfinite(v) for v in (start, current, captured_seconds))
            and abs((current - start) - captured_seconds) <= CLOCK_TOLERANCE)


def _match_tokens(tokens, transcript):
    matcher = SequenceMatcher(None, [v[2] for v in tokens],
                              [v[0] for v in transcript], autojunk=False)
    return {block.a + k: transcript[block.b + k]
            for block in matcher.get_matching_blocks()
            for k in range(block.size)}


def _line_intervals(text, tokens, matched):
    """Accept supported endpoints and coverage, retaining original word spans."""
    from timing import normalize_word_timings

    if not tokens or not (0 in matched and len(tokens) - 1 in matched
                          and len(matched) / len(tokens) >= MIN_MATCH_RATIO):
        return None
    intervals = []
    previous_word = None
    for ti, (cs, ce, _token) in enumerate(tokens):
        if ti not in matched:
            previous_word = None
            continue
        _token, wi, start, end = matched[ti]
        if wi == previous_word:
            intervals[-1] = (*intervals[-1][:3], ce)
        else:
            intervals.append((start, end, cs, ce))
        previous_word = wi
    clean = normalize_word_timings(text, intervals)
    return clean if len(clean) == len(intervals) else None


def _nearby_phrase(lines, li, tokens, transcript):
    """Find one unambiguous complete phrase within the correction neighborhood."""
    original = float(lines[li][0])
    end_limit = (float(lines[li + 1][0]) + MAX_LINE_SHIFT
                 if li + 1 < len(lines) else original + 45.0)
    candidates = {}
    for index, seed in enumerate(transcript):
        if seed[0] != tokens[0][2] or abs(seed[2] - original) > MAX_LINE_SHIFT:
            continue
        window = [word for word in transcript[index:] if word[3] <= end_limit + 0.05]
        matched = _match_tokens(tokens, window)
        if not matched or matched.get(0) != seed:
            continue
        intervals = _line_intervals(lines[li][1], tokens, matched)
        if intervals:
            signature = tuple((ti, word[1]) for ti, word in sorted(matched.items()))
            candidates[signature] = (seed[2], intervals)
    return next(iter(candidates.values())) if len(candidates) == 1 else None


def _align_trusted(lines, by_line, transcript, global_matches):
    """Correct late/early LRC using full-song evidence or corroborated neighbors.

    No offset is guessed for an unsupported line. A single common/repeated
    phrase can add word timing inside its existing window, but cannot move it.
    """
    sung = [li for li, tokens in enumerate(by_line) if tokens]
    global_candidates = {}
    for li in sung:
        intervals = _line_intervals(lines[li][1], by_line[li],
                                    global_matches.get(li, {}))
        if intervals:
            global_candidates[li] = (intervals[0][0], intervals)

    signatures = Counter(tuple(token[2] for token in by_line[li]) for li in sung)
    distinctive = any(len(signature) >= 3 and count == 1
                      and len(set(signature)) >= 3
                      for signature, count in signatures.items())
    full_match = len(global_candidates) == len(sung) and distinctive
    if full_match and any(abs(start - float(lines[li][0])) > MAX_LINE_SHIFT
                          for li, (start, _words) in global_candidates.items()):
        # A complete match far from the expected recording is not evidence
        # for small lyric latency. Do not borrow a distant chorus or version.
        return None

    if full_match:
        corrections = dict(global_candidates)
    else:
        nearby = {li: candidate for li in sung
                  if (candidate := _nearby_phrase(lines, li, by_line[li], transcript))
                  and len(by_line[li]) >= 3 and len(candidate[1]) >= 2}
        corrections = {}
        for index, li in enumerate(sung):
            if li not in nearby:
                continue
            start, intervals = nearby[li]
            drift = start - float(lines[li][0])
            for neighbor in sung[max(0, index - 1):index] + sung[index + 1:index + 2]:
                if neighbor not in nearby:
                    continue
                other_start = nearby[neighbor][0]
                other_drift = other_start - float(lines[neighbor][0])
                if (abs(other_start - start) <= 45.0
                        and abs(other_drift - drift) <= MAX_NEIGHBOR_DRIFT):
                    corrections[li] = (start, intervals)
                    break

    # Preserve untouched silence markers and unsupported lines. Revert any
    # correction that would reorder them or swallow an adjacent phrase.
    while corrections:
        times = [corrections[i][0] if i in corrections else float(time)
                 for i, (time, _text) in enumerate(lines)]
        unsafe = set()
        for li, (start, intervals) in corrections.items():
            if li and start <= times[li - 1]:
                unsafe.add(li)
            if li + 1 < len(times) and (start >= times[li + 1]
                                       or intervals[-1][1] > times[li + 1] + 0.05):
                unsafe.add(li)
                if li + 1 in corrections:
                    unsafe.add(li + 1)
        if not unsafe:
            break
        for li in unsafe:
            corrections.pop(li, None)

    synced = [(corrections[i][0] if i in corrections else time, text)
              for i, (time, text) in enumerate(lines)]
    word_times = {}
    for li in sung:
        start = float(synced[li][0])
        end = float(synced[li + 1][0]) if li + 1 < len(synced) else math.inf
        if li in corrections:
            intervals = corrections[li][1]
        else:
            candidates = [word for word in transcript if start <= word[2] < end
                          and word[3] <= end + 0.05]
            intervals = _line_intervals(lines[li][1], by_line[li],
                                        _match_tokens(by_line[li], candidates))
        if intervals and intervals[0][0] >= start and intervals[-1][1] <= end + 0.05:
            word_times[li] = intervals
    return (synced, word_times) if word_times else None


class AutoSyncAI:
    """Background capture/alignment; on_result(track_key, lines, word_times)."""

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

    def start(self, track_key, pos_getter, plain_lines, duration,
              preserve_line_times=False):
        """pos_getter() -> (song_pos, sigue_siendo_esta_cancion_y_sonando)."""
        if self._busy or importlib.util.find_spec("faster_whisper") is None:
            return
        self._cancel = threading.Event()
        self._busy = True
        threading.Thread(
            target=self._run,
            args=(track_key, pos_getter, list(plain_lines), duration,
                  self._cancel, preserve_line_times),
            daemon=True, name="autosync-ai").start()

    # ------------------------------------------------------------------

    def _run(self, key, pos_getter, lines, duration, cancel,
             preserve_line_times=False):
        import os
        import tempfile
        wav = None
        try:
            handle, wav = tempfile.mkstemp(prefix="lyrio_ai_", suffix=".wav")
            os.close(handle)
            pos0 = self._capture(wav, pos_getter, cancel, duration)
            if pos0 is None or cancel.is_set():
                return
            words = self._transcribe(wav, self._model_getter())
            if not words or cancel.is_set():
                return
            result = self._align(lines, words, pos0, preserve_line_times)
            if result:
                synced, word_times = result
                if not cancel.is_set():
                    self._on_result(key, synced, word_times)
        except Exception:
            pass
        finally:
            self._busy = False
            try:
                if wav:
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
            if not ok or not math.isfinite(pos0):
                return None
            chunks = []
            limit = min(MAX_SECONDS,
                        max(0.0, duration - pos0 - CHUNK) if duration else MAX_SECONDS)
            captured_frames = 0
            while captured_frames / rate < limit and not cancel.is_set():
                # An overflow means samples were lost: retaining that recording
                # would silently apply the wrong offset to everything after it.
                chunk = stream.read(frames, exception_on_overflow=True)
                if len(chunk) != frames * ch * 2:
                    return None
                chunks.append(chunk)
                captured_frames += len(chunk) // (ch * 2)
                current, ok = pos_getter()
                if not ok or not _clock_continuous(
                        pos0, current, captured_frames / rate):
                    return None     # pause, seek or track change: discard
            if cancel.is_set() or not chunks:
                return None
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
        """[(relative_start, relative_end, word)] with local Whisper."""
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
                if _norm_word(w.word):
                    out.append((float(w.start), float(w.end), w.word))
        return out

    @staticmethod
    def _align(lines, words, pos0, preserve_line_times=False):
        """Align vocal evidence; preserve_line_times keeps unsupported LRC onsets."""
        if not lines or not words or not math.isfinite(pos0):
            return None
        by_line = [list(_token_spans(text)) for _time, text in lines]
        transcript = []       # (token, source word id, start, end)
        last_end = -1.0
        for wi, row in enumerate(words):
            try:
                start, end, word = row
                start, end = float(start) + pos0, float(end) + pos0
                if not (math.isfinite(start) and math.isfinite(end)):
                    continue
                if start < max(0.0, last_end) or end <= start:
                    continue
            except (TypeError, ValueError):
                continue
            last_end = end
            for _cs, _ce, token in _token_spans(word):
                transcript.append((token, wi, start, end))
        if not transcript:
            return None

        matches = {}          # line -> {token index: transcript tuple}
        flat = [(li, ti, token) for li, tokens in enumerate(by_line)
                for ti, token in enumerate(tokens)]
        mapped = _match_tokens([token for _li, _ti, token in flat], transcript)
        for index, word in mapped.items():
            li, ti, _token = flat[index]
            matches.setdefault(li, {})[ti] = word
        if preserve_line_times:
            return _align_trusted(lines, by_line, transcript, matches)

        anchors, word_times = {}, {}
        for li, tokens in enumerate(by_line):
            if not tokens:
                continue
            matched = matches.get(li, {})
            clean = _line_intervals(lines[li][1], tokens, matched)
            if not clean:
                return None
            anchors[li] = matched[0][2]
            word_times[li] = clean
        if not word_times:
            return None
        # Empty LRC rows are silence markers. There is no vocal evidence for
        # moving them when the input has only estimated timing.
        sung = [li for li, tokens in enumerate(by_line) if tokens]
        if len(sung) != len(lines):
            return None
        if any(anchors[b] < word_times[a][-1][1]
               for a, b in zip(sung, sung[1:])):
            return None
        return [(anchors[i], text) for i, (_time, text) in enumerate(lines)], word_times
