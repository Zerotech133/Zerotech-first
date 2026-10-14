"""池级参数寻优：对 watchlist 全池跑网格，按平均夏普（+Top3 命中数）选参。
用法：
  python opt_defaults.py --strategy trend_follow            # 只看榜单
  python opt_defaults.py --strategy trend_follow --apply    # 最优组合写入 config
  python opt_defaults.py --all --apply --push               # 全部策略 + 写入 + 飞书摘要（季度任务用）

W1-2 纪律门：--apply 写 config 前用 walk_forward 口径（train 252d / test 63d
滚动、≥4 折）做样本外校验，OOS 夏普 ≥ 0.6×IS 夏普才放行；否决则写回默认参数。
开关 config.opt_discipline_gate（默认 true），false 保留旧行为（对照模式）。
"""
import argparse
import datetime
import itertools
import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import backtest           # noqa: E402
import metrics as mt      # noqa: E402  (纪律门折窗评估，walk_forward 同源)
import runner             # noqa: E402  (复用 CFG/resolve_watchlist/NEWS_DIR/push_feishu)
import strategies as strat  # noqa: E402
from data_loader import load_bars  # noqa: E402

# 纪律门口径（与 walk_forward.py 对齐）
GATE_TRAIN_N = 252
GATE_TEST_N = 63
GATE_MIN_FOLDS = 4
GATE_RATIO = 0.6


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


def tune(name, frames, trade=None):
    """单策略网格寻优：返回按 (平均夏普, Top3命中) 降序的行。

    成本口径：frames 键为 (market, symbol)，逐标的按所属市场分层取成本
    （CRYPTO 为 T+0）；trade 参数仅为兼容旧调用签名保留，不再使用。
    """
    grid = runner.CFG.get("opt_grids", {}).get(name)
    if not grid:
        return []
    keys = list(grid)
    combos = [dict(zip(keys, t))
              for t in itertools.product(*[grid[k] for k in keys])]
    rows = []
    for c in combos:
        sharpes, rets = [], []
        for key, df in frames.items():
            try:
                tc = backtest.load_trade_config(
                    key[0] if isinstance(key, tuple) else None)
                sig = strat.REGISTRY[name](df, c)
                m = backtest.run(df, sig, **tc)
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
    for key, df in frames.items():
        scored = []
        for c in combos:
            try:
                tc = backtest.load_trade_config(
                    key[0] if isinstance(key, tuple) else None)
                sig = strat.REGISTRY[name](df, c)
                m = backtest.run(df, sig, **tc)
                scored.append((m["sharpe"], ckey(c)))
            except Exception:
                continue
        for _s, k in sorted(scored, reverse=True)[:3]:
            hits[k] += 1
    for r in rows:
        r["top3"] = hits.get(ckey(r["params"]), 0)
    rows.sort(key=lambda x: (x["mean_sharpe"], x["top3"]), reverse=True)
    return rows


def _gate_folds(name, params, frames, train_n=GATE_TRAIN_N,
                test_n=GATE_TEST_N):
    """纪律门折窗评估（walk_forward 口径：train_n/test_n 滚动推进）。

    对网格选出的最优参数组逐折计算：
      IS  夏普 = 训练窗(train_n) 内信号收益夏普（普通口径，同 walk_forward tr_sh）
      OOS 净收益/持仓 = 测试窗(test_n) 段（run_vec 的 shift(1) 在段内生效，
      与 walk_forward._seg 同口径），全部折拼接后统一 evaluate。
    成本用 CN 保守口径（成本12+滑点3=15bps，宁可高估不低估）。
    返回 (is_mean, oos_sharpe, n_folds)；无可用折返回 (None, None, 0)。
    """
    cost = backtest.load_trade_config("CN")
    gate_cost = cost["cost_bps"] + cost["slippage_bps"]
    is_sharpes, oos_nets, oos_poss = [], [], []
    for df in frames.values():
        n = len(df)
        try:
            sig = strat.REGISTRY[name](df, params)
        except Exception:
            continue
        close = df["close"]
        lo = 0
        while lo + train_n + test_n <= n:
            tr_hi = lo + train_n
            te_hi = tr_hi + test_n
            # 样本内：训练窗整段夏普（信号切段后由 run_vec 统一 shift）
            net_tr, _, pos_tr = mt.run_vec(close.iloc[lo:tr_hi],
                                           sig.iloc[lo:tr_hi], gate_cost)
            is_sharpes.append(mt.evaluate(net_tr, pos_tr)["sharpe"])
            # 样本外：测试窗段，折间首尾相接后拼接统一评估
            net_te, _, pos_te = mt.run_vec(close.iloc[tr_hi:te_hi],
                                           sig.iloc[tr_hi:te_hi], gate_cost)
            oos_nets.append(net_te)
            oos_poss.append(pos_te)
            lo += test_n
    if not is_sharpes or not oos_nets:
        return None, None, 0
    oos_m = mt.evaluate(pd.concat(oos_nets), pd.concat(oos_poss))
    return (sum(is_sharpes) / len(is_sharpes), oos_m["sharpe"],
            len(is_sharpes))


def discipline_gate(name, params, frames, min_folds=GATE_MIN_FOLDS,
                    ratio=GATE_RATIO):
    """纪律门：OOS 夏普 ≥ ratio×IS 夏普才允许 --apply 写入 config。

    返回 {"status": PASS/VETO/SKIP, "is": x, "oos": y, "folds": n, "msg": str}
      PASS：样本外达标，放行最优参数
      VETO：OOS < 0.6×IS，写回默认参数并在摘要/飞书明示否决原因
      SKIP：池内样本不足（折数 < 4），标注"样本不足未校验"——不静默放行也不炸任务
    """
    is_mean, oos_sh, n_folds = _gate_folds(name, params, frames)
    if n_folds < min_folds or is_mean is None:
        return {"status": "SKIP", "is": is_mean, "oos": oos_sh,
                "folds": n_folds,
                "msg": "SKIP 样本不足未校验（%d 折 < %d）" % (
                    n_folds, min_folds)}
    if oos_sh >= ratio * is_mean:
        return {"status": "PASS", "is": is_mean, "oos": oos_sh,
                "folds": n_folds,
                "msg": "PASS OOS=%.2f ≥ 0.6×IS=%.2f（%d 折）" % (
                    oos_sh, ratio * is_mean, n_folds)}
    return {"status": "VETO", "is": is_mean, "oos": oos_sh,
            "folds": n_folds,
            "msg": "纪律门否决：%s %s OOS=%.2f < 0.6×IS=%.2f" % (
                name, json.dumps(params), oos_sh, ratio * is_mean)}


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

    # 纪律门开关：config.opt_discipline_gate 默认 true；false 保留旧行为（对照）
    gate_on = bool(runner.CFG.get("opt_discipline_gate", True))

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

        # ── 纪律门：--apply 前样本外校验（gate_on=false 走旧行为对照）──
        gate = None
        apply_params = best["params"]
        if a.apply and gate_on and best["params"] != before.get(name):
            gate = discipline_gate(name, best["params"], frames)
            print("GATE %s %s" % (name, gate["msg"]))
            if gate["status"] == "VETO":
                # 写回默认参数（PARAM_DEFAULTS 为底，缺键用原值补齐）；
                # 回退路径：gate_on=false 或还原 config.json.bak_20261014
                dft = dict(strat.PARAM_DEFAULTS.get(name, {}))
                for k, v in (before.get(name) or {}).items():
                    dft.setdefault(k, v)
                apply_params = dft
        changed[name] = (before.get(name), best, gate, apply_params)

        if a.apply and apply_params != before.get(name):
            cfg = json.load(open(os.path.join(HERE, "config.json"),
                                 encoding="utf-8"))
            cfg["strategies"][name] = apply_params
            with open(os.path.join(HERE, "config.json"), "w",
                      encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            print("APPLIED %s -> %s" % (name, json.dumps(apply_params)))

    if a.apply:
        try:
            runner.write_opt_page(frames)  # 调参后顺带刷新寻优榜单页
        except Exception:
            pass

    if a.push and changed:
        lines = ["🔁 量化策略季度调参完成（%d 只 · %s ~ %s）" % (
            len(frames), a.start, a.end)]
        for name, (old, best, gate, applied) in changed.items():
            tag = "未变" if best["params"] == old else "更新 ← %s" % json.dumps(old)
            lines.append("· %s → %s 夏普%.2f [%s]" % (
                name, json.dumps(applied),
                best["mean_sharpe"], tag))
            if gate is not None:
                lines.append("  纪律门：%s" % gate["msg"])
        lines.append("—— 参数为全池样本内寻优%s，新参数即时生效（16:00 扫描起用）" % (
            "，已过 walk_forward 纪律门" if gate_on else "（纪律门关闭·对照模式）"))
        ok = runner.push_feishu("\n".join(lines))
        print("RETUNE_PUSH %s" % ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
