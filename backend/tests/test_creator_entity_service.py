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

    def test_build_entity_support_chunk_contains_identity_and_urls(self):
        entity = self.service.resolve_entity(
            "do you know the book buy your time",
            creator_profile=self.creator,
            conversation_history=[],
        )
        chunks = self.service.build_entity_support_chunks(
            entity=entity,
            creator_profile=self.creator,
            query="do you know the book buy your time",
        )
        self.assertEqual(len(chunks), 1)
        chunk = chunks[0]
        self.assertIn("Buy Back Your Time", chunk.get("content", ""))
        self.assertEqual(chunk.get("source"), "entity_graph")
        self.assertTrue(chunk.get("url"))

    def test_relationship_slang_does_not_resolve_to_website(self):
        entity = self.service.resolve_entity(
            "do you have a misus",
            creator_profile=self.creator,
            conversation_history=[],
        )

        self.assertIsNone(entity)

    def test_website_identity_never_claims_unverified_site(self):
        answer = self.service.describe_entity_identity(
            {"type": "website", "name": "Official Website", "official_urls": []}
        )

        self.assertNotEqual(answer, "I have an official website.")
        self.assertIn("verified official site", answer)


if __name__ == "__main__":
    unittest.main()
