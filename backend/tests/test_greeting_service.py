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
    def test_greeting_is_deterministic_for_same_creator(self):
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
        )
        second = greeting_service.generate_greeting(
            "Nathan",
            voice_profile,
            creator_name="Alex",
            creator_category="business",
        )

        self.assertEqual(first, second)

    def test_distinct_creators_get_distinct_greetings(self):
        high_energy_voice = {
            "energy": {"bucket": "HIGH"},
            "greeting_high_energy": ["Lock in", "Let's move"],
            "greeting_questions": ["What are we building?", "What's the move right now?"],
            "signature_phrases": ["Lock in"],
            "tone_traits": {"hype": 0.9, "supportive": 0.1, "blunt": 0.5},
        }
        supportive_voice = {
            "energy": {"bucket": "LOW"},
            "greeting_short": ["Hey"],
            "greeting_questions": ["What's been hard lately?", "What do you need help with today?"],
            "signature_phrases": ["Take a breath"],
            "tone_traits": {"hype": 0.1, "supportive": 0.95, "blunt": 0.2},
        }

        first = greeting_service.generate_greeting(
            "Nathan",
            high_energy_voice,
            creator_name="Alex",
            creator_category="business",
        )
        second = greeting_service.generate_greeting(
            "Nathan",
            supportive_voice,
            creator_name="Sarah",
            creator_category="fitness",
        )

        self.assertNotEqual(first, second)
        self.assertIn("Nathan", first)
        self.assertIn("Nathan", second)

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
