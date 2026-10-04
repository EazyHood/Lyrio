"""Controller regressions without creating windows or touching user settings."""
import os
import atexit
import logging
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

if sys.platform != "win32":
    raise unittest.SkipTest("Lyrio's controller imports Windows SMTC")

_data = tempfile.TemporaryDirectory()
with patch.dict(os.environ, {"APPDATA": _data.name, "LOCALAPPDATA": _data.name}):
    import main
atexit.register(logging.shutdown)
from lyrics import Lyrics
from overlay import LyricsOverlay


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.app = main.LyrioApp.__new__(main.LyrioApp)
        self.settings = {"romanize": True, "romanization_language": "auto"}
        self.app.cfg = SimpleNamespace(get=self.settings.get)
        self.app._display_cache = []

    def test_display_copy_keeps_source_and_is_cached(self):
        source = Lyrics([(1, "世界"), (3, "こんにちは")], "lrclib", True)
        display = self.app.display_lyrics(source)
        self.assertIs(display, self.app.display_lyrics(source))
        self.assertEqual(display.lines[0], (1, "sekai"))
        self.assertEqual(source.lines[0], (1, "世界"))
        self.settings["romanize"] = False
        self.assertIs(self.app.display_lyrics(source), source)

    def test_line_only_lyrics_never_enable_fake_word_sweep(self):
        lyrics = Lyrics([(10, "hold the note"), (20, "next")], "lrclib", True)
        overlay = Mock(ov={"sweep_mode": "word"})
        self.app._render_timed_overlay(overlay, lyrics, 0, 15, 60,
                                      "hold the note", "next")
        self.assertFalse(overlay.render.call_args.kwargs["timed"])
        overlay.update_progress.assert_called_once_with(0.5, None)

    def test_sustained_word_does_not_light_the_next_word_early(self):
        lyrics = Lyrics([(10, "hold on"), (20, "next")], "ai", True,
                        words={0: [(10, 16, 0, 4), (18, 19, 5, 7)]})
        overlay = Mock(ov={"sweep_mode": "word"})
        self.app._render_timed_overlay(overlay, lyrics, 0, 17, 60, "hold on", "next")
        self.assertTrue(overlay.render.call_args.kwargs["timed"])
        overlay.update_progress.assert_called_once_with(0.7, 4.0)

    def test_romanized_offsets_cannot_highlight_different_characters(self):
        lyrics = Lyrics([(10, "こんにちは世界")], "ai", True,
                        words={0: [(10, 18, 0, 7)]})
        display = self.app.display_lyrics(lyrics)
        overlay = Mock(ov={})
        self.app._render_timed_overlay(overlay, display, 0, 14, 60,
                                      display.lines[0][1], "")
        self.assertFalse(overlay.render.call_args.kwargs["timed"])

    def test_uppercase_expansion_keeps_measured_character_boundaries(self):
        overlay = LyricsOverlay.__new__(LyricsOverlay)
        overlay.ov = {"caps": True}
        overlay._current = "groß jetzt"
        overlay._sung_chars = 4
        self.assertEqual(overlay._sweep_chars(0.9, "GROSS JETZT", []), 5)

    def test_late_callback_from_disabled_updater_is_ignored(self):
        self.app.updater = object()
        self.app._stop_event = threading.Event()
        self.app.window = Mock()
        self.app._update_state = "disabled"
        self.app._update_status(object(), "ready", {"version": "9.0.0"})
        self.assertEqual(self.app._update_state, "disabled")
        self.app.window.refresh_update_status.assert_not_called()

    def test_tray_hide_never_installs_updates(self):
        self.settings["tray_notice_shown"] = True
        self.app.updater = Mock()
        self.app.root = Mock()
        self.app.hide_window()
        self.app.root.withdraw.assert_called_once()
        self.app.updater.install_pending_on_exit.assert_not_called()

    def test_restart_aborts_if_update_cannot_be_prepared(self):
        self.app._quitting = False
        self.app.updater = Mock()
        self.app.updater.install_pending_on_exit.side_effect = main.UpdateError("read only")
        self.app.window = Mock()
        self.app.on_quit(restart=True)
        self.app.window.flash_status.assert_called_once()
        self.assertFalse(self.app._quitting)


class TimingControllerTests(unittest.TestCase):
    def setUp(self):
        self.app = main.LyrioApp.__new__(main.LyrioApp)
        self.key = ("Artist", "Song", 120)
        self.state = SimpleNamespace(artist="Artist", title="Song", duration=120,
                                     track_key=self.key, playing=True,
                                     position_now=Mock(return_value=10),
                                     source_app="Spotify")
        self.app.cfg = SimpleNamespace(get={"sync_author": "Listener",
                                           "offset_global": 0.5}.get)
        self.app._lock = threading.Lock()
        self.app._lyrics = Lyrics([(12, "Stay with me"), (22, "One more day")],
                                   "lrclib", True)
        self.app._lyrics_key = self.key
        self.app._current_key = self.key
        self.app._cal_state = None
        self.app._offsets = {}
        self.app._ai_tried = set()
        self.app._id_tried = set()
        self.app._failed = {}
        self.app._fetching_key = None
        self.app.autosync_ai = Mock(busy=False)
        self.app.audiocal = Mock()
        self.app.watcher = Mock()
        self.app.watcher.get_state.return_value = self.state
        self.app.window = Mock()
        self.app.translator = Mock()
        self.app.ui_call = Mock()

    def test_manual_song_offset_prevents_double_correction_but_can_be_reset(self):
        for offset in (-2.0, 2.0):
            with self.subTest(offset=offset):
                self.app._offsets[self.key] = offset
                self.app._maybe_ai_sync(self.state)
                self.app.autosync_ai.start.assert_not_called()
                self.assertEqual(self.app._offsets[self.key], offset)
                self.assertNotIn(self.key, self.app._ai_tried)
        self.app._offsets.pop(self.key)
        self.app._maybe_ai_sync(self.state)
        self.app.autosync_ai.start.assert_called_once()
        self.assertTrue(self.app.autosync_ai.start.call_args.kwargs["preserve_line_times"])

    def test_offset_changed_during_capture_blocks_result_and_success_notice(self):
        self.app._maybe_ai_sync(self.state)
        original = self.app._lyrics
        self.app._offsets[self.key] = 2.0
        with patch("main.save_ai_sync") as save:
            self.app._on_ai_sync(self.key, [(10, "Stay with me")],
                                  {0: [(10, 16, 0, 4)]})
        save.assert_not_called()
        self.app.ui_call.assert_not_called()
        self.assertIs(self.app._lyrics, original)
        self.assertEqual(self.app._offsets[self.key], 2.0)

    def test_audio_calibration_skips_a_manually_corrected_song_at_start(self):
        self.app._lyrics.estimated = True
        self.app._lyrics.synced = False
        for offset in (-2.0, 2.0):
            with self.subTest(offset=offset):
                self.app._offsets[self.key] = offset
                self.app._maybe_calibrate(self.state)
                self.app.audiocal.start.assert_not_called()
                self.assertEqual(self.app._offsets[self.key], offset)

    def test_audio_calibration_callback_preserves_offset_or_new_manual_sync(self):
        self.app._cal_state = self.state
        self.app._lyrics.estimated = True
        self.app._lyrics.synced = False
        original = self.app._lyrics
        self.app._offsets[self.key] = 2.0
        with patch("main.apply_anchor") as anchor:
            self.app._on_audio_anchor(self.key, 4.0)
            anchor.assert_not_called()
            self.assertIs(self.app._lyrics, original)
            self.app._offsets.pop(self.key)
            manual = Lyrics([(11, "Stay with me")], "user", True)
            self.app._lyrics = manual
            self.app._on_audio_anchor(self.key, 4.0)
            anchor.assert_not_called()
            self.assertIs(self.app._lyrics, manual)
        self.app.ui_call.assert_not_called()

    def test_existing_user_sync_never_starts_automatic_alignment(self):
        self.app._lyrics.source = "user"
        self.app._maybe_ai_sync(self.state)
        self.app.autosync_ai.start.assert_not_called()
        self.assertNotIn(self.key, self.app._ai_tried)

    def test_manual_sync_cancels_capture_and_rejects_a_late_ai_callback(self):
        stamped = [(11, "Stay with me"), (21, "One more day")]
        manual = Lyrics(stamped, "user", True, author="Listener")
        self.app._offsets[self.key] = 2.0
        with patch("main.save_user_sync", return_value=manual), \
             patch("main.save_offset") as save_offset:
            self.app.apply_user_sync(self.key, stamped)
        self.app.autosync_ai.cancel.assert_called_once()
        self.app.audiocal.cancel.assert_called_once()
        save_offset.assert_called_once_with(*self.key, 0.0)
        with patch("main.save_ai_sync") as save_ai:
            self.app._on_ai_sync(self.key, [(10, "Stay with me")])
        save_ai.assert_not_called()
        self.app.ui_call.assert_not_called()
        self.assertIs(self.app._lyrics, manual)
        self.assertEqual(self.app._lyrics.lines, stamped)

    def test_paused_player_does_not_start_capture_or_consume_retry(self):
        self.state.playing = False
        self.app._maybe_ai_sync(self.state)
        self.app.autosync_ai.start.assert_not_called()
        self.assertNotIn(self.key, self.app._ai_tried)
        self.state.playing = True
        self.app._maybe_ai_sync(self.state)
        self.app.autosync_ai.start.assert_called_once()

    def test_capture_getter_observes_pause_track_change_and_seek(self):
        self.app._maybe_ai_sync(self.state)
        getter = self.app.autosync_ai.start.call_args.args[1]
        self.assertEqual(getter(), (10, True))
        self.state.playing = False
        self.assertEqual(getter(), (10, False))
        self.state.playing = True
        self.state.position_now.return_value = 35
        self.assertEqual(getter(), (35, True))  # core rejects this discontinuity
        self.state.track_key = ("Other", "Track", 180)
        self.assertEqual(getter(), (35, False))

    def test_seek_accounts_for_song_and_global_offsets_once(self):
        self.app._offsets[self.key] = 2.0
        self.app.seek(20)
        self.app.watcher.request_seek.assert_called_once_with(17.5)

    def test_busy_capture_does_not_prevent_retry_when_worker_finishes(self):
        self.app.autosync_ai.busy = True
        self.app._maybe_ai_sync(self.state)
        self.assertNotIn(self.key, self.app._ai_tried)
        self.app.autosync_ai.busy = False
        self.app._maybe_ai_sync(self.state)
        self.app.autosync_ai.start.assert_called_once()

    def test_track_change_cancels_old_capture_and_allows_new_attempt(self):
        self.app._ai_tried.add(self.key)
        self.app._start_fetch = Mock()
        self.app._on_track_change(self.state)
        self.app.autosync_ai.cancel.assert_called_once()
        self.app.audiocal.cancel.assert_called_once()
        self.assertFalse(self.app._ai_tried)
        self.app._start_fetch.assert_called_once_with(self.state)

    def test_reload_cancels_capture_without_erasing_manual_offset(self):
        self.app._offsets[self.key] = 2.0
        self.app._ai_tried.add(self.key)
        self.app._start_fetch = Mock()
        with patch("main.clear_cache_entry"):
            self.app.reload_lyrics()
        self.app.autosync_ai.cancel.assert_called_once()
        self.app.audiocal.cancel.assert_called_once()
        self.assertNotIn(self.key, self.app._ai_tried)
        self.assertEqual(self.app._offsets[self.key], 2.0)
        self.app._start_fetch.assert_called_once_with(self.state, use_cache=False)


if __name__ == "__main__":
    unittest.main()
