
import json
import logging
from typing import Dict, Any
from grounded_rag import grounded_rag_ask
from services.conversation_state_manager import ConversationStateManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("PipelineTest")

# Mock Database Row for Creator
MOCK_CREATOR = {
    "id": 1,
    "name": "Expert Trader",
    "handle": "@experttrader",
    "persona": "Professional, analytical, focuses on psychology and risk management.",
    "stronghold_json": {
        "allowed_domains": ["trading", "psychology", "risk management", "markets"],
        "forbidden_topics": ["politics", "medical advice", "cooking"]
    },
    "rhythm_profile_json": {
        "dm_chunk_style": "two_block",
        "avg_sentence_words": 12,
        "connector_avoidance": ["therefore", "moreover"]
    }
}

async def test_scenario(name: str, question: str, creator_id: int):
    print(f"\n=== Running Scenario: {name} ===")
    print(f"User: {question}")
    
    # We need a conversation_id
    conversation_id = f"test_conv_{name.lower().replace(' ', '_')}"
    
    # Run the pipeline
    try:
        response = grounded_rag_ask(
            creator_id=creator_id,
            thread_id=conversation_id,
            question=question
        )
        
        print(f"Assistant: {response.get('answer')}")
        print(f"Meta: {json.dumps(response.get('meta'), indent=2)}")
        
        # Check if we have rhythm shaping applied (no "therefore", max paragraphs)
        answer = response.get('answer', '')
        if "therefore" in answer.lower():
            print("FAIL: Found forbidden connector 'therefore'")
        if answer.count('\n\n') > 2:
            print("FAIL: More than 3 paragraphs detected")
            
        return response
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

async def run_all_tests():
    # 1. Beginner Confusion (should see steering for guidance)
    await test_scenario("Beginner Confusion", "I don't understand how to start trading. It's too complex.", 26)
    
    # 2. Stronghold Boundary (should decline)
    await test_scenario("Stronghold Boundary", "What do you think about the current political situation?", 26)
    
    # 3. Personal Question (should use web verify)
    await test_scenario("Personal/Factual", "What is the capital of France and what is your favorite color?", 26)
    
    # 4. Anti-Repetition (run twice)
    await test_scenario("Anti-Repetition 1", "How do I manage risk?", 26)
    await test_scenario("Anti-Repetition 2", "Tell me more about risk management.", 26)

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_all_tests())
