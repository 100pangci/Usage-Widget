"""配置模块：负责默认配置生成、读写、插件设置访问。"""
import copy
import json
import os
import sys
from pathlib import Path

DEFAULT_CONFIG: dict = {
    "window": {
        "width": 300,
        "opacity": 0.92,
        "always_on_top": True,
        "position": [],
        "hidden_sections": [],
    },
    "plugins": {
        "enabled": ["clock", "opencode_usage", "commandcode"],
        "order": ["clock", "opencode_usage", "commandcode"],
        "settings": {},
    },
}


def user_config_dir() -> Path:
    """配置目录：Windows 用 %APPDATA%；Linux 用 $XDG_CONFIG_HOME/usage-widget。"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "usage-widget"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "usage-widget"


class Config:
    """配置读写。修改后需调 save() 持久化。"""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else user_config_dir() / "config.json"
        self.data: dict = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                self.data = _deep_merge(copy.deepcopy(DEFAULT_CONFIG), saved)
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                pass
        else:
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, *keys, default=None):
        node = self.data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def set(self, *keys, value) -> None:
        node = self.data
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value

    def plugin_settings(self, plugin_id: str) -> dict:
        return self.get("plugins", "settings", plugin_id, default={}) or {}

    def set_plugin_setting(self, plugin_id: str, key: str, value) -> None:
        settings = self.data.setdefault("plugins", {}).setdefault("settings", {})
        settings.setdefault(plugin_id, {})[key] = value


def _deep_merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base
