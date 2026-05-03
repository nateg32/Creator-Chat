import importlib.util
import unittest
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[1] / "services" / "text_sanitizer.py"
    spec = importlib.util.spec_from_file_location("text_sanitizer", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


text_sanitizer = _load_module()


class TextSanitizerTests(unittest.TestCase):
    def test_removes_compound_hyphens(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("Pick one high-income skill."),
            "Pick one high income skill.",
        )

    def test_replaces_clause_dashes_with_commas(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("Do the work - then raise your price."),
            "Do the work, then raise your price.",
        )

    def test_preserves_numeric_percent_ranges(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("I usually risk 1-3% of the account on a trade."),
            "I usually risk 1-3% of the account on a trade.",
        )

    def test_preserves_numeric_word_ranges(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("That usually takes 3-4 years if you stay consistent."),
            "That usually takes 3-4 years if you stay consistent.",
        )

    def test_preserves_leading_bullets(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("- Keep going"),
            "- Keep going",
        )

    def test_preserves_multiline_bullets_after_colon(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens('Examples that sell:\n- "We book 20 calls."\n- "We revive old leads."'),
            'Examples that sell:\n- "We book 20 calls."\n- "We revive old leads."',
        )

    def test_replaces_tight_em_dash_clauses(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("If prompt engineering does not work in every case—as models can be unpredictable—you can post process it."),
            "If prompt engineering does not work in every case, as models can be unpredictable, you can post process it.",
        )

    def test_preserves_urls(self):
        text = "Use https://anti-gravity-bice.vercel.app or [this link](https://anti-gravity-bice.vercel.app) for approval."
        self.assertEqual(text_sanitizer.strip_mid_sentence_hyphens(text), text)

    def test_streaming_sanitizer_cleans_split_em_dash(self):
        sanitizer = text_sanitizer.StreamingTextSanitizer()
        parts = [
            sanitizer.feed("If prompt engineering does not work in every case"),
            sanitizer.feed("—as models can be unpredictable—you can "),
            sanitizer.feed("post process it."),
            sanitizer.flush(),
        ]
        self.assertEqual(
            "".join(parts),
            "If prompt engineering does not work in every case, as models can be unpredictable, you can post process it.",
        )

    def test_streaming_sanitizer_preserves_chunk_spaces(self):
        sanitizer = text_sanitizer.StreamingTextSanitizer(tail_size=12)
        parts = [
            sanitizer.feed("If you're thinking "),
            sanitizer.feed("about going to "),
            sanitizer.feed("ACCESS, go for the right reason."),
            sanitizer.flush(),
        ]
        self.assertEqual(
            "".join(parts),
            "If you're thinking about going to ACCESS, go for the right reason.",
        )

    def test_inserts_space_before_bible_verse_reference(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("It is built around Matthew28:19, which matters."),
            "It is built around Matthew 28:19, which matters.",
        )

    def test_inserts_space_before_bare_domain(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("1. Check2819Church.org for details."),
            "1. Check 2819Church.org for details.",
        )

    def test_inserts_space_between_word_and_number(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("Send50 messages a day. Call20 businesses a day. Walk in to5 places a day."),
            "Send 50 messages a day. Call 20 businesses a day. Walk in to 5 places a day.",
        )

    def test_inserts_space_before_year(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("We got married in2017."),
            "We got married in 2017.",
        )

    def test_inserts_space_before_frequency_suffix(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("Lift or do hard exercise3x a week."),
            "Lift or do hard exercise 3x a week.",
        )

    def test_inserts_space_before_age_suffix(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("Watch How to disappoint your dad in your20s."),
            "Watch How to disappoint your dad in your 20s.",
        )

    def test_repairs_split_word_fragments(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("Buyer: Yo u tur n long videos into clips."),
            "Buyer: You turn long videos into clips.",
        )

    def test_repairs_line_start_split_word_fragments(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("Perfect.\nB uyer: agencies and coaches"),
            "Perfect.\nBuyer: agencies and coaches",
        )

    def test_repairs_merged_common_word_pairs(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("Are you asking because you want to decide whatyou believe?"),
            "Are you asking because you want to decide what you believe?",
        )

    def test_streaming_sanitizer_inserts_missing_boundary_space(self):
        sanitizer = text_sanitizer.StreamingTextSanitizer(tail_size=12)
        parts = [
            sanitizer.feed("Are you asking because you want to decide what"),
            sanitizer.feed("you believe, or because you're"),
            sanitizer.feed(" wrestling with something right now?"),
            sanitizer.flush(),
        ]
        self.assertEqual(
            "".join(parts),
            "Are you asking because you want to decide what you believe, or because you're wrestling with something right now?",
        )

    def test_repairs_merged_connector_suffix(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("What do you selland what's your current price?"),
            "What do you sell and what's your current price?",
        )

    def test_repairs_split_mean_fragment_after_modal(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("This could me an automating more tasks."),
            "This could mean automating more tasks.",
        )

    def test_preserves_real_word_mean(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("This could mean automating more tasks."),
            "This could mean automating more tasks.",
        )

    def test_preserves_normal_me_an_phrase(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("Give me an example."),
            "Give me an example.",
        )

    def test_repairs_dangling_preposition_sentence_end(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("I'm Alex Hormozi. I'm the founder and managing partner of. I focus on growth."),
            "I'm Alex Hormozi. I'm the founder and managing partner. I focus on growth.",
        )

    def test_preserves_real_words_that_end_with_and(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("The command line matters."),
            "The command line matters.",
        )

    def test_repairs_split_suffix_fragment(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("That just ifies 2 to 10x pricing."),
            "That justifies 2 to 10x pricing.",
        )

    def test_repairs_short_split_suffix_fragment(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens(
                "It is the cleanest step by step blueprint and us ing simple automation tools."
            ),
            "It is the cleanest step by step blueprint and using simple automation tools.",
        )

    def test_repairs_merged_single_letter_heads(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("I'm Dan Martell. Ibuild and Icoach founders."),
            "I'm Dan Martell. I build and I coach founders.",
        )

    def test_does_not_split_real_words_that_start_with_a(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("Amazon is where I sell it."),
            "Amazon is where I sell it.",
        )
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("Alex Hormozi said it clearly."),
            "Alex Hormozi said it clearly.",
        )
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("Alright, let's get into it."),
            "Alright, let's get into it.",
        )

    def test_repairs_merged_common_heads(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("Myfirst real business taught me a lot. Youkeep going."),
            "My first real business taught me a lot. You keep going.",
        )

    def test_repairs_merged_trailing_common_words(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("What kind of businessare you trying to start?"),
            "What kind of business are you trying to start?",
        )
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("A free trialwill just create support load."),
            "A free trial will just create support load.",
        )
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens(
                "Designingyour weeks around your highest leverage activities matters."
            ),
            "Designing your weeks around your highest leverage activities matters.",
        )

    def test_repairs_split_prefix_with_merged_suffix(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens(
                "If you tell me what you do right now, I'll tra nslatethe main framework into a plan."
            ),
            "If you tell me what you do right now, I'll translate the main framework into a plan.",
        )

    def test_repairs_contraction_boundary_with_merged_word(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("I'lltranslate that into a plan."),
            "I'll translate that into a plan.",
        )

    def test_inserts_missing_space_after_sentence_punctuation(self):
        self.assertEqual(
            text_sanitizer.strip_mid_sentence_hyphens("1 Offer.1 customer type.1 acquisition channel."),
            "1 Offer. 1 customer type. 1 acquisition channel.",
        )

    def test_strips_youtube_id_fragments_when_card_exists(self):
        self.assertEqual(
            text_sanitizer.strip_card_attachment_artifacts(
                "I attached both below.\nnp YUmc\nns RU",
                [{"url": "https://youtu.be/npYUmcnsRU"}],
            ),
            "I attached it below.",
        )

    def test_strips_single_line_youtube_id_fragments_when_card_exists(self):
        self.assertEqual(
            text_sanitizer.strip_card_attachment_artifacts(
                "Iattached it below.\nAYfwX 4 bkY",
                [{"url": "https://youtu.be/AYfwX4bkY"}],
            ),
            "I attached it below.",
        )

    def test_strips_partial_youtube_id_fragments_when_card_exists(self):
        self.assertEqual(
            text_sanitizer.strip_card_attachment_artifacts(
                "Here it is, attached below\nxi W9h M",
                [{"url": "https://youtu.be/AAxiW9hMbb1"}],
            ),
            "Here it is, attached below",
        )

    def test_rewrites_plural_attachment_language_when_only_one_card_exists(self):
        self.assertEqual(
            text_sanitizer.strip_card_attachment_artifacts(
                "I attached both below.",
                [{"url": "https://youtu.be/AAxiW9hMbb1"}],
            ),
            "I attached it below.",
        )

    def test_finalize_generated_text_accepts_generic_model_spacing_fix(self):
        original = text_sanitizer._run_final_spacing_cleanup_model
        text_sanitizer._run_final_spacing_cleanup_model = lambda text: "That feels conversational and natural."
        try:
            self.assertEqual(
                text_sanitizer.finalize_generated_text("That feels convers ational and natural."),
                "That feels conversational and natural.",
            )
        finally:
            text_sanitizer._run_final_spacing_cleanup_model = original

    def test_finalize_generated_text_rejects_rewrite(self):
        original = text_sanitizer._run_final_spacing_cleanup_model
        text_sanitizer._run_final_spacing_cleanup_model = lambda text: "This is a total rewrite with different words."
        try:
            self.assertEqual(
                text_sanitizer.finalize_generated_text("That feels convers ational and natural."),
                "That feels convers ational and natural.",
            )
        finally:
            text_sanitizer._run_final_spacing_cleanup_model = original


if __name__ == "__main__":
    unittest.main()
