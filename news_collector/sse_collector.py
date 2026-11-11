# -*- coding: utf-8 -*-
"""上证e互动采集器（P4）—— SSE 平台投资者问答，requests 直通。

端点（akshare 路线，解析自研——akshare 1.18.40 的 stock_sns_sseinfo 解析返回空行，弃用）：
  1) code->uid 映射：POST sns.sseinfo.com/allcompany.do  72 页全量遍历，缓存 uid_map.json
  2) 问答列表：POST sns.sseinfo.com/ajax/userfeeds.do  typeCode=company type=11 pageSize=100
     HTML 内 m_feed_txt 交替（问/答），m_feed_from 含发布时间

落盘：data/sse/{code6}.jsonl，行结构与 irm 同构：
  {"id","code","name","ts","content","reply_content","has_reply","src"}
行级护栏与 irm_collector 同款：落盘前断言行 code == 目标 code。

用法：
  python sse_collector.py "600930,600519"          # 指定票
  python sse_collector.py --queue sse              # 队列里沪市票
  python sse_collector.py --uidmap                 # 仅重建 uid 映射
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
import warnings

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(HERE), "data", "sse")
UID_MAP_PATH = os.path.join(OUT_DIR, "uid_map.json")
QUEUE_PATH = os.path.join(HERE, "target_queue_chanlun.json")

BASE = "https://sns.sseinfo.com"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36",
      "Referer": "https://sns.sseinfo.com/"}
_GAP = 1.2
_PAGE_SIZE = 100
_MAX_PAGES = 40  # 100条/页 x 40 = 4000 条上限


def _session():
    s = requests.Session()
    s.trust_env = False
    s.headers.update(UA)
    return s


def _load_queue():
    q = json.load(open(QUEUE_PATH, encoding="utf-8"))
    items = q.get("actionable") or q.get("all") or []
    out = []
    for r in items:
        code = str(r.get("code") or "").split(".")[0].zfill(6)
        if code[:2] in ("60", "68"):  # 沪市：60xxxx 主板 / 68xxxx 科创板
            out.append((code, r.get("name") or ""))
    return out


def build_uid_map(s, pages=80):
    """全量遍历 allcompany.do 建 code->uid 映射（约 70 页，一次性缓存）。"""
    uid_map = {}
    empty_streak = 0
    for page in range(1, pages + 1):
        r = s.post(BASE + "/allcompany.do",
                   data={"code": "0", "order": "2", "areaId": "0", "page": str(page)},
                   timeout=15)
        try:
            content = r.json().get("content", "")
        except Exception:
            content = ""
        # companyBox 块级解析：uid=NN ... title='名称' ... >600000<
        boxes = re.findall(r"uid=(\d+)[^>]*title='([^']+)'[^>]*>.*?<a href='user\.do\?uid=\d+'>(\d{6})</a>",
                           content, re.S)
        if not boxes:
            empty_streak += 1
            if empty_streak >= 3:
                break
        else:
            empty_streak = 0
        for uid, name, code6 in boxes:
            uid_map[code6] = {"uid": uid, "name": name.strip()}
        print("[uidmap] page=%d 累计=%d" % (page, len(uid_map)), flush=True)
        time.sleep(0.6)
    with open(UID_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(uid_map, f, ensure_ascii=False)
    return uid_map


def _stable_sse_id(code, ts, content):
    """A1② sse id 归一化：用 sha256 内容指纹生成确定性 id。

    旧实现用 Python 内置 hash()（按进程加盐、跨进程不稳定），导致同一帖子
    每次重抓得到不同 id，按 id 去重失效——每日重跑把全部历史帖重复追加
    （实测 600018.jsonl 共 32 行仅 8 行唯一）。改用 sha256 内容指纹：
      - 确定性：同 (code,ts,content) 永远得到同一 id，去重跨进程稳定；
      - 十六进制：绝不可能是连续 11 位数字（手机号形态），PHONE_CN 误报根治。
    """
    raw = "%s|%s|%s" % (code, ts or "", content or "")
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return "sse:%s:%s" % (code, h)


def _load_uid_map(s):
    if os.path.exists(UID_MAP_PATH):
        with open(UID_MAP_PATH, encoding="utf-8") as f:
            return json.load(f)
    return build_uid_map(s)


def fetch_company(s, code, uid):
    """拉单家公司全部问答（翻页穿透全历史）。
    结构：m_feed_item(id=item-N) 块内，问题=m_feed_txt（无 id，公司锚点前缀需剥），
    回答=m_feed_txt-N（带 id），日期只在问题块 m_feed_from。"""
    rows = []
    for page in range(1, _MAX_PAGES + 1):
        r = s.get(BASE + "/ajax/userfeeds.do",
                  params={"typeCode": "company", "type": "11",
                          "pageSize": "10", "uid": uid, "page": str(page)},
                  timeout=15)
        html = r.text
        blocks = re.split(r'<div class="m_feed_item" id="item-\d+">', html)[1:]
        if not blocks:
            break
        got = 0
        for b in blocks:
            qm = re.search(r'<div class="m_feed_txt">(.*?)</div>', b, re.S)
            am = re.search(r'<div class="m_feed_txt" id="m_feed_txt-\d+">(.*?)</div>', b, re.S)
            if not qm:
                continue
            q = re.sub(r'<a[^>]*>.*?</a>', "", qm.group(1), flags=re.S)
            q = re.sub(r"<[^>]+>", "", q)
            q = re.sub(r"\s+", " ", q).strip()
            if not q:
                continue
            a = ""
            if am:
                a = re.sub(r"<[^>]+>", " ", am.group(1))
                a = re.sub(r"\s+", " ", a).strip()
            dm = re.search(r'm_feed_from">.*?<span>(\d{4}年\d{2}月\d{2}日\s*\d{2}:\d{2})?', b, re.S)
            ts = ""
            if dm and dm.group(1):
                m2 = re.search(r"(\d{4})年(\d{2})月(\d{2})日\s*(\d{2}:\d{2})?", dm.group(1))
                if m2:
                    ts = "%s-%s-%s %s" % (m2.group(1), m2.group(2), m2.group(3), m2.group(4) or "")
            rows.append({"ts": ts, "content": q, "reply_content": a})
            got += 1
        if got == 0:
            break
        time.sleep(_GAP)
    # 组装行（A1②：id 用内容指纹确定性生成，跨进程稳定、去重不漂移）
    out = []
    for row in rows:
        rid = _stable_sse_id(code, row.get("ts", ""), row.get("content", ""))
        out.append({
            "id": rid,
            "code": code,
            "name": "",
            "ts": row.get("ts", ""),
            "content": row.get("content", ""),
            "reply_content": row.get("reply_content", ""),
            "has_reply": 1 if row.get("reply_content") else 0,
            "src": "sns.sseinfo.com",
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="?", default="", help="逗号分隔代码")
    ap.add_argument("--queue", action="store_true", help="跑队列里的沪市票")
    ap.add_argument("--uidmap", action="store_true", help="仅重建 uid 映射")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    s = _session()

    if args.uidmap:
        m = build_uid_map(s)
        print("uid 映射完成：%d 家" % len(m))
        return 0

    if args.queue:
        targets = _load_queue()
    else:
        targets = [(c.zfill(6), "") for c in args.codes.split(",") if c.strip()]
    if not targets:
        print("无目标（沪市过滤后为空？）")
        return 1

    umap = _load_uid_map(s)
    s.get(BASE + "/company.do?uid=519", timeout=15)  # 预热会话拿 cookie
    tot_new = 0
    for code, qname in targets:
        info = umap.get(code)
        if not info:
            print("[sse] %s 无 uid 映射（未开通 e互动或映射缺页），跳过" % code)
            continue
        uid = info["uid"]
        rows = fetch_company(s, code, uid)
        path = os.path.join(OUT_DIR, "%s.jsonl" % code)
        # 行级护栏：断言 code；去重按 id
        existing = set()
        existing_keys = set()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for ln in f:
                    try:
                        r = json.loads(ln)
                        existing.add(r["id"])
                        existing_keys.add((r.get("ts", ""), r.get("content", "")))
                    except Exception:
                        pass
        new_rows, dropped = [], 0
        for row in rows:
            row["name"] = info.get("name") or qname
            if str(row["code"]) != code:
                dropped += 1
                continue
            if row["id"] in existing:
                continue
            # A1②：内容键兜底，旧随机 id 文件重抓也不会重复追加
            if (row.get("ts", ""), row.get("content", "")) in existing_keys:
                continue
            new_rows.append(row)
        if new_rows:
            with open(path, "a", encoding="utf-8") as f:
                for row in new_rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
        tot_new += len(new_rows)
        n_ans = sum(r["has_reply"] for r in rows)
        print("[sse] %s %-8s uid=%-8s 拉 %d 条（答 %d）新增 %d 丢弃 %d"
              % (code, info.get("name", ""), uid, len(rows), n_ans, len(new_rows), dropped),
              flush=True)
        time.sleep(_GAP)
    print("[sse] 完成：新增 %d" % tot_new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
