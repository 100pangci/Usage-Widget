"""Codex CLI /status 用量客户端。

Codex CLI 的 ChatGPT 登录会请求 chatgpt.com 的 Codex 用量接口。接口不是
公开稳定的第三方 API，因此这里保留两个当前 CLI 使用过的路径，并对返回
字段做兼容解析。认证支持浏览器 Cookie、单独的 access token，以及 Codex
CLI 的 auth.json（其中包含 OAuth access_token）。
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .proxy import detect_proxy

log = logging.getLogger("codex_usage.api")

BASE_URL = "https://chatgpt.com/backend-api"
# 当前 Codex CLI 会在这两个路径之间按 base URL 选择。保留两个路径是为了
# 兼容旧部署；默认的 /backend-api 应优先使用 /wham/usage。
USAGE_PATHS = ("/api/codex/usage", "/wham/usage")


class CodexError(Exception):
    """可直接显示给用户的 Cookie、网络或响应解析错误。"""


@dataclass(frozen=True)
class CodexAuth:
    """从 Cookie 文本或 Codex auth.json 提取出的请求认证信息。"""

    cookie_header: str = ""
    bearer_token: str = ""
    account_id: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.cookie_header or self.bearer_token)


@dataclass(frozen=True)
class UsageWindow:
    """一个用量窗口。reset_at 为 Unix 时间戳（秒）。"""

    used_percent: int = 0
    window_seconds: int | None = None
    reset_at: float | None = None


@dataclass(frozen=True)
class CodexUsage:
    """与 Codex CLI /status 对应的用量摘要。"""

    primary_window: UsageWindow | None = None
    secondary_window: UsageWindow | None = None
    plan_type: str = ""
    limit_reached: bool = False
    is_unlimited: bool = False


def _decode_jwt_payload(token: str) -> dict:
    """尽力读取 JWT payload，用于自动发现 account_id；不验证签名。"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        raw = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        value = json.loads(raw.decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError, UnicodeDecodeError, UnicodeError,
            json.JSONDecodeError, binascii.Error):
        return {}


def _account_id_from_value(value) -> str:
    """从嵌套 JSON/JWT claims 中找 ChatGPT account id。"""
    if isinstance(value, dict):
        keys = (
            "account_id",
            "accountId",
            "chatgpt_account_id",
            "chatgptAccountId",
            "chatgpt_account_user_id",
        )
        for key in keys:
            candidate = value.get(key)
            if candidate:
                return str(candidate)
        for nested in value.values():
            found = _account_id_from_value(nested)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _account_id_from_value(nested)
            if found:
                return found
    return ""


def _string_from_value(value, keys: tuple[str, ...]) -> str:
    """递归提取 JSON 中指定名称的字符串字段。"""
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if candidate is not None and str(candidate).strip():
                return str(candidate).strip()
        for nested in value.values():
            found = _string_from_value(nested, keys)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _string_from_value(nested, keys)
            if found:
                return found
    return ""


def _json_cookie_pairs(value) -> list[tuple[str, str]]:
    """兼容 Chrome/Cookie-Editor 导出的 JSON 结构。"""
    pairs: list[tuple[str, str]] = []
    if isinstance(value, list):
        for item in value:
            pairs.extend(_json_cookie_pairs(item))
        return pairs
    if not isinstance(value, dict):
        return pairs

    if "name" in value and "value" in value:
        name = str(value.get("name") or "").strip()
        if name:
            pairs.append((name, str(value.get("value") or "")))
        return pairs

    cookies = value.get("cookies")
    if cookies is not None:
        pairs.extend(_json_cookie_pairs(cookies))

    # 也接受 {"cookie_name": "cookie_value"} 形式，但跳过 auth.json
    # 的 tokens/metadata 等普通字段。
    skip = {
        "tokens", "account_id", "accountId", "cookie", "cookie_header",
        "cookies", "access_token", "accessToken", "id_token", "idToken",
        "refresh_token", "refreshToken", "session_token", "sessionToken",
        "oauth_access_token", "oauthAccessToken", "auth_mode", "authProvider",
        "last_refresh", "expires", "user", "account", "subscription", "name",
        "email", "image", "isAuthenticated", "OPENAI_API_KEY",
    }
    for key, item in value.items():
        if key in skip or not isinstance(item, (str, int, float)):
            continue
        if key and " " not in key and ";" not in key:
            pairs.append((str(key), str(item)))
    return pairs


def _parse_netscape_cookie(text: str) -> list[tuple[str, str]]:
    pairs = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 7:
            name = fields[5].strip()
            if name:
                pairs.append((name, fields[6].strip()))
    return pairs


def _raw_cookie_pairs(text: str) -> list[tuple[str, str]]:
    text = re.sub(r"^\s*Cookie:\s*", "", text.strip(), flags=re.I)
    pairs = []
    for item in re.split(r"[;\r\n]+", text):
        item = item.strip()
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        if name and " " not in name:
            pairs.append((name, value.strip()))
    return pairs


def _token_from_pairs(pairs: list[tuple[str, str]]) -> str:
    names = {
        "access_token", "accessToken", "oauth_access_token", "token",
    }
    for name, value in pairs:
        if name in names and value:
            return value
    return ""


def parse_auth_text(text: str) -> CodexAuth:
    """解析 Cookie header、Netscape/JSON Cookie 导出或 Codex auth.json。

    没有 access token 时按原值发送 Cookie；若文本里有 access_token，优先
    使用 Bearer 认证，避免两套认证同时参与请求。函数不会把认证值写日志
    或做任何外传。
    """
    text = str(text or "").strip()
    if not text:
        return CodexAuth()

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        parsed = None

    if parsed is not None:
        bearer = _string_from_value(parsed, (
            "access_token", "accessToken", "oauth_access_token", "oauthAccessToken",
            "token",
        ))
        account_id = _account_id_from_value(parsed)
        pairs = _json_cookie_pairs(parsed)
        cookie_value = parsed.get("cookie") if isinstance(parsed, dict) else None
        if isinstance(cookie_value, str):
            nested = parse_auth_text(cookie_value)
            pairs.extend(_raw_cookie_pairs(nested.cookie_header))
            bearer = bearer or nested.bearer_token
            account_id = account_id or nested.account_id
        bearer = bearer or _token_from_pairs(pairs)
        session_token = _string_from_value(parsed, ("session_token", "sessionToken"))
        if not pairs and session_token and not bearer:
            # /api/auth/session 可能同时返回 sessionToken；它不是 Bearer，
            # 只有没有 accessToken 时才作为 NextAuth session cookie 发送。
            # 同时发送两套认证可能让后端在不同路由/节点选择不同身份。
            pairs = [("__Secure-next-auth.session-token", session_token)]
        account_id = account_id or _account_id_from_value(_decode_jwt_payload(bearer))
        cookie = "; ".join(f"{name}={value}" for name, value in pairs)
        return CodexAuth(cookie, bearer, account_id)

    pairs = _parse_netscape_cookie(text)
    if not pairs:
        pairs = _raw_cookie_pairs(text)
    bearer = _token_from_pairs(pairs)
    # 允许用户直接把 access token 粘进设置框。
    if not pairs and "=" not in text and "\t" not in text:
        bearer = text
    account_id = _account_id_from_value(_decode_jwt_payload(bearer))
    cookie = "; ".join(f"{name}={value}" for name, value in pairs)
    return CodexAuth(cookie, bearer, account_id)


def read_auth_file(path: str | Path) -> CodexAuth:
    path = Path(path).expanduser()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise CodexError(f"读取 Cookie 文件失败：{e}") from e
    auth = parse_auth_text(text)
    if not auth.configured:
        raise CodexError("Cookie 文件为空或格式无法识别")
    return auth


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percent(value, key: str = "") -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().rstrip("%")
    number = _number(value)
    if number is None:
        return None
    normalized = key.lower().replace("_", "")
    if normalized in {"usedratio", "usedfraction", "ratio", "fraction"}:
        number *= 100
    return max(0, min(100, int(number)))


def _timestamp(value) -> float | None:
    if value is None:
        return None
    number = _number(value)
    if number is not None:
        # 当前 Unix 秒约 1e9，毫秒约 1e12；两种都兼容。
        if number > 100_000_000_000:
            number /= 1000
        return number if number > 0 else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            return None
    return None


def _window(value: object) -> UsageWindow | None:
    if not isinstance(value, dict):
        return None
    percent = None
    for key in (
        "used_percent", "usedPercent", "percent", "percentage",
        "used_percentage", "usedPercentage", "used_ratio", "usedRatio",
        "used_fraction", "usedFraction",
    ):
        if key in value:
            percent = _percent(value.get(key), key)
            break
    if percent is None and "used" in value:
        used = _number(value.get("used"))
        cap = _number(value.get("limit", value.get("cap")))
        if used is not None and cap and cap > 0:
            percent = _percent(used / cap * 100, "percent")

    reset = None
    for key in ("reset_at", "resetAt", "reset_at_ms", "resetAtMs"):
        if key in value:
            reset = _timestamp(value.get(key))
            break
    duration = None
    for key in (
        "window_seconds", "windowSeconds", "limit_window_seconds",
        "limitWindowSeconds",
    ):
        if key in value:
            number = _number(value.get(key))
            if number is not None:
                duration = max(0, int(number))
            break
    if duration is None:
        for key in ("window_minutes", "windowMinutes"):
            if key in value:
                number = _number(value.get(key))
                if number is not None:
                    duration = max(0, int(number * 60))
                break

    if percent is None and reset is None and duration is None:
        return None
    return UsageWindow(percent or 0, duration, reset)


def _find_dict(value: object, names: tuple[str, ...]) -> dict:
    if not isinstance(value, dict):
        return {}
    for name in names:
        child = value.get(name)
        if isinstance(child, dict):
            return child
    return {}


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def parse_usage_response(payload: object) -> CodexUsage:
    """解析 /status 用量响应，兼容 snake_case/camelCase 字段。"""
    if not isinstance(payload, dict):
        raise CodexError("用量接口返回的不是 JSON 对象")

    wrapper = _find_dict(payload, ("usage", "data"))
    rate = _find_dict(payload, ("rate_limit", "rateLimit", "rate_limits", "rateLimits"))
    if not rate:
        rate = _find_dict(wrapper, ("rate_limit", "rateLimit", "rate_limits", "rateLimits"))
    source = rate or wrapper or payload
    primary = _window(_find_dict(source, ("primary_window", "primaryWindow", "primary")))
    secondary = _window(
        _find_dict(source, ("secondary_window", "secondaryWindow", "secondary"))
    )

    # 兼容服务端曾使用的扁平字段。
    if primary is None and any(k in payload for k in ("primary_used_percent", "primary_reset_at")):
        primary = _window({
            "used_percent": payload.get("primary_used_percent"),
            "window_seconds": payload.get("primary_window_seconds"),
            "reset_at": payload.get("primary_reset_at"),
        })
    if secondary is None and any(k in payload for k in ("secondary_used_percent", "secondary_reset_at")):
        secondary = _window({
            "used_percent": payload.get("secondary_used_percent"),
            "window_seconds": payload.get("secondary_window_seconds"),
            "reset_at": payload.get("secondary_reset_at"),
        })

    plan = ""
    for container in (payload, rate, wrapper, _find_dict(payload, ("account", "subscription"))):
        for key in ("plan_type", "planType", "plan", "plan_name", "planName"):
            if isinstance(container, dict) and container.get(key):
                plan = str(container[key])
                break
        if plan:
            break

    limit_reached = _as_bool(
        rate.get("limit_reached", rate.get("limitReached", payload.get("limit_reached", False)))
    )
    unlimited = _as_bool(
        rate.get("is_unlimited", rate.get("isUnlimited", payload.get("is_unlimited", False)))
    )
    return CodexUsage(primary, secondary, plan, limit_reached, unlimited)


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except (ImportError, OSError):
        return ssl.create_default_context()


class CodexClient:
    """访问 Codex CLI 使用的 ChatGPT 用量接口。"""

    def __init__(
        self,
        cookie_path: str | Path | None = None,
        account_id: str = "",
        proxy: str | None = "auto",
        timeout: int = 15,
        base_url: str = BASE_URL,
        cookie_text: str | None = None,
    ):
        self.cookie_path = Path(cookie_path).expanduser() if cookie_path else None
        self.account_id = str(account_id or "").strip()
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        if proxy == "auto":
            proxy = detect_proxy()
        if proxy in (None, "none"):
            handler = urllib.request.ProxyHandler({})
        else:
            handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=_ssl_context()), handler)
        self._cookie_text = cookie_text

    def _auth(self) -> CodexAuth:
        if self._cookie_text is not None:
            auth = parse_auth_text(self._cookie_text)
        elif self.cookie_path is not None:
            auth = read_auth_file(self.cookie_path)
        else:
            auth = CodexAuth()
        if not auth.configured:
            raise CodexError("未配置 Cookie：请在插件设置中填写")
        return CodexAuth(
            auth.cookie_header,
            auth.bearer_token,
            self.account_id or auth.account_id,
        )

    def _usage_paths(self) -> tuple[str, ...]:
        """按 Codex CLI 的 base URL 规则选择用量接口路径。"""
        if "/backend-api" in self.base_url.lower():
            return ("/wham/usage", "/api/codex/usage")
        return ("/api/codex/usage", "/wham/usage")

    def usage(self) -> CodexUsage:
        auth = self._auth()
        headers = {
            "Accept": "application/json",
            "User-Agent": "usage-widget/codex_usage",
            "Origin": "https://chatgpt.com",
            "Referer": "https://chatgpt.com/",
            "Cache-Control": "no-cache, no-store",
            "Pragma": "no-cache",
        }
        if auth.bearer_token:
            # Codex CLI 对 OAuth 使用 Bearer + account ID。若用户粘贴的
            # session JSON 还带 sessionToken，不要再混发 Cookie，避免服务端
            # 在不同请求节点采用不同认证上下文。
            headers["Authorization"] = f"Bearer {auth.bearer_token}"
        elif auth.cookie_header:
            headers["Cookie"] = auth.cookie_header
        if auth.account_id:
            headers["ChatGPT-Account-Id"] = auth.account_id

        last_error: Exception | None = None
        auth_errors: list[tuple[str, int]] = []
        paths = self._usage_paths()
        for path in paths:
            request = urllib.request.Request(
                self.base_url + path, headers=headers, method="GET")
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    raw = response.read()
                try:
                    payload = json.loads(raw.decode("utf-8", "replace"))
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    raise CodexError("用量接口返回的 JSON 无法解析") from e
                return parse_usage_response(payload)
            except urllib.error.HTTPError as e:
                if e.code in (404, 405):
                    last_error = e
                    e.close()
                    continue
                if e.code in (401, 403):
                    # 两种路径可能由不同的部署路由处理；先尝试备用路径，
                    # 最后再按真实状态码给出诊断，避免把路由问题误报成过期。
                    auth_errors.append((path, e.code))
                    last_error = e
                    e.close()
                    continue
                raise CodexError(f"用量接口 HTTP {e.code}") from e
            except CodexError:
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                raise CodexError(f"请求用量失败：{e}") from e

        if auth_errors:
            codes = sorted({code for _, code in auth_errors})
            code_text = "/".join(str(code) for code in codes)
            if 403 in codes:
                message = (
                    f"用量接口拒绝认证（HTTP {code_text}）：请检查 ChatGPT account ID "
                    "及当前账号权限"
                )
            else:
                message = (
                    f"accessToken/Cookie 认证失败（HTTP {code_text}）：请重新复制 "
                    "/api/auth/session 的完整 JSON"
                )
            raise CodexError(message) from last_error
        if last_error is not None:
            path_text = " 与 ".join(paths)
            raise CodexError(f"Codex 用量接口不可用（{path_text} 均失败）") from last_error
        raise CodexError("Codex 用量接口不可用")
