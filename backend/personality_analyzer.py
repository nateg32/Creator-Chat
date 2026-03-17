import json

from backend.db import db
from backend.rag import get_client
from backend.settings import settings


def _default_fingerprint() -> dict:
    return {
        "schema_version": 3,
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
            "public_role": "",
            "private_boundary_style": "",
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
            "debate": {"opening_move": "", "friction_style": "", "evidence_posture": "", "forbidden": []},
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
        "belief_graph": {
            "core_beliefs": [],
            "value_hierarchy": [],
            "non_negotiables": [],
            "tension_points": [],
            "beliefs_they_attack": [],
            "beliefs_they_protect": [],
        },
        "domain_map": {
            "creator_lane": "",
            "strong_topics": [],
            "adjacent_topics": [],
            "weak_topics": [],
            "unsafe_topics": [],
        },
        "value_model": {
            "core_values": [],
            "tradeoff_preferences": [],
            "rejections": [],
            "decision_heuristics": [],
        },
        "reasoning_profile": {
            "framework_vs_story": "balanced",
            "premise_challenge_rate": "medium",
            "action_bias": "medium",
            "proof_style": "hybrid",
            "emotional_vs_analytical": "balanced",
            "default_problem_solving_pattern": [],
        },
        "unknown_topic_policy": {
            "allow_identity_fallback": True,
            "disclosure_threshold": 0.45,
            "max_assertiveness": 0.65,
            "boundary_style": "",
            "never_infer": [
                "exact facts without evidence",
                "private life",
                "personal history not grounded in content",
                "medical, legal, or financial claims without support",
            ],
        },
        "story_bank": [],
        "pressure_engine": {
            "challenged": {},
            "user_insecure": {},
            "user_ashamed": {},
            "user_flirty": {},
            "user_grieving": {},
            "user_confused": {},
            "user_needs_action": {},
            "user_needs_comfort": {},
            "asked_private_question": {},
            "outside_domain": {},
        },
        "speech_mechanics": {
            "sentence_shape": "balanced",
            "question_density": 0.2,
            "imperative_density": 0.2,
            "analogy_domains": [],
            "signature_openings": [],
            "signature_landings": [],
            "humor_profile": "light",
            "cadence_markers": [],
            "punctuation_rules": [],
        },
        "signature_response_moves": [],
        "contrastive_identity": {
            "nearest_neighbor_creators": [],
            "confusion_risks": [],
            "must_show": [],
            "must_avoid": [],
            "anti_persona": [],
        },
        "temporal_voice": {
            "eras": [],
            "current_voice_vs_old_voice": [],
            "stable_traits": [],
            "drift_signals": [],
        },
        "knowledge_boundaries": {
            "confirmed_public_facts": [],
            "inferred_only": [],
            "private_or_unknown": [],
            "must_verify_topics": [],
        },
        "golden_replies": {
            "teaching": [],
            "comfort": [],
            "rebuke": [],
            "boundary": [],
            "sales": [],
        },
        "scoring": {
            "identity_confidence": 0.5,
            "belief_confidence": 0.5,
            "mode_confidence": 0.5,
            "distinctiveness_score": 0.5,
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


def _backfill_v3_fields(fingerprint: dict) -> dict:
    worldview = fingerprint.get("worldview") or {}
    identity = fingerprint.get("identity_signature") or {}
    audience = fingerprint.get("audience_and_power") or {}
    cadence = fingerprint.get("cadence_rules") or {}
    linguistic = fingerprint.get("linguistic_dna") or {}
    anti = fingerprint.get("anti_persona") or {}
    markers = fingerprint.get("disambiguation_markers") or {}
    content_truth = fingerprint.get("content_truth") or {}
    pressure_map = fingerprint.get("pressure_map") or {}

    if not fingerprint.get("value_hierarchy"):
        fingerprint["value_hierarchy"] = list(worldview.get("moral_hierarchy") or [])
    if not fingerprint.get("signature_moves"):
        fingerprint["signature_moves"] = list(fingerprint.get("rhetorical_moves") or [])
    if not fingerprint.get("signature_response_moves"):
        fingerprint["signature_response_moves"] = list(fingerprint.get("signature_moves") or fingerprint.get("rhetorical_moves") or [])

    lexical_rules = fingerprint.get("lexical_rules") or {}
    if not lexical_rules.get("signature_phrases"):
        lexical_rules["signature_phrases"] = list(fingerprint.get("signature_phrases") or [])
    if not lexical_rules.get("high_signal_words"):
        lexical_rules["high_signal_words"] = list(fingerprint.get("lexicon") or [])
    if not lexical_rules.get("swearing_level"):
        lexical_rules["swearing_level"] = linguistic.get("swearing", "none")
    fingerprint["lexical_rules"] = lexical_rules

    if not identity.get("power_position"):
        identity["power_position"] = audience.get("dynamic", "hybrid")
    if not identity.get("audience_model"):
        identity["audience_model"] = audience.get("target_audience", "")
    fingerprint["identity_signature"] = identity

    belief_graph = fingerprint.get("belief_graph") or {}
    if not belief_graph.get("core_beliefs"):
        belief_graph["core_beliefs"] = list(worldview.get("core_beliefs") or [])
    if not belief_graph.get("value_hierarchy"):
        belief_graph["value_hierarchy"] = list(fingerprint.get("value_hierarchy") or worldview.get("moral_hierarchy") or [])
    if not belief_graph.get("beliefs_they_attack"):
        belief_graph["beliefs_they_attack"] = list(worldview.get("conceptual_enemies") or [])
    fingerprint["belief_graph"] = belief_graph

    domain_map = fingerprint.get("domain_map") or {}
    if not domain_map.get("creator_lane"):
        domain_map["creator_lane"] = ", ".join((fingerprint.get("recurring_themes") or [])[:2])
    if not domain_map.get("strong_topics"):
        domain_map["strong_topics"] = list(fingerprint.get("recurring_themes") or [])[:8]
    if not domain_map.get("unsafe_topics"):
        domain_map["unsafe_topics"] = list((fingerprint.get("knowledge_boundaries") or {}).get("must_verify_topics") or [])
    fingerprint["domain_map"] = domain_map

    value_model = fingerprint.get("value_model") or {}
    if not value_model.get("core_values"):
        value_model["core_values"] = list(worldview.get("values") or fingerprint.get("value_hierarchy") or worldview.get("moral_hierarchy") or [])
    if not value_model.get("rejections"):
        value_model["rejections"] = list(worldview.get("conceptual_enemies") or belief_graph.get("beliefs_they_attack") or [])
    if not value_model.get("decision_heuristics"):
        value_model["decision_heuristics"] = list(fingerprint.get("signature_moves") or fingerprint.get("rhetorical_moves") or [])[:8]
    fingerprint["value_model"] = value_model

    reasoning_profile = fingerprint.get("reasoning_profile") or {}
    if not reasoning_profile.get("framework_vs_story"):
        reasoning_profile["framework_vs_story"] = cadence.get("story_vs_list", "balanced")
    if not reasoning_profile.get("proof_style"):
        reasoning_profile["proof_style"] = fingerprint.get("mode_matrix", {}).get("teaching", {}).get("proof_style") or linguistic.get("evidence_style", "hybrid")
    if not reasoning_profile.get("action_bias"):
        reasoning_profile["action_bias"] = fingerprint.get("behavioral_patterns", {}).get("decision_style") or "medium"
    if not reasoning_profile.get("emotional_vs_analytical"):
        temperature = (fingerprint.get("emotional_signature") or {}).get("temperature", "hybrid")
        reasoning_profile["emotional_vs_analytical"] = "emotional" if temperature in {"warm", "hot"} else "balanced"
    if not reasoning_profile.get("default_problem_solving_pattern"):
        reasoning_profile["default_problem_solving_pattern"] = list(fingerprint.get("signature_response_moves") or fingerprint.get("signature_moves") or [])[:6]
    fingerprint["reasoning_profile"] = reasoning_profile

    speech_mechanics = fingerprint.get("speech_mechanics") or {}
    if not speech_mechanics.get("sentence_shape"):
        speech_mechanics["sentence_shape"] = cadence.get("sentence_shape") or linguistic.get("sentence_structure") or "balanced"
    if not speech_mechanics.get("question_density"):
        speech_mechanics["question_density"] = cadence.get("question_rate", 0.2)
    if not speech_mechanics.get("imperative_density"):
        speech_mechanics["imperative_density"] = cadence.get("imperative_rate", 0.2)
    if not speech_mechanics.get("analogy_domains"):
        speech_mechanics["analogy_domains"] = list(fingerprint.get("analogy_families") or [])
    if not speech_mechanics.get("cadence_markers"):
        speech_mechanics["cadence_markers"] = list(cadence.get("pause_markers") or [])
    fingerprint["speech_mechanics"] = speech_mechanics

    pressure_engine = fingerprint.get("pressure_engine") or {}
    for key, text in pressure_map.items():
        if text and not pressure_engine.get(key):
            pressure_engine[key] = {
                "default_move": text,
                "tone_shift": "stay in character",
                "goal": text,
                "forbidden": [],
            }
    fingerprint["pressure_engine"] = pressure_engine

    contrastive = fingerprint.get("contrastive_identity") or {}
    if not contrastive.get("nearest_neighbor_creators"):
        contrastive["nearest_neighbor_creators"] = list(markers.get("closest_neighbor_creators") or anti.get("confusable_with") or [])
    if not contrastive.get("must_show"):
        contrastive["must_show"] = list(markers.get("must_show") or [])
    if not contrastive.get("must_avoid"):
        contrastive["must_avoid"] = list(markers.get("must_avoid") or [])
    if not contrastive.get("anti_persona"):
        contrastive["anti_persona"] = list(anti.get("sounds_like_someone_else_if") or [])
    fingerprint["contrastive_identity"] = contrastive

    knowledge_boundaries = fingerprint.get("knowledge_boundaries") or {}
    if not knowledge_boundaries.get("confirmed_public_facts"):
        confirmed = []
        confirmed.extend(content_truth.get("milestones") or [])
        confirmed.extend(content_truth.get("businesses") or [])
        confirmed.extend(content_truth.get("products") or [])
        knowledge_boundaries["confirmed_public_facts"] = confirmed[:12]
    if not knowledge_boundaries.get("must_verify_topics"):
        knowledge_boundaries["must_verify_topics"] = ["age", "net worth", "family", "private life"]
    fingerprint["knowledge_boundaries"] = knowledge_boundaries

    unknown_topic_policy = fingerprint.get("unknown_topic_policy") or {}
    if "allow_identity_fallback" not in unknown_topic_policy:
        unknown_topic_policy["allow_identity_fallback"] = True
    if not isinstance(unknown_topic_policy.get("disclosure_threshold"), (int, float)):
        unknown_topic_policy["disclosure_threshold"] = 0.45
    if not isinstance(unknown_topic_policy.get("max_assertiveness"), (int, float)):
        unknown_topic_policy["max_assertiveness"] = 0.65
    if not unknown_topic_policy.get("boundary_style"):
        unknown_topic_policy["boundary_style"] = fingerprint.get("mode_matrix", {}).get("boundary", {}).get("private_life_style") or identity.get("private_boundary_style") or ""
    if not unknown_topic_policy.get("never_infer"):
        unknown_topic_policy["never_infer"] = list(knowledge_boundaries.get("private_or_unknown") or []) + list(knowledge_boundaries.get("must_verify_topics") or [])
    fingerprint["unknown_topic_policy"] = unknown_topic_policy

    temporal_voice = fingerprint.get("temporal_voice") or {}
    if not temporal_voice.get("stable_traits"):
        temporal_voice["stable_traits"] = list(fingerprint.get("traits") or [])[:5]
    fingerprint["temporal_voice"] = temporal_voice

    story_bank = fingerprint.get("story_bank") or []
    normalized_stories = []
    for idx, story in enumerate(story_bank, start=1):
        if not isinstance(story, dict):
            continue
        normalized_stories.append({
            "story_id": story.get("story_id") or f"story_{idx}",
            "title": story.get("title") or f"Story {idx}",
            "era": story.get("era") or "current",
            "trigger_topics": list(story.get("trigger_topics") or []),
            "summary": story.get("summary") or "",
            "lesson": story.get("lesson") or "",
            "emotion": story.get("emotion") or "measured",
            "proof_type": story.get("proof_type") or "lived_experience",
            "source_refs": list(story.get("source_refs") or []),
            "confidence": story.get("confidence") if isinstance(story.get("confidence"), (int, float)) else 0.5,
        })
    fingerprint["story_bank"] = normalized_stories

    golden_replies = fingerprint.get("golden_replies") or {}
    golden_examples = fingerprint.get("golden_examples") or {}
    for key in ("teaching", "comfort", "rebuke", "boundary"):
        if not golden_replies.get(key):
            golden_replies[key] = list(golden_examples.get(key) or [])
    if not golden_replies.get("sales"):
        golden_replies["sales"] = list(golden_examples.get("teaching") or [])[:2]
    fingerprint["golden_replies"] = golden_replies

    scoring = fingerprint.get("scoring") or {}
    for key in ("identity_confidence", "belief_confidence", "mode_confidence", "distinctiveness_score"):
        if not isinstance(scoring.get(key), (int, float)):
            scoring[key] = 0.5
    fingerprint["scoring"] = scoring

    fingerprint["schema_version"] = 3
    return fingerprint


class PersonalityAnalyzer:
    """Extract a deeper style fingerprint from ingested creator content."""

    @staticmethod
    def _load_corpus(creator_id: int):
        query = """
            SELECT content, metadata, source, source_id, title
            FROM documents
            WHERE creator_id = %s AND source != 'persona'
            ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
            LIMIT 32
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
            published = metadata.get("published_at") or metadata.get("publishedAt") or ""
            content = (doc.get("content") or "").strip()
            if not content:
                continue
            excerpt = content[:2200]
            header = f"[Sample {idx}] {title} | platform={platform}"
            if published:
                header += f" | published={published}"
            samples.append(f"{header}\n{excerpt}")
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
- Most important: identify what makes this creator DISTINCTIVE, not just competent.
- Extract stories they repeat, pressures that change their tone, and beliefs they defend or attack.
- Infer a practical domain map, value model, reasoning profile, and unknown-topic policy from recurring patterns in the corpus.
- Fill contrastive_identity, anti_persona, and disambiguation_markers aggressively.
- Preserve the older fields for compatibility, but prioritize the v3 fields.

Return JSON only with this schema:
{
  "schema_version": 3,
  "traits": ["5-8 concrete trait statements using exact creator name"],
  "summary": ["3-5 dense summary bullets about identity and thinking"],
  "signature_phrases": ["exact or near-exact repeated phrases"],
  "recurring_themes": ["themes they repeatedly return to"],
  "teaching_style": ["how they teach or persuade"],
  "rhetorical_moves": ["repeatable response moves or sequencing patterns"],
  "identity_signature": {
    "self_concept": "",
    "mission_frame": "",
    "audience_model": "",
    "power_position": "mentor|challenger|friend|authority|hybrid",
    "public_role": "",
    "private_boundary_style": ""
  },
  "belief_graph": {
    "core_beliefs": [""],
    "value_hierarchy": [""],
    "non_negotiables": [""],
    "tension_points": ["where they contain real contradiction or evolution"],
    "beliefs_they_attack": [""],
    "beliefs_they_protect": [""]
  },
  "domain_map": {
    "creator_lane": "short phrase for their main lane",
    "strong_topics": ["topics they can answer with high confidence"],
    "adjacent_topics": ["topics they can discuss through worldview and reasoning"],
    "weak_topics": ["topics they touch lightly"],
    "unsafe_topics": ["topics where inference should usually stop"]
  },
  "value_model": {
    "core_values": [""],
    "tradeoff_preferences": ["what they would prioritize over what"],
    "rejections": ["ideas, habits, or mindsets they reject"],
    "decision_heuristics": ["repeatable rules they use to make decisions"]
  },
  "reasoning_profile": {
    "framework_vs_story": "framework|story|balanced",
    "premise_challenge_rate": "low|medium|high",
    "action_bias": "low|medium|high",
    "proof_style": "anecdotal|analytical|hybrid",
    "emotional_vs_analytical": "emotional|balanced|analytical",
    "default_problem_solving_pattern": ["how they usually structure an answer or solve a problem"]
  },
  "unknown_topic_policy": {
    "allow_identity_fallback": true,
    "disclosure_threshold": 0.0,
    "max_assertiveness": 0.0,
    "boundary_style": "how they should set limits on unsupported topics",
    "never_infer": ["facts or stance categories that should never be guessed"]
  },
  "story_bank": [
    {
      "story_id": "short id",
      "title": "canonical short title",
      "era": "old|current|timeless",
      "trigger_topics": ["topics that should retrieve this story"],
      "summary": "story summary",
      "lesson": "lesson they extract from it",
      "emotion": "tone of the story",
      "proof_type": "lived_experience|client_result|warning|origin_story|failure_story",
      "source_refs": ["sample ids or titles"],
      "confidence": 0.0
    }
  ],
  "mode_matrix": {
    "greeting": {"opening_move": "", "energy": "", "question_style": "", "forbidden": []},
    "teaching": {"opening_move": "", "proof_style": "", "structure": "", "forbidden": []},
    "comfort": {"opening_move": "", "validation_style": "", "pivot_style": "", "forbidden": []},
    "rebuke": {"opening_move": "", "intensity": "", "boundary_style": "", "forbidden": []},
    "story": {"opening_move": "", "story_shape": "", "lesson_drop": "", "forbidden": []},
    "sales": {"opening_move": "", "trust_mechanism": "", "cta_style": "", "forbidden": []},
    "debate": {"opening_move": "", "friction_style": "", "evidence_posture": "", "forbidden": []},
    "uncertainty": {"admission_style": "", "what_they_never_say": []},
    "boundary": {"private_life_style": "", "moral_limit_style": "", "forbidden": []}
  },
  "pressure_engine": {
    "challenged": {"default_move": "", "tone_shift": "", "goal": "", "forbidden": []},
    "user_insecure": {"default_move": "", "tone_shift": "", "goal": "", "forbidden": []},
    "user_ashamed": {"default_move": "", "tone_shift": "", "goal": "", "forbidden": []},
    "user_flirty": {"default_move": "", "tone_shift": "", "goal": "", "forbidden": []},
    "user_grieving": {"default_move": "", "tone_shift": "", "goal": "", "forbidden": []},
    "user_confused": {"default_move": "", "tone_shift": "", "goal": "", "forbidden": []},
    "user_needs_action": {"default_move": "", "tone_shift": "", "goal": "", "forbidden": []},
    "user_needs_comfort": {"default_move": "", "tone_shift": "", "goal": "", "forbidden": []},
    "asked_private_question": {"default_move": "", "tone_shift": "", "goal": "", "forbidden": []},
    "outside_domain": {"default_move": "", "tone_shift": "", "goal": "", "forbidden": []}
  },
  "speech_mechanics": {
    "sentence_shape": "short_bursts|balanced|flowing",
    "question_density": 0.0,
    "imperative_density": 0.0,
    "analogy_domains": [""],
    "signature_openings": [""],
    "signature_landings": [""],
    "humor_profile": "",
    "cadence_markers": [""],
    "punctuation_rules": [""]
  },
  "signature_moves": [""],
  "signature_response_moves": [""],
  "contrastive_identity": {
    "nearest_neighbor_creators": [""],
    "confusion_risks": [""],
    "must_show": [""],
    "must_avoid": [""],
    "anti_persona": [""]
  },
  "temporal_voice": {
    "eras": [""],
    "current_voice_vs_old_voice": [""],
    "stable_traits": [""],
    "drift_signals": [""]
  },
  "knowledge_boundaries": {
    "confirmed_public_facts": [""],
    "inferred_only": [""],
    "private_or_unknown": [""],
    "must_verify_topics": [""]
  },
  "analogy_families": [""],
  "lexical_rules": {
    "signature_phrases": [""],
    "high_signal_words": [""],
    "banned_words": [""],
    "banned_frames": [""],
    "swearing_level": "none|low|medium|high"
  },
  "cadence_rules": {
    "sentence_shape": "short_bursts|balanced|flowing",
    "question_rate": 0.0,
    "imperative_rate": 0.0,
    "story_vs_list": "story|list|hybrid",
    "pause_markers": [""]
  },
  "anti_persona": {
    "sounds_like_someone_else_if": [""],
    "forbidden_emotional_postures": [""],
    "forbidden_generic_coach_lines": [""],
    "confusable_with": [""]
  },
  "disambiguation_markers": {
    "must_show": [""],
    "must_avoid": [""],
    "closest_neighbor_creators": [""]
  },
  "golden_examples": {
    "greeting": [""],
    "comfort": [""],
    "rebuke": [""],
    "teaching": [""],
    "boundary": [""],
    "uncertainty": [""]
  },
  "golden_replies": {
    "teaching": [""],
    "comfort": [""],
    "rebuke": [""],
    "boundary": [""],
    "sales": [""]
  },
  "scoring": {
    "identity_confidence": 0.0,
    "belief_confidence": 0.0,
    "mode_confidence": 0.0,
    "distinctiveness_score": 0.0
  },
  "content_truth": {
    "milestones": [""],
    "businesses": [""],
    "products": [""],
    "named_individuals": [""],
    "quantified_claims": [""]
  },
  "lexicon": [""],
  "evidence_snippets": ["3-6 short evidence-backed observations"]
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
            fingerprint = _backfill_v3_fields(fingerprint)
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
