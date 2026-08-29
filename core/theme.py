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

# 浅色主题：浅底深字
LIGHT_COLORS = {
    "bg": "#f2f4f8",
    "text": "#1f2430",
    "dim": "#5a6270",
    "border": "rgba(0,0,0,40)",
    "bar_bg": "rgba(0,0,0,24)",
    "card_bg": "rgba(0,0,0,14)",
    "accent": "#2f6fd6",
    "green": "#3f9e4f",
    "amber": "#b8860b",
    "red": "#d64545",
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
