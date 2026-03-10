import json
import logging
from copy import deepcopy
from typing import Dict, Any

logger = logging.getLogger(__name__)


class StyleDistiller:
    """
    Manages Style DNA: rhythm, structure, lexical, attitude, and differential persona rules.
    """

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
            },
            "differential": {
                "signature_moves": [],
                "value_hierarchy": [],
                "analogy_families": [],
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
        }
        return mapping.get(mode, "teaching")

    def get_style_dna(self, creator_id: int, creator_profile: Dict[str, Any] = None) -> Dict[str, Any]:
        dna = deepcopy(self.default_dna)
        if not creator_profile:
            return dna

        lexical_rules = creator_profile.get("lexical_rules") or {}
        worldview = creator_profile.get("worldview") or {}
        identity = creator_profile.get("identity_signature") or {}
        audience = creator_profile.get("audience_and_power") or {}
        linguistic = creator_profile.get("linguistic_dna") or {}
        emotional = creator_profile.get("emotional_signature") or {}
        cadence = creator_profile.get("cadence_rules") or {}
        mode_matrix = creator_profile.get("mode_matrix") or {}

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
        dna["attitude"]["humour"] = creator_profile.get("linguistic_dna", {}).get("analogy_style") or dna["attitude"]["humour"]
        dna["attitude"]["empathy"] = emotional.get("validation_style") or dna["attitude"]["empathy"]
        dna["identity"]["self_concept"] = identity.get("self_concept") or ""
        dna["identity"]["mission_frame"] = identity.get("mission_frame") or ""
        dna["identity"]["audience_model"] = identity.get("audience_model") or audience.get("target_audience") or ""
        dna["identity"]["power_position"] = identity.get("power_position") or audience.get("dynamic") or "hybrid"
        dna["differential"]["signature_moves"] = creator_profile.get("signature_moves") or creator_profile.get("rhetorical_moves") or []
        dna["differential"]["value_hierarchy"] = creator_profile.get("value_hierarchy") or worldview.get("moral_hierarchy") or []
        dna["differential"]["analogy_families"] = creator_profile.get("analogy_families") or []
        anti = creator_profile.get("anti_persona") or {}
        dna["anti_persona"]["must_avoid"] = creator_profile.get("disambiguation_markers", {}).get("must_avoid") or []
        dna["anti_persona"]["forbidden_postures"] = anti.get("forbidden_emotional_postures") or []
        dna["anti_persona"]["generic_lines"] = anti.get("forbidden_generic_coach_lines") or []
        dna["anti_persona"]["confusable_with"] = anti.get("confusable_with") or []
        disambiguation = creator_profile.get("disambiguation_markers") or {}
        dna["disambiguation"]["must_show"] = disambiguation.get("must_show") or []
        dna["disambiguation"]["must_avoid"] = disambiguation.get("must_avoid") or []
        dna["disambiguation"]["closest_neighbors"] = disambiguation.get("closest_neighbor_creators") or []
        dna["modes"] = mode_matrix
        return dna

    def format_for_prompt(self, dna: Dict[str, Any], voice_profile: Dict[str, Any] = None, mode: str = "task") -> str:
        mode_key = self._resolve_mode_key(mode)
        mode_rules = dna.get("modes", {}).get(mode_key, {})
        base = f"""
[STYLE DNA CONSTRAINTS]
RHYTHM: {json.dumps(dna['rhythm'])}
STRUCTURE: {json.dumps(dna['structure'])}
IDENTITY: {json.dumps(dna['identity'])}
SIGNATURE MOVES: {json.dumps(dna['differential']['signature_moves'][:6])}
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

        differential_block = f"""
[DIFFERENTIAL PERSONA]
MUST SHOW: {json.dumps(dna['disambiguation']['must_show'][:8])}
MUST AVOID: {json.dumps(dna['disambiguation']['must_avoid'][:8])}
CONFUSABLE WITH: {json.dumps(dna['anti_persona']['confusable_with'][:5])}
FORBIDDEN POSTURES: {json.dumps(dna['anti_persona']['forbidden_postures'][:6])}
FORBIDDEN GENERIC LINES: {json.dumps(dna['anti_persona']['generic_lines'][:6])}
""".strip()

        blocks = [base, mode_block, differential_block]

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
