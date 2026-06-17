from backend.services.emotional_intelligence import detect_message_vibe, format_vibe_prompt_block


def test_detects_overwhelmed_vibe_without_llm():
    vibe = detect_message_vibe("honestly im overwhelmed and dont know where to start")

    assert vibe["primary"] == "overwhelmed"
    assert vibe["vibe"] == "vulnerable"
    assert "manageable" in format_vibe_prompt_block(vibe)


def test_detects_frustration_and_directness():
    vibe = detect_message_vibe("this is still broken, just tell me the fix")

    assert vibe["primary"] == "frustrated"
    assert vibe["directness_requested"] is True
    prompt = format_vibe_prompt_block(vibe)
    assert "Do not sound defensive" in prompt


def test_neutral_vibe_does_not_force_fake_empathy():
    vibe = detect_message_vibe("what should i do first")

    assert vibe["primary"] == "neutral"
    assert "Do not add fake empathy" in format_vibe_prompt_block(vibe)
