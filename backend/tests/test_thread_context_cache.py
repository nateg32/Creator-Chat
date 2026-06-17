from backend.services.thread_context_cache import ThreadContextCache


def _cache() -> ThreadContextCache:
    cache = ThreadContextCache()
    cache.redis_url = ""
    cache._redis_failed = True
    cache.clear_local()
    return cache


def _support_chunk():
    return {
        "content": "Beginner soccer gym plan: two full body sessions with squats, hinges, presses, and sprint recovery.",
        "title": "Hybrid Soccer Strength Plan",
        "url": "https://example.com/soccer-strength",
        "source_ref": {
            "title": "Hybrid Soccer Strength Plan",
            "canonical_url": "https://example.com/soccer-strength",
            "platform": "youtube",
            "content_type": "video",
        },
    }


def _video_chunk():
    return {
        "content": "In this video I explain how AI becomes operating infrastructure for business workflows in 2026.",
        "title": "How to Actually Use AI in 2026",
        "url": "https://youtube.com/watch?v=abc",
        "source_ref": {
            "title": "How to Actually Use AI in 2026",
            "canonical_url": "https://youtube.com/watch?v=abc",
            "platform": "youtube",
            "content_type": "video",
        },
    }


def test_reuses_short_term_context_for_followup_question():
    cache = _cache()
    saved = cache.save_context(
        user_id=1,
        creator_id=40,
        thread_id="thread-a",
        question="I play soccer and want to start the gym",
        support_set=[_support_chunk()],
    )

    hit = cache.get_reusable_context(
        user_id=1,
        creator_id=40,
        thread_id="thread-a",
        question="what should i start with",
        conversation_history=[{"role": "user", "content": "I play soccer and want to start the gym"}],
    )

    assert saved is True
    assert hit is not None
    assert hit["support_set"][0]["source_ref"]["canonical_url"] == "https://example.com/soccer-strength"


def test_topic_change_clears_short_term_context():
    cache = _cache()
    cache.save_context(
        user_id=1,
        creator_id=40,
        thread_id="thread-a",
        question="I play soccer and want to start the gym",
        support_set=[_support_chunk()],
    )

    miss = cache.get_reusable_context(
        user_id=1,
        creator_id=40,
        thread_id="thread-a",
        question="how do I sell software to real estate agents",
        conversation_history=[],
    )
    second_miss = cache.get_reusable_context(
        user_id=1,
        creator_id=40,
        thread_id="thread-a",
        question="what should i start with",
        conversation_history=[],
    )

    assert miss is None
    assert second_miss is None


def test_reuses_links_for_resource_followup():
    cache = _cache()
    cache.save_context(
        user_id=1,
        creator_id=40,
        thread_id="thread-a",
        question="what should i start with for soccer gym",
        support_set=[_support_chunk()],
    )

    hit = cache.get_reusable_context(
        user_id=1,
        creator_id=40,
        thread_id="thread-a",
        question="send me the link",
        conversation_history=[{"role": "assistant", "content": "Start with this soccer strength plan."}],
    )

    assert hit is not None
    assert hit["_reuse_reason"] == "followup_resource_request"


def test_reuses_context_for_video_breakdown_followup():
    cache = _cache()
    cache.save_context(
        user_id=1,
        creator_id=40,
        thread_id="thread-a",
        question="what do u talk about how to use ai in 2026?",
        support_set=[_video_chunk()],
    )

    hit = cache.get_reusable_context(
        user_id=1,
        creator_id=40,
        thread_id="thread-a",
        question="give me a deep breakdown, i dont wanna watch the video",
        conversation_history=[
            {
                "role": "assistant",
                "content": "I attached the video below.",
                "cards": [{"title": "How to Actually Use AI in 2026"}],
            }
        ],
    )

    assert hit is not None
    assert hit["_reuse_reason"] == "followup_resource_request"
    assert hit["support_set"][0]["source_ref"]["title"] == "How to Actually Use AI in 2026"


def test_freshness_question_bypasses_cache():
    cache = _cache()
    cache.save_context(
        user_id=1,
        creator_id=40,
        thread_id="thread-a",
        question="what is your business firm called",
        support_set=[_support_chunk()],
    )

    hit = cache.get_reusable_context(
        user_id=1,
        creator_id=40,
        thread_id="thread-a",
        question="what is your latest business firm right now",
        conversation_history=[],
    )

    assert hit is None
