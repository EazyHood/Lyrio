"""Release regression: WinRT must not shadow the newer C++ runtime."""
from pathlib import Path
import unittest
from unittest.mock import patch

from packaging_runtime import normalize_runtime_entries


class RuntimePackagingTests(unittest.TestCase):
    def setUp(self):
        self.old = (14, 29, 30157, 0)
        self.new = (14, 51, 36247, 0)
        self.entries = [
            ("winrt\\MSVCP140.dll", "old-msvcp.dll", "BINARY"),
            ("msvcp140.dll", "new-msvcp.dll", "BINARY"),
            ("VCRUNTIME140.dll", "old-vcruntime.dll", "BINARY"),
            ("rapidfuzz.libs/msvcp140-unique.dll", "private-runtime.dll", "BINARY"),
            ("ctranslate2/ctranslate2.dll", "ctranslate2.dll", "BINARY"),
        ]

    def metadata(self, path):
        return 0x8664, self.old if str(path).startswith("old-") else self.new

    def normalize(self, metadata=None):
        with patch("packaging_runtime.inspect_runtime", side_effect=metadata or self.metadata):
            return normalize_runtime_entries(self.entries, Path("runtime"))

    def test_replaces_shadowing_copy_and_root_copy_with_same_dll(self):
        result = self.normalize()
        self.assertEqual(result[0][1], result[1][1])
        self.assertEqual(result[0][1], str(Path("runtime") / "msvcp140.dll"))
        self.assertEqual(result[0][0], "winrt\\MSVCP140.dll")
        self.assertEqual(result[2][1], str(Path("runtime") / "vcruntime140.dll"))
        self.assertEqual(result[3:], self.entries[3:])

    def test_rejects_old_runtime_even_when_all_sources_are_old(self):
        with self.assertRaisesRegex(RuntimeError, "14.40 or newer"):
            self.normalize(lambda path: (0x8664, self.old))

    def test_rejects_32_bit_runtime(self):
        with self.assertRaisesRegex(RuntimeError, "must be x64"):
            self.normalize(lambda path: (0x014c, self.new))

    def test_rejects_downgrading_any_collected_copy(self):
        def newer_source(path):
            return 0x8664, (14, 52, 1, 0) if str(path) == "new-msvcp.dll" else self.new
        with self.assertRaisesRegex(RuntimeError, "Refusing to downgrade"):
            self.normalize(newer_source)

    def test_rejects_mixed_runtime_set(self):
        def mixed(path):
            if str(path) == str(Path("runtime") / "vcruntime140.dll"):
                return 0x8664, (14, 50, 0, 0)
            return self.metadata(path)
        with self.assertRaisesRegex(RuntimeError, "one release"):
            self.normalize(mixed)

    def test_missing_replacement_fails_the_build(self):
        with self.assertRaisesRegex(RuntimeError, "Cannot package"):
            self.normalize(lambda path: (_ for _ in ()).throw(FileNotFoundError(path)))

    def test_explicit_runtime_directory_environment(self):
        with patch.dict("os.environ", {"LYRIO_MSVC_RUNTIME_DIR": "pinned-runtime"}), \
                patch("packaging_runtime.inspect_runtime", side_effect=self.metadata):
            result = normalize_runtime_entries(self.entries)
        self.assertEqual(result[0][1], str(Path("pinned-runtime") / "msvcp140.dll"))


if __name__ == "__main__":
    unittest.main()
