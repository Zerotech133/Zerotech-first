# 缠论 CHAN_V7「增强版」策略优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改动 `CHAN`(v1) 对照组行为的前提下，新建 `CHAN_V7`「缠论增强版」，叠加三入场门 + 收紧止损 + 15 交易日时间止损 + 中枢下沿动态出场；并诊断修复近期"当日平仓"漏损。

**Architecture:** 复用现有 A/B 框架：选股端给 `chan/strategy.py::select_candidates` 加默认关闭的三门开关（`CHAN` 走默认路径逐字节不变），`CHAN_V7` 用 `partial` 三门全开注册；出场端扩展 `ExitPolicy`（加 `max_hold_days`/`dynamic_stop`）并在出场链接入时间止损，中枢下沿动态出场走日频独立函数。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy / pandas / czsc==0.10.12 / pytest。跑测：`PYTHONPATH=src .venv/bin/python -m pytest -q`。

---

## 前置说明（所有任务通用）

- **分支**：当前在 `main`。实施前先 `git checkout -b feat/chan-v7-strategy`（不要直接在 main 提交）。
- **czsc 版本**：确认 `czsc==0.10.12`（历史教训：版本漂移会让缠论中枢运行时全废，单测打桩会掩盖）。运行 `.venv/bin/python -c "import czsc; print(czsc.__version__)"` 应输出 `0.10.12`。
- **对照组红线**：任何 task 完成后，`CHAN`(v1) 的选股与出场行为必须逐字节不变。Task 5 有专门回归测试守卫这一点。

---

## Task 1：诊断第 1 项——"当日平仓"漏损根因

诊断任务（先查清来源，不预设修法）。产出结论写入 `docs/audit/2026-09-01-chan-sameday-close-diagnosis.md`。

**Files:**
- Create: `docs/audit/2026-09-01-chan-sameday-close-diagnosis.md`

- [ ] **Step 1: 取 9 笔当日平仓 position_id**

Run:
```bash
PGPASSWORD=zhice_pass psql -h localhost -U zhice -d zhice_os_v2 -c "
SELECT position_id, ts_code, open_price, close_price, pnl_amount,
       opened_at, closed_at, trade_plan_id
FROM v2_position
WHERE strategy_version_id='v1-cn-sv-4' AND status='CLOSED' AND direction='BUY'
  AND (closed_at::date - opened_at::date)=0
ORDER BY opened_at;"
```
Expected: 9 行，含精确的 opened_at/closed_at 时间戳（看是否同一时刻批量写入）。

- [ ] **Step 2: 关联执行/成交/复盘表看 exit_kind 与成交时间**

Run:
```bash
PGPASSWORD=zhice_pass psql -h localhost -U zhice -d zhice_os_v2 -c "
SELECT er.position_id, er.exit_kind, er.created_at, fr.filled_at, fr.price
FROM v2_execution_record er
LEFT JOIN v2_fill_record fr ON er.position_id = fr.position_id
WHERE er.position_id IN (
  SELECT position_id FROM v2_position
  WHERE strategy_version_id='v1-cn-sv-4' AND status='CLOSED' AND direction='BUY'
    AND (closed_at::date - opened_at::date)=0)
ORDER BY er.created_at;"
```
（若表列名不符，先 `\d v2_execution_record` / `\d v2_fill_record` 查实际列名再调整。）
Expected: 每笔的 exit_kind 与成交时间戳。

- [ ] **Step 3: 判定来源并记录结论**

按证据归类到三选一，写入诊断文档：
- (a) **数据口径**：opened_at/closed_at 是同一批量时刻、无真实盘中 exit_kind → 属回测/重放写入，非 bug。记录口径，绩效统计中标注剔除。
- (b) **真 bug**：exit_kind 显示当日走了 STOP_LOSS/TAKE_PROFIT 且 opened_at 是当日 → 绕过了 T+1 锁（`workflow_service.py:1048`）。定位绕过路径。
- (c) **隐藏出场**：出现非标准 exit_kind → 存在预期外的即时平仓逻辑，确认是否有意。

- [ ] **Step 4: 条件修复（仅当 Step 3 判为 (b) 或 (c)）**

若为 (b)：在诊断文档记录绕过点后，新建一个 task（写失败回归测试复现当日平仓 → 修 → 测试转绿）。若为 (a)：无需改代码，仅在文档记录口径。**修法待 Step 3 结论确定，不在本计划预写。**

- [ ] **Step 5: Commit**

```bash
git add docs/audit/2026-09-01-chan-sameday-close-diagnosis.md
git commit -m "docs(chan): diagnose same-day close root cause"
```

---

## Task 2：扩展 ExitPolicy + 注册 CHAN_V7 出场参数

**Files:**
- Modify: `src/zhice_os_v2/domain/strategy/exit_policies.py:18-26`（加字段）、`:41`（加注册项）
- Test: `tests/test_exit_policy_chan_v7.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_exit_policy_chan_v7.py`:
```python
from decimal import Decimal

from zhice_os_v2.domain.strategy.exit_policies import (
    DEFAULT_EXIT_POLICY,
    get_exit_policy,
)


def test_chan_v7_policy_values():
    p = get_exit_policy("CHAN_V7")
    assert p.stop_loss_pct == Decimal("0.05")      # 6%→5%
    assert p.take_profit_pct == Decimal("0.18")    # 沿用 CHAN，本轮不动
    assert p.use_trailing is True
    assert p.trailing_activate_pct == Decimal("0.05")
    assert p.trailing_trail_pct == Decimal("0.035")
    assert p.max_hold_days == 15                    # 时间止损
    assert p.dynamic_stop == "CHAN_ZS"              # 中枢下沿动态出场


def test_existing_policies_backward_compatible():
    # 既有策略不启用时间止损/动态出场（向后兼容）
    assert get_exit_policy("CHAN").max_hold_days is None
    assert get_exit_policy("CHAN").dynamic_stop is None
    assert DEFAULT_EXIT_POLICY.max_hold_days is None
    assert DEFAULT_EXIT_POLICY.dynamic_stop is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_exit_policy_chan_v7.py`
Expected: FAIL（`ExitPolicy` 无 `max_hold_days` 字段 / `CHAN_V7` 未注册）。

- [ ] **Step 3: 扩展 ExitPolicy dataclass**

Modify `exit_policies.py` 的 dataclass（`:18-26`），在末尾追加两个带默认值的字段：
```python
@dataclass(frozen=True)
class ExitPolicy:
    """单个策略类型的出场参数。百分比均为正数小数（0.05 = 5%）。"""

    stop_loss_pct: Decimal          # 初始止损：入场价 ×(1 - stop_loss_pct)
    take_profit_pct: Decimal        # 止盈：入场价 ×(1 + take_profit_pct)
    use_trailing: bool              # 是否启用浮动止损
    trailing_activate_pct: Decimal  # 浮盈达到此值时启动浮动止损
    trailing_trail_pct: Decimal     # 止损跟随：当前价下方此幅度
    max_hold_days: int | None = None    # 时间止损：持有达此交易日数强制平仓；None=不启用
    dynamic_stop: str | None = None     # 动态出场类型（"CHAN_ZS"=跌破日线笔中枢下沿）；None=不启用
```
（新字段有默认值，既有 12 条注册项全用关键字构造，无需改动即向后兼容。）

- [ ] **Step 4: 注册 CHAN_V7 出场参数**

在 `EXIT_POLICY_REGISTRY`（`:41`）字典内、`"CHAN_V6"` 条目之后追加：
```python
    # 缠论 v7 = v1 几何选股 + 三门 + 收紧止损(5%) + 15 交易日时间止损 + 中枢下沿动态出场。
    "CHAN_V7": ExitPolicy(
        stop_loss_pct=Decimal("0.05"),
        take_profit_pct=Decimal("0.18"),
        use_trailing=True,
        trailing_activate_pct=Decimal("0.05"),
        trailing_trail_pct=Decimal("0.035"),
        max_hold_days=15,
        dynamic_stop="CHAN_ZS",
    ),
```

- [ ] **Step 5: 跑测试确认通过**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_exit_policy_chan_v7.py`
Expected: PASS（2 passed）。

- [ ] **Step 6: Commit**

```bash
git add src/zhice_os_v2/domain/strategy/exit_policies.py tests/test_exit_policy_chan_v7.py
git commit -m "feat(chan-v7): extend ExitPolicy with time/dynamic stop, register CHAN_V7"
```

---

## Task 3：交易日计数 helper（纯函数）

时间止损与动态出场都需"持有了几个交易日"。仓库无现成通用工具，新建纯函数（便于测试），DB 查询单独薄封装。

**Files:**
- Create: `src/zhice_os_v2/domain/market/trading_days.py`
- Test: `tests/test_trading_days.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_trading_days.py`:
```python
from datetime import date

from zhice_os_v2.domain.market.trading_days import count_trading_days


def test_count_trading_days_inclusive_open_days():
    # 交易日历：周一~周五 open，周末 closed
    cal = {
        date(2026, 1, 5): True,   # 一
        date(2026, 1, 6): True,   # 二
        date(2026, 1, 7): True,   # 三
        date(2026, 1, 8): True,   # 四
        date(2026, 1, 9): True,   # 五
        date(2026, 1, 10): False, # 六
        date(2026, 1, 11): False, # 日
        date(2026, 1, 12): True,  # 一
    }
    # 从 1/5 开仓，到 1/12：跨越的开放交易日 = 5,6,7,8,9,12 = 6 天
    assert count_trading_days(cal, date(2026, 1, 5), date(2026, 1, 12)) == 6
    # 同日 = 1 个交易日
    assert count_trading_days(cal, date(2026, 1, 5), date(2026, 1, 5)) == 1
    # as_of 早于 opened → 0
    assert count_trading_days(cal, date(2026, 1, 8), date(2026, 1, 5)) == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_trading_days.py`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现纯函数**

Create `src/zhice_os_v2/domain/market/trading_days.py`:
```python
"""交易日计数（纯函数，与持久化解耦，便于测试）。"""
from __future__ import annotations

from datetime import date


def count_trading_days(
    calendar: dict[date, bool], opened: date, as_of: date
) -> int:
    """统计 [opened, as_of] 闭区间内 is_open=True 的交易日数（含首尾）。

    calendar: {trade_date: is_open}。缺失的日期视为非交易日。
    as_of < opened 返回 0。
    """
    if as_of < opened:
        return 0
    return sum(
        1
        for d, is_open in calendar.items()
        if is_open and opened <= d <= as_of
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_trading_days.py`
Expected: PASS（1 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/zhice_os_v2/domain/market/trading_days.py tests/test_trading_days.py
git commit -m "feat(market): add trading-day counter pure function"
```

---

## Task 4：时间止损接入出场链

在出场链中，正常止盈/移动止损/止损都未触发后，追加时间止损兜底。

**Files:**
- Modify: `src/zhice_os_v2/application/services/workflow_service.py:25-32`（加 exit_kind 文案）、`:184`（加 helper）、`:1101` 止损分支之后（加时间止损分支）
- Test: `tests/test_time_stop_exit.py`

- [ ] **Step 1: 写失败测试（纯逻辑判定）**

先把"是否超期"判定抽成模块级纯函数，便于测试。Create `tests/test_time_stop_exit.py`:
```python
from datetime import date

from zhice_os_v2.application.services.workflow_service import _time_stop_hit


def test_time_stop_hit_at_threshold():
    cal = {date(2026, 1, d): True for d in range(5, 30)}  # 全开放（简化）
    # 持有第 15 个交易日达阈值
    assert _time_stop_hit(cal, date(2026, 1, 5), date(2026, 1, 19), 15) is True
    # 第 14 个交易日未达
    assert _time_stop_hit(cal, date(2026, 1, 5), date(2026, 1, 18), 15) is False
    # max_hold_days=None 永不触发
    assert _time_stop_hit(cal, date(2026, 1, 5), date(2026, 1, 19), None) is False
```
（1/5 起算第 1 天，全开放日历下第 15 天 = 1/19。）

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_time_stop_exit.py`
Expected: FAIL（`_time_stop_hit` 不存在）。

- [ ] **Step 3: 实现 _time_stop_hit + 接入出场链**

在 `workflow_service.py` 顶部（`_is_today` 函数附近，`:184` 区域）加两个 helper：
```python
def _time_stop_hit(
    calendar: dict, opened, as_of, max_hold_days: int | None
) -> bool:
    """持有交易日数是否达到时间止损阈值。max_hold_days=None 恒 False。"""
    if not max_hold_days:
        return False
    from zhice_os_v2.domain.market.trading_days import count_trading_days
    opened_d = opened.date() if hasattr(opened, "date") else opened
    as_of_d = as_of.date() if hasattr(as_of, "date") else as_of
    return count_trading_days(calendar, opened_d, as_of_d) >= max_hold_days


def _load_trading_calendar(market: str) -> dict:
    """加载该市场交易日历 {trade_date: is_open}，供时间止损计数。"""
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session
    session = get_session()
    try:
        rows = session.query(
            orm.MarketCalendarModel.trade_date,
            orm.MarketCalendarModel.is_open,
        ).filter(orm.MarketCalendarModel.market == market).all()
        return {r[0]: bool(r[1]) for r in rows}
    finally:
        session.close()
```

在 `_EXIT_KIND_TEXT`（`:25-32`）加两条文案：
```python
    "TIME_STOP": "时间止损：持有超过最大交易日数，到期了结",
    "CHAN_EXIT": "缠论离场：跌破中枢下沿，结构走坏",
```

在出场循环开始前（`positions` for 循环之前，`:1031` 之上）一次性加载日历：
```python
        _cal = _load_trading_calendar(market)  # {date: is_open}，供时间止损
```

在止损分支（`:1101` 的 `if stop is not None and current <= stop:` 整块）之后、循环体末尾追加时间止损兜底：
```python
            # 时间止损（云端语义）：正常止盈/止损未触发，持有达 max_hold_days 交易日即了结
            if _time_stop_hit(_cal, position.opened_at, profile.trading_date(),
                              policy.max_hold_days):
                sellable, why = can_sell(
                    float(current), pre_close, position.ts_code,
                    names.get(position.ts_code), market=market)
                if sellable:
                    self._close_position(position, current, exit_kind="TIME_STOP")
                    replay = replay_service.build_replay_case(position)
                    if replay:
                        learning_service.record_from_replay(replay)
                        replay_created += 1
                else:
                    blocked.append({"ts_code": position.ts_code,
                                    "reason": f"时间止损触发但{why}"})
                continue
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_time_stop_exit.py`
Expected: PASS。

- [ ] **Step 5: 全量回归（确认没打破出场链既有测试）**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_plan_exit_pollution.py tests/test_intraday.py`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add src/zhice_os_v2/application/services/workflow_service.py tests/test_time_stop_exit.py
git commit -m "feat(chan-v7): add time-stop exit to workflow exit chain"
```

---

## Task 5：CHAN_V7 选股三门（改 strategy.py，含对照组回归）

给 v1 `select_candidates` 加默认关闭的三门开关。**默认路径必须与改动前逐字节一致。**

**Files:**
- Modify: `src/zhice_os_v2/domain/strategy/strategies/chan/strategy.py:161-196`
- Test: `tests/test_chan_v7_gates.py`

- [ ] **Step 1: 写失败测试（对照组回归 + 三门各分支）**

Create `tests/test_chan_v7_gates.py`:
```python
from datetime import date

import pandas as pd

from zhice_os_v2.domain.strategy.strategies.chan import engine as chan_engine
from zhice_os_v2.domain.strategy.strategies.chan import strategy as chan_strategy


def _mini_bars(codes, n=80):
    frames = []
    for i, code in enumerate(codes):
        frames.append(pd.DataFrame({
            "ts_code": [code] * n,
            "security_id": [i + 1] * n,
            "dt": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": [10.0] * n, "high": [11.0] * n,
            "low": [9.0] * n, "close": [10.5] * n,
            "volume": [1e6] * n, "amount": [1e7] * n,
        }))
    return pd.concat(frames, ignore_index=True)


def _stub_universe(monkeypatch, codes):
    monkeypatch.setattr(chan_strategy, "_load_universe",
                        lambda td, market="CN": codes)
    monkeypatch.setattr(chan_strategy, "_load_ohlcv",
                        lambda td, codes_, market="CN": _mini_bars(codes))

    def fake_analyze(ts_code, df):
        return chan_engine.ChanSnapshot(
            symbol=ts_code, bar_count=len(df), bs_point="b3",
            divergence="medium", prev_zs={"bi_count": 10, "zd": 10.0, "zg": 11.0})
    monkeypatch.setattr(chan_engine, "analyze", fake_analyze)


def test_control_group_default_unchanged(monkeypatch):
    """对照组红线：默认参数（三门全关）时，AAA/ZZZ 两只 b3 全部保留。"""
    _stub_universe(monkeypatch, ["AAA", "ZZZ"])
    picks = chan_strategy.select_candidates(date(2025, 1, 2), max_candidates=20)
    assert {p["ts_code"] for p in picks} == {"AAA", "ZZZ"}


def test_market_gate_halts_all(monkeypatch):
    """大盘门：regime 不允许 → 返回空。"""
    _stub_universe(monkeypatch, ["AAA", "ZZZ"])
    monkeypatch.setattr(chan_strategy, "market_regime_permits_entry",
                        lambda td, market="CN": False)
    picks = chan_strategy.select_candidates(
        date(2025, 1, 2), max_candidates=20, market_gate=True)
    assert picks == []


def test_weekly_gate_filters_per_stock(monkeypatch):
    """周线门：只有 ZZZ 过周线 → 只保留 ZZZ。"""
    _stub_universe(monkeypatch, ["AAA", "ZZZ"])
    monkeypatch.setattr(
        chan_strategy, "weekly_trend_permits_long",
        lambda df: bool((df["ts_code"] == "ZZZ").any()))
    picks = chan_strategy.select_candidates(
        date(2025, 1, 2), max_candidates=20, weekly_filter=True)
    assert {p["ts_code"] for p in picks} == {"ZZZ"}


def test_intraday_gate_confirms_head(monkeypatch):
    """30分钟确认：只有 ZZZ 确认 → 只保留 ZZZ。"""
    _stub_universe(monkeypatch, ["AAA", "ZZZ"])
    monkeypatch.setattr(
        chan_strategy, "intraday_confirms_buy",
        lambda ts_code, td, market="CN": ts_code == "ZZZ")
    picks = chan_strategy.select_candidates(
        date(2025, 1, 2), max_candidates=20, intraday_confirm=True)
    assert {p["ts_code"] for p in picks} == {"ZZZ"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_chan_v7_gates.py`
Expected: FAIL（`select_candidates` 无三门参数 / 无 `market_regime_permits_entry` 等模块级名字）。

- [ ] **Step 3: 改 strategy.py 加三门（模块级 import + 开关分支）**

在 `strategy.py` 顶部 import 区加（便于测试 monkeypatch 到 `chan_strategy` 命名空间）：
```python
from zhice_os_v2.domain.strategy.strategies.chan.engine import (
    weekly_trend_permits_long,
)
from zhice_os_v2.domain.strategy.strategies.chan.market_regime import (
    market_regime_permits_entry,
)
from zhice_os_v2.domain.strategy.strategies.chan.intraday import (
    intraday_confirms_buy,
)
```
改 `select_candidates` 签名（`:161`）为：
```python
def select_candidates(trade_date: date, max_candidates: int = 20,
                      weekly_filter: bool = False,
                      market_gate: bool = False,
                      intraday_confirm: bool = False,
                      market: str = "CN") -> list[dict]:
    """入口：预过滤 → 逐股结构分析 → 二买/三买评分排序。

    三门默认关闭；CHAN(v1) 走默认路径行为不变，CHAN_V7 三门全开。
    """
```
门①（大盘门）：在函数体最前、加载 universe 之前早退：
```python
    if market_gate and not market_regime_permits_entry(trade_date, market):
        logger.info("chan.market_halt trade_date=%s", trade_date)
        return []
```
门②（周线门）：在逐股循环内，`if snap.bs_point not in ("b2", "b3"): continue` 之后、`_score` 之前插入（`g` 为该股 df）：
```python
        if weekly_filter and not weekly_trend_permits_long(g.reset_index(drop=True)):
            continue
```
门③（30分钟确认）：在 `picks.sort(...)` 之后、`return` 之前，照 strategy_v2 模板对头部做确认：
```python
    if intraday_confirm and picks:
        confirmed = []
        for p in picks[: max_candidates * 4]:
            if len(confirmed) >= max_candidates:
                break
            if intraday_confirms_buy(p["ts_code"], trade_date, market):
                confirmed.append(p)
        picks = confirmed
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_chan_v7_gates.py`
Expected: PASS（4 passed）。

- [ ] **Step 5: 跑既有缠论测试确认对照组无回归**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_chan_scoring.py tests/test_chan_dt_slice.py`
Expected: PASS（默认路径不变）。

- [ ] **Step 6: Commit**

```bash
git add src/zhice_os_v2/domain/strategy/strategies/chan/strategy.py tests/test_chan_v7_gates.py
git commit -m "feat(chan-v7): add three optional entry gates to v1 selector (default off)"
```

---

## Task 6：注册 CHAN_V7 到 STRATEGY_REGISTRY

**Files:**
- Modify: `src/zhice_os_v2/application/services/strategy_registry_service.py:24-40`
- Test: `tests/test_chan_v7_registry.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_chan_v7_registry.py`:
```python
from functools import partial

from zhice_os_v2.application.services.strategy_registry_service import (
    STRATEGY_REGISTRY,
)
from zhice_os_v2.domain.strategy.strategies import chan


def test_chan_v7_registered_with_three_gates():
    sel = STRATEGY_REGISTRY["CHAN_V7"]
    assert isinstance(sel, partial)
    assert sel.func is chan.strategy.select_candidates
    assert sel.keywords == {
        "weekly_filter": True, "market_gate": True, "intraday_confirm": True}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_chan_v7_registry.py`
Expected: FAIL（`KeyError: 'CHAN_V7'`）。

- [ ] **Step 3: 注册**

在 `STRATEGY_REGISTRY`（`:24`）字典内 `"CHAN_V6"` 之后加：
```python
    # V7 = v1 几何选股 + 三门全开（周线+大盘+30分钟）
    "CHAN_V7": partial(chan.strategy.select_candidates,
                       weekly_filter=True, market_gate=True, intraday_confirm=True),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_chan_v7_registry.py`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/zhice_os_v2/application/services/strategy_registry_service.py tests/test_chan_v7_registry.py
git commit -m "feat(chan-v7): register CHAN_V7 selector with three gates"
```

---

## Task 7：中枢下沿动态出场（日频，叠加）

纯逻辑函数判定"现价是否跌破最近中枢下沿 zd"；service 层日频扫描 CHAN_V7 持仓并平仓。

**Files:**
- Create: `src/zhice_os_v2/domain/strategy/strategies/chan/dynamic_exit.py`
- Modify: `src/zhice_os_v2/scheduler_jobs.py`（收盘后 job 注册）
- Test: `tests/test_chan_dynamic_exit.py`

- [ ] **Step 1: 写失败测试（纯逻辑）**

Create `tests/test_chan_dynamic_exit.py`:
```python
from zhice_os_v2.domain.strategy.strategies.chan.dynamic_exit import (
    price_breaks_pivot_low,
)


def test_break_below_pivot_low_triggers():
    zs_list = [{"zd": 10.0, "zg": 11.0}, {"zd": 12.0, "zg": 13.0}]  # 取最后一个
    assert price_breaks_pivot_low(zs_list, current_price=11.9) is True   # < 12.0
    assert price_breaks_pivot_low(zs_list, current_price=12.0) is False  # 等于不破
    assert price_breaks_pivot_low(zs_list, current_price=12.5) is False


def test_no_pivot_returns_false():
    assert price_breaks_pivot_low([], current_price=5.0) is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_chan_dynamic_exit.py`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现纯逻辑函数**

Create `src/zhice_os_v2/domain/strategy/strategies/chan/dynamic_exit.py`:
```python
"""缠论动态出场：跌破最近日线笔中枢下沿（zd）即离场。"""
from __future__ import annotations


def price_breaks_pivot_low(zs_list: list[dict], current_price: float) -> bool:
    """现价是否跌破最近有效中枢的下沿 zd（严格小于）。无中枢返回 False。"""
    if not zs_list:
        return False
    last_zd = zs_list[-1].get("zd")
    if last_zd is None:
        return False
    return current_price < float(last_zd)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_chan_dynamic_exit.py`
Expected: PASS。

- [ ] **Step 5: 写 service 层扫描函数（含集成测试）**

在 `dynamic_exit.py` 追加（用 `extract_bi_list` + `build_zs_list` 现算中枢）：
```python
def evaluate_position_pivot_exit(ts_code: str, df, current_price: float) -> bool:
    """对单笔持仓：用日线 df 现算笔中枢，判断现价是否跌破下沿。"""
    from zhice_os_v2.domain.strategy.strategies.chan.engine import (
        build_zs_list,
        extract_bi_list,
    )
    bi_list, _ = extract_bi_list(ts_code, df)
    zs_list = build_zs_list(bi_list)
    return price_breaks_pivot_low(zs_list, current_price)
```
`tests/test_chan_dynamic_exit.py` 追加集成测试（真跑 czsc，短序列不崩）：
```python
import pandas as pd

from zhice_os_v2.domain.strategy.strategies.chan.dynamic_exit import (
    evaluate_position_pivot_exit,
)


def test_evaluate_no_crash_on_short_series():
    """不足以构成中枢的短序列不应崩溃，返回 False。"""
    df = pd.DataFrame({
        "dt": pd.to_datetime(["2024-01-05", "2024-01-06", "2024-01-07"]),
        "open": [10.0, 9.0, 8.0], "high": [10.0, 9.0, 8.0],
        "low": [9.0, 8.0, 7.0], "close": [9.0, 8.0, 7.0],
        "volume": [100.0, 90.0, 80.0],
    })
    assert evaluate_position_pivot_exit("TEST", df, current_price=5.0) is False
```

- [ ] **Step 6: 跑测试确认通过**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_chan_dynamic_exit.py`
Expected: PASS（3 passed）。

- [ ] **Step 7: 接入日频调度（收盘后扫描 CHAN_V7 持仓）**

在 `scheduler_jobs.py` 找到收盘后 job 区（与晚报/EOD 同批），新增 job：查 `status=OPEN` 且 `strategy_version_id='v1-cn-sv-7'`（CHAN_V7）的持仓，逐笔取日线 df + 最新价，调 `evaluate_position_pivot_exit`，命中则走 `workflow_service._close_position(position, price, exit_kind="CHAN_EXIT")`（受 T+1 约束，次日生效）。**该 job 必须只扫 CHAN_V7 持仓（按 strategy_version_id 过滤），绝不触及 CHAN 对照组。** job 注册照 `scheduler_jobs.py` 现有 job 结构；实现后手动触发一次看日志确认只扫到 CHAN_V7。

- [ ] **Step 8: Commit**

```bash
git add src/zhice_os_v2/domain/strategy/strategies/chan/dynamic_exit.py tests/test_chan_dynamic_exit.py src/zhice_os_v2/scheduler_jobs.py
git commit -m "feat(chan-v7): add pivot-break dynamic exit (daily eval)"
```

---

## Task 8：版本落地 + 端到端验证

**Files:**
- Create: `scripts/seed_chan_v7.py`（幂等 upsert CHAN_V7 strategy/version 行）
- Modify: 调度器选股策略清单（把 CHAN_V7 纳入每日选股/回测）

- [ ] **Step 1: 写幂等 seed 脚本**

Create `scripts/seed_chan_v7.py`（psycopg2 直连，`ON CONFLICT DO NOTHING`）：
```python
"""幂等注册 CHAN_V7 策略与版本（STAGING，不实盘下单）。"""
import psycopg2

conn = psycopg2.connect(host="localhost", dbname="zhice_os_v2",
                        user="zhice", password="zhice_pass")
cur = conn.cursor()
cur.execute("""
INSERT INTO v2_strategy (strategy_id, strategy_name, market, strategy_type,
                         status, description, created_at, updated_at)
VALUES ('v1-cn-strat-4-v7', '[V7] 缠论增强版', 'CN', 'CHAN_V7', 'ACTIVE',
        'v1几何选股+周线门+大盘regime门+30分钟确认+5%止损+15交易日时间止损+中枢下沿动态出场',
        now(), now())
ON CONFLICT (strategy_id) DO NOTHING;
""")
cur.execute("""
INSERT INTO v2_strategy_version (strategy_version_id, strategy_id, version_no,
                                 status, created_at)
VALUES ('v1-cn-sv-7', 'v1-cn-strat-4-v7', 'v7.0.0', 'STAGING', now())
ON CONFLICT (strategy_version_id) DO NOTHING;
""")
conn.commit()
print("CHAN_V7 seeded (idempotent).")
conn.close()
```
（先 `\d v2_strategy` / `\d v2_strategy_version` 确认列名与唯一约束，按实际调整。）

- [ ] **Step 2: 跑 seed 并核对**

Run:
```bash
PYTHONPATH=src .venv/bin/python scripts/seed_chan_v7.py
PGPASSWORD=zhice_pass psql -h localhost -U zhice -d zhice_os_v2 -c "
SELECT strategy_id, strategy_type, status FROM v2_strategy WHERE strategy_type='CHAN_V7';
SELECT strategy_version_id, status FROM v2_strategy_version WHERE strategy_version_id='v1-cn-sv-7';"
```
Expected: 各 1 行，strategy ACTIVE / version STAGING。

- [ ] **Step 3: 调度器纳入 CHAN_V7 选股**

在调度器每日选股的策略类型清单（`scheduler_jobs.py` 中调用 `StrategyRegistryService().run(strategy_type, ...)` 的策略列表处）加入 `"CHAN_V7"`。确认它与 `CHAN` 并行跑、各自独立落 `v2_candidate_pool` / `v2_trade_plan`。

- [ ] **Step 4: 端到端验证（不打桩，真跑一次选股）**

Run（触发一次 CHAN_V7 选股，看是否有信号、三门是否生效）：
```bash
PYTHONPATH=src .venv/bin/python -c "
from datetime import date
from zhice_os_v2.application.services.strategy_registry_service import StrategyRegistryService
picks = StrategyRegistryService().run('CHAN_V7', date.today(), max_candidates=20, market='CN')
print('CHAN_V7 candidates:', len(picks))
for p in picks[:5]: print(p['ts_code'], p['score'])
"
```
Expected: 无异常；打印候选数（三门叠加后可能明显少于 CHAN，属预期）。**若候选长期为 0，回报用户评估放宽为双门。**

- [ ] **Step 5: 全量测试 + lint/type gate**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/mypy src
```
Expected: 全绿（保持仓库既有的 mypy 0 错误基准）。

- [ ] **Step 6: Commit**

```bash
git add scripts/seed_chan_v7.py src/zhice_os_v2/scheduler_jobs.py
git commit -m "feat(chan-v7): seed strategy/version and wire into scheduler (STAGING)"
```

---

## Self-Review 结果（作者已核对）

- **Spec 覆盖**：第1项→Task1；第2项(三门)→Task5/6；第3项(止损5%+时间止损)→Task2/3/4；第4项(动态出场)→Task7；版本落地→Task8；对照组回归在 Task5。
- **Placeholder 扫描**：Task1 与 Task7-Step7、Task8-Step3 的"具体修法/job 与 scheduler 清单照现有模式"是有意的（诊断结论未知 / 需对齐 scheduler 现有结构），非代码占位符——每步均有可执行命令或明确落点与过滤条件。
- **类型一致**：`count_trading_days(calendar, opened, as_of)`、`_time_stop_hit(cal, opened, as_of, max_hold_days)`、`price_breaks_pivot_low(zs_list, current_price)`、`evaluate_position_pivot_exit(ts_code, df, current_price)`、exit_kind `TIME_STOP`/`CHAN_EXIT`、中枢下沿字段 `zd`——全计划一致。

## 验收（全部完成后）

CHAN(对照) vs CHAN_V7 并行积累数据后，用 `v2_backtest_run` / 归因表对比胜率、盈亏比、总收益、平均持仓、"3–10 天死亡地带"亏损占比、信号数量。目标：V7 盈亏比与总收益优于 v1，死亡地带亏损显著收窄。
