"""Offline release smoke check; never creates windows or captures system audio."""
import importlib.util
import json
from pathlib import Path


def run_self_test(path):
    """Catch startup failures too, so a broken GUI build cannot hang CI."""
    try:
        import main  # full application imports without starting the application
        write_self_test(path)
        return 0
    except Exception as error:
        try:
            Path(path).write_text(json.dumps({"status": "failed",
                "error": f"{type(error).__name__}: {error}"}, indent=2), encoding="utf-8")
        except OSError:
            pass
        return 1


def write_self_test(path):
    """Exercise packaged imports and dictionaries; raises if the build is broken."""
    from appconfig import APP_VERSION
    from display_lyrics import prepare_display_lyrics
    from lyrics import Lyrics, parse_lrc_timing
    from timing import word_progress
    from updater import AutoUpdater
    from translate import romanize
    import pyaudiowpatch
    import segno
    import syncedlyrics
    import shazamio
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager

    updater = AutoUpdater(APP_VERSION)
    display = prepare_display_lyrics(Lyrics([(0, "こんにちは"), (2, "世界")], "test", True))
    assert display.lines[1][1] == "sekai", "Japanese dictionary missing"
    assert romanize("你好", "zh") == "ni hao", "Chinese dictionary missing"
    assert romanize("안녕하세요", "ko") == "annyeonghaseyo", "Korean romanizer missing"
    lines, words = parse_lrc_timing("[00:01]<00:01>Hold <00:09>on<00:10>")
    assert word_progress(lines[0][1], words[0], 8, "word") == 4
    has_ai = importlib.util.find_spec("faster_whisper") is not None
    if updater.enabled:
        assert has_ai == (updater.channel == "full"), "Incorrect full/lite build marker"
    if has_ai:
        from faster_whisper import WhisperModel  # verify native libraries, no model download
    Path(path).write_text(json.dumps({
        "status": "passed", "version": APP_VERSION,
        "channel": updater.channel, "automatic_updates": updater.enabled,
        "ai_available": has_ai, "romanization": "ja/zh/ko",
        "enhanced_lrc": True,
    }, indent=2), encoding="utf-8")
