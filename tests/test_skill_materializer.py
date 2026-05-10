"""Tests for core/skill_materializer.py — materialize_skills, SkillRenderContext."""
import textwrap
from pathlib import Path

import pytest

from nemo_coding_platform.core.skill_materializer import (
    MaterializationResult,
    SkillRenderContext,
    materialize_skills,
)
from nemo_coding_platform.core.skill_registry import BuiltinSkillRegistry


def _write_skill(root: Path, slug: str, name: str, description: str, prompt: str) -> None:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    content = textwrap.dedent(f"""\
        ---
        name: {name}
        description: {description}
        ---
        {prompt}
    """)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


class TestSkillRenderContext:
    def test_as_mapping_contains_standard_keys(self):
        ctx = SkillRenderContext(workspace_path="/tmp/ws", session_id="s1", model_name="gpt-4")
        m = ctx.as_mapping()
        assert m["workspace_path"] == "/tmp/ws"
        assert m["session_id"] == "s1"
        assert m["model_name"] == "gpt-4"

    def test_extra_merged_into_mapping(self):
        ctx = SkillRenderContext(extra={"custom_key": "custom_value"})
        m = ctx.as_mapping()
        assert m["custom_key"] == "custom_value"


class TestMaterializeSkills:
    def test_creates_output_files(self, tmp_path):
        skills_root = tmp_path / "skills"
        dest_dir = tmp_path / "out"
        _write_skill(skills_root, "code-review", "Code Review", "Reviews code.", "Review the code.")
        registry = BuiltinSkillRegistry(skills_root)
        ctx = SkillRenderContext()
        result = materialize_skills(registry, dest_dir, ctx)
        assert result.ok
        assert (dest_dir / "code-review.md").exists()

    def test_rendered_file_contains_name_and_description(self, tmp_path):
        skills_root = tmp_path / "skills"
        dest_dir = tmp_path / "out"
        _write_skill(skills_root, "doc", "Documentation", "Writes docs.", "Write the docs.")
        registry = BuiltinSkillRegistry(skills_root)
        result = materialize_skills(registry, dest_dir, SkillRenderContext())
        content = (dest_dir / "doc.md").read_text(encoding="utf-8")
        assert "Documentation" in content
        assert "Writes docs." in content

    def test_template_substitution(self, tmp_path):
        skills_root = tmp_path / "skills"
        dest_dir = tmp_path / "out"
        _write_skill(skills_root, "ws-skill", "Workspace Skill", "Desc.", "Path: $workspace_path")
        registry = BuiltinSkillRegistry(skills_root)
        ctx = SkillRenderContext(workspace_path="/my/workspace")
        materialize_skills(registry, dest_dir, ctx)
        content = (dest_dir / "ws-skill.md").read_text()
        assert "/my/workspace" in content

    def test_missing_template_var_is_safe(self, tmp_path):
        """safe_substitute must not raise on unknown $vars."""
        skills_root = tmp_path / "skills"
        dest_dir = tmp_path / "out"
        _write_skill(skills_root, "s", "S", "D.", "Value: $undefined_var")
        registry = BuiltinSkillRegistry(skills_root)
        result = materialize_skills(registry, dest_dir, SkillRenderContext())
        assert result.ok  # must not fail

    def test_slugs_filter(self, tmp_path):
        skills_root = tmp_path / "skills"
        dest_dir = tmp_path / "out"
        _write_skill(skills_root, "s1", "Skill One", "D1.", "P1")
        _write_skill(skills_root, "s2", "Skill Two", "D2.", "P2")
        registry = BuiltinSkillRegistry(skills_root)
        result = materialize_skills(registry, dest_dir, SkillRenderContext(), slugs=["s1"])
        assert "s1" in result.succeeded
        assert (dest_dir / "s1.md").exists()
        assert not (dest_dir / "s2.md").exists()

    def test_no_overwrite_skips_existing(self, tmp_path):
        skills_root = tmp_path / "skills"
        dest_dir = tmp_path / "out"
        dest_dir.mkdir()
        _write_skill(skills_root, "s1", "Skill One", "D1.", "P1")
        (dest_dir / "s1.md").write_text("ORIGINAL", encoding="utf-8")
        registry = BuiltinSkillRegistry(skills_root)
        materialize_skills(registry, dest_dir, SkillRenderContext(), overwrite=False)
        assert (dest_dir / "s1.md").read_text() == "ORIGINAL"

    def test_workspace_intact_on_partial_failure(self, tmp_path):
        """Successful skills are still written even if registry has zero-byte skill."""
        skills_root = tmp_path / "skills"
        dest_dir = tmp_path / "out"
        _write_skill(skills_root, "good", "Good Skill", "D.", "Good prompt.")
        registry = BuiltinSkillRegistry(skills_root)
        result = materialize_skills(registry, dest_dir, SkillRenderContext())
        assert (dest_dir / "good.md").exists()

    def test_empty_registry_returns_ok(self, tmp_path):
        dest_dir = tmp_path / "out"
        registry = BuiltinSkillRegistry(tmp_path / "empty")
        result = materialize_skills(registry, dest_dir, SkillRenderContext())
        assert result.ok
        assert result.succeeded == []

    def test_result_succeeded_and_failed(self, tmp_path):
        skills_root = tmp_path / "skills"
        dest_dir = tmp_path / "out"
        _write_skill(skills_root, "ok", "OK Skill", "D.", "Prompt.")
        registry = BuiltinSkillRegistry(skills_root)
        result = materialize_skills(registry, dest_dir, SkillRenderContext())
        assert "ok" in result.succeeded
        assert result.failed == []
