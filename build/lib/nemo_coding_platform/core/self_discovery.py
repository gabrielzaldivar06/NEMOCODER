from __future__ import annotations

from pathlib import Path


SELF_MOD_PERMISSION_TEMPLATE = {
    "write_file": {
        "*": "deny",
        "src/nemo_coding_platform/**": "allow",
        "tests/**": "allow",
        "apps/mission-control/**": "allow",
        "skills/**": "allow",
        "README.md": "allow",
        "API.md": "allow",
        "TESTING.md": "allow",
        ".git/**": "deny",
        ".venv/**": "deny",
        ".nemo-runtimes/**": "deny",
        "*.db": "deny",
        "*.sqlite": "deny",
        "*.sqlite-wal": "deny",
        "*.sqlite-shm": "deny",
        ".env*": "deny",
    },
    "run_command": {
        "*": "deny",
        "python -m unittest*": "allow",
        "*.venv/Scripts/python.exe -m unittest*": "allow",
        "c:/dev/dev4/.venv/Scripts/python.exe -m unittest*": "allow",
        "npm run build": "allow",
        "git push*": "deny",
    },
    "git_push": "deny",
}


def find_nemocode_repo(start: str | Path | None = None) -> Path:
    """Find the NEMOCODE repository root by walking up from *start*."""
    current = Path(start).resolve() if start else Path(__file__).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if is_nemocode_repo(candidate):
            return candidate
    raise FileNotFoundError(f"NEMOCODE repository root not found from {current}")


def is_nemocode_repo(path: str | Path) -> bool:
    root = Path(path)
    pyproject = root / "pyproject.toml"
    package_root = root / "src" / "nemo_coding_platform"
    tests_root = root / "tests"
    if not pyproject.exists() or not package_root.is_dir() or not tests_root.is_dir():
        return False
    try:
        return "nemo_coding_platform" in pyproject.read_text(encoding="utf-8")
    except OSError:
        return False


def ensure_self_mod_permissions_file(repo_root: str | Path, filename: str = ".nemocode-self-mod.permissions.json") -> Path:
    """Create the conservative self-mod permission template if it does not already exist."""
    import json

    target = Path(repo_root) / filename
    if not target.exists():
        target.write_text(json.dumps(SELF_MOD_PERMISSION_TEMPLATE, indent=2, sort_keys=True), encoding="utf-8")
    return target