from __future__ import annotations

import os
import subprocess
from pathlib import Path


def item_kind(path: str) -> str:
    p = Path(path)
    return "folder" if p.is_dir() else "file"


def display_name(path: str) -> str:
    p = Path(path)
    return p.name or str(p)


def open_path(path: str) -> None:
    p = Path(path)
    if p.is_dir():
        os.startfile(str(p))
        return
    if p.exists():
        os.startfile(str(p))
        return
    subprocess.Popen(["explorer", str(p.parent)])
