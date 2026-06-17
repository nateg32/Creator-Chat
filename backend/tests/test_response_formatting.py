"""Regression tests for response-formatting artifacts in Creator Chat.

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
prepare_chat_response = formatting.prepare_chat_response
should_strip_hyphens = formatting.should_strip_hyphens

text_sanitizer_spec = importlib.util.spec_from_file_location(
    "text_sanitizer_module",
    BACKEND_ROOT / "services" / "text_sanitizer.py",
)
text_sanitizer_module = importlib.util.module_from_spec(text_sanitizer_spec)
assert text_sanitizer_spec.loader is not None
text_sanitizer_spec.loader.exec_module(text_sanitizer_module)

rhythm_shaper_spec = importlib.util.spec_from_file_location(
    "rhythm_shaper_module",
    BACKEND_ROOT / "services" / "rhythm_shaper.py",
)
rhythm_shaper_module = importlib.util.module_from_spec(rhythm_shaper_spec)
assert rhythm_shaper_spec.loader is not None

# Patch sys.modules just for the rhythm_shaper exec, then restore. This avoids
# permanently clobbering the real backend / backend.services packages, which
# used to leak stubs into other test modules (e.g. test_security_hardening).
_rhythm_overrides = {
    "backend.services.formatting": formatting,
    "backend.services.text_sanitizer": text_sanitizer_module,
}
# Only fabricate package-level stubs if real ones aren't already loaded.
if "backend" not in sys.modules:
    _stub_backend = types.ModuleType("backend")
    _stub_backend.__path__ = [str(BACKEND_ROOT)]  # type: ignore[attr-defined]
    _rhythm_overrides["backend"] = _stub_backend
if "backend.services" not in sys.modules:
    _stub_services = types.ModuleType("backend.services")
    _stub_services.__path__ = [str(BACKEND_ROOT / "services")]  # type: ignore[attr-defined]
    _rhythm_overrides["backend.services"] = _stub_services

with patch.dict(sys.modules, _rhythm_overrides):
    rhythm_shaper_spec.loader.exec_module(rhythm_shaper_module)
RhythmShaper = rhythm_shaper_module.RhythmShaper


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
            "door": clean_response("Walk through your front door.", strip_hyphens=True),
            "some": clean_response("Move some weight.", strip_hyphens=True),
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
        self.assertEqual(clean_response("well-known"), "well-known")
        self.assertIn("front door", cases["door"])
        self.assertNotIn("do or", cases["door"])
        self.assertIn("some weight", cases["some"])
        self.assertNotIn("so me", cases["some"])

        self._assert_no_short_alpha_fragments(clean_response("You need a non-negotiable standard.", strip_hyphens=True))

    def test_should_strip_hyphens_reads_nested_rhythm_flag(self):
        self.assertFalse(should_strip_hyphens({}))
        self.assertFalse(should_strip_hyphens({"voice_patterns": {}}))
        self.assertTrue(
            should_strip_hyphens(
                {"voice_patterns": {"rhythm": {"strip_hyphens": True}}}
            )
        )
        self.assertTrue(
            should_strip_hyphens(
                {"voice_patterns": '{"rhythm": {"strip_hyphens": true}}'}
            )
        )

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

    def test_inline_citation_markers_and_censored_spacing_are_cleaned(self):
        cleaned = clean_response(
            "They overcomplicate the basics just to sell you junk [3]. Most guys getf*cked by noise and feelf*cked after."
        )
        self.assertNotIn("[3]", cleaned)
        self.assertIn("get f*cked", cleaned)
        self.assertIn("feel f*cked", cleaned)

        stream_chunk = clean_for_stream_chunk("Drop fat by keeping protein high [2]. Do not getf*cked by noise.")
        self.assertNotIn("[2]", stream_chunk)
        self.assertIn("get f*cked", stream_chunk)

    def test_source_domain_markers_are_not_visible_in_chat_prose(self):
        raw = (
            "When I was 17, I crashed into a house [thewantrepreneurshow.com]. "
            "That period became fuel (https://example.com/story). "
            "My firm is acquisition.com."
        )
        cleaned = clean_response(raw)
        self.assertNotIn("thewantrepreneurshow.com", cleaned)
        self.assertNotIn("https://example.com/story", cleaned)
        self.assertIn("My firm is acquisition.com.", cleaned)

    def test_low_trust_google_handoffs_are_removed(self):
        cleaned = clean_response(
            "She handles the people and scaling while I focus on acquisitions and math. (you can google it)"
        )
        self.assertEqual(
            cleaned,
            "She handles the people and scaling while I focus on acquisitions and math.",
        )

        stream_chunk = clean_for_stream_chunk("That is public. You can google it.")
        self.assertEqual(stream_chunk, "That is public.")

    def test_generic_censored_word_spacing_is_cleaned_without_flattening_voice(self):
        cleaned = clean_response(
            "Pretty much every guy goes through that phase, but you should not end up lookingf*cked. If you know you know."
        )
        self.assertIn("Pretty much every guy", cleaned)
        self.assertIn("looking f*cked", cleaned)
        self.assertNotIn("lookingf*cked", cleaned)
        self.assertNotIn("If you know you know", cleaned)

    def test_ai_identity_disclosures_are_removed_from_visible_response(self):
        cleaned = clean_response(
            "I appreciate that. Just so you know, I am an AI creator style assistant trained on Alex's content to help you solve problems. What are you building?"
        )
        self.assertIn("I appreciate that.", cleaned)
        self.assertIn("What are you building?", cleaned)
        self.assertNotIn("AI", cleaned)
        self.assertNotIn("assistant", cleaned.lower())
        self.assertNotIn("trained", cleaned.lower())

    def test_prepare_chat_response_does_not_repair_observed_split_words(self):
        cleaned = prepare_chat_response(
            "I ndividual company revenues with in that group range from 5 million to 100 million. So me of the specific entities include Skool. You can have so me KFC here and there, but your recovery can feelf*cked."
        )
        self.assertIn("I ndividual company revenues with in that group", cleaned)
        self.assertIn("So me of the specific entities", cleaned)
        self.assertIn("so me KFC", cleaned)
        self.assertIn("feel f*cked", cleaned)

    def test_prepare_chat_response_does_not_repair_split_brand_words(self):
        cleaned = prepare_chat_response(
            "You can find my official I nstagram and You Tube channels from there.",
            allow_model_cleanup=False,
        )
        self.assertIn("official I nstagram and You Tube channels", cleaned)

    def test_prepare_chat_response_does_not_repair_ironman_word_break(self):
        cleaned = prepare_chat_response(
            "That period became the operating system I use to build companies and finish I ronmans.",
            allow_model_cleanup=False,
        )
        self.assertIn("finish I ronmans.", cleaned)

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


    def test_prepare_chat_response_rewrites_raw_link_to_attached_card_reference(self):
        result = prepare_chat_response(
            "Go to acquisition.com and grab the details there.",
            cards=[{"url": "https://acquisition.com", "title": "Acquisition.com"}],
        )
        self.assertNotRegex(result, r"https?://")
        self.assertIn('"Acquisition.com"', result)
        self.assertIn('attached "Acquisition.com"', result)
        self.assertNotIn("link below", result.lower())

    def test_prepare_chat_response_turns_generic_video_attachment_into_specific_handoff(self):
        result = prepare_chat_response(
            "I started it because I wanted to solve bigger problems. I've attached the link below.",
            cards=[
                {
                    "url": "https://www.youtube.com/watch?v=abc12345678",
                    "title": "Alex Hormozi: What is Acquisition.com & Why I Started It - YouTube",
                }
            ],
        )

        self.assertIn("I wanted to solve bigger problems.", result)
        self.assertIn("I attached my breakdown of what Acquisition.com is and why I started it", result)
        self.assertNotIn("link below", result.lower())

    def test_prepare_chat_response_naturalizes_third_person_interview_title(self):
        result = prepare_chat_response(
            (
                "In the interview Alex Hormozi: This Is EXACTLY.. On Purpose with Jay Shetty, "
                "I break down the 4 step framework."
            ),
            cards=[
                {
                    "url": "https://podcasts.apple.com/example",
                    "title": "Alex Hormozi: This Is EXACTLY What I Would Do - On Purpose with Jay Shetty",
                }
            ],
        )

        self.assertIn("In the interview I did with Jay Shetty, I break down", result)
        self.assertNotIn("interview Alex Hormozi:", result)

    def test_prepare_chat_response_naturalizes_paid_day_resource_title(self):
        result = prepare_chat_response(
            "I Paid Alex Hormozi A Bunch Of Money To Spend A Day With Him shows the reality of finding the constraint.",
            cards=[
                {
                    "url": "https://podcasts.apple.com/example",
                    "title": "I Paid Alex Hormozi A Bunch Of Money To Spend A Day With Him",
                }
            ],
        )

        self.assertIn("The episode where someone paid to spend a day with me shows", result)
        self.assertNotIn("I Paid Alex Hormozi", result)

    def test_prepare_chat_response_turns_creator_result_title_into_first_person_handoff(self):
        result = prepare_chat_response(
            (
                "I broke down the whole timeline in an interview on My First Million. "
                'I attached the video "From Broke At 26 To $100 Million Net Worth By 31 '
                '(Alex Hormozi Interview)" if you want the deeper breakdown. '
                "where I talk about the journey from broke to 100 million below."
            ),
            cards=[
                {
                    "url": "https://www.youtube.com/watch?v=abc12345678",
                    "title": "From Broke At 26 To $100 Million Net Worth By 31 (Alex Hormozi Interview)",
                }
            ],
        )

        self.assertIn("Watch how I went from broke at 26 to $100 million net worth by 31", result)
        self.assertNotIn("Alex Hormozi Interview", result)
        self.assertNotIn("where I talk about", result)

    def test_prepare_chat_response_dedupes_episode_handoff_tail(self):
        result = prepare_chat_response(
            (
                "In that episode, I dive into why most people lose their drive once they stop being desperate. "
                "I attached the episode \"How to Keep Pushing Hard Years Later... | Ep 690\" if you want the longer version. "
                "\"How to Keep Pushing Hard Years Later... | Ep 690\" if you want the longer version. "
                "if you want to listen to the full breakdown. "
                "Are you feeling like you have lost some of that early day fire lately?"
            ),
            cards=[
                {
                    "url": "https://podcasts.apple.com/example",
                    "title": "How to Keep Pushing Hard Years Later... | Ep 690",
                }
            ],
        )

        self.assertIn("I attached Ep 690 if you want the longer version.", result)
        self.assertEqual(result.count("Ep 690"), 1)
        self.assertNotIn("How to Keep Pushing Hard Years Later", result)
        self.assertNotIn("if you want to listen", result.lower())
        self.assertNotIn("\n\n| Ep 690", result)

    def test_prepare_chat_response_naturalizes_creator_title_as_sentence_subject(self):
        result = prepare_chat_response(
            (
                "From Broke At 26 To $100 Million Net Worth By 31 "
                "(Alex Hormozi Interview) breaks down the timeline."
            ),
            cards=[
                {
                    "url": "https://mfmpod.com/example",
                    "title": "From Broke At 26 To $100 Million Net Worth By 31 (Alex Hormozi Interview)",
                }
            ],
        )

        self.assertIn("How I went from broke at 26 to $100 million net worth by 31 breaks down", result)
        self.assertNotIn("Alex Hormozi Interview", result)

    def test_prepare_chat_response_does_not_force_card_handoff_without_reference(self):
        result = prepare_chat_response(
            "I started it because the same playbooks could apply to any industry.",
            cards=[
                {
                    "url": "https://www.youtube.com/watch?v=abc12345678",
                    "title": "Alex Hormozi: What is Acquisition.com & Why I Started It - YouTube",
                }
            ],
        )

        self.assertNotIn("attached", result.lower())
        self.assertNotIn("link below", result.lower())

    def test_prepare_chat_response_breaks_long_prose_into_short_paragraphs(self):
        raw = (
            "It's four business playbooks that cover the core levers to scale: leads, sales, pricing, and retention. "
            "$100M Offers is how to make an offer so good people feel dumb saying no. "
            "$100M Leads is how to get customers predictably. "
            "$100M Sales is how to convert more of the leads you already have. "
            "$100M Money Models is how to structure the business so you keep the money, with pricing, margins, and compounding systems. "
            "Which one are you struggling with right now?"
        )
        result = prepare_chat_response(raw)
        self.assertIn("\n\n", result)
        paragraphs = [part for part in result.split("\n\n") if part.strip()]
        self.assertGreaterEqual(len(paragraphs), 2)
        self.assertTrue(paragraphs[-1].endswith("?"), result)

    def test_prepare_chat_response_normalizes_list_spacing(self):
        raw = "1)first thing\n2)second thing\n-keep going"
        result = prepare_chat_response(raw)
        self.assertIn("1) first thing", result)
        self.assertIn("2) second thing", result)
        self.assertIn("- keep going", result)

    def test_rhythm_shaper_keeps_all_sentences_when_chunking_long_reply(self):
        shaper = RhythmShaper()
        sentences = [
            "First point stays.",
            "Second point stays.",
            "Third point stays.",
            "Fourth point stays.",
            "Fifth point stays.",
            "Sixth point stays.",
            "Seventh point stays.",
        ]

        result = shaper._apply_dm_chunking(sentences, max_paragraphs=3)
        paragraphs = [part for part in result.split("\n\n") if part.strip()]

        self.assertLessEqual(len(paragraphs), 3)
        for sentence in sentences:
            self.assertIn(sentence, result)


if __name__ == "__main__":
    unittest.main()
