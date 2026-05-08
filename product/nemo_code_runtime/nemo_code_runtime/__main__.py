from __future__ import annotations

from aider.main import main as _legacy_main


def main() -> int:
    result = _legacy_main()
    return int(result) if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
