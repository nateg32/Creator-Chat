import json
from backend.db import db
from backend.settings import settings
from backend.rag import get_client


def _default_fingerprint() -> dict:
    return {
        "schema_version": 2,
        "traits": [],
        "summary": [],
        "signature_phrases": [],
        "recurring_themes": [],
        "teaching_style": [],
        "rhetorical_moves": [],
        "linguistic_dna": {
            "sentence_structure": "varied",
            "energy": "measured",
            "evidence_style": "hybrid",
            "analogy_style": "light",
            "swearing": "none",
            "emoji": "none",
        },
        "behavioral_patterns": {
            "pressure_response": "",
            "disagreement_handling": "",
            "confidence_level": "medium",
            "decision_style": "",
        },
        "cognitive_style": {
            "depth": "hybrid",
            "abstraction": "hybrid",
            "outlook": "realist",
            "orientation": "hybrid",
        },
        "worldview": {
            "core_beliefs": [],
            "values": [],
            "conceptual_enemies": [],
            "moral_hierarchy": [],
        },
        "audience_and_power": {
            "target_audience": "",
            "dynamic": "hybrid",
        },
        "emotional_signature": {
            "temperature": "hybrid",
            "validation_style": "",
            "praise_frequency": "medium",
        },
        "content_truth": {
            "milestones": [],
            "businesses": [],
            "products": [],
            "named_individuals": [],
            "quantified_claims": [],
        },
        "lexicon": [],
        "evidence_snippets": [],
        "identity_signature": {
            "self_concept": "",
            "mission_frame": "",
            "audience_model": "",
            "power_position": "hybrid",
        },
        "value_hierarchy": [],
        "signature_moves": [],
        "mode_matrix": {
            "greeting": {"opening_move": "", "energy": "", "question_style": "", "forbidden": []},
            "teaching": {"opening_move": "", "proof_style": "", "structure": "", "forbidden": []},
            "comfort": {"opening_move": "", "validation_style": "", "pivot_style": "", "forbidden": []},
            "rebuke": {"opening_move": "", "intensity": "", "boundary_style": "", "forbidden": []},
            "story": {"opening_move": "", "story_shape": "", "lesson_drop": "", "forbidden": []},
            "sales": {"opening_move": "", "trust_mechanism": "", "cta_style": "", "forbidden": []},
            "uncertainty": {"admission_style": "", "what_they_never_say": []},
            "boundary": {"private_life_style": "", "moral_limit_style": "", "forbidden": []},
        },
        "pressure_map": {
            "challenged": "",
            "user_insecure": "",
            "user_needs_conviction": "",
            "user_needs_comfort": "",
            "asked_private_question": "",
            "outside_domain": "",
        },
        "analogy_families": [],
        "lexical_rules": {
            "signature_phrases": [],
            "high_signal_words": [],
            "banned_words": [],
            "banned_frames": [],
            "swearing_level": "none",
        },
        "cadence_rules": {
            "sentence_shape": "balanced",
            "question_rate": 0.2,
            "imperative_rate": 0.2,
            "story_vs_list": "hybrid",
            "pause_markers": [],
        },
        "anti_persona": {
            "sounds_like_someone_else_if": [],
            "forbidden_emotional_postures": [],
            "forbidden_generic_coach_lines": [],
            "confusable_with": [],
        },
        "disambiguation_markers": {
            "must_show": [],
            "must_avoid": [],
            "closest_neighbor_creators": [],
        },
        "golden_examples": {
            "greeting": [],
            "comfort": [],
            "rebuke": [],
            "teaching": [],
            "boundary": [],
            "uncertainty": [],
        },
    }


def _merge_defaults(value, default):
    if isinstance(default, dict):
        merged = {}
        value = value if isinstance(value, dict) else {}
        for key, default_value in default.items():
            merged[key] = _merge_defaults(value.get(key), default_value)
        for key, extra_value in value.items():
            if key not in merged:
                merged[key] = extra_value
        return merged
    if isinstance(default, list):
        return value if isinstance(value, list) else list(default)
    return default if value is None else value


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
            return _default_fingerprint()

        corpus = PersonalityAnalyzer._build_corpus(docs)
        client = get_client()
        name_row = db.execute_one("SELECT name, handle FROM creators WHERE id = %s", (creator_id,))
        display_name = name_row.get("name") or name_row.get("handle") or "The Creator"

        system_prompt = """
You are an elite creator intelligence analyst.
Analyze the provided creator corpus and output a DEEP, contrastive style fingerprint for __CREATOR_NAME__.

RULES:
- Use the creator name exactly as provided: __CREATOR_NAME__.
- Ground every trait in the corpus. Do not invent facts.
- Prefer specificity over generic praise.
- If a fact is uncertain, omit it rather than soften it.
- Capture how the creator thinks, teaches, frames problems, uses evidence, and presents identity.
- Extract facts both from ingested content and from the explicit business/product names mentioned in that content.
- Most important: identify what makes this creator DISTINCTIVE, not just competent.
- Fill anti_persona and disambiguation_markers aggressively. Those fields should explain what would make this creator sound fake.

Return JSON only with this schema:
{{
  "schema_version": 2,
  "traits": ["5-8 concrete trait statements using exact creator name"],
  "summary": ["3-5 dense summary bullets about the creator's identity and thinking"],
  "signature_phrases": ["exact or near-exact repeated phrases"],
  "recurring_themes": ["themes they repeatedly return to"],
  "teaching_style": ["how they teach or persuade"],
  "rhetorical_moves": ["repeatable response moves or sequencing patterns"],
  "linguistic_dna": {
    "sentence_structure": "short|long|varied",
    "energy": "calm|measured|intense|high-energy",
    "evidence_style": "data-driven|story-driven|hybrid",
    "analogy_style": "none|light|heavy",
    "swearing": "none|low|medium|high",
    "emoji": "none|low|high"
  },
  "behavioral_patterns": {
    "pressure_response": "...",
    "disagreement_handling": "...",
    "confidence_level": "low|medium|high",
    "decision_style": "..."
  },
  "cognitive_style": {
    "depth": "big-picture|tactical|hybrid",
    "abstraction": "abstract|concrete|hybrid",
    "outlook": "optimistic|realist|cynical",
    "orientation": "builder|teacher|operator|performer|hybrid"
  },
  "worldview": {
    "core_beliefs": ["..."],
    "values": ["..."],
    "conceptual_enemies": ["..."],
    "moral_hierarchy": ["..."]
  },
  "audience_and_power": {
    "target_audience": "...",
    "dynamic": "mentor|challenger|friend|authority|hybrid"
  },
  "emotional_signature": {
    "temperature": "warm|intense|calm|dry|hybrid",
    "validation_style": "...",
    "praise_frequency": "low|medium|high"
  },
  "content_truth": {
    "milestones": ["specific dates, revenue numbers, timelines, turning points"],
    "businesses": ["company / ministry / brand / channel names"],
    "products": ["specific products / programs / offerings"],
    "named_individuals": ["people explicitly mentioned"],
    "quantified_claims": ["specific numbers with context"]
  },
  "lexicon": ["specific recurring words or concepts"],
  "evidence_snippets": ["3-6 short evidence-backed observations from the corpus"],
  "identity_signature": {
    "self_concept": "who they believe they are",
    "mission_frame": "how they frame their role in the world",
    "audience_model": "how they think about the person they are talking to",
    "power_position": "mentor|challenger|friend|authority|hybrid"
  },
  "value_hierarchy": ["what wins when values conflict"],
  "signature_moves": ["5-10 response patterns they repeat under pressure"],
  "mode_matrix": {
    "greeting": {"opening_move": "...", "energy": "...", "question_style": "...", "forbidden": ["..."]},
    "teaching": {"opening_move": "...", "proof_style": "...", "structure": "...", "forbidden": ["..."]},
    "comfort": {"opening_move": "...", "validation_style": "...", "pivot_style": "...", "forbidden": ["..."]},
    "rebuke": {"opening_move": "...", "intensity": "...", "boundary_style": "...", "forbidden": ["..."]},
    "story": {"opening_move": "...", "story_shape": "...", "lesson_drop": "...", "forbidden": ["..."]},
    "sales": {"opening_move": "...", "trust_mechanism": "...", "cta_style": "...", "forbidden": ["..."]},
    "uncertainty": {"admission_style": "...", "what_they_never_say": ["..."]},
    "boundary": {"private_life_style": "...", "moral_limit_style": "...", "forbidden": ["..."]}
  },
  "pressure_map": {
    "challenged": "how they react when contradicted",
    "user_insecure": "how they respond to fear/shame",
    "user_needs_conviction": "how they confront gently or hard",
    "user_needs_comfort": "how they reassure without losing persona",
    "asked_private_question": "how they guard privacy",
    "outside_domain": "how they pivot without sounding fake"
  },
  "analogy_families": ["domains they naturally pull metaphors from"],
  "lexical_rules": {
    "signature_phrases": ["repeatable lines or fragments"],
    "high_signal_words": ["creator-specific vocabulary"],
    "banned_words": ["words they never naturally use"],
    "banned_frames": ["generic framing they would never use"],
    "swearing_level": "none|low|medium|high"
  },
  "cadence_rules": {
    "sentence_shape": "short_bursts|balanced|flowing",
    "question_rate": 0.0,
    "imperative_rate": 0.0,
    "story_vs_list": "story|list|hybrid",
    "pause_markers": ["...", "--", "?"]
  },
  "anti_persona": {
    "sounds_like_someone_else_if": ["what would make them feel fake"],
    "forbidden_emotional_postures": ["emotional tones that break persona"],
    "forbidden_generic_coach_lines": ["generic lines to avoid"],
    "confusable_with": ["creator archetypes they could be mistaken for"]
  },
  "disambiguation_markers": {
    "must_show": ["signals that make this creator uniquely identifiable"],
    "must_avoid": ["signals that make them sound generic or like another creator"],
    "closest_neighbor_creators": ["adjacent creators or archetypes"]
  },
  "golden_examples": {
    "greeting": ["1-2 short examples"],
    "comfort": ["1-2 short examples"],
    "rebuke": ["1-2 short examples"],
    "teaching": ["1-2 short examples"],
    "boundary": ["1-2 short examples"],
    "uncertainty": ["1-2 short examples"]
  }
}
""".replace("__CREATOR_NAME__", display_name)

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
            fingerprint = _merge_defaults(json.loads(response.choices[0].message.content), _default_fingerprint())
            if not fingerprint.get("value_hierarchy"):
                fingerprint["value_hierarchy"] = list(fingerprint.get("worldview", {}).get("moral_hierarchy", []))
            if not fingerprint.get("signature_moves"):
                fingerprint["signature_moves"] = list(fingerprint.get("rhetorical_moves", []))
            if not fingerprint.get("lexical_rules", {}).get("signature_phrases"):
                fingerprint["lexical_rules"]["signature_phrases"] = list(fingerprint.get("signature_phrases", []))
            if not fingerprint.get("lexical_rules", {}).get("high_signal_words"):
                fingerprint["lexical_rules"]["high_signal_words"] = list(fingerprint.get("lexicon", []))
            if not fingerprint.get("lexical_rules", {}).get("swearing_level"):
                fingerprint["lexical_rules"]["swearing_level"] = fingerprint.get("linguistic_dna", {}).get("swearing", "none")
            if not fingerprint.get("identity_signature", {}).get("power_position"):
                fingerprint["identity_signature"]["power_position"] = fingerprint.get("audience_and_power", {}).get("dynamic", "hybrid")
            db.execute_update(
                "UPDATE creators SET style_fingerprint = %s WHERE id = %s",
                (json.dumps(fingerprint), creator_id),
            )
            print(f"Successfully updated style fingerprint for creator {creator_id}")
            return fingerprint
        except Exception as e:
            print(f"Failed to analyze personality: {e}")
            return _default_fingerprint()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        PersonalityAnalyzer.analyze_creator(int(sys.argv[1]))
    else:
        print("Usage: python personality_analyzer.py <creator_id>")
