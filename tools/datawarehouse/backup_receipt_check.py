# -*- coding: utf-8 -*-
"""备份回执核对（只读）：最新备份代、产物齐全、异地 receipt、断档小时数。

用法：python tools/datawarehouse/backup_receipt_check.py --json
退出码：0=正常 / 1=断档或缺件 / 2=备份根缺失
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

BACKUP_ROOT = common.MAIN_REPO / "backups"
OFFSITE_DIR = common.WORKSPACE / "_offsite"
EXPECTED_PRODUCTS = ["jev_eval", "neo4j_kg", "quant_code", "quant_data", "zhice_db", "offsite_remote_receipt"]
MAX_AGE_HOURS = 26.0


def clean_receipt(raw: bytes, limit: int = 400) -> str:
    """回执文本清洗：去 BOM/控制字符，非 ASCII 转义，防止 GBK 控制台崩溃。"""
    text = raw.decode("utf-8-sig", "replace")
    out = []
    for ch in text[:limit]:
        if ch in "\r\n\t" or 32 <= ord(ch) < 127:
            out.append(ch)
        elif ord(ch) > 127:
            out.append("?")
    return "".join(out)[:limit]

def latest_generation(root: Path) -> Path | None:
    if not root.is_dir():
        return None
    gens = [p for p in root.iterdir() if p.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}_\d{2,4}", p.name)]
    return max(gens, key=lambda p: p.name) if gens else None


def age_hours(ts: float, now: float | None = None) -> float:
    now = now if now is not None else dt.datetime.now().timestamp()
    return round((now - ts) / 3600.0, 2)


def collect() -> dict:
    if not BACKUP_ROOT.is_dir():
        return {"tool": "backup_receipt_check", "code": common.EXIT_NOT_READY,
                "error": "备份根不存在", "path": str(BACKUP_ROOT)}
    gen = latest_generation(BACKUP_ROOT)
    if gen is None:
        return {"tool": "backup_receipt_check", "code": common.EXIT_NOT_READY, "error": "无备份代目录"}
    products = [p.name for p in gen.iterdir() if p.is_file()]
    present = [k for k in EXPECTED_PRODUCTS if any(k in n for n in products)]
    missing = [k for k in EXPECTED_PRODUCTS if k not in present]
    latest_mtime = max((p.stat().st_mtime for p in gen.iterdir() if p.is_file()), default=gen.stat().st_mtime)
    hours = age_hours(latest_mtime)
    receipt = gen / "offsite_remote_receipt.txt"
    receipt_text = ""
    if receipt.is_file():
        try:
            receipt_text = clean_receipt(receipt.read_bytes())
        except Exception:  # noqa: BLE001
            receipt_text = "<unreadable>"
    offsite_files = len(list(OFFSITE_DIR.glob("*"))) if OFFSITE_DIR.is_dir() else -1
    issues = []
    if missing:
        issues.append("备份代缺产物: %s" % ",".join(missing))
    if hours > MAX_AGE_HOURS:
        issues.append("备份断档 %.1f 小时（阈值 %.0f）" % (hours, MAX_AGE_HOURS))
    if not receipt.is_file():
        issues.append("备份代缺 offsite_remote_receipt.txt")
    return {"tool": "backup_receipt_check", "code": common.EXIT_WARN if issues else common.EXIT_OK,
            "latest_generation": gen.name, "generation_size_mb": round(
                sum(p.stat().st_size for p in gen.iterdir() if p.is_file()) / 1048576, 1),
            "products": len(products), "present": present, "missing": missing,
            "age_hours": hours, "receipt_present": receipt.is_file(),
            "receipt_head": receipt_text, "offsite_local_files": offsite_files,
            "issues": issues}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="备份回执核对（只读）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    return common.emit(collect(), as_json=a.json, title="backup_receipt_check")


if __name__ == "__main__":
    raise SystemExit(main())
