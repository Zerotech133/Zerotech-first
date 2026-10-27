# -*- coding: utf-8 -*-
"""G3+G4 投研会审三席 + combo persona_neutral 第三例测试
（WO-QT-20261020-083，先红后绿，tmp 隔离，纯标准库确定性规则引擎）。

覆盖验收口径：
  1. 三席各出意见：同一股票池 v1 输入，三分析师席（技术/价值/风控）
     各自产出评级意见（persona 各异只影响权重/风格参数）；
  2. 聚合结论可复算：加权聚合为确定性纯函数——同输入两次运行
     JSON 全等，且聚合分可由三席分按 seat_weights 手工复算；
  3. 股票池 v2 账本 append-only：两次会审追加，首行字节不变，
     不 update 不 delete；
  4. persona_neutral 第三例：注册面三实例在册、与既有两实例隔离
     （既有两实例字段零变化），工厂路径可零代码注册且重复拒绝；
  5. 影子期红线：会审模块零 import 执行链（AST 级断言），新 combo
     只注册不触 engine.exec_run / signal_consumer / 交易链。
"""

import ast
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "paper"))
sys.path.insert(0, str(HERE / "tests"))

import combo_registry  # noqa: E402

WO = "WO-QT-20261020-083"

# ---------- 造股票池 v1 测试输入（合成小池，证据字段对齐真实 schema） ----------


def _stock(code, name, **kw):
    base = {
        "code": code,
        "name": name,
        "industry": None,
        "close": 10.0,
        "pct_chg": 1.0,
        "sources": [],
        "n_hit": 1,
        "tier": "T3",
        "env_diff": False,
        "panic_buy": False,
        "q_toxic": False,
        "q_first_buy": False,
    }
    base.update(kw)
    return base


def _picks(stocks, scan_run_date="2026-10-19"):
    return {
        "generated_at": "2026-10-19 15:40:09",
        "scan_run_date": scan_run_date,
        "trade_basis": "测试口径",
        "strategies": {},
        "t1_gate": {"ok": True, "failed": [], "note": ""},
        "counts": {"total": len(stocks)},
        "stocks": stocks,
    }


def _sample_stocks():
    return [
        # 三重利好：缠论买点 + 九维出分 + T2 → 三席一致看多
        _stock(
            "000001.SZ",
            "平安甲",
            tier="T2",
            n_hit=2,
            sources=[
                {"s": "chanlun", "tag": "1买", "dt": "2026-10-01"},
                {"s": "ninedim", "tag": "九维出分", "dt": "2026-10-16"},
            ],
        ),
        # 风控否决样：缠论买点但 q_toxic + weak 深度垫底
        _stock(
            "000002.SZ",
            "平安乙",
            sources=[
                {"s": "chanlun", "tag": "2买", "dt": "2026-10-01"},
                {"s": "weak", "tag": "深度垫底", "dt": "2026-10-16"},
            ],
            q_toxic=True,
        ),
        # 平庸样：仅九维候选影子，无强证据
        _stock(
            "000003.SZ",
            "平安丙",
            sources=[
                {"s": "ninedim", "tag": "九维候选(影子未出分)", "dt": "2026-10-16"}
            ],
        ),
    ]


# ---------- 验收①：三席各出意见 ----------


def test_three_seats_each_produce_opinion():
    import invest_committee as ic

    session = ic.run_session(_picks(_sample_stocks()), now="2026-10-20T09:00:00")
    assert session["wo"] == WO
    seat_ids = [s["seat_id"] for s in session["seats"]]
    assert len(seat_ids) == 3 and len(set(seat_ids)) == 3
    for rev in session["reviews"]:
        ops = rev["opinions"]
        assert [o["seat_id"] for o in ops] == seat_ids  # 三席齐全
        for o in ops:
            assert o["rating"] in ("BUY", "HOLD", "AVOID")
            assert 0.0 <= o["score"] <= 1.0
            assert o["reasons"], "意见须附确定性理由"
    # persona 相异实证：同一只票三席分不全等（样票证据使然）
    rev0 = session["reviews"][0]
    scores = {o["seat_id"]: o["score"] for o in rev0["opinions"]}
    assert len(set(scores.values())) >= 2


# ---------- 验收②：聚合结论可复算（确定性） ----------


def test_aggregate_recomputable_deterministic():
    import invest_committee as ic

    picks = _picks(_sample_stocks())
    s1 = ic.run_session(picks, now="2026-10-20T09:00:00")
    s2 = ic.run_session(picks, now="2026-10-20T09:00:00")
    assert json.dumps(s1, sort_keys=True, ensure_ascii=False) == json.dumps(
        s2, sort_keys=True, ensure_ascii=False
    )
    w = s1["seat_weights"]
    for rev in s1["reviews"]:
        manual = round(sum(o["score"] * w[o["seat_id"]] for o in rev["opinions"]), 4)
        assert rev["aggregate"]["score"] == manual
        # 精选判定 = 聚合分过线 且 风控席非 AVOID（聚合稳健联判；原 persona 已去人格化）
        risk = [o for o in rev["opinions"] if o["seat_id"] == ic.RISK_SEAT_ID][0]
        expect_sel = (
            rev["aggregate"]["score"] >= s1["v2_floor"] and risk["rating"] != "AVOID"
        )
        assert rev["aggregate"]["selected"] == expect_sel
    # 样票 000002.SZ（q_toxic）必被风控席 AVOID 拦在 v2 外
    rev_b = [r for r in s1["reviews"] if r["code"] == "000002.SZ"][0]
    risk_b = [o for o in rev_b["opinions"] if o["seat_id"] == ic.RISK_SEAT_ID][0]
    assert risk_b["rating"] == "AVOID"
    assert rev_b["aggregate"]["selected"] is False
    assert set(s1["summary"]["codes_selected"]).issubset(
        {s["code"] for s in picks["stocks"]}
    )


# ---------- 验收②补强（主会话验收补）：高分但风控 AVOID 必被一票否决 ----------
# 样票与 000001 同证据强度（聚合分过线）但 q_toxic → 风控席 AVOID；
# 聚合稳健联判下 selected 必为 False。该用例使"一票否决"逻辑可被
# 变异区分（拆 risk_ok 否决 → 本用例红），防否决条款沦为冗余保险。


def test_high_score_but_risk_avoid_vetoed():
    import invest_committee as ic

    stock = _stock(
        "000004.SZ",
        "平安丁",
        tier="T2",
        n_hit=2,
        sources=[
            {"s": "chanlun", "tag": "1买", "dt": "2026-10-01"},
            {"s": "ninedim", "tag": "九维出分", "dt": "2026-10-16"},
        ],
        q_toxic=True,
    )
    picks = _picks(
        [
            _stock(
                "000001.SZ",
                "平安甲",
                tier="T2",
                n_hit=2,
                sources=[
                    {"s": "chanlun", "tag": "1买", "dt": "2026-10-01"},
                    {"s": "ninedim", "tag": "九维出分", "dt": "2026-10-16"},
                ],
            ),
            stock,
        ]
    )
    s = ic.run_session(picks, now="2026-10-20T09:00:00")
    rev = [r for r in s["reviews"] if r["code"] == "000004.SZ"][0]
    risk = [o for o in rev["opinions"] if o["seat_id"] == ic.RISK_SEAT_ID][0]
    assert risk["rating"] == "AVOID"
    # 聚合分确已过精选线（否则本用例对否决条款不可区分）
    assert rev["aggregate"]["score"] >= s["v2_floor"]
    assert rev["aggregate"]["selected"] is False


# ---------- 验收③：v2 账本 append-only ----------


def test_v2_ledger_append_only(tmp_path):
    import invest_committee as ic

    ledger = tmp_path / "pool_v2_sessions.jsonl"
    picks = _picks(_sample_stocks())
    # 轮次计数依赖账本状态：run_session 传同一 ledger_path（确定性）
    r1 = ic.run_session(picks, now="2026-10-20T09:00:00", ledger_path=ledger)
    ic.append_session(r1, ledger_path=ledger)
    first_bytes = ledger.read_bytes()
    r2 = ic.run_session(picks, now="2026-10-20T15:30:00", ledger_path=ledger)
    ic.append_session(r2, ledger_path=ledger)
    # 首行字节级不变（append-only：不 update 不 delete）
    assert ledger.read_bytes().startswith(first_bytes)
    lines = [
        json.loads(x)
        for x in ledger.read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    assert len(lines) == 2
    # 轮次化 session_id：同 scan_run_date 第二轮 r2
    assert r1["session_id"].endswith(".r1")
    assert r2["session_id"].endswith(".r2")
    assert ic.read_sessions(ledger_path=ledger)[0]["session_id"] == r1["session_id"]


# ---------- 验收④：persona_neutral 第三例注册与隔离 ----------

NEUTRAL_COMBO_ID = "combo_zhongxing"


def test_persona_neutral_registered_and_isolated():
    reg = combo_registry.load_registry()  # 生产注册面（k_stock/paper/combos.json）
    combos = {c["combo_id"]: c for c in reg["combos"]}
    assert set(combos) == {"combo_wenjian", "combo_jijin", NEUTRAL_COMBO_ID}
    # 既有两实例零变化（隔离红线：本工单不改其任何字段）
    assert combos["combo_wenjian"]["quad"]["persona"]["id"] == "persona_conservative"
    assert combos["combo_wenjian"]["quad"]["persona"]["params"] == {
        "risk_appetite": "conservative",
        "conf_floor": 0.70,
        "max_pos_pct": 0.15,
        "max_total_pct": 0.60,
    }
    assert combos["combo_jijin"]["quad"]["persona"]["id"] == "persona_aggressive"
    assert combos["combo_jijin"]["quad"]["persona"]["params"] == {
        "risk_appetite": "aggressive",
        "conf_floor": 0.50,
        "max_pos_pct": 0.30,
        "max_total_pct": 0.90,
    }
    # 第三例 persona_neutral：口径一致（四元组+persona v1 参数齐全）
    neutral = combos[NEUTRAL_COMBO_ID]
    assert neutral["quad"]["persona"]["id"] == "persona_neutral"
    p = neutral["quad"]["persona"]["params"]
    assert (
        0 <= p["conf_floor"] <= 1
        and 0 < p["max_pos_pct"] <= 1
        and 0 < p["max_total_pct"] <= 1
    )
    for key in combo_registry.QUAD_KEYS:
        assert neutral["quad"][key]["id"] and neutral["quad"][key]["version"]


def test_persona_neutral_factory_zero_code_and_dup_rejected(tmp_path):
    """松耦合实证：tmp 注册表 + 注册文件经 create_combo 零代码注册第三例。"""
    import combo_factory

    reg_path = tmp_path / "combos.json"
    reg_path.write_text(
        json.dumps(
            {
                "registry_schema_version": 1,
                "combos": [
                    c
                    for c in combo_registry.load_registry()["combos"]
                    if c["combo_id"] != NEUTRAL_COMBO_ID
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    reg_file = tmp_path / "neutral_registration.json"
    reg_file.write_text(
        json.dumps(
            {
                "factory_schema_version": 1,
                "combo_id": NEUTRAL_COMBO_ID,
                "name": "中性组合（虚拟）",
                "quad": {
                    "pick_source": {"id": "poc_run", "version": "v1"},
                    "persona": {
                        "id": "persona_neutral",
                        "version": "v1",
                        "params": {
                            "risk_appetite": "neutral",
                            "conf_floor": 0.60,
                            "max_pos_pct": 0.20,
                            "max_total_pct": 0.75,
                        },
                    },
                    "watch_style": {"id": "daily_close", "version": "v1"},
                    "trader": {"id": "trader_main", "version": "v1"},
                },
                "budget": {"monthly_llm_cost_budget": 10.0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    sub_log = tmp_path / "subs.jsonl"
    cred = combo_factory.create_combo(
        reg_file, registry_path=reg_path, subscription_log=sub_log
    )
    assert cred["status"] == "ready"
    assert cred["quad"]["persona"]["id"] == "persona_neutral"
    # 隔离：既有两实例条目原文不动
    after = {c["combo_id"]: c for c in combo_registry.load_registry(reg_path)["combos"]}
    assert len(after) == 3
    assert after["combo_wenjian"]["quad"]["persona"]["params"]["conf_floor"] == 0.70
    assert after["combo_jijin"]["quad"]["persona"]["params"]["conf_floor"] == 0.50
    # 重复注册拒绝（closed on failure）
    with pytest.raises(combo_factory.DuplicateComboError):
        combo_factory.create_combo(
            reg_file, registry_path=reg_path, subscription_log=sub_log
        )


# ---------- 验收⑤：影子期红线——零 import 执行链 ----------

FORBIDDEN_IMPORTS = {
    "signal_consumer",
    "engine",
    "combo_factory",
    "combo_review",
    "gaorta_client",
    "trade_guard",
    "decision_snapshot",
    "portfolio",
}


def _module_imports(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_shadow_period_no_execution_chain_import():
    src_path = HERE / "paper" / "invest_committee.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    # AST 级断言：import 面零执行链模块
    imports = _module_imports(src_path)
    bad = imports & FORBIDDEN_IMPORTS
    assert not bad, f"影子期红线：会审模块不得 import 执行链模块 {bad}"
    # 代码标识符级断言（剥离模块 docstring 后）：不得出现执行链调用词
    identifiers = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.Name):
            identifiers.add(node.id)
    for token in ("exec_run", "place_order", "consume", "normalize_signal"):
        assert token not in identifiers, f"影子期红线：代码不得调用执行链入口 {token}"
    # 字符串常量级断言（结构化跳过模块 docstring 节点）：不引用执行链路径/库文件
    doc_node = (
        tree.body[0].value
        if tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        else None
    )
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node is not doc_node
        ):
            for token in ("signal_pool", "paper.db", "exec_run"):
                assert token not in node.value, (
                    f"影子期红线：代码字符串不得引用执行链 {token}"
                )


# ==========================================================================
# WO-QT-20261021-091：聚合席去人格化（聚合改无名确定性引擎；鲁肃归属经
# 2026-10-27 判例更正=D1 量化交易部最终决策官，不在通信部）
# 验收口径：①组织者无任何人格字段 ②场次/纪要全文无人名残留
#          ③聚合数值口径零变化（逻辑未动）④persona_registry 判例口径
# ==========================================================================

NAMING_WO = "WO-QT-20261021-091"


def test_wo091_organizer_is_anonymous_engine():
    """组织者=无名确定性引擎：无 zi/字号等人格字段，id/kind 显式声明机器属性。"""
    import invest_committee as ic

    assert ic.ORGANIZER["id"] == "anonymous_engine"
    assert ic.ORGANIZER["kind"] == "deterministic_engine"
    assert "zi" not in ic.ORGANIZER, "组织者不得携带字号（人格字段）"
    assert "子敬" not in json.dumps(ic.ORGANIZER, ensure_ascii=False)
    assert "鲁肃" not in json.dumps(ic.ORGANIZER, ensure_ascii=False)
    # 席权/精选线/风控席原值保留（改名不动数）
    assert ic.SEAT_WEIGHTS == {
        "seat_jishu": 0.40,
        "seat_jiazhi": 0.30,
        "seat_fengkong": 0.30,
    }
    assert ic.V2_FLOOR == 0.62
    assert ic.RISK_SEAT_ID == "seat_fengkong"


def test_wo091_session_has_no_persona_naming():
    """整场会审 JSON 无人名残留；minutes.host 为引擎名；带命名修订单号留痕。"""
    import invest_committee as ic

    s = ic.run_session(_picks(_sample_stocks()), now="2026-10-21T10:00:00")
    blob = json.dumps(s, ensure_ascii=False)
    for bad in ("鲁肃", "子敬", "lu_su"):
        assert bad not in blob, f"场次残留人格命名: {bad}"
    assert s["naming_revision"] == NAMING_WO
    assert s["wo"] == WO  # 建单号不变（083 建的模块）
    assert s["minutes"]["host"] == "确定性聚合引擎"
    assert "确定性聚合引擎" in s["minutes"]["conclusion"]


def test_wo091_aggregate_numeric_unchanged():
    """聚合数值口径零变化：按 0.40/0.30/0.30 手工复算必与实现全等。

    本用例对「改名顺手改了权重/线值」类回归承重（权重改则红）。
    """
    import invest_committee as ic

    ops = [
        {"seat_id": "seat_jishu", "score": 0.80, "rating": "BUY"},
        {"seat_id": "seat_jiazhi", "score": 0.60, "rating": "HOLD"},
        {"seat_id": "seat_fengkong", "score": 0.50, "rating": "HOLD"},
    ]
    got = ic.aggregate(ops)
    expect = round(0.80 * 0.40 + 0.60 * 0.30 + 0.50 * 0.30, 4)  # 0.65
    assert got["score"] == expect == 0.65
    assert got["rating"] == "SELECT" and got["selected"] is True
    # 风控席 AVOID 一票否决仍在（改名不动否决条款）
    ops_veto = [dict(o) for o in ops]
    ops_veto[2]["rating"] = "AVOID"
    got2 = ic.aggregate(ops_veto)
    assert got2["score"] == expect, "否决只影响 selected，不改聚合分"
    assert got2["selected"] is False


def test_wo091_persona_registry_annotated():
    """人格册鲁肃条目带 091 标注 + 2026-10-27 判例口径；量化职能已剥离、通信部归属不成立。"""
    reg = json.loads(
        (HERE.parent / "portal" / "persona_registry.json").read_text(encoding="utf-8")
    )
    dep = [x for x in reg["departments"] if x.get("name") == "鲁肃"][0]
    assert dep["naming_revision"] == NAMING_WO
    assert dep.get("service_line") == "invest_decision"
    assert "阚泽" in dep["naming_note"], "命名注须说明通信部主官=阚泽"
    assert "鲁肃在通信" not in dep["naming_note"], "旧「鲁肃在通信部」口径须已清除"
    assert "2026-10-27_鲁肃与阚泽定名判例" in dep["naming_note"]
    assert "去人格化" in dep["duties"] or "无名" in dep["duties"]
    assert "二次筛选" not in dep["duties"], "量化聚合职能须已从该条目剥离"


def test_wo091_no_persona_leak_in_module_code():
    """代码层无残留：源码（剥离 docstring）不得再出现人名组织者。"""
    tree = ast.parse(
        (HERE / "paper" / "invest_committee.py").read_text(encoding="utf-8")
    )
    doc_node = (
        tree.body[0].value
        if tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        else None
    )
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node is not doc_node
        ):
            assert "鲁肃" not in node.value, (
                f"代码字符串残留人名组织者: {node.value[:40]}"
            )
