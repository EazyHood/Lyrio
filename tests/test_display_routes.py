"""Display/source integration across playback surfaces, without opening a UI."""
import os
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

if sys.platform != "win32":
    raise unittest.SkipTest("Lyrio's controller imports Windows SMTC")

_data = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
with patch.dict(os.environ, {"APPDATA": _data.name, "LOCALAPPDATA": _data.name}):
    import main
from lyrics import Lyrics


class DisplayRoutesTests(unittest.TestCase):
    def setUp(self):
        self.app = main.LyrioApp.__new__(main.LyrioApp)
        self.settings = {"romanize": True, "romanization_language": "auto",
                         "translate": True, "overlay_hint_shown": True}
        self.app.cfg = SimpleNamespace(get=self.settings.get)
        self.app._display_cache = []
        self.app._lock = threading.Lock()
        self.app._offsets = {}
        self.app._failed = {}
        self.app._fetching_key = None
        self.app._loaded_at = time.monotonic() - 20
        self.source = Lyrics([(1.0, "世界"), (4.0, "こんにちは")], "lrclib", True,
                             words={0: [(1.0, 3.0, 0, 2)]})
        self.app._lyrics = self.source
        self.state = SimpleNamespace(
            connected=True, title="song", artist="singer", source_app="spotify",
            duration=10.0, playing=True, track_key=("singer", "song", 10),
            position_now=lambda: 2.0)
        self.app._lyrics_key = self.state.track_key
        self.app.watcher = SimpleNamespace(get_state=lambda: self.state,
                                           get_state2=lambda: self.state)
        self.app.root = Mock()
        self.app.root.state.return_value = "normal"
        self.app.window = Mock()
        self.app.translator = Mock()
        self.app.translator.get.return_value = "translated source"
        self.app.party = Mock()
        self.app.overlay = Mock(ov={"sweep_mode": "word"})

    def test_main_window_party_and_phone_use_display_translation_uses_source(self):
        self.app._render()
        self.assertEqual(self.app.overlay.render.call_args.args[:2],
                         ("sekai", "konnichiha"))
        self.assertFalse(self.app.overlay.render.call_args.kwargs["timed"])
        self.app.overlay.update_progress.assert_called_with(1 / 3, None)
        self.assertEqual(self.app.window.update_now_playing.call_args.args[1].lines[0],
                         (1.0, "sekai"))
        self.app.translator.get.assert_called_with("世界", "en")
        self.assertEqual(self.app.party.render.call_args.args[3:5],
                         ("sekai", "konnichiha"))
        self.assertEqual(self.app._now["current"], "sekai")
        self.assertEqual(self.source.lines[0], (1.0, "世界"))

        self.settings["romanize"] = False
        self.app._render()
        self.assertEqual(self.app.overlay.render.call_args.args[0], "世界")
        self.assertTrue(self.app.overlay.render.call_args.kwargs["timed"])
        self.app.overlay.update_progress.assert_called_with(1 / 3, 2.0)
        self.assertIs(self.app.window.update_now_playing.call_args.args[1], self.source)

    def test_secondary_overlay_uses_same_display_without_changing_source(self):
        self.app.overlay2 = Mock(ov={"sweep_mode": "word"})
        self.app._key2 = self.state.track_key
        self.app._lyrics2 = self.source
        self.app._tick_secondary()
        self.assertEqual(self.app.overlay2.render.call_args.args[:2],
                         ("sekai", "konnichiha"))
        self.assertFalse(self.app.overlay2.render.call_args.kwargs["timed"])
        self.assertIs(self.app._lyrics2, self.source)

    def test_radio_party_and_phone_use_same_display(self):
        self.app._radio = {"lyr": self.source, "song_pos": 2.0,
                           "at_mono": time.monotonic(), "artist": "singer",
                           "title": "song"}
        self.assertTrue(self.app._radio_render())
        self.assertEqual(self.app.overlay.render.call_args.args[:2],
                         ("sekai", "konnichiha"))
        self.assertFalse(self.app.overlay.render.call_args.kwargs["timed"])
        self.assertEqual(self.app._now["current"], "sekai")
        self.assertEqual(self.app.party.render.call_args.args[3], "sekai")
        self.assertIs(self.app._radio["lyr"], self.source)


if __name__ == "__main__":
    unittest.main()
