"""插件窗口：统一的多插件悬浮窗（主窗口与独立窗口同构）。

- 插件实例由 WindowManager 分配，本窗口只负责 UI 挂载/卸载
- add_plugin = create_widget + start（幂等）；remove_plugin = stop
- 右键菜单：「合并到 → 其他窗口」「置顶」「插件设置…」「关闭」
- 关闭窗口时通知管理器（插件回主窗口、配置清理）
"""
import logging

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication
from PySide6.QtWidgets import QLayout, QMenu, QVBoxLayout, QWidget

import core.theme as theme
from core.kwin import is_kde_session, set_keepabove, unload_keepabove_script
from core.window_manager import MAIN_ID
from ui.sections import SectionsContainer

log = logging.getLogger("usage-widget.plugin_window")

PANEL_STYLE = """
#panel QLabel { color: %(text)s; }
#panel QToolButton { color: %(text)s; border: none; background: transparent; padding: 2px 6px; border-radius: 4px; }
#panel QToolButton:hover { background: %(hover)s; }
"""


class PluginWindow(QWidget):
    """多插件悬浮窗（主窗口或独立窗口）。"""

    # (source_id, target_id)：合并窗口请求
    merge_requested = Signal(str, str)
    # (window_id)：窗口被用户关闭
    closed = Signal(str)

    def __init__(self, window_id: str, config, title: str, parent=None):
        super().__init__(parent)
        self.window_id = window_id
        self.config = config
        self._plugins: dict[str, object] = {}
        self._drag_pos: QPoint | None = None
        self._targets: list[tuple[str, str]] = []
        self._manager = None  # WindowManager
        self._user_closing = False

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
        self._container.setStyleSheet(self._panel_style())

        fill = QVBoxLayout(self)
        fill.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        fill.setContentsMargins(0, 0, 0, 0)
        fill.addWidget(self._container)

        self.apply_theme(
            self.config.get("window", "theme", default=theme.DARK),
            self.config.get("window", "opacity", default=0.92),
        )
        self._build_menu()

    # ---- 管理器 ----

    def bind_manager(self, manager) -> None:
        self._manager = manager

    # ---- 插件挂载 ----

    @property
    def plugin_ids(self) -> list[str]:
        return list(self._plugins)

    def get_plugin(self, pid: str):
        return self._plugins.get(pid)

    def add_plugin(self, plugin) -> None:
        """挂载插件：create_widget + start（幂等）。"""
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
        # 「显示分区」菜单隐藏的分区：挂载但不启动 tick（不跑后台监控）
        hidden = set(self.config.get("window", "hidden_sections", default=[]) or [])
        if pid in hidden:
            self.hide_section(pid)
        else:
            # 迁移复用实例：确保 timer 在跑
            try:
                if not plugin._timer.isActive():
                    plugin.start()
            except AttributeError:
                plugin.start()
        QTimer.singleShot(0, self._fit_to_content)

    def remove_plugin(self, pid: str):
        """卸载插件：stop + 清引用，返回实例供迁移。"""
        plugin = self._plugins.pop(pid, None)
        if plugin is None:
            return None
        for section in list(self._container._sections):
            if section.key == pid:
                self._container._lay.removeWidget(section)
                section.hide()
                section.setParent(None)
                section.deleteLater()
                self._container._sections.remove(section)
                break
        try:
            plugin.stop(grace_ms=300)
        except Exception:
            log.exception("停止插件 %s 失败", pid)
        QTimer.singleShot(0, self._fit_to_content)
        return plugin

    def hide_section(self, pid: str) -> None:
        """隐藏分区并停止插件 tick（「显示分区」菜单的「关」）。"""
        for section in self._container._sections:
            if section.key == pid:
                section.setVisible(False)
                break
        plugin = self._plugins.get(pid)
        if plugin is not None:
            try:
                plugin._timer.stop()
            except AttributeError:
                pass
        QTimer.singleShot(0, self._fit_to_content)

    def show_section(self, pid: str) -> None:
        """显示分区并恢复插件运行（「显示分区」菜单的「开」）。

        恢复走完整 start()（on_start + 定时器 + 首次 tick），否则
        opencode/commandcode 这类在 on_start 里启动倒计时/加载设置的
        插件重新显示后不会恢复工作。
        """
        for section in self._container._sections:
            if section.key == pid:
                section.setVisible(True)
                break
        plugin = self._plugins.get(pid)
        if plugin is not None:
            try:
                if plugin._timer.isActive():
                    plugin.tick()
                else:
                    plugin.start()
            except AttributeError:
                pass
            except Exception:
                log.exception("恢复插件 %s 失败", pid)
        QTimer.singleShot(0, self._fit_to_content)

    def rebuild(self) -> None:
        """按当前 _plugins 顺序重建分区（实例不变，仅重建 UI 顺序）。"""
        hidden = set(self.config.get("window", "hidden_sections", default=[]) or [])
        plugins = list(self._plugins.values())
        # 先卸载 UI（不 stop 实例，稍后重新挂载）
        for pid in list(self._plugins):
            for section in list(self._container._sections):
                if section.key == pid:
                    self._container._lay.removeWidget(section)
                    section.hide()
                    section.setParent(None)
                    section.deleteLater()
                    self._container._sections.remove(section)
                    break
            self._plugins.pop(pid, None)
        for p in plugins:
            self.add_plugin(p)
            if p.id in hidden:
                self.hide_section(p.id)
        QTimer.singleShot(0, self._fit_to_content)

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
        self._container.setStyleSheet(self._panel_style())

    def _panel_style(self) -> str:
        if theme.is_dark():
            return PANEL_STYLE % {"text": "#dfe3ea", "hover": "rgba(255,255,255,18)"}
        return PANEL_STYLE % {"text": "#23272f", "hover": "rgba(0,0,0,12)"}

    # ---- 尺寸 ----

    def _fit_to_content(self) -> None:
        self._container.layout().activate()
        hint = self._container.sizeHint()
        wcfg = self.config.get("window", default={}) or {}
        base_w = int(wcfg.get("width", 300))
        self.setMinimumSize(0, 0)
        self.resize(max(base_w, hint.width()), max(60, hint.height()))

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

        split_action = QAction("拆分（每个插件独立窗口）", self)
        split_action.triggered.connect(self._request_split)
        self._menu.addAction(split_action)

        topmost_action = QAction("置顶", self)
        topmost_action.setCheckable(True)
        topmost_action.setChecked(True)
        topmost_action.toggled.connect(self._toggle_topmost)
        self._menu.addAction(topmost_action)

        # 子窗口：简化设置入口（打开第一个插件的对话框）。
        # 主窗口不加——它有自己的「插件设置」子菜单（逐插件入口，
        # 见 FloatingWindow._build_main_menu），重复且易误导
        if self.window_id != MAIN_ID:
            settings_action = QAction("插件设置…", self)
            settings_action.triggered.connect(self._open_plugin_settings)
            self._menu.addAction(settings_action)

        # 子窗口补全局入口（系统设置/重载/配置目录/退出），委托主窗口；
        # 主窗口（FloatingWindow）有自己的 _build_main_menu，不重复
        if self.window_id != MAIN_ID:
            self._menu.addSeparator()
            sys_action = QAction("系统设置…", self)
            sys_action.triggered.connect(self._open_main_dialog)
            self._menu.addAction(sys_action)
            reload_action = QAction("重新加载插件", self)
            reload_action.triggered.connect(self._reload_plugins_via_main)
            self._menu.addAction(reload_action)
            cfg_action = QAction("打开配置目录", self)
            cfg_action.triggered.connect(self._open_config_dir_via_main)
            self._menu.addAction(cfg_action)
            quit_action = QAction("退出", self)
            quit_action.triggered.connect(self._quit_via_main)
            self._menu.addAction(quit_action)

        close_action = QAction("关闭", self)
        close_action.triggered.connect(self._user_close)
        self._menu.addAction(close_action)

    # ---- 委托主窗口的全局操作（子窗口菜单入口） ----

    def _main_window(self):
        if self._manager is None:
            return None
        return self._manager.windows.get(MAIN_ID)

    def _open_main_dialog(self) -> None:
        main = self._main_window()
        if main is not None:
            main._open_system_settings()

    def _reload_plugins_via_main(self) -> None:
        main = self._main_window()
        if main is not None:
            main._reload_plugins()

    def _open_config_dir_via_main(self) -> None:
        main = self._main_window()
        if main is not None:
            main._open_config_dir()

    def _quit_via_main(self) -> None:
        main = self._main_window()
        if main is not None:
            main.close()

    def _request_split(self) -> None:
        if self._manager is not None:
            self._manager.split_window(self.window_id)

    def _user_close(self) -> None:
        self._user_closing = True
        self.close()

    def _rebuild_merge_menu(self) -> None:
        self._merge_menu.clear()
        # 只列非空窗口；主窗口例外——空主窗口也可作为合并目标
        # （合并回去会显示主窗口，恢复系统设置等入口）
        visible = []
        if self._manager is not None:
            for wid, name in self._targets:
                win = self._manager.windows.get(wid)
                if win is None:
                    continue
                if wid == MAIN_ID or win.plugin_ids:
                    visible.append((wid, name))
        else:
            visible = self._targets
        for wid, name in visible:
            action = QAction(name, self._merge_menu)
            action.triggered.connect(
                lambda _=False, t=wid: self._request_merge(t))
            self._merge_menu.addAction(action)
        self._merge_menu.setEnabled(not self._merge_menu.isEmpty())

    def set_merge_targets(self, targets: list[tuple[str, str]]) -> None:
        self._targets = targets
        self._rebuild_merge_menu()

    def contextMenuEvent(self, event):
        self._menu.exec(event.globalPos())

    def apply_topmost(self, on: bool) -> None:
        """应用置顶状态（切 flag 不跳位 + KDE KWin 脚本），不触发持久化。"""
        pos = self.pos()
        flags = self.windowFlags()
        if on:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()
        if "wayland" not in QGuiApplication.platformName():
            self.move(pos)
        if is_kde_session():
            set_keepabove(on, f"usage-widget-{self.window_id}-keepabove")

    def _toggle_topmost(self, checked: bool) -> None:
        self.apply_topmost(checked)
        if self._manager is not None:
            self._manager.persist()

    def _open_plugin_settings(self) -> None:
        # 对每个插件依次打开设置（简化为第一个）
        if self._plugins:
            first = next(iter(self._plugins.values()))
            dialog = first.settings_dialog(self)
            if dialog is not None:
                dialog.exec()

    # ---- 合并 ----

    def _request_merge(self, target_id: str) -> None:
        if self._manager is not None:
            self._manager.merge_window(self.window_id, target_id)

    def closeEvent(self, event):
        if is_kde_session():
            unload_keepabove_script(f"usage-widget-{self.window_id}-keepabove")
        # 只有用户主动关闭（菜单「关闭」/点 X）才通知管理器回收插件；
        # 程序内部 deleteLater 触发的 close 不应触发（防止重启恢复时
        # 窗口被误判为「用户关闭」而把插件挪回主窗口）。
        if (self._manager is not None and self.window_id != MAIN_ID
                and (self._user_closing or event.spontaneous())):
            self.closed.emit(self.window_id)
        for pid in list(self._plugins):
            try:
                self._plugins[pid].stop(grace_ms=1000)
            except Exception:
                pass
        super().closeEvent(event)
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None and app.topLevelWidgets():
            alive = [w for w in app.topLevelWidgets()
                     if w.isVisible() and isinstance(w, (QWidget,))]
            if not alive:
                app.quit()
