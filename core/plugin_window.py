"""插件窗口：多插件悬浮窗（主窗口或独立窗口共用）。

- 每个窗口持有 0..N 个插件实例，按顺序渲染分区
- 右键菜单「合并到」列出其他窗口，把本窗口全部插件迁过去
- 置顶、关闭、插件设置
"""
import logging

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication
from PySide6.QtWidgets import QLayout, QMenu, QVBoxLayout, QWidget

import core.theme as theme
from core.window import _kwin_unload_script, is_kde_session, kwin_set_always_on_top
from ui.sections import SectionsContainer

log = logging.getLogger("usage-widget.plugin_window")

# 独立窗口的 KWin 置顶脚本名（与主窗口区分，避免互相覆盖）
_KWIN_SCRIPT_NAME = "usage-widget-plugin-keepabove"

PANEL_STYLE = """
#panel QLabel { color: #dfe3ea; }
#panel QToolButton { color: #dfe3ea; border: none; background: transparent; padding: 2px 6px; border-radius: 4px; }
#panel QToolButton:hover { background: rgba(255, 255, 255, 18); }
"""


class PluginWindow(QWidget):
    """多插件悬浮窗（主窗口或独立窗口）。"""

    # (plugin_id, target_window_id)：把插件合并到目标窗口
    merge_requested = Signal(str, str)

    def __init__(self, window_id: str, config, title: str, parent=None):
        super().__init__(parent)
        self.window_id = window_id
        self.config = config
        self._plugins: dict[str, object] = {}
        self._drag_pos: QPoint | None = None
        self._targets: list[tuple[str, str]] = []  # (window_id, title) 合并目标
        self._on_merge = None  # 由管理器注入的回调(plugin_id, target_id)

        self.setWindowTitle(f"{title} - usage-widget")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._container = SectionsContainer(self)
        self._container.setObjectName("panel")
        self._container.layout_changed.connect(
            lambda: QTimer.singleShot(0, self._fit_to_content))
        self._container.setStyleSheet(PANEL_STYLE)

        fill = QVBoxLayout(self)
        fill.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        fill.setContentsMargins(0, 0, 0, 0)
        fill.addWidget(self._container)

        saved_theme = self.config.get("window", "theme", default=theme.DARK)
        saved_alpha = self.config.get("window", "opacity", default=0.92)
        self.apply_theme(saved_theme, saved_alpha)

        self._build_menu()

    # ---- 插件管理 ----

    @property
    def plugin_ids(self) -> list[str]:
        return list(self._plugins)

    def get_plugin(self, pid: str):
        return self._plugins.get(pid)

    def add_plugin(self, plugin) -> None:
        """添加插件分区（追加到末尾）；实例未启动则启动。"""
        pid = plugin.id
        if pid in self._plugins:
            return
        try:
            widget = plugin.create_widget(self._container)
        except Exception:
            log.exception("插件 %s create_widget 失败", pid)
            return
        self._plugins[pid] = plugin
        title = plugin.name or pid
        self._container.add_section(pid, title, widget)
        # 迁移复用实例：确保 timer 在跑（start 幂等）
        try:
            if not plugin._timer.isActive():
                plugin.start()
        except AttributeError:
            plugin.start()
        QTimer.singleShot(0, self._fit_to_content)

    def remove_plugin(self, pid: str) -> None:
        """移除插件分区（不停止实例，供迁移到其他窗口复用）。"""
        plugin = self._plugins.pop(pid, None)
        if plugin is None:
            return
        for section in list(self._container._sections):
            if section.key == pid:
                self._container._lay.removeWidget(section)
                section.hide()
                section.setParent(None)
                section.deleteLater()
                self._container._sections.remove(section)
                break
        QTimer.singleShot(0, self._fit_to_content)

    def set_merge_targets(self, targets: list[tuple[str, str]]) -> None:
        """设置「合并到」菜单的目标窗口列表。"""
        self._targets = targets
        self._rebuild_merge_menu()

    def rebuild(self) -> None:
        """按当前 _plugins 顺序重建分区（顺序变更后调用）。"""
        plugins = list(self._plugins.values())
        for pid in list(self._plugins):
            for section in list(self._container._sections):
                if section.key == pid:
                    self._container._lay.removeWidget(section)
                    section.hide()
                    section.setParent(None)
                    section.deleteLater()
                    self._container._sections.remove(section)
                    break
        self._plugins = {}
        for p in plugins:
            self.add_plugin(p)
        QTimer.singleShot(0, self._fit_to_content)

    def set_merge_callback(self, callback) -> None:
        """注入合并回调(plugin_id, target_window_id)。"""
        self._on_merge = callback

    # ---- 主题 ----

    def apply_theme(self, theme_name: str, alpha: float) -> None:
        theme.set_theme(theme_name)
        if theme.is_dark():
            bg = QColor(40, 45, 56, int(alpha * 255))
            border = QColor(255, 255, 255, 26)
        else:
            bg = QColor(242, 244, 248, int(alpha * 255))
            border = QColor(0, 0, 0, 40)
        self._container.set_panel_colors(bg, border)

    # ---- 尺寸 ----

    def _fit_to_content(self) -> None:
        self._container.layout().activate()
        hint = self._container.sizeHint()
        self.setMinimumSize(0, 0)
        self.resize(max(220, hint.width()), max(60, hint.height()))

    # ---- 拖动 ----

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is None or not (
            event.buttons() & Qt.MouseButton.LeftButton
        ):
            return
        delta = event.globalPosition().toPoint() - self._drag_pos
        if delta.manhattanLength() > 4:
            if "wayland" in QGuiApplication.platformName():
                win = self.windowHandle()
                if win is not None and win.startSystemMove():
                    self._drag_pos = None
            else:
                self.move(self.pos() + delta)
                self._drag_pos = event.globalPosition().toPoint()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    # ---- 右键菜单 ----

    def _build_menu(self) -> None:
        self._menu = QMenu(self)
        self._merge_menu = self._menu.addMenu("合并到")
        self._rebuild_merge_menu()

        topmost_action = QAction("置顶", self)
        topmost_action.setCheckable(True)
        topmost_action.setChecked(True)
        topmost_action.toggled.connect(self._toggle_topmost)
        self._menu.addAction(topmost_action)

        close_action = QAction("关闭", self)
        close_action.triggered.connect(self.close)
        self._menu.addAction(close_action)

    def _rebuild_merge_menu(self) -> None:
        self._merge_menu.clear()
        for wid, title in self._targets:
            action = QAction(title, self._merge_menu)
            action.triggered.connect(
                lambda _=False, t=wid: self._request_merge(t))
            self._merge_menu.addAction(action)
        self._merge_menu.setEnabled(not self._merge_menu.isEmpty())

    def contextMenuEvent(self, event):
        self._menu.exec(event.globalPos())

    def _toggle_topmost(self, checked: bool) -> None:
        pos = self.pos()
        flags = self.windowFlags()
        if checked:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()
        if "wayland" not in QGuiApplication.platformName():
            self.move(pos)
        if is_kde_session():
            kwin_set_always_on_top(checked, _KWIN_SCRIPT_NAME)

    def closeEvent(self, event):
        if is_kde_session():
            _kwin_unload_script(_KWIN_SCRIPT_NAME)
        for pid in list(self._plugins):
            try:
                self._plugins[pid].stop(grace_ms=1000)
            except Exception:
                log.exception("停止插件 %s 失败", pid)
        super().closeEvent(event)
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None and app.topLevelWidgets():
            alive = [w for w in app.topLevelWidgets()
                     if w.isVisible() and isinstance(w, (QWidget,))]
            if not alive:
                app.quit()

    # ---- 合并 ----

    def _request_merge(self, target_id: str) -> None:
        """把本窗口全部插件合并到目标窗口。"""
        if self._on_merge is not None:
            self._on_merge(self.window_id, target_id)
