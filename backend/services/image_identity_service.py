import json
import logging
import re
from typing import Any, Dict, List, Optional

import backend.rag as rag
from backend.settings import settings
from backend.services.research_provider import get_research_provider


logger = logging.getLogger(__name__)


_IDENTITY_PATTERNS = [
    r"\bwho('?s| is)\s+(this|that|her|him|the person)\b",
    r"\bwho('?s| is)\s+(this|that)\s+(girl|guy|chick|woman|man|lady|dude|person)\b",
    r"\bwho('?s| is)\s+(she|he|her|him)\b",
    r"\bidentify\s+(this|that)\b",
    r"\bis\s+(this|that)\s+[a-z]",
    r"\bdo you know who (this|that) is\b",
    r"\bwho is in (this|the) (photo|picture|image)\b",
]

_RELATION_HINTS = {
    "wife": ["wife", "wifey", "spouse"],
    "husband": ["husband", "spouse"],
    "partner": ["partner", "girlfriend", "boyfriend", "fiance", "fiancee"],
    "family": ["mom", "mother", "dad", "father", "brother", "sister", "son", "daughter", "family"],
    "cofounder": ["cofounder", "founder", "business partner", "partner in business"],
    "team": ["employee", "team", "staff", "assistant", "producer", "editor"],
    "guest": ["guest", "podcast guest", "interview guest"],
}


def looks_like_image_identity_question(question: str) -> bool:
    text = (question or "").strip().lower()
    if not text:
        return False
    if any(re.search(pattern, text) for pattern in _IDENTITY_PATTERNS):
        return True
    if "who" in text and any(token in text for token in ["photo", "picture", "image", "pic", "girl", "guy", "chick", "woman", "man", "lady", "dude", "person", "she", "he", "her", "him"]):
        return True
    return bool(extract_relation_hints(text))


def extract_relation_hints(question: str) -> List[str]:
    text = (question or "").strip().lower()
    hints: List[str] = []
    for label, synonyms in _RELATION_HINTS.items():
        if any(term in text for term in synonyms):
            hints.append(label)
    return hints


def _safe_json_loads(raw: Any, fallback: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return fallback
    try:
        return json.loads(raw)
    except Exception:
        return fallback


def _truncate(text: str, limit: int = 320) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _dedupe_strings(items: List[str], limit: int = 8) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        cleaned = _truncate(item, 220)
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
        if len(out) >= limit:
            break
    return out


def _extend_texts(target: List[str], value: Any) -> None:
    if not value:
        return
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                target.append(json.dumps(item))
            else:
                target.append(str(item))
        return
    if isinstance(value, dict):
        target.append(json.dumps(value))
        return
    target.append(str(value))


class ImageIdentityService:
    def __init__(self):
        self.research_provider = get_research_provider()

    def inspect(
        self,
        question: str,
        images: List[Dict[str, Any]],
        creator_id: int,
        creator_profile: Optional[Dict[str, Any]] = None,
        allow_web: bool = True,
    ) -> Dict[str, Any]:
        if not images:
            return {"handled": False}

        creator_profile = creator_profile or {}
        observation = self._observe_images(question, images)
        support_chunk = self._build_support_chunk(observation, images)

        if not looks_like_image_identity_question(question):
            return {
                "handled": False,
                "support_chunk": support_chunk,
                "observation": observation,
            }

        evidence = self._collect_candidate_evidence(
            question,
            creator_id,
            creator_profile,
            observation,
            allow_web=allow_web,
        )
        candidates = evidence.get("candidates") or []
        match = self._match_candidate_to_image(question, images, observation, candidates)
        if not match.get("matched_name") or float(match.get("confidence") or 0.0) < 0.68:
            answer = self._synthesize_uncertain_answer(question, creator_profile, observation, candidates)
            return {
                "handled": True,
                "answer": answer,
                "sources": evidence.get("sources") or [],
                "meta": {
                    "question_type": "image_identity",
                    "match": match,
                    "observation": observation,
                    "reason": "low_confidence_match",
                },
            }

        targeted = self._collect_targeted_facts(
            creator_id,
            creator_profile,
            match,
            question,
            allow_web=allow_web,
        )
        answer = self._synthesize_confirmed_answer(
            question,
            creator_profile,
            observation,
            match,
            targeted.get("facts") or [],
        )
        return {
            "handled": True,
            "answer": answer,
            "sources": targeted.get("sources") or evidence.get("sources") or [],
            "meta": {
                "question_type": "image_identity",
                "match": match,
                "observation": observation,
                "reason": "creator_aware_match",
            },
        }

    def _observe_images(self, question: str, images: List[Dict[str, Any]]) -> Dict[str, Any]:
        prompt = """
Analyze the attached image or images. Do not identify any named public person.
Return JSON with:
- summary: short plain-English description
- primary_subject: "person" | "people" | "object" | "scene"
- person_count: integer
- people: array of up to 2 objects with gender_presentation, age_band, notable_features
- identity_clues: array of short clues that may help match against a known creator-associated person
- visible_text: array of short OCR snippets if any
- confidence: 0 to 1
Keep it factual and visual only.
""".strip()
        user_content: List[Dict[str, Any]] = [
            {
                "type": "text",
                "text": f"User question: {question or 'No text question provided.'}",
            }
        ]
        for image in images[:4]:
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image.get("data_url"),
                        "detail": image.get("detail") or "auto",
                    },
                }
            )
        try:
            raw = rag.generate_chat_completion(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_content},
                ],
                model=settings.VISION_MODEL,
                temperature=0.0,
                json_mode=True,
            )
            data = _safe_json_loads(raw, {})
            if isinstance(data, dict) and data:
                return data
        except Exception as exc:
            logger.warning("Image observation failed: %s", exc)
        return {
            "summary": "The image appears to show a person, but the visual analysis did not complete cleanly.",
            "primary_subject": "person",
            "person_count": 1,
            "people": [],
            "identity_clues": [],
            "visible_text": [],
            "confidence": 0.2,
        }

    def _build_support_chunk(self, observation: Dict[str, Any], images: List[Dict[str, Any]]) -> Dict[str, Any]:
        people = observation.get("people") or []
        features = []
        for person in people[:2]:
            notable = ", ".join((person.get("notable_features") or [])[:4])
            descriptor = ", ".join(
                part for part in [person.get("gender_presentation"), person.get("age_band"), notable] if part
            )
            if descriptor:
                features.append(descriptor)
        content_lines = [
            "CURRENT MESSAGE INCLUDED IMAGE ATTACHMENT(S). You do have visual context for this turn.",
            f"Visual summary: {observation.get('summary') or 'Image attached by the user.'}",
            f"Primary subject: {observation.get('primary_subject') or 'unknown'}",
            f"People visible: {observation.get('person_count') or 0}",
        ]
        if features:
            content_lines.append(f"Visible features: {'; '.join(features)}")
        if observation.get("identity_clues"):
            content_lines.append(f"Identity clues: {', '.join(observation.get('identity_clues')[:5])}")
        if observation.get("visible_text"):
            content_lines.append(f"Visible text: {', '.join(observation.get('visible_text')[:5])}")
        return {
            "content": "\n".join(content_lines),
            "is_image_context": True,
            "source_ref": {
                "platform": "user_upload",
                "title": f"User uploaded image ({len(images)} attached)",
                "canonical_url": "",
                "content_type": "image",
            },
        }

    def _collect_candidate_evidence(
        self,
        question: str,
        creator_id: int,
        creator_profile: Dict[str, Any],
        observation: Dict[str, Any],
        allow_web: bool = True,
    ) -> Dict[str, Any]:
        creator_name = creator_profile.get("name") or creator_profile.get("handle") or "the creator"
        relation_hints = extract_relation_hints(question)
        style_fp = _safe_json_loads(creator_profile.get("style_fingerprint"), {})
        identity_fp = _safe_json_loads(creator_profile.get("identity_fingerprint"), {})
        research_summary = _safe_json_loads(creator_profile.get("research_summary"), {})
        soul_md = str(creator_profile.get("soul_md") or "")

        profile_texts: List[str] = []
        _extend_texts(profile_texts, identity_fp.get("verified_facts"))
        _extend_texts(profile_texts, identity_fp.get("public_figures"))
        _extend_texts(profile_texts, identity_fp.get("businesses"))
        _extend_texts(profile_texts, research_summary.get("public_consensus"))
        _extend_texts(profile_texts, research_summary.get("creator_claims"))
        _extend_texts(profile_texts, (style_fp.get("knowledge_boundaries") or {}).get("confirmed_public_facts"))
        if soul_md:
            for line in soul_md.splitlines():
                if ":" in line or "-" in line:
                    profile_texts.append(line.strip())

        retrieved: List[Dict[str, Any]] = []
        query_variants = [
            f"{creator_name} public people close to them",
            f"{creator_name} spouse partner cofounder family team",
            f"{creator_name} {question}".strip(),
        ]
        if relation_hints:
            query_variants.insert(0, f"{creator_name} {' '.join(relation_hints)}")

        seen_queries = set()
        for query in query_variants[:4]:
            key = query.lower().strip()
            if key in seen_queries:
                continue
            seen_queries.add(key)
            try:
                emb = rag.create_embedding(query)
                chunks = rag.retrieve_chunks(
                    creator_id=creator_id,
                    query_embedding=emb,
                    top_k=4,
                    max_distance=0.62,
                )
                for chunk in chunks:
                    retrieved.append(
                        {
                            "text": chunk.get("content") or "",
                            "title": chunk.get("title") or "",
                            "url": chunk.get("url") or "",
                            "source": "internal",
                        }
                    )
            except Exception as exc:
                logger.warning("Image identity internal retrieval failed for '%s': %s", query, exc)

        web_results: List[Dict[str, Any]] = []
        if allow_web:
            web_query = f"{creator_name} {' '.join(relation_hints or ['wife', 'husband', 'partner', 'cofounder', 'family'])}"
            try:
                web_results = self.research_provider.search(
                    web_query,
                    creator_profile,
                    resource_type="web",
                    conversation_history=None,
                )[:6]
            except Exception as exc:
                logger.warning("Image identity web search failed: %s", exc)

        evidence_lines = _dedupe_strings(profile_texts, limit=18)
        evidence_lines.extend(
            _dedupe_strings(
                [
                    f"{item.get('title')}: {item.get('text')}"
                    for item in retrieved[:8]
                    if item.get("text") or item.get("title")
                ],
                limit=10,
            )
        )
        evidence_lines.extend(
            _dedupe_strings(
                [
                    f"{item.get('title')}: {item.get('snippet') or item.get('text') or ''}"
                    for item in web_results[:6]
                    if item.get("title") or item.get("snippet") or item.get("text")
                ],
                limit=8,
            )
        )

        extraction_prompt = f"""
Creator: {creator_name}
User question: {question}
Visual observation: {json.dumps(observation)}

Evidence:
{json.dumps(evidence_lines)}

Extract up to 6 PUBLIC people strongly connected to the creator who are plausible identity candidates for the image.
Prefer spouse, business partner, cofounder, public family member, team member, or recurring public collaborator only when evidence supports it.
Return JSON:
{{
  "candidates": [
    {{
      "name": "string",
      "relationship": "string",
      "confidence": 0.0,
      "support": ["short evidence"]
    }}
  ]
}}
""".strip()
        candidates: List[Dict[str, Any]] = []
        try:
            raw = rag.generate_chat_completion(
                messages=[
                    {"role": "system", "content": "Extract structured candidate identities from creator evidence."},
                    {"role": "user", "content": extraction_prompt},
                ],
                model=settings.ROUTER_MODEL,
                temperature=0.0,
                json_mode=True,
            )
            parsed = _safe_json_loads(raw, {})
            for candidate in (parsed.get("candidates") or [])[:6]:
                name = _truncate(candidate.get("name") or "", 80)
                if not name:
                    continue
                candidates.append(
                    {
                        "name": name,
                        "relationship": _truncate(candidate.get("relationship") or "", 80),
                        "confidence": float(candidate.get("confidence") or 0.0),
                        "support": _dedupe_strings(candidate.get("support") or [], limit=3),
                    }
                )
        except Exception as exc:
            logger.warning("Image identity candidate extraction failed: %s", exc)

        sources = []
        for idx, item in enumerate(retrieved[:6], start=1):
            sources.append(
                {
                    "source_id": f"img_internal_{idx}",
                    "title": item.get("title") or f"Internal source {idx}",
                    "url": item.get("url"),
                    "snippet": _truncate(item.get("text") or "", 220),
                    "platform": item.get("source") or "internal",
                }
            )
        for idx, item in enumerate(web_results[:4], start=1):
            sources.append(
                {
                    "source_id": f"img_web_{idx}",
                    "title": item.get("title") or f"Web source {idx}",
                    "url": item.get("url"),
                    "snippet": _truncate(item.get("snippet") or item.get("text") or "", 220),
                    "platform": item.get("platform") or "web",
                }
            )
        return {
            "candidates": candidates,
            "sources": sources,
        }

    def _match_candidate_to_image(
        self,
        question: str,
        images: List[Dict[str, Any]],
        observation: Dict[str, Any],
        candidates: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not candidates:
            return {"matched_name": None, "confidence": 0.0, "relationship": "", "reason": "no_candidates"}

        candidate_payload = [
            {
                "name": c.get("name"),
                "relationship": c.get("relationship"),
                "support": c.get("support"),
            }
            for c in candidates[:6]
        ]
        user_content: List[Dict[str, Any]] = [
            {
                "type": "text",
                "text": f"""
User question: {question}
Visual observation: {json.dumps(observation)}
Candidate list: {json.dumps(candidate_payload)}

Choose the best candidate only if the match is reasonably supported by the visible image and creator context.
Do not claim certainty when the image is insufficient.
Return JSON:
{{
  "matched_name": "string or null",
  "relationship": "string",
  "confidence": 0.0,
  "reason": "short explanation",
  "disclaimer_required": true
}}
""".strip(),
            }
        ]
        for image in images[:4]:
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image.get("data_url"),
                        "detail": image.get("detail") or "auto",
                    },
                }
            )
        try:
            raw = rag.generate_chat_completion(
                messages=[
                    {"role": "system", "content": "Match the image against a constrained candidate list. Prefer 'unknown' over guessing."},
                    {"role": "user", "content": user_content},
                ],
                model=settings.VISION_MODEL,
                temperature=0.0,
                json_mode=True,
            )
            data = _safe_json_loads(raw, {})
            if isinstance(data, dict) and data:
                return data
        except Exception as exc:
            logger.warning("Image identity candidate match failed: %s", exc)
        return {"matched_name": None, "confidence": 0.0, "relationship": "", "reason": "match_failed"}

    def _collect_targeted_facts(
        self,
        creator_id: int,
        creator_profile: Dict[str, Any],
        match: Dict[str, Any],
        question: str,
        allow_web: bool = True,
    ) -> Dict[str, Any]:
        creator_name = creator_profile.get("name") or creator_profile.get("handle") or "the creator"
        matched_name = str(match.get("matched_name") or "").strip()
        relationship = str(match.get("relationship") or "").strip()
        query = f"{creator_name} {matched_name} {relationship} public facts".strip()

        facts: List[str] = []
        sources: List[Dict[str, Any]] = []
        try:
            emb = rag.create_embedding(query)
            chunks = rag.retrieve_chunks(
                creator_id=creator_id,
                query_embedding=emb,
                top_k=5,
                max_distance=0.58,
            )
            for idx, chunk in enumerate(chunks[:5], start=1):
                text = _truncate(chunk.get("content") or "", 260)
                if text:
                    facts.append(text)
                    sources.append(
                        {
                            "source_id": f"img_fact_internal_{idx}",
                            "title": chunk.get("title") or f"Internal fact {idx}",
                            "url": chunk.get("url"),
                            "snippet": text,
                            "platform": "internal",
                        }
                    )
        except Exception as exc:
            logger.warning("Image identity targeted internal search failed: %s", exc)

        if allow_web:
            try:
                results = self.research_provider.search(
                    query,
                    creator_profile,
                    resource_type="web",
                    conversation_history=None,
                )[:4]
                for idx, result in enumerate(results, start=1):
                    snippet = _truncate(result.get("snippet") or result.get("text") or "", 220)
                    if snippet:
                        facts.append(f"{result.get('title')}: {snippet}")
                    sources.append(
                        {
                            "source_id": f"img_fact_web_{idx}",
                            "title": result.get("title") or f"Web fact {idx}",
                            "url": result.get("url"),
                            "snippet": snippet,
                            "platform": result.get("platform") or "web",
                        }
                    )
            except Exception as exc:
                logger.warning("Image identity targeted web search failed: %s", exc)

        return {
            "facts": _dedupe_strings(facts, limit=8),
            "sources": sources,
        }

    def _synthesize_confirmed_answer(
        self,
        question: str,
        creator_profile: Dict[str, Any],
        observation: Dict[str, Any],
        match: Dict[str, Any],
        facts: List[str],
    ) -> str:
        creator_name = creator_profile.get("name") or creator_profile.get("handle") or "the creator"
        soul_md = str(creator_profile.get("soul_md") or "")[:3000]
        prompt = f"""
You are {creator_name}.

Creator persona reference:
{soul_md}

User question: {question}
Visual observation: {json.dumps(observation)}
Matched public identity: {json.dumps(match)}
Supported public facts: {json.dumps(facts)}

Write a short answer in the creator's voice.
Rules:
- Max 2 sentences.
- Answer in first person if it fits the creator voice.
- Only mention facts that appear in the supported public facts or matched identity.
- Do not invent dates, history, or private details.
- If support is decent but not perfect, soften slightly instead of sounding absolute.
""".strip()
        try:
            answer = rag.generate_chat_completion(
                messages=[{"role": "system", "content": prompt}],
                model=settings.FINAL_RESPONSE_MODEL,
                temperature=0.4,
            )
            if isinstance(answer, str) and answer.strip():
                return answer.strip()
        except Exception as exc:
            logger.warning("Image identity confirmed synthesis failed: %s", exc)
        relationship = match.get("relationship") or "someone close to me"
        return f"That looks like {match.get('matched_name')}. Publicly, they've been associated with me as {relationship}."

    def _synthesize_uncertain_answer(
        self,
        question: str,
        creator_profile: Dict[str, Any],
        observation: Dict[str, Any],
        candidates: List[Dict[str, Any]],
    ) -> str:
        creator_name = creator_profile.get("name") or creator_profile.get("handle") or "the creator"
        soul_md = str(creator_profile.get("soul_md") or "")[:2500]
        candidate_list = [f"{c.get('name')} ({c.get('relationship')})" for c in candidates[:4] if c.get("name")]
        prompt = f"""
You are {creator_name}.

Creator persona reference:
{soul_md}

User question: {question}
Visual observation: {json.dumps(observation)}
Possible public candidates: {json.dumps(candidate_list)}

Write a short answer in the creator's voice that does NOT pretend to know who the person is.
Rules:
- Max 2 sentences.
- Say what you can genuinely tell from the image.
- If you are unsure, say that naturally and invite the user to give more context.
- Do not invent names or relationships.
""".strip()
        try:
            answer = rag.generate_chat_completion(
                messages=[{"role": "system", "content": prompt}],
                model=settings.FINAL_RESPONSE_MODEL,
                temperature=0.4,
            )
            if isinstance(answer, str) and answer.strip():
                return answer.strip()
        except Exception as exc:
            logger.warning("Image identity uncertain synthesis failed: %s", exc)
        return "I can tell you what I see in the photo, but I wouldn't want to pretend I know exactly who that is from the image alone."


image_identity_service = ImageIdentityService()
