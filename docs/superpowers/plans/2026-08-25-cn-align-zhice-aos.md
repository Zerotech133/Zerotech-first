# CN 市场对齐云端 zhice-aos 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把本地 v2 CN 板块与云端 zhice-aos 对齐的 4 处差距（候选池 N、每策略计划 top-3、盘中按策略出场、冲突裁决时序）落地。

**Architecture:** 本地 CN 已是云端同源移植，本计划只做参数与接线级对齐，不改缠论引擎/入场/调度/取价。核心改动集中在 `application/services/workflow_service.py`（盘前建计划循环 + 盘中出场循环）与 `scheduler_jobs.py`（盘前传参）。

**Tech Stack:** Python 3.12、SQLAlchemy、pytest、ruff、InMemoryDatabase 测试桩、Decimal 金额口径。

设计依据：`docs/superpowers/specs/2026-08-25-cn-align-zhice-aos-design.md`。

**关键前提（执行前必读）：**
- 数值口径：**止损数值一律不改**（CHAN 保持 6%），价格带保持 3~500。改动 C 只把 `run_intraday` 接到 `exit_policies` 表并采用云端"现价跟随"移动止损语义。
- ⚠️ 改动 C 含一处**行为变化**：移动止损从"锁定到开仓价×(1+4%)"改为云端语义"现价×(1−trail_pct)"。已在设计中获批。

---

## 文件结构

| 文件 | 职责 | 本计划改动 |
|---|---|---|
| `src/zhice_os_v2/scheduler_jobs.py` | 盘前 job 编排 | 改动 A：CN 传 `max_candidates=20` |
| `src/zhice_os_v2/application/services/workflow_service.py` | 盘前建计划循环 + 盘中出场循环 | 改动 B（top-3）、C（出场接线）、D（冲突时序） |
| `src/zhice_os_v2/domain/strategy/exit_policies.py` | 按策略出场参数表 | 只读消费，不改数值 |
| `tests/test_pre_market_align.py` | 新增：改动 A/B/D 单测 | 新建 |
| `tests/test_intraday.py` | 盘中出场单测 | 追加改动 C 单测 |

---

## Task 0: 建特性分支

- [ ] **Step 1: 从 main 建分支**

Run:
```bash
cd /Users/join/Desktop/zhice/zhice-os-v2
git checkout -b feat/cn-align-zhice-aos
```
Expected: `Switched to a new branch 'feat/cn-align-zhice-aos'`

---

## Task A: 盘前候选池 N 10→20（CN）

**Files:**
- Modify: `src/zhice_os_v2/scheduler_jobs.py:104-106`（`run_pre_market_job` 调用 `run_pre_market` 处）
- Test: `tests/test_pre_market_align.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pre_market_align.py`：
```python
"""CN 盘前对齐云端单测：候选池 N=20 / 每策略计划 top-3 / 冲突裁决时序。"""
from zhice_os_v2 import scheduler_jobs as sj


def test_pre_market_job_cn_passes_max_candidates_20(monkeypatch):
    captured = {}

    def fake_run_pre_market(self, market, trade_date, strategy_types=None, max_candidates=10, **kw):
        captured["max_candidates"] = max_candidates
        captured["market"] = market
        return {"market": market, "candidates": 0, "plans": [], "approvals": [], "pools": []}

    monkeypatch.setattr(sj, "_active_strategy_types", lambda market: ["TREND"])
    monkeypatch.setattr(
        "zhice_os_v2.application.services.workflow_service.WorkflowService.run_pre_market",
        fake_run_pre_market,
    )
    sj.run_pre_market_job("CN")
    assert captured["max_candidates"] == 20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pre_market_align.py::test_pre_market_job_cn_passes_max_candidates_20 -v`
Expected: FAIL（当前 `run_pre_market_job` 不传 max_candidates，默认 10 ≠ 20）

- [ ] **Step 3: Write minimal implementation**

在 `scheduler_jobs.py` 的 `run_pre_market_job` 中，把调用改为按市场传 N（CN=20）：
```python
    result = WorkflowService(_db()).run_pre_market(
        market, date.today(), strategy_types=strategy_types,
        max_candidates=20 if market == "CN" else 10,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_pre_market_align.py::test_pre_market_job_cn_passes_max_candidates_20 -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_pre_market_align.py src/zhice_os_v2/scheduler_jobs.py
git commit -m "feat(盘前): CN 候选池 N 10→20 对齐云端"
```

---

## Task B: 每策略计划 top-3 截断

**Files:**
- Modify: `src/zhice_os_v2/application/services/workflow_service.py:539-540`（建计划循环 `for st, candidates in strategy_picks.items():` 内层 `for c in candidates:`）
- Test: `tests/test_pre_market_align.py`

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_pre_market_align.py`：
```python
from datetime import date
from decimal import Decimal

from zhice_os_v2.application.services.workflow_service import WorkflowService
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase


def test_pre_market_builds_top3_plans_per_strategy(monkeypatch):
    """一个策略给 5 个候选（不同 score），只应对 score 最高的前 3 个建计划。"""
    import zhice_os_v2.application.services.workflow_service as wf

    picks = [{"ts_code": f"00000{i}.SZ", "score": float(i), "reason": "r"} for i in range(5)]

    monkeypatch.setattr(wf, "_sv_for_type", lambda market, st: "sv-cn-chan")
    monkeypatch.setattr(wf, "_strategy_type_status", lambda market, sv: "ACTIVE")
    monkeypatch.setattr(
        "zhice_os_v2.application.services.strategy_registry_service.StrategyRegistryService.run",
        lambda self, st, td, max_candidates=10, market=None: picks,
    )
    monkeypatch.setattr(
        "zhice_os_v2.application.services.regime_service.route_strategies",
        lambda market, types, status: {"enabled": types, "regime_info": {"regime": "SIDEWAYS"}},
    )
    monkeypatch.setattr("zhice_os_v2.api.routers.positions._get_latest_prices",
                        lambda codes, market: {p["ts_code"]: 10.0 for p in picks})
    monkeypatch.setattr(
        "zhice_os_v2.application.services.conflict_resolver_service.resolve_plan_conflicts",
        lambda market: {"cancelled_plan_ids": []},
    )
    monkeypatch.setattr(
        "zhice_os_v2.application.services.position_sizing_service.size_new_position",
        lambda db, market, sv, code, price: Decimal("100"),
    )
    db = InMemoryDatabase()
    svc = WorkflowService(db)
    result = svc.run_pre_market("CN", date.today(), strategy_types=["CHAN"])
    built = [p for p in result["plans"]]
    assert len(built) == 3
    # 应是 score 最高的 3 个：004/003/002
    built_codes = {p["ts_code"] if isinstance(p, dict) else p.ts_code for p in built}
    assert built_codes == {"000004.SZ", "000003.SZ", "000002.SZ"}
```

> 注：若 `result["plans"]` 元素结构不同（对象 vs dict），执行时按实际返回结构调整取 `ts_code` 的方式；断言核心是「数量=3 且为 score top-3」。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pre_market_align.py::test_pre_market_builds_top3_plans_per_strategy -v`
Expected: FAIL（当前无 top-3，5 个候选全建计划 → len==5）

- [ ] **Step 3: Write minimal implementation**

`workflow_service.py`：把内层循环由遍历全部候选改为「按 score 降序取前 3」。原：
```python
        for st, candidates in strategy_picks.items():
            for c in candidates:
                if c["ts_code"] in held:
                    continue
```
改为：
```python
        for st, candidates in strategy_picks.items():
            # 每策略计划 top-3（对齐云端 build_trade_plans_for_pool max_plans=3）：
            # 按 candidate_score 降序取前 3 进入建计划，其余留在候选池不建计划。
            top3 = sorted(candidates, key=lambda c: c.get("score", 0), reverse=True)[:3]
            for c in top3:
                if c["ts_code"] in held:
                    continue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_pre_market_align.py::test_pre_market_builds_top3_plans_per_strategy -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_pre_market_align.py src/zhice_os_v2/application/services/workflow_service.py
git commit -m "feat(盘前): 每策略计划 top-3 截断对齐云端"
```

---

## Task C: 盘中出场按策略读 ExitPolicy（含现价跟随移动止损）

**Files:**
- Modify: `src/zhice_os_v2/application/services/workflow_service.py:918-957`（`run_intraday` 止损/止盈/移动止损段）
- Test: `tests/test_intraday.py`（追加）

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_intraday.py`（顶部已有 `_lot`/`_make_service`/`SV`/`MARKET`/`CODE`）：
```python
from zhice_os_v2.domain.strategy.exit_policies import ExitPolicy


def _patch_policy(monkeypatch, policy):
    monkeypatch.setattr(wf, "get_exit_policy_for_version",
                        lambda sv_id, db: policy, raising=False)


def test_take_profit_uses_strategy_policy(monkeypatch):
    """CHAN 止盈 18%：现价 = 开仓价 ×1.18 触发止盈（旧硬编码 20% 不会触发）。"""
    policy = ExitPolicy(stop_loss_pct=Decimal("0.06"), take_profit_pct=Decimal("0.18"),
                        use_trailing=True, trailing_activate_pct=Decimal("0.05"),
                        trailing_trail_pct=Decimal("0.035"))
    lot = _lot(price="10")
    db, svc, _ = _make_service(monkeypatch, [lot],
                               prices={CODE: 11.8}, pre_closes={CODE: 10.0})
    _patch_policy(monkeypatch, policy)
    r = svc.run_intraday(MARKET)
    assert r["take_profit_triggered"] == [CODE]


def test_trailing_uses_current_price_semantics(monkeypatch):
    """移动止损现价跟随：浮盈≥activate 时 new_stop = 现价×(1−trail_pct)。"""
    policy = ExitPolicy(stop_loss_pct=Decimal("0.06"), take_profit_pct=Decimal("0.50"),
                        use_trailing=True, trailing_activate_pct=Decimal("0.05"),
                        trailing_trail_pct=Decimal("0.035"))
    lot = _lot(price="10")
    db, svc, saved = _make_service(monkeypatch, [lot],
                                   prices={CODE: 11.0}, pre_closes={CODE: 10.0})
    _patch_policy(monkeypatch, policy)
    svc.run_intraday(MARKET)
    # 现价 11，trail 3.5% → 11×0.965 = 10.615
    assert saved and saved[-1][1] == Decimal("11.0") * (Decimal("1") - Decimal("0.035"))


def test_mean_reversion_no_trailing(monkeypatch):
    """use_trailing=False 时不产生移动止损写回。"""
    policy = ExitPolicy(stop_loss_pct=Decimal("0.04"), take_profit_pct=Decimal("0.50"),
                        use_trailing=False, trailing_activate_pct=Decimal("0.03"),
                        trailing_trail_pct=Decimal("0.015"))
    lot = _lot(price="10")
    db, svc, saved = _make_service(monkeypatch, [lot],
                                   prices={CODE: 11.0}, pre_closes={CODE: 10.0})
    _patch_policy(monkeypatch, policy)
    svc.run_intraday(MARKET)
    assert saved == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_intraday.py::test_take_profit_uses_strategy_policy tests/test_intraday.py::test_trailing_uses_current_price_semantics tests/test_intraday.py::test_mean_reversion_no_trailing -v`
Expected: FAIL（当前 tp 用硬编码 20% → 11.8 不触发；trailing 用 open×1.04 而非现价跟随；无 use_trailing 门）

- [ ] **Step 3: Write minimal implementation**

在 `run_intraday` 中，对每个 position 先取策略出场参数，再用其驱动止损/止盈/移动止损。

顶部新增 import（文件已有 `from decimal import Decimal`）：
```python
from zhice_os_v2.domain.strategy.exit_policies import get_exit_policy_for_version
```

把 918-925 段（stop/take_profit 计算）改为：
```python
            # 按持仓所属策略取出场参数（对齐云端 exit_policies；未命中走默认口径）
            policy = get_exit_policy_for_version(position.strategy_version_id, self._db)
            plan_stop, plan_tp = plan_exits.get(position.ts_code, (None, None))
            stop = pos_stops.get(position.position_id) or plan_stop or \
                (position.open_price * (Decimal("1") - policy.stop_loss_pct)
                 if position.open_price else None)
            take_profit = plan_tp or (
                position.open_price * (Decimal("1") + policy.take_profit_pct)
                if position.open_price else None)
```

把 943-957 段（移动止损）改为现价跟随 + use_trailing 门：
```python
            # 移动止损（云端语义）：启用且浮盈≥activate → 止损上移至 现价×(1−trail_pct)，只上移不下移
            if (policy.use_trailing and position.open_price and position.open_price > 0):
                gain = (current - position.open_price) / position.open_price
                if gain >= policy.trailing_activate_pct:
                    new_stop = current * (Decimal("1") - policy.trailing_trail_pct)
                    old_stop = stop if stop is not None else Decimal("0")
                    if new_stop > old_stop:
                        if _save_position_stop(position.position_id, new_stop):
                            stop = new_stop
                            trailing_updated.append({
                                "position_id": position.position_id,
                                "ts_code": position.ts_code,
                                "old_stop": old_stop,
                                "new_stop": new_stop,
                            })
```

删除不再使用的模块常量（若无其他引用）：`TRAILING_ACTIVATE_PCT`、`TRAILING_STOP_PCT`、`DEFAULT_STOP_LOSS_PCT`、`DEFAULT_TAKE_PROFIT_PCT`（workflow_service.py:24-27）。执行时先 `grep -n "TRAILING_ACTIVATE_PCT\|TRAILING_STOP_PCT\|DEFAULT_STOP_LOSS_PCT\|DEFAULT_TAKE_PROFIT_PCT" src/` 确认无其他引用再删；若有引用则保留常量、不强删。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_intraday.py -v`
Expected: PASS（新增 3 例 + 原有盘中用例全绿；原有移动止损用例若断言 open×1.04 需同步更新为现价跟随口径）

> 注：原 `test_intraday.py` 若已有断言旧「open×(1+4%)」移动止损值的用例，执行时同步改为现价跟随口径（`现价×(1−trail_pct)`），因这是设计内的行为对齐。

- [ ] **Step 5: Commit**

```bash
git add tests/test_intraday.py src/zhice_os_v2/application/services/workflow_service.py
git commit -m "feat(盘中): 出场按策略读 ExitPolicy + 现价跟随移动止损对齐云端"
```

---

## Task D: 冲突裁决移到计划全落库后

**Files:**
- Modify: `src/zhice_os_v2/application/services/workflow_service.py:528-537`（把 `resolve_plan_conflicts` 块下移）
- Test: `tests/test_pre_market_align.py`

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_pre_market_align.py`：
```python
def test_conflict_resolution_runs_after_plans_built(monkeypatch):
    """冲突裁决须在所有计划 create_from_candidate 落库之后调用。"""
    import zhice_os_v2.application.services.workflow_service as wf
    events = []

    picks = [{"ts_code": "000001.SZ", "score": 1.0, "reason": "r"}]
    monkeypatch.setattr(wf, "_sv_for_type", lambda market, st: "sv-cn-chan")
    monkeypatch.setattr(wf, "_strategy_type_status", lambda market, sv: "ACTIVE")
    monkeypatch.setattr(
        "zhice_os_v2.application.services.strategy_registry_service.StrategyRegistryService.run",
        lambda self, st, td, max_candidates=10, market=None: picks,
    )
    monkeypatch.setattr(
        "zhice_os_v2.application.services.regime_service.route_strategies",
        lambda market, types, status: {"enabled": types, "regime_info": {"regime": "SIDEWAYS"}},
    )
    monkeypatch.setattr("zhice_os_v2.api.routers.positions._get_latest_prices",
                        lambda codes, market: {"000001.SZ": 10.0})
    monkeypatch.setattr(
        "zhice_os_v2.application.services.position_sizing_service.size_new_position",
        lambda db, market, sv, code, price: __import__("decimal").Decimal("100"),
    )
    orig_create = wf.TradePlanService.create_from_candidate
    def spy_create(self, *a, **k):
        events.append("create")
        return orig_create(self, *a, **k)
    monkeypatch.setattr(wf.TradePlanService, "create_from_candidate", spy_create)
    monkeypatch.setattr(
        "zhice_os_v2.application.services.conflict_resolver_service.resolve_plan_conflicts",
        lambda market: events.append("resolve") or {"cancelled_plan_ids": []},
    )
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase
    WorkflowService = wf.WorkflowService
    WorkflowService(InMemoryDatabase()).run_pre_market("CN", date.today(), strategy_types=["CHAN"])
    assert "create" in events and "resolve" in events
    assert events.index("resolve") > max(i for i, e in enumerate(events) if e == "create")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pre_market_align.py::test_conflict_resolution_runs_after_plans_built -v`
Expected: FAIL（当前 `resolve` 在 build 循环前 → resolve 的 index < create）

- [ ] **Step 3: Write minimal implementation**

在 `workflow_service.py` 中，把 528-537 的冲突裁决块（`from ... import resolve_plan_conflicts` 到 `logger.info("pre_market conflicts resolved ...")`）整块**剪切**，粘贴到「`for st, candidates in strategy_picks.items():` 建计划循环结束之后、进入逐计划风控/授权之前」。`held` 集合的构建（526-527）保留在原位（建计划循环之前需要它）。移动后语义：所有策略计划先落库 → 再统一 `resolve_plan_conflicts` → 再风控授权。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_pre_market_align.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_pre_market_align.py src/zhice_os_v2/application/services/workflow_service.py
git commit -m "fix(盘前): 冲突裁决移到计划全落库后对齐云端时序"
```

---

## Task E: 全量回归

- [ ] **Step 1: ruff**

Run: `uv run ruff check src/zhice_os_v2/application/services/workflow_service.py src/zhice_os_v2/scheduler_jobs.py tests/test_pre_market_align.py tests/test_intraday.py`
Expected: `All checks passed!`

- [ ] **Step 2: 全量 pytest**

Run: `uv run pytest -q`
Expected: 全绿（基线 141 + 本计划新增用例；若有既有用例断言旧移动止损口径，按 Task C 注释同步更新）

- [ ] **Step 3: 回归确认价格带未动**

Run: `grep -n "candidate_price_band\|500" src/zhice_os_v2/domain/market/profiles/cn.py`
Expected: 缠论选股价格带仍为 3.0~500.0（未被本次改动影响）

- [ ] **Step 4: Commit（如有 lint 微调）**

```bash
git add -A && git commit -m "chore(CN对齐): ruff + 回归收尾" || echo "无需额外提交"
```

---

## Self-Review（作者自检，已过）

- **Spec 覆盖**：改动 A→Task A、B→Task B、C→Task C、D→Task D，价格带/止损"不动"在 Task E Step3 + Task C 数值不改中体现。全覆盖。
- **占位扫描**：无 TODO/TBD；每个代码步骤含真实代码。
- **类型一致**：`ExitPolicy` 字段（stop_loss_pct/take_profit_pct/use_trailing/trailing_activate_pct/trailing_trail_pct）与 `exit_policies.py` 定义一致；`get_exit_policy_for_version(sv_id, db)` 签名与源文件一致。
- **已知执行期不确定点**（执行者需按实际代码微调，已在对应 Task 注释标注）：① `result["plans"]` 元素结构（dict vs 对象）取 ts_code 方式；② Task D 剪切粘贴的精确行号（按锚点定位）；③ 原 test_intraday 若有旧移动止损口径断言需同步更新。
