# -*- coding: utf-8 -*-
"""P7 东财千股千评采集器（akshare stock_comment_em，全市场一次拉齐）。

价值（P5 报告定位）：散户文本情绪之外的机构侧维度，全市场覆盖
（5202 票，不挑热门）——补雪球 LLM 标注覆盖稀疏的空洞：
  · 机构参与度 [0,1]      聪明钱关注度
  · 综合得分 / 目前排名 / 上升  全市场横截面评价与热度迁移
  · 主力成本 → dist_maincost=(最新价-主力成本)/主力成本  浮筹/套牢结构
  · 关注指数              散户关注热度（可与股吧声量交叉验证）
  · 换手率 / 市盈率        佐证 R2 量能口径

产物：
  data/qianping/qianping_{交易日}.jsonl   全市场日快照（同日重跑覆盖）
  data/qianping/latest.json              最新交易日队列票投影（机读契约）
  reports/qianping_{date}.md             队列票快照表

纪律：akshare 只读白嫖；失败 fail-visible 不静默；交易日以服务端
返回为准（T 盘后更新，盘前拿到的是 T-1，合法观测态）。
CLI：python qianping_collector.py [--codes 000001,600519] [--timeout 120]
"""
import argparse
import json
import os
import sys
from datetime import datetime

import akshare as ak

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "data", "qianping")
# 队列判例路径：news_collector/target_queue_chanlun.json（irm/sse 同款），
# 结构 dict{asof, regime, all, actionable}，元素 code="301563.SZ" 带后缀
QUEUE_F = os.path.join(HERE, "target_queue_chanlun.json")


def load_queue_codes():
    """队列票：默认 actionable，缺失降级 all；剥 .SZ/.SH 后缀。"""
    try:
        with open(QUEUE_F, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return []
    items = cfg.get("actionable") or cfg.get("all") or []
    codes = []
    for item in items:
        code = str(item.get("code") or "")
        code = code.split(".")[0].strip()
        if len(code) == 6 and code.isdigit():
            codes.append(code)
    return sorted(set(codes))


def _f(v):
    """安全 float。"""
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="", help="逗号分隔队列票；空=读 target_queue.json")
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()

    df = ak.stock_comment_em()
    if df is None or df.empty:
        print("FAIL: stock_comment_em 空返回")
        return 1
    cols = {c: c for c in df.columns}
    trade_day = str(df[cols["交易日"]].iloc[0])[:10]
    os.makedirs(OUT_DIR, exist_ok=True)

    # 1) 全市场日快照（同交易日覆盖）
    snap_f = os.path.join(OUT_DIR, "qianping_%s.jsonl" % trade_day.replace("-", ""))
    rows = []
    for _, r in df.iterrows():
        px = _f(r[cols["最新价"]])
        mc = _f(r[cols["主力成本"]])
        rows.append({
            "trade_day": trade_day,
            "code": str(r[cols["代码"]]).zfill(6),
            "name": str(r[cols["名称"]]).strip(),
            "px": px, "chg_pct": _f(r[cols["涨跌幅"]]),
            "turnover_pct": _f(r[cols["换手率"]]),
            "pe_ttm": _f(r[cols["市盈率"]]),
            "main_cost": mc,
            "dist_maincost": (round((px - mc) / mc, 4) if px and mc else None),
            "inst_part": _f(r[cols["机构参与度"]]),
            "score": _f(r[cols["综合得分"]]),
            "rank_up": int(r[cols["上升"]]) if str(r[cols["上升"]]).lstrip("-").isdigit() else None,
            "rank": int(r[cols["目前排名"]]) if str(r[cols["目前排名"]]).isdigit() else None,
            "focus_idx": _f(r[cols["关注指数"]]),
        })
    tmp = snap_f + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, snap_f)

    # 2) 横截面分位（机构参与度/综合得分/关注指数，全市场 rank 百分位）
    for key in ("inst_part", "score", "focus_idx"):
        vals = sorted((r[key] for r in rows if r[key] is not None))
        n = len(vals)
        if not n:
            continue
        import bisect
        for r in rows:
            v = r[key]
            r[key + "_pct"] = (round(bisect.bisect_right(vals, v) / n, 4)
                               if v is not None else None)

    # 3) 队列票投影
    codes = ([c.strip() for c in args.codes.split(",") if c.strip()]
             or load_queue_codes())
    sel = {r["code"]: r for r in rows if r["code"] in set(codes)}
    latest = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trade_day": trade_day, "n_market": len(rows), "n_queue": len(sel),
        "queue": [sel[c] for c in sorted(sel)],
        "snapshot": snap_f,
    }
    with open(os.path.join(OUT_DIR, "latest.json"), "w",
              encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, indent=1)

    # 4) 报告（队列票快照）
    L = ["# 千股千评队列票快照（交易日 %s，全市场 %d 家）" % (trade_day, len(rows)), ""]
    L.append("| 代码 | 名称 | 现价 | 涨跌% | 机构参与度(分位) | 综合得分(分位) | 排名(升) | 关注指数(分位) | 距主力成本 |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for c in sorted(sel):
        r = sel[c]
        L.append("| %s | %s | %s | %+s | %s(%s) | %s(%s) | %s(%+s) | %s(%s) | %+s%% |" % (
            c, r["name"], r["px"], r["chg_pct"],
            r["inst_part"], r.get("inst_part_pct"),
            r["score"], r.get("score_pct"),
            r["rank"], r.get("rank_up"),
            r["focus_idx"], r.get("focus_idx_pct"),
            (round(r["dist_maincost"] * 100, 1)
             if r["dist_maincost"] is not None else "-")))
    L.append("")
    L.append("口径：分位=全市场横截面 rank 百分位；距主力成本=(现价-主力成本)/"
             "主力成本，正=现价上方（浮筹区）负=下方（套牢区）。"
             "生成器 qianping_collector.py，快照 %s" % snap_f)
    rep = os.path.join(os.path.dirname(HERE), "reports",
                       "qianping_%s.md" % datetime.now().strftime("%Y%m%d"))
    with open(rep, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    print("trade_day=%s market=%d queue=%d" % (trade_day, len(rows), len(sel)))
    print("snapshot: %s" % snap_f)
    print("latest:   %s" % os.path.join(OUT_DIR, "latest.json"))
    print("report:   %s" % rep)
    miss = sorted(set(codes) - set(sel))
    if miss:
        print("队列未命中(%d): %s" % (len(miss), ",".join(miss[:10])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
