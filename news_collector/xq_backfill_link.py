# -*- coding: utf-8 -*-
"""xq_backfill_link.py — 存量 xq_timeline jsonl 幂等补 link 字段（L3 / C6 溯源缺口闭环）

仅补充溯源 link，不改其他字段；已含合规 link 的行跳过。
采集器 xq_stock_timeline.py 自 2026-11-11 起新抓取自动带 link，本脚本补齐历史存量。
用法：python xq_backfill_link.py
"""
import glob
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
XQ_DIR = HERE / "data" / "xq_timeline"


def make_link(rec):
    uid = rec.get("user_id")
    sid = str(rec.get("id") or "")
    if sid.startswith("xqtl:"):
        sid = sid.rsplit(":", 1)[-1]
    if not sid:
        return None
    if uid:
        return "https://xueqiu.com/{0}/{1}".format(uid, sid)
    return "https://xueqiu.com/status/{0}".format(sid)


def main():
    total = 0
    updated = 0
    skipped = 0
    for fp in sorted(XQ_DIR.glob("*.jsonl")):
        rows = []
        changed = False
        with fp.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    rows.append(line)
                    continue
                total += 1
                existing = rec.get("link")
                if existing and str(existing).startswith("https://xueqiu.com/"):
                    skipped += 1
                    rows.append(json.dumps(rec, ensure_ascii=False))
                    continue
                lnk = make_link(rec)
                if lnk:
                    rec["link"] = lnk
                    changed = True
                    updated += 1
                rows.append(json.dumps(rec, ensure_ascii=False))
        if changed:
            tmp = fp.with_suffix(".jsonl.tmp")
            tmp.write_text("\n".join(rows) + "\n", encoding="utf-8")
            os.replace(tmp, fp)
    print("DONE total={0} updated={1} skipped={2}".format(total, updated, skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
