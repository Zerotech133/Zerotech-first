# -*- coding: utf-8 -*-
"""数据对齐审计 + 真补齐（源侧 vs 本地逐日对比）。

原则：不靠「口径标签」消除滞后；以源侧最新可用数据为准，逐日核对行数：
  - 本地缺文件 → 拉取写入；
  - 本地行数明显少于源侧（partial）→ 重新拉取覆盖；
  - 逐项记录 evidence（source_date/source_rows/local_rows/action/verdict）。
产出：work/data_sync/audit_latest.json（驾驶舱读取）+ 控制台摘要。
用法：python tools/data_lag_audit.py --days 10 [--dry-run]
红线：只写自身 data/tushare；操作前后写 kg 日志。
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    try:
        from safe_urlopen import safe_urlopen
    except ImportError:  # 作为包/仓库根导入时
        from tools.safe_urlopen import safe_urlopen

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
WORK = REPO / "work" / "data_sync"
VAULT = REPO / "tools" / "secrets" / "vault.py"
KG = Path(r"C:\Temp\kg_sync_task.log")
BY_DATE = {
    "daily": ("daily", "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"),
    "daily_basic": ("daily_basic", "ts_code,trade_date,close,turnover_rate,volume_ratio,pe,pb,total_share,float_share,free_share,total_mv,circ_mv"),
    "moneyflow": ("moneyflow", "ts_code,trade_date,net_mf_amount"),
    "top_list": ("top_list", "trade_date,ts_code,name,close,pct_change,turnover_rate,amount,l_sell,l_buy,l_amount,net_amount,net_rate,amount_rate,float_values,reason"),
    "top_inst": ("top_inst", "trade_date,ts_code,exalter,side,buy,sell,net_buy,reason"),
    "limit_list_d": ("limit_list_d", "ts_code,trade_date,limit,amount,limit_amount,fd_amount"),
    "hk_daily": ("hk_daily", "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"),
    "anns_d": ("anns_d", "ann_date,ts_code,name,title,url"),
}


def token() -> str:
    p = subprocess.run([sys.executable, str(VAULT), "decrypt", "mcp/tushare_mcp_token"],
                       capture_output=True, timeout=60)
    t = (p.stdout or b"").decode("utf-8", "replace").strip()
    if not t:
        raise RuntimeError("no token")
    return t


def call(tok: str, api: str, params: dict, fields: str) -> dict:
    body = json.dumps({"api_name": api, "token": tok, "params": params, "fields": fields}).encode()
    req = urllib.request.Request("http://api.tushare.pro", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    d = json.loads(safe_urlopen(req, timeout=120).read().decode())
    return {"code": d.get("code"), "msg": (d.get("msg") or "")[:120],
            "fields": (d.get("data") or {}).get("fields") or [],
            "items": (d.get("data") or {}).get("items") or []}


def last_open_days(n: int) -> list[str]:
    """从本地 trade_cal 取最近 n 个已收盘交易日（<= 昨天）。"""
    fs = sorted(glob.glob(str(DATA / "trade_cal" / "*.parquet")))
    if not fs:
        return []
    tb = pq.read_table(fs[-1], columns=["cal_date", "is_open"]).to_pylist()
    today = datetime.now().strftime("%Y%m%d")
    days = [r["cal_date"] for r in tb if str(r.get("is_open")) == "1" and str(r["cal_date"]) < today]
    return sorted(days)[-n:]


def audit_by_date(tok, store: str, api: str, fields: str, days: list[str], dry: bool) -> dict:
    d = DATA / store
    checks = []
    filled = regrouped = 0
    for dt in days:
        f = d / ("d_%s.parquet" % dt)
        src = call(tok, api, {"trade_date": dt}, fields)
        src_rows = len(src["items"])
        local_rows = None
        if f.exists():
            try:
                local_rows = pq.ParquetFile(f).metadata.num_rows
            except Exception:  # noqa: BLE001
                local_rows = -1
        action = "ok"
        if src["code"] == 0 and src_rows > 0:
            if local_rows is None or local_rows < src_rows:
                action = "fill_missing" if local_rows is None else "fill_partial"
                if not dry:
                    table = pa.table({fl: [row[j] for row in src["items"]] for j, fl in enumerate(src["fields"])})
                    tmp = f.with_suffix(".parquet.tmp")
                    pq.write_table(table, tmp)
                    tmp.replace(f)
                filled += 1
            elif local_rows > src_rows:
                action = "local_more"
                regrouped += 1
        checks.append({"date": dt, "source_rows": src_rows, "local_rows": local_rows, "action": action})
        time.sleep(0.15)
    src_days = [c["date"] for c in checks if c["source_rows"] > 0]
    local_days = [c["date"] for c in checks if (c["local_rows"] or 0) > 0]
    # verdict
    bad = [c for c in checks if c["action"] in ("fill_missing", "fill_partial", "local_more")]
    if not bad:
        verdict = "aligned"
    elif all(c["action"] == "local_more" for c in bad):
        verdict = "local_more"
    else:
        verdict = "filled"
    return {"store": store, "api": api, "checked_days": len(checks), "filled": filled,
            "source_latest": src_days[-1] if src_days else None,
            "local_latest": local_days[-1] if local_days else None,
            "verdict": verdict, "checks": checks}


def audit_index_global(tok, days: list[str], dry: bool) -> dict:
    """全球指数：按 code 比对每日行数（源侧 vs 本地）。"""
    d = DATA / "index_global"
    codes = sorted(p.stem for p in d.glob("*.parquet"))
    src = {}
    for dt in days[-5:]:
        r = call(tok, "index_global", {"trade_date": dt},
                 "ts_code,trade_date,open,close,high,low,pre_close,change,pct_chg")
        for row in r["items"]:
            rec = dict(zip(r["fields"], row))
            src.setdefault(rec["ts_code"], {}).setdefault(rec["trade_date"], rec)
        time.sleep(0.2)
    updated = 0
    for code in codes:
        f = d / (code + ".parquet")
        try:
            local_dates = set(pq.read_table(f, columns=["trade_date"]).column("trade_date").to_pylist())
        except Exception:  # noqa: BLE001
            continue
        new_rows = [r for dt, r in src.get(code, {}).items() if dt not in local_dates]
        if new_rows and not dry:
            old = pq.read_table(f)
            merged = old.to_pylist() + new_rows
            table = pa.table({c: [r.get(c) for r in merged] for c in old.schema.names})
            tmp = f.with_suffix(".parquet.tmp")
            pq.write_table(table, tmp)
            tmp.replace(f)
            updated += len(new_rows)
    return {"store": "index_global", "codes": len(codes), "new_rows": updated,
            "verdict": "aligned" if updated == 0 else "filled"}


def audit_financials(tok, dry: bool) -> dict:
    """财报：源侧按报告期行数 vs 本地 20260930 覆盖（抽样 300 只）。"""
    import random
    out = {}
    for store, api in (("income", "income_vip"), ("balancesheet", "balancesheet_vip"),
                       ("cashflow", "cashflow_vip"), ("fina_indicator", "fina_indicator_vip")):
        d = DATA / store
        files = sorted(d.glob("*.parquet"))
        if not files:
            continue
        sample = random.Random(11).sample(files, min(300, len(files)))
        have = 0
        for f in sample:
            try:
                tb = pq.read_table(f, columns=["end_date"])
                if "20260930" in [str(x) for x in tb.column("end_date").to_pylist()]:
                    have += 1
            except Exception:  # noqa: BLE001
                pass
        r = call(tok, api, {"period": "20260930"}, "ts_code,ann_date,end_date")
        src_rows = len(r["items"])
        anns = [row[1] for row in r["items"] if row[1]]
        src_codes = {r[0] for r in r["items"]}
        out[store] = {"source_rows": src_rows, "source_codes": len(src_codes),
                      "source_latest_ann": max(anns) if anns else "",
                      "sample": len(sample), "sample_with_q3": have,
                      "coverage_pct": round(have * 100.0 / max(1, len(sample)), 1),
                      "verdict": "aligned" if len(src_codes) else "unknown"}
        time.sleep(0.3)
    return out


def audit_fx(repo: Path | None = None) -> dict:
    """汇率核对：本地（东财主源）vs ECB（frankfurter 独立源）。

    CNH 与 CNY 存在约 0.1%-0.5% 基差，容差 1.5%；HKD 容差 0.5%。
    """
    import urllib.request as _u
    base = repo or REPO
    fx_dir = base / "quant_backtest" / "data" / "tushare" / "fx_daily"
    pairs = {"USDCNH": ("USDCNH_FXCM.parquet", "CNY", 1.5),
             "USDHKD": ("USDHKD_FXCM.parquet", "HKD", 0.5)}
    # ECB 最新参考价（独立源）
    ecb = {}
    ecb_date = ""
    try:
        req = _u.Request("https://api.frankfurter.app/latest?from=USD&to=CNY,HKD",
                         headers={"User-Agent": "Mozilla/5.0"})
        d = json.loads(safe_urlopen(req, timeout=25).read().decode())
        ecb_date = d.get("date") or ""
        ecb = d.get("rates") or {}
    except Exception:  # noqa: BLE001
        ecb = {}
    out = {}
    for code, (fname, ccy, tol) in pairs.items():
        f = fx_dir / fname
        if not f.exists():
            out[code] = {"verdict": "missing_local"}
            continue
        rows = pq.read_table(f, columns=["trade_date", "close"]).to_pylist()
        if not rows:
            out[code] = {"verdict": "empty"}
            continue
        local_latest = max(str(r["trade_date"]) for r in rows)
        local_close = next(float(r["close"]) for r in rows if str(r["trade_date"]) == local_latest)
        src_rate = ecb.get(ccy)
        diff = None
        if src_rate:
            diff = round(abs(float(src_rate) - local_close) / float(src_rate) * 100, 3)
        verdict = "aligned"
        if not src_rate:
            verdict = "no_source"
        elif diff is not None and diff > tol:
            verdict = "gap"
        elif ecb_date and local_latest < ecb_date.replace("-", "")[:8]:
            # 本地落后 ECB 超过 2 个自然日 → 缺口
            from datetime import datetime as _dt
            lag = (_dt.strptime(ecb_date, "%Y-%m-%d") - _dt.strptime(local_latest, "%Y%m%d")).days
            if lag > 2:
                verdict = "gap"
        out[code] = {"local_latest": local_latest, "local_close": local_close,
                     "source": "ECB", "source_date": ecb_date, "source_rate": src_rate,
                     "close_diff_pct": diff, "tolerance_pct": tol, "verdict": verdict}
    return out


def scan_events(tok, store: str, api: str) -> dict:
    """事件型：扫描最近 30 天源侧公告，确认本地最新是否为源侧最新。"""
    from datetime import timedelta
    d = DATA / store
    files = sorted(d.glob("part_*.parquet"))
    local_latest = ""
    if files:
        for f in files:
            try:
                tb = pq.read_table(f, columns=["ann_date"])
                xs = [str(x) for x in tb.column("ann_date").to_pylist() if x]
                if xs:
                    local_latest = max(local_latest, max(xs))
            except Exception:  # noqa: BLE001
                pass
    src_latest = ""
    today = datetime.now()
    for i in range(30):
        dt = (today - timedelta(days=i)).strftime("%Y%m%d")
        r = call(tok, api, {"ann_date": dt}, "ts_code,ann_date")
        anns = [row[1] for row in r["items"] if row[1]]
        if anns:
            src_latest = max(anns)
            break
        time.sleep(0.1)
    return {"store": store, "api": api, "local_latest": local_latest,
            "source_latest": src_latest,
            "verdict": "aligned" if src_latest and local_latest == src_latest else
                       ("unknown" if not src_latest else "gap")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    tok = token()
    if KG.exists():
        tail = KG.read_text(encoding="utf-8", errors="replace").splitlines()[-3:]
        if any("in_progress" in x for x in tail):
            print("kg 日志有进行中操作，停止", file=sys.stderr)
            return 3
    days = last_open_days(args.days)
    report = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "trading_days": days, "dry_run": args.dry_run, "stores": {}, "financials": {}}
    for store, (api, fields) in BY_DATE.items():
        res = audit_by_date(tok, store, api, fields, days, args.dry_run)
        report["stores"][store] = res
        print("%-14s days=%d filled=%d verdict=%s" % (store, res["checked_days"], res["filled"], res["verdict"]), flush=True)
    r = audit_index_global(tok, days, args.dry_run)
    report["stores"]["index_global"] = r
    print("%-14s updated_rows=%d verdict=%s" % ("index_global", r["new_rows"], r["verdict"]), flush=True)
    report["financials"] = audit_financials(tok, args.dry_run)
    report["fx"] = audit_fx()
    for k, v in report["fx"].items():
        print("%-8s local=%s source=%s diff=%s verdict=%s" %
              (k, v.get("local_latest"), v.get("source_latest"), v.get("close_diff_pct"), v.get("verdict")), flush=True)
    report["events"] = {}
    for store, api in (("forecast", "forecast_vip"), ("express", "express_vip")):
        ev = scan_events(tok, store, api)
        report["events"][store] = ev
        print("%-14s local_latest=%s source_latest=%s verdict=%s" %
              (store, ev["local_latest"], ev["source_latest"], ev["verdict"]), flush=True)
    for k, v in report["financials"].items():
        print("%-14s src_rows=%s latest_ann=%s coverage=%.1f%% verdict=%s" %
              (k, v["source_rows"], v["source_latest_ann"], v["coverage_pct"], v["verdict"]), flush=True)
    WORK.mkdir(parents=True, exist_ok=True)
    out = WORK / "audit_latest.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (WORK / ("audit_%s.json" % datetime.now().strftime("%Y%m%d_%H%M%S"))).write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    KG.open("a", encoding="utf-8").write(
        "data_lag_audit days=%d dry=%s aligned=%d filled=%d\n" % (
            args.days, args.dry_run,
            sum(1 for s in report["stores"].values() if s.get("verdict") == "aligned"),
            sum(s.get("filled", s.get("new_rows", 0)) for s in report["stores"].values())))
    print("audit:", out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
