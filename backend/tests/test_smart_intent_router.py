import json
import unittest
from unittest.mock import patch

from backend.services.smart_intent_router import (
    _CACHE,
    _history_digest,
    _build_prompt,
    should_use_smart_router,
    smart_intent_router,
)


class SmartIntentRouterTests(unittest.TestCase):
    def setUp(self):
        _CACHE.clear()

    def test_router_runs_for_ambiguous_creator_company_question(self):
        self.assertTrue(
            should_use_smart_router(
                "what did u do in Gym Launch?",
                route="ROUTE_2_TASK",
                question_type="domain_advice",
                rule_intent="request",
                history=[],
            )
        )

    def test_router_skips_local_fast_lane_greeting(self):
        self.assertFalse(
            should_use_smart_router(
                "yo",
                route="ROUTE_0_GREETING",
                question_type="greeting",
                rule_intent="greeting_only",
                history=[],
            )
        )

    def test_router_skips_local_fast_lane_small_talk(self):
        self.assertFalse(
            should_use_smart_router(
                "what have you been up to",
                route="ROUTE_1_SMALL_TALK",
                question_type="small_talk",
                rule_intent="small_talk",
                history=[],
            )
        )

    def test_history_digest_includes_resource_titles(self):
        plain = _history_digest([{"role": "assistant", "content": "I attached the video below."}])
        with_card = _history_digest(
            [
                {
                    "role": "assistant",
                    "content": "I attached the video below.",
                    "cards": [{"title": "How to Actually Use AI in 2026"}],
                }
            ]
        )

        self.assertNotEqual(plain, with_card)

    def test_router_runs_for_creator_agnostic_social_openers(self):
        self.assertTrue(
            should_use_smart_router(
                "yoooo broskki",
                route="ROUTE_2_TASK",
                question_type="domain_advice",
                rule_intent="request",
                history=[],
            )
        )
        self.assertTrue(
            should_use_smart_router(
                "heyyy danny whatsup",
                route="ROUTE_2_TASK",
                question_type="domain_advice",
                rule_intent="request",
                history=[],
            )
        )

    def test_light_route_decision_forces_no_retrieval_or_sources(self):
        payload = {
            "intent": "casual_greeting",
            "route": "ROUTE_0_GREETING",
            "question_type": "greeting",
            "query_goal": "general",
            "needs_memory": True,
            "needs_corpus": True,
            "needs_web": True,
            "needs_sources": True,
            "response_mode": "small_talk",
            "confidence": 0.94,
            "reason": "social opener only",
        }
        with patch(
            "backend.services.smart_intent_router.rag.generate_chat_completion",
            return_value=json.dumps(payload),
        ):
            decision = smart_intent_router.classify(
                "yoooo broskki",
                history=[],
                timeout_seconds=1.0,
            )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.route, "ROUTE_0_GREETING")
        self.assertFalse(decision.needs_memory)
        self.assertFalse(decision.needs_corpus)
        self.assertFalse(decision.needs_web)
        self.assertFalse(decision.needs_sources)
        self.assertEqual(decision.source_policy, "none")

    def test_classify_coerces_creator_business_history_json(self):
        payload = {
            "intent": "creator_business_history",
            "route": "ROUTE_2_TASK",
            "question_type": "creator_fact",
            "query_goal": "journey_lookup",
            "needs_memory": False,
            "needs_corpus": True,
            "needs_web": True,
            "needs_sources": True,
            "is_creator_fact": True,
            "entity_subject": "Gym Launch",
            "query_plan": ["Alex Hormozi Gym Launch role", "Alex Hormozi Gym Launch background"],
            "response_mode": "answer",
            "confidence": 0.91,
            "reason": "Asks what the creator did inside a company.",
        }
        with patch(
            "backend.services.smart_intent_router.rag.generate_chat_completion",
            return_value=json.dumps(payload),
        ):
            decision = smart_intent_router.classify(
                "what did u do in Gym Launch?",
                history=[],
                timeout_seconds=1.0,
            )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.intent, "creator_business_history")
        self.assertEqual(decision.query_goal, "journey_lookup")
        self.assertTrue(decision.is_creator_fact)
        self.assertEqual(decision.entity_subject, "Gym Launch")
        self.assertEqual(decision.query_plan[0], "Alex Hormozi Gym Launch role")
        self.assertEqual(decision.source_policy, "must_cite")

    def test_classify_creator_motivation_uses_journey_web_not_timeline(self):
        payload = {
            "intent": "creator_motivation",
            "route": "ROUTE_2_TASK",
            "question_type": "personal_bio",
            "query_goal": "journey_lookup",
            "needs_memory": False,
            "needs_corpus": True,
            "needs_web": True,
            "needs_sources": True,
            "is_creator_fact": True,
            "entity_subject": "Acquisition.com and Gym Launch",
            "query_plan": [
                "Alex Hormozi why started Acquisition.com after Gym Launch",
                "Alex Hormozi why not retire after Gym Launch",
            ],
            "response_mode": "answer",
            "confidence": 0.93,
            "reason": "Asks motivation/story, not a launch date.",
        }
        with patch(
            "backend.services.smart_intent_router.rag.generate_chat_completion",
            return_value=json.dumps(payload),
        ):
            decision = smart_intent_router.classify(
                "what inspired you to start acquisition, why didnt u just retire after scaling gym launch",
                history=[],
                timeout_seconds=1.0,
            )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.question_type, "personal_bio")
        self.assertEqual(decision.query_goal, "journey_lookup")
        self.assertTrue(decision.needs_web)
        self.assertTrue(decision.needs_sources)
        self.assertEqual(decision.source_policy, "must_cite")

    def test_prompt_includes_clean_memory_packet_for_contextual_followup(self):
        prompt = json.loads(
            _build_prompt(
                "what made u turn it around?",
                history=[
                    {
                        "role": "user",
                        "content": "tell me about your background, whats your story/journey, how did u get rich?",
                    },
                    {
                        "role": "assistant",
                        "content": (
                            "My journey started in a dark place, going from a convict involved with stolen cars "
                            "to finding my path in technology and entrepreneurship."
                        ),
                    },
                ],
            )
        )

        packet = prompt["conversation_memory_packet"]
        self.assertTrue(packet["is_likely_contextual_followup"])
        self.assertEqual(packet["contextual_followup_kind"], "creator_turnaround")
        self.assertIn("turning point", packet["current_followup_target_hint"])
        self.assertIn("convict", " ".join(packet["last_assistant_claims"]).lower())
        self.assertIn("latest_user_before_current", packet)

    def test_turning_point_followup_overrides_generic_model_classification(self):
        payload = {
            "intent": "general",
            "route": "ROUTE_2_TASK",
            "question_type": "domain_advice",
            "query_goal": "general",
            "needs_memory": True,
            "needs_corpus": True,
            "needs_web": False,
            "needs_sources": False,
            "is_creator_fact": False,
            "resolved_user_message": "The user asks what specific catalyst made the creator turn his life around.",
            "response_mode": "answer",
            "confidence": 0.91,
            "reason": "Model under-classified the contextual biography follow-up.",
        }
        with patch(
            "backend.services.smart_intent_router.rag.generate_chat_completion",
            return_value=json.dumps(payload),
        ):
            decision = smart_intent_router.classify(
                "what made u turn it around?",
                history=[
                    {
                        "role": "assistant",
                        "content": "My journey started in a dark place before I found technology and turned my life around.",
                    }
                ],
                timeout_seconds=1.0,
            )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.question_type, "personal_bio")
        self.assertEqual(decision.query_goal, "journey_lookup")
        self.assertTrue(decision.needs_web)
        self.assertTrue(decision.needs_sources)
        self.assertEqual(decision.source_policy, "must_cite")

    def test_misus_slang_overrides_bad_website_classification(self):
        payload = {
            "intent": "official_website_lookup",
            "route": "ROUTE_2_TASK",
            "question_type": "creator_fact",
            "query_goal": "availability_lookup",
            "needs_memory": False,
            "needs_corpus": False,
            "needs_web": True,
            "needs_sources": True,
            "is_creator_fact": True,
            "entity_subject": "Official Website",
            "query_plan": ["Alex Hormozi official website"],
            "source_policy": "must_cite",
            "response_mode": "answer",
            "confidence": 0.93,
            "reason": "Bad model classification from slang.",
        }
        with patch(
            "backend.services.smart_intent_router.rag.generate_chat_completion",
            return_value=json.dumps(payload),
        ):
            decision = smart_intent_router.classify(
                "do you have a misus",
                history=[],
                timeout_seconds=1.0,
            )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.question_type, "personal_bio")
        self.assertEqual(decision.query_goal, "identity_lookup")
        self.assertTrue(decision.needs_web)
        self.assertTrue(decision.needs_sources)
        self.assertTrue(decision.is_creator_fact)
        self.assertEqual(decision.source_policy, "cite_if_used")
        self.assertEqual(decision.query_plan, [])
        self.assertEqual(decision.entity_subject, "")

    def test_classify_coerces_casual_check_in_without_search(self):
        payload = {
            "intent": "casual_check_in",
            "route": "ROUTE_1_SMALL_TALK",
            "question_type": "small_talk",
            "query_goal": "general",
            "needs_memory": False,
            "needs_corpus": False,
            "needs_web": False,
            "needs_sources": False,
            "is_creator_fact": False,
            "entity_subject": "",
            "query_plan": [],
            "response_mode": "small_talk",
            "confidence": 0.89,
            "reason": "Reciprocal small talk.",
        }
        with patch(
            "backend.services.smart_intent_router.rag.generate_chat_completion",
            return_value=json.dumps(payload),
        ):
            decision = smart_intent_router.classify(
                "nothing much wbu",
                history=[],
                timeout_seconds=1.0,
            )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.route, "ROUTE_1_SMALL_TALK")
        self.assertFalse(decision.needs_web)
        self.assertFalse(decision.needs_sources)
        self.assertEqual(decision.source_policy, "none")

    def test_classify_resolves_short_answer_from_history(self):
        payload = {
            "intent": "sales_conversion_answer",
            "route": "ROUTE_2_TASK",
            "question_type": "domain_advice",
            "query_goal": "general",
            "needs_memory": True,
            "needs_corpus": True,
            "needs_web": False,
            "needs_sources": False,
            "is_creator_fact": False,
            "entity_subject": "",
            "query_plan": [],
            "resolved_user_message": "The user says about 2 of the last 10 qualified sales calls converted.",
            "source_policy": "none",
            "response_mode": "answer",
            "confidence": 0.91,
            "reason": "Short answer resolves against previous assistant question.",
        }
        history = [
            {
                "role": "assistant",
                "content": "Out of the last ten qualified leads that actually got on a call with you, how many of them said yes?",
            }
        ]
        with patch(
            "backend.services.smart_intent_router.rag.generate_chat_completion",
            return_value=json.dumps(payload),
        ):
            decision = smart_intent_router.classify(
                "like 2",
                history=history,
                timeout_seconds=1.0,
            )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.question_type, "domain_advice")
        self.assertTrue(decision.needs_memory)
        self.assertFalse(decision.needs_web)
        self.assertEqual(decision.source_policy, "none")
        self.assertIn("2 of the last 10", decision.resolved_user_message)

    def test_sales_script_followup_overrides_public_stat_misclassification(self):
        payload = {
            "intent": "creator_financial_public_stat",
            "route": "ROUTE_2_TASK",
            "question_type": "creator_fact",
            "query_goal": "current_stat_lookup",
            "needs_memory": False,
            "needs_corpus": False,
            "needs_web": True,
            "needs_sources": True,
            "is_creator_fact": True,
            "entity_subject": "sales",
            "query_plan": ["sales current figures"],
            "source_policy": "must_cite",
            "response_mode": "answer",
            "confidence": 0.94,
            "reason": "Bad model classification from the word sales.",
        }
        history = [
            {
                "role": "assistant",
                "content": "Are you using a specific sales script right now or just winging the calls?",
            }
        ]
        with patch(
            "backend.services.smart_intent_router.rag.generate_chat_completion",
            return_value=json.dumps(payload),
        ):
            decision = smart_intent_router.classify(
                "im using a sales script",
                history=history,
                timeout_seconds=1.0,
            )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.question_type, "domain_advice")
        self.assertEqual(decision.query_goal, "general")
        self.assertTrue(decision.needs_memory)
        self.assertTrue(decision.needs_corpus)
        self.assertFalse(decision.needs_web)
        self.assertFalse(decision.needs_sources)
        self.assertFalse(decision.is_creator_fact)
        self.assertEqual(decision.source_policy, "none")
        self.assertIn("sales script", decision.resolved_user_message)

    def test_user_business_metric_overrides_price_lookup_misclassification(self):
        payload = {
            "intent": "creator_price_lookup",
            "route": "ROUTE_2_TASK",
            "question_type": "creator_fact",
            "query_goal": "price_lookup",
            "needs_memory": False,
            "needs_corpus": False,
            "needs_web": True,
            "needs_sources": True,
            "is_creator_fact": True,
            "entity_subject": "software pricing",
            "query_plan": ["software customer acquisition cost"],
            "source_policy": "must_cite",
            "response_mode": "answer",
            "confidence": 0.94,
            "reason": "Bad model classification from the word cost.",
        }
        with patch(
            "backend.services.smart_intent_router.rag.generate_chat_completion",
            return_value=json.dumps(payload),
        ):
            decision = smart_intent_router.classify(
                "im selling software and it costs me 100 dollars for a customer",
                history=[
                    {
                        "role": "assistant",
                        "content": "What is the specific product you are selling and what does it cost you to acquire a single customer right now?",
                    }
                ],
                timeout_seconds=1.0,
            )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.question_type, "domain_advice")
        self.assertEqual(decision.query_goal, "general")
        self.assertTrue(decision.needs_memory)
        self.assertTrue(decision.needs_corpus)
        self.assertFalse(decision.needs_web)
        self.assertFalse(decision.needs_sources)
        self.assertFalse(decision.is_creator_fact)
        self.assertEqual(decision.source_policy, "none")

    def test_user_business_problem_overrides_private_misclassification(self):
        payload = {
            "intent": "private_sensitive",
            "route": "ROUTE_2_TASK",
            "question_type": "private_sensitive",
            "query_goal": "general",
            "needs_memory": False,
            "needs_corpus": False,
            "needs_web": False,
            "needs_sources": False,
            "is_creator_fact": True,
            "entity_subject": "",
            "query_plan": [],
            "source_policy": "must_cite",
            "response_mode": "boundary",
            "confidence": 0.9,
            "reason": "Bad model classification.",
        }
        with patch(
            "backend.services.smart_intent_router.rag.generate_chat_completion",
            return_value=json.dumps(payload),
        ):
            decision = smart_intent_router.classify(
                "ive been getting leads but i dont know how to convert them, i run a marketing agency",
                history=[],
                timeout_seconds=1.0,
            )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.question_type, "domain_advice")
        self.assertEqual(decision.response_mode, "answer")
        self.assertTrue(decision.needs_memory)
        self.assertTrue(decision.needs_corpus)
        self.assertFalse(decision.needs_web)
        self.assertFalse(decision.needs_sources)
        self.assertFalse(decision.is_creator_fact)
        self.assertEqual(decision.source_policy, "none")

    def test_classify_coerces_self_harm_to_crisis_no_search(self):
        payload = {
            "intent": "self_harm",
            "route": "ROUTE_2_TASK",
            "question_type": "self_harm",
            "query_goal": "general",
            "needs_memory": True,
            "needs_corpus": True,
            "needs_web": True,
            "needs_sources": True,
            "is_creator_fact": False,
            "entity_subject": "",
            "query_plan": ["should i kill myself"],
            "response_mode": "answer",
            "confidence": 0.98,
            "reason": "Direct self-harm question.",
        }
        with patch(
            "backend.services.smart_intent_router.rag.generate_chat_completion",
            return_value=json.dumps(payload),
        ):
            decision = smart_intent_router.classify(
                "should i kill myself",
                history=[],
                timeout_seconds=1.0,
            )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.question_type, "self_harm")
        self.assertEqual(decision.query_goal, "crisis_support")
        self.assertEqual(decision.response_mode, "crisis")
        self.assertFalse(decision.needs_memory)
        self.assertFalse(decision.needs_corpus)
        self.assertFalse(decision.needs_web)
        self.assertFalse(decision.needs_sources)
        self.assertEqual(decision.source_policy, "none")

    def test_classify_allows_harmful_request_boundary(self):
        payload = {
            "intent": "harmful_request",
            "route": "ROUTE_2_TASK",
            "question_type": "harmful_request",
            "query_goal": "general",
            "needs_memory": False,
            "needs_corpus": False,
            "needs_web": False,
            "needs_sources": False,
            "is_creator_fact": False,
            "entity_subject": "",
            "query_plan": [],
            "response_mode": "boundary",
            "confidence": 0.96,
            "reason": "Unsafe request.",
        }
        with patch(
            "backend.services.smart_intent_router.rag.generate_chat_completion",
            return_value=json.dumps(payload),
        ):
            decision = smart_intent_router.classify(
                "how do I hurt someone without getting caught",
                history=[],
                timeout_seconds=1.0,
            )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.question_type, "harmful_request")
        self.assertEqual(decision.response_mode, "boundary")
        self.assertFalse(decision.needs_web)
        self.assertFalse(decision.needs_sources)
        self.assertEqual(decision.source_policy, "none")

    def test_current_financial_stat_forces_web_and_sources(self):
        payload = {
            "intent": "creator_financial_public_stat",
            "route": "ROUTE_2_TASK",
            "question_type": "creator_fact",
            "query_goal": "current_stat_lookup",
            "needs_memory": False,
            "needs_corpus": True,
            "needs_web": False,
            "needs_sources": False,
            "is_creator_fact": False,
            "entity_subject": "Acquisition.com",
            "query_plan": ["Alex Hormozi Acquisition.com revenue now"],
            "response_mode": "answer",
            "confidence": 0.95,
            "reason": "Asks for a current public company stat.",
        }
        with patch(
            "backend.services.smart_intent_router.rag.generate_chat_completion",
            return_value=json.dumps(payload),
        ):
            decision = smart_intent_router.classify(
                "whats acquisitions revenue now",
                history=[],
                timeout_seconds=1.0,
            )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.route, "ROUTE_2_TASK")
        self.assertEqual(decision.query_goal, "current_stat_lookup")
        self.assertTrue(decision.needs_web)
        self.assertTrue(decision.needs_sources)
        self.assertTrue(decision.is_creator_fact)
        self.assertEqual(decision.source_policy, "must_cite")

    def test_memory_packet_marks_resource_breakdown_followup(self):
        prompt = json.loads(
            _build_prompt(
                "give me a deep breakdown, i dont wanna watch the video",
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
        )
        packet = prompt["conversation_memory_packet"]

        self.assertEqual(packet["contextual_followup_kind"], "resource_breakdown")
        self.assertIn("How to Actually Use AI in 2026", packet["current_followup_target_hint"])
        self.assertEqual(packet["last_mentioned_entities_or_resources"][0], "How to Actually Use AI in 2026")

    def test_classify_coerces_deep_breakdown_followup_to_resource_lookup(self):
        payload = {
            "intent": "business_ai_advice",
            "route": "ROUTE_2_TASK",
            "question_type": "domain_advice",
            "query_goal": "general",
            "needs_memory": True,
            "needs_corpus": False,
            "needs_web": False,
            "needs_sources": False,
            "is_creator_fact": False,
            "entity_subject": "",
            "query_plan": ["generic ai advice"],
            "response_mode": "answer",
            "source_policy": "none",
            "confidence": 0.72,
            "reason": "Model treated it as generic advice.",
        }
        with patch(
            "backend.services.smart_intent_router.rag.generate_chat_completion",
            return_value=json.dumps(payload),
        ):
            decision = smart_intent_router.classify(
                "give me a deep breakdown, i dont wanna watch the video",
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
                timeout_seconds=1.0,
            )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.query_goal, "resource_lookup")
        self.assertTrue(decision.needs_corpus)
        self.assertTrue(decision.needs_web)
        self.assertTrue(decision.needs_sources)
        self.assertEqual(decision.source_policy, "attach_resource")
        self.assertIn("How to Actually Use AI in 2026", decision.resolved_user_message)
        self.assertEqual(
            decision.query_plan[0],
            'Give me a detailed breakdown of your video "How to Actually Use AI in 2026".',
        )


if __name__ == "__main__":
    unittest.main()
