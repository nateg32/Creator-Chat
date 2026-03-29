import importlib.util
import unittest
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[1] / "services" / "decision_service.py"
    spec = importlib.util.spec_from_file_location("decision_service", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


decision_service_module = _load_module()


class DecisionServiceTests(unittest.TestCase):
    def test_general_personal_bio_defaults_to_public_ok(self):
        service = decision_service_module.DecisionService()
        move = service.choose_move(
            service.DEFAULT_POLICY,
            question_type="personal_bio",
            topic="general",
            confidence="LOW",
            intent="personal_bio_question",
            sufficiency=2,
        )
        self.assertEqual(move, "ANSWER_WITH_QUALIFIER")

    def test_relationship_question_stays_private(self):
        service = decision_service_module.DecisionService()
        move = service.choose_move(
            service.DEFAULT_POLICY,
            question_type="personal_bio",
            topic="relationship",
            confidence="HIGH",
            intent="personal_bio_question",
            sufficiency=2,
        )
        self.assertEqual(move, "DECLINE_PRIVATE")

    def test_book_question_classifies_as_general_topic(self):
        service = decision_service_module.DecisionService()
        q_type, topic, _ = service.classify_question("When did you write your book?", "personal_bio_question")
        self.assertEqual(q_type, "personal_bio")
        self.assertEqual(topic, "general")

    def test_resolves_affirmative_book_followup_from_clarification(self):
        service = decision_service_module.DecisionService()
        resolved = service.resolve_followup_question(
            "yes",
            [
                {"role": "user", "content": "when was your book launch"},
                {"role": "assistant", "content": "If you tell me which book you mean, I can help. Which one, Buy Back Your Time?"},
            ],
        )
        self.assertEqual(resolved, "When was Buy Back Your Time launched?")

    def test_does_not_rewrite_affirmation_without_book_context(self):
        service = decision_service_module.DecisionService()
        resolved = service.resolve_followup_question(
            "yes",
            [
                {"role": "user", "content": "can you help me with pricing"},
                {"role": "assistant", "content": "Do you want me to break down pricing for software?"},
            ],
        )
        self.assertEqual(resolved, "yes")

    def test_resolves_pronoun_book_followup_from_recent_title(self):
        service = decision_service_module.DecisionService()
        resolved = service.resolve_followup_question(
            "when did u write it?",
            [
                {"role": "user", "content": "do you have a book?"},
                {"role": "assistant", "content": "Yeah. I wrote a book called Buy Back Your Time."},
            ],
        )
        self.assertEqual(resolved, "When did you write Buy Back Your Time?")

    def test_partner_business_question_stays_domain_advice(self):
        service = decision_service_module.DecisionService()
        q_type, topic, _ = service.classify_question(
            "what would u reccomend if i have a partner who doesnt like my business?",
            "unknown",
        )
        self.assertEqual(q_type, "domain_advice")
        self.assertEqual(topic, "general")
        self.assertTrue(
            service.is_user_relationship_business_question(
                "what would u reccomend if i have a partner who doesnt like my business?"
            )
        )

    def test_creator_family_question_still_counts_as_personal(self):
        service = decision_service_module.DecisionService()
        q_type, topic, _ = service.classify_question("yo do you have a family?", "unknown")
        self.assertEqual(q_type, "personal_bio")
        self.assertEqual(topic, "family")

    def test_resolves_wdym_as_contextual_clarification(self):
        service = decision_service_module.DecisionService()
        resolved = service.resolve_followup_question(
            "wdymean?",
            [
                {
                    "role": "user",
                    "content": "what would u reccomend if i have a partner who doesnt like my business?",
                },
                {
                    "role": "assistant",
                    "content": "I keep that side of my life private.",
                },
            ],
        )
        self.assertIn("clarify what you meant", resolved.lower())
        self.assertIn("partner who doesnt like my business", resolved.lower())


if __name__ == "__main__":
    unittest.main()
