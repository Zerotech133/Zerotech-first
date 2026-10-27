# -*- coding: utf-8 -*-
"""check_observation_point：周一观察点一键巡检（只读，fail-visible）。

## 背景

2026-10-18 预演四步全通（docs/audit/2026-10-18_周一观察点预演记录.md），
把 17:25 巡检固化为脚本，现场照跑即判 PASS/PENDING/FAIL：
17:10 AgentShadowDaily 影子首跑 → 17:25 monday_checkpoint 两观察项。

## 五项判据（对齐预演记录第六节 checklist）

  a) shadow/last_run.log 含 [strategy] 成功标记行（「桥接插件 +N」或
     「产出 N 条 recorded=」——Registry 4 策略装配的直接证据）；
     文件缺失或无标记 = PENDING（17:10 未触发 / strategy 域 fail-open
     跳过——主链与 strategy 观察项两条独立成败线，分开判读）。
  b) strategy/strategy_runs.jsonl 带 run_id 行数 ≥1；文件不存在
     = PENDING 中性（真仓首跑前不存在，2026-10-18 实证）；存在
     但零好行 = PENDING（写盘竞争态）；有好行却无一带 run_id = FAIL。
  c) shadow/monday_checkpoint.log 含两个 observe 段标记
     （[observe] strategy_runs 与 [observe] codex_registrations）；
     两标记全有 = PASS；恰缺一个 = FAIL（跑过但段缺）；全无
     = PENDING（17:25 新版 checkpoint 未跑，首跑前属预期形态）。
  d) shadow/shadow_ledger.jsonl 存在 m5.v1 行（影子行 schema 标记）；
     文件缺失或 0 行 = PENDING（首跑 17:10 先落影子行，真仓现状实证 0 行）。
  e) verdict_key 三方对账——直接复用 tools/verify_strategy_ledger.verify()
     （幂等键算法同源纪律，见其 docstring「独立性边界」）：
     neutral=空账本 → PENDING；ok → PASS；存在 diffs → FAIL（明细可见）。

## 退出码

  2 = 存在 FAIL（先查清再判门）
  0 = 无 FAIL 且有 PASS（可放行）
  1 = 全 PENDING（中性——首跑/巡检未到，非失败）

## 红线

零写面：不创建目录、不追加账本、不写日志——连 mkdir 都不做
（对齐 verify_strategy_ledger M5 只读观测面判例）。

用法：

    python tools/check_observation_point.py --data-root <agent_platform/data>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

_TOOLS_DIR = Path(__file__).resolve().parent
_AGENT_PLATFORM = _TOOLS_DIR.parent
if str(_AGENT_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_AGENT_PLATFORM))
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from verify_strategy_ledger import verify  # noqa: E402  同目录复用三方对账

# 判据 a：last_run.log 成功标记行前缀（run_shadow.py:414/428 原样输出）
LAST_RUN_SUCCESS_PREFIX = "[strategy] "
LAST_RUN_REL = ("shadow", "last_run.log")
STRATEGY_RUNS_REL = ("strategy", "strategy_runs.jsonl")
CHECKPOINT_LOG_REL = ("shadow", "monday_checkpoint.log")
SHADOW_LEDGER_REL = ("shadow", "shadow_ledger.jsonl")
M5_SCHEMA = "m5.v1"
# 判据 c：monday_checkpoint 两个 observe 段的输出标记（monday_checkpoint.py:226/231/243/248）
OBSERVE_STRATEGY_MARK = "[observe] strategy_runs"
OBSERVE_REGS_MARK = "[observe] codex_registrations"

PENDING, PASS, FAIL = "PENDING", "PASS", "FAIL"


def _join(root: Path, rel) -> Path:
    return root.joinpath(*rel)


def _read_text_safe(path: Path) -> Optional[str]:
    """只读全文；文件不存在或不可读返回 None（由判据决定 PENDING/FAIL）。"""
    try:
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001  fail-visible：异常透出为不可读
        return ".__unreadable__(%s)" % str(exc)[:80]


def check_a_last_run(root: Path):
    """a) last_run.log 含 [strategy] 成功标记。"""
    text = _read_text_safe(_join(root, LAST_RUN_REL))
    if text is None:
        return PENDING, "last_run.log 不存在（17:10 首跑未触发）"
    if text.startswith(".__unreadable__"):
        return PENDING, text  # 不可读不妄判 FAIL，中性可见
    n = sum(1 for line in text.splitlines() if line.startswith(LAST_RUN_SUCCESS_PREFIX))
    if n:
        return PASS, "last_run.log 含 %d 行 [strategy] 成功标记" % n
    return (
        PENDING,
        "last_run.log 存在但无 [strategy] 标记（首跑未到或 strategy 域 fail-open，分开判读）",
    )


def check_b_strategy_runs(root: Path):
    """b) strategy_runs.jsonl 带 run_id 行数 ≥1。"""
    import json

    path = _join(root, STRATEGY_RUNS_REL)
    text = _read_text_safe(path)
    if text is None:
        return (
            PENDING,
            "strategy_runs.jsonl 不存在（PENDING 中性——周一 17:10 首跑后应存在）",
        )
    if text.startswith(".__unreadable__"):
        return PENDING, text
    rows: List[dict] = []
    bad = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
    if not rows:
        detail = "strategy_runs.jsonl 存在但零好行（写盘竞争态，中性）"
        if bad:
            detail += "，另有 %d 行坏行" % bad
        return PENDING, detail
    anchored = sum(1 for r in rows if r.get("run_id"))
    if anchored >= 1:
        detail = "strategy_runs.jsonl %d 行 / 带 run_id %d 行（≥1 达标）" % (
            len(rows),
            anchored,
        )
        if bad:
            detail += "，坏行 %d" % bad
        return PASS, detail
    return FAIL, (
        "strategy_runs.jsonl %d 好行 / 带 run_id 0 行"
        "（判决行应恒带 run_id，契约缺失）" % len(rows)
    )


def check_c_checkpoint_log(root: Path):
    """c) monday_checkpoint.log 含两个 observe 段标记。

    两标记全有 = PASS；恰缺一个 = FAIL（跑过但段缺，异常）；
    全无 = PENDING（17:25 新版 checkpoint 未跑——真仓 2026-10-18 实证：
    历史 log 只有旧版 tasks.json schema 段，strategy_runs/codex_regs
    两段是周一 main() 运行时才写入，全无属首跑前预期形态）。
    """
    text = _read_text_safe(_join(root, CHECKPOINT_LOG_REL))
    if text is None:
        return PENDING, "monday_checkpoint.log 不存在（17:25 巡检未跑）"
    if text.startswith(".__unreadable__"):
        return PENDING, text
    has_s = OBSERVE_STRATEGY_MARK in text
    has_r = OBSERVE_REGS_MARK in text
    if has_s and has_r:
        return (
            PASS,
            "monday_checkpoint.log 含 strategy_runs 与 codex_registrations 两个 observe 段",
        )
    if has_s or has_r:
        missing = OBSERVE_REGS_MARK if has_s else OBSERVE_STRATEGY_MARK
        return FAIL, "monday_checkpoint.log 缺 observe 段标记：%s" % missing
    return PENDING, (
        "monday_checkpoint.log 无两 observe 段（17:25 新版 checkpoint 未跑，"
        "首跑前属预期形态）"
    )


def check_d_shadow_m5(root: Path):
    """d) 影子账本存在 m5.v1 行。"""
    import json

    path = _join(root, SHADOW_LEDGER_REL)
    text = _read_text_safe(path)
    if text is None:
        return PENDING, "shadow_ledger.jsonl 不存在（首跑未落账）"
    if text.startswith(".__unreadable__"):
        return PENDING, text
    n = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("schema_version") == M5_SCHEMA:
            n += 1
    if n:
        return PASS, "shadow_ledger.jsonl 含 %d 行 m5.v1" % n
    return (
        PENDING,
        "shadow_ledger.jsonl 存在但 0 行 m5.v1（首跑 17:10 先落影子行，现属预期）",
    )


def check_e_verdict_reconcile(root: Path):
    """e) verdict_key 三方对账（复用 verify_strategy_ledger.verify）。"""
    try:
        rep = verify(root)
    except Exception as exc:  # noqa: BLE001  fail-visible：对账工具自身异常必须可见
        return FAIL, "verify_strategy_ledger 调用异常（%s）" % str(exc)[:100]
    if rep["neutral"]:
        return PENDING, "strategy_runs 空账本中性，三项对账待首跑后实检"
    if rep["ok"]:
        return PASS, (
            "三方对账 PASS（判决 %d 行 / 去重 verdict_key %d）"
            % (rep["rows"], rep["verdict_keys"])
        )
    diffs = []
    for c in rep["checks"]:
        for d in c["diffs"]:
            diffs.append("%s：%s" % (c["item"], d))
    return FAIL, "三方对账 FAIL：%s" % "；".join(diffs[:4])


CHECKS = (
    ("a_last_run_marker", check_a_last_run),
    ("b_strategy_runs_anchored", check_b_strategy_runs),
    ("c_checkpoint_observe_sections", check_c_checkpoint_log),
    ("d_shadow_m5_rows", check_d_shadow_m5),
    ("e_verdict_key_reconcile", check_e_verdict_reconcile),
)


def run_checks(data_root) -> dict:
    """巡检主入口（只读）。返回含逐项状态与汇总退出码的报告。"""
    root = Path(data_root)
    items = []
    for name, fn in CHECKS:
        status, evidence = fn(root)
        items.append({"name": name, "status": status, "evidence": evidence})
    n_fail = sum(1 for it in items if it["status"] == FAIL)
    n_pass = sum(1 for it in items if it["status"] == PASS)
    if n_fail:
        exit_code = 2
    elif n_pass:
        exit_code = 0
    else:
        exit_code = 1
    return {
        "root": str(root),
        "items": items,
        "pass": n_pass,
        "pending": sum(1 for it in items if it["status"] == PENDING),
        "fail": n_fail,
        "exit_code": exit_code,
    }


def _print_report(rep: dict) -> None:
    print("=" * 62)
    print("周一观察点一键巡检 %s" % rep["root"])
    print("=" * 62)
    for it in rep["items"]:
        print("  [%s] %s —— %s" % (it["status"], it["name"], it["evidence"]))
    print(
        "汇总: PASS %d / PENDING %d / FAIL %d"
        % (rep["pass"], rep["pending"], rep["fail"])
    )
    verdict = {
        0: "无 FAIL 且有 PASS——可放行（退出码 0）",
        1: "全 PENDING——中性：首跑/巡检未到，非失败（退出码 1）",
        2: "存在 FAIL——先查清再判门（退出码 2）",
    }[rep["exit_code"]]
    print("结论: %s" % verdict)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="周一观察点一键巡检（只读，零写面，fail-visible）"
    )
    ap.add_argument(
        "--data-root",
        required=True,
        help="数据根（=agent_platform/data，显式必填——显式 root 纪律）",
    )
    args = ap.parse_args(argv)
    rep = run_checks(args.data_root)
    _print_report(rep)
    return rep["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
