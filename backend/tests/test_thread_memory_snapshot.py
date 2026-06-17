import importlib.util
import sys
import types
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _load_snapshot_module(row_payload=None):
    db_stub = types.SimpleNamespace(
        execute_update=lambda *args, **kwargs: 1,
        execute_one=lambda *args, **kwargs: row_payload,
    )
    rag_stub = types.SimpleNamespace(generate_chat_completion=lambda *args, **kwargs: "{}")
    settings_stub = types.SimpleNamespace(ROUTER_MODEL="router", MODEL_MEMORY="memory")

    sys.modules["backend.db"] = types.SimpleNamespace(db=db_stub)
    sys.modules["backend.rag"] = rag_stub
    sys.modules["backend.settings"] = types.SimpleNamespace(settings=settings_stub)

    module_path = BACKEND_ROOT / "services" / "thread_memory_snapshot.py"
    spec = importlib.util.spec_from_file_location("thread_memory_snapshot_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ThreadMemorySnapshotTests(unittest.TestCase):
    def test_prompt_block_includes_operational_memory_rules(self):
        module = _load_snapshot_module(
            {
                "snapshot": {
                    "user_context": ["plays soccer"],
                    "goals": ["start going to the gym"],
                    "answered_questions": ["What sports do you play? -> soccer"],
                    "advice_given": ["recommended full body training"],
                    "next_best_step": "give a simple soccer-friendly beginner gym plan",
                }
            }
        )

        block = module.thread_memory_snapshot_service.get_prompt_block(1, 2, "thread-1")

        self.assertIn("THREAD MEMORY SNAPSHOT", block)
        self.assertIn("Do not re-ask answered questions", block)
        self.assertIn("plays soccer", block)
        self.assertIn("soccer-friendly beginner gym plan", block)

    def test_heuristic_patch_moves_previous_question_to_answered(self):
        module = _load_snapshot_module()
        service = module.ThreadMemorySnapshotService()
        snapshot = {
            "open_questions": ["Are you training on game days or off days?"],
            "answered_questions": [],
        }

        updated = service._heuristic_patch(
            snapshot,
            "a bit of both to be fair",
            "Are you thinking about hitting weights before or after soccer practice?",
            [{"role": "assistant", "content": "Are you training on game days or off days?"}],
        )

        self.assertIn("may train on soccer days and off days", updated["preferences"])
        self.assertTrue(any("a bit of both" in item for item in updated["answered_questions"]))
        self.assertIn(
            "Are you thinking about hitting weights before or after soccer practice?",
            updated["open_questions"],
        )

    def test_heuristic_patch_contextualizes_numeric_sales_answer(self):
        module = _load_snapshot_module()
        service = module.ThreadMemorySnapshotService()

        updated = service._heuristic_patch(
            {},
            "like 2",
            "So your close rate is 20 percent. Next I need your show rate.",
            [
                {"role": "user", "content": "i run a marketing agency and leads are not converting"},
                {
                    "role": "assistant",
                    "content": "Out of the last ten qualified leads that actually got on a call with you, how many of them said yes?",
                },
            ],
        )

        self.assertIn("runs a marketing agency", updated["user_context"])
        self.assertIn("struggling to convert leads into high-paying customers", updated["constraints"])
        self.assertTrue(any("about 2 out of the last 10" in item for item in updated["answered_questions"]))

    def test_runtime_prompt_block_preserves_recent_resource_target(self):
        module = _load_snapshot_module(
            {
                "snapshot": {
                    "current_topic": "AI workflow video",
                    "goals": ["understand AI operating systems"],
                }
            }
        )

        block = module.thread_memory_snapshot_service.get_runtime_prompt_block(
            1,
            2,
            "thread-1",
            current_user_message="give me a deep breakdown, i dont wanna watch the video",
            history=[
                {
                    "role": "assistant",
                    "content": "I attached the video below.",
                    "cards": [
                        {
                            "title": "How to Actually Use AI in 2026",
                            "url": "https://youtube.com/watch?v=abc",
                        }
                    ],
                }
            ],
        )

        self.assertIn("CONVERSATION MEMORY PACKET", block)
        self.assertIn("How to Actually Use AI in 2026", block)
        self.assertIn("Follow-up target", block)

    def test_low_signal_turn_skips_llm_memory_update(self):
        module = _load_snapshot_module()
        service = module.ThreadMemorySnapshotService()

        should_update = service._should_use_llm_update(
            {},
            "yo",
            "Yo Nathan.",
            [],
            [],
        )

        self.assertFalse(should_update)


if __name__ == "__main__":
    unittest.main()
