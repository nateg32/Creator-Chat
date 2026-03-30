"""
Structured asset understanding for creator content recommendations.

This service gives the recommender lightweight, cacheable metadata about each
asset so ranking can optimize for user-goal fit instead of raw similarity alone.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    from backend.db import db
except Exception:  # pragma: no cover - lightweight test environments may not ship psycopg
    db = type(
        "_NullDB",
        (),
        {
            "execute_update": staticmethod(lambda *args, **kwargs: None),
            "execute_query": staticmethod(lambda *args, **kwargs: []),
            "execute_one": staticmethod(lambda *args, **kwargs: None),
        },
    )()


logger = logging.getLogger(__name__)


_SCHEMA_READY = False

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "your", "what", "when",
    "where", "which", "have", "about", "into", "then", "them", "they", "been",
    "will", "would", "could", "should", "just", "than", "more", "less", "some",
    "over", "under", "need", "want", "make", "made", "does", "did", "doing",
    "watch", "video", "videos", "post", "posts", "reel", "reels", "podcast",
    "episode", "clip", "clips", "this", "that", "there", "their", "because",
    "here", "while", "into", "onto", "about", "like", "really",
}

_TACTICAL_TERMS = {
    "how", "step", "steps", "framework", "system", "script", "template",
    "process", "playbook", "execute", "execution", "fix", "improve", "solve",
    "strategy", "tactic", "tactical", "walkthrough", "checklist",
}

_MINDSET_TERMS = {
    "mindset", "belief", "discipline", "confidence", "identity", "fear",
    "motivation", "focus", "clarity", "consistency", "lesson", "lessons",
    "truth", "truths",
}

_PROOF_TERMS = {
    "proof", "results", "case", "case study", "numbers", "metric", "metrics",
    "data", "evidence", "worked", "tested", "experiment", "grew", "growth",
}

_STORY_TERMS = {
    "story", "journey", "learned", "lesson", "lessons", "mistake", "mistakes",
    "experience", "life", "happened", "failed", "failure",
}

_BEGINNER_TERMS = {
    "beginner", "beginners", "start", "starting", "first", "basics", "basic",
    "101", "intro", "introduction", "foundation", "foundations",
}

_ADVANCED_TERMS = {
    "advanced", "optimize", "optimization", "scale", "scaling", "expert",
    "deep dive", "sophisticated", "breakdown", "masterclass",
}


@dataclass
class RecommendationAssetProfile:
    creator_id: int
    document_id: int
    summary: str
    problem_solved: str
    audience_level: str
    content_mode: str
    format_label: str
    actionability_score: float
    primary_topic: str
    secondary_topics: List[str]
    frameworks: List[str]
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "creator_id": self.creator_id,
            "document_id": self.document_id,
            "summary": self.summary,
            "problem_solved": self.problem_solved,
            "audience_level": self.audience_level,
            "content_mode": self.content_mode,
            "format_label": self.format_label,
            "actionability_score": self.actionability_score,
            "primary_topic": self.primary_topic,
            "secondary_topics": list(self.secondary_topics or []),
            "frameworks": list(self.frameworks or []),
            "metadata": self.metadata or {},
        }


def _ensure_schema() -> bool:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return True
    try:
        db.execute_update(
            """
            CREATE TABLE IF NOT EXISTS recommendation_asset_profiles (
                document_id BIGINT PRIMARY KEY,
                creator_id BIGINT NOT NULL,
                summary TEXT,
                problem_solved TEXT,
                audience_level TEXT,
                content_mode TEXT,
                format_label TEXT,
                actionability_score FLOAT DEFAULT 0.5,
                primary_topic TEXT,
                secondary_topics JSONB DEFAULT '[]'::jsonb,
                frameworks JSONB DEFAULT '[]'::jsonb,
                metadata JSONB DEFAULT '{}'::jsonb,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        db.execute_update(
            """
            CREATE INDEX IF NOT EXISTS idx_recommendation_asset_profiles_creator
            ON recommendation_asset_profiles (creator_id, updated_at DESC)
            """
        )
        _SCHEMA_READY = True
        return True
    except Exception as exc:
        logger.warning("Recommendation asset profile schema bootstrap failed: %s", exc)
        return False


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _split_sentences(text: str) -> List[str]:
    sentences = re.split(r"(?<=[.!?])\s+", _clean_text(text))
    return [sentence.strip() for sentence in sentences if sentence.strip()]


def _topic_tokens(text: str) -> List[str]:
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    return [word for word in words if len(word) >= 4 and word not in _STOPWORDS]


def _extract_topics(title: str, content: str, limit: int = 3) -> List[str]:
    title_terms = _topic_tokens(title)
    body_terms = _topic_tokens(content[:2400])
    weighted = Counter(title_terms * 3 + body_terms)
    return [token for token, _ in weighted.most_common(limit)]


def _extract_frameworks(title: str, content: str) -> List[str]:
    text = " ".join([title or "", content[:1200] if content else ""])
    frameworks: List[str] = []
    seen = set()

    for match in re.findall(r"\$?\d+[A-Za-z][A-Za-z\s]{1,30}", text):
        clean = _clean_text(match)
        key = clean.lower()
        if len(clean) >= 4 and key not in seen:
            seen.add(key)
            frameworks.append(clean)

    for match in re.findall(r"(?:[A-Z][a-z0-9]+(?:\s+[A-Z][a-z0-9]+){0,3})", title or ""):
        clean = _clean_text(match)
        key = clean.lower()
        if len(clean) >= 4 and key not in seen:
            seen.add(key)
            frameworks.append(clean)

    return frameworks[:4]


def _infer_audience_level(title: str, content: str) -> str:
    haystack = f"{title} {content[:1200]}".lower()
    if any(term in haystack for term in _BEGINNER_TERMS):
        return "beginner"
    if any(term in haystack for term in _ADVANCED_TERMS):
        return "advanced"
    if "scale" in haystack or "optimize" in haystack or "refine" in haystack:
        return "intermediate"
    return "general"


def _infer_content_mode(title: str, content: str) -> str:
    haystack = f"{title} {content[:1600]}".lower()
    scores = {
        "tactical": sum(1 for term in _TACTICAL_TERMS if term in haystack),
        "mindset": sum(1 for term in _MINDSET_TERMS if term in haystack),
        "proof": sum(1 for term in _PROOF_TERMS if term in haystack),
        "story": sum(1 for term in _STORY_TERMS if term in haystack),
    }
    mode = max(scores.items(), key=lambda item: item[1])[0]
    return mode if scores[mode] > 0 else "mixed"


def _infer_format_label(candidate: Dict[str, Any]) -> str:
    source_ref = candidate.get("source_ref") or {}
    platform = str(source_ref.get("platform") or candidate.get("platform") or "").lower()
    content_type = str(source_ref.get("content_type") or candidate.get("resource_type") or "").lower()
    url = str(candidate.get("url") or source_ref.get("canonical_url") or "").lower()

    if content_type == "reel" or "instagram.com/reel/" in url:
        return "reel"
    if content_type in {"video", "clip", "short", "tutorial"} or "youtube.com" in url or "youtu.be" in url or "/video/" in url:
        return "video"
    if content_type in {"tweet", "status"} or "x.com/" in url or "twitter.com/" in url:
        return "status"
    if platform == "tiktok":
        return "video"
    if content_type == "post":
        return "post"
    return content_type or "resource"


def _estimate_actionability(title: str, content: str, mode: str) -> float:
    haystack = f"{title} {content[:1800]}".lower()
    score = 0.35
    if mode == "tactical":
        score += 0.3
    if re.search(r"\b\d+\b", haystack):
        score += 0.08
    if any(term in haystack for term in ("step", "steps", "how to", "script", "template", "checklist")):
        score += 0.18
    if any(term in haystack for term in ("do this", "here's how", "what to do", "fix this")):
        score += 0.1
    return max(0.1, min(1.0, score))


def _build_summary(title: str, content: str, primary_topic: str) -> str:
    title = _clean_text(title)
    sentences = _split_sentences(content)
    if title and len(title.split()) >= 4:
        if sentences:
            return _clean_text(f"{title}. {sentences[0][:180]}")
        return title
    if sentences:
        return sentences[0][:220]
    if primary_topic:
        return f"Creator content about {primary_topic}."
    return "Creator resource."


def _build_problem_statement(mode: str, primary_topic: str, summary: str) -> str:
    if primary_topic:
        if mode == "tactical":
            return f"Helps the user execute or improve {primary_topic}."
        if mode == "mindset":
            return f"Helps the user think differently about {primary_topic}."
        if mode == "proof":
            return f"Gives concrete evidence or examples around {primary_topic}."
        if mode == "story":
            return f"Shares lessons and lived experience around {primary_topic}."
        return f"Helps the user understand {primary_topic}."
    return summary[:180] if summary else "Helps with the user's question."


def _profile_from_candidate(creator_id: int, candidate: Dict[str, Any]) -> RecommendationAssetProfile:
    title = _clean_text(candidate.get("title") or (candidate.get("source_ref") or {}).get("title") or "")
    content = _clean_text(candidate.get("content") or "")
    topics = _extract_topics(title, content, limit=3)
    primary_topic = topics[0] if topics else ""
    secondary_topics = topics[1:]
    mode = _infer_content_mode(title, content)
    summary = _build_summary(title, content, primary_topic)
    profile = RecommendationAssetProfile(
        creator_id=int(creator_id),
        document_id=int(candidate.get("document_id") or 0),
        summary=summary,
        problem_solved=_build_problem_statement(mode, primary_topic, summary),
        audience_level=_infer_audience_level(title, content),
        content_mode=mode,
        format_label=_infer_format_label(candidate),
        actionability_score=_estimate_actionability(title, content, mode),
        primary_topic=primary_topic,
        secondary_topics=secondary_topics,
        frameworks=_extract_frameworks(title, content),
        metadata={
            "title": title,
            "platform": str((candidate.get("source_ref") or {}).get("platform") or candidate.get("platform") or ""),
        },
    )
    return profile


def _coerce_json_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(item) for item in parsed if str(item or "").strip()]
        except Exception:
            return []
    return []


class RecommendationAssetService:
    def get_profile(self, creator_id: int, candidate: Dict[str, Any]) -> Dict[str, Any]:
        document_id = int(candidate.get("document_id") or 0)
        if document_id <= 0:
            return _profile_from_candidate(creator_id, candidate).to_dict()

        if _ensure_schema():
            try:
                row = db.execute_one(
                    """
                    SELECT *
                    FROM recommendation_asset_profiles
                    WHERE document_id = %s
                    LIMIT 1
                    """,
                    (document_id,),
                )
            except Exception as exc:
                logger.warning("Recommendation asset profile lookup failed: %s", exc)
                row = None
            if row:
                return {
                    "creator_id": int(row.get("creator_id") or creator_id),
                    "document_id": int(row.get("document_id") or document_id),
                    "summary": _clean_text(row.get("summary")),
                    "problem_solved": _clean_text(row.get("problem_solved")),
                    "audience_level": _clean_text(row.get("audience_level")) or "general",
                    "content_mode": _clean_text(row.get("content_mode")) or "mixed",
                    "format_label": _clean_text(row.get("format_label")) or "resource",
                    "actionability_score": float(row.get("actionability_score") or 0.5),
                    "primary_topic": _clean_text(row.get("primary_topic")),
                    "secondary_topics": _coerce_json_list(row.get("secondary_topics")),
                    "frameworks": _coerce_json_list(row.get("frameworks")),
                    "metadata": row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
                }

        profile = _profile_from_candidate(creator_id, candidate)
        if _ensure_schema() and document_id > 0:
            try:
                db.execute_update(
                    """
                    INSERT INTO recommendation_asset_profiles (
                        document_id,
                        creator_id,
                        summary,
                        problem_solved,
                        audience_level,
                        content_mode,
                        format_label,
                        actionability_score,
                        primary_topic,
                        secondary_topics,
                        frameworks,
                        metadata,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, NOW())
                    ON CONFLICT (document_id)
                    DO UPDATE SET
                        creator_id = EXCLUDED.creator_id,
                        summary = EXCLUDED.summary,
                        problem_solved = EXCLUDED.problem_solved,
                        audience_level = EXCLUDED.audience_level,
                        content_mode = EXCLUDED.content_mode,
                        format_label = EXCLUDED.format_label,
                        actionability_score = EXCLUDED.actionability_score,
                        primary_topic = EXCLUDED.primary_topic,
                        secondary_topics = EXCLUDED.secondary_topics,
                        frameworks = EXCLUDED.frameworks,
                        metadata = EXCLUDED.metadata,
                        updated_at = NOW()
                    """,
                    (
                        document_id,
                        int(creator_id),
                        profile.summary,
                        profile.problem_solved,
                        profile.audience_level,
                        profile.content_mode,
                        profile.format_label,
                        float(profile.actionability_score),
                        profile.primary_topic,
                        json.dumps(profile.secondary_topics),
                        json.dumps(profile.frameworks),
                        json.dumps(profile.metadata or {}),
                    ),
                )
            except Exception as exc:
                logger.warning("Recommendation asset profile upsert failed: %s", exc)
        return profile.to_dict()

    def enrich_candidates(self, candidates: List[Dict[str, Any]], creator_id: int) -> List[Dict[str, Any]]:
        enriched: List[Dict[str, Any]] = []
        for candidate in candidates or []:
            item = dict(candidate)
            item["asset_profile"] = self.get_profile(creator_id, item)
            enriched.append(item)
        return enriched

    def score_fit(
        self,
        profile: Dict[str, Any],
        question: str,
        resource_intent: Optional[Dict[str, Any]] = None,
        context_features: Optional[Dict[str, Any]] = None,
    ) -> float:
        if not profile:
            return 0.45

        resource_intent = resource_intent or {}
        context_features = context_features or {}

        score = 0.35
        lowered_question = _clean_text(question).lower()
        summary = _clean_text(profile.get("summary")).lower()
        problem = _clean_text(profile.get("problem_solved")).lower()
        topic = _clean_text(profile.get("primary_topic")).lower()
        secondary = [str(item).lower() for item in (profile.get("secondary_topics") or [])]
        mode = str(profile.get("content_mode") or "mixed").lower()
        audience = str(profile.get("audience_level") or "general").lower()
        actionability = float(profile.get("actionability_score") or 0.5)

        for token in _topic_tokens(lowered_question)[:6]:
            if token and token in {topic, *secondary}:
                score += 0.08
            elif token and (token in summary or token in problem):
                score += 0.04

        if context_features.get("wants_tactical") and mode == "tactical":
            score += 0.18
        if context_features.get("wants_mindset") and mode == "mindset":
            score += 0.16
        if context_features.get("wants_proof") and mode == "proof":
            score += 0.18
        if context_features.get("wants_story") and mode == "story":
            score += 0.14

        if context_features.get("wants_beginner") and audience in {"beginner", "general"}:
            score += 0.12
        if context_features.get("wants_advanced") and audience in {"advanced", "intermediate"}:
            score += 0.1

        learning_phase = str(resource_intent.get("learning_phase") or "").lower()
        if learning_phase == "execution" and mode == "tactical":
            score += 0.12
        elif learning_phase == "overview" and audience in {"beginner", "general"}:
            score += 0.08
        elif learning_phase == "troubleshooting" and mode in {"tactical", "proof"}:
            score += 0.08

        specificity = str(resource_intent.get("specificity") or "").lower()
        if specificity == "specific" and topic:
            question_terms = set(_topic_tokens(lowered_question))
            if topic in question_terms:
                score += 0.08

        if str(profile.get("format_label") or "").lower() in {"video", "reel"} and context_features.get("wants_video"):
            score += 0.08
        if str(profile.get("format_label") or "").lower() in {"post", "status"} and context_features.get("wants_post"):
            score += 0.08

        score += min(0.15, max(0.0, actionability - 0.5))
        return max(0.0, min(1.0, score))


recommendation_asset_service = RecommendationAssetService()

