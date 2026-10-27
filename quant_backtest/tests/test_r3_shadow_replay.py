# -*- coding: utf-8 -*-
"""R3 绝对数闸 5→8 影子对账（r3_shadow_replay）单测：WO-QT-20261020-066。

真源：deliverables/2026-10-20_R3绝对数闸5to8影子校准准备件_WO-QT-20261020-023.md
§三（影子期方案：只记录不拦截，观测拦截面收窄 ~64%）。
纪律：脚本对账本/参数只读、零写入；本测试全部用临时 fixture，不触真实账本，
不触 params.yaml，不触 enforce_allowed()。
"""
import io
import json
import os
import sys


_QB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _QB not in sys.path:
    sys.path.insert(0, _QB)

import r3_shadow_replay as R  # noqa: E402


# ══ reason 解析 ════════════════════════════════════════════════════
def test_parse_reason_block():
    """shadow_block 行 reason → 判定输入全量解析（density 与绝对数双闸）。"""
    reason = ("bear事件4+矛盾边3=7 密度0.35/日(阈0.50未中) "
              "绝对数闸(阈5中)")
    p = R.parse_r3_reason(reason)
    assert p == {"n_bear": 4, "n_con": 3, "n_tot": 7, "density": 0.35,
                 "density_th": 0.50, "hit_d": False,
                 "recorded_min_n": 5, "hit_n": True}


def test_parse_reason_density_hit():
    reason = ("bear事件2+矛盾边1=3 密度0.60/日(阈0.50中) "
              "绝对数闸(阈5未中)")
    p = R.parse_r3_reason(reason)
    assert p is not None and p["hit_d"] is True and p["hit_n"] is False


def test_parse_reason_not_evaluated():
    """无数据放行 / UNKNOWN / 基数守卫 → 不可重放（返回 None，如实计数）。"""
    assert R.parse_r3_reason("无事件风险数据-放行(无据不拦)") is None
    assert R.parse_r3_reason(
        "UNKNOWN: 事件图无 as-of 查询与可见性证明，R3 不评") is None
    assert R.parse_r3_reason(
        "GUARD(窗口事件基数 2<3)-不评(密度分母不稳)") is None


# ══ 单行重放 ═══════════════════════════════════════════════════════
def _row(action="pass", reason="bear事件0+矛盾边0=0 密度0.00/日(阈0.50未中) "
         "绝对数闸(阈5未中)", ts="2026-10-21 09:00:00",
         security="000001.SZ", rule_id="R3-event-risk", mode="shadow"):
    return {"ts": ts, "security": security, "rule_id": rule_id,
            "action": action, "ref_px": None, "score": None,
            "reason": reason, "data_version": "2026-10-08-user-approved",
            "mode": mode}


def test_replay_only_n5_narrowed_out():
    """n_tot=6：5 档拦（账本 shadow_block）、8 档放 → 拦截面收窄样本。"""
    r = R.replay_row(_row(
        action="shadow_block",
        reason="bear事件6+矛盾边0=6 密度0.30/日(阈0.50未中) 绝对数闸(阈5中)"),
        shadow_min_n=8)
    assert r["status"] == "evaluated"
    assert r["block_n5"] is True and r["block_n8"] is False
    assert r["delta"] == "only_n5"


def test_replay_density_block_kept():
    """密度闸命中：5→8 均拦（密度闸不动，收窄只来自绝对数闸）。"""
    r = R.replay_row(_row(
        action="shadow_block",
        reason="bear事件2+矛盾边1=3 密度0.60/日(阈0.50中) 绝对数闸(阈5未中)"),
        shadow_min_n=8)
    assert r["delta"] == "both_block"


def test_replay_both_pass():
    r = R.replay_row(_row(), shadow_min_n=8)
    assert r["delta"] == "both_pass"


def test_replay_not_evaluated():
    r = R.replay_row(_row(
        reason="UNKNOWN: 事件图无 as-of 查询与可见性证明，R3 不评"),
        shadow_min_n=8)
    assert r["status"] == "not_evaluated" and r["delta"] is None


def test_replay_mismatch_flagged():
    """账本自洽：实录 pass 但 n_tot>=5 → 重放与实录不符，必须显式上浮。"""
    r = R.replay_row(_row(
        action="pass",
        reason="bear事件6+矛盾边0=6 密度0.30/日(阈0.50未中) 绝对数闸(阈5中)"),
        shadow_min_n=8)
    assert r["replay_mismatch"] is True


def test_replay_ignores_other_rules():
    r = R.replay_row(_row(rule_id="R1-market-quality"), shadow_min_n=8)
    assert r is None


# ══ 汇总 ═══════════════════════════════════════════════════════════
def test_aggregate_narrowing_ratio():
    """拦截面收窄 = 1 - block8/block5；准备件 §三 预期 ~64%。"""
    rows = []
    # 3 行 5 档拦、8 档放（only_n5）
    for i in range(3):
        rows.append(_row(
            action="shadow_block",
            reason="bear事件6+矛盾边0=6 密度0.30/日(阈0.50未中) 绝对数闸(阈5中)",
            security="60000%d.SS" % i))
    # 1 行双档均拦（密度）
    rows.append(_row(
        action="shadow_block",
        reason="bear事件2+矛盾边1=3 密度0.60/日(阈0.50中) 绝对数闸(阈5未中)",
        security="600010.SS"))
    # 2 行双档均放
    rows.extend([_row(), _row(security="600011.SS")])
    s = R.aggregate([R.replay_row(r, shadow_min_n=8) for r in rows])
    assert s["evaluated"] == 6
    assert s["block_n5"] == 4 and s["block_n8"] == 1
    assert s["only_n5"] == 3
    assert s["narrowing"] == round(1 - 1 / 4, 4)   # 0.75
    assert s["mismatch"] == 0


def test_aggregate_empty():
    s = R.aggregate([])
    assert s["evaluated"] == 0 and s["narrowing"] is None


def test_aggregate_date_window():
    rows = [_row(ts="2026-10-21 09:00:00"),
            _row(ts="2026-10-22 09:00:00", security="600011.SS")]
    rep = [R.replay_row(r, shadow_min_n=8) for r in rows]
    s = R.aggregate(rep, since="2026-10-22", until="2026-10-22")
    assert s["evaluated"] == 1


# ══ 端到端（临时账本，只读保证）═══════════════════════════════════
def test_main_readonly_smoke(tmp_path, capsys):
    led = tmp_path / "ledger.jsonl"
    rows = [
        _row(action="shadow_block",
             reason="bear事件6+矛盾边0=6 密度0.30/日(阈0.50未中) "
                    "绝对数闸(阈5中)", security="600001.SS"),
        _row(),
        _row(rule_id="R1-market-quality", action="pass",
             reason="市场健康占比(酝酿+爆发)/n=20/20=0.5000 滚动125日P10"
                    "分位阈=0.1000 市场级闸(常态)"),
    ]
    led.write_text("\n".join(json.dumps(r, ensure_ascii=False)
                             for r in rows) + "\n", encoding="utf-8")
    before = sorted(os.listdir(tmp_path))
    out = io.StringIO()
    sys.stdout, back = out, sys.stdout
    try:
        rc = R.main(["--ledger", str(led), "--json"])
    finally:
        sys.stdout = back
    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["evaluated"] == 2
    assert payload["block_n5"] == 1 and payload["block_n8"] == 0
    assert payload["only_n5"] == 1
    # 只读：目录内容无增删
    assert sorted(os.listdir(tmp_path)) == before


def test_main_missing_ledger_errors(tmp_path, capsys):
    rc = R.main(["--ledger", str(tmp_path / "nope.jsonl"), "--json"])
    assert rc == 1
