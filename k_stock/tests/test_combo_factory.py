# -*- coding: utf-8 -*-
"""WO-QT-20261020-011 验收测试：E-4 组合工厂。

验收口径（工单+契约）：
  1. 新组合创建零代码——全新注册文件（数据）→ create_combo → ready，
     account_ids/订阅条目就位，既有代码 diff 为零；
  2. 成本预算强制（X-5）：缺 budget / budget≤0 → 拒绝创建且不留半成品；
  3. 成本账+超预算：record_combo_cost 累计超预算 → over_budget 标记 +
     guard_dispatch 抛 BudgetExceededError；
  4. 重复 combo_id 拒绝；closed on failure 各路径（文件缺失/JSON 非法/
     schema 版本不符）显式抛错；
  5. 端到端：工厂创建的新组合即刻可战——喂信号 → 激进分支在
     account_id=combo@aggressive_direct 隔离账落单 + trader_action 回执。

未接线路径照契约：hire 真实 Agent 实例化、真实调度消费 guard_dispatch、
真实 token 计量均属后续工单——本文件不为其留桩。
"""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "paper"))
sys.path.insert(0, str(HERE / "tests"))

import combo_factory  # noqa: E402
import combo_registry  # noqa: E402
import strategy_branch as sb  # noqa: E402
from market import FakeMarket  # noqa: E402
from engine import PaperEngine  # noqa: E402

from test_paper_engine import _bar  # noqa: E402
from test_strategy_branch import _signal  # noqa: E402

STOCK = "000002.SZ"


# ---------- fixture 与构造 ----------


def _base_registry(tmp_path) -> Path:
    """拷贝默认注册表到临时路径（工厂写副本，不污染默认 combos.json）。"""
    dst = tmp_path / "registry.json"
    dst.write_text(
        json.dumps(combo_registry.load_registry(), ensure_ascii=False), encoding="utf-8"
    )
    return dst


def _registration(tmp_path, combo_id="combo_factory_e2e", budget=100.0, **drop) -> Path:
    """一份全新组合注册文件（纯数据，零代码）。drop 可弹出字段构造违例。"""
    reg = {
        "factory_schema_version": 1,
        "combo_id": combo_id,
        "name": "工厂端到端组合（测试）",
        "quad": {
            "pick_source": {"id": "src_e2e", "version": "v1"},
            "persona": {
                "id": "persona_e2e",
                "version": "v1",
                "params": {
                    "risk_appetite": "balanced",
                    "conf_floor": 0.55,
                    "max_pos_pct": 0.25,
                    "max_total_pct": 0.80,
                },
            },
            "watch_style": {"id": "watch_e2e", "version": "v1"},
            "trader": {"id": "trader_e2e", "version": "v1"},
        },
        "budget": {"monthly_llm_cost_budget": budget},
    }
    for k in drop.pop("drop", ()):
        reg.pop(k, None)
    p = tmp_path / f"reg_{combo_id}.json"
    p.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def factory_env(tmp_path):
    """隔离三账：注册表副本/订阅账/成本账全部注入临时路径。"""
    return {
        "registry_path": _base_registry(tmp_path),
        "subscription_log": tmp_path / "subs.jsonl",
        "ledger": tmp_path / "budget.jsonl",
    }


# ---------- 验收①：零代码创建 ----------


def test_create_combo_zero_code_ready(factory_env):
    out = combo_factory.create_combo(
        _registration(factory_env["registry_path"].parent),
        registry_path=factory_env["registry_path"],
        subscription_log=factory_env["subscription_log"],
    )
    assert out["status"] == "ready"
    assert out["combo_id"] == "combo_factory_e2e"
    # 隔离账本标识对齐 -008：combo@分支
    assert (
        out["account_ids"]["aggressive_direct"] == "combo_factory_e2e@aggressive_direct"
    )
    assert out["account_ids"]["follow_research"] == "combo_factory_e2e@follow_research"
    # 复盘订阅落账且消费口指向 -007/-008 只读函数
    subs = combo_factory.load_subscriptions(
        factory_env["subscription_log"], combo_id="combo_factory_e2e"
    )
    assert len(subs) == 1
    assert set(subs[0]["consumes"]) == set(combo_factory.REVIEW_CONSUMERS)
    # 注册表确实新增条目（且携带 budget 供预算状态读取）
    reg = combo_registry.load_registry(factory_env["registry_path"])
    entry = combo_registry.resolve(reg, "combo_factory_e2e")
    assert entry["budget"]["monthly_llm_cost_budget"] == 100.0


# ---------- 验收②：预算强制（X-5，不留半成品） ----------


@pytest.mark.parametrize("budget", [None, 0, -5.0, "100"])
def test_budget_violation_rejected_no_half_state(factory_env, budget):
    tp = factory_env["registry_path"].parent
    kw = {} if budget is None else {"budget": budget}
    reg_path = _registration(tp, combo_id="combo_bad_budget", **kw)
    if budget is None:
        # 显式构造缺 budget 字段
        raw = json.loads(reg_path.read_text(encoding="utf-8"))
        raw.pop("budget")
        reg_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(combo_factory.BudgetViolationError):
        combo_factory.create_combo(
            reg_path,
            registry_path=factory_env["registry_path"],
            subscription_log=factory_env["subscription_log"],
        )
    # 不留半成品：注册表/订阅账均无该 combo
    reg = combo_registry.load_registry(factory_env["registry_path"])
    with pytest.raises(combo_registry.ComboNotFound):
        combo_registry.resolve(reg, "combo_bad_budget")
    assert (
        combo_factory.load_subscriptions(
            factory_env["subscription_log"], combo_id="combo_bad_budget"
        )
        == []
    )


def test_registration_closed_on_failure_paths(tmp_path, factory_env):
    # 文件缺失
    with pytest.raises(combo_factory.ComboFactoryError) as ei:
        combo_factory.create_combo(
            tmp_path / "nope.json",
            registry_path=factory_env["registry_path"],
            subscription_log=factory_env["subscription_log"],
        )
    assert ei.value.reason_code == "registration_missing"
    # JSON 非法
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(combo_factory.ComboFactoryError) as ei:
        combo_factory.create_combo(
            bad,
            registry_path=factory_env["registry_path"],
            subscription_log=factory_env["subscription_log"],
        )
    assert ei.value.reason_code == "registration_invalid_json"
    # schema 版本不符
    reg_path = _registration(tmp_path, combo_id="combo_bad_schema")
    raw = json.loads(reg_path.read_text(encoding="utf-8"))
    raw["factory_schema_version"] = 99
    reg_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(combo_factory.ComboFactoryError) as ei:
        combo_factory.create_combo(
            reg_path,
            registry_path=factory_env["registry_path"],
            subscription_log=factory_env["subscription_log"],
        )
    assert ei.value.reason_code == "schema_version_unsupported"
    # 四元组违例（复用 -003 校验口径）
    reg_path = _registration(tmp_path, combo_id="combo_bad_quad")
    raw = json.loads(reg_path.read_text(encoding="utf-8"))
    raw["quad"].pop("persona")
    reg_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(combo_factory.ComboFactoryError) as ei:
        combo_factory.create_combo(
            reg_path,
            registry_path=factory_env["registry_path"],
            subscription_log=factory_env["subscription_log"],
        )
    assert ei.value.reason_code == "quad_invalid"


def test_duplicate_combo_rejected(factory_env):
    reg_path = _registration(factory_env["registry_path"].parent)
    combo_factory.create_combo(
        reg_path,
        registry_path=factory_env["registry_path"],
        subscription_log=factory_env["subscription_log"],
    )
    with pytest.raises(combo_factory.DuplicateComboError):
        combo_factory.create_combo(
            reg_path,
            registry_path=factory_env["registry_path"],
            subscription_log=factory_env["subscription_log"],
        )


# ---------- 验收③：成本账 + 超预算闸门 ----------


def test_cost_ledger_over_budget_guard(factory_env):
    cid = "combo_factory_e2e"
    combo_factory.create_combo(
        _registration(factory_env["registry_path"].parent, budget=10.0),
        registry_path=factory_env["registry_path"],
        subscription_log=factory_env["subscription_log"],
    )
    # 成本登记：数值校验 closed on failure
    with pytest.raises(combo_factory.BudgetViolationError):
        combo_factory.record_combo_cost(cid, 0, ledger=factory_env["ledger"])
    combo_factory.record_combo_cost(
        cid, 4.0, note="sim-batch-1", ledger=factory_env["ledger"]
    )
    combo_factory.record_combo_cost(
        cid, 6.5, note="sim-batch-2", ledger=factory_env["ledger"]
    )
    st = combo_factory.combo_budget_status(
        cid, registry_path=factory_env["registry_path"], ledger=factory_env["ledger"]
    )
    assert st["cost_accumulated"] == 10.5 and st["over_budget"] is True
    assert st["status"] == "over_budget"
    # 超预算 → 拒绝再派任务（X-5 预留 API）
    with pytest.raises(combo_factory.BudgetExceededError):
        combo_factory.guard_dispatch(
            cid,
            registry_path=factory_env["registry_path"],
            ledger=factory_env["ledger"],
        )


# ---------- 验收⑤：端到端——工厂产物即刻可战 ----------


def test_e2e_factory_combo_feeds_signal(factory_env, tmp_path):
    cid = "combo_factory_e2e"
    combo_factory.create_combo(
        _registration(factory_env["registry_path"].parent),
        registry_path=factory_env["registry_path"],
        subscription_log=factory_env["subscription_log"],
    )
    bars = {
        STOCK: [
            _bar("20261008", open_=99.0, pre_close=100.0, close=100.0),
            _bar("20261009", open_=102.0, pre_close=100.0, close=101.0),
        ]
    }
    e = PaperEngine(tmp_path / "paper.db", FakeMarket(bars))
    e.ensure_account()
    runner = sb.BranchRunner(
        e,
        registry_path=factory_env["registry_path"],
        receipt_log=tmp_path / "receipts.jsonl",
        rejection_log=tmp_path / "rejections.jsonl",
    )
    out = runner.feed_signal(_signal(), cid)
    # 激进分支在隔离账落单（account = combo@aggressive_direct）
    res = out["branches"]["aggressive_direct"]
    assert res["status"] == "placed" and res["receipt_status"] == "recorded"
    orders = e.history(account_id=f"{cid}@aggressive_direct")
    assert orders, "工厂新组合的激进分支应产生隔离账落单"
    # trader_action 回执在共享账（actor 区分分支，-007 契约）
    import event_receipts

    tl = event_receipts.timeline(tmp_path / "receipts.jsonl", _signal()["signal_id"])
    acts = [t for t in tl if t["receipt_kind"] == event_receipts.TRADER_ACTION]
    assert any(t["actor"] == "branch:aggressive_direct" for t in acts)
