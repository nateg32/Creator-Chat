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


if __name__ == "__main__":
    unittest.main()
