"""窗口管理器测试：拆分/分离/启动行为。

回归覆盖（v1.1.0 Unreleased 修复）：
- 子窗口「拆分」曾丢失第一个插件（随源窗口销毁、配置丢失、timer 僵尸）
- always_on_top=false 启动路径曾强制 show 空主窗口
- 主窗口曾有重复的「插件设置」入口
- 拆分/恢复的独立窗口标题曾显示 w2/w3 而非插件名
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PLUGIN_SRC = '''\
from PySide6.QtWidgets import QLabel
from plugins.base import Plugin


class P(Plugin):
    id = "{pid}"
    name = "{name}"
    refresh_interval = 1000

    def create_widget(self, parent):
        self.label = QLabel("--", parent)
        return self.label

    def settings_dialog(self, parent=None):
        return None

    def tick(self):
        # 触碰自建控件：窗口被销毁后这里会抛 RuntimeError（僵尸检测）
        self.label.setText("ok")
'''


@pytest.fixture()
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _make_plugin_dir(root: Path, pid: str, name: str) -> None:
    d = root / pid
    d.mkdir()
    (d / "plugin.py").write_text(
        PLUGIN_SRC.format(pid=pid, name=name), encoding="utf-8")
    (d / "__init__.py").write_text("", encoding="utf-8")


def _make_window(tmp_path: Path):
    """两个假插件 + 主窗口，返回 (app, cfg, mgr, win, wm)。"""
    from core.config import Config
    from core.plugin_manager import PluginManager
    from core.window import FloatingWindow

    _make_plugin_dir(tmp_path, "p1", "插件一")
    _make_plugin_dir(tmp_path, "p2", "插件二")
    cfg = Config(tmp_path / "config.json")
    mgr = PluginManager(cfg, plugins_dir=tmp_path)
    mgr.load_all()
    win = FloatingWindow(cfg, mgr)
    return cfg, mgr, win, win.window_manager


def _pump(app, times: int = 5) -> None:
    for _ in range(times):
        app.processEvents()


def test_split_child_window_keeps_all_plugins(tmp_path, app):
    """子窗口拆分：每个插件都进独立窗口，第一个不能随源窗口销毁。"""
    cfg, mgr, win, wm = _make_window(tmp_path)
    assert win.plugin_ids == ["p1", "p2"]

    # p1 分离到子窗口 w1，再把 p2 也挪进去：w1 有两个插件
    win._detach_plugin(mgr.plugins["p1"])
    w1 = next(wid for wid in wm.windows if wid != "main")
    wm.move_plugin("main", w1, "p2")
    assert wm.windows[w1].plugin_ids == ["p1", "p2"]

    wm.split_window(w1)
    assert w1 not in wm.windows
    # 回归：两个插件都还在（旧实现 p1 随 w1 一起被销毁）
    assert sorted(wm.all_plugin_ids()) == ["p1", "p2"]
    # 配置持久化里 p1 也没丢
    cfg_plugins = [
        pid for wc in (cfg.get("windows") or {}).values()
        for pid in wc.get("plugins", [])]
    assert sorted(cfg_plugins) == ["p1", "p2"]
    # 窗口销毁后 tick 不抛 RuntimeError（无僵尸实例）
    _pump(app)
    for pid in ("p1", "p2"):
        mgr.plugins[pid].tick()


def test_split_main_window_one_plugin_per_window(tmp_path, app):
    """主窗口拆分：每个插件独立窗口，主窗口清空并隐藏，标题用插件名。"""
    cfg, mgr, win, wm = _make_window(tmp_path)

    wm.split_window("main")
    assert not win.isVisible()
    children = [wid for wid in wm.windows if wid != "main"]
    assert len(children) == 2
    for wid in children:
        w = wm.windows[wid]
        assert len(w.plugin_ids) == 1
        pid = w.plugin_ids[0]
        # 标题是插件名，不是 w2/w3
        assert w.windowTitle().startswith(mgr.plugins[pid].name)
        # 子窗口保留基类的「插件设置…」单项入口
        assert "插件设置…" in [a.text() for a in w._menu.actions()]
    assert sorted(wm.all_plugin_ids()) == ["p1", "p2"]
    _pump(app)
    for pid in ("p1", "p2"):
        mgr.plugins[pid].tick()


def test_startup_topmost_off_keeps_main_hidden(tmp_path, app):
    """always_on_top=false 启动：只清 flag，不能把主窗口 show 出来。"""
    from PySide6.QtCore import Qt

    from core.config import Config
    from core.plugin_manager import PluginManager
    from core.window import FloatingWindow

    _make_plugin_dir(tmp_path, "p1", "插件一")
    cfg = Config(tmp_path / "cfg-topmost.json")
    cfg.set("window", "always_on_top", value=False)
    cfg.save()

    mgr = PluginManager(cfg, plugins_dir=tmp_path)
    mgr.load_all()
    win = FloatingWindow(cfg, mgr)
    # 构造完成、尚未显式 show：不应自己冒出来（main.py 决定是否显示）
    assert not win.isVisible()
    assert not (win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    assert win.plugin_ids  # 插件照常挂载


def test_topmost_toggle_restores_position(tmp_path, app):
    """切换 Qt 置顶 flag 重建原生窗口后，位置不能跳到默认位置。"""
    _, _, win, _ = _make_window(tmp_path)
    win.show()
    _pump(app)
    win.move(37, 53)
    _pump(app)
    pos = win.pos()

    win.apply_topmost(False)
    _pump(app)
    assert win.pos() == pos

    win.apply_topmost(True)
    _pump(app)
    assert win.pos() == pos


def test_clamp_to_screens_keeps_intersecting_window():
    """与任一屏幕相交的窗口保持不动（允许把窗口停在屏幕边缘）。"""
    from PySide6.QtCore import QRect

    from core.window_manager import clamp_to_screens

    screens = [QRect(0, 0, 1536, 864), QRect(-3840, 0, 1920, 1200)]
    assert clamp_to_screens(QRect(100, 100, 300, 200), screens) is None
    # 窗口边缘探出左屏左侧：仍可见，不修正
    assert clamp_to_screens(QRect(-200, 100, 300, 200), screens) is None


def test_clamp_to_screens_recovers_window_outside_all_screens():
    """完全在所有屏幕外的窗口拉回最近的屏幕（混合 DPI 布局）。"""
    from PySide6.QtCore import QRect

    from core.window_manager import clamp_to_screens

    screens = [QRect(0, 0, 1536, 864), QRect(-3840, 0, 1920, 1200)]

    # 被甩到右屏外面：拉回最近的左屏
    pos = clamp_to_screens(QRect(5000, 300, 300, 200), screens)
    assert pos is not None
    assert screens[0].contains(QRect(pos.x(), pos.y(), 300, 200))

    # 被甩到 UHD 左侧外面：拉回 UHD
    pos = clamp_to_screens(QRect(-9000, 300, 300, 200), screens)
    assert pos is not None
    assert screens[1].contains(QRect(pos.x(), pos.y(), 300, 200))

    # 落在两块屏幕逻辑坐标之间的空隙：也拉回最近的屏幕
    pos = clamp_to_screens(QRect(-1500, 300, 300, 200), screens)
    assert pos is not None
    assert screens[1].contains(QRect(pos.x(), pos.y(), 300, 200))


def test_ensure_on_screen_recovers_lost_window(tmp_path, app):
    """窗口被拖出所有屏幕后，ensure_on_screen 把它拉回可见区域。"""
    from PySide6.QtGui import QGuiApplication

    _, _, win, _ = _make_window(tmp_path)
    win.show()
    _pump(app)
    win.move(20000, 20000)
    _pump(app)

    win.ensure_on_screen()
    _pump(app)

    screen = QGuiApplication.primaryScreen().availableGeometry()
    assert screen.contains(win.frameGeometry())


def test_ensure_on_screen_keeps_visible_window(tmp_path, app):
    """正常可见的窗口不被 ensure_on_screen 挪动。"""
    from PySide6.QtCore import QPoint

    _, _, win, _ = _make_window(tmp_path)
    win.show()
    _pump(app)
    win.move(37, 53)
    _pump(app)

    win.ensure_on_screen()

    assert win.pos() == QPoint(37, 53)


def test_windows_drag_uses_system_move(tmp_path, app, monkeypatch):
    """Windows 手动 move 会在跨屏时被 Qt 的坐标跳变甩飞，必须用原生拖动。"""
    import types

    from PySide6.QtCore import QPoint

    from core import plugin_window

    _, _, win, _ = _make_window(tmp_path)
    win.show()
    _pump(app)

    calls = []

    class Handle:
        def startSystemMove(self):
            calls.append(True)
            return True

    monkeypatch.setattr(win, "windowHandle", lambda: Handle())
    monkeypatch.setattr(
        plugin_window, "QGuiApplication",
        types.SimpleNamespace(
            platformName=lambda: "windows", screens=lambda: []),
    )

    win._drag_pos = QPoint(0, 0)
    assert win._start_system_move() is True
    assert calls == [True]
    # 原生拖动已接管，后续 move 事件不再手动移动窗口
    win._drag_pos = None


def test_manual_drag_ignores_cross_screen_coordinate_jump(tmp_path, app):
    """兜底手动拖动：全局坐标在屏幕边界跳变时不能把窗口甩出桌面。"""
    from PySide6.QtCore import QPoint

    _, _, win, _ = _make_window(tmp_path)
    win.show()
    _pump(app)
    win.move(37, 53)
    _pump(app)

    win._drag_pos = QPoint(0, 0)
    win._manual_drag(QPoint(3000, 0))  # 屏幕宽度的跳变，不是真实拖动
    assert win.pos() == QPoint(37, 53)
    assert win._drag_pos is None  # 结束本次拖动，窗口留在原地

    # 正常的鼠标位移仍然跟随
    win._drag_pos = QPoint(0, 0)
    win._manual_drag(QPoint(30, 10))
    assert win.pos() == QPoint(67, 63)


def test_x11_drag_stays_manual(tmp_path, app, monkeypatch):
    """X11 全局坐标连续：保持手动拖动，不把拖动交给系统。"""
    import types

    from core import plugin_window

    _, _, win, _ = _make_window(tmp_path)
    win.show()
    _pump(app)

    class Handle:
        def startSystemMove(self):  # pragma: no cover - 不应被调用
            raise AssertionError("X11 不应调用 startSystemMove")

    monkeypatch.setattr(win, "windowHandle", lambda: Handle())
    monkeypatch.setattr(
        plugin_window, "QGuiApplication",
        types.SimpleNamespace(
            platformName=lambda: "xcb", screens=lambda: []),
    )

    assert win._start_system_move() is False
    assert win._system_move_unsupported is True


def test_wayland_ensure_on_screen_does_not_move_window(
        tmp_path, app, monkeypatch):
    """Wayland 坐标由 KWin 管理：ensure_on_screen 不能去 move 窗口。"""
    from PySide6.QtCore import QPoint

    _, _, win, _ = _make_window(tmp_path)
    win.show()
    _pump(app)
    win.move(20000, 20000)
    _pump(app)
    monkeypatch.setattr(win, "_is_wayland", lambda: True)

    win.ensure_on_screen()

    assert win.pos() == QPoint(20000, 20000)


def test_wayland_topmost_toggle_does_not_remap_window(tmp_path, app, monkeypatch):
    """Wayland 用 KWin 切换置顶，不能通过 setWindowFlags 触发居中。"""
    from core import plugin_window

    _, _, win, _ = _make_window(tmp_path)
    win.show()
    _pump(app)
    win.move(37, 53)
    _pump(app)
    flags = win.windowFlags()
    calls = []
    monkeypatch.setattr(win, "_is_wayland", lambda: True)
    monkeypatch.setattr(plugin_window, "is_kde_session", lambda: True)
    monkeypatch.setattr(
        plugin_window,
        "set_keepabove",
        lambda on, window_id, position=None: calls.append(
            (on, window_id, position)),
    )

    win.apply_topmost(False)

    assert win.windowFlags() == flags
    assert calls == [(False, "main", None)]
    assert win.topmost is False


def test_wayland_position_callback_persists_kwin_coordinates(tmp_path, app, monkeypatch):
    """Wayland 位置必须使用 KWin 回传坐标，而不是 Qt 的 pos()。"""
    _, _, win, wm = _make_window(tmp_path)
    monkeypatch.setattr(win, "_is_wayland", lambda: True)

    win._on_kwin_position("main", -123, 77)
    wm.persist()

    assert win.pos() != win._kwin_position
    assert win.config.get("windows", "main", "pos") == [-123, 77]


def test_wayland_child_positions_are_persisted_by_window_id(
        tmp_path, app, monkeypatch):
    """多个独立窗口的 KWin 坐标必须按 window_id 分开保存。"""
    cfg, mgr, win, wm = _make_window(tmp_path)
    win._detach_plugin(mgr.plugins["p1"])
    win._detach_plugin(mgr.plugins["p2"])
    child_ids = [wid for wid in wm.windows if wid != "main"]
    assert child_ids == ["w1", "w2"]

    monkeypatch.setattr(win, "_is_wayland", lambda: True)
    for child in (wm.windows["w1"], wm.windows["w2"]):
        monkeypatch.setattr(child, "_is_wayland", lambda: True)
    win._on_kwin_position("w1", -80, 120)
    win._on_kwin_position("w2", 700, -12)
    wm.persist()

    assert cfg.get("windows", "w1", "pos") == [-80, 120]
    assert cfg.get("windows", "w2", "pos") == [700, -12]


def test_main_menu_no_duplicate_settings_entry(tmp_path, app):
    """主窗口菜单：只有「插件设置」子菜单，没有基类的单项版。"""
    _, mgr, win, wm = _make_window(tmp_path)
    texts = [a.text() for a in win._menu.actions()]
    assert "插件设置" in texts          # 子菜单（逐插件入口）
    assert "插件设置…" not in texts     # 基类单项版（只开第一个插件）


def test_settings_menu_updates_on_section_toggle(tmp_path, app):
    """显示分区显隐后「插件设置」子菜单同步更新。

    回归：重新显示的分区不会出现在「插件设置」里（菜单是隐藏时
    构建的，显隐后没有刷新），opencode 等插件设置入口缺失。
    """
    cfg, mgr, win, wm = _make_window(tmp_path)

    # 隐藏 p2：设置入口消失
    win._toggle_section("p2", False)
    texts = [a.text() for a in win._settings_menu.actions()]
    assert "插件二…" not in texts

    # 重新显示：设置入口必须回来
    win._toggle_section("p2", True)
    texts = [a.text() for a in win._settings_menu.actions()]
    assert "插件二…" in texts


def test_rebuild_plugin_section_preserves_collapse(tmp_path, app):
    """单个分区重建后保留折叠状态（不依赖 QTimer 事件循环）。"""
    cfg, mgr, win, wm = _make_window(tmp_path)
    plugin = mgr.plugins["p1"]

    # 折叠 p1
    win._container.collapse_section("p1")
    assert "p1" in win._container.collapsed_keys()

    win._rebuild_plugin_section(plugin)
    _pump(app)
    assert "p1" in win._container.collapsed_keys()
    assert "p1" in win.plugin_ids


def test_rebuild_plugin_section_preserves_order(tmp_path, app):
    """编辑靠前插件的设置后，单区重建不能把它挪到分区末尾。"""
    _, mgr, win, _ = _make_window(tmp_path)
    plugin = mgr.plugins["p1"]

    assert win.plugin_ids == ["p1", "p2"]
    assert [section.key for section in win._container._sections] == ["p1", "p2"]

    win._rebuild_plugin_section(plugin)

    assert win.plugin_ids == ["p1", "p2"]
    assert [section.key for section in win._container._sections] == ["p1", "p2"]


def test_rebuild_immediately_refreshes_active_plugin(tmp_path, app, monkeypatch):
    """重建后 active timer 的插件立即刷新，避免新控件等待下一周期。"""
    cfg, mgr, win, wm = _make_window(tmp_path)
    plugin = mgr.plugins["p1"]
    calls = []
    monkeypatch.setattr(plugin, "tick", lambda: calls.append(True))

    assert plugin._timer.isActive()
    win.rebuild()

    assert calls == [True]


def test_opacity_change_does_not_rebuild_plugin_sections(tmp_path, app, monkeypatch):
    """只改透明度时保留插件控件，避免触发耗时的插件初始化。"""
    from core.system_settings_dialog import SystemSettingsDialog

    cfg, mgr, win, wm = _make_window(tmp_path)
    section = win._container._sections[0]
    rebuild_calls = []

    monkeypatch.setattr(win, "rebuild", lambda: rebuild_calls.append(True))
    dialog = SystemSettingsDialog(cfg, win, win)
    dialog._opacity_slider.setValue(dialog._opacity_slider.value() - 1)
    dialog._save()

    assert rebuild_calls == []
    assert win._container._sections[0] is section
    assert cfg.get("window", "opacity") == 0.91


def test_opacity_change_updates_detached_windows(tmp_path, app):
    """透明度是全局窗口设置，主窗口和独立窗口都要立即更新面板。"""
    from core.system_settings_dialog import SystemSettingsDialog

    cfg, mgr, win, wm = _make_window(tmp_path)
    win._detach_plugin(mgr.plugins["p1"])
    child_id = next(wid for wid in wm.windows if wid != "main")
    child = wm.windows[child_id]

    dialog = SystemSettingsDialog(cfg, win, win)
    dialog._opacity_slider.setValue(dialog._opacity_slider.value() - 1)
    dialog._save()

    alpha = int(0.91 * 255)
    assert win._container._bg_color.alpha() == alpha
    assert child._container._bg_color.alpha() == alpha


def test_reload_disabled_all_keeps_menus_valid(tmp_path, app):
    """全部插件禁用后重载：窗口清空、菜单不残留旧插件引用。"""
    cfg, mgr, win, wm = _make_window(tmp_path)

    cfg.set("plugins", "enabled", value=[])
    cfg.save()
    win._reload_plugins()
    _pump(app)
    assert mgr.plugins == {}
    assert win.plugin_ids == []
    # 窗口菜单里不再出现任何插件条目
    assert win._sections_menu.isEmpty()
    assert win._settings_menu.isEmpty()


def test_split_is_idempotent(tmp_path, app):
    """同一窗口重复调用拆分只生效一次（回归：拆分后可再拆不报错）。"""
    cfg, mgr, win, wm = _make_window(tmp_path)

    wm.split_window("main")
    children = [wid for wid in wm.windows if wid != "main"]
    assert len(children) == 2

    # 再次对已空的子窗口拆分：不应重复创建窗口
    for wid in children:
        before = len(wm.windows)
        wm.split_window(wid)
        assert len(wm.windows) == before


def test_merge_main_window_guard(tmp_path, app):
    """防御：合并时主窗口不能作为源（防止主窗口被合掉）。"""
    cfg, mgr, win, wm = _make_window(tmp_path)
    child_ids = [wid for wid in wm.windows if wid != "main"]
    assert not child_ids  # 初始无子窗口

    # 直接调 merge_window 把主窗口当源（正常路径不会出现）
    before = wm.all_plugin_ids()
    wm.merge_window("main", "nonexistent")
    assert sorted(wm.all_plugin_ids()) == sorted(before)


def _dialog_list_texts(dialog) -> list[str]:
    return [dialog._plugin_list.item(i).text()
            for i in range(dialog._plugin_list.count())]


def test_settings_dialog_follows_topmost_window(tmp_path, app):
    """置顶窗口的设置对话框也要置顶，否则会被父窗口盖住（Linux/X11）。

    父窗口带 WindowStaysOnTopHint 时位于 Above 层，普通子对话框仍在
    普通层，打开后会显示在主窗口下方；对话框必须跟随父窗口置顶。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog

    _, _, win, _ = _make_window(tmp_path)
    win.show()
    _pump(app)
    assert win.topmost

    class FakeDialog(QDialog):
        def exec(self):
            return QDialog.DialogCode.Accepted

    dialog = FakeDialog(win)
    assert win.exec_dialog(dialog) == QDialog.DialogCode.Accepted
    assert dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint

    # 未置顶的窗口：对话框保持普通层级，不能强行置顶压住其他应用
    win.apply_topmost(False)
    _pump(app)
    dialog = FakeDialog(win)
    win.exec_dialog(dialog)
    assert not (dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)


def test_system_settings_dialog_opens_via_exec_dialog(
        tmp_path, app, monkeypatch):
    """「系统设置…」入口必须走 exec_dialog，让对话框继承置顶状态。"""
    from PySide6.QtWidgets import QDialog

    import core.system_settings_dialog as settings_module

    seen = []

    class FakeSettingsDialog:
        def __init__(self, config, window, parent=None):
            self.flag = None
            seen.append(self)

        def setWindowFlag(self, flag, on=True):
            self.flag = (flag, on)

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(
        settings_module, "SystemSettingsDialog", FakeSettingsDialog)
    cfg, mgr, win, wm = _make_window(tmp_path)

    win._open_system_settings()

    assert len(seen) == 1
    assert seen[0].flag[1] is True


def test_plugin_settings_dialog_opens_via_exec_dialog(
        tmp_path, app, monkeypatch):
    """插件设置入口（主窗口与独立窗口）同样走 exec_dialog。"""
    from PySide6.QtWidgets import QDialog

    cfg, mgr, win, wm = _make_window(tmp_path)
    plugin = mgr.plugins["p1"]
    seen = []

    class FakeDialog(QDialog):
        def exec(self):
            return QDialog.DialogCode.Accepted

    def fake_settings_dialog(parent=None):
        dialog = FakeDialog(parent)
        seen.append(dialog)
        return dialog

    monkeypatch.setattr(plugin, "settings_dialog", fake_settings_dialog)

    win._open_plugin_settings_dlg(plugin)
    win._open_plugin_settings()

    assert len(seen) == 2


def test_settings_save_order_applies_and_not_reverted(tmp_path, app):
    """系统设置排序保存：运行时顺序立即生效，后续 persist 不回滚配置。

    回归：旧实现只写配置不重排运行时，_plugins 顺序不变，任何后续
    persist()（拖动/关窗/合并）都会把刚保存的顺序回滚成旧值。
    """
    from core.system_settings_dialog import SystemSettingsDialog

    cfg, mgr, win, wm = _make_window(tmp_path)
    dialog = SystemSettingsDialog(cfg, win, win)
    assert _dialog_list_texts(dialog) == ["p1", "p2"]

    # p1 下移 → 保存
    dialog._plugin_list.setCurrentRow(0)
    dialog._move_plugin(1)
    dialog._save()

    assert win.plugin_ids == ["p2", "p1"]
    assert [s.key for s in win._container._sections] == ["p2", "p1"]
    assert cfg.get("windows", "main", "plugins") == ["p2", "p1"]
    # 顺序改动后运行时已同步，persist 不应再回滚配置
    wm.persist()
    assert cfg.get("windows", "main", "plugins") == ["p2", "p1"]


def test_settings_list_excludes_closed_sections(tmp_path, app):
    """系统设置顺序列表不列「显示分区」关闭的分区；保存不丢隐藏分区。

    回归：关闭分区仍挂载在窗口里，旧实现全列出来误导用户；过滤后
    保存需把隐藏分区留在原槽位，否则配置丢插件、重新显示时错位。
    """
    from core.system_settings_dialog import SystemSettingsDialog

    cfg, mgr, win, wm = _make_window(tmp_path)
    win._toggle_section("p1", False)

    dialog = SystemSettingsDialog(cfg, win, win)
    assert _dialog_list_texts(dialog) == ["p2"]
    dialog._save()

    # 隐藏分区 p1 仍留在原槽位（配置与运行时一致）
    assert win.plugin_ids == ["p1", "p2"]
    assert cfg.get("windows", "main", "plugins") == ["p1", "p2"]


def test_win_topmost_reports_foreign_window_above(monkeypatch):
    """z-order 上方压着其他进程的非置顶窗口 → 判定为异常层级。"""
    import os

    from core import win_topmost

    rects = {
        10: (0, 0, 300, 200),   # 本窗口
        20: (0, 0, 0, 0),       # 不可见/无面积辅助窗口：跳过
        30: (50, 50, 250, 150),  # 其他进程普通窗口，遮挡本窗口
    }
    chain = {10: 20, 20: 30, 30: 0}

    class Api:
        def rect(self, h):
            return rects.get(h)

        def window_above(self, h):
            return chain.get(h, 0)

        def is_visible(self, h):
            return h != 20

        def is_minimized(self, h):
            return False

        def is_cloaked(self, h):
            return False

        def has_topmost(self, h):
            return False

        def pid(self, h):
            return 999 if h == 30 else os.getpid()

    monkeypatch.setattr(win_topmost, "_api", Api())
    assert win_topmost.occluding_intruder(10) == 30


def test_win_topmost_ignores_topmost_and_own_windows(monkeypatch):
    """上方是置顶窗口或本进程窗口（自己的对话框）都不算异常。"""
    import os

    from core import win_topmost

    rects = {10: (0, 0, 300, 200), 20: (50, 50, 250, 150)}
    chain = {10: 20, 20: 0}

    class Api:
        def __init__(self, topmost, pid):
            self._topmost = topmost
            self._pid = pid

        def rect(self, h):
            return rects.get(h)

        def window_above(self, h):
            return chain.get(h, 0)

        def is_visible(self, h):
            return True

        def is_minimized(self, h):
            return False

        def is_cloaked(self, h):
            return False

        def has_topmost(self, h):
            return self._topmost

        def pid(self, h):
            return self._pid

    monkeypatch.setattr(win_topmost, "_api", Api(topmost=True, pid=999))
    assert win_topmost.occluding_intruder(10) is None

    monkeypatch.setattr(
        win_topmost, "_api", Api(topmost=False, pid=os.getpid()))
    assert win_topmost.occluding_intruder(10) is None


def test_win_topmost_repair_restores_lost_flag(monkeypatch):
    """原生置顶位丢失时重新 SetWindowPos(HWND_TOPMOST)。"""
    from core import win_topmost

    class Widget:
        def winId(self):
            return 10

    calls = []

    class Api:
        has_topmost_value = False

        def has_topmost(self, h):
            return self.has_topmost_value

        def set_topmost(self, h, on):
            calls.append((h, on))
            self.has_topmost_value = on
            return True

    api = Api()
    monkeypatch.setattr(win_topmost, "_api", api)

    assert win_topmost.repair_if_needed(Widget()) == "原生置顶属性丢失"
    assert calls == [(10, True)]
    # 已恢复：再次检查无需修复
    monkeypatch.setattr(win_topmost, "occluding_intruder", lambda hwnd: None)
    assert win_topmost.repair_if_needed(Widget()) is None


def test_topmost_guard_repairs_broken_window(tmp_path, app, monkeypatch):
    """守护定时器检测到置顶被破坏时重新置顶，并在恢复正常后复位告警。"""
    from core import plugin_window

    _, _, win, _ = _make_window(tmp_path)
    win.show()
    _pump(app)

    calls = []
    monkeypatch.setattr(win, "_is_windows_native", lambda: True)
    monkeypatch.setattr(
        plugin_window.win_topmost, "repair_if_needed",
        lambda widget: (calls.append(widget)
                        or "被非置顶窗口 0x1 遮挡"))

    win._topmost_repair_logged = False
    win._guard_topmost()
    assert calls == [win]
    assert win._topmost_repair_logged

    monkeypatch.setattr(
        plugin_window.win_topmost, "repair_if_needed", lambda widget: None)
    win._guard_topmost()
    assert not win._topmost_repair_logged


def test_topmost_guard_skips_off_or_hidden_windows(
        tmp_path, app, monkeypatch):
    """关闭置顶或窗口隐藏时守护不做任何原生操作。"""
    from core import plugin_window

    _, _, win, _ = _make_window(tmp_path)
    win.show()
    _pump(app)

    calls = []
    monkeypatch.setattr(win, "_is_windows_native", lambda: True)
    monkeypatch.setattr(
        plugin_window.win_topmost, "repair_if_needed",
        lambda widget: calls.append(widget))

    win.apply_topmost(False)
    _pump(app)
    win._guard_topmost()
    assert calls == []

    win.apply_topmost(True)
    _pump(app)
    win.hide()
    _pump(app)
    win._guard_topmost()
    assert calls == []


def test_windows_topmost_toggle_reasserts_native_flag(
        tmp_path, app, monkeypatch):
    """Windows 下切换置顶后显式按原生层级再设置一次。"""
    from core import plugin_window

    _, _, win, _ = _make_window(tmp_path)
    win.show()
    _pump(app)

    seen = []
    monkeypatch.setattr(win, "_is_windows_native", lambda: True)
    monkeypatch.setattr(
        plugin_window.win_topmost, "set_native_topmost",
        lambda hwnd, on=True: seen.append((hwnd, on)) or True)

    win.apply_topmost(False)
    win.apply_topmost(True)

    assert seen and all(hwnd for hwnd, _ in seen)
    assert any(on is False for _, on in seen)
    assert seen[-1][1] is True
