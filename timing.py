"""Validated word intervals and display progress, independent of the UI.

Intervals are (song_start, song_end, character_start, character_end). Character
indices refer to the original lyric, not a translated or romanized version.
Legacy (song_start, character_end) records are onset events: without an end
timestamp there is no evidence for inventing a duration.
"""
import math


def normalize_word_timings(text, entries):
    """Keep valid monotonic intervals; corrupt cache rows cannot break playback."""
    if not isinstance(entries, (list, tuple)):
        return []
    clean = []
    for row in entries:
        try:
            if not isinstance(row, (list, tuple)):
                continue
            if len(row) == 4:
                start, end, cstart, cend = row
            elif len(row) == 2:
                start, cend = row
                end = start
                cstart = clean[-1][3] if clean else 0
                while cstart < len(text) and text[cstart].isspace():
                    cstart += 1
            else:
                continue
            start, end = float(start), float(end)
            if isinstance(cstart, bool) or isinstance(cend, bool):
                continue
            if int(cstart) != float(cstart) or int(cend) != float(cend):
                continue
            cstart, cend = int(cstart), int(cend)
            if not (math.isfinite(start) and math.isfinite(end)):
                continue
            if start < 0 or end < start or not 0 <= cstart < cend <= len(text):
                continue
            if clean and (start < clean[-1][1] or cstart < clean[-1][3]):
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        clean.append((start, end, cstart, cend))
    return clean


def word_progress(text, words, pos, mode="word"):
    """Return highlighted character count, or None when no word timing exists.

    Word mode reveals the active word at its real onset. Character mode sweeps
    only that word during its recorded duration. Both hold during gaps and
    never consume any of the next word before its own timestamp. A word's
    duration is not phoneme timing; a sustained vowel is kept inside that word.
    """
    intervals = normalize_word_timings(text, words)
    if not intervals:
        return None
    try:
        pos = float(pos)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(pos):
        return 0.0
    done = 0.0
    for start, end, cstart, cend in intervals:
        if pos < start:
            break
        if mode != "char" or end <= start or pos >= end:
            done = float(cend)
        else:
            return cstart + (cend - cstart) * (pos - start) / (end - start)
    return done
