"""系统监控插件设置对话框：指标顺序（拖拽）与显隐（勾选）。"""
import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from .plugin import ALL_ITEMS

log = logging.getLogger("system_monitor.settings")

_NAME_MAP = dict(ALL_ITEMS)


class DragList(QListWidget):
    """修复 Qt6 InternalMove 拖拽重排的 off-by-one bug。

    Qt6 的 InternalMove 在 drop 到某 item 上方时，dropEvent 里
    index.row() 是拖拽前的行号，takeItem 后行号偏移，导致目标项
    被覆盖消失。这里在 dropEvent 里手动计算正确的插入位置。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dragging_row: int | None = None

    def startDrag(self, actions):
        # 记录拖拽起始行，供 dropEvent 修正偏移
        row = self.currentRow()
        self._dragging_row = row if row >= 0 else None
        super().startDrag(actions)
        self._dragging_row = None

    def dropEvent(self, event: QDropEvent):
        # 需要目标行：drop 指示器指向的行
        target = self.indexAt(event.position().toPoint())
        target_row = target.row() if target.isValid() else self.count() - 1
        src_row = self._dragging_row
        if src_row is None:
            # 兜底：没记录到就交给默认实现
            super().dropEvent(event)
            return
        if target_row < 0:
            target_row = self.count() - 1
        # 把源 item 取出来，插入到目标行（跳过自身）
        item = self.takeItem(src_row)
        insert_row = target_row
        if src_row < insert_row:
            insert_row -= 1  # takeItem 后行号前移
        self.insertItem(insert_row, item)
        self.setCurrentRow(insert_row)
        event.acceptProposedAction()
        event.accept()


class SettingsDialog(QDialog):
    """顺序列表（可拖拽）+ 显隐勾选。

    注意：必须用内置 checkbox（ItemIsUserCheckable + setCheckState），
    不能 setItemWidget(QCheckBox)——itemWidget 会拦截拖拽事件，
    导致 InternalMove 完全拖不动。
    """

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setWindowTitle(f"{plugin.name} · 设置")
        self.setMinimumWidth(360)
        self.setMinimumHeight(360)

        hint = QLabel("上下拖动调整顺序；取消勾选 = 隐藏该项。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9aa3b5; font-size: 11px;")

        self._list = DragList()
        self._list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setDefaultDropAction(Qt.DropAction.MoveAction)
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
        item = QListWidgetItem(_NAME_MAP.get(key, key))
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setFlags(
            item.flags()
            | Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsDragEnabled
            | Qt.ItemFlag.ItemIsDropEnabled
        )
        item.setCheckState(
            Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._list.addItem(item)

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
