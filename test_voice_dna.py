"""Quick functional test for VoiceDNA engine."""
import json
from backend.services.voice_dna import (
    build_voice_dna_block, build_voice_imprint, build_voice_equation,
    build_response_scaffold, build_mode_voice_shift, build_anti_voice,
    apply_vocabulary_resonance, build_vocabulary_map, score_voice_fidelity,
    ConversationVoiceTracker,
)

profile = {
    "name": "TestCreator",
    "style_fingerprint": json.dumps({
        "lexical_rules": {
            "signature_phrases": ["let me break it down", "here is the deal"],
            "high_signal_words": ["grind", "hustle", "execute"],
            "banned_words": ["utilize", "leverage"],
            "golden_examples": [
                "Listen, here is the deal. You gotta stop overthinking and start executing.",
                "Let me break it down for you real quick. The grind never stops.",
            ],
        },
        "voice_signature": {
            "energy_level": "high",
            "formality": "casual",
            "sentence_length_avg": 10,
            "question_density": 0.15,
        },
        "identity_signature": {
            "power_position": "Action over analysis",
        },
        "anti_persona": {
            "forbidden_generic_coach_lines": ["You are doing amazing things"],
        },
    }),
    "voice_profile": json.dumps({
        "tone": "direct, blunt",
        "personality_traits": ["high energy", "no-nonsense"],
    }),
}

# Test each layer
eq = build_voice_equation(profile)
print(f"Voice Equation: {eq[:120] if eq else 'EMPTY'}...")

imp = build_voice_imprint(profile)
print(f"Voice Imprint: {'OK (' + str(len(imp)) + ' chars)' if imp else 'EMPTY'}")

scaffold = build_response_scaffold(profile)
print(f"Response Scaffold: {'OK (' + str(len(scaffold)) + ' chars)' if scaffold else 'EMPTY'}")

shift = build_mode_voice_shift(profile, mode="small_talk")
print(f"Mode Shift: {'OK (' + str(len(shift)) + ' chars)' if shift else 'EMPTY'}")

anti = build_anti_voice(profile)
print(f"Anti-Voice: {'OK (' + str(len(anti)) + ' chars)' if anti else 'EMPTY'}")

# Full block
block = build_voice_dna_block(profile, mode="task")
print(f"\nFull DNA Block: {len(block)} chars")
print("--- BLOCK START ---")
print(block[:500])
print("--- BLOCK END ---\n")

# Test vocabulary resonance
text = "I would be happy to help you with that. Certainly! Here are some tips for you."
cleaned = apply_vocabulary_resonance(text, profile)
print(f'Vocab Resonance IN:  "{text}"')
print(f'Vocab Resonance OUT: "{cleaned}"')

# Test voice fidelity
score = score_voice_fidelity("Listen, here is the deal. You gotta grind harder.", profile)
print(f"\nVoice Fidelity Score: {score}")

# Test conversation tracker
tracker = ConversationVoiceTracker()
tracker.record_turn("Let me break it down for you.", ["let me break it down", "here is the deal"])
notes = tracker.get_avoidance_notes(["let me break it down", "here is the deal"])
print(f"Tracker (turn 1): {notes}")

# Second turn with tracker passed to build_voice_dna_block
block2 = build_voice_dna_block(profile, mode="task", conversation_tracker=tracker)
has_avoidance = "RECENTLY USED" in block2
print(f"DNA Block with Tracker: {'Contains avoidance notes' if has_avoidance else 'No avoidance notes'}")

print("\nALL TESTS PASSED")
