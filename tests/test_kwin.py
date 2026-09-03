"""KWin 置顶脚本测试：只精确匹配本窗口的唯一标题标记。

回归覆盖（Unreleased 修复）：脚本曾按标题含 "usage-widget" 的宽泛
子串（或资源类名）匹配，把标题恰好含该字符串的其他应用窗口（文件
管理器/终端等）一起置顶，也让本应用其他窗口被连带置顶。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def kde_env(monkeypatch):
    monkeypatch.setenv("KDE_FULL_SESSION", "1")


def test_token_and_script_name_unique_per_window():
    from core.kwin import caption_token, script_name

    assert caption_token("main") != caption_token("w2")
    assert script_name("w2") == "usage-widget-w2-keepabove"


def test_script_matches_only_own_window(kde_env, monkeypatch):
    """脚本只含本窗口的精确标记，无宽泛 caption/资源类名匹配。"""
    from core import kwin

    captured = {}

    def fake_run(args, timeout=5.0):
        if "org.kde.kwin.Scripting.loadScript" in args:
            captured["script"] = Path(args[3]).read_text(encoding="utf-8")
            return 0, "1"
        return 0, ""

    monkeypatch.setattr(kwin, "_run_qdbus", fake_run)
    assert kwin.set_keepabove(True, "w2") is True
    script = captured["script"]
    # 只匹配自己的标记
    assert 'indexOf("usage-widget/w2")' in script
    assert "w.keepAbove = true" in script
    # 不能按宽泛子串/资源类名匹配（会误伤其他窗口）
    assert 'indexOf("usage-widget")' not in script
    assert "resourceClass" not in script


def test_script_off_uses_false(kde_env, monkeypatch):
    from core import kwin

    captured = {}

    def fake_run(args, timeout=5.0):
        if "org.kde.kwin.Scripting.loadScript" in args:
            captured["script"] = Path(args[3]).read_text(encoding="utf-8")
            return 0, "1"
        return 0, ""

    monkeypatch.setattr(kwin, "_run_qdbus", fake_run)
    assert kwin.set_keepabove(False, "main") is True
    assert "w.keepAbove = false" in captured["script"]


def test_set_keepabove_noop_without_kde(monkeypatch):
    monkeypatch.delenv("KDE_FULL_SESSION", raising=False)
    monkeypatch.delenv("KDE_SESSION_VERSION", raising=False)
    from core import kwin

    assert kwin.set_keepabove(True, "main") is False
