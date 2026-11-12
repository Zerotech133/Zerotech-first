# -*- coding: utf-8 -*-
"""淘股吧（tgb.cn）个股页定向采集器 —— P2，游资/短线情绪维度。

数据源：淘股吧个股讨论页 https://www.taoguba.com.cn/quotes/{code6}
（302 → www.tgb.cn）。关键发现（2026-11-09 实测）：帖子列表是
**SSR 直出**（requests 直通 277KB，无需 Playwright——此前网络层
看到的只是行情 XHR）。

解析锚点（页面每帖一块，块首 <img class="headImg"）：
  · 作者:  <a href="https://www.tgb.cn/blog/<uid>">用户名</a>
  · 时间:  <div class="related-sources">YYYY-MM-DD HH:MM 发布主帖|跟帖回复</div>
  · 主帖:  id="gioMsg_T_<topicId>" subject="标题" userid username
           + <a class="related-body">【摘要】正文（含<font>高亮）
  · 帖链:  https://www.tgb.cn/a/<topicB62>
  · 计数:  赞(x) 浏览(x) 评论(x)

落盘：data/taoguba/{code6}.jsonl（行结构对齐 irm/guba 约定）：
  {"id": "tgb:T<topicId>" | "tgb:R<md5>", "code", "name", "ts",
   "kind": "post"|"reply", "author", "title", "content",
   "praise", "view", "comment", "src": "tgb.cn"}

CLI：
  python taoguba_collector.py                # actionable 队列
  python taoguba_collector.py 300308,000423  # 指定代码
  python taoguba_collector.py 300308 --pages 3
独立自检：python taoguba_collector.py 300308
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(HERE), "data", "taoguba")
QUEUE_FILE = os.path.join(HERE, "target_queue_chanlun.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36")
_GAP = 2.0

# ── parse_empty 护栏（2026-11-13，判例：11-12 那轮 65 只全报「新增 0」）
# 失效形态：HTTP 200 + 页面 >8KB + 解析 0 行 → 采集器不报错，只记「新增 0」，
# 与「确实无新帖」在日志里完全同形。护栏把这两种情况分开计数，并给出判定。
PARSE_EMPTY_PCT = 0.30      # 首页解析 0 行的占比上限
PARSE_MIN_SAMPLE = 5        # 样本不足不判（手动跑 2-3 只不触发）

RE_BLOCK = re.compile(r'<img class="headImg"', re.S)
RE_AUTHOR = re.compile(r'href="https://www\.tgb\.cn/blog/(\d+)"[^>]*>([^<]{1,30})</a>')
RE_TIME = re.compile(r'class="related-sources">(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*([^<]{2,6})<')
RE_GIO = re.compile(r'id="gioMsg_T_(\d+)"\s+subject="([^"]*)"\s+userid="(\d+)"\s+username="([^"]*)"')
RE_LINK = re.compile(r'href="(https://www\.tgb\.cn/a/([A-Za-z0-9]+))[^"]*"[^>]*>([^<]{4,100})</a>')
RE_BODY = re.compile(r'class="related-body"[^>]*>\s*<span>(.*?)</span>', re.S)
RE_CNT = re.compile(r'赞\((\d+)\)\s*.*?浏览\((\d+)\)\s*.*?评论\((\d+)\)', re.S)


def _clean(s):
    s = re.sub(r'<[^>]+>', '', s or '')
    s = re.sub(r'【摘要】', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def fetch_stock_page(sess, code, page=1):
    url = "https://www.taoguba.com.cn/quotes/%s" % code
    if page > 1:
        url += "/?page=%d" % page
    r = sess.get(url, timeout=20)
    if r.status_code != 200:
        return None, r.status_code
    return r.text, 200


def parse_blocks(html, code):
    """拆 headImg 块并解析主帖/跟帖。返回 row 列表。"""
    rows = []
    parts = RE_BLOCK.split(html)
    for chunk in parts[1:]:
        m_time = RE_TIME.search(chunk)
        if not m_time:
            continue
        ts, kind_txt = m_time.group(1), m_time.group(2)
        kind = "post" if "主帖" in kind_txt else "reply"
        m_auth = RE_AUTHOR.search(chunk)
        author = m_auth.group(2).strip() if m_auth else ""
        uid = m_auth.group(1) if m_auth else ""

        title, content, tid, link = "", "", "", ""
        m_gio = RE_GIO.search(chunk)
        if m_gio:
            tid, title = m_gio.group(1), m_gio.group(2)
            m_link = RE_LINK.search(chunk)
            link = m_link.group(1).split("#")[0] if m_link else ""
        m_body = RE_BODY.search(chunk)
        if m_body:
            content = _clean(m_body.group(1))
        if kind == "reply" and not content:
            # 跟帖：正文是 related-sources 之后的纯文本块（容错取中段文本）
            seg = chunk[m_time.end():]
            seg = re.split(r'赞\(', seg)[0]
            content = _clean(seg)[:400]
        m_cnt = RE_CNT.search(chunk)
        praise, view, comment = (m_cnt.groups() if m_cnt else ("", "", ""))

        if kind == "post":
            rid = "tgb:T%s" % tid
        else:
            rid = "tgb:R" + hashlib.md5(
                ("%s|%s|%s" % (uid, ts, content[:80])).encode()).hexdigest()[:12]
        if not (title or content):
            continue
        rows.append({
            "id": rid, "code": code, "name": "",
            "ts": ts, "kind": kind, "author": author,
            "title": title[:120], "content": content[:800],
            "praise": praise, "view": view, "comment": comment,
            "link": link, "src": "tgb.cn",
        })
    return rows


def _load_existing(path):
    seen = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for ln in f:
                try:
                    seen.add(json.loads(ln)["id"])
                except Exception:
                    continue
    return seen


def judge_parse_empty(p1_ok, p1_empty, struct_bad, pct_limit=None,
                      min_sample=None):
    """判定本轮是否「静默空」。返回 (verdict, rc)；verdict 空串表示通过。

    两条独立判据：
      1. struct_bad > 0 —— 页面有帖块标记却解析 0 行，解析器已失效（不管占比）；
      2. 首页 0 行占比 > pct_limit 且样本 >= min_sample —— 大面积空，疑似挑战页。
    空页与解析失效必须分开：前者是「源端无帖」（正常），后者是「我们瞎了」（故障）。
    """
    pct_limit = PARSE_EMPTY_PCT if pct_limit is None else pct_limit
    min_sample = PARSE_MIN_SAMPLE if min_sample is None else min_sample
    if struct_bad:
        return ("STRUCT_MISMATCH %d 只（页面有帖块标记但解析 0 行，"
                "疑似模板变更）" % struct_bad, 2)
    if p1_ok >= min_sample and p1_empty and (p1_empty / p1_ok) > pct_limit:
        return ("PARSE_EMPTY %.0f%% > %.0f%%（首页 %d/%d 解析 0 行，"
                "疑似被挑战页/模板变更伪装成成功）"
                % (100.0 * p1_empty / p1_ok, 100.0 * pct_limit,
                   p1_empty, p1_ok), 2)
    return "", 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="?", default="", help="逗号分隔 6 位代码；缺省用队列")
    ap.add_argument("--queue", choices=["actionable", "all"], default="actionable")
    ap.add_argument("--pages", type=int, default=2, help="每标的翻页数")
    ap.add_argument("--parse-empty-pct", type=float, default=PARSE_EMPTY_PCT,
                    help="首页解析 0 行占比超过此值即判失败（默认 0.30）")
    ap.add_argument("--no-parse-guard", action="store_true",
                    help="关闭 parse_empty 护栏（仅排障用，正常采集勿关）")
    args = ap.parse_args()

    if args.codes:
        queue = [(c.strip(), "") for c in args.codes.split(",") if c.strip()]
    else:
        q = json.load(open(QUEUE_FILE, encoding="utf-8"))
        items = q.get("actionable") if args.queue == "actionable" else q.get("all")
        queue = [(it["code"].split(".")[0], it.get("name", "")) for it in items]

    os.makedirs(OUT_DIR, exist_ok=True)
    sess = requests.Session()
    sess.trust_env = False
    sess.headers.update({"User-Agent": UA, "Referer": "https://www.tgb.cn/"})

    t0 = time.time()
    tot_new, fails, blocked = 0, [], []
    # 护栏计数：p1_ok/p1_empty=首页请求与其中解析 0 行；struct_bad=有帖块却解析不出
    p1_ok, p1_empty, struct_bad = 0, 0, 0
    empty_codes = []
    for code, name in queue:
        got_new = 0
        try:
            path = os.path.join(OUT_DIR, "%s.jsonl" % code)
            seen = _load_existing(path)
            for pg in range(1, args.pages + 1):
                html, st = fetch_stock_page(sess, code, pg)
                if html is None:
                    fails.append("%s: HTTP %s p%d" % (code, st, pg))
                    break
                if "身份核实" in html[:3000] or len(html) < 8000:
                    blocked.append(code)
                    break
                if pg == 1:
                    p1_ok += 1
                rows = parse_blocks(html, code)
                if pg == 1 and not rows:
                    p1_empty += 1
                    empty_codes.append(code)
                    if RE_BLOCK.search(html):
                        # 页面里明明有帖块标记，却一行都解析不出 → 解析器失效（非空页）
                        struct_bad += 1
                        print("[tgb] %s 结构失配：headImg %d 个、解析 0 行（疑似模板变更）"
                              % (code, len(RE_BLOCK.findall(html))), flush=True)
                new = [r for r in rows if r["id"] not in seen]
                for r in new:
                    seen.add(r["id"])
                if new:
                    with open(path, "a", encoding="utf-8") as f:
                        for r in new:
                            f.write(json.dumps(r, ensure_ascii=False) + "\n")
                got_new += len(new)
                if len(rows) < 5:        # 尾页
                    break
                time.sleep(_GAP)
            tot_new += got_new
            print("[tgb] %s %-10s 新增 %d" % (code, name, got_new))
        except Exception as e:
            fails.append("%s: %s" % (code, str(e)[:70]))
        time.sleep(_GAP)
    pct = (100.0 * p1_empty / p1_ok) if p1_ok else 0.0
    print("[tgb] 完成: %d 标的, 新增 %d 条, 失败 %d, 拦截页 %s, "
          "首页解析0行 %d/%d (%.0f%%), 结构失配 %d, 耗时 %.0fs"
          % (len(queue), tot_new, len(fails), blocked or "-",
             p1_empty, p1_ok, pct, struct_bad, time.time() - t0))
    for m in fails[:6]:
        print("[tgb] FAIL %s" % m)

    # ── 判定：静默空必须显形，不能与「确实无新帖」同形
    verdict, rc = ("", 0)
    if not args.no_parse_guard:
        verdict, rc = judge_parse_empty(p1_ok, p1_empty, struct_bad,
                                        pct_limit=args.parse_empty_pct)
    if verdict:
        print("[tgb] PARSE_EMPTY_GUARD 判失败：%s | 0 行样本 %s"
              % (verdict, ",".join(empty_codes[:12]) or "-"), flush=True)
        return rc
    if p1_empty and p1_ok >= PARSE_MIN_SAMPLE:
        print("[tgb] parse_empty %d/%d (%.0f%%) 在阈值内——视为源端确无帖，"
              "非解析失效" % (p1_empty, p1_ok, pct), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
