"""Provider-based LLM access for Gemini analysis and configurable chat."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Type

from pydantic import BaseModel

from backend.settings import settings

logger = logging.getLogger(__name__)


class LLMProviderError(RuntimeError):
    pass


@dataclass
class _Delta:
    content: str


@dataclass
class _Choice:
    delta: _Delta


@dataclass
class OpenAIStyleStreamChunk:
    """Tiny adapter so existing stream extraction works for Gemini chunks."""

    text: str

    @property
    def choices(self) -> List[_Choice]:
        return [_Choice(delta=_Delta(content=self.text))]


def selected_chat_provider() -> str:
    provider = (settings.CHAT_PROVIDER or "gemini").strip().lower()
    return provider if provider in {"gemini", "openai"} else "gemini"


def _messages_to_prompt(messages: List[Dict[str, str]]) -> tuple[str, str]:
    system_parts: List[str] = []
    turn_parts: List[str] = []
    for message in messages or []:
        role = str(message.get("role") or "user").lower()
        content = str(message.get("content") or "")
        if not content:
            continue
        if role == "system":
            system_parts.append(content)
        else:
            turn_parts.append(f"{role.upper()}:\n{content}")
    return "\n\n".join(system_parts), "\n\n".join(turn_parts)


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


def _extract_json_text(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    if cleaned.startswith("{") or cleaned.startswith("["):
        return cleaned
    match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
    if match:
        return match.group(1)
    return cleaned


def _model_json_schema(schema_model: Type[BaseModel]) -> Dict[str, Any]:
    if hasattr(schema_model, "model_json_schema"):
        return schema_model.model_json_schema()
    return schema_model.schema()


def _model_validate(schema_model: Type[BaseModel], value: Any) -> BaseModel:
    if hasattr(schema_model, "model_validate"):
        return schema_model.model_validate(value)
    return schema_model.parse_obj(value)


class GeminiLLMProvider:
    name = "gemini"

    def __init__(self, api_key: Optional[str] = None, client: Any = None):
        self.api_key = api_key or settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY
        self._client = client

    @property
    def enabled(self) -> bool:
        return bool(self.api_key or self._client)

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise LLMProviderError("GEMINI_API_KEY is not configured.")
        try:
            from google import genai
        except Exception as exc:  # pragma: no cover - exercised only without dependency
            raise LLMProviderError("google-genai is not installed. Install backend requirements.") from exc
        self._client = genai.Client(api_key=self.api_key)
        return self._client

    @staticmethod
    def safety_settings(threshold: Optional[str] = None) -> List[Dict[str, str]]:
        threshold = (threshold or settings.GEMINI_SAFETY_THRESHOLD or "BLOCK_MEDIUM_AND_ABOVE").strip()
        categories = [
            "HARM_CATEGORY_HARASSMENT",
            "HARM_CATEGORY_HATE_SPEECH",
            "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "HARM_CATEGORY_DANGEROUS_CONTENT",
        ]
        return [{"category": category, "threshold": threshold} for category in categories]

    def _config(
        self,
        *,
        system_instruction: str = "",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        json_mode: bool = False,
    ) -> Dict[str, Any]:
        config: Dict[str, Any] = {
            "temperature": temperature,
            "safety_settings": self.safety_settings(),
        }
        if system_instruction:
            config["system_instruction"] = system_instruction
        if max_tokens:
            config["max_output_tokens"] = max_tokens
        if json_mode or response_schema:
            config["response_mime_type"] = "application/json"
        if response_schema:
            config["response_json_schema"] = response_schema
        return config

    def generate_text(
        self,
        *,
        messages: Optional[List[Dict[str, str]]] = None,
        prompt: str = "",
        system_instruction: str = "",
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
        response_schema: Optional[Dict[str, Any]] = None,
        stream: bool = False,
    ) -> Any:
        client = self._get_client()
        if messages is not None:
            system_from_messages, contents = _messages_to_prompt(messages)
            system_instruction = "\n\n".join([p for p in [system_from_messages, system_instruction] if p])
        else:
            contents = prompt
        config = self._config(
            system_instruction=system_instruction,
            temperature=temperature,
            max_tokens=max_tokens,
            response_schema=response_schema,
            json_mode=json_mode,
        )
        target_model = model or settings.GEMINI_CHAT_MODEL
        if stream:
            return self._stream_text(client, target_model, contents, config)
        response = client.models.generate_content(
            model=target_model,
            contents=contents,
            config=config,
        )
        return _extract_text(response).strip()

    def _stream_text(self, client: Any, model: str, contents: str, config: Dict[str, Any]) -> Iterable[OpenAIStyleStreamChunk]:
        for chunk in client.models.generate_content_stream(model=model, contents=contents, config=config):
            text = _extract_text(chunk)
            if text:
                yield OpenAIStyleStreamChunk(text=text)

    async def generate_text_async(self, **kwargs: Any) -> Any:
        if kwargs.get("stream"):
            return self._stream_text_async(**kwargs)
        return await asyncio.to_thread(self.generate_text, **kwargs)

    async def _stream_text_async(self, **kwargs: Any) -> AsyncIterator[OpenAIStyleStreamChunk]:
        kwargs = dict(kwargs)
        kwargs["stream"] = True
        chunks = await asyncio.to_thread(lambda: list(self.generate_text(**kwargs)))
        for chunk in chunks:
            yield chunk

    def generate_json(
        self,
        *,
        system_instruction: str,
        prompt: str,
        schema_model: Optional[Type[BaseModel]] = None,
        schema: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        temperature: float = 0.2,
        repair_label: str = "JSON",
    ) -> Any:
        response_schema = schema or (_model_json_schema(schema_model) if schema_model else None)
        raw = self.generate_text(
            prompt=prompt,
            system_instruction=system_instruction,
            model=model or settings.GEMINI_ANALYSIS_MODEL,
            temperature=temperature,
            json_mode=True,
            response_schema=response_schema,
        )
        try:
            parsed = json.loads(_extract_json_text(raw))
            return _model_validate(schema_model, parsed) if schema_model else parsed
        except Exception as exc:
            logger.warning("Gemini returned malformed %s. Raw output: %s", repair_label, raw)
            repair_prompt = f"""
The previous response was invalid for {repair_label}.
Return only corrected JSON matching the schema. Do not add commentary.

Validation error:
{exc}

Invalid output:
{raw}
"""
            repaired = self.generate_text(
                prompt=repair_prompt,
                system_instruction=system_instruction,
                model=model or settings.GEMINI_ANALYSIS_MODEL,
                temperature=0.0,
                json_mode=True,
                response_schema=response_schema,
            )
            try:
                parsed = json.loads(_extract_json_text(repaired))
                return _model_validate(schema_model, parsed) if schema_model else parsed
            except Exception as repair_exc:
                logger.error("Gemini JSON repair failed. Raw repaired output: %s", repaired)
                raise LLMProviderError(f"Gemini returned invalid {repair_label}: {repair_exc}") from repair_exc

    async def generate_json_async(self, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.generate_json, **kwargs)


def get_gemini_provider(client: Any = None) -> GeminiLLMProvider:
    return GeminiLLMProvider(client=client)
