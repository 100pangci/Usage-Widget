"""系统监控插件：CPU/内存/磁盘/网络/开机时长。

纯标准库实现（collector.py），不依赖第三方包——发行版插件目录
可独立运行。Windows 用 ctypes 调系统 API，Linux 读 /proc。
"""
import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from plugins.base import Plugin

from .collector import NetSampler, cpu_percent, disk_percent, mem_percent, uptime_hours

log = logging.getLogger("system_monitor.plugin")

DIM = "#9aa3b5"
GREEN = "#7cc76b"
AMBER = "#e5b94d"
RED = "#e06c5a"


def percent_color(percent: int) -> str:
    """使用率颜色：<50% 绿 / <80% 黄 / ≥80% 红。"""
    if percent >= 80:
        return RED
    if percent >= 50:
        return AMBER
    return GREEN


class SystemMonitorPlugin(Plugin):
    id = "system_monitor"
    name = "系统监控"
    version = "0.1.0"
    description = "CPU/内存/磁盘/网络/开机时长"
    refresh_interval = 1000

    def __init__(self, context=None):
        super().__init__(context)
        self._labels: dict[str, QLabel] = {}
        self._net = NetSampler()

    # ---- UI ----

    def create_widget(self, parent) -> QWidget:
        widget = QWidget(parent)
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(12, 4, 12, 8)
        lay.setSpacing(4)

        def add_row(key: str, label: str, value: str = "--") -> None:
            row = QLabel(f"{label}  {value}")
            row.setStyleSheet(f"font-size: 12px; color: {DIM};")
            lay.addWidget(row)
            self._labels[key] = row

        add_row("cpu", "CPU", "--")
        add_row("mem", "内存", "--")
        add_row("disk", "磁盘", "--")
        add_row("net", "网络", "--")
        add_row("uptime", "开机", "--")

        widget.setLayout(lay)
        return widget

    # ---- 刷新 ----

    def tick(self) -> None:
        if not self._labels:
            return
        cpu = cpu_percent()
        self._labels["cpu"].setText(
            f"CPU  <span style='color:{percent_color(cpu)};'>{cpu}%</span>")

        mem = mem_percent()
        self._labels["mem"].setText(
            f"内存  <span style='color:{percent_color(mem)};'>{mem}%</span>")

        disk = disk_percent()
        self._labels["disk"].setText(
            f"磁盘  <span style='color:{percent_color(disk)};'>{disk}%</span>")

        down, up = self._net.sample()
        self._labels["net"].setText(
            f"网络  ↓{_fmt_speed(down)}  ↑{_fmt_speed(up)}")

        hours = uptime_hours()
        self._labels["uptime"].setText(f"开机  {_fmt_uptime(hours)}")

    def on_stop(self) -> None:
        self._labels = {}
        self._net.reset()


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
    if days >= 1:
        return f"{days}天{h}小时"
    return f"{h}小时"


def create_plugin() -> SystemMonitorPlugin:
    return SystemMonitorPlugin()
