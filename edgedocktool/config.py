from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def _app_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "EdgeDockTool"


CONFIG_DIR = _app_data_dir()
CONFIG_PATH = CONFIG_DIR / "config.json"


@dataclass
class ShortcutItem:
    name: str
    path: str
    kind: str


@dataclass
class AppConfig:
    auto_start: bool = False
    hotkey_modifiers: list[str] = field(default_factory=lambda: ["Alt"])
    hotkey_key: str = "Space"
    shortcuts: list[ShortcutItem] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        # Configs from the edge-dock release used these fields. The new
        # launcher intentionally starts with the requested Alt + Space default.
        legacy_edge_config = "edge" in data or "launch_mode" in data
        shortcuts = []
        for item in data.get("shortcuts", []):
            try:
                shortcuts.append(ShortcutItem(**item))
            except (TypeError, KeyError):
                continue

        if legacy_edge_config:
            hotkey_modifiers = ["Alt"]
            hotkey_key = "Space"
        else:
            hotkey_modifiers = [
                item
                for item in data.get("hotkey_modifiers", ["Alt"])
                if item in {"Ctrl", "Alt", "Shift"}
            ]
            hotkey_key = str(data.get("hotkey_key", "Space")) or "Space"
        if not hotkey_modifiers:
            hotkey_modifiers = ["Alt"]
        return cls(
            auto_start=bool(data.get("auto_start", False)),
            hotkey_modifiers=hotkey_modifiers,
            hotkey_key=hotkey_key,
            shortcuts=shortcuts,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "auto_start": self.auto_start,
            "hotkey_modifiers": self.hotkey_modifiers,
            "hotkey_key": self.hotkey_key,
            "shortcuts": [asdict(item) for item in self.shortcuts],
        }


def load_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        return AppConfig()
    try:
        return AppConfig.from_dict(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception:
        return AppConfig()


def save_config(config: AppConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(config.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
