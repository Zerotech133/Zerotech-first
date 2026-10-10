# -*- coding: utf-8 -*-
"""选股信号后验计算器：历史池子里的票，信号日后 1/3/5 交易日的真实反应。

数据地基：portal/data/picks_history/todays_<YYYYMMDD>.json（build_todays_picks.py 每日归档）。
口径：信号日 = 归档的 scan_run_date（数据基准收盘日）。可成交口径（九审）：
      画像日 D 的清单在 D+1 15:10 才产出、最早 D+2 开盘可操作——买入价 =
      信号日后第 2 个交易日开盘，卖出 = 买入日后第 1/3/5 个交易日收盘，
      剔除「高开买不进」的虚假收益（原 D 收盘起算对强势共振票系统性虚高）。
      湖价（ts_daily，未复权原价口径与页面行情列一致）；对照 = 同窗口
      全市场收盘-收盘中位数（隔夜差异在中位数层摊薄，近似合理）。
输出：portal/data/pick_verdicts.json
      summary: 按窗口的整体命中率/均值/超额 + 按策略分层（二十五审方案A：
      by_strategy 只收 T1/T2 可操作层事件，windows 整体仍为全池口径）
      per_code: 每票最近一次信号的后验（前端详情卡直接消费，win 带 exec 首买日）
窗口未满（湖还没走到首次可操作日或窗口终点）如实标 pending，不硬凑。
用法: "C:/Program Files/Python311/python.exe" portal/build_pick_verdicts.py
"""
import glob
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
QB = os.path.join(ROOT, "quant_backtest")
HIST_DIR = os.path.join(HERE, "data", "picks_history")
OUT = os.path.join(HERE, "data", "pick_verdicts.json")
WINDOWS = (1, 3, 5)


def _trading_days(cal):
    """湖全市场交易日序列（升序 YYYYMMDD 字符串）。"""
    return sorted(cal)


def _code_to_ts(code):
    """600540.SH → ts_daily 的 ts_code 同形态（湖本身就是 .SH 风格）。"""
    return (code or "").strip().upper()


def main():
    sys.path.insert(0, QB)
    from tdx_read import read_day, latest_date  # noqa: E402

    archs = sorted(glob.glob(os.path.join(HIST_DIR, "todays_*.json")))
    if not archs:
        print("[verdicts] 无归档可算（等 build_todays_picks 首份归档）")
        sys.exit(0)
    today = latest_date("ts_daily")
    if not today:
        print("[verdicts] 行情湖不可达，跳过")
        sys.exit(0)

    # 对照基准（K-STOCK-077 修）：与个股同口径的窗口收益中位数——
    # exec_d 全表开盘 × d1 全表收盘按 ts_code 对齐，中位=(close/open-1)。
    # 旧实现 day_mid(d1) 只取 d1 单日 pct_chg 中位，与个股 w 天累计错配
    # （牛市系统性负超额/熊市正超额），今日审计发现，n=0 未产数前修掉。
    day_cache = {}

    def window_mid(d0, d1):
        # 真窗口中位：全市场 d0 开盘 → d1 收盘（与个股 exec 买入口径一致）
        key = (d0, d1)
        if key not in day_cache:
            try:
                import pandas as pd
                o = read_day("ts_daily", d0, columns=["ts_code", "open"])
                c = read_day("ts_daily", d1, columns=["ts_code", "close"])
                if o is None or c is None or not len(o) or not len(c):
                    day_cache[key] = None
                    return None
                m = pd.merge(o[["ts_code", "open"]],
                             c[["ts_code", "close"]], on="ts_code")
                vals = sorted(
                    (float(cc) / float(oo) - 1) * 100
                    for oo, cc in zip(m["open"], m["close"])
                    if oo and float(oo) > 0 and cc is not None)
                day_cache[key] = vals[len(vals) // 2] if vals else None
            except Exception as ex:  # noqa: BLE001
                # A5 分诊：静默 None 会让基准对照整体缺失（裁决静默劣化）——留痕
                print("[verdicts] 窗口基准中位数读取失败 %s~%s：%s"
                      % (d0, d1, str(ex)[:60]))
                day_cache[key] = None
        return day_cache[key]

    # 全市场交易日历：任取一只宽基票的交易日序列
    cal_df = read_day("ts_daily", today, columns=["ts_code"])
    # 交易日历从逐日 latest 回推不现实——改从某只票全史读：
    spy = cal_df["ts_code"].iloc[0] if cal_df is not None and len(cal_df) else None
    trade_days = []
    if spy:
        try:
            from tdx_read import read_range
            # ⚠ read_range 带 codes 时 columns 必须含 ts_code（否则 code 列
            # 被裁掉、过滤恒空——tdx_read 实测暗坑）
            df_all = read_range("ts_daily", "20200101", today,
                                codes=[spy],
                                columns=["ts_code", "trade_date"])
            trade_days = sorted({str(d) for d in
                                 (df_all["trade_date"].tolist()
                                  if df_all is not None else [])})
        except Exception as ex:  # noqa: BLE001
            print("[verdicts] 交易日历读取降级：%s" % str(ex)[:60])

    def after_days(sig_day, n):
        """信号日(YYYYMMDD)后第 n 个交易日（严格大于信号日）。"""
        try:
            i = trade_days.index(sig_day)
        except ValueError:
            # 信号日非交易日 → 找首个 >sig 的
            later = [d for d in trade_days if d > sig_day]
            if not later:
                return None
            i = trade_days.index(later[0]) - 1  # 回退一格统一用 index+n
        j = i + n
        return trade_days[j] if j < len(trade_days) else None

    def pct_between(code, exec_day, d1):
        """可成交口径（九审）：exec_day 开盘买入 → d1 收盘卖出。

        画像日 D 的清单在 D+1 15:10 产出、最早 D+2 开盘可操作——起算价
        用首次可操作日开盘，剔除强势票「次日高开买不进」的虚假收益。
        停牌近似：exec_day 无行时首行（复牌日）开盘即买点。
        """
        try:
            from tdx_read import read_range
            df = read_range("ts_daily", exec_day, d1, codes=[code],
                            columns=["ts_code", "trade_date", "open", "close"])
            if df is None or len(df) < 1:
                return None
            df = df.sort_values("trade_date")
            buy = float(df["open"].iloc[0])    # 首次可操作日开盘
            sel = float(df["close"].iloc[-1])  # 窗口终点收盘
            return round((sel / buy - 1) * 100, 2) if buy else None
        except Exception as ex:  # noqa: BLE001
            # A5 分诊：pct_between 是后验收益计算核心——静默 None 会
            # 直接污染 RiskGuard 误杀复算统计（拦截/放行样本收益缺失）——留痕
            print("[verdicts] 个股收益读取失败 code=%s %s~%s：%s"
                  % (code, exec_day, d1, str(ex)[:60]))
            return None

    per_code, buckets = {}, {w: [] for w in WINDOWS}
    strat_buckets = {}
    n_arch = 0
    for fp in archs:
        try:
            doc = json.load(open(fp, encoding="utf-8"))
        except Exception as ex:  # noqa: BLE001
            # A5 分诊：坏档静默跳过会让该信号日整批样本消失——留痕
            print("[verdicts] 档案读取失败跳过 %s：%s"
                  % (os.path.basename(fp), str(ex)[:60]))
            continue
        sig = (doc.get("scan_run_date") or "").replace("-", "")
        if not sig or sig > today:
            continue
        n_arch += 1
        for r in doc.get("stocks") or []:
            code = _code_to_ts(r.get("code"))
            if not code or "." not in code:
                continue  # 后验口径当前只覆盖 A 股（湖 ts_daily）
            rec = per_code.setdefault(code, {
                "name": r.get("name"), "tier": r.get("tier"),
                "date": sig, "strategies": sorted({s.get("s") for s in
                                                   (r.get("sources") or [])
                                                   if s.get("s")}),
                "win": {}})
            if sig < rec["date"]:
                continue  # 只保留每票最近一次信号
            if sig > rec["date"]:
                # 九审修：票再次入选（信号日更新）→ 刷新 date/tier/strategies
                # 并重置 win 重算。原实现 win 已有 key 恒 continue，重复
                # 入选的票永远停在首入选的后验（「最近一次信号」语义失效，
                # 汇总事件样本也漏记新入选）。
                rec.update({"name": r.get("name"), "tier": r.get("tier"),
                            "date": sig, "win": {},
                            "strategies": sorted({s.get("s") for s in
                                                  (r.get("sources") or [])
                                                  if s.get("s")})})
            exec_d = after_days(sig, 2)  # 首次可操作日（画像+1 日产出、+2 开盘可买）
            for w in WINDOWS:
                if w in rec["win"]:
                    continue
                d1 = after_days(exec_d, w) if exec_d else None
                if d1 is None:
                    rec["win"][w] = None  # 窗口未满 → pending
                    continue
                r1 = pct_between(code, exec_d, d1)
                b1 = window_mid(exec_d, d1)
                rec["win"][w] = {"r": r1, "base": b1, "exec": exec_d, "ex": (
                    round(r1 - b1, 2) if r1 is not None and b1 is not None
                    else None)}
                if r1 is not None:
                    buckets[w].append(r1)
                    # 二十五审（方案A 2026-10-10）：策略分层只收可操作层
                    # （T1/T2）事件，tier 取本归档行 r 自身（rec["tier"] 会被
                    # 后续归档覆写成最新信号，事件级语义才对）。全 tier 分母
                    # 混入卖点票/反向参考/无观点影子（chanlun 桶 49% T3 且
                    # 买41/卖49 混算），读作「策略成绩」必然误导。整体
                    # buckets（summary.windows）保持全池口径不变。
                    if r.get("tier") in ("T1", "T2"):
                        for s in rec["strategies"]:
                            strat_buckets.setdefault(s, {}).setdefault(
                                w, []).append(r1)
    # 汇总
    def blk(vals, w):
        if not vals:
            return {"n": 0, "hit": None, "avg": None, "win_rt": None}
        vals_s = sorted(vals)
        return {"n": len(vals),
                "hit": round(100 * sum(1 for v in vals if v > 0) / len(vals), 1),
                "avg": round(sum(vals) / len(vals), 2),
                "win_rt": round(100 * sum(1 for v in vals if v > 0) / len(vals), 1),
                "med": vals_s[len(vals_s) // 2]}
    summary = {
        "asof": today, "archives": n_arch,
        # K-STOCK-070 口径自证：可成交口径（九审）自 2026-10-10 起生效，
        # 与此前 D 收盘起算的旧口径数字不可直接比（旧口径对强势票系统性虚高）
        "caliber": {
            "id": "exec-d2-open-20261010",
            "buy": "信号日后第2个交易日开盘（清单 D+1 15:10 产出、D+2 开盘可操作）",
            "sell": "首买日后 1/3/5 个交易日收盘",
            "benchmark": "同窗口全市场收盘-收盘中位数",
            "supersedes": "close-d0（2026-10-10 前，不可比）",
        },
        "windows": {str(w): blk(buckets[w], w) for w in WINDOWS},
        # 二十五审（方案A）：分层口径自证——by_strategy 只收 T1/T2 事件
        # （可操作层，分母不含 T3/CROWD 影子），windows 为全池口径，勿混比
        "by_strategy_caliber": "仅 T1/T2 入池事件（可操作层）；windows 整体为全池口径",
        "by_strategy": {s: {str(w): blk(b.get(w) or [], w) for w in WINDOWS}
                        for s, b in sorted(strat_buckets.items())},
    }
    out = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "summary": summary, "per_code": per_code}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("[verdicts] 归档 %d 份 | 覆盖 %d 票 | asof %s" %
          (n_arch, len(per_code), today))
    for w in WINDOWS:
        b = summary["windows"][str(w)]
        print("  execT+%d(首买=信号+2开盘): n=%s 命中率=%s%% 均值=%s%%" %
              (w, b["n"], b["hit"], b["avg"]))


if __name__ == "__main__":
    main()
