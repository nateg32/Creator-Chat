import json
from db import db
from settings import settings
from rag import get_client

class PersonalityAnalyzer:
    """
    Algorithm to extract a Creator's Style Fingerprint from their content.
    This creates an authentic, personified identity for any creator.
    """
    
    @staticmethod
    def analyze_creator(creator_id: int):
        print(f"[IDENTITY] Re-analyzing fingerprint for creator {creator_id}...")
        # 1. Sample content (get 5-10 diverse transcripts/captions)
        query = """
            SELECT content, metadata
            FROM documents
            WHERE creator_id = %s AND source != 'persona'
            LIMIT 10
        """
        docs = db.execute_query(query, (creator_id,))
        if not docs:
            print(f"[IDENTITY] No content found (outside persona) for creator {creator_id}. Cannot analyze.")
            return {"traits": [], "tone_intensity": "low", "impact": "neutral", "mechanical": "none", "lexicon": []}
        
        # Combine snippets for analysis
        corpus = "\n---\n".join([d['content'][:1000] for d in docs])
        
        system_prompt = """
        You are a master linguistic and psychological profiler. Analyze the following content and extract a 'Style Fingerprint' that reverse-engineers the creator's brain.
        
        EXTRACT STATEMENTS FOR:
        1. LINGUISTIC DNA: (N-grams, sentence length, rhetorical questions, swearing level, emoji usage, analogies/metaphors, statistics vs stories).
        2. BEHAVIORAL PATTERNS: (Response to pressure/disagreement, escalation style, emotional baseline, confidence level, absolutes vs hedging).
        3. COGNITIVE STYLE: (Big-picture vs tactical, data-driven vs anecdotal, abstract vs concrete, philosophical vs practical, optimism vs cynicism).
        4. RHETORICAL MOVES: (Signature ways of speaking: "truth bombs", analogies, tough love, "steps-based" teaching, call-out vs reframe).
        5. WORLDVIEW & VALUES: (Origin story elements, core beliefs, values, "enemies" conceptually, what they believe is right/wrong with the world).
        6. HUMOR PROFILE: (Type: sarcasm, deadpan, absurdist, mockery, etc.; Usage: tension relief vs dominance; Frequency).
        7. AUDIENCE & POWER: (Perceived audience: beginners, hustlers, etc.; Power dynamic: mentor, challenger, friend, authority).
        8. EMOTIONAL SIGNATURE: (Temperature: warm/intense/calm; Validation style; Praise frequency).
        
        CONTENT-TRUTH MINING (CRITICAL):
        Search for ANY mentions of:
        - Specific numbers (revenue, debt, costs, followers).
        - Specific dates or timelines (years, months, "first year").
        - Specific proper names (business names, product names, software used, people mentioned).
        - Specific winning products or "winners" they describe.

        Output ONLY a JSON object:
        {
            "linguistic_dna": {
                "swearing": "none|low|frequent",
                "emoji": "none|low|high",
                "humor": "description",
                "sentence_structure": "short|long|varied",
                "n_grams": ["phrase1", "phrase2"],
                "opening_closing": "description"
            },
            "behavioral_patterns": {
                "pressure_response": "description",
                "disagreement_handling": "mock|educate|dismiss|escalate",
                "confidence_level": "low|medium|high",
                "hedging_vs_absolutes": "description"
            },
            "cognitive_style": {
                "depth": "big-picture|tactical",
                "evidence": "data-driven|anecdotal",
                "outlook": "optimistic|cynical"
            },
            "rhetorical_moves": ["analogy", "tough love", "..."],
            "worldview": {
                "core_beliefs": ["..."],
                "values": ["..."],
                "conceptual_enemies": ["..."],
                "moral_hierarchy": ["..."]
            },
            "humor_profile": {
                "type": "...",
                "usage": "..."
            },
            "audience_and_power": {
                "target_audience": "...",
                "dynamic": "mentor|challenger|friend|authority"
            },
            "emotional_signature": {
                "temperature": "warm|intense|calm|sarcastic",
                "validation": "..."
            },
            "content_truth": {
                "milestones": ["$1,000 student loan", "2017 start"],
                "businesses": ["Pluto Deals"],
                "products": ["Fidget spinners"],
                "named_individuals": []
            },
            "lexicon": ["words"],
            "traits": ["{Name} is...", "{Name} loves to..."]
        }
        """
        
        try:
            client = get_client()
            # Fetch creator name for statements
            name_row = db.execute_one("SELECT name, handle FROM creators WHERE id = %s", (creator_id,))
            display_name = name_row.get("name") or name_row.get("handle") or "The Creator"

            response = client.chat.completions.create(
                model=settings.CHAT_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt.replace("{Name}", display_name)},
                    {"role": "user", "content": f"Content Samples:\n{corpus}"}
                ],
                response_format={ "type": "json_object" },
                temperature=0.3
            )
            
            fingerprint = json.loads(response.choices[0].message.content)
            
            # 2. Update the database
            db.execute_update(
                "UPDATE creators SET style_fingerprint = %s WHERE id = %s",
                (json.dumps(fingerprint), creator_id)
            )
            print(f"Successfully updated style fingerprint for creator {creator_id}")
            return fingerprint
            
        except Exception as e:
            print(f"Failed to analyze personality: {e}")
            return None

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        PersonalityAnalyzer.analyze_creator(int(sys.argv[1]))
    else:
        print("Usage: python personality_analyzer.py <creator_id>")
