# -*- coding: utf-8 -*-
"""互动易（irm.cninfo.com.cn）官方问答定向采集器 —— P1。

数据源：深交所互动易，投资者提问 + 问答状态（零风控，requests 直通）。
定位：标的驱动采集的「官方问答」维度（TDX 无此维度，净新增）。

链路（2026-11-09 抓包定版）：
  1) POST /newircs/index/queryKeyboardInfo  data={keyWord: <代码或名称>}
     → data[].{stockCode, shortName, secid}     （注意参数名是 keyWord 大写 W）
  2) GET  /newircs/search/searchResult?stockCodes=<secid>_<code>&infoTypes=1,11
     &startDate=&endDate=&pageNum=&pageSize=&onlyAttentionCompany=2
     → data.results[]  提问记录（mainContent/pubDate/qaStatus/…）
  已知边界：keyword 全文搜索接口（/newircs/index/search POST）服务端忽略
  keyword（乱码=茅台同 total），勿用；回答正文不在列表接口中（v1 问题级，
  回答留 P1.1 详情接口）。

队列：news_collector/target_queue_chanlun.json（标的驱动）。
落盘：data/irm/{code6}.jsonl，行结构对齐 guba/xq_kol 的 jsonl 约定：
  {"id": "irm:<esId>", "code": "300308", "name": "中际旭创",
   "ts": "2026-11-09 10:30:00", "content": "<提问正文>",
   "q_status": 2, "praise": 0, "src": "irm.cninfo.com.cn"}

CLI：
  python irm_collector.py                # actionable 队列（65 只）
  python irm_collector.py --queue all    # 全量 489 只
  python irm_collector.py 300308,000423  # 指定代码
  python irm_collector.py --days 90      # 拉取窗口（默认 180 天）
独立自检：python irm_collector.py 300308 --days 30
"""
import argparse
import glob
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(HERE), "data", "irm")
QUEUE_FILE = os.path.join(HERE, "target_queue_chanlun.json")
ORG_CACHE = os.path.join(HERE, "irm_orgid.json")

BASE = "https://irm.cninfo.com.cn/newircs"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36")

_GAP = 1.2          # 请求间隔（秒）——官方站保持礼貌
_PAGE_SIZE = 30
_MAX_PAGES = 2      # 单标的最多翻 2 页（60 条/窗口）


def _session():
    s = requests.Session()
    s.trust_env = False
    s.headers.update({
        "User-Agent": UA,
        "Referer": "https://irm.cninfo.com.cn/",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    })
    return s


def _resolve_secid(s, code, cache):
    """代码 → secid（带本地缓存，一次解析终身有效）。"""
    if code in cache:
        return cache[code]
    r = s.post("%s/index/queryKeyboardInfo?_t=%d" % (BASE, int(time.time() * 1000)),
               data={"keyWord": code}, timeout=15)
    j = r.json()
    secid = None
    for it in (j.get("data") or []):
        if str(it.get("stockCode")) == code:
            secid = it.get("secid")
            break
    if secid:
        cache[code] = secid
    return secid


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


def fetch_company(s, code, secid, days, seen):
    """拉单家公司窗口内提问，返回 (rows, total, dropped)。

    行级护栏（2026-11-09 事故补丁）：secid 解析到错误公司时，服务端
    会忽略 stockCodes 过滤返回全市场问答流（曾污染 8 只北交所/沪市票，
    每文件 60 条异公司数据）。落盘前逐行断言 row.code == 目标 code，
    不一致即丢弃计数。
    """
    end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows, total, dropped = [], None, 0
    for pg in range(1, _MAX_PAGES + 1):
        r = s.get("%s/search/searchResult" % BASE, params={
            "stockCodes": "%s_%s" % (secid, code),
            "keywords": "", "infoTypes": "1,11",
            "startDate": start, "endDate": end,
            "pageNum": pg, "pageSize": _PAGE_SIZE,
            "onlyAttentionCompany": 2,
        }, timeout=15)
        data = (r.json().get("data") or {})
        total = data.get("totalRecord")
        results = data.get("results") or []
        for it in results:
            row_code = str(it.get("stockCode") or "")
            if row_code != str(code):
                dropped += 1        # 全市场流污染行：丢弃
                continue
            rid = "irm:%s" % it.get("esId", it.get("indexId"))
            if rid in seen:
                continue
            seen.add(rid)
            ts_ms = it.get("pubDate")
            try:
                ts = datetime.fromtimestamp(int(ts_ms) / 1000).strftime("%Y-%m-%d %H:%M:%S") if ts_ms else ""
            except (TypeError, ValueError, OSError):
                ts = ""
            upd_ms = it.get("updateDate")
            try:
                upd = datetime.fromtimestamp(int(upd_ms) / 1000).strftime("%Y-%m-%d %H:%M:%S") if upd_ms else ""
            except (TypeError, ValueError, OSError):
                upd = ""
            # 已答推断（P1.1 降级口径）：列表接口不含回答正文（服务端硬隔离），
            # 但回答发布会 bump 问题记录的 updateDate——upd 晚于 pub 10 分钟即视为已答。
            answered = 0
            if ts and upd:
                try:
                    answered = 1 if (datetime.strptime(upd, "%Y-%m-%d %H:%M:%S")
                                     - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                                     ).total_seconds() > 600 else 0
                except ValueError:
                    pass
            rows.append({
                "id": rid,
                "code": row_code,
                "name": it.get("companyShortName") or "",
                "ts": ts,
                "update_date": upd,
                "answered": answered,
                "content": (it.get("mainContent") or "").strip(),
                "q_status": it.get("qaStatus"),
                "praise": it.get("praiseCount") or 0,
                "src": "irm.cninfo.com.cn",
            })
        if len(results) < _PAGE_SIZE:
            break
        time.sleep(_GAP)
    return rows, total, dropped


def _load_queue(mode, codes_arg):
    if codes_arg:
        return [(c.strip(), "") for c in codes_arg.split(",") if c.strip()]
    q = json.load(open(QUEUE_FILE, encoding="utf-8"))
    items = q.get("actionable") if mode == "actionable" else q.get("all")
    return [(it["code"].split(".")[0], it.get("name", "")) for it in items]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="?", default="", help="逗号分隔代码；缺省用队列")
    ap.add_argument("--queue", choices=["actionable", "all"], default="actionable")
    ap.add_argument("--days", type=int, default=180, help="拉取窗口（天）")
    args = ap.parse_args()

    queue = _load_queue(args.queue, args.codes)
    os.makedirs(OUT_DIR, exist_ok=True)
    cache = {}
    if os.path.exists(ORG_CACHE):
        try:
            cache = json.load(open(ORG_CACHE, encoding="utf-8"))
        except Exception:
            cache = {}
    s = _session()
    t0 = time.time()
    tot_new, fails, resolves = 0, [], 0
    for code, name in queue:
        try:
            secid = _resolve_secid(s, code, cache)
            if not secid:
                fails.append("%s: secid 解析失败" % code)
                continue
            resolves += 1
            path = os.path.join(OUT_DIR, "%s.jsonl" % code)
            seen = _load_existing(path)
            rows, total, dropped = fetch_company(s, code, secid, args.days, seen)
            if rows:
                with open(path, "a", encoding="utf-8") as f:
                    for row in rows:
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
            tot_new += len(rows)
            warn = " [丢弃异公司 %d 行]" % dropped if dropped else ""
            print("[irm] %s %-10s total=%-6s 新增 %d%s" % (code, name, total, len(rows), warn))
        except Exception as e:
            fails.append("%s: %s" % (code, str(e)[:70]))
        time.sleep(_GAP)
    json.dump(cache, open(ORG_CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[irm] 完成: %d 标的, secid 解析 %d, 新增 %d 条, 失败 %d, 耗时 %.0fs"
          % (len(queue), resolves, tot_new, len(fails), time.time() - t0))
    for m in fails[:8]:
        print("[irm] FAIL %s" % m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
