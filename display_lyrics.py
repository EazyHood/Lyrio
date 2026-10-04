# -*- coding: utf-8 -*-
"""Prepare readable lyrics without changing the source used for alignment."""

from lyrics import Lyrics
from translate import infer_romanization_language, romanize


def prepare_display_lyrics(lyrics, enabled=True, language=None):
    """Return a romanized display copy, or the source object if unchanged.

    Use once per source song / preference change and retain the source for
    translation, editing, export, and audio alignment. All line timestamps are
    preserved. Character offsets cannot be reused after transliteration, so
    word highlighting is removed only from altered rows. ``romanized_lines``
    identifies those rows for renderers that otherwise estimate highlighting.
    """
    if lyrics is None or not enabled:
        return lyrics
    context = infer_romanization_language(
        (text for _, text in lyrics.lines), language)
    changed = set()
    lines = []
    for index, (timestamp, text) in enumerate(lyrics.lines):
        display = romanize(text, context) or text
        lines.append((timestamp, display))
        if display != text:
            changed.add(index)
    if not changed:
        return lyrics
    result = Lyrics(
        lines, lyrics.source, lyrics.synced, lyrics.estimated, lyrics.author,
        words={index: list(words) for index, words in lyrics.words.items()
               if index not in changed})
    result.romanized_lines = frozenset(changed)
    return result
