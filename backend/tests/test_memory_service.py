"""Regression tests for conversational memory ranking.

These tests cover the light-weight retrieval layer that chooses which stored
facts to surface back into the prompt. The goal is to prefer facts that match
the current turn semantically while still keeping high-value evergreen context
like goals and constraints near the top when there is no strong lexical match.
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _load_memory_service(row_payload):
    db_stub = types.SimpleNamespace(execute_one=lambda *args, **kwargs: row_payload)
    rag_stub = types.SimpleNamespace(generate_chat_completion=lambda *args, **kwargs: "[]")
    settings_stub = types.SimpleNamespace(ROUTER_MODEL="test-router")

    sys.modules["backend.db"] = types.SimpleNamespace(db=db_stub)
    sys.modules["backend.rag"] = rag_stub
    sys.modules["backend.settings"] = types.SimpleNamespace(settings=settings_stub)

    module_path = BACKEND_ROOT / "services" / "memory_service.py"
    spec = importlib.util.spec_from_file_location("memory_service_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MemoryServiceTests(unittest.TestCase):
    def test_relevant_context_prefers_overlap_and_high_value_slots(self):
        module = _load_memory_service(
            {
                "facts": [
                    {"slot": "personal_detail", "value": "live in Sydney", "confidence": 0.95},
                    {"slot": "goal", "value": "launch a recruiting SaaS", "confidence": 0.96},
                    {"slot": "constraint", "value": "only have five hours a week", "confidence": 0.94},
                    {"slot": "preference", "value": "likes Loom videos", "confidence": 0.8},
                ]
            }
        )

        results = module.memory_service.get_relevant_context(
            user_id=1,
            creator_id=1,
            thread_id="thread-1",
            current_message="I only have a few hours each week to launch my recruiting software business.",
        )

        ordered_slots = [item["slot"] for item in results]
        self.assertIn("goal", ordered_slots[:2])
        self.assertIn("constraint", ordered_slots[:2])

    def test_relevant_context_falls_back_to_evergreen_priority_when_overlap_is_weak(self):
        module = _load_memory_service(
            {
                "facts": [
                    {"slot": "personal_detail", "value": "has a golden retriever", "confidence": 0.9},
                    {"slot": "goal", "value": "grow a B2B outbound agency", "confidence": 0.97},
                    {"slot": "constraint", "value": "working with a low budget", "confidence": 0.93},
                ]
            }
        )

        results = module.memory_service.get_relevant_context(
            user_id=1,
            creator_id=1,
            thread_id="thread-2",
            current_message="I want a clearer plan for next quarter.",
        )

        self.assertGreaterEqual(len(results), 2)
        self.assertEqual(results[0]["slot"], "goal")
        self.assertEqual(results[1]["slot"], "constraint")


if __name__ == "__main__":
    unittest.main()
