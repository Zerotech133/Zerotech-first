# -*- coding: utf-8 -*-
"""RiskGuard 误杀后验复算分析器 v0.1（K-ORG-113 · S3 备料·只读）。

背景（协作协议 v0 / 086-S3）：critical 拦截样本后验 +0.65% vs 放行 -0.25%，
四条规则（R1 市场质量/R2 流动性冲击/R3 事件风险/R4 仓位集中度）疑似过紧误杀。
kg_riskguard.py L438 注明「复算下轮做」——本工具即该复算的只读实现，
产出 S3 复核（16:10 automation / 规则校准拍板）所需的对账数字。

数据面（全程只读，不写任何账本）：
- 拦截事件账：C:/项目/zerobound-kg/riskguard_ledger/ledger.jsonl
  （32078 行：shadow_block 11567 / pass 20511，字段 security/ts/ref_px/
  action/rule_id/reason/score/mode/data_version）
- 日线价格：E:/KGDataRoot/tdx/ts_daily/part_*.parquet（44 分片，
  列 ts_code/trade_date(YYYYMMDD int)/close，per-kg_paths.get_data_root()）

口径（v0.1 双基准，分节呈现不混桶）：
- 决策日 d = ts 的日期部分；个股交易日历用其自身日线序列（停牌顺延）；
  p = 决策日（或停牌后第一个交易日）bar 下标。
- A 组（ref_px 基准·贴近实盘语义）：base=账本 ref_px（引擎决策参考价），
  fwdN=(close[p+N-1]/base-1)×100。
- B 组（收盘价基准·fallback 大样本）：ref_px 缺失（引擎早期版本未记，97.9%）
  时 base=close[p]，fwdN=(close[p+N]/base-1)×100（自收盘起算 N 个交易日）。
- 决策日 > 日线覆盖尾 → 未来未发生，单列计数不计入后验。
- 数据不足（决策日近尾/缺标的）→ fwd=None 计数。

用法（系统 3.11：C:/Program Files/Python311/python.exe，pandas 判例）：
    python tools/riskguard_postmortem.py                # 分析+落报告
    python tools/riskguard_postmortem.py --no-report    # 只打控制台摘要
报告落 docs/dev/2026-10-15_S3误杀复算报告_K-ORG-113.md。
"""

import argparse
import glob
import json
import os
from bisect import bisect_left
from collections import defaultdict

LEDGER = "C:/项目/zerobound-kg/riskguard_ledger/ledger.jsonl"
TS_DAILY_GLOB = "E:/KGDataRoot/tdx/ts_daily/part_*.parquet"
REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      os.pardir, "docs", "dev",
                      "2026-10-15_S3误杀复算报告_K-ORG-113.md")
HORIZONS = (5, 20)


def load_daily():
    """44 分片 → {ts_code: (dates[int 升序], closes[float])}。"""
    import pandas as pd
    parts = sorted(glob.glob(TS_DAILY_GLOB))
    if not parts:
        raise SystemExit("ts_daily parquet 分片为空: %s" % TS_DAILY_GLOB)
    df = pd.concat([pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
                    for p in parts], ignore_index=True)
    # 分片间 trade_date 类型不一致（int/str 混），统一数值化
    df["trade_date"] = pd.to_numeric(df["trade_date"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["trade_date", "close"])
    df["trade_date"] = df["trade_date"].astype(int)
    daily = {}
    for code, g in df.groupby("ts_code"):
        g = g.sort_values("trade_date")
        daily[code] = (g["trade_date"].tolist(), g["close"].tolist())
    return daily, len(df)


def fwd_returns(daily, security, ts, ref_px, last_date):
    """返回 (fwd5, fwd20, base_src, skip_reason)。

    base_src: 'ref_px' | 'close'；skip_reason: None | 'future'(决策日越过
    价格覆盖尾·未来未发生) | 'no_bar'(无该股日线) | 'short'(bar 不足)。
    """
    ent = daily.get(security)
    if not ent:
        return None, None, None, "no_bar"
    dates, closes = ent
    try:
        d = int(str(ts)[:10].replace("-", ""))
    except (ValueError, TypeError):
        return None, None, None, "no_bar"
    p = bisect_left(dates, d)
    if p >= len(dates):
        return None, None, None, "future"
    if ref_px:  # A 组：ref_px 基准（fwdN 含决策日当根 bar，第 N 日=p+N-1）
        base, src = ref_px, "ref_px"
        idx_of = lambda h: p + h - 1
    else:       # B 组：决策日收盘基准（自 p 收盘起算 N 日=p+N）
        base, src = closes[p], "close"
        if not base:
            return None, None, None, "short"
        idx_of = lambda h: p + h
    out = []
    for h in HORIZONS:
        i = idx_of(h)
        out.append(round((closes[i] / base - 1) * 100, 2)
                   if i < len(closes) and closes[i] else None)
    return out[0], out[1], src, None


def _stats(vals):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    n = len(vals)
    return {"n": n, "mean": round(sum(vals) / n, 2),
            "median": round(vals[n // 2], 2),
            "win": round(sum(1 for v in vals if v > 0) / n * 100, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-report", action="store_true")
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(LEDGER, encoding="utf-8") if l.strip()]
    daily, n_price_rows = load_daily()
    last_date = max(v[0][-1] for v in daily.values())
    print("账本 %d 行 · 日线 %d 分片 %d 行 %d 标的 · 覆盖尾 %d"
          % (len(rows), len(glob.glob(TS_DAILY_GLOB)), n_price_rows, len(daily), last_date))

    # 逐条复算
    skip = defaultdict(int)
    for r in rows:
        f5, f20, src, why = fwd_returns(daily, r.get("security"), r.get("ts"),
                                        r.get("ref_px"), last_date)
        r["_fwd5"], r["_fwd20"], r["_src"] = f5, f20, src
        if why:
            skip[why] += 1
    print("复算分布: ref_px 基准 %d 行 / 收盘基准 %d 行 / 跳过 %s"
          % (sum(1 for r in rows if r["_src"] == "ref_px"),
             sum(1 for r in rows if r["_src"] == "close"),
             dict(skip) or "{}"))

    # 汇总：action × base_src 分组（不混桶）
    def bucket(rows_, act, src):
        return [r for r in rows_ if r["action"] == act and r["_src"] == src]

    table = {}
    for src in ("ref_px", "close"):
        for act in ("shadow_block", "pass"):
            rs = bucket(rows, act, src)
            table[(src, act)] = {"total": len(rs),
                                 "fwd5": _stats([r["_fwd5"] for r in rs]),
                                 "fwd20": _stats([r["_fwd20"] for r in rs])}
    print("\n== 后验对账（fwdN=决策日起第 N 交易日收盘涨幅%%·分基准不混桶）==")
    for (src, act), v in table.items():
        print("[%s·%s] n=%-6d fwd5: %s" % (src, act, v["total"], v["fwd5"]))
        print("%*sfwd20: %s" % (16, "", v["fwd20"]))

    # 拦截按规则分桶（收盘基准大样本为主口径）
    rule_summary = {}
    for rule, rs in sorted(_groupby(bucket(rows, "shadow_block", "close"), "rule_id").items()):
        rule_summary[rule] = {"n": len(rs), "fwd5": _stats([r["_fwd5"] for r in rs])}
    print("\n== 拦截按规则分桶（收盘基准大样本·fwd5 均值越高=误杀越重）==")
    for rule, v in rule_summary.items():
        print("%-24s n=%-6d fwd5: %s" % (rule, v["n"], v["fwd5"]))

    # 按月趋势
    monthly = defaultdict(lambda: ([], []))
    for r in rows:
        if r["_src"] != "close":
            continue
        monthly[str(r.get("ts", ""))[:7]][0 if r["action"] == "shadow_block" else 1].append(r["_fwd5"])
    monthly_out = {m: {"block": _stats(b), "pass": _stats(a)} for m, (b, a) in monthly.items()}
    print("\n== 按月趋势（收盘基准·fwd5 均值：拦截 vs 放行）==")
    for m in sorted(monthly_out):
        v = monthly_out[m]
        print("%s 拦截 %s | 放行 %s" % (m, v["block"] and v["block"]["mean"],
                                        v["pass"] and v["pass"]["mean"]))
    # 成熟度窗口说明（fwdN 需决策日 +N 交易日 ≤ 覆盖尾）
    from collections import Counter
    weekly = Counter()
    for r in rows:
        weekly[str(r.get("ts", ""))[:10]] += 1
    dmin, dmax = min(weekly), max(weekly)
    print("\n决策期 %s ~ %s：fwd5 仅决策日≤覆盖尾前 5 交易日可算，fwd20 需再早 15 个交易日"
          "（本账本积累仅约 1 个月，fwd20 当前物理不可得·非缺陷）" % (dmin, dmax))

    if args.no_report:
        return
    write_report(rows, skip, table, rule_summary, monthly_out, n_price_rows,
                 len(daily), last_date)
    print("\n报告已落:", os.path.normpath(REPORT))


def _groupby(rows, key):
    d = defaultdict(list)
    for r in rows:
        d[r.get(key) or "?"].append(r)
    return d


def _fmt(s):
    return ("%s (%d)" % (s["mean"], s["n"])) if s else "-"


def write_report(rows, skip, table, rule_summary, monthly_out, n_price_rows,
                 n_codes, last_date):
    lines = [
        "# S3 误杀后验复算报告（K-ORG-113 备料）",
        "",
        "- 生成：2026-10-15，工具 `tools/riskguard_postmortem.py` v0.1（只读）",
        "- 数据：ledger.jsonl %d 行（shadow_block %d / pass %d）× 日线 %d 行 %d 标的（覆盖尾 %d）"
        % (len(rows), table[("ref_px", "shadow_block")]["total"] + table[("close", "shadow_block")]["total"],
           table[("ref_px", "pass")]["total"] + table[("close", "pass")]["total"],
           n_price_rows, n_codes, last_date),
        "- 口径：fwdN=(决策日起第 N 交易日收盘/基准-1)×100，个股自身交易日历（停牌顺延）；"
        "**双基准不混桶**——A 组 ref_px 基准（引擎决策参考价·贴近实盘），"
        "B 组收盘价基准（ref_px 缺失回退·大样本）；未来未发生 %d 行、无日线 %d 行不计入"
        % (skip.get("future", 0), skip.get("no_bar", 0)),
        "",
        "## 一、总对账（S3 机械触发线）",
        "",
        "| 基准 | 组 | n | fwd5 均值 | fwd5 中位 | fwd5 胜率 | fwd20 均值 | fwd20 中位 | fwd20 胜率 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for src, slabel in (("ref_px", "A·ref_px"), ("close", "B·收盘")):
        for act, alabel in (("shadow_block", "拦截"), ("pass", "放行")):
            v = table[(src, act)]
            f = lambda s, k: ("%s" % s[k] if k != "win" else "%s%%" % s[k]) if s else "-"
            s5, s20 = v["fwd5"], v["fwd20"]
            lines.append("| %s | %s | %d | %s | %s | %s | %s | %s | %s |" % (
                slabel, alabel, v["total"],
                f(s5, "mean"), f(s5, "median"), f(s5, "win"),
                f(s20, "mean"), f(s20, "median"), f(s20, "win")))
    lines += ["", "## 二、拦截样本按规则分桶（B 组收盘基准·fwd5 均值越高=误杀越重）", "",
              "| 规则 | n | fwd5 均值 | fwd5 中位 | fwd5 胜率 | fwd20 均值 |", "|---|---|---|---|---|---|"]
    ranked = sorted(rule_summary.items(),
                    key=lambda kv: -(kv[1]["fwd5"]["mean"] if kv[1]["fwd5"] else -9e9))
    for rule, v in ranked:
        s5 = v["fwd5"]
        s20 = _stats([r["_fwd20"] for r in _groupby(rows, "rule_id").get(rule, [])
                      if r["action"] == "shadow_block" and r["_src"] == "close"])
        lines.append("| %s | %d | %s | %s | %s | %s |" % (
            rule, v["n"], _fmt(s5), s5 and s5["median"],
            ("%s%%" % s5["win"]) if s5 else "-", _fmt(s20)))
    lines += ["", "## 三、按月趋势（B 组·fwd5 均值%）", "",
              "| 月份 | 拦截均值(n) | 放行均值(n) |", "|---|---|---|"]
    for m in sorted(monthly_out):
        v = monthly_out[m]
        lines.append("| %s | %s | %s |" % (m, _fmt(v["block"]), _fmt(v["pass"])))
    lines += ["", "## 四、给 S3 复核的判读要点", "",
              "0. **数据面声明（防误读）**：本报告复算的是 KGStockAnalysis `riskguard_ledger/"
              "ledger.jsonl`（9-10 月 shadow 决策留痕 ~3.2 万行）；历史判案「拦截 +0.65% vs "
              "放行 -0.25%（213 样本）」来自盯盘 quant_engine 侧更早的分析口径——**两者不同源"
              "不同期，数值不可直接对比**，本报告提供的是当前账本窗口的独立证据。",
              "0b. **成熟度窗口（本版核心局限）**：账本决策期 %s ~ %s（约两周），"
              "fwd5 可算窗=覆盖尾前 5 个交易日前的决策（本版实质≈09-30 单日截面·n≈6000），"
              "fwd20 全部未成熟（物理不可得·非缺陷）——**当前数字不具备拍板效力**，"
              "工具与口径就绪是本版产出；有效拍板需 10 月下旬（fwd5 满月）与 11 月（fwd20）复跑。"
              % (min(str(r.get('ts', ''))[:10] for r in rows),
                 max(str(r.get('ts', ''))[:10] for r in rows)),
              "1. **总对账**：看 B 组（大样本）拦截 vs 放行的 fwd5 差——"
              "若拦截组系统性高于放行组，过紧误杀持续，支持阈值放宽方向；"
              "A 组样本小仅作方向印证。",
              "2. **规则分桶**：fwd5 均值最高的规则=误杀重灾区，校准优先级最高；"
              "R1 市场级闸为市场面规则（误杀面=全市场），与个股级规则分开评估。",
              "3. **月度趋势**：若近月拦截后验收敛，说明数据版本迭代已部分自愈，"
              "拍板幅度宜保守。",
              "4. 局限：shadow 决策留痕非真实成交，滑点与流动性冲击未计入；"
              "B 组收盘基准含「决策当日已收盘才知价」的轻微前视（决策 ts 多为盘前/盘中，"
              "实际可成交价更接近次日开盘），解读时留余量。",
              "5. 后续：kg_riskguard 引擎已补 ref_px 记录（新账本行），观察积累后 "
              "A 组口径将自然增厚；本工具幂等可重跑。"]
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
