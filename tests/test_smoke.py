"""冒烟测试：插件发现/加载、容错、配置读写。"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import Config, user_config_dir
from core.plugin_manager import (
    PluginManager,
    discover_plugin_ids,
    import_plugin,
    instantiate_plugin,
)


@pytest.fixture()
def config():
    with tempfile.TemporaryDirectory() as d:
        cfg = Config(os.path.join(d, "config.json"))
        yield cfg


def test_default_config_created():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "config.json")
        cfg = Config(path)
        assert path == str(cfg.path)
        assert cfg.get("window", "opacity") == 0.92
        assert cfg.get("plugins", "enabled") == ["clock", "opencode_usage", "commandcode", "system_monitor", "codex_usage"]
        cfg.set("window", "opacity", value=0.5)
        cfg.save()
        cfg2 = Config(path)
        assert cfg2.get("window", "opacity") == 0.5


def test_discover_clock():
    plugins_dir = Path(__file__).resolve().parent.parent / "plugins"
    assert "clock" in discover_plugin_ids(plugins_dir)
    assert "opencode_usage" in discover_plugin_ids(plugins_dir)
    assert "commandcode" in discover_plugin_ids(plugins_dir)
    assert "system_monitor" in discover_plugin_ids(plugins_dir)
    assert "codex_usage" in discover_plugin_ids(plugins_dir)


def test_import_and_instantiate():
    plugins_dir = Path(__file__).resolve().parent.parent / "plugins"
    module = import_plugin(plugins_dir, "clock")
    plugin = instantiate_plugin(module, "clock", {})
    assert plugin.id == "clock"
    assert plugin.name == "时钟"


def test_manager_load(config):
    manager = PluginManager(config)
    loaded = manager.load_all()
    assert "clock" in loaded
    assert "opencode_usage" in loaded
    assert "commandcode" in loaded
    assert "system_monitor" in loaded
    assert "clock" in manager.plugins


def test_new_plugin_auto_enabled(tmp_path, config):
    """老配置没写的新插件：自动补到 enabled/order 并持久化。"""
    (tmp_path / "clock").mkdir()
    (tmp_path / "clock" / "plugin.py").write_text(
        "from plugins.base import Plugin\nclass P(Plugin):\n    id='clock'\n")
    config.set("plugins", "enabled", value=["clock"])
    config.set("plugins", "order", value=["clock"])
    config.save()

    manager = PluginManager(config, plugins_dir=tmp_path)
    loaded = manager.load_all()
    assert "clock" in loaded

    # 目录里新增一个配置外插件
    (tmp_path / "battery").mkdir()
    (tmp_path / "battery" / "plugin.py").write_text(
        "from plugins.base import Plugin\nclass P(Plugin):\n    id='battery'\n")

    manager2 = PluginManager(config, plugins_dir=tmp_path)
    loaded2 = manager2.load_all()
    assert "battery" in loaded2
    assert "clock" in loaded2
    # 已持久化，且顺序为原列表 + 新增
    assert config.get("plugins", "enabled") == ["clock", "battery"]
    assert config.get("plugins", "order") == ["clock", "battery"]


def test_disabled_plugin_not_reattracted(tmp_path, config):
    """用户从 enabled 里删掉的插件（order 里出现过）不会被自动补全拉回来。"""
    (tmp_path / "clock").mkdir()
    (tmp_path / "clock" / "plugin.py").write_text(
        "from plugins.base import Plugin\nclass P(Plugin):\n    id='clock'\n")
    (tmp_path / "battery").mkdir()
    (tmp_path / "battery" / "plugin.py").write_text(
        "from plugins.base import Plugin\nclass P(Plugin):\n    id='battery'\n")
    # 模拟用户已见过 clock 但主动禁用：order 里有它，enabled 里没有
    config.set("plugins", "enabled", value=["battery"])
    config.set("plugins", "order", value=["clock", "battery"])
    config.save()

    manager = PluginManager(config, plugins_dir=tmp_path)
    loaded = manager.load_all()
    assert loaded == ["battery"]
    assert "clock" not in loaded
    # enabled/order 均未被改写
    assert config.get("plugins", "enabled") == ["battery"]
    assert config.get("plugins", "order") == ["clock", "battery"]


def test_bad_plugin_skipped(tmp_path, config):
    bad = tmp_path / "bad_plugin"
    bad.mkdir()
    (bad / "plugin.py").write_text("raise RuntimeError('broken')")
    manager = PluginManager(config, plugins_dir=tmp_path)
    loaded = manager.load_all()
    assert loaded == []


def test_empty_plugin_dir(config, tmp_path):
    manager = PluginManager(config, plugins_dir=tmp_path)
    assert manager.load_all() == []


def test_config_corrupted_encoding():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "config.json")
        with open(path, "wb") as f:
            f.write(b"\xff\xfe\x00broken")
        cfg = Config(path)
        assert cfg.get("window", "opacity") == 0.92


def test_rsc_parser_malformed_number():
    from plugins.opencode_usage.api import OpencodeError, _JSParser

    with pytest.raises(OpencodeError):
        _JSParser("($R=>$R[0]={a:-x})").parse()


def test_refresh_interval_sanitize():
    from plugins.opencode_usage.plugin import OpencodeUsagePlugin

    assert OpencodeUsagePlugin._sanitize_refresh_ms(1000) == 30000
    assert OpencodeUsagePlugin._sanitize_refresh_ms("60000") == 60000
    assert OpencodeUsagePlugin._sanitize_refresh_ms("abc") == 60000
    assert OpencodeUsagePlugin._sanitize_refresh_ms(None) == 60000


def test_base_widgets_smoke():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from ui.base_widgets import BarGauge, TextRow

    row = TextRow("CPU", "12%", "%")
    row.set_value("33%")
    gauge = BarGauge("mem", 42, "%")
    gauge.set_value(120, "%")
    assert gauge._bar.value() == 100
    assert app is not None


def test_section_collapse_signal():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    app = QApplication.instance() or QApplication([])
    from ui.sections import SectionsContainer

    container = SectionsContainer()
    container.show()
    app.processEvents()
    fired = []
    container.layout_changed.connect(lambda: fired.append(True))
    section = container.add_section("test", "测试", QLabel("x"))
    assert fired == []
    section.toggle_collapse()
    app.processEvents()
    assert fired == [True]
    assert not section._content.isVisible()
    section.toggle_collapse()
    app.processEvents()
    assert len(fired) == 2
    assert section._content.isVisible()


def test_reload_no_clipped_labels():
    """重新加载插件后窗口高度恢复、无 label 被上下裁切（v1.0.4 修复）。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QLabel

    from core.config import Config
    from core.plugin_manager import PluginManager
    from core.window import FloatingWindow

    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as d:
        cfg = Config(os.path.join(d, "config.json"))
        mgr = PluginManager(cfg)
        mgr.load_all()
        win = FloatingWindow(cfg, mgr)
        win.populate_sections()
        mgr.start_all()
        QTimer.singleShot(100, app.quit)
        app.exec()

        def clipped() -> int:
            n = 0
            for c in win.findChildren(QLabel):
                if c.text() and not c.isHidden() and c.height() < c.sizeHint().height():
                    n += 1
            return n

        assert clipped() == 0
        win._reload_plugins()
        QTimer.singleShot(100, app.quit)
        app.exec()
        assert clipped() == 0
        # 窗口高度与 reload 前一致（内容相同则尺寸应不变）
        assert win.height() > 0


def test_theme_switching():
    """主题切换：core.theme 状态 + 插件取色函数跟随主题。"""
    from core.theme import DARK, LIGHT, color, get_theme, is_dark, set_theme

    set_theme(DARK)
    assert is_dark()
    assert get_theme() == DARK
    assert color("dim") == "#9aa3b5"
    assert color("text") == "#dfe3ea"

    set_theme(LIGHT)
    assert not is_dark()
    assert get_theme() == LIGHT
    assert color("dim") == "#4b525c"
    assert color("text") == "#23272f"

    # 非法值回退深色
    set_theme("weird")
    assert is_dark()

    # 插件取色跟随主题（唯一颜色源：core.theme）
    from plugins.system_monitor import plugin as sm

    set_theme(DARK)
    assert sm.DIM() == "#9aa3b5"
    set_theme(LIGHT)
    assert sm.DIM() == "#4b525c"
    set_theme(DARK)


def test_hidden_sections_restored_on_restart():
    """「显示分区」菜单隐藏的分区，重启后保持隐藏且 tick 停止。

    回归：v1.1.0 之前 restore() 无视 hidden_sections，重启后全部插件
    被重新挂载并启动（「只开 clock 下次全开」的 bug）。
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from core.config import Config
    from core.plugin_manager import PluginManager
    from core.window import FloatingWindow

    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as d:
        cfg = Config(os.path.join(d, "config.json"))
        cfg.set("window", "hidden_sections",
                value=["opencode_usage", "commandcode", "system_monitor"])
        cfg.set("plugins", "enabled", value=["clock", "opencode_usage", "commandcode", "system_monitor"])
        cfg.save()

        mgr = PluginManager(cfg)
        mgr.load_all()
        win = FloatingWindow(cfg, mgr)
        win.populate_sections()
        mgr.start_all()
        QTimer.singleShot(50, app.quit)
        app.exec()

        def visible_ids() -> list[str]:
            return [s.key for s in win._container._sections if not s.isHidden()]

        # 隐藏的分区不可见，clock 可见
        assert visible_ids() == ["clock"], visible_ids()
        # 隐藏分区的 timer 已停止（不跑监控）
        for pid in ("opencode_usage", "commandcode", "system_monitor"):
            plugin = win.get_plugin(pid)
            assert plugin is not None
            assert not plugin._timer.isActive(), f"{pid} timer 未停止"
        # clock 的 timer 在跑
        assert win.get_plugin("clock")._timer.isActive()

        win._reload_plugins()
        QTimer.singleShot(50, app.quit)
        app.exec()
        # 重载后隐藏状态依然保持
        assert visible_ids() == ["clock"], visible_ids()
        assert not win.get_plugin("opencode_usage")._timer.isActive()
        assert win.get_plugin("clock")._timer.isActive()
