"""系统监控插件设置对话框：指标顺序（拖拽）与显隐（勾选）。"""
import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .plugin import ALL_ITEMS

log = logging.getLogger("system_monitor.settings")

_NAME_MAP = dict(ALL_ITEMS)


class SettingsDialog(QDialog):
    """顺序列表（可拖拽）+ 显隐勾选。"""

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setWindowTitle(f"{plugin.name} · 设置")
        self.setMinimumWidth(360)
        self.setMinimumHeight(360)

        hint = QLabel("上下拖动调整顺序；取消勾选 = 隐藏该项。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9aa3b5; font-size: 11px;")

        self._list = QListWidget()
        self._list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        # 重新加载当前设置
        plugin.load_settings()
        for key in plugin.settings.get("order") or []:
            self._add_item(key, key not in (plugin.settings.get("hidden") or []))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addWidget(self._list)
        lay.addWidget(buttons)
        self.setLayout(lay)

    def _add_item(self, key: str, checked: bool) -> None:
        item = QListWidgetItem()
        # 用 checkbox 表示显隐
        cb = QCheckBox(_NAME_MAP.get(key, key))
        cb.setChecked(checked)
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setSizeHint(cb.sizeHint())
        self._list.addItem(item)
        self._list.setItemWidget(item, cb)

    def _save(self) -> None:
        order = []
        hidden = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            key = item.data(Qt.ItemDataRole.UserRole)
            widget = self._list.itemWidget(item)
            if key:
                order.append(key)
                if widget is not None and not widget.isChecked():
                    hidden.append(key)
        self.plugin.settings["order"] = order
        self.plugin.settings["hidden"] = hidden
        self.plugin.save_settings()
        self.accept()
