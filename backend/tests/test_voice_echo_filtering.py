import importlib.util
import pathlib
import sys
import unittest


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_voice_dna_module():
    module_path = BACKEND_ROOT / "services" / "voice_dna.py"
    spec = importlib.util.spec_from_file_location("voice_dna_echo_filter_tests", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["voice_dna_echo_filter_tests"] = module
    spec.loader.exec_module(module)
    return module


voice_dna = _load_voice_dna_module()


class VoiceEchoFilteringTests(unittest.TestCase):
    def test_voice_echo_skips_content_hooks_but_keeps_natural_voice(self):
        chunks = [
            {
                "content": (
                    "Bro needs to see this. If you know you know. "
                    "Why are they like this thinking fried chicken is the only way to get big. "
                    "I keep calories simple when the goal is dropping fat without losing muscle. "
                    "Most guys getf*cked by the noise."
                )
            }
        ]

        echoes = voice_dna.extract_voice_echoes(chunks, max_phrases=5)
        joined = " ".join(echoes).lower()

        self.assertIn("calories simple", joined)
        self.assertNotIn("bro needs to see this", joined)
        self.assertNotIn("if you know you know", joined)
        self.assertNotIn("why are they like this", joined)
        self.assertNotIn("getf*cked", joined)


if __name__ == "__main__":
    unittest.main()
