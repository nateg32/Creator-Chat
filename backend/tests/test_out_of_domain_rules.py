import importlib.util
import unittest
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[1] / "services" / "out_of_domain_rules.py"
    spec = importlib.util.spec_from_file_location("out_of_domain_rules", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


out_of_domain_rules = _load_module()


class OutOfDomainRuleTests(unittest.TestCase):
    def test_detects_bitcoin_price_as_out_of_domain_for_ministry_creator(self):
        self.assertTrue(
            out_of_domain_rules.should_soft_decline_external_live_fact(
                "what is the current price of bitcoin",
                creator_category="ministry",
                stronghold_config={"primary_domains": ["faith"]},
            )
        )

    def test_allows_bitcoin_price_for_crypto_creator(self):
        self.assertFalse(
            out_of_domain_rules.should_soft_decline_external_live_fact(
                "what is the current price of bitcoin",
                creator_category="crypto",
                stronghold_config={},
            )
        )

    def test_recent_bridge_topic_uses_previous_user_turn(self):
        history = [
            {"role": "user", "content": "when is the next event?"},
            {"role": "assistant", "content": "Which event are you asking about?"},
            {"role": "user", "content": "ACCESS event"},
        ]
        self.assertEqual(
            out_of_domain_rules.recent_bridge_topic(history, "what is the current price of bitcoin"),
            "ACCESS event",
        )

    def test_recent_bridge_topic_skips_private_questions(self):
        history = [
            {"role": "user", "content": "software business"},
            {"role": "assistant", "content": "What kind?"},
            {"role": "user", "content": "Nooo, do you believe in God?"},
        ]
        self.assertEqual(
            out_of_domain_rules.recent_bridge_topic(history, "whats a breadth first search"),
            "software business",
        )

    def test_detects_software_fundamentals_as_general_knowledge(self):
        self.assertEqual(
            out_of_domain_rules.detect_general_knowledge_topic("what are the fundamentals of software engineering"),
            "coding",
        )
        self.assertTrue(
            out_of_domain_rules.should_redirect_general_knowledge(
                "what are the fundamentals of software engineering",
                creator_primary_domains=["fitness"],
                creator_secondary_domains=["nutrition"],
            )
        )

    def test_allows_software_fundamentals_for_software_creator(self):
        self.assertFalse(
            out_of_domain_rules.should_redirect_general_knowledge(
                "what are the fundamentals of software engineering",
                creator_primary_domains=["software"],
                creator_secondary_domains=[],
            )
        )

    def test_general_how_to_redirects_when_no_domain_allows_it(self):
        self.assertTrue(
            out_of_domain_rules.should_redirect_general_knowledge(
                "how to fold a shirt",
                creator_primary_domains=["fitness"],
                creator_secondary_domains=[],
            )
        )


if __name__ == "__main__":
    unittest.main()
