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
            "value_hierarchy": ["truth", "discipline", "comfort"],
            "analogy_families": ["basketball", "warfare"],
            "lexical_rules": {
                "signature_phrases": ["stay sharp"],
                "high_signal_words": ["discipline"],
                "banned_words": ["delve"],
                "banned_frames": ["generic thought leader wrap-up"],
            },
            "disambiguation_markers": {
                "must_show": ["protective conviction"],
                "must_avoid": ["soft therapist voice"],
                "closest_neighbor_creators": ["generic life coach"],
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

        self.assertIn("SIGNATURE MOVES", prompt)
        self.assertIn("protective conviction", prompt)
        self.assertIn("generic life coach", prompt)
        self.assertIn("hard truth first", prompt)

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


if __name__ == "__main__":
    unittest.main()
