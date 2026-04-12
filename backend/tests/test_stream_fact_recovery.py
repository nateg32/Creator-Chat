import unittest

from backend.services import stream_fact_recovery


class _StubPersonalBioService:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def handle_personal_question(self, **kwargs):
        self.calls.append(kwargs)
        return self.payload


class StreamFactRecoveryTests(unittest.TestCase):
    def test_creator_journey_recovery_uses_personal_bio_service_and_normalizes_citations(self):
        stub = _StubPersonalBioService(
            {
                "answer": "I got into trading because I wanted more control over my future.",
                "sources": [
                    {
                        "title": "TJR interview",
                        "url": "https://example.com/interview",
                        "text": "He said he got into trading because he wanted more control over his future.",
                        "source": "web",
                    }
                ],
                "move": "ANSWER_PUBLIC_FACT",
            }
        )
        result = stream_fact_recovery.recover_streamed_creator_fact_answer(
            user_id=7,
            creator_id=9,
            question="why did u start trading?",
            creator_row={
                "name": "Tjr",
                "voice_profile": {"energy": "direct"},
                "decision_policy": {},
                "search_mode": "hybrid",
            },
            conversation_history=[{"role": "user", "content": "why did u start trading?"}],
            personal_service=stub,
        )

        self.assertEqual(result["answer"], "I got into trading because I wanted more control over my future.")
        self.assertEqual(result["move"], "ANSWER_PUBLIC_FACT")
        self.assertEqual(result["citations"][0]["title"], "TJR interview")
        self.assertEqual(result["citations"][0]["url"], "https://example.com/interview")
        self.assertEqual(stub.calls[0]["allow_web"], True)

    def test_legacy_ingested_mode_disables_web_during_recovery(self):
        stub = _StubPersonalBioService({"answer": "Recovered answer.", "sources": [], "move": "ANSWER_PUBLIC_FACT"})
        stream_fact_recovery.recover_streamed_creator_fact_answer(
            user_id=1,
            creator_id=2,
            question="when did you start trading?",
            creator_row={"name": "Tjr", "search_mode": "ingested"},
            conversation_history=[],
            personal_service=stub,
        )

        self.assertEqual(stub.calls[0]["allow_web"], False)

    def test_non_creator_fact_queries_skip_recovery(self):
        stub = _StubPersonalBioService({"answer": "Should not run", "sources": [], "move": "ANSWER_PUBLIC_FACT"})
        result = stream_fact_recovery.recover_streamed_creator_fact_answer(
            user_id=1,
            creator_id=2,
            question="what's your best productivity tip?",
            creator_row={"name": "Tjr", "search_mode": "hybrid"},
            conversation_history=[],
            personal_service=stub,
        )

        self.assertEqual(result["answer"], "")
        self.assertEqual(stub.calls, [])


if __name__ == "__main__":
    unittest.main()