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
            print(f"No content found for creator {creator_id} to analyze.")
            return
        
        # Combine snippets for analysis
        corpus = "\n---\n".join([d['content'][:1000] for d in docs])
        
        system_prompt = """
        You are a master linguistic profiler. Analyze the following content and extract a 'Style Fingerprint' for the author.
        
        Focus on:
        1. MECHANICAL VOICE: (Punctuation habits, sentence length, capitalization quirks, use of emojis).
        2. IMPACT MODE: (How do they provide value? Coaching, Educating, Comedic Reframing, Blunt Challenge, Motivation?).
        3. QUESTION PROFILE: (Do they ask questions often? What style? Opener or Closer?).
        4. LEXICON: (Specific signature words, slang, or frameworks they use).
        5. TONE: (Intensity, warmth, irony, or authoritative levels).
        6. GREETING & HOOKS: (How do they typically start a video or thought? Do they use "So...", "What's up", "Listen...", etc?).
        7. SMALL TALK STYLE: (How do they handle casual interaction? Are they blunt and dismissive of fluff, or warm and welcoming?).
        
        Output ONLY a JSON object representing this fingerprint.
        """
        
        try:
            client = get_client()
            response = client.chat.completions.create(
                model=settings.CHAT_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
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
