"""系统监控插件设置对话框：指标顺序（上/下按钮）与显隐（勾选）。"""
import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .plugin import ALL_ITEMS

log = logging.getLogger("system_monitor.settings")

_NAME_MAP = dict(ALL_ITEMS)


class SettingsDialog(QDialog):
    """顺序列表（选中项可用上/下按钮移动）+ 显隐勾选。

    用内置 checkbox（ItemIsUserCheckable）表示显隐；
    顺序用「上移/下移」按钮操作，不用拖拽（Qt6 拖拽有坑）。
    """

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setWindowTitle(f"{plugin.name} · 设置")
        self.setMinimumWidth(400)
        self.setMinimumHeight(360)

        hint = QLabel("选中一项后用右侧按钮调整顺序；取消勾选 = 隐藏该项。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9aa3b5; font-size: 11px;")

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        # 重新加载当前设置
        plugin.load_settings()
        for key in plugin.settings.get("order") or []:
            self._add_item(key, key not in (plugin.settings.get("hidden") or []))

        # 上移/下移按钮
        self._up_btn = QPushButton("↑ 上移")
        self._down_btn = QPushButton("↓ 下移")
        self._up_btn.clicked.connect(lambda: self._move(-1))
        self._down_btn.clicked.connect(lambda: self._move(1))
        self._up_btn.setEnabled(False)
        self._down_btn.setEnabled(False)
        self._list.currentRowChanged.connect(self._update_buttons)
        self._update_buttons()

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
        list_lay.addWidget(self._list, 1)
        list_lay.addLayout(btn_col)
        list_lay.addStretch(0)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addWidget(list_row)
        lay.addWidget(buttons)
        self.setLayout(lay)

    def _add_item(self, key: str, checked: bool) -> None:
        item = QListWidgetItem(_NAME_MAP.get(key, key))
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._list.addItem(item)

    def _update_buttons(self, *_) -> None:
        row = self._list.currentRow()
        self._up_btn.setEnabled(row > 0)
        self._down_btn.setEnabled(row >= 0 and row < self._list.count() - 1)

    def _move(self, direction: int) -> None:
        """direction: -1 上移 / +1 下移。选中项交换到目标行。"""
        row = self._list.currentRow()
        if row < 0:
            return
        new_row = row + direction
        if new_row < 0 or new_row >= self._list.count():
            return
        item = self._list.takeItem(row)
        self._list.insertItem(new_row, item)
        self._list.setCurrentRow(new_row)
        self._update_buttons()

    def _save(self) -> None:
        order = []
        hidden = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            key = item.data(Qt.ItemDataRole.UserRole)
            if key:
                order.append(key)
                if item.checkState() != Qt.CheckState.Checked:
                    hidden.append(key)
        self.plugin.settings["order"] = order
        self.plugin.settings["hidden"] = hidden
        self.plugin.save_settings()
        self.accept()
