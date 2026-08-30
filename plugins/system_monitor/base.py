"""系统监控插件基类：采集编排 + 历史曲线数据。

架构参考 Glances 的 GlancesPluginModel 的三个核心机制，按本插件的
体量简化：

- 采集注册：子类用 register_sample() 注册各域采样函数（对应 Glances
  的 update_local 各插件），collect() 统一执行并写入 stats 与历史
- 节流装饰器：throttled(ticks) 对应 Glances 的 _check_decorator
  （refresh_timer 节流），用于 GPU 等昂贵采集降频
- 环形历史：history 保存最近 N 个采样点（对应 Glances 的 stats_history），
  曲线直接以此为数据源

不引入 Glances 的 fields_description / 阈值告警 / SNMP / export。
"""
import time
from collections import deque
from typing import Callable

from plugins.base import Plugin

HISTORY_SIZE = 90  # 曲线保留 90 个采样点（system_monitor tick 2s ≈ 180 秒）


def throttled(ticks: int):
    """节流装饰器：每 ticks 次调用执行一次，其余返回 None。

    对应 Glances 的 _check_decorator（refresh_timer 节流）。
    用法：@throttled(5) 表示每 5 次 tick 才真正采集一次。
    """

    def decorator(fn: Callable):
        def wrapper(self, *args, **kwargs):
            counter = getattr(self, "_throttle_counters", {})
            n = counter.get(fn.__qualname__, 0) + 1
            counter[fn.__qualname__] = n
            self._throttle_counters = counter
            if n % ticks != 1:
                return None
            return fn(self, *args, **kwargs)

        return wrapper

    return decorator


class RateSampler:
    """累计计数器 → 每秒速率（对应 Glances _manage_rate 装饰器）。

    对 psutil 的累计字节/毫秒类计数器做两次采样差值：
        rate = (current - last) / dt
    首次调用返回 0.0。
    """

    def __init__(self):
        self._last = None
        self._last_t = 0.0

    def sample(self, current: float) -> float:
        """返回每秒增量；首次调用返回 0.0。"""
        now = time.monotonic()
        if self._last is None:
            self._last, self._last_t = current, now
            return 0.0
        dt = now - self._last_t
        delta = current - self._last
        self._last, self._last_t = current, now
        if dt <= 0 or delta <= 0:
            return 0.0
        return delta / dt

    def reset(self) -> None:
        self._last = None
        self._last_t = 0.0


class SystemPlugin(Plugin):
    """系统监控插件基类。

    子类职责：
    - __init__ 里用 register_sample() 注册采集函数
    - create_widget() 构建 UI
    - tick() 调 self.collect() 拿最新 stats，再刷新各控件
    - 曲线用 self.history["key"]（deque，环形缓冲）当数据源
    """

    def __init__(self, context=None):
        super().__init__(context)
        self._samples: dict[str, Callable[[], float]] = {}
        self._stats: dict[str, float] = {}
        self._history: dict[str, deque] = {}
        self._throttle_counters: dict[str, int] = {}

    # ---- 采集注册 ----

    @property
    def history(self) -> dict[str, deque]:
        """环形历史（key → 最近 N 个采样点的 deque），曲线数据源。"""
        return self._history

    def register_sample(self, key: str, fn: Callable[[], float]) -> None:
        """注册一个指标采样函数。collect() 时会调用并把结果写入
        stats 与 history，key 冲突时后注册的覆盖先注册的。"""
        self._samples[key] = fn
        self._history.setdefault(key, deque(maxlen=HISTORY_SIZE))

    # ---- 采集 ----

    def collect(self) -> dict[str, float]:
        """执行全部已注册采样器，写入 stats 与 history，返回 stats。"""
        for key, fn in self._samples.items():
            try:
                value = fn()
            except Exception:
                value = 0.0
            if value is None:
                continue
            self._stats[key] = value
            self._history[key].append(value)
        return self._stats

    def reset_history(self) -> None:
        """清空曲线历史（插件重启时调用，避免旧数据残留）。"""
        for hist in self._history.values():
            hist.clear()

    # ---- 生命周期 ----

    def on_stop(self) -> None:
        self._stats.clear()
        self.reset_history()
