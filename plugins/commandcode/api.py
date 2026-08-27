"""commandcode.ai 用量 API 客户端（零框架依赖，插件内自包含）。

协议（从浏览器抓包还原）：
- 端点: https://api.commandcode.ai（REST GET，JSON 响应）
- 认证: Cookie: __Secure-commandcode_prod_.session_token=<token>;
              __Secure-commandcode_prod_.session_data=<base64url JSON>
- 金额单位: 美元（totalCost / credits 字段即 USD，无需换算）

端点：
- /auth/get-session               会话与用户信息
- /internal/usage/summary         计费周期汇总：请求数/花费/tokens/成功率
- /internal/billing/credits       剩余 credits 与 5 小时/每周窗口限制
- /internal/billing/subscriptions 订阅计划与计费周期
"""
import json
import logging
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

log = logging.getLogger("commandcode.api")

BASE = "https://api.commandcode.ai"

TOKEN_COOKIE = "__Secure-commandcode_prod_.session_token"
DATA_COOKIE = "__Secure-commandcode_prod_.session_data"


class CommandCodeError(Exception):
    """可展示给用户的错误（cookie 失效/网络失败/解析失败）。"""


# ---- 数据记录 ----

@dataclass
class UsageSummary:
    total_count: int
    total_cost: float
    total_tokens: int
    total_tokens_in: int
    total_tokens_out: int
    success_rate: float
    period_basis: str

    @classmethod
    def from_dict(cls, d: dict | None) -> "UsageSummary":
        d = d or {}
        return cls(
            total_count=_as_int(d.get("totalCount")),
            total_cost=_as_float(d.get("totalCost")),
            total_tokens=_as_int(d.get("totalTokens")),
            total_tokens_in=_as_int(d.get("totalTokensIn")),
            total_tokens_out=_as_int(d.get("totalTokensOut")),
            success_rate=_as_float(d.get("successRate")),
            period_basis=str(d.get("periodBasis") or ""),
        )


@dataclass
class WindowLimit:
    """5 小时/每周窗口限制：已用 / 上限（USD）+ 重置时间戳（epoch 毫秒）。"""

    used: float
    cap: float
    reset_at_ms: int

    @classmethod
    def from_dict(cls, d: dict | None) -> "WindowLimit | None":
        d = d or {}
        if not d:
            return None
        return cls(
            used=_as_float(d.get("used")),
            cap=_as_float(d.get("cap")),
            reset_at_ms=_as_int(d.get("resetAt")),
        )

    @property
    def percent(self) -> int:
        if self.cap <= 0:
            return 0
        return max(0, min(100, int(self.used / self.cap * 100)))


@dataclass
class Credits:
    monthly_remaining: float
    five_hour: WindowLimit | None
    weekly: WindowLimit | None

    @classmethod
    def from_dict(cls, d: dict | None) -> "Credits":
        d = d or {}
        credits = d.get("credits") or {}
        limits = d.get("windowLimits") or {}
        return cls(
            monthly_remaining=_as_float(credits.get("monthlyCredits")),
            five_hour=WindowLimit.from_dict(limits.get("fiveHour")),
            weekly=WindowLimit.from_dict(limits.get("weekly")),
        )


@dataclass
class Subscription:
    plan_id: str
    status: str
    period_start: datetime | None
    period_end: datetime | None

    @classmethod
    def from_dict(cls, d: dict | None) -> "Subscription | None":
        d = d or {}
        if "data" in d:  # 接口返回 {success, data:{...}} 包装
            d = d.get("data") or {}
        if not d.get("planId"):
            return None
        return cls(
            plan_id=str(d["planId"]),
            status=str(d.get("status") or ""),
            period_start=_as_dt(d.get("currentPeriodStart")),
            period_end=_as_dt(d.get("currentPeriodEnd")),
        )


# ---- 工具 ----

def _ssl_context() -> ssl.SSLContext:
    """带 CA 证书的默认上下文。

    PyInstaller 打包后系统证书路径不可用（CERTIFICATE_VERIFY_FAILED），
    certifi 的 cacert.pem 会随打包一起收集（pyinstaller-hooks-contrib）。
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_cookie_text(text: str) -> tuple[str, str] | None:
    """从粘贴文本解析 (token, session_data)。

    支持两种形式：
    - 完整 Cookie header：__Secure-commandcode_prod_.session_token=...;
                           __Secure-commandcode_prod_.session_data=...
    - 仅 token 值（不含 = 号的整串）
    返回 None 表示格式无法识别。
    """
    text = (text or "").strip()
    if not text:
        return None
    token = re.search(r"session_token=([^;\s]+)", text)
    data = re.search(r"session_data=([^;\s]+)", text)
    if token is not None or data is not None:
        return (
            unquote(token.group(1)) if token else "",
            unquote(data.group(1)) if data else "",
        )
    if "=" not in text:
        return unquote(text), ""
    return None


def read_cookie_file(path: Path) -> tuple[str, str] | None:
    """从 cookie 文件读取 (token, session_data)。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise CommandCodeError(f"读取 cookie 文件失败: {e}") from e
    parsed = parse_cookie_text(text)
    if parsed is None:
        raise CommandCodeError(
            "cookie 文件格式无法识别：请粘贴浏览器 F12 → 网络 → 请求头里的完整 Cookie")
    return parsed


# ---- 客户端 ----

class CommandCodeClient:
    def __init__(self, cookie_path, proxy=None, timeout=15):
        self.cookie_path = Path(cookie_path)
        self.timeout = timeout
        if proxy:
            handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        else:
            handler = urllib.request.ProxyHandler({})
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=_ssl_context()), handler)

    def cookie_header(self) -> str:
        token, data = read_cookie_file(self.cookie_path)
        parts = []
        if token:
            parts.append(f"{TOKEN_COOKIE}={token}")
        if data:
            parts.append(f"{DATA_COOKIE}={data}")
        if not parts:
            raise CommandCodeError("cookie 为空")
        return "; ".join(parts)

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(BASE + path, method="GET", headers={
            "Cookie": self.cookie_header(),
            "User-Agent": "usage-widget/0.1",
            "Referer": "https://commandcode.ai/",
            "Origin": "https://commandcode.ai",
            "Accept": "application/json",
        })
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise CommandCodeError("cookie 失效或无权限，请更新 cookie") from e
            raise CommandCodeError(f"HTTP {e.code}: {e.reason}") from e
        except OSError as e:
            raise CommandCodeError(f"网络错误: {e}") from e
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise CommandCodeError(f"响应解析失败: {e}") from e

    # -- 接口 --

    def session(self) -> dict:
        """会话信息：user.name 等；cookie 失效时抛 CommandCodeError。"""
        return self._get("/auth/get-session")

    def usage_summary(self) -> UsageSummary:
        return UsageSummary.from_dict(self._get("/internal/usage/summary"))

    def credits(self) -> Credits:
        return Credits.from_dict(self._get("/internal/billing/credits"))

    def subscription(self) -> Subscription | None:
        return Subscription.from_dict(self._get("/internal/billing/subscriptions"))