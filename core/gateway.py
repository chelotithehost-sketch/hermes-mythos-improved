"""
Hermes-Mythos Gateway — LLM provider abstraction with retry and fallback.

Provides a unified interface to call any supported LLM provider with:
- Exponential backoff retry (max 3 attempts)
- Automatic fallback through a provider chain
- Structured error handling
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx

from core.config import Config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class GatewayError(Exception):
    """Base error for gateway operations."""


class ProviderError(GatewayError):
    """A specific provider failed after all retries."""

    def __init__(self, provider: str, message: str, status_code: int = 0):
        self.provider = provider
        self.status_code = status_code
        super().__init__(f"[{provider}] {message}")


class AllProvidersFailedError(GatewayError):
    """Every provider in the fallback chain failed."""

    def __init__(self, errors: List[Tuple[str, Exception]]):
        self.errors = errors
        detail = "; ".join(f"{p}: {e}" for p, e in errors)
        super().__init__(f"All providers failed: {detail}")


class RateLimitError(ProviderError):
    """Provider returned 429 Too Many Requests."""


# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------

MAX_RETRIES = 3
BASE_DELAY = 1.0  # seconds


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------

@dataclass
class Gateway:
    """Unified LLM gateway with retry and fallback.

    Usage:
        gateway = Gateway(cfg)
        response = await gateway.complete(
            messages=[{"role": "user", "content": "Hello"}],
            provider="openai",
            model="gpt-4o",
        )
        # Or with fallback:
        response = await gateway.complete_with_fallback(
            messages=[{"role": "user", "content": "Hello"}],
        )
    """

    cfg: Config
    _http_client: httpx.AsyncClient = field(default=None, repr=False)

    def __post_init__(self):
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=120.0)

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    async def complete(
        self,
        messages: List[Dict[str, str]],
        provider: str = "openai",
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> str:
        """Call a single provider with retry logic.

        Args:
            messages: Chat messages in OpenAI format.
            provider: Provider name (openai, anthropic, etc.).
            model: Model override; defaults to provider default.
            temperature: Sampling temperature.
            max_tokens: Maximum response tokens.

        Returns:
            The assistant's response text.

        Raises:
            ProviderError: If all retries are exhausted.
        """
        model = model or self.cfg.model_for(provider)
        last_error: Optional[Exception] = None

        for attempt in range(MAX_RETRIES):
            try:
                return await self._call_provider(
                    messages=messages,
                    provider=provider,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
            except RateLimitError as e:
                last_error = e
                delay = BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "[%s] Rate limited, retry %d/%d in %.1fs",
                    provider, attempt + 1, MAX_RETRIES, delay,
                )
                await asyncio.sleep(delay)
            except ProviderError as e:
                last_error = e
                if e.status_code >= 500:
                    delay = BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "[%s] Server error %d, retry %d/%d in %.1fs",
                        provider, e.status_code, attempt + 1, MAX_RETRIES, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    # 4xx errors (except 429) are not retryable
                    raise
            except Exception as e:
                last_error = e
                delay = BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "[%s] Unexpected error, retry %d/%d in %.1fs: %s",
                    provider, attempt + 1, MAX_RETRIES, delay, e,
                )
                await asyncio.sleep(delay)

        raise ProviderError(provider, f"Failed after {MAX_RETRIES} retries: {last_error}")

    async def complete_with_fallback(
        self,
        messages: List[Dict[str, str]],
        preferred_provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> Tuple[str, str]:
        """Try providers in fallback chain until one succeeds.

        Args:
            messages: Chat messages.
            preferred_provider: Start with this provider, then fall back.
            model: Model override.
            temperature: Sampling temperature.
            max_tokens: Maximum response tokens.

        Returns:
            Tuple of (response_text, provider_used).

        Raises:
            AllProvidersFailedError: If every provider fails.
        """
        chain = self.cfg.active_fallback_chain
        if preferred_provider and preferred_provider in chain:
            chain = [preferred_provider] + [p for p in chain if p != preferred_provider]

        errors: List[Tuple[str, Exception]] = []

        for provider in chain:
            try:
                response = await self.complete(
                    messages=messages,
                    provider=provider,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                return response, provider
            except GatewayError as e:
                logger.warning("[%s] Failed, trying next: %s", provider, e)
                errors.append((provider, e))
            except Exception as e:
                logger.warning("[%s] Unexpected failure: %s", provider, e)
                errors.append((provider, e))

        raise AllProvidersFailedError(errors)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._http_client.aclose()

    # -------------------------------------------------------------------
    # Provider dispatch
    # -------------------------------------------------------------------

    async def _call_provider(
        self,
        messages: List[Dict[str, str]],
        provider: str,
        model: str,
        temperature: float,
        max_tokens: int,
        **kwargs,
    ) -> str:
        """Dispatch to the correct provider implementation."""
        dispatch = {
            "openai": self._call_openai,
            "anthropic": self._call_anthropic,
            "gemini": self._call_gemini,
            "mistral": self._call_mistral,
            "ollama": self._call_ollama,
        }
        handler = dispatch.get(provider)
        if handler is None:
            raise ProviderError(provider, f"Unknown provider: {provider}")
        return await handler(messages, model, temperature, max_tokens, **kwargs)

    # -------------------------------------------------------------------
    # OpenAI
    # -------------------------------------------------------------------

    async def _call_openai(
        self, messages: List[Dict], model: str, temperature: float, max_tokens: int, **kw
    ) -> str:
        api_key = self.cfg.openai_api_key
        if not api_key:
            raise ProviderError("openai", "API key not configured")

        resp = await self._http_client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        if resp.status_code == 429:
            raise RateLimitError("openai", "Rate limited", 429)
        if resp.status_code >= 400:
            raise ProviderError("openai", resp.text, resp.status_code)
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    # -------------------------------------------------------------------
    # Anthropic
    # -------------------------------------------------------------------

    async def _call_anthropic(
        self, messages: List[Dict], model: str, temperature: float, max_tokens: int, **kw
    ) -> str:
        api_key = self.cfg.anthropic_api_key
        if not api_key:
            raise ProviderError("anthropic", "API key not configured")

        # Convert from OpenAI format
        system_msg = ""
        anthropic_msgs = []
        for m in messages:
            if m["role"] == "system":
                system_msg += m["content"] + "\n"
            else:
                anthropic_msgs.append({"role": m["role"], "content": m["content"]})

        body: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": anthropic_msgs,
        }
        if system_msg.strip():
            body["system"] = system_msg.strip()

        resp = await self._http_client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
        )
        if resp.status_code == 429:
            raise RateLimitError("anthropic", "Rate limited", 429)
        if resp.status_code >= 400:
            raise ProviderError("anthropic", resp.text, resp.status_code)
        data = resp.json()
        return data["content"][0]["text"]

    # -------------------------------------------------------------------
    # Gemini
    # -------------------------------------------------------------------

    async def _call_gemini(
        self, messages: List[Dict], model: str, temperature: float, max_tokens: int, **kw
    ) -> str:
        api_key = self.cfg.gemini_api_key
        if not api_key:
            raise ProviderError("gemini", "API key not configured")

        # Convert to Gemini format
        contents = []
        for m in messages:
            role = "user" if m["role"] == "user" else "model"
            if m["role"] == "system":
                role = "user"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})

        resp = await self._http_client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
            json={
                "contents": contents,
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": max_tokens,
                },
            },
        )
        if resp.status_code == 429:
            raise RateLimitError("gemini", "Rate limited", 429)
        if resp.status_code >= 400:
            raise ProviderError("gemini", resp.text, resp.status_code)
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

    # -------------------------------------------------------------------
    # Mistral
    # -------------------------------------------------------------------

    async def _call_mistral(
        self, messages: List[Dict], model: str, temperature: float, max_tokens: int, **kw
    ) -> str:
        api_key = self.cfg.mistral_api_key
        if not api_key:
            raise ProviderError("mistral", "API key not configured")

        resp = await self._http_client.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        if resp.status_code == 429:
            raise RateLimitError("mistral", "Rate limited", 429)
        if resp.status_code >= 400:
            raise ProviderError("mistral", resp.text, resp.status_code)
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    # -------------------------------------------------------------------
    # Ollama (local)
    # -------------------------------------------------------------------

    async def _call_ollama(
        self, messages: List[Dict], model: str, temperature: float, max_tokens: int, **kw
    ) -> str:
        base_url = self.cfg.ollama_base_url.rstrip("/")
        try:
            resp = await self._http_client.post(
                f"{base_url}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    },
                },
            )
        except httpx.ConnectError as e:
            raise ProviderError("ollama", f"Cannot connect to Ollama at {base_url}: {e}")

        if resp.status_code >= 400:
            raise ProviderError("ollama", resp.text, resp.status_code)
        data = resp.json()
        return data["message"]["content"]
