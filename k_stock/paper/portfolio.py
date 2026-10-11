# -*- coding: utf-8 -*-
"""K-STOCK-122 M4.2 组合策略集并集自动跟单。

组合（portal/data/strategy_subscriptions.json）聘交易员后：
- hires.json 落 source=<pf_id> 雇佣条目（live/training 校验同 hire 口径，
  但绕过 trainee.hire 的 STRAT_OF_SOURCE 白名单——组合 id 是合法新源）；
- exec：读组合内各策略最新 poc run → **并集去重**（同 stock 按订阅顺序
  先到先得，多策略重复信号只取最高优先级一条）→ 合并 run 落盘
  poc_<pf_id>_<ts>.json → trainee.exec_for(run_path=merged, source=<pf_id>)
  （replan/幂等/交易日守卫/tier_gate 全部复用既有链）。

用法：
  python portfolio.py hire   --pf pf_001 --trader chaser [--json]
  python portfolio.py exec   --pf pf_001 [--dry-run] [--json]
  python portfolio.py board  --pf pf_001 [--json]   # 组合+雇佣状态快照
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from engine import Reject, _norm_stock          # noqa: E402
import trainee                                  # noqa: E402
import picks_adapter                            # noqa: E402

PORTAL_DATA = HERE.parent.parent / "portal" / "data"
SUBS_PATH = PORTAL_DATA / "strategy_subscriptions.json"
STRAT_PREFIX = {
    # 策略 id（strategies.json / 订阅表口径）→ 产物前缀
    "double_knife": "poc_qd_",      # 量子双刀（qd_scanner 产物）
    "qd": "poc_qd_",                # 兼容源名口径
    "qingping": "poc_qpzm_",
    "qpzm": "poc_qpzm_",            # 青萍之末（CN；qpzmus 独立 US 账本不并入）
}
# 其余策略（chanlun/ninedim/strong/weak/jev-gate）→ picks_adapter 动态生成
# （M4.3）；linkage/cross/qpzm-tool 等仍无聚合信号 → exec 时记 skipped 挂账


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_subs(pf_id: str) -> dict:
    if not SUBS_PATH.exists():
        raise Reject("无订阅数据（%s 不存在）" % SUBS_PATH)
    d = json.loads(SUBS_PATH.read_text(encoding="utf-8"))
    for p in d.get("portfolios") or []:
        if p.get("id") == pf_id:
            return p
    raise Reject("组合不存在: %s" % pf_id)


def ensure_hire(pf_id: str, trader: str) -> dict:
    """组合聘交易员：live/training 校验 → hires.json 落 source=<pf_id> 条目。
    幂等：同 trader+source 已 active 则跳过。"""
    try:
        prof = trainee.load_profile(trader)
    except Reject as e:
        return {"ok": False, "error": str(e)}
    if prof.get("status") not in ("training", "live"):
        return {"ok": False, "error":
                "%s 状态 %s ≠ live/training（雇佣硬约束）"
                % (trader, prof.get("status"))}
    d = trainee.load_hires()
    for h in d.get("hires", []):
        if (h.get("trader") == trader and h.get("source") == pf_id
                and h.get("status") == "active"):
            return {"ok": True, "note": "已在职，跳过", "hires": d["hires"]}
    d["hires"].append({"trader": trader, "source": pf_id,
                       "status": "active", "hired_at": _now(),
                       "note": "K-STOCK-122 M4.2 组合跟单雇佣"})
    trainee._save_hires(d)
    return {"ok": True, "note": "%s 已聘为组合 %s 操盘手" % (trader, pf_id)}


def _newest_run_for_strategy(sid: str) -> Path | None:
    prefix = STRAT_PREFIX.get(sid)
    if prefix is not None:
        runs = sorted(trainee.DATA_DIR.glob(prefix + "*.json"))
        return runs[-1] if runs else None
    # M4.3：无独立产物链的策略 → todays_picks 适配器动态生成当日 run
    if sid in picks_adapter.ADAPTER_STRATS:
        return picks_adapter.build_run(sid)
    return None


def merge_portfolio_runs(pf: dict) -> tuple[Path, list]:
    """组合策略集并集去重 → 合并 run 文件。返回 (path, per_strategy 统计)。"""
    seen: dict[str, dict] = {}
    stats = []
    for sid in pf.get("strategies") or []:
        run = _newest_run_for_strategy(sid)
        if run is None:
            stats.append({"strategy": sid, "run": None,
                          "why": "暂无 poc 产物链（prefix 未注册）"})
            continue
        items = json.loads(run.read_text(encoding="utf-8"))
        n_in, n_kept = len(items), 0
        for it in items:
            key = _norm_stock(str(it.get("stock", "")))
            if not key or it.get("error"):
                continue
            if key in seen:
                continue          # 订阅顺序先到先得（去重核心）
            it2 = dict(it)
            it2["_src_strategy"] = sid
            seen[key] = it2
            n_kept += 1
        stats.append({"strategy": sid, "run": run.name,
                      "items": n_in, "kept": n_kept})
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    merged = trainee.DATA_DIR / ("poc_%s_%s.json" % (pf["id"], ts))
    merged.write_text(json.dumps(list(seen.values()), ensure_ascii=False),
                      encoding="utf-8")
    return merged, stats


def exec_portfolio(pf_id: str, dry_run: bool = False,
                   force_trading: bool = False) -> dict:
    """组合并集 run → 雇佣交易员落单（复用 exec_for 既有守卫链）。"""
    pf = _load_subs(pf_id)
    hires = [h for h in trainee.load_hires().get("hires", [])
             if h.get("source") == pf_id and h.get("status") == "active"]
    if not hires:
        return {"ok": False, "error":
                "组合 %s 无在职交易员（先 portfolio.py hire）" % pf_id}
    merged, stats = merge_portfolio_runs(pf)
    r = trainee.exec_for(run_path=str(merged), source=pf_id, dry_run=dry_run,
                         force_trading=force_trading)
    r["portfolio"] = {"id": pf_id, "strategies": pf.get("strategies"),
                      "merge_stats": stats, "merged_run": merged.name}
    return r


def board(pf_id: str) -> dict:
    pf = _load_subs(pf_id)
    hires = [h for h in trainee.load_hires().get("hires", [])
             if h.get("source") == pf_id]
    return {"ok": True, "portfolio": {k: pf.get(k) for k in
                                      ("id", "name", "strategies", "trader",
                                       "price", "paid")},
            "hires": hires}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="portfolio", description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true")
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("hire", parents=[common])
    p.add_argument("--pf", default="pf_001")
    p.add_argument("--trader", required=True)
    p = sub.add_parser("exec", parents=[common])
    p.add_argument("--pf", default="pf_001")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force-trading", dest="force_trading",
                   action="store_true", help="调试绕过交易日守卫")
    p = sub.add_parser("board", parents=[common])
    p.add_argument("--pf", default="pf_001")
    a, _unk = ap.parse_known_args(argv)
    if a.cmd == "hire":
        r = ensure_hire(a.pf, a.trader)
    elif a.cmd == "exec":
        r = exec_portfolio(a.pf, dry_run=a.dry_run,
                           force_trading=a.force_trading)
    else:
        r = board(a.pf)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, default=str))
    else:
        print(json.dumps({k: r.get(k) for k in
                          ("ok", "error", "note", "portfolio")},
                         ensure_ascii=False, default=str))
    return 0 if r.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
