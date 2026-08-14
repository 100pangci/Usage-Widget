"""通用 UI 控件：插件分区可复用的基础部件。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar


class TextRow(QFrame):
    """一行文本：左标签 + 右值，例如 CPU 使用率。"""

    def __init__(self, label: str, value: str = "", unit: str = "", parent=None):
        super().__init__(parent)
        self._value_label = QLabel(value)
        self._value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._value_label.setStyleSheet("font-weight: bold;")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 1, 4, 1)
        lay.setSpacing(8)
        lay.addWidget(QLabel(label))
        lay.addStretch(1)
        lay.addWidget(self._value_label)
        if unit:
            lay.addWidget(QLabel(unit))
        self.setLayout(lay)

    def set_value(self, text: str) -> None:
        self._value_label.setText(text)


class BarGauge(QFrame):
    """带标签的进度条：标签 + 百分比条 + 数值。"""

    def __init__(self, label: str, value: int = 0, unit: str = "", parent=None):
        super().__init__(parent)
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(value)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        self._value_label = QLabel(f"{value}{unit}")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 1, 4, 1)
        lay.setSpacing(8)
        lay.addWidget(QLabel(label))
        lay.addWidget(self._bar, 1)
        lay.addWidget(self._value_label)
        self.setLayout(lay)

    def set_value(self, value: int, unit: str = "") -> None:
        self._bar.setValue(max(0, min(100, value)))
        self._value_label.setText(f"{value}{unit}")
