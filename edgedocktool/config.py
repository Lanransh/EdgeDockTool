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
    edge: str = "right"
    offset: int = 0
    hover_delay_ms: int = 1000
    hide_delay_ms: int = 240
    pinned_position: bool = False
    launch_mode: str = "hover"
    hotkey_modifiers: list[str] = field(default_factory=lambda: ["Ctrl", "Alt"])
    hotkey_key: str = "Space"
    shortcuts: list[ShortcutItem] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        shortcuts = [ShortcutItem(**item) for item in data.get("shortcuts", [])]
        edge = data.get("edge", "right")
        offset = int(data.get("offset", 0))
        hover_delay_ms = int(data.get("hover_delay_ms", 1000))
        hide_delay_ms = int(data.get("hide_delay_ms", 240))
        pinned_position = bool(data.get("pinned_position", False))
        launch_mode = str(data.get("launch_mode", "hover"))
        hotkey_modifiers = list(data.get("hotkey_modifiers", ["Ctrl", "Alt"]))
        hotkey_key = str(data.get("hotkey_key", "Space"))
        if edge not in {"left", "right", "top", "bottom"}:
            edge = "right"
        if launch_mode not in {"hover", "hotkey"}:
            launch_mode = "hover"
        hotkey_modifiers = [item for item in hotkey_modifiers if item in {"Ctrl", "Alt", "Shift"}]
        if not hotkey_modifiers:
            hotkey_modifiers = ["Ctrl", "Alt"]
        if not hotkey_key:
            hotkey_key = "Space"
        hover_delay_ms = max(100, min(5000, hover_delay_ms))
        hide_delay_ms = max(0, min(5000, hide_delay_ms))
        return cls(
            edge=edge,
            offset=offset,
            hover_delay_ms=hover_delay_ms,
            hide_delay_ms=hide_delay_ms,
            pinned_position=pinned_position,
            launch_mode=launch_mode,
            hotkey_modifiers=hotkey_modifiers,
            hotkey_key=hotkey_key,
            shortcuts=shortcuts,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge": self.edge,
            "offset": self.offset,
            "hover_delay_ms": self.hover_delay_ms,
            "hide_delay_ms": self.hide_delay_ms,
            "pinned_position": self.pinned_position,
            "launch_mode": self.launch_mode,
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
    CONFIG_PATH.write_text(json.dumps(config.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
