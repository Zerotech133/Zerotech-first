# -*- coding: utf-8 -*-
"""互动易回答正文回填（P1.1 终解，2026-11-09）。

接口（akshare stock_irm_ans_cninfo 同款）：GET /newircs/question/getQuestionDetail
    ?questionId=<indexId>  →  data.replyContent / replyDate（未答则无此键）

对 data/irm/*.jsonl 逐条回填：reply_content / reply_date / has_reply。
幂等：已有 reply_content 的行跳过；新库行追加写，旧库行重写（原子 tmp+replace）。
限速：0.35s/条（官方接口，温和节奏）。
"""
import json
import os
import time
import glob as _glob
import urllib.request

BASE = "https://irm.cninfo.com.cn/newircs/question/getQuestionDetail"
DIR = r"C:/项目/老田的AI助手/data/irm"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"}


def fetch_reply(index_id):
    url = "%s?questionId=%s&_t=%d" % (BASE, index_id, int(time.time() * 1000))
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=12) as resp:
        d = json.loads(resp.read().decode("utf-8")).get("data") or {}
    reply = (d.get("replyContent") or "").strip()
    rdate_ms = d.get("replyDate")
    try:
        rdate = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(rdate_ms) / 1000)) if rdate_ms else ""
    except (TypeError, ValueError, OSError):
        rdate = ""
    return reply, rdate


def backfill_file(path):
    rows = [json.loads(ln) for ln in open(path, encoding="utf-8") if ln.strip()]
    todo = [r for r in rows if not r.get("reply_content")]
    if not todo:
        return 0, len(rows)
    for r in todo:
        idx = r["id"].split(":", 1)[1].split("_", 1)[-1]
        try:
            reply, rdate = fetch_reply(idx)
        except Exception as e:
            print("  [skip] %s %s" % (r["id"], str(e)[:50]))
            continue
        r["reply_content"] = reply
        r["reply_date"] = rdate
        r["has_reply"] = 1 if reply else 0
        time.sleep(0.35)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    got = sum(1 for r in todo if r.get("has_reply"))
    return got, len(rows)


def main():
    total_new, total_rows, files = 0, 0, 0
    for path in sorted(_glob.glob(os.path.join(DIR, "*.jsonl"))):
        code = os.path.basename(path).split(".")[0]
        got, n = backfill_file(path)
        total_new += got
        total_rows += n
        files += 1
        print("[irm] %s 回答回填 %d/%d" % (code, got, n))
    print("完成: %d 文件, 总 %d 行, 新回填回答 %d" % (files, total_rows, total_new))


if __name__ == "__main__":
    main()
