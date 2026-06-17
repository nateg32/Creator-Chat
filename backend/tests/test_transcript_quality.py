import importlib.util
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, relative_path: str):
    module_path = BACKEND_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


transcript_quality = _load_module("transcript_quality_module_tests", "services/transcript_quality.py")


class TranscriptQualityTests(unittest.TestCase):
    def test_rejects_login_wall_text(self):
        report = transcript_quality.assess_transcript_quality("Sign in to continue watching this video.")

        self.assertFalse(report["usable"])
        self.assertEqual(report["reason"], "blocked")

    def test_rejects_short_caption_mirror(self):
        report = transcript_quality.assess_transcript_quality(
            "Build the smallest version first.",
            caption="Build the smallest version first.",
            title="Build the smallest version first.",
        )

        self.assertFalse(report["usable"])
        self.assertIn(report["reason"], {"too_short", "title_only", "caption_only", "caption_mirror"})

    def test_accepts_longer_real_transcript(self):
        report = transcript_quality.assess_transcript_quality(
            (
                "Pick one buyer with money and urgency, then find one workflow they already hate. "
                "Pre sell the smallest version first, get cash before code, and only then build the product."
            ),
            caption="Pick one buyer with money and urgency.",
            title="How to find your first software offer",
        )

        self.assertTrue(report["usable"])
        self.assertIn(report["coverage"], {"partial", "full"})
        self.assertGreaterEqual(report["score"], 0.45)


if __name__ == "__main__":
    unittest.main()
