# -*- coding: utf-8 -*-
"""聚合全部选股策略今日强制扫描结果 → portal/data/todays_picks.json

来源（各策略最新扫描产物，数据基准日见各 strategy meta）：
  1. strong  : quant_backtest/reports/strong_latest.json   tag 板块领涨/逆板块强势
  2. weak    : reports/weak_scan_<ymd>.csv                 强失速/深度垫底（周扫；
               数据基准落后行情湖超 MAX_LAG_DAYS 天时旧名单不入清单）
  3. chanlun : quant_backtest/data/chanlun_pool.jsonl      最新 detect_date 批次
  4. qpzm    : quant_backtest/reports/rps_latest.json      res 列表（score）
  5. ninedim : quant_backtest/data/nine_dim_scores.json    rows（score/full/band）
  6. cross   : import cross_poll.build_cross               多策略共振 n_hit>=2
  7. linkage : 个股级工具，无全市场扫描口径（记录说明，不产生名单）

分层：T1=看多源共振(strong领涨+chanlun买+qpzm 计数>=2，或 cross)；T2=单策略高价值
(强领涨/缠论买点/qpzm分>=80)；CROWD=九维高分拥挤警报(K33 语义：
回避位非买入，不参与 T2 抬层)；T3=其余。weak/ninedim 影子/卖点不参与共振计数。
用法: python portal/build_todays_picks.py [--details N]
"""
import csv
import glob
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
MAX_LAG_DAYS = 10  # weak 周扫源允许的最大滞后自然日（覆盖长假+周一节奏）
QB = os.path.join(ROOT, "quant_backtest")
OUT = os.path.join(HERE, "data", "todays_picks.json")


def _std_code(code):
    """统一代码为 603268.SH / 301520.SZ / 920028.BJ 风格。"""
    c = (code or "").strip().lower()
    if "." in c:
        s, suf = c.split(".", 1)
        return "%s.%s" % (s.upper(), suf.upper())
    if c.startswith("sh"):
        return c[2:].upper() + ".SH"
    if c.startswith("sz"):
        return c[2:].upper() + ".SZ"
    if c.startswith("bj"):
        return c[2:].upper() + ".BJ"
    return c.upper()


def load_strong(stocks):
    fp = os.path.join(QB, "reports", "strong_latest.json")
    d = json.load(open(fp, encoding="utf-8"))
    rows = d.get("list") or []
    for r in rows:
        c = _std_code(r.get("code"))
        e = stocks.setdefault(c, {"code": c, "name": r.get("name"),
                                  "industry": None, "close": None,
                                  "pct_chg": None, "sources": []})
        e["name"] = e["name"] or r.get("name")
        e["sources"].append({
            "s": "strong", "tag": r.get("tag"),
            "block": r.get("block"), "ex250": r.get("ex250"),
            "reasons": (r.get("reasons") or [])[:2]})
    n = len(rows)
    lead = len(d.get("lead") or [])
    meta = {"label": "强势板块领涨（599板块扫描）", "n": n, "lead": lead,
            "observe": n - lead, "file": "quant_backtest/reports/strong_latest.json",
            "ts": d.get("ts"), "note": "lead=板块领涨 observe=逆板块强势"}
    return meta


def load_weak(stocks, basis_day=None):
    rep = os.path.join(ROOT, "reports")
    cands = [f for f in os.listdir(rep)
             if f.startswith("weak_scan_") and f.endswith(".csv")]
    if not cands:
        return {"label": "弱势失速扫描", "n": 0, "note": "无输出"}
    cands.sort()
    fp = os.path.join(rep, cands[-1])
    # 过期闸门：WeakScan 为周扫（周一 15:35），数据基准落后行情湖超
    # MAX_LAG_DAYS 自然日（覆盖长假+周一节奏）视为过期，旧名单不入清单
    m = re.match(r"weak_scan_(\d{8})\.csv$", cands[-1])
    if m and basis_day:
        lag = (datetime.strptime(basis_day, "%Y%m%d")
               - datetime.strptime(m.group(1), "%Y%m%d")).days
        if lag > MAX_LAG_DAYS:
            return {"label": "弱势失速扫描（超额回撤监控）", "n": 0,
                    "stale": True, "file": "reports/" + cands[-1],
                    "note": "数据基准 %s 落后行情湖 %s %d 天(>%d)，旧名单不入清单；"
                            "待 WeakScan 周一 15:35 刷新"
                            % (m.group(1), basis_day, lag, MAX_LAG_DAYS)}
    rows = list(csv.DictReader(open(fp, encoding="utf-8-sig")))
    # 只取 focus 标注的重点池（深度垫底/强失速等），CSV 全量行不进清单
    rows = [r for r in rows if (r.get("focus") or "").strip()]
    for r in rows:
        c = _std_code(r.get("code"))
        e = stocks.setdefault(c, {"code": c, "name": r.get("name"),
                                  "industry": r.get("industry"), "close": None,
                                  "pct_chg": None, "sources": []})
        e["industry"] = e["industry"] or r.get("industry")
        e["sources"].append({
            "s": "weak", "tag": r.get("focus"),
            "block": r.get("block_name"), "ex": r.get("ex"),
            "rank_in": r.get("rank_in")})
    return {"label": "弱势失速扫描（超额回撤监控）", "n": len(rows),
            "file": "reports/" + os.path.basename(fp),
            "note": "focus=深度垫底/强失速等弱势标签（反向参考）"}


def load_chanlun(stocks):
    fp = os.path.join(QB, "data", "chanlun_pool.jsonl")
    rows = [json.loads(l) for l in open(fp, encoding="utf-8") if l.strip()]
    latest = max((r.get("detect_date") or "") for r in rows)
    batch = [r for r in rows if r.get("detect_date") == latest]
    for r in batch:
        c = _std_code(r.get("code"))
        e = stocks.setdefault(c, {"code": c, "name": r.get("name"),
                                  "industry": None, "close": r.get("price"),
                                  "pct_chg": None, "sources": []})
        e["name"] = e["name"] or r.get("name")
        e["close"] = e["close"] or r.get("price")
        e["sources"].append({
            "s": "chanlun", "tag": r.get("type"),
            "dt": r.get("dt"), "trend": r.get("trend"),
            "tbe": r.get("tbe"), "entry_px": r.get("entry_px")})
    return {"label": "缠论信号池（三类买卖点）", "n": len(batch),
            "batch_date": latest, "file": "quant_backtest/data/chanlun_pool.jsonl",
            "note": "取最新扫描批次；type=1买/2买/3买/1卖/2卖/3卖"}


def load_qpzm(stocks):
    fp = os.path.join(QB, "reports", "rps_latest.json")
    d = json.load(open(fp, encoding="utf-8"))
    rows = d.get("res") or []
    for r in rows:
        c = _std_code(r.get("code"))
        e = stocks.setdefault(c, {"code": c, "name": r.get("name"),
                                  "industry": None, "close": r.get("px"),
                                  "pct_chg": None, "sources": []})
        e["name"] = e["name"] or r.get("name")
        e["close"] = e["close"] or r.get("px")
        e["sources"].append({
            "s": "qpzm", "tag": (r.get("action") or "").split("（")[0] or "观察池",
            "action": r.get("action"), "score": r.get("score"),
            "rps": r.get("rps"), "tech": r.get("tech"), "env": r.get("env"),
            "added": r.get("added")})
    return {"label": "青萍之末（RPS+技术+环境闸门）", "n": len(rows),
            "trade_date": d.get("latest"), "file": "quant_backtest/reports/rps_latest.json",
            "ts": d.get("ts"), "note": "action 含 回避（环境闸门）标记"}


def load_ninedim(stocks):
    fp = os.path.join(QB, "data", "nine_dim_scores.json")
    d = json.load(open(fp, encoding="utf-8"))
    rows = d.get("rows") or []
    n_cand = 0
    for r in rows:
        n_cand += 1
        c = _std_code(r.get("code"))
        scored = r.get("score") is not None
        e = stocks.setdefault(c, {"code": c, "name": r.get("name"),
                                  "industry": r.get("industry"),
                                  "close": r.get("close"),
                                  "pct_chg": r.get("pct_chg"), "sources": []})
        e["industry"] = e["industry"] or r.get("industry")
        e["close"] = e["close"] if e["close"] is not None else r.get("close")
        e["sources"].append({
            "s": "ninedim",
            "tag": ("九维出分" if scored else
                    ("九维候选·满权重" if r.get("full") else "九维候选(影子未出分)")),
            "score": r.get("score"), "cov": r.get("cov"),
            "band": r.get("band"), "rps50": r.get("rps50")})
    return {"label": "九维评分（多维因子合成）", "n": n_cand,
            "trade_date": d.get("trade_date"),
            "file": "quant_backtest/data/nine_dim_scores.json",
            "ts": d.get("generated_at"),
            "note": "候选池口径（RPS50>80+额>2亿+动能）；K33：高分=拥挤警报非买入；"
                    "cov<0.75 不出分，本批全部影子未出分"}


def load_cross(stocks):
    sys.path.insert(0, QB)
    import cross_poll
    d = cross_poll.build_cross(force=True)
    n = 0
    for s in d.get("stocks") or []:
        if (s.get("n_hit") or 0) < 2:
            continue
        n += 1
        c = _std_code(s.get("code"))
        e = stocks.setdefault(c, {"code": c, "name": s.get("name"),
                                  "industry": None, "close": None,
                                  "pct_chg": None, "sources": []})
        e["name"] = e["name"] or s.get("name")
        hits = ["%s|%s" % (h.get("s"), h.get("label"))
                for h in (s.get("hits") or [])]
        e["sources"].append({
            "s": "cross", "tag": "多策略共振",
            "state": s.get("state"), "hits": hits})
    return {"label": "交叉共振（多策略同命中）", "n": n,
            "file": "quant_backtest/cross_poll.py(内存聚合)",
            "note": "n_hit>=2 才入选；state=mixed 表示多空信号并存"}


def tier_of(e):
    # K33 复盘（2026-10-08 产品评审 P0）：九维高分=拥挤警报而非买入信号，
    # band 票不再抬进 T2；alert 级入独立 CROWD 警报层（回避标记），其余落 T3。
    # 2026-10-10 复审修正：T1 共振只数看多源（strong 板块领涨 / chanlun 买点 /
    # qpzm），weak(反向回避)、ninedim 影子(未出分无观点)、chanlun 卖点不再
    # 参与凑对——此前 60% 的 T1 靠非看多源凑成；cross(n_hit>=2 派生)仍直接 T1。
    srcs = e["sources"]
    ss = {s["s"] for s in srcs}
    bull = (("qpzm" in ss)
            + any(s["s"] == "chanlun" and "买" in (s.get("tag") or "")
                  for s in srcs)
            + any(s["s"] == "strong" and s.get("tag") == "板块领涨"
                  for s in srcs))
    if bull >= 2 or "cross" in ss:
        return "T1"
    if "chanlun" in ss and any(s["s"] == "chanlun"
                               and "买" in (s.get("tag") or "")
                               for s in srcs):
        return "T2"
    if "strong" in ss and any(s["s"] == "strong"
                              and s.get("tag") == "板块领涨"
                              for s in srcs):
        return "T2"
    if "qpzm" in ss and any(s["s"] == "qpzm" and (s.get("score") or 0) >= 80
                            for s in e["sources"]):
        return "T2"
    if "ninedim" in ss and any(s["s"] == "ninedim"
                               and s.get("band") in ("alert", "alert2")
                               for s in e["sources"]):
        return "CROWD"
    return "T3"


def _latest_history(hist_dir, exclude_day=None):
    """归档目录里最近一份（排除今日自身），供轮动 diff。"""
    best = None
    for fp in glob.glob(os.path.join(hist_dir, "todays_*.json")):
        if exclude_day and exclude_day in fp:
            continue
        if best is None or os.path.getmtime(fp) > os.path.getmtime(best):
            best = fp
    return best


def _backfill_quotes(stocks):
    """A股 close/pct_chg 回填：从 tdx_read 行情湖最新日一次取全市场。

    六策略源里仅 weak 带 pct_chg，其余 null → 列表页涨跌幅大面积「—」。
    湖不可达/无 pandas 时整体降级（保持 null，页面已有占位显示），不拖垮聚合。
    返回 (基准日YYYYMMDD, 回填数)。
    """
    try:
        sys.path.insert(0, QB)
        from tdx_read import read_day, latest_date  # noqa: E402
        day = latest_date("ts_daily")
        if not day:
            return None, 0
        df = read_day("ts_daily", day, columns=["ts_code", "close", "pct_chg"])
        q = {r.ts_code: (float(r.close), float(r.pct_chg))
             for r in df.itertuples() if r.ts_code}
        n = 0
        for c, e in stocks.items():
            hit = q.get(c)
            if hit and e.get("close") is None:
                e["close"], e["pct_chg"] = hit[0], hit[1]
                n += 1
            elif hit and e.get("pct_chg") is None:
                e["pct_chg"] = hit[1]
                n += 1
        return day, n
    except Exception as ex:  # noqa: BLE001 — 回填失败降级不拖垮
        print("[backfill] 行情回填降级：%s" % str(ex)[:80])
        return None, 0


def main():
    stocks = {}
    metas = {}
    # 行情湖基准日提前取一次（weak 过期闸门用；backfill 内部会再取，轻查询幂等）
    try:
        sys.path.insert(0, QB)
        from tdx_read import latest_date
        basis0 = latest_date("ts_daily")
    except Exception:
        basis0 = None
    for sid, fn in (("strong", load_strong), ("weak", load_weak),
                    ("chanlun", load_chanlun), ("qpzm", load_qpzm),
                    ("ninedim", load_ninedim), ("cross", load_cross)):
        try:
            metas[sid] = fn(stocks, basis0) if sid == "weak" else fn(stocks)
            metas[sid]["ok"] = True
        except Exception as ex:  # noqa: BLE001 — 单源失败不拖垮聚合
            metas[sid] = {"label": sid, "ok": False, "err": str(ex)[:120]}
    metas["linkage"] = {
        "label": "板块联动信号", "ok": True, "n": None,
        "note": "个股级工具（linkage_signals.py 需指定代码），无全市场扫描口径，今日不产生名单"}

    out_rows = []
    for c, e in stocks.items():
        e["n_hit"] = len({s["s"] for s in e["sources"]} - {"cross"})
        e["tier"] = tier_of(e)
        out_rows.append(e)
    order = {"T1": 0, "T2": 1, "CROWD": 2, "T3": 3}
    out_rows.sort(key=lambda x: (order[x["tier"]], -x["n_hit"], x["code"]))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    basis_day, n_backfill = _backfill_quotes(stocks)
    # 基准日动态推导：行情湖最新日（硬编码 2026-10-07 时代已废——每天跑也是旧文案）
    if basis_day:
        basis_s = "%s-%s-%s" % (basis_day[:4], basis_day[4:6], basis_day[6:])
        trade_basis = "%s 收盘（行情湖 ts_daily 最新可得交易日）" % basis_s
    else:
        basis_s = None
        trade_basis = "行情湖不可达，涨跌幅以策略源自带为准"
    # ── 池子轮动：与上一份归档 diff（新进/退出/留存），随归档积累成轮动视图 ──
    hist_dir = os.path.join(HERE, "data", "picks_history")
    os.makedirs(hist_dir, exist_ok=True)
    cur_codes = {r["code"] for r in out_rows}
    prev_path = _latest_history(hist_dir, exclude_day=basis_day)
    rotation = {"new_in": None, "dropped": None, "retained": None,
                "vs": os.path.basename(prev_path) if prev_path else None}
    if prev_path:
        try:
            prev = json.load(open(prev_path, encoding="utf-8"))
            prev_codes = {r["code"] for r in prev.get("stocks") or []}
            rotation["new_in"] = len(cur_codes - prev_codes)
            rotation["dropped"] = len(prev_codes - cur_codes)
            rotation["retained"] = len(cur_codes & prev_codes)
        except Exception as ex:  # noqa: BLE001
            print("[rotation] 上份归档不可读：%s" % str(ex)[:60])
    doc = {
        "generated_at": now,
        "scan_run_date": basis_s or now[:10],
        "trade_basis": trade_basis,
        "strategies": metas,
        "counts": {
            "total": len(out_rows),
            "T1_resonance": sum(1 for r in out_rows if r["tier"] == "T1"),
            "T2_high_value": sum(1 for r in out_rows if r["tier"] == "T2"),
            "T3_single": sum(1 for r in out_rows if r["tier"] == "T3"),
            "crowd_alert": sum(1 for r in out_rows if r["tier"] == "CROWD"),
            "rotation": rotation,
        },
        "stocks": out_rows,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    # 每日归档（轮动视图与后验计算的数据地基）
    if basis_day:
        arch = os.path.join(hist_dir, "todays_%s.json" % basis_day)
        with open(arch, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
    print("  轮动(vs %s)：新进 %s / 退出 %s / 留存 %s；归档 %s"
          % (rotation["vs"] or "无", rotation["new_in"], rotation["dropped"],
             rotation["retained"],
             "todays_%s.json" % basis_day if basis_day else "跳过(无基准日)"))
    print("todays_picks: total=%d T1=%d T2=%d CROWD=%d T3=%d -> %s"
          % (doc["counts"]["total"], doc["counts"]["T1_resonance"],
             doc["counts"]["T2_high_value"], doc["counts"]["crowd_alert"],
             doc["counts"]["T3_single"], OUT))
    print("  行情回填：基准 %s，回填 %d 只" % (basis_day or "无", n_backfill))
    for sid, m in metas.items():
        print("  %-8s ok=%s n=%s %s" % (sid, m.get("ok"), m.get("n"),
                                        (m.get("err") or "")[:60]))


if __name__ == "__main__":
    # argparse 化：此前无参解析，--help 会直接触发真实聚合写盘（副作用 bug）
    import argparse
    _ap = argparse.ArgumentParser(
        description="聚合六策略选股产物 → portal/data/todays_picks.json"
                    "（个股详情见 build_pick_details.py）")
    _ap.parse_args()
    main()
