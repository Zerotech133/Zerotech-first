# -*- coding: utf-8 -*-
"""调参实验预判单（P-1 门禁）：跑之前先证明这层信息有没有信号。

背景（2026-11-11 立规）：
    P63 地板 / P64 提权 / P64 阈值 / P66 仓位 / P68 排除项 / P69 质量因子——
    六次实验调的是同一个 beta（1.18~1.33），全部否决。问题不在"没调对"，
    在于「先跑一遍看结果」这个顺序本身：六次里有五次，在跑之前就能判定
    上限不足。白工的成本不只是算力，是六份互不相干的报告撑不起一条知识线。

三条铁律（本模块强制执行）：
    1. 信息上限：这层信息理论上最多能解释多少收益（必须给数，且已扣成本）
    2. 放弃线：净上限低于多少就直接弃（必须给数）
    3. 证伪条件：如果假设成立应该观察到什么（必须带数值，不许写空话）

    未填完 → verify 退出码 1；填完但上限低于放弃线 → 退出码 2（预判劝退，
    这个实验根本不该开始）。

用法：
    python experiment_precheck.py init P70 --layer "事件驱动-龙虎榜" \\
        --hypothesis "上榜资金次日跟随可获超额"
    python experiment_precheck.py verify P70            # 0=可做 1=不完整 2=劝退
    python experiment_precheck.py list                  # 列全部预判单
    python experiment_precheck.py verify P70 --json     # 机器可读

在调参脚本里当门禁用：
    from experiment_precheck import require_pass
    require_pass("P70")      # 不通过直接 raise，实验跑不起来

产物：quant_backtest/experiments/<EXP_ID>/precheck.yaml
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
EXP_ROOT = os.path.join(HERE, "experiments")

RC_OK = 0
RC_INCOMPLETE = 1
RC_ABANDON = 2

METHODS = {
    "spread": "完美分层毛收益法：分位多空 spread - 双边成本",
    "fundamental_law": "基本面法则：|IC_ub| x sqrt(BR) x sigma，年化超额",
    "variance": "可解释方差法：R^2 x 全市场收益方差",
}

NUM_FIELDS = [
    ("cap", "upper_bound_pct"),
    ("cap", "cost_pct"),
    ("abandon", "threshold_pct"),
    ("falsify", "monotonicity_rho_min"),
    ("falsify", "t_min"),
]

TEXT_FIELDS = [
    ("meta", "exp_id"),
    ("meta", "layer"),
    ("meta", "hypothesis"),
    ("cap", "basis"),
    ("abandon", "rationale"),
]


def exp_dir(exp_id: str) -> str:
    return os.path.join(EXP_ROOT, str(exp_id))


def exp_path(exp_id: str) -> str:
    return os.path.join(exp_dir(exp_id), "precheck.yaml")


def _num(v):
    """严格取数：bool 不算数字（避免 True 被当 1 混过去）。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def template(exp_id: str, layer: str, hypothesis: str) -> dict:
    return {
        "meta": {
            "exp_id": exp_id,
            "layer": layer,
            "hypothesis": hypothesis,
            "created": _dt.date.today().isoformat(),
            "status": "draft",
            "verdict": None,
        },
        "cap": {
            "method": "spread",
            "upper_bound_pct": "TODO",
            "cost_pct": "TODO",
            "basis": "TODO 哪张表 / 哪段窗口 / 多少只票，例如 ts_daily 20240101-20261110 全A",
            "inputs": {
                "quantiles": 10,
                "holding_days": "TODO",
                "spread_gross_pct": "TODO",
                "cost_note": "双边佣金+印花+冲击，A股默认 0.30pct",
            },
            "note": "完美分层毛收益法：先算不扣成本的最优分位多空差，再减 cost_pct。"
                    "净上限 <= 0 就该弃，因为再怎么调参也变不成钱。",
        },
        "abandon": {
            "threshold_pct": "TODO",
            "rationale": "TODO 例如 净上限低于 0.5pct/年（无风险利率一半）就弃",
        },
        "falsify": {
            "if_true": [
                "TODO 带数值，例：按信号分 10 档，单调性 |rho| >= 0.8",
                "TODO 带数值，例：IS 与 OOS 符号一致且 OOS t >= 2.0",
            ],
            "if_false": [
                "TODO 带数值，例：分档倒 U 或 rho 符号与预期相反",
            ],
            "monotonicity_rho_min": "TODO",
            "t_min": "TODO",
            "is_oos_sign_agree": True,
            "sample_note": "TODO 至少多少笔 / 多少个截面才算有效样本",
        },
        "selfcheck": {
            "reference": "TODO 拿哪个已知结果反证（例：P67 的 maxret20 IC=-0.0668）",
            "tolerance_pct": "TODO 允许偏差百分比，建议 5",
            "note": "强烈建议填。P69 首版 IC 差 4.5 倍，是靠 P67 已知因子反证才发现的；"
                    "没有参照物的新实现，大概率带着口径错误产出'看起来有结论'的结果。",
        },
    }


def cmd_init(args) -> int:
    d = exp_dir(args.exp_id)
    os.makedirs(d, exist_ok=True)
    p = exp_path(args.exp_id)
    if os.path.exists(p) and not args.force:
        sys.stderr.write("已存在：%s\n要覆盖加 --force\n" % p)
        return RC_INCOMPLETE
    with open(p, "w", encoding="utf-8") as fh:
        yaml.safe_dump(template(args.exp_id, args.layer, args.hypothesis),
                       fh, allow_unicode=True, sort_keys=False, default_flow_style=False)
    print("已生成预判单：%s" % p)
    print("三块必填：cap 信息上限 / abandon 放弃线 / falsify 证伪条件")
    print("填完跑：python experiment_precheck.py verify %s" % args.exp_id)
    return RC_OK


def _is_placeholder(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        s = v.strip()
        return (not s) or s.upper().startswith("TODO")
    return False


def check(doc: dict) -> tuple[list, list]:
    """返回 (errors, warns)。errors 非空 = 不完整；warns 只提示不阻断。"""
    errors, warns = [], []

    for sec, key in TEXT_FIELDS:
        v = (doc.get(sec) or {}).get(key)
        if _is_placeholder(v):
            errors.append("%s.%s 未填" % (sec, key))

    method = (doc.get("cap") or {}).get("method")
    if method not in METHODS:
        errors.append("cap.method 必须是 %s 之一（当前 %r）" % ("/".join(METHODS), method))

    for sec, key in NUM_FIELDS:
        v = (doc.get(sec) or {}).get(key)
        if _is_placeholder(v):
            errors.append("%s.%s 未填（必须是数字）" % (sec, key))
        elif _num(v) is None:
            errors.append("%s.%s 不是合法数字（%r）" % (sec, key, v))

    cap = doc.get("cap") or {}
    inputs = cap.get("inputs") or {}
    if method == "spread":
        for k in ("spread_gross_pct", "holding_days"):
            if _is_placeholder(inputs.get(k)):
                errors.append("cap.inputs.%s 未填（method=spread 必需）" % k)
    elif method == "fundamental_law":
        for k in ("ic_upper", "breadth_br", "sigma_annual_pct"):
            if _is_placeholder(inputs.get(k)):
                errors.append("cap.inputs.%s 未填（method=fundamental_law 必需）" % k)
    elif method == "variance":
        for k in ("r_squared", "annual_vol_pct"):
            if _is_placeholder(inputs.get(k)):
                errors.append("cap.inputs.%s 未填（method=variance 必需）" % k)

    fal = doc.get("falsify") or {}
    if_true = fal.get("if_true") or []
    if not isinstance(if_true, list) or len(if_true) < 2:
        errors.append("falsify.if_true 至少 2 条")
    else:
        for i, s in enumerate(if_true, 1):
            if _is_placeholder(s):
                errors.append("falsify.if_true 第 %d 条未填" % i)
            elif not re.search(r"\d", str(s)):
                errors.append("falsify.if_true 第 %d 条没有数值：%r（证伪条件必须可量化）" % (i, s))
    if_false = fal.get("if_false") or []
    if not isinstance(if_false, list) or len(if_false) < 1:
        errors.append("falsify.if_false 至少 1 条")
    else:
        for i, s in enumerate(if_false, 1):
            if _is_placeholder(s):
                errors.append("falsify.if_false 第 %d 条未填" % i)

    sc = doc.get("selfcheck") or {}
    if _is_placeholder(sc.get("reference")) or _is_placeholder(sc.get("tolerance_pct")):
        warns.append("selfcheck 未填：没有口径自证基准，结果无法判断是信号还是 bug")

    return errors, warns


def verdict_of(doc: dict) -> tuple[str, str]:
    """返回 (verdict, 说明)。verdict: pass | abandon | incomplete"""
    errors, _ = check(doc)
    if errors:
        return "incomplete", "预判单不完整"
    upper = _num((doc["cap"]).get("upper_bound_pct"))
    thr = _num((doc["abandon"]).get("threshold_pct"))
    if upper is None or thr is None:
        return "incomplete", "上限或放弃线读不出数字"
    if upper <= thr:
        return "abandon", ("净上限 %+.2f%% <= 放弃线 %+.2f%%，此实验不应开始"
                           % (upper, thr))
    return "pass", "净上限 %+.2f%% > 放弃线 %+.2f%%，可以开跑" % (upper, thr)


def cmd_verify(args) -> int:
    p = args.target if args.target.endswith((".yaml", ".yml")) else exp_path(args.target)
    if not os.path.exists(p):
        sys.stderr.write("找不到预判单：%s\n先跑 init\n" % p)
        return RC_INCOMPLETE
    with open(p, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}

    errors, warns = check(doc)
    verdict, msg = verdict_of(doc)

    if args.json:
        print(json.dumps({"path": p, "verdict": verdict, "message": msg,
                          "errors": errors, "warns": warns}, ensure_ascii=False, indent=1))
    else:
        print("预判单 %s" % p)
        if verdict == "incomplete":
            print("结论：不完整，不能开跑")
            for e in errors:
                print("  [缺] %s" % e)
        elif verdict == "abandon":
            print("结论：预判劝退 —— %s" % msg)
            print("  cap.upper_bound_pct = %s / abandon.threshold_pct = %s"
                  % (doc["cap"]["upper_bound_pct"], doc["abandon"]["threshold_pct"]))
        else:
            print("结论：通过 —— %s" % msg)
            print("  层：%s" % doc["meta"]["layer"])
            print("  假设：%s" % doc["meta"]["hypothesis"])
        for w in warns:
            print("  [建议] %s" % w)

    if args.strict and warns:
        return RC_INCOMPLETE
    return {"pass": RC_OK, "abandon": RC_ABANDON}.get(verdict, RC_INCOMPLETE)


def cmd_list(args) -> int:
    if not os.path.isdir(EXP_ROOT):
        print("还没有任何预判单（%s 不存在）" % EXP_ROOT)
        return RC_OK
    rows = []
    for name in sorted(os.listdir(EXP_ROOT)):
        p = exp_path(name)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        v, msg = verdict_of(doc)
        rows.append((name, (doc.get("meta") or {}).get("layer", ""), v, msg))
    if not rows:
        print("还没有任何预判单")
        return RC_OK
    print("%-14s %-22s %-11s %s" % ("EXP", "层", "结论", "说明"))
    for n, layer, v, msg in rows:
        print("%-14s %-22s %-11s %s" % (n, str(layer)[:20], v, msg))
    return RC_OK


def require_pass(exp_id: str) -> dict:
    """给调参脚本用的门禁。未通过直接 raise，实验跑不起来。"""
    p = exp_path(exp_id)
    if not os.path.exists(p):
        raise RuntimeError("实验 %s 没有预判单（%s）。先跑 experiment_precheck.py init" % (exp_id, p))
    with open(p, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    v, msg = verdict_of(doc)
    if v != "pass":
        raise RuntimeError("实验 %s 预判未通过（%s）：%s" % (exp_id, v, msg))
    return doc


def main():
    ap = argparse.ArgumentParser(description="调参实验预判单门禁")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="生成预判单模板")
    p_init.add_argument("exp_id")
    p_init.add_argument("--layer", default="TODO 这一层信息是什么，例如 公开季度财务-质量因子")
    p_init.add_argument("--hypothesis", default="TODO 一句话假设，必须可证伪")
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=cmd_init)

    p_v = sub.add_parser("verify", help="校验预判单（0 可做 / 1 不完整 / 2 劝退）")
    p_v.add_argument("target", help="EXP_ID 或 yaml 路径")
    p_v.add_argument("--json", action="store_true")
    p_v.add_argument("--strict", action="store_true", help="有 warn 也算不通过")
    p_v.set_defaults(func=cmd_verify)

    p_l = sub.add_parser("list", help="列全部预判单")
    p_l.set_defaults(func=cmd_list)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
