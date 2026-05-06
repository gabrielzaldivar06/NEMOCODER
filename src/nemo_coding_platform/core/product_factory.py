from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _ensure_aider_fork_on_path() -> None:
    aider_root = _repo_root() / "product" / "aider"
    if aider_root.exists() and str(aider_root) not in sys.path:
        sys.path.insert(0, str(aider_root))


def get_platform_info(product_name: str) -> dict[str, Any]:
    normalized = product_name.strip().lower()
    if normalized != "aider":
        raise ValueError(f"unknown product platform: {product_name}")
    _ensure_aider_fork_on_path()
    module = importlib.import_module("aider.nemo_platform.platform_info")
    return dict(module.platform_info())
