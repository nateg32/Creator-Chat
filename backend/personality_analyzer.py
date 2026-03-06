import json
from backend.db import db
from backend.settings import settings
from backend.rag import get_client


class PersonalityAnalyzer:
    """
    Extract a richer style fingerprint from ingested creator content.
    """

    @staticmethod
    def _load_corpus(creator_id: int):
        query = """
            SELECT content, metadata, source, source_id, title
            FROM documents
            WHERE creator_id = %s AND source != 'persona'
            ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
            LIMIT 24
        """
        return db.execute_query(query, (creator_id,))

    @staticmethod
    def _build_corpus(docs):
        samples = []
        for idx, doc in enumerate(docs, start=1):
            metadata = doc.get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}

            title = doc.get("title") or metadata.get("title") or doc.get("source") or f"Sample {idx}"
            platform = metadata.get("platform") or doc.get("source") or "unknown"
            content = (doc.get("content") or "").strip()
            if not content:
                continue
            excerpt = content[:1800]
            samples.append(f"[Sample {idx}] {title} | platform={platform}\n{excerpt}")
        return "\n\n---\n\n".join(samples)

    @staticmethod
    def analyze_creator(creator_id: int):
        print(f"[IDENTITY] Re-analyzing fingerprint for creator {creator_id}...")
        docs = PersonalityAnalyzer._load_corpus(creator_id)
        if not docs:
            print(f"[IDENTITY] No content found (outside persona) for creator {creator_id}. Cannot analyze.")
            return {
                "traits": [],
                "summary": [],
                "signature_phrases": [],
                "recurring_themes": [],
                "teaching_style": [],
                "tone_intensity": "low",
                "impact": "neutral",
                "mechanical": "none",
                "lexicon": [],
                "content_truth": {},
                "evidence_snippets": [],
            }

        corpus = PersonalityAnalyzer._build_corpus(docs)
        client = get_client()
        name_row = db.execute_one("SELECT name, handle FROM creators WHERE id = %s", (creator_id,))
        display_name = name_row.get("name") or name_row.get("handle") or "The Creator"

        system_prompt = f"""
You are an elite creator intelligence analyst.
Analyze the provided creator corpus and output a DEEP style fingerprint for {display_name}.

RULES:
- Use the creator name exactly as provided: {display_name}
- Ground every trait in the corpus. Do not invent facts.
- Prefer specificity over generic praise.
- If a fact is uncertain, omit it rather than soften it.
- Capture how the creator thinks, teaches, frames problems, uses evidence, and presents identity.
- Extract facts both from ingested content and from the explicit business/product names mentioned in that content.

Return JSON only with this schema:
{{
  "traits": ["5-8 concrete trait statements using exact creator name"],
  "summary": ["3-5 dense summary bullets about the creator's identity and thinking"],
  "signature_phrases": ["exact or near-exact repeated phrases"],
  "recurring_themes": ["themes they repeatedly return to"],
  "teaching_style": ["how they teach or persuade"],
  "linguistic_dna": {{
    "sentence_structure": "short|long|varied",
    "energy": "calm|measured|intense|high-energy",
    "evidence_style": "data-driven|story-driven|hybrid",
    "analogy_style": "none|light|heavy",
    "swearing": "none|low|medium|high",
    "emoji": "none|low|high"
  }},
  "behavioral_patterns": {{
    "pressure_response": "...",
    "disagreement_handling": "...",
    "confidence_level": "low|medium|high",
    "decision_style": "..."
  }},
  "cognitive_style": {{
    "depth": "big-picture|tactical|hybrid",
    "abstraction": "abstract|concrete|hybrid",
    "outlook": "optimistic|realist|cynical",
    "orientation": "builder|teacher|operator|performer|hybrid"
  }},
  "worldview": {{
    "core_beliefs": ["..."],
    "values": ["..."],
    "conceptual_enemies": ["..."],
    "moral_hierarchy": ["..."]
  }},
  "audience_and_power": {{
    "target_audience": "...",
    "dynamic": "mentor|challenger|friend|authority|hybrid"
  }},
  "emotional_signature": {{
    "temperature": "warm|intense|calm|dry|hybrid",
    "validation_style": "...",
    "praise_frequency": "low|medium|high"
  }},
  "content_truth": {{
    "milestones": ["specific dates, revenue numbers, timelines, turning points"],
    "businesses": ["company / ministry / brand / channel names"],
    "products": ["specific products / programs / offerings"],
    "named_individuals": ["people explicitly mentioned"],
    "quantified_claims": ["specific numbers with context"]
  }},
  "lexicon": ["specific recurring words or concepts"],
  "evidence_snippets": ["3-6 short evidence-backed observations from the corpus"]
}}
"""

        try:
            response = client.chat.completions.create(
                model=settings.MODEL_CLASSIFICATION,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Creator Corpus:\n{corpus}"},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            fingerprint = json.loads(response.choices[0].message.content)
            db.execute_update(
                "UPDATE creators SET style_fingerprint = %s WHERE id = %s",
                (json.dumps(fingerprint), creator_id),
            )
            print(f"Successfully updated style fingerprint for creator {creator_id}")
            return fingerprint
        except Exception as e:
            print(f"Failed to analyze personality: {e}")
            return {
                "traits": [],
                "summary": [],
                "signature_phrases": [],
                "recurring_themes": [],
                "teaching_style": [],
                "tone_intensity": "low",
                "impact": "neutral",
                "mechanical": "none",
                "lexicon": [],
                "content_truth": {},
                "evidence_snippets": [],
            }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        PersonalityAnalyzer.analyze_creator(int(sys.argv[1]))
    else:
        print("Usage: python personality_analyzer.py <creator_id>")
