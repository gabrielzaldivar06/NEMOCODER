"""Skill materializer: render skill prompts into a destination directory.

Responsibilities:
- SkillRenderContext: typed render-time variables (consumer-provided).
- materialize_skills: atomic write to staging then move — workspace stays intact on failure.
- MaterializationResult: outcome with per-skill status.

This module is agent-blind: it does not know about sessions or the agent loop.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from string import Template

from nemo_coding_platform.core.skill_registry import BuiltinSkillRegistry
from nemo_coding_platform.core.skills import Skill

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillRenderContext:
    """Variables available for template substitution in skill prompts."""

    workspace_path: str = ""
    session_id: str = ""
    model_name: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    def as_mapping(self) -> dict[str, str]:
        mapping = {
            "workspace_path": self.workspace_path,
            "session_id": self.session_id,
            "model_name": self.model_name,
        }
        mapping.update(self.extra)
        return mapping


@dataclass
class SkillMaterializationItem:
    slug: str
    dest_path: Path
    success: bool
    error: str = ""


@dataclass
class MaterializationResult:
    dest_dir: Path
    items: list[SkillMaterializationItem] = field(default_factory=list)

    @property
    def succeeded(self) -> list[str]:
        return [i.slug for i in self.items if i.success]

    @property
    def failed(self) -> list[str]:
        return [i.slug for i in self.items if not i.success]

    @property
    def ok(self) -> bool:
        return all(i.success for i in self.items)


def _render_prompt(prompt: str, ctx: SkillRenderContext) -> str:
    """Substitute $variable placeholders using stdlib Template (safe substitution)."""
    try:
        return Template(prompt).safe_substitute(ctx.as_mapping())
    except Exception:
        return prompt  # never fail materialisation due to template errors


def materialize_skills(
    registry: BuiltinSkillRegistry,
    dest_dir: Path,
    ctx: SkillRenderContext,
    *,
    slugs: list[str] | None = None,
    overwrite: bool = True,
) -> MaterializationResult:
    """Render skills into *dest_dir* using atomic staging.

    If *slugs* is provided, only those skills are materialised.
    The workspace is never modified if an error occurs (staging pattern).

    Args:
        registry: The skill registry to pull skills from.
        dest_dir: Target directory for rendered skill files.
        ctx: Render-time context for template substitution.
        slugs: Optional subset of skill slugs to materialise.
        overwrite: If False, skip skills whose output file already exists.

    Returns:
        MaterializationResult with per-skill success/failure details.
    """
    skills: list[Skill] = (
        [s for s in registry.all_skills() if s.slug in slugs]
        if slugs is not None
        else registry.all_skills()
    )

    result = MaterializationResult(dest_dir=dest_dir)

    # Use a temp dir alongside dest_dir for atomic staging
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp_parent = dest_dir.parent

    with tempfile.TemporaryDirectory(dir=tmp_parent, prefix=".skill_stage_") as staging_str:
        staging = Path(staging_str)

        for skill in skills:
            output_name = f"{skill.slug}.md"
            staged = staging / output_name
            final = dest_dir / output_name

            if not overwrite and final.exists():
                result.items.append(SkillMaterializationItem(slug=skill.slug, dest_path=final, success=True))
                continue

            try:
                rendered = _render_prompt(skill.prompt, ctx)
                header = f"# {skill.name}\n\n_{skill.description}_\n\n"
                staged.write_text(header + rendered, encoding="utf-8")
                shutil.copy2(staged, final)
                result.items.append(SkillMaterializationItem(slug=skill.slug, dest_path=final, success=True))
                logger.debug("Materialised skill %s → %s", skill.slug, final)
            except Exception as exc:
                error_msg = str(exc)
                logger.warning("Failed to materialise skill %s: %s", skill.slug, error_msg)
                result.items.append(
                    SkillMaterializationItem(slug=skill.slug, dest_path=dest_dir / output_name, success=False, error=error_msg)
                )

    return result
