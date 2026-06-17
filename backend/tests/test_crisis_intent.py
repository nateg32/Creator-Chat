import unittest

from backend.services.crisis_intent import (
    build_crisis_response,
    detect_crisis_followup_intent,
    detect_crisis_intent,
)


class CrisisIntentTests(unittest.TestCase):
    def test_detects_direct_self_harm_question(self):
        intent = detect_crisis_intent("should i kill myself")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "self_harm")
        self.assertEqual(intent.urgency, "immediate")

    def test_detects_first_person_suicidal_language(self):
        intent = detect_crisis_intent("I don't want to live anymore")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "self_harm")

    def test_detects_soft_first_person_suicidal_language(self):
        intent = detect_crisis_intent("i just been feeling suicidal lately")

        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "self_harm")

    def test_detects_creator_story_followup_inside_crisis_context(self):
        history = [
            {"role": "user", "content": "i just been feeling suicidal lately"},
            {
                "role": "assistant",
                "content": "Call your local emergency number or local lifeline now. Stay with me.",
            },
        ]
        intent = detect_crisis_followup_intent("did u ever feel like that in your career", history)

        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "self_harm_followup")

    def test_does_not_fast_path_educational_or_third_party_question(self):
        self.assertIsNone(detect_crisis_intent("what did Alex say about suicide prevention?"))
        self.assertIsNone(detect_crisis_intent("what should I say if my friend is suicidal?"))

    def test_crisis_response_is_direct_global_and_has_emergency_steps(self):
        response = build_crisis_response(
            user_name="Nathan",
            creator_profile={"name": "Alex Hormozi"},
        )

        self.assertIn("Nathan, no.", response)
        self.assertIn("local emergency number", response)
        self.assertIn("local suicide lifeline", response)
        self.assertNotIn("988", response)
        self.assertIn("I might hurt myself and I need you with me now.", response)

    def test_crisis_followup_response_redirects_to_safety_not_bio(self):
        response = build_crisis_response(
            user_name="Nathan",
            creator_profile={"name": "Alex Hormozi"},
            followup=True,
        )

        self.assertIn("not going to turn this into my story", response)
        self.assertIn("local emergency number", response)


if __name__ == "__main__":
    unittest.main()
