"""Tests for core/skill_registry.py — BuiltinSkillRegistry, SkillsManifest."""
import textwrap
from pathlib import Path

import pytest

from nemo_coding_platform.core.skill_registry import BuiltinSkillRegistry, SkillsManifest


def _write_skill(root: Path, slug: str, name: str, description: str, prompt: str = "Do the thing.") -> Path:
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
    return skill_dir


class TestBuiltinSkillRegistry:
    def test_loads_skills_from_disk(self, tmp_path):
        _write_skill(tmp_path, "code-review", "Code Review", "Reviews code.")
        _write_skill(tmp_path, "documentation", "Documentation", "Writes docs.")
        registry = BuiltinSkillRegistry(tmp_path)
        assert len(registry) == 2

    def test_empty_directory_returns_zero(self, tmp_path):
        registry = BuiltinSkillRegistry(tmp_path)
        assert len(registry) == 0

    def test_nonexistent_root_returns_zero(self, tmp_path):
        registry = BuiltinSkillRegistry(tmp_path / "does_not_exist")
        assert len(registry) == 0

    def test_get_by_slug(self, tmp_path):
        _write_skill(tmp_path, "code-review", "Code Review", "Reviews code.")
        registry = BuiltinSkillRegistry(tmp_path)
        skill = registry.get("code-review")
        assert skill is not None
        assert skill.name == "Code Review"

    def test_get_missing_returns_none(self, tmp_path):
        registry = BuiltinSkillRegistry(tmp_path)
        assert registry.get("missing") is None

    def test_find_by_name_case_insensitive(self, tmp_path):
        _write_skill(tmp_path, "code-review", "Code Review", "Reviews code.")
        registry = BuiltinSkillRegistry(tmp_path)
        assert registry.find_by_name("CODE REVIEW") is not None
        assert registry.find_by_name("code-review") is not None

    def test_all_skills_sorted_by_name(self, tmp_path):
        _write_skill(tmp_path, "zz-skill", "Zebra Skill", "Z.")
        _write_skill(tmp_path, "aa-skill", "Alpha Skill", "A.")
        registry = BuiltinSkillRegistry(tmp_path)
        names = [s.name for s in registry.all_skills()]
        assert names == sorted(names, key=str.lower)

    def test_is_available_true(self, tmp_path):
        _write_skill(tmp_path, "code-review", "Code Review", "Reviews code.")
        registry = BuiltinSkillRegistry(tmp_path)
        assert registry.is_available("code-review") is True

    def test_is_available_false_missing(self, tmp_path):
        registry = BuiltinSkillRegistry(tmp_path)
        assert registry.is_available("missing") is False

    def test_refresh_reloads_new_skills(self, tmp_path):
        registry = BuiltinSkillRegistry(tmp_path)
        assert len(registry) == 0
        _write_skill(tmp_path, "new-skill", "New Skill", "New.")
        registry.refresh()
        assert len(registry) == 1

    def test_iterate_yields_skills(self, tmp_path):
        _write_skill(tmp_path, "s1", "Skill One", "D1.")
        _write_skill(tmp_path, "s2", "Skill Two", "D2.")
        registry = BuiltinSkillRegistry(tmp_path)
        slugs = {s.slug for s in registry}
        assert slugs == {"s1", "s2"}

    def test_registry_is_consumer_blind(self, tmp_path):
        """Registry must not reference sessions, workspaces, or agent concepts."""
        import inspect
        import nemo_coding_platform.core.skill_registry as mod
        src = inspect.getsource(mod)
        for forbidden in ("session", "workspace", "agent_loop", "sandbox"):
            assert forbidden not in src, f"skill_registry.py must not reference '{forbidden}'"


class TestSkillsManifest:
    def test_slugs_property(self, tmp_path):
        _write_skill(tmp_path, "r1", "Review One", "D.")
        registry = BuiltinSkillRegistry(tmp_path)
        manifest = registry.manifest()
        assert isinstance(manifest, SkillsManifest)
        assert "r1" in manifest.slugs

    def test_as_list_shape(self, tmp_path):
        _write_skill(tmp_path, "r1", "Review One", "Desc.")
        registry = BuiltinSkillRegistry(tmp_path)
        items = registry.manifest().as_list()
        assert len(items) == 1
        assert set(items[0].keys()) == {"slug", "name", "description"}
