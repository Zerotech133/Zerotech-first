# -*- coding: utf-8 -*-
"""verify_strategy_ledger：strategy_runs × 影子账本 × 证据收编 三方对账（只读）。

## 为什么要它

军团-M2 后，strategy 判决链的三方一致性只靠设计意图：
  1. `data/strategy/strategy_runs.jsonl` 判决行（幂等键 verdict_key）；
  2. `data/shadow/shadow_ledger.jsonl` m5.v1 影子行（run_id 锚点）；
  3. `adapters/evidence_collect.py` 第十一域把判决行转成证据行
     （source_ref="strategy:<verdict_key>"，payload.idempotency_key=verdict_key）。
三方靠 verdict_key/run_id 串起来，但没有任何独立工具验证它们真的一致。
本工具是第二意见（与 tools/gate3_verdict.py 同范式）：从原始账本独立
重算同一套关联关系，差异 fail-visible。

## 三项对账

  ① 幂等键完好：每行 verdict_key 必须等于 verdict_key(strategy_id,
     symbol, trade_date) 重算值——防手编辑篡改/字段漂移。
  ② run_id 双向可查：判决行 run_id 必须命中影子账本 m5.v1 行索引
     （反向：影子 m5.v1 run_id 未被任何判决引用只提示不计 FAIL——
     影子 run 可合法不产出判决，bars 缺失时 strategy 域 fail-open 跳过）。
  ③ 证据覆盖：按 evidence_collect._collect_strategy 同 schema 重放
     （source="strategy.runs"，payload.idempotency_key=verdict_key），
     证据 idempotency_key 集合必须覆盖账本全部 verdict_key——
     空/缺 verdict_key 的行在证据侧坍缩成空键，即覆盖缺口。

## 独立性边界（刻意为之，对齐 gate3_verdict 判例）

唯一复用的生产实现是 `engine.strategy_registry.verdict_key`——幂等键
算法必须同源，重写一份 sha1 截断反而引入口径分歧风险；该 import 只读
不写。证据行构建不复用 _collect_strategy：其 root 语义是仓库根
（root/"agent_platform"/data/...），与本工具的 data-root（=agent_platform/data，
对齐 run_shadow DATA_ROOT / strategy_registry._ledger_path）不同层，
且判据只需键集合不签 statement，重放 schema 是更小的耦合面。

## 用法与退出码

    python tools/verify_strategy_ledger.py --data-root <agent_platform/data>

退出码：0=全 PASS（含空账本中性）／1=存在 FAIL／2=参数缺失（无 root 拒绝运行）。

红线：**零写面**。不创建目录、不追加账本、不写证据仓——连 mkdir 都不做
（M5 只读观测面判例平移）。空账本（文件缺失或零好行）= 中性非通过，
对齐周末 PENDING 口径，退出码仍为 0。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_AGENT_PLATFORM = Path(__file__).resolve().parents[1]
if str(_AGENT_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_AGENT_PLATFORM))

from engine.strategy_registry import verdict_key  # noqa: E402  只读复用（见独立性边界）

STRATEGY_LEDGER_REL = ("strategy", "strategy_runs.jsonl")
SHADOW_LEDGER_REL = ("shadow", "shadow_ledger.jsonl")
M5_SCHEMA = "m5.v1"
# 对账项①必填字段（缺任一即无法重算幂等键 → fail-visible）
KEY_FIELDS = ("strategy_id", "symbol", "trade_date")


def _read_jsonl(path: Path) -> Tuple[List[dict], int]:
    """逐行只读 jsonl，返回 (好行列表, 坏行数)。坏行=解析失败或非对象行。"""
    rows: List[dict] = []
    bad = 0
    for line in path.read_text(encoding="utf-8").splitlines():
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
        else:
            bad += 1
    return rows, bad


def _load_shadow_index(root: Path) -> Tuple[Dict[str, dict], bool]:
    """影子账本 m5.v1 行索引：run_id -> 行（首见为准，对齐 strategy_replay）。

    返回 (索引, 文件是否存在)。文件缺失返回空索引（缺账本由对账项②
    结合判决行 run_id 情况判 FAIL，无判决行则中性）。
    """
    p = root / SHADOW_LEDGER_REL[0] / SHADOW_LEDGER_REL[1]
    if not p.exists():
        return {}, False
    rows, _ = _read_jsonl(p)
    index: Dict[str, dict] = {}
    for row in rows:
        if row.get("schema_version") != M5_SCHEMA:
            continue
        rid = str(row.get("run_id", "")).strip()
        if rid and rid not in index:
            index[rid] = row
    return index, True


# ---------------------------------------------------------------- 对账项

def check_key_integrity(rows: List[dict], bad_lines: int) -> Tuple[bool, List[str]]:
    """对账项①：verdict_key 重算一致。返回 (ok, 差异清单)。"""
    diffs: List[str] = []
    if bad_lines:
        diffs.append("strategy_runs 有 %d 行损坏（JSON 解析失败/非对象）——"
                     "手编辑/写坏必须先查清" % bad_lines)
    for i, row in enumerate(rows, 1):
        missing = [f for f in KEY_FIELDS if not str(row.get(f, "")).strip()]
        if missing:
            diffs.append("第 %d 行缺 %s，无法重算幂等键" % (i, "/".join(missing)))
            continue
        expect = verdict_key(row["strategy_id"], row["symbol"], row["trade_date"])
        actual = str(row.get("verdict_key", ""))
        if actual != expect:
            diffs.append("第 %d 行 verdict_key=%r 与重算值 %r 不符"
                         "（strategy_id=%s symbol=%s trade_date=%s）"
                         % (i, actual, expect, row["strategy_id"],
                            row["symbol"], row["trade_date"]))
    return not diffs, diffs


def check_run_id_link(rows: List[dict], shadow_index: Dict[str, dict],
                      shadow_exists: bool) -> Tuple[bool, List[str], List[str]]:
    """对账项②：判决 run_id 与影子 m5.v1 行双向可查。返回 (ok, 差异, 反向提示)。"""
    diffs: List[str] = []
    notes: List[str] = []
    if not shadow_exists:
        diffs.append("影子账本不存在（%s），%d 条判决行无锚点可查"
                     % ("/".join(SHADOW_LEDGER_REL), len(rows)))
        return False, diffs, notes
    for i, row in enumerate(rows, 1):
        rid = str(row.get("run_id", "")).strip()
        if not rid:
            diffs.append("第 %d 行 run_id 为空（无回放锚点；run_shadow 落账应恒带）"
                         % i)
        elif rid not in shadow_index:
            diffs.append("第 %d 行 run_id=%s 未命中影子账本 m5.v1 行" % (i, rid))
    referenced = {str(r.get("run_id", "")).strip() for r in rows}
    orphan = [rid for rid in shadow_index if rid not in referenced]
    if orphan:
        notes.append("影子 m5.v1 有 %d 个 run_id 无判决行引用（合法：bars 缺失时 "
                     "strategy 域 fail-open 跳过，不产判决）：%s"
                     % (len(orphan), ", ".join(sorted(orphan)[:4])))
    return not diffs, diffs, notes


def check_evidence_coverage(rows: List[dict]) -> Tuple[bool, List[str]]:
    """对账项③：证据 idempotency_key 集合覆盖全部 verdict_key。

    口径逐条抄自 adapters/evidence_collect.py _collect_strategy（283-310 行）：
    每好行 → payload.idempotency_key = r.get("verdict_key", "")。
    覆盖判据：每行 verdict_key 非空且在证据键集合中——键为空/缺失的行
    在证据侧坍缩成 ""，即覆盖缺口（无法在证据仓定位该判决）。
    """
    diffs: List[str] = []
    ev_keys = {str(r.get("verdict_key", "")) for r in rows}
    for i, row in enumerate(rows, 1):
        k = str(row.get("verdict_key", ""))
        if not k:
            diffs.append("第 %d 行 verdict_key 空/缺失——证据行 idempotency_key "
                         "将坍缩为空串，覆盖缺口" % i)
        elif k not in ev_keys:  # 结构恒真（同源集合），保留作口径自证断言
            diffs.append("第 %d 行 verdict_key=%s 未被证据键集合覆盖" % (i, k))
    return not diffs, diffs


# ---------------------------------------------------------------- 汇总

def verify(data_root) -> dict:
    """三方对账主入口（只读）。返回 JSON 可序列化报告。"""
    root = Path(data_root)
    s_path = root / STRATEGY_LEDGER_REL[0] / STRATEGY_LEDGER_REL[1]
    if s_path.exists():
        rows, bad_lines = _read_jsonl(s_path)
    else:
        rows, bad_lines = [], 0

    neutral = not rows  # 空账本（文件缺失或零好行）= 中性非通过
    shadow_index, shadow_exists = _load_shadow_index(root)

    ok1, d1 = (True, []) if neutral else check_key_integrity(rows, bad_lines)
    if neutral:
        ok2, d2, notes = True, [], []
        ok3, d3 = True, []
        if bad_lines:
            notes.append("账本零好行但有 %d 行坏行（首跑前残留，中性不计 FAIL，"
                         "仍建议查清）" % bad_lines)
    else:
        ok2, d2, notes = check_run_id_link(rows, shadow_index, shadow_exists)
        ok3, d3 = check_evidence_coverage(rows)

    return {
        "root": str(root),
        "neutral": neutral,
        "rows": len(rows),
        "bad_lines": bad_lines,
        "verdict_keys": len({str(r.get("verdict_key", "")) for r in rows} - {""}),
        "checks": [
            {"item": "①verdict_key 重算一致", "ok": ok1, "diffs": d1},
            {"item": "②run_id 双向可查", "ok": ok2, "diffs": d2},
            {"item": "③证据 idempotency_key 覆盖", "ok": ok3, "diffs": d3},
        ],
        "notes": notes,
        "ok": ok1 and ok2 and ok3,
    }


def _print_report(rep: dict) -> None:
    print("=" * 62)
    print("strategy 三方对账 %s" % rep["root"])
    print("=" * 62)
    if rep["neutral"]:
        print("  [中性] strategy_runs.jsonl 不存在或零好行——空=中性非通过"
              "（对齐周末 PENDING 口径），三项对账待首跑后实检")
        for n in rep["notes"]:
            print("  [提示] %s" % n)
        print("结论: 空账本中性（退出码 0）")
        return
    print("  判决行 %d（坏行 %d）/ 去重 verdict_key %d" % (
        rep["rows"], rep["bad_lines"], rep["verdict_keys"]))
    for c in rep["checks"]:
        print("  [%s] %s" % ("PASS" if c["ok"] else "FAIL", c["item"]))
        for d in c["diffs"]:
            print("       - %s" % d)
    for n in rep["notes"]:
        print("  [提示] %s" % n)
    print("结论: %s" % ("全部 PASS" if rep["ok"] else "存在 FAIL（先查清再判门）"))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="strategy_runs × 影子账本 × 证据收编 三方对账（只读，零写面）")
    ap.add_argument("--data-root", required=True,
                    help="数据根（=agent_platform/data，对齐 run_shadow "
                         "DATA_ROOT 语义；显式必填，无 root 拒绝运行）")
    args = ap.parse_args(argv)
    rep = verify(args.data_root)
    _print_report(rep)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
