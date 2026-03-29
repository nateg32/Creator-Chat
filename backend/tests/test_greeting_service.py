import importlib.util
import pathlib
import unittest


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_module(name: str, relative_path: str):
    module_path = BACKEND_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


greeting_module = _load_module("greeting_service_module", pathlib.Path("services") / "greeting_service.py")
greeting_service = greeting_module.greeting_service
is_greeting = greeting_module.is_greeting


def _fixture():
    creator = {
        "name": "Operator",
        "creator_category": "business",
        "voice_patterns": {
            "interaction_style": {"energy_level": "high"},
            "rhythm": {"pacing": "fast"},
            "sentence_structure": {"avg_sentence_length": "short"},
        },
    }
    voice_profile = {
        "energy": {"bucket": "HIGH"},
        "greeting_high_energy": ["Let's go", "Alright", "Good to see you"],
        "greeting_neutral": ["Hey"],
        "tone_traits": {"hype": 0.8, "supportive": 0.2, "blunt": 0.6},
    }
    style_fingerprint = {
        "speech_mechanics": {"signature_openings": ["Cut the fluff", "Let's get into it"]},
        "golden_examples": {"greeting": ["Cut the fluff. What's on your mind right now?"]},
    }
    return creator, voice_profile, style_fingerprint


class GreetingServiceTests(unittest.TestCase):
    def test_is_greeting_detects_social_openers(self):
        self.assertTrue(is_greeting("hello"))
        self.assertTrue(is_greeting("hey there"))
        self.assertTrue(is_greeting("yo"))
        self.assertFalse(is_greeting("what's your best advice for starting a business"))

    def test_greeting_is_stable_for_same_variation_seed(self):
        creator, voice_profile, style_fingerprint = _fixture()
        first = greeting_service.generate_greeting(
            "Nathan",
            voice_profile,
            creator_name=creator["name"],
            creator_category=creator["creator_category"],
            style_fingerprint=style_fingerprint,
            variation_seed="thread-1|seed-a",
            creator_profile=creator,
        )
        second = greeting_service.generate_greeting(
            "Nathan",
            voice_profile,
            creator_name=creator["name"],
            creator_category=creator["creator_category"],
            style_fingerprint=style_fingerprint,
            variation_seed="thread-1|seed-a",
            creator_profile=creator,
        )
        self.assertEqual(first, second)

    def test_greeting_varies_across_calls_for_same_creator(self):
        creator, voice_profile, style_fingerprint = _fixture()
        greetings = {
            greeting_service.generate_greeting(
                "Nathan",
                voice_profile,
                creator_name=creator["name"],
                creator_category=creator["creator_category"],
                style_fingerprint=style_fingerprint,
                creator_profile=creator,
            )
            for _ in range(6)
        }
        self.assertGreater(len(greetings), 1)

    def test_distinct_creators_get_distinct_greetings(self):
        creator, voice_profile, style_fingerprint = _fixture()
        first = greeting_service.generate_greeting(
            "Nathan",
            voice_profile,
            creator_name=creator["name"],
            creator_category=creator["creator_category"],
            style_fingerprint=style_fingerprint,
            variation_seed="creator-a",
            creator_profile=creator,
        )
        second = greeting_service.generate_greeting(
            "Nathan",
            {
                "energy": {"bucket": "LOW"},
                "greeting_short": ["Hey", "Good to hear from you"],
                "tone_traits": {"supportive": 0.9, "hype": 0.1, "blunt": 0.1},
            },
            creator_name="Sarah",
            creator_category="fitness",
            style_fingerprint={
                "speech_mechanics": {"signature_openings": ["Take a breath"]},
                "golden_examples": {"greeting": ["Take a breath. What's been on your mind lately?"]},
            },
            variation_seed="creator-b",
            creator_profile={
                "name": "Sarah",
                "creator_category": "fitness",
                "voice_patterns": {
                    "interaction_style": {"energy_level": "calm"},
                    "rhythm": {"pacing": "measured"},
                    "sentence_structure": {"avg_sentence_length": "medium"},
                },
            },
        )
        self.assertNotEqual(first, second)
        self.assertIn("Nathan", first)
        self.assertIn("Nathan", second)

    def test_greeting_avoids_hyper_specific_business_prompt(self):
        creator, voice_profile, style_fingerprint = _fixture()
        greeting = greeting_service.generate_greeting(
            "Nathan",
            voice_profile,
            creator_name=creator["name"],
            creator_category=creator["creator_category"],
            style_fingerprint=style_fingerprint,
            variation_seed="no-business-diagnosis",
            creator_profile=creator,
        )
        self.assertNotIn("what part of", greeting.lower())
        self.assertNotIn("needs tightening", greeting.lower())
        self.assertNotIn("bottleneck", greeting.lower())

    def test_unknown_name_question_stays_personal(self):
        creator, voice_profile, style_fingerprint = _fixture()
        greeting = greeting_service.generate_greeting(
            "",
            voice_profile,
            creator_name=creator["name"],
            creator_category=creator["creator_category"],
            style_fingerprint=style_fingerprint,
            variation_seed="unknown-name",
            creator_profile=creator,
        )
        self.assertTrue(
            greeting.endswith("What should I call you?")
            or greeting.endswith("What's your name?")
            or greeting.endswith("Who am I talking to?")
        )


if __name__ == "__main__":
    unittest.main()
