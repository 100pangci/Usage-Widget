"""系统监控插件：CPU/内存/磁盘/GPU/网络实时曲线 + 开机时长。

纯标准库实现（collector.py），不依赖第三方包——发行版插件目录
可独立运行。Windows 用 ctypes 调系统 API，Linux 读 /proc。

设置（右键 → 插件设置）：
- 指标顺序：上下拖动调整
- 显隐：每项勾选框，隐藏项不显示
"""
import json
import logging
from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from plugins.base import Plugin

from .collector import (
    NetSampler,
    cpu_percent,
    disk_io_percent,
    gpu_names,
    gpu_stats,
    mem_percent,
    uptime_hours,
)

log = logging.getLogger("system_monitor.plugin")

# ---- 主题颜色（深色/浅色两套，跟随 core.theme）----

_COLORS_DARK = {
    "dim": "#9aa3b5",
    "text": "#dfe3ea",
    "green": "#7cc76b",
    "amber": "#e5b94d",
    "red": "#e06c5a",
}

_COLORS_LIGHT = {
    "dim": "#5a6270",
    "text": "#1f2430",
    "green": "#3f9e4f",
    "amber": "#b8860b",
    "red": "#d64545",
}


def _theme_colors() -> dict:
    try:
        from core.theme import is_dark

        return _COLORS_DARK if is_dark() else _COLORS_LIGHT
    except ImportError:
        return _COLORS_DARK  # 独立运行（无框架）时用深色


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

_HISTORY = 90  # 曲线保留 90 个采样点（90 秒）

# 指标 key 顺序（默认显示顺序，设置里可改）
ALL_ITEMS = [
    ("cpu", "CPU"),
    ("gpu", "GPU"),
    ("mem", "内存"),
    ("disk", "磁盘"),
    ("net", "网络"),
    ("uptime", "开机"),
]

DEFAULT_ORDER = [key for key, _ in ALL_ITEMS]

COLORS = {
    "cpu": "#4f8cff",
    "mem": "#e5b94d",
    "disk": "#7cc76b",
    "gpu": "#c67cff",
}

# 合法指标 key（设置持久化校验用）
_KNOWN_KEYS = {key for key, _ in ALL_ITEMS}


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
        draw_side(self._down, "#4f8cff", 1)  # 下行（蓝色，下半）

        # 中间分隔线
        p.setPen(_hex_color("#ffffff", 20))
        p.drawLine(0, int(mid), w, int(mid))
        p.end()


class SystemMonitorPlugin(Plugin):
    id = "system_monitor"
    name = "系统监控"
    version = "0.3.0"
    description = "CPU/内存/磁盘/GPU/网络实时曲线、开机时长"
    refresh_interval = 1000

    def __init__(self, context=None):
        super().__init__(context)
        self.settings = {
            "order": list(DEFAULT_ORDER),
            "hidden": [],
        }
        self._sparks: dict[str, SparkLine] = {}
        self._pct_labels: dict[str, QLabel] = {}
        self._mem_labels: dict[str, QLabel] = {}
        self._gpu_sparks: list[tuple[str, SparkLine, QLabel, QLabel]] = []
        self._net_spark: NetSpark | None = None
        self._net_labels: dict[str, QLabel] = {}
        self._uptime_label: QLabel | None = None
        self._net = NetSampler()

    # ---- 设置持久化 ----

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    def load_settings(self) -> None:
        """读取设置；默认顺序 + 空 hidden，缺项补齐。"""
        self.settings = {
            "order": list(DEFAULT_ORDER),
            "hidden": [],
        }
        if self.settings_path.is_file():
            try:
                saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
                order = saved.get("order")
                if isinstance(order, list) and order:
                    # 只保留已知 key，缺失的补到末尾
                    known = [k for k in order if k in _KNOWN_KEYS]
                    for k in DEFAULT_ORDER:
                        if k not in known:
                            known.append(k)
                    self.settings["order"] = known
                hidden = saved.get("hidden")
                if isinstance(hidden, list):
                    self.settings["hidden"] = [k for k in hidden if k in _KNOWN_KEYS]
            except (json.JSONDecodeError, OSError):
                log.warning("settings.json 解析失败，使用默认值")

    def save_settings(self) -> None:
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(
                json.dumps(self.settings, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError:
            log.exception("保存设置失败")

    def visible_items(self) -> list[str]:
        """按设置顺序返回可见的指标 key（hidden 里的跳过）。"""
        hidden = set(self.settings.get("hidden") or [])
        return [k for k in self.settings.get("order") or DEFAULT_ORDER if k not in hidden]

    # ---- UI ----

    def create_widget(self, parent) -> QWidget:
        self.load_settings()
        widget = QWidget(parent)
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(12, 4, 12, 8)
        lay.setSpacing(5)

        def make_gauge(key: str, label: str, color: str) -> None:
            name = QLabel(label)
            name.setStyleSheet(f"font-size: 12px; color: {DIM()};")
            pct = QLabel("--")
            pct.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {DIM()};")
            head = QWidget()
            head_lay = QHBoxLayout(head)
            head_lay.setContentsMargins(0, 0, 0, 0)
            head_lay.setSpacing(6)
            head_lay.addWidget(name)
            head_lay.addStretch(1)
            head_lay.addWidget(pct)
            lay.addWidget(head)

            spark = SparkLine(color)
            lay.addWidget(spark)
            self._sparks[key] = spark
            self._pct_labels[key] = pct

        def make_net() -> None:
            net_head = QWidget()
            net_head_lay = QHBoxLayout(net_head)
            net_head_lay.setContentsMargins(0, 0, 0, 0)
            net_head_lay.setSpacing(6)
            net_label = QLabel("网络")
            net_label.setStyleSheet(f"font-size: 12px; color: {DIM()};")
            net_head_lay.addWidget(net_label)
            net_head_lay.addStretch(1)
            down_lbl = QLabel("↓--")
            down_lbl.setStyleSheet(f"font-size: 11px; color: {TEXT()};")
            up_lbl = QLabel("↑--")
            up_lbl.setStyleSheet(f"font-size: 11px; color: {TEXT()};")
            net_head_lay.addWidget(down_lbl)
            net_head_lay.addWidget(up_lbl)
            lay.addWidget(net_head)
            self._net_labels["down"] = down_lbl
            self._net_labels["up"] = up_lbl

            net_spark = NetSpark()
            lay.addWidget(net_spark)
            self._net_spark = net_spark

        def make_uptime() -> None:
            uptime_box = QFrame()
            uptime_box.setStyleSheet(
                "background: rgba(255,255,255,12); border-radius: 6px;")
            uptime_lay = QHBoxLayout(uptime_box)
            uptime_lay.setContentsMargins(8, 4, 8, 4)
            uptime_lay.setSpacing(6)
            up_label = QLabel("开机")
            up_label.setStyleSheet(f"font-size: 11px; color: {DIM()};")
            uptime_lay.addWidget(up_label)
            uptime_lay.addStretch(1)
            self._uptime_label = QLabel("--")
            self._uptime_label.setStyleSheet(
                f"font-size: 12px; font-weight: 600; color: {TEXT()};")
            uptime_lay.addWidget(self._uptime_label)
            lay.addWidget(uptime_box)

        # GPU 分区构建（自动枚举，每块一行曲线）
        gpu_list = gpu_names()

        def make_gpu() -> None:
            for i, gname in enumerate(gpu_list):
                name = QLabel(gname)
                name.setStyleSheet(f"font-size: 11px; color: {DIM()};")
                name.setToolTip(gname)
                pct = QLabel("--")
                pct.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {DIM()};")
                mem_lbl = QLabel("")
                mem_lbl.setStyleSheet(f"font-size: 10px; color: {DIM()};")
                head = QWidget()
                head_lay = QHBoxLayout(head)
                head_lay.setContentsMargins(0, 0, 0, 0)
                head_lay.setSpacing(6)
                head_lay.addWidget(name)
                head_lay.addStretch(1)
                head_lay.addWidget(pct)
                head_lay.addWidget(mem_lbl)
                lay.addWidget(head)

                spark = SparkLine(GPU_COLOR)
                lay.addWidget(spark)
                self._gpu_sparks.append((f"gpu{i}", spark, pct, mem_lbl))

        for key in self.visible_items():
            if key == "gpu":
                if gpu_list:
                    make_gpu()
            elif key in COLORS:
                label = dict(ALL_ITEMS)[key]
                make_gauge(key, label, COLORS[key])
            elif key == "net":
                make_net()
            elif key == "uptime":
                make_uptime()

        widget.setLayout(lay)
        return widget

    # ---- 刷新 ----

    def tick(self) -> None:
        if not self._sparks and not self._net_spark and not self._gpu_sparks:
            return

        if "cpu" in self._sparks:
            cpu = cpu_percent()
            self._sparks["cpu"].add(cpu)
            self._pct_labels["cpu"].setText(f"{cpu}%")
            self._pct_labels["cpu"].setStyleSheet(
                f"font-size: 13px; font-weight: 700; color: {percent_color(cpu)};")

        if "mem" in self._sparks:
            mem = mem_percent()
            self._sparks["mem"].add(mem)
            self._pct_labels["mem"].setText(f"{mem}%")
            self._pct_labels["mem"].setStyleSheet(
                f"font-size: 13px; font-weight: 700; color: {percent_color(mem)};")

        if "disk" in self._sparks:
            disk = disk_io_percent()
            self._sparks["disk"].add(disk)
            self._pct_labels["disk"].setText(f"{disk}%")
            self._pct_labels["disk"].setStyleSheet(
                f"font-size: 13px; font-weight: 700; color: {percent_color(disk)};")

        # GPU：多卡自动更新
        if self._gpu_sparks:
            gpu_data = gpu_stats()
            for idx, (key, spark, pct_lbl, mem_lbl) in enumerate(self._gpu_sparks):
                if idx < len(gpu_data):
                    d = gpu_data[idx]
                    util = d["util"]
                    spark.add(util)
                    pct_lbl.setText(f"{util}%")
                    pct_lbl.setStyleSheet(
                        f"font-size: 12px; font-weight: 700; color: {percent_color(util)};")
                    if d["mem_total_mb"] > 0:
                        mem_lbl.setText(
                            f"{_fmt_mb(d['mem_used_mb'])}/{_fmt_mb(d['mem_total_mb'])}")
                    else:
                        mem_lbl.setText("")

        if self._net_spark is not None:
            down, up = self._net.sample()
            self._net_spark.add(down, up)
            self._net_labels["down"].setText(f"↓{_fmt_speed(down)}")
            self._net_labels["up"].setText(f"↑{_fmt_speed(up)}")

        if self._uptime_label is not None:
            hours = uptime_hours()
            self._uptime_label.setText(_fmt_uptime(hours))

    def on_stop(self) -> None:
        self._sparks = {}
        self._pct_labels = {}
        self._gpu_sparks = []
        self._net_spark = None
        self._net_labels = {}
        self._uptime_label = None
        self._net.reset()

    def settings_dialog(self, parent=None):
        from .settings_dialog import SettingsDialog

        return SettingsDialog(self, parent)


def _fmt_mb(mb: int) -> str:
    """MB → 自适应单位（GB 显示小数，MB 显示整数）。"""
    if mb >= 1024:
        gb = mb / 1024
        return f"{gb:.1f}G" if gb < 10 else f"{gb:.0f}G"
    return f"{mb}M"


def _fmt_speed(kb_per_s: float) -> str:
    """KB/s → 自适应单位（KB/s / MB/s / GB/s）。"""
    if kb_per_s >= 1024 * 1024:
        return f"{kb_per_s / 1024 / 1024:.1f}GB/s"
    if kb_per_s >= 1024:
        return f"{kb_per_s / 1024:.1f}MB/s"
    return f"{kb_per_s:.0f}KB/s"


def _fmt_uptime(hours: float) -> str:
    total_minutes = int(hours * 60)
    days = total_minutes // (24 * 60)
    h = total_minutes % (24 * 60) // 60
    m = total_minutes % 60
    parts = []
    if days >= 1:
        parts.append(f"{days}天")
    if h >= 1:
        parts.append(f"{h}小时")
    if m > 0 and days < 1:
        parts.append(f"{m}分")
    if not parts:
        parts.append(f"{m}分")
    return " ".join(parts)


def create_plugin() -> SystemMonitorPlugin:
    return SystemMonitorPlugin()
