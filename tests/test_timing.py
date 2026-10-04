import json
import math
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from autosyncai import AutoSyncAI, _clock_continuous, _norm_word
from lyrics import Lyrics, fetch_lyrics, parse_lrc, parse_lrc_timing, save_ai_sync
from timing import normalize_word_timings, word_progress


class WordProgressTests(unittest.TestCase):
    def setUp(self):
        self.text = "Stay with me"
        self.words = [(1, 8, 0, 4), (10, 11, 5, 9), (12, 15, 10, 12)]

    def test_long_vowel_never_consumes_next_word(self):
        for pos in (1, 3, 7.99, 8, 9.99):
            self.assertEqual(word_progress(self.text, self.words, pos), 4)
            self.assertLessEqual(word_progress(self.text, self.words, pos, "char"), 4)
        self.assertEqual(word_progress(self.text, self.words, 10), 9)

    def test_character_sweep_uses_word_end_then_holds_during_silence(self):
        self.assertEqual(word_progress(self.text, self.words, 0, "char"), 0)
        self.assertEqual(word_progress(self.text, self.words, 4.5, "char"), 2)
        self.assertEqual(word_progress(self.text, self.words, 9, "char"), 4)
        self.assertEqual(word_progress(self.text, self.words, 10.5, "char"), 7)
        self.assertEqual(word_progress(self.text, self.words, 11.5, "char"), 9)
        self.assertEqual(word_progress(self.text, self.words, 30, "char"), len(self.text))

    def test_line_timing_does_not_invent_word_progress(self):
        self.assertIsNone(word_progress(self.text, None, 10))
        self.assertIsNone(word_progress(self.text, [], 10))

    def test_legacy_cache_holds_onset_instead_of_guessing_next_duration(self):
        words = [(1, 4), (10, 9), (12, 12)]
        self.assertEqual(word_progress(self.text, words, 5, "char"), 4)
        self.assertEqual(word_progress(self.text, words, 10, "char"), 9)
        self.assertEqual(normalize_word_timings(self.text, words),
                         [(1, 1, 0, 4), (10, 10, 5, 9), (12, 12, 10, 12)])

    def test_malformed_entries_do_not_poison_valid_data(self):
        words = [None, [math.nan, 3, 0, 3], [1, 2, 0, 4], [1, 4, 2, 8],
                 [2, 1, 4, 5], [2, 3, 4, 99], [2, 3, 4.5, 9], [10, 11, 5, 9]]
        self.assertEqual(normalize_word_timings(self.text, words),
                         [(1, 2, 0, 4), (10, 11, 5, 9)])
        self.assertEqual(word_progress(self.text, self.words, math.nan), 0)


class LrcAndCacheTests(unittest.TestCase):
    def test_enhanced_lrc_preserves_durations_and_offset(self):
        lrc = ("[offset:500]\n[by:Alice]\n"
               "[00:10.00] <00:10.00>Stay <00:18.00>here<00:20.00>  \n"
               "[00:22]Next line")
        lines, words = parse_lrc_timing(lrc)
        self.assertEqual(lines, [(9.5, "Stay here"), (21.5, "Next line")])
        self.assertEqual(words, {0: [(9.5, 17.5, 0, 4), (17.5, 19.5, 5, 9)]})
        self.assertEqual(parse_lrc(lrc), lines)

    def test_repeated_enhanced_lines_shift_words_even_with_mixed_tag_formats(self):
        lines, words = parse_lrc_timing(
            "[offset:-250]\n[00:20][00:10.00]<00:20>Go <00:22>home<00:25>")
        self.assertEqual(lines, [(10.25, "Go home"), (20.25, "Go home")])
        self.assertEqual(words[0], [(10.25, 12.25, 0, 2), (12.25, 15.25, 3, 7)])
        self.assertEqual(words[1], [(20.25, 22.25, 0, 2), (22.25, 25.25, 3, 7)])

    def test_last_word_without_end_is_onset_only(self):
        lines, words = parse_lrc_timing("[00:10]<00:10>Hello <00:12>world")
        self.assertEqual(words[0][-1], (12, 12, 6, 11))
        self.assertEqual(word_progress(lines[0][1], words[0], 12, "char"), 11)

    def test_plain_lrc_remains_without_words_and_supports_hour_timestamps(self):
        lines, words = parse_lrc_timing("[ar:Artist]\n[01:02:03.5]Hello\n[00:15]World")
        self.assertEqual(lines, [(15, "World"), (3723.5, "Hello")])
        self.assertEqual(words, {})

    def test_canonical_cache_round_trip_preserves_word_ends(self):
        original = Lyrics([(10, "Hold on")], "ai", True,
                          words={0: [(10, 20, 0, 4), (23, 24, 5, 7)]})
        loaded = Lyrics.from_dict(json.loads(json.dumps(original.to_dict())))
        self.assertEqual(loaded.words, original.words)
        self.assertTrue(loaded.synced)
        self.assertFalse(loaded.estimated)

    def test_bad_cache_line_does_not_remove_other_lines_words(self):
        loaded = Lyrics.from_dict({"lines": [[10, "Hold"], [20, "Go home"]],
                                   "words": {"n/a": [], "9": [], "0": [[10, 4]],
                                             "1": [[20, 21, 0, 2], [None], [22, 24, 3, 7]]}})
        self.assertEqual(loaded.words[0], [(10, 10, 0, 4)])
        self.assertEqual(len(loaded.words[1]), 2)

    def test_legacy_ai_estimates_are_not_claimed_to_be_real_sync(self):
        loaded = Lyrics.from_dict({"lines": [[10, "Hold"]], "source": "ai",
                                   "synced": True, "words": {"0": [[10, 4]]}})
        self.assertFalse(loaded.synced)
        self.assertTrue(loaded.estimated)

    def test_fetch_and_cache_preserve_provider_enhanced_data(self):
        record = {"duration": 100, "syncedLyrics": "[00:10]<00:10>Hold<00:15>"}
        with tempfile.TemporaryDirectory() as directory:
            with patch("lyrics.CACHE_DIR", directory), patch("lyrics._lrclib_get", return_value=[record]):
                result = fetch_lyrics("Artist", "Title", duration=100)
                cached = fetch_lyrics("Artist", "Title", duration=100)
        self.assertEqual(result.words[0], [(10, 15, 0, 4)])
        self.assertEqual(cached.words, result.words)

    def test_save_sorted_lines_remaps_word_indices(self):
        with tempfile.TemporaryDirectory() as directory, patch("lyrics.CACHE_DIR", directory):
            result = save_ai_sync("A", "T", 30, [(20, "Late"), (10, "Early")],
                                  {0: [(20, 22, 0, 4)], 1: [(10, 15, 0, 5)]})
        self.assertEqual(result.words[0], [(10, 15, 0, 5)])
        self.assertEqual(result.words[1], [(20, 22, 0, 4)])


class AlignmentTests(unittest.TestCase):
    def test_alignment_keeps_sustained_word_end_and_actual_next_start(self):
        lines = [(0, "Stay with me"), (5, "One more day")]
        words = [(1, 8, "Stay"), (10, 11, "with"), (12, 14, "me"),
                 (20, 21, "One"), (21, 22, "more"), (22, 28, "day")]
        synced, intervals = AutoSyncAI._align(lines, words, 2)
        self.assertEqual(synced, [(3, "Stay with me"), (22, "One more day")])
        self.assertEqual(intervals[0], [(3, 10, 0, 4), (12, 13, 5, 9), (14, 16, 10, 12)])

    def test_sparse_matches_and_unanchored_lines_are_rejected(self):
        lines = [(0, "Stay with me forever"), (5, "One more sunny day")]
        words = [(1, 8, "Stay"), (10, 12, "One")]
        self.assertIsNone(AutoSyncAI._align(lines, words, 0))
        self.assertIsNone(AutoSyncAI._align(lines, words, 0, preserve_line_times=True))

    def test_middle_word_cannot_become_line_start(self):
        self.assertIsNone(AutoSyncAI._align([(0, "Please stay with me")],
                         [(1, 2, "stay"), (3, 4, "with"), (5, 6, "me")], 0))

    def test_trusted_lrc_preserved_and_repeated_chorus_cannot_move(self):
        lines = [(0, "Stay with me"), (20, "Stay with me"), (40, "Keep on going")]
        words = [(21, 25, "Stay"), (26, 27, "with"), (28, 30, "me")]
        synced, intervals = AutoSyncAI._align(lines, words, 0, preserve_line_times=True)
        self.assertEqual(synced, lines)
        self.assertEqual(list(intervals), [1])
        self.assertEqual(intervals[1][0][:2], (21, 25))

    def test_words_crossing_trusted_line_window_rejected(self):
        self.assertIsNone(AutoSyncAI._align([(0, "Hold"), (10, "Other")],
                                            [(1, 20, "Hold")], 0, True))

    def test_delayed_lrc_onsets_are_corrected_without_shortening_held_words(self):
        lines = [(13, "Stay with me"), (23, "One more day"), (31, "Let us sing")]
        words = [(10, 16, "Stay"), (17, 18, "with"), (19, 20, "me"),
                 (21, 22, "One"), (23, 24, "more"), (25, 28, "day"),
                 (30, 31, "Let"), (31, 32, "us"), (33, 37, "sing")]
        synced, intervals = AutoSyncAI._align(lines, words, 0, True)
        self.assertEqual(synced, [(10, "Stay with me"), (21, "One more day"),
                                  (30, "Let us sing")])
        self.assertEqual(intervals[0][0], (10, 16, 0, 4))
        self.assertEqual(word_progress(synced[0][1], intervals[0], 16.9), 4)
        self.assertLess(word_progress(synced[0][1], intervals[0], 15, "char"), 4)

    def test_partial_capture_corrects_corroborated_neighbors_only(self):
        lines = [(12, "Stay with me"), (22, "One more day"),
                 (32, "Let us sing"), (42, "Come back home")]
        words = [(21, 22, "One"), (23, 24, "more"), (25, 28, "day"),
                 (31, 32, "Let"), (33, 34, "us"), (35, 37, "sing")]
        synced, intervals = AutoSyncAI._align(lines, words, 0, True)
        self.assertEqual([time for time, _text in synced], [12, 21, 31, 42])
        self.assertEqual(list(intervals), [1, 2])

    def test_isolated_early_phrase_cannot_move_a_trusted_line(self):
        lines = [(12, "Stay with me"), (22, "One more day")]
        words = [(10, 13, "Stay"), (14, 15, "with"), (16, 17, "me")]
        self.assertIsNone(AutoSyncAI._align(lines, words, 0, True))

    def test_ambiguous_repeated_phrase_cannot_move_a_trusted_line(self):
        lines = [(10, "Come back home"), (20, "One more day"),
                 (30, "Unknown lyric here")]
        words = [(7, 8, "Come"), (8, 8.5, "back"), (8.5, 9, "home"),
                 (10, 11, "Come"), (11, 12, "back"), (12, 13, "home"),
                 (19, 20, "One"), (21, 22, "more"), (23, 24, "day")]
        synced, _intervals = AutoSyncAI._align(lines, words, 0, True)
        self.assertEqual(synced, lines)

    def test_different_recording_timing_is_not_used_as_a_small_correction(self):
        lines = [(12, "Stay with me"), (22, "One more day"), (32, "Let us sing")]
        words = [(1, 2, "Stay"), (3, 4, "with"), (5, 6, "me"),
                 (10, 11, "One"), (12, 13, "more"), (14, 15, "day"),
                 (20, 21, "Let"), (22, 23, "us"), (24, 25, "sing")]
        self.assertIsNone(AutoSyncAI._align(lines, words, 0, True))
        unrelated = [(10, 11, "Hello"), (12, 14, "stranger"), (15, 17, "tonight")]
        self.assertIsNone(AutoSyncAI._align(lines, unrelated, 0, True))

    def test_correction_cannot_cross_an_unsupported_silence_marker(self):
        lines = [(12, "Stay with me"), (15, ""), (22, "One more day")]
        words = [(10, 13, "Stay"), (14, 15, "with"), (16, 17, "me"),
                 (20, 21, "One"), (22, 23, "more"), (24, 25, "day")]
        synced, intervals = AutoSyncAI._align(lines, words, 0, True)
        self.assertEqual(synced[:2], lines[:2])
        self.assertEqual(synced[2][0], 20)
        self.assertNotIn(0, intervals)

    def test_japanese_and_other_scripts_remain_matchable(self):
        self.assertEqual(_norm_word("が世界！"), "が世界")
        self.assertEqual(_norm_word("Привет!"), "привет")
        self.assertEqual(_norm_word("corazón"), "corazon")
        result = AutoSyncAI._align([(0, "世界が好き")],
                                   [(1, 3, "世界"), (4, 5, "が"), (6, 9, "好き")], 0)
        self.assertIsNotNone(result)
        self.assertEqual(result[1][0], [(1, 3, 0, 2), (4, 5, 2, 3), (6, 9, 3, 5)])

    def test_transcription_requests_and_preserves_word_end(self):
        fake_word = types.SimpleNamespace(start=2.0, end=8.5, word=" Hold")
        model = types.SimpleNamespace(transcribe=lambda *a, **kw: (
            iter([types.SimpleNamespace(words=[fake_word])]), None))
        factory = unittest.mock.Mock(return_value=model)
        with patch.dict("sys.modules", {"faster_whisper": types.SimpleNamespace(WhisperModel=factory)}):
            self.assertEqual(AutoSyncAI._transcribe("unused.wav"), [(2, 8.5, " Hold")])

    def test_lite_build_does_not_capture_when_transcriber_unavailable(self):
        engine = AutoSyncAI(lambda *args: None)
        with patch("autosyncai.importlib.util.find_spec", return_value=None), patch("autosyncai.threading.Thread") as thread:
            engine.start("track", lambda: (0, True), [(0, "Hold")], 100)
        thread.assert_not_called()
        self.assertFalse(engine.busy)

    def test_cancel_during_alignment_suppresses_stale_callback(self):
        callback = unittest.mock.Mock()
        cancel = threading.Event()
        engine = AutoSyncAI(callback)

        def alignment(*args):
            cancel.set()
            return [(1, "Hold")], {0: [(1, 5, 0, 4)]}

        with patch.object(engine, "_capture", return_value=0), \
             patch.object(engine, "_transcribe", return_value=[(1, 5, "Hold")]), \
             patch.object(engine, "_align", side_effect=alignment):
            engine._run("key", lambda: (0, True), [(0, "Hold")], 10, cancel)
        callback.assert_not_called()


class CaptureTests(unittest.TestCase):
    def _capture(self, positions, directory):
        stream = unittest.mock.Mock()
        stream.read.return_value = b"\x00" * 20  # 0.1 seconds, 100 Hz, mono int16
        backend = unittest.mock.Mock()
        backend.get_host_api_info_by_type.return_value = {"defaultOutputDevice": 0}
        backend.get_device_info_by_index.return_value = {"name": "Speaker"}
        backend.get_loopback_device_info_generator.return_value = iter([
            {"name": "Speaker loopback", "defaultSampleRate": 100,
             "maxInputChannels": 1, "index": 1}])
        backend.open.return_value = stream
        module = types.SimpleNamespace(PyAudio=lambda: backend, paWASAPI=1, paInt16=2)
        getter = unittest.mock.Mock(side_effect=positions)
        path = str(Path(directory) / "capture.wav")
        with patch.dict("sys.modules", {"pyaudiowpatch": module}), patch("autosyncai.MAX_SECONDS", 0.2):
            result = AutoSyncAI._capture(path, getter, threading.Event(), 100)
        stream.close.assert_called_once()
        backend.terminate.assert_called_once()
        return result, path, stream

    def test_continuous_capture_has_a_valid_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            result, path, stream = self._capture([(10, True), (10.1, True), (10.2, True)], directory)
            self.assertEqual(result, 10)
            self.assertTrue(Path(path).is_file())
            self.assertTrue(stream.read.call_args.kwargs["exception_on_overflow"])

    def test_pause_discards_capture_instead_of_aligning_across_it(self):
        with tempfile.TemporaryDirectory() as directory:
            result, path, _stream = self._capture([(10, True), (10, False)], directory)
            self.assertIsNone(result)
            self.assertFalse(Path(path).exists())

    def test_seek_discards_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            result, path, _stream = self._capture([(10, True), (40, True)], directory)
            self.assertIsNone(result)
            self.assertFalse(Path(path).exists())
        self.assertFalse(_clock_continuous(10, 5, 1))
        self.assertTrue(_clock_continuous(10, 11.2, 1))


if __name__ == "__main__":
    unittest.main()
