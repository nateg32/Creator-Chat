"""
Conversation Pulse Engine
=========================
Proprietary conversation closure intelligence that determines HOW a creator
naturally closes each conversational turn.

Instead of always appending a generic follow-up question, this engine reads
5 momentum signals from the conversation state and the creator's voice DNA
to produce a persona-authentic closure directive.

Signals:
    1. Depth Momentum   — conversation arc position (early → ask more, deep → land more)
    2. Emotional Momentum — user emotion (struggling → probe, neutral → don't over-ask)
    3. Question Fatigue  — consecutive bot questions (anti-interrogation decay)
    4. Topic Completion  — whether the answer naturally closes the topic
    5. Creator Closure DNA — this creator's actual ending patterns from fingerprint

Output: ClosureDirective with a prompt instruction injected into the system
prompt so the LLM follows persona-authentic closure behavior.

Zero LLM calls. Pure signal processing. ~0.1ms per invocation.
"""

import re
import json
import hashlib
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
#  Data structures
# ──────────────────────────────────────────────────────────

@dataclass
class ClosureDirective:
    """Output of the Conversation Pulse Engine."""
    should_ask_question: bool
    closure_type: str          # QUESTION | CHALLENGE | STATEMENT_LANDING | SILENCE
    question_probability: float
    prompt_instruction: str    # Injected directly into the system prompt
    creator_question_hint: str = ""  # Creator-specific question for greetings/bridges


# ──────────────────────────────────────────────────────────
#  Signal constants
# ──────────────────────────────────────────────────────────

# Turn depth → question probability curve (linearly interpolated)
_DEPTH_CURVE = [
    (0, 0.90),   # First turn (greeting): almost always ask
    (1, 0.82),   # Second turn: very likely
    (3, 0.68),   # Rapport building: moderate-high
    (6, 0.50),   # Established: moderate
    (10, 0.38),  # Deep conversation: declining
    (20, 0.28),  # Very long: rarely ask
]

# Intent → topic completion signal (higher = topic more naturally closed)
_COMPLETION_MAP = {
    "factual_question": 0.70,
    "personal_bio_question": 0.65,
    "recommendation": 0.45,
    "advice": 0.30,
    "how_to": 0.40,
    "greeting": 0.05,
    "greeting_only": 0.05,
    "small_talk": 0.15,
    "opinion": 0.50,
    "story": 0.55,
    "task": 0.40,
    "unknown": 0.40,
}

# Word sets for emotional momentum detection
_HIGH_ENERGY = frozenset({
    "excited", "grateful", "pumped", "motivated", "inspired", "hyped",
    "love", "amazing", "fire", "perfect", "incredible", "insane",
    "awesome", "letsgoo", "sick", "bless", "blessed",
})

_LOW_ENERGY = frozenset({
    "frustrated", "stuck", "struggling", "confused", "lost", "help",
    "overwhelmed", "anxious", "scared", "worried", "stressed",
    "failing", "broke", "hopeless", "depressed", "drowning",
})

_NEUTRAL_ACK = frozenset({
    "okay", "alright", "sure", "cool", "gotcha", "right", "bet",
    "yep", "yeah", "ok", "kk", "aight", "word", "facts",
})

_TOPIC_CLOSED = frozenset({
    "thanks", "thank you", "appreciate", "thx", "ty",
    "got it", "makes sense", "understood", "that helps",
})

# Domain → creator-specific greeting questions (richer than the old static map)
_DOMAIN_QUESTIONS = {
    "fitness": "What are you training right now?",
    "health": "What's your main health goal right now?",
    "gym": "What are you training right now?",
    "bodybuilding": "What are you working on with your physique right now?",
    "nutrition": "What are you trying to dial in with your nutrition right now?",
    "trading": "Where are you at in your trading journey right now?",
    "stocks": "What are you watching in the markets right now?",
    "crypto": "What are you trading or building in crypto right now?",
    "business": "What are you trying to build right now?",
    "entrepreneurship": "What are you trying to build right now?",
    "marketing": "What are you trying to grow right now?",
    "ecommerce": "What are you selling or building right now?",
    "finance": "What financial goal are you working on right now?",
    "personal_finance": "What money decision are you sitting on right now?",
    "ministry": "What are you needing clarity on right now?",
    "faith": "What's God been putting on your heart lately?",
    "music": "What are you working on musically right now?",
    "content": "What content are you creating right now?",
    "coaching": "What's the biggest thing you're working through right now?",
    "real_estate": "What deal or property are you looking at right now?",
    "mindset": "What's the biggest mental block you're trying to break right now?",
    "general": "What are you working on right now?",
}


# ──────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────

def _coerce(val) -> Dict:
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return {}
    return {}


def _interpolate_depth(turn_count: int) -> float:
    """Interpolate question probability from the depth curve."""
    if turn_count <= _DEPTH_CURVE[0][0]:
        return _DEPTH_CURVE[0][1]
    if turn_count >= _DEPTH_CURVE[-1][0]:
        return _DEPTH_CURVE[-1][1]
    for i in range(len(_DEPTH_CURVE) - 1):
        t0, p0 = _DEPTH_CURVE[i]
        t1, p1 = _DEPTH_CURVE[i + 1]
        if t0 <= turn_count <= t1:
            ratio = (turn_count - t0) / max(1, t1 - t0)
            return p0 + ratio * (p1 - p0)
    return 0.45


# ──────────────────────────────────────────────────────────
#  Engine
# ──────────────────────────────────────────────────────────

class ConversationPulseEngine:
    """
    Multi-signal closure intelligence engine.

    Reads conversation momentum and creator voice DNA to determine
    the natural ending for each turn. Produces a ClosureDirective
    with a system-prompt-ready instruction.
    """

    def compute(
        self,
        history: List[Dict[str, str]],
        creator_profile: Dict[str, Any],
        intent: str = "task",
        mode: str = "task",
        user_message: str = "",
    ) -> ClosureDirective:
        sfp = _coerce(creator_profile.get("style_fingerprint"))

        # ── 5 momentum signals ──
        s_depth = self._sig_depth(history)
        s_emotion = self._sig_emotion(user_message)
        s_fatigue = self._sig_fatigue(history)
        s_completion = self._sig_completion(intent, user_message)
        dna = self._sig_creator_dna(sfp)

        # ── Weighted combination ──
        base = dna["rate"]
        q_prob = (
            base * 0.35                      # Creator DNA is the anchor
            + s_depth * 0.20                  # Conversation depth
            + s_emotion * 0.15                # Emotional state
            + (1.0 - s_fatigue) * 0.15        # Fatigue reduces questions
            + (1.0 - s_completion) * 0.15     # Completed topics reduce questions
        )
        q_prob = max(0.0, min(1.0, q_prob))

        # Mode floors — greetings and small talk almost always ask
        if mode == "greeting":
            q_prob = max(q_prob, 0.92)
        elif mode == "small_talk":
            q_prob = max(q_prob, 0.72)

        # ── Decision ──
        should_ask = self._decide(q_prob, history)
        closure_type = self._pick_type(should_ask, dna, mode)
        prompt_instruction = self._build_instruction(closure_type, dna)
        question_hint = self._question_hint(sfp, creator_profile)

        return ClosureDirective(
            should_ask_question=should_ask,
            closure_type=closure_type,
            question_probability=round(q_prob, 3),
            prompt_instruction=prompt_instruction,
            creator_question_hint=question_hint,
        )

    # ──────────────────────────────────────────────────────
    #  SIGNAL 1: Turn Depth
    # ──────────────────────────────────────────────────────

    def _sig_depth(self, history: List[Dict]) -> float:
        """Deeper conversations need fewer probing questions."""
        turns = sum(1 for m in history if m.get("role") == "assistant")
        return _interpolate_depth(turns)

    # ──────────────────────────────────────────────────────
    #  SIGNAL 2: Emotional Momentum
    # ──────────────────────────────────────────────────────

    def _sig_emotion(self, msg: str) -> float:
        """
        Struggling users → probe more (understand their situation).
        Excited users → engage more (ride the energy).
        Neutral/short acks → don't over-probe (they may be satisfied).
        """
        words = set(re.findall(r"\b[a-z]+\b", msg.lower()))

        if len(words & _LOW_ENERGY) >= 1:
            return 0.78   # Struggling → ask what's going on
        if len(words & _HIGH_ENERGY) >= 2:
            return 0.65   # Excited → match and engage
        if len(words & _NEUTRAL_ACK) >= 2 or (len(msg.split()) <= 3 and not msg.strip().endswith("?")):
            return 0.22   # Short ack → they might be done
        return 0.48       # Default: moderate

    # ──────────────────────────────────────────────────────
    #  SIGNAL 3: Question Fatigue
    # ──────────────────────────────────────────────────────

    def _sig_fatigue(self, history: List[Dict]) -> float:
        """
        Track consecutive questions from the bot.
        More consecutive → higher fatigue → less likely to ask again.
        Prevents the "interrogation" feel.
        """
        consecutive = 0
        for m in reversed(history):
            if m.get("role") != "assistant":
                continue
            text = (m.get("content") or "").rstrip()
            if text.endswith("?"):
                consecutive += 1
            else:
                break
        return {0: 0.0, 1: 0.20, 2: 0.50}.get(consecutive, 0.80)

    # ──────────────────────────────────────────────────────
    #  SIGNAL 4: Topic Completion
    # ──────────────────────────────────────────────────────

    def _sig_completion(self, intent: str, msg: str) -> float:
        """
        Some intents close naturally (factual Q&A).
        Others invite follow-up (advice, coaching).
        User saying "thanks" / "got it" is a strong close signal.
        """
        base = _COMPLETION_MAP.get(intent, 0.40)
        lo = msg.lower()

        # Follow-up markers → topic NOT complete
        if any(m in lo for m in ("also", "and what about", "one more", "another question", "follow up")):
            base *= 0.5

        # Closure markers → topic IS complete
        if any(m in lo for m in _TOPIC_CLOSED):
            base = max(base, 0.80)

        return base

    # ──────────────────────────────────────────────────────
    #  SIGNAL 5: Creator Closure DNA
    # ──────────────────────────────────────────────────────

    def _sig_creator_dna(self, sfp: Dict) -> Dict:
        """
        Extract the creator's natural closure patterns from fingerprint.

        Returns a dict with:
            rate           — base question rate (0-1)
            dominant       — dominant closure style
            q_landings     — signature endings with questions
            s_landings     — signature endings with statements
            challenge_aff  — affinity for challenge-style endings (0-1)
        """
        cadence = _coerce(sfp.get("cadence_rules"))
        mech = _coerce(sfp.get("speech_mechanics"))
        behav = _coerce(sfp.get("behavioral_patterns"))

        # Base question rate from fingerprint
        raw = cadence.get("question_rate") or mech.get("question_density") or 0.38
        if isinstance(raw, str):
            raw = {"high": 0.70, "medium": 0.45, "low": 0.20, "moderate": 0.38}.get(
                raw.lower().strip(), 0.38
            )
        rate = max(0.0, min(1.0, float(raw)))

        # Signature landings → closure style detection
        landings = list(mech.get("signature_landings") or [])[:8]
        q_land = [l for l in landings if "?" in l]
        s_land = [l for l in landings if "?" not in l and len(l.strip()) > 4]

        # Detect dominant style from actual patterns
        if len(q_land) > len(s_land):
            dom = "QUESTION"
        elif s_land and any(
            re.search(r"\b(go|do|start|stop|now|today|execute|act|build|move)\b", l, re.I)
            for l in s_land
        ):
            dom = "CHALLENGE"
        elif s_land and not q_land:
            dom = "STATEMENT_LANDING"
        else:
            dom = "QUESTION"

        # Challenge affinity from behavioral traits
        push = str(behav.get("pushback_style") or "").lower()
        conf = str(behav.get("confidence_level") or "").lower()
        ch_aff = 0.0
        if any(w in push for w in ("direct", "confrontational", "blunt", "aggressive")):
            ch_aff = 0.30
        if "high" in conf:
            ch_aff += 0.15

        return {
            "rate": rate,
            "dominant": dom,
            "q_landings": q_land,
            "s_landings": s_land,
            "challenge_aff": min(ch_aff, 0.60),
        }

    # ──────────────────────────────────────────────────────
    #  Decision
    # ──────────────────────────────────────────────────────

    def _decide(self, prob: float, history: List[Dict]) -> bool:
        """
        Deterministic decision using conversation content hash.
        Same conversation state always produces the same decision.
        """
        if prob >= 0.82:
            return True
        if prob <= 0.18:
            return False

        # Hash-based deterministic coin flip
        last = (history[-1].get("content", "") if history else "")[:80]
        seed = f"{len(history)}:{last}"
        h = int(hashlib.md5(seed.encode()).hexdigest()[:8], 16)
        return (h / 0xFFFFFFFF) < prob

    def _pick_type(self, should_ask: bool, dna: Dict, mode: str) -> str:
        """Select the closure type based on decision and creator DNA."""
        if mode in ("greeting", "small_talk"):
            return "QUESTION"

        if not should_ask:
            if dna["challenge_aff"] > 0.30 and dna["s_landings"]:
                return "CHALLENGE"
            if dna["s_landings"]:
                return "STATEMENT_LANDING"
            return "SILENCE"

        # Should ask — but which style?
        if dna["dominant"] == "CHALLENGE" and dna["challenge_aff"] > 0.25:
            return "CHALLENGE"
        return "QUESTION"

    # ──────────────────────────────────────────────────────
    #  Prompt instruction builder
    # ──────────────────────────────────────────────────────

    def _build_instruction(self, ctype: str, dna: Dict) -> str:
        """Build the LLM prompt instruction for the chosen closure type."""

        if ctype == "QUESTION":
            ex = ""
            if dna["q_landings"]:
                samples = ", ".join(repr(q) for q in dna["q_landings"][:3])
                ex = f" How you naturally close with a question: {samples}."
            return (
                "CONVERSATION CLOSURE [FOLLOW-UP QUESTION]: End with exactly ONE "
                "natural follow-up question that is SPECIFIC to what the user just said. "
                "The question should sound like something you would type in a real DM, "
                "not a workbook prompt or coaching exercise. "
                "Do NOT ask a question you already asked in this conversation. "
                "NEVER use assistant patterns like 'does that make sense?', "
                "'is there anything else I can help with?', 'do you have any questions?', "
                "'would you like to know more?', or 'how does that sound?'."
                f"{ex}"
            )

        if ctype == "CHALLENGE":
            ex = ""
            cta = [
                l for l in dna["s_landings"]
                if re.search(r"\b(go|do|start|stop|now|today|execute|act|build|move)\b", l, re.I)
            ]
            if cta:
                samples = ", ".join(repr(l) for l in cta[:3])
                ex = f" How you naturally land a challenge: {samples}."
            return (
                "CONVERSATION CLOSURE [CHALLENGE]: End with a direct challenge or "
                "call to action. No question mark needed. Let the statement push "
                "them to act. This is your moment to provoke action, not probe."
                f"{ex}"
            )

        if ctype == "STATEMENT_LANDING":
            ex = ""
            if dna["s_landings"]:
                samples = ", ".join(repr(l) for l in dna["s_landings"][:3])
                ex = f" How you naturally land messages: {samples}."
            return (
                "CONVERSATION CLOSURE [STATEMENT]: End with a definitive statement "
                "that lands with impact. No question this turn. Let the message "
                "breathe. Some turns just need to hit."
                f"{ex}"
            )

        # SILENCE
        return (
            "CONVERSATION CLOSURE [CLEAN END]: End the response when the point "
            "is made. Do not force a question, prompt, or call to action. "
            "Deliver and stop."
        )

    # ──────────────────────────────────────────────────────
    #  Greeting / bridge question hint
    # ──────────────────────────────────────────────────────

    def _question_hint(self, sfp: Dict, profile: Dict) -> str:
        """
        Build a creator-specific question hint for greeting / bridge contexts.
        Priority: creator fingerprint data → domain default.
        """
        mm = _coerce(sfp.get("mode_matrix"))
        gr = _coerce(mm.get("greeting"))

        # Priority 1: Creator's actual question style from fingerprint
        qs = (gr.get("question_style") or "").strip()
        if qs and len(qs) > 8:
            # It might be a full question or a style description
            return qs

        # Priority 2: Domain-specific default
        cat = (profile.get("creator_category") or "general").lower().strip()
        return _DOMAIN_QUESTIONS.get(cat, _DOMAIN_QUESTIONS["general"])


# ──────────────────────────────────────────────────────────
#  Module-level API
# ──────────────────────────────────────────────────────────

_engine = ConversationPulseEngine()


def compute_closure(
    history: List[Dict[str, str]],
    creator_profile: Dict[str, Any],
    intent: str = "task",
    mode: str = "task",
    user_message: str = "",
) -> ClosureDirective:
    """Compute a closure directive for the current turn."""
    return _engine.compute(history, creator_profile, intent, mode, user_message)


def get_greeting_question(creator_profile: Dict[str, Any]) -> str:
    """
    Get a creator-specific greeting question.
    Replaces the static DOMAIN_GREETING_QUESTIONS lookup.
    """
    sfp = _coerce(creator_profile.get("style_fingerprint"))
    return _engine._question_hint(sfp, creator_profile)


def get_bridge_question(
    creator_profile: Dict[str, Any],
    creator_focus: str = "general",
) -> str:
    """
    Get a creator-specific bridge question for out-of-domain redirects.
    Falls back to domain default.
    """
    sfp = _coerce(creator_profile.get("style_fingerprint"))
    hint = _engine._question_hint(sfp, creator_profile)
    if hint:
        return hint
    return _DOMAIN_QUESTIONS.get(creator_focus.lower().strip(), _DOMAIN_QUESTIONS["general"])
