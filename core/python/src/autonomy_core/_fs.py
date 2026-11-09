from __future__ import annotations

import os
from pathlib import Path


def atomic_write(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` so a crash leaves the old or the new content."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
