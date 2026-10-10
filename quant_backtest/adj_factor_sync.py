# -*- coding: utf-8 -*-
"""ts_adj_factor 复权因子入湖（P2 2026-10-11，2027 分红季前必须）。

背景：湖价 ts_daily 未复权（原价口径，与页面行情列一致），verdicts /
t2_age_compare 跨除权窗口只能 -30.5% 隔夜缺口守卫 void 剔除样本
（三十一审/四十一审）——2026 年实测 77 起，2027 年 5-7 月分红季更多，
样本损失不可持续。本表入湖后消费端按 close×adj 比值算复权收益，
除权窗口从「剔除」升级为「修正」。

数据源：tushare Pro adj_factor（远端 TDX-Trading 仓 30 表无此表——
已实测 /api/data/status，主链 tdx_sync 不可用，走直连；token=.env.local
XT_DATA_KEY，权限已探针验证：单日全市场 5573 行）。因子口径=后复权
累积因子，历史值固定不变，仅新除权时全序列尾部增长。

落库：data/tdx/ts_adj_factor/part_NNNN.parquet（湖布局，tdx_read 的
read_day/read_range/latest_date 零改动可用——trade_date/ts_code 列
约定天然满足）；进度登记 data/tdx/_manifest.json（与 tdx_sync 同构，
done 标志供 table_ready）。因子只需湖内交易日（停牌日湖无行，跨停牌
窗口 close×adj 比值仍正确）。

节奏：按日全市场拉取（单日一次调用拿全 ~5573 行，无翻页边界风险）。
全量=湖 ts_daily 覆盖日 20230731 起逐日（~760 次，含限频退避约 15
分钟，一次性；中断重跑从头覆盖写，幂等）；日常增量=表内 latest_date
之后逐日补（常态 1 次调用/日），不足一片时并入最后分片重写（防小
文件膨胀）。计划任务 AdjFactorSync 每日 18:10（TdxSync 17:30 之后，
因子与当日行情同日齐备；错峰 40 分钟，manifest 读改写无交叠）。

用法：
  python adj_factor_sync.py            # 全量或增量（按表内 done 自判）
  python adj_factor_sync.py --verify   # 数据验证（覆盖率/单调性/除权互证/收益一致）
"""
import argparse
import glob
import json
import os
import sys
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from tushare_sync import ts_call  # noqa: E402  token + 限频退避（61s）
from tdx_read import latest_date, read_range  # noqa: E402

OUT = os.path.join(HERE, "data", "tdx")
TBL = "ts_adj_factor"
MANIFEST = os.path.join(OUT, "_manifest.json")
ROWS_PER_PART = 200_000  # 与 tdx_sync FLUSH_PAGES×PAGE 同口径


def _part_path(i):
    d = os.path.join(OUT, TBL)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "part_%04d.parquet" % i)


def _load_manifest():
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_manifest(m):
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    os.replace(tmp, MANIFEST)


def _lake_days():
    """湖 ts_daily 覆盖的 distinct 交易日（因子只需湖内日子）。"""
    df = read_range("ts_daily", columns=["trade_date"])
    return sorted(df["trade_date"].astype(str).str[:8].unique())


def _flush(buf, part, merge_last):
    """buffer 落盘一个分片；merge_last=并入最后分片重写（增量防小文件）。

    去重键 (ts_code, trade_date)——全量覆盖写与增量并片共用。
    返回 (新 part 序号, 本片行数, 写入路径)。
    """
    df = pd.DataFrame(buf, columns=["ts_code", "trade_date", "adj_factor"])
    out = None
    if merge_last and part > 0:
        last = _part_path(part - 1)
        if os.path.exists(last):
            df = pd.concat([pd.read_parquet(last), df], ignore_index=True)
            out = last
    if out is None:
        out = _part_path(part)
        part += 1
    df = df.drop_duplicates(["ts_code", "trade_date"])
    tmp = out + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, out)
    return part, len(df), out


def sync():
    m = _load_manifest()
    st = m.setdefault(TBL, {"part": 0, "rows": 0, "done": False})
    days = _lake_days()
    if not days:
        print("[%s] 湖 ts_daily 无数据，退出" % TBL)
        return 1
    if st.get("done") and st.get("rows"):
        latest = latest_date(TBL)
        todo = [d for d in days if d > latest]
        mode = "incremental from %s (%d days)" % (latest, len(todo))
        merge_last = True   # 增量小量并入最后分片
    else:
        todo = days
        mode = "full (%d days)" % len(todo)
        merge_last = False  # 全量覆盖写（中断重跑幂等）
        st.update({"part": 0, "rows": 0, "done": False})
    print("[%s] %s: %s -> %s" % (TBL, mode, todo[0] if todo else "-",
                                 todo[-1] if todo else "-"))
    if not todo:
        print("[%s] 已是最新（湖最新日 %s）" % (TBL, days[-1]))
        return 0
    buf, part, t0 = [], st["part"], time.time()
    for i, d in enumerate(todo):
        data = ts_call("adj_factor", {"trade_date": d})
        rows = data.get("items") or []
        for r in rows:
            buf.append({"ts_code": r[0], "trade_date": str(r[1])[:8],
                        "adj_factor": r[2]})
        if (i + 1) % 50 == 0:
            print("  %d/%d days buf=%d %.0fs"
                  % (i + 1, len(todo), len(buf), time.time() - t0),
                  flush=True)
        if len(buf) >= ROWS_PER_PART:
            # 全量每片新开 part（湖布局 20 万行/片）；仅增量（todo 少量）
            # 并入最后分片重写防小文件膨胀。
            part, n, _ = _flush(buf, part, merge_last)
            st.update({"part": part, "rows": st["rows"] + len(buf)})
            _save_manifest(m)
            buf = []
        time.sleep(0.3)
    if buf:
        part, n, _ = _flush(buf, part, merge_last)
        st.update({"part": part, "rows": st["rows"] + len(buf)})
    st["done"] = True
    _save_manifest(m)
    total = sum(len(pd.read_parquet(p)) for p in
                glob.glob(os.path.join(OUT, TBL, "part_*.parquet")))
    st["rows"] = total  # 权威重算（累计加法与去重有微小出入）
    _save_manifest(m)
    print("[%s] done parts=%d rows=%d %.0fs"
          % (TBL, part, total, time.time() - t0))
    return 0


def verify():
    """四项验证：覆盖率 / 因子单调性 / 除权互证 / 复权收益一致性。"""
    adj = read_range(TBL)
    daily = read_range("ts_daily", columns=["ts_code", "trade_date",
                                            "open", "close", "pct_chg"])
    print("[verify] adj %d rows %s->%s；daily %d rows"
          % (len(adj), adj["trade_date"].min(), adj["trade_date"].max(),
             len(daily)))
    ok = True

    # 1) 覆盖率：湖行能 join 到因子的比例（缺行=停牌票当日湖有行而因子
    #    无——tushare 停牌日也给因子，正常应 ~100%）
    m = daily.merge(adj, on=["ts_code", "trade_date"], how="left")
    hit = m["adj_factor"].notna().mean()
    print("[verify] 1) 覆盖率 %.4f（缺 %d/%d）"
          % (hit, m["adj_factor"].isna().sum(), len(m)))
    if hit < 0.99:
        ok = False

    # 2) 单票因子单调非降（后复权因子只随分红/送转增长）。首轮实证
    #    （2026-10-11）：927 处负跳变中 905 处为 2024-06-25/26 tushare
    #    源端精度修订（4 位→3 位小数，幅度 ~-0.02%，万分之一量级噪声，
    #    收益影响 <0.05% 无决策意义）；结构性（≤-0.1%）仅 22 处且全部
    #    为退市票因子重置（600069 等）与源端历史重发——非拉取错误。
    #    断言口径：结构性负跳变 <50 处（大面积=按日拉取损坏）。
    a = adj.sort_values(["ts_code", "trade_date"])
    chg = a["adj_factor"] / a.groupby("ts_code")["adj_factor"].shift(1) - 1
    neg_all = int((chg < -1e-9).sum())
    neg_big = int((chg <= -0.001).sum())
    print("[verify] 2) 因子负跳变 %d 处（精度噪声 %d + 结构性 %d，"
          "结构性应<50）" % (neg_all, neg_all - neg_big, neg_big))
    if neg_big >= 50:
        ok = False

    # 3) 除权互证：湖 -30.5% 隔夜缺口日（三十一审守卫口径）分两类——
    #    因子跳升=真除权（守卫正确）；无跳升且 pct_chg≤-28%=退市整理/
    #    真暴跌（守卫假阳性，首轮实证 49/277=18%，均为低价退市股 open
    #    跳水）。本项度量守卫精度而非因子质量，消费端升级为「因子跳升
    #    判除权」后假阳性归零。
    d2 = daily.sort_values(["ts_code", "trade_date"]).copy()
    d2["prev_close"] = d2.groupby("ts_code")["close"].shift(1)
    d2["gap"] = d2["open"] / d2["prev_close"] - 1
    ex = d2[d2["gap"] <= -0.305][["ts_code", "trade_date"]].dropna()
    a3 = adj.sort_values(["ts_code", "trade_date"]).copy()
    a3["ratio"] = a3["adj_factor"] / \
        a3.groupby("ts_code")["adj_factor"].shift(1)
    exm = ex.merge(a3[["ts_code", "trade_date", "ratio"]],
                   on=["ts_code", "trade_date"]).merge(
        d2[["ts_code", "trade_date", "pct_chg"]],
        on=["ts_code", "trade_date"])
    n_ex = len(exm)
    n_jump = int((exm["ratio"] > 1.0001).sum())
    n_crash = int(((exm["ratio"] <= 1.0001) &
                   (exm["pct_chg"] <= -28)).sum())
    n_other = n_ex - n_jump - n_crash
    print("[verify] 3) 缺口日 %d 起 = 真除权 %d + 真暴跌(退市整理) %d "
          "+ 未归类 %d（未归类应=0）" % (n_ex, n_jump, n_crash, n_other))
    if n_other:
        ok = False

    # 4) 复权收益 vs pct_chg 累积链一致性（pct_chg 已除权校正，两链同窗
    #    口应吻合；误差源=pct_chg 四舍五入，容差 0.5%）
    mm = m.dropna(subset=["adj_factor"]).copy()
    codes = mm["ts_code"].drop_duplicates()
    import random
    random.seed(42)
    picks = random.sample(list(codes), min(50, len(codes)))
    worst, worst_c = 0.0, None
    for c in picks:
        s = mm[mm["ts_code"] == c].sort_values("trade_date")
        if len(s) < 12:
            continue
        w = 5
        h0, h1 = s.iloc[0], s.iloc[w]
        q = (h1["close"] * h1["adj_factor"]) / \
            (h0["close"] * h0["adj_factor"]) - 1
        chain = (1 + s.iloc[1:w + 1]["pct_chg"] / 100).prod() - 1
        diff = abs(q - chain)
        if diff > worst:
            worst, worst_c = diff, c
    print("[verify] 4) 抽 %d 票 5 日窗复权收益 vs pct 链最大偏差 "
          "%.4f（%s，容差 0.005）" % (len(picks), worst, worst_c))
    if worst > 0.005:
        ok = False

    print("[verify] %s" % ("ALL PASS" if ok else "FAIL —— 见上"))
    return 0 if ok else 2


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="ts_adj_factor 复权因子入湖")
    ap.add_argument("--verify", action="store_true",
                    help="只跑数据验证，不拉取")
    a = ap.parse_args()
    # 退出码：0 正常；1 崩溃/无可拉；2 验证失败——launcher 透传 alert_fail
    sys.exit(verify() if a.verify else sync())
