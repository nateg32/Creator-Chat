import json
import logging
import re
from copy import deepcopy
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class StyleDistiller:
    """Manage style DNA and build runtime identity packets for generation."""

    def __init__(self):
        self.default_dna = {
            "rhythm": {
                "sentence_length_dist": "varied",
                "paragraph_length_dist": "short_to_medium",
                "punctuation_style": "standard",
                "question_frequency": "moderate",
            },
            "structure": {
                "framework_usage": "high",
                "list_vs_story": "balanced",
                "opening_style": "direct_hook",
                "closing_style": "actionable_step",
                "cta_pattern": "soft_nudge",
            },
            "lexical": {
                "signature_phrases": [],
                "high_signal_vocab": [],
                "banned_words": ["delve", "tapestry", "plethora", "unlock", "ensure", "moreover"],
                "filler_banlist": ["kind of", "sort of", "basically", "essentially", "literally"],
                "banned_frames": [],
            },
            "attitude": {
                "bluntness": "balanced",
                "humour": "occasional",
                "empathy": "high",
                "certainty": "high",
            },
            "identity": {
                "self_concept": "",
                "mission_frame": "",
                "audience_model": "",
                "power_position": "hybrid",
                "public_role": "",
                "private_boundary_style": "",
            },
            "differential": {
                "signature_moves": [],
                "value_hierarchy": [],
                "analogy_families": [],
                "response_moves": [],
            },
            "beliefs": {
                "core_beliefs": [],
                "non_negotiables": [],
                "beliefs_they_attack": [],
                "beliefs_they_protect": [],
                "tension_points": [],
            },
            "stories": [],
            "pressure": {},
            "boundaries": {
                "confirmed_public_facts": [],
                "inferred_only": [],
                "private_or_unknown": [],
                "must_verify_topics": [],
            },
            "temporal": {
                "eras": [],
                "current_voice_vs_old_voice": [],
                "stable_traits": [],
                "drift_signals": [],
            },
            "anti_persona": {
                "must_avoid": [],
                "forbidden_postures": [],
                "generic_lines": [],
                "confusable_with": [],
            },
            "disambiguation": {
                "must_show": [],
                "must_avoid": [],
                "closest_neighbors": [],
                "confusion_risks": [],
            },
            "modes": {},
        }

    def _resolve_mode_key(self, mode: str) -> str:
        mode = (mode or "task").lower()
        mapping = {
            "task": "teaching",
            "answer": "teaching",
            "small_talk": "comfort",
            "greeting": "greeting",
            "sales": "sales",
            "story": "story",
            "rebuke": "rebuke",
            "boundary": "boundary",
            "uncertainty": "uncertainty",
            "comfort": "comfort",
            "teaching": "teaching",
            "debate": "debate",
        }
        return mapping.get(mode, "teaching")

    def _coerce_json(self, value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    def _normalize_terms(self, text: str) -> List[str]:
        return re.findall(r"[a-z0-9']+", (text or "").lower())

    def _score_match(self, question_terms: List[str], candidate: str, extra_terms: List[str] = None) -> int:
        haystack = set(self._normalize_terms(candidate))
        haystack.update(self._normalize_terms(" ".join(extra_terms or [])))
        if not haystack:
            return 0
        return len(set(question_terms) & haystack)

    def _select_story_bank(self, question: str, story_bank: List[Dict[str, Any]], limit: int = 2) -> List[Dict[str, Any]]:
        if not story_bank:
            return []
        question_terms = self._normalize_terms(question)
        ranked = []
        for index, story in enumerate(story_bank):
            if not isinstance(story, dict):
                continue
            score = self._score_match(
                question_terms,
                " ".join([
                    story.get("title", ""),
                    story.get("summary", ""),
                    story.get("lesson", ""),
                    story.get("emotion", ""),
                ]),
                extra_terms=story.get("trigger_topics") or [],
            )
            ranked.append((score, index, story))
        ranked.sort(key=lambda item: (item[0], -item[1]), reverse=True)
        selected = [story for score, _, story in ranked if score > 0][:limit]
        if selected:
            return selected
        return [story for _, _, story in ranked[:limit]]

    def _select_beliefs(self, question: str, belief_graph: Dict[str, Any], limit: int = 4) -> Dict[str, List[str]]:
        if not isinstance(belief_graph, dict):
            return {
                "core_beliefs": [],
                "value_hierarchy": [],
                "non_negotiables": [],
                "tension_points": [],
                "beliefs_they_attack": [],
                "beliefs_they_protect": [],
            }
        question_terms = self._normalize_terms(question)
        selected = {}
        for key in (
            "core_beliefs",
            "value_hierarchy",
            "non_negotiables",
            "tension_points",
            "beliefs_they_attack",
            "beliefs_they_protect",
        ):
            values = [str(v).strip() for v in (belief_graph.get(key) or []) if str(v).strip()]
            if not values:
                selected[key] = []
                continue
            ranked = sorted(values, key=lambda value: self._score_match(question_terms, value), reverse=True)
            chosen = [value for value in ranked if self._score_match(question_terms, value) > 0][:limit]
            selected[key] = chosen or values[:limit]
        return selected

    def _resolve_pressure_key(self, user_state: Dict[str, Any] = None, mode: str = "task") -> str:
        user_state = user_state or {}
        text = json.dumps(user_state).lower()
        if any(flag in text for flag in ["ashamed", "shame", "guilt"]):
            return "user_ashamed"
        if any(flag in text for flag in ["grief", "grieving", "loss"]):
            return "user_grieving"
        if any(flag in text for flag in ["flirt", "flirty", "sexual"]):
            return "user_flirty"
        if any(flag in text for flag in ["insecure", "fear", "behind", "not enough"]):
            return "user_insecure"
        if any(flag in text for flag in ["confused", "unclear", "lost", "overwhelmed"]):
            return "user_confused"
        if any(flag in text for flag in ["challenge", "pushback", "argue", "disagree"]):
            return "challenged"
        if mode == "comfort":
            return "user_needs_comfort"
        if mode == "boundary":
            return "asked_private_question"
        return "user_needs_action"

    def get_style_dna(self, creator_id: int, creator_profile: Dict[str, Any] = None) -> Dict[str, Any]:
        dna = deepcopy(self.default_dna)
        if not creator_profile:
            return dna

        creator_profile = self._coerce_json(creator_profile) if not isinstance(creator_profile, dict) else creator_profile
        lexical_rules = creator_profile.get("lexical_rules") or {}
        worldview = creator_profile.get("worldview") or {}
        identity = creator_profile.get("identity_signature") or {}
        audience = creator_profile.get("audience_and_power") or {}
        linguistic = creator_profile.get("linguistic_dna") or {}
        emotional = creator_profile.get("emotional_signature") or {}
        cadence = creator_profile.get("cadence_rules") or {}
        mode_matrix = creator_profile.get("mode_matrix") or {}
        belief_graph = creator_profile.get("belief_graph") or {}
        temporal_voice = creator_profile.get("temporal_voice") or {}
        knowledge_boundaries = creator_profile.get("knowledge_boundaries") or {}
        contrastive = creator_profile.get("contrastive_identity") or {}

        dna["lexical"]["signature_phrases"] = lexical_rules.get("signature_phrases") or creator_profile.get("signature_phrases") or []
        dna["lexical"]["high_signal_vocab"] = lexical_rules.get("high_signal_words") or creator_profile.get("lexicon") or []
        dna["lexical"]["banned_words"] = list(dict.fromkeys(dna["lexical"]["banned_words"] + (lexical_rules.get("banned_words") or [])))
        dna["lexical"]["banned_frames"] = lexical_rules.get("banned_frames") or []
        dna["rhythm"]["sentence_length_dist"] = cadence.get("sentence_shape") or linguistic.get("sentence_structure") or dna["rhythm"]["sentence_length_dist"]
        dna["rhythm"]["question_frequency"] = cadence.get("question_rate") if cadence.get("question_rate") is not None else dna["rhythm"]["question_frequency"]
        dna["rhythm"]["punctuation_style"] = "custom" if cadence.get("pause_markers") else dna["rhythm"]["punctuation_style"]
        dna["structure"]["list_vs_story"] = cadence.get("story_vs_list") or dna["structure"]["list_vs_story"]
        dna["structure"]["opening_style"] = mode_matrix.get("teaching", {}).get("opening_move") or dna["structure"]["opening_style"]
        dna["attitude"]["certainty"] = creator_profile.get("behavioral_patterns", {}).get("confidence_level") or dna["attitude"]["certainty"]
        dna["attitude"]["humour"] = creator_profile.get("speech_mechanics", {}).get("humor_profile") or creator_profile.get("linguistic_dna", {}).get("analogy_style") or dna["attitude"]["humour"]
        dna["attitude"]["empathy"] = emotional.get("validation_style") or dna["attitude"]["empathy"]
        dna["identity"]["self_concept"] = identity.get("self_concept") or ""
        dna["identity"]["mission_frame"] = identity.get("mission_frame") or ""
        dna["identity"]["audience_model"] = identity.get("audience_model") or audience.get("target_audience") or ""
        dna["identity"]["power_position"] = identity.get("power_position") or audience.get("dynamic") or "hybrid"
        dna["identity"]["public_role"] = identity.get("public_role") or ""
        dna["identity"]["private_boundary_style"] = identity.get("private_boundary_style") or ""
        dna["differential"]["signature_moves"] = creator_profile.get("signature_moves") or creator_profile.get("rhetorical_moves") or []
        dna["differential"]["response_moves"] = creator_profile.get("signature_response_moves") or creator_profile.get("signature_moves") or []
        dna["differential"]["value_hierarchy"] = creator_profile.get("value_hierarchy") or worldview.get("moral_hierarchy") or []
        dna["differential"]["analogy_families"] = creator_profile.get("analogy_families") or creator_profile.get("speech_mechanics", {}).get("analogy_domains") or []
        dna["beliefs"]["core_beliefs"] = belief_graph.get("core_beliefs") or worldview.get("core_beliefs") or []
        dna["beliefs"]["non_negotiables"] = belief_graph.get("non_negotiables") or []
        dna["beliefs"]["beliefs_they_attack"] = belief_graph.get("beliefs_they_attack") or worldview.get("conceptual_enemies") or []
        dna["beliefs"]["beliefs_they_protect"] = belief_graph.get("beliefs_they_protect") or []
        dna["beliefs"]["tension_points"] = belief_graph.get("tension_points") or []
        dna["stories"] = creator_profile.get("story_bank") or []
        dna["pressure"] = creator_profile.get("pressure_engine") or creator_profile.get("pressure_map") or {}
        dna["boundaries"] = knowledge_boundaries
        dna["temporal"] = temporal_voice
        anti = creator_profile.get("anti_persona") or {}
        dna["anti_persona"]["must_avoid"] = creator_profile.get("disambiguation_markers", {}).get("must_avoid") or contrastive.get("must_avoid") or []
        dna["anti_persona"]["forbidden_postures"] = anti.get("forbidden_emotional_postures") or []
        dna["anti_persona"]["generic_lines"] = anti.get("forbidden_generic_coach_lines") or []
        dna["anti_persona"]["confusable_with"] = anti.get("confusable_with") or contrastive.get("nearest_neighbor_creators") or []
        disambiguation = creator_profile.get("disambiguation_markers") or {}
        dna["disambiguation"]["must_show"] = disambiguation.get("must_show") or contrastive.get("must_show") or []
        dna["disambiguation"]["must_avoid"] = disambiguation.get("must_avoid") or contrastive.get("must_avoid") or []
        dna["disambiguation"]["closest_neighbors"] = disambiguation.get("closest_neighbor_creators") or contrastive.get("nearest_neighbor_creators") or []
        dna["disambiguation"]["confusion_risks"] = contrastive.get("confusion_risks") or []
        dna["modes"] = mode_matrix
        return dna

    def build_runtime_identity_packet(self, question: str, creator_profile: Dict[str, Any], user_state: Dict[str, Any] = None, mode: str = "task") -> Dict[str, Any]:
        creator_profile = creator_profile or {}
        style_fp = self._coerce_json(creator_profile.get("style_fingerprint") or creator_profile)
        identity_fp = self._coerce_json(creator_profile.get("identity_fingerprint"))
        research_summary = self._coerce_json(creator_profile.get("research_summary"))

        belief_focus = self._select_beliefs(question, style_fp.get("belief_graph") or {})
        stories = self._select_story_bank(question, style_fp.get("story_bank") or [], limit=2)
        mode_key = self._resolve_mode_key(mode)
        pressure_key = self._resolve_pressure_key(user_state=user_state, mode=mode_key)
        pressure_guidance = (style_fp.get("pressure_engine") or {}).get(pressure_key) or {}
        if not pressure_guidance and (style_fp.get("pressure_map") or {}).get(pressure_key):
            pressure_guidance = {"default_move": (style_fp.get("pressure_map") or {}).get(pressure_key)}

        facts = []
        for key in ("verified_facts", "businesses", "products", "themes", "public_consensus"):
            facts.extend(identity_fp.get(key) or [])
        boundaries = style_fp.get("knowledge_boundaries") or {}
        temporal = style_fp.get("temporal_voice") or {}
        contrastive = style_fp.get("contrastive_identity") or {}

        return {
            "belief_focus": belief_focus,
            "stories": stories,
            "pressure_key": pressure_key,
            "pressure_guidance": pressure_guidance,
            "identity_facts": facts[:10],
            "boundaries": {
                "confirmed_public_facts": (boundaries.get("confirmed_public_facts") or [])[:8],
                "private_or_unknown": (boundaries.get("private_or_unknown") or [])[:6],
                "must_verify_topics": (boundaries.get("must_verify_topics") or [])[:6],
            },
            "temporal_focus": {
                "current_voice_vs_old_voice": (temporal.get("current_voice_vs_old_voice") or [])[:4],
                "stable_traits": (temporal.get("stable_traits") or [])[:5],
                "drift_signals": (temporal.get("drift_signals") or [])[:4],
            },
            "contrastive_focus": {
                "must_show": (contrastive.get("must_show") or style_fp.get("disambiguation_markers", {}).get("must_show") or [])[:8],
                "must_avoid": (contrastive.get("must_avoid") or style_fp.get("disambiguation_markers", {}).get("must_avoid") or [])[:8],
                "nearest_neighbors": (contrastive.get("nearest_neighbor_creators") or style_fp.get("disambiguation_markers", {}).get("closest_neighbor_creators") or [])[:5],
                "confusion_risks": (contrastive.get("confusion_risks") or [])[:5],
            },
            "research_quality": research_summary.get("research_quality"),
            "mode": mode_key,
        }

    def format_for_prompt(self, dna: Dict[str, Any], voice_profile: Dict[str, Any] = None, mode: str = "task", identity_packet: Dict[str, Any] = None) -> str:
        mode_key = self._resolve_mode_key(mode)
        mode_rules = dna.get("modes", {}).get(mode_key, {})
        base = f"""
[STYLE DNA CONSTRAINTS]
RHYTHM: {json.dumps(dna['rhythm'])}
STRUCTURE: {json.dumps(dna['structure'])}
IDENTITY: {json.dumps(dna['identity'])}
SIGNATURE MOVES: {json.dumps(dna['differential']['signature_moves'][:6])}
SIGNATURE RESPONSE MOVES: {json.dumps(dna['differential']['response_moves'][:6])}
VALUE HIERARCHY: {json.dumps(dna['differential']['value_hierarchy'][:5])}
ANALOGY FAMILIES: {json.dumps(dna['differential']['analogy_families'][:5])}
KEY VOCABULARY: {json.dumps(dna['lexical']['signature_phrases'][:10])}
HIGH SIGNAL VOCAB: {json.dumps(dna['lexical']['high_signal_vocab'][:12])}
BANNED WORDS: {json.dumps(dna['lexical']['banned_words'] + dna['lexical']['filler_banlist'])}
BANNED FRAMES: {json.dumps(dna['lexical'].get('banned_frames', []))}
ATTITUDE: {json.dumps(dna['attitude'])}
""".strip()

        mode_block = f"""
[MODE RULES]
MODE: {mode_key}
RULES: {json.dumps(mode_rules)}
""".strip()

        belief_block = f"""
[BELIEF GRAPH]
CORE BELIEFS: {json.dumps(dna['beliefs']['core_beliefs'][:6])}
NON NEGOTIABLES: {json.dumps(dna['beliefs']['non_negotiables'][:5])}
BELIEFS THEY ATTACK: {json.dumps(dna['beliefs']['beliefs_they_attack'][:5])}
BELIEFS THEY PROTECT: {json.dumps(dna['beliefs']['beliefs_they_protect'][:5])}
TENSION POINTS: {json.dumps(dna['beliefs']['tension_points'][:4])}
""".strip()

        differential_block = f"""
[DIFFERENTIAL PERSONA]
MUST SHOW: {json.dumps(dna['disambiguation']['must_show'][:8])}
MUST AVOID: {json.dumps(dna['disambiguation']['must_avoid'][:8])}
CONFUSABLE WITH: {json.dumps(dna['anti_persona']['confusable_with'][:5])}
CONFUSION RISKS: {json.dumps(dna['disambiguation']['confusion_risks'][:5])}
FORBIDDEN POSTURES: {json.dumps(dna['anti_persona']['forbidden_postures'][:6])}
FORBIDDEN GENERIC LINES: {json.dumps(dna['anti_persona']['generic_lines'][:6])}
TEMPORAL VOICE: {json.dumps(dna['temporal'])}
""".strip()

        blocks = [base, mode_block, belief_block, differential_block]

        if identity_packet:
            packet_block = f"""
[RUNTIME IDENTITY RETRIEVAL]
BELIEF FOCUS: {json.dumps(identity_packet.get('belief_focus', {}))}
RELEVANT STORIES: {json.dumps(identity_packet.get('stories', []))}
PRESSURE STATE: {identity_packet.get('pressure_key')}
PRESSURE GUIDANCE: {json.dumps(identity_packet.get('pressure_guidance', {}))}
IDENTITY FACTS: {json.dumps(identity_packet.get('identity_facts', [])[:8])}
BOUNDARIES: {json.dumps(identity_packet.get('boundaries', {}))}
CONTRASTIVE FOCUS: {json.dumps(identity_packet.get('contrastive_focus', {}))}
""".strip()
            blocks.append(packet_block)

        if voice_profile:
            constraints = voice_profile.get("style_constraints", {})
            traits = voice_profile.get("interaction_traits", {})
            hard_rules = f"""
[HARD STYLE CONSTRAINTS]
- ALLOWED GREETINGS: {voice_profile.get('greetings', [])}
- SIGNOFFS: {voice_profile.get('signoffs', [])}
- SIGNATURE PHRASES: {voice_profile.get('signature_phrases', [])}
- TARGET SENTENCE LENGTH: {constraints.get('avg_sentence_words', 15)} words
- EMOJI USAGE: {constraints.get('emoji_rate', 'low')}
- CAPS USAGE: {constraints.get('caps_rate', 'rare')}
- DASHES: {"Use often" if constraints.get('uses_dashes') else "Avoid"}
- ELLIPSES: {"Use often" if constraints.get('uses_ellipses') else "Avoid"}
- INTERACTION: {"Ask question first" if traits.get('question_first_rate', 0) > 0.5 else "Direct answer"}
- ACTION STEP RATE: {traits.get('action_step_rate', 0.5)}

ENSURE: Use 1-2 signature signals above, but do not spam catchphrases.
""".strip()
            blocks.append(hard_rules)

        return "\n\n".join(blocks)
