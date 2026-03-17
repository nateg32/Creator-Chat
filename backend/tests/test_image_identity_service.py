import importlib.util
import pathlib
import sys
import types
import unittest


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "services" / "image_identity_service.py"
sys.modules.setdefault("backend.rag", types.SimpleNamespace())
sys.modules.setdefault(
    "backend.settings",
    types.SimpleNamespace(settings=types.SimpleNamespace(VISION_MODEL="gpt-4o", ROUTER_MODEL="gpt-4o-mini", FINAL_RESPONSE_MODEL="gpt-4o-mini")),
)
sys.modules.setdefault(
    "backend.services.research_provider",
    types.SimpleNamespace(get_research_provider=lambda: types.SimpleNamespace(search=lambda *args, **kwargs: [])),
)
spec = importlib.util.spec_from_file_location("image_identity_service_module", MODULE_PATH)
image_identity_service_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image_identity_service_module)

extract_relation_hints = image_identity_service_module.extract_relation_hints
looks_like_image_identity_question = image_identity_service_module.looks_like_image_identity_question


class ImageIdentityServiceTests(unittest.TestCase):
    def test_detects_direct_identity_question(self):
        self.assertTrue(looks_like_image_identity_question("Who's this in the photo?"))

    def test_detects_relation_hints_even_without_who_phrase(self):
        self.assertTrue(looks_like_image_identity_question("Is this your wife?"))
        self.assertIn("wife", extract_relation_hints("Is this your wife or business partner?"))
        self.assertIn("cofounder", extract_relation_hints("Is this your wife or business partner?"))

    def test_ignores_generic_visual_question(self):
        self.assertFalse(looks_like_image_identity_question("What do you see in this image?"))


if __name__ == "__main__":
    unittest.main()
