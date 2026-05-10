"""Evidence handle: typed wrapper for NEMO evidence references.

An EvidenceHandle represents a large content block that has been summarized
into a compact claim. The full content can be retrieved on demand via the
NEMO expand_context_evidence tool.

Usage pattern:
  handle = EvidenceHandle(handle_id="h-abc123", claim="Spec says X.")
  full   = handle.expand(nemo_call)  # returns expanded text or falls back to claim
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Type alias for a NEMO tool call function.
NemoCallFn = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class EvidenceHandle:
    """A compact claim backed by an expandable evidence reference.

    Attributes:
        handle_id: Opaque identifier used by NEMO to retrieve the full content.
        claim:     Compact human-readable claim that stands in for the full content.
        source:    Optional label describing where this evidence came from.
    """

    handle_id: str
    claim: str
    source: str = ""

    def expand(self, nemo_call: NemoCallFn | None, *, query: str = "") -> str:
        """Return the full content for this handle.

        Calls NEMO's expand_context_evidence tool. Falls back to `claim` if
        NEMO is unavailable, the handle is missing/expired, or an error occurs.

        Args:
            nemo_call: Callable(tool_name, **kwargs) -> dict for NEMO tools.
            query:     Optional query for scoped evidence retrieval.

        Returns:
            Full expanded text, or `claim` as fallback.
        """
        if nemo_call is None:
            return self.claim
        try:
            kwargs: dict[str, Any] = {"handle": self.handle_id}
            if query:
                kwargs["query"] = query
            payload = nemo_call("expand_context_evidence", **kwargs)
            content = str(payload.get("content") or "").strip()
            return content if content else self.claim
        except Exception as exc:
            logger.warning("EvidenceHandle.expand failed for %s: %s", self.handle_id, exc)
            return self.claim

    def as_dict(self) -> dict[str, Any]:
        return {"handle_id": self.handle_id, "claim": self.claim, "source": self.source}


def parse_evidence_handles(raw: object, *, source: str = "") -> list[EvidenceHandle]:
    """Convert a raw NEMO evidence_handles list to typed EvidenceHandle objects.

    Args:
        raw:    The raw value from portfolio payload["evidence_handles"].
        source: Label to attach to all parsed handles.

    Returns:
        List of EvidenceHandle objects (may be empty if raw is not a list).
    """
    if not isinstance(raw, list):
        return []
    handles: list[EvidenceHandle] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            handles.append(EvidenceHandle(handle_id=item, claim=item, source=source))
        elif isinstance(item, dict):
            handle_id = str(item.get("handle") or item.get("handle_id") or "").strip()
            claim = str(item.get("claim") or item.get("content") or handle_id)
            if handle_id:
                handles.append(EvidenceHandle(handle_id=handle_id, claim=claim, source=source))
    return handles
