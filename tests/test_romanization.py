# -*- coding: utf-8 -*-
"""Offline display regressions, exercised with the real language dictionaries."""

import importlib.util
import unittest
from unittest.mock import patch

import translate
from display_lyrics import prepare_display_lyrics
from lyrics import Lyrics


HAS_JAPANESE = importlib.util.find_spec("pykakasi") is not None
HAS_CHINESE = importlib.util.find_spec("pypinyin") is not None
HAS_KOREAN = importlib.util.find_spec("korean_romanizer") is not None


class LanguageContextTests(unittest.TestCase):
    def test_kana_anywhere_establishes_context_for_kanji_lines(self):
        self.assertEqual(translate.infer_romanization_language(
            ["世界", "Love", "こんにちは"]), "ja")

    def test_han_alone_stays_chinese_without_explicit_override(self):
        self.assertEqual(translate.infer_romanization_language(["世界"]), "zh")
        self.assertEqual(translate.infer_romanization_language(["世界"], "ja"),
                         "ja")

    def test_english_and_empty_lyrics_have_no_script_context(self):
        self.assertIsNone(translate.infer_romanization_language(["Hello", ""]))
        self.assertIsNone(translate.infer_romanization_language([]))
        self.assertEqual(translate.romanize("Hello WORLD!", "ja"), "")

    def test_predominantly_korean_song_remains_korean(self):
        self.assertEqual(translate.infer_romanization_language(
            ["안녕하세요", "ありがとう", "한국어"]), "ko")


@unittest.skipUnless(HAS_JAPANESE, "pykakasi dictionary required")
class JapaneseDisplayTests(unittest.TestCase):
    def test_kanji_only_row_uses_context_from_later_row(self):
        source = Lyrics([(1.25, "世界"), (5.5, "こんにちは")],
                        "user", True, author="Singer")
        display = prepare_display_lyrics(source)
        self.assertEqual(display.lines[0], (1.25, "sekai"))
        self.assertFalse(translate.needs_romanization(display.lines[1][1]))
        self.assertEqual(display.author, "Singer")
        self.assertEqual(display.source, "user")
        self.assertTrue(display.synced)
        self.assertEqual(source.lines[0], (1.25, "世界"))

    def test_override_handles_entire_song_without_kana(self):
        source = Lyrics([(1.0, "世界")], "lrclib", True)
        self.assertEqual(prepare_display_lyrics(source, language="ja").lines,
                         [(1.0, "sekai")])

    def test_latin_case_spacing_and_punctuation_survive_mixed_line(self):
        text = "こんにちは Hello  WORLD! Don't stop. 未来へ"
        display = translate.romanize(text, "ja")
        self.assertIn(" Hello  WORLD! Don't stop. ", display)
        self.assertFalse(translate.needs_romanization(display))
        self.assertEqual(translate.romanize("Love世界Forever", "ja"),
                         "Love sekai Forever")

    def test_halfwidth_kana_is_recognized_and_converted(self):
        self.assertEqual(translate.infer_romanization_language(["ｺﾝﾆﾁﾊ"]), "ja")
        self.assertFalse(translate.needs_romanization(
            translate.romanize("ｺﾝﾆﾁﾊ")))

    def test_changed_rows_drop_unsafe_offsets_but_keep_unmodified_rows(self):
        source = Lyrics(
            [(2.0, "世界"), (8.0, "Hello"), (12.0, "こんにちは")],
            "whisper", True,
            words={0: [(2.0, 5.0, 0, 2)], 1: [(8.0, 9.0, 0, 5)],
                   2: [(12.0, 5)]})
        display = prepare_display_lyrics(source)
        self.assertEqual(display.words, {1: [(8.0, 9.0, 0, 5)]})
        self.assertEqual(display.romanized_lines, frozenset({0, 2}))
        self.assertEqual(len(source.words), 3)
        display.words[1].clear()
        self.assertEqual(source.words[1], [(8.0, 9.0, 0, 5)])

    def test_toggle_restores_original_lines_and_offsets(self):
        source = Lyrics([(2.0, "世界")], "user", True,
                        words={0: [(2.0, 5.0, 0, 2)]})
        self.assertIs(prepare_display_lyrics(source, enabled=False), source)
        self.assertEqual(prepare_display_lyrics(source, language="ja").lines,
                         [(2.0, "sekai")])
        self.assertIs(prepare_display_lyrics(source, enabled=False), source)

    def test_missing_dictionary_does_not_blank_or_mutate_line(self):
        source = Lyrics([(1.0, "世界")], "user", True)
        with patch.object(translate, "_kakasi") as converter:
            converter.convert.side_effect = FileNotFoundError("dictionary")
            self.assertEqual(translate.romanize("世界", "ja"), "")
            self.assertIs(prepare_display_lyrics(source, language="ja"), source)


class OtherScriptsTests(unittest.TestCase):
    def test_latin_source_is_identity_and_empty_source_is_supported(self):
        source = Lyrics([(0.0, "Don't change ME!")], "user", True)
        self.assertIs(prepare_display_lyrics(source), source)
        self.assertIsNone(prepare_display_lyrics(None))
        empty = Lyrics([], "user", True)
        self.assertIs(prepare_display_lyrics(empty), empty)

    @unittest.skipUnless(HAS_CHINESE, "pypinyin required")
    def test_chinese_remains_pinyin(self):
        source = Lyrics([(1.0, "你好世界"), (5.0, "LOVE")], "lrclib", True)
        self.assertEqual(prepare_display_lyrics(source).lines,
                         [(1.0, "ni hao shi jie"), (5.0, "LOVE")])

    @unittest.skipUnless(HAS_KOREAN, "korean_romanizer required")
    def test_korean_and_latin_remain_complete(self):
        self.assertEqual(translate.romanize("안녕하세요 My LOVE!"),
                         "annyeonghaseyo My LOVE!")

    @unittest.skipUnless(HAS_JAPANESE and HAS_KOREAN,
                         "Japanese and Korean dictionaries required")
    def test_mixed_scripts_do_not_drop_hangul(self):
        display = translate.romanize("世界안녕하세요", "ja")
        self.assertEqual(display, "sekai annyeonghaseyo")


if __name__ == "__main__":
    unittest.main()
