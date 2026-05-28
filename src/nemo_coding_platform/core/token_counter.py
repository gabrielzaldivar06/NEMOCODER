"""Token counting fallback when the LLM provider doesn't report usage.

Provider-reported `token_usage.total_tokens` is always preferred. This module is the
fallback path for that case. It tries tiktoken (cl100k_base — good fit for OpenAI,
NIM and many local OpenAI-compatible servers); when tiktoken is unavailable or fails
we drop to a smarter heuristic that accounts for reasoning models emitting longer
output per char.

For reasoning models (qwen3.5, kimi-k2, DeepSeek-R1 family), the visible char count
under-estimates true token use by 2-3× because reasoning_content is invisible to the
consumer but still billed/counted. We bias the heuristic upward to limit drift.
"""
from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

_TIKTOKEN_ENC = None
_TIKTOKEN_TRIED = False


def _get_tiktoken_encoder():
    """Lazy-load tiktoken cl100k_base encoder. Returns None if tiktoken unavailable."""
    global _TIKTOKEN_ENC, _TIKTOKEN_TRIED
    if _TIKTOKEN_TRIED:
        return _TIKTOKEN_ENC
    _TIKTOKEN_TRIED = True
    try:
        import tiktoken  # type: ignore[import-not-found]
        _TIKTOKEN_ENC = tiktoken.get_encoding("cl100k_base")
    except Exception as exc:  # noqa: BLE001
        _logger.debug("tiktoken unavailable, falling back to heuristic: %s", exc)
        _TIKTOKEN_ENC = None
    return _TIKTOKEN_ENC


def count_tokens(text: str) -> int:
    """Best-effort token count.

    Order of preference:
      1. tiktoken cl100k_base — accurate for GPT-family and NIM
      2. Heuristic: max(chars/3.5, whitespace_words × 1.3) — biased upward
         to track reasoning models that emit hidden tokens.

    Returns at least 1 for non-empty text to avoid zero-cost loops.
    """
    if not text:
        return 0
    enc = _get_tiktoken_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception as exc:  # noqa: BLE001
            _logger.debug("tiktoken encode failed, falling back: %s", exc)
    char_based = int(len(text) / 3.5)
    word_based = int(len(text.split()) * 1.3)
    return max(1, char_based, word_based)
