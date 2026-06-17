import importlib

import backend as backend_pkg


app_module = importlib.import_module("backend.app")
setattr(backend_pkg, "app", app_module)

_empty_stream_answer_fallback = app_module._empty_stream_answer_fallback
_fallback_thread_title_from_messages = app_module._fallback_thread_title_from_messages
_looks_like_incomplete_visible_answer = app_module._looks_like_incomplete_visible_answer
_is_weak_auto_thread_title = app_module._is_weak_auto_thread_title
_parse_thread_title_response = app_module._parse_thread_title_response
_repair_metadata_biography = app_module._repair_metadata_biography
_title_copies_message_fragment = app_module._title_copies_message_fragment
grounded_rag_module = importlib.import_module("backend.grounded_rag")
interaction_engine_module = importlib.import_module("backend.core.interaction_engine")


def test_thread_title_detects_greeting_titles_as_weak():
    assert _is_weak_auto_thread_title("Good Have You Here Nathan")
    assert _is_weak_auto_thread_title("Yo Alex")
    assert _is_weak_auto_thread_title("Whats Up Nathan")
    assert _is_weak_auto_thread_title("Starting the Conversation")
    assert _is_weak_auto_thread_title("Here is the JSON")
    assert _is_weak_auto_thread_title("Here is the")
    assert _is_weak_auto_thread_title("Who Are")
    assert _is_weak_auto_thread_title("Who Are You")
    assert _is_weak_auto_thread_title("Goodmorning Woke")
    assert _is_weak_auto_thread_title("Misus Did She One")
    assert not _is_weak_auto_thread_title("Starting a Business")


def test_metadata_bio_repair_recovers_greeting_as_greeting():
    answer, repaired = _repair_metadata_biography("I was published in 2024.", "hello")

    assert repaired is True
    assert answer == "Hey. What's on your mind?"


def test_metadata_bio_repair_uses_greeting_repair_fallback():
    answer, repaired = _repair_metadata_biography(
        "I was published in 2024.",
        "hello",
        greeting_fallback="Hey Nathan. What's on your mind?",
    )

    assert repaired is True
    assert answer == "Hey Nathan. What's on your mind?"


def test_metadata_bio_repair_recovers_reactive_clarification_as_chat():
    answer, repaired = _repair_metadata_biography("I was published in 2024.", "huh?")

    assert repaired is True
    assert "what part threw you off" in answer.lower()
    assert "metadata" not in answer.lower()


def test_empty_stream_fallback_recovers_greeting_as_greeting(monkeypatch):
    monkeypatch.setattr(app_module.db, "execute_one", lambda *args, **kwargs: {"name": "Alex Hormozi"})

    assert _empty_stream_answer_fallback(1, 1, "hello") == "Hey. What's on your mind?"


def test_empty_stream_fallback_recovers_reactive_chat_as_chat(monkeypatch):
    monkeypatch.setattr(app_module.db, "execute_one", lambda *args, **kwargs: {"name": "Alex Hormozi"})

    answer = _empty_stream_answer_fallback(1, 1, "huh?")

    assert "what part threw you off" in answer.lower()
    assert "smallest next step" not in answer.lower()


def test_incomplete_reply_guards_catch_dangling_want_fragment():
    fragment = "Yo Nathan. Want"

    assert _looks_like_incomplete_visible_answer(fragment)
    assert grounded_rag_module._looks_like_truncated_stream_answer(fragment)
    assert interaction_engine_module._looks_like_incomplete_visible_reply(fragment)


def test_incomplete_reply_guard_catches_partial_second_sentence():
    assert _looks_like_incomplete_visible_answer("Most of what you've been")
    assert _looks_like_incomplete_visible_answer("garbage. If you're trading every")


def test_stream_salvages_completed_prefix_without_retry():
    raw = (
        "Most people are chasing the wrong things when they talk about hitting that seven figure mark. "
        "They think it's about a lucky shot, like hitting a buzzer beater w"
    )

    assert grounded_rag_module._complete_stream_prefix(raw) == (
        "Most people are chasing the wrong things when they talk about hitting that seven figure mark."
    )
    assert grounded_rag_module._complete_stream_prefix("garbage. If you're trading every") == ""


def test_thread_title_fallback_ignores_auto_welcome_and_titles_user_intent():
    title = _fallback_thread_title_from_messages([
        {"role": "assistant", "content": "Good to have you here Nathan. What's on your mind?"},
        {"role": "user", "content": "i was thinking of starting a business"},
        {"role": "assistant", "content": "Here is the constraint: start with a problem and customer."},
    ])

    assert title == "Starting a Business"


def test_thread_title_fallback_skips_small_talk_until_real_topic():
    assert _fallback_thread_title_from_messages([
        {"role": "user", "content": "Yo Alex"},
        {"role": "assistant", "content": "What's up?"},
    ]) == ""


def test_thread_title_fallback_summarizes_appreciation():
    title = _fallback_thread_title_from_messages([
        {"role": "user", "content": "i just wanna say, i love you"},
        {"role": "assistant", "content": "I appreciate that."},
    ])

    assert title == "Personal Appreciation"


def test_thread_title_fallback_detects_business_sale_timeline():
    title = _fallback_thread_title_from_messages([
        {"role": "user", "content": "when did u sell your business?"},
        {"role": "assistant", "content": "I sold a majority stake in Gym Launch later in my journey."},
    ])

    assert title == "Business Sale Timeline"


def test_thread_title_fallback_detects_creator_background_question():
    title = _fallback_thread_title_from_messages([
        {"role": "user", "content": "who are you, whats your story like how did u get rich"},
        {"role": "assistant", "content": "I opened my first gym and then scaled Gym Launch."},
    ])

    assert title == "Creator Background"


def test_thread_title_fallback_summarizes_simple_identity_question():
    title = _fallback_thread_title_from_messages([
        {"role": "user", "content": "who are you"},
        {"role": "assistant", "content": "I talk about my work and the lessons I've shared."},
    ])

    assert title == "Creator Background"


def test_thread_title_fallback_summarizes_relationship_question():
    title = _fallback_thread_title_from_messages([
        {"role": "user", "content": "do you have a misus"},
        {"role": "assistant", "content": "I am married to Leila Hormozi."},
    ])

    assert title == "Creator Relationship"


def test_thread_title_fallback_summarizes_partner_advice():
    title = _fallback_thread_title_from_messages([
        {"role": "user", "content": "how do i find a partner like leila"},
        {"role": "assistant", "content": "Selection matters more than training."},
    ])

    assert title == "Finding a Partner"


def test_thread_title_fallback_rejects_pure_goodmorning_chat():
    title = _fallback_thread_title_from_messages([
        {"role": "user", "content": "goodmorning woke"},
        {"role": "assistant", "content": "Morning. What's on your mind?"},
    ])

    assert title == ""


def test_thread_title_response_parses_json_title():
    assert _parse_thread_title_response('{"title":"Gym Launch Sale Timeline"}') == "Gym Launch Sale Timeline"


def test_thread_title_response_rejects_json_wrapper_without_title():
    assert _parse_thread_title_response("Here is the JSON") == ""


def test_thread_title_response_rejects_phrase_echo_identity_title():
    assert _parse_thread_title_response('{"title":"Who Are You"}') == ""


def test_thread_title_response_rejects_awful_short_generated_titles():
    assert _parse_thread_title_response('{"title":"Goodmorning Woke"}') == ""
    assert _parse_thread_title_response('{"title":"Misus Did She One"}') == ""


def test_thread_title_response_parses_wrapped_json_title():
    raw = 'Here is the JSON:\n\n{"title":"Marketing Agency Lead Conversion"}'
    assert _parse_thread_title_response(raw) == "Marketing Agency Lead Conversion"


def test_thread_title_fallback_titles_agency_conversion_context():
    title = _fallback_thread_title_from_messages([
        {"role": "user", "content": "ive been struggling to convert leads for my business, i run a marketing agency"},
        {"role": "assistant", "content": "Here is the JSON"},
        {"role": "user", "content": "im using a sales script"},
    ])

    assert title == "Lead Conversion"


def test_thread_title_fallback_summarizes_image_feedback():
    title = _fallback_thread_title_from_messages([
        {"role": "user", "content": "describe image and point out anything"},
    ])

    assert title == "Image Feedback"


def test_thread_title_fallback_summarizes_car_buying_advice():
    title = _fallback_thread_title_from_messages([
        {"role": "user", "content": "yo if i wanted to buy a car, what would u reccomend?"},
    ])

    assert title == "Car Buying Advice"


def test_thread_title_fallback_summarizes_getting_into_cars():
    title = _fallback_thread_title_from_messages([
        {"role": "user", "content": "how did you get yourself into cars?"},
    ])

    assert title == "Getting Into Cars"


def test_thread_title_fallback_summarizes_latest_wreck_context():
    title = _fallback_thread_title_from_messages([
        {"role": "assistant", "content": "You should see the state of the latest wreck we just pulled into the shop."},
        {"role": "user", "content": "What was the latest wreck?"},
    ])

    assert title == "Latest Car Project"


def test_thread_title_rejects_cleaned_user_phrase_titles():
    assert _is_weak_auto_thread_title("If Wanted Buy Car Reccomend")
    assert _is_weak_auto_thread_title("Describe Image Point Out Anything")
    assert _is_weak_auto_thread_title("Yourself Into Cars")


def test_thread_title_allows_specific_topic_overlap():
    msgs = [
        {"role": "user", "content": "im trying to start a business, i just dont know where to start"},
        {"role": "assistant", "content": "Start with a specific problem and customer."},
    ]

    assert not _title_copies_message_fragment("Starting a Business", msgs)
