# -*- coding: utf-8 -*-
"""experiment_precheck 门禁测试。

覆盖六个容易被绕过的点：
  T1 空模板必须被拦（不能"生成了就算通过"）
  T2 合规单必须放行（门禁不能只会拦）
  T3 上限 <= 放弃线必须判劝退（rc=2，不是 rc=1）
  T4 if_true 写空话（不含数字）必须被拦
  T5 bool 冒充数字必须被拦（True 不能被当成 1 混过去）
  T6 selfcheck 缺失只 warn 不阻断；--strict 下才拦
  T7 require_pass 对未通过单抛异常
"""
import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import experiment_precheck as pc  # noqa: E402


def _doc(upper=5.0, threshold=0.0, if_true=None, spread=5.3):
    """构造一份完整合规的预判单。数值走变量，避免字面量被传输通道串味。"""
    return {
        "meta": {
            "exp_id": "T",
            "layer": "测试层",
            "hypothesis": "测试假设",
            "created": "2026-11-11",
        },
        "cap": {
            "method": "spread",
            "upper_bound_pct": upper,
            "cost_pct": 0.3,
            "basis": "测试数据 100 天 500 只",
            "inputs": {"quantiles": 10, "holding_days": 5, "spread_gross_pct": spread},
        },
        "abandon": {"threshold_pct": threshold, "rationale": "低于成本就弃"},
        "falsify": {
            "if_true": if_true if if_true is not None else [
                "分10档单调性 |rho| >= 0.8",
                "OOS t >= 2.0 且与 IS 同号",
            ],
            "if_false": ["分档倒 U 或 rho 反向"],
            "monotonicity_rho_min": 0.8,
            "t_min": 2.0,
            "is_oos_sign_agree": True,
        },
        "selfcheck": {"reference": "P67 maxret20 IC=-0.0668", "tolerance_pct": 5},
    }


def _write(tmp_path, doc, monkeypatch, name="T"):
    monkeypatch.setattr(pc, "EXP_ROOT", str(tmp_path))
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "precheck.yaml"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return str(p)


def test_t1_empty_template_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "EXP_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["x", "init", "E1", "--layer", "L", "--hypothesis", "H"])
    rc = pc.cmd_init(type("A", (), {"exp_id": "E1", "layer": "L", "hypothesis": "H", "force": False})())
    assert rc == pc.RC_OK
    doc = yaml.safe_load(open(tmp_path / "E1" / "precheck.yaml", encoding="utf-8"))
    verdict, _ = pc.verdict_of(doc)
    assert verdict == "incomplete", "空模板必须判不完整"


def test_t2_compliant_passes(tmp_path, monkeypatch):
    p = _write(tmp_path, _doc(upper=5.0, threshold=0.0), monkeypatch)
    doc = yaml.safe_load(open(p, encoding="utf-8"))
    errors, warns = pc.check(doc)
    assert errors == [], "合规单不应有错误：%s" % errors
    verdict, msg = pc.verdict_of(doc)
    assert verdict == "pass", msg


def test_t3_upper_below_threshold_is_abandon(tmp_path, monkeypatch):
    p = _write(tmp_path, _doc(upper=-0.04, threshold=0.0), monkeypatch)
    doc = yaml.safe_load(open(p, encoding="utf-8"))
    verdict, msg = pc.verdict_of(doc)
    assert verdict == "abandon", "上限低于放弃线必须判劝退（rc=2），不是不完整（rc=1）"


def test_t4_if_true_without_number_blocked(tmp_path, monkeypatch):
    p = _write(tmp_path, _doc(if_true=["要看结果好不好", "观察一下"]), monkeypatch)
    doc = yaml.safe_load(open(p, encoding="utf-8"))
    errors, _ = pc.check(doc)
    assert any("没有数值" in e for e in errors), "证伪条件写空话必须被拦：%s" % errors


def test_t5_bool_is_not_a_number(tmp_path, monkeypatch):
    d = _doc()
    d["cap"]["upper_bound_pct"] = True
    p = _write(tmp_path, d, monkeypatch)
    doc = yaml.safe_load(open(p, encoding="utf-8"))
    errors, _ = pc.check(doc)
    assert any("不是合法数字" in e for e in errors), "bool 不能被当数字：%s" % errors


def test_t6_selfcheck_missing_warns_only(tmp_path, monkeypatch):
    d = _doc()
    d.pop("selfcheck")
    p = _write(tmp_path, d, monkeypatch)
    doc = yaml.safe_load(open(p, encoding="utf-8"))
    errors, warns = pc.check(doc)
    assert errors == [], "selfcheck 缺失只应 warn"
    assert any("selfcheck" in w for w in warns)
    assert pc.verdict_of(doc)[0] == "pass", "缺 selfcheck 默认仍放行"


def test_t7_require_pass_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "EXP_ROOT", str(tmp_path))
    with pytest.raises(RuntimeError):
        pc.require_pass("NOT_EXIST")

    p = _write(tmp_path, _doc(upper=-1.0, threshold=0.0), monkeypatch, name="BAD")
    with pytest.raises(RuntimeError):
        pc.require_pass("BAD")


def test_t8_spread_method_requires_inputs(tmp_path, monkeypatch):
    d = _doc()
    d["cap"]["inputs"] = {"quantiles": 10}
    p = _write(tmp_path, d, monkeypatch)
    doc = yaml.safe_load(open(p, encoding="utf-8"))
    errors, _ = pc.check(doc)
    assert any("spread_gross_pct" in e for e in errors), "方法参数缺失必须被拦"


def test_t9_real_backfill_verdicts():
    """真实回填单：P69 劝退、GATE 通过 —— 这是规矩有效性的核心证据。"""
    for exp, want in (("P69_backfill", "abandon"), ("GATE_backfill", "pass")):
        p = pc.exp_path(exp)
        if not os.path.exists(p):
            pytest.skip("回填单缺失：%s" % exp)
        doc = yaml.safe_load(open(p, encoding="utf-8"))
        got, msg = pc.verdict_of(doc)
        assert got == want, "%s 期望 %s，实得 %s（%s）" % (exp, want, got, msg)
