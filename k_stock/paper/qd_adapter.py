# -*- coding: utf-8 -*-
"""量子双刀 adapter（策略源 → Picks 落单链的第一条非 AI 团队接入）。

数据流：
  portal/qd_scan_cache.json（qd_scanner 每日全湖扫描产物，含 signals_now 当日信号）
    → 本 adapter 转为 poc_* 兼容格式（outputs.D1.action/confidence）
    → data/poc_qd_*.json 落盘（与 AI 团队产物同构，trace 可溯）
    → engine.exec_run(strategy='double_knife') 复用既有目标仓位/幂等/风控/撮合

口径：
  · 双刀信号为二值（触发/不触发），无连续置信度 → 基线 CONF=0.60
    （目标仓位 = 20% × 0.60/0.75 = 16%，刻意低于 AI 团队满配，留保守余量）
  · regime=trend_down 的信号一票否决（双刀 Regime 分层纪律：下行市况不接刀）
  · 幂等：exec_run 的 ref={run文件}#{stock} 天然防重，同缓存重复导入自动跳过
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATION_PORTAL = HERE.parent.parent / "portal"
DATA_DIR = HERE.parent / "data"

DOUBLE_KNIFE_CONF = 0.60
BLOCKED_REGIMES = {"trend_down"}


def _cache_path() -> Path:
    for p in (
        STATION_PORTAL / "qd_scan_cache.json",
        HERE.parent / "qd_scan_cache.json",
    ):
        if p.exists():
            return p
    raise FileNotFoundError("找不到 qd_scan_cache.json（先跑 qd_scanner 生成扫描缓存）")


def cache_to_run(cache_path: Path | None = None, out_dir: Path | None = None) -> Path:
    """扫描缓存 → poc_* 兼容决策产物（strategy 语义在 exec_run 侧标注）。"""
    cache_path = Path(cache_path) if cache_path else _cache_path()
    out_dir = Path(out_dir) if out_dir else DATA_DIR
    d = json.loads(cache_path.read_text(encoding="utf-8"))
    items, skipped = [], []
    for sig in d.get("signals_now") or []:
        code = str(sig.get("code", "")).strip()
        regime = str(sig.get("regime", "")).strip()
        if not code:
            continue
        if regime in BLOCKED_REGIMES:
            skipped.append({"stock": code, "why": f"regime={regime} 下行市况一票否决"})
            continue
        items.append(
            {
                "stock": code,
                "asof": d.get("ts") or datetime.now().isoformat(timespec="seconds"),
                "outputs": {"D1": {"action": "BUY", "confidence": DOUBLE_KNIFE_CONF}},
                "_meta": {
                    "source": "double_knife",
                    "regime": regime,
                    "name": sig.get("name"),
                    "close": sig.get("close"),
                    "tech": sig.get("tech"),
                },
            }
        )
    if not items and not skipped:
        raise ValueError("扫描缓存无当日信号（signals_now 为空）")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"poc_qd_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def run(dry_run: bool = False, cache_path: Path | None = None) -> dict:
    """一条龙：缓存 → 产物 → exec 落单（strategy=double_knife）。"""
    sys.path.insert(0, str(HERE))
    from engine import PaperEngine  # noqa: E402
    from market import ParquetMarket  # noqa: E402

    run_path = cache_to_run(cache_path)
    eng = PaperEngine(DATA_DIR / "paper.db", ParquetMarket())
    r = eng.exec_run(run_path, dry_run=dry_run, strategy="double_knife")
    r["run_path"] = str(run_path)
    return r


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="量子双刀信号 → 模拟仓落单")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--cache",
        default=None,
        help="指定扫描缓存路径（默认 portal/qd_scan_cache.json）",
    )
    ap.add_argument(
        "--json", action="store_true", help="JSON 输出（供门户 API 子进程解析）"
    )
    a = ap.parse_args()
    try:
        res = run(dry_run=a.dry_run, cache_path=a.cache)
        res["ok"] = True
    except Exception as e:  # 缓存缺失/无信号等 → fail loud（JSON 化）
        if a.json:
            print(
                json.dumps(
                    {"ok": False, "error": f"{type(e).__name__}: {e}"},
                    ensure_ascii=False,
                )
            )
            sys.exit(2)
        raise
    if a.json:
        n_pend = len([o for o in res["orders"] if o.get("status") == "pending"])
        print(
            json.dumps(
                {
                    "ok": True,
                    "run_path": res["run_path"],
                    "pending": n_pend,
                    "orders": res["orders"],
                    "skipped": res["skipped"],
                },
                ensure_ascii=False,
            )
        )
    else:
        print(
            f"双刀产物 {Path(res['run_path']).name}: 落单 "
            f"{len([o for o in res['orders'] if o.get('status') == 'pending'])} 笔"
        )
        for o in res["orders"]:
            print(
                f"  [{o['status']}] {o['side']} {o['stock']} {o['qty']} 股 | {o['reason']}"
            )
        for s in res["skipped"]:
            print(f"  [skip] {s['stock']}: {s['why']}")
