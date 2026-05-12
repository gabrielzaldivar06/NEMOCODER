"""Skill loading and parsing for Space Code.

Skills are Markdown files (SKILL.md) with a simple YAML-like front-matter block
that defines the skill's name, description, and an optional prompt injected into
the Aider mutation message. Uses only stdlib — no external YAML library required.
Inspired by deer-flow's skill system.

Directory layout::

    skills/
    ├── code-review/
    │   └── SKILL.md
    └── documentation/
        └── SKILL.md

Each SKILL.md starts with a front-matter block between ``---`` fences::

    ---
    name: Code Review
    description: Thorough code review assistant.
    ---

    # Prompt

    Review the changed files for correctness, style, and edge cases...
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SKILL_FILENAME = "SKILL.md"


@dataclass(frozen=True, slots=True)
class Skill:
    """A parsed skill loaded from a SKILL.md file."""

    name: str
    description: str
    prompt: str
    skill_dir: Path

    @property
    def slug(self) -> str:
        """URL-safe identifier derived from the skill directory name."""
        return self.skill_dir.name


def _parse_front_matter(text: str) -> dict[str, str]:
    """Parse simple ``key: value`` front-matter into a dict.

    Supports single-line values only (sufficient for skill metadata).
    Multi-line values are not needed here. Lines starting with ``#`` are
    treated as comments and ignored.

    Args:
        text: The raw front-matter text (between the ``---`` fences).

    Returns:
        A dict of key/value pairs parsed from the front-matter.
    """
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip().lower()] = value.strip()
    return result


def parse_skill_file(skill_file: Path) -> Skill | None:
    """Parse a SKILL.md file and return a :class:`Skill`, or ``None`` on error.

    The file must start with a front-matter block between ``---`` fences.
    The fields ``name`` and ``description`` are required; ``prompt`` falls back
    to the body of the document (everything after the closing ``---`` fence).

    Args:
        skill_file: Absolute path to a ``SKILL.md`` file.

    Returns:
        A parsed :class:`Skill`, or ``None`` if the file is malformed.
    """
    if not skill_file.exists() or skill_file.name != SKILL_FILENAME:
        return None

    try:
        content = skill_file.read_text(encoding="utf-8")
    except OSError:
        logger.exception("Cannot read skill file %s", skill_file)
        return None

    # Extract front-matter between the leading ``---`` fences.
    front_matter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", content, re.DOTALL)
    if not front_matter_match:
        logger.debug("No front-matter found in %s", skill_file)
        return None

    front_matter_text = front_matter_match.group(1)
    body = front_matter_match.group(2).strip()
    metadata = _parse_front_matter(front_matter_text)

    name = metadata.get("name", "").strip()
    description = metadata.get("description", "").strip()

    if not name:
        logger.debug("Missing required 'name' field in %s", skill_file)
        return None
    if not description:
        logger.debug("Missing required 'description' field in %s", skill_file)
        return None

    # The prompt can be specified inline in the front-matter or as the body.
    prompt = metadata.get("prompt", body).strip()

    return Skill(
        name=name,
        description=description,
        prompt=prompt,
        skill_dir=skill_file.parent,
    )


def load_skills(skills_root: Path) -> list[Skill]:
    """Recursively load all valid skills from *skills_root*.

    Args:
        skills_root: Path to the directory containing skill sub-directories.

    Returns:
        A list of successfully parsed :class:`Skill` objects, alphabetically
        sorted by skill name.
    """
    if not skills_root.is_dir():
        return []

    skills: list[Skill] = []
    for skill_file in skills_root.rglob(SKILL_FILENAME):
        skill = parse_skill_file(skill_file)
        if skill is not None:
            skills.append(skill)

    return sorted(skills, key=lambda s: s.name.lower())


def find_skill_by_name(name: str, skills_root: Path) -> Skill | None:
    """Find a skill by its ``name`` or directory ``slug`` (case-insensitive).

    Args:
        name: The skill name or slug to search for.
        skills_root: Root directory containing skill sub-directories.

    Returns:
        The matching :class:`Skill`, or ``None`` if not found.
    """
    needle = name.strip().lower()
    for skill in load_skills(skills_root):
        if skill.name.lower() == needle or skill.slug.lower() == needle:
            return skill
    return None
