"""系统设置对话框：主题、透明度、插件排序与启停。"""
import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

import core.theme as theme

log = logging.getLogger("usage-widget.settings")


class SystemSettingsDialog(QDialog):
    """系统设置：深色/浅色主题 + 背景透明度 + 插件排序/启停。"""

    def __init__(self, config, window, parent=None):
        super().__init__(parent)
        self.config = config
        self.window = window
        self.manager = window.manager
        self.setWindowTitle("系统设置")
        self.setMinimumWidth(400)
        self.setMinimumHeight(480)

        # ---- 主题 ----
        self._theme_combo = QComboBox()
        self._theme_combo.addItem("深色", theme.DARK)
        self._theme_combo.addItem("浅色", theme.LIGHT)
        current = self.config.get("window", "theme", default=theme.DARK)
        idx = self._theme_combo.findData(current)
        self._theme_combo.setCurrentIndex(max(0, idx))

        # ---- 透明度 ----
        alpha = self.config.get("window", "opacity", default=0.92)
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(30, 100)
        self._opacity_slider.setValue(int(alpha * 100))
        self._opacity_value = QLabel(f"{self._opacity_slider.value()}%")
        self._opacity_value.setStyleSheet("color: #9aa3b5;")
        self._opacity_slider.valueChanged.connect(
            lambda v: self._opacity_value.setText(f"{v}%"))

        form = QFormLayout()
        form.addRow("主题", self._theme_combo)
        form.addRow("背景透明度", self._opacity_slider)
        form.addRow("", self._opacity_value)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # ---- 插件排序/启停 ----
        plugins_hint = QLabel("勾选 = 启用；选中后用右侧按钮调整分区顺序。")
        plugins_hint.setWordWrap(True)
        plugins_hint.setStyleSheet("color: #9aa3b5; font-size: 11px;")

        self._plugin_list = QListWidget()
        self._plugin_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        conf = self.config.get("plugins", default={}) or {}
        self._enabled = list(conf.get("enabled") or [])
        self._order = list(conf.get("order") or self._enabled)
        # 已知插件（已加载 + 磁盘发现的）
        from core.plugin_manager import discover_plugin_ids

        known = set(discover_plugin_ids(self.manager.plugins_dir))
        known |= set(self.manager.plugins.keys())
        for pid in self._order:
            if pid in known:
                self._add_plugin_item(pid, pid in self._enabled)
        for pid in sorted(known - set(self._order)):
            self._add_plugin_item(pid, pid in self._enabled)

        self._up_btn = QPushButton("↑ 上移")
        self._down_btn = QPushButton("↓ 下移")
        self._up_btn.clicked.connect(lambda: self._move_plugin(-1))
        self._down_btn.clicked.connect(lambda: self._move_plugin(1))
        self._plugin_list.currentRowChanged.connect(self._update_plugin_buttons)
        self._update_plugin_buttons()

        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)
        btn_col.addStretch(1)
        btn_col.addWidget(self._up_btn)
        btn_col.addWidget(self._down_btn)
        btn_col.addStretch(1)

        list_row = QWidget()
        list_lay = QHBoxLayout(list_row)
        list_lay.setContentsMargins(0, 0, 0, 0)
        list_lay.setSpacing(8)
        list_lay.addWidget(self._plugin_list, 1)
        list_lay.addLayout(btn_col)

        # ---- 按钮 ----
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(plugins_hint)
        lay.addWidget(list_row)
        lay.addWidget(buttons)
        self.setLayout(lay)

    # ---- 插件列表 ----

    def _add_plugin_item(self, pid: str, enabled: bool) -> None:
        item = QListWidgetItem(pid)
        item.setData(Qt.ItemDataRole.UserRole, pid)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        self._plugin_list.addItem(item)

    def _update_plugin_buttons(self, *_) -> None:
        row = self._plugin_list.currentRow()
        self._up_btn.setEnabled(row > 0)
        self._down_btn.setEnabled(row >= 0 and row < self._plugin_list.count() - 1)

    def _move_plugin(self, direction: int) -> None:
        row = self._plugin_list.currentRow()
        if row < 0:
            return
        new_row = row + direction
        if new_row < 0 or new_row >= self._plugin_list.count():
            return
        item = self._plugin_list.takeItem(row)
        self._plugin_list.insertItem(new_row, item)
        self._plugin_list.setCurrentRow(new_row)
        self._update_plugin_buttons()

    # ---- 保存 ----

    def _save(self) -> None:
        # 主题 + 透明度
        new_theme = self._theme_combo.currentData()
        new_alpha = self._opacity_slider.value() / 100.0
        self.config.set("window", "theme", value=new_theme)
        self.config.set("window", "opacity", value=round(new_alpha, 2))

        # 插件顺序 + 启停
        enabled = []
        order = []
        for i in range(self._plugin_list.count()):
            item = self._plugin_list.item(i)
            pid = item.data(Qt.ItemDataRole.UserRole)
            if pid:
                order.append(pid)
                if item.checkState() == Qt.CheckState.Checked:
                    enabled.append(pid)
        self.config.set("plugins", "enabled", value=enabled)
        self.config.set("plugins", "order", value=order)

        # 主题/透明度即时生效
        self.window.apply_theme(new_theme, new_alpha)

        # 插件启停/顺序有变更才重载插件（否则只重建分区，不打扰其他插件）
        old_order = list(self.manager.plugins)
        old_enabled = set(old_order)
        changed = (sorted(old_enabled) != sorted(enabled)
                   or old_order != order)
        if changed:
            self.config.save()
            self.window._reload_plugins()
        else:
            self.config.save()
            self.window.populate_sections()
        self.accept()
