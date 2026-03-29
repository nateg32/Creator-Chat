"""Tests for the creator evidence graph and entity resolution layer."""

import importlib.util
import pathlib
import sys
import types
import unittest


BASE_DIR = pathlib.Path(__file__).resolve().parents[1]


def _stub_module(name: str, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def _load_creator_entity_service():
    _stub_module(
        "backend.db",
        db=types.SimpleNamespace(
            execute_one=lambda *args, **kwargs: None,
            execute_query=lambda *args, **kwargs: [],
            execute_update=lambda *args, **kwargs: None,
        ),
    )
    module_path = BASE_DIR / "services" / "creator_entity_service.py"
    spec = importlib.util.spec_from_file_location("creator_entity_service_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["creator_entity_service_test"] = module
    spec.loader.exec_module(module)
    return module.creator_entity_service


class CreatorEntityServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = _load_creator_entity_service()
        cls.creator = {
            "id": 1,
            "name": "Dan Martell",
            "handle": "danmartell",
            "official_domains": ["danmartell.com"],
            "platform_configs": {
                "youtube": {"handle": "danmartell"},
                "instagram": {"handle": "danmartell"},
            },
            "identity_fingerprint": 'Author of "Buy Back Your Time" and creator of a program called "High Performance CEO".',
            "research_summary": {
                "creator_claims": ['Hosts a podcast called "Growth Stacking".'],
            },
            "soul_md": 'You wrote "Buy Back Your Time" and teach systems for founder leverage.',
        }

    def test_build_entity_graph_extracts_creator_owned_entities(self):
        graph = self.service.build_entity_graph(creator_profile=self.creator, refresh=True)
        entities = graph.get("entities") or []
        names = {entity.get("name") for entity in entities}
        self.assertIn("Buy Back Your Time", names)
        self.assertIn("High Performance CEO", names)
        self.assertIn("Growth Stacking", names)

        book = next(entity for entity in entities if entity.get("name") == "Buy Back Your Time")
        self.assertEqual(book.get("type"), "book")
        self.assertIn("your book", [alias.lower() for alias in book.get("aliases") or []])
        self.assertTrue(book.get("official_urls"))

    def test_resolve_generic_followup_to_single_book_entity(self):
        entity = self.service.resolve_entity(
            "when was your book published",
            creator_profile=self.creator,
            conversation_history=[],
        )
        self.assertIsNotNone(entity)
        self.assertEqual(entity.get("name"), "Buy Back Your Time")
        self.assertEqual(entity.get("type"), "book")

    def test_resolve_history_based_followup(self):
        entity = self.service.resolve_entity(
            "when did u write it?",
            creator_profile=self.creator,
            conversation_history=[
                {"role": "user", "content": "do you have a book?"},
                {"role": "assistant", "content": "Yeah. I wrote a book called Buy Back Your Time."},
            ],
        )
        self.assertIsNotNone(entity)
        self.assertEqual(entity.get("name"), "Buy Back Your Time")


if __name__ == "__main__":
    unittest.main()
