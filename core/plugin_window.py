"""插件独立悬浮窗：把单个插件分区拆成单独窗口显示。

与主窗口同款视觉：无边框、半透明圆角背景、置顶、可拖动、
右键菜单（合并回主窗口 / 置顶 / 关闭）。合并后窗口销毁，
分区回到主窗口容器。
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
    """单个插件分区的独立悬浮窗。"""

    merge_requested = Signal(str)  # 请求合并回主窗口，参数为插件 id

    def __init__(self, config, plugin, title: str, parent=None):
        super().__init__(parent)
        self.config = config
        self.plugin = plugin
        self._drag_pos: QPoint | None = None

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

        # 应用主题
        saved_theme = self.config.get("window", "theme", default=theme.DARK)
        saved_alpha = self.config.get("window", "opacity", default=0.92)
        self.apply_theme(saved_theme, saved_alpha)

        # 装载插件分区（用独立容器，标题用插件名）
        widget = plugin.create_widget(self._container)
        self._container.add_section(plugin.id, title, widget)

        self._build_menu()
        self._fit_to_content()

        # KDE 下窗口映射后补 KWin 置顶脚本（与主窗口同样处理）
        if is_kde_session():
            QTimer.singleShot(800, lambda: kwin_set_always_on_top(True, _KWIN_SCRIPT_NAME))

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

        merge_action = QAction("合并回主窗口", self)
        merge_action.triggered.connect(self._request_merge)
        self._menu.addAction(merge_action)

        topmost_action = QAction("置顶", self)
        topmost_action.setCheckable(True)
        topmost_action.setChecked(True)
        topmost_action.toggled.connect(self._toggle_topmost)
        self._menu.addAction(topmost_action)

        close_action = QAction("关闭", self)
        close_action.triggered.connect(self.close)
        self._menu.addAction(close_action)

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
        # KDE 下走 KWin 脚本（独立脚本名，与主窗口互不干扰）
        if is_kde_session():
            kwin_set_always_on_top(checked, _KWIN_SCRIPT_NAME)

    def closeEvent(self, event):
        # 卸载本窗口的 KWin 置顶脚本
        if is_kde_session():
            _kwin_unload_script(_KWIN_SCRIPT_NAME)
        # 停止独立插件实例（grace 收尾，避免带活线程退出）
        try:
            self.plugin.stop(grace_ms=1500)
        except Exception:
            log.exception("停止分离插件 %s 失败", self.plugin.id)
        super().closeEvent(event)
        # 全部窗口（主窗口 + 分离窗口）都关闭后退出应用
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None and app.topLevelWidgets():
            alive = [w for w in app.topLevelWidgets()
                     if w.isVisible() and isinstance(w, (QWidget,))]
            if not alive:
                app.quit()

    # ---- 合并 ----

    def _request_merge(self) -> None:
        # 通知主窗口合并（主窗口负责把分区加回去并销毁本窗口）
        self.merge_requested.emit(self.plugin.id)
