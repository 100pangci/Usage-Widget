"""主悬浮窗：主窗口 = PluginWindow 子类，管理独立窗口。

主窗口持有 WindowManager，负责：
- 右键菜单（分区显隐/系统设置/分离合并/重载）
- 位置记忆、退出清理
- 独立窗口的创建（分离插件）
"""
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QGuiApplication
from PySide6.QtWidgets import QApplication, QDialog, QMenu

import core.theme as theme
from core.kwin import is_kde_session, set_keepabove, unload_keepabove_script
from core.plugin_window import PluginWindow
from core.window_manager import MAIN_ID, WindowManager
from plugins.base import Plugin

log = logging.getLogger("usage-widget.window")

# 兼容别名（旧代码/插件引用）
kwin_set_always_on_top = set_keepabove
_kwin_unload_script = unload_keepabove_script


class FloatingWindow(PluginWindow):
    """主窗口（window_id="main"）：PluginWindow + 系统菜单/位置记忆/退出清理。"""

    def __init__(self, config, plugin_manager, parent=None):
        super().__init__(MAIN_ID, config, "usage-widget", parent)
        self.manager = plugin_manager

        def _factory(window_id, wc):
            win = PluginWindow(window_id, self.config, window_id)
            win.bind_manager(self.window_manager)
            win.closed.connect(self.window_manager.close_window)
            return win

        self.window_manager = WindowManager(
            config, plugin_manager, window_factory=_factory)
        self.window_manager.create_main(self)  # 注册自己为主窗口
        self.window_manager.set_refresh_callback(self._on_window_layout_changed)
        self.bind_manager(self.window_manager)
        self.window_manager.restore()

        self._apply_saved_position()
        self._build_main_menu()
        self._refresh_all_merge_targets()

        if not self.config.get("window", "always_on_top", default=True):
            self._toggle_topmost()
        elif is_kde_session():
            QTimer.singleShot(800, lambda: kwin_set_always_on_top(True))

    def _on_window_layout_changed(self) -> None:
        """窗口/插件分配变化：刷新所有窗口的合并目标 + 主窗口菜单。"""
        self._refresh_all_merge_targets()
        self._refresh_main_menu()

    # ---- 插件分区（主窗口的容器操作） ----

    def populate_sections(self) -> None:
        """重建主窗口分区（重载插件后调用）。"""
        self.window_manager.restore()

    def _rebuild_main_sections(self) -> None:
        # 主窗口当前挂载的插件重建 UI
        for pid in list(self._plugins):
            self.remove_plugin(pid)
        self.window_manager.restore()

    # ---- 右键菜单（主窗口专属） ----

    def _build_main_menu(self) -> None:
        self._sections_menu = self._menu.addMenu("显示分区")
        self._settings_menu = self._menu.addMenu("插件设置")
        self._detach_menu = self._menu.addMenu("窗口")

        sys_settings_action = QAction("系统设置…", self)
        sys_settings_action.triggered.connect(self._open_system_settings)
        self._menu.addAction(sys_settings_action)

        reload_action = QAction("重新加载插件", self)
        reload_action.triggered.connect(self._reload_plugins)
        self._menu.addAction(reload_action)

        cfg_action = QAction("打开配置目录", self)
        cfg_action.triggered.connect(self._open_config_dir)
        self._menu.addAction(cfg_action)

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.close)
        self._menu.addAction(quit_action)

        self._refresh_main_menu()

    def _refresh_main_menu(self) -> None:
        hidden = set(self.config.get("window", "hidden_sections", default=[]) or [])
        # 显示分区
        self._sections_menu.clear()
        for pid in self.plugin_ids:
            title = self._plugins[pid].name or pid
            action = QAction(title, self._sections_menu)
            action.setCheckable(True)
            action.setChecked(pid not in hidden)
            action.toggled.connect(
                lambda checked, p=pid: self._toggle_section(p, checked))
            self._sections_menu.addAction(action)
        # 插件设置
        self._settings_menu.clear()
        for pid, plugin in self._plugins.items():
            if pid in hidden:
                continue
            if type(plugin).settings_dialog is Plugin.settings_dialog:
                continue
            action = QAction(f"{plugin.name or pid}…", self._settings_menu)
            action.triggered.connect(
                lambda _=False, p=plugin: self._open_plugin_settings_dlg(p))
            self._settings_menu.addAction(action)
        self._settings_menu.setEnabled(not self._settings_menu.isEmpty())
        # 窗口菜单（分离）
        self._detach_menu.clear()
        has_child = any(wid != MAIN_ID for wid in self.window_manager.windows)
        # 插件在哪个窗口？
        for pid, plugin in self.manager.plugins.items():
            title = plugin.name or pid
            if pid in self.plugin_ids:
                # 在主窗口：显示「分离」
                action = QAction(f"分离「{title}」", self._detach_menu)
                action.triggered.connect(
                    lambda _=False, p=plugin: self._detach_plugin(p))
            else:
                # 在子窗口：不提供分离（子窗口自己的菜单有「合并到」）
                loc = next((wid for wid, w in self.window_manager.windows.items()
                            if wid != MAIN_ID and pid in w.plugin_ids), None)
                label = f"{title}（在 {loc}）" if loc else f"{title}（未加载）"
                action = QAction(label, self._detach_menu)
                action.setEnabled(False)
            self._detach_menu.addAction(action)
        # 只有一个窗口（主窗口，无子窗口）且无插件可分离时隐藏
        has_main_plugins = any(pid in self.plugin_ids for pid, _ in self.manager.plugins.items())
        self._detach_menu.setEnabled(has_child or has_main_plugins)

    def _toggle_section(self, pid: str, visible: bool) -> None:
        for section in self._container._sections:
            if section.key == pid:
                section.setVisible(visible)
                break
        hidden = list(self.config.get("window", "hidden_sections", default=[]) or [])
        if visible and pid in hidden:
            hidden.remove(pid)
        elif not visible and pid not in hidden:
            hidden.append(pid)
        self.config.set("window", "hidden_sections", value=hidden)
        self.config.save()
        QTimer.singleShot(0, lambda: self._fit_to_content())

    def _open_plugin_settings_dlg(self, plugin) -> None:
        dialog = plugin.settings_dialog(self)
        if dialog is not None and dialog.exec() == QDialog.DialogCode.Accepted:
            self._rebuild_plugin_section(plugin)

    def _rebuild_plugin_section(self, plugin) -> None:
        self.remove_plugin(plugin.id)
        self.add_plugin(plugin)
        self._refresh_main_menu()

    # ---- 分离插件到独立窗口 ----

    def _detach_plugin(self, plugin, restore: bool = False) -> None:
        """把插件分离到新独立窗口。"""
        wm = self.window_manager
        pid = plugin.id
        # 已在某个独立窗口？
        for wid, win in wm.windows.items():
            if wid != MAIN_ID and pid in win.plugin_ids:
                return
        # 主窗口实例移交给独立窗口（实例唯一）
        inst = self._plugins.get(pid)
        if inst is None:
            inst = self.manager.plugins.get(pid)
        if inst is None:
            return
        self.remove_plugin(pid)
        wid = wm.next_window_id()
        wc = (self.config.get("windows", default={}) or {}).get(wid, {})
        win = PluginWindow(wid, self.config, plugin.name or pid)
        win.bind_manager(wm)
        win.closed.connect(wm.close_window)
        win.set_merge_targets(self._all_merge_targets(wid))
        wm.windows[wid] = win
        win.add_plugin(inst)
        pos = wc.get("pos")
        if pos and len(pos) == 2:
            win.move(int(pos[0]), int(pos[1]))
        win.show()
        # 更新其他窗口的合并目标
        self._refresh_all_merge_targets()
        wm.persist()
        self._refresh_main_menu()
        QTimer.singleShot(0, lambda: self._fit_to_content())

    def _all_merge_targets(self, exclude_id: str) -> list[tuple[str, str]]:
        wm = self.window_manager
        out = []
        for wid, win in wm.windows.items():
            if wid == exclude_id:
                continue
            name = "主窗口" if wid == MAIN_ID else (win.plugin_ids[0] if win.plugin_ids else wid)
            out.append((wid, name))
        return out

    def _refresh_all_merge_targets(self) -> None:
        wm = self.window_manager
        for wid, win in wm.windows.items():
            win.set_merge_targets(self._all_merge_targets(wid))

    # ---- 系统设置 ----

    def _open_system_settings(self) -> None:
        from core.system_settings_dialog import SystemSettingsDialog

        dialog = SystemSettingsDialog(self.config, self, self)
        dialog.exec()

    # ---- 重载/配置 ----

    def _reload_plugins(self) -> None:
        log.info("重新加载插件")
        self.manager.reload()
        self.manager.start_all()
        self.window_manager.restore()
        self._refresh_main_menu()

    def _open_config_dir(self) -> None:
        path = self.config.path.parent
        if sys.platform == "win32":
            os.startfile(str(path))
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def _toggle_topmost(self, checked: bool = True) -> None:
        pos = self.pos()
        flags = self.windowFlags()
        if checked:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()
        if not self._is_wayland():
            self.move(pos)
        self.config.set("window", "always_on_top", value=bool(checked))
        self.config.save()
        kwin_set_always_on_top(bool(checked))

    def _apply_saved_position(self) -> None:
        pos = self.config.get("window", "position", default=[])
        if pos and len(pos) == 2 and not self._is_wayland():
            self.move(int(pos[0]), int(pos[1]))

    def _is_wayland(self) -> bool:
        return "wayland" in QGuiApplication.platformName()

    def closeEvent(self, event):
        if not self._is_wayland():
            self.config.set("window", "position", value=[self.x(), self.y()])
            self.config.save()
        _kwin_unload_script()
        # 退出时给后台线程 2.5s 收尾
        self.manager.stop_all(grace_ms=2500)
        # 还有独立窗口：主窗口隐藏，应用继续跑
        if len(self.window_manager.windows) > 1:
            event.ignore()
            self.hide()
            return
        super().closeEvent(event)
        QApplication.instance().quit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        super().keyPressEvent(event)
