#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把会议纪要里的编码损坏条目标记为 voided（R12 D6 存量处置）。

背景：`hq-20261029-054846.json` / `hq-20261103-075303.json` 里有条目在入参阶段
就被代码页替换成 0x3F（`?`）。写入端编码正确，坏的是**送进来的字节**，落盘后
不可逆——只能作废，不能"修复"。

本脚本只做一件事：给含连续 `?` 的条目加
    status="voided" / void_reason="encoding_loss" / voided_at=<ISO8601>
使其从「未派单议题」等口径里退出，同时**保留原始乱码原文**作为证据（不删不改）。

用法：
    python tools/void_mojibake_meetings.py --dry     # 只报表
    python tools/void_mojibake_meetings.py           # 落盘（原子写，幂等）
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MEETINGS = REPO / "agent_platform" / "data" / "meetings" / "hq"
TZ = dt.timezone(dt.timedelta(hours=8))

# 各 section 里需要检查的文本字段（只查正文，不 dump 整条，避免误伤 id/due）
TEXT_FIELDS = {
    "agenda": ("item",),
    "decisions": ("decision",),
    "transcript": ("text",),
    "orders": ("title",),
    "acceptance": ("evidence", "report"),
}


def _garbled_re():
    """复用 hq_meeting 的正则，保证「入口拒收」与「存量标记」判据一致。"""
    path = REPO / "agent_platform" / "legion_command" / "hq_meeting.py"
    spec = importlib.util.spec_from_file_location("aios_hq_meeting", path)
    if spec is None or spec.loader is None:
        raise SystemExit("无法加载 hq_meeting.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.GARBLED_RE


def scan(doc: dict, garble) -> list[tuple[str, int, str]]:
    """返回 [(section, idx, 命中的字段)]。"""
    hits = []
    for section, fields in TEXT_FIELDS.items():
        for i, row in enumerate(doc.get(section) or []):
            if not isinstance(row, dict):
                continue
            for f in fields:
                if garble.search(str(row.get(f) or "")):
                    hits.append((section, i, f))
                    break
    return hits


def void_doc(doc: dict, garble, now: str) -> list[dict]:
    changed = []
    for section, idx, field in scan(doc, garble):
        row = doc[section][idx]
        if row.get("status") == "voided":
            continue  # 幂等
        row["status"] = "voided"
        row["void_reason"] = "encoding_loss"
        row["voided_at"] = now
        changed.append({"section": section, "index": idx, "field": field,
                        "preview": str(row.get(field) or "")[:40]})
    return changed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="标记会议纪要中的编码损坏条目")
    ap.add_argument("--dry", action="store_true", help="只打印，不写文件")
    ap.add_argument("--dir", default=str(MEETINGS))
    ap.add_argument("--file", action="append", default=[], metavar="NAME",
                    help="只处理指定文件（可重复，如 hq-20261029-054846.json）；"
                         "默认处理目录内全部")
    a = ap.parse_args(argv)

    garble = _garbled_re()
    now = dt.datetime.now(TZ).replace(microsecond=0).isoformat()
    d = Path(a.dir)
    if a.file:
        files = [d / n for n in a.file]
        missing = [str(p) for p in files if not p.is_file()]
        if missing:
            raise SystemExit("文件不存在：%s" % ", ".join(missing))
    else:
        files = sorted(d.glob("hq-*.json")) if d.is_dir() else []
    if not files:
        print("未找到会议纪要：%s" % d)
        return 0

    total, touched = 0, 0
    for p in files:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print("跳过 %s（%s）" % (p.name, exc))
            continue
        if not isinstance(doc, dict):
            continue
        changed = void_doc(doc, garble, now)
        if not changed:
            continue
        touched += 1
        total += len(changed)
        print("%s%s：%d 条" % (p.name, "（dry）" if a.dry else "", len(changed)))
        for c in changed:
            print("  - %s[%d].%s  %s" % (c["section"], c["index"], c["field"],
                                         c["preview"]))
        if not a.dry:
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(p)

    print("\n合计：%d 个文件 / %d 条%s" % (touched, total, "（未写盘）" if a.dry else "已标记"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
