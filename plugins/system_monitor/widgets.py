"""曲线控件：SparkLine（单指标历史曲线）+ NetSpark（网络上下行双曲线）。

QPainter 自绘，跟随主题取色；颜色从 core.theme 读取（重写插件时
主题已切换，控件重建时重新取色），无框架独立运行时回退深色。
"""
from collections import deque

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

_HISTORY = 90  # 曲线保留 90 个采样点（tick 2s ≈ 180 秒）

# 无框架独立运行（如直接跑插件）时的回退颜色
_FALLBACK_COLORS = {
    "dim": "#9aa3b5",
    "text": "#dfe3ea",
    "green": "#7cc76b",
    "amber": "#e5b94d",
    "red": "#e06c5a",
}


def _theme_colors() -> dict:
    try:
        from core.theme import color

        return {k: color(k) for k in ("dim", "text", "green", "amber", "red")}
    except ImportError:
        return _FALLBACK_COLORS


def _is_dark_theme() -> bool:
    try:
        from core.theme import is_dark

        return is_dark()
    except ImportError:
        return True


def DIM() -> str:
    return _theme_colors()["dim"]


def TEXT() -> str:
    return _theme_colors()["text"]


def GREEN() -> str:
    return _theme_colors()["green"]


def AMBER() -> str:
    return _theme_colors()["amber"]


def RED() -> str:
    return _theme_colors()["red"]


# 曲线/进度条颜色（两种主题下保持辨识度）
ACCENT = "#4f8cff"
GPU_COLOR = "#c67cff"
COLORS = {
    "cpu": "#4f8cff",
    "mem": "#e5b94d",
    "disk": "#7cc76b",
    "gpu": "#c67cff",
}


def percent_color(percent: int) -> str:
    """使用率颜色：<50% 绿 / <80% 黄 / ≥80% 红。"""
    if percent >= 80:
        return RED()
    if percent >= 50:
        return AMBER()
    return GREEN()


def _hex_color(hex_str: str, alpha: int = 255) -> QColor:
    c = QColor(hex_str)
    c.setAlpha(alpha)
    return c


class SparkLine(QWidget):
    """滚动历史曲线（QPainter 自绘）。

    value() 范围 [min_v, max_v]，自动归一化到控件高度；
    内部保留最近 N 个采样点，新点从右侧进入、旧点向左滚动。
    """

    def __init__(self, color: str, min_v: float = 0.0, max_v: float = 100.0,
                 max_points: int = _HISTORY, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._min_v = min_v
        self._max_v = max_v
        self._max_points = max_points
        self._data: deque[float] = deque(maxlen=max_points)
        self.setFixedHeight(28)
        self.setSizePolicy(
            self.sizePolicy().horizontalPolicy(),
            self.sizePolicy().verticalPolicy(),
        )

    def add(self, value: float) -> None:
        self._data.append(value)
        self.update()

    def clear(self) -> None:
        self._data.clear()
        self.update()

    def paintEvent(self, event):
        if not self._data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        if w <= 2 or h <= 2:
            p.end()
            return

        span = self._max_v - self._min_v
        if span <= 0:
            p.end()
            return

        def to_point(i: int, v: float) -> QPointF:
            x = (i + 0.5) / self._max_points * w
            y = h - 2 - (v - self._min_v) / span * (h - 4)
            return QPointF(x, max(0.0, min(float(h - 2), y)))

        # 填充渐变（曲线下方向透明）
        fill = QPainterPath()
        pts = [to_point(i, v) for i, v in enumerate(self._data)]
        fill.moveTo(pts[0].x(), float(h))
        for pt in pts:
            fill.lineTo(pt)
        fill.lineTo(pts[-1].x(), float(h))
        fill.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        grad = QColor(self._color)
        grad.setAlpha(60)
        p.setBrush(grad)
        p.drawPath(fill)

        # 曲线本身
        path = QPainterPath()
        path.moveTo(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        p.setPen(QColor(self._color))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.end()


class NetSpark(QWidget):
    """网络迷你双曲线：下行（下）/上行（上）方向相反的填充区。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._down: deque[float] = deque(maxlen=_HISTORY)
        self._up: deque[float] = deque(maxlen=_HISTORY)
        self.setFixedHeight(24)
        self.setSizePolicy(
            self.sizePolicy().horizontalPolicy(),
            self.sizePolicy().verticalPolicy(),
        )

    def add(self, down_kb: float, up_kb: float) -> None:
        self._down.append(down_kb)
        self._up.append(up_kb)
        self.update()

    def clear(self) -> None:
        self._down.clear()
        self._up.clear()
        self.update()

    def paintEvent(self, event):
        if not self._down:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        if w <= 2 or h <= 2:
            p.end()
            return

        # 动态最大值：取当前窗口内最大速率，避免曲线永远贴着顶部
        peak = max(max(self._down or [0]), max(self._up or [0]), 1.0)

        mid = h / 2.0
        def to_pt(data: deque, i: int, sign: float) -> QPointF:
            x = (i + 0.5) / _HISTORY * w
            y = mid - sign * (data[i] / peak) * (mid - 2)
            return QPointF(x, max(1.0, min(float(h - 1), y)))

        def draw_side(data: deque, color_hex: str, sign: float) -> None:
            color = QColor(color_hex)
            path = QPainterPath()
            pts = [to_pt(data, i, sign) for i in range(len(data))]
            path.moveTo(pts[0].x(), mid)
            for pt in pts:
                path.lineTo(pt)
            path.lineTo(pts[-1].x(), mid)
            path.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            grad = QColor(color)
            grad.setAlpha(45)
            p.setBrush(grad)
            p.drawPath(path)
            # 曲线本身
            line = QPainterPath()
            line.moveTo(pts[0])
            for pt in pts[1:]:
                line.lineTo(pt)
            p.setPen(color)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(line)

        draw_side(self._up, GREEN(), -1)     # 上行（绿色，上半）
        draw_side(self._down, ACCENT, 1)     # 下行（蓝色，下半）

        # 中间分隔线（跟随主题：深色用白、浅色用黑，透明度低）
        sep_hex = "#ffffff" if _is_dark_theme() else "#000000"
        p.setPen(_hex_color(sep_hex, 26))
        p.drawLine(0, int(mid), w, int(mid))
        p.end()
