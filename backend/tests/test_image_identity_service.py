import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import patch


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "services" / "image_identity_service.py"

# Use patch.dict so any stubs we install for the module-load step are torn down
# after exec, instead of leaking into other test modules (e.g. tests that need
# the real backend.rag.get_persona).
_image_overrides = {}
if "backend.rag" not in sys.modules:
    _image_overrides["backend.rag"] = types.SimpleNamespace()
if "backend.settings" not in sys.modules:
    _image_overrides["backend.settings"] = types.SimpleNamespace(
        settings=types.SimpleNamespace(VISION_MODEL="gpt-4o", ROUTER_MODEL="gpt-4o-mini", FINAL_RESPONSE_MODEL="gpt-4o-mini")
    )
if "backend.services.research_provider" not in sys.modules:
    _image_overrides["backend.services.research_provider"] = types.SimpleNamespace(
        get_research_provider=lambda: types.SimpleNamespace(search=lambda *args, **kwargs: [])
    )

spec = importlib.util.spec_from_file_location("image_identity_service_module", MODULE_PATH)
image_identity_service_module = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, _image_overrides):
    spec.loader.exec_module(image_identity_service_module)

extract_relation_hints = image_identity_service_module.extract_relation_hints
looks_like_image_identity_question = image_identity_service_module.looks_like_image_identity_question


class ImageIdentityServiceTests(unittest.TestCase):
    def test_detects_direct_identity_question(self):
        self.assertTrue(looks_like_image_identity_question("Who's this in the photo?"))
        self.assertTrue(looks_like_image_identity_question("whos this chick"))

    def test_detects_relation_hints_even_without_who_phrase(self):
        self.assertTrue(looks_like_image_identity_question("Is this your wife?"))
        self.assertIn("wife", extract_relation_hints("Is this your wife or business partner?"))
        self.assertIn("cofounder", extract_relation_hints("Is this your wife or business partner?"))

    def test_ignores_generic_visual_question(self):
        self.assertFalse(looks_like_image_identity_question("What do you see in this image?"))


if __name__ == "__main__":
    unittest.main()
