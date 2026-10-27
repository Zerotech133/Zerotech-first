# -*- coding: utf-8 -*-
"""strategy_replay：strategy 判决 × 影子账本 回放聚合视图（K-ORG-167 第三批）。

设计（对齐 adapters/trace_link.py 读侧风格）：
  - 按 run_id 将 strategy_runs.jsonl 判决行与 shadow_ledger.jsonl 的
    m5.v1 done 行 join，产出按 strategy_id 汇总的只读视图：
    总判决数、五档 recommendation 枚举分布（对齐 Verdict 出口枚举）、
    stance 三档分布、按 symbol 维度的最新判决、命中率类统计。
  - 零写面红线：本模块不创建/不修改/不追加任何账本文件，连目录都不建
    （M5 只读观测面判例平移）；无 pandas，仅标准库 json/collections。
  - fail-visible：账本文件缺失返回空视图并在 missing/hints 显式提示，
    不抛裸异常也不静默吞；坏行（JSON 解析失败/非对象行）跳过但计入
    bad_lines 返还调用方。
  - root 显式参数化（默认 agent_platform 自身 data/，即真仓），供测试
    注入隔离根——真仓账本只读不碰。

命中率口径（hit_rate，调用方必读）：
  - 分母 = 带非空 run_id 的判决行数（with_run_id，回放锚点口径）；
  - 分子 = 其中 run_id 命中影子账本 m5.v1 行的判决数（joined）；
  - 空 run_id 判决无回放锚点，不计入分母（单列 no_anchor 显式可见）；
  - 影子侧 v0 存量行（无 schema_version=m5.v1，K-ORG-129 冻结不重写）
    不参与 join；
  - 同 run_id 多条影子行时首见为准（append-only 账本，后写视为重放）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = REPO_ROOT / "agent_platform" / "data"

STRATEGY_LEDGER_REL = ("strategy", "strategy_runs.jsonl")
SHADOW_LEDGER_REL = ("shadow", "shadow_ledger.jsonl")

M5_SCHEMA = "m5.v1"
# 对齐 engine/strategy_registry.py Verdict 出口枚举（不 import，读侧自持）
RECOMMENDATIONS = ("strong_buy", "buy", "hold", "sell", "strong_sell")
STANCES = ("bull", "bear", "neutral")


# ---------------------------------------------------------------- 只读行读取


def _read_jsonl(path: Path) -> Tuple[List[dict], int]:
    """逐行读 jsonl，返回 (行列表, 坏行数)。坏行=JSON 解析失败或非对象行。"""
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
        if not isinstance(row, dict):
            bad += 1
            continue
        rows.append(row)
    return rows, bad


def load_shadow_index(root) -> Tuple[Dict[str, dict], int]:
    """影子账本只读 join 索引：run_id -> m5.v1 done 行。

    仅收 schema_version=m5.v1 且 run_id 非空的行；同 run_id 首见为准。
    文件缺失返回空索引（缺账本信息由 aggregate 统一 fail-visible）。
    """
    p = Path(root) / SHADOW_LEDGER_REL[0] / SHADOW_LEDGER_REL[1]
    if not p.exists():
        return {}, 0
    rows, bad = _read_jsonl(p)
    index: Dict[str, dict] = {}
    for row in rows:
        if row.get("schema_version") != M5_SCHEMA:
            continue
        rid = str(row.get("run_id", "")).strip()
        if not rid or rid in index:
            continue
        index[rid] = row
    return index, bad


# ---------------------------------------------------------------- 聚合


def _new_dist(enums) -> Dict[str, int]:
    """枚举分布计数器：枚举值预置 0，枚举外值落 other（fail-visible）。"""
    d = {e: 0 for e in enums}
    d["other"] = 0
    return d


def _bump(dist: Dict[str, int], value) -> None:
    key = str(value) if value is not None else ""
    if key in dist:
        dist[key] += 1
    else:
        dist["other"] += 1


def aggregate(root=None) -> dict:
    """按 run_id join 两账本，产出按 strategy_id 汇总的只读聚合视图。

    返回结构（JSON 可直接序列化）：
      root / missing(缺账本键列表) / hints(人类可读提示) /
      bad_lines{strategy_runs, shadow_ledger} /
      totals{verdicts, with_run_id, joined, no_anchor, no_strategy_id,
             hit_rate} /
      strategies{sid: {total, with_run_id, joined, hit_rate,
                       recommendations(五档+other), stances(三档+other),
                       latest_by_symbol{symbol: 最新判决摘要}}}
    """
    root = Path(root) if root else DEFAULT_DATA_ROOT
    missing: List[str] = []
    hints: List[str] = []
    bad = {"strategy_runs": 0, "shadow_ledger": 0}

    s_path = root / STRATEGY_LEDGER_REL[0] / STRATEGY_LEDGER_REL[1]
    if s_path.exists():
        verdict_rows, bad["strategy_runs"] = _read_jsonl(s_path)
    else:
        verdict_rows = []
        missing.append("strategy")
        hints.append("strategy 账本不存在（只读空视图）: %s" % s_path)

    shadow_index, shadow_bad = load_shadow_index(root)
    bad["shadow_ledger"] = shadow_bad
    h_path = root / SHADOW_LEDGER_REL[0] / SHADOW_LEDGER_REL[1]
    if not h_path.exists():
        missing.append("shadow")
        hints.append("影子账本不存在（join 侧空索引，命中率恒 0）: %s" % h_path)

    totals = {
        "verdicts": 0,
        "with_run_id": 0,
        "joined": 0,
        "no_anchor": 0,
        "no_strategy_id": 0,
    }
    per: Dict[str, dict] = {}

    for row in verdict_rows:
        sid = str(row.get("strategy_id", "")).strip()
        if not sid:
            totals["no_strategy_id"] += 1
            continue
        st = per.setdefault(
            sid,
            {
                "total": 0,
                "with_run_id": 0,
                "joined": 0,
                "recommendations": _new_dist(RECOMMENDATIONS),
                "stances": _new_dist(STANCES),
                "latest_by_symbol": {},
            },
        )
        st["total"] += 1
        totals["verdicts"] += 1
        _bump(st["recommendations"], row.get("recommendation"))
        _bump(st["stances"], row.get("stance"))

        rid = str(row.get("run_id", "")).strip()
        if not rid:
            totals["no_anchor"] += 1
        else:
            st["with_run_id"] += 1
            totals["with_run_id"] += 1
            if rid in shadow_index:
                st["joined"] += 1
                totals["joined"] += 1

        sym = str(row.get("symbol", "")).strip()
        if sym:
            order_key = (
                str(row.get("trade_date", "")),
                str(row.get("recorded_at", "")),
                str(row.get("verdict_key", "")),
            )
            cur = st["latest_by_symbol"].get(sym)
            if cur is None or order_key >= cur["_order"]:
                st["latest_by_symbol"][sym] = {
                    "_order": order_key,
                    "recommendation": row.get("recommendation"),
                    "confidence": row.get("confidence"),
                    "stance": row.get("stance"),
                    "trade_date": row.get("trade_date"),
                    "recorded_at": row.get("recorded_at"),
                    "run_id": rid,
                    "verdict_key": row.get("verdict_key"),
                }

    strategies = {}
    for sid, st in per.items():
        latest = {}
        for sym, info in st["latest_by_symbol"].items():
            info.pop("_order")
            latest[sym] = info
        strategies[sid] = {
            "total": st["total"],
            "with_run_id": st["with_run_id"],
            "joined": st["joined"],
            "hit_rate": (
                round(st["joined"] / st["with_run_id"], 4)
                if st["with_run_id"]
                else None
            ),
            "recommendations": st["recommendations"],
            "stances": st["stances"],
            "latest_by_symbol": latest,
        }

    totals["hit_rate"] = (
        round(totals["joined"] / totals["with_run_id"], 4)
        if totals["with_run_id"]
        else None
    )
    return {
        "root": str(root),
        "missing": missing,
        "hints": hints,
        "bad_lines": bad,
        "totals": totals,
        "strategies": strategies,
    }


# ---------------------------------------------------------------- CLI


def _print_table(view: dict) -> None:
    """极简表格输出（无三方依赖）。"""
    print("root=%s" % view["root"])
    for h in view["hints"]:
        print("[提示] %s" % h)
    if view["bad_lines"]["strategy_runs"] or view["bad_lines"]["shadow_ledger"]:
        print("[提示] 坏行跳过: %s" % view["bad_lines"])
    print("%-24s %6s %8s %7s %9s" % ("strategy", "total", "anchor", "join", "hit_rate"))
    for sid in sorted(view["strategies"]):
        st = view["strategies"][sid]
        print(
            "%-24s %6d %8d %7d %9s"
            % (
                sid[:24],
                st["total"],
                st["with_run_id"],
                st["joined"],
                "n/a" if st["hit_rate"] is None else "%.1f%%" % (st["hit_rate"] * 100),
            )
        )
    t = view["totals"]
    print(
        "TOTAL verdicts=%d with_run_id=%d joined=%d no_anchor=%d "
        "no_strategy_id=%d hit_rate=%s"
        % (
            t["verdicts"],
            t["with_run_id"],
            t["joined"],
            t["no_anchor"],
            t["no_strategy_id"],
            "n/a" if t["hit_rate"] is None else "%.1f%%" % (t["hit_rate"] * 100),
        )
    )


def _cli(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="strategy 回放聚合视图（只读，零写面）")
    ap.add_argument(
        "--root", default="", help="数据根（默认 agent_platform/data 真仓，只读）"
    )
    ap.add_argument(
        "--format",
        choices=["json", "table"],
        default="json",
        help="输出格式：json（默认）或 table",
    )
    args = ap.parse_args(argv)
    view = aggregate(args.root or None)
    if args.format == "table":
        _print_table(view)
    else:
        print(json.dumps(view, ensure_ascii=False, indent=1))
    # 退出码：0=至少一侧账本可读（聚合视图已产出）；
    #         2=两侧账本均缺失（fail-visible 空视图，无任何可回放数据）。
    return 2 if len(view["missing"]) == 2 else 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
