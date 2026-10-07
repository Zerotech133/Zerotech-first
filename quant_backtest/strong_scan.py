# -*- coding: utf-8 -*-
"""强于板块领涨识别（strong 产线 · 阶段2a：全板块池扫描循环）。

镜像 weak 产线的全池扫描：遍历行业板块池 → block_quorum 门槛 →
板块内成员逐股评估（250 日超额 + 日线趋势）→ strong_verdict →
名单落盘。板块领涨为强信号名单；逆板块强势为观察名单（单独归组，
不推送，人工复核）。

口径（与 strong_track.py / linkage.resolve_block 同源）：
  · 板块池: tdx_sector_def 的 industry881/industry_l1/old_industry
    （概念/风格板块无个股行业归属，不进池）
  · 成员: ts_stock_basic.industry 归组（linkage.block_members，
    每股唯一归属，与单股 resolve_block 同口径）
  · 单板块逐股评估上限 min(成员数, --limit) 只（按清单顺序取前 N）：
    K 线取数为主要代价，全量成员扫描留待阶段2b 评估增量后放开
  · 趋势判定直接用 series 归一后的个股 pct 序列：pct = close×k−c
    （k、c 为常数）是单调仿射变换，last>ma20>ma60 序关系不变，
    与原始 close 判定等价——每股省一次 load_kline 取数

熔断：当日已扫描（reports/strong_latest.json date = 今天）直接跳过，
--force 强制重扫（名单文件当日覆盖写，幂等）。

用法:
  python strong_scan.py                    # 全板块池扫描（当日熔断）
  python strong_scan.py --block 881019     # 单板块调试
  python strong_scan.py --dry              # 只打印不落盘
  python strong_scan.py --force --limit 5  # 全池小样本强制重扫
"""
import argparse
import datetime as _dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from strong_track import (MIN_BLOCK_MEMBERS, STRONG_EX_TH,  # noqa: E402
                          block_quorum, strong_verdict, trend_bull)

POOL_SEGS = ("industry881", "industry_l1", "old_industry")
PER_BLOCK_CAP = 20          # 单板块逐股评估默认上限（控制取数代价）
REPORTS = os.path.join(HERE, "reports")


def now_iso():
    return _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def today_str():
    return _dt.date.today().isoformat()


def block_pool():
    """行业板块池 [(code,name)]，按代码升序（进度行稳定可续跟）。"""
    import linkage
    defs = linkage.sector_defs()
    return [(bc, d["name"]) for bc, d in sorted(defs.items())
            if d["seg"] in POOL_SEGS]


def eval_stock(code, block_code):
    """单股 250 日超额 + 日线趋势 → (ex250, trend_bull, ok)。"""
    import linkage
    r = linkage.series(code, "1d", limit=250, block=block_code)
    if not r.get("ok"):
        return None, False, False
    pct = r.get("stock") or []
    ex = None
    if r.get("block") and pct and r["block"][-1] is not None \
            and pct[-1] is not None:
        ex = round(pct[-1] - r["block"][-1], 1)
    return ex, trend_bull(pct), True


def scan_block(bc, bname, limit, th_ex, min_members, n_eval_counter):
    """单板块扫描 → (hits, n_members, n_eval, skip_reason)。

    hits 已过 strong_verdict（tag=板块领涨/逆板块强势）；skip_reason
    非空表示板块被门槛拦下（成员不足/无成员数据），不计入扫描数。
    """
    import linkage
    members = linkage.block_members(bc)
    n = len(members)
    if not block_quorum(n, min_members):
        return [], n, 0, "成员 %s < %d（噪声板块跳过）" % (n, min_members)
    hits, n_eval = [], 0
    for m in members[:max(1, int(limit))]:
        code = m["code"]
        try:
            ex, bull, ok = eval_stock(code, bc)
        except Exception as e:
            ex, bull, ok = None, False, False
            sys.stderr.write("    ! %s 评估异常: %s\n" % (code, e))
        if not ok:
            continue  # K 线取数失败：跳过个股，不计评估数
        n_eval += 1
        n_eval_counter[0] += 1
        is_strong, tag, reasons = strong_verdict(ex, n, bull,
                                                 th_ex, min_members)
        if is_strong:
            hits.append({"ts": now_iso(), "code": code, "name": m["name"],
                         "block": bname, "block_code": bc, "ex250": ex,
                         "tag": tag, "reasons": reasons})
    return hits, n, n_eval, None


def main():
    ap = argparse.ArgumentParser(description="strong 阶段2a 全板块池扫描")
    ap.add_argument("--block", default=None,
                    help="只扫单板块（代码如 881019，调试用）")
    ap.add_argument("--limit", type=int, default=PER_BLOCK_CAP,
                    help="单板块逐股评估上限（默认 %d）" % PER_BLOCK_CAP)
    ap.add_argument("--th-ex", type=float, default=STRONG_EX_TH,
                    help="超额达标阈值 pp（默认 %g）" % STRONG_EX_TH)
    ap.add_argument("--min-members", type=int, default=MIN_BLOCK_MEMBERS)
    ap.add_argument("--dry", action="store_true", help="只打印不落盘")
    ap.add_argument("--force", action="store_true", help="忽略当日已扫熔断")
    a = ap.parse_args()

    latest_fp = os.path.join(REPORTS, "strong_latest.json")
    if not a.dry and not a.force and os.path.exists(latest_fp):
        try:
            with open(latest_fp, encoding="utf-8") as f:
                prev = json.load(f)
            if prev.get("date") == today_str():
                print("今日（%s）已扫描过，熔断跳过（--force 可强制重扫）"
                      % prev.get("date"))
                return 0
        except Exception:
            pass  # 汇总文件损坏视为未扫，继续

    import linkage
    pool = block_pool()
    if a.block:
        b = str(a.block).split(".")[0]
        d = linkage.sector_defs().get(b)
        if not d:
            print("板块 %s 不在 tdx_sector_def，退出" % b)
            return 2
        pool = [(b, d["name"])]

    all_hits, skipped = [], 0
    n_eval_total = [0]
    n_scanned = 0
    for i, (bc, bname) in enumerate(pool, 1):
        try:
            hits, n, n_eval, skip = scan_block(
                bc, bname, a.limit, a.th_ex, a.min_members, n_eval_total)
        except Exception as e:
            skipped += 1
            print("[%d/%d] %s %s 板块级异常跳过: %s"
                  % (i, len(pool), bc, bname, e), flush=True)
            continue
        if skip:
            skipped += 1
            print("[%d/%d] %s %s 成员=%s —— %s"
                  % (i, len(pool), bc, bname, n, skip), flush=True)
            continue
        n_scanned += 1
        lead = sum(1 for h in hits if h["tag"] == "板块领涨")
        print("[%d/%d] %s %s 成员=%d 评估=%d 命中=%d（领涨=%d 观察=%d）"
              % (i, len(pool), bc, bname, n, n_eval, len(hits), lead,
                 len(hits) - lead), flush=True)
        all_hits.extend(hits)

    lead_rows = [h for h in all_hits if h["tag"] == "板块领涨"]
    obs_rows = [h for h in all_hits if h["tag"] == "逆板块强势"]
    summary = {
        "date": today_str(), "ts": now_iso(),
        "blocks_total": len(pool), "blocks_scanned": n_scanned,
        "blocks_skipped": skipped, "stocks_evaluated": n_eval_total[0],
        "per_block_cap": int(a.limit),
        "counts": {"板块领涨": len(lead_rows), "逆板块强势": len(obs_rows)},
        "lead": [{"code": h["code"], "name": h["name"],
                  "block": h["block"], "ex250": h["ex250"]}
                 for h in lead_rows],
        "observe": [{"code": h["code"], "name": h["name"],
                     "block": h["block"], "ex250": h["ex250"]}
                    for h in obs_rows],
    }

    if a.dry:
        print("--- dry 模式，不落盘 ---")
        for h in all_hits:
            print(json.dumps(h, ensure_ascii=False))
    else:
        os.makedirs(REPORTS, exist_ok=True)
        list_fp = os.path.join(REPORTS, "strong_list_%s.jsonl"
                               % today_str().replace("-", ""))
        with open(list_fp, "w", encoding="utf-8") as f:
            for h in all_hits:
                f.write(json.dumps(h, ensure_ascii=False) + "\n")
        with open(latest_fp, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print("落盘 %s（%d 行）+ %s" % (list_fp, len(all_hits), latest_fp))
    print("汇总：板块 %d/%d（跳过 %d），个股评估 %d，"
          "板块领涨 %d，逆板块强势 %d"
          % (n_scanned, len(pool), skipped, n_eval_total[0],
             len(lead_rows), len(obs_rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
