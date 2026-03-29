"""Exhaustive regression tests for response formatting artifacts.

These tests pin the known failure modes in the response-cleaning layer:
capital-word corruption, unsafe hyphen stripping, apostrophe damage, emoji
adjacency bugs, whitespace artifacts, and stream-assembly drift.
"""

import importlib.util
import re
import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _load_formatting_module():
    module_path = BACKEND_ROOT / "services" / "formatting.py"
    spec = importlib.util.spec_from_file_location("formatting_exhaustive_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["formatting_exhaustive_module"] = module
    spec.loader.exec_module(module)
    return module


formatting = _load_formatting_module()
clean_response = formatting.clean_response
clean_for_stream_chunk = formatting.clean_for_stream_chunk


def _find_stream_emit_boundary(text: str) -> int:
    matches = list(re.finditer(r"(?<=[.!?])\s+|\n", text))
    if matches:
        return matches[-1].end()
    if len(text) <= 24:
        return 0
    limit = len(text) - 24
    for index in range(limit, 0, -1):
        if text[index - 1].isspace():
            return index
    return 0


def _simulate_stream(raw_text: str):
    step = max(6, len(raw_text) // 4)
    raw_chunks = [raw_text[i:i + step] for i in range(0, len(raw_text), step)]
    pending = ""
    emitted = []
    for chunk in raw_chunks:
        safe = clean_for_stream_chunk(chunk)
        if not safe:
            continue
        pending += safe
        boundary = _find_stream_emit_boundary(pending)
        if boundary > 0:
            emitted.append(pending[:boundary])
            pending = pending[boundary:]
    if pending:
        emitted.append(pending)
    return raw_chunks, emitted, clean_response("".join(emitted))


def _fake_generate_response(query: str) -> str:
    fixtures = {
        "where can I buy your book": "You can buy it on Amazon and Audible today.",
        "what platforms are you on": "I'm on YouTube, Instagram, and LinkedIn.",
        "what tools do you recommend": "I use notion and slack every day.",
        "show me the Amazon version": "Check A\uFE0Fmazon for the listing.",
        "give me a direct answer": "Here's    the   thing.\n\n\n\nInvest   in yourself.",
    }
    raw = fixtures.get(query, "You can find it on Amazon.")
    return clean_response(raw)


class FormattingExhaustiveTests(unittest.TestCase):
    # Group A
    def test_a1_capital_words_not_split(self):
        inputs = [
            "Amazon",
            "Audible",
            "YouTube",
            "Instagram",
            "LinkedIn",
            "PayPal",
            "iPhone",
            "MacBook",
            "ChatGPT",
            "OpenAI",
            "Buy Back Your Time",
            "I've invested in Amazon",
            "Check it out on Amazon and Audible",
            "Available on Amazon.com",
        ]
        for value in inputs:
            result = clean_response(value)
            self.assertIn(value.split()[0], result, f"Input: {value!r} -> Output: {result!r}")

    def test_a2_hyphen_removal_does_not_split_words(self):
        cases = {
            "non-negotiable": {"non negotiable", "nonnegotiable"},
            "well-known": {"well known", "wellknown"},
            "buy-back": {"buy back", "buyback"},
            "self-made": {"self made", "selfmade"},
            "full-time": {"full time", "fulltime"},
        }
        for raw, expected in cases.items():
            result = clean_response(raw, strip_hyphens=True)
            self.assertIn(result, expected, f"Input: {raw!r} -> Output: {result!r}")
            self.assertNotRegex(result, r"[A-Za-z]\s+[A-Za-z]{1,2}\s+[A-Za-z]", result)

    def test_a3_apostrophes_preserved(self):
        inputs = ["don't", "it's", "can't", "I've", "you're", "we're", "I'm", "that's", "here's", "there's"]
        for value in inputs:
            self.assertEqual(clean_response(value), value)

    def test_a4_emoji_removal_does_not_consume_adjacent_chars(self):
        cases = [
            ("\U0001f525 you need to invest", "you need to invest"),
            ("invest in yourself \U0001f525", "invest in yourself"),
            ("Here's the thing \U0001f525 invest", "invest"),
            ("\U0001f4a1Amazon is where", "Amazon is where"),
            ("Step 1\ufe0f\u20e3 build", "build"),
            ("\U0001f1e6\U0001f1fa Australia", "Australia"),
            ("check \u2705 this out", "check"),
            ("growth \U0001f4c8 mindset", "mindset"),
            ("\U0001f4b0 money mindset", "money mindset"),
            ("A\ufe0flist item", "list item"),
            ("A\ufe0fmazon is where I sell my book", "Amazon"),
        ]
        for raw, must_contain in cases:
            result = clean_response(raw)
            self.assertIn(must_contain, result, f"Input: {raw.encode('unicode_escape').decode()} -> Output: {result.encode('unicode_escape').decode()}")

    def test_a5_whitespace_collapse_is_safe(self):
        raw = "Here's    the   thing.\n\n\n\nInvest   in yourself."
        result = clean_response(raw)
        self.assertNotIn("  ", result)
        self.assertNotIn("\n\n\n", result)
        for line in result.splitlines():
            self.assertFalse(line.startswith(" "), line)
            self.assertFalse(line.endswith(" "), line)

    def test_a6_numbers_and_special_not_corrupted(self):
        cases = [
            "10x return",
            "1,600 books",
            "$30,000",
            "50%",
            "stage 1",
            "1) first thing",
            "the S&P 500",
            "ROI",
            "B2B SaaS",
        ]
        for value in cases:
            self.assertEqual(clean_response(value), value)

    def test_a7_cleaning_is_idempotent(self):
        inputs = [
            "Amazon",
            "Check it out on Amazon and Audible",
            "non-negotiable",
            "Here's the thing \U0001f525 you need to invest",
            "A\ufe0fmazon is where I sell my book",
            "1,600 books on Amazon.com",
        ]
        for value in inputs:
            once = clean_response(value, strip_hyphens=True)
            twice = clean_response(once, strip_hyphens=True)
            self.assertEqual(once, twice, f"Cleaner not idempotent on {value!r}")

    # Group B
    def test_b1_real_response_capital_words_intact(self):
        queries_and_watch_words = [
            ("where can I buy your book", ["Amazon", "Audible"]),
            ("what platforms are you on", ["YouTube", "Instagram", "LinkedIn"]),
            ("what tools do you recommend", ["notion", "slack"]),
        ]
        for query, words in queries_and_watch_words:
            response = _fake_generate_response(query)
            for word in words:
                self.assertIn(word, response, f"Query: {query!r} -> Response: {response!r}")
            self.assertNotIn("A mazon", response, response)
            self.assertNotIn("A udible", response, response)

    def test_b2_real_response_no_double_spaces(self):
        queries = [
            "where can I buy your book",
            "what platforms are you on",
            "what tools do you recommend",
            "give me a direct answer",
            "show me the Amazon version",
        ]
        for query in queries:
            response = _fake_generate_response(query)
            self.assertNotIn("  ", response, f"Query: {query!r} -> Response: {response!r}")

    def test_b3_real_response_no_orphaned_punctuation(self):
        queries = [
            "where can I buy your book",
            "what platforms are you on",
            "what tools do you recommend",
            "give me a direct answer",
            "show me the Amazon version",
        ]
        bad_patterns = [
            r"\.\s*,",
            r",\s*\.",
            r"\(\s*\)",
            r"\[\s*\]",
            r"[-–—]\s*[-–—]",
            r"\s+[,\.!?]",
        ]
        for query in queries:
            response = _fake_generate_response(query)
            for pattern in bad_patterns:
                self.assertIsNone(re.search(pattern, response), f"Query: {query!r} Pattern: {pattern!r} Response: {response!r}")

    # Group C
    def test_c1_stream_chunks_not_cleaned_mid_stream(self):
        raw = "Check A\uFE0Fmazon and Audible for the listing today."
        raw_chunks, emitted, joined = _simulate_stream(raw)
        self.assertTrue(emitted)
        for index, chunk in enumerate(emitted):
            self.assertNotEqual(chunk, "", f"Empty chunk at index {index}")
        for prev, curr in zip(emitted, emitted[1:]):
            if prev and curr and prev[-1].isalpha():
                self.assertFalse(curr.startswith(" "), f"Potential mid-word break: {prev!r} | {curr!r}")

    def test_c2_assembled_stream_equals_clean_response(self):
        raw = "Check A\uFE0Fmazon and Audible for the listing today."
        _, _, joined = _simulate_stream(raw)
        non_stream = clean_response(raw)
        self.assertEqual(
            re.sub(r"\s+", " ", joined).strip(),
            re.sub(r"\s+", " ", non_stream).strip(),
        )
        self.assertIn("Amazon", joined, joined)


if __name__ == "__main__":
    unittest.main()
