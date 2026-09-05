from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import CONFIG_DIR, ShortcutItem
from .utils import display_name, item_kind


SHORTCUTS_DIR = CONFIG_DIR / "Shortcuts"
ENTRY_SUFFIX = ".edgedock"


def ensure_shortcuts_dir() -> Path:
    SHORTCUTS_DIR.mkdir(parents=True, exist_ok=True)
    return SHORTCUTS_DIR


def _safe_name(name: str) -> str:
    cleaned = "".join("_" if char in '<>:"/\\|?*' else char for char in name).strip(" .")
    return cleaned[:100] or "快捷入口"


def _unique_path(directory: Path, name: str, suffix: str = "") -> Path:
    candidate = directory / f"{name}{suffix}"
    index = 2
    while candidate.exists():
        candidate = directory / f"{name} ({index}){suffix}"
        index += 1
    return candidate


def _is_managed(path: Path) -> bool:
    try:
        path.resolve().relative_to(ensure_shortcuts_dir().resolve())
        return True
    except (OSError, ValueError):
        return False


def add_shortcut(target: str, directory: Path | None = None) -> Path | None:
    source = Path(target)
    if not source.exists() or _is_managed(source):
        return None

    destination_dir = directory or ensure_shortcuts_dir()
    destination_dir.mkdir(parents=True, exist_ok=True)
    name = _safe_name(source.stem if source.suffix.lower() in {".exe", ".lnk"} else display_name(target))
    if source.suffix.lower() == ".lnk":
        destination = _unique_path(destination_dir, name, ".lnk")
        shutil.copy2(source, destination)
        return destination

    destination = _unique_path(destination_dir, name, ENTRY_SUFFIX)
    destination.write_text(json.dumps({"target": str(source)}, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def migrate_legacy_shortcuts(shortcuts: list[ShortcutItem]) -> bool:
    if not shortcuts:
        return False
    for item in shortcuts:
        add_shortcut(item.path)
    return True


def _read_item(entry: Path) -> ShortcutItem | None:
    if entry.is_dir():
        return ShortcutItem(entry.name, str(entry), "stack", str(entry))
    if entry.suffix.lower() == ENTRY_SUFFIX:
        try:
            target = str(json.loads(entry.read_text(encoding="utf-8"))["target"])
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            return None
        return ShortcutItem(entry.stem, target, item_kind(target), str(entry))
    return ShortcutItem(entry.stem if entry.suffix.lower() == ".lnk" else entry.name, str(entry), "file", str(entry))


def scan_shortcuts(directory: Path | None = None) -> list[ShortcutItem]:
    folder = directory or ensure_shortcuts_dir()
    if not folder.exists():
        return []
    items = []
    for entry in sorted(folder.iterdir(), key=lambda path: (not path.is_dir(), path.name.casefold())):
        if entry.name.startswith("."):
            continue
        item = _read_item(entry)
        if item is not None:
            items.append(item)
    return items


def create_stack(source_storage: str, target_storage: str) -> Path | None:
    source = Path(source_storage)
    target = Path(target_storage)
    if source == target or source.is_dir() or not source.exists() or not target.exists():
        return None
    if not _is_managed(source) or not _is_managed(target):
        return None

    if target.is_dir():
        destination = _unique_path(target, source.stem, source.suffix)
        source.rename(destination)
        return target
    if source.parent != target.parent:
        return None

    stack = _unique_path(target.parent, f"{target.stem} 堆叠")
    stack.mkdir()
    target.rename(_unique_path(stack, target.stem, target.suffix))
    source.rename(_unique_path(stack, source.stem, source.suffix))
    return stack


def storage_signature(directory: Path | None = None) -> tuple[tuple[str, int, int], ...]:
    root = directory or ensure_shortcuts_dir()
    if not root.exists():
        return ()
    signature = []
    for entry in root.iterdir():
        try:
            stat = entry.stat()
            signature.append((entry.name, stat.st_mtime_ns, stat.st_size))
        except OSError:
            continue
    return tuple(sorted(signature))
