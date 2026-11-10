# -*- coding: utf-8 -*-
"""各策略线出票统一汇总（OSINT1 马良 · 只读盘点，不产生研判）。

扫描全部已知策略出票源，产出一张总表 news_collector/strategy_picks_summary.json：
  1) k_stock 策略中台（paper.db：持仓按 strategy 账本 + 近 N 日有效 BUY 订单）
  2) 侯风 HOUFENG（最新 houfeng_pick_*.json 选股 30 + 候选池 164 冻结快照）
  3) 两池（portal/daily_report/cohorts/cohort_*_{main,auto}.json 最新 asof 批次）
  4) 缠论队列（target_queue_chanlun.json：all/actionable 规模）
  5) 选股日更（portal/static/selection_daily.json：picks + S02 熔断标志）
  6) 策略库 S01-S12 状态（agent_platform/catalogs/strategy_registry.json）
并对每线计算 OSINT 覆盖状态（irm/sse/taoguba/xq 有数据文件数 + 在 07:00 xq 队列数），
给出"完全无覆盖"缺口清单 —— 仅数据/数字/判据，是否接入采集归上级裁定。

用法：python strategy_picks_summary.py [orders_days]
  orders_days = k_stock 近 N 日订单窗口，默认 14
"""
import os
import sys
import json
import glob
import time
import sqlite3

HERE = r"C:/aios/news_collector"
REPO = os.path.dirname(HERE)
OUT = os.path.join(HERE, "strategy_picks_summary.json")
DATA = r"C:/aios/data"
XQ_DATA = os.path.join(HERE, "data", "xq_timeline")


def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def coverage(codes6):
    """对 6 位代码集合计算各源覆盖。"""
    c = set(codes6)
    cov = {}
    for src, d in (("irm", os.path.join(DATA, "irm")),
                   ("sse", os.path.join(DATA, "sse")),
                   ("taoguba", os.path.join(DATA, "taoguba")),
                   ("xq_data", XQ_DATA)):
        cov[src] = sorted({x for x in c if os.path.exists(os.path.join(d, x + ".jsonl"))})
    try:
        import xq_stock_timeline as xqmod
        q = set(xqmod.load_queue("all"))
        cov["xq_queue"] = sorted(c & q)
    except Exception:
        cov["xq_queue"] = []
    uncov = c - set(cov["irm"]) - set(cov["sse"]) - set(cov["taoguba"]) - set(cov["xq_data"]) - set(cov["xq_queue"])
    cov["uncovered"] = sorted(uncov)
    cov["n"] = len(c)
    cov["n_uncovered"] = len(uncov)
    return cov


def kstock_line(days):
    db = os.path.join(REPO, "k_stock", "data", "paper.db")
    if not os.path.exists(db):
        return None
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    cutoff = time.strftime("%Y%m%d", time.localtime(time.time() - days * 86400))
    pos = {}
    for r in con.execute("SELECT strategy, stock FROM positions"):
        pos.setdefault(r["strategy"], set()).add(r["stock"])
    orders = {}
    for r in con.execute(
            "SELECT strategy, stock, side, status, plan_date FROM orders "
            "WHERE side='BUY' AND status IN ('pending','filled') AND plan_date>=?",
            (cutoff,)):
        orders.setdefault(r["strategy"], set()).add((r["stock"], r["plan_date"], r["status"]))
    con.close()
    holdings = sorted({c.rsplit(".", 1)[0] for s in pos.values() for c in s
                       if not c.startswith("gb_")})
    recent = sorted({c.rsplit(".", 1)[0] for c, _, _ in {x for s in orders.values() for x in s}
                     if not c.startswith("gb_")})
    return {
        "source": "k_stock/data/paper.db",
        "holdings_by_strategy": {k: sorted(v) for k, v in sorted(pos.items())},
        "n_holdings_rows": sum(len(v) for v in pos.values()),
        "recent_buy_window_days": days,
        "recent_buy_cutoff": cutoff,
        "recent_buy_codes": recent,
        "recent_buy_detail": sorted({x for s in orders.values() for x in s}),
        "coverage": coverage(holdings + recent),
    }


def houfeng_line():
    picks = sorted(glob.glob(os.path.join(REPO, "portal", "houfeng", "houfeng_pick_*.json")))
    if not picks:
        return None
    best, best_key = None, None
    for f in picks:
        try:
            d = json.load(open(f, encoding="utf-8"))
            sd = str(d.get("meta", {}).get("signal_date", ""))
        except Exception:
            sd = ""
        key = (sd, os.path.getmtime(f))
        if best is None or key > best_key:
            best, best_key = f, key
    d = json.load(open(best, encoding="utf-8"))
    pk = (d.get("pool_off") or {}).get("picks") or []
    codes = sorted({p["code"] for p in pk if p.get("code")})
    line = {
        "source": os.path.relpath(best, REPO).replace("\\", "/"),
        "signal_date": best_key[0],
        "n_picks": len(codes),
        "pick_codes": codes,
        "gate_safe": bool(d.get("gate_safe", False)),
    }
    cand_f = os.path.join(HERE, "houfeng_candidates_164.json")
    if os.path.exists(cand_f):
        cd = json.load(open(cand_f, encoding="utf-8"))
        cc = sorted({r["code"] for r in cd.get("candidates", []) if r.get("code")})
        line["candidate_pool_file"] = os.path.basename(cand_f)
        line["candidate_pool_signal_date"] = cd.get("signal_date")
        line["n_candidates"] = len(cc)
        line["candidate_codes"] = cc
        line["candidate_coverage"] = coverage([c.split(".")[0] for c in cc])
    line["coverage"] = coverage([c.split(".")[0] for c in codes])
    return line


def cohorts_line():
    files = sorted(glob.glob(os.path.join(REPO, "portal", "daily_report", "cohorts",
                                          "cohort_*_main.json")))
    if not files:
        return None
    asofs = {}
    for f in files:
        try:
            asofs[str(json.load(open(f, encoding="utf-8")).get("asof", ""))] = f
        except Exception:
            pass
    latest_asof = max(asofs)
    out = {"source_dir": "portal/daily_report/cohorts/", "latest_asof": latest_asof,
           "pools": {}}
    all_codes = set()
    for tag in ("main", "auto"):
        f = asofs[latest_asof].replace("_main.json", "_%s.json" % tag)
        if not os.path.exists(f):
            continue
        d = json.load(open(f, encoding="utf-8"))
        codes = d.get("codes") or []
        out["pools"][tag] = {
            "file": os.path.basename(f),
            "pool_label": d.get("pool_label"),
            "asof": d.get("asof"),
            "entry_date": d.get("entry_date"),
            "n": d.get("n"),
            "codes": codes,
        }
        all_codes |= {c.split(".")[0] for c in codes}
    out["n_union"] = len(all_codes)
    out["coverage"] = coverage(all_codes)
    return out


def chanlun_line():
    f = os.path.join(HERE, "target_queue_chanlun.json")
    if not os.path.exists(f):
        return None
    d = json.load(open(f, encoding="utf-8"))
    alli = d.get("all") or []
    act = d.get("actionable") or []
    codes = {i["code"].split(".")[0] for i in alli if isinstance(i, dict) and i.get("code")}
    return {
        "source": "news_collector/target_queue_chanlun.json",
        "file_mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                    time.localtime(os.path.getmtime(f))),
        "n_all": len(alli),
        "n_actionable": len(act),
        "coverage": coverage(codes),
    }


def selection_line():
    f = os.path.join(REPO, "portal", "static", "selection_daily.json")
    if not os.path.exists(f):
        return None
    d = json.load(open(f, encoding="utf-8"))
    picks = d.get("picks") or []
    codes = []
    for p in picks:
        c = p.get("code") or p.get("ts_code") or (p.get("stock") if isinstance(p, dict) else None)
        if c:
            codes.append(c)
    return {
        "source": "portal/static/selection_daily.json",
        "date": d.get("date"),
        "generated_at": d.get("generated_at"),
        "n_picks": len(picks),
        "s02_paused": d.get("s02_paused"),
        "pick_codes": codes,
        "coverage": coverage([c.split(".")[0] for c in codes]) if codes else None,
    }


def registry_line():
    f = os.path.join(REPO, "agent_platform", "catalogs", "strategy_registry.json")
    if not os.path.exists(f):
        return None
    d = json.load(open(f, encoding="utf-8"))
    s = d.get("strategies") or d
    rows = []
    if isinstance(s, dict):
        it = s.items()
    else:
        it = [(v.get("id", "?"), v) for v in s]
    for k, v in it:
        if isinstance(v, dict):
            rows.append({"id": k, "status": v.get("status", "?"),
                         "name": v.get("name", "")})
    return {"source": "agent_platform/catalogs/strategy_registry.json",
            "n": len(rows), "strategies": rows}


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    log("各策略出票统一汇总 start (orders 窗口=%d 天)" % days)
    out = {"schema": "osint1.strategy_picks_summary/v1",
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "note": "只读盘点各策略出票与 OSINT 覆盖缺口；不产生研判；接入采集范围归上级裁定",
           "lines": {}}
    for name, fn in (("k_stock", lambda: kstock_line(days)),
                     ("houfeng", houfeng_line),
                     ("cohorts_two_pools", cohorts_line),
                     ("chanlun", chanlun_line),
                     ("selection_daily", selection_line),
                     ("strategy_registry", registry_line)):
        try:
            out["lines"][name] = fn()
        except Exception as e:  # noqa: BLE001
            out["lines"][name] = {"error": str(e)}
            log("!! %s 汇总失败: %s" % (name, e))
    # 总缺口（两池为当前最大未覆盖线）
    cov = out["lines"].get("cohorts_two_pools") or {}
    if cov.get("coverage"):
        log("两池 %d 只中无覆盖 %d 只" % (cov["coverage"]["n"], cov["coverage"]["n_uncovered"]))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log("已写出 %s" % OUT)
    # 控制台总表
    print("\n===== 各策略出票总表 =====")
    for name, ln in out["lines"].items():
        if name == "strategy_registry":
            print("%-18s S01-S12 状态数=%d" % (name, ln.get("n", 0)))
            continue
        if not ln or ln.get("error"):
            print("%-18s ERROR %s" % (name, ln.get("error") if ln else "none"))
            continue
        c = ln.get("coverage") or {}
        print("%-18s n=%s 覆盖: irm=%s sse=%s tgb=%s xq数据=%s xq队列=%s 无覆盖=%s"
              % (name, c.get("n", "-"), len(c.get("irm", [])), len(c.get("sse", [])),
                 len(c.get("taoguba", [])), len(c.get("xq_data", [])),
                 len(c.get("xq_queue", [])), c.get("n_uncovered", "-")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
