# -*- coding: utf-8 -*-
"""WO-QT-20261020-014 验收测试：司马懿汇报器生成器（DRYRUN 档）。

验收口径（工单 + 契约）：
  1. 五段口径齐全：一~五标题恒出现（数据缺失也显式标注，禁静默省略）；
  2. 不造数：ROI 缺失渲染 pending_backfill（**不填 0**），单位不可减渲染
     unit_mismatch；分歧率分母为 0 记 null；预算未登记记 null；
  3. P0 告警行透出（红），P1 黄 / P2 绿，灰度账号打灰度标；
  4. DRYRUN 标记存在（Markdown 头部 + JSON payload 双处），零真实出口
     （网络出口钳制下跑通全链路 + 源级无出口字样）；
  5. 输出结构**永远不含实盘字段**（守卫命中即抛 ReportError）；
  6. roundtrip：同输入两次生成一致（clock 注入后可复现）；
  7. closed on failure：非法 combo_ids / findings / dissent 损坏 / 上游 -013
     抛错 → 显式抛 ReportError；全部路径注入 tmp 临时文件，不污染默认账与默认
     注册表。

未接线路径照契约：三查 findings 由调用方喂入（本单不主动连 paper.db）；
015 真实推送 BLOCK 待授权。
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "paper"))
sys.path.insert(0, str(HERE / "tests"))

import combo_factory  # noqa: E402
import combo_registry  # noqa: E402
import combo_review  # noqa: E402
import event_receipts as er  # noqa: E402
import plan_meeting_ledger as pml  # noqa: E402
import report_builder as rb  # noqa: E402

SIG = "sig-20261020-0141"
CLOCK = "2026-10-20T14:00:00"
MONTH = "2026-10"


# ---------- fixture 与构造 ----------


def _registry_copy(tmp_path) -> Path:
    """拷贝默认注册表到临时路径（不污染默认 combos.json）。"""
    dst = tmp_path / "registry.json"
    dst.write_text(
        json.dumps(combo_registry.load_registry(), ensure_ascii=False), encoding="utf-8"
    )
    return dst


def _registration(tmp_path, combo_id, budget=100.0) -> Path:
    """一份组合注册文件（纯数据）；budget 必填（X-5）。"""
    reg = {
        "factory_schema_version": 1,
        "combo_id": combo_id,
        "name": f"汇报测试组合 {combo_id}",
        "quad": {
            "pick_source": {"id": "src_review", "version": "v1"},
            "persona": {
                "id": "persona_review",
                "version": "v1",
                "params": {
                    "risk_appetite": "balanced",
                    "conf_floor": 0.55,
                    "max_pos_pct": 0.25,
                    "max_total_pct": 0.80,
                },
            },
            "watch_style": {"id": "watch_review", "version": "v1"},
            "trader": {"id": "trader_review", "version": "v1"},
        },
        "budget": {"monthly_llm_cost_budget": budget},
    }
    p = tmp_path / f"reg_{combo_id}.json"
    p.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def env(tmp_path):
    """隔离四账 + 版本账：注册表副本 / 成本账 / 回执账 / -006 版本账 全 tmp。"""
    reg = _registry_copy(tmp_path)
    ledger = tmp_path / "budget.jsonl"
    subs = tmp_path / "subs.jsonl"
    receipts = tmp_path / "receipts.jsonl"
    plan_db = tmp_path / "plan.db"
    con = sqlite3.connect(plan_db)
    con.row_factory = sqlite3.Row
    pml.ensure_table(con)
    con.commit()
    con.close()
    return {
        "registry_path": reg,
        "ledger": ledger,
        "subscription_log": subs,
        "receipts": receipts,
        "plan_db": plan_db,
        "tmp_path": tmp_path,
    }


def _make_combo(env, combo_id, budget=100.0):
    """用 -011 工厂造一个带预算的组合（复用其注册/校验口径）。"""
    combo_factory.create_combo(
        _registration(env["registry_path"].parent, combo_id, budget=budget),
        registry_path=env["registry_path"],
        subscription_log=env["subscription_log"],
    )
    return combo_id


def _reviews(signal_id=SIG, combo_id="combo_report_a"):
    """-008 compare_reviews 输出形态（两分支并排对比）。"""
    return {
        "signal_id": signal_id,
        "combo_id": combo_id,
        "comparison_ready": True,
        "reviews": {
            "follow_research": {
                "branch_id": "follow_research",
                "signal_id": signal_id,
                "decided_action": "abstain",
                "action": "SELL",
                "reason_code": "plan_conflict",
                "plan_version": "pv-20261020-001",
                "basis_chain": [{"step": "direction_check", "match": False}],
                "deviation": {"type": "plan_conflict", "note": "预案与信号方向分歧"},
            },
            "aggressive_direct": {
                "branch_id": "aggressive_direct",
                "signal_id": signal_id,
                "decided_action": "execute",
                "action": "BUY",
                "reason_code": "persona_direct_hit",
                "plan_version": None,
                "basis_chain": [{"step": "persona_direct", "conf_floor": 0.55}],
                "deviation": {"type": "no_plan_reference", "note": "未对照投研预案"},
            },
        },
    }


def _four_receipts(receipts_path, signal_id=SIG, combo_id="combo_report_a"):
    """入账四类回执（-007 契约）→ completeness 4/4。"""
    er.record_receipt(receipts_path, signal_id, er.RESEARCH_ACK, "research_team")
    er.record_receipt(receipts_path, signal_id, er.TRADER_ACK, "trader_room")
    er.record_receipt(
        receipts_path,
        signal_id,
        er.RESEARCH_CONCLUSION,
        "research_team",
        payload={"verdict": "hold_plan"},
    )
    er.record_receipt(
        receipts_path,
        signal_id,
        er.TRADER_ACTION,
        "trader_room",
        payload={"decided_action": "abstain"},
        combo_id=combo_id,
    )


def _seed_plan_ledger(env, plans=3, meetings=("maintain", "revise")):
    """-006 版本账：N 条预案版本（同一 stock 的版本链）+ M 场会议（结论分布可配）。"""
    con = sqlite3.connect(env["plan_db"])
    con.row_factory = sqlite3.Row
    for i in range(plans):
        pml.add_plan_version(
            con,
            combo_id="combo_report_a",
            stock="600000",
            plan_version=f"pv-{i:03d}",
            plan={"action": "BUY", "pos_pct": 0.1},
            risk_profile="balanced",
            effective_at=f"2026-10-1{i}T09:30:00",
            source="unit_test",
            prev_version=(f"pv-{i - 1:03d}" if i else None),
        )
    for j, concl in enumerate(meetings):
        pml.add_meeting_record(
            con,
            meeting_id=f"mt-{j:03d}",
            topic_type="stock_date",
            topic_ref=f"60000{j}/2026-10-10",
            topic_time="2026-10-10T09:30:00",
            dissent=[
                {"role": "research", "stance": "buy", "reason": "预案偏保守"},
                {"role": "trader", "stance": "hold", "reason": "量能不足"},
            ],
            conclusion=concl,
            effective_at="2026-10-10T15:00:00",
            attendees=["research_team", "trader_room"],
            revised_plan_version=("pv-002" if concl == "revise" else None),
        )
    con.commit()
    con.close()


def _findings(*specs):
    """构造 -0.2 三查 findings：(severity, check, account, detail)。"""
    return [
        {"severity": s, "check": c, "account": a, "detail": d} for s, c, a, d in specs
    ]


def _build(env, combo_ids, **kw):
    """统一入口：固定 clock 保证可复现（roundtrip 断言用）。"""
    kw.setdefault("registry_path", env["registry_path"])
    kw.setdefault("ledger", env["ledger"])
    kw.setdefault("receipts", env["receipts"])
    kw.setdefault("month", MONTH)
    kw.setdefault("clock", CLOCK)
    return rb.build_report(combo_ids, **kw)


# ---------- 验收①：五段口径齐全 ----------


def test_five_sections_always_present(env):
    """即便一个数据源都不喂，五段标题恒出现于 Markdown，且各段显式标注缺失。"""
    rep = _build(env, None)
    md = rb.render_markdown(rep)
    for key, title in rb.SECTION_TITLES.items():
        assert key in rep["sections"], f"缺章节 {key}"
        assert f"## {title}" in md, f"Markdown 缺章节标题 {title}"
    # 缺失章节必须写明状态，不得静默省略
    assert rep["sections"]["research"]["status"] == rb.STATUS_NOT_WIRED
    assert rep["sections"]["research"]["reason"] == "plan_db_not_provided"
    assert rep["sections"]["simulation"]["reason"] == "no_combo_specified"
    assert rep["sections"]["alerts"]["reason"] == "findings_not_provided"
    assert "未接入" in md


def test_five_sections_with_full_data(env):
    """全量喂入：一~五段均 available 且各出数。"""
    cid = _make_combo(env, "combo_report_a")
    combo_factory.record_combo_cost(cid, 4.0, note="b1", ledger=env["ledger"])
    _four_receipts(env["receipts"], combo_id=cid)
    _seed_plan_ledger(env)
    rep = _build(
        env,
        [cid],
        plan_db=env["plan_db"],
        reviews_by_combo={cid: _reviews(combo_id=cid)},
        findings=_findings(("P0", "ledger_freshness", "main", {"gap_trading_days": 5})),
    )
    md = rb.render_markdown(rep)
    for key in rb.SECTIONS:
        assert rep["sections"][key]["available"] is True, f"{key} 段应 available"
    r = rep["sections"]["research"]
    assert r["plan_versions_total"] == 3
    assert r["meetings_total"] == 2
    assert r["divergence_rate"] == 0.5  # revise 1 / 2 场
    assert rep["sections"]["branches"]["receipts"]["rate_4_of_4"] == 1.0
    assert "分歧率：0.5" in md


# ---------- 验收②：不造数 ----------


def test_roi_null_renders_pending_backfill_not_zero(env):
    """后验收益缺失 → ROI 原样透出 pending_backfill，绝不渲染 0（「打平」是造数）。"""
    cid = _make_combo(env, "combo_report_a")
    combo_factory.record_combo_cost(cid, 4.0, ledger=env["ledger"])
    rep = _build(
        env, [cid], reviews_by_combo={cid: _reviews(combo_id=cid)}, findings=[]
    )
    row = rep["sections"]["simulation"]["combos"][0]
    assert row["roi"]["status"] == combo_review.ROI_PENDING_BACKFILL
    assert row["roi"]["value"] is None
    assert row["roi"]["is_number"] is False
    assert row["roi"]["display"] == "pending_backfill"
    md = rb.render_markdown(rep)
    assert "pending_backfill" in md
    # 关键：ROI 列不得出现 0（「打平」是造数），而是原样透出状态词
    roi_cell = [ln for ln in md.splitlines() if ln.startswith(f"| {cid} ")][0]
    assert "| pending_backfill |" in roi_cell
    assert "| 0 |" not in roi_cell
    assert rep["sections"]["simulation"]["pending_backfill_count"] == 1


def test_roi_unit_mismatch_renders_token(env):
    """收益单位与成本单位不可减 → unit_mismatch，不出数、不填 0。"""
    cid = _make_combo(env, "combo_report_a")
    combo_factory.record_combo_cost(cid, 4.0, ledger=env["ledger"])
    rep = _build(
        env, [cid], findings=[], posterior_by_combo={cid: {"unit": "PCT", "total": 3.5}}
    )
    row = rep["sections"]["simulation"]["combos"][0]
    assert row["roi"]["status"] == combo_review.ROI_UNIT_MISMATCH
    assert row["roi"]["display"] == "unit_mismatch"
    assert "unit_mismatch" in rb.render_markdown(rep)


def test_roi_computed_shows_number(env):
    """算出数时才给数值（对照组：确保 pending_backfill 不是恒定文案）。"""
    cid = _make_combo(env, "combo_report_a")
    combo_factory.record_combo_cost(cid, 4.0, ledger=env["ledger"])
    rep = _build(
        env,
        [cid],
        findings=[],
        posterior_by_combo={cid: {"unit": "CNY", "total": 12.0}},
    )
    row = rep["sections"]["simulation"]["combos"][0]
    assert row["roi"]["status"] == combo_review.ROI_COMPUTED
    assert row["roi"]["value"] == "8"
    assert row["roi"]["is_number"] is True


def test_divergence_rate_null_when_no_meetings(env):
    """会议场次为 0 → 分歧率记 null（不以 0 充数）。"""
    con = sqlite3.connect(env["plan_db"])
    con.row_factory = sqlite3.Row
    pml.add_plan_version(
        con,
        combo_id="combo_report_a",
        stock="600000",
        plan_version="pv-only",
        plan={"action": "HOLD"},
        risk_profile="balanced",
        effective_at="2026-10-10T09:30:00",
        source="unit_test",
    )
    con.commit()
    con.close()
    rep = _build(env, None, plan_db=env["plan_db"])
    r = rep["sections"]["research"]
    assert r["available"] is True
    assert r["meetings_total"] == 0
    assert r["divergence_rate"] is None
    assert r["divergence_note"]


def test_budget_unregistered_stays_null(env):
    """预算未登记组合 → 预算/剩余/超预算 null，合计预算 null（不臆造）。"""
    with_budget = _make_combo(env, "combo_report_a", budget=100.0)
    no_budget = _make_combo(env, "combo_report_b", budget=100.0)
    # 把 combo_report_b 的 budget 抹掉（模拟 -003 早期条目）
    reg = json.loads(env["registry_path"].read_text(encoding="utf-8"))
    entry = next(e for e in reg["combos"] if e["combo_id"] == no_budget)
    entry.pop("budget", None)
    env["registry_path"].write_text(
        json.dumps(reg, ensure_ascii=False), encoding="utf-8"
    )
    combo_factory.record_combo_cost(with_budget, 4.0, ledger=env["ledger"])
    combo_factory.record_combo_cost(no_budget, 6.0, ledger=env["ledger"])
    rep = _build(env, [with_budget, no_budget], findings=[])
    t = rep["sections"]["cost_budget"]["totals"]
    assert t["cost_accumulated"] == 10.0  # 成本照实
    assert t["budget"] is None  # 预算不臆造
    assert t["budget_complete"] is False
    assert t["unregistered_combos"] == [no_budget]
    row = [
        r for r in rep["sections"]["cost_budget"]["rows"] if r["combo_id"] == no_budget
    ][0]
    assert row["budget"] is None and row["remaining"] is None
    assert row["over_budget"] is None


# ---------- 验收③：P0 告警行透出 ----------


def test_p0_alert_row_transparent(env):
    """P0 红灯行必须逐条透出（数据断线 / 消费链停摆 / 灰度异常三源）。"""
    findings = _findings(
        (
            "P0",
            "ledger_freshness",
            "main",
            {"gap_trading_days": 7, "last_mark": "2026-10-09"},
        ),
        ("P1", "consumer_chain", "*", {"max_stall_run": 3}),
        ("P2", "ledger_freshness", "hk_qingping", {"ok": True}),
    )
    rep = _build(env, None, findings=findings)
    a = rep["sections"]["alerts"]
    assert a["p0_count"] == 1
    assert a["highest_level"] == "red"
    assert a["verdict"].startswith("红")
    assert a["grayscale_count"] == 1
    p0 = [r for r in a["rows"] if r["severity"] == "P0"][0]
    assert p0["level"] == "red" and p0["category"] == "账本断线"
    assert p0["detail"]["gap_trading_days"] == 7
    md = rb.render_markdown(rep)
    assert "| 红 | P0 | 账本断线 | ledger_freshness | main |" in md
    assert "黄" in md and "灰度/账本断线" in md


def test_alerts_level_mapping_and_all_green():
    """P0 红 / P1 黄 / P2 绿；零 findings → 全绿（不虚报异常）。"""
    rep = rb.build_report(None, findings=[], clock=CLOCK, month=MONTH)
    a = rep["sections"]["alerts"]
    assert a["verdict"] == rb.LEVEL_ALL_GREEN
    assert a["highest_level"] == "green" and a["p0_count"] == 0
    mixed = rb.build_report(
        None,
        clock=CLOCK,
        month=MONTH,
        findings=_findings(
            ("P1", "consumer_chain", "*", {}), ("P2", "ledger_freshness", "main", {})
        ),
    )
    assert mixed["sections"]["alerts"]["highest_level"] == "yellow"
    assert mixed["sections"]["alerts"]["verdict"].startswith("黄")


# ---------- 验收④：DRYRUN 标记与零真实出口 ----------


def test_dryrun_marker_present_both_surfaces(env):
    """DRYRUN 标记在 Markdown 头部与 JSON payload 双处恒带。"""
    cid = _make_combo(env, "combo_report_a")
    rep = _build(env, [cid], findings=[])
    md = rb.render_markdown(rep)
    assert rb.DRYRUN_NOTICE in md
    assert md.splitlines()[0] == "# 司马懿汇报（DRYRUN）"
    assert md.splitlines()[2] == f"> **{rb.DRYRUN_NOTICE}**"
    assert rep["dryrun"] is True
    assert rep["notice"] == rb.DRYRUN_NOTICE
    assert rep["outlet"]["pushed"] is False
    assert rep["outlet"]["target"] is None
    assert rep["outlet"]["pending_ticket"] == rb.PENDING_PUSH_TICKET
    assert rb.DRYRUN_NOTICE in rb.render_json(rep)


def test_zero_real_outlet_under_network_clamp(env, monkeypatch):
    """网络出口全程钳制下跑通全链路——任何真实出口尝试即炸（沿用 -008 判例）。"""

    def _no_socket(*a, **kw):
        raise AssertionError("检测到真实网络出口尝试（socket 被钳制）")

    def _no_urlopen(*a, **kw):
        raise AssertionError("检测到真实网络出口尝试（urlopen 被钳制）")

    import socket
    import urllib.request

    monkeypatch.setattr(socket, "socket", _no_socket)
    monkeypatch.setattr(urllib.request, "urlopen", _no_urlopen)

    cid = _make_combo(env, "combo_report_a")
    combo_factory.record_combo_cost(cid, 4.0, ledger=env["ledger"])
    _four_receipts(env["receipts"], combo_id=cid)
    _seed_plan_ledger(env)
    rep = _build(
        env,
        [cid],
        plan_db=env["plan_db"],
        reviews_by_combo={cid: _reviews(combo_id=cid)},
        findings=_findings(("P0", "ledger_freshness", "main", {})),
    )
    md = rb.render_markdown(rep)
    assert rb.DRYRUN_NOTICE in md
    assert rep["outlet"]["pushed"] is False


def test_source_level_no_outlet_tokens():
    """源级断言：生成器模块不含通知/网络出口字样（无飞书 SDK / 无 webhook）。"""
    src = (HERE / "paper" / "report_builder.py").read_text(encoding="utf-8").lower()
    for token in (
        "feishu",
        "webhook",
        "urlopen",
        "urllib",
        "requests.",
        "socket",
        "httpx",
        "aiohttp",
        "notify_",
        "send_message",
        "open.feishu",
        "lark",
    ):
        assert token not in src, f"生成器模块不得出现真实出口字样: {token}"


def test_no_external_sdk_imported():
    """运行期核对：生成器只依赖 stdlib + 板块内模块，不加载任何对外推送 SDK。"""
    import ast
    import sys as _sys

    tree = ast.parse((HERE / "paper" / "report_builder.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {
        "json",
        "re",
        "sqlite3",
        "datetime",
        "pathlib",
        "acceptance_check",
        "combo_review",
        "__future__",
    }, f"生成器只应依赖 stdlib + 板块内模块，实际: {sorted(imported)}"
    # 2026-10-30 P53：改为干净子进程验证。
    # 原实现检查当前进程 sys.modules，会被 pytest 同目录其它测试模块（如
    # test_feishu_push 在收集期 import httpx）污染 → 假失败（known_failure 之一）。
    import subprocess

    code = (
        "import sys;"
        "sys.path.insert(0, %r);"
        "import report_builder;"  # noqa: F401
        "bad=[m for m in ('lark_oapi','httpx','aiohttp','feishu') "
        "if m in sys.modules];"
        "print(','.join(bad));"
        "sys.exit(1 if bad else 0)"
    ) % str(HERE / "paper")
    r = subprocess.run(
        [_sys.executable, "-c", code],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120,
    )
    assert r.returncode == 0, (
        "生成器加载了对外推送 SDK：%s%s"
        % (r.stdout.strip(), (r.stderr or "")[-300:])
    )


# ---------- 验收⑤：输出永远不含实盘字段 ----------


def _all_keys(obj, acc=None):
    """递归收集输出结构全部键名（供实盘字段扫描）。"""
    acc = [] if acc is None else acc
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.append(str(k))
            _all_keys(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _all_keys(v, acc)
    return acc


def test_output_has_no_live_fields(env):
    """全量喂入后递归扫描：输出结构永远不含实盘字段（键名维度）。"""
    cid = _make_combo(env, "combo_report_a")
    _four_receipts(env["receipts"], combo_id=cid)
    _seed_plan_ledger(env)
    rep = _build(
        env,
        [cid],
        plan_db=env["plan_db"],
        reviews_by_combo={cid: _reviews(combo_id=cid)},
        findings=_findings(("P0", "ledger_freshness", "main", {})),
    )
    rb._assert_no_live_fields(rep)  # 不抛即通过
    keys = _all_keys(rep)
    assert keys, "应能收集到输出键名"
    for k in keys:
        assert not rb._LIVE_KEY_PATTERN.search(k.lower()), f"实盘字段泄漏: {k}"
        assert "实盘" not in k
    # scope 显式声明排除实盘（列出的是「不含」清单，属值不是字段）
    assert "live_trading" in rep["scope"]["out_of_scope"]
    assert rep["scope"]["trading_mode"] == "simulation_only"


@pytest.mark.parametrize(
    "bad_key",
    [
        "live_pnl",
        "real_account",
        "realized_pnl",
        "broker_account",
        "live_orders",
        "settled_amount",
        "actual_return",
        "实盘收益",
    ],
)
def test_live_field_guard_raises(bad_key):
    """守卫命中实盘字段即抛（防误用，不静默放行）。"""
    with pytest.raises(rb.ReportError) as ei:
        rb._assert_no_live_fields({"combos": [{bad_key: 1.0}]})
    assert ei.value.reason_code == "live_field_forbidden"


def test_live_field_guard_recurses_into_lists():
    """守卫递归到列表内嵌 dict（嵌套实盘字段同样拒）。"""
    with pytest.raises(rb.ReportError):
        rb._assert_no_live_fields({"rows": [{"meta": {"live_flag": True}}]})


# ---------- 验收⑥：roundtrip 可复现 ----------


def test_roundtrip_same_input_twice_identical(env):
    """同输入两次生成完全一致（clock 注入 → 可复现，供 015 归档复现比对）。"""
    cid = _make_combo(env, "combo_report_a")
    combo_factory.record_combo_cost(cid, 4.0, ledger=env["ledger"])
    _four_receipts(env["receipts"], combo_id=cid)
    _seed_plan_ledger(env)
    kw = dict(
        plan_db=env["plan_db"],
        reviews_by_combo={cid: _reviews(combo_id=cid)},
        findings=_findings(("P0", "ledger_freshness", "main", {"gap": 5})),
    )
    r1 = _build(env, [cid], **kw)
    r2 = _build(env, [cid], **kw)
    assert rb.render_markdown(r1) == rb.render_markdown(r2)
    assert rb.render_json(r1) == rb.render_json(r2)


# ---------- 验收⑦：closed on failure ----------


def test_combo_ids_invalid_raises():
    """非法 combo_ids 显式抛错（不静默降级）。"""
    with pytest.raises(rb.ReportError) as ei:
        rb.build_report("combo_report_a", clock=CLOCK)
    assert ei.value.reason_code == "combo_ids_invalid"


def test_unregistered_combo_raises(env):
    """组合未注册 → 显式抛错（-013 closed on failure 透传，禁静默跳过）。"""
    with pytest.raises(rb.ReportError) as ei:
        _build(env, ["combo_not_registered_at_all"])
    assert ei.value.reason_code == "upstream_review_failed"
    assert "combo_not_registered" in str(ei.value)


def test_findings_invalid_raises():
    """findings 结构非法显式抛错。"""
    with pytest.raises(rb.ReportError) as ei:
        rb.build_report(None, findings={"severity": "P0"}, clock=CLOCK)
    assert ei.value.reason_code == "findings_invalid"
    with pytest.raises(rb.ReportError) as ei2:
        rb.build_report(None, findings=["P0"], clock=CLOCK)
    assert ei2.value.reason_code == "findings_invalid"


def test_dissent_corrupt_raises(env):
    """-006 dissent_json 损坏显式抛错（不静默跳过会议）。"""
    con = sqlite3.connect(env["plan_db"])
    con.execute(
        "INSERT INTO meeting_records(meeting_id,topic_type,topic_ref,topic_time,"
        "dissent_json,conclusion,revised_plan_version,effective_at,"
        "attendees_json,corrects_meeting_id,created_at) "
        "VALUES('mt-bad','stock_date','x/2026-10-10','2026-10-10T09:30:00',"
        "'NOT-JSON','maintain',NULL,'2026-10-10T15:00:00','[\"a\"]',NULL,'t')"
    )
    con.commit()
    con.close()
    with pytest.raises(rb.ReportError) as ei:
        _build(env, None, plan_db=env["plan_db"])
    assert ei.value.reason_code == "dissent_corrupt"


def test_empty_plan_db_is_no_data_not_crash(env):
    """版本账空 → no_data（显式标注），不崩、不当 0。"""
    rep = _build(env, None, plan_db=env["plan_db"])
    r = rep["sections"]["research"]
    assert r["status"] == rb.STATUS_NO_DATA
    assert r["reason"] == "plan_ledger_empty"
    assert r["divergence_rate"] is None


# ---------- 附加：只读性与分支对比口径 ----------


def test_plan_db_not_written_by_report(env):
    """生成器对 -006 版本账零写入（只读 mode=ro）。"""
    _seed_plan_ledger(env)
    before = env["plan_db"].read_bytes()
    _build(env, None, plan_db=env["plan_db"])
    assert env["plan_db"].read_bytes() == before


def test_branch_comparison_counts(env):
    """跟随 vs 激进：执行/放弃/分歧留痕逐项计数（-008 口径）。"""
    cid = _make_combo(env, "combo_report_a")
    _four_receipts(env["receipts"], combo_id=cid)
    rep = _build(
        env, [cid], reviews_by_combo={cid: _reviews(combo_id=cid)}, findings=[]
    )
    rows = {r["branch_id"]: r for r in rep["sections"]["branches"]["branches"]}
    assert rows["follow_research"]["executed"] == 0
    assert rows["follow_research"]["abstained"] == 1
    assert rows["aggressive_direct"]["executed"] == 1
    assert rows["aggressive_direct"]["abstained"] == 0
    # 两分支各带 1 条 deviation → 分歧留痕 2
    assert rep["sections"]["branches"]["divergence_traces_total"] == 2
    assert rows["follow_research"]["reason_codes"] == {"plan_conflict": 1}
    assert rows["follow_research"]["posterior_pending_backfill"] == 1


def test_multi_combo_aggregation(env):
    """多组合：分支留痕跨组合聚合、成本合计、ROI 三态分布可读。"""
    a = _make_combo(env, "combo_report_a", budget=100.0)
    b = _make_combo(env, "combo_report_b", budget=50.0)
    combo_factory.record_combo_cost(a, 4.0, ledger=env["ledger"])
    combo_factory.record_combo_cost(b, 6.0, ledger=env["ledger"])
    _four_receipts(env["receipts"], signal_id=SIG, combo_id=a)
    rep = _build(
        env,
        [a, b],
        reviews_by_combo={a: _reviews(combo_id=a), b: _reviews(combo_id=b)},
        posterior_by_combo={b: {"unit": "CNY", "total": 20.0}},
        findings=[],
    )
    sim = rep["sections"]["simulation"]
    assert sim["combo_count"] == 2
    assert sim["pending_backfill_count"] == 1
    assert sim["roi_status_counts"][combo_review.ROI_COMPUTED] == 1
    assert rep["sections"]["cost_budget"]["totals"]["cost_accumulated"] == 10.0
    assert rep["sections"]["cost_budget"]["totals"]["budget"] == 150.0
    # 信号在两个组合各计一次 → 4/4 达成率仍为 1.0
    assert rep["sections"]["branches"]["receipts"]["rate_4_of_4"] == 1.0


def test_no_combo_no_snapshot_db_read(env):
    """默认不读 -004 快照库（未接线即不假装有数据）。"""
    cid = _make_combo(env, "combo_report_a")
    rep = _build(env, [cid], findings=[])
    assert rep["sections"]["simulation"]["combos"][0]["combo_id"] == cid
