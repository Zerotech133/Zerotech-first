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
  · K-STOCK-044 M2 起：market=US 买入行走独立通道（poc_qpzmus_*.json → UsMarket 行情 →
    独立账本 us_qingping（USD 原币语义）→ strategy=qingping_us）；CRYPTO 仍 skip
  · US 落单前先 refresh 行情缓存（SPY 日历 + 各标的）；单票 refresh 失败降级 skip 不炸主链
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
US_ACCOUNT = "us_qingping"       # US 独立子账本（USD 原币，与 A 股账本不混算）
US_STRATEGY = "qingping_us"


def _cache_path() -> Path:
    p = REPORTS / "rps_latest.json"
    if not p.exists():
        raise FileNotFoundError("找不到 rps_latest.json（先跑青萍每日共振扫描）")
    return p


def cache_to_run(cache_path: Path | None = None, out_dir: Path | None = None,
                 split_us: bool = False):
    """青萍产物 → poc_* 兼容决策产物（strategy 语义在 exec_run 侧标注）。

    split_us=False（默认）：与历史行为一致——US/CRYPTO 行进 skipped，返回 CN run 路径。
    split_us=True：US 买入行单独写 poc_qpzmus_*.json，返回 (cn_path | None, us_path | None)。
    """
    cache_path = Path(cache_path) if cache_path else _cache_path()
    out_dir = Path(out_dir) if out_dir else DATA_DIR
    d = json.loads(cache_path.read_text(encoding="utf-8"))
    items, us_items, skipped = [], [], []
    for row in d.get("res") or []:
        code = str(row.get("code", "")).strip()
        market = str(row.get("market", "")).strip()
        action = str(row.get("action", ""))
        if "买入" not in action:
            continue                      # 观察/回避（含环境闸门）不进 picks
        if market not in ("CN", "US"):
            skipped.append({"stock": code or market,
                            "why": f"market={market} 暂不支持（CRYPTO 等独立再议）"})
            continue
        if market == "US" and not split_us:
            skipped.append({"stock": code,
                            "why": "market=US 默认兼容口径未路由（split_us=True 开启 US 独立账本）"})
            continue
        if not code:
            continue
        item = {
            "stock": code,
            "asof": d.get("ts") or datetime.now().isoformat(timespec="seconds"),
            "outputs": {"D1": {"action": "BUY", "confidence": QINGPING_CONF}},
            "_meta": {"source": "qingping", "name": row.get("name"),
                      "score": row.get("score"), "px": row.get("px"),
                      "dims": row.get("dims"), "rps": row.get("rps")},
        }
        (items if market == "CN" else us_items).append(item)
    if not items and not us_items and not skipped:
        raise ValueError("青萍产物无买入行（res 为空或全回避）")
    # stamp 派生自缓存 ts（非 now()）：同一份 rps 缓存重复跑 4d 产同名 run 文件
    # → exec_run 的 ref={文件名}#{stock} 不变 → 幂等跳过（now() 时间戳会造出
    # 新文件名 → ref 变 → 同票双份 pending 双倍成交，044 M3 复盘补丁）
    # 缓存无 ts 字段时用文件 mtime 兜底（文件不变 mtime 不变，幂等同样成立）；
    # 仍用 now() 的话幂等彻底失效——红队审查 045a FIX-1
    ts_raw = d.get("ts")
    if not ts_raw:
        ts_raw = datetime.fromtimestamp(
            Path(cache_path).stat().st_mtime).isoformat(timespec="seconds")
    stamp = str(ts_raw).replace("-", "").replace(":", "").replace(" ", "_")
    out_dir.mkdir(parents=True, exist_ok=True)
    cn_path = us_path = None
    if items:
        cn_path = out_dir / f"poc_qpzm_{stamp}.json"
        cn_path.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    if not split_us:
        # 兼容口径：split_us=False 时 US 行回退 skipped（历史行为），必须返回单值 CN 路径
        if cn_path is None:
            raise ValueError("青萍产物无 CN 买入行（split_us=False 仅支持 CN）")
        return cn_path
    if us_items:
        us_path = out_dir / f"poc_qpzmus_{stamp}.json"
        us_path.write_text(json.dumps(us_items, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return cn_path, us_path


def _us_stocks(us_path: Path) -> list[str]:
    """从 US run 文件提取标的（gb_AAPL → refresh 用 aapl）。"""
    payload = json.loads(Path(us_path).read_text(encoding="utf-8"))
    return [str(x.get("stock", "")) for x in payload if x.get("stock")]


def _run_us(us_path: Path, dry_run: bool) -> dict:
    """US 独立账本通道：refresh 行情缓存 → UsMarket 引擎 → exec_run。

    单票 refresh 失败只降级该票（写 skipped），SPY 日历失败才整段放弃。"""
    sys.path.insert(0, str(HERE))
    from engine import PaperEngine          # noqa: E402
    from us_market import UsMarket, refresh  # noqa: E402

    res = {"run": us_path.name, "account_id": US_ACCOUNT, "strategy": US_STRATEGY,
           "orders": [], "skipped": [], "refreshed": [], "dry_run": dry_run}
    try:
        refresh("spy")                     # 日历基准优先
    except Exception as e:  # noqa: BLE001
        return {**res, "skipped": [{"stock": "ALL",
                                    "why": f"SPY 日历 refresh 失败，US 段放弃: {e}"}]}
    for sym in _us_stocks(us_path):
        try:
            p = refresh(sym)
            res["refreshed"].append(p.stem)
        except Exception as e:  # noqa: BLE001
            res["skipped"].append({"stock": sym,
                                   "why": f"行情 refresh 失败降级跳过: {e}"})
    eng = PaperEngine(DATA_DIR / "paper.db", UsMarket())
    r = eng.exec_run(us_path, dry_run=dry_run, strategy=US_STRATEGY,
                     account_id=US_ACCOUNT)
    # 合并：exec 结果为主，refresh 降级行去重后追加（同票已进 exec skipped 则不重复记）
    exec_stocks = {o["stock"] for o in r["orders"]} | {s["stock"] for s in r["skipped"]}
    extra = [s for s in res["skipped"] if s["stock"] not in exec_stocks]
    return {**res, "orders": r["orders"], "skipped": list(r["skipped"]) + extra}


def run(dry_run: bool = False, cache_path: Path | None = None) -> dict:
    """一条龙：青萍产物 → run 文件 → 双路 exec（CN 走 A 股湖 / US 走独立账本）。"""
    sys.path.insert(0, str(HERE))
    from engine import PaperEngine          # noqa: E402
    from market import ParquetMarket        # noqa: E402

    cn_path, us_path = cache_to_run(cache_path, split_us=True)
    result = {"run": Path(cn_path).name if cn_path else (Path(us_path).name if us_path else None),
              "orders": [], "skipped": [], "us": None}
    if cn_path:
        eng = PaperEngine(DATA_DIR / "paper.db", ParquetMarket())
        r = eng.exec_run(cn_path, dry_run=dry_run, strategy="qingping")
        result.update({"run": r["run"], "orders": r["orders"], "skipped": r["skipped"]})
    if us_path:
        result["us"] = _run_us(us_path, dry_run)
    result["run_path"] = str(cn_path or us_path)
    return result


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
        if res.get("us"):
            u = res["us"]
            print(f"US 账本 {u['account_id']}: 落单 "
                  f"{len([o for o in u['orders'] if o.get('status') == 'pending'])} 笔"
                  f"（refresh {len(u.get('refreshed', []))} 票）")
            for o in u["orders"]:
                print(f"  [{o['status']}] {o['side']} {o['stock']} {o['qty']} 股 | {o['reason']}")
            for s in u["skipped"]:
                print(f"  [skip] {s['stock']}: {s['why']}")
