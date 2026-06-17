"""Content & creator archetype classifier.

Three-tier hybrid: rules first (free, instant), LLM only when ambiguous,
LLM creator-profile synthesis once at the end.

Tiers:
  T1 — `classify_item()` runs cheap regex/duration/density rules. Always runs.
  T2 — `classify_item_with_llm()` is invoked when T1 confidence is low; it
       reads the actual title + transcript snippet and returns the same
       dict shape as T1.
  T3 — `synthesize_creator_profile_with_llm()` runs once per creator after
       ingest. It reads the per-item distribution + a few representative
       samples and returns a free-form profile (descriptive label, blend
       percentages, key traits, recommended policy overrides). This is the
       "AI sense of the creator" — the part that handles a podcaster who
       also drops music, or a vlogger who's pivoting to docs.

The downstream `fingerprint_policy.get_policy()` consumes the T3 profile
when present and falls back to the T1/T2 distribution otherwise. Nothing
in the pipeline depends on T2 or T3 succeeding — both are graceful.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from backend.db import db
from backend.settings import settings

log = logging.getLogger(__name__)

# Confidence below this triggers the LLM second-opinion classifier.
LLM_FALLBACK_THRESHOLD = 0.55

# How many representative items the creator-profile synthesizer sees.
PROFILE_SAMPLE_SIZE = 8

# Canonical archetype taxonomy. Keep tight — too many buckets dilute signal.
ITEM_ARCHETYPES = {
    "talking_head",      # vlogger / opinion / single-speaker camera
    "podcast",           # multi-speaker, conversational, long-form audio
    "music",             # audio-only music release (Spotify, SoundCloud)
    "music_video",       # video performance of a song
    "documentary",       # narrator-driven, fact-heavy long-form
    "tutorial",          # how-to, instructional, step-by-step
    "news_commentary",   # current-events analysis
    "comedy",            # sketch / standup / scripted humor
    "live_stream",       # streamed broadcast (often gaming, IRL)
    "short_meme",        # <60s, low-text, punchline-driven
    "text_post",         # twitter / linkedin / threads
    "vlog",              # day-in-the-life personal narrative
    "interview",         # host + guest, formal Q&A
}

CREATOR_ARCHETYPES = {
    "podcaster", "musician", "documentarian", "educator", "commentator",
    "comedian", "vlogger", "journalist", "streamer", "writer", "mixed",
}

# Keyword patterns. Order doesn't matter; multiple matches stack.
_KEYWORDS: Dict[str, List[str]] = {
    "podcast": [r"\bpodcast\b", r"\bep(\.|isode)?\s*\d+", r"\bep\d+", r"\b#\d+\b.*talk", r"\binterview\b", r"\bjoe rogan\b", r"\blex fridman\b", r"jre\s*#?\d+"],
    "music": [r"\b(official audio|lyric video|lyrics)\b", r"\bprod\.?\s*by\b", r"\bft\.?\s+", r"\bfeat\.?\s+", r"\balbum\b", r"\bsingle\b", r"\bmixtape\b", r"\bremix\b"],
    "music_video": [r"\bofficial (music )?video\b", r"\bmv\b", r"\bdance video\b", r"\b(dir\.?|directed by)\b"],
    "documentary": [r"\bdocumentary\b", r"\bdocu(-|\s)?series\b", r"\bthe story of\b", r"\binvestigation\b", r"\bexpose(d)?\b"],
    "tutorial": [r"\bhow to\b", r"\btutorial\b", r"\bstep[-\s]?by[-\s]?step\b", r"\bguide\b", r"\bbeginners?\b", r"\blearn\b", r"\bexplained\b", r"\bcourse\b"],
    "news_commentary": [r"\bbreaking\b", r"\bnews\b", r"\b(today|tonight)'?s\b", r"\breacts? to\b", r"\banalysis\b", r"\bopinion\b"],
    "comedy": [r"\bsketch\b", r"\bstandup\b", r"\bcomedy\b", r"\bskit\b", r"\bparody\b", r"\bprank\b"],
    "live_stream": [r"\blive\s*(stream)?\b", r"\bstream(ing)?\b", r"\bwatchparty\b", r"\bvod\b"],
    "vlog": [r"\bvlog\b", r"\bday in (my|the) life\b", r"\bweek in my life\b", r"\bdaily\b"],
    "interview": [r"\binterview(s|ed)?\b", r"\bsits down with\b", r"\bin conversation with\b", r"\b1[\s-]?on[\s-]?1\b"],
}

# Compile once.
_COMPILED: Dict[str, List[re.Pattern]] = {
    arch: [re.compile(p, re.IGNORECASE) for p in patterns]
    for arch, patterns in _KEYWORDS.items()
}

# Platform priors — what's the most likely archetype on each platform if we
# have no other signal? Used only as a soft fallback.
_PLATFORM_PRIORS: Dict[str, str] = {
    "twitter": "text_post",
    "x": "text_post",
    "linkedin": "text_post",
    "threads": "text_post",
    "reddit": "text_post",
    "facebook": "text_post",
    "spotify": "music",
    "soundcloud": "music",
    "tiktok": "short_meme",
    "instagram": "short_meme",  # default for reels; overridden if it's clearly something else
    "youtube": "talking_head",  # very weak prior, almost always overridden
}


def _word_count(text: str) -> int:
    return len(re.findall(r"\w+", text or ""))


def _score_item(
    title: str,
    transcript: str,
    caption: str,
    platform: str,
    content_type: str,
    duration_sec: Optional[float],
    hashtags: List[str],
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """Return (archetype -> score, signal trace). Higher score = more likely."""
    scores: Dict[str, float] = {a: 0.0 for a in ITEM_ARCHETYPES}
    signals: Dict[str, Any] = {
        "title_len": len(title or ""),
        "transcript_words": _word_count(transcript),
        "caption_words": _word_count(caption),
        "duration_sec": duration_sec,
        "hashtag_count": len(hashtags or []),
        "matched_keywords": {},
    }

    haystack = " ".join([title or "", caption or "", " ".join(hashtags or [])])

    # Keyword voting
    for archetype, patterns in _COMPILED.items():
        hits = []
        for pat in patterns:
            if pat.search(haystack):
                hits.append(pat.pattern)
        if hits:
            scores[archetype] += 1.5 * len(hits)
            signals["matched_keywords"][archetype] = hits

    # Duration cues
    if duration_sec is not None:
        if duration_sec < 60:
            scores["short_meme"] += 1.5
        if duration_sec >= 1500:  # 25+ min
            scores["podcast"] += 2.0
            scores["documentary"] += 0.8
        if 300 <= duration_sec <= 1500:  # 5–25 min sweet spot
            scores["talking_head"] += 0.8
            scores["tutorial"] += 0.6

    # Transcript density signal — music has very low transcript density per minute
    transcript_words = signals["transcript_words"]
    if duration_sec and duration_sec > 30 and transcript_words > 0:
        words_per_min = transcript_words / (duration_sec / 60.0)
        signals["words_per_min"] = round(words_per_min, 1)
        if words_per_min < 40:
            # Music has lyrics but lots of instrumental pause; pure music ~30-60 wpm
            scores["music"] += 1.2
            scores["music_video"] += 1.2
        elif 40 <= words_per_min < 90:
            scores["talking_head"] += 0.4
        elif words_per_min >= 90:
            scores["podcast"] += 1.0
            scores["talking_head"] += 0.5
            scores["documentary"] += 0.3

    # Multiple-speaker hint from transcript (very rough — speaker change markers)
    if transcript:
        speaker_markers = len(re.findall(r"^[A-Z][a-z]+:|^\[?(speaker|guest|host)\s*\d?\]?:", transcript, re.MULTILINE))
        if speaker_markers >= 3:
            scores["podcast"] += 1.5
            scores["interview"] += 1.2
            signals["speaker_markers"] = speaker_markers

    # Content_type override hints (set by Apify scrapers)
    ctype = (content_type or "").lower()
    if ctype in {"reel", "short"}:
        scores["short_meme"] += 1.5
    elif ctype == "tweet":
        scores["text_post"] += 3.0
    elif ctype == "post":
        scores["text_post"] += 2.5

    # Platform prior (very small weight)
    plat_low = (platform or "").lower()
    prior = _PLATFORM_PRIORS.get(plat_low)
    if prior:
        scores[prior] += 0.5

    return scores, signals


def classify_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Return {archetype, confidence, signals, alternatives}.

    `item` is a dict with keys: title, transcript, caption, platform,
    content_type, duration_sec, hashtags, metadata.
    """
    title = item.get("title") or ""
    transcript = item.get("transcript") or ""
    caption = item.get("caption") or ""
    platform = item.get("platform") or ""
    content_type = item.get("content_type") or ""
    duration_sec = item.get("duration_sec")
    if duration_sec is None:
        meta = item.get("metadata") or {}
        if isinstance(meta, dict):
            duration_sec = meta.get("duration_sec") or meta.get("duration") or meta.get("video_duration")
    hashtags = item.get("hashtags") or []
    if not hashtags and caption:
        hashtags = re.findall(r"#(\w+)", caption)

    scores, signals = _score_item(
        title=title,
        transcript=transcript,
        caption=caption,
        platform=platform,
        content_type=content_type,
        duration_sec=duration_sec,
        hashtags=hashtags,
    )

    # Pick winner. Confidence = (top - second) / (top + second) — gives a clean
    # 0..1 score that's high only when the winner is clearly ahead.
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_arch, top_score = ranked[0]
    second_arch, second_score = ranked[1] if len(ranked) > 1 else ("", 0.0)
    if top_score <= 0.0:
        # Nothing matched — fall back to platform prior or "talking_head" for video.
        top_arch = _PLATFORM_PRIORS.get(platform.lower(), "talking_head")
        confidence = 0.25
    else:
        denom = top_score + second_score
        confidence = (top_score - second_score) / denom if denom > 0 else 1.0

    return {
        "archetype": top_arch,
        "confidence": round(float(confidence), 3),
        "signals": signals,
        "alternatives": [
            {"archetype": a, "score": round(s, 2)} for a, s in ranked[:3] if s > 0
        ],
    }


def classify_and_persist(scrape_item_id: str, item: Dict[str, Any]) -> Dict[str, Any]:
    """Classify an item and persist the archetype/confidence/signals."""
    result = classify_item(item)
    try:
        db.execute_update(
            """
            UPDATE scrape_items
            SET item_archetype = %s,
                archetype_confidence = %s,
                archetype_signals = %s::jsonb
            WHERE id = %s::uuid
            """,
            (
                result["archetype"],
                result["confidence"],
                json.dumps(
                    {"signals": result["signals"], "alternatives": result["alternatives"]},
                    default=str,
                ),
                str(scrape_item_id),
            ),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("classify_and_persist write failed for %s: %s", scrape_item_id, exc)
    return result


async def classify_and_persist_smart(scrape_item_id: str, item: Dict[str, Any]) -> Dict[str, Any]:
    """Async variant: runs T1 always, T2 (LLM) only on low-confidence items, persists the merged result."""
    result = await classify_item_smart_async(item)
    try:
        db.execute_update(
            """
            UPDATE scrape_items
            SET item_archetype = %s,
                archetype_confidence = %s,
                archetype_signals = %s::jsonb
            WHERE id = %s::uuid
            """,
            (
                result["archetype"],
                result["confidence"],
                json.dumps(
                    {
                        "signals": result.get("signals"),
                        "alternatives": result.get("alternatives"),
                        "source": result.get("source", "rule"),
                        "llm_reasoning": result.get("llm_reasoning"),
                    },
                    default=str,
                ),
                str(scrape_item_id),
            ),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("classify_and_persist_smart write failed for %s: %s", scrape_item_id, exc)
    return result


# ---------------------------------------------------------------------------
# Creator-level aggregation
# ---------------------------------------------------------------------------

# Minimum share of corpus an archetype needs to "own" a creator. Below this
# we mark them as a "mixed" creator so policy reflects the blend.
_DOMINANCE_THRESHOLD = 0.55

# Map item archetype -> creator archetype label.
_ITEM_TO_CREATOR_LABEL = {
    "podcast": "podcaster",
    "interview": "podcaster",
    "music": "musician",
    "music_video": "musician",
    "documentary": "documentarian",
    "tutorial": "educator",
    "news_commentary": "commentator",
    "comedy": "comedian",
    "live_stream": "streamer",
    "vlog": "vlogger",
    "talking_head": "vlogger",
    "short_meme": "vlogger",
    "text_post": "writer",
}


def compute_creator_archetype(creator_id: int) -> Dict[str, Any]:
    """Aggregate per-item archetypes into a primary creator archetype.

    Returns {creator_archetype, confidence, distribution, item_count, sample_size}.
    distribution = {label: pct} across creator-archetype labels.
    """
    rows = db.execute_query(
        """
        SELECT item_archetype, archetype_confidence
        FROM scrape_items
        WHERE creator_handle = (
            SELECT COALESCE(handle, name) FROM creators WHERE id = %s
        )
          AND review_status = 'approved'
          AND item_archetype IS NOT NULL
        """,
        (creator_id,),
    ) or []
    if not rows:
        return {
            "creator_archetype": "mixed",
            "confidence": 0.0,
            "distribution": {},
            "item_count": 0,
            "sample_size": 0,
        }

    # Weight by per-item confidence; floor at 0.25 so an unsure item still
    # contributes something.
    weighted: Counter = Counter()
    for r in rows:
        item_arch = (r.get("item_archetype") or "").lower()
        creator_label = _ITEM_TO_CREATOR_LABEL.get(item_arch)
        if not creator_label:
            continue
        weight = max(0.25, float(r.get("archetype_confidence") or 0.5))
        weighted[creator_label] += weight

    total_weight = sum(weighted.values()) or 1.0
    distribution = {label: round(w / total_weight, 3) for label, w in weighted.most_common()}
    if not distribution:
        return {
            "creator_archetype": "mixed",
            "confidence": 0.0,
            "distribution": {},
            "item_count": len(rows),
            "sample_size": len(rows),
        }

    top_label, top_share = next(iter(distribution.items()))
    if top_share < _DOMINANCE_THRESHOLD:
        # No clear winner — call them mixed but record the distribution so the
        # policy can do a weighted blend later.
        chosen = "mixed"
        confidence = round(top_share, 3)
    else:
        chosen = top_label
        confidence = round(top_share, 3)

    return {
        "creator_archetype": chosen,
        "confidence": confidence,
        "distribution": distribution,
        "item_count": len(rows),
        "sample_size": sum(1 for r in rows if r.get("item_archetype")),
    }


def compute_and_persist_creator_archetype(
    creator_id: int,
    profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute the creator archetype and persist to the creators row.

    If `profile` (from `synthesize_creator_profile_with_llm`) is supplied,
    the LLM-derived primary archetype and blend overrides take precedence
    over the rule aggregation, and the profile blob is stored alongside the
    distribution so `fingerprint_policy.get_policy()` can read it later.
    """
    result = compute_creator_archetype(creator_id)
    chosen_archetype = result["creator_archetype"]
    if profile and isinstance(profile, dict):
        # The LLM profile is the authoritative source when present. We still
        # keep the rule distribution as ground-truth signal for transparency.
        llm_primary = (profile.get("primary_archetype") or "").strip().lower()
        if llm_primary in CREATOR_ARCHETYPES:
            chosen_archetype = llm_primary
    try:
        from datetime import datetime, timezone
        payload = {
            "distribution": result["distribution"],
            "confidence": result["confidence"],
            "item_count": result["item_count"],
        }
        if profile:
            payload["llm_profile"] = profile
        db.execute_update(
            """
            UPDATE creators
            SET creator_archetype = %s,
                archetype_distribution = %s::jsonb,
                archetype_updated_at = %s
            WHERE id = %s
            """,
            (
                chosen_archetype,
                json.dumps(payload, default=str),
                datetime.now(timezone.utc),
                creator_id,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("compute_and_persist_creator_archetype write failed for %s: %s", creator_id, exc)
    return {**result, "creator_archetype": chosen_archetype, "llm_profile": profile}


# ---------------------------------------------------------------------------
# Tier 2 — LLM item classifier (only fires on low-confidence rule output)
# ---------------------------------------------------------------------------

_LLM_ITEM_SYSTEM = (
    "You are a content-format classifier. Given a single piece of social/web "
    "content, identify the most accurate format archetype. Be honest about "
    "ambiguity — confidence should reflect how clearly the signals point to "
    "one archetype. Return only valid JSON."
)


def _build_llm_item_prompt(item: Dict[str, Any], rule_result: Dict[str, Any]) -> str:
    transcript = (item.get("transcript") or "")[:1200]
    caption = (item.get("caption") or "")[:400]
    return f"""Classify this content into ONE of: {sorted(ITEM_ARCHETYPES)}

Title: {item.get('title') or '(none)'}
Platform: {item.get('platform') or '(unknown)'}
Apify content_type: {item.get('content_type') or '(unknown)'}
Duration (sec): {item.get('duration_sec')}
Caption excerpt: {caption!r}
Transcript excerpt: {transcript!r}

Rule-based first guess: {rule_result.get('archetype')} (confidence={rule_result.get('confidence')})
Rule alternatives: {rule_result.get('alternatives')}

Respond with JSON:
{{
  "archetype": "<one of the canonical archetypes>",
  "confidence": <float 0..1>,
  "reasoning": "<one sentence explaining the dominant signal>"
}}
"""


async def classify_item_with_llm(item: Dict[str, Any], rule_result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """LLM tiebreaker for ambiguous items. Returns None on failure."""
    if not (settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY):
        return None
    try:
        # Local import keeps `content_archetype` cheap to import in non-LLM paths.
        from backend import rag
        raw = await rag.generate_chat_completion_async(
            messages=[
                {"role": "system", "content": _LLM_ITEM_SYSTEM},
                {"role": "user", "content": _build_llm_item_prompt(item, rule_result)},
            ],
            model=settings.MODEL_CLASSIFICATION,
            json_mode=True,
            temperature=0.1,
        )
        parsed = json.loads(raw)
        archetype = (parsed.get("archetype") or "").strip().lower()
        if archetype not in ITEM_ARCHETYPES:
            return None
        return {
            "archetype": archetype,
            "confidence": float(parsed.get("confidence") or 0.6),
            "reasoning": parsed.get("reasoning") or "",
            "source": "llm",
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("classify_item_with_llm failed: %s", exc)
        return None


def classify_item_smart(item: Dict[str, Any]) -> Dict[str, Any]:
    """Run T1 rules; if ambiguous and LLM is available, run T2 and merge.

    This is sync-friendly: it kicks the async LLM call from the running loop
    if one exists, otherwise spins up a short-lived loop. Callers in async
    code should prefer `classify_item_smart_async`.
    """
    rule_result = classify_item(item)
    if rule_result["confidence"] >= LLM_FALLBACK_THRESHOLD or not (settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY):
        return rule_result

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Inside an async context — caller should have used the async variant.
            return rule_result
        llm_result = loop.run_until_complete(classify_item_with_llm(item, rule_result))
    except RuntimeError:
        llm_result = asyncio.run(classify_item_with_llm(item, rule_result))
    if not llm_result:
        return rule_result

    # Merge: prefer LLM archetype but keep rule signals for traceability.
    rule_result.update({
        "archetype": llm_result["archetype"],
        "confidence": max(rule_result["confidence"], llm_result["confidence"]),
        "llm_reasoning": llm_result.get("reasoning"),
        "source": "rule+llm",
    })
    return rule_result


async def classify_item_smart_async(item: Dict[str, Any]) -> Dict[str, Any]:
    """Async variant — safe to call from FastAPI handlers."""
    rule_result = classify_item(item)
    if rule_result["confidence"] >= LLM_FALLBACK_THRESHOLD or not (settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY):
        return rule_result
    llm_result = await classify_item_with_llm(item, rule_result)
    if not llm_result:
        return rule_result
    rule_result.update({
        "archetype": llm_result["archetype"],
        "confidence": max(rule_result["confidence"], llm_result["confidence"]),
        "llm_reasoning": llm_result.get("reasoning"),
        "source": "rule+llm",
    })
    return rule_result


# ---------------------------------------------------------------------------
# Tier 3 — LLM creator-profile synthesizer (one call per creator)
# ---------------------------------------------------------------------------

_LLM_PROFILE_SYSTEM = (
    "You are an expert content strategist. Given a sample of a creator's "
    "actual content and a rule-based format distribution, synthesize a "
    "free-form profile of who this creator IS. Be specific. Avoid generic "
    "labels — when a creator blends formats, describe the blend. Return only "
    "valid JSON. The downstream system will use your output to gate web "
    "research, weight transcript-vs-caption signal, and steer voice prompts."
)


def _select_profile_samples(creator_id: int, limit: int = PROFILE_SAMPLE_SIZE) -> List[Dict[str, Any]]:
    """Pick representative items: try to cover each item_archetype present."""
    rows = db.execute_query(
        """
        SELECT id, title, raw_text, source_platform, content_type,
               item_archetype, archetype_confidence, metadata
        FROM scrape_items
        WHERE creator_handle = (
            SELECT COALESCE(handle, name) FROM creators WHERE id = %s
        )
          AND review_status = 'approved'
          AND item_archetype IS NOT NULL
        ORDER BY archetype_confidence DESC NULLS LAST
        LIMIT %s
        """,
        (creator_id, limit * 4),
    ) or []
    if not rows:
        return []

    # Take 1-2 highest-confidence items per archetype, up to `limit` total.
    by_arch: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_arch.setdefault(r.get("item_archetype") or "_", []).append(r)
    picks: List[Dict[str, Any]] = []
    while len(picks) < limit:
        added = False
        for arch, group in by_arch.items():
            if not group:
                continue
            picks.append(group.pop(0))
            added = True
            if len(picks) >= limit:
                break
        if not added:
            break
    return picks


def _format_profile_samples(samples: List[Dict[str, Any]]) -> str:
    out = []
    for s in samples:
        body = (s.get("raw_text") or "")[:300].replace("\n", " ")
        out.append(
            f"- [{s.get('item_archetype')}] ({s.get('source_platform')}/{s.get('content_type')}) "
            f"\"{(s.get('title') or '')[:100]}\" — {body}"
        )
    return "\n".join(out) or "(no items available)"


_PROFILE_OVERRIDABLE_FIELDS = {
    "enable_link_research", "enable_google_expansion", "enable_persona_agent",
    "enable_voice_extraction", "voice_signal_weight", "transcript_is_voice",
    "extract_lexicon", "voice_register", "expects_multi_speaker",
    "primary_format",
}


async def synthesize_creator_profile_with_llm(
    creator_id: int,
    creator_name: str,
    rule_aggregation: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Build a free-form creator profile via LLM.

    Returns:
      {
        "primary_archetype": "podcaster" | "musician" | ... | "mixed",
        "secondary_archetype": "<canonical or empty>",
        "descriptive_label": "podcaster who also drops music videos",
        "format_blend": {"podcaster": 0.55, "musician": 0.30, "vlogger": 0.15},
        "key_traits": ["...", "..."],
        "policy_overrides": {"voice_signal_weight": 1.1, ...},
        "rationale": "..."
      }
    """
    if not (settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY):
        return None

    samples = _select_profile_samples(creator_id)
    if not samples:
        return None

    prompt = f"""Synthesize a creator profile for {creator_name!r} (id={creator_id}).

Rule-based aggregation across approved items:
{json.dumps(rule_aggregation, indent=2, default=str)}

Representative items (archetype tag in brackets):
{_format_profile_samples(samples)}

Available canonical creator archetypes:
{sorted(CREATOR_ARCHETYPES)}

Available policy override fields (only suggest ones that DIFFER from sensible defaults):
{sorted(_PROFILE_OVERRIDABLE_FIELDS)}

Return JSON with this exact shape:
{{
  "primary_archetype": "<one canonical>",
  "secondary_archetype": "<one canonical or empty string>",
  "descriptive_label": "<5-12 words describing what this creator actually does>",
  "format_blend": {{"<archetype>": <0..1>, ...}},
  "key_traits": ["<trait>", ...],
  "policy_overrides": {{<field>: <value>, ...}},
  "rationale": "<2-3 sentences justifying primary + blend + overrides>"
}}

Rules:
- format_blend values must sum to ~1.0 and only use canonical archetypes.
- primary_archetype is the dominant blend bucket; use "mixed" only when no archetype exceeds 0.5.
- policy_overrides should be empty {{}} unless this creator clearly deviates from typical behavior for their archetype.
"""

    try:
        from backend import rag
        raw = await rag.generate_chat_completion_async(
            messages=[
                {"role": "system", "content": _LLM_PROFILE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            model=settings.MODEL_CLASSIFICATION,
            json_mode=True,
            temperature=0.2,
        )
        parsed = json.loads(raw or "{}")
    except Exception as exc:  # noqa: BLE001
        log.warning("synthesize_creator_profile_with_llm failed for %s: %s", creator_id, exc)
        return None

    # Sanitize: keep only canonical labels in blend / archetypes, only known
    # fields in policy_overrides.
    blend_raw = parsed.get("format_blend") or {}
    blend = {k.lower(): float(v) for k, v in blend_raw.items()
             if isinstance(v, (int, float)) and k.lower() in CREATOR_ARCHETYPES}
    total = sum(blend.values()) or 1.0
    blend = {k: round(v / total, 3) for k, v in blend.items()}

    overrides_raw = parsed.get("policy_overrides") or {}
    overrides = {k: v for k, v in overrides_raw.items() if k in _PROFILE_OVERRIDABLE_FIELDS}

    primary = (parsed.get("primary_archetype") or "").strip().lower()
    if primary not in CREATOR_ARCHETYPES:
        primary = "mixed"
    secondary = (parsed.get("secondary_archetype") or "").strip().lower()
    if secondary and secondary not in CREATOR_ARCHETYPES:
        secondary = ""

    return {
        "primary_archetype": primary,
        "secondary_archetype": secondary,
        "descriptive_label": parsed.get("descriptive_label") or "",
        "format_blend": blend,
        "key_traits": [t for t in (parsed.get("key_traits") or []) if isinstance(t, str)][:8],
        "policy_overrides": overrides,
        "rationale": parsed.get("rationale") or "",
    }


async def compute_and_persist_creator_archetype_smart(creator_id: int, creator_name: str = "") -> Dict[str, Any]:
    """End-to-end T1+T3: rule aggregation, then LLM profile, then persist."""
    rule_agg = compute_creator_archetype(creator_id)
    name = creator_name
    if not name:
        row = db.execute_one("SELECT name, handle FROM creators WHERE id = %s", (creator_id,))
        if row:
            name = row.get("name") or row.get("handle") or f"creator_{creator_id}"
    profile = await synthesize_creator_profile_with_llm(creator_id, name or f"creator_{creator_id}", rule_agg)
    return compute_and_persist_creator_archetype(creator_id, profile=profile)

