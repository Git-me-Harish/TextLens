"""Provider-neutral LLM calls for classification and document extraction.

Agentic actions intentionally use the Anthropic SDK separately. This module is
for JSON document intelligence only and uses OpenAI-compatible providers.
"""
import json
from typing import Any

import httpx

from app.core.config import settings


def _provider_config() -> tuple[str, str, str]:
    provider = settings.EXTRACTION_LLM_PROVIDER.lower()
    if provider == "deepseek":
        return (
            settings.DEEPSEEK_API_KEY,
            settings.DEEPSEEK_BASE_URL,
            settings.EXTRACTION_LLM_MODEL or "deepseek-chat",
        )
    if provider == "groq":
        return (
            settings.GROQ_API_KEY,
            settings.GROQ_BASE_URL,
            settings.EXTRACTION_LLM_MODEL or "openai/gpt-oss-120b",
        )
    raise RuntimeError(f"Unsupported extraction LLM provider: {provider}")


async def json_completion(
    *,
    system: str,
    user: str,
    max_tokens: int,
    timeout: float,
) -> dict[str, Any]:
    """Return a parsed JSON object from the configured extraction provider."""
    api_key, base_url, model = _provider_config()
    if not api_key:
        raise RuntimeError(
            f"{settings.EXTRACTION_LLM_PROVIDER.upper()} API key is not configured"
        )

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )

    if response.status_code >= 400:
        raise RuntimeError(
            f"{settings.EXTRACTION_LLM_PROVIDER} API error {response.status_code}: "
            f"{response.text[:300]}"
        )

    payload = response.json()
    content = payload["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content)
    return json.loads(content)
