# -*- coding: utf-8 -*-
"""JEV Base 补缺口（按 (判断时间,标题,来源) 精确去重；2026-10-26）。"""
import json
import sys
import time

HERE = r"C:\项目\老田的AI助手\news_collector"
sys.path.insert(0, HERE)
sys.path.insert(0, r"C:\项目\老田的AI助手\portal")
import jev_base_backfill as B  # noqa: E402
import jev_base_dedupe as D  # noqa: E402


def main():
    fields, rows, ids = D.fetch_all()
    idx = {name: fields.index(name) for name in ("判断时间", "标题", "来源")}
    base_keys = set()
    for row in rows:
        try:
            ts = str(row[idx["判断时间"]] or "").replace("T", " ")[:16]
            title = str(row[idx["标题"]] or "")[:80]
            src = str(row[idx["来源"]] or "")
            base_keys.add((ts, title, src))
        except (IndexError, KeyError):
            continue
    recs = B.build_records()
    missing = []
    for r in recs:
        key = (r["ts"], r["rec"]["标题"][:80], r["rec"]["来源"])
        if key not in base_keys:
            missing.append(r)
    print("base rows=%d base_keys=%d news=%d missing=%d"
          % (len(rows), len(base_keys), len(recs), len(missing)))
    ok = fail = 0
    for i in range(0, len(missing), B.BATCH):
        batch = [r["rec"] for r in missing[i:i + B.BATCH]]
        d = B.lark(["base", "+record-batch-create", "--as", "user",
                    "--base-token", B.TOK, "--table-id", B.TABLE,
                    "--json", json.dumps({"create_records": batch},
                                         ensure_ascii=False)], timeout=180)
        if d.get("ok"):
            ok += len(batch)
        else:
            fail += len(batch)
            print("FAIL", str(d.get("error"))[:200])
        time.sleep(0.2)
    print("FILL DONE ok=%d fail=%d" % (ok, fail))


if __name__ == "__main__":
    main()
