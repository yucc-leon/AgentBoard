"""LLM provider module — OpenAI-compatible API client."""

import json
import os
import re
from typing import Any

import httpx

from agentboard.config import LLMConfig
from agentboard.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------

class LLMClient:
    """OpenAI-compatible chat completion client."""

    def __init__(self, config: LLMConfig):
        self.config = config
        api_key = config.api_key or os.environ.get(config.api_key_env, "")
        if not api_key:
            logger.warning("No LLM API key configured — summarization will be skipped")
        self._api_key = api_key
        self._base_url = config.base_url.rstrip("/")
        self._model = config.model

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 8192,
        timeout: float = 120.0,
        json_mode: bool = False,
    ) -> dict[str, Any] | None:
        """Send a chat completion request."""
        if not self.available:
            logger.warning("LLM not available (no API key)")
            return None

        url = f"{self._base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        # DeepSeek reasoning budget
        if self.config.reasoning_effort:
            payload["reasoning_effort"] = self.config.reasoning_effort

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                logger.debug("LLM response received (%d chars)", len(content))
                return {"content": content, "model": data.get("model"), "usage": data.get("usage")}
        except httpx.TimeoutException:
            logger.error("LLM request timed out after %.0fs", timeout)
            return None
        except httpx.HTTPStatusError as e:
            logger.error("LLM HTTP error %d", e.response.status_code)
            logger.debug("LLM error body (first 300): %s", e.response.text[:300])
            return None
        except Exception:
            logger.exception("LLM request failed")
            return None

    async def extract_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        timeout: float = 120.0,
    ) -> dict[str, Any] | None:
        """Send a request and extract a JSON object from the response."""
        result = await self.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            timeout=timeout,
            json_mode=True,  # 🔑 Force JSON output
        )
        if not result:
            return None

        content = result["content"]
        json_str = _extract_json_from_text(content)
        if json_str is None:
            logger.error("Could not extract JSON from LLM response")
            logger.debug("LLM raw (first 500): %s", content[:500])
            return None
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON from LLM: %s", e)
            logger.debug("JSON attempt (first 500): %s", json_str[:500])
            return None


def _extract_json_from_text(text: str) -> str | None:
    """Extract a JSON object string from LLM output (may be in code fences)."""
    # Strip ```json ... ``` fences (use greedy match for content)
    fence_match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fence_match:
        candidate = fence_match.group(1).strip()
        # Find JSON object within the fence content
        obj = _find_json_object(candidate)
        if obj:
            return obj

    # Try to find the outermost { ... } in the raw text
    return _find_json_object(text)


def _find_json_object(text: str) -> str | None:
    """Find a balanced JSON object {} in text. Handles truncated JSON."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == '\\' and in_string:
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    # Truncated JSON: try to auto-close
    if depth > 0 and not in_string:
        candidate = text[start:] + "}" * depth
        return candidate
    return None
