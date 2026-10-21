# -*- coding: utf-8 -*-
"""量化板块盘后复盘入口（WO-QT-20261021-105 步1）。

职责：**只读聚合 + 调 combo_review 出报表 + 落盘归档**。
`k_stock/paper/combo_review.py` 一行不改（只读复用其 build_review /
compare_portfolios / render_*），不碰晨会脚本 / 门户 / 计划任务 / 交易链。

不造数三条硬约束（继承 combo_review 语义，**本模块不得放宽**）：
  1. 后验收益缺失 → ROI 标 `pending_backfill`，**禁填 0**（0 会被读成"白干且不亏"）
  2. 成本预算未登记 → null，不臆造预算
  3. 收益与成本单位不可比 → `unit_mismatch`，不出数
本模块只做搬运与归档，不自行计算任何收益/成本/ROI。

空态：注册表无组合 → 产出显式空态（`empty_state=true`、`combos=0`），
不伪造任何组合报告。

失败语义：单组合复盘失败 → 记入 `_summary.json.errors` 且**不为它产出 md**
（不能给失败组合造一份报告），其余组合照常；注册表本身不可读 → 直接抛错。

产出（默认 `quant_backtest/reports/review/YYYY-MM-DD/`）：
  <combo_id>.md      单组合复盘报表
  _compare.md        横向对比（仅当有成功组合时）
  _summary.json      机读摘要（供下游/晨会按需消费）

用法：
  python review_daily.py --all
  python review_daily.py --combo combo_wenjian --date 2026-10-21
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent            # quant_backtest
PAPER = HERE.parent / "k_stock" / "paper"         # combo_review 所在目录
if str(PAPER) not in sys.path:
    sys.path.insert(0, str(PAPER))

import combo_registry as creg                     # noqa: E402
import combo_review as cr                         # noqa: E402

REVIEW_ROOT = HERE / "reports" / "review"
SUMMARY_SCHEMA = 1


def load_combo_ids(registry_path=None) -> list:
    """读注册表全部 combo_id（保持注册顺序）。注册表不可读 → 抛错不吞。"""
    reg = creg.load_registry(registry_path)
    out = []
    for c in reg.get("combos") or []:
        cid = (c or {}).get("combo_id")
        if cid:
            out.append(cid)
    return out


def _cost_ledger_state(ledger=None) -> dict:
    """探测成本账是否在盘（区分「账本不在」与「真零成本」）。

    `combo_review._ledger_accumulated` 在账本文件缺失时返回 `0.0`，与「当月
    确实零成本」不可分。当前因收益恒 pending_backfill 尚不出 ROI，故无害；
    但步 2 接入后验收益后，「ROI = 收益 − 0」会**高估**。本函数只读探测并把
    事实记进 summary，不改 combo_review 的口径（它一行不改是本单边界）。
    """
    try:
        p = Path(ledger) if ledger is not None \
            else Path(cr.combo_factory.DEFAULT_BUDGET_LEDGER)
    except Exception:                                # noqa: BLE001
        return {"path": None, "exists": None}
    try:
        return {"path": str(p), "exists": p.exists()}
    except OSError:
        return {"path": str(p), "exists": None}


def run(date=None, combo_ids=None, out_root=None, registry_path=None,
        ledger=None, receipts=None, snapshot_db=None, month=None) -> dict:
    """跑一轮盘后复盘，返回 summary dict（同时落盘）。

    combo_ids=None → 取注册表全量；显式传 [] → 按空态处理（不臆造组合）。
    """
    day = date or time.strftime("%Y-%m-%d")
    root = Path(out_root) if out_root else REVIEW_ROOT
    out_dir = root / day
    out_dir.mkdir(parents=True, exist_ok=True)

    if combo_ids is None:
        combo_ids = load_combo_ids(registry_path)      # 注册表坏了就抛，不装空

    written, errors, roi_counts = [], [], {}
    for cid in combo_ids:
        try:
            rev = cr.build_review(cid, registry_path=registry_path,
                                  ledger=ledger, receipts=receipts,
                                  month=month, snapshot_db=snapshot_db)
        except Exception as e:                          # noqa: BLE001
            # 失败组合**不产出 md**——给它写一份报告就是造数
            errors.append({
                "combo_id": cid,
                "reason_code": getattr(e, "reason_code", "unknown"),
                "error": str(e)[:200],
            })
            continue
        md = cr.render_markdown(rev)
        (out_dir / ("%s.md" % cid)).write_text(md, encoding="utf-8")
        written.append(cid)
        st = (rev.get("roi") or {}).get("status") or "unknown"
        roi_counts[st] = roi_counts.get(st, 0) + 1

    compare_written = False
    if written:
        cmp_ = cr.compare_portfolios(written, registry_path=registry_path,
                                     ledger=ledger, receipts=receipts,
                                     month=month, snapshot_db=snapshot_db)
        (out_dir / "_compare.md").write_text(
            cr.render_comparison_markdown(cmp_), encoding="utf-8")
        compare_written = True

    summary = {
        "summary_schema": SUMMARY_SCHEMA,
        "date": day,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "requested": len(combo_ids),
        "combos": len(written),
        "combo_ids": written,
        "errors": errors,
        "roi_status_counts": roi_counts,
        "compare_written": compare_written,
        "empty_state": len(combo_ids) == 0,
        "cost_ledger": _cost_ledger_state(ledger),
        "out_dir": str(out_dir),
        "notes": [
            "本文件只搬运 combo_review 的结论，不自行计算收益/成本/ROI",
            "后验收益缺失时 ROI=pending_backfill，不以 0 充数",
            "失败组合不产出 md，原因见 errors",
            "cost_ledger.exists=false 时，成本累计 0 不代表真零成本（账本不在盘）",
        ],
    }
    (out_dir / "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="量化板块盘后复盘（只读聚合 + 归档）")
    ap.add_argument("--date", help="归档日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--combo", action="append", dest="combos",
                    help="指定组合 id（可重复）；缺省=注册表全量")
    ap.add_argument("--all", action="store_true", help="显式全量（与缺省同义）")
    ap.add_argument("--out", help="输出根目录（默认 reports/review）")
    args = ap.parse_args(argv)

    summary = run(date=args.date, combo_ids=args.combos or None,
                  out_root=args.out)
    brief = {k: summary[k] for k in ("date", "combos", "errors",
                                     "roi_status_counts", "out_dir")}
    print(json.dumps(brief, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
