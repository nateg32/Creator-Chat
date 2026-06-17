from backend.app import question_refers_to_recent_image


def test_text_only_software_question_does_not_reuse_recent_image():
    assert not question_refers_to_recent_image("what are the fundamentals of software engineering")


def test_the_does_not_match_he_pronoun():
    assert not question_refers_to_recent_image("what are the best fundamentals")


def test_visual_followup_reuses_recent_image():
    assert question_refers_to_recent_image("what do you think of this setup right here?")


def test_explicit_image_question_reuses_recent_image():
    assert question_refers_to_recent_image("can you analyze the image above?")
