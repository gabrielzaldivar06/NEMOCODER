from __future__ import annotations

from dataclasses import dataclass
from os import environ


DEFAULT_LMSTUDIO_BASE_URL = "http://localhost:1234/v1"
DEFAULT_LMSTUDIO_MODEL = "nvidia.agentic.coder-4b"
QUALITY_FALLBACK_LMSTUDIO_MODEL = "opus4.7-gods.ghost.codex-4b.gguf"


@dataclass(frozen=True, slots=True)
class ModelProfile:
    name: str = "lmstudio-small-coder"
    base_url: str = DEFAULT_LMSTUDIO_BASE_URL
    model: str = DEFAULT_LMSTUDIO_MODEL
    api_key_env: str = "LMSTUDIO_API_KEY"

    @property
    def api_key(self) -> str:
        return environ.get(self.api_key_env, "lm-studio")


def default_model_profile() -> ModelProfile:
    return ModelProfile(
        base_url=environ.get("LMSTUDIO_BASE_URL", DEFAULT_LMSTUDIO_BASE_URL),
        model=environ.get("LMSTUDIO_MODEL", DEFAULT_LMSTUDIO_MODEL),
    )
