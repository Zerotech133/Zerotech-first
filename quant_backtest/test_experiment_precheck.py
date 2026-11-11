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
  T10~T13 多重检验记账：6 组未校正 warn、5 组不 warn、标校正却不给方法报错
  T14~T16 成本敏感性：derived 自动算三档、自报翻转矛盾报错、不适用须写理由
  T17~T18 reality-check 格式复核记录：字段齐、判定映射对
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


# ── P1：多重检验记账（attempts）──
GROSS = 0.259          # P69 实测毛 spread，用变量传递，避免字面量被通道串味


def _with(doc, **blocks):
    d = dict(doc)
    d.update(blocks)
    return d


def test_t10_six_trials_without_correction_warns(tmp_path, monkeypatch):
    d = _with(_doc(), attempts={"family": "参数层", "total_trials": 3 * 2,
                                "correction": {"applied": False, "method": "none"}})
    p = _write(tmp_path, d, monkeypatch)
    doc = yaml.safe_load(open(p, encoding="utf-8"))
    errors, warns = pc.check(doc)
    assert errors == [], "未校正只应 warn 不阻断：%s" % errors
    assert any("多重检验" in w for w in warns), "6 组未校正必须警告：%s" % warns
    assert pc.verdict_of(doc)[0] == "pass", "警告不改退出码"


def test_t11_five_trials_is_below_threshold(tmp_path, monkeypatch):
    d = _with(_doc(), attempts={"family": "参数层", "total_trials": 2 + 3,
                                "correction": {"applied": False, "method": "none"}})
    p = _write(tmp_path, d, monkeypatch)
    doc = yaml.safe_load(open(p, encoding="utf-8"))
    _, warns = pc.check(doc)
    assert not any("多重检验" in w for w in warns), "5 组以内不该警告：%s" % warns


def test_t12_applied_without_method_is_error(tmp_path, monkeypatch):
    d = _with(_doc(), attempts={"family": "参数层", "total_trials": 3 * 2,
                                "correction": {"applied": True, "method": "none"}})
    p = _write(tmp_path, d, monkeypatch)
    doc = yaml.safe_load(open(p, encoding="utf-8"))
    errors, warns = pc.check(doc)
    assert any("绕过门禁" in e for e in errors), "标校正却不给方法必须报错：%s" % errors
    assert not any("多重检验" in w for w in warns), "已 applied 就不该再警告未校正"


def test_t13_applied_with_real_method_clears_warning(tmp_path, monkeypatch):
    d = _with(_doc(), attempts={"family": "参数层", "total_trials": 3 * 2,
                                "correction": {"applied": True, "method": "bh_fdr"}})
    p = _write(tmp_path, d, monkeypatch)
    doc = yaml.safe_load(open(p, encoding="utf-8"))
    errors, warns = pc.check(doc)
    assert errors == [] and not any("多重检验" in w for w in warns)


# ── P1：成本敏感性（cost_sensitivity）──
def test_t14_derived_cost_grid_computed_from_spread(tmp_path, monkeypatch):
    d = _with(_doc(spread=GROSS), cost_sensitivity={"mode": "derived"})
    p = _write(tmp_path, d, monkeypatch)
    doc = yaml.safe_load(open(p, encoding="utf-8"))
    cs = pc.cost_sensitivity_of(doc)
    assert [round(c, 2) for c, _ in cs["rows"]] == [0.20, 0.30, 0.50], cs["rows"]
    assert abs(cs["rows"][1][1] - (GROSS - 0.30)) < 1e-9, "净上限必须 = 毛 spread - 成本"
    assert cs["flips"] is True, "0.20 档为正、0.30 档为负，必须判翻转"
    assert abs(cs["flip_at"] - 0.30) < 1e-9, cs["flip_at"]
    _, warns = pc.check(doc)
    assert any("成本敏感性" in w and "脆弱" in w for w in warns)


def test_t15_measured_flips_contradiction_is_error(tmp_path, monkeypatch):
    d = _with(_doc(spread=GROSS), cost_sensitivity={
        "mode": "measured",
        "grid_pct": [0.20, 0.30, 0.50],
        "net_upper_pct": {0.20: 0.06, 0.30: -0.04, 0.50: -0.24},
        "flips": False,
    })
    p = _write(tmp_path, d, monkeypatch)
    doc = yaml.safe_load(open(p, encoding="utf-8"))
    errors, _ = pc.check(doc)
    assert any("矛盾" in e for e in errors), "自报 flips 与三档数字冲突必须报错：%s" % errors


def test_t16_not_applicable_needs_reason(tmp_path, monkeypatch):
    p = _write(tmp_path, _with(_doc(), cost_sensitivity={"mode": "not_applicable"}),
               monkeypatch)
    doc = yaml.safe_load(open(p, encoding="utf-8"))
    _, warns = pc.check(doc)
    assert any("reason" in w for w in warns), "标不适用却不写理由等于跳过：%s" % warns


# ── P2：reality-check 格式的复核记录 ──
def test_t17_gate_report_shape_and_verdict(tmp_path, monkeypatch):
    d = _with(_doc(upper=-0.04, threshold=0.0, spread=GROSS),
              attempts={"family": "参数层", "total_trials": 3 * 2,
                        "correction": {"applied": False, "method": "none"}})
    p = _write(tmp_path, d, monkeypatch)
    doc = yaml.safe_load(open(p, encoding="utf-8"))
    errors, warns = pc.check(doc)
    verdict, _ = pc.verdict_of(doc)
    g = pc.gate_report(doc, verdict, errors, warns)
    for k in ("item", "verdict", "evidence", "note", "scope", "triggers", "final"):
        assert k in g and g[k], "复核记录缺字段 %s" % k
    assert g["verdict"] == "不成立", g["verdict"]
    assert g["final"] == "不放行", g["final"]
    assert "净上限" in g["evidence"] and "同族" in g["evidence"]


def test_t18_gate_pass_with_warns_is_conditional(tmp_path, monkeypatch):
    p = _write(tmp_path, _with(_doc(), cost_sensitivity={"mode": "derived"}), monkeypatch)
    doc = yaml.safe_load(open(p, encoding="utf-8"))
    errors, warns = pc.check(doc)
    verdict, _ = pc.verdict_of(doc)
    g = pc.gate_report(doc, verdict, errors, warns)
    assert verdict == "pass"
    assert g["final"] == ("有条件交付" if warns else "可交付"), g["final"]

