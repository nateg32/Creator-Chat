import importlib.util
import pathlib
import unittest

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "services" / "style_distiller.py"
spec = importlib.util.spec_from_file_location("style_distiller_module", MODULE_PATH)
style_distiller_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(style_distiller_module)
StyleDistiller = style_distiller_module.StyleDistiller


class StyleDistillerTests(unittest.TestCase):
    def test_style_distiller_promotes_differential_fields_into_prompt(self):
        distiller = StyleDistiller()
        fingerprint = {
            "signature_moves": ["reframe to principle", "close with command"],
            "signature_response_moves": ["hard truth first"],
            "value_hierarchy": ["truth", "discipline", "comfort"],
            "analogy_families": ["basketball", "warfare"],
            "belief_graph": {
                "core_beliefs": ["discipline compounds"],
                "non_negotiables": ["personal responsibility"],
                "beliefs_they_attack": ["victim thinking"],
            },
            "lexical_rules": {
                "signature_phrases": ["stay sharp"],
                "high_signal_words": ["discipline"],
                "banned_words": ["delve"],
                "banned_frames": ["generic thought leader wrap up"],
            },
            "disambiguation_markers": {
                "must_show": ["protective conviction"],
                "must_avoid": ["soft therapist voice"],
                "closest_neighbor_creators": ["generic life coach"],
            },
            "contrastive_identity": {
                "confusion_risks": ["empty motivational fluff"],
            },
            "anti_persona": {
                "forbidden_emotional_postures": ["needy reassurance"],
                "forbidden_generic_coach_lines": ["you got this"],
                "confusable_with": ["generic life coach"],
            },
            "mode_matrix": {
                "teaching": {"opening_move": "hard truth first", "structure": "principle -> step", "forbidden": ["apologetic hedging"]}
            },
        }
        dna = distiller.get_style_dna(1, fingerprint)
        prompt = distiller.format_for_prompt(dna, mode="task")

        self.assertIn("SIGNATURE RESPONSE MOVES", prompt)
        self.assertIn("discipline compounds", prompt)
        self.assertIn("protective conviction", prompt)
        self.assertIn("empty motivational fluff", prompt)

    def test_style_distiller_maps_small_talk_to_comfort_mode(self):
        distiller = StyleDistiller()
        fingerprint = {
            "mode_matrix": {
                "comfort": {"opening_move": "acknowledge emotion first", "forbidden": ["debate mode"]}
            }
        }
        dna = distiller.get_style_dna(1, fingerprint)
        prompt = distiller.format_for_prompt(dna, mode="small_talk")

        self.assertIn("acknowledge emotion first", prompt)

    def test_runtime_identity_packet_selects_matching_story_and_pressure(self):
        distiller = StyleDistiller()
        creator_profile = {
            "style_fingerprint": {
                "belief_graph": {
                    "core_beliefs": ["discipline beats mood"],
                    "beliefs_they_attack": ["victim mindset"],
                },
                "story_bank": [
                    {
                        "story_id": "s1",
                        "title": "Broke to profitable",
                        "trigger_topics": ["money", "business"],
                        "summary": "He tells the story of being broke and learning to sell.",
                        "lesson": "skill compounds when you stop blaming.",
                    },
                    {
                        "story_id": "s2",
                        "title": "Training camp",
                        "trigger_topics": ["fitness"],
                        "summary": "A fitness story.",
                        "lesson": "consistency matters.",
                    },
                ],
                "pressure_engine": {
                    "user_insecure": {"default_move": "steady them before pushing", "goal": "restore confidence"}
                },
                "knowledge_boundaries": {
                    "must_verify_topics": ["net worth"],
                    "private_or_unknown": ["family details"],
                },
                "contrastive_identity": {
                    "must_show": ["protective conviction"],
                },
            },
            "identity_fingerprint": {
                "verified_facts": ["Built multiple businesses"],
                "products": ["Acquisition.com"],
            },
        }

        packet = distiller.build_runtime_identity_packet(
            "I feel insecure about money and business",
            creator_profile,
            user_state={"emotion": "insecure"},
            mode="task",
        )

        self.assertEqual(packet["pressure_key"], "user_insecure")
        self.assertEqual(packet["stories"][0]["story_id"], "s1")
        self.assertIn("discipline beats mood", packet["belief_focus"]["core_beliefs"])
        self.assertIn("Built multiple businesses", packet["identity_facts"])


if __name__ == "__main__":
    unittest.main()
