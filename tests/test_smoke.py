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
        assert cfg.get("plugins", "enabled") == ["clock", "opencode_usage"]
        cfg.set("window", "opacity", value=0.5)
        cfg.save()
        cfg2 = Config(path)
        assert cfg2.get("window", "opacity") == 0.5


def test_discover_clock():
    plugins_dir = Path(__file__).resolve().parent.parent / "plugins"
    assert "clock" in discover_plugin_ids(plugins_dir)
    assert "opencode_usage" in discover_plugin_ids(plugins_dir)


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
    assert "clock" in manager.plugins


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
