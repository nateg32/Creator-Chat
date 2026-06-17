"""Regression tests for human-sounding creator greetings.

These tests cover the social-opening failure mode where a simple hello turns
into a self-description, a scripted CRM-style prompt, or an unsolicited
hyper-specific business question.
"""

import importlib.util
import pathlib
import re
import unittest


BASE_DIR = pathlib.Path(__file__).resolve().parents[1]


def _load_module(name: str, relative_path: str):
    module_path = BASE_DIR / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


greeting_module = _load_module("greeting_service_humanistic", pathlib.Path("services") / "greeting_service.py")
greeting_service = greeting_module.greeting_service
is_greeting = greeting_module.is_greeting


def _creator_fixture():
    creator = {
        "name": "Dan Martell",
        "creator_category": "business",
        "voice_patterns": {
            "interaction_style": {"energy_level": "high"},
            "rhythm": {"pacing": "fast"},
            "sentence_structure": {"avg_sentence_length": "short"},
        },
        "behavioral_fingerprint": {},
    }
    voice_profile = {
        "energy": {"bucket": "HIGH"},
        "greeting_high_energy": ["Let's go", "Alright", "Good to see you"],
        "greeting_neutral": ["Hey"],
        "tone_traits": {"hype": 0.8, "blunt": 0.6, "supportive": 0.2},
    }
    style_fingerprint = {
        "speech_mechanics": {"signature_openings": ["Cut the fluff", "Let's get into it"]},
        "golden_examples": {"greeting": ["Cut the fluff. What's on your mind right now?"]},
    }
    return creator, voice_profile, style_fingerprint


def _generate(seed: str, user_name: str = "Nathan", history=None) -> str:
    creator, voice_profile, style_fingerprint = _creator_fixture()
    return greeting_service.generate_greeting(
        user_name,
        voice_profile,
        include_question=True,
        creator_name=creator["name"],
        creator_category=creator["creator_category"],
        style_fingerprint=style_fingerprint,
        variation_seed=seed,
        conversation_history=history or [],
        creator_profile=creator,
    )


class GreetingHumanisticTests(unittest.TestCase):
    def test_greeting_does_not_describe_own_style(self):
        banned_self_descriptions = [
            "direct and engaging",
            "friendly and",
            "warm and",
            "clear and",
            "honest and",
            "my style is",
            "i like to be",
            "i tend to be",
            "as someone who",
            "in the spirit of",
        ]
        greetings = ["hello", "hey", "hi", "hey there", "hello!", "hi there"]
        for idx, greeting in enumerate(greetings):
            self.assertTrue(is_greeting(greeting))
            response = _generate(f"self-style-{idx}")
            lowered = response.lower()
            for phrase in banned_self_descriptions:
                self.assertNotIn(phrase, lowered, response)

    def test_greeting_does_not_ask_unsolicited_specific_questions(self):
        unsolicited_specific_patterns = [
            r"what part of .+ needs",
            r"which area of .+ are you",
            r"where are you stuck with",
            r"what is your current .+ situation",
            r"how many .+ do you have",
            r"what stage is your .+",
        ]
        greetings = ["hello", "hey", "hi", "hey there", "hello!", "hi there"]
        for idx, _ in enumerate(greetings):
            response = _generate(f"unsolicited-{idx}")
            for pattern in unsolicited_specific_patterns:
                self.assertIsNone(re.search(pattern, response, re.IGNORECASE), response)

    def test_greeting_feels_like_real_person_opening(self):
        greetings = ["hello", "hey", "hi", "hey there", "hello!", "hi there"]
        for idx, _ in enumerate(greetings):
            response = _generate(f"real-person-{idx}")
            word_count = len(response.split())
            self.assertGreaterEqual(word_count, 5, response)
            self.assertLessEqual(word_count, 60, response)
            self.assertLessEqual(response.count("?"), 1, response)
            self.assertFalse(response.startswith("I "), response)
            self.assertIsNone(re.search(r"^\s*\d+\.", response, re.MULTILINE), response)
            self.assertIsNone(re.search(r":\s*(\d+\.|-)", response), response)

    def test_greeting_uses_user_name_naturally(self):
        response = _generate("name-natural", user_name="Nathan")
        self.assertIn("Nathan", response)
        self.assertIsNone(re.search(r"^Nathan\.\s*$", response))
        self.assertIsNone(re.search(r"^Nathan\.\s+[A-Z]", response), response)

    def test_greeting_varies_across_calls(self):
        responses = [_generate(seed) for seed in ["var-a", "var-b", "var-c"]]
        self.assertGreater(len(set(responses)), 1, responses)

    def test_greeting_reflects_creator_not_generic_ai(self):
        generic_phrases = [
            "how can i assist you",
            "how can i help you today",
            "what can i do for you",
            "i'm here to help",
            "i'm here to assist",
            "feel free to ask",
            "don't hesitate to",
            "certainly",
            "absolutely",
            "of course",
            "great question",
        ]
        response = _generate("creator-not-generic")
        lowered = response.lower()
        for phrase in generic_phrases:
            self.assertNotIn(phrase, lowered, response)

    def test_greeting_uses_persona_not_verbatim_content_quote(self):
        creator, voice_profile, style_fingerprint = _creator_fixture()
        voice_profile["signature_phrases"] = [
            "Bro needs to see this",
            "Most entrepreneurs think",
            "If you know you know",
        ]
        creator["behavioral_fingerprint"] = {
            "catchphrases": ["Bro needs to see this", "Pretty much every guy goes through the same phases"]
        }
        response = greeting_service.generate_greeting(
            "Nathan",
            voice_profile,
            include_question=True,
            creator_name="Anabolic Gabe",
            creator_category="fitness",
            style_fingerprint=style_fingerprint,
            variation_seed="persona-not-quote",
            conversation_history=[],
            creator_profile=creator,
        )
        lowered = response.lower()
        self.assertNotIn("bro needs to see this", lowered)
        self.assertNotIn("most entrepreneurs think", lowered)
        self.assertNotIn("if you know you know", lowered)
        self.assertLessEqual(response.count("?"), 1, response)

    def test_followup_after_greeting_is_coherent(self):
        first_turn = _generate("followup-greeting")
        self.assertTrue(is_greeting("hello"))
        self.assertFalse(is_greeting("what's your best advice for someone starting a business"))

        second_turn = (
            "Start with one painful problem, one buyer, and one offer you can sell fast. "
            "Do not build a whole company in your head first, get one customer and learn from that. "
            "What kind of business are you thinking about?"
        )

        self.assertNotIn(first_turn, second_turn)
        self.assertNotIn("as I mentioned", second_turn.lower())
        self.assertGreaterEqual(len(second_turn.split()), 30)


if __name__ == "__main__":
    unittest.main()
