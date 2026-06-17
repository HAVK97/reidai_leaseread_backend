"""LLM provider abstraction for the extraction stage.

All providers implement the LLMProvider Protocol via structured_completion().
Provider SDK imports are LAZY — only the provider you actually use needs to be
installed. Required env vars:
  - Gemini:    GEMINI_API_KEY
  - OpenAI:    OPENAI_API_KEY
  - Anthropic: ANTHROPIC_API_KEY

Usage:
  provider = build_provider("gemini", model="gemini-2.5-flash", temperature=0)
  response = provider.structured_completion(request)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol


# Gemini rejects empty strings inside ``enum`` arrays ("enum[N]: cannot be empty").
# We substitute a sentinel in the schema only for Gemini; responses are normalized back.
GEMINI_BLANK_ENUM_SENTINEL = "__REIDAI_BLANK__"


# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------


@dataclass
class LLMRequest:
    """Everything needed for one structured extraction call."""

    system_prompt: str
    user_prompt: str
    json_schema: dict  # JSON Schema dict describing expected response
    model: str
    temperature: float = 0
    max_output_tokens: int = 8192
    # Metadata (not sent to model, used for logging)
    group_id: str = ""
    prompt_id: str = ""
    prompt_version: str = "1.0"


@dataclass
class LLMResponse:
    """Parsed response from a structured completion call."""

    content: dict  # parsed JSON matching the schema
    raw_text: str  # raw response text before JSON extraction
    provider: str
    model: str
    usage: dict = field(default_factory=dict)  # token counts if available
    error: str | None = None


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class LLMProvider(Protocol):
    """Stable interface all providers implement."""

    provider_name: str
    model: str

    def structured_completion(self, request: LLMRequest) -> LLMResponse:
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_unsupported_json_schema_for_gemini(obj: Any) -> Any:
    """Gemini structured-output Schema rejects some JSON Schema draft keywords.

    Passing ``additionalProperties`` raises: Unknown field for Schema: additionalProperties.
    Empty string cannot appear as an ``enum`` member; replace with
    :data:`GEMINI_BLANK_ENUM_SENTINEL`. OpenAI/Anthropic keep the full schema unchanged.
    """
    unsupported = frozenset({"additionalProperties", "$schema", "$defs", "definitions"})
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in unsupported:
                continue
            if k == "enum" and isinstance(v, list):
                out[k] = [
                    GEMINI_BLANK_ENUM_SENTINEL
                    if x == ""
                    else _strip_unsupported_json_schema_for_gemini(x)
                    for x in v
                ]
            else:
                out[k] = _strip_unsupported_json_schema_for_gemini(v)
        return out
    if isinstance(obj, list):
        return [_strip_unsupported_json_schema_for_gemini(x) for x in obj]
    return obj


def _normalize_gemini_blank_sentinel(content: dict) -> None:
    """In-place: map sentinel back to empty string for each field's ``value``."""
    for field in content.values():
        if not isinstance(field, dict):
            continue
        if field.get("value") == GEMINI_BLANK_ENUM_SENTINEL:
            field["value"] = ""


def _extract_json_from_text(text: str) -> str:
    """Pull first JSON object from text that may have markdown fences."""
    # Strip markdown code fence if present
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    # Otherwise try to find the first {...}
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def _gemini_response_text(response: Any) -> str:
    """Return text parts without triggering SDK warnings for thought parts."""
    chunks: list[str] = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            text = getattr(part, "text", None)
            if text:
                chunks.append(text)
    if chunks:
        return "".join(chunks)
    return getattr(response, "text", "") or ""


# ---------------------------------------------------------------------------
# Fake provider (for tests — no API calls)
# ---------------------------------------------------------------------------


class FakeProvider:
    """Returns canned empty responses without hitting any API."""

    provider_name = "fake"

    def __init__(self, model: str = "fake-model-v1", canned_response: dict | None = None):
        self.model = model
        self._canned = canned_response or {}

    def structured_completion(self, request: LLMRequest) -> LLMResponse:
        if self._canned:
            return LLMResponse(
                content=self._canned,
                raw_text=json.dumps(self._canned),
                provider=self.provider_name,
                model=self.model,
            )
        # Build a minimal valid response with status=unclear for every field
        # by looking at the schema's top-level properties
        props = request.json_schema.get("properties", {})
        empty: dict[str, Any] = {}
        for field_id in props:
            empty[field_id] = {
                "value": "",
                "status": "unclear",
                "quotes": [],
                "reasoning": "Fake provider — no real extraction performed.",
                "confidence": "low",
            }
        raw = json.dumps(empty)
        return LLMResponse(
            content=empty,
            raw_text=raw,
            provider=self.provider_name,
            model=self.model,
        )


# ---------------------------------------------------------------------------
# Gemini provider
# ---------------------------------------------------------------------------


class GeminiProvider:
    provider_name = "gemini"

    def __init__(self, model: str, temperature: float = 0, max_output_tokens: int = 8192):
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    def _client(self):
        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore
        except ImportError as e:
            raise ImportError(
                "google-genai is required for Gemini. "
                "Install with: pip install google-genai"
            ) from e
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "GEMINI_API_KEY environment variable not set."
            )
        return genai.Client(api_key=api_key), types

    def structured_completion(self, request: LLMRequest) -> LLMResponse:
        client, types = self._client()

        gemini_schema = _strip_unsupported_json_schema_for_gemini(request.json_schema)

        generation_config = types.GenerateContentConfig(
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            response_mime_type="application/json",
            response_schema=gemini_schema,
            system_instruction=request.system_prompt,
            # Gemini 3 can otherwise spend the output cap on hidden thinking and
            # return truncated JSON for larger schemas.
            thinking_config=types.ThinkingConfig(
                thinkingBudget=0,
                includeThoughts=False,
            ),
        )

        raw_text = ""
        response = None
        last_error: json.JSONDecodeError | None = None
        content: dict[str, Any] = {}
        for _attempt in range(2):
            response = client.models.generate_content(
                model=self.model,
                contents=request.user_prompt,
                config=generation_config,
            )
            raw_text = _gemini_response_text(response)

            try:
                content = json.loads(raw_text)
                last_error = None
                break
            except json.JSONDecodeError as e:
                content_str = _extract_json_from_text(raw_text)
                try:
                    content = json.loads(content_str)
                    last_error = None
                    break
                except json.JSONDecodeError as inner:
                    last_error = inner or e

        if last_error is not None:
            return LLMResponse(
                content={},
                raw_text=raw_text,
                provider=self.provider_name,
                model=self.model,
                error=f"JSON parse failed: {last_error}",
            )

        if isinstance(content, dict):
            _normalize_gemini_blank_sentinel(content)

        usage = {}
        if hasattr(response, "usage_metadata"):
            um = response.usage_metadata
            usage = {
                "input_tokens": getattr(um, "prompt_token_count", None),
                "output_tokens": getattr(um, "candidates_token_count", None),
            }

        return LLMResponse(
            content=content,
            raw_text=raw_text,
            provider=self.provider_name,
            model=self.model,
            usage=usage,
        )


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------


class OpenAIProvider:
    provider_name = "openai"

    def __init__(self, model: str, temperature: float = 0, max_output_tokens: int = 8192):
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    def _client(self):
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as e:
            raise ImportError(
                "openai is required for OpenAI. Install with: pip install openai"
            ) from e
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY environment variable not set.")
        return OpenAI(api_key=api_key)

    def structured_completion(self, request: LLMRequest) -> LLMResponse:
        client = self._client()

        completion_kwargs = {
            "model": self.model,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": request.group_id or "extraction",
                    "schema": request.json_schema,
                    "strict": True,
                },
            },
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
        }
        if self.model.startswith("gpt-5"):
            completion_kwargs["max_completion_tokens"] = self.max_output_tokens
        else:
            completion_kwargs["temperature"] = self.temperature
            completion_kwargs["max_tokens"] = self.max_output_tokens

        response = client.chat.completions.create(**completion_kwargs)

        raw_text = response.choices[0].message.content or ""
        usage = {}
        if response.usage:
            usage = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            }

        try:
            content = json.loads(raw_text)
        except json.JSONDecodeError:
            content_str = _extract_json_from_text(raw_text)
            try:
                content = json.loads(content_str)
            except json.JSONDecodeError as e:
                return LLMResponse(
                    content={},
                    raw_text=raw_text,
                    provider=self.provider_name,
                    model=self.model,
                    usage=usage,
                    error=f"JSON parse failed: {e}",
                )

        return LLMResponse(
            content=content,
            raw_text=raw_text,
            provider=self.provider_name,
            model=self.model,
            usage=usage,
        )


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------


class AnthropicProvider:
    provider_name = "anthropic"

    def __init__(self, model: str, temperature: float = 0, max_output_tokens: int = 8192):
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    def _client(self):
        try:
            import anthropic  # type: ignore
        except ImportError as e:
            raise ImportError(
                "anthropic is required for Claude. Install with: pip install anthropic"
            ) from e
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY environment variable not set.")
        return anthropic.Anthropic(api_key=api_key)

    def structured_completion(self, request: LLMRequest) -> LLMResponse:
        client = self._client()

        # Claude doesn't support JSON schema mode natively in all versions;
        # we append schema instructions to the user prompt and parse the response.
        schema_hint = (
            "\n\nIMPORTANT: Respond with a single JSON object only. "
            "No prose. The JSON must match this schema:\n"
            + json.dumps(request.json_schema, indent=2)
        )

        response = client.messages.create(
            model=self.model,
            max_tokens=self.max_output_tokens,
            temperature=self.temperature,
            system=request.system_prompt,
            messages=[
                {"role": "user", "content": request.user_prompt + schema_hint},
            ],
        )

        raw_text = response.content[0].text if response.content else ""
        usage = {}
        if response.usage:
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }

        content_str = _extract_json_from_text(raw_text)
        try:
            content = json.loads(content_str)
        except json.JSONDecodeError as e:
            return LLMResponse(
                content={},
                raw_text=raw_text,
                provider=self.provider_name,
                model=self.model,
                usage=usage,
                error=f"JSON parse failed: {e}",
            )

        return LLMResponse(
            content=content,
            raw_text=raw_text,
            provider=self.provider_name,
            model=self.model,
            usage=usage,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_provider(
    provider: str,
    model: str,
    temperature: float = 0,
    max_output_tokens: int = 8192,
    canned_response: dict | None = None,
) -> LLMProvider:
    """Instantiate a provider by name string.

    Args:
        provider:  'gemini' | 'openai' | 'anthropic' | 'fake'
        model:     model id string
        temperature:  sampling temp (0 = deterministic)
        max_output_tokens: response length cap
        canned_response:  only used by 'fake' provider for tests
    """
    match provider:
        case "gemini":
            return GeminiProvider(model=model, temperature=temperature, max_output_tokens=max_output_tokens)
        case "openai":
            return OpenAIProvider(model=model, temperature=temperature, max_output_tokens=max_output_tokens)
        case "anthropic":
            return AnthropicProvider(model=model, temperature=temperature, max_output_tokens=max_output_tokens)
        case "fake":
            return FakeProvider(model=model, canned_response=canned_response)
        case _:
            raise ValueError(
                f"Unknown provider {provider!r}. Available: gemini, openai, anthropic, fake"
            )


def build_provider_from_profile(profile: dict, canned_response: dict | None = None) -> LLMProvider:
    """Build a provider from a model_profiles.json profile dict."""
    return build_provider(
        provider=profile["provider"],
        model=profile["model"],
        temperature=profile.get("temperature", 0),
        max_output_tokens=profile.get("max_output_tokens", 8192),
        canned_response=canned_response,
    )
