"""Variation tests for creator greeting generation.

These tests check that greetings vary for the same creator, differ across
creators, and change structure rather than only swapping a few synonyms.
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


greeting_module = _load_module("greeting_service_variation", pathlib.Path("services") / "greeting_service.py")
greeting_service = greeting_module.greeting_service


def _creator_high_energy():
    creator = {
        "name": "Dan Martell",
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
        "tone_traits": {"hype": 0.9, "blunt": 0.6, "supportive": 0.1},
    }
    style_fingerprint = {
        "speech_mechanics": {"signature_openings": ["Cut the fluff", "Let's get into it"]},
        "golden_examples": {"greeting": ["Cut the fluff. What's on your mind right now?"]},
    }
    return creator, voice_profile, style_fingerprint


def _creator_calm():
    creator = {
        "name": "Sarah Jones",
        "creator_category": "fitness",
        "voice_patterns": {
            "interaction_style": {"energy_level": "calm"},
            "rhythm": {"pacing": "measured"},
            "sentence_structure": {"avg_sentence_length": "medium"},
        },
    }
    voice_profile = {
        "energy": {"bucket": "LOW"},
        "greeting_short": ["Hey", "Good to hear from you"],
        "greeting_neutral": ["Hey there"],
        "tone_traits": {"hype": 0.1, "blunt": 0.2, "supportive": 0.9},
    }
    style_fingerprint = {
        "speech_mechanics": {"signature_openings": ["Take a breath", "Good to have you here"]},
        "golden_examples": {"greeting": ["Take a breath. What's been on your mind lately?"]},
    }
    return creator, voice_profile, style_fingerprint


def _generate(bundle, seed: str, history=None):
    creator, voice_profile, style_fingerprint = bundle
    return greeting_service.generate_greeting(
        "Nathan",
        voice_profile,
        include_question=True,
        creator_name=creator["name"],
        creator_category=creator["creator_category"],
        style_fingerprint=style_fingerprint,
        variation_seed=seed,
        conversation_history=history or [],
        creator_profile=creator,
    )


def _word_overlap(left: str, right: str) -> float:
    left_words = set(re.findall(r"[a-z']+", left.lower()))
    right_words = set(re.findall(r"[a-z']+", right.lower()))
    universe = left_words | right_words
    if not universe:
        return 0.0
    return len(left_words & right_words) / len(universe)


def _structure_type(response: str) -> str:
    stripped = response.strip()
    if stripped.endswith("?") and stripped.count(".") == 0:
        return "question_only"
    if re.match(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?[,.]", stripped):
        return "name_then_question"
    if "." in stripped and "?" in stripped:
        head = stripped.split(".", 1)[0]
        if re.search(r"\b(good to see you|glad|good to hear from you|back)\b", head, re.IGNORECASE):
            return "acknowledgement_then_open"
        return "statement_then_question"
    return "direct_opener"


class GreetingVariationTests(unittest.TestCase):
    def test_same_creator_greetings_vary_across_calls(self):
        bundle = _creator_high_energy()
        responses = [_generate(bundle, seed) for seed in ["a", "b", "c", "d", "e"]]
        self.assertEqual(len(responses), len(set(responses)), responses)

        for idx, left in enumerate(responses):
            for right in responses[idx + 1:]:
                self.assertLess(_word_overlap(left, right), 0.85, (left, right))

        endings = {
            tuple(response.split("?")[0].split()[-3:]) if "?" in response else tuple(response.split()[-3:])
            for response in responses
        }
        self.assertGreater(len(endings), 1, responses)
        first_words = {response.split()[0] for response in responses}
        self.assertGreaterEqual(len(first_words), 3, responses)

    def test_greeting_structure_varies_not_just_words(self):
        bundle = _creator_high_energy()
        responses = [_generate(bundle, seed) for seed in ["shape-a", "shape-b", "shape-c", "shape-d", "shape-e"]]
        structures = {_structure_type(response) for response in responses}
        self.assertGreaterEqual(len(structures), 2, responses)

    def test_different_creators_have_different_greeting_styles(self):
        high_energy = _creator_high_energy()
        calm = _creator_calm()
        high_responses = [_generate(high_energy, seed) for seed in ["high-a", "high-b", "high-c"]]
        calm_responses = [_generate(calm, seed) for seed in ["calm-a", "calm-b", "calm-c"]]

        overlaps = [_word_overlap(left, right) for left in high_responses for right in calm_responses]
        self.assertLess(sum(overlaps) / len(overlaps), 0.5, (high_responses, calm_responses))

        first_word_pairs = [(a.split()[0], b.split()[0]) for a, b in zip(high_responses, calm_responses)]
        differing = sum(1 for left, right in first_word_pairs if left != right)
        self.assertGreaterEqual(differing, 2, first_word_pairs)

        high_question_lengths = [len(response.split("?")[0].split()) for response in high_responses]
        calm_question_lengths = [len(response.split("?")[0].split()) for response in calm_responses]
        self.assertLessEqual(sum(high_question_lengths) / len(high_question_lengths), sum(calm_question_lengths) / len(calm_question_lengths) + 2)

    def test_returning_user_greeting_differs_from_first_time(self):
        bundle = _creator_calm()
        first_time = _generate(bundle, "returning-shared", history=[])
        returning = _generate(
            bundle,
            "returning-shared",
            history=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "Hey Nathan. What's on your mind?"},
                {"role": "user", "content": "business is messy"},
                {"role": "assistant", "content": "Where is it messy?"},
            ],
        )
        self.assertNotEqual(first_time, returning)
        self.assertNotIn("welcome", returning.lower())
        self.assertNotIn("nice to meet", returning.lower())
        self.assertLessEqual(len(first_time.split()), 60)
        self.assertLessEqual(len(returning.split()), 60)

    def test_greeting_variation_seed_not_fixed(self):
        source = (BASE_DIR / "services" / "greeting_service.py").read_text(encoding="utf-8")
        self.assertNotIn("random.seed(", source)
        self.assertNotIn("numpy.random.seed(", source)
        self.assertNotIn(" seed=", source)

    def test_greeting_entropy_score(self):
        bundle = _creator_high_energy()
        responses = [_generate(bundle, seed) for seed in ["e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8"]]
        vocabulary = {}
        for response in responses:
            for token in set(re.findall(r"[a-z']+", response.lower())):
                vocabulary[token] = vocabulary.get(token, 0) + 1
        rare_words = sum(1 for count in vocabulary.values() if count < 5)
        entropy_score = rare_words / max(len(vocabulary), 1)
        self.assertGreater(entropy_score, 0.5, (responses, entropy_score))


if __name__ == "__main__":
    unittest.main()
