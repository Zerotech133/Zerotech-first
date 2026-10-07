"""池级参数寻优：对 watchlist 全池跑网格，按平均夏普（+Top3 命中数）选参。
用法：
  python opt_defaults.py --strategy trend_follow            # 只看榜单
  python opt_defaults.py --strategy trend_follow --apply    # 最优组合写入 config
  python opt_defaults.py --all --apply --push               # 全部策略 + 写入 + 飞书摘要（季度任务用）
"""
import argparse
import datetime
import itertools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import backtest           # noqa: E402
import runner             # noqa: E402  (复用 CFG/resolve_watchlist/NEWS_DIR/push_feishu)
import strategies as strat  # noqa: E402
from data_loader import load_bars  # noqa: E402


def pool_frames(start, end):
    """与 run_scan 相同的全池加载：按市场分组批量查询。"""
    wl = json.load(open(os.path.join(runner.NEWS_DIR, "news_config.json"),
                        encoding="utf-8")).get("watchlist", [])
    entries = runner.resolve_watchlist(wl)
    by_mkt = {}
    for mkt, sym, _nm in entries:
        by_mkt.setdefault(mkt, []).append(sym)
    frames = {}
    for mkt, syms in by_mkt.items():
        got = load_bars(mkt, syms, start, end)
        for sym, df in got.items():
            if len(df) >= 30:
                frames[(mkt, sym)] = df
    return frames


def tune(name, frames, trade):
    """单策略网格寻优：返回按 (平均夏普, Top3命中) 降序的行。"""
    grid = runner.CFG.get("opt_grids", {}).get(name)
    if not grid:
        return []
    keys = list(grid)
    combos = [dict(zip(keys, t))
              for t in itertools.product(*[grid[k] for k in keys])]
    rows = []
    for c in combos:
        sharpes, rets = [], []
        for df in frames.values():
            try:
                sig = strat.REGISTRY[name](df, c)
                m = backtest.run(df, sig, **trade)
            except Exception:
                continue
            sharpes.append(m["sharpe"])
            rets.append(m["total_return"])
        if sharpes:
            rows.append({"params": c,
                         "mean_sharpe": sum(sharpes) / len(sharpes),
                         "mean_ret": sum(rets) / len(rets),
                         "n": len(sharpes), "top3": 0})

    # Top3 命中数：该组合在多少只股票上进该股前三（稳健性加分项）
    def ckey(c):
        return json.dumps(c, sort_keys=True)

    hits = {ckey(c): 0 for c in combos}
    for df in frames.values():
        scored = []
        for c in combos:
            try:
                sig = strat.REGISTRY[name](df, c)
                m = backtest.run(df, sig, **trade)
                scored.append((m["sharpe"], ckey(c)))
            except Exception:
                continue
        for _s, k in sorted(scored, reverse=True)[:3]:
            hits[k] += 1
    for r in rows:
        r["top3"] = hits.get(ckey(r["params"]), 0)
    rows.sort(key=lambda x: (x["mean_sharpe"], x["top3"]), reverse=True)
    return rows


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--strategy")
    g.add_argument("--all", action="store_true",
                   help="对 config opt_grids 里全部策略寻优")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=datetime.date.today().isoformat())
    ap.add_argument("--apply", action="store_true",
                    help="把最优组合写入 config.json 的 strategies.<name>")
    ap.add_argument("--push", action="store_true",
                    help="推送调参摘要到飞书（配合 --apply）")
    a = ap.parse_args()

    names = list(runner.CFG.get("opt_grids", {})) if a.all else [a.strategy]
    if not a.start:
        look = runner.CFG.get("daily_scan", {}).get("lookback_days", 365)
        a.start = (datetime.date.today()
                   - datetime.timedelta(days=look)).isoformat()

    frames = pool_frames(a.start, a.end)
    print("POOL %d stocks · %d strategies · %s ~ %s" % (
        len(frames), len(names), a.start, a.end))
    if len(frames) < 10:
        print("POOL_TOO_SMALL skip retune")
        return 1

    before = {n: runner.CFG["strategies"].get(n, {}) for n in names}
    changed = {}
    for name in names:
        rows = tune(name, frames, runner.CFG["trade"])
        if not rows:
            print("NO_ROWS %s" % name)
            continue
        print("===== %s =====" % name)
        print("%-4s %-44s %8s %8s %5s" % (
            "#", "params", "avgSharpe", "avgRet", "top3"))
        for i, r in enumerate(rows):
            print("%-4d %-44s %8.2f %+7.1f%% %5d" % (
                i + 1, json.dumps(r["params"]), r["mean_sharpe"],
                r["mean_ret"] * 100, r["top3"]))
        best = rows[0]
        changed[name] = (before.get(name), best)
        if a.apply and best["params"] != before.get(name):
            cfg = json.load(open(os.path.join(HERE, "config.json"),
                                 encoding="utf-8"))
            cfg["strategies"][name] = best["params"]
            with open(os.path.join(HERE, "config.json"), "w",
                      encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            print("APPLIED %s -> %s" % (name, json.dumps(best["params"])))

    if a.apply:
        try:
            runner.write_opt_page(frames)  # 调参后顺带刷新寻优榜单页
        except Exception:
            pass

    if a.push and changed:
        lines = ["🔁 量化策略季度调参完成（%d 只 · %s ~ %s）" % (
            len(frames), a.start, a.end)]
        for name, (old, best) in changed.items():
            tag = "未变" if best["params"] == old else "更新 ← %s" % json.dumps(old)
            lines.append("· %s → %s 夏普%.2f [%s]" % (
                name, json.dumps(best["params"]),
                best["mean_sharpe"], tag))
        lines.append("—— 参数为全池样本内寻优，新参数即时生效（16:00 扫描起用）")
        ok = runner.push_feishu("\n".join(lines))
        print("RETUNE_PUSH %s" % ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
