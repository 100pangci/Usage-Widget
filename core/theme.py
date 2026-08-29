"""全局主题：深色/浅色模式。

插件通过 theme.is_dark() 或 theme.colors() 获取当前主题色，
无需感知切换逻辑。窗口层（背景/边框/菜单）也读取主题。
"""
from __future__ import annotations

DARK = "dark"
LIGHT = "light"

_current = DARK

# 深色主题（默认）：深底浅字
DARK_COLORS = {
    "bg": "#282d38",          # 面板背景
    "text": "#dfe3ea",        # 主文字
    "dim": "#9aa3b5",         # 次要文字
    "border": "rgba(255,255,255,26)",
    "bar_bg": "rgba(255,255,255,16)",
    "card_bg": "rgba(255,255,255,12)",
    # 语义色（两种主题下保持一致，避免误读）
    "accent": "#4f8cff",
    "green": "#7cc76b",
    "amber": "#e5b94d",
    "red": "#e06c5a",
}

# 浅色主题：暖调柔和（米白底 + 深灰字 + 柔和语义色）
LIGHT_COLORS = {
    "bg": "#faf8f4",          # 米白底（比纯白柔和，比冷灰温暖）
    "text": "#23272f",        # 主文字（深色，高对比）
    "dim": "#4b525c",         # 次要文字（深灰，保证可读）
    "border": "rgba(60, 64, 76, 40)",   # 半透明深灰边框
    "bar_bg": "rgba(60, 64, 76, 25)",   # 进度条底（半透明深灰）
    "card_bg": "rgba(60, 64, 76, 12)",  # 卡片底（半透明深灰）
    # 语义色（柔和、不刺眼，浅底上可读）
    "accent": "#3b7dd8",
    "green": "#3e8e4c",
    "amber": "#a97a10",
    "red": "#c74343",
}


def set_theme(theme: str) -> None:
    global _current
    _current = theme if theme in (DARK, LIGHT) else DARK


def get_theme() -> str:
    return _current


def is_dark() -> bool:
    return _current == DARK


def colors() -> dict:
    """当前主题的全部颜色。"""
    return DARK_COLORS if _current == DARK else LIGHT_COLORS


def color(key: str) -> str:
    """取当前主题下某个颜色（未知 key 返回空串）。"""
    return colors().get(key, "")
