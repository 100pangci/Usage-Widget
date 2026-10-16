"""Codex 用量插件测试：认证文本、/status 响应和基础插件行为。"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugins.codex_usage.api import (
    CodexAuth,
    CodexClient,
    CodexError,
    CodexUsage,
    UsageWindow,
    parse_auth_text,
    parse_usage_response,
    serialize_auth,
)
from plugins.codex_usage.format import format_plan_name, format_reset_time
from plugins.codex_usage.plugin import CodexUsagePlugin
from plugins.codex_usage import proxy as proxy_module


def test_parse_cookie_header():
    auth = parse_auth_text(
        "__Secure-next-auth.session-token=abc.def; cf_clearance=xyz")
    assert auth.cookie_header == (
        "__Secure-next-auth.session-token=abc.def; cf_clearance=xyz")
    assert auth.bearer_token == ""
    assert auth.configured


def test_parse_netscape_cookie():
    auth = parse_auth_text(
        "# Netscape HTTP Cookie File\n"
        ".chatgpt.com\tTRUE\t/\tTRUE\t0\tfoo\tbar\n")
    assert auth.cookie_header == "foo=bar"


def test_parse_auth_json_and_account_id():
    auth = parse_auth_text(
        '{"tokens": {"access_token": "header.payload.signature"},'
        ' "account_id": "acct-123"}'
    )
    assert auth.bearer_token == "header.payload.signature"
    assert auth.account_id == "acct-123"
    assert auth.cookie_header == ""


def test_parse_chatgpt_session_json():
    auth = parse_auth_text(
        '{"user": {"email": "user@example.com"},'
        ' "accessToken": "access-token-123",'
        ' "accountId": "acct-456",'
        ' "sessionToken": "session-token-789"}'
    )
    assert auth.bearer_token == "access-token-123"
    assert auth.account_id == "acct-456"
    # accessToken 优先；不要把可能不同步的 sessionToken 与 Bearer 混发。
    assert auth.cookie_header == ""


def test_parse_standalone_access_token():
    auth = parse_auth_text("a-standalone-access-token")
    assert auth == CodexAuth("", "a-standalone-access-token", "")


def test_parse_standalone_token_with_bearer_prefix():
    auth = parse_auth_text("Bearer a-standalone-access-token")
    assert auth == CodexAuth("", "a-standalone-access-token", "")


def test_truncated_session_json_is_not_sent_as_token():
    """session JSON 超过 32767 字符被截断后解析失败，整段文本（含中文等
    非 ASCII 字符）不能当成 Bearer token 发送。

    回归：设置框曾用 QLineEdit（默认上限 32767 字符）静默截断粘贴内容，
    截断后的 JSON 解析失败又落入「直接粘贴 token」兜底，导致请求头
    编码报 latin-1 错误。
    """
    payload = (
        '{"user":{"name":"测试用户","email":"u@example.com"},'
        '"mappedEventName":"purple_zinc","data":"' + "a" * 40000 + '"}'
    )
    truncated = payload[:32767]
    assert not parse_auth_text(truncated).configured


def test_session_json_with_cjk_still_parses():
    payload = json.dumps({
        "user": {"name": "测试用户"},
        "accessToken": "tk-" + "a" * 5000,
        "mappedEventName": "purple_zinc",
    }, ensure_ascii=False)
    auth = parse_auth_text(payload)
    assert auth.bearer_token == "tk-" + "a" * 5000


def test_serialize_auth_keeps_only_needed_fields():
    auth = parse_auth_text(json.dumps({
        "user": {"name": "测试用户"},
        "accessToken": "tk-" + "a" * 100,
        "accountId": "acct-1",
        "mappedEventName": "purple_zinc",
    }, ensure_ascii=False))
    compact = serialize_auth(auth)
    assert "测试用户" not in compact
    assert "mappedEventName" not in compact
    restored = parse_auth_text(compact)
    # 有 Bearer 时 Cookie 本就不会参与请求，不必保留
    assert restored.bearer_token == auth.bearer_token
    assert restored.account_id == auth.account_id


def test_client_reports_unencodable_auth_as_friendly_error():
    class Opener:
        def open(self, request, timeout):
            raise UnicodeEncodeError(
                "latin-1", "value", 2789, 2791, "ordinal not in range(256)")

    client = CodexClient(cookie_text="session=abc", proxy="none")
    client._opener = Opener()
    with pytest.raises(CodexError, match="重新粘贴"):
        client.usage()


def test_detect_proxy_from_environment(monkeypatch):
    for key in proxy_module._ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10808")
    assert proxy_module.detect_proxy() == "http://127.0.0.1:10808"


def test_detect_proxy_from_legacy_config(tmp_path, monkeypatch):
    for key in proxy_module._ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    # 隔离本机系统代理（Windows 注册表 / KDE 配置），只测试旧版项目配置分支
    monkeypatch.setattr(proxy_module, "_win_registry_proxy", lambda: None)
    monkeypatch.setattr(proxy_module, "_kde_proxy", lambda: None)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[ui]\nproxy = "http://127.0.0.1:10808"\n', encoding="utf-8")
    monkeypatch.setattr(proxy_module, "_LEGACY_CONFIG", config_path)
    assert proxy_module.detect_proxy() == "http://127.0.0.1:10808"


def test_parse_status_usage_response():
    usage = parse_usage_response({
        "plan_type": "plus",
        "rate_limit": {
            "primary_window": {
                "used_percent": 23,
                "limit_window_seconds": 18000,
                "reset_at": 1789000000,
            },
            "secondary_window": {
                "used_percent": 7,
                "limit_window_seconds": 604800,
                "reset_at": 1789500000000,
            },
            "limit_reached": False,
        },
    })
    assert isinstance(usage, CodexUsage)
    assert usage.plan_type == "plus"
    assert usage.primary_window.used_percent == 23
    assert usage.primary_window.window_seconds == 18000
    assert usage.secondary_window.reset_at == 1789500000


def test_parse_status_camel_case_and_ratio():
    usage = parse_usage_response({
        "planType": "pro",
        "rateLimit": {
            "primaryWindow": {
                "usedRatio": 0.5,
                "resetAt": "2026-09-08T04:00:00Z",
            },
            "secondaryWindow": {"percent": "12%"},
        },
    })
    assert usage.plan_type == "pro"
    assert usage.primary_window.used_percent == 50
    assert usage.primary_window.reset_at is not None
    assert usage.secondary_window.used_percent == 12


def test_formatters_and_plugin():
    assert format_plan_name("plus") == "Plus"
    assert format_plan_name("custom") == "custom"
    assert format_reset_time(3660) == "1小时1分"
    assert format_reset_time(5) == "几秒"
    plugin = CodexUsagePlugin({})
    assert plugin.id == "codex_usage"
    assert plugin.refresh_interval == 60000
    assert plugin.cookie_configured() is False


def test_client_sends_cookie_and_account_header():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"rate_limit": {"primary_window": {"used_percent": 1}}}'

    class Opener:
        def open(self, request, timeout):
            assert request.full_url.endswith("/backend-api/wham/usage")
            assert request.get_header("Cookie") == "session=abc"
            headers = {
                key.lower(): value for key, value in request.header_items()
            }
            assert headers["chatgpt-account-id"] == "acct-123"
            assert headers["cache-control"] == "no-cache, no-store"
            return Response()

    client = CodexClient(
        cookie_text="session=abc",
        account_id="acct-123",
        proxy="none",
    )
    client._opener = Opener()
    usage = client.usage()
    assert usage.primary_window.used_percent == 1


def test_client_prefers_bearer_and_falls_back_after_auth_route_rejection():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"rate_limit": {"primary_window": {"used_percent": 2}}}'

    class Opener:
        def __init__(self):
            self.requests = []

        def open(self, request, timeout):
            self.requests.append(request)
            if len(self.requests) == 1:
                raise urllib.error.HTTPError(
                    request.full_url, 403, "forbidden", {}, None)
            assert request.full_url.endswith("/backend-api/api/codex/usage")
            assert request.get_header("Authorization") == "Bearer access-token"
            assert request.get_header("Cookie") is None
            return Response()

    import urllib.error

    client = CodexClient(
        cookie_text=(
            '{"accessToken":"access-token", "accountId":"acct-123", '
            '"sessionToken":"stale-session"}'
        ),
        proxy="none",
    )
    opener = Opener()
    client._opener = opener
    usage = client.usage()
    assert usage.primary_window.used_percent == 2
    assert len(opener.requests) == 2


def test_widget_matches_usage_style_and_updates_reset_label():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    plugin = CodexUsagePlugin({})
    widget = plugin.create_widget(None)
    plugin._apply_usage(CodexUsage(
        primary_window=UsageWindow(42, 18000, 4102444800),
        secondary_window=UsageWindow(7, 604800, None),
        plan_type="plus",
    ))
    assert plugin._labels["plan"].text() == "Plus"
    assert plugin._labels["primary_pct"].text() == "42%"
    assert plugin._labels["primary_reset"].text().startswith("重置于 ")
    assert plugin._labels["secondary_pct"].text() == "7%"
    widget.deleteLater()
    assert app is not None


def test_settings_dialog_filters_pasted_json():
    """粘贴完整 session JSON 后输入框只保留必要字段；超过 32767 字符的
    accessToken 不会被截断。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from plugins.codex_usage.settings_dialog import SettingsDialog

    app = QApplication.instance() or QApplication([])
    plugin = CodexUsagePlugin({})
    dialog = SettingsDialog(plugin, None)
    token = "tk-" + "a" * 40000
    payload = json.dumps({
        "user": {"name": "测试用户"},
        "accessToken": token,
        "mappedEventName": "purple_zinc",
    }, ensure_ascii=False)
    dialog._fill_from_text(payload)
    compact = dialog._cookie_edit.toPlainText()
    assert "测试用户" not in compact
    assert "mappedEventName" not in compact
    assert parse_auth_text(compact).bearer_token == token
    dialog.deleteLater()
    assert app is not None


def test_settings_dialog_saves_only_necessary_fields(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from plugins.codex_usage.settings_dialog import SettingsDialog

    app = QApplication.instance() or QApplication([])
    plugin = CodexUsagePlugin({"data_dir": tmp_path})
    monkeypatch.setattr(plugin, "refresh_now", lambda: None)
    dialog = SettingsDialog(plugin, None)
    cookie_path = tmp_path / "cookie.txt"
    dialog._cookie_path_edit.setText(str(cookie_path))
    token = "tk-" + "a" * 5000
    dialog._cookie_edit.setPlainText(json.dumps({
        "user": {"name": "测试用户"},
        "accessToken": token,
        "sessionToken": "session-token-xyz",
    }, ensure_ascii=False))
    dialog._save()
    saved = cookie_path.read_text(encoding="utf-8")
    assert "测试用户" not in saved
    assert "sessionToken" not in saved
    assert len(saved) < 6000
    auth = parse_auth_text(saved)
    assert auth.bearer_token == token
    dialog.deleteLater()
    assert app is not None


def test_settings_dialog_rejects_truncated_json():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from plugins.codex_usage.settings_dialog import SettingsDialog

    QApplication.instance() or QApplication([])
    truncated = '{"user":{"name":"测试用户"}, "data":"' + "a" * 40000
    with pytest.raises(CodexError, match="不完整"):
        SettingsDialog._validate_auth(truncated)
