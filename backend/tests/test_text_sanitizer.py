import importlib.util
import unittest
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[1] / "services" / "text_sanitizer.py"
    spec = importlib.util.spec_from_file_location("text_sanitizer", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


text_sanitizer = _load_module()


class TextSanitizerTests(unittest.TestCase):
    def test_removes_compound_hyphens(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("Pick one high-income skill."),
            "Pick one high income skill.",
        )

    def test_replaces_clause_dashes_with_commas(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("Do the work - then raise your price."),
            "Do the work, then raise your price.",
        )

    def test_preserves_leading_bullets(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("- Keep going"),
            "- Keep going",
        )


if __name__ == "__main__":
    unittest.main()
