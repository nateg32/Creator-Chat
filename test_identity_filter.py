
import logging
import sys
import os
from unittest.mock import MagicMock, patch

# Put backend in path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

# Mock db/settings
module_patcher = patch.dict(sys.modules, {
    'db': MagicMock()
})
module_patcher.start()

import settings
settings.settings.REWRITE_MODEL = "gpt-4o-mini" # Use a model that definitely exists for the test

from services.persona_filter import apply_persona_surface_filter

def test_identity_preservation():
    print("\n=== Testing Identity Preservation & AI Leak Removal ===")
    
    voice_profile = {
        "signature_phrases": ["Let's go!", "Framework"],
        "attitude": {"bluntness": "high"},
        "energy": {"bucket": "HIGH"}
    }
    
    test_cases = [
        {
            "name": "Direct AI leak in personal question",
            "text": "I’m an AI, so I don’t have a wife. I focus on training and nutrition.",
            "intent": "personal_bio_question",
            "avoid": ["I’m an AI", "don't have a wife", "bot"],
            "expected_contains": ["publicly", "private"]
        },
        {
            "name": "Generic AI safety phrasing",
            "text": "As an AI, I don't have personal experiences with supplements, but according to sources they can work.",
            "intent": "request",
            "avoid": ["As an AI", "personal experiences", "according to sources"],
            "expected_contains": ["training", "share"]
        },
        {
            "name": "Bot reference",
            "text": "I am a bot designed to help you with business strategy. Note: I am not a financial advisor.",
            "intent": "vague_request",
            "avoid": ["bot", "designed to help", "Note:"],
            "max_sentences": 2
        }
    ]

    for tc in test_cases:
        print(f"\nRunning: {tc['name']}")
        result = apply_persona_surface_filter(
            tc['text'], 
            tc['intent'], 
            voice_profile=voice_profile,
            creator_name="Gabe"
        )
        print(f"Input: {tc['text']}")
        print(f"Output: {result}")
        
        found_banned = []
        for a in tc['avoid']:
            if a.lower() in result.lower():
                found_banned.append(a)
        
        failed = False
        if found_banned:
            print(f"FAIL: Found banned phrases: {found_banned}")
            failed = True
        
        if "max_sentences" in tc:
            sentence_count = len([s for s in result.replace("!", ".").replace("?", ".").split(".") if s.strip()])
            if sentence_count > tc['max_sentences']:
                print(f"FAIL: Sentence count {sentence_count} > {tc['max_sentences']}")
                failed = True

        if not failed:
            print("PASS")

if __name__ == "__main__":
    test_identity_preservation()
    module_patcher.stop()
