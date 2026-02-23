import sys
import os
import json
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.interaction_engine import interaction_engine
from core.interaction_engine import InteractionPlan, VerbosityBudget, GroundingPolicy

def test_interaction_prompt():
    creator_profile = {
        "id": 25,
        "name": "Jordan Welch",
        "handle": "JordanWelch",
        "creator_category": "business",
        "soul_md": "I am a business mentor.",
        "identity_fingerprint": {},
        "style_fingerprint": {
            "traits": ["direct", "helpful"],
            "linguistic_dna": {"swearing": "none", "emoji": "none"}
        }
    }
    
    # Mock search results including a web result
    rag_chunks = [
        {
            "content": "[LIVE WEB SEARCH RESULT] The current price of Ethereum is approximately $2,500.",
            "url": "https://coinmarketcap.com/currencies/ethereum/",
            "title": "Ethereum Price Today",
            "source_ref": {
                "canonical_url": "https://coinmarketcap.com/currencies/ethereum/",
                "title": "Ethereum Price Today",
                "platform": "web"
            }
        }
    ]
    
    plan = InteractionPlan(
        route="ROUTE_2_TASK",
        routing="IN_DOMAIN",
        mode="EXECUTE",
        verbosity_budget=VerbosityBudget(max_lines=12, max_bullets=0),
        grounding=GroundingPolicy(requires_sources=True, video_policy="one_if_helpful")
    )
    
    # Use the internal method to build the prompt for inspection
    prompt = interaction_engine._build_combined_system_prompt(
        creator_profile=creator_profile,
        rag_chunks=rag_chunks,
        creator_id=25,
        user_id=1,
        thread_id="test_thread",
        user_name="Nathan",
        persona=None,
        history=[],
        user_preferences={}
    )
    
    print("--- GENERATED PROMPT PREVIEW ---")
    # Look for the specific sections we changed
    if "Live Web Search (Verified Link)" in prompt:
        print("SUCCESS: Found 'Live Web Search (Verified Link)' in prompt.")
    else:
        print("FAILURE: 'Live Web Search (Verified Link)' NOT found.")
        
    if "Verified Live Web Search Results" in prompt:
        print("SUCCESS: Found 'Verified Live Web Search Results' in PRIORITY 1.")
    else:
        print("FAILURE: 'Verified Live Web Search Results' NOT found in PRIORITY 1.")
        
    if "USE LIVE WEB SEARCH RESULTS" in prompt:
         print("SUCCESS: Found 'USE LIVE WEB SEARCH RESULTS' guardrail.")
    else:
         print("FAILURE: 'USE LIVE WEB SEARCH RESULTS' guardrail NOT found.")

    if "don't have access" in prompt:
        print("FAILURE: 'don't have access' fallback still present!")
    else:
        print("SUCCESS: 'don't have access' fallback removed.")

if __name__ == "__main__":
    test_interaction_prompt()
