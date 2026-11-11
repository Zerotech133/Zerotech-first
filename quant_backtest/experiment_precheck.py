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

两项记账（2026-11-11 P1，补 reality-check 指出的两个漏项）：
    4. attempts：这组假设是同族第几次尝试（含历史）。同族 > 5 组且未标校正
       → verify 警告并给出族错误率上界；标了 applied=true 却不给方法 → 直接报错
       （防止用"我校正过了"一句话绕过多重检验）。
    5. cost_sensitivity：0.20 / 0.30 / 0.50 三档成本下净上限是否击穿放弃线。
       默认 derived，由 cap.inputs.spread_gross_pct 自动减各档成本算出，
       不用自报；两组对照型实验可标 not_applicable 并写 reason。
    两者都只 warn（不阻断、不改退出码），--strict 下才拦。

verify 输出尾部固定附一段 reality-check 格式的复核记录
（ITEM / VERDICT / EVIDENCE / NOTE + 汇总判定），与人工核对盘后复盘的
写法一致，便于直接贴进台账。

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

# ── 记账扩展（2026-11-11 P1，对应 reality-check 的「多重检验」与「成本敏感性」）──
TRIAL_WARN_AT = 5            # 同族累计超过 5 组且未标校正 → 警告
ALPHA = 0.05                 # 单次检验假定显著性水平，只用于算族错误率上界
DEFAULT_COST_GRID = (0.20, 0.30, 0.50)
CORRECTION_METHODS = ("bonferroni", "bh_fdr", "deflated_sharpe")


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
        "attempts": {
            "family": "TODO 同族名称：共用同一段样本、反复调同一批参数的一组假设，"
                      "例如 参数层-仓位与阈值",
            "total_trials": "TODO 含历史同族的累计第几组（P63/P64地板/P64提权/P66/P68/P69 "
                            "这六张单子应记 6）",
            "same_sample": True,
            "correction": {
                "applied": False,
                "method": "none",
                "note": "同族超过 %d 组仍 applied=false，verify 会警告并给出族错误率上界；"
                        "确实校正过就把 method 改成 %s 之一并置 applied=true"
                        % (TRIAL_WARN_AT, " / ".join(CORRECTION_METHODS)),
            },
        },
        "cost_sensitivity": {
            "mode": "derived",
            "grid_pct": [0.20, 0.30, 0.50],
            "net_upper_pct": {},
            "reason": "",
            "note": "derived = 用 cap.inputs.spread_gross_pct 减各档成本自动算（推荐，不用自报）；"
                    "measured = 手工填 net_upper_pct 三档（键为成本档）；"
                    "not_applicable = 成本网格不适用（如闸门那种两组对照），须填 reason",
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


def attempts_check(doc: dict) -> tuple[list, list]:
    """多重检验记账。返回 (errors, warns)。

    errors 只给一种情形：标了 applied=true 却不给有效方法——那是绕过门禁，不是提醒。
    其余一律 warn，不影响退出码（除非 --strict）。
    """
    errors, warns = [], []
    at = doc.get("attempts") or {}
    corr = at.get("correction") or {}
    n = _num(at.get("total_trials"))

    if n is None:
        warns.append("attempts.total_trials 未填：这是同族第几组不清楚，多重检验无从打折")
        return errors, warns

    if corr.get("applied") is True:
        method = corr.get("method")
        if method not in CORRECTION_METHODS:
            errors.append("attempts.correction.applied=true 但 method=%r 不是有效方法（%s）："
                          "标校正却不给方法等于绕过门禁" % (method, " / ".join(CORRECTION_METHODS)))
    elif n > TRIAL_WARN_AT:
        fw = (1.0 - (1.0 - ALPHA) ** int(n)) * 100.0
        warns.append("多重检验：同族累计第 %d 组、未标校正 → 族错误率上界 %.0f%%"
                     "（1-0.95^%d）；结论应降为「存疑」并在台账登记试验总数，"
                     "或补 %s" % (int(n), fw, int(n), " / ".join(CORRECTION_METHODS)))
    return errors, warns


def cost_sensitivity_of(doc: dict) -> dict:
    """0.20 / 0.30 / 0.50 三档成本下净上限是否击穿放弃线。

    返回 dict：mode / rows / flips / flip_at / text / errors / warns
    """
    out = {"mode": "missing", "rows": [], "flips": None, "flip_at": None,
           "text": "未做", "errors": [], "warns": []}
    cs = doc.get("cost_sensitivity") or {}
    cap = doc.get("cap") or {}
    gross = _num((cap.get("inputs") or {}).get("spread_gross_pct"))
    thr = _num((doc.get("abandon") or {}).get("threshold_pct"))
    thr_use = 0.0 if thr is None else thr

    grid = [v for v in (_num(g) for g in (cs.get("grid_pct") or DEFAULT_COST_GRID))
            if v is not None]
    if len(grid) < 2:
        grid = list(DEFAULT_COST_GRID)

    mode = cs.get("mode")
    if _is_placeholder(mode):
        raw = cs.get("net_upper_pct") or {}
        mode = "measured" if raw else ("derived" if gross is not None else None)
    if mode is None:
        out["warns"].append("cost_sensitivity 未做：0.20/0.30/0.50 三档未测，"
                            "结论对成本假设的稳健性未知")
        return out

    if mode == "not_applicable":
        reason = cs.get("reason")
        if _is_placeholder(reason):
            out["warns"].append("cost_sensitivity.mode=not_applicable 但没填 reason："
                                "不适用也要写清为什么，否则等于跳过")
        else:
            out["mode"] = "not_applicable"
            out["text"] = "不适用（%s）" % _clip(reason, 50)
        return out

    if mode == "derived":
        out["mode"] = "derived"
        if gross is None:
            out["warns"].append("cost_sensitivity.mode=derived 但 "
                                "cap.inputs.spread_gross_pct 读不出数字")
            return out
        out["rows"] = [(c, gross - c) for c in grid]
    elif mode == "measured":
        out["mode"] = "measured"
        raw = cs.get("net_upper_pct") or {}
        norm = {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                kk, vv = _num(k), _num(v)
                if kk is not None and vv is not None:
                    norm[round(kk, 4)] = vv
        miss = [c for c in grid if round(c, 4) not in norm]
        if miss:
            out["warns"].append("cost_sensitivity.net_upper_pct 缺档：%s"
                                % " / ".join("%.2f" % c for c in miss))
        out["rows"] = [(c, norm[round(c, 4)]) for c in grid if round(c, 4) in norm]
    else:
        out["warns"].append("cost_sensitivity.mode=%r 不认识（用 derived / measured / "
                            "not_applicable）" % mode)
        return out

    rows = out["rows"]
    if len(rows) < 2:
        return out

    passed = [n > thr_use for _, n in rows]
    out["text"] = " ".join("%.2f档%+.3f" % (c, n) for c, n in rows)
    if any(passed) and not all(passed):
        out["flips"] = True
        out["flip_at"] = next(c for c, n in rows if n <= thr_use)
        out["warns"].append("成本敏感性：净上限在 %.2fpct 档击穿放弃线 %+.3fpct"
                            "（%s），结论只在更低成本下成立 → 标为脆弱"
                            % (out["flip_at"], thr_use, out["text"]))
    else:
        out["flips"] = False

    if out["mode"] == "measured":
        claimed = cs.get("flips")
        if claimed is None:
            out["warns"].append("cost_sensitivity.flips 未填（实测 %s）"
                                % ("会翻转" if out["flips"] else "不翻转"))
        elif isinstance(claimed, bool) and claimed != out["flips"]:
            out["errors"].append("cost_sensitivity.flips=%s 与自己填的三档数字矛盾"
                                 "（实测 %s）" % (claimed, "会翻转" if out["flips"] else "不翻转"))
    return out


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

    a_err, a_warn = attempts_check(doc)
    errors.extend(a_err)
    warns.extend(a_warn)

    cs = cost_sensitivity_of(doc)
    errors.extend(cs["errors"])
    warns.extend(cs["warns"])

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


def _clip(s, n: int) -> str:
    """截断并加省略号，避免复核记录里出现半截括号。"""
    t = str(s).strip()
    return t if len(t) <= n else t[:n].rstrip() + "…"


def gate_report(doc: dict, verdict: str, errors: list, warns: list,
                cs: dict = None) -> dict:
    """reality-check 格式的复核记录：ITEM / VERDICT / EVIDENCE / NOTE + 汇总。

    与人工核对盘后复盘的写法保持一致，可直接贴进台账或复盘正文。
    """
    meta = doc.get("meta") or {}
    cap = doc.get("cap") or {}
    if cs is None:
        cs = cost_sensitivity_of(doc)

    item = "%s · %s · %s" % (meta.get("exp_id") or "?",
                             _clip(meta.get("layer") or "?", 28),
                             _clip(meta.get("hypothesis") or "?", 60))
    vmap = {"pass": "成立", "abandon": "不成立", "incomplete": "存疑"}

    upper = _num(cap.get("upper_bound_pct"))
    thr = _num((doc.get("abandon") or {}).get("threshold_pct"))
    ev = []
    if upper is not None and thr is not None:
        ev.append("净上限 %+.3f%% vs 放弃线 %+.3f%%" % (upper, thr))
    ev.append("成本敏感性 %s" % _clip(cs.get("text", "未做"), 34))

    at = doc.get("attempts") or {}
    n = _num(at.get("total_trials"))
    corr = at.get("correction") or {}
    if n is None:
        ev.append("同族次数 未填")
    elif corr.get("applied") is True:
        ev.append("同族第 %d 组、已校正（%s）" % (int(n), corr.get("method")))
    elif n > TRIAL_WARN_AT:
        fw = (1.0 - (1.0 - ALPHA) ** int(n)) * 100.0
        ev.append("同族第 %d 组、未校正（族错误率上界 %.0f%%）" % (int(n), fw))
    else:
        ev.append("同族第 %d 组（未超 %d 组阈值，未校正亦不打折）"
                  % (int(n), TRIAL_WARN_AT))

    sc = doc.get("selfcheck") or {}
    if _is_placeholder(sc.get("reference")):
        ev.append("自证基准 未填")
    else:
        ev.append("自证基准 %s（容差 %s%%）"
                  % (_clip(sc.get("reference"), 40),
                     "未填" if _is_placeholder(sc.get("tolerance_pct"))
                     else sc.get("tolerance_pct")))

    sn = (doc.get("falsify") or {}).get("sample_note")
    ev.append("样本门槛 %s" % ("未填" if _is_placeholder(sn) else _clip(sn, 40)))

    if verdict == "pass":
        note = "三块必填齐、净上限过放弃线"
        note += "；无保留" if not warns else "；尚有 %d 条建议项未消（见上）" % len(warns)
    elif verdict == "abandon":
        note = "净上限未过放弃线，属信息上限不足而非填单不完整 → 实验不应开始"
    else:
        note = ("缺：%s" % "；".join(errors[:3])) if errors else "信息不全，无法判定"

    triggers = []
    if verdict == "incomplete":
        triggers.append("必填项缺失 %d 处" % len(errors))
    if verdict == "abandon":
        triggers.append("净上限 <= 放弃线")
    if any(("矛盾" in e) or ("绕过" in e) for e in errors):
        triggers.append("账内自相矛盾")

    if verdict in ("abandon", "incomplete"):
        final = "不放行"
    elif warns:
        final = "有条件交付"
    else:
        final = "可交付"

    return {
        "item": item,
        "verdict": vmap.get(verdict, "存疑"),
        "evidence": "；".join(ev),
        "note": note,
        "scope": "meta / cap / abandon / falsify / selfcheck + attempts + cost_sensitivity",
        "pass_n": 1 if verdict == "pass" else 0,
        "fail_n": 1 if verdict == "abandon" else 0,
        "doubt_n": 1 if verdict == "incomplete" else 0,
        "triggers": "、".join(triggers) if triggers else "无",
        "final": final,
        "followup": "；".join(warns) if warns else "无",
    }


def cmd_verify(args) -> int:
    p = args.target if args.target.endswith((".yaml", ".yml")) else exp_path(args.target)
    if not os.path.exists(p):
        sys.stderr.write("找不到预判单：%s\n先跑 init\n" % p)
        return RC_INCOMPLETE
    with open(p, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}

    errors, warns = check(doc)
    verdict, msg = verdict_of(doc)
    cs = cost_sensitivity_of(doc)
    gate = gate_report(doc, verdict, errors, warns, cs)

    if args.json:
        print(json.dumps({"path": p, "verdict": verdict, "message": msg,
                          "errors": errors, "warns": warns, "gate": gate},
                         ensure_ascii=False, indent=1))
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

        print("")
        print("── 质量门复核（reality-check 格式）──")
        print("ITEM: %s" % gate["item"])
        print("VERDICT: %s" % gate["verdict"])
        print("EVIDENCE: %s" % gate["evidence"])
        print("NOTE: %s" % gate["note"])
        print("")
        print("复核范围：  %s" % gate["scope"])
        print("成立：%d 条 · 不成立：%d 条 · 存疑：%d 条"
              % (gate["pass_n"], gate["fail_n"], gate["doubt_n"]))
        print("自动不放行触发项：%s" % gate["triggers"])
        print("最终判定：  %s" % gate["final"])
        if gate["final"] == "有条件交付":
            print("交付后需跟进：%s" % gate["followup"])

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
        n = _num((doc.get("attempts") or {}).get("total_trials"))
        rows.append((name, (doc.get("meta") or {}).get("layer", ""), v, msg,
                     "-" if n is None else str(int(n))))
    if not rows:
        print("还没有任何预判单")
        return RC_OK
    print("%-14s %-22s %-11s %-6s %s" % ("EXP", "层", "结论", "同族", "说明"))
    for n_, layer, v, msg, trials in rows:
        print("%-14s %-22s %-11s %-6s %s" % (n_, str(layer)[:20], v, trials, msg))
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
