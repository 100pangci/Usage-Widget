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
