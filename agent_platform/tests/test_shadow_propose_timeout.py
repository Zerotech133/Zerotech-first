# -*- coding: utf-8 -*-
"""任务 #112：run_shadow 单策略 propose 超时包裹——SKIPPED_TIMEOUT 落账契约测试。

既有护栏 test_run_shadow_guard.py 锁的是包裹机制本身（超时 fail-open /
可配置 / done 分支 smoke）；本文件补任务 #112 的增量契约：

1. sleep 桩策略超时 → _collect_verdicts_with_skips 产出 SKIPPED_TIMEOUT
   记录（带非空 reason）；
2. 主链继续：混合 registry 中慢策略被跳过后，快策略 Verdict 照常收集；
3. 落账面：skip 记录挂影子账本行 strategy_skipped 字段，JSONL 落盘读回
   字段无损（tempfile 隔离，不碰真仓 data/）；
4. 无 skip 时账本行不带 strategy_skipped 键（m5.v1 附加可选字段兼容）；
5. env 通道：SHADOW_PROPOSE_TIMEOUT_S 导入期解析（合法值生效/坏值回退）。

纪律：全部 tmp 隔离；慢桩用小 timeout（0.3s 级），不拖测试节奏。
"""

import importlib
import json
import sys
import time
from pathlib import Path


PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM))
sys.path.insert(0, str(PLATFORM / "engine"))

import run_shadow  # noqa: E402
from strategy_registry import StrategyAgent, StrategyError, Verdict  # noqa: E402


class _FastAgent(StrategyAgent):
    """快策略：立即返回合法 Verdict（主链继续的对照组）。"""

    strategy_id = "timeout_fast"
    stance = "neutral"

    def propose(self, context):
        return Verdict(
            strategy_id=self.strategy_id,
            symbol=context["symbol"],
            recommendation="hold",
            confidence=0.5,
        )


class _SleepStub(StrategyAgent):
    """sleep 桩策略：睡 seconds 后返回合法 Verdict（timeout_s < seconds 即超时）。

    用 sleep 而非纯 CPU 自旋（判例同 test_run_shadow_guard._DeadloopAgent）：
    释放 GIL，超时后的僵尸线程不拖慢 pytest 进程其余测试。"""

    strategy_id = "timeout_slow"
    stance = "neutral"

    def __init__(self, seconds):
        self.seconds = seconds

    def propose(self, context):
        time.sleep(self.seconds)
        return Verdict(
            strategy_id=self.strategy_id,
            symbol=context["symbol"],
            recommendation="buy",
            confidence=0.8,
        )


class _BoomAgent(StrategyAgent):
    """异常策略：propose 抛 StrategyError（SKIPPED_ERROR 分支对照）。"""

    strategy_id = "timeout_boom"
    stance = "neutral"

    def propose(self, context):
        raise StrategyError("timeout_boom 故意抛错")


def _registry(*agents):
    from strategy_registry import StrategyRegistry

    r = StrategyRegistry()
    for a in agents:
        r.register(a)
    return r


_CTX = {"symbol": "600519", "closes": [100.0, 101.0, 102.0]}


# ------------------------------------------------- 超时触发与落账记录


def test_sleep_stub_timeout_yields_skipped_timeout_record():
    """sleep 桩超时：verdict 被剔除，产出 SKIPPED_TIMEOUT 记录且 reason 非空。"""
    reg = _registry(_SleepStub(1.5))
    vs, skips = run_shadow._collect_verdicts_with_skips(reg, _CTX, timeout_s=0.3)
    assert vs == []
    assert len(skips) == 1
    rec = skips[0]
    assert rec["strategy_id"] == "timeout_slow"
    assert rec["status"] == "SKIPPED_TIMEOUT"
    assert rec["reason"].strip()  # 隔离门超时诊断文案，落账可回查


def test_slow_stub_within_timeout_still_records_verdict():
    """慢桩在 timeout 内跑完：正常入 vs，不产生 skip 记录（不误伤长尾策略）。"""
    reg = _registry(_SleepStub(0.05))
    vs, skips = run_shadow._collect_verdicts_with_skips(reg, _CTX, timeout_s=5.0)
    assert [v.strategy_id for v in vs] == ["timeout_slow"]
    assert skips == []


# ------------------------------------------------- 主链继续


def test_main_chain_continues_after_timeout():
    """主链继续：混合 registry 中慢策略超时被跳过，快策略 Verdict 照常收集。"""
    reg = _registry(_SleepStub(1.5), _FastAgent())
    t0 = time.monotonic()
    vs, skips = run_shadow._collect_verdicts_with_skips(reg, _CTX, timeout_s=0.3)
    elapsed = time.monotonic() - t0
    assert [v.strategy_id for v in vs] == ["timeout_fast"]
    assert [s["strategy_id"] for s in skips] == ["timeout_slow"]
    assert elapsed <= 4.0  # 有界：绝不等慢桩跑完 1.5s×N 也不挂死


def test_error_strategy_recorded_as_skipped_error():
    """异常策略：SKIPPED_ERROR 记录，reason 含诊断（区分于超时形态）。"""
    reg = _registry(_BoomAgent())
    vs, skips = run_shadow._collect_verdicts_with_skips(reg, _CTX, timeout_s=5.0)
    assert vs == []
    assert skips[0]["status"] == "SKIPPED_ERROR"
    assert "timeout_boom 故意抛错" in skips[0]["reason"]


# ------------------------------------------------- 落账面（影子账本行）


def test_skip_records_survive_ledger_roundtrip(tmp_path):
    """落账面：skip 记录挂影子账本行 strategy_skipped，JSONL 写读无损。"""
    reg = _registry(_SleepStub(1.5), _FastAgent())
    _vs, skips = run_shadow._collect_verdicts_with_skips(reg, _CTX, timeout_s=0.3)
    row = run_shadow.build_ledger_row(
        "2026-10-19", "600519", "贵州茅台", "run-test", 100.0, {}, True, {}, 1.0
    )
    row["strategy_skipped"] = skips  # 与 main done 分支接线同形
    ledger = tmp_path / "shadow_ledger.jsonl"
    ledger.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    back = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    skipped = back["strategy_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["strategy_id"] == "timeout_slow"
    assert skipped[0]["status"] == "SKIPPED_TIMEOUT"
    assert skipped[0]["reason"]


def test_row_without_skips_has_no_strategy_skipped_key():
    """schema 兼容：无 skip 时账本行不带 strategy_skipped 键（附加可选字段）。"""
    row = run_shadow.build_ledger_row(
        "2026-10-19", "600519", "贵州茅台", "run-test", 100.0, {}, True, {}, 1.0
    )
    assert "strategy_skipped" not in row


# ------------------------------------------------- env 通道


def test_env_channel_overrides_default(monkeypatch):
    """env 通道：SHADOW_PROPOSE_TIMEOUT_S=120 导入期解析生效（120s 建议档）。"""
    monkeypatch.setenv("SHADOW_PROPOSE_TIMEOUT_S", "120")
    mod = importlib.reload(run_shadow)
    assert mod.PROPOSE_TIMEOUT_S == 120.0
    # 恢复模块默认状态，避免污染同进程其余测试
    monkeypatch.delenv("SHADOW_PROPOSE_TIMEOUT_S")
    importlib.reload(run_shadow)
    assert run_shadow.PROPOSE_TIMEOUT_S == 10.0


def test_env_channel_bad_value_falls_back(monkeypatch):
    """env 通道容错：非法/非正值回退默认 10.0（不因坏配置挂死启动）。"""
    for bad in ("abc", "-3", "0"):
        monkeypatch.setenv("SHADOW_PROPOSE_TIMEOUT_S", bad)
        importlib.reload(run_shadow)
        assert run_shadow.PROPOSE_TIMEOUT_S == 10.0, bad
    monkeypatch.delenv("SHADOW_PROPOSE_TIMEOUT_S")
    importlib.reload(run_shadow)


def test_env_channel_feeds_no_arg_collect():
    """env 设定后无参调用链生效：_collect_verdicts_with_skips 读模块常量。

    monkeypatch 模块常量模拟 env 解析结果（None 哨兵语义，调用期解析）。"""
    import unittest.mock as mock

    reg = _registry(_SleepStub(1.5))
    with mock.patch.object(run_shadow, "PROPOSE_TIMEOUT_S", 0.2):
        vs, skips = run_shadow._collect_verdicts_with_skips(reg, _CTX)
    assert vs == []
    assert skips[0]["status"] == "SKIPPED_TIMEOUT"
