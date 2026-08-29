"""系统设置对话框：主题、透明度、各窗口插件顺序。"""
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
    """系统设置：主题 + 透明度 + 各窗口插件顺序（可分别设置，启动恢复）。"""

    def __init__(self, config, window, parent=None):
        super().__init__(parent)
        self.config = config
        self.window = window
        self.manager = window.manager
        self.setWindowTitle("系统设置")
        self.setMinimumWidth(440)
        self.setMinimumHeight(520)

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

        # ---- 窗口选择 + 插件排序 ----
        win_hint = QLabel("选择窗口，调整该窗口内插件分区的顺序。")
        win_hint.setWordWrap(True)
        win_hint.setStyleSheet("color: #9aa3b5; font-size: 11px;")

        self._window_combo = QComboBox()
        self._window_combo.currentIndexChanged.connect(self._load_window_plugins)

        self._plugin_list = QListWidget()
        self._plugin_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)

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

        # 填充窗口下拉框
        self._windows = self.window._all_windows()
        self._window_ids = list(self._windows)
        for wid in self._window_ids:
            name = "主窗口" if wid == "main" else wid
            self._window_combo.addItem(name, wid)
        self._load_window_plugins()

        # ---- 按钮 ----
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(win_hint)
        lay.addWidget(self._window_combo)
        lay.addWidget(list_row)
        lay.addWidget(buttons)
        self.setLayout(lay)

    # ---- 窗口插件列表 ----

    def _current_window_id(self) -> str:
        idx = self._window_combo.currentIndex()
        return self._window_ids[idx] if 0 <= idx < len(self._window_ids) else "main"

    def _load_window_plugins(self, *_) -> None:
        """加载当前选中窗口的插件列表。"""
        self._plugin_list.clear()
        wid = self._current_window_id()
        win = self._windows.get(wid)
        if win is None:
            return
        for pid in win.plugin_ids:
            item = QListWidgetItem(pid)
            item.setData(Qt.ItemDataRole.UserRole, pid)
            self._plugin_list.addItem(item)
        self._update_plugin_buttons()

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
        new_theme = self._theme_combo.currentData()
        new_alpha = self._opacity_slider.value() / 100.0
        self.config.set("window", "theme", value=new_theme)
        self.config.set("window", "opacity", value=round(new_alpha, 2))

        # 把当前选中窗口的列表写回（UI 里只改了一个窗口）
        wid = self._current_window_id()
        order = []
        for i in range(self._plugin_list.count()):
            item = self._plugin_list.item(i)
            pid = item.data(Qt.ItemDataRole.UserRole)
            if pid:
                order.append(pid)
        windows = dict(self.config.get("windows", default={}) or {})
        wconf = dict(windows.get(wid, {}))
        wconf["plugins"] = order
        windows[wid] = wconf
        self.config.set("windows", value=windows)
        self.config.save()

        # 应用主题 + 透明度
        self.window.apply_theme(new_theme, new_alpha)
        # 重建对应窗口的分区（顺序生效）
        target = self._windows.get(wid)
        if target is not None:
            if wid == "main":
                self.window.populate_sections()
            else:
                target.rebuild()
        self.accept()
