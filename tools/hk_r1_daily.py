# -*- coding: utf-8 -*-
"""HK R1 两周观察 · 每日跑批（默认 dry-run；--append 才执行并追加台账）。"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

CMD_TIMEOUT = int(os.environ.get("HK_R1_CMD_TIMEOUT", "600"))
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO.parent / "_reviews_20261026" / "hk_r1_observation_v1.md"
LOGS = REPO.parent / "_reviews_20261026" / "hk_r1_logs"
PY = sys.executable

FILL_RE = re.compile(r"#(\d+)\s+(\S+)\s+(\w+)\s+(\d+)\s+股\s+@\s+([\d.]+)\s*（费\s*([\d.]+)）")
EQ_RE = re.compile(
    r"现金\s*([\d,]+\.\d+)\s*HKD\s*\|\s*持仓市值\s*([\d,]+\.\d+)\s*"
    r"\|\s*总资产\s*([\d,]+\.\d+)\s*\|\s*仓位\s*([\d.]+)%\s*\|\s*对账\s*(OK|FAIL)")
SNAP_RE = re.compile(r"快照\s*(\d{4}-\d{2}-\d{2}):.*?待撮合\s*(\d+).*?对账\s*(OK|FAIL)")


def _run(cmd: list[str], timeout: int = CMD_TIMEOUT) -> dict:
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return {"cmd": cmd, "rc": r.returncode, "out": r.stdout or "", "err": r.stderr or ""}


def parse_run(out: str) -> dict:
    fills = []
    for m in FILL_RE.finditer(out):
        fills.append({"id": int(m.group(1)), "stock": m.group(2), "side": m.group(3),
                      "qty": int(m.group(4)), "price": float(m.group(5)),
                      "fee": float(m.group(6))})
    eqs = EQ_RE.findall(out)
    snaps = SNAP_RE.findall(out)
    res = {"fills": fills, "fill_count": len(fills),
           "fee_total": round(sum(f["fee"] for f in fills), 2)}
    if eqs:
        e = eqs[-1]
        res.update({"cash": float(e[0].replace(",", "")),
                    "market_value": float(e[1].replace(",", "")),
                    "total": float(e[2].replace(",", "")),
                    "pos_pct": round(float(e[3]) / 100.0, 6), "reconcile": e[4]})
    if snaps:
        s = snaps[-1]
        res.update({"snapshot": s[0], "pending": int(s[1]), "snap_reconcile": s[2]})
    return res


def _lake_latest(calendar_out: str) -> str:
    try:
        d = json.loads(calendar_out.strip().splitlines()[-1])
        p = Path(d.get("path", ""))
        if p.is_file():
            import csv
            lines = list(csv.DictReader(p.open(encoding="utf-8")))
            for k in ("date", "trade_date", "cal_date"):
                vals = [r.get(k) for r in lines if r.get(k)]
                if vals:
                    return str(max(vals)).replace("-", "")[:8]
    except (ValueError, OSError, KeyError):
        pass
    return "?"


def build_row(parsed: dict, lake: str) -> str:
    date = dt.date.today().isoformat()
    note = "自动跑批"
    if parsed.get("reconcile") != "OK":
        note = "⚠ 对账 %s" % parsed.get("reconcile", "?")
    if parsed.get("fill_count") == 0:
        note += "；0 成交"
    return ("| %s | %s | %d | %.2f | %s | %s | %s | %.1f%% | %s | %s |"
            % (date, lake, parsed["fill_count"], parsed["fee_total"],
               "{:,.2f}".format(parsed.get("cash", 0)),
               "{:,.2f}".format(parsed.get("market_value", 0)),
               "{:,.2f}".format(parsed.get("total", 0)),
               parsed.get("pos_pct", 0.0) * 100,
               parsed.get("reconcile", "?"), note))


TABLE_HEADER = "| 日期 | 湖最新 |"


def insert_row(ledger: Path, row: str) -> dict:
    """把一行写进台账「台账」表格（不是文件尾），同日重复则跳过；带文件锁。"""
    ledger = Path(ledger)
    date = row.split("|")[1].strip()
    lock = ledger.with_suffix(".lock")
    try:
        from file_lock import acquire, release  # noqa: PLC0415
    except ImportError:
        acquire = release = None
    tok = acquire(str(lock), "hk_r1_ledger", timeout=30) if acquire else None
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
        try:
            hi = next(i for i, ln in enumerate(lines) if ln.startswith(TABLE_HEADER))
        except StopIteration:
            return {"ok": False, "error": "table_header_missing"}
        end = hi + 2  # 跳过表头与分隔行
        while end < len(lines) and lines[end].startswith("|"):
            parts = lines[end].split("|")
            if len(parts) > 1 and parts[1].strip() == date:
                return {"ok": True, "skipped": True, "reason": "duplicate_date"}
            end += 1
        lines.insert(end, row)
        tmp = ledger.with_suffix(".md.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(ledger)
        return {"ok": True, "skipped": False}
    finally:
        if acquire and release and tok:
            release(str(lock), tok)


def run(append: bool = False, ledger: Path | None = None) -> dict:
    ledger = Path(ledger) if ledger else LEDGER
    cmds = [
        [PY, "-X", "utf8", "quant_backtest/export_us_hk.py", "--market", "HK"],
        [PY, "-X", "utf8", "k_stock/paper/hk_market.py", "calendar"],
        [PY, "-X", "utf8", "k_stock/paper/hk_market.py", "run"],
        [PY, "-X", "utf8", "k_stock/paper/hk_market.py", "status"],
    ]
    if not append:
        return {"ok": True, "dry_run": True, "ledger": str(ledger),
                "commands": [" ".join(c) for c in cmds],
                "note": "dry-run：未执行；--append 才跑批并追加台账"}
    logs = LOGS
    logs.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log = logs / ("hk_r1_%s.log" % stamp)
    outs = []
    for c in cmds:
        try:
            r = _run(c)
        except subprocess.TimeoutExpired:
            with log.open("a", encoding="utf-8") as f:
                f.write("$ %s\n[TIMEOUT >%ds]\n" % (" ".join(c), CMD_TIMEOUT))
            return {"ok": False, "error": "command_timeout",
                    "command": " ".join(c), "timeout_sec": CMD_TIMEOUT,
                    "log": str(log)}
        outs.append(r)
        with log.open("a", encoding="utf-8") as f:
            f.write("$ %s\n[rc=%s]\n%s\n%s\n" % (" ".join(c), r["rc"], r["out"], r["err"]))
        if r["rc"] != 0:
            return {"ok": False, "error": "command_failed", "command": " ".join(c),
                    "rc": r["rc"], "log": str(log)}
    parsed = parse_run(outs[2]["out"] + "\n" + outs[3]["out"])
    lake = _lake_latest(outs[1]["out"])
    if "total" not in parsed:
        return {"ok": False, "error": "parse_failed", "log": str(log), "parsed": parsed}
    row = build_row(parsed, lake)
    if not ledger.exists():
        return {"ok": False, "error": "ledger_missing", "ledger": str(ledger),
                "row": row, "log": str(log)}
    ins = insert_row(ledger, row)
    if not ins.get("ok"):
        return {"ok": False, "error": "ledger_write_failed", "detail": ins,
                "row": row, "log": str(log)}
    return {"ok": True, "dry_run": False, "row": row, "parsed": parsed,
            "skipped": ins.get("skipped", False),
            "log": str(log), "ledger": str(ledger)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HK R1 两周观察每日跑批")
    ap.add_argument("--append", action="store_true", help="执行跑批并追加台账（默认 dry-run）")
    ap.add_argument("--ledger", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rep = run(args.append, Path(args.ledger) if args.ledger else None)
    print(json.dumps(rep, ensure_ascii=False, indent=1) if args.json
          else json.dumps(rep, ensure_ascii=False)[:1200])
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
