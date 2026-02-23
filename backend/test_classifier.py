"""Quick test for classifier with word-boundary-safe matching."""

GREETING_WORDS = {"hello", "hi", "hey", "yo", "sup"}
REACTIVE_WORDS = {"lol", "haha", "true", "wow", "ok", "okay", "yeah", "yea", "ya", "bet", "nice", "cool"}
TASK_VERBS = {"help", "explain", "how to", "what is", "what are", "i want to", "i need", "i dont know", "i don't know"}
EMOTION_WORDS = {"tired", "stressed", "bored", "hyped", "excited", "confused", "lost", "overwhelmed"}
SMALL_TALK_PHRASES = {"wyd", "how are you", "how's it going"}

def phrase_in_msg(phrase_set, text, word_list):
    word_set = set(word_list)
    for phrase in phrase_set:
        if " " not in phrase:
            if phrase in word_set:
                return True
        else:
            if phrase in text:
                return True
    return False

def test_classify(msg, history):
    msg_lower = msg.strip().lower()
    words = msg_lower.split()
    word_count = len(words)

    is_social = msg_lower in GREETING_WORDS or phrase_in_msg(GREETING_WORDS, msg_lower, words)
    is_reactive = msg_lower in REACTIVE_WORDS or (word_count <= 3 and any(w in REACTIVE_WORDS for w in words))
    is_emotional = phrase_in_msg(EMOTION_WORDS, msg_lower, words)
    is_small_talk_phrase = phrase_in_msg(SMALL_TALK_PHRASES, msg_lower, words)
    has_task_verb = phrase_in_msg(TASK_VERBS, msg_lower, words)

    # CONVERSATION CONTINUATION (before all other checks)
    if history and not is_reactive and not is_social:
        last_msg = None
        for m in reversed(history):
            if m.get("role") == "assistant":
                last_msg = m
                break
        if last_msg and "?" in last_msg.get("content", ""):
            return "TASK_CONTINUATION"

    has_question_mark = "?" in msg
    specificity = word_count / 15.0

    if is_social and not has_task_verb and word_count <= 4 and not has_question_mark:
        return "GREETING"
    if has_task_verb or has_question_mark or specificity >= 0.4:
        return "TASK"
    if is_reactive or is_emotional or is_small_talk_phrase or (word_count <= 4 and not has_task_verb):
        return "SMALL_TALK"
    return "TASK"

h_q = [{"role": "assistant", "content": "What kind of business are you thinking?"}]
h_none = []

with open("test_output.txt", "w") as f:
    f.write("WITHOUT HISTORY:\n")
    for msg in ["yo", "hello", "hi", "im thinking fitness", "not sure yet",
                "i want to start trading", "what are the different markets"]:
        f.write(f"  {msg:40s} => {test_classify(msg, h_none)}\n")

    f.write("\nWITH QUESTION IN HISTORY:\n")
    for msg in ["im thinking fitness", "probably stocks", "not sure yet", "fitness",
                "lol", "yo", "hey", "yeah", "ok", "not sure"]:
        f.write(f"  {msg:40s} => {test_classify(msg, h_q)}\n")

    f.write("\nSUBSTRING BUG TESTS:\n")
    f.write(f"  {'thinking (should NOT be greeting)':40s} => {test_classify('thinking', h_none)}\n")
    f.write(f"  {'im thinking (should NOT be greeting)':40s} => {test_classify('im thinking', h_none)}\n")
    f.write(f"  {'hi (SHOULD be greeting)':40s} => {test_classify('hi', h_none)}\n")
    f.write(f"  {'hi there (SHOULD be greeting)':40s} => {test_classify('hi there', h_none)}\n")

print("Done")
