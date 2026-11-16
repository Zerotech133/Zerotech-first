# -*- coding: utf-8 -*-
"""事件资料（公告）生成器：从本地 anns_d 取近期公告并按类型归类（2026-11-16 WO-EVENTS-PROFILE）。

## 为什么

T1/T2 在每一只上都反复点同一件事：**「抢跑异动是否获事件核验」**。此前工单里没有任何公告字段，
所以它们只能写进 `evidence_missing`，结论因此永远停在"待核验"。

本地 `quant_backtest/data/tushare_curated/anns_d.parquet`（268MB，列 `ann_date/ts_code/name/title/url`）
实测可用：`300476.SZ` 有 2657 条，按代码过滤读取 0.87 秒。

## 只做归类与计数，不做判断

按标题关键词把公告分到有限几类，给出**近期条数与最近标题**；不判断利好利空、不打分。
分类词表固定、可复核；未命中归"其他"。

    python -X utf8 tools/events_profile.py 300476.SZ --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CURATED = REPO / "quant_backtest" / "data" / "tushare_curated"

WINDOW_DAYS = 90
RECENT_N = 8
# 类别 → 关键词（命中即归入，按顺序优先）
CATEGORIES = (
    ("业绩", ("业绩预告", "业绩快报", "年度报告", "季度报告", "半年度报告", "第三季度", "第一季度")),
    ("减持", ("减持", "股份变动", "拟减持")),
    ("增持回购", ("增持", "回购")),
    ("诉讼", ("诉讼", "仲裁", "立案")),
    ("监管问询", ("问询函", "关注函", "监管函", "警示函", "处罚")),
    ("重组并购", ("重组", "收购", "重大资产", "要约")),
    ("分红", ("分红", "权益分派", "利润分配")),
    ("质押", ("质押", "冻结", "解押")),
    ("融资", ("非公开发行", "定增", "可转债", "配股")),
)
# 需在 warnings 里点名的"需核验"类（不是好坏判断，只是提醒存在这类事项）
ATTENTION = ("减持", "诉讼", "监管问询", "质押", "重组并购")


def _classify(title: str) -> str:
    for name, kws in CATEGORIES:
        if any(k in title for k in kws):
            return name
    return "其他"


def _read(code: str):
    import pyarrow.parquet as pq
    p = CURATED / "anns_d.parquet"
    if not p.is_file():
        return None
    try:
        tbl = pq.read_table(p, columns=["ann_date", "ts_code", "name", "title"],
                            filters=[("ts_code", "=", code)])
        return tbl.to_pandas() if tbl.num_rows else None
    except Exception:  # noqa: BLE001
        return None


def profile(code: str, day: str | None = None, window_days: int = WINDOW_DAYS) -> dict:
    day = day or dt.date.today().isoformat()
    out: dict = {"code": code, "source": "anns_d", "as_of": day,
                 "window_days": window_days,
                 "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M")}
    df = _read(code)
    if df is None or df.empty:
        out["available"] = False
        out["warnings"] = ["anns_d 无此代码公告记录"]
        return out

    df = df.copy()
    df["ann_date"] = df["ann_date"].astype(str)
    df = df.sort_values("ann_date")

    try:
        cutoff = (dt.datetime.strptime(day, "%Y-%m-%d") - dt.timedelta(days=window_days)).strftime("%Y%m%d")
    except ValueError:
        cutoff = "0"
    win = df[df["ann_date"] >= cutoff]

    by_type: dict[str, int] = {}
    for t in win["title"].astype(str).tolist():
        k = _classify(t)
        by_type[k] = by_type.get(k, 0) + 1

    recent = [{"date": str(r["ann_date"]), "title": str(r["title"])[:100],
               "category": _classify(str(r["title"]))}
              for _, r in win.tail(RECENT_N).iterrows()]

    out.update({
        "available": True,
        "name": str(df.iloc[-1].get("name") or ""),
        "total": int(len(df)),
        "window_count": int(len(win)),
        "last_date": str(df.iloc[-1]["ann_date"]),
        "last_title": str(df.iloc[-1]["title"])[:100],
        "by_category": by_type,
        "recent": recent[::-1],          # 倒序：最新在前
    })
    attention = [c for c in ATTENTION if by_type.get(c)]
    if attention:
        out["attention_categories"] = attention
        out["attention_note"] = "窗口内存在 %s 类公告，需自行判断影响" % "、".join(attention)
    if not len(win):
        out["warnings"] = ["近 %d 天无公告（不等于无事项，可能未披露）" % window_days]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="事件资料（本地 anns_d 公告）")
    ap.add_argument("code")
    ap.add_argument("--day", default=None)
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    p = profile(args.code, args.day, args.window_days)
    print(json.dumps(p, ensure_ascii=False, indent=1))
    return 0 if p.get("available") else 2


if __name__ == "__main__":
    raise SystemExit(main())
