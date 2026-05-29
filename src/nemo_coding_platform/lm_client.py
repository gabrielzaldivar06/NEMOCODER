"""LmClient — all LM Studio HTTP calls in one place.

Owns the Arc iGPU serialization semaphore. Every LM call goes through
_acquire()/_release() with a timeout so the server never zombies.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Generator


class LmClientError(RuntimeError):
    """Raised for any LM Studio communication failure."""


_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _is_local_endpoint(base_url: str) -> bool:
    """Return True when base_url targets the local machine (LM Studio, Ollama, etc.).

    Remote cloud APIs (NVIDIA NIM, Anthropic, OpenAI, etc.) do not contend for
    the Arc iGPU Vulkan backend and must not be serialized by the local semaphore.
    """
    try:
        from urllib.parse import urlparse
        host = urlparse(base_url).hostname or ""
        return host in _LOCAL_HOSTS
    except Exception:
        return False


class LmClient:
    """Wraps all LM Studio API calls. One instance per server process.

    _sem is class-level so all LmClient instances share one semaphore — this
    is the Arc iGPU serialization guarantee. Creating multiple instances (e.g.
    for different base_urls) must not bypass the global inference lock.

    The semaphore is ONLY acquired for local endpoints (127.0.0.1, localhost).
    Cloud API calls (NVIDIA NIM, etc.) bypass the semaphore so they are never
    blocked by a long-running local plan-loop iteration.
    """

    _sem: threading.Semaphore = threading.Semaphore(1)

    def __init__(self, base_url: str, api_key: str = "lm-studio", cooldown: float = 1.5) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.cooldown = cooldown
        self._needs_sem: bool = _is_local_endpoint(self.base_url)
        # Set by chat_stream after the SSE stream ends. Inspect from the caller to
        # detect length-truncated completions and run a continuation pass.
        self.last_finish_reason: str | None = None

    # ------------------------------------------------------------------
    # Semaphore helpers
    # ------------------------------------------------------------------

    def _acquire(self, timeout: float = 30.0) -> None:
        if not self._needs_sem:
            return
        if not self._sem.acquire(timeout=timeout):
            raise RuntimeError(
                f"LLM semaphore busy after {timeout}s — LM Studio may be hung."
            )

    def _release(self) -> None:
        if self._needs_sem:
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
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                raise LmClientError(f"HTTP {exc.code} from LM Studio: {exc.reason}") from exc
            except urllib.error.URLError as exc:
                raise LmClientError(f"Cannot reach LM Studio ({self.base_url}): {exc.reason}") from exc
        finally:
            self._release()
        # Cooldown after release so other callers aren't blocked during the sleep.
        if with_cooldown:
            time.sleep(self.cooldown)

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LmClientError(f"LM Studio returned non-JSON response: {raw[:200]!r}") from exc

        choices = payload.get("choices") or []
        if not choices:
            raise LmClientError(f"LM Studio returned no choices: {str(payload)[:200]}")
        return str(choices[0].get("message", {}).get("content", ""))

    # ------------------------------------------------------------------
    # Streaming chat completion
    # ------------------------------------------------------------------

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        timeout: float = 180.0,
        acquire_timeout: float = 30.0,
        extra_body: dict[str, Any] | None = None,
    ) -> "Generator[str, None, None]":
        """Yield assistant content deltas as they arrive (OpenAI streaming format)."""
        body: dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
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
        self.last_finish_reason = None  # reset per-call
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                buffer = b""
                while True:
                    chunk = resp.read(512)
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        text = line.decode("utf-8", errors="replace").strip()
                        if not text.startswith("data:"):
                            continue
                        payload_str = text[5:].strip()
                        if payload_str == "[DONE]":
                            return
                        try:
                            obj = json.loads(payload_str)
                            choice = (obj.get("choices") or [{}])[0]
                            delta = (choice.get("delta") or {}).get("content")
                            fr = choice.get("finish_reason")
                            if fr:
                                self.last_finish_reason = str(fr)
                            if delta:
                                yield delta
                        except (json.JSONDecodeError, IndexError, KeyError):
                            pass
        except urllib.error.HTTPError as exc:
            raise LmClientError(f"HTTP {exc.code} streaming from LM: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise LmClientError(f"Cannot reach LM endpoint ({self.base_url}): {exc.reason}") from exc
        finally:
            self._release()
