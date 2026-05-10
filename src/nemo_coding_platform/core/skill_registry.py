"""Skill registry: discovery, singleton registry, and availability checks.

Responsibilities:
- BuiltinSkillRegistry: discover and index skills from a root path.
- is_skill_available: check availability without side effects.
- SkillsManifest: a serialisable snapshot of available skills.

Consumer-blind: no coupling to runtime, orchestration, or UI concepts.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Iterator

from nemo_coding_platform.core.skills import Skill, load_skills, parse_skill_file

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillsManifest:
    """Serialisable snapshot of all available skills."""

    skills: tuple[Skill, ...]
    root: Path

    @property
    def slugs(self) -> list[str]:
        return [s.slug for s in self.skills]

    def as_list(self) -> list[dict[str, str]]:
        return [{"slug": s.slug, "name": s.name, "description": s.description} for s in self.skills]


class BuiltinSkillRegistry:
    """Registry for built-in skills discovered from a filesystem root.

    Thread-safe. Skills are loaded lazily on first access or on explicit
    ``refresh()``. The registry is a plain object — callers control lifetime.

    Usage::

        registry = BuiltinSkillRegistry(skills_root=Path("skills/"))
        skill = registry.get("code-review")
        manifest = registry.manifest()
    """

    def __init__(self, skills_root: Path) -> None:
        self._root = skills_root
        self._skills: dict[str, Skill] = {}  # slug -> Skill
        self._loaded = False
        self._lock = Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh(self) -> int:
        """Reload all skills from disk. Returns the number of skills loaded."""
        with self._lock:
            loaded = load_skills(self._root)
            self._skills = {s.slug: s for s in loaded}
            self._loaded = True
            logger.debug("BuiltinSkillRegistry: loaded %d skills from %s", len(loaded), self._root)
            return len(loaded)

    def get(self, slug: str) -> Skill | None:
        """Return the skill with *slug*, or None if not found."""
        self._ensure_loaded()
        return self._skills.get(slug)

    def find_by_name(self, name: str) -> Skill | None:
        """Find a skill by name or slug (case-insensitive)."""
        self._ensure_loaded()
        needle = name.strip().lower()
        for skill in self._skills.values():
            if skill.slug.lower() == needle or skill.name.lower() == needle:
                return skill
        return None

    def all_skills(self) -> list[Skill]:
        """Return all skills sorted by name."""
        self._ensure_loaded()
        return sorted(self._skills.values(), key=lambda s: s.name.lower())

    def manifest(self) -> SkillsManifest:
        """Return an immutable snapshot of the current registry state."""
        return SkillsManifest(skills=tuple(self.all_skills()), root=self._root)

    def is_available(self, slug: str) -> bool:
        """Return True if the skill exists and its SKILL.md is readable."""
        self._ensure_loaded()
        skill = self._skills.get(slug)
        if skill is None:
            return False
        skill_file = skill.skill_dir / "SKILL.md"
        return skill_file.exists() and skill_file.is_file()

    def __len__(self) -> int:
        self._ensure_loaded()
        return len(self._skills)

    def __iter__(self) -> Iterator[Skill]:
        self._ensure_loaded()
        return iter(self.all_skills())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.refresh()
