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
if not hasattr(sys.modules.get("backend.services.research_provider"), "get_research_provider"):
    _image_overrides["backend.services.research_provider"] = types.SimpleNamespace(
        get_research_provider=lambda: types.SimpleNamespace(search=lambda *args, **kwargs: [])
    )

spec = importlib.util.spec_from_file_location("image_identity_service_module", MODULE_PATH)
image_identity_service_module = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, _image_overrides):
    spec.loader.exec_module(image_identity_service_module)

extract_relation_hints = image_identity_service_module.extract_relation_hints
ImageIdentityService = image_identity_service_module.ImageIdentityService
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

    def test_trading_chart_does_not_hard_redirect_for_non_trading_creator(self):
        service = ImageIdentityService()

        result = service.build_off_domain_visual_redirect(
            question="whats this?",
            observation={
                "visual_domain": "trading_chart",
                "summary": "Candlestick chart with a marked support zone.",
            },
            creator_profile={"name": "Alex Hormozi", "creator_category": "business"},
        )

        self.assertIsNone(result)

    def test_allows_trading_chart_for_trading_creator(self):
        service = ImageIdentityService()

        result = service.build_off_domain_visual_redirect(
            question="whats this?",
            observation={"visual_domain": "trading_chart"},
            creator_profile={"name": "Alex G", "creator_category": "trading"},
        )

        self.assertIsNone(result)

    def test_food_visual_turn_acknowledges_image_without_sources(self):
        service = ImageIdentityService()

        with patch.object(
            image_identity_service_module.rag,
            "generate_chat_completion",
            return_value=(
                "That looks like fried chicken. I would not build the whole diet around it, "
                "but one meal is never the bottleneck, the default system is."
            ),
            create=True,
        ) as mocked_generate:
            result = service.build_visual_turn_response(
                question="do you eat a bit of this here and there?",
                observation={
                    "visual_domain": "food",
                    "summary": "A box of fried chicken pieces.",
                    "primary_subject": "fried chicken",
                },
                creator_profile={"name": "Alex Hormozi", "creator_category": "business"},
            )

        self.assertIn("fried chicken", result["answer"].lower())
        self.assertNotIn("attached", result["answer"].lower())
        self.assertEqual(result["meta"]["question_type"], "visual_chat")
        prompt = mocked_generate.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Acknowledge the actual image", prompt)
        self.assertIn("next best step", prompt)
        self.assertIn("Do not mention search", prompt)

    def test_non_specialist_open_visual_domain_does_not_hard_redirect(self):
        service = ImageIdentityService()

        result = service.build_off_domain_visual_redirect(
            question="which video explains this?",
            observation={
                "visual_domain": "fried_chicken_meal",
                "summary": "A box of fried chicken pieces.",
            },
            creator_profile={"name": "Alex Hormozi", "creator_category": "business"},
        )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
