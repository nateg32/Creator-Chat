import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "services" / "stance_selector.py"
spec = importlib.util.spec_from_file_location("stance_selector_module", MODULE_PATH)
stance_selector_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stance_selector_module)
select_stance = stance_selector_module.select_stance


class StanceSelectorTests(unittest.TestCase):
    def test_identity_fallback_selected_for_adjacent_low_evidence_question(self):
        creator_profile = {
            "style_fingerprint": {
                "domain_map": {
                    "creator_lane": "fitness",
                    "strong_topics": ["fitness", "training", "nutrition"],
                    "adjacent_topics": ["discipline", "burnout", "consistency"],
                },
                "value_model": {
                    "core_values": ["discipline", "simplicity"],
                    "decision_heuristics": ["cut to essentials", "focus on controllables"],
                },
                "belief_graph": {
                    "core_beliefs": ["discipline beats mood"],
                },
                "reasoning_profile": {
                    "default_problem_solving_pattern": ["identify the bottleneck", "return to basics"],
                },
                "unknown_topic_policy": {
                    "allow_identity_fallback": True,
                    "disclosure_threshold": 0.35,
                    "max_assertiveness": 0.55,
                },
            }
        }

        stance = select_stance(
            "How would you handle startup burnout?",
            creator_profile,
            support_set=[],
        )

        self.assertEqual(stance["response_mode"], "IDENTITY_FALLBACK")
        self.assertTrue(stance["disclaimer_required"])
        self.assertIn("discipline", stance["activated_values"])

    def test_boundary_selected_for_unsafe_topic_without_support(self):
        creator_profile = {
            "style_fingerprint": {
                "domain_map": {
                    "creator_lane": "business",
                    "strong_topics": ["business", "sales"],
                    "unsafe_topics": ["medical advice", "diagnosis"],
                },
                "value_model": {
                    "core_values": ["truth"],
                },
                "unknown_topic_policy": {
                    "allow_identity_fallback": True,
                },
            }
        }

        stance = select_stance(
            "What diagnosis would you give for this medical problem?",
            creator_profile,
            support_set=[],
        )

        self.assertEqual(stance["response_mode"], "BOUNDARY")

    def test_boundary_selected_for_high_stakes_question_even_without_unsafe_topics(self):
        creator_profile = {
            "style_fingerprint": {
                "domain_map": {
                    "creator_lane": "business",
                    "strong_topics": ["business", "sales"],
                },
                "value_model": {
                    "core_values": ["truth"],
                },
            }
        }

        stance = select_stance(
            "What dosage should I take for this medication?",
            creator_profile,
            support_set=[],
        )

        self.assertEqual(stance["response_mode"], "BOUNDARY")
        self.assertTrue(stance["high_stakes_hit"])


if __name__ == "__main__":
    unittest.main()
