import importlib.util
import unittest
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[1] / "services" / "live_search_rules.py"
    spec = importlib.util.spec_from_file_location("live_search_rules", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


live_search_rules = _load_module()


class LiveSearchRuleTests(unittest.TestCase):
    def test_detects_event_timing_question(self):
        self.assertTrue(live_search_rules.needs_fresh_public_web_search("when is the next event?"))

    def test_detects_short_follow_up_with_history(self):
        history = [
            {"role": "user", "content": "when is the next event?"},
            {"role": "assistant", "content": "Which event are you asking about?"},
        ]
        self.assertTrue(live_search_rules.needs_fresh_public_web_search("ACCESS event", history))

    def test_does_not_trigger_for_general_opinion(self):
        self.assertFalse(live_search_rules.needs_fresh_public_web_search("what do you think about discipline?"))

    def test_builds_contextual_live_search_query(self):
        history = [
            {"role": "user", "content": "when is the next event?"},
            {"role": "assistant", "content": "Which event are you asking about?"},
            {"role": "user", "content": "ACCESS event"},
        ]
        self.assertEqual(
            live_search_rules.build_live_search_query("ACCESS event", history),
            "when is the next event? ACCESS event",
        )


if __name__ == "__main__":
    unittest.main()
