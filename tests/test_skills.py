from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.core.skills import Skill, find_skill_by_name, load_skills, parse_skill_file


def _write_skill(directory: Path, content: str) -> Path:
    skill_file = directory / "SKILL.md"
    skill_file.write_text(content, encoding="utf-8")
    return skill_file


class TestParseSkillFile(unittest.TestCase):
    def test_parses_valid_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "my-skill"
            skill_dir.mkdir()
            _write_skill(
                skill_dir,
                "---\nname: My Skill\ndescription: Does something useful.\n---\n\nDo useful work.\n",
            )
            skill = parse_skill_file(skill_dir / "SKILL.md")
            self.assertIsNotNone(skill)
            assert skill is not None
            self.assertEqual(skill.name, "My Skill")
            self.assertEqual(skill.description, "Does something useful.")
            self.assertEqual(skill.prompt, "Do useful work.")
            self.assertEqual(skill.slug, "my-skill")

    def test_returns_none_for_missing_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "bad-skill"
            skill_dir.mkdir()
            _write_skill(skill_dir, "---\ndescription: No name.\n---\n\nPrompt.\n")
            self.assertIsNone(parse_skill_file(skill_dir / "SKILL.md"))

    def test_returns_none_for_missing_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "bad-skill"
            skill_dir.mkdir()
            _write_skill(skill_dir, "---\nname: No Desc\n---\n\nPrompt.\n")
            self.assertIsNone(parse_skill_file(skill_dir / "SKILL.md"))

    def test_returns_none_for_missing_front_matter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "bad-skill"
            skill_dir.mkdir()
            _write_skill(skill_dir, "Just plain text, no front-matter.")
            self.assertIsNone(parse_skill_file(skill_dir / "SKILL.md"))

    def test_returns_none_for_wrong_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "README.md"
            p.write_text("---\nname: X\ndescription: Y\n---\n\nZ\n", encoding="utf-8")
            self.assertIsNone(parse_skill_file(p))

    def test_prompt_from_frontmatter_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "inline"
            skill_dir.mkdir()
            _write_skill(
                skill_dir,
                "---\nname: Inline\ndescription: Inline prompt.\nprompt: From frontmatter.\n---\n\nBody is ignored.\n",
            )
            skill = parse_skill_file(skill_dir / "SKILL.md")
            self.assertIsNotNone(skill)
            assert skill is not None
            self.assertEqual(skill.prompt, "From frontmatter.")


class TestLoadSkills(unittest.TestCase):
    def test_loads_multiple_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for name in ("alpha", "beta", "gamma"):
                d = root / name
                d.mkdir()
                _write_skill(d, f"---\nname: {name.title()}\ndescription: Skill {name}.\n---\n\nDo {name}.\n")
            skills = load_skills(root)
            self.assertEqual(len(skills), 3)
            self.assertEqual([s.name for s in skills], ["Alpha", "Beta", "Gamma"])

    def test_empty_root_returns_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills = load_skills(Path(tmpdir))
            self.assertEqual(skills, [])

    def test_nonexistent_root_returns_empty_list(self) -> None:
        skills = load_skills(Path("/does/not/exist"))
        self.assertEqual(skills, [])

    def test_skips_malformed_skill_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            good = root / "good"
            good.mkdir()
            _write_skill(good, "---\nname: Good\ndescription: Fine.\n---\n\nOK.\n")
            bad = root / "bad"
            bad.mkdir()
            _write_skill(bad, "no front-matter here")
            skills = load_skills(root)
            self.assertEqual(len(skills), 1)
            self.assertEqual(skills[0].name, "Good")


class TestFindSkillByName(unittest.TestCase):
    def _setup_skills(self, tmpdir: str) -> Path:
        root = Path(tmpdir)
        for slug, name in (("code-review", "Code Review"), ("docs", "Documentation")):
            d = root / slug
            d.mkdir()
            _write_skill(d, f"---\nname: {name}\ndescription: {name} skill.\n---\n\n{name} prompt.\n")
        return root

    def test_finds_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = self._setup_skills(tmpdir)
            skill = find_skill_by_name("Code Review", root)
            self.assertIsNotNone(skill)
            assert skill is not None
            self.assertEqual(skill.name, "Code Review")

    def test_finds_by_slug(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = self._setup_skills(tmpdir)
            skill = find_skill_by_name("code-review", root)
            self.assertIsNotNone(skill)

    def test_case_insensitive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = self._setup_skills(tmpdir)
            self.assertIsNotNone(find_skill_by_name("CODE REVIEW", root))

    def test_returns_none_for_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = self._setup_skills(tmpdir)
            self.assertIsNone(find_skill_by_name("nonexistent", root))


if __name__ == "__main__":
    unittest.main()
