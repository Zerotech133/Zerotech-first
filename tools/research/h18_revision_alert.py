# -*- coding: utf-8 -*-
"""H18 事件风险提醒：业绩预告修订 → 恶化修订清单（短期规避提醒）。

流程：
1) 自刷新 forecast 最近 N 天公告（forecast_vip 按 ann_date）；
2) 识别同一 ts_code+end_date 的二次+公告（修订）；
3) 输出最近 W 天内「恶化修订」清单（类型极性下降，或极性不变但幅度中值恶化）；
4) 写入 work/research/h18_alert_<date>.{json,md} + 日志。
仅研究提醒，不构成交易指令。

用法：python tools/research/h18_revision_alert.py [--days 30] [--refresh-days 14] [--no-refresh]
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from tools.safe_urlopen import safe_urlopen

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "work" / "research"
VAULT = REPO / "tools" / "secrets" / "vault.py"
POS = {"预增", "扭亏", "略增", "续盈", "减亏"}
NEG = {"预减", "首亏", "续亏", "略减", "增亏", "不确定"}


def token() -> str:
    p = subprocess.run([sys.executable, str(VAULT), "decrypt", "mcp/tushare_mcp_token"],
                       capture_output=True, timeout=60)
    return (p.stdout or b"").decode("utf-8", "replace").strip()


def call(tok: str, api: str, params: dict, fields: str) -> dict:
    body = json.dumps({"api_name": api, "token": tok, "params": params, "fields": fields}).encode()
    req = urllib.request.Request("http://api.tushare.pro", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    d = json.loads(safe_urlopen(req, timeout=90).read().decode())
    data = d.get("data") or {}
    return {"code": d.get("code"), "msg": (d.get("msg") or "")[:120],
            "fields": data.get("fields") or [], "items": data.get("items") or []}


def refresh_forecast(tok: str, days: int) -> int:
    """把最近 days 天 forecast_vip 公告合并进 forecast 年度文件。"""
    d = DATA / "forecast"
    files = sorted(d.glob("part_*.parquet"))
    if not files:
        return 0
    target = files[-1]
    for f in files:
        try:
            tb = pq.read_table(f, columns=["ann_date"])
            xs = [str(x) for x in tb.column("ann_date").to_pylist() if x]
            if xs and max(xs)[:4] == "2026":
                target = f
                break
        except Exception:  # noqa: BLE001
            continue
    old = pq.read_table(target)
    old_rows = old.to_pylist()
    existing = {(r.get("ts_code"), str(r.get("ann_date")), str(r.get("end_date"))) for r in old_rows}
    added = []
    today = datetime.now()
    for i in range(days):
        dt = (today - timedelta(days=i)).strftime("%Y%m%d")
        r = call(tok, "forecast_vip", {"ann_date": dt},
                 "ts_code,ann_date,end_date,type,p_change_min,p_change_max,last_parent_net,first_ann_date,summary,change_reason")
        if r["code"] != 0 or not r["items"]:
            continue
        for row in r["items"]:
            rec = dict(zip(r["fields"], row))
            key = (rec.get("ts_code"), str(rec.get("ann_date")), str(rec.get("end_date")))
            if key not in existing:
                added.append(rec)
                existing.add(key)
    if added:
        merged = old_rows + added
        table = pa.table({c: [r.get(c) for r in merged] for c in old.schema.names})
        tmp = target.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp)
        tmp.replace(target)
    print(f"refresh forecast: +{len(added)} rows -> {target.name}", flush=True)
    return len(added)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="提醒扫描窗口（自然日）")
    ap.add_argument("--refresh-days", type=int, default=14)
    ap.add_argument("--no-refresh", action="store_true")
    args = ap.parse_args(argv)
    tok = token()
    added = 0
    if not args.no_refresh:
        try:
            added = refresh_forecast(tok, args.refresh_days)
        except Exception as e:  # noqa: BLE001
            print("refresh ERR", type(e).__name__, str(e)[:120], flush=True)

    fs = sorted(glob.glob(str(DATA / "forecast" / "*.parquet")))
    df = pa.concat_tables([pq.read_table(f) for f in fs]).to_pandas()
    df["ann_date"] = df["ann_date"].astype(str)
    df["end_date"] = df["end_date"].astype(str)
    df["score"] = df["type"].map(lambda x: 1 if x in POS else (-1 if x in NEG else 0))
    df["mid"] = (df["p_change_min"].fillna(0) + df["p_change_max"].fillna(0)) / 2
    df = df.sort_values(["ts_code", "end_date", "ann_date"])
    df["prev_score"] = df.groupby(["ts_code", "end_date"])["score"].shift(1)
    df["prev_mid"] = df.groupby(["ts_code", "end_date"])["mid"].shift(1)
    df["prev_type"] = df.groupby(["ts_code", "end_date"])["type"].shift(1)
    df["prev_ann"] = df.groupby(["ts_code", "end_date"])["ann_date"].shift(1)
    rev = df[df["prev_score"].notna()].copy()
    rev["dscore"] = rev["score"] - rev["prev_score"]
    rev["dmid"] = rev["mid"] - rev["prev_mid"]
    rev["direction"] = (rev["dscore"] > 0).astype(int) - (rev["dscore"] < 0).astype(int)
    rev.loc[rev["dscore"] == 0, "direction"] = (rev.loc[rev["dscore"] == 0, "dmid"] > 0).astype(int) - (
        rev.loc[rev["dscore"] == 0, "dmid"] < 0).astype(int)

    cutoff = (datetime.now() - timedelta(days=args.days)).strftime("%Y%m%d")
    recent = rev[rev["ann_date"] >= cutoff].copy()
    bad = recent[recent["direction"] < 0].sort_values("ann_date", ascending=False)
    good = recent[recent["direction"] > 0].sort_values("ann_date", ascending=False)
    today = datetime.now().strftime("%Y%m%d")
    alerts = []
    for r in bad.itertuples():
        alerts.append({"ts_code": r.ts_code, "ann_date": r.ann_date, "end_date": r.end_date,
                       "prev_type": r.prev_type, "type": r.type,
                       "p_change": [r.p_change_min, r.p_change_max],
                       "dmid": None if r.dmid != r.dmid else round(float(r.dmid), 2),
                       "severity": "high" if r.dscore < 0 else "medium"})
    out = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "scan_days": args.days, "refreshed_rows": added,
           "revisions_recent": int(len(recent)),
           "deteriorations": len(alerts), "improvements": int(len(good)),
           "alerts": alerts,
           "note": "恶化修订提醒：建议在公告后 5–20 个交易日规避/降权（H18 证据：OOS 20 日 -3.04%，t=-3.06）；仅研究提醒。"}
    OUT.mkdir(parents=True, exist_ok=True)
    js = OUT / ("h18_alert_%s.json" % today)
    js.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    L = ["# H18 事件风险提醒 · %s" % today, "",
         "- 扫描窗口：最近 %d 天；新增预告 %d 行；近期修订 %d 个（恶化 %d / 改善 %d）" % (
             args.days, added, len(recent), len(alerts), len(good)), ""]
    if alerts:
        L += ["## ⚠ 恶化修订（建议 5–20 交易日规避/降权）", "| 代码 | 公告日 | 报告期 | 上次→本次 | 幅度区间 |", "|---|---|---|---|---|"]
        for a in alerts[:30]:
            L.append("| %s | %s | %s | %s→%s | %s ~ %s |" % (
                a["ts_code"], a["ann_date"], a["end_date"], a["prev_type"], a["type"],
                a["p_change"][0], a["p_change"][1]))
    else:
        L.append("## 无恶化修订")
    md = OUT / ("h18_alert_%s.md" % today)
    md.write_text("\n".join(L), encoding="utf-8")
    try:
        logf = REPO / "work" / "data_sync" / "h18_alert.log"
        logf.parent.mkdir(parents=True, exist_ok=True)
        logf.open("a", encoding="utf-8").write(
            json.dumps({"ts": out["generated_at"], "refreshed": added, "recent": len(recent),
                        "deteriorations": len(alerts), "improvements": int(len(good))},
                       ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass
    print(json.dumps({"date": today, "recent_revisions": int(len(recent)),
                      "deteriorations": len(alerts), "improvements": int(len(good)),
                      "json": str(js), "md": str(md)}, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
