"""Gemini-first image understanding for chat attachments."""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.settings import settings

logger = logging.getLogger(__name__)

DATA_URL_RE = re.compile(r"^data:(?P<mime>image/[a-zA-Z0-9.+-]+);base64,(?P<data>.+)$", re.DOTALL)


def _extract_text(response: Any) -> str:
    try:
        text = getattr(response, "text", None)
    except Exception:
        text = None
    if isinstance(text, str):
        return text
    parts: List[str] = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            value = getattr(part, "text", None)
            if value:
                parts.append(str(value))
    return "".join(parts)


def _extract_json(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    if not (text.startswith("{") and text.endswith("}")):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _fallback_observation(reason: str) -> Dict[str, Any]:
    return {
        "summary": "I could not read the image clearly enough to analyze it reliably.",
        "visual_domain": "unknown",
        "primary_subject": "unknown",
        "person_count": 0,
        "people": [],
        "visible_text": [],
        "identity_clues": [],
        "direct_answer_hint": "Ask for a clearer upload or describe the part of the image you want analyzed.",
        "chart_analysis": {},
        "confidence": 0.12,
        "uncertainties": [reason],
        "provider": "gemini",
    }


class GeminiVisionService:
    def __init__(self, client: Any = None):
        self._client = client
        self.api_key = settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY

    def enabled(self) -> bool:
        return bool(self._client or self.api_key)

    def analyze_images(
        self,
        *,
        question: str,
        images: List[Dict[str, Any]],
        creator_profile: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not images:
            return _fallback_observation("No image was attached.")
        if not self.enabled():
            return _fallback_observation("GEMINI_API_KEY is not configured.")

        prompt = self._analysis_prompt(question, creator_profile or {})
        try:
            contents = self._build_contents(prompt, images)
            raw = self._generate_json(contents, max_tokens=1400)
            data = _extract_json(raw)
            if data:
                data.setdefault("provider", "gemini")
                data.setdefault("model", settings.GEMINI_VISION_MODEL)
                data.setdefault("visible_text", [])
                data.setdefault("identity_clues", [])
                data.setdefault("people", [])
                data.setdefault("chart_analysis", {})
                data.setdefault("confidence", 0.5)
                return data
        except Exception as exc:
            logger.warning("Gemini image analysis failed: %s", exc)
            return _fallback_observation(str(exc))
        return _fallback_observation("Gemini returned an empty image analysis.")

    def match_candidates(
        self,
        *,
        question: str,
        images: List[Dict[str, Any]],
        observation: Dict[str, Any],
        candidates: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not images or not candidates:
            return {"matched_name": None, "confidence": 0.0, "relationship": "", "reason": "no_images_or_candidates"}
        candidate_payload = [
            {
                "name": c.get("name"),
                "relationship": c.get("relationship"),
                "support": c.get("support"),
            }
            for c in candidates[:6]
        ]
        prompt = f"""
You are matching an attached image against a constrained candidate list.
Prefer "unknown" over guessing.

User question:
{question}

Prior visual observation:
{json.dumps(observation, ensure_ascii=False)}

Candidate list:
{json.dumps(candidate_payload, ensure_ascii=False)}

Return JSON only:
{{
  "matched_name": "string or null",
  "relationship": "string",
  "confidence": 0.0,
  "reason": "short visual/evidence reasoning",
  "disclaimer_required": true
}}

Rules:
- Do not identify someone unless the visible image and candidate evidence support it.
- If the face/image is too small, unclear, cropped, or indirect, return matched_name=null.
- Keep confidence conservative.
""".strip()
        try:
            contents = self._build_contents(prompt, images)
            parsed = _extract_json(self._generate_json(contents, max_tokens=700))
            if parsed:
                return parsed
        except Exception as exc:
            logger.warning("Gemini candidate image match failed: %s", exc)
        return {"matched_name": None, "confidence": 0.0, "relationship": "", "reason": "gemini_match_failed"}

    def _analysis_prompt(self, question: str, creator_profile: Dict[str, Any]) -> str:
        creator_name = creator_profile.get("name") or creator_profile.get("handle") or "the creator"
        creator_category = creator_profile.get("creator_category") or "general"
        return f"""
You are the Gemini vision layer for a creator-chat product.
Analyze only what is visible in the attached image(s), then produce compact structured evidence for another model to answer in {creator_name}'s voice.

Creator category: {creator_category}
User question: {question or "Describe this image and point out anything important."}

Return JSON only with this shape:
{{
  "summary": "short factual visual summary",
  "visual_domain": "short open label in your words, e.g. trading_chart, text_screenshot, person, meal, product, scene, document, other",
  "primary_subject": "short label",
  "person_count": 0,
  "people": [
    {{"gender_presentation": "if visible", "age_band": "if visible", "notable_features": ["visual facts only"]}}
  ],
  "visible_text": ["OCR snippets, labels, ticker/timeframe if visible"],
  "identity_clues": ["non-name clues only"],
  "direct_answer_hint": "plain-English answer to the user's visual question, if possible",
  "chart_analysis": {{
    "chart_type": "candlestick/line/etc if visible",
    "visible_setup": "pattern/setup visible in the chart",
    "trend_context": "what price is doing visually",
    "key_levels": ["support/resistance/trendlines/liquidity zones visible"],
    "possible_trade_read": "conservative interpretation, not financial advice",
    "invalidation_or_risk": "what would make the setup fail visually",
    "confidence": 0.0
  }},
  "uncertainties": ["what cannot be read from the image"],
  "confidence": 0.0
}}

Special rules for trading/chart screenshots:
- Read visible annotations, trendlines, support/resistance zones, swing highs/lows, breakout/retest, liquidity sweep, rejection, continuation, and reversal clues.
- Do not invent ticker, timeframe, entry, stop, or target if not visible.
- Say when the screenshot is too small/cropped to read labels.
- This is educational chart interpretation, not financial advice.

General rules:
- Never rely on creator transcripts to describe the image; this pass is visual only.
- Do not name or identify a real person from face alone.
- Use your own visual reasoning for the domain label. Do not force the image into a fixed taxonomy when a more precise label is obvious.
- Be precise about uncertainty.
""".strip()

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured.")
        from google import genai

        try:
            from google.genai import types

            timeout_seconds = float(getattr(settings, "GEMINI_VISION_TIMEOUT_SECONDS", 12.0))
            http_options = types.HttpOptions(timeout=int(timeout_seconds * 1000))
            self._client = genai.Client(api_key=self.api_key, http_options=http_options)
        except Exception:
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _generate_json(self, contents: Any, *, max_tokens: int) -> str:
        client = self._get_client()
        resolution = str(getattr(settings, "GEMINI_VISION_MEDIA_RESOLUTION", "MEDIA_RESOLUTION_HIGH") or "").strip()
        config: Any = {
            "temperature": 0.0,
            "max_output_tokens": max_tokens,
            "response_mime_type": "application/json",
        }
        if resolution:
            config["media_resolution"] = resolution
        try:
            from google.genai import types

            media_resolution_enum = getattr(types, "MediaResolution", None)
            media_resolution = getattr(media_resolution_enum, resolution, None) if media_resolution_enum and resolution else None
            config_kwargs = dict(
                temperature=0.0,
                max_output_tokens=max_tokens,
                response_mime_type="application/json",
                safety_settings=[
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": settings.GEMINI_SAFETY_THRESHOLD},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": settings.GEMINI_SAFETY_THRESHOLD},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": settings.GEMINI_SAFETY_THRESHOLD},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": settings.GEMINI_SAFETY_THRESHOLD},
                ],
            )
            if media_resolution is not None:
                config_kwargs["media_resolution"] = media_resolution
            try:
                config = types.GenerateContentConfig(**config_kwargs)
            except TypeError:
                config_kwargs.pop("media_resolution", None)
                config = types.GenerateContentConfig(**config_kwargs)
        except Exception:
            pass
        try:
            response = client.models.generate_content(
                model=settings.GEMINI_VISION_MODEL,
                contents=contents,
                config=config,
            )
        except TypeError:
            if not (isinstance(config, dict) and "media_resolution" in config):
                raise
            retry_config = dict(config)
            retry_config.pop("media_resolution", None)
            response = client.models.generate_content(
                model=settings.GEMINI_VISION_MODEL,
                contents=contents,
                config=retry_config,
            )
        return _extract_text(response).strip()

    def _build_contents(self, prompt: str, images: List[Dict[str, Any]]) -> Any:
        image_parts: List[Any] = []
        for image in images[:1]:
            mime_type, raw_bytes = self._decode_data_url(image.get("data_url") or "")
            image_parts.append(self._image_part(mime_type, raw_bytes))
        if image_parts and all(not isinstance(part, dict) for part in image_parts):
            return [prompt, *image_parts]
        return {
            "role": "user",
            "parts": [
                {"text": prompt},
                *image_parts,
            ],
        }

    def _image_part(self, mime_type: str, raw_bytes: bytes) -> Any:
        try:
            from google.genai import types

            return types.Part.from_bytes(data=raw_bytes, mime_type=mime_type)
        except Exception:
            return {
                "inline_data": {
                    "mime_type": mime_type,
                    "data": base64.b64encode(raw_bytes).decode("ascii"),
                }
            }

    def _decode_data_url(self, data_url: str) -> Tuple[str, bytes]:
        match = DATA_URL_RE.match(str(data_url or ""))
        if not match:
            raise ValueError("Invalid image data URL.")
        mime_type = match.group("mime").lower()
        if mime_type not in {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/heic", "image/heif"}:
            raise ValueError(f"Unsupported image MIME type: {mime_type}")
        raw = base64.b64decode(match.group("data"), validate=True)
        max_bytes = int(getattr(settings, "GEMINI_VISION_MAX_INLINE_BYTES", 8_000_000))
        if len(raw) > max_bytes:
            raise ValueError(f"Image is too large for inline analysis ({len(raw)} bytes).")
        return ("image/jpeg" if mime_type == "image/jpg" else mime_type), raw


gemini_vision_service = GeminiVisionService()
