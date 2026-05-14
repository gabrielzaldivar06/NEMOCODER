def test_python_docstring_extracted(tmp_path):
    from nemo_coding_platform.core.repo_map import _extract_summary
    f = tmp_path / "mod.py"
    f.write_text('"""My module does things."""\n\ndef foo(): pass\n', encoding="utf-8")
    assert _extract_summary(f) == "My module does things."


def test_python_multiline_docstring_first_line_only(tmp_path):
    from nemo_coding_platform.core.repo_map import _extract_summary
    f = tmp_path / "mod.py"
    f.write_text('"""First line.\n\nSecond paragraph.\n"""\n', encoding="utf-8")
    assert _extract_summary(f) == "First line."


def test_non_python_comment_extracted(tmp_path):
    from nemo_coding_platform.core.repo_map import _extract_summary
    f = tmp_path / "comp.ts"
    f.write_text("// React component for the dashboard\nexport default function Dash() {}\n", encoding="utf-8")
    assert _extract_summary(f) == "React component for the dashboard"


def test_shebang_line_skipped(tmp_path):
    from nemo_coding_platform.core.repo_map import _extract_summary
    f = tmp_path / "script.sh"
    f.write_text("#!/usr/bin/env bash\n# Real summary here\necho hello\n", encoding="utf-8")
    assert _extract_summary(f) == "Real summary here"


def test_binary_file_has_no_summary(tmp_path):
    from nemo_coding_platform.core.repo_map import _extract_summary
    f = tmp_path / "image.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00")
    assert _extract_summary(f) == ""


def test_is_text_file_true_for_source(tmp_path):
    from nemo_coding_platform.core.repo_map import _is_text_file
    f = tmp_path / "hello.py"
    f.write_text("print('hi')\n", encoding="utf-8")
    assert _is_text_file(f) is True


def test_is_text_file_false_for_binary(tmp_path):
    from nemo_coding_platform.core.repo_map import _is_text_file
    f = tmp_path / "data.bin"
    f.write_bytes(b"hello\x00world")
    assert _is_text_file(f) is False


def test_python_no_docstring_returns_empty(tmp_path):
    from nemo_coding_platform.core.repo_map import _extract_summary
    f = tmp_path / "nodoc.py"
    f.write_text("def foo():\n    pass\n", encoding="utf-8")
    assert _extract_summary(f) == ""


def test_is_text_file_false_for_missing_file(tmp_path):
    from nemo_coding_platform.core.repo_map import _is_text_file
    assert _is_text_file(tmp_path / "does_not_exist.py") is False


def test_format_tree_groups_by_directory(tmp_path):
    from nemo_coding_platform.core.repo_map import _format_tree
    a = tmp_path / "src" / "core" / "foo.py"
    b = tmp_path / "src" / "core" / "bar.py"
    c = tmp_path / "tests" / "test_foo.py"
    entries = [(a, "Foo module."), (b, ""), (c, "Tests for foo.")]
    result = _format_tree(entries, tmp_path)
    assert "## src/core/" in result
    assert "foo.py — Foo module." in result
    assert "bar.py" in result
    assert "## tests/" in result
    assert "test_foo.py — Tests for foo." in result


def test_load_cache_returns_empty_on_missing(tmp_path):
    from nemo_coding_platform.core.repo_map import _load_cache
    assert _load_cache(tmp_path / "nonexistent.json") == {}


def test_save_and_load_cache_roundtrip(tmp_path):
    from nemo_coding_platform.core.repo_map import _load_cache, _save_cache
    data = {"src/foo.py": {"mtime": 1234567890.0, "summary": "My module."}}
    cache_file = tmp_path / "cache.json"
    _save_cache(cache_file, data)
    loaded = _load_cache(cache_file)
    assert loaded == data
