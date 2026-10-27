# -*- coding: utf-8 -*-
"""JEV Base 去重/测试行清理（2026-10-26 回填事故修复）。

事故：`record-list` 分页参数误用（page-size 不支持，实际用 offset/limit），
回填脚本只看到首页 32 条 → 认为全缺 → 全量重插，产生约一倍重复记录。
本脚本按 (判断时间, 标题前80, 来源) 去重，保留最早一条；并清理测试行。

用法：
  python jev_base_dedupe.py --dry     # 只统计
  python jev_base_dedupe.py           # 执行删除（--yes，后台跑）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
EXE = os.path.join(os.path.dirname(HERE), "bin", "lark-cli.exe")


def _load_base_token() -> str:
    """2026-10-26 SEC：Base token 禁止硬编码——env / 私有文件（config_local 链路）。"""
    try:
        from config_local import load_news_config

        tok = (load_news_config().get("jev_base_token") or "").strip()
    except Exception:
        tok = ""
    if not tok:
        tok = os.environ.get("JEV_BASE_TOKEN", "").strip()
    if not tok:
        raise SystemExit(
            "JEV_BASE_TOKEN 未配置（设 env 或 _vault/secrets/news_config_private.json）"
        )
    return tok


TOK = _load_base_token()
TID = "tbl3mCOORkb98xES"
LOG = os.path.join(HERE, "logs", "jev_base_dedupe.log")
LIMIT = 200
DEL_BATCH = 100


def log(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/jev_base_dedupe.py", exc)


def lark(args, timeout=180):
    p = subprocess.run(
        [EXE] + args,
        cwd=HERE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )
    s = p.stdout or ""
    i = s.find("{")
    if i < 0:
        s = p.stderr or ""
        i = s.find("{")
    if i < 0:
        return {"ok": False, "error": (s or "")[:200]}
    try:
        return json.loads(s[i:])
    except ValueError:
        return {"ok": False, "error": s[i : i + 200]}


def fetch_all():
    rows = []
    ids = []
    offset = 0
    fields = None
    while True:
        d = lark(
            [
                "base",
                "+record-list",
                "--as",
                "user",
                "--base-token",
                TOK,
                "--table-id",
                TID,
                "--limit",
                str(LIMIT),
                "--offset",
                str(offset),
                "--format",
                "json",
            ]
        )
        if not d.get("ok"):
            log("fetch FAIL offset=%d %s" % (offset, str(d.get("error"))[:160]))
            break
        data = d.get("data") or {}
        fields = data.get("fields") or fields or []
        page = data.get("data") or []
        rid = data.get("record_id_list") or []
        rows.extend(page)
        ids.extend(rid)
        if len(page) < LIMIT:
            break
        offset += LIMIT
        if offset > 100000:
            break
    log("fetched rows=%d ids=%d fields=%s" % (len(rows), len(ids), fields))
    return fields or [], rows, ids


def norm_ts(v):
    return str(v or "").replace("T", " ")[:16]


TEST_TIMES = {"2026-10-19 11:00", "2026-10-19 11:01"}


def main():
    dry = "--dry" in sys.argv
    fields, rows, ids = fetch_all()
    try:
        i_ts = fields.index("判断时间")
        i_title = fields.index("标题")
        i_src = fields.index("来源")
    except ValueError:
        log("字段缺失，退出")
        return 1
    keep = {}
    delete_ids = []
    dup_n = test_n = 0
    for rid, row in zip(ids, rows):
        ts = norm_ts(row[i_ts]) if i_ts < len(row) else ""
        title = str(row[i_title] or "") if i_title < len(row) else ""
        src = str(row[i_src] or "") if i_src < len(row) else ""
        is_test = (
            "回填连接测试" in title
            or "batch测试" in title
            or src == "test"
            or ts in TEST_TIMES
        )
        if is_test:
            test_n += 1
            delete_ids.append(rid)
            continue
        key = (ts, title[:80], src)
        if key in keep:
            dup_n += 1
            delete_ids.append(rid)
        else:
            keep[key] = rid
    log(
        "total=%d unique_keep=%d dup=%d test=%d to_delete=%d dry=%s"
        % (len(rows), len(keep), dup_n, test_n, len(delete_ids), dry)
    )
    if dry:
        return 0
    ok = fail = 0
    for i in range(0, len(delete_ids), DEL_BATCH):
        batch = delete_ids[i : i + DEL_BATCH]
        d = lark(
            [
                "base",
                "+record-delete",
                "--as",
                "user",
                "--base-token",
                TOK,
                "--table-id",
                TID,
                "--json",
                json.dumps({"record_id_list": batch}),
                "--yes",
            ],
            timeout=120,
        )
        if d.get("ok"):
            ok += len(batch)
            if (i // DEL_BATCH) % 20 == 0:
                log(
                    "delete %d/%d ok (total_deleted=%d)"
                    % (
                        i // DEL_BATCH + 1,
                        (len(delete_ids) + DEL_BATCH - 1) // DEL_BATCH,
                        ok,
                    )
                )
        else:
            fail += len(batch)
            log("delete FAIL %s" % str(d.get("error"))[:200])
        time.sleep(0.15)
    log("DONE deleted_ok=%d fail=%d" % (ok, fail))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
