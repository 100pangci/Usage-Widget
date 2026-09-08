"""Codex 用量插件测试：认证文本、/status 响应和基础插件行为。"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugins.codex_usage.api import (
    CodexAuth,
    CodexClient,
    CodexUsage,
    UsageWindow,
    parse_auth_text,
    parse_usage_response,
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


def test_detect_proxy_from_environment(monkeypatch):
    for key in proxy_module._ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10808")
    assert proxy_module.detect_proxy() == "http://127.0.0.1:10808"


def test_detect_proxy_from_legacy_config(tmp_path, monkeypatch):
    for key in proxy_module._ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
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
