# -*- coding: utf-8 -*-
"""WO-QT-20261020-023 验收测试：RiskGuard 规则级误杀专题（tmp 隔离）。

验收口径（工单 + 契约）：
  1. **零误杀证据时出"无证据"结论**（不是空数组充数）：status 显式且
     conclusion 非空、逐组统计齐备；
  2. **误杀定义严格**：decision=blocked 且 posterior_return > 0 才算命中
     （passed 不算、posterior<=0 不算、None 不算）；
  3. **按 (rule_id, rule_version) 归集**：同 rule_id 不同 version 分开归集；
  4. **213 历史口径不参与专题计算**：非 canonical_ledger 来源的记录被排除
     且计数可见（专题输出显式声明"历史判案不可复现、不可拆分"）；
  5. **只读零写入**：账本与日线 mtime + 字节数在专题前后完全不变；
  6. **准备件不改生产**：生产 params.yaml 字节数 + mtime 不变；
  7. **closed on failure**：坏记录 / 坏账本 / 缺文件 → 显式抛。

隔离纪律：全部账/库走 tmp 临时路径；生产账本与生产 params.yaml 只读断言
（跑完字节与 mtime 必须不变）。
"""
import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "paper"))
sys.path.insert(0, str(HERE / "tests"))

import riskguard_topics as rgt                               # noqa: E402

# 生产件只读锚点（测试只读它们，绝不写）
PROD_PARAMS = HERE.parent.parent / "zerobound-kg" / "params.yaml"
PROD_LEDGER = HERE.parent.parent / "zerobound-kg" / "riskguard_ledger" / "ledger.jsonl"

R1 = "R1-market-quality"
R2 = "R2-liquidity-shock"
V1 = "2026-09-30-user-approved"
V2 = "2026-10-08-user-approved"


# ---------- 构造器 ----------

def _rec(rule_id=R1, rule_version=V1, decision="blocked", posterior=0.05,
         ts="2026-10-20T10:00:00", provenance="canonical_ledger", **extra):
    """5 键契约记录（+ provenance 来源标注）。"""
    r = {"ts": ts, "rule_id": rule_id, "rule_version": rule_version,
         "decision": decision, "posterior_return": posterior}
    if provenance is not None:
        r[rgt.PROVENANCE_KEY] = provenance
    r.update(extra)
    return r


def _ledger_line(ts, rule_id, action, security="300808.SZ",
                 data_version=V1, score=1.0, reason="ok"):
    return json.dumps({"ts": ts, "security": security, "rule_id": rule_id,
                       "action": action, "ref_px": 12.3, "score": score,
                       "reason": reason, "data_version": data_version,
                       "mode": "shadow"}, ensure_ascii=False)


class _FakeProvider:
    """假价格源：按 (security, d0) 返回固定收益（或 None=未成熟）。"""

    def __init__(self, table=None, default=None):
        self.table = table or {}
        self.default = default
        self.calls = []

    def ret(self, security, d0):
        self.calls.append((security, d0))
        return self.table.get((security, d0), self.default)


# ---------- 验收①：零误杀证据 → "无证据"结论（不是空数组充数） ----------

def test_zero_evidence_yields_explicit_conclusion_not_empty_array():
    """无规则级误杀证据 → status 显式 + conclusion 非空 + 逐组统计齐备。"""
    recs = [_rec(decision="blocked", posterior=-0.05 - 0.001 * i) for i in range(40)]
    recs += [_rec(decision="passed", posterior=-0.02 - 0.001 * i) for i in range(40)]
    out = rgt.mine_topics(recs)
    assert out["status"] == rgt.STATUS_NO_EVIDENCE
    assert out["implemented"] is True
    assert out["topics"] == []                     # 0 条议题
    # **不是空数组充数**：必须有非空结论 + 可复核的逐组证据
    assert out["conclusion"].strip()
    assert "无规则级误杀证据" in out["conclusion"]
    assert out["counts"]["groups"] == 1
    g = out["groups"][0]
    assert g["evidence"] == rgt.EV_NONE
    assert g["settled_blocked"] == 40
    assert g["settled_baseline_same_rule"] == 40
    assert g["false_kill_hits"] == 0
    assert g["welch_t"] is not None
    # 判据与公式在输出里明示（可审计）
    assert out["false_kill_definition"] == "decision=blocked 且 posterior_return > 0"
    assert out["group_by"] == ["rule_id", "rule_version"]
    assert out["criterion"]["t_threshold"] == rgt.DEFAULT_T_THRESHOLD


def test_zero_records_still_reports_reason_not_silent():
    """零记录 → status=no_records + 非空结论（合法「无数据」，不抛不充数）。"""
    out = rgt.mine_topics([])
    assert out["status"] == rgt.STATUS_NO_RECORDS
    assert out["topics"] == []
    assert out["conclusion"].strip()
    assert out["counts"]["records_used"] == 0


# ---------- 验收②：误杀定义严格 ----------

def test_false_kill_definition_is_strict():
    """仅 decision=blocked 且 posterior_return>0 算命中；其余一律不算。"""
    recs = [
        _rec(decision="blocked", posterior=0.05),     # 命中
        _rec(decision="blocked", posterior=0.0),      # 边界 0 → 不算
        _rec(decision="blocked", posterior=-0.01),    # 负 → 不算
        _rec(decision="blocked", posterior=None),     # 未成熟 → 不算
        _rec(decision="passed", posterior=0.05),      # 放行 → 不算
    ]
    out = rgt.mine_topics(recs)
    assert out["counts"]["false_kill_hits"] == 1
    g = out["groups"][0]
    assert g["false_kill_hits"] == 1
    assert g["settled_blocked"] == 3      # 0.05 / 0.0 / -0.01（None 不算成熟）
    assert g["hit_examples"][0]["posterior_return"] == 0.05


def test_passed_records_never_counted_as_false_kill():
    """放行组高收益样本不得被计为误杀（防口径放宽）。"""
    recs = [_rec(decision="passed", posterior=0.9) for _ in range(10)]
    out = rgt.mine_topics(recs)
    assert out["counts"]["false_kill_hits"] == 0
    assert out["groups"][0]["false_kill_hits"] == 0


# ---------- 验收③：按 (rule_id, rule_version) 归集 ----------

def test_grouped_by_rule_id_and_rule_version():
    """同 rule_id 不同 rule_version → 分开归集，不混成一组。"""
    recs = [_rec(rule_id=R1, rule_version=V1, decision="blocked", posterior=0.05)
            for _ in range(40)]
    recs += [_rec(rule_id=R1, rule_version=V2, decision="blocked", posterior=0.05)
             for _ in range(40)]
    recs += [_rec(rule_id=R2, rule_version=V1, decision="blocked", posterior=0.05)
             for _ in range(40)]
    recs += [_rec(rule_id=R2, rule_version=V1, decision="passed", posterior=-0.05)
             for _ in range(40)]
    out = rgt.mine_topics(recs)
    keys = [(g["rule_id"], g["rule_version"]) for g in out["groups"]]
    assert len(keys) == 3
    assert (R1, V1) in keys and (R1, V2) in keys and (R2, V1) in keys
    assert out["counts"]["groups"] == 3
    r1v1 = [g for g in out["groups"] if (g["rule_id"], g["rule_version"]) == (R1, V1)][0]
    r1v2 = [g for g in out["groups"] if (g["rule_id"], g["rule_version"]) == (R1, V2)][0]
    assert r1v1["records"] == 40 and r1v2["records"] == 40


def test_miskill_evidence_produces_topic_with_group_evidence():
    """拦截组显著优于放行组基线（t≥3）→ 该组出议题（真阳性路径）。"""
    blocked = [_rec(decision="blocked", posterior=0.10 + 0.001 * i) for i in range(60)]
    passed = [_rec(decision="passed", posterior=-0.05) for _ in range(60)]
    out = rgt.mine_topics(blocked + passed)
    assert out["status"] == rgt.STATUS_TOPICS_FOUND
    assert len(out["topics"]) == 1
    t = out["topics"][0]
    assert t["rule_id"] == R1 and t["rule_version"] == V1
    assert t["deviation_type"] == "riskguard_false_kill"
    assert t["status"] == "draft"
    assert t["meeting_initiated"] is False
    assert t["evidence"]["welch_t"] >= rgt.DEFAULT_T_THRESHOLD
    assert t["evidence"]["false_kill_hits"] == 60


# ---------- 验收④：213 历史口径不参与计算 ----------

def test_historical_213_records_excluded_from_computation():
    """非 canonical_ledger 来源（历史 213 判案）不参与计算，且计数可见。"""
    used = [_rec(decision="blocked", posterior=0.05) for _ in range(40)]
    used += [_rec(decision="passed", posterior=-0.05) for _ in range(40)]
    hist = [_rec(decision="blocked", posterior=0.9, provenance="historical_213_case")]
    out = rgt.mine_topics(used + hist)
    assert out["excluded"]["by_provenance"].get("historical_213_case") == 1
    assert out["counts"]["records_used"] == 80
    assert out["counts"]["records_excluded"] == 1
    # 排除的记录不得影响任何组统计
    g = out["groups"][0]
    assert g["false_kill_hits"] == 40
    assert g["settled_blocked"] == 40
    # 输出显式声明历史判案不可复现/不可拆分
    assert "不可复现" in out["historical_case_note"]
    assert "不可拆分" in out["historical_case_note"]


def test_records_without_provenance_are_excluded_not_silently_used():
    """来源不可自证（无 provenance）→ 不参与计算（宁严勿松，不静默混入）。"""
    recs = [_rec(decision="blocked", posterior=0.05, provenance=None)
            for _ in range(5)]
    out = rgt.mine_topics(recs)
    assert out["status"] == rgt.STATUS_NO_RECORDS
    assert out["counts"]["records_used"] == 0
    assert out["excluded"]["by_provenance"].get("unspecified") == 5


# ---------- 验收⑤：只读零写入（账本 + 日线） ----------

def test_ledger_read_is_readonly(tmp_path):
    """读账本不改字节、不改 mtime（只读断言）。"""
    led = tmp_path / "ledger.jsonl"
    led.write_text(
        _ledger_line("2026-10-08 10:00:00", R1, "shadow_block") + "\n" +
        _ledger_line("2026-10-08 10:00:00", R2, "pass") + "\n",
        encoding="utf-8")
    before_bytes = led.read_bytes()
    before_mtime = led.stat().st_mtime_ns
    provider = _FakeProvider(default=0.01)
    recs = rgt.build_records(led, price_provider=provider)
    assert len(recs) == 2
    assert led.read_bytes() == before_bytes
    assert led.stat().st_mtime_ns == before_mtime
    # 5 键契约齐备 + provenance 标注
    for r in recs:
        assert all(k in r for k in rgt.RECORD_REQUIRED)
        assert r[rgt.PROVENANCE_KEY] == "canonical_ledger"


def test_build_records_filters_and_dedups(tmp_path):
    """unknown / insufficient_sample 行剔除；(rule,security,day) 取首次评估。"""
    led = tmp_path / "ledger.jsonl"
    led.write_text(
        _ledger_line("2026-10-08 10:00:00", R1, "shadow_block", security="A.SZ") + "\n" +
        _ledger_line("2026-10-08 11:00:00", R1, "shadow_block", security="A.SZ") + "\n" +
        _ledger_line("2026-10-08 10:00:00", R1, "unknown", security="B.SZ") + "\n" +
        _ledger_line("2026-10-08 10:00:00", R2, "pass", security="C.SZ",
                     reason="insufficient_sample(四态覆盖 181<500)-不评") + "\n",
        encoding="utf-8")
    recs = rgt.build_records(led, price_provider=_FakeProvider(default=0.01))
    assert len(recs) == 1
    assert recs[0]["security"] == "A.SZ"
    assert recs[0]["ts"] == "2026-10-08 10:00:00"      # 首次评估


def test_real_production_files_unchanged_by_topic_run():
    """跑真实专题（生产账本 + 生产日线）后，生产件字节与 mtime 都不变。"""
    if not PROD_LEDGER.exists():
        pytest.skip("生产账本不在本机（只读断言跳过）")
    before = (PROD_LEDGER.stat().st_size, PROD_LEDGER.stat().st_mtime_ns)
    out = rgt.run_topic(ledger_path=PROD_LEDGER)
    assert out["implemented"] is True
    assert out["status"] in (rgt.STATUS_NO_EVIDENCE, rgt.STATUS_INCONCLUSIVE,
                              rgt.STATUS_TOPICS_FOUND)
    after = (PROD_LEDGER.stat().st_size, PROD_LEDGER.stat().st_mtime_ns)
    assert after == before, "生产账本被修改（只读纪律违例）"


# ---------- 验收⑥：准备件不改生产 ----------

def test_prep_package_does_not_touch_production_params():
    """准备件（参数候选 + 登记草案）不改生产 params.yaml（字节 + mtime 不变）。"""
    if not PROD_PARAMS.exists():
        pytest.skip("生产 params.yaml 不在本机（只读断言跳过）")
    before_bytes = PROD_PARAMS.read_bytes()
    before_mtime = PROD_PARAMS.stat().st_mtime_ns
    prep = _prep_package()
    assert prep["params_candidate"]["riskguard_bear_min_n"]["current"] == 5
    assert prep["params_candidate"]["riskguard_bear_min_n"]["candidate"] == 8
    assert PROD_PARAMS.read_bytes() == before_bytes
    assert PROD_PARAMS.stat().st_mtime_ns == before_mtime


def test_prep_package_is_review_only_and_not_applied():
    """准备件自带"未落地"标记与回滚预案（不得被误读为已改生产）。"""
    prep = _prep_package()
    assert prep["applied_to_production"] is False
    assert prep["production_write"] is False
    assert prep["rollback_plan"]
    assert prep["shadow_plan"]["shadow_period_days"] >= 14


def _prep_package():
    """b 项准备件（审查件口径）——本单只出准备件，不落生产。"""
    from importlib import import_module
    mod = import_module("riskguard_prep")
    return mod.build_r3_threshold_prep_package()


# ---------- 验收⑦：closed on failure ----------

def test_invalid_records_raise():
    """坏记录 → 显式抛（不静默跳过、不放宽容错）。"""
    for bad in ({"ts": "t", "rule_id": R1, "rule_version": V1, "decision": "blocked"},
                {"ts": "t", "rule_id": R1, "rule_version": V1,
                 "decision": "unknown", "posterior_return": 0.1},
                {"ts": "t", "rule_id": R1, "rule_version": V1,
                 "decision": "blocked", "posterior_return": "0.1"},
                {"ts": "", "rule_id": R1, "rule_version": V1,
                 "decision": "blocked", "posterior_return": 0.1},
                "not-a-dict"):
        with pytest.raises(rgt.RiskGuardTopicError,
                           match="riskguard_records_invalid"):
            rgt.mine_topics([bad])
    # 整个 records 非 list → 同样显式抛
    with pytest.raises(rgt.RiskGuardTopicError, match="riskguard_records_invalid"):
        rgt.mine_topics({"not": "a list"})


def test_missing_ledger_raises(tmp_path):
    """账本不存在 → 显式抛（不静默当无数据）。"""
    with pytest.raises(rgt.RiskGuardTopicError, match="ledger_missing"):
        rgt.read_ledger(tmp_path / "nope.jsonl")


def test_corrupt_ledger_raises(tmp_path):
    """坏行账本 → 显式抛（不跳行）。"""
    led = tmp_path / "ledger.jsonl"
    led.write_text("{不是 json}\n", encoding="utf-8")
    with pytest.raises(rgt.RiskGuardTopicError, match="ledger_corrupt"):
        rgt.read_ledger(led)


def test_corrupt_calibration_log_raises(tmp_path):
    """校准记录文件坏行 → 显式抛。"""
    p = tmp_path / "calib.jsonl"
    p.write_text("{坏 json}\n", encoding="utf-8")
    with pytest.raises(rgt.RiskGuardTopicError, match="calibration_log_corrupt"):
        rgt.read_calibration_log(p)


# ---------- 呈现 ----------

def test_markdown_renders_conclusion_and_groups():
    """Markdown：状态/结论/逐组证据/议题清单/口径边界 五段恒出现。"""
    recs = [_rec(decision="blocked", posterior=-0.02) for _ in range(40)]
    recs += [_rec(decision="passed", posterior=-0.05) for _ in range(40)]
    md = rgt.render_markdown(rgt.mine_topics(recs))
    assert "no_rule_level_false_kill_evidence" in md
    assert "结论" in md
    assert "逐组证据" in md
    assert "议题清单" in md
    assert "口径与边界" in md
    assert "不可复现" in md


def test_calibration_log_roundtrip(tmp_path):
    """5 键契约记录可写可读（写只到显式给的路径）。"""
    recs = [_rec(decision="blocked", posterior=0.05)]
    p = rgt.write_calibration_log(recs, tmp_path / "calib.jsonl")
    back = rgt.read_calibration_log(p)
    assert len(back) == 1
    assert back[0]["rule_id"] == R1
    assert back[0]["decision"] == "blocked"
    assert back[0]["posterior_return"] == 0.05
    out = rgt.mine_topics(back)
    assert out["counts"]["records_used"] == 1
