"""系统设置对话框：主题（深色/浅色）与背景透明度。"""
import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
)

import core.theme as theme

log = logging.getLogger("usage-widget.settings")


class SystemSettingsDialog(QDialog):
    """系统设置：深色/浅色主题 + 背景透明度。"""

    def __init__(self, config, window, parent=None):
        super().__init__(parent)
        self.config = config
        self.window = window
        self.setWindowTitle("系统设置")
        self.setMinimumWidth(360)

        # 主题
        self._theme_combo = QComboBox()
        self._theme_combo.addItem("深色", theme.DARK)
        self._theme_combo.addItem("浅色", theme.LIGHT)
        current = self.config.get("window", "theme", default=theme.DARK)
        idx = self._theme_combo.findData(current)
        self._theme_combo.setCurrentIndex(max(0, idx))

        # 透明度
        alpha = self.config.get("window", "opacity", default=0.92)
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(30, 100)
        self._opacity_slider.setValue(int(alpha * 100))
        self._opacity_value = QLabel(f"{self._opacity_slider.value()}%")
        self._opacity_value.setStyleSheet("color: #9aa3b5;")
        self._opacity_slider.valueChanged.connect(
            lambda v: self._opacity_value.setText(f"{v}%"))
        opacity_row = self._opacity_slider

        form = QFormLayout()
        form.addRow("主题", self._theme_combo)
        form.addRow("背景透明度", opacity_row)
        form.addRow("", self._opacity_value)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)
        self.setLayout(lay)

    def _save(self) -> None:
        new_theme = self._theme_combo.currentData()
        new_alpha = self._opacity_slider.value() / 100.0
        self.config.set("window", "theme", value=new_theme)
        self.config.set("window", "opacity", value=round(new_alpha, 2))
        self.config.save()
        # 应用主题 + 透明度
        self.window.apply_theme(new_theme, new_alpha)
        self.accept()
