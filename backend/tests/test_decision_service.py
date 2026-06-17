import importlib.util
import unittest
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[1] / "services" / "decision_service.py"
    spec = importlib.util.spec_from_file_location("decision_service", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


decision_service_module = _load_module()


class DecisionServiceTests(unittest.TestCase):
    def test_general_personal_bio_defaults_to_public_ok(self):
        service = decision_service_module.DecisionService()
        move = service.choose_move(
            service.DEFAULT_POLICY,
            question_type="personal_bio",
            topic="general",
            confidence="LOW",
            intent="personal_bio_question",
            sufficiency=2,
        )
        self.assertEqual(move, "ANSWER_WITH_QUALIFIER")

    def test_relationship_question_stays_private_without_public_fact_route(self):
        service = decision_service_module.DecisionService()
        move = service.choose_move(
            service.DEFAULT_POLICY,
            question_type="personal_bio",
            topic="relationship",
            confidence="HIGH",
            intent="personal_bio_question",
            sufficiency=2,
        )
        self.assertEqual(move, "DECLINE_PRIVATE")

    def test_who_is_your_wife_is_creator_personal_fact(self):
        service = decision_service_module.DecisionService()
        q_type, topic, _ = service.classify_question("wait whos your wife", "unknown")
        self.assertEqual(q_type, "personal_bio")
        self.assertEqual(topic, "relationship")

    def test_misus_slang_is_creator_relationship_fact(self):
        service = decision_service_module.DecisionService()
        q_type, topic, _ = service.classify_question("do you have a misus", "unknown")
        self.assertEqual(q_type, "personal_bio")
        self.assertEqual(topic, "relationship")

    def test_book_question_classifies_as_general_topic(self):
        service = decision_service_module.DecisionService()
        q_type, topic, _ = service.classify_question("When did you write your book?", "personal_bio_question")
        self.assertEqual(q_type, "personal_bio")
        self.assertEqual(topic, "general")

    def test_public_wealth_journey_is_not_private_finance(self):
        service = decision_service_module.DecisionService()
        q_type, topic, _ = service.classify_question("how did u get rich?", "personal_bio_question")
        self.assertEqual(q_type, "personal_bio")
        self.assertEqual(topic, "general")
        self.assertFalse(service.is_creator_personal_fact_question("how did u get rich?"))

    def test_resolves_affirmative_book_followup_from_clarification(self):
        service = decision_service_module.DecisionService()
        resolved = service.resolve_followup_question(
            "yes",
            [
                {"role": "user", "content": "when was your book launch"},
                {"role": "assistant", "content": "If you tell me which book you mean, I can help. Which one, Buy Back Your Time?"},
            ],
        )
        self.assertEqual(resolved, "When was Buy Back Your Time launched?")

    def test_does_not_rewrite_affirmation_without_book_context(self):
        service = decision_service_module.DecisionService()
        resolved = service.resolve_followup_question(
            "yes",
            [
                {"role": "user", "content": "can you help me with pricing"},
                {"role": "assistant", "content": "Do you want me to break down pricing for software?"},
            ],
        )
        self.assertEqual(resolved, "yes")

    def test_resolves_pronoun_book_followup_from_recent_title(self):
        service = decision_service_module.DecisionService()
        resolved = service.resolve_followup_question(
            "when did u write it?",
            [
                {"role": "user", "content": "do you have a book?"},
                {"role": "assistant", "content": "Yeah. I wrote a book called Buy Back Your Time."},
            ],
        )
        self.assertEqual(resolved, "When did you write Buy Back Your Time?")

    def test_resolves_pronoun_followup_from_sentence_style_book_title(self):
        service = decision_service_module.DecisionService()
        resolved = service.resolve_followup_question(
            "when did u write it?",
            [
                {"role": "user", "content": "what about 100m money models?"},
                {"role": "assistant", "content": "Yeah, that's mine too. $100M Money Models is the third book I put out in the $100M series."},
            ],
        )
        self.assertEqual(resolved, "When did you write $100M Money Models?")

    def test_resolves_focusless_creator_start_followup_from_recent_subject(self):
        service = decision_service_module.DecisionService()
        resolved = service.resolve_followup_question(
            "when did u start",
            [
                {"role": "user", "content": "yo"},
                {"role": "assistant", "content": "yo what's up"},
                {"role": "user", "content": "why did u start trading?"},
                {"role": "assistant", "content": "I got into trading because I wanted leverage."},
            ],
        )
        self.assertEqual(resolved, "When did you start trading?")

    def test_resolves_subject_swap_followup_from_focusless_previous_question(self):
        service = decision_service_module.DecisionService()
        resolved = service.resolve_followup_question(
            "what about trading",
            [
                {"role": "user", "content": "when did u start"},
                {"role": "assistant", "content": "I started Tjr in 2017."},
            ],
        )
        self.assertEqual(resolved, "When did you start trading?")

    def test_resolves_assistant_introduced_project_anchor(self):
        service = decision_service_module.DecisionService()
        resolved = service.resolve_followup_question(
            "What was the latest wreck?",
            [
                {"role": "user", "content": "Rate my looks"},
                {
                    "role": "assistant",
                    "content": (
                        "I'm usually more of a judge of panel gaps than outfits. "
                        "If you want to see a real transformation though, you should see "
                        "the state of the latest wreck we just pulled into the shop."
                    ),
                },
            ],
        )
        self.assertEqual(resolved, "What was the latest wreck you just pulled into the shop?")

    def test_resolves_declarative_assistant_anchor_clarification(self):
        service = decision_service_module.DecisionService()
        resolved = service.resolve_followup_question(
            "You were talking about the latest wreck you pulled into the shop",
            [
                {"role": "user", "content": "What was the latest wreck?"},
                {
                    "role": "assistant",
                    "content": "The latest wreck we just pulled into the shop is the one worth checking next.",
                },
            ],
        )
        self.assertEqual(resolved, "Tell me more about the latest wreck you just pulled into the shop.")

    def test_resolves_turn_it_around_to_life_turning_point(self):
        service = decision_service_module.DecisionService()
        resolved = service.resolve_followup_question(
            "what made u turn it around",
            [
                {"role": "user", "content": "your story"},
                {
                    "role": "assistant",
                    "content": (
                        "My journey started in a dark place, going from a convict involved with stolen cars "
                        "to finding my path in technology and entrepreneurship. I built my wealth by becoming "
                        "a 5 time SaaS entrepreneur and turning my life around."
                    ),
                },
            ],
        )
        self.assertEqual(resolved, "What made you turn your life around?")

    def test_resolves_link_followup_to_prior_named_resource(self):
        service = decision_service_module.DecisionService()
        resolved = service.resolve_followup_question(
            "can u link the video",
            [
                {"role": "user", "content": "do you have a misus?"},
                {
                    "role": "assistant",
                    "content": (
                        "Yes, I am married to Leila Hormozi. I've shared the story on "
                        "various platforms, including an episode of The Kim Constable Podcast "
                        "where I talk about our early days."
                    ),
                },
            ],
        )
        self.assertIn("The Kim Constable Podcast", resolved)
        self.assertIn("link", resolved.lower())
        self.assertNotIn("the video", resolved.lower())

    def test_partner_business_question_stays_domain_advice(self):
        service = decision_service_module.DecisionService()
        q_type, topic, _ = service.classify_question(
            "what would u reccomend if i have a partner who doesnt like my business?",
            "unknown",
        )
        self.assertEqual(q_type, "domain_advice")
        self.assertEqual(topic, "general")
        self.assertTrue(
            service.is_user_relationship_business_question(
                "what would u reccomend if i have a partner who doesnt like my business?"
            )
        )

    def test_marketing_agency_does_not_trigger_age_topic(self):
        service = decision_service_module.DecisionService()
        q_type, topic, _ = service.classify_question(
            "ive been getting leads but i dont know how to convert them, i run a marketing agency",
            "request",
        )
        self.assertEqual(q_type, "domain_advice")
        self.assertEqual(topic, "general")

    def test_domain_advice_move_is_deterministic_answer_directly(self):
        service = decision_service_module.DecisionService()
        noisy_policy = {"answer_style": {"legacy_random_knob": 1.0}}
        move = service.choose_move(
            noisy_policy,
            question_type="domain_advice",
            topic="general",
            confidence="LOW",
            intent="request",
            sufficiency=3,
        )
        self.assertEqual(move, "ANSWER_DIRECTLY")

    def test_creator_family_question_still_counts_as_personal(self):
        service = decision_service_module.DecisionService()
        q_type, topic, _ = service.classify_question("yo do you have a family?", "unknown")
        self.assertEqual(q_type, "personal_bio")
        self.assertEqual(topic, "family")

    def test_private_religion_question_declines_without_public_fact_guess(self):
        service = decision_service_module.DecisionService()
        q_type, topic, _ = service.classify_question("do u believe in God", "unknown")
        self.assertEqual(q_type, "personal_bio")
        self.assertEqual(topic, "politics_religion")
        self.assertEqual(
            service.choose_move(
                service.DEFAULT_POLICY,
                question_type=q_type,
                topic=topic,
                confidence="HIGH",
                intent="personal_bio_question",
                sufficiency=2,
            ),
            "DECLINE_PRIVATE",
        )

    def test_private_body_question_gets_boundary_move(self):
        service = decision_service_module.DecisionService()
        q_type, topic, _ = service.classify_question("how big is your dick", "unknown")
        self.assertEqual(q_type, "private_sensitive")
        self.assertEqual(
            service.choose_move(
                service.DEFAULT_POLICY,
                question_type=q_type,
                topic=topic,
                confidence="HIGH",
                intent="personal_bio_question",
                sufficiency=2,
            ),
            "BOUNDARY_PUSHBACK",
        )

    def test_resolves_wdym_as_contextual_clarification(self):
        service = decision_service_module.DecisionService()
        resolved = service.resolve_followup_question(
            "wdymean?",
            [
                {
                    "role": "user",
                    "content": "what would u reccomend if i have a partner who doesnt like my business?",
                },
                {
                    "role": "assistant",
                    "content": "I keep that side of my life private.",
                },
            ],
        )
        self.assertIn("clarify what you meant", resolved.lower())
        self.assertIn("partner who doesnt like my business", resolved.lower())

    def test_resolves_deep_breakdown_followup_to_recent_video_card(self):
        service = decision_service_module.DecisionService()
        resolved = service.resolve_followup_question(
            "give me a deep breakdown, i dont wanna watch the video",
            [
                {
                    "role": "user",
                    "content": "what do u talk about how to use ai in 2026?",
                },
                {
                    "role": "assistant",
                    "content": "I attached the video below if you want the full thing.",
                    "cards": [
                        {
                            "title": "How to Actually Use AI in 2026",
                            "url": "https://youtube.com/watch?v=abc",
                        }
                    ],
                },
            ],
        )
        self.assertEqual(
            resolved,
            'Give me a detailed breakdown of your video "How to Actually Use AI in 2026".',
        )


if __name__ == "__main__":
    unittest.main()
