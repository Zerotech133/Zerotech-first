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
from tdx_read import read_range  # noqa: E402

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
                and "买" in (s.get("tag") or "")]
        if not buys:
            continue
        b = max(buys, key=lambda s: s.get("dt") or "")
        sells = [s for s in r["sources"] if s["s"] == "chanlun"
                 and "卖" in (s.get("tag") or "")]
        age = (basis - datetime.strptime(b["dt"], "%Y-%m-%d")).days
        picks.append({"code": r["code"], "name": r.get("name") or "",
                      "tag": b["tag"], "dt": b["dt"], "age": age,
                      "has_sell": bool(sells)})
    return d, basis, picks


def calc_returns(p, basis):
    """信号+2 开盘买入 -> w 有行日收盘卖；统一近窗另计。"""
    df = read_range("ts_daily", "20260101", basis.strftime("%Y%m%d"),
                    codes=[p["code"]],
                    columns=["ts_code", "trade_date", "open", "close"])
    if df is None or len(df) == 0:
        return
    df = df.sort_values("trade_date").reset_index(drop=True)
    post = df[df.trade_date > p["dt"].replace("-", "")]
    if len(post) >= 3:
        buy = float(post["open"].iloc[1])  # 信号+2 交易日开盘
        if buy > 0:
            for w in WINDOWS:
                if len(post) >= 2 + w:
                    p["w%d" % w] = round(
                        (float(post["close"].iloc[1 + w]) / buy - 1) * 100, 2)
    uw = df[(df.trade_date >= (basis - timedelta(days=13)).strftime("%Y%m%d"))
            & (df.trade_date <= basis.strftime("%Y%m%d"))]
    if len(uw) >= 2:
        p["uw"] = round(
            (float(uw["close"].iloc[-1]) / float(uw["open"].iloc[0]) - 1) * 100, 2)


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
            ws = " ".join("w%d=%s" % (w, p.get("w%d" % w)) for w in WINDOWS)
            print("  %-10s %-6s %s %s age=%3d %s 近窗=%s%s"
                  % (p["code"], p["name"][:4], p["tag"], p["dt"], p["age"],
                     ws, p.get("uw"),
                     " ⚠含卖点" if p["has_sell"] else ""))


if __name__ == "__main__":
    main()
