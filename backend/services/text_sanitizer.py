import difflib
import logging
import re
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

DASH_CHARS = "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
WORD_BREAK_DASH_CHARS = "-\u2010\u2011\u2012\u2212"
CLAUSE_BREAK_DASH_CHARS = "\u2013\u2014\u2015"
DASH_CLASS = re.escape(DASH_CHARS)
WORD_BREAK_DASH_CLASS = re.escape(WORD_BREAK_DASH_CHARS)
CLAUSE_BREAK_DASH_CLASS = re.escape(CLAUSE_BREAK_DASH_CHARS)
MOJIBAKE_DASHES = ("\u00e2\u20ac\u201d", "\u00e2\u20ac\u201c")
PROTECTED_SPAN_RE = re.compile(r"\[[^\]]+\]\(https?://[^\s)]+\)|https?://[^\s)]+")
NUMERIC_RANGE_RE = re.compile(
    rf"(?<!\w)(?:[$£€]?\d+(?:\.\d+)?)\s*(?:[{DASH_CLASS}])\s*(?:[$£€]?\d+(?:\.\d+)?)(?:\s?(?:%|percent|x|times?|k|m|b|years?|year|months?|month|weeks?|week|days?|day|hours?|hour|minutes?|minute))?(?=(?:\s|[,.;:!?)]|$))",
    re.IGNORECASE,
)
CLAUSE_DASH_RE = re.compile(
    rf"(?<=\S)(?:[ \t]+(?:--+|[{DASH_CLASS}]+)[ \t]*|[ \t]*(?:--+|[{DASH_CLASS}]+)[ \t]+)(?=\S)"
)
WORD_BREAK_DASH_RE = re.compile(rf"(?<=\w)(?:[{WORD_BREAK_DASH_CLASS}])(?=\w)")
WORD_CLAUSE_DASH_RE = re.compile(rf"(?<=\w)(?:--+|[{CLAUSE_BREAK_DASH_CLASS}]+)(?=\w)")
INLINE_TIGHT_DASH_RE = re.compile(rf"(?<=\S)(?:--+|[{DASH_CLASS}]+)(?=\S)")
SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?])")
DANGLING_PREPOSITION_PUNCT_RE = re.compile(
    r"\s+\b(of|for|with|to|from|at|by|about|into|onto|over|under|between|among)\s*([.!?])",
    re.IGNORECASE,
)
DANGLING_PREPOSITION_WHERE_RE = re.compile(
    r"\s+\b(of|for|with|to|from|at|by|about|into|onto|over|under|between|among)\s*,\s*where\s+",
    re.IGNORECASE,
)
DANGLING_PREPOSITION_COMMA_RE = re.compile(
    r"\s+\b(of|for|with|to|from|at|by|about|into|onto|over|under|between|among)\s*,",
    re.IGNORECASE,
)
LETTER_END_PUNCT_BOUNDARY_RE = re.compile(r"([A-Za-z][.!?])(?=[A-Z0-9])")
REPEATED_COMMA_RE = re.compile(r",\s*,+")
COMMA_BEFORE_END_PUNCT_RE = re.compile(r",\s*([.!?])")
MULTISPACE_RE = re.compile(r"[ \t]{2,}")
LIST_NUMBER_SPACE_RE = re.compile(r"(?m)^(\s*\d+[.)])(?=\S)")
BIBLE_VERSE_BOUNDARY_RE = re.compile(r"(?<=[A-Za-z])(?=(?:[1-3]?\d{1,3}:\d{1,3}(?:-\d{1,3})?))")
WORD_TO_NUMBER_BOUNDARY_RE = re.compile(r"(?<=[A-Za-z])(?=\d{1,4}(?=(?:\s|[,.;:!?)]|$)))")
WORD_TO_NUMBER_SUFFIX_BOUNDARY_RE = re.compile(
    r"(?<=[A-Za-z])(?=\d{1,4}(?:s|x|st|nd|rd|th)(?=(?:\s|[,.;:!?)]|$)))",
    re.IGNORECASE,
)
NUMBER_TO_WORD_BOUNDARY_RE = re.compile(r"(?<=\d)(?=[A-Za-z]{2,}(?=(?:\s|[,;:!?)]|$)))")
DOMAIN_BOUNDARY_RE = re.compile(r"(?<=[A-Za-z])(?=(?:www\.)?(?:\d|[A-Z])[A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)+(?:/[^\s]*)?)")
STREAM_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+|\n")
SPLIT_HEAD_RE = re.compile(r"(^|[\n([{\"])([A-Za-z])\s+([a-z]{3,})(?=\b)", re.MULTILINE)
SPLIT_MIDDLE_RE = re.compile(
    r"\b([A-Za-z]{2,})\s+([aeiou])\b(?=\s+[A-Za-z]{2,}\s+[bcdfghjklmnpqrstvwxyz]\b)",
    re.IGNORECASE,
)
SPLIT_TAIL_RE = re.compile(r"\b([A-Za-z]{2,})\s+([bcdfghjklmnpqrstvwxyz])\b", re.IGNORECASE)
SPLIT_SUFFIX_RE = re.compile(
    r"\b([A-Za-z]{3,})\s+"
    r"(ify|ifies|ified|ifying|ise|ises|ised|ising|ize|izes|ized|izing|"
    r"ation|ations|ment|ments|ness|less|able|ably|ible|ibly|ally|fully|ously|ship|ships|ward|wards)\b",
    re.IGNORECASE,
)
SPLIT_SHORT_SUFFIX_RE = re.compile(
    r"\b([A-Za-z]{2,4})\s+(ing|ings|ed|er|ers|est|ly)\b(?=\s+[A-Za-z]{2,})",
    re.IGNORECASE,
)
SPLIT_PREFIX_MERGED_SUFFIX_RE = re.compile(
    r"(?<!')\b([A-Za-z]{2,4})\s+([a-z]{4,}(?:your|the))\b(?=(?:\s+[A-Za-z]{2,}\b|[,.!?;:]|$))",
    re.IGNORECASE,
)
MERGED_SINGLE_HEAD_RE = re.compile(r"\b(I)([a-z]{3,})\b")
MERGED_ARTICLE_HEAD_RE = re.compile(
    r"\b(A)(free|few|lot|little|long|short|big|small|new|good|bad|clear|single|simple)\b",
    re.IGNORECASE,
)
MERGED_COMMON_HEAD_RE = re.compile(r"\b(My|Your|Our|Their|This|That|These|Those|We|You)([a-z]{4,})\b")
# Real English words that start with these prefixes and must NOT be split
_MERGED_HEAD_REAL_WORDS: frozenset = frozenset({
    "welcome", "welcomes", "welcomed", "welcoming",
    "welfare", "wellbeing",
    "western", "westbound", "westward",
    "weather", "wealthy", "weakness", "weapon", "wearing",
    "yourself", "yourselves",
    "themselves", "thereby", "therefore", "therein", "thereafter",
    "their",  # already blocked by regex boundary but safety
    "these",  # same
    "those",  # same
    "otherwise", "thousand",
    "throughout", "thatched",
    "myself", "myopic",
    "ourselves",
})
MERGED_TRAILING_COMMON_RE = re.compile(
    r"\b([A-Za-z]{5,})(are|will|were|with|your|this|that|what|when|where|which|have|them|they)\b"
    r"(?=\s+(?:you|your|the|that|this|it|we|they|he|she|who|what|when|where|why|and|or|but|just|to|for|if|because|so|then)\b)",
    re.IGNORECASE,
)
MERGED_FOCUSED_SUFFIX_RE = re.compile(
    r"\b([A-Za-z]{4,})(your|the)\b(?=(?:\s+[A-Za-z]{2,}\b|[,.!?;:]|$))",
    re.IGNORECASE,
)
CONTRACTION_BOUNDARY_RE = re.compile(
    r"((?:'s|'re|'ve|'ll|'d|'m))(?=(?:[a-z]{4,}|you|your|the|that|this|it|we|they|he|she|who|what|when|where|why)\b)",
    re.IGNORECASE,
)
TRAILING_ALPHA_RE = re.compile(r"([A-Za-z]+)$")
LEADING_ALPHA_RE = re.compile(r"^([A-Za-z]+)")
MERGED_COMMON_TOKEN_RE = re.compile(r"\b[A-Za-z]{4,24}\b")
COMMON_SHORT_WORDS = {
    "a", "i", "an", "as", "at", "be", "by", "do", "go", "he", "if", "in", "is",
    "it", "me", "my", "no", "of", "on", "or", "so", "to", "up", "us", "we",
    "for", "and", "but", "not", "the", "you", "your",
}
MERGEABLE_COMMON_WORDS = COMMON_SHORT_WORDS | {
    "are", "been", "before", "being", "because", "between", "can", "could", "did",
    "does", "every", "from", "have", "here", "how", "into", "just", "more", "much",
    "must", "never", "now", "onto", "only", "over", "right", "should", "since",
    "still", "than", "that", "their", "them", "then", "there", "these", "they",
    "this", "those", "through", "under", "until", "very", "was", "were", "what",
    "when", "where", "which", "while", "who", "why", "will", "with", "without",
    "would",
}
# Words commonly merged with the pronoun "I" in streaming (e.g. "Ithink", "Iwant").
# Only these suffixes trigger the I-prefix split; proper nouns like Instagram are safe.
_I_SPLIT_WORDS = MERGEABLE_COMMON_WORDS | {
    "attach", "attached", "attaching",
    "think", "want", "know", "love", "need", "like", "feel", "believe",
    "remember", "understand", "mean", "see", "hear", "hope", "wish",
    "guess", "got", "get", "really", "also", "always", "actually",
    "agree", "had", "may", "might", "said", "say", "told", "tell",
    "tried", "try", "used", "usually", "went", "made", "make",
    "build", "building", "built", "coach", "coaching", "coached",
    "talk", "talked", "thought", "found", "keep", "kept", "left",
    "live", "lived", "look", "looked", "met", "moved", "play",
    "read", "run", "saw", "started", "took", "work", "worked",
}
MERGEABLE_CONNECTOR_SUFFIXES = ("and",)
MERGED_TOKEN_BLOCKLIST = {
    "command", "commands", "demand", "demands", "expand", "expands", "grand", "brand",
    "mean", "meaning", "meanings",
    "island", "remand", "remands", "strand", "strands",
    # Common -and ending words that must NOT be split into "X and"
    "understand", "understands", "understanding", "misunderstand", "misunderstands",
    "husband", "husbands", "thousand", "thousands", "errand", "errands",
    "headband", "headbands", "wristband", "wristbands", "armband", "armbands",
    "garland", "garlands", "highland", "highlands", "lowland", "lowlands",
    "mainland", "moorland", "outland", "wasteland", "homeland", "scotland",
    "england", "ireland", "iceland", "finland", "poland", "thailand", "swaziland",
    "rotterdam", "amsterdam",  # not -and but adjacent class
    "errand", "demand", "remand", "ampersand", "contraband", "secondhand",
    "beforehand", "underhand", "overhand", "shorthand", "longhand", "freehand",
    "stagehand", "farmhand",
    "withstand", "withstands", "notwithstanding",
    "reprimand", "reprimands", "reprimanded",
    "playland", "moorland", "fatherland", "motherland",
    "quicksand", "ironclad",
    "outstanding", "standstill",
    # Common words that must not be split into short-word pairs.
    "some", "within",
}
# Real English words starting with capital "I" + lowercase that the LLM sometimes
# emits as "I word" (e.g. "I dentify", "I nstead"). Merge back when the suffix
# is NOT a standalone English word so we never collapse legitimate "I deal cards".
_I_PREFIX_MERGE_WORDS = frozenset({
    "instead", "identify", "identifies", "identified", "identifying", "identity",
    "iterate", "iterates", "iterated", "iterating", "iteration", "iterations",
    "ignite", "ignites", "ignited", "igniting",
    "imagine", "imagines", "imagined", "imagining", "imagination",
    "immediate", "immediately", "immerse", "immersed", "imminent",
    "impact", "impacts", "impacted", "impacting", "impactful",
    "important", "importantly", "improve", "improves", "improved", "improving", "improvement",
    "include", "includes", "included", "including", "income", "increase", "increases",
    "increased", "increasing", "incredible", "incredibly", "indeed",
    "indicate", "indicates", "indicated", "indicating", "individual", "individuals",
    "industry", "industries", "infinite", "influence", "influences", "influenced",
    "inform", "informs", "informed", "informing", "information", "informative",
    "ingest", "ingredient", "ingredients", "initial", "initially", "initiate",
    "innovation", "innovate", "innovative",
    "inquiry", "insight", "insights", "inspect", "inspire", "inspires", "inspired", "inspiring",
    "install", "installs", "installed", "installing", "instance", "instances", "instant",
    "instantly", "institute", "institution", "instruct", "instructions", "instrument",
    "intact", "integrate", "integrates", "integrated", "integrating", "integration",
    "intellect", "intelligent", "intend", "intends", "intended", "intent", "intention",
    "interact", "interest", "interests", "interested", "interesting", "internal", "internet",
    "interpret", "interrupt", "interview", "intricate",
    "introduce", "introduces", "introduced", "introducing", "introduction",
    "intuition", "intuitive", "invest", "invests", "invested", "investing", "investment",
    "investor", "investors", "invite", "invites", "invited", "inviting", "involve", "involves",
    "involved", "involving", "irrelevant", "irresistible", "isolate",
    "ironman", "ironmans", "ironwoman", "ironwomen",
    "issue", "issues", "issued", "issuing",
})
_HEAD_PREFIX_MERGE_WORDS = _I_PREFIX_MERGE_WORDS | frozenset({
    "alex", "buyer", "seller", "client", "creator", "customer", "founder",
    "prospect", "business", "welcome",
})
_I_PREFIX_MERGE_RE = re.compile(r"\bI\s+([a-z]{2,})\b")

# Words like "Understand" / "Husband" that get emitted as "Underst and" / "Husb and".
# Only merge when the combined form is in this allowlist so we don't collapse
# legitimate "thousand and" or "demand and" sequences.
_AND_MERGE_WORDS = frozenset({
    "understand", "understands", "understanding", "misunderstand", "misunderstands",
    "withstand", "withstands", "withstanding",
    "reprimand", "reprimands",
    "ampersand", "contraband", "secondhand",
    "beforehand", "underhand", "overhand", "shorthand", "longhand", "freehand",
})
_AND_MERGE_RE = re.compile(r"\b([A-Za-z]{4,})\s+(and(?:s|ing)?)\b")
_ME_AN_MEAN_RE = re.compile(
    r"\b(can|could|may|might|would|should|does|do|did|will|this|that|it|which|what)\s+me\s+an\b",
    re.IGNORECASE,
)
IDENTITY_DISCLOSURE_RE = re.compile(
    r"(?is)(?P<prefix>^|(?<=[.!?])\s+)"
    r"(?:just\s+so\s+you\s+know,\s*)?"
    r"(?:(?:i\s*(?:am|'m)|this\s+is)\s+(?:a|an)?\s*(?:ai|artificial\s+intelligence)\b"
    r"(?=[^.!?\n]{0,260}\b(?:assistant|model|bot|creator[-\s]?style|trained)\b)"
    r"|as\s+(?:a|an)?\s*(?:ai|artificial\s+intelligence)\b"
    r"(?=[^.!?\n]{0,260}\b(?:assistant|model|bot|creator[-\s]?style|trained)\b)"
    r"|(?:i\s*(?:am|'m)|this\s+is)\s+(?:a|an)?\s*(?:chatbot|bot|language\s+model)\b)"
    r"[^.!?\n]{0,260}(?:[.!?]|$)"
)
WITHIN_SPLIT_RE = re.compile(r"\bwith\s+in\b", re.IGNORECASE)
SOME_SPLIT_RE = re.compile(
    r"\bso\s+me\b(?=\s+(?:of|specific|kind|times|people|things|examples|companies|businesses|"
    r"entities|cases|ways|the|those|these|are|were|will|can|could|would|might|may|do|does|did|"
    r"(?!(?:and|or|but|too|to|you|your|me|my|he|she|we|they)\b)[A-Z0-9][A-Z0-9'&-]{1,}|"
    r"(?!(?:and|or|but|too|to|you|your|me|my|he|she|we|they)\b)[a-z][a-z'&-]{2,})\b)",
    re.IGNORECASE,
)
BRAND_SPACING_REPLACEMENTS = (
    (re.compile(r"\bI\s+nstagram\b", re.IGNORECASE), "Instagram"),
    (re.compile(r"\bYou\s+Tube\b", re.IGNORECASE), "YouTube"),
    (re.compile(r"\bTik\s+Tok\b", re.IGNORECASE), "TikTok"),
    (re.compile(r"\bSnap\s+chat\b", re.IGNORECASE), "Snapchat"),
    (re.compile(r"\bFace\s+book\b", re.IGNORECASE), "Facebook"),
    (re.compile(r"\bLinked\s+In\b", re.IGNORECASE), "LinkedIn"),
    (re.compile(r"\bOpen\s+AI\b", re.IGNORECASE), "OpenAI"),
    (re.compile(r"\bChat\s+GPT\b", re.IGNORECASE), "ChatGPT"),
    (re.compile(r"\bPay\s+Pal\b", re.IGNORECASE), "PayPal"),
    (re.compile(r"\bMac\s+Book\b", re.IGNORECASE), "MacBook"),
    (re.compile(r"\bi\s+Phone\b"), "iPhone"),
)
MERGED_TRAILING_BLOCKLIST = {
    "software", "hardware", "aware", "beware", "elsewhere", "somewhere", "anywhere", "nowhere",
    "everywhere", "somewhat", "lathe", "loathe", "clothe", "unclothe", "writhe",
    "scythe", "soothe", "seethe", "bathe", "breathe", "blithe",
}
FINAL_CLEANUP_MAX_CHARS = 2400
FRAGMENT_LINE_RE = re.compile(r"(?m)^[A-Za-z]{1,4}(?:\s+[A-Za-z]{1,4}){1,3}$")
GENERIC_SPLIT_FRAGMENT_RE = re.compile(r"\b([A-Za-z]{4,})\s+([a-z]{4,})\b")
SHORT_SPLIT_FRAGMENT_RE = re.compile(r"\b([A-Za-z]{2,3})\s+([a-z]{4,})\b")
ONE_LETTER_SPLIT_FRAGMENT_RE = re.compile(r"\b([A-Z])\s+([a-z]{3,})\b")
SUSPICIOUS_FRAGMENT_STARTS = (
    "ation", "ational", "ations", "ative", "atively", "ality", "alities",
    "ment", "ments", "ness", "lessly", "less", "able", "ably", "ible", "ibly",
    "fully", "ously", "ology", "ologies", "tion", "tions", "sion", "sions",
    "ician", "icians", "preneur", "preneurs", "preneurial", "preneurship",
)
COMMON_SHORT_STANDALONE_WORDS = COMMON_SHORT_WORDS | {
    "ad", "ads", "age", "app", "apps", "ask", "bad", "big", "box", "buy", "car",
    "day", "dm", "dms", "fat", "few", "fit", "gym", "hit", "job", "kfc", "law",
    "low", "new", "old", "pay", "pro", "run", "seo", "tax", "top", "try", "ui",
    "ux", "web", "win",
}

logger = logging.getLogger(__name__)


def _match_word_case(original: str, replacement: str) -> str:
    return replacement[:1].upper() + replacement[1:] if original[:1].isupper() else replacement


def strip_identity_disclosure(text: str) -> str:
    """Remove product/AI self-disclosure sentences from creator-facing replies."""
    if not text:
        return text

    def _remove(match: re.Match[str]) -> str:
        return match.group("prefix") or ""

    cleaned = IDENTITY_DISCLOSURE_RE.sub(_remove, text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def _repair_high_confidence_split_words(text: str) -> str:
    repaired = WITHIN_SPLIT_RE.sub(lambda m: _match_word_case(m.group(0), "within"), text)
    repaired = SOME_SPLIT_RE.sub(lambda m: _match_word_case(m.group(0), "some"), repaired)
    for pattern, replacement in BRAND_SPACING_REPLACEMENTS:
        repaired = pattern.sub(replacement, repaired)
    return repaired


def _normalize_numeric_range(span: str) -> str:
    cleaned = re.sub(rf"\s*(?:[{DASH_CLASS}])\s*", "-", str(span or "").strip())
    cleaned = re.sub(r"(?<=\d)\s+(?=%)", "", cleaned)
    return cleaned


def _protect_spans(text: str) -> Tuple[str, Dict[str, str]]:
    protected: Dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        token = f"__CB_PROTECTED_{len(protected)}__"
        span = match.group(0)
        if NUMERIC_RANGE_RE.fullmatch(span):
            span = _normalize_numeric_range(span)
        protected[token] = span
        return token

    cleaned = text
    for pattern in (PROTECTED_SPAN_RE, NUMERIC_RANGE_RE):
        cleaned = pattern.sub(_replace, cleaned)
    return cleaned, protected


def _restore_spans(text: str, protected: Dict[str, str]) -> str:
    restored = text
    for token, value in protected.items():
        restored = restored.replace(token, value)
    return restored


def _repair_split_word_fragments(text: str) -> str:
    repaired = text

    def _merge_head_fragment(match: re.Match[str]) -> str:
        prefix = match.group(1)
        merged = f"{match.group(2)}{match.group(3)}"
        if merged.lower() in _HEAD_PREFIX_MERGE_WORDS:
            return f"{prefix}{merged}"
        return match.group(0)

    repaired = SPLIT_HEAD_RE.sub(_merge_head_fragment, repaired)

    # Merge stray "I word" sequences when they form a known English I-word
    # (e.g. "I dentify" -> "Identify", "I nstead" -> "Instead"). Restricted to
    # an allowlist so legitimate "I deal cards" / "I think" stay intact.
    def _merge_i_prefix(match: re.Match[str]) -> str:
        rest = match.group(1)
        merged = "I" + rest
        if merged.lower() in _I_PREFIX_MERGE_WORDS:
            return merged
        return match.group(0)

    repaired = _I_PREFIX_MERGE_RE.sub(_merge_i_prefix, repaired)

    def _merge_and_suffix(match: re.Match[str]) -> str:
        stem = match.group(1)
        tail = match.group(2)
        merged = stem + tail
        if merged.lower() in _AND_MERGE_WORDS:
            return merged
        return match.group(0)

    repaired = _AND_MERGE_RE.sub(_merge_and_suffix, repaired)
    repaired = _ME_AN_MEAN_RE.sub(lambda m: f"{m.group(1)} mean", repaired)
    repaired = _repair_high_confidence_split_words(repaired)

    while True:
        next_repaired = SPLIT_MIDDLE_RE.sub(
            lambda m: f"{m.group(1)}{m.group(2)}"
            if m.group(1).lower() not in COMMON_SHORT_WORDS
            else m.group(0),
            repaired,
        )
        next_repaired = SPLIT_TAIL_RE.sub(
            lambda m: f"{m.group(1)}{m.group(2)}"
            if m.group(1).lower() not in COMMON_SHORT_WORDS
            else m.group(0),
            next_repaired,
        )
        next_repaired = SPLIT_SUFFIX_RE.sub(
            lambda m: f"{m.group(1)}{m.group(2)}"
            if m.group(1).lower() not in COMMON_SHORT_WORDS
            else m.group(0),
            next_repaired,
        )
        next_repaired = SPLIT_SHORT_SUFFIX_RE.sub(
            lambda m: f"{m.group(1)}{m.group(2)}",
            next_repaired,
        )
        next_repaired = SPLIT_PREFIX_MERGED_SUFFIX_RE.sub(
            lambda m: f"{m.group(1)}{m.group(2)}"
            if m.group(1).lower() not in COMMON_SHORT_WORDS
            else m.group(0),
            next_repaired,
        )
        if next_repaired == repaired:
            break
        repaired = next_repaired

    return repaired


def _repair_merged_common_word_pairs(text: str) -> str:
    repaired = MERGED_SINGLE_HEAD_RE.sub(
        lambda m: f"{m.group(1)} {m.group(2)}" if m.group(2).lower() in _I_SPLIT_WORDS else m.group(0),
        text,
    )
    repaired = MERGED_ARTICLE_HEAD_RE.sub(lambda m: f"{m.group(1)} {m.group(2)}", repaired)
    repaired = MERGED_COMMON_HEAD_RE.sub(
        lambda m: m.group(0) if m.group(0).lower() in _MERGED_HEAD_REAL_WORDS else f"{m.group(1)} {m.group(2)}",
        repaired,
    )
    repaired = MERGED_TRAILING_COMMON_RE.sub(
        lambda m: m.group(0)
        if m.group(0).lower() in MERGED_TRAILING_BLOCKLIST
        else f"{m.group(1)} {m.group(2)}",
        repaired,
    )
    repaired = MERGED_FOCUSED_SUFFIX_RE.sub(
        lambda m: m.group(0)
        if m.group(0).lower() in MERGED_TRAILING_BLOCKLIST
        else f"{m.group(1)} {m.group(2)}",
        repaired,
    )

    def _split_token(match: re.Match[str]) -> str:
        token = match.group(0)
        lower = token.lower()
        if lower in MERGEABLE_COMMON_WORDS:
            return token
        if lower in MERGED_TOKEN_BLOCKLIST:
            return token

        for index in range(2, len(token) - 1):
            left = lower[:index]
            right = lower[index:]
            if left in MERGEABLE_COMMON_WORDS and right in MERGEABLE_COMMON_WORDS:
                return f"{token[:index]} {token[index:]}"

        for suffix in MERGEABLE_CONNECTOR_SUFFIXES:
            if lower.endswith(suffix):
                left = lower[: -len(suffix)]
                if len(left) >= 4 and re.search(r"[aeiou]", left, re.IGNORECASE):
                    return f"{token[:len(left)]} {token[len(left):]}"
        return token

    return MERGED_COMMON_TOKEN_RE.sub(_split_token, repaired)


def _should_insert_boundary_space(left: str, right: str) -> bool:
    if not left or not right or left[-1].isspace() or right[0].isspace():
        return False
    if not left[-1].isalnum() or not right[0].isalnum():
        return False

    left_match = TRAILING_ALPHA_RE.search(left)
    right_match = LEADING_ALPHA_RE.search(right)
    if not left_match or not right_match:
        return False

    left_word = left_match.group(1)
    right_word = right_match.group(1)
    if not left_word or not right_word:
        return False

    if left_word.lower() in MERGEABLE_COMMON_WORDS and right_word.lower() in MERGEABLE_COMMON_WORDS:
        return True
    if left_word == "I" and right_word[0].islower() and f"i{right_word.lower()}" not in _I_PREFIX_MERGE_WORDS:
        return True
    if left_word[-1].islower() and right_word[0].isupper():
        return True
    return False


def append_stream_text(existing: str, chunk: str) -> str:
    if not existing:
        return chunk
    if not chunk:
        return existing
    return existing + chunk


def _sanitize_core(text: str, trim_line_edges: bool) -> str:
    cleaned, protected = _protect_spans(text)
    for token in MOJIBAKE_DASHES:
        cleaned = cleaned.replace(token, ", ")

    cleaned = CLAUSE_DASH_RE.sub(", ", cleaned)
    cleaned = WORD_BREAK_DASH_RE.sub(" ", cleaned)
    cleaned = WORD_CLAUSE_DASH_RE.sub(", ", cleaned)
    cleaned = INLINE_TIGHT_DASH_RE.sub(", ", cleaned)
    cleaned = REPEATED_COMMA_RE.sub(", ", cleaned)
    cleaned = COMMA_BEFORE_END_PUNCT_RE.sub(r"\1", cleaned)
    cleaned = DANGLING_PREPOSITION_WHERE_RE.sub(", and ", cleaned)
    cleaned = DANGLING_PREPOSITION_COMMA_RE.sub(",", cleaned)
    cleaned = DANGLING_PREPOSITION_PUNCT_RE.sub(r"\2", cleaned)
    cleaned = strip_identity_disclosure(cleaned)
    cleaned = SPACE_BEFORE_PUNCT_RE.sub(r"\1", cleaned)
    cleaned = LETTER_END_PUNCT_BOUNDARY_RE.sub(r"\1 ", cleaned)
    cleaned = LIST_NUMBER_SPACE_RE.sub(r"\1 ", cleaned)
    cleaned = BIBLE_VERSE_BOUNDARY_RE.sub(" ", cleaned)
    cleaned = WORD_TO_NUMBER_BOUNDARY_RE.sub(" ", cleaned)
    cleaned = WORD_TO_NUMBER_SUFFIX_BOUNDARY_RE.sub(" ", cleaned)
    cleaned = NUMBER_TO_WORD_BOUNDARY_RE.sub(" ", cleaned)
    cleaned = DOMAIN_BOUNDARY_RE.sub(" ", cleaned)
    cleaned = MULTISPACE_RE.sub(" ", cleaned)

    if trim_line_edges:
        lines = [line.strip() for line in cleaned.splitlines()]
        cleaned = "\n".join(lines).strip()

    return _restore_spans(cleaned, protected)


def strip_mid_sentence_hyphens(text: str) -> str:
    """Remove inline dash punctuation from generated prose while preserving links and bullets."""
    if not text:
        return text
    return _sanitize_core(text, trim_line_edges=True).strip()


def _alnum_skeleton(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", text or "").lower()


def _is_safe_cleanup_candidate(original: str, candidate: str) -> bool:
    if not candidate or not candidate.strip():
        return False

    original_skeleton = _alnum_skeleton(original)
    candidate_skeleton = _alnum_skeleton(candidate)
    if not original_skeleton or not candidate_skeleton:
        return False

    similarity = difflib.SequenceMatcher(None, original_skeleton, candidate_skeleton).ratio()
    if similarity < 0.995:
        return False

    if formatting_artifact_score(candidate) > formatting_artifact_score(original):
        return False

    allowed_delta = max(24, int(len(original) * 0.2))
    return abs(len(candidate) - len(original)) <= allowed_delta


def _run_final_spacing_cleanup_model(text: str) -> Optional[str]:
    try:
        from backend.rag import generate_chat_completion
        from backend.settings import settings

        prompt = (
            "Fix only formatting corruption in this message. "
            "This includes merged words, split words, missing spaces after punctuation, broken numbered lists, and paragraph spacing. "
            "Do not rewrite, summarize, add, remove, or change wording. "
            "Preserve the exact tone, sentence order, paragraph breaks, numbering, and punctuation unless a spacing fix requires a tiny punctuation adjustment. "
            "Return only the corrected message."
        )

        return generate_chat_completion(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
            model=settings.MODEL_FALLBACK_SMART,
            temperature=0.0,
            max_tokens=min(600, max(120, len(text) // 2)),
        ).strip()
    except Exception as exc:
        logger.warning("Final spacing cleanup model pass failed: %s", exc)
        return None


def _youtube_video_id(url: str) -> str:
    parsed = urlparse(url or "")
    host = (parsed.netloc or "").lower()
    if "youtu.be" in host:
        return (parsed.path or "").strip("/").split("/")[0]
    if "youtube.com" in host:
        query_id = parse_qs(parsed.query or "").get("v", [""])[0]
        if query_id:
            return query_id
        path = (parsed.path or "").strip("/")
        parts = path.split("/")
        if len(parts) >= 2 and parts[0].lower() == "shorts":
            return parts[1]
    return ""


def _alnum_lower(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", text or "").lower()


def _card_title_variants(cards) -> list[str]:
    variants: list[str] = []
    seen: set[str] = set()
    for card in cards or []:
        if not isinstance(card, dict):
            continue
        title = str(card.get("title") or card.get("text") or "").strip()
        if not title:
            continue
        candidates = {
            title,
            re.sub(
                r"\s*(?:[-|–—]\s*)?(?:YouTube|Apple Podcasts|Spotify|Instagram|TikTok|LinkedIn|Audible(?:\.com)?)\s*$",
                "",
                title,
                flags=re.IGNORECASE,
            ).strip(),
        }
        for candidate in candidates:
            normalized = re.sub(r"\s+", " ", candidate).strip()
            key = _alnum_lower(normalized)
            if normalized and len(key) >= 8 and key not in seen:
                variants.append(normalized)
                seen.add(key)
    return variants


def _loose_literal_pattern(value: str) -> str:
    parts = [part for part in re.split(r"\s+", str(value or "").strip()) if part]
    if not parts:
        return ""
    return r"\s+".join(re.escape(part) for part in parts)


def _episode_ref_label(label: str) -> str:
    match = re.search(r"\bEp(?:isode)?\.?\s*#?\s*(\d{1,5})\b", str(label or ""), flags=re.IGNORECASE)
    if match:
        return f"Ep {match.group(1)}"
    return ""


def _strip_duplicate_card_title_handoffs(text: str, cards) -> str:
    """
    Remove duplicated source-handoff tails for an already attached card.

    This is deliberately narrow: it only fires when the response already has an
    attachment/include/link sentence for the same card title, then removes a
    repeated standalone title tail like:
    "Title | Ep 690" if you want the longer version.
    """
    if not text or not cards:
        return text

    cleaned = text
    removed_duplicate_tail = False
    for label in _card_title_variants(cards):
        label_pattern = _loose_literal_pattern(label)
        if not label_pattern:
            continue

        reference_patterns = [label_pattern]
        episode_ref = _episode_ref_label(label)
        if episode_ref:
            reference_patterns.append(_loose_literal_pattern(episode_ref))
        has_attached_reference = any(
            re.search(
                rf"(?is)\b(?:i\s+)?(?:attached|included|linked)\b[^.!?\n]{{0,260}}(?:\"{pattern}\"|{pattern})",
                cleaned,
            )
            for pattern in reference_patterns
            if pattern
        )
        if not has_attached_reference:
            continue

        duplicate_tail_pattern = re.compile(
            rf"(?is)(?:^|(?<=[.!?])\s+)\"?{label_pattern}\"?\s+"
            rf"(?:if\s+you\s+want|if\s+you'd\s+like|if\s+you\s+want\s+to)\b[^.!?]{{0,180}}[.!?]",
        )
        cleaned, tail_count = duplicate_tail_pattern.subn(" ", cleaned)
        if tail_count:
            removed_duplicate_tail = True

        duplicate_attachment_pattern = re.compile(
            rf"(?is)(?P<sentence>\b(?:i\s+)?(?:attached|included|linked)\b[^.!?]{{0,320}}"
            rf"(?:\"{label_pattern}\"|{label_pattern})[^.!?]{{0,180}}[.!?])"
            rf"(?:\s+(?P=sentence))+",
        )
        cleaned = duplicate_attachment_pattern.sub(lambda match: match.group("sentence"), cleaned)

    if removed_duplicate_tail:
        cleaned = re.sub(
            r"(?is)(?:^|(?<=[.!?])\s+)if\s+you\s+want\s+to\s+"
            r"(?:listen|watch|read|see|hear|check(?:\s+out)?|dig\s+into)\b[^.!?]{0,180}[.!?]",
            " ",
            cleaned,
        )

    return cleaned


def strip_card_attachment_artifacts(text: str, cards) -> str:
    """
    Remove raw link/video-id fragments from prose when the same resources already
    exist as preview cards below the message.
    """
    if not text or not cards:
        return text

    cleaned = text
    card_video_ids = []
    for card in cards or []:
        url = (card or {}).get("url") or ""
        if not url:
            continue

        exact_variants = {
            url,
            url.rstrip("/"),
            url.replace("https://", ""),
            url.replace("http://", ""),
        }
        for variant in exact_variants:
            if variant:
                cleaned = cleaned.replace(variant, "")

        video_id = _youtube_video_id(url)
        if video_id and len(video_id) >= 8:
            card_video_ids.append(video_id)
            spaced_pattern = r"\b" + r"\s*".join(map(re.escape, video_id)) + r"\b"
            cleaned = re.sub(spaced_pattern, "", cleaned)
            cleaned = re.sub(rf"\b{re.escape(video_id)}\b", "", cleaned)

    if card_video_ids:
        lines = cleaned.splitlines()
        drop_indexes = set()
        normalized_ids = {_alnum_lower(video_id) for video_id in card_video_ids if video_id}
        for idx, line in enumerate(lines):
            line_key = _alnum_lower(line)
            if line_key and line_key in normalized_ids:
                drop_indexes.add(idx)
                continue
            stripped = (line or "").strip()
            if (
                line_key
                and len(line_key) >= 4
                and len(stripped) <= 24
                and re.fullmatch(r"[A-Za-z0-9 ]+", stripped)
                and any(ch.isdigit() for ch in stripped)
                and any(line_key in normalized_id for normalized_id in normalized_ids)
            ):
                drop_indexes.add(idx)
                continue
            if idx + 1 < len(lines):
                pair_key = _alnum_lower(f"{line}{lines[idx + 1]}")
                if pair_key and pair_key in normalized_ids:
                    drop_indexes.add(idx)
                    drop_indexes.add(idx + 1)
                    continue
                if (
                    pair_key
                    and len(pair_key) >= 4
                    and any(pair_key in normalized_id for normalized_id in normalized_ids)
                ):
                    left = (line or "").strip()
                    right = (lines[idx + 1] or "").strip()
                    if (
                        len(left) <= 24
                        and len(right) <= 24
                        and re.fullmatch(r"[A-Za-z0-9 ]*", left)
                        and re.fullmatch(r"[A-Za-z0-9 ]*", right)
                        and (any(ch.isdigit() for ch in left) or any(ch.isdigit() for ch in right))
                    ):
                        drop_indexes.add(idx)
                        drop_indexes.add(idx + 1)
        if drop_indexes:
            cleaned = "\n".join(
                line for idx, line in enumerate(lines)
                if idx not in drop_indexes
            )

    if len(cards or []) == 1:
        cleaned = re.sub(r"(?i)\battached both below\b", "attached it below", cleaned)
        cleaned = re.sub(r"(?i)\bhere they are, attached below\b", "Here it is, attached below", cleaned)
        cleaned = re.sub(r"(?i)\bboth below\b", "it below", cleaned)

    cleaned = _strip_duplicate_card_title_handoffs(cleaned, cards)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return _sanitize_core(cleaned, trim_line_edges=True).strip()


def formatting_artifact_score(text: str) -> int:
    if not text:
        return 0

    score = 0
    if LETTER_END_PUNCT_BOUNDARY_RE.search(text):
        score += 1
    if (
        DANGLING_PREPOSITION_PUNCT_RE.search(text)
        or DANGLING_PREPOSITION_WHERE_RE.search(text)
        or DANGLING_PREPOSITION_COMMA_RE.search(text)
    ):
        score += 1
    if WORD_TO_NUMBER_BOUNDARY_RE.search(text) or WORD_TO_NUMBER_SUFFIX_BOUNDARY_RE.search(text) or NUMBER_TO_WORD_BOUNDARY_RE.search(text):
        score += 1
    if FRAGMENT_LINE_RE.search(text):
        score += 1
    return score


def _has_suspicious_formatting(text: str) -> bool:
    return formatting_artifact_score(text) > 0


def finalize_generated_text(text: str, allow_model_cleanup: bool = True) -> str:
    """
    Final answer normalization for user-visible model output.
    Keep this intentionally conservative: do not repair/split/merge English
    words after generation. Modern chat models normally emit clean prose, and
    broad word repair has caused worse user-visible corruption than it solved.
    """
    return strip_mid_sentence_hyphens(text)


def sanitize_stream_fragment(text: str) -> str:
    """Sanitize streamed prose without trimming chunk edge whitespace."""
    if not text:
        return text

    leading_match = re.match(r"^\s*", text)
    trailing_match = re.search(r"\s*$", text)
    leading_ws = leading_match.group(0) if leading_match else ""
    trailing_ws = trailing_match.group(0) if trailing_match else ""
    start = len(leading_ws)
    end = len(text) - len(trailing_ws) if trailing_ws else len(text)
    middle = text[start:end]

    if not middle:
        return text

    cleaned_middle = _sanitize_core(middle, trim_line_edges=False)
    return f"{leading_ws}{cleaned_middle}{trailing_ws}"


class StreamingTextSanitizer:
    """Buffers streamed text so inline dashes can be cleaned before the user sees them."""

    def __init__(self, tail_size: int = 32):
        self._buffer = ""
        self._tail_size = max(8, tail_size)

    def feed(self, text: str) -> str:
        if not text:
            return ""

        self._buffer = append_stream_text(self._buffer, text)
        emit_upto = self._find_emit_boundary()
        if emit_upto <= 0:
            return ""

        safe_chunk = self._buffer[:emit_upto]
        self._buffer = self._buffer[emit_upto:]
        return sanitize_stream_fragment(safe_chunk)

    def flush(self) -> str:
        if not self._buffer:
            return ""
        safe_chunk = sanitize_stream_fragment(self._buffer)
        self._buffer = ""
        return safe_chunk

    def _find_emit_boundary(self) -> int:
        last_match = None
        for match in STREAM_BOUNDARY_RE.finditer(self._buffer):
            last_match = match
        if last_match:
            return last_match.end()

        if len(self._buffer) <= self._tail_size:
            return 0

        limit = len(self._buffer) - self._tail_size
        soft_break = max(
            self._buffer.rfind(" ", 0, limit),
            self._buffer.rfind("\t", 0, limit),
            self._buffer.rfind("\n", 0, limit),
        )
        if soft_break >= 0:
            return soft_break + 1
        return limit
