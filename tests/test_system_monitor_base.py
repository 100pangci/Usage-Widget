"""系统监控插件基类测试：采集注册、节流、环形历史、RateSampler。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugins.system_monitor.base import RateSampler, SystemPlugin, throttled


class _Demo(SystemPlugin):
    id = "demo"

    def __init__(self, context=None):
        super().__init__(context)
        self.register_sample("cpu", lambda: 42.0)
        self.register_sample("mem", lambda: 60.0)


def test_collect_merges_stats_and_history():
    p = _Demo()
    stats = p.collect()
    assert stats["cpu"] == 42.0
    assert stats["mem"] == 60.0
    assert list(p.history["cpu"]) == [42.0]
    assert list(p.history["mem"]) == [60.0]
    # 再次 collect：历史累积
    p.collect()
    assert list(p.history["cpu"]) == [42.0, 42.0]


def test_history_ring_bounded():
    p = _Demo()
    for _ in range(200):
        p.collect()
    assert len(p.history["cpu"]) == 90  # HISTORY_SIZE
    assert list(p.history["cpu"])[-1] == 42.0


def test_collect_handles_exception():
    def boom():
        raise RuntimeError("boom")

    p = _Demo()
    p.register_sample("bad", boom)
    stats = p.collect()
    assert stats["bad"] == 0.0
    assert list(p.history["bad"]) == [0.0]


def test_collect_skips_none():
    p = _Demo()
    p.register_sample("slow", lambda: None)
    stats = p.collect()
    assert "slow" not in stats
    # 采样返回 None：不入 stats，历史保持空
    assert len(p.history["slow"]) == 0


def test_reset_history_clears():
    p = _Demo()
    p.collect()
    p.reset_history()
    assert len(p.history["cpu"]) == 0


def test_on_stop_clears_stats_and_history():
    p = _Demo()
    p.collect()
    p.on_stop()
    assert p._stats == {}
    assert len(p.history["cpu"]) == 0


def test_throttled_runs_every_n_ticks():
    calls = []

    class _Throttled:
        _throttle_counters = {}

        @throttled(3)
        def run(self):
            calls.append(1)

    obj = _Throttled()
    for _ in range(6):
        obj.run()
    assert len(calls) == 2  # 第 1、4 次执行


def test_rate_sampler_computes_rate():
    rs = RateSampler()
    assert rs.sample(1000) == 0.0  # 首次打底
    # 人为缩短时间间隔来验证差值逻辑
    rs._last_t -= 10.0  # 模拟 10 秒前采样
    rate = rs.sample(2000)
    # (2000-1000)/10；两次调用间真实时间仍会流逝，容差放宽
    assert abs(rate - 100.0) < 0.1
    rs.reset()
    assert rs.sample(0) == 0.0
