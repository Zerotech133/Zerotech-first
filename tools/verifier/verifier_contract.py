# -*- coding: utf-8 -*-
"""verifier_contract/v1 —— 守卫类测试的三类夹具契约。

移植依据（智谱 GLM-5.3 验证器三检查，只取方法论层）：
    oracle      正确答案必须给分       -> 已知合法输入，守卫必须放行
    no-op       什么都不干不给分       -> 空输入/零采集/空变异，守卫必须拒绝
    unfinished  没做完不给分           -> 半完成/部分采集/未恢复，守卫必须拒绝

本仓对应判据（不是新发明，是把散落的铁律收成一条可执行契约）：
    · 断言不许用被测对象当参照系 —— tools/preopen/data_health.py 判例
    · 「数据未就绪」与「无事可做」必须分两种输出
    · 测试要过变异检验：把被测逻辑改回旧行为，断言必须失败
    · 「成功但没做那件事」比报错更危险

红线：
    · 只读生产源码，不改任何业务脚本、不写业务账本、不联网。
    · G0 夹具只在临时目录写 parquet，随用随删。
    · 缺任一类夹具 = INCOMPLETE，不许合并（rc=1）；no-op/unfinished 被放行 = FAIL（rc=2）。

用法：
    python tools/verifier/verifier_contract.py list
    python tools/verifier/verifier_contract.py verify --json-out <path>
退出码：0 全 PASS / 1 INCOMPLETE（缺夹具，不许合并） / 2 FAIL（存在假通过）
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DELIV = ROOT.parent / "deliverables"
VAR_AUDIT = ROOT / "var" / "audit"

DH_PATH = ROOT / "portal" / "daily_report" / "data_health.py"
EP_PATH = ROOT / "quant_backtest" / "experiment_precheck.py"

RULES_VERSION = "verifier_contract/v1"
KINDS = ("oracle", "no_op", "unfinished")
RC_PASS, RC_INCOMPLETE, RC_FAIL = 0, 1, 2


def D(seq):
    """日期/编号字面量一律码点构造，避免传输通道串味。"""
    return "".join(chr(c) for c in seq)


EXEC_DAY = D([50, 48, 50, 54, 49, 49, 49, 51])
PREV_DAY = D([50, 48, 50, 54, 49, 49, 49, 50])


@dataclasses.dataclass
class Case:
    kind: str
    id: str
    note: str
    expect_pass: bool
    payload: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class CaseResult:
    kind: str
    id: str
    passed: bool
    detail: str
    shortcut: bool


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── G0：数据健康（真实函数，临时 parquet 夹具） ────────────────────────────
def _write_df(directory: Path, filename: str, dcol: str, per_day: dict, pd):
    rows = []
    for day, n in per_day.items():
        for i in range(n):
            rows.append({"ts_code": "%06d.SZ" % i, dcol: day})
    pd.DataFrame(rows).to_parquet(str(directory / filename), index=False)


def probe_g0(case: Case) -> tuple[bool, str]:
    """passed = inspect_one 判绿（ok / no_event）。"""
    import pandas as pd

    dh = _load(DH_PATH, "vc_data_health")
    with tempfile.TemporaryDirectory(prefix="vc_g0_") as td:
        d = Path(td)
        t = dict(id="daily", name="日线行情", use="夹具",
                 file="daily_delta.parquet", dcol="trade_date", kind="daily")
        if case.kind == "oracle":
            # 已知合法输入：目标日齐备、历史充分 -> 必须放行
            _write_df(d, t["file"], t["dcol"],
                      {PREV_DAY: 100, D([50, 48, 50, 54, 49, 49, 48, 57]): 100,
                       EXEC_DAY: 100}, pd)
            target = EXEC_DAY
        elif case.kind == "no_op":
            # 11-10 判例：整天零采集，旧版把 target 取成「表内最新日」当作参照系
            _write_df(d, t["file"], t["dcol"], {PREV_DAY: 100}, pd)
            target = PREV_DAY
        else:
            # 半完成：目标日只有 1 行、无任何历史 -> prev_median=0，覆盖率检查被跳过
            _write_df(d, t["file"], t["dcol"], {EXEC_DAY: 1}, pd)
            target = EXEC_DAY
        dh.DATA = str(d)
        item = dh.inspect_one(t, target, False)
        passed = bool(item["ok"])
        return passed, "target=%s max=%s n_at_target=%d prev_median=%d verdict=%s" % (
            target, item["max_in_table"], item["n_at_target"],
            item["prev_median"], item["verdict"])


# ── G1：信号准入 / 实验预判单（真实函数，内存 dict 夹具） ─────────────────
def _valid_doc() -> dict:
    return {
        "meta": {"exp_id": "VC_ORACLE", "layer": "夹具-环境闸门",
                 "hypothesis": "夹具：已知可通过的预判单"},
        "cap": {"method": "spread", "upper_bound_pct": 0.371, "cost_pct": 0.30,
                "basis": "夹具：两组对照",
                "inputs": {"quantiles": 2, "holding_days": 5,
                           "spread_gross_pct": 0.671}},
        "abandon": {"threshold_pct": 0.0, "rationale": "夹具：覆盖不了成本就弃"},
        "falsify": {"if_true": ["夹具：单调性 rho >= 0.8", "夹具：OOS t >= 2.0"],
                    "if_false": ["夹具：分档倒 U 即证伪"],
                    "monotonicity_rho_min": 0.8, "t_min": 2.0,
                    "is_oos_sign_agree": True},
        "attempts": {"family": "夹具", "total_trials": 1,
                     "correction": {"applied": False, "method": "none"}},
        "cost_sensitivity": {"mode": "derived", "grid_pct": [0.20, 0.30, 0.50]},
        "selfcheck": {"reference": "夹具：GATE_backfill +0.371", "tolerance_pct": 5},
    }


def probe_g1(case: Case) -> tuple[bool, str]:
    ep = _load(EP_PATH, "vc_experiment_precheck")
    doc = _valid_doc()
    if case.kind == "no_op":
        # 不改任何决策，只把自报上限抬到 99；底层 spread 仍是 -5（明摆着矛盾）
        doc["cap"]["upper_bound_pct"] = 99.0
        doc["cap"]["inputs"]["spread_gross_pct"] = -5.0
        doc["selfcheck"] = {"reference": "夹具", "tolerance_pct": 5}
    elif case.kind == "unfinished":
        # 半完成：口径自证与成本敏感性都没做，check() 只给 warn，不进 errors
        doc.pop("selfcheck", None)
        doc["cost_sensitivity"] = {}
        doc["attempts"] = {}
    v, msg = ep.verdict_of(doc)
    errors, warns = ep.check(doc)
    return v == "pass", "verdict=%s errors=%d warns=%d | %s" % (
        v, len(errors), len(warns), msg[:90])


# ── 5 份变异检验脚本：把「验证器自身」当守卫 ─────────────────────────────
# 三类防呆各自的可检索特征。三套正则互不重叠，且不用 finally/shutil 这类
# 在脚本里到处都是的弱特征——第一版用它们做「删一个再看」的削弱夹具，
# 结果删不干净，5 份脚本全部被判成假通过，属于夹具本身失灵（已修正）。
BASELINE_PAT = re.compile(
    r"assert\s+all\(prod|生产版基线异常|production guard lost|"
    r"assert\s+rc_p\s*==|prod_out|恢复后全套|run_pytest\(None\)|全套测试|"
    r"return 0 if r\.returncode == 0", re.I)
ANCHOR_PAT = re.compile(
    r"count\([^)]*\)\s*==\s*1|ANCHOR_FAIL|assert\s+nb\s+in|替换后新串存在|"
    r"assert\s+SRC_TEXT\.count|assert\s+PROD\.count|assert\s+src\.count", re.I)
RESTORE_PAT = re.compile(
    r"恢复后字节|assert\s+io\.open\(path.*?==\s*raw|已按字节恢复|"
    r"survived\s*=|prod_out != mut_out|assert\s+not\s*\(excluded|"
    r"MUTATION_RESULT|killed \+= 1|恢复后全套测试", re.I)


def _scan(src: str) -> dict:
    return {"oracle": bool(BASELINE_PAT.search(src)),
            "no_op": bool(ANCHOR_PAT.search(src)),
            "unfinished": bool(RESTORE_PAT.search(src))}


def probe_mutation(path: Path, case: Case, na_reason: str = "") -> tuple[bool, str]:
    """静态守卫：passed = 该夹具下「守卫会放行」。

    oracle: 原样源码三类防呆齐备 -> 放行（守卫合格）
    no_op: 会放过空变异（缺锚点生效性断言）-> 放行 = 假通过
    unfinished: 会放过半完成（缺恢复/复核）-> 放行 = 假通过

    不再用「削弱源码再扫描」：那种夹具在 finally: 多处出现时删不干净，
    会把所有脚本都判成假通过，自身先没过 no-op。
    """
    src = path.read_text(encoding="utf-8", errors="replace")
    marks = _scan(src)
    if case.kind == "oracle":
        # 不改生产文件的脚本没有「恢复」这回事，unfinished 防呆按不适用处理，
        # 但必须写明 reason（与 cost_sensitivity.not_applicable 同一口径）。
        passed = (marks["oracle"] and marks["no_op"]
                  and (marks["unfinished"] or bool(na_reason)))
        detail = "marks=%s unfinished_na=%s evidence=static_scan" % (
            marks, na_reason or "no")
    elif case.kind == "no_op":
        passed = not marks["no_op"]
        detail = "锚点生效性断言=%s -> 空变异会被放过=%s evidence=static_scan" % (
            marks["no_op"], passed)
    else:
        if na_reason and not marks["unfinished"]:
            passed = False
            detail = "not_applicable（%s）-> 不存在半完成状态 evidence=static_scan" % na_reason
        else:
            passed = not marks["unfinished"]
            detail = "恢复/复核断言=%s -> 半完成会被放过=%s evidence=static_scan" % (
                marks["unfinished"], passed)
    return passed, detail


# ── 守卫注册表与夹具 ────────────────────────────────────────────────────
MUTATION_SCRIPTS = [
    ("MUT_suppress", "mutation_suppress.py", "抑制令复盘/引擎双向变异", ""),
    ("MUT_portfolio_suppress", "mutation_portfolio_suppress.py", "组合调度抑制感知变异",
     "只在临时目录写 _mut_pt_*.py 副本，从不写生产文件，无恢复动作"),
    ("MUT_paper_trade_temporal", "mutation_paper_trade_temporal.py", "v2 时点单调四变异", ""),
    ("MUT_check_preopen", "mutation_check_preopen.py", "盘前链路八变异", ""),
    ("MUT_check_daily0530", "mutation_check_daily0530.py", "05:30 检查六变异", ""),
]


def build_guards() -> list[dict]:
    guards = [
        dict(guard_id="G0_data_health", subject=str(DH_PATH.relative_to(ROOT)),
             desc="G0 日切与数据健康", probe=probe_g0,
             evidence_level="behavioral"),
        dict(guard_id="G1_experiment_precheck", subject=str(EP_PATH.relative_to(ROOT)),
             desc="G1 信号准入 / 预判单", probe=probe_g1,
             evidence_level="behavioral"),
    ]
    for gid, fname, desc, na_reason in MUTATION_SCRIPTS:
        p = VAR_AUDIT / fname
        guards.append(dict(guard_id=gid, subject=str(p.relative_to(ROOT)),
                           desc=desc, evidence_level="static_scan",
                           probe=(lambda path, na:
                                  (lambda c: probe_mutation(path, c, na)))(p, na_reason)))
    return guards


def fixtures_for(guard_id: str) -> list[Case]:
    if guard_id == "G0_data_health":
        return [
            Case("oracle", "VC-G0-O1", "目标日齐备 + 历史充分，必须放行", True),
            Case("no_op", "VC-G0-N1", "整天零采集，target 退回表内最新日（11-10 判例）", False),
            Case("unfinished", "VC-G0-U1", "目标日仅 1 行且无历史，覆盖率检查被跳过", False),
        ]
    if guard_id == "G1_experiment_precheck":
        return [
            Case("oracle", "VC-G1-O1", "完整合法预判单，必须放行", True),
            Case("no_op", "VC-G1-N1", "自报上限 99 与底层 spread -5 矛盾，仍判 pass", False),
            Case("unfinished", "VC-G1-U1", "缺口径自证与成本敏感性，仅 warn 不阻断", False),
        ]
    return [
        Case("oracle", "VC-" + guard_id.split("_")[1][:4].upper() + "-O1",
             "原样源码须具备三类防呆", True),
        Case("no_op", "VC-" + guard_id.split("_")[1][:4].upper() + "-N1",
             "移除锚点生效性断言后不得仍判合格（空变异）", False),
        Case("unfinished", "VC-" + guard_id.split("_")[1][:4].upper() + "-U1",
             "移除恢复复核后不得仍判合格（半完成）", False),
    ]


class Contract:
    def __init__(self, guard: dict):
        self.guard = guard
        self.cases = fixtures_for(guard["guard_id"])

    def verify(self) -> dict:
        kinds = {c.kind for c in self.cases}
        missing = [k for k in KINDS if k not in kinds]
        results = []
        for c in self.cases:
            if c.kind in ("no_op", "unfinished") and missing:
                continue
            try:
                passed, detail = self.guard["probe"](c)
            except Exception as exc:  # noqa: BLE001 失败关闭：探测异常不得算放行
                passed, detail = False, "PROBE_ERROR %s: %s" % (type(exc).__name__, exc)
            shortcut = (c.kind in ("no_op", "unfinished") and passed is True)
            results.append(CaseResult(c.kind, c.id, passed, detail, shortcut))
        mismatches = [r for r in results
                      if r.passed != next(c.expect_pass for c in self.cases if c.id == r.id)]
        no_op = next((r for r in results if r.kind == "no_op"), None)
        if missing:
            verdict = "INCOMPLETE"
        elif mismatches or any(r.shortcut for r in results):
            verdict = "FAIL"
        else:
            verdict = "PASS"
        return {
            "guard_id": self.guard["guard_id"],
            "subject": self.guard["subject"],
            "desc": self.guard["desc"],
            "evidence_level": self.guard["evidence_level"],
            "rules_version": RULES_VERSION,
            "fixture_kinds": sorted(kinds),
            "missing_kinds": missing,
            "verdict": verdict,
            "no_op_survived": bool(no_op and no_op.passed),
            "shortcuts": [r.id for r in results if r.shortcut],
            "results": [dataclasses.asdict(r) for r in results],
        }


def run_all() -> dict:
    reports = [Contract(g).verify() for g in build_guards()]
    beh = [r for r in reports if r["evidence_level"] == "behavioral"]
    sta = [r for r in reports if r["evidence_level"] == "static_scan"]
    return {
        "schema": "verifier_contract_report/v1",
        "rules_version": RULES_VERSION,
        "exec_day": EXEC_DAY,
        "n_guards": len(reports),
        "n_pass": len([r for r in reports if r["verdict"] == "PASS"]),
        "n_incomplete": len([r for r in reports if r["verdict"] == "INCOMPLETE"]),
        "n_fail": len([r for r in reports if r["verdict"] == "FAIL"]),
        "n_no_op_survived": len([r for r in reports if r["no_op_survived"]]),
        "n_no_op_survived_behavioral": len([r for r in beh if r["no_op_survived"]]),
        "n_no_op_survived_static": len([r for r in sta if r["no_op_survived"]]),
        "n_shortcut_cases": sum(len(r["shortcuts"]) for r in reports),
        "guards": reports,
        "codepoints": {"exec_day": [ord(c) for c in EXEC_DAY],
                       "prev_day": [ord(c) for c in PREV_DAY]},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="守卫三类夹具契约")
    ap.add_argument("cmd", choices=["list", "verify", "marks"])
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()
    if args.cmd == "list":
        for g in build_guards():
            print("%-28s %-14s %-52s %s" % (
                g["guard_id"], g["evidence_level"], g["subject"], g["desc"]))
        return RC_PASS
    if args.cmd == "marks":
        for _, fname, _, na in MUTATION_SCRIPTS:
            p = VAR_AUDIT / fname
            print("%-38s %s na=%s" % (
                fname, _scan(p.read_text(encoding="utf-8")), bool(na)))
        return RC_PASS
    rep = run_all()
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print("%-28s %-12s %-12s %-12s %s" % (
        "GUARD", "证据级别", "VERDICT", "NO-OP 放行", "假通过夹具"))
    for r in rep["guards"]:
        print("%-28s %-12s %-12s %-12s %s" % (
            r["guard_id"], r["evidence_level"], r["verdict"],
            "YES" if r["no_op_survived"] else "no",
            ",".join(r["shortcuts"]) or "-"))
    print("\n合计 %d 个守卫：PASS %d / INCOMPLETE %d / FAIL %d；"
          "no-op 假通过 %d 个（行为实证 %d / 静态登记 %d）；假通过夹具 %d 条" % (
              rep["n_guards"], rep["n_pass"], rep["n_incomplete"],
              rep["n_fail"], rep["n_no_op_survived"],
              rep["n_no_op_survived_behavioral"], rep["n_no_op_survived_static"],
              rep["n_shortcut_cases"]))
    print("CODEPOINTS exec_day=%s" % rep["codepoints"]["exec_day"])
    if rep["n_incomplete"]:
        return RC_INCOMPLETE
    return RC_FAIL if rep["n_fail"] else RC_PASS


if __name__ == "__main__":
    raise SystemExit(main())
