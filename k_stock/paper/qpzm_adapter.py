# -*- coding: utf-8 -*-
"""青萍之末 adapter（策略源 → Picks 落单链的第三条接入，B1/K-STOCK-041）。

数据流：
  quant_backtest/reports/rps_latest.json（青萍每日多维共振产物，res 行含 action）
    → 过滤 market=CN 且 action 含「买入」
    → 转 poc_* 兼容格式（outputs.D1.action/confidence）
    → data/poc_qpzm_*.json 落盘（与 AI 团队/双刀产物同构，trace 可溯）
    → engine.exec_run(strategy='qingping') 复用既有目标仓位/幂等/风控/撮合

口径：
  · 青萍 action 为二值（买入/观察/回避），无连续置信度 → 基线 CONF=0.60
    （与双刀同款基线：目标仓位 = 20% × 0.60/0.75 = 16%，保守余量；score 原值存 _meta 供审计）
  · 环境闸门语义在上游已处理（回避（环境闸门）不进买入），本层只认「买入」
  · US/CRYPTO 买入行 → skip 记录（引擎行情湖仅 A 股，接入国际行情是独立挂账项）
  · 幂等：exec_run 的 ref={run文件}#{stock} 天然防重，同缓存重复导入自动跳过
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATION = HERE.parent.parent
REPORTS = STATION / "quant_backtest" / "reports"
DATA_DIR = HERE.parent / "data"

QINGPING_CONF = 0.60


def _cache_path() -> Path:
    p = REPORTS / "rps_latest.json"
    if not p.exists():
        raise FileNotFoundError("找不到 rps_latest.json（先跑青萍每日共振扫描）")
    return p


def cache_to_run(cache_path: Path | None = None, out_dir: Path | None = None) -> Path:
    """青萍产物 → poc_* 兼容决策产物（strategy 语义在 exec_run 侧标注）。"""
    cache_path = Path(cache_path) if cache_path else _cache_path()
    out_dir = Path(out_dir) if out_dir else DATA_DIR
    d = json.loads(cache_path.read_text(encoding="utf-8"))
    items, skipped = [], []
    for row in d.get("res") or []:
        code = str(row.get("code", "")).strip()
        market = str(row.get("market", "")).strip()
        action = str(row.get("action", ""))
        if "买入" not in action:
            continue                      # 观察/回避（含环境闸门）不进 picks
        if market != "CN":
            skipped.append({"stock": code or market,
                            "why": f"market={market} 引擎行情湖不支持（仅 A 股）"})
            continue
        if not code:
            continue
        items.append({
            "stock": code,
            "asof": d.get("ts") or datetime.now().isoformat(timespec="seconds"),
            "outputs": {"D1": {"action": "BUY", "confidence": QINGPING_CONF}},
            "_meta": {"source": "qingping", "name": row.get("name"),
                      "score": row.get("score"), "px": row.get("px"),
                      "dims": row.get("dims"), "rps": row.get("rps")},
        })
    if not items and not skipped:
        raise ValueError("青萍产物无买入行（res 为空或全回避）")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"poc_qpzm_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def run(dry_run: bool = False, cache_path: Path | None = None) -> dict:
    """一条龙：青萍产物 → run 文件 → exec 落单（strategy=qingping）。"""
    sys.path.insert(0, str(HERE))
    from engine import PaperEngine          # noqa: E402
    from market import ParquetMarket        # noqa: E402

    run_path = cache_to_run(cache_path)
    eng = PaperEngine(DATA_DIR / "paper.db", ParquetMarket())
    r = eng.exec_run(run_path, dry_run=dry_run, strategy="qingping")
    r["run_path"] = str(run_path)
    return r


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="青萍之末买入信号 → 模拟仓落单")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cache", default=None, help="指定 rps_latest.json 路径")
    ap.add_argument("--json", action="store_true", help="JSON 输出（供门户 API 子进程解析）")
    a = ap.parse_args()
    try:
        res = run(dry_run=a.dry_run, cache_path=a.cache)
        res["ok"] = True
    except Exception as e:  # 产物缺失/无买入等 → fail loud（JSON 化）
        if a.json:
            print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"},
                             ensure_ascii=False))
            sys.exit(2)
        raise
    if a.json:
        n_pend = len([o for o in res["orders"] if o.get("status") == "pending"])
        print(json.dumps({"ok": True, "run_path": res["run_path"],
                          "pending": n_pend, "orders": res["orders"],
                          "skipped": res["skipped"]},
                         ensure_ascii=False))
    else:
        print(f"青萍产物 {Path(res['run_path']).name}: 落单 "
              f"{len([o for o in res['orders'] if o.get('status') == 'pending'])} 笔")
        for o in res["orders"]:
            print(f"  [{o['status']}] {o['side']} {o['stock']} {o['qty']} 股 | {o['reason']}")
        for s in res["skipped"]:
            print(f"  [skip] {s['stock']}: {s['why']}")
