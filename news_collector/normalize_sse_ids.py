# -*- coding: utf-8 -*-
"""A1② 一次性迁移：归一化 data/sse/*.jsonl 的 id 并去重。

旧采集器用 Python 内置 hash()（按进程加盐）生成 id，确定性缺失导致
按 id 去重失效、每日重抓重复追加。本脚本：
  - 用与 sse_collector._stable_sse_id 完全一致的 sha256 内容指纹重算 id；
  - 按 (ts, content) 去重（保留首现行），清理历史重复；
  - 原样保留其它字段与行顺序；幂等（重跑不破坏已归一化数据）。

用法：python normalize_sse_ids.py            # 全量迁移并打印统计
      python normalize_sse_ids.py --dry      # 只统计不写盘
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(HERE), "data", "sse")


def stable_id(code, ts, content):
    raw = "%s|%s|%s" % (code, ts or "", content or "")
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return "sse:%s:%s" % (code, h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只统计不写盘")
    args = ap.parse_args()

    files = sorted(
        f for f in os.listdir(OUT_DIR) if f.endswith(".jsonl")
    ) if os.path.isdir(OUT_DIR) else []
    tot_files = tot_before = tot_after = tot_dup = 0
    changed_files = 0
    for fn in files:
        path = os.path.join(OUT_DIR, fn)
        rows = []
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rows.append(json.loads(ln))
                except Exception:
                    rows.append(ln)  # 非 JSON 行原样保留
        before = len(rows)
        seen = set()
        out_rows = []
        for r in rows:
            if not isinstance(r, dict):
                out_rows.append(r)
                continue
            code = str(r.get("code", ""))
            ts = r.get("ts", "")
            content = r.get("content", "")
            key = (ts, content)
            if key in seen:
                tot_dup += 1
                continue
            seen.add(key)
            r["id"] = stable_id(code, ts, content)
            out_rows.append(r)
        after = len(out_rows)
        tot_files += 1
        tot_before += before
        tot_after += after
        # 始终重写：即便本文件无重复，也把旧随机 id 归一化为确定性 sha256 指纹，
        # 否则未重复文件重抓时旧 id 与新 id 不一致仍会重复追加。
        if not args.dry:
            with open(path, "w", encoding="utf-8") as f:
                for r in out_rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            changed_files += 1
        if tot_files <= 5 or changed_files <= 3:
            print("[norm] %s before=%d after=%d dup=%d" % (fn, before, after, before - after))
    print("=== A1② 迁移统计 ===")
    print("files=%d changed=%d before=%d after=%d removed_dup=%d dry=%s"
          % (tot_files, changed_files, tot_before, tot_after, tot_before - tot_after, args.dry))
    return 0


if __name__ == "__main__":
    sys.exit(main())
