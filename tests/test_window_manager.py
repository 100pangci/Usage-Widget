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
