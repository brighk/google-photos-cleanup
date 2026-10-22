from __future__ import annotations

from pathlib import Path


def verify_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False
