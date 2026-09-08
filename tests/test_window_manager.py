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
