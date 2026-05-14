from __future__ import annotations

from dataclasses import dataclass
from os import environ


DEFAULT_LMSTUDIO_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_LMSTUDIO_MODEL = ""  # auto-discovered from LM Studio at runtime
QUALITY_FALLBACK_LMSTUDIO_MODEL = "opus4.7-gods.ghost.codex-4b.gguf"
MODEL_ROLES = ("planner", "editor", "reviewer", "summarizer")


@dataclass(frozen=True, slots=True)
class ModelProfile:
    name: str = "lmstudio-small-coder"
    base_url: str = DEFAULT_LMSTUDIO_BASE_URL
    model: str = DEFAULT_LMSTUDIO_MODEL
    api_key_env: str = "LMSTUDIO_API_KEY"

    @property
    def api_key(self) -> str:
        return environ.get(self.api_key_env, "lm-studio")


@dataclass(frozen=True, slots=True)
class ModelRoleProfile:
    planner: str
    editor: str
    reviewer: str
    summarizer: str

    def model_for_role(self, role: str) -> str:
        normalized = str(role or "").strip().lower()
        if normalized not in MODEL_ROLES:
            raise ValueError(f"unknown model role: {role}")
        return getattr(self, normalized)


def default_model_profile() -> ModelProfile:
    return ModelProfile(
        base_url=environ.get("LMSTUDIO_BASE_URL", DEFAULT_LMSTUDIO_BASE_URL),
        model=environ.get("LMSTUDIO_MODEL", DEFAULT_LMSTUDIO_MODEL),
    )


def default_model_role_profile(default_model: str | None = None) -> ModelRoleProfile:
    fallback = default_model or environ.get("LMSTUDIO_MODEL", DEFAULT_LMSTUDIO_MODEL)
    return ModelRoleProfile(
        planner=environ.get("LMSTUDIO_MODEL_PLANNER", fallback),
        editor=environ.get("LMSTUDIO_MODEL_EDITOR", fallback),
        reviewer=environ.get("LMSTUDIO_MODEL_REVIEWER", fallback),
        summarizer=environ.get("LMSTUDIO_MODEL_SUMMARIZER", fallback),
    )
