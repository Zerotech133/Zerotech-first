# -*- coding: utf-8 -*-
"""T2 缠论票信号年龄分组收益对比（十六审后验工具，2026-10-10 固化）。

口径
----
- 样本：最新 todays_picks.json 的 T2 层中带缠论买点的票（一票多买点取最新）；
  买卖并存的降级票（四审口径）也计入，明细中标注 ⚠含卖点。
- 分组：信号 dt 距画像基准日（basis=scan_run_date 前一交易日，即清单数据日）
  的自然日年龄 <=30 / 31-60 / >60。
- 回测：信号+2 交易日开盘买入（可成交口径，剔除信号日不可知与次日高开），
  持 w=5/10/20 个该票有行日收盘卖出（自动跳停牌）。
- 近窗：统一窗口 = 基准日前推 13 个自然日开盘买 -> 基准日收盘卖，
  消除各组市场环境差异的公平视角（仍有动量机制偏差，见下）。

局限（决策依据，勿略）
----
1. 各组回测窗口所处市场环境不同，跨组 w5~w20 不可直接比；
2. 组内样本 <=16 只，单票极值（如海特生物 w10 -17.8%）主导均值；
3. 同窗"新强老弱"含机制偏差：新 3 买票处验证期动量正盛，老票行情多已兑现；
   且同窗结论对窗口起点高度敏感——首跑实证：统一窗起点 09-22 时 >60 组
   -0.8%（win 54%），起点 09-26 时反转为 +1.4%（win 90%），勿单窗下结论；
4. 结论基准：本工具 2026-10-10 首跑不支持加 age 判定闸门；待 verdicts
   入池口径积累 3~4 周后，用统一基准的同窗可成交数据复跑本工具再决策。

用法：python t2_age_compare.py   （固定三组边界，无需参数）
"""
import json
import sys
from datetime import datetime, timedelta

sys.path.insert(0, r"C:\aios\quant_backtest")
from tdx_read import read_range, table_ready  # noqa: E402

PICKS = r"C:\aios\portal\data\todays_picks.json"
WINDOWS = (5, 10, 20)
GROUPS = (("<=30", 0, 30), ("31-60", 31, 60), (">60", 61, 9999))


def load_picks():
    d = json.load(open(PICKS, encoding="utf-8"))
    basis = datetime.strptime(d["scan_run_date"], "%Y-%m-%d")  # 数据基准日=scan_run_date（与 verdicts 的 sig 口径一致）
    picks = []
    for r in d["stocks"]:
        if r.get("tier") != "T2":
            continue
        buys = [s for s in r["sources"] if s["s"] == "chanlun"
                and "买" in (s.get("tag") or "") and s.get("dt")]
        if not buys:
            continue  # 四十一审：无 dt 买点跳过（age 无法计算，防 ValueError）
        b = max(buys, key=lambda s: s.get("dt") or "")
        sells = [s for s in r["sources"] if s["s"] == "chanlun"
                 and "卖" in (s.get("tag") or "")]
        age = (basis - datetime.strptime(b["dt"], "%Y-%m-%d")).days
        picks.append({"code": r["code"], "name": r.get("name") or "",
                      "tag": b["tag"], "dt": b["dt"], "age": age,
                      "has_sell": bool(sells)})
    return d, basis, picks


def _exdiv_days(df):
    """除权日检测（三十一审同构，降级路径）：相邻日 open_t/close_{t-1}
    <= -30.5% 判除权——涨跌幅限制下不可能是交易行为。返回除权
    trade_date 集合（YYYYMMDD）。

    2026-10-11 复权升级（ts_adj_factor 入湖）：正常路径因子齐备时
    calc_returns 直接按 open×adj/close×adj 修正收益（除权窗口不剔除，
    样本保留）；因子表未就绪/该票缺行才走本守卫剔除。首轮湖实证：
    277 起缺口中 49 起为退市整理真暴跌（因子无跳升），缺口守卫
    假阳性 18%——复权口径下这些样本按真实收益计入。"""
    bad = set()
    o, c, t = df["open"].tolist(), df["close"].tolist(), df["trade_date"].tolist()
    for k in range(1, len(df)):
        prev = float(c[k - 1])
        if prev and float(o[k]) / prev - 1 <= -0.305:
            bad.add(str(t[k])[:8])
    return bad


def calc_returns(p, basis):
    """信号+2 开盘买入 -> w 有行日收盘卖；统一近窗另计。

    2026-10-11 复权口径（ts_adj_factor 入湖）：全窗因子齐备时
    buy=open×adj、sell=close×adj——除权窗口自动修正、样本保留不再
    剔除（四十一审的 exdiv_w* 剔除降级为因子不可用时的回退路径）。"""
    df = read_range("ts_daily", "20260101", basis.strftime("%Y%m%d"),
                    codes=[p["code"]],
                    columns=["ts_code", "trade_date", "open", "close"])
    if df is None or len(df) == 0:
        return
    df = df.sort_values("trade_date").reset_index(drop=True)
    # 复权因子：全窗口齐备才走修正路径（缺一行即降级守卫剔除）
    adj_ok = False
    if table_ready("ts_adj_factor"):
        da = read_range("ts_adj_factor", "20260101",
                        basis.strftime("%Y%m%d"), codes=[p["code"]])
        if da is not None and len(da):
            am = {str(t)[:8]: a for t, a in zip(
                da["trade_date"], da["adj_factor"]) if a is not None}
            if am:
                df["adj"] = [am.get(str(t)[:8]) for t in df["trade_date"]]
                adj_ok = df["adj"].notna().all()
    exd = set() if adj_ok else _exdiv_days(df)
    if adj_ok:
        p["adjfix"] = True  # 明细留痕：复权口径
    post = df[df.trade_date > p["dt"].replace("-", "")]
    if len(post) >= 3:
        buy = float(post["open"].iloc[1])  # 信号+2 交易日开盘
        if buy > 0:
            for w in WINDOWS:
                if len(post) >= 2 + w:
                    if adj_ok:
                        b_adj = float(post["adj"].iloc[1])
                        s_adj = float(post["adj"].iloc[1 + w])
                        p["w%d" % w] = round(
                            (float(post["close"].iloc[1 + w]) * s_adj)
                            / (buy * b_adj) * 100 - 100, 2)
                        continue
                    # 降级：除权污染窗 = 买入日之后至卖出日（价格尺度断裂）
                    span = {str(x)[:8] for x in
                            post["trade_date"].iloc[2:2 + w + 1]}
                    if span & exd:
                        p["exdiv_w%d" % w] = True
                        continue
                    p["w%d" % w] = round(
                        (float(post["close"].iloc[1 + w]) / buy - 1) * 100, 2)
    uw = df[(df.trade_date >= (basis - timedelta(days=13)).strftime("%Y%m%d"))
            & (df.trade_date <= basis.strftime("%Y%m%d"))]
    if len(uw) >= 2:
        if adj_ok:
            p["uw"] = round(
                (float(uw["close"].iloc[-1]) * float(uw["adj"].iloc[-1]))
                / (float(uw["open"].iloc[0]) * float(uw["adj"].iloc[0]))
                * 100 - 100, 2)
        else:
            span = {str(x)[:8] for x in uw["trade_date"].iloc[1:]}
            if span & exd:
                p["exdiv_uw"] = True
            else:
                p["uw"] = round(
                    (float(uw["close"].iloc[-1]) / float(uw["open"].iloc[0])
                     - 1) * 100, 2)


def stat(ps, key):
    vs = sorted(p[key] for p in ps if p.get(key) is not None)
    if not vs:
        return "n=0"
    return ("n=%d mean=%+.1f%% med=%+.1f%% win=%d%% "
            "best=%+.1f worst=%+.1f" % (
                len(vs), sum(vs) / len(vs), vs[len(vs) // 2],
                sum(1 for v in vs if v > 0) / len(vs) * 100,
                max(vs), min(vs)))


def main():
    d, basis, picks = load_picks()
    print("清单 scan_run_date=%s（basis=%s 收盘画像）T2 缠论票 %d 只"
          % (d["scan_run_date"], basis.date(), len(picks)))
    groups = {g[0]: [] for g in GROUPS}
    for p in picks:
        calc_returns(p, basis)
        for g, lo, hi in GROUPS:
            if lo <= p["age"] <= hi:
                groups[g].append(p)
                break
    for g, _, _ in GROUPS:
        ps = groups[g]
        print("\n── age %s 组（%d 只，卖点并存 %d 只）──"
              % (g, len(ps), sum(1 for p in ps if p["has_sell"])))
        for k in tuple("w%d" % w for w in WINDOWS) + ("uw",):
            print("  %-3s %s" % (k, stat(ps, k)))
    for g in (GROUPS[0][0], GROUPS[2][0]):  # <=30 与 >60 两组明细
        print("\n── %s 组明细 ──" % g)
        for p in sorted(groups[g], key=lambda x: -x["age"]):
            ws = " ".join(
                ("w%d=%s" % (w, p.get("w%d" % w))) if p.get("w%d" % w) is not None
                else ("w%d=除权剔" % w) if p.get("exdiv_w%d" % w) else
                ("w%d=未满" % w)
                for w in WINDOWS)
            print("  %-10s %-6s %s %s age=%3d %s 近窗=%s%s"
                  % (p["code"], p["name"][:4], p["tag"], p["dt"], p["age"],
                     ws,
                     "除权剔" if p.get("exdiv_uw") else p.get("uw"),
                     " ⚠含卖点" if p["has_sell"] else ""))


if __name__ == "__main__":
    main()
