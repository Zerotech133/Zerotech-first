# -*- coding: utf-8 -*-
"""板块源证据包 —— tdx_sector_daily 断供后，能不能切到 ts_ths_daily？

只出证据，不改任何口径（用户 2026-11-12 裁定）。

为什么单独成文而不是直接切
--------------------------
tdx 板块是**通达信自编指数**（880xxx/881xxx），ths 是**同花顺板块**（885xxx.TI）。
两套代码体系独立，同名板块也不保证成分股一致。切源会改结论，
必须先把「差在哪、差多少、谁受影响」摆出来再让人做决定。

证据四段
--------
1. 断供现场：本地最后一天、缺了哪些交易日、断供前后每天行数
2. 源对比：代码体系、板块数、日期覆盖、字段
3. 可映射性：名称重叠率（唯一能做映射的桥）
4. 消费方：37 个文件各自读什么（切源要改的就是这些地方）

用法
----
  python sector_evidence.py            # 生成 reports/sector_source_evidence.json
  python sector_evidence.py --show     # 顺便打印摘要
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import data_contracts as DC  # noqa: E402

REPORTS = os.path.join(HERE, "reports")
OUT = os.path.join(REPORTS, "sector_source_evidence.json")
HEALTH = os.path.join(REPORTS, "contract_health.json")
TDX = "tdx_sector_daily"
THS = "ts_ths_daily"


def _frames():
    import tdx_read as T
    return {
        "tdx": T.read_all(TDX),
        "ths": T.read_all(THS),
        "tdx_def": T.read_all("tdx_sector_def"),
        "ths_idx": T.read_all("ts_ths_index"),
    }


def _src_stat(df, code_col, name_col=None):
    d = df["trade_date"].astype(str)
    codes = df[code_col].astype(str)
    prefix = codes.str[:3].value_counts().head(6).to_dict()
    out = {"rows": int(len(df)), "codes": int(codes.nunique()),
           "date_min": d.min(), "date_max": d.max(),
           "n_trade_days": int(d.nunique()), "code_prefix": prefix}
    if name_col:
        out["has_name"] = True
    return out


def _outage(df, open_days, base):
    """断供现场：不是看「最后一天」，而是看「每天还有多少个板块在更新」。

    2026-11-12 实测改变了结论：tdx_sector_daily 不是 10-22 突然停，
    而是 **10-13 起每天只剩 3 行（此前 1119 行）**。上游仍在每天写、
    _fetched_at 一直很新 —— 所有只看「最后日期」的检查都被骗了：
    日期在往前走，内容却只覆盖 3/1119 个板块。

    判据：单日行数 vs 历史中位数。跌破 50% 就算实质断供，与「最后一天」无关。
    """
    d = df["trade_date"].astype(str)
    last = d.max()
    daily = d.value_counts().sort_index()
    med = float(daily.median())

    # 从尾部往前找「第一次跌破中位数 50%」的那天 —— 那才是真实断供起点
    bad = daily[daily < med * 0.5]
    collapse_at = bad.index.min() if len(bad) else None
    collapse_days = [str(x) for x in bad.index]

    missing = [x for x in open_days if last < x <= base]
    # 实质缺失 = 从萎缩日到基准日之间的交易日（含被萎缩覆盖的「假更新」期间）
    real_gap = [x for x in open_days
                if (collapse_at is None or x > collapse_at) and x <= base]
    return {
        "local_last": last,
        "want": base,
        "missing_trade_days": len(missing),
        "missing_head": missing[:5],
        "median_rows_per_day": int(med),
        "collapse_at": collapse_at,
        "collapse_rows": int(daily.get(collapse_at, 0)) if collapse_at else None,
        "collapse_days": collapse_days,
        "real_gap_trade_days": len(real_gap),
        "rows_per_day_tail": {str(k): int(v) for k, v in daily.tail(12).items()},
        "trap": "「最后一天在往前走」不等于数据在更新。10-13~10-22 每天都有"
                "3 行新数据，日期看着新鲜，实际覆盖 3/1119 个板块。",
    }


def _name_map(a_def, b_idx):
    """名称是两套代码体系之间唯一能对的桥。"""
    a = {str(r["name"]).strip(): str(r["code"]) for _, r in a_def.iterrows()}
    b = {str(r["name"]).strip(): str(r["ts_code"]) for _, r in b_idx.iterrows()}
    both = sorted(set(a) & set(b))
    return {
        "tdx_names": len(a), "ths_names": len(b),
        "overlap": len(both),
        "overlap_pct": round(100.0 * len(both) / max(1, len(a)), 1),
        "both_only_in_tdx": len(set(a) - set(b)),
        "both_only_in_ths": len(set(b) - set(a)),
        "sample": [{"name": n, "tdx": a[n], "ths": b[n]} for n in both[:10]],
        "caveat": "同名 ≠ 同成分。名称重叠只能当候选集，切源前必须逐板块核成分股。",
    }


def _consumers():
    """谁在用这张表、读的是什么。切源要改的就是这些地方。

    不能读 contract_health.json 里的 consumers —— 那里为控体积只存了前 6 个，
    会低估影响面（实测 37 个消费方只显示出 6 个）。
    """
    try:
        import contract_health as CH
        files = CH.build_consumers([TDX]).get(TDX) or []
    except Exception:
        return []
    out = []
    for rel in files:
        path = os.path.join(HERE, rel)
        hits = []
        try:
            txt = open(path, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        for m in re.finditer(r"^.*\b" + TDX + r"\b.*$", txt, re.M):
            hits.append(m.group(0).strip()[:110])
        out.append({"file": rel, "refs": hits[:3], "n_refs": len(hits)})
    out.sort(key=lambda x: -x["n_refs"])
    return out


def build():
    import data_contracts as DC  # noqa: F811
    now = dt.datetime.now()
    open_days = DC.load_open_days()
    base = DC.base_asof(open_days, now)

    fr = _frames()
    tdx = fr["tdx"]
    ths = fr["ths"]

    ev = {
        "run_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "base_asof": base,
        "question": "tdx_sector_daily 断供后能否切到 ts_ths_daily",
        "outage": _outage(tdx, open_days, base),
        "sources": {
            "tdx": _src_stat(tdx, "code"),
            "ths": _src_stat(ths, "ts_code"),
        },
        "columns": {
            "tdx": list(tdx.columns),
            "ths": list(ths.columns),
            "shared": sorted(set(tdx.columns) & set(ths.columns)),
            "tdx_only": sorted(set(tdx.columns) - set(ths.columns)),
            "ths_only": sorted(set(ths.columns) - set(tdx.columns)),
        },
        "mapping": _name_map(fr["tdx_def"], fr["ths_idx"]),
        "consumers": _consumers(),
    }
    ev["verdict"] = _verdict(ev)
    return ev


def _verdict(ev):
    """结论只写证据支持的，不写建议口径。"""
    o, m = ev["outage"], ev["mapping"]
    s = ev["sources"]
    bits = [
        "真实断供起点是 %s：那天起每天只剩 %s 行（历史中位 %d 行），"
        "不是 10-22 —— 此后上游仍每天写入，日期一直在前走，所以「最后一天」查不出问题"
        % (o["collapse_at"], o["collapse_rows"], o["median_rows_per_day"]),
        "实质缺 %d 个交易日（从萎缩日算起），表面只缺 %d 个"
        % (o["real_gap_trade_days"], o["missing_trade_days"]),
        "两源代码体系不同（%s vs %s），不能按代码直接换表"
        % (list(s["tdx"]["code_prefix"])[:1], list(s["ths"]["code_prefix"])[:1]),
        "名称可对上的 %d 个（占 tdx 的 %.1f%%），只能当候选集"
        % (m["overlap"], m["overlap_pct"]),
        "ths 覆盖 %s~%s、%d 个板块，日期上是可用的"
        % (s["ths"]["date_min"], s["ths"]["date_max"], s["ths"]["codes"]),
        "受影响消费方 %d 个文件" % len(ev["consumers"]),
    ]
    return {"facts": bits,
            "not_a_conclusion": "本文件不裁决是否切源；成分股一致性未验证。"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args(argv)
    ev = build()
    os.makedirs(REPORTS, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False, indent=1)

    print("板块源证据 · %s   基准=%s" % (ev["run_at"], ev["base_asof"]))
    o = ev["outage"]
    print("断供：本地最后 %s，表面缺 %d 个交易日" % (o["local_last"], o["missing_trade_days"]))
    print("      真实断供起点 %s（当天 %s 行 / 中位 %d 行），实质缺 %d 个交易日"
          % (o["collapse_at"], o["collapse_rows"], o["median_rows_per_day"],
             o["real_gap_trade_days"]))
    print("      尾部每日行数 %s" % list(o["rows_per_day_tail"].values())[-8:])
    print("源对比：tdx %d 板块/%d 行 (%s~%s)   ths %d 板块/%d 行 (%s~%s)"
          % (ev["sources"]["tdx"]["codes"], ev["sources"]["tdx"]["rows"],
             ev["sources"]["tdx"]["date_min"], ev["sources"]["tdx"]["date_max"],
             ev["sources"]["ths"]["codes"], ev["sources"]["ths"]["rows"],
             ev["sources"]["ths"]["date_min"], ev["sources"]["ths"]["date_max"]))
    m = ev["mapping"]
    print("名称映射：重叠 %d / tdx %d（%.1f%%，仅候选集）"
          % (m["overlap"], m["tdx_names"], m["overlap_pct"]))
    print("消费方：%d 个文件" % len(ev["consumers"]))
    for c in ev["consumers"][:5]:
        print("  %-42s %d 处  %s" % (c["file"], c["n_refs"], (c["refs"] or [""])[0][:60]))
    print("\n事实：")
    for b in ev["verdict"]["facts"]:
        print("  · " + b)
    if a.show:
        print("\n" + json.dumps(ev, ensure_ascii=False, indent=1)[:2000])
    print("\nJSON -> %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
