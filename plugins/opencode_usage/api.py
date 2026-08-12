"""opencode.ai 用量 API 客户端（零框架依赖，插件内自包含）。

协议（从浏览器抓包还原）：
- 端点: POST https://opencode.ai/_server
- 认证: Cookie: auth=<浏览器会话 token>
- 请求体: SolidStart server-fn 序列化 {"t":<参数树>,"f":31,"m":[]}
- 响应: ;0x<hex>;((self.$R=...)...)  RSC 序列化（$R[n]= 引用赋值）
- 计费单位: totalCost/cost 字段 ÷ 1e8 = 美元

函数（X-Server-Id = 服务端函数 hash）：
- getCosts(ws, year, month0, tz)        逐日/逐模型费用
- usage.list(ws, page)                  按请求明细（50/页，含 token）
- getWorkspaces()                       工作区列表
- lite.subscription.get(ws)             滚动/每周/每月用量百分比与重置时间
"""
import json
import logging
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger("opencode.api")

ENDPOINT = "https://opencode.ai/_server"

FN_COST = "15702f3a12ff8bff357f8c2aa154a17e65b746d5f6b96adc9002c86ee0c15205"
FN_USAGE = "bfd684bfc2e4eed05cd0b518f5e4eafd3f3376e3938abb9e536e7c03df831e5c"
FN_WORKSPACES = "def39973159c7f0483d8793a822b8dbb10d067e12c65455fcb4608459ba0234f"
FN_SUBSCRIPTION = "c7389bd0e731f80f49593e5ee53835475f4e28594dd6bd83eb229bab753498cd"

COST_UNIT = 1e8
PAGE_SIZE = 50
MAX_SAMPLE_RECORDS = 2000


class OpencodeError(Exception):
    """可展示给用户的错误（cookie 失效/网络失败/解析失败）。"""


# ---- 数据记录 ----

@dataclass
class CostRecord:
    date: str
    model: str
    cost_units: int
    key_id: str
    plan: str


@dataclass
class UsageRecord:
    created: datetime | None
    model: str
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cache_read_tokens: int
    cache_write_5m_tokens: int | None
    cache_write_1h_tokens: int | None
    cost_units: int
    key_id: str
    plan: str

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.reasoning_tokens
            + self.cache_read_tokens
            + (self.cache_write_5m_tokens or 0)
            + (self.cache_write_1h_tokens or 0)
        )


@dataclass
class Workspace:
    id: str
    name: str
    slug: str | None


@dataclass
class UsageLevel:
    """lite 订阅用量：用量百分比 + 重置倒计时秒数。"""

    status: str
    usage_percent: int
    reset_in_sec: int

    @classmethod
    def from_dict(cls, data: dict | None) -> "UsageLevel":
        data = data or {}
        return cls(
            status=str(data.get("status") or ""),
            usage_percent=_as_int(data.get("usagePercent")),
            reset_in_sec=_as_int(data.get("resetInSec")),
        )


@dataclass
class SubscriptionUsage:
    rolling: UsageLevel
    weekly: UsageLevel
    monthly: UsageLevel


def _as_int(value) -> int:
    return int(value or 0)


def _arg_str(value: str) -> dict:
    return {"t": 1, "s": value}


def _arg_num(value: int) -> dict:
    return {"t": 0, "s": value}


# ---- RSC 序列化解析器 ----

class _JSParser:
    """SolidStart RSC 序列化的 JS 子集解析器。

    支持：字符串/数字/布尔(!0 !1 true false)/null/undefined/数组/对象/
    $R[n] 引用与内联赋值/new Date("...")。
    """

    def __init__(self, text: str):
        self.s = text
        self.i = 0
        self.reg: dict[int, object] = {}

    def parse(self):
        m = re.search(r"\(\$R=>", self.s)
        if not m:
            raise OpencodeError("响应格式无法识别")
        self.i = m.end()
        self._stmts()
        if 0 not in self.reg:
            raise OpencodeError("响应缺少根对象")
        return self.reg[0]

    def _skip(self):
        while self.i < len(self.s) and self.s[self.i] in " \t\r\n":
            self.i += 1

    def _stmts(self):
        while True:
            self._skip()
            if self.i >= len(self.s) or self.s[self.i] == ")":
                return
            if self.s[self.i] == ",":
                self.i += 1
                continue
            self._stmt()

    def _stmt(self):
        self._skip()
        if not self.s.startswith("$R[", self.i):
            raise OpencodeError(f"无法解析语句 @{self.i}")
        n = self._ref_index()
        self._skip()
        if self.i >= len(self.s) or self.s[self.i] != "=":
            raise OpencodeError(f"语句缺赋值 @{self.i}")
        self.i += 1
        self.reg[n] = self._value()

    def _ref_index(self) -> int:
        m = re.match(r"\$R\[(\d+)\]", self.s[self.i:])
        if not m:
            raise OpencodeError("引用格式错误")
        self.i += m.end()
        return int(m.group(1))

    def _value(self):
        self._skip()
        if self.i >= len(self.s):
            raise OpencodeError("值意外结束")
        ch = self.s[self.i]
        if ch == '"':
            return self._string()
        if ch == "{":
            return self._object()
        if ch == "[":
            return self._array()
        if ch == "-" or ch.isdigit():
            return self._number()
        if self.s.startswith("$R[", self.i):
            n = self._ref_index()
            self._skip()
            if self.i < len(self.s) and self.s[self.i] == "=":
                self.i += 1
                self.reg[n] = self._value()
                return self.reg[n]
            return self.reg.get(n)
        if self.s.startswith("new Date(", self.i):
            self.i += len("new Date(")
            inner = self._value()
            self._skip()
            if self.i < len(self.s) and self.s[self.i] == ")":
                self.i += 1
            if isinstance(inner, str):
                try:
                    return datetime.fromisoformat(inner.replace("Z", "+00:00"))
                except ValueError:
                    return inner
            return inner
        if self.s.startswith("new Error(", self.i):
            self.i += len("new Error(")
            inner = self._value()
            self._skip()
            if self.i < len(self.s) and self.s[self.i] == ")":
                self.i += 1
            return f"服务器错误: {inner}" if isinstance(inner, str) else inner
        if self.s.startswith("Object.assign(", self.i):
            self.i += len("Object.assign(")
            merged: dict = {}
            while True:
                self._skip()
                if self.i >= len(self.s):
                    raise OpencodeError("Object.assign 未闭合")
                if self.s[self.i] == ")":
                    self.i += 1
                    break
                if self.s[self.i] == ",":
                    self.i += 1
                    continue
                value = self._value()
                if isinstance(value, dict):
                    merged.update(value)
                else:
                    return value
            return merged
        for kw, v in (("!0", False), ("!1", True), ("true", True),
                      ("false", False), ("null", None), ("undefined", None)):
            if self.s.startswith(kw, self.i):
                self.i += len(kw)
                return v
        raise OpencodeError(f"无法解析值 @{self.i}: {self.s[self.i:self.i + 40]!r}")

    def _string(self) -> str:
        out = []
        self.i += 1
        escapes = {"n": "\n", "t": "\t", "r": "\r", "b": "\b",
                   "f": "\f", '"': '"', "\\": "\\", "/": "/"}
        while self.i < len(self.s):
            ch = self.s[self.i]
            if ch == "\\":
                nxt = self.s[self.i + 1:self.i + 2]
                out.append(escapes.get(nxt, nxt))
                self.i += 2
            elif ch == '"':
                self.i += 1
                return "".join(out)
            else:
                out.append(ch)
                self.i += 1
        raise OpencodeError("字符串未闭合")

    def _number(self):
        m = re.match(r"-?\d+(\.\d+)?", self.s[self.i:])
        self.i += m.end()
        return float(m.group(0)) if "." in m.group(0) else int(m.group(0))

    def _array(self):
        self.i += 1
        out = []
        while True:
            self._skip()
            if self.i >= len(self.s):
                raise OpencodeError("数组未闭合")
            if self.s[self.i] == "]":
                self.i += 1
                return out
            if self.s[self.i] == ",":
                self.i += 1
                continue
            out.append(self._value())

    def _object(self):
        self.i += 1
        out = {}
        while True:
            self._skip()
            if self.i >= len(self.s):
                raise OpencodeError("对象未闭合")
            if self.s[self.i] == "}":
                self.i += 1
                return out
            if self.s[self.i] == ",":
                self.i += 1
                continue
            if self.s[self.i] == '"':
                key = self._string()
            else:
                m = re.match(r"[A-Za-z_$][\w$]*", self.s[self.i:])
                if not m:
                    raise OpencodeError(f"对象键解析失败 @{self.i}")
                key = m.group(0)
                self.i += m.end()
            self._skip()
            if self.i >= len(self.s) or self.s[self.i] != ":":
                raise OpencodeError("对象缺冒号")
            self.i += 1
            out[key] = self._value()


# ---- 客户端 ----

class OpencodeClient:
    def __init__(self, cookie_path, workspace_id="", proxy=None, timeout=15):
        self.cookie_path = Path(cookie_path)
        self.workspace_id = workspace_id
        self.timeout = timeout
        if proxy:
            handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        else:
            handler = urllib.request.ProxyHandler({})
        self._opener = urllib.request.build_opener(handler)

    # -- 认证 --

    def auth_token(self) -> str:
        try:
            text = self.cookie_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as e:
            raise OpencodeError(f"读取 cookie 文件失败: {e}") from e
        if not text:
            raise OpencodeError(f"cookie 文件为空: {self.cookie_path}")
        m = re.search(r"(?:^|[;,\s])auth=([^;,\s]+)", text)
        if m:
            return m.group(1)
        if text.startswith("#"):
            raise OpencodeError("cookie 文件里没有 auth 值")
        return text.split(";")[0].split("=", 1)[-1].strip()

    # -- 传输 --

    def _call(self, fn_hash, args):
        body = json.dumps(
            {"t": {"t": 9, "i": 0, "l": len(args), "a": args, "o": 0},
             "f": 31, "m": []}
        ).encode()
        req = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "X-Server-Id": fn_hash,
            "X-Server-Instance": "server-fn:0",
            "Cookie": f"auth={self.auth_token()}",
            "User-Agent": "usage-widget/0.1",
            "Referer": "https://opencode.ai/",
            "Origin": "https://opencode.ai",
        })
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise OpencodeError(f"HTTP {e.code}: {e.reason}") from e
        except OSError as e:
            raise OpencodeError(f"网络错误: {e}") from e
        try:
            return _JSParser(text).parse()
        except OpencodeError:
            raise
        except Exception as e:
            raise OpencodeError(f"响应解析失败: {e}") from e

    # -- 接口 --

    def workspaces(self) -> list[Workspace]:
        root = self._call(FN_WORKSPACES, [])
        out = []
        for d in root or []:
            if isinstance(d, dict) and d.get("id"):
                out.append(Workspace(
                    id=str(d["id"]),
                    name=str(d.get("name") or ""),
                    slug=d.get("slug"),
                ))
        return out

    def subscription_usage(self) -> SubscriptionUsage:
        """滚动/每周/每月用量（lite.subscription.get，与 /go 页一致）。"""
        root = self._call(FN_SUBSCRIPTION, [_arg_str(self.workspace_id)])
        root = root if isinstance(root, dict) else {}
        return SubscriptionUsage(
            rolling=UsageLevel.from_dict(root.get("rollingUsage")),
            weekly=UsageLevel.from_dict(root.get("weeklyUsage")),
            monthly=UsageLevel.from_dict(root.get("monthlyUsage")),
        )

    def month_costs(self, year: int, month0: int, tz: str = "+08:00") -> list[CostRecord]:
        """month0 为 0 基月份（7 = 8月），与网页一致。"""
        root = self._call(FN_COST, [
            _arg_str(self.workspace_id), _arg_num(year), _arg_num(month0), _arg_str(tz),
        ])
        out = []
        for d in root.get("usage") or []:
            if not isinstance(d, dict):
                continue
            out.append(CostRecord(
                date=str(d.get("date") or ""),
                model=str(d.get("model") or ""),
                cost_units=_as_int(d.get("totalCost")),
                key_id=str(d.get("keyId") or ""),
                plan=str(d.get("plan") or ""),
            ))
        return out

    def usage_page(self, page: int) -> list[UsageRecord]:
        root = self._call(FN_USAGE, [_arg_str(self.workspace_id), _arg_num(page)])
        out = []
        for d in root or []:
            if not isinstance(d, dict) or "timeCreated" not in d:
                continue
            enrichment = d.get("enrichment") or {}
            out.append(UsageRecord(
                created=d.get("timeCreated"),
                model=str(d.get("model") or ""),
                input_tokens=_as_int(d.get("inputTokens")),
                output_tokens=_as_int(d.get("outputTokens")),
                reasoning_tokens=_as_int(d.get("reasoningTokens")),
                cache_read_tokens=_as_int(d.get("cacheReadTokens")),
                cache_write_5m_tokens=d.get("cacheWrite5mTokens"),
                cache_write_1h_tokens=d.get("cacheWrite1hTokens"),
                cost_units=_as_int(d.get("cost")),
                key_id=str(d.get("keyID") or ""),
                plan=str((enrichment or {}).get("plan") or ""),
            ))
        return out

    def usage_sample(self, max_records: int, should_stop=None) -> list[UsageRecord]:
        """翻页取最近 max_records 条明细（页满为止，并发翻页）。"""
        out: list[UsageRecord] = []
        page = 0
        while len(out) < max_records:
            if should_stop is not None and should_stop():
                break
            pages = min((max_records - len(out) + PAGE_SIZE - 1) // PAGE_SIZE, 8)
            with ThreadPoolExecutor(max_workers=min(pages, 6)) as pool:
                results = list(pool.map(self.usage_page, range(page, page + pages)))
            for recs in results:
                out.extend(recs)
                if len(recs) < PAGE_SIZE:
                    return out
            page += pages
            if page > 200:
                break
        return out[:max_records]


# ---- 统计（估算） ----

def token_ratios(sample: list[UsageRecord]) -> dict[str, dict]:
    """按模型统计样本: {model: {"cost": units, "tokens": n}}"""
    stats: dict[str, dict] = {}
    for r in sample:
        s = stats.setdefault(r.model, {"cost": 0, "tokens": 0})
        s["cost"] += r.cost_units
        s["tokens"] += r.total_tokens
    return stats


def estimate_tokens(model_cost_units: dict[str, int],
                    ratios: dict[str, dict]) -> int:
    """按「每成本单位的 token 数」外推 token 总量。"""
    total = 0
    for model, units in model_cost_units.items():
        s = ratios.get(model)
        if s and s["cost"] > 0:
            total += int(units * s["tokens"] / s["cost"])
    return total
