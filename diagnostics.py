"""Offline release smoke check; never creates windows or captures system audio."""
import importlib.util
import json
import os
import sys
from pathlib import Path


def run_self_test(path, model_path=None):
    """Catch startup failures; preserve the last stage even for a native crash."""
    report = {"status": "running", "stage": "imports",
              "extraction_dir": getattr(sys, "_MEIPASS", None)}
    try:
        if os.name == "nt":
            import ctypes
            # Only diagnostic subprocesses: a native fault must fail CI, not
            # open a Windows Error Reporting dialog and wait for a person.
            ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)
        _save_report(path, report)
        import main  # full application imports without starting the application
        report.update(check_packaged_features())
        report["msvc_runtime"] = loaded_msvc_runtime()
        if model_path is not None:
            if not report["ai_available"]:
                raise RuntimeError("This edition has no AI engine")
            exercise_ai(model_path, report, path)
        report.update(status="passed", stage="complete")
        _save_report(path, report)
        return 0
    except Exception as error:
        try:
            report.update(status="failed", error=f"{type(error).__name__}: {error}")
            _save_report(path, report)
        except OSError:
            pass
        return 1


def _save_report(path, report):
    Path(path).write_text(json.dumps(report, indent=2), encoding="utf-8")


def loaded_msvc_runtime():
    """Record the DLL actually loaded, rather than only inspecting the archive."""
    if os.name != "nt":
        return None
    import ctypes
    from packaging_runtime import inspect_runtime, MIN_RUNTIME_VERSION

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel.GetModuleHandleW.restype = ctypes.c_void_p
    kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                        ctypes.c_uint32]
    kernel.GetModuleFileNameW.restype = ctypes.c_uint32
    module = kernel.GetModuleHandleW("msvcp140.dll")
    if not module:
        raise RuntimeError("Microsoft C++ runtime was not loaded")
    filename = ctypes.create_unicode_buffer(32768)
    if not kernel.GetModuleFileNameW(module, filename, len(filename)):
        raise ctypes.WinError(ctypes.get_last_error())
    machine, version = inspect_runtime(filename.value)
    if getattr(sys, "frozen", False) and (machine != 0x8664 or version < MIN_RUNTIME_VERSION):
        raise RuntimeError(f"Incompatible packaged Microsoft C++ runtime: {filename.value}")
    return {"path": filename.value, "version": ".".join(map(str, version))}


def exercise_ai(model_path, report, report_path):
    """Run native inference offline on synthetic audio, including the VAD path."""
    import numpy as np
    from faster_whisper import WhisperModel

    model_path = Path(model_path).resolve(strict=True)
    for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
        model_file = model_path / name
        if not model_file.is_file() or not model_file.stat().st_size:
            raise ValueError(f"Incomplete local model: {name}")
    report.update(stage="model-load", ai_inference=False)
    _save_report(report_path, report)
    model = WhisperModel(str(model_path), device="cpu", compute_type="int8",
                         local_files_only=True)
    report.update(stage="inference")
    _save_report(report_path, report)
    audio = np.random.default_rng(1).normal(0, 0.01, 16000).astype(np.float32)
    segments, _ = model.transcribe(audio, language="es", word_timestamps=True,
                                   vad_filter=False, beam_size=1)
    # transcribe returns a lazy generator. Importing or calling it alone never
    # exercised the CTranslate2 encoder/decoder and missed the original crash.
    segments = list(segments)
    report.update(stage="voice-activity-detection", segment_count=len(segments))
    _save_report(report_path, report)
    vad_segments, _ = model.transcribe(audio, language="es", word_timestamps=True,
                                       vad_filter=True, beam_size=1)
    list(vad_segments)
    report["ai_inference"] = True


def check_packaged_features():
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
        from faster_whisper import WhisperModel
    return {
        "version": APP_VERSION,
        "channel": updater.channel, "automatic_updates": updater.enabled,
        "ai_available": has_ai, "romanization": "ja/zh/ko",
        "enhanced_lrc": True,
    }
