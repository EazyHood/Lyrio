"""Exercise release diagnostics without loading native libraries or the UI."""
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import diagnostics


class NativeDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.model_path = self.root / "local model"
        self.model_path.mkdir()
        for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
            (self.model_path / name).write_bytes(b"test fixture")
        self.report_path = self.root / "report.json"
        self.model = Mock()
        self.factory = Mock(return_value=self.model)
        whisper = ModuleType("faster_whisper")
        whisper.WhisperModel = self.factory
        numpy = ModuleType("numpy")
        self.audio = object()
        samples = Mock()
        samples.astype.return_value = self.audio
        numpy.random = SimpleNamespace(default_rng=Mock(
            return_value=SimpleNamespace(normal=Mock(return_value=samples))))
        numpy.float32 = object()
        # The real main imports Windows UI modules. Error mode is process-wide,
        # so mock that too; tests must not change the runner's native behavior.
        modules = patch.dict(sys.modules, {
            "main": ModuleType("main"), "faster_whisper": whisper,
            "numpy": numpy,
            "ctypes": SimpleNamespace(windll=SimpleNamespace(
                kernel32=SimpleNamespace(SetErrorMode=Mock()))),
        })
        modules.start()
        self.addCleanup(modules.stop)
        features = patch.object(diagnostics, "check_packaged_features", return_value={
            "version": "test", "channel": "full", "automatic_updates": True,
            "ai_available": True,
        })
        features.start()
        self.addCleanup(features.stop)
        runtime = patch.object(diagnostics, "loaded_msvc_runtime", return_value={
            "path": "test-runtime", "version": "14.40.0.0",
        })
        runtime.start()
        self.addCleanup(runtime.stop)

    def run_diagnostic(self, model_path=None):
        result = diagnostics.run_self_test(
            self.report_path,
            model_path=self.model_path if model_path is None else model_path,
        )
        return result, json.loads(self.report_path.read_text(encoding="utf-8"))

    def test_missing_local_model_fails_without_constructing_or_downloading(self):
        result, report = self.run_diagnostic(self.root / "missing model")
        self.assertEqual(result, 1)
        self.assertEqual(report["status"], "failed")
        self.assertIn("FileNotFoundError", report["error"])
        self.factory.assert_not_called()

    def test_incomplete_local_model_fails_before_native_initialization(self):
        (self.model_path / "model.bin").write_bytes(b"")
        result, report = self.run_diagnostic()
        self.assertEqual(result, 1)
        self.assertEqual(report["status"], "failed")
        self.assertIn("Incomplete local model: model.bin", report["error"])
        self.factory.assert_not_called()

    def test_model_load_stage_is_saved_before_calling_native_engine(self):
        def fail_to_load(*args, **kwargs):
            checkpoint = json.loads(self.report_path.read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["stage"], "model-load")
            self.assertFalse(checkpoint["ai_inference"])
            raise RuntimeError("native model load failed")

        self.factory.side_effect = fail_to_load
        result, report = self.run_diagnostic()
        self.assertEqual(result, 1)
        self.assertEqual(report["stage"], "model-load")
        self.assertIn("native model load failed", report["error"])
        self.model.transcribe.assert_not_called()

    def test_lazy_raw_inference_failure_does_not_report_success(self):
        def segments():
            yield object()
            raise RuntimeError("decoder failed while iterating")

        self.model.transcribe.return_value = (segments(), None)
        result, report = self.run_diagnostic()
        self.assertEqual(result, 1)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["stage"], "inference")
        self.assertFalse(report["ai_inference"])
        self.assertIn("decoder failed while iterating", report["error"])
        self.assertEqual(self.model.transcribe.call_count, 1)

    def test_lazy_vad_failure_does_not_report_raw_inference_as_success(self):
        def failing_vad():
            yield object()
            raise RuntimeError("VAD failed while iterating")

        self.model.transcribe.side_effect = [(iter([object()]), None),
                                            (failing_vad(), None)]
        result, report = self.run_diagnostic()
        self.assertEqual(result, 1)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["stage"], "voice-activity-detection")
        self.assertFalse(report["ai_inference"])
        self.assertIn("VAD failed while iterating", report["error"])

    def test_success_exhausts_raw_and_vad_generators_in_order(self):
        consumed = []

        def segments(kind):
            consumed.append(kind + ":started")
            yield object()
            consumed.append(kind + ":finished")

        def transcribe(audio, **options):
            self.assertIs(audio, self.audio)
            checkpoint = json.loads(self.report_path.read_text(encoding="utf-8"))
            self.assertFalse(checkpoint["ai_inference"])
            self.assertTrue(options["word_timestamps"])
            if options["vad_filter"]:
                self.assertEqual(consumed, ["raw:started", "raw:finished"])
                self.assertEqual(checkpoint["stage"], "voice-activity-detection")
                return segments("vad"), None
            self.assertEqual(checkpoint["stage"], "inference")
            return segments("raw"), None

        self.model.transcribe.side_effect = transcribe
        result, report = self.run_diagnostic()
        self.assertEqual(result, 0)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["stage"], "complete")
        self.assertTrue(report["ai_inference"])
        self.assertEqual(consumed, ["raw:started", "raw:finished",
                                    "vad:started", "vad:finished"])
        self.assertEqual(self.model.transcribe.call_count, 2)
        self.factory.assert_called_once_with(str(self.model_path.resolve()),
            device="cpu", compute_type="int8", local_files_only=True)


if __name__ == "__main__":
    unittest.main()
