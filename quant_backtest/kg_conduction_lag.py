# -*- coding: utf-8 -*-
"""K208 · C 阶段延伸：传导时滞回测（主题事件日对齐行情出时滞分布）。

回答 K201 打样留下的核心疑问——传导链是静态骨架还是真有「传导」：
上游环节异动（T0）后，同链其他环节在 T+1..T+K 是否跟动（超额收益）。
为 E3 推送口径提供量化依据（哪些环节对有时滞传导证据）。

口径（可成交、零未来函数）：
· 环节日收益 r_g(t) = 成员标的当日收益等权均值（成员按 SUPPLIES_TO 直接
  归属；链内标的 parquet 日线，与 kg_pattern 同源 data/ashare）；
· 发起事件 T0：r_A(t) >= MOVE_THR（默认 3%）的交易日；
· 响应：B 在 T+1..LAGS 的累计超额 = Σ(r_B - r_mkt)，r_mkt = 同主题全体
  链上标的等权日收益（链内基准——样本小打样口径，非全 A 基准）；
· 跟动率：T+1..FOLLOW_WIN 内 max r_B >= FOLLOW_THR 的 T0 占比。
影子纪律：纯读（图 + parquet），落 data/kg_conduction_lag.json 不推送；
样本小（链上 17 标的），输出标注「打样统计，非显著结论」。

用法：
  python kg_conduction_lag.py run            # 图内全部主题
  python kg_conduction_lag.py run 人形机器人 [--days 365]
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pandas as pd  # noqa: E402
from kg_client import KgClient  # noqa: E402

ASHARE = os.path.join(HERE, "data", "ashare")
OUT = os.path.join(HERE, "data", "kg_conduction_lag.json")
MOVE_THR = 0.03  # 发起环节异动阈值（日收益 ≥3%）
FOLLOW_THR = 0.015  # 跟动判定阈值（响应环节日收益 ≥1.5%）
FOLLOW_WIN = 3  # 跟动判定窗口（T+1..T+3）
LAGS = 5  # 时滞分布窗口（T+1..T+5）


def _members(cli):
    """图内链成员：theme → seg → [code]，及 code→name。"""
    with cli._session() as s:
        rows = s.run(
            "MATCH (sec:Security)-[sp:SUPPLIES_TO]->(g:Segment) "
            "RETURN sp.chain AS theme, g.name AS seg, sec.ts_code AS code, "
            "sec.name AS name"
        ).data()
    tree, names = {}, {}
    for r in rows:
        th = r["theme"] or "（未标注链）"
        tree.setdefault(th, {}).setdefault(r["seg"], []).append(r["code"])
        names[r["code"]] = r["name"]
    return tree, names


def _closes(codes, days):
    """链上成员收盘矩阵：{code: Series(date8→close)}（窗口自然日）。"""
    d0 = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    out = {}
    for c in codes:
        fp = os.path.join(ASHARE, "%s.parquet" % c.replace(".", "_"))
        if not os.path.exists(fp):
            continue
        try:
            df = pd.read_parquet(fp)
        except Exception:
            continue
        if not {"trade_date", "close"}.issubset(df.columns):
            continue
        s = df.drop_duplicates("trade_date", keep="last").sort_values("trade_date")
        s = s[s["trade_date"].astype(str).str[:8] >= d0]
        out[c] = pd.Series(
            s["close"].astype(float).values,
            index=s["trade_date"].astype(str).str[:8].values,
        )
    return out


def _seg_returns(tree, closes):
    """环节日收益：{theme: {seg: Series(r)}}（成员等权均值）。"""
    rets = {}
    for th, segs in tree.items():
        rets[th] = {}
        for seg, codes in segs.items():
            ss = [closes[c].pct_change() for c in codes if c in closes]
            if not ss:
                continue
            rets[th][seg] = pd.concat(ss, axis=1).mean(axis=1).dropna()
    return rets


def run(theme=None, days=365):
    """传导时滞回测：环节对 × lag 超额 + 跟动率。theme=None 跑全部。"""
    cli = KgClient(retry=1)
    tree, names = _members(cli)
    cli.close()
    if theme:
        tree = {k: v for k, v in tree.items() if k == theme}
        if not tree:
            print("（图内无主题 %s 的 SUPPLIES_TO 成员）" % theme)
            return
    codes = sorted({c for segs in tree.values() for cs in segs.values() for c in cs})
    closes = _closes(codes, days)
    rets = _seg_returns(tree, closes)

    pairs, n_events = [], 0
    for th, seg_rets in rets.items():
        all_r = pd.concat(list(seg_rets.values()), axis=1)
        r_mkt = all_r.mean(axis=1)  # 链内基准（全体成员等权）
        segs = sorted(seg_rets)
        for a in segs:
            ra = seg_rets[a]
            ev = ra.index[ra >= MOVE_THR].tolist()
            if not ev:
                continue
            n_events += len(ev)
            for b in segs:
                if b == a:
                    continue
                rb = seg_rets[b]
                lags = [[] for _ in range(LAGS)]
                hit = miss = 0
                for t0 in ev:
                    i = rb.index.get_indexer([t0])
                    i0 = int(i[0]) if i.size and i[0] >= 0 else -1
                    if i0 < 0:
                        continue
                    lm = rb.index.get_indexer(rb.index[i0 + 1 : i0 + 1 + LAGS])
                    lm = lm[lm >= 0]
                    if lm.size < LAGS:  # 右端截断（缺未来行情）不计
                        continue
                    im = r_mkt.index.get_indexer(rb.index[lm])
                    im = im[im >= 0]
                    if im.size < LAGS:
                        continue
                    ex = rb.iloc[lm].values - r_mkt.iloc[im].values
                    for k in range(LAGS):
                        lags[k].append(float(ex[k]))
                    if max(rb.iloc[lm[:FOLLOW_WIN]]) >= FOLLOW_THR:
                        hit += 1
                    else:
                        miss += 1
                if not (hit + miss):
                    continue
                pairs.append(
                    {
                        "theme": th,
                        "src": a,
                        "dst": b,
                        "n_t0": hit + miss,
                        "lag_ex": [
                            round(sum(x) / len(x), 4) if x else None for x in lags
                        ],
                        "follow_rate": round(hit / (hit + miss), 3),
                        "src_moves": len(ev),
                    }
                )
    pairs.sort(key=lambda p: -(p["lag_ex"][2] or 0))  # 按 T+3 超额排序

    print(
        "== 传导时滞回测（异动≥%.0f%% · 链内基准 · 窗口 %d 自然日） =="
        % (MOVE_THR * 100, days)
    )
    print(
        "环节 %d · 发起事件 %d · 环节对 %d（样本小打样统计，非显著结论）"
        % (sum(len(x) for x in rets.values()), n_events, len(pairs))
    )
    hdr = (
        "发起环节 → 响应环节".ljust(34)
        + "T0".ljust(5)
        + "".join("T+%d" % (k + 1) for k in range(LAGS))
        + " 跟动率"
    )
    print(hdr)
    for p in pairs[:20]:
        lags_txt = "".join(
            ("%+5.1f%%" % (x * 100)) if x is not None else "    —" for x in p["lag_ex"]
        )
        print(
            "%s %-5d %s %5.0f%%"
            % (
                ("%s → %s" % (p["src"], p["dst"])).ljust(34),
                p["n_t0"],
                lags_txt,
                p["follow_rate"] * 100,
            )
        )
    res = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "window_days": days,
        "move_thr": MOVE_THR,
        "follow_thr": FOLLOW_THR,
        "follow_win": FOLLOW_WIN,
        "lags": LAGS,
        "n_segments": sum(len(x) for x in rets.values()),
        "n_events": n_events,
        "n_pairs": len(pairs),
        "pairs": pairs,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUT), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print("影子落盘: %s" % OUT)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("theme", nargs="?", default=None)
    ap.add_argument("--days", type=int, default=365)
    a = ap.parse_args()
    run(a.theme, a.days)


if __name__ == "__main__":
    main()
