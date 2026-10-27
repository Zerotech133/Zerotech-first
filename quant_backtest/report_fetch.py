# -*- coding: utf-8 -*-
"""券商研报正文结构化存档：pdf_url → 下载 → pypdf 抽文本 → JSONL 增量入库。

设计要点（盘前报告第七节不再依赖远端链接）：
- 只抓 tdx_info.db stock_report 给出的 pdf.dfcfw.com 直链；
- 按 url 去重增量：已入库跳过，失败不写（下次再试）；
- 正文限长 60000 字符；首页头部段落即可作「核心摘要」；
- 所有接口静默容错：抓取失败不影响报告生成。
"""

import io
import json
import os
import re
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
TEXTS_PATH = os.path.join(BASE, "reports", "report_texts.jsonl")
MAX_CHARS = 60000
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_lock = threading.Lock()


def load_texts():
    """读 JSONL → {url: record}。文件不存在/损坏返回空 dict。"""
    out = {}
    if not os.path.exists(TEXTS_PATH):
        return out
    try:
        with open(TEXTS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("url"):
                        out[rec["url"]] = rec
                except ValueError:
                    continue
    except OSError:
        return {}
    return out


def _pdf_text(url, timeout=20):
    """下载 PDF 并抽文本；返回 (text, pages) 或抛异常。
    2026-10-07: dfcfw EO_Bot 升级 TLS 指纹挑战（requests 全头组仍 988B <script>），
    实证解法 curl_cffi impersonate=chrome131（K266 反爬实验）。"""
    from curl_cffi import requests as creq
    from pypdf import PdfReader

    content = b""
    for attempt in (1, 2):
        r = creq.get(
            url,
            timeout=timeout,
            impersonate="chrome131",
            headers={"Referer": "https://data.eastmoney.com/"},
        )
        r.raise_for_status()
        if r.content[:4] == b"%PDF":
            content = r.content
            break
        if attempt == 1:
            time.sleep(3)
    if not content:
        raise ValueError("not a pdf response (anti-bot?)")
    if len(content) < 512:
        raise ValueError("pdf too small (%d bytes)" % len(content))
    reader = PdfReader(io.BytesIO(content))
    pages = []
    for pg in reader.pages:
        try:
            pages.append(pg.extract_text() or "")
        except Exception:
            pages.append("")
    text = "\n".join(pages)
    return text[:MAX_CHARS], len(reader.pages)


_TBL = (
    r"(走势比较|股票数据|市场数据|基本数据|基础数据|相关研究|"
    r"盈利预测与估值|总股本|总市值|当前股价|52周价格|日均成交)"
)
_ANCHOR = r"(投资要点|核心观点|点评[:：]|事件[:：]|业绩点评|投资建议[:：]|盈利预测[:：]|首次覆盖|维持「?买入|维持「?增持)"


def _clean_head(text, n=90):
    """研报观点摘要：多锚择优——选锚后紧跟正文（非表格）的观点段起。"""
    t = re.sub(r"\s+", "", text or "")
    # 剥离分析师署名/执业号/邮箱噪音
    t = re.sub(r"(证券)?分析师[:：][\u4e00-\u9fa5]{2,4}", "", t)
    t = re.sub(r"S\d{8,14}", "", t)
    t = re.sub(r"\[Table_\w+\]", "", t)
    t = re.sub(r"[A-Za-z0-9._%+\-]+[@＠][A-Za-z0-9.\-]+\.[A-Za-z]{2,4}", "", t)
    # 择优锚：锚后 200 字窗内 60 字内不撞表格词才可用
    best = -1
    for m in re.finditer(_ANCHOR, t):
        win = t[m.start() : m.start() + 260]
        mt = re.search(_TBL, win)
        if mt and mt.start() < 60:
            continue
        best = m.start()
        break
    if best >= 0:
        t = t[best:]
    else:
        # 无可用锚：从头剥封面噪音后取首个中文长句
        t = re.sub(r"^(请务必阅读|免责)[\u4e00-\u9fa5]{0,24}", "", t)
    # 尾部表格噪音截断
    m3 = re.search(_TBL, t)
    if m3 and m3.start() > 8:
        t = t[: m3.start()]
    return t[:n]


def fetch_missing(items, max_fetch=10, per_timeout=20):
    """增量抓取：items 为 layer6 rated 列表（含 url 字段）。
    返回本次新增条数；任何失败静默跳过。"""
    with _lock:
        done = load_texts()
        todo = [it for it in items if it.get("url") and it["url"] not in done][
            :max_fetch
        ]
        if not todo:
            return 0
        added = 0
        for it in todo:
            url = it["url"]
            try:
                text, pages = _pdf_text(url, timeout=per_timeout)
                if len(re.sub(r"\s+", "", text)) < 200:
                    raise ValueError("extracted text too short")
                rec = {
                    "url": url,
                    "code": it.get("code", ""),
                    "org": it.get("org", ""),
                    "rating": it.get("rating", ""),
                    "title": it.get("name", ""),
                    "date": it.get("date", ""),
                    "pages": pages,
                    "chars": len(re.sub(r"\s+", "", text)),
                    "summary": _clean_head(text),
                    "text": text,
                    "analysts": _extract_analysts(text, it.get("org", "")),
                    "post_perf": None,  # 走势追踪位：报告日后 N 日涨跌幅，待行情回填
                    "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                with open(TEXTS_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                added += 1
            except Exception:
                continue
        return added


_STOP = {
    "认为",
    "预计",
    "指出",
    "表示",
    "维持",
    "给予",
    "判断",
    "团队",
    "简介",
    "声明",
    "联系",
}
# 免责声明段「分析师：」后跟非人名文本的特征词
_STOP_SUB = (
    "声明",
    "承诺",
    "本报",
    "作者",
    "具有",
    "认为",
    "可靠",
    "形式",
    "工作",
    "报告",
    "编号",
    "研究",
    "撰写",
    "任何",
    "准确",
    "信息",
    "来源",
    "独立",
    "客观",
    "获得",
    "登记",
)
# 名字尾部粘连词（PDF 抽文本无空格导致）
_NAME_TAIL = re.compile(
    r"(电话|执业|登记|编号|证券|联系|证书|邮箱|热线"
    r"|客服|作者|电|证).*$"
)


def _clean_name(nm):
    # 版式B前缀粘连：标题尾字贴在名字前（深度报告王高展）
    nm = re.sub(r"^[告评究文点报述章]", "", nm)
    nm = _NAME_TAIL.sub("", nm)
    if not (2 <= len(nm) <= 4):
        return ""
    if nm in _STOP or any(w in nm for w in _STOP_SUB):
        return ""
    return nm


def _extract_analysts(text, org=""):
    """从研报正文提取分析师要素（姓名/执业号/邮箱）。
    典型版式（东财 PDF 抽文本后无空格）：
      证券分析师：唐旭霞证券分析师：孙树林…tangxx@guosen.com.cn…
      分析师：任春阳S1050521110006rency@cfsc.com.cn
    策略：名字/执业号/邮箱分别 findall，按序配对（研报排版顺序一致），
    名字去重保序、剥尾部粘连词并剔除免责声明误匹配。"""
    t = re.sub(r"\s+", "", text or "")
    names, seen = [], set()

    def _add(raw):
        nm = _clean_name(raw)
        if not nm:
            return
        for ex in names:  # 子串去重：「柏良」⊂「叶柏良」保留全名
            if nm in ex or ex in nm:
                return
        seen.add(nm)
        names.append(nm)

    # 版式A：分析师：王高展 / 证券分析师：唐旭霞（名字在后）
    for m in re.finditer(r"(?:证券)?分析师[:：]?([\u4e00-\u9fa5]{2,6})", t):
        _add(m.group(1))
    # 版式B：王高展（分析师）（名字在前，括号标注）
    for m in re.finditer(r"([\u4e00-\u9fa5]{2,4})[（(]分析师[）)]", t):
        _add(m.group(1))
    # 版式C：王晓萱SAC编号：S1340…（名字贴编号标签）
    for m in re.finditer(
        r"([\u4e00-\u9fa5]{2,4})"
        r"(?:SAC编号|证书编号|资格编号|执业编号)",
        t,
    ):
        _add(m.group(1))
    certs = re.findall(r"S\d{8,14}", t)
    mails = []
    for m in re.findall(r"[A-Za-z0-9._%+\-]+[@＠][A-Za-z0-9.\-]+\.[A-Za-z]{2,4}", t):
        rest = m
        while rest:
            mm = re.match(
                r"^[A-Za-z0-9._%+\-]*[@＠][A-Za-z0-9.\-]+"
                r"\.[A-Za-z]{2,4}",
                rest,
            )
            if not mm:
                break
            one = mm.group(0)
            # 剥电话前缀（0755-8198…tangxx@ → tangxx@）
            one = re.sub(r"^\d[\d\-]{6,}(?=[A-Za-z])", "", one)
            # 剥粘连执业号前缀（S0790…lixintian@ → lixintian@）
            one = re.sub(r"^[A-Za-z]?\d{8,14}(?=[A-Za-z])", "", one, count=1)
            if "@" in one:
                mails.append(one)
            rest = rest[mm.end() :]
            if "@" not in rest:
                break
    out = []
    for i, nm in enumerate(names[:4]):  # 一篇研报合作者一般 ≤4
        rec = {"name": nm, "org": org}
        if i < len(certs):
            rec["cert"] = certs[i]
        if i < len(mails):
            rec["email"] = mails[i]
        out.append(rec)
    return out


def backfill_fields(rebuild_summary=True, rebuild_analysts=False):
    """存量记录回填：analysts / post_perf 字段（幂等，可重复跑）。"""
    texts = load_texts()
    if not texts:
        return 0
    n = 0
    with open(TEXTS_PATH, "w", encoding="utf-8") as f:
        for u, rec in texts.items():
            if rebuild_summary:
                rec["summary"] = _clean_head(rec.get("text") or "")
            if rebuild_analysts or "analysts" not in rec:
                rec["analysts"] = _extract_analysts(
                    rec.get("text") or "", rec.get("org", "")
                )
                n += 1
            rec.setdefault("post_perf", None)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return n


def analyst_stats():
    """分析师聚合（知识图谱节点表）：
    {分析师: {org, reports: N, stocks: {code: [日期, 评级, 标题]…}}}"""
    g = {}
    for u, rec in load_texts().items():
        for a in rec.get("analysts") or []:
            nm = a.get("name")
            if not nm:
                continue
            node = g.setdefault(nm, {"orgs": set(), "reports": 0, "stocks": {}})
            if a.get("org"):
                node["orgs"].add(a["org"])
            if a.get("email"):
                node["email"] = a.get("email")
            if a.get("cert"):
                node["cert"] = a.get("cert")
            node["reports"] += 1
            node["stocks"].setdefault(rec.get("code") or "?", []).append(
                (
                    rec.get("date") or "",
                    rec.get("rating") or "",
                    (rec.get("title") or "")[:36],
                    u,
                    rec.get("post_perf") or {},
                )
            )
    for nm, node in g.items():
        node["orgs"] = sorted(node["orgs"])
    return g


def backfill_post_perf():
    """股评后走势回填：post_perf = {base_date, base_close, d1/d5/d10/d20}。
    基准=报告日（或其之前最近交易日）收盘；N=基准日后第 N 个交易日收盘涨幅%。
    幂等：K 线更新后重跑即可补全（如假期后）。返回更新条数。"""
    from chanlun_data import load_kline

    texts = load_texts()
    if not texts:
        return 0
    kcache = {}
    updated = 0
    for u, rec in texts.items():
        code, d = rec.get("code") or "", rec.get("date") or ""
        if not code or not d:
            continue
        bars = kcache.get(code)
        if bars is None:
            try:
                r = load_kline(code, "1d", 300)
                bars = [
                    (b.get("dt"), float(b["c"]))
                    for b in (r.get("bars") or [])
                    if b.get("dt") and b.get("c")
                ]
            except Exception:
                bars = []
            kcache[code] = bars
        if not bars:
            continue
        # 基准：报告日或其之前最近交易日
        i0 = -1
        for i, (dt, _) in enumerate(bars):
            if dt <= d:
                i0 = i
            else:
                break
        if i0 < 0:
            continue
        bclose = bars[i0][1]
        perf = {"base_date": bars[i0][0], "base_close": round(bclose, 3)}
        for n in (1, 5, 10, 20):
            j = i0 + n
            if j < len(bars):
                perf["d%d" % n] = round((bars[j][1] / bclose - 1) * 100, 2)
        if perf != rec.get("post_perf"):
            rec["post_perf"] = perf
            updated += 1
    if updated:
        with open(TEXTS_PATH, "w", encoding="utf-8") as f:
            for u, rec in texts.items():
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return updated


def fetch_missing_async(items, max_fetch=10):
    """daemon 线程后台抓取，不阻塞报告生成。"""
    t = threading.Thread(target=fetch_missing, args=(items, max_fetch), daemon=True)
    t.start()
    return t
