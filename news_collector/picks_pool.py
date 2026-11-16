# -*- coding: utf-8 -*-
"""策略中台每日出票 → 采集池（OSINT1 马良 · 只读读取，不做研判）

职责（老田 2026-11-16）：
  「读取更新策略中台今天选股的数据」——本脚本是这句话的可执行形态：
  把 `portal/data/picks_history/todays_<YYYYMMDD>.json` 读出来，归一成
  6 位代码清单，带上 tier / n_hit / 策略来源，供 gap_scan.py 逐票逐源体检。

为什么单独成脚本而不是每次现写：
  1) 出票文件的行尾/编码/字段形状每次都要重新确认，写成代码才不靠记忆；
  2) **「今天」必须校验**——中台 18:20 出票，08:00 去读「今天」读到的是空；
     不校验就会把周五的 649 只当成今日清单用（11-16 判例）；
  3) 清单是「采集对象」，必须能被别的脚本稳定消费，而不是人肉粘贴。

口径纪律：
  - 只读 `picks_history/`，不写、不删、不改中台任何产物；
  - 输出代码为 6 位数字、LF 行尾（CRLF 会被下游 shell 处理成尾随 \\r，
    使 649 只票全部判成「不适用该源」——11-16 判例，见 gap_scan.norm_code）；
  - **出票 ≠ 事实**：清单只说明「策略侧命中」，不代表已采集、更不代表研判结论。
    是否纳入每日采集队列是 L2（马超配准），本脚本不改队列。
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PICK_DIR = os.path.join(ROOT, "portal", "data", "picks_history")


def norm6(x):
    v = str(x).split(".")[0]
    v = "".join(ch for ch in v if not ch.isspace())
    if not v:
        return ""
    v = v.zfill(6)
    return v if (len(v) == 6 and v.isdigit()) else ""


def list_picks(dates=None):
    """返回 {YYYYMMDD: path}，只认 todays_*.json（qpzm_* 是另一口径，不混入）。"""
    out = {}
    for p in glob.glob(os.path.join(PICK_DIR, "todays_*.json")):
        d = os.path.basename(p)[len("todays_"):-len(".json")]
        if len(d) == 8 and d.isdigit():
            out[d] = p
    if dates:
        return {d: out[d] for d in dates if d in out}
    return out


def load_picks(date=None):
    """读指定日期（默认最新可得）的出票；返回 (report, rows)。"""
    avail = list_picks()
    if not avail:
        raise ValueError("picks_history 下没有 todays_*.json：%s" % PICK_DIR)

    latest = max(avail)
    target = date or latest
    if target not in avail:
        raise ValueError("没有 %s 的出票文件（可得：%s）" % (target, ",".join(sorted(avail))))

    with open(avail[target], encoding="utf-8") as f:
        raw = json.load(f)
    st = raw.get("stocks") or []
    rows = st if isinstance(st, list) else list(st.values())

    recs = []
    for o in rows:
        c = norm6(o.get("code", ""))
        if not c:
            continue
        srcs = o.get("sources") or []
        recs.append({
            "code": c,
            "name": o.get("name") or "",
            "industry": o.get("industry") or "",
            "tier": o.get("tier") or "",
            "n_hit": o.get("n_hit") if o.get("n_hit") is not None else len(srcs),
            "strategies": sorted({str(s.get("s")) for s in srcs if isinstance(s, dict) and s.get("s")}),
            "tags": sorted({str(s.get("tag")) for s in srcs if isinstance(s, dict) and s.get("tag")}),
            "env_diff": bool(o.get("env_diff")),
            "panic_buy": bool(o.get("panic_buy")),
        })

    tiers = {}
    for r in recs:
        tiers[r["tier"] or "-"] = tiers.get(r["tier"] or "-", 0) + 1

    rep = {
        "file": avail[target],
        "date": target,
        "latest_available": latest,
        "generated_at": raw.get("generated_at"),
        "trade_basis": raw.get("trade_basis"),
        "counts": raw.get("counts") or {},
        "n_rows": len(rows),
        "n_codes": len({r["code"] for r in recs}),
        "tiers": tiers,
        "n_hit_ge2": sum(1 for r in recs if (r["n_hit"] or 0) >= 2),
    }
    return rep, recs


def staleness(rep, today=None):
    """「今天」的校验：出票文件的日期是否是当前交易日对应的最新可得批次。

    周末/节假日没有出票属正常；工作日若 latest < today，说明当日尚未出票
    （中台 18:20 才出），此时清单是**上一交易日的**，必须在输出里写明。
    """
    today = today or datetime.now().strftime("%Y%m%d")
    rep["today"] = today
    rep["is_today"] = (rep["date"] == today)
    if rep["is_today"]:
        rep["note"] = "出票日期=今日"
    else:
        rep["note"] = ("出票日期 %s ≠ 今日 %s：今日批次尚未生成（中台约 18:20 出票），"
                       "当前清单为最近一次交易日批次" % (rep["date"], today))
    return rep


def write_codes(recs, path):
    codes = sorted({r["code"] for r in recs})
    with open(path, "w", newline="") as f:   # newline="" 保 LF，防 CR 污染下游
        f.write("\n".join(codes) + "\n")
    return codes


def main(argv=None):
    ap = argparse.ArgumentParser(description="策略中台每日出票 → 采集池清单（只读）")
    ap.add_argument("--date", default=None, help="YYYYMMDD；缺省取最新可得批次")
    ap.add_argument("--out", default=None, help="写出 6 位代码清单（LF 行尾）")
    ap.add_argument("--min-hit", type=int, default=None, help="只输出 n_hit>=N 的票")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--list", action="store_true", help="只列可得批次")
    a = ap.parse_args(argv)

    if a.list:
        for d in sorted(list_picks(), reverse=True)[:15]:
            print(d)
        return 0

    try:
        rep, recs = load_picks(a.date)
    except ValueError as e:
        print("[FATAL] %s" % e, file=sys.stderr)
        return 2
    rep = staleness(rep)

    if a.min_hit is not None:
        recs = [r for r in recs if (r["n_hit"] or 0) >= a.min_hit]
        rep["n_filtered"] = len(recs)

    if a.out:
        codes = write_codes(recs, a.out)
        rep["out"] = a.out
        rep["out_n"] = len(codes)

    if a.json:
        print(json.dumps({"report": rep, "items": recs}, ensure_ascii=False, indent=1))
        return 0

    print("出票文件  %s" % rep["file"])
    print("出票日期  %s（%s）" % (rep["date"], rep["note"]))
    print("交易基准  %s" % rep["trade_basis"])
    print("生成时刻  %s" % rep["generated_at"])
    print("票数      %d（去重 %d）｜T2 %d｜T3 %d｜n_hit>=2 %d"
          % (rep["n_rows"], rep["n_codes"], rep["tiers"].get("T2", 0),
             rep["tiers"].get("T3", 0), rep["n_hit_ge2"]))
    if a.out:
        print("已写出    %s（%d 只，LF）" % (rep["out"], rep["out_n"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
