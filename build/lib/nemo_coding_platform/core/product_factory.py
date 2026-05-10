from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _ensure_embedded_runtime_on_path() -> None:
    runtime_root = _repo_root() / "product" / "nemo_code_runtime"
    if runtime_root.exists() and str(runtime_root) not in sys.path:
        sys.path.insert(0, str(runtime_root))


def get_platform_info(product_name: str) -> dict[str, Any]:
    normalized = product_name.strip().lower()
    if normalized not in {"nemo-code", "nemo_code", "nemo code", "nemocode"}:
        raise ValueError(f"unknown product platform: {product_name}")
    _ensure_embedded_runtime_on_path()
    module = importlib.import_module("nemo_code_runtime.nemo_platform.platform_info")
    return dict(module.platform_info())
