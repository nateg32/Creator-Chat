"""Regression tests for transcript regurgitation in Creator Chat.

These tests cover the failure mode where a title-matching chunk or transcript
snippet bleeds too directly into the final answer. The goal is to keep the
runtime guardrails focused on conversational, creator-voiced answers instead of
point-by-point transcript recap, artifact leakage, or dead-end replies with no
follow-up.
"""

import importlib.util
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, relative_path: str):
    module_path = BACKEND_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


guard = _load_module("regurgitation_guard_tests", "services/regurgitation_guard.py")


def _title_matching_chunks():
    return [
        {
            "title": "How the Top 0.1% Invest Their Money",
            "content": (
                "Stage 1: Build a cash engine first, through a business, skill, or equity upside. "
                "Stage 2: Buy boring cash flowing assets that protect downside. "
                "Stage 3: Use tax efficiency and long duration compounding. "
                "Stage 4: Only then take asymmetric swings. "
                "0:02 [music] Most people skip straight to stage four and get smoked."
            ),
            "source_ref": {
                "title": "How the Top 0.1% Invest Their Money",
                "canonical_url": "https://www.youtube.com/watch?v=INVEST001",
            },
        },
        {
            "title": "The Real Order of Wealth Building",
            "content": (
                "Wealth is built in layers. First increase earning power, then protect capital, "
                "then let time do the heavy lifting. The order matters more than the asset class."
            ),
            "source_ref": {
                "title": "The Real Order of Wealth Building",
                "canonical_url": "https://www.youtube.com/watch?v=INVEST002",
            },
        },
        {
            "title": "Why Most Investors Start Too Late in the Stack",
            "content": (
                "If you have no cash engine, every investment decision feels emotional. "
                "The rich can buy volatility because the machine underneath keeps printing."
            ),
            "source_ref": {
                "title": "Why Most Investors Start Too Late in the Stack",
                "canonical_url": "https://www.youtube.com/watch?v=INVEST003",
            },
        },
    ]


class TranscriptRegurgitationTests(unittest.TestCase):
    def test_response_does_not_mirror_chunk_structure(self):
        top_chunk = _title_matching_chunks()[0]
        mirrored_response = (
            "Stage 1 is to build a cash engine first. "
            "Stage 2 is to buy boring cash flowing assets. "
            "Stage 3 is to use tax efficiency and long duration compounding. "
            "Stage 4 is to take asymmetric swings."
        )

        chunk_markers = guard.find_structure_markers(top_chunk["content"])
        response_markers = guard.find_structure_markers(mirrored_response)
        report = guard.check_for_regurgitation(mirrored_response, [top_chunk])

        self.assertGreaterEqual(len(chunk_markers), 4)
        self.assertEqual(len(response_markers), len(chunk_markers))
        self.assertTrue(report["mirrors_structure"])
        self.assertFalse(report["is_clean"])

    def test_response_word_count_vs_chunk(self):
        top_chunk = _title_matching_chunks()[0]
        too_long_response = (
            "Most people get this wrong because they skip the order completely. "
            "First you build the cash engine through a business, skill, or equity upside. "
            "Second you buy boring cash flowing assets that protect downside. "
            "Third you use tax efficiency and long duration compounding. "
            "Fourth you finally take the asymmetric swings once the base is already built."
        )

        report = guard.check_for_regurgitation(too_long_response, [top_chunk])

        self.assertGreater(report["word_ratio"], 0.5)
        self.assertFalse(report["is_clean"])

    def test_trigram_overlap_rate(self):
        chunks = _title_matching_chunks()
        conversational_response = (
            "Honestly, rich people usually earn before they invest. "
            "They build a machine that throws off cash, lock in safer assets, "
            "and only get aggressive after the base is already working. "
            "Where are you right now, still building income or already allocating capital?"
        )

        source_corpus = " ".join(chunk["content"] for chunk in chunks)
        overlap = guard.compute_trigram_overlap_rate(source_corpus, conversational_response)

        self.assertLess(overlap, 0.15)

    def test_no_transcript_artifacts_in_response(self):
        bad_responses = [
            "0:02 The answer is to build a cash engine first, then compound.",
            "Start with the boring stuff [music] and stop trying to get rich fast.",
            "Stage one, build a cash engine. Stage two, buy cash flowing assets.",
            "1: Build a cash engine first. 2: Buy assets after that.",
        ]

        reports = [guard.check_for_regurgitation(text, _title_matching_chunks()) for text in bad_responses]

        for report in reports:
            self.assertFalse(report["is_clean"])
            self.assertIn(
                report["reason"],
                {
                    "timestamp_artifact",
                    "transcript_tag",
                    "transcript_structure_marker",
                    "mirrors_structure",
                    "high_trigram_overlap",
                    "high_word_ratio",
                },
            )

    def test_response_ends_with_question_or_followup(self):
        responses = [
            "Most people want the asset before they have the cash engine. Build the machine first. What stage are you in right now?",
            "The move is to earn harder before you invest harder. Are you still trying to grow income, or are you already allocating cash every month?",
            "I would focus on cash flow first, then safety, then upside. What does your current setup actually look like?",
        ]

        for response in responses:
            self.assertTrue(guard.response_tail_has_question(response, tail_chars=300))


if __name__ == "__main__":
    unittest.main()
