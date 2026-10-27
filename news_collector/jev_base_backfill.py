# -*- coding: utf-8 -*-
"""JEV 判断记录 → 飞书 Base 历史回填（一次性，2026-10-26）。

背景：news.db 有 ~16.8k 条 JEV 判断，Base「判断记录」仅 23 条（2026-09-26 单日）
→ 历史回填。幂等：按「判断时间」（分钟精度）去重，可重复跑。

用法：
  python jev_base_backfill.py --dry          # 只统计缺失，不写
  python jev_base_backfill.py --limit 100    # 只回填最早 100 条（试跑）
  python jev_base_backfill.py                # 全量回填（后台跑，落日志）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "portal"))

EXE = os.path.join(os.path.dirname(HERE), "bin", "lark-cli.exe")


def _load_base_token() -> str:
    """2026-10-26 SEC：Base token 禁止硬编码——env / 私有文件（config_local 链路）。"""
    try:
        from config_local import load_news_config  # 同目录；敏感字段已从 tracked 迁出
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
TABLE = "tbl3mCOORkb98xES"  # 判断记录
BATCH_FILE = os.path.join(HERE, "_jev_bf_batch.json")
BATCH = 40  # inline --json 受 Windows argv 长度限制
LOG = os.path.join(HERE, "logs", "jev_base_backfill.log")


def log(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def lark(args, timeout=120):
    r = subprocess.run([EXE] + args, cwd=HERE, capture_output=True, text=True,
                       encoding="utf-8", timeout=timeout)
    out = r.stdout or ""
    i = out.find("{")
    if i < 0:
        return {"ok": False, "error": (out or r.stderr or "")[:200]}
    try:
        return json.loads(out[i:])
    except ValueError:
        return {"ok": False, "error": out[i:i + 200]}


def fetch_existing():
    """分页读全量（本 CLI 用 offset/limit；page-size/page-token 不支持）。"""
    seen = set()
    n = 0
    offset = 0
    limit = 200
    while True:
        d = lark(["base", "+record-list", "--as", "user", "--base-token", TOK,
                  "--table-id", TABLE, "--limit", str(limit),
                  "--offset", str(offset), "--format", "json"])
        if not d.get("ok"):
            log("fetch_existing FAIL %s" % str(d.get("error"))[:160])
            break
        data = d.get("data") or {}
        rows = data.get("data") or []
        fields = data.get("fields") or []
        try:
            ci = fields.index("判断时间")
        except ValueError:
            ci = None
        for row in rows:
            n += 1
            if ci is not None and len(row) > ci and row[ci]:
                seen.add(str(row[ci]).replace("T", " ")[:16])
        if len(rows) < limit:
            break
        offset += limit
        if offset > 100000:
            break
    log("existing rows=%d unique_ts=%d" % (n, len(seen)))
    return seen


def build_records():
    import news_jev_chart as C
    now, items = C.load()
    BJ = {"bull": "利多", "bear": "利空", "neutral": "中性"}
    SC = {"cn": "A股", "hk": "港股", "us": "美股", "commodity": "商品",
          "crypto": "加密", "macro": "宏观"}
    recs = []
    for x in items:  # items 新→旧
        ts = x["t"].strftime("%Y-%m-%d %H:%M")
        recs.append({"ts": ts, "rec": {
            "标题": x["title"][:200],
            "方向": [BJ.get(x["bias"], "中性")],
            "置信度": round(x["conf"] * 100),
            "市场": [SC.get(x["scope"]) or "其他"],
            "判断时间": ts,
            "依据": str(x.get("reason") or "")[:500],
            "来源": str(x.get("source") or "")[:30],
        }})
    log("news.db items=%d" % len(recs))
    return recs


def main():
    dry = "--dry" in sys.argv
    limit = 0
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    t0 = time.time()
    existing = fetch_existing()
    recs = build_records()
    missing = [r for r in recs if r["ts"] not in existing]
    missing.sort(key=lambda r: r["ts"])  # 旧→新
    log("missing=%d (existing=%d total=%d) dry=%s limit=%s"
        % (len(missing), len(existing), len(recs), dry, limit))
    if dry:
        return 0
    if limit:
        missing = missing[:limit]
    ok_n = fail_n = 0
    for i in range(0, len(missing), BATCH):
        batch = [r["rec"] for r in missing[i:i + BATCH]]
        d = lark(["base", "+record-batch-create", "--as", "user",
                  "--base-token", TOK, "--table-id", TABLE,
                  "--json", json.dumps({"create_records": batch},
                                       ensure_ascii=False)], timeout=180)
        if d.get("ok"):
            ok_n += len(batch)
            log("batch %d/%d ok (+%d, total_ok=%d)"
                % (i // BATCH + 1, (len(missing) + BATCH - 1) // BATCH,
                   len(batch), ok_n))
        else:
            fail_n += len(batch)
            log("batch FAIL %s" % json.dumps(d.get("error") or d,
                                             ensure_ascii=False)[:240])
        time.sleep(0.2)
    log("DONE ok=%d fail=%d elapsed=%.1fs" % (ok_n, fail_n,
                                              time.time() - t0))
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
