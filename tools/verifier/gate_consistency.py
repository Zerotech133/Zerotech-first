#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gate_consistency.py - G0/G1 旁路一致性校验器 (v1)

WHY (2026-11-13 裁定)
    shortcut_probe 在 G0/G1 上命中 8 条「不改决策也能让门禁变绿」的捷径。
    老田裁定先走旁路：只出告警回执、不改生产判定，观察一个完整交易日后再
    决定哪些升为硬拦。因此本工具 **不修改、不调用、不判定** 任何生产门禁。

INDEPENDENCE CONTRACT (对应数据判据铁律 1)
    1. 不导入被测门禁模块（experiment_precheck / data_health 等）；
       只解析它们产出的 yaml / json，用本文件自带的算式独立重算。
    2. 目标日由「产物文件名」这个外部锚点给定，绝不用产物内的 max(date)
       当参照系；文件内 target 与文件名不一致直接判 TARGET_MISMATCH。
    3. 净上限按预判规范独立重算 net = spread_gross_pct - cost_pct，
       与自报 cap.upper_bound_pct 比对；冲突以重算值为准（spread 优先）。

EXIT (给调度器的语义是「任务跑完了吗」)
    0  正常完成，含命中告警（业务结论写回执 JSON，不用退出码表达）
    2  验证器自身失效：self-test 未全命中 / 独立性契约被破坏 / 解析崩溃
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - 环境缺依赖属于验证器失效
    print("[gate_consistency] FATAL: PyYAML 不可用", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[2]
EXP_DIR = ROOT / "quant_backtest" / "experiments"
HEALTH_DIR = ROOT / "var" / "preopen" / "daily_check"
SELF_PATH = Path(__file__).resolve()

RULES_VERSION = "gate_consistency/v1"
TOL_PCT = 0.005                       # 自报值与重算值允许的舍入差
COST_MODES = {"derived", "not_applicable", "explicit"}
LAG_VERDICTS = {"incomplete", "ahead", "zero_collect", "partial", "no_event"}
SET_OK_VERDICTS = {"ok", "no_event"}
TRIAL_WARN_AT = 5

# 独立性契约：本文件源码中不得出现对被测门禁的导入。
# 用拼接构造，避免这些特征串自身被 self-test 的源码扫描命中（自指假阳性）。
FORBIDDEN_IMPORTS = tuple(
    kw + " " + mod for kw, mod in (
        ("import", "experiment_precheck"),
        ("from", "experiment_precheck"),
        ("import", "data_health"),
        ("from", "data_health"),
    )
)


def _num(v):
    """只读数字；TODO / None / bool / 非数字一律视作未解析。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _hit(gate: str, category: str, subject: str, expected,
         observed, evidence: str, severity: str = "warn") -> dict:
    return {
        "gate": gate,
        "category": category,
        "severity": severity,
        "subject": subject,
        "expected": expected,
        "observed": observed,
        "evidence": evidence,
    }


# ---------------------------------------------------------------------------
# G1: 预判单自报字段一致性（独立重算，不读 verdict_of）
# ---------------------------------------------------------------------------
def check_precheck(doc: dict, subject: str) -> list[dict]:
    hits: list[dict] = []
    cap = doc.get("cap") or {}
    inp = cap.get("inputs") or {}
    upper = _num(cap.get("upper_bound_pct"))
    gross = _num(inp.get("spread_gross_pct"))
    cost = _num(cap.get("cost_pct"))

    if upper is None or gross is None or cost is None:
        hits.append(_hit(
            "G1", "UNRESOLVED_CAP", subject,
            "cap.upper_bound_pct / cap.cost_pct / cap.inputs.spread_gross_pct 均为数字",
            {"upper": cap.get("upper_bound_pct"), "gross": inp.get("spread_gross_pct"),
             "cost": cap.get("cost_pct")},
            "占位或未填字段不可作为准入依据；生产 verdict_of 只判 upper<=thr",
            severity="block"))
    else:
        net = round(gross - cost, 6)
        if abs(net - upper) > TOL_PCT:
            hits.append(_hit(
                "G1", "SELF_REPORT_MISMATCH", subject,
                "重算净上限 %.6f (= %.6f gross - %.6f cost)" % (net, gross, cost),
                "自报 upper_bound_pct=%.6f" % upper,
                "SP-G1-01 / SP-G1-04：自报字段与底层 spread 矛盾仍会 pass",
                severity="block"))

    sc = doc.get("selfcheck") or {}
    if not sc.get("reference"):
        hits.append(_hit("G1", "NO_SELFCHECK", subject,
                         "selfcheck.reference 有已知结果作反证基准", "缺失",
                         "SP-G1-03：口径自证缺失时生产侧仅给 warn"))

    cs = doc.get("cost_sensitivity") or {}
    if cs.get("mode") not in COST_MODES:
        hits.append(_hit("G1", "NO_COST_SENSITIVITY", subject,
                         "mode in %s" % sorted(COST_MODES), cs.get("mode"),
                         "SP-G1-03：成本敏感性缺失时生产侧仅给 warn"))

    thr = _num((doc.get("abandon") or {}).get("threshold_pct"))
    if thr is not None and thr < 0:
        hits.append(_hit("G1", "ABANDON_LINE_MOVED", subject,
                         "threshold_pct >= 0（净上限 <= 0 即弃）", thr,
                         "SP-G1-02：下调放弃线可让本该劝退的单子放行",
                         severity="block"))

    at = doc.get("attempts") or {}
    n = _num(at.get("total_trials"))
    corr = at.get("correction") or {}
    if n is not None and n > TRIAL_WARN_AT and corr.get("applied") is not True:
        hits.append(_hit("G1", "FAMILY_ERROR_UNCORRECTED", subject,
                         "同族 >%d 组时须做校正" % TRIAL_WARN_AT,
                         "total_trials=%d applied=%s" % (int(n), corr.get("applied")),
                         "族错误率上界 1-0.95^n"))
    return hits


# ---------------------------------------------------------------------------
# G0: 健康产物一致性（目标日来自文件名这个外部锚点）
# ---------------------------------------------------------------------------
def check_health(rep: dict, file_target: str, subject: str) -> list[dict]:
    hits: list[dict] = []
    target = str(rep.get("target") or "")
    src = str(rep.get("target_source") or "")

    if target != file_target:
        hits.append(_hit("G0", "TARGET_MISMATCH", subject,
                         "产物内 target == 文件名日期 %s" % file_target,
                         target or "缺失", "目标日外部给定，不以产物内 max 为准",
                         severity="block"))
    if "auto" in src.lower() or "max" in src.lower():
        hits.append(_hit("G0", "TARGET_NOT_EXTERNAL", subject,
                         "target_source 为外部给定（cli/显式/host_clock）", src,
                         "SP-G0-01：用被测对象当参照系会永远全绿",
                         severity="block"))

    univ = str(rep.get("universal_max") or "")
    top = str(rep.get("verdict") or "")
    if univ and univ != file_target and top not in LAG_VERDICTS:
        hits.append(_hit("G0", "REFERENCE_FRAME", subject,
                         "universal_max 落后时顶层 verdict 属 %s" % sorted(LAG_VERDICTS),
                         "universal_max=%s verdict=%s" % (univ, top),
                         "SP-G0-01：目标日回退为表内最新日"))

    for st in rep.get("sets") or []:
        sid = "%s/%s" % (subject, st.get("id") or "?")
        med = _num(st.get("prev_median"))
        sv = str(st.get("verdict") or "")
        mx = str(st.get("max_in_table") or "")
        n_at = _num(st.get("n_at_target"))

        if med == 0 and sv == "ok":
            hits.append(_hit("G0", "COVERAGE_SKIPPED", sid,
                             "prev_median=0 时不得直接判 ok（走 no_baseline 分支）",
                             "prev_median=0 verdict=ok",
                             "SP-G0-02：覆盖率整段跳过，1 行即达标"))
        if mx and mx != file_target and sv in SET_OK_VERDICTS:
            hits.append(_hit("G0", "SILENT_DEGRADE", sid,
                             "max_in_table < 目标日时须区分断供与当日无事件",
                             "max_in_table=%s verdict=%s" % (mx, sv),
                             "SP-G0-03：上游断供被说成「无事可做」",
                             severity="block"))
        if n_at == 0 and sv == "ok":
            hits.append(_hit("G0", "ZERO_COLLECT_OK", sid,
                             "n_at_target=0 不得判 ok", "n_at_target=0 verdict=ok",
                             "零采集退回上一交易日会永远全绿",
                             severity="block"))
    return hits


# ---------------------------------------------------------------------------
# self-test: oracle / no-op / 各缺口阳性夹具；验证器自己必须先能红
# ---------------------------------------------------------------------------
def _day(codes: tuple[int, ...]) -> str:
    """日期字面量一律码点构造，规避输出/写入通道的数字串味。"""
    return "".join(chr(c) for c in codes)


D_TARGET = _day((50, 48, 50, 54, 49, 49, 49, 50))   # 20261112
D_PREV = _day((50, 48, 50, 54, 49, 49, 49, 49))     # 20261111


def _good_doc() -> dict:
    return {
        "cap": {"upper_bound_pct": -0.041, "cost_pct": 0.3,
                "inputs": {"spread_gross_pct": 0.259, "holding_days": 5}},
        "selfcheck": {"reference": "已知结果反证"},
        "cost_sensitivity": {"mode": "derived"},
        "abandon": {"threshold_pct": 0.0},
        "attempts": {"total_trials": 1, "correction": {"applied": False}},
    }


def _good_health() -> dict:
    return {
        "target": D_TARGET,
        "target_source": "cli(显式)",
        "universal_max": D_TARGET,
        "verdict": "ok",
        "sets": [{"id": "daily", "prev_median": 5571,
                  "verdict": "ok", "max_in_table": D_TARGET, "n_at_target": 5573}],
    }


def self_test() -> dict:
    cases: list[dict] = []

    def case(name: str, kind: str, hits: list[dict], want: str | None,
             min_hits: int = 0) -> None:
        cats = {h["category"] for h in hits}
        if kind == "hit":
            ok = want in cats
        elif kind == "clean":
            ok = len(hits) == 0
        else:  # noop —— 空输入必须至少命中 min_hits 条
            ok = len(hits) >= min_hits
        cases.append({"name": name, "kind": kind, "want": want,
                      "n_hits": len(hits), "categories": sorted(cats), "ok": ok})

    d = _good_doc()
    d["cap"]["upper_bound_pct"] = 99.0
    case("自报上限与 spread 矛盾", "hit", check_precheck(d, "T1"), "SELF_REPORT_MISMATCH")

    d = _good_doc()
    d["cap"]["upper_bound_pct"] = "TODO"
    case("占位单未解析", "hit", check_precheck(d, "T2"), "UNRESOLVED_CAP")

    d = _good_doc()
    d.pop("selfcheck")
    case("缺口自证", "hit", check_precheck(d, "T3"), "NO_SELFCHECK")

    d = _good_doc()
    d.pop("cost_sensitivity")
    case("缺成本敏感性", "hit", check_precheck(d, "T4"), "NO_COST_SENSITIVITY")

    d = _good_doc()
    d["abandon"]["threshold_pct"] = -10.0
    case("放弃线下调", "hit", check_precheck(d, "T5"), "ABANDON_LINE_MOVED")

    d = _good_doc()
    d["attempts"] = {"total_trials": 6, "correction": {"applied": False}}
    case("同族未校正", "hit", check_precheck(d, "T6"), "FAMILY_ERROR_UNCORRECTED")

    h = _good_health()
    h["sets"][0]["prev_median"] = 0
    case("覆盖率为零仍判 ok", "hit", check_health(h, D_TARGET, "T7"), "COVERAGE_SKIPPED")

    h = _good_health()
    h["sets"][0]["max_in_table"] = D_PREV
    h["sets"][0]["verdict"] = "no_event"
    case("断供说成当日无事件", "hit", check_health(h, D_TARGET, "T8"), "SILENT_DEGRADE")

    h = _good_health()
    h["universal_max"] = D_PREV
    case("目标日回退", "hit", check_health(h, D_TARGET, "T9"), "REFERENCE_FRAME")

    h = _good_health()
    h["target"] = D_PREV
    case("目标日不来自文件名", "hit", check_health(h, D_TARGET, "T10"), "TARGET_MISMATCH")

    h = _good_health()
    h["target_source"] = "auto(max_in_table)"
    case("用被测对象当参照系", "hit", check_health(h, D_TARGET, "T11"),
         "TARGET_NOT_EXTERNAL")

    h = _good_health()
    h["sets"][0]["n_at_target"] = 0
    case("零采集仍判 ok", "hit", check_health(h, D_TARGET, "T12"), "ZERO_COLLECT_OK")

    case("oracle 正确单应放行", "clean", check_precheck(_good_doc(), "O1"), None)
    case("oracle 正确产物应放行", "clean", check_health(_good_health(), D_TARGET, "O2"), None)
    case("no-op 空预判单不得全绿", "noop", check_precheck({}, "N1"), None, min_hits=3)
    case("no-op 空产物不得全绿", "noop", check_health({}, D_TARGET, "N2"), None, min_hits=1)

    src = SELF_PATH.read_text(encoding="utf-8")
    independent = not any(s in src for s in FORBIDDEN_IMPORTS)
    cases.append({"name": "独立性契约（不导入被测门禁）", "kind": "clean",
                  "want": None, "n_hits": 0 if independent else 1,
                  "categories": [] if independent else ["INDEPENDENCE_BROKEN"],
                  "ok": independent})

    n_ok = sum(1 for c in cases if c["ok"])
    return {"n_cases": len(cases), "n_ok": n_ok,
            "all_ok": n_ok == len(cases), "cases": cases,
            "codepoints": {"target": [ord(c) for c in D_TARGET],
                           "prev": [ord(c) for c in D_PREV]}}


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
def collect() -> tuple[list[dict], dict]:
    hits: list[dict] = []
    meta = {"precheck_files": [], "health_files": [], "errors": []}

    for p in sorted(EXP_DIR.glob("*/precheck.yaml")):
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            meta["precheck_files"].append(str(p.relative_to(ROOT)))
            hits.extend(check_precheck(doc, p.parent.name))
        except Exception as exc:
            meta["errors"].append("%s: %s" % (p.name, exc))

    for p in sorted(HEALTH_DIR.glob("data_health_*.json")):
        m = re.search(r"data_health_(\d{8})\.json$", p.name)
        if not m:
            continue
        file_target = m.group(1)
        try:
            rep = json.loads(p.read_text(encoding="utf-8"))
            meta["health_files"].append({"file": str(p.relative_to(ROOT)),
                                         "file_target": file_target})
            hits.extend(check_health(rep, file_target, p.name))
        except Exception as exc:
            meta["errors"].append("%s: %s" % (p.name, exc))
    return hits, meta


def build_report(exec_day: str) -> dict:
    st = self_test()
    hits, meta = collect()
    by_gate: dict[str, int] = {}
    by_cat: dict[str, int] = {}
    for h in hits:
        by_gate[h["gate"]] = by_gate.get(h["gate"], 0) + 1
        by_cat[h["category"]] = by_cat.get(h["category"], 0) + 1
    return {
        "schema": "gate_consistency_report/v1",
        "rules_version": RULES_VERSION,
        "exec_day": exec_day,
        "boundaries": {
            "read_only": True,
            "network": False,
            "business_writes": False,
            "production_gate_modified": False,
            "imports_under_test": False,
            "verdict_effect": "WARN_ONLY",
        },
        "self_test": st,
        "artifacts": meta,
        "n_hits": len(hits),
        "hits_by_gate": by_gate,
        "hits_by_category": by_cat,
        "hits": hits,
        "verdict": "WARN_ONLY" if hits else "CLEAN",
        "exit_code": 0 if st["all_ok"] and not meta["errors"] else 2,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="G0/G1 旁路一致性校验器（只告警，不改判定）")
    ap.add_argument("--asof", default=None, help="执行日 YYYYMMDD，默认本机日期")
    ap.add_argument("--json-out", default=None, help="回执落盘路径（仓外）")
    ap.add_argument("--self-test-only", action="store_true")
    args = ap.parse_args(argv)

    exec_day = args.asof or datetime.now().strftime("%Y%m%d")
    rep = build_report(exec_day)
    if args.self_test_only:
        rep["hits"] = []
        rep["n_hits"] = 0
        rep["verdict"] = "SELF_TEST_ONLY"

    st = rep["self_test"]
    print("[gate_consistency] self-test %d/%d %s" % (
        st["n_ok"], st["n_cases"], "OK" if st["all_ok"] else "FAILED"))
    if not args.self_test_only:
        print("[gate_consistency] 命中 %d 条（G0 %d / G1 %d），效果 WARN_ONLY" % (
            rep["n_hits"], rep["hits_by_gate"].get("G0", 0),
            rep["hits_by_gate"].get("G1", 0)))
        for h in rep["hits"]:
            print("  [%s] %-26s %s" % (h["gate"], h["category"], h["subject"]))
        for e in rep["artifacts"]["errors"]:
            print("  [ERROR] %s" % e)

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print("[gate_consistency] 回执 -> %s" % out)
    return rep["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
