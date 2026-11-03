# -*- coding: utf-8 -*-
"""restic 每日备份 receipt 生成器（P1 生产化第一步）。

由 restic_trial_backup.ps1 在 backup/check/forget/prune 后调用：
- 读取 restic snapshots latest --json（UTF-8 BOM 兼容）
- 统计仓库体积
- 写 work/restic_trial/receipts/receipt-YYYYMMDD.json + receipts.jsonl
- 输出 JSON 摘要；失败 rc=1（fail-visible，不写坏 receipt）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

RECEIPT_DIR = Path(r"C:\项目\work\restic_trial\receipts")


def repo_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def latest_snapshot(path: Path, restic: str = "") -> dict:
    if restic:
        raw = subprocess.run([restic, "snapshots", "latest", "--json"], capture_output=True).stdout.decode("utf-8-sig")
    else:
        raw = path.read_text(encoding="utf-8-sig")
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    if not data:
        raise ValueError("snapshot json empty")
    return data[-1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup-rc", type=int, required=True)
    ap.add_argument("--check-rc", type=int, default=-1)
    ap.add_argument("--forget-rc", type=int, default=-1)
    ap.add_argument("--prune-rc", type=int, default=-1)
    ap.add_argument("--subset", default="")
    ap.add_argument("--snapshot-json", default="")
    ap.add_argument("--restic", default=r"C:\Users\t\dw_ops\restic.exe")
    ap.add_argument("--repo", default=r"C:\项目\_restic_repo")
    ap.add_argument("--log", default="")
    args = ap.parse_args()

    try:
        snap = latest_snapshot(Path(args.snapshot_json) if args.snapshot_json else Path('-'), args.restic)
        rb = repo_bytes(Path(args.repo))
        summary = snap.get("summary") or {}
        rec = {
            "schema": "restic_receipt_v1",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "ok": args.backup_rc == 0 and args.check_rc == 0 and args.forget_rc in (0, -1),
            "snapshot": {
                "id": snap.get("short_id") or snap.get("id"),
                "time": snap.get("time"),
                "hostname": snap.get("hostname"),
                "paths": snap.get("paths"),
                "tags": snap.get("tags"),
            },
            "backup": {
                "rc": args.backup_rc,
                "files_new": summary.get("files_new"),
                "files_changed": summary.get("files_changed"),
                "files_unmodified": summary.get("files_unmodified"),
                "bytes_processed": summary.get("total_bytes_processed"),
                "data_added": summary.get("data_added"),
            },
            "check": {"rc": args.check_rc, "read_data_subset": args.subset},
            "forget": {"rc": args.forget_rc},
            "prune": {"rc": args.prune_rc},
            "repo_bytes": rb,
            "repo_gb": round(rb / 1e9, 3),
            "log": args.log,
        }
        RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
        day = datetime.now().strftime("%Y%m%d")
        out = RECEIPT_DIR / ("receipt-%s.json" % day)
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, out)
        jl = RECEIPT_DIR / "receipts.jsonl"
        with jl.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(json.dumps({"ok": rec["ok"], "receipt": str(out), "snapshot": rec["snapshot"]["id"]}, ensure_ascii=False))
        return 0 if rec["ok"] else 1
    except Exception as exc:  # noqa: BLE001
        print("receipt_err: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
