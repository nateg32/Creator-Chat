import unittest
import importlib.util
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, relative_path: str):
    module_path = BACKEND_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


greeting_service = _load_module("greeting_service", "services/greeting_service.py").greeting_service


class GreetingServiceTests(unittest.TestCase):
    def test_greeting_is_stable_for_same_variation_seed(self):
        voice_profile = {
            "energy": {"bucket": "HIGH"},
            "greeting_high_energy": ["Let's move", "Lock in"],
            "greeting_questions": ["What are we building?", "What's the move right now?"],
            "signature_phrases": ["Lock in"],
            "tone_traits": {"hype": 0.9, "supportive": 0.2, "blunt": 0.4},
        }

        first = greeting_service.generate_greeting(
            "Nathan",
            voice_profile,
            creator_name="Alex",
            creator_category="business",
            variation_seed="thread-1|seed-a",
        )
        second = greeting_service.generate_greeting(
            "Nathan",
            voice_profile,
            creator_name="Alex",
            creator_category="business",
            variation_seed="thread-1|seed-a",
        )

        self.assertEqual(first, second)

    def test_greeting_varies_across_calls_for_same_creator(self):
        voice_profile = {
            "energy": {"bucket": "HIGH"},
            "greeting_high_energy": ["Let's move", "Lock in"],
            "greeting_questions": ["What are we building?", "What's the move right now?"],
            "signature_phrases": ["Lock in"],
            "tone_traits": {"hype": 0.9, "supportive": 0.2, "blunt": 0.4},
        }
        style_fingerprint = {
            "speech_mechanics": {"signature_openings": ["Cut the fluff", "Alright, let's go"]},
            "domain_map": {"strong_topics": ["offers", "pricing", "sales process"]},
            "golden_examples": {"greeting": ["Cut the fluff. Where is the offer leaking right now?"]},
        }

        greetings = {
            greeting_service.generate_greeting(
                "Nathan",
                voice_profile,
                creator_name="Alex",
                creator_category="business",
                style_fingerprint=style_fingerprint,
            )
            for _ in range(6)
        }

        self.assertGreater(len(greetings), 1)

    def test_distinct_creators_get_distinct_greetings(self):
        shared_voice = {
            "energy": {"bucket": "HIGH"},
            "greeting_high_energy": ["Let's move", "Lock in"],
            "greeting_questions": ["What are we building?", "What's the move right now?"],
            "signature_phrases": ["Lock in"],
            "tone_traits": {"hype": 0.9, "supportive": 0.1, "blunt": 0.5},
        }
        first_style = {
            "speech_mechanics": {"signature_openings": ["Cut the fluff"]},
            "domain_map": {"strong_topics": ["offers", "B2B outbound"]},
            "golden_examples": {"greeting": ["Cut the fluff. Where is the offer leaking right now?"]},
        }
        second_style = {
            "speech_mechanics": {"signature_openings": ["Take a breath"]},
            "domain_map": {"strong_topics": ["recovery", "training blocks"]},
            "golden_examples": {"greeting": ["Take a breath. What part of training feels off right now?"]},
        }

        first = greeting_service.generate_greeting(
            "Nathan",
            shared_voice,
            creator_name="Alex",
            creator_category="business",
            style_fingerprint=first_style,
        )
        second = greeting_service.generate_greeting(
            "Nathan",
            shared_voice,
            creator_name="Sarah",
            creator_category="fitness",
            style_fingerprint=second_style,
        )

        self.assertNotEqual(first, second)
        self.assertIn("Nathan", first)
        self.assertIn("Nathan", second)

    def test_style_fingerprint_replaces_generic_build_prompt(self):
        voice_profile = {
            "energy": {"bucket": "HIGH"},
            "greeting_high_energy": ["Let's move"],
            "greeting_questions": ["What are you building right now?"],
            "signature_phrases": ["Lock in"],
            "tone_traits": {"hype": 0.9, "supportive": 0.1, "blunt": 0.7},
        }
        style_fingerprint = {
            "domain_map": {"strong_topics": ["offers", "outbound systems"]},
            "speech_mechanics": {"signature_openings": ["Cut the fluff"]},
            "golden_examples": {"greeting": ["Cut the fluff. Where is the offer leaking right now?"]},
            "anti_persona": {"forbidden_generic_coach_lines": ["What are you building right now?"]},
            "lexical_rules": {"banned_frames": ["What are you building right now?"]},
        }

        greeting = greeting_service.generate_greeting(
            "Nathan",
            voice_profile,
            creator_name="Operator",
            creator_category="business",
            style_fingerprint=style_fingerprint,
        )

        self.assertNotIn("What are you building right now?", greeting)
        self.assertTrue("offer" in greeting.lower() or "outbound" in greeting.lower())

    def test_unknown_name_question_stays_personal(self):
        voice_profile = {
            "energy": {"bucket": "LOW"},
            "tone_traits": {"supportive": 0.9},
        }

        greeting = greeting_service.generate_greeting(
            "",
            voice_profile,
            creator_name="Sarah",
            creator_category="fitness",
        )

        self.assertTrue(
            greeting.endswith("What should I call you?") or greeting.endswith("What's your name?") or greeting.endswith("Who am I talking to?")
        )


if __name__ == "__main__":
    unittest.main()
