"""commandcode 插件测试：cookie 解析、格式化、API 数据模型、插件加载。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugins.commandcode.api import (
    Credits,
    Subscription,
    UsageSummary,
    parse_cookie_text,
)
from plugins.commandcode.format import (
    format_credits,
    format_plan_name,
    format_reset_time,
    format_tokens,
    format_usd,
    plan_total_credits,
)
from plugins.commandcode.plugin import CommandCodePlugin


# ---- cookie 解析 ----

FULL_COOKIE = (
    "__Secure-commandcode_prod_.session_token=abc.def%3D; "
    "__Secure-commandcode_prod_.session_data=eyJzZXNzaW9uIn0%3D"
)


def test_parse_full_cookie_header():
    token, data = parse_cookie_text(FULL_COOKIE)
    assert token == "abc.def="
    assert data == "eyJzZXNzaW9uIn0="


def test_parse_token_only():
    token, data = parse_cookie_text("abc.def%3D")
    assert token == "abc.def="
    assert data == ""


def test_parse_garbage():
    assert parse_cookie_text("hello=world") is None
    assert parse_cookie_text("") is None


def test_parse_partial_header():
    token, data = parse_cookie_text(
        "some=1; __Secure-commandcode_prod_.session_token=abc; other=2")
    assert token == "abc"
    assert data == ""


# ---- 格式化 ----

def test_format_usd():
    assert format_usd(0.1299316549) == "$0.13"
    assert format_usd(12.34) == "$12.34"
    assert format_usd(1234.5) == "$1,234.50"
    assert format_usd(20000) == "$20.0k"


def test_format_credits():
    assert format_credits(69.8700683451) == "69.9"
    assert format_credits(100) == "100"
    assert format_credits(0) == "0.0"


def test_format_tokens():
    assert format_tokens(50109) == "5.0万"
    assert format_tokens(3290410) == "329.0万"
    assert format_tokens(999) == "999"


def test_format_reset_time():
    assert format_reset_time(3600) == "1小时0分"
    assert format_reset_time(86400 + 7200) == "1天2小时"
    assert format_reset_time(90) == "1分"
    assert format_reset_time(5) == "几秒"


def test_format_plan_name():
    assert format_plan_name("individual-goat") == "GOAT"
    assert format_plan_name("individual-pro") == "Pro"
    assert format_plan_name("unknown-plan") == "unknown-plan"


def test_plan_total_credits():
    assert plan_total_credits("individual-goat") == 70
    assert plan_total_credits("individual-pro") == 30
    assert plan_total_credits("individual-max") == 150
    assert plan_total_credits("unknown-plan") is None


# ---- API 数据模型（HAR 真实响应结构） ----

def test_usage_summary_from_dict():
    s = UsageSummary.from_dict({
        "totalCount": 98, "totalCost": 0.1299316549, "averageCost": 0.0013,
        "successRate": 100, "completedCount": 98, "failedCount": 0,
        "totalTokensIn": 3240301, "totalTokensOut": 50109, "totalTokens": 3290410,
        "periodBasis": "billing-period",
    })
    assert s.total_count == 98
    assert s.total_cost == 0.1299316549
    assert s.total_tokens == 3290410
    assert s.success_rate == 100


def test_credits_from_dict():
    c = Credits.from_dict({
        "credits": {"monthlyCredits": 69.8700683451, "purchasedCredits": 0},
        "windowLimits": {
            "fiveHour": {"used": 0.0795566144, "cap": 14, "resetAt": 1787808597500},
            "weekly": {"used": 0.1299316549, "cap": 35, "resetAt": 1788368192827},
        },
    })
    assert c.monthly_remaining == 69.8700683451
    assert c.five_hour.used == 0.0795566144
    assert c.five_hour.cap == 14
    assert c.five_hour.percent == 0
    assert c.weekly.percent == 0


def test_subscription_from_dict():
    s = Subscription.from_dict({
        "success": True,
        "data": {
            "status": "active", "planId": "individual-goat",
            "currentPeriodStart": "2026-08-26T16:16:25.000Z",
            "currentPeriodEnd": "2026-09-26T16:16:25.000Z",
        },
    })
    assert s.plan_id == "individual-goat"
    assert s.period_end is not None
    assert s.period_end.month == 9


def test_subscription_empty():
    assert Subscription.from_dict(None) is None


# ---- 插件 ----

def test_plugin_instantiate():
    plugin = CommandCodePlugin({})
    assert plugin.id == "commandcode"
    assert plugin.name == "commandcode 用量"
    assert plugin.refresh_interval == 60000
    assert plugin.cookie_configured() is False


def test_refresh_interval_sanitize():
    assert CommandCodePlugin._sanitize_refresh_ms(1000) == 30000
    assert CommandCodePlugin._sanitize_refresh_ms("60000") == 60000
    assert CommandCodePlugin._sanitize_refresh_ms("abc") == 60000
    assert CommandCodePlugin._sanitize_refresh_ms(None) == 60000