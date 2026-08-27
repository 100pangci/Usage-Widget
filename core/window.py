"""主悬浮窗：无边框、半透明、置顶、可拖动（Wayland 用系统移动）。"""
import logging
import os
import shutil
import subprocess
import sys
import tempfile

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QGuiApplication
from PySide6.QtWidgets import QApplication, QLayout, QMenu, QVBoxLayout, QWidget

from plugins.base import Plugin
from ui.sections import SectionsContainer

log = logging.getLogger("usage-widget.window")

PANEL_STYLE = """
#panel QLabel { color: #dfe3ea; }
#panel QToolButton { color: #dfe3ea; border: none; background: transparent; padding: 2px 6px; border-radius: 4px; }
#panel QToolButton:hover { background: rgba(255, 255, 255, 18); }
#panel QProgressBar {
    background: rgba(255, 255, 255, 16);
    border: none;
    border-radius: 4px;
}
#panel QProgressBar::chunk { background: #4f8cff; border-radius: 4px; }
"""

_KDE_ENV_MARKERS = ("KDE_FULL_SESSION", "KDE_SESSION_VERSION")

# KWin 脚本：精确匹配本窗口设置 keepAbove（等价标题栏「保持在上」）。
# 注意用精确匹配（===）而非前缀，避免误伤 "usage-widget — Dolphin" 这类
# 同名前缀的其他窗口；QtScript 环境窗口列表 API 是 windowList()，
# KWin 6 的置顶属性名是 keepAbove（alwaysOnTop 是 KWin 5 的旧名）。
# 脚本 run 后保持挂载并监听 windowAdded：Wayland 下窗口映射是异步的，
# setWindowFlags 重建原生窗口也会产生新 KWin 窗口，新窗口出现时自动补设。
_KWIN_KEEP_ABOVE_SCRIPT = """
function applyKeepAbove(w) {
    if (!w) return;
    var c = String(w.caption || "");
    var r = String(w.resourceClass || "");
    if (c === "usage-widget" || r === "usage-widget") {
        w.keepAbove = %s;
    }
}
var windows = workspace.windowList();
for (var i = 0; i < windows.length; ++i) {
    if (windows[i]) applyKeepAbove(windows[i]);
}
workspace.windowAdded.connect(applyKeepAbove);
"""


def is_kde_session() -> bool:
    """是否运行在 KDE Plasma 会话（KWin 可被 DBus 控制）。"""
    return any(os.environ.get(key) for key in _KDE_ENV_MARKERS)


_QDBUS_BIN: str | None = None


def _find_qdbus() -> str | None:
    """找可用的 qdbus 命令（qdbus / qdbus6 / qdbus-qt6）。"""
    global _QDBUS_BIN
    if _QDBUS_BIN is None:
        for name in ("qdbus", "qdbus6", "qdbus-qt6"):
            path = shutil.which(name)
            if path:
                _QDBUS_BIN = path
                break
    return _QDBUS_BIN


def _run_qdbus(args: list[str], timeout: float = 5.0) -> tuple[int, str]:
    binary = _find_qdbus()
    if binary is None:
        log.debug("qdbus 不可用")
        return 1, ""
    env = os.environ.copy()
    ld = env.get("LD_LIBRARY_PATH")
    if ld:
        # PyInstaller bootloader 会把 _internal/ 下的 Qt 库放进
        # LD_LIBRARY_PATH，子进程 qdbus 加载到版本不匹配的库会直接
        # 崩溃（rc=1，stderr 报 version not found），需剔除
        env["LD_LIBRARY_PATH"] = ":".join(
            p for p in ld.split(":") if p and "_internal" not in p)
    try:
        proc = subprocess.run(
            [binary, *args], capture_output=True, text=True,
            timeout=timeout, env=env)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.debug("qdbus 调用失败: %s", e)
        return 1, ""
    return proc.returncode, proc.stdout.strip()


class FloatingWindow(QWidget):
    """悬浮主窗口。

    Wayland 注意：
    - 拖动必须走 startSystemMove()（Wayland 禁止程序化移动窗口）
    - 置顶靠 WindowStaysOnTopHint（Qt 6.5+ 在 KDE Wayland 生效）
    - 窗口位置无法在 Wayland 下读回，位置记忆仅 X11 生效
    """

    def __init__(self, config, plugin_manager):
        super().__init__()
        self.config = config
        self.manager = plugin_manager
        self._drag_pos: QPoint | None = None
        self._section_titles: list[tuple[str, str]] = []

        self.setWindowTitle("usage-widget")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        # 注意：Windows 分层窗口下 QSS 的 rgba 背景不会合成上屏，
        # 面板背景由 SectionsContainer.paintEvent 用 QPainter 绘制（见 ui/sections.py）
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._container = SectionsContainer(self)
        self._container.setObjectName("panel")
        # setVisible 触发的 LayoutRequest 是异步的，延迟一帧再按新内容
        # 收缩窗口，否则 sizeHint 还是折叠前的旧值
        self._container.layout_changed.connect(
            lambda: QTimer.singleShot(0, lambda: self._fit_to_content(shrink=True)))
        alpha = self.config.get("window", "opacity", default=0.92)
        self._container.set_panel_colors(
            QColor(40, 45, 56, int(alpha * 255)),
            QColor(255, 255, 255, 26),
        )
        self._container.setStyleSheet(PANEL_STYLE)

        fill = QVBoxLayout(self)
        fill.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        fill.setContentsMargins(0, 0, 0, 0)
        fill.addWidget(self._container)

        wcfg = self.config.get("window", default={}) or {}
        self.resize(int(wcfg.get("width", 300)), int(wcfg.get("height", 200)))
        self._apply_saved_position()
        self._build_menu()

        if not self.config.get("window", "always_on_top", default=True):
            self._toggle_topmost()
        elif is_kde_session():
            # KDE（尤其 Wayland）下 Qt 置顶 hint 可能被忽略，等窗口
            # 映射后由 KWin 脚本补一次「保持在上」
            QTimer.singleShot(800, lambda: self._kwin_set_always_on_top(True))

    # ---- 插件分区 ----

    def populate_sections(self) -> None:
        """为每个已加载插件创建分区。"""
        self._container.clear()
        self._section_titles: list[tuple[str, str]] = []
        hidden = set(self.config.get("window", "hidden_sections", default=[]) or [])
        for pid, plugin in self.manager.plugins.items():
            try:
                widget = plugin.create_widget(self._container)
            except Exception:
                log.exception("插件 %s 的 create_widget 失败", pid)
                continue
            title = plugin.name or pid
            self._section_titles.append((pid, title))
            self._container.add_section(pid, title, widget)
            if pid in hidden:
                self._container.set_section_visible(pid, False)
        self._rebuild_menu()
        self._fit_to_content()

    def _fit_to_content(self, shrink: bool = False) -> None:
        """无边框窗口无法手动缩放，按分区内容自适应尺寸。

        shrink=True 时（折叠/隐藏分区）高度下限用实际内容高度，
        让窗口真正收缩；否则以配置的基础尺寸为下限（内容增减时
        窗口都能收缩/伸展）。注意：布局激活时会按内容设置窗口最小
        尺寸，收缩前必须先清掉旧的最小尺寸约束，否则 resize 会被钳制。
        """
        self._container.layout().activate()
        hint = self._container.sizeHint()
        wcfg = self.config.get("window", default={}) or {}
        base_w = int(wcfg.get("width", 300))
        base_h = int(wcfg.get("height", 200))
        width = min(max(base_w, hint.width()), max(480, base_w))
        min_h = hint.height() if shrink else max(base_h, hint.height())
        height = min(max(min_h, 0), max(900, base_h))
        self.setMinimumSize(0, 0)
        self.resize(width, height)

    # ---- 交互 ----

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
            if self._is_wayland():
                # Wayland 禁止程序化移动，必须走系统移动
                win = self.windowHandle()
                if win is not None and win.startSystemMove():
                    self._drag_pos = None
            else:
                # Windows/X11：手动移动（startSystemMove 在 Windows 的
                # Tool 窗口上不可靠）
                self.move(self.pos() + delta)
                self._drag_pos = event.globalPosition().toPoint()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        self._menu.exec(event.globalPos())

    # ---- 右键菜单 ----

    def _build_menu(self) -> None:
        self._menu = QMenu(self)
        self._sections_menu = self._menu.addMenu("显示分区")
        self._topmost_action = QAction("置顶", self)
        self._topmost_action.setCheckable(True)
        self._topmost_action.setChecked(self.config.get("window", "always_on_top", default=True))
        self._topmost_action.toggled.connect(self._toggle_topmost)
        self._menu.addAction(self._topmost_action)

        self._settings_menu = self._menu.addMenu("插件设置")

        reload_action = QAction("重新加载插件", self)
        reload_action.triggered.connect(self._reload_plugins)
        self._menu.addAction(reload_action)

        cfg_action = QAction("打开配置目录", self)
        cfg_action.triggered.connect(self._open_config_dir)
        self._menu.addAction(cfg_action)

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.close)
        self._menu.addAction(quit_action)
        self._rebuild_menu()

    def _rebuild_menu(self) -> None:
        self._sections_menu.clear()
        self._section_actions: list[QAction] = []
        hidden = set(self.config.get("window", "hidden_sections", default=[]) or [])
        for pid, title in self._section_titles:
            action = QAction(title, self._sections_menu)
            action.setCheckable(True)
            action.setChecked(pid not in hidden)
            action.toggled.connect(
                lambda checked, p=pid: self._toggle_section(p, checked)
            )
            self._section_actions.append(action)
            self._sections_menu.addAction(action)

        self._settings_menu.clear()
        for pid, plugin in self.manager.plugins.items():
            if pid in hidden:
                # 隐藏的分区不出现在设置菜单
                continue
            if type(plugin).settings_dialog is Plugin.settings_dialog:
                continue
            action = QAction(f"{plugin.name or pid}…", self._settings_menu)
            action.triggered.connect(
                lambda _=False, p=plugin: self._open_plugin_settings(p)
            )
            self._settings_menu.addAction(action)
        self._settings_menu.setEnabled(not self._settings_menu.isEmpty())

    def _toggle_section(self, pid: str, visible: bool) -> None:
        """切换分区显隐并持久化到配置。"""
        self._container.set_section_visible(pid, visible)
        hidden = list(self.config.get("window", "hidden_sections", default=[]) or [])
        if visible and pid in hidden:
            hidden.remove(pid)
        elif not visible and pid not in hidden:
            hidden.append(pid)
        self.config.set("window", "hidden_sections", value=hidden)
        self.config.save()
        self._rebuild_menu()
        QTimer.singleShot(0, lambda: self._fit_to_content(shrink=True))

    def _open_plugin_settings(self, plugin) -> None:
        dialog = plugin.settings_dialog(self)
        if dialog is not None:
            dialog.exec()

    def _reload_plugins(self) -> None:
        log.info("重新加载插件")
        self.manager.reload()
        self.manager.start_all()
        self.populate_sections()

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
            # Windows 上 setWindowFlags 会重建原生窗口导致位置跳变，手动恢复
            self.move(pos)
        self.config.set("window", "always_on_top", value=bool(checked))
        self.config.save()
        self._kwin_set_always_on_top(bool(checked))

    def _kwin_set_always_on_top(self, on: bool) -> bool:
        """KDE Plasma 下通过 KWin 脚本设置置顶（X11/Wayland 均有效）。

        KDE Wayland 会忽略 Qt 的 WindowStaysOnTopHint，但 KWin 窗口的
        keepAbove 属性（即标题栏「保持在上」按钮）可被 DBus 脚本控制：
        loadScript 加载 JS → Script.run 执行。脚本保持挂载监听 windowAdded，
        窗口映射/重建后自动补设 keepAbove；下次切换方向时先卸载旧脚本。
        非 KDE 环境直接返回 False。
        """
        if not is_kde_session():
            return False
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
                f.write(_KWIN_KEEP_ABOVE_SCRIPT % ("true" if on else "false"))
                script_path = f.name
        except OSError as e:
            log.warning("写入 KWin 脚本失败: %s", e)
            return False
        try:
            self._kwin_unload_script()
            code, out = _run_qdbus([
                "org.kde.KWin", "/Scripting",
                "org.kde.kwin.Scripting.loadScript",
                script_path, "usage-widget-keepabove",
            ])
            if code != 0 or not out.isdigit():
                log.warning("KWin 脚本加载失败（%s）：%s", code, out)
                return False
            script_id = out
            code, out = _run_qdbus([
                "org.kde.KWin", f"/Scripting/Script{script_id}",
                "org.kde.kwin.Script.run",
            ])
            if code != 0:
                log.warning("KWin 脚本执行失败（%s）：%s", code, out)
                return False
            log.info("KWin 置顶脚本已挂载（keepAbove=%s）", "true" if on else "false")
            return True
        finally:
            try:
                os.unlink(script_path)
            except OSError:
                pass

    def _kwin_unload_script(self) -> None:
        """卸载 keepAbove 监听脚本（应用退出或切换置顶方向时调用）。"""
        _run_qdbus([
            "org.kde.KWin", "/Scripting",
            "org.kde.kwin.Scripting.unloadScript",
            "usage-widget-keepabove",
        ])

    # ---- 位置记忆（Wayland 无法读写窗口位置） ----

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
        self._kwin_unload_script()
        # 退出时给在跑的后台取数线程共享 2.5s 收尾预算；
        # 平时 reload 路径的 stop_all 保持零阻塞
        self.manager.stop_all(grace_ms=2500)
        super().closeEvent(event)
        QApplication.instance().quit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        super().keyPressEvent(event)
