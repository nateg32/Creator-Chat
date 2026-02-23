
import sys
import os
import json
from unittest.mock import MagicMock, patch

# Setup path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

# Mock db
module_patcher = patch.dict(sys.modules, {
    'db': MagicMock()
})
module_patcher.start()

from backend.services.decision_service import decision_service

def test_context_sufficiency_and_ask_clarify():
    print("\n=== Testing Context Sufficiency & ASK_CLARIFY Mode ===")

    policy = decision_service.DEFAULT_POLICY

    test_cases = [
        {
            "q": "hello",
            "intent": "greeting_only",
            "expect_move": "ASK_CLARIFY",
            "expect_score": 0
        },
        {
            "q": "I need help",
            "intent": "request",
            "expect_move": "ASK_CLARIFY",
            "expect_score": 0
        },
        {
            "q": "how do I grow my business?",
            "intent": "request",
            "expect_move": "ANSWER_DIRECTLY", # Score should be >= 2
            "expect_score_at_least": 2
        },
        {
            "q": "What's the best workout for fat loss if I only have 3 days a week?",
            "intent": "request",
            "expect_move": "ANSWER_DIRECTLY",
            "expect_score_at_least": 2
        }
    ]

    for tc in test_cases:
        print(f"\nQuestion: {tc['q']}")
        q_type, topic, score = decision_service.classify_question(tc['q'], tc['intent'])
        print(f"Detected Type: {q_type}, Score: {score}")
        
        move = decision_service.choose_move(
            policy, q_type, topic, 
            confidence="LOW",
            intent=tc['intent'],
            sufficiency=score
        )
        print(f"Selected Move: {move}")
        
        failed = False
        if "expect_move" in tc and tc["expect_move"] == "ASK_CLARIFY":
            if move != "ASK_CLARIFY":
                print(f"  FAIL: Expected ASK_CLARIFY, got {move}")
                failed = True
        elif score >= 2:
            if move == "ASK_CLARIFY":
                print(f"  FAIL: Got ASK_CLARIFY for high sufficiency question")
                failed = True
        
        if not failed:
            print("  PASS")

if __name__ == "__main__":
    test_context_sufficiency_and_ask_clarify()
    module_patcher.stop()
