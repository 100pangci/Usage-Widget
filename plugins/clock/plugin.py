"""示例插件：时钟。

演示插件 API：create_widget 建分区、tick 定时刷新、
get_setting 读配置。新插件照此目录结构写即可。
"""
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from plugins.base import Plugin


class ClockPlugin(Plugin):
    id = "clock"
    name = "时钟"
    version = "0.1.0"
    description = "示例插件：显示当前时间"
    refresh_interval = 1000

    def __init__(self):
        super().__init__()
        self._time_label = None
        self._date_label = None

    def create_widget(self, parent) -> QWidget:
        widget = QWidget(parent)
        self._time_label = QLabel("--:--:--")
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._time_label.setStyleSheet("font-size: 26px; font-weight: bold;")
        self._date_label = QLabel()
        self._date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lay = QVBoxLayout(widget)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.addWidget(self._time_label)
        lay.addWidget(self._date_label)
        widget.setLayout(lay)
        return widget

    def tick(self) -> None:
        now = datetime.now()
        fmt = self.get_setting("time_format", "%H:%M:%S")
        if self._time_label is not None:
            self._time_label.setText(now.strftime(fmt))
        if self._date_label is not None:
            self._date_label.setText(now.strftime(self.get_setting("date_format", "%Y-%m-%d %A")))


def create_plugin() -> ClockPlugin:
    return ClockPlugin()
