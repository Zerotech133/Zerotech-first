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
  · H-D2 起：market=HK 买入行走独立通道（split_hk=True → poc_qpzmhk_*.json →
    HkMarket 行情（17:30 ChanlunScanHK 湖）→ 独立账本 hk_qingping（HKD 原币语义）
    → strategy=qingping_hk）；watchlist 形态 rt_hk00700 适配器内转 00700.HK
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
                 split_us: bool = False, split_hk: bool = False,
                 write_cn: bool = True):
    """青萍产物 → poc_* 兼容决策产物（strategy 语义在 exec_run 侧标注）。

    split_us=False（默认）：与历史行为一致——US 行进 skipped，返回 CN run 路径。
    split_us=True：US 买入行单独写 poc_qpzmus_*.json，返回 (cn_path | None, us_path | None)。
    split_hk=True：HK 买入行单独写 poc_qpzmhk_*.json（watchlist 形态 rt_hk00700
      适配器内转引擎规范码 00700.HK——引擎 _norm_stock 不识 rt_ 前缀，原样落
      item["stock"] 湖查找必炸），返回三元组 (cn, us, hk)；split_hk=False 时
      HK 行进 skipped（历史行为零变化），返回形态与 split_us 口径一致。
    write_cn=False：不写 CN run 文件（--hk-only 专用，防夹带产物；CN 买入行
      静默不落盘不报错——落单归属另行拍板）。默认 True 历史零变化。
    """
    cache_path = Path(cache_path) if cache_path else _cache_path()
    out_dir = Path(out_dir) if out_dir else DATA_DIR
    d = json.loads(cache_path.read_text(encoding="utf-8"))
    items, us_items, hk_items, skipped = [], [], [], []
    for row in d.get("res") or []:
        code = str(row.get("code", "")).strip()
        market = str(row.get("market", "")).strip()
        action = str(row.get("action", ""))
        if "买入" not in action:
            continue                      # 观察/回避（含环境闸门）不进 picks
        if market not in ("CN", "US", "HK"):
            skipped.append({"stock": code or market,
                            "why": f"market={market} 暂不支持（CRYPTO 等独立再议）"})
            continue
        if market == "US" and not split_us:
            skipped.append({"stock": code,
                            "why": "market=US 默认兼容口径未路由（split_us=True 开启 US 独立账本）"})
            continue
        if market == "HK" and not split_hk:
            skipped.append({"stock": code,
                            "why": "market=HK 默认兼容口径未路由（split_hk=True 开启 HK 独立账本）"})
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
        if market == "CN":
            items.append(item)
        elif market == "US":
            us_items.append(item)
        else:
            item = {**item, "stock": _hk_engine_code(code)}
            hk_items.append(item)
    if not items and not us_items and not hk_items and not skipped:
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
    cn_path = us_path = hk_path = None
    if items and write_cn:
        cn_path = out_dir / f"poc_qpzm_{stamp}.json"
        cn_path.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    if not split_hk:
        # 兼容口径：split_hk=False 时 HK 行回退 skipped（历史行为），
        # 返回形态与 split_us 口径完全一致（单值 / 二元组）。
        if not split_us:
            if cn_path is None and write_cn:
                raise ValueError("青萍产物无 CN 买入行（split_us=False 仅支持 CN）")
            return cn_path
        if us_items:
            us_path = out_dir / f"poc_qpzmus_{stamp}.json"
            us_path.write_text(json.dumps(us_items, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        return cn_path, us_path
    if us_items:
        us_path = out_dir / f"poc_qpzmus_{stamp}.json"
        us_path.write_text(json.dumps(us_items, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    if hk_items:
        hk_path = out_dir / f"poc_qpzmhk_{stamp}.json"
        hk_path.write_text(json.dumps(hk_items, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return cn_path, us_path, hk_path


def _hk_engine_code(raw: str) -> str:
    """watchlist 形态 rt_hk00700 → 引擎规范码 00700.HK（适配器内转换）。

    青萍 res 行 code=watchlist 原码（US 先例 gb_AAPL 纯净直通；HK 必须转换，
    引擎 _norm_stock 只识 \\d{6}\\.(SH|SZ|BJ)，rt_ 前缀原样落 stock 会导致
    湖文件查找失败 → _plan_buy raise）。剥 rt_hk 前缀后走 hk_market 单源
    normalize_hk_code（老票 1-4 位 zfill(5) 同口径）。"""
    sys.path.insert(0, str(HERE))
    from hk_market import normalize_hk_code  # noqa: E402  同目录纯 stdlib 模块
    s = str(raw).strip()
    if s.lower().startswith("rt_hk"):
        s = s[len("rt_hk"):]
    return normalize_hk_code(s)


def _us_stocks(us_path: Path) -> list[str]:
    """从 US run 文件提取标的（gb_AAPL → refresh 用 aapl）。"""
    payload = json.loads(Path(us_path).read_text(encoding="utf-8"))
    return [str(x.get("stock", "")) for x in payload if x.get("stock")]


def _run_us(us_path: Path, dry_run: bool, force_trading: bool = False) -> dict:
    """US 独立账本通道：refresh 行情缓存 → UsMarket 引擎 → exec_run。

    单票 refresh 失败只降级该票（写 skipped），SPY 日历失败才整段放弃。"""
    sys.path.insert(0, str(HERE))
    from engine import PaperEngine          # noqa: E402
    from us_market import UsMarket, refresh  # noqa: E402
    from trade_guard import ensure_trading_day  # noqa: E402

    us_mkt = UsMarket()
    if not force_trading:
        ensure_trading_day(us_mkt, market_label="US")   # 077q 交易日守卫
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
    eng = PaperEngine(DATA_DIR / "paper.db", us_mkt)
    r = eng.exec_run(us_path, dry_run=dry_run, strategy=US_STRATEGY,
                     account_id=US_ACCOUNT)
    # 合并：exec 结果为主，refresh 降级行去重后追加（同票已进 exec skipped 则不重复记）
    exec_stocks = {o["stock"] for o in r["orders"]} | {s["stock"] for s in r["skipped"]}
    extra = [s for s in res["skipped"] if s["stock"] not in exec_stocks]
    return {**res, "orders": r["orders"], "skipped": list(r["skipped"]) + extra}


def _run_hk(hk_path: Path, dry_run: bool, force_trading: bool = False) -> dict:
    """HK 独立账本通道（镜像 _run_us，H-D2）。

    - 交易日守卫：ensure_trading_day(HkMarket(), "HK")（09:30 HKT 界，盘前
      当日可成交，hk_market.py next_trading_day）；
    - hk_pass：聚合日历 refresh + 敞口票湖文件校验 + 引擎豁免（LOT=1 /
      HK 码涨跌停 1.0，进程内生效）——豁免是 monkey-patch，HK 跑批必须
      独立进程（hk_market.py 红线），本函数只跑 hk_% 账本 exec，不与
      main/us 同进程合流；
    - 行情湖数据本体由 17:30 ChanlunScanHK（export_us_hk --market HK）落盘，
      此处零新增拉数代码；
    - 无 hk_% 账本灰态跳过（ok 语义同 US 先例）；敞口票湖缺失降级记录不炸
      settle（缺 bar 单保持 pending 幂等等待）。"""
    sys.path.insert(0, str(HERE))
    from hk_market import HkMarket, hk_pass, HK_ACCOUNT, HK_STRATEGY  # noqa: E402
    from trade_guard import ensure_trading_day  # noqa: E402

    res = {"run": hk_path.name, "account_id": HK_ACCOUNT, "strategy": HK_STRATEGY,
           "orders": [], "skipped": [], "refreshed": [], "dry_run": dry_run}
    if not force_trading:
        ensure_trading_day(HkMarket(), market_label="HK")
    hk = hk_pass(do_refresh=True)
    if not hk:
        return {**res, "skipped": [{"stock": "ALL",
                                    "why": "无 hk_% 账本（灰态，同 US ok 先例），HK 段跳过"}]}
    res["refreshed"] = list(hk["refreshed"])
    if hk["refresh_fail"]:
        res["skipped"].extend({"stock": s.split(":", 1)[0], "why": s}
                              for s in hk["refresh_fail"])
    eng = dict(hk["engines"]).get(HK_ACCOUNT)
    if eng is None:
        return {**res, "skipped": [{"stock": "ALL",
                                    "why": f"hk_pass 引擎清单无 {HK_ACCOUNT}（账本未开立？）"}]}
    r = eng.exec_run(hk_path, dry_run=dry_run, strategy=HK_STRATEGY,
                     account_id=HK_ACCOUNT)
    exec_stocks = {o["stock"] for o in r["orders"]} | {s["stock"] for s in r["skipped"]}
    extra = [s for s in res["skipped"] if s["stock"] not in exec_stocks]
    return {**res, "orders": r["orders"], "skipped": list(r["skipped"]) + extra}


def run_hk_only(dry_run: bool = False, cache_path: Path | None = None,
                force_trading: bool = False) -> dict:
    """仅 HK 段（07:05 automation 4e 专用）：HK 产物落盘 + HK exec。

    不触碰 CN/US 落单、CN run 文件也不写——当前 07:05 编排重构后无 4d
    青萍段，CN/US 落单归属另行拍板，HK 段不得夹带启动（H-D2 实施裁决）。"""
    try:
        hk_path = cache_to_run(cache_path, split_hk=True, write_cn=False)[2]
    except ValueError as e:
        if "无买入行" in str(e):
            # 全回避/空 res：HK 段灰态正常（同 4d 验收单「无买入」口径），
            # 不作失败——4e 汇报灰态即可，exit 0。
            return {"run": None, "orders": [], "skipped": [], "hk": None,
                    "run_path": None,
                    "note": "青萍产物无 HK 买入行（灰态正常）"}
        raise
    result = {"run": Path(hk_path).name if hk_path else None, "orders": [],
              "skipped": [], "hk": None,
              "run_path": str(hk_path) if hk_path else None}
    if not hk_path:
        result["note"] = "青萍产物无 HK 买入行（灰态正常）"
        return result
    result["hk"] = _run_hk(hk_path, dry_run, force_trading=force_trading)
    return result


def run_cn_us(dry_run: bool = False, cache_path: Path | None = None,
              force_trading: bool = False) -> dict:
    """仅 CN/US 双路（07:05 automation 4d 段恢复专用，2026-10-20 老田拍板）。

    HK 行走 skipped 侧（split_hk=False）——HK 落单归属 4e --hk-only 段
    （H-D2 分工），本入口不启动 HK，避免同 07:05 窗夹带 HK 落单与 4e
    双消费同源（幂等去重虽可兜住，但分工语义保持干净）。
    灰态：无买入行 / 无 CN/US 买入行 → 返回 note 不 raise（4d 安静日
    路径不作失败，同 4d 验收单「无买入」口径）。"""
    sys.path.insert(0, str(HERE))
    from engine import PaperEngine          # noqa: E402
    from market import ParquetMarket        # noqa: E402
    from trade_guard import ensure_trading_day  # noqa: E402

    try:
        cn_path, us_path = cache_to_run(cache_path, split_us=True, split_hk=False)
    except ValueError as e:
        if "无买入行" in str(e):
            return {"run": None, "orders": [], "skipped": [], "us": None,
                    "run_path": None,
                    "note": "青萍产物无买入行（灰态正常）"}
        raise
    result = {"run": Path(cn_path).name if cn_path else
              (Path(us_path).name if us_path else None),
              "orders": [], "skipped": [], "us": None}
    if not cn_path and not us_path:
        result["note"] = "青萍产物无 CN/US 买入行（灰态正常）"
        return result
    cn_mkt = ParquetMarket()
    if not force_trading:
        ensure_trading_day(cn_mkt, market_label="CN")   # 077q 交易日守卫
    if cn_path:
        eng = PaperEngine(DATA_DIR / "paper.db", cn_mkt)
        r = eng.exec_run(cn_path, dry_run=dry_run, strategy="qingping")
        result.update({"run": r["run"], "orders": r["orders"], "skipped": r["skipped"]})
    if us_path:
        result["us"] = _run_us(us_path, dry_run, force_trading=force_trading)
    result["run_path"] = str(cn_path or us_path)
    return result


def run(dry_run: bool = False, cache_path: Path | None = None,
        force_trading: bool = False) -> dict:
    """一条龙：青萍产物 → run 文件 → 三路 exec（CN 走 A 股湖 / US、HK 走独立账本）。"""
    sys.path.insert(0, str(HERE))
    from engine import PaperEngine          # noqa: E402
    from market import ParquetMarket        # noqa: E402
    from trade_guard import ensure_trading_day  # noqa: E402

    cn_mkt = ParquetMarket()
    if not force_trading:
        ensure_trading_day(cn_mkt, market_label="CN")   # 077q 交易日守卫
    cn_path, us_path, hk_path = cache_to_run(cache_path, split_us=True, split_hk=True)
    result = {"run": Path(cn_path).name if cn_path else next(
                  (Path(p).name for p in (us_path, hk_path) if p), None),
              "orders": [], "skipped": [], "us": None, "hk": None}
    if cn_path:
        eng = PaperEngine(DATA_DIR / "paper.db", cn_mkt)
        r = eng.exec_run(cn_path, dry_run=dry_run, strategy="qingping")
        result.update({"run": r["run"], "orders": r["orders"], "skipped": r["skipped"]})
    if us_path:
        result["us"] = _run_us(us_path, dry_run, force_trading=force_trading)
    if hk_path:
        result["hk"] = _run_hk(hk_path, dry_run, force_trading=force_trading)
    result["run_path"] = str(cn_path or us_path or hk_path)
    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="青萍之末买入信号 → 模拟仓落单")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--hk-only", action="store_true",
                    help="仅跑 HK 段（07:05 4e 专用；CN/US 落单不在此触发）")
    ap.add_argument("--cnus", action="store_true",
                    help="仅跑 CN/US 双路（07:05 4d 恢复；HK 落单不在此触发）")
    ap.add_argument("--cache", default=None, help="指定 rps_latest.json 路径")
    ap.add_argument("--force-trading", dest="force_trading", action="store_true",
                    help="跳过交易日守卫（调试用；automation 勿加）")
    ap.add_argument("--json", action="store_true", help="JSON 输出（供门户 API 子进程解析）")
    a = ap.parse_args()
    try:
        res = (run_hk_only(dry_run=a.dry_run, cache_path=a.cache,
                           force_trading=getattr(a, "force_trading", False))
               if a.hk_only else
               run_cn_us(dry_run=a.dry_run, cache_path=a.cache,
                         force_trading=getattr(a, "force_trading", False))
               if a.cnus else
               run(dry_run=a.dry_run, cache_path=a.cache,
                   force_trading=getattr(a, "force_trading", False)))
        res["ok"] = True
    except Exception as e:  # 产物缺失/无买入等 → fail loud（JSON 化）
        if a.json:
            print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"},
                             ensure_ascii=False))
            sys.exit(2)
        raise
    if a.json:
        n_pend = len([o for o in res["orders"] if o.get("status") == "pending"])
        out = {"ok": True, "run_path": res["run_path"],
               "pending": n_pend, "orders": res["orders"],
               "skipped": res["skipped"]}
        if res.get("note"):
            out["note"] = res["note"]       # 灰态口径透出（供 automation 汇报）
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"青萍产物 {Path(res['run_path']).name if res['run_path'] else '（灰态：无买入行）'}: "
              f"落单 {len([o for o in res['orders'] if o.get('status') == 'pending'])} 笔")
        if res.get("note"):
            print(f"  [note] {res['note']}")
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
        if res.get("hk"):
            h = res["hk"]
            print(f"HK 账本 {h['account_id']}: 落单 "
                  f"{len([o for o in h['orders'] if o.get('status') == 'pending'])} 笔"
                  f"（refresh {len(h.get('refreshed', []))} 项）")
            for o in h["orders"]:
                print(f"  [{o['status']}] {o['side']} {o['stock']} {o['qty']} 股 | {o['reason']}")
            for s in h["skipped"]:
                print(f"  [skip] {s['stock']}: {s['why']}")
