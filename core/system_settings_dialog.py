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
        self._windows = self.window.window_manager.windows
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
        """加载当前选中窗口的插件列表（只列「显示分区」开启的分区）。"""
        self._plugin_list.clear()
        wid = self._current_window_id()
        win = self._windows.get(wid)
        if win is None:
            return
        hidden = set(self.config.get("window", "hidden_sections", default=[]) or [])
        for pid in win.plugin_ids:
            if pid in hidden:
                continue
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
        old_theme = self.config.get("window", "theme", default=theme.DARK)
        new_theme = self._theme_combo.currentData()
        new_alpha = self._opacity_slider.value() / 100.0
        self.config.set("window", "theme", value=new_theme)
        self.config.set("window", "opacity", value=round(new_alpha, 2))

        # 把当前选中窗口的列表写回（UI 里只改了一个窗口）。
        # 列表只含「显示分区」开启的分区；保存时把关闭的分区留在
        # 原槽位，否则会从配置里丢插件、重启后顺序错乱。
        wid = self._current_window_id()
        target = self._windows.get(wid)
        order = []
        for i in range(self._plugin_list.count()):
            item = self._plugin_list.item(i)
            pid = item.data(Qt.ItemDataRole.UserRole)
            if pid:
                order.append(pid)
        new_full = order
        if target is not None:
            hidden = set(self.config.get("window", "hidden_sections", default=[]) or [])
            # 隐藏分区占原槽位不动，开启的分区按对话框里的新顺序
            # 依次填入剩余槽位，构成完整顺序再写配置/应用运行时
            order_iter = iter(order)
            taken = set()
            new_full = []
            for pid in target.plugin_ids:
                if pid in hidden:
                    new_full.append(pid)
                else:
                    nxt = next(order_iter, None)
                    if nxt is None:
                        nxt = pid
                    new_full.append(nxt)
                    taken.add(nxt)
            # 兜底：列表里未能归位的项补在末尾（正常不会发生）
            new_full += [
                pid for pid in order
                if pid not in taken and pid not in new_full]
        windows = dict(self.config.get("windows", default={}) or {})
        wconf = dict(windows.get(wid, {}))
        wconf["plugins"] = new_full
        windows[wid] = wconf
        self.config.set("windows", value=windows)
        self.config.save()

        # 应用主题 + 透明度
        self.window.apply_theme(new_theme, new_alpha)
        # 透明度只影响面板绘制，不需要重建插件 UI。重建会销毁并重新
        # 创建插件控件，系统监控等插件可能因此重新初始化硬件信息，
        # 造成保存透明度后长时间显示「初始化…」。
        theme_changed = old_theme != new_theme
        order_changed = (
            target is not None and target.plugin_ids != new_full)
        if order_changed:
            # 运行时立即按新顺序重排：rebuild 后 _plugins 挂载顺序
            # 与配置一致，后续任何 persist()（拖动/关窗/合并）都不会
            # 再把配置回滚成旧顺序。重排已经按新主题重建了 UI。
            target.reorder_plugins(new_full)
            if theme_changed:
                for win in list(self._windows.values()):
                    if win is not target:
                        win.rebuild()
        elif theme_changed:
            # 插件内的颜色通常在 create_widget() 时生成，主题改变时
            # 需要重建所有窗口的插件 UI；透明度改变则不需要。
            for win in list(self._windows.values()):
                win.rebuild()
        self.accept()
