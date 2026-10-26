# -*- coding: utf-8 -*-
"""S2 事件库 MVP：公告 -> PDF -> 文本 -> 分类 -> SQLite。

只读 tushare anns_d + 巨潮 PDF；不写 curated 仓库；缓存到 WORK/cache/pdf。
"""
import hashlib
import json
import os
import re
import sys
import time

from . import config, db

MATERIAL = re.compile(
    r"重整|预重整|重组|并购|发行股份|收购|重大资产|减持|增持|回购|质押|解除限售|解禁|"
    r"利润分配|分红|业绩预告|业绩快报|年度报告|半年度报告|季度报告|问询函|监管函|警示|立案|"
    r"诉讼|仲裁|限制性股票|股权激励|辞职|辞任|离任|聘任|中标|订单|重大合同|停产|复产|"
    r"风险警示|退市|异常波动|停牌|复牌|控制权|要约|终止|破产|清算|担保|关联交易|会计政策|审计"
)
AMOUNT = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(亿元|万元|元)")


def _ts_call(api, params):
    sys.path.insert(0, os.path.join(config.MAIN_ROOT, "quant_backtest"))
    import tushare_sync as T
    return T.ts_call(api, params)


def _download(url, path, timeout=30):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    with open(path, "wb") as f:
        f.write(data)
    return len(data)


def _pdf_text(path):
    try:
        from pypdf import PdfReader
        rd = PdfReader(path)
        txt = []
        for p in rd.pages[:40]:
            try:
                txt.append(p.extract_text() or "")
            except Exception:
                txt.append("")
        return "\n".join(txt)
    except Exception:
        return ""


def classify(title, text=""):
    # 只按标题分类：公告标题是法定披露摘要，正文常含"风险警示/退市"等模板句，会误分类
    for cat, pat in config.EVENT_RULES:
        if re.search(pat, title or ""):
            return cat
    return "其他"


def extract_facts(text):
    facts = {"amounts": [], "dates": []}
    for m in AMOUNT.finditer(text[:6000]):
        facts["amounts"].append(m.group(0))
    facts["amounts"] = list(dict.fromkeys(facts["amounts"]))[:8]
    return facts


def build_events(code, as_of, name="", max_pdf=12, fetch_pdf=True, end_ymd=None):
    """返回事件 list（含已入库），并写 SQLite。

    公告拉取到 end_ymd（默认今天），以便识别 as-of 之后发生的停牌/控制权变更等事件。
    """
    ymd = as_of.replace("-", "")
    start = str(int(ymd[:4]) - 1) + ymd[4:]
    end = end_ymd or time.strftime("%Y%m%d")
    try:
        r = _ts_call("anns_d", {"ts_code": code, "start_date": start, "end_date": max(end, ymd)})
    except Exception as e:
        return [], "anns_d 拉取失败: " + str(e)[:120]
    fields = r.get("fields") or []
    items = [dict(zip(fields, it)) for it in (r.get("items") or [])]

    pdf_dir = os.path.join(config.DATA_CACHE, "pdf", code.replace(".", "_"))
    os.makedirs(pdf_dir, exist_ok=True)
    events = []
    picked = 0
    for it in items:
        title = (it.get("title") or "").strip()
        if not title or not MATERIAL.search(title):
            continue
        d8 = str(it.get("ann_date") or "")
        date = "%s-%s-%s" % (d8[:4], d8[4:6], d8[6:8]) if len(d8) == 8 else as_of
        url = it.get("url") or ""
        eid = hashlib.md5((code + d8 + title).encode("utf-8")).hexdigest()[:16]
        pdf_path, text = "", ""
        if fetch_pdf and url and picked < max_pdf:
            pdf_path = os.path.join(pdf_dir, eid + ".pdf")
            try:
                if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) < 1000:
                    _download(url, pdf_path)
                text = _pdf_text(pdf_path)
            except Exception:
                text = ""
            picked += 1
        cat = classify(title, text)
        facts = extract_facts(text)
        events.append({"event_id": eid, "code": code, "date": date, "category": cat, "title": title,
                       "url": url, "pdf": pdf_path, "text_len": len(text), "confidence": 0.8 if text else 0.5,
                       "reviewed": False, "facts": facts})
    events.sort(key=lambda x: x["date"], reverse=True)
    seen, uniq = set(), []
    for e in events:
        key = e["title"][:40]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(e)
    events = uniq
    if events:
        db.upsert_events(events)
    note = "公告 %d 条，其中关键事件 %d 条（去重后），抽取 PDF %d 份" % (len(items), len(events), picked)
    return events, note


def load(code, limit=60):
    return db.load_events(code, limit)


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "600337.SH"
    ev, note = build_events(code, "2026-10-23")
    print(note)
    for e in ev[:10]:
        print(e["date"], e["category"], e["title"][:60], e["text_len"])
