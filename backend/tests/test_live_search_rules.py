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

    def test_extract_requested_platforms_from_current_turn(self):
        platforms = live_search_rules.extract_requested_platforms("what youtube video is good for ai")
        self.assertEqual(platforms, ["youtube"])

    def test_extract_requested_platforms_from_follow_up_history(self):
        history = [
            {"role": "user", "content": "what youtube video is good for ai"},
            {"role": "assistant", "content": "Try this one."},
        ]
        platforms = live_search_rules.extract_requested_platforms("any other video?", history=history)
        self.assertEqual(platforms, ["youtube"])

    def test_build_live_search_query_adds_creator_platform_and_video_context(self):
        history = [
            {"role": "user", "content": "I need a good youtube video for ai"},
            {"role": "assistant", "content": "What kind of business are you thinking about?"},
        ]
        query = live_search_rules.build_live_search_query(
            "what about one for beginners?",
            history=history,
            creator_name="Alex Hormozi",
            preferred_platforms=["youtube"],
            require_video=True,
        )
        self.assertIn("Alex Hormozi", query)
        self.assertIn("youtube", query.lower())
        self.assertIn("video", query.lower())
        self.assertIn("beginners", query.lower())

    def test_build_live_search_query_adds_handle_and_niche(self):
        query = live_search_rules.build_live_search_query(
            "beliefs on risk",
            creator_name="Alex Gonzalez",
            creator_handle="@wayondtv",
            creator_niche="forex trading",
        )
        self.assertIn("Alex Gonzalez", query)
        self.assertIn("@wayondtv", query)
        self.assertIn("forex trading", query)


if __name__ == "__main__":
    unittest.main()
