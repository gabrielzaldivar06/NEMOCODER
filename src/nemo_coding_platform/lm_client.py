"""LmClient — all LM Studio HTTP calls in one place.

Owns the Arc iGPU serialization semaphore. Every LM call goes through
_acquire()/_release() with a timeout so the server never zombies.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Any


class LmClient:
    """Wraps all LM Studio API calls. One instance per server process.

    _sem is class-level so all LmClient instances share one semaphore — this
    is the Arc iGPU serialization guarantee. Creating multiple instances (e.g.
    for different base_urls) must not bypass the global inference lock.
    """

    _sem: threading.Semaphore = threading.Semaphore(1)

    def __init__(self, base_url: str, api_key: str = "lm-studio", cooldown: float = 1.5) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.cooldown = cooldown

    # ------------------------------------------------------------------
    # Semaphore helpers
    # ------------------------------------------------------------------

    def _acquire(self, timeout: float = 30.0) -> None:
        if not self._sem.acquire(timeout=timeout):
            raise RuntimeError(
                f"LLM semaphore busy after {timeout}s — LM Studio may be hung."
            )

    def _release(self) -> None:
        self._sem.release()

    # ------------------------------------------------------------------
    # Chat completion
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        timeout: float = 180.0,
        acquire_timeout: float = 30.0,
        extra_body: dict[str, Any] | None = None,
        with_cooldown: bool = False,
    ) -> str:
        """Send a chat/completions request. Returns the assistant content string."""
        body: dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if extra_body:
            body.update(extra_body)

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        self._acquire(timeout=acquire_timeout)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        finally:
            self._release()
        # Cooldown after release so other callers aren't blocked during the sleep.
        if with_cooldown:
            time.sleep(self.cooldown)

        choices = payload.get("choices") or []
        if not choices:
            raise ValueError("LmClient.chat: no choices in response")
        return str(choices[0].get("message", {}).get("content", ""))

    # ------------------------------------------------------------------
    # Model resolver
    # ------------------------------------------------------------------

    def resolve_model(self) -> str | None:
        """Return the best loaded chat model name, or None."""
        mgmt_base = self.base_url.rsplit("/v1", 1)[0]
        try:
            req = urllib.request.Request(f"{mgmt_base}/api/v0/models")
            with urllib.request.urlopen(req, timeout=5) as r:
                models = json.loads(r.read().decode("utf-8")).get("data", [])
            loaded = [
                m for m in models
                if m.get("state") == "loaded"
                and m.get("type") in ("llm", "vlm")
                and not any(x in m.get("id", "").lower() for x in ("embed", "rerank", "bge", "nomic"))
            ]
            loaded.sort(key=lambda m: m.get("loaded_context_length", 0), reverse=True)
            return loaded[0]["id"] if loaded else None
        except Exception:
            pass
        # Fallback: /v1/models
        try:
            req = urllib.request.Request(f"{self.base_url}/models")
            with urllib.request.urlopen(req, timeout=5) as r:
                models = json.loads(r.read().decode("utf-8")).get("data", [])
            chat = [
                m for m in models
                if not any(x in m.get("id", "").lower() for x in ("embed", "rerank", "bge", "nomic"))
            ]
            return chat[0]["id"] if chat else None
        except Exception:
            return None
