"""Regression tests for response-formatting artifacts in Creator Bot.

These tests cover the response-cleaning layer that runs after generation and
the lightweight chunk cleaning used during streaming. The goal is to catch the
specific corruption modes reported in chat: split words, double whitespace,
orphaned punctuation, transcript artifacts, and emoji/hyphen cleanup damage.
"""

import importlib.util
import re
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
KNOWN_SHORT_WORDS = {
    "a", "am", "an", "as", "at", "be", "by", "do", "go", "he", "hi", "i",
    "if", "in", "is", "it", "me", "my", "no", "of", "ok", "on", "or", "so",
    "to", "up", "us", "we",
}


def _load_formatting_module():
    module_path = BACKEND_ROOT / "services" / "formatting.py"
    spec = importlib.util.spec_from_file_location("response_formatting_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    fake_emoji = types.ModuleType("emoji")
    emoji_pattern = re.compile(
        "["
        "\U0001F300-\U0001FAFF"
        "\U00002600-\U000027BF"
        "\U0000FE0F"
        "\U0000200D"
        "\U000020E3"
        "]+",
        flags=re.UNICODE,
    )
    fake_emoji.replace_emoji = lambda text, replace="": emoji_pattern.sub(replace, text)

    with patch.dict(sys.modules, {"emoji": fake_emoji}):
        spec.loader.exec_module(module)

    return module


formatting = _load_formatting_module()
clean_response = formatting.clean_response
clean_for_stream_chunk = formatting.clean_for_stream_chunk


def _sample_pipeline_responses():
    return [
        "Build the machine first.  Then allocate capital.\n Start lean.",
        "The answer is simple . , fix the offer first.",
        "0:02 [music] Start with a single buyer and a single promise.",
        "Here's the thing 🔥 you need to keep the structure tight.",
    ]


def _simulate_stream_output(chunks):
    pending = ""
    emitted = []
    for chunk in chunks:
        safe_chunk = clean_for_stream_chunk(chunk)
        if not safe_chunk:
            continue
        pending += safe_chunk

        matches = list(re.finditer(r"(?<=[.!?])\s+|\n", pending))
        emit_boundary = matches[-1].end() if matches else 0
        if not emit_boundary and len(pending) > 24:
            limit = len(pending) - 24
            for index in range(limit, 0, -1):
                if pending[index - 1].isspace():
                    emit_boundary = index
                    break

        if emit_boundary > 0:
            emitted.append(pending[:emit_boundary])
            pending = pending[emit_boundary:]

    if pending:
        emitted.append(pending)
    return emitted


class ResponseFormattingTests(unittest.TestCase):
    def _assert_no_short_alpha_fragments(self, text: str):
        for token in text.split():
            letters_only = "".join(ch for ch in token if ch.isalpha())
            lowered = letters_only.lower()
            if lowered and len(lowered) < 2 and lowered not in KNOWN_SHORT_WORDS:
                self.fail(f"Found orphaned short fragment '{token}' in '{text}'")

    def _assert_no_whitespace_artifacts(self, text: str):
        self.assertNotIn("  ", text)
        for line in text.splitlines():
            self.assertFalse(line.startswith(" "), f"Line starts with space: {line!r}")
            self.assertFalse(line.endswith(" "), f"Line ends with space: {line!r}")

    def _assert_no_punctuation_artifacts(self, text: str):
        self.assertIsNone(re.search(r"\.\s*,", text), text)
        self.assertIsNone(re.search(r",\s*\.", text), text)
        self.assertIsNone(re.search(r"\(\s*\)", text), text)
        self.assertIsNone(re.search(r"\[\s*\]", text), text)
        self.assertIsNone(re.search(r"[-–—]\s*[-–—]", text), text)
        self.assertIsNone(re.search(r"\s+[,\.\!\?]", text), text)

    def _assert_no_transcript_artifacts(self, text: str):
        self.assertIsNone(re.search(r"\b\d{1,2}:\d{2}\b", text), text)
        self.assertNotIn("[music]", text.lower())
        self.assertNotIn("[applause]", text.lower())
        self.assertIsNone(re.search(r"\[[\w\s]{2,20}\]", text), text)
        self.assertNotIn("Stage one,", text)
        self.assertNotIn("Stage two,", text)
        self.assertNotIn("Stage three,", text)
        self.assertNotIn("Stage four,", text)

    def test_clean_response_does_not_split_words(self):
        cases = {
            "non-negotiable": clean_response("non-negotiable", strip_hyphens=True),
            "well-known": clean_response("well-known", strip_hyphens=True),
            "buy-back": clean_response("buy-back", strip_hyphens=True),
            "don't": clean_response("don't", strip_hyphens=True),
            "it's": clean_response("it's", strip_hyphens=True),
            "can't": clean_response("can't", strip_hyphens=True),
            "I've": clean_response("I've", strip_hyphens=True),
        }

        self.assertIn(cases["non-negotiable"], {"non negotiable", "nonnegotiable"})
        self.assertNotIn("neg otiable", cases["non-negotiable"])
        self.assertNotIn("n on", cases["non-negotiable"])

        self.assertIn(cases["well-known"], {"well known", "wellknown"})
        self.assertNotIn("w ell", cases["well-known"])

        self.assertIn(cases["buy-back"], {"buy back", "buyback"})
        self.assertNotIn("bu y", cases["buy-back"])

        self.assertEqual(cases["don't"], "don't")
        self.assertEqual(cases["it's"], "it's")
        self.assertEqual(cases["can't"], "can't")
        self.assertEqual(cases["I've"], "I've")

        self._assert_no_short_alpha_fragments(clean_response("You need a non-negotiable standard.", strip_hyphens=True))

    def test_no_double_whitespace_in_response(self):
        for raw in _sample_pipeline_responses():
            cleaned = clean_response(raw)
            self._assert_no_whitespace_artifacts(cleaned)

    def test_no_orphaned_punctuation(self):
        for raw in [
            "This is broken . , and awkward.",
            "This is broken , . and awkward.",
            "Empty ( ) brackets and [ ] markers.",
            "Double dash - - issue and space before !",
        ]:
            cleaned = clean_response(raw)
            self._assert_no_punctuation_artifacts(cleaned)

    def test_no_transcript_artifacts_in_response(self):
        for raw in [
            "0:02 [music] Start with the first principle.",
            "The answer is [applause] focus on the cash engine.",
            "Stage one, build the engine. Stage two, allocate the capital.",
            "1:45 [laughter] Keep it simple.",
        ]:
            cleaned = clean_response(raw)
            self._assert_no_transcript_artifacts(cleaned)

    def test_streaming_does_not_break_words(self):
        raw_chunks = [
            "Here is the real non-neg",
            " otiable truth about building wealth. ",
            "You need a clean process, not [music] noise. ",
            "0:02 Start with one market and one offer.",
        ]

        streamed_chunks = _simulate_stream_output(raw_chunks)

        for left, right in zip(streamed_chunks, streamed_chunks[1:]):
            bad_boundary = bool(re.search(r"[A-Za-z-]$", left) and right.startswith(" "))
            self.assertFalse(
                bad_boundary,
                msg=f"Chunk boundary appears to split a word: {left!r} | {right!r}",
            )

        joined = clean_response("".join(streamed_chunks), strip_hyphens=True)
        self.assertGreaterEqual(len(joined.split()), 20)
        self.assertTrue(joined.strip())
        self._assert_no_short_alpha_fragments(joined)
        self._assert_no_whitespace_artifacts(joined)

    def test_emoji_removal_does_not_corrupt_adjacent_text(self):
        first = clean_response("Here's the thing 🔥 you need to invest in yourself")
        self.assertIn(first, {
            "Here's the thing you need to invest in yourself",
            "Here's the thing  you need to invest in yourself",
        })
        self.assertIn("thing you", first)

        second = clean_response("Step 1️⃣ build your foundation")
        self.assertIn("build your foundation", second)

        third = clean_response("💡Great insight from the book")
        self.assertTrue(third.startswith("Great insight"))


if __name__ == "__main__":
    unittest.main()
