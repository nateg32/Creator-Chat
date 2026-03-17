import importlib.util
import unittest
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[1] / "services" / "text_sanitizer.py"
    spec = importlib.util.spec_from_file_location("text_sanitizer", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


text_sanitizer = _load_module()


class TextSanitizerTests(unittest.TestCase):
    def test_removes_compound_hyphens(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("Pick one high-income skill."),
            "Pick one high income skill.",
        )

    def test_replaces_clause_dashes_with_commas(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("Do the work - then raise your price."),
            "Do the work, then raise your price.",
        )

    def test_preserves_leading_bullets(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("- Keep going"),
            "- Keep going",
        )

    def test_replaces_tight_em_dash_clauses(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("If prompt engineering does not work in every case—as models can be unpredictable—you can post process it."),
            "If prompt engineering does not work in every case, as models can be unpredictable, you can post process it.",
        )

    def test_preserves_urls(self):
        text = "Use https://anti-gravity-bice.vercel.app or [this link](https://anti-gravity-bice.vercel.app) for approval."
        self.assertEqual(text_sanitizer.strip_mid_sentence_hyphens(text), text)

    def test_streaming_sanitizer_cleans_split_em_dash(self):
        sanitizer = text_sanitizer.StreamingTextSanitizer()
        parts = [
            sanitizer.feed("If prompt engineering does not work in every case"),
            sanitizer.feed("—as models can be unpredictable—you can "),
            sanitizer.feed("post process it."),
            sanitizer.flush(),
        ]
        self.assertEqual(
            "".join(parts),
            "If prompt engineering does not work in every case, as models can be unpredictable, you can post process it.",
        )

    def test_streaming_sanitizer_preserves_chunk_spaces(self):
        sanitizer = text_sanitizer.StreamingTextSanitizer(tail_size=12)
        parts = [
            sanitizer.feed("If you're thinking "),
            sanitizer.feed("about going to "),
            sanitizer.feed("ACCESS, go for the right reason."),
            sanitizer.flush(),
        ]
        self.assertEqual(
            "".join(parts),
            "If you're thinking about going to ACCESS, go for the right reason.",
        )

    def test_inserts_space_before_bible_verse_reference(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("It is built around Matthew28:19, which matters."),
            "It is built around Matthew 28:19, which matters.",
        )

    def test_inserts_space_before_bare_domain(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("1. Check2819Church.org for details."),
            "1. Check 2819Church.org for details.",
        )

    def test_inserts_space_between_word_and_number(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("Send50 messages a day. Call20 businesses a day. Walk in to5 places a day."),
            "Send 50 messages a day. Call 20 businesses a day. Walk in to 5 places a day.",
        )

    def test_inserts_space_before_year(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("We got married in2017."),
            "We got married in 2017.",
        )

    def test_inserts_space_before_frequency_suffix(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("Lift or do hard exercise3x a week."),
            "Lift or do hard exercise 3x a week.",
        )

    def test_inserts_space_before_age_suffix(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("Watch How to disappoint your dad in your20s."),
            "Watch How to disappoint your dad in your 20s.",
        )


if __name__ == "__main__":
    unittest.main()
