# -*- coding: utf-8 -*-
"""AI 金融报告 — 本地 Ollama 客观归纳

两种模式:
  brief（默认，每日 08:00 盘前晨报）
    全球市场快照 + 近 14h 快讯 → 总览/主题/利多利空/今日关注
  review（传参 review，交易日 15:30 收盘复盘）
    收盘快照 + 当日 7h 快讯 → 大盘复盘/盘面驱动/自选股动态/明日关注

降级: 单个行情解析失败跳过；LLM 不可用时推送「行情+命中要闻」无分析版。
存储: news.db 表 ai_briefs / ai_reviews + runs；ai_brief.json / ai_review.json 供门户。
用法: python news_ai.py [review] [--dry]
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "news.db")
CFG_PATH = os.path.join(HERE, "news_config.json")
LOG_PATH = os.path.join(HERE, "logs", "ai.log")
CST = timezone(timedelta(hours=8))

OLLAMA = "http://127.0.0.1:11434/api/chat"
JEV_BASE = "https://api.typesafe.ai/v1"
JEV_ENV_PS1 = os.path.abspath(os.path.join(
    HERE, "..", "jev-eval", "jev-gate.env.ps1"))
SINA_HQ = "https://hq.sinajs.cn/list=%s"
SINA_CODES = [
    ("A股", "sh000001"), ("A股", "sz399001"), ("A股", "sz399006"), ("A股", "sh000300"),
    ("港股", "rt_hkHSI"),
    ("美股", "gb_dji"), ("美股", "gb_ixic"), ("美股", "gb_inx"),
    ("商品", "hf_CL"), ("商品", "hf_GC"),
    ("汇率", "fx_susdcny"),
]
GROUP_EMOJI = {"A股": "🇨🇳", "港股": "🇭🇰", "美股": "🇺🇸", "商品": "🛢",
               "汇率": "💱", "自选": "⭐"}
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
           "Referer": "https://finance.sina.com.cn"}

SYS_PROMPT = (
    "你是一名严谨的中文金融市场分析师，为个人投资者整理市场报告。"
    "规则：①只能依据用户提供的行情与新闻归纳，禁止编造未出现的数据或事件；"
    "②客观中立，不使用「必涨/抄底/闭眼买」等荐股式措辞；"
    "③信息不足处直接说「暂无明确信息」；④全部用简体中文，语言精炼。"
)

BRIEF_RULES = (
    "请严格按下面格式输出（不要输出其他内容）：\n"
    "【一句话总览】\n（一句话，40字以内，须同时点出市场表现与最重要的新闻主线）\n"
    "【关键主题】\n（3-6条，每条一行：序号. 主题 — 客观事实与可能影响方向）\n"
    "【对A股/港股的潜在影响】\n"
    "利多因素：（结合新闻与行情，列1-3条；确实没有才写「暂无明显新增利多」）\n"
    "利空因素：（结合新闻与行情，列1-3条，指数普跌/商品剧烈波动等行情事实须纳入；确实没有才写「暂无明显新增利空」）\n"
    "待观察：（1-3条，不得留空）\n"
    "【今日重点关注】\n（2-4条事件/数据/风险点；新闻中没有就写「无明确新增事项」）"
)

REVIEW_RULES = (
    "请严格按下面格式输出（不要输出其他内容）：\n"
    "【大盘复盘】\n（一句话，40字以内，概括今日A股/港股主要指数与行业板块表现、盘面情绪）\n"
    "【盘面驱动】\n（3-6条，每条一行：序号. 事件/主题 — 如何影响今日盘面或板块，"
    "须把新闻与指数/行业板块/商品涨跌对应起来）\n"
    "【明日关注】\n（2-4条，基于今日新闻与外盘提示明日需跟踪的事件、数据或风险）"
)
STOCK_RULE = (
    "\n另外，若「自选股相关」区块非空，在最后追加一节：\n"
    "【关注个股动态】\n（按股票名分条，客观转述相关新闻事实；没有新增信息的股票不要写）"
)

QUICK_RULES = (
    "以下是刚采集到的关注命中财经快讯。请用简体中文输出对市场的即时判断，"
    "不要输出任何其他内容，只输出一段 60~120 字的紧凑判断："
    "先给整体倾向（偏多/偏空/中性/分化），点出主要影响的市场"
    "（A股/港股/美股/商品/加密中有涉及的才写），再给一句核心逻辑；"
    "如有明显风险或分歧，用分号补一句。禁止荐股措辞，禁止编造未出现的信息。"
)


def quick_judge(model, items, timeout=90):
    """即时速报轻量市场判断：仅命中标题 → 60~120 字。失败返回 None（不影响推送）。"""
    if not items:
        return None
    news = "\n".join("· %s%s" % (
        ("【%s】" % "/".join(it["stocks"])) if it.get("stocks") else "",
        (it["title"] or "")[:60]) for it in items[:8])
    try:
        r = requests.post(OLLAMA, json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYS_PROMPT},
                {"role": "user", "content":
                    "== 关注命中快讯 ==\n" + news + "\n\n" + QUICK_RULES}],
            "stream": False, "think": False,
            "options": {"temperature": 0.3, "num_predict": 300},
        }, timeout=timeout)
        r.raise_for_status()
        text = r.json()["message"]["content"].strip()
        return text if 10 <= len(text) <= 400 else None
    except Exception as e:
        log("quick_judge FAIL %s" % str(e)[:100])
        return None


# ── JEV（TypeSafe.ai）结构化新闻判断 ──────────────────────
JEV_NEWS_QUESTIONS = {
    "bias": {
        "type": "choice",
        "instructions": "这条财经快讯对相关市场/资产下一交易日的短期影响方向。",
        "criteria": {
            "bull": "偏多：利好相关资产、板块或风险偏好",
            "bear": "偏空：利空相关资产、板块或压制风险偏好",
            "neutral": "中性：影响有限、双向对冲或方向不明",
        },
    },
    "scope": {
        "type": "choice",
        "instructions": "该快讯主要影响的市场（选最主要的一个）。",
        "criteria": {
            "cn": "A股/人民币资产",
            "hk": "港股",
            "us": "美股",
            "commodity": "商品（原油/黄金/工业金属等）",
            "crypto": "加密资产",
            "macro": "宏观全局/跨市场",
        },
    },
    # 坑: JEV TypeSafe 仅支持 choice 问题，text 类型 → 400 Invalid request
    #     （已实测）。判断依据由 jev_reason() 用本地 Ollama 单独生成。
}
_BIAS_CN = {"bull": "利多", "bear": "利空", "neutral": "中性"}
_SCOPE_CN = {"cn": "A股", "hk": "港股", "us": "美股", "commodity": "商品",
             "crypto": "加密", "macro": "宏观"}


def _jev_key():
    """JEV_API_KEY：环境变量优先，否则解析 jev-eval/jev-gate.env.ps1。"""
    key = os.environ.get("JEV_API_KEY", "")
    if key:
        return key
    import re
    try:
        with open(JEV_ENV_PS1, encoding="utf-8") as f:
            txt = f.read()
        m = re.search(r'JEV_API_KEY"\s*=\s*"([^"]+)"', txt) \
            or re.search(r'JEV_API_KEY\s*=\s*"([^"]+)"', txt)
        return m.group(1) if m else ""
    except Exception:
        return ""  # 豁免（C2批2）：与"未配置 key"同一出口，jev_judge_one 侧按无 key 跳过


def _jev_judge_one_cloud(title, stocks, timeout=15):
    """typesafe systemone 云判定（原 jev_judge_one 主体，K-STOCK-083 拆出）。

    K-STOCK-084（2026-10-10 451 根因定位）：CF 对大陆出口 geo-block，
    全端点 451（"not available in your region"，edge 层、key 校验之前）；
    走海外出口（本机 7890，SG）实测 systemone 200 全链路恢复 ⇒ typesafe
    请求统一带 proxies 出站。代理不可达时连接失败 → jev_judge_one 路由
    自动落本地回落（K-STOCK-083），链路不断，只退化。
    """
    key = _jev_key()
    if not key or not title:
        return None
    ts_proxy = os.environ.get("TS_PROXY", "http://127.0.0.1:7890")
    ts_proxies = {"https": ts_proxy, "http": ts_proxy}
    timeout = max(timeout, 25)   # 7890 首次建连偶发 >15s（K-STOCK-084 实测）
    tag = ("（关联标的：%s）" % "/".join(stocks)) if stocks else ""
    state = "以下是一条刚采集的财经快讯，请判断其市场影响。\n· %s%s" % (
        title[:80], tag)
    body = {"model": "jev-latest", "state": state,
            "questions": JEV_NEWS_QUESTIONS}
    try:
        r = requests.post(
            JEV_BASE + "/systemone", json=body, timeout=timeout,
            headers={"Authorization": "Bearer %s" % key},
            proxies=ts_proxies)
        r.raise_for_status()
        ans = (r.json() or {}).get("answers", {})
        bias = (ans.get("bias") or {}).get("choice", "")
        scope = (ans.get("scope") or {}).get("choice", "")
        probs = (ans.get("bias") or {}).get("probabilities") or {}
        conf = probs.get(bias)
        if conf is None:
            conf = (ans.get("bias") or {}).get("confidence", 0.0)
        if bias not in _BIAS_CN:
            return None
        return {"bias": bias, "bias_cn": _BIAS_CN[bias],
                "conf": round(float(conf or 0), 2),
                "scope": scope, "scope_cn": _SCOPE_CN.get(scope, ""),
                "reason": jev_reason(title, _BIAS_CN[bias],
                                     _SCOPE_CN.get(scope, scope))}
    except Exception as e:
        log("jev_judge FAIL %s" % str(e)[:100])
        return None


def jev_reason(title, bias_cn, scope_cn, timeout=20):
    """本地 Ollama 生成一句话判断依据（30 字内）。失败返回 ""（不阻塞）。"""
    model = ""
    try:
        with open(os.path.join(HERE, "news_config.json"),
                  encoding="utf-8") as f:
            model = (json.load(f) or {}).get("ai_model") or ""
    except Exception:
        pass  # 豁免（C2批2）：配置缺省→环境变量/默认模型兜底，常态容错
    if not model:
        model = os.environ.get("OLLAMA_MODEL", "qwen3:8b-8k")
    try:
        r = requests.post(OLLAMA, json={
            "model": model,
            "messages": [{"role": "user", "content":
                "财经快讯：%s\n判断：%s·%s。请用一句话（30字内）写出判断依据，"
                "只输出依据本身，不要解释。" % (title[:80], bias_cn, scope_cn)}],
            "stream": False, "think": False,
            "options": {"temperature": 0.2, "num_predict": 80},
        }, timeout=timeout)
        r.raise_for_status()
        return r.json()["message"]["content"].strip().replace("\n", " ")[:60]
    except Exception:
        return ""  # 豁免（C2批2）：docstring 声明——Ollama 判断依据失败返回空串不阻塞


# K-STOCK-083（2026-10-10）：本地回落接线。盲测门已过（86.7% ENABLE，
# docs/plans/2026-10-10_A3_jev情绪源本地回落预研.md §三）。
_CLOUD_FAIL_UNTIL = 0.0   # 云失败后此 epoch 前直接走本地，防 451 期每条空打探测


def jev_judge_one_local(title, stocks, timeout=60):
    """本地回落判定（qwen3.5:9b + format json，v2 资产价格口径）。

    prompt 口径与盲测 v2 逐字一致（a3_blindtest.py，一致率 86.7%、反向误判 0）。
    失败返回 None（与云路径同契约，绝不抛出）。
    """
    if not title:
        return None
    tag = ("（关联标的：%s）" % "/".join(stocks)) if stocks else ""
    prompt = (
        "财经快讯：%s%s\n判断该快讯对相关市场【资产价格】的短期影响方向，只输出 JSON："
        '{"bias":"bull|bear|neutral","conf":0.0到1.0}，不要输出其他内容。'
        "口径：只看对资产价格的直接影响——外交会晤、一般性产业与技术进展、常规信息披露、"
        "国债/政策性金融债发行供给等属 neutral；明确的需求扩张、超预期业绩、降息等利好才判 bull；"
        "明确的供给冲击、业绩暴雷、加息、制裁等才判 bear。" % (title[:100], tag))
    try:
        r = requests.post(OLLAMA, json={
            "model": os.environ.get("OLLAMA_MODEL", "qwen3.5:9b"),
            "messages": [{"role": "user", "content": prompt}],
            "stream": False, "think": False, "format": "json",
            "options": {"temperature": 0.1, "num_predict": 60},
        }, timeout=timeout)
        r.raise_for_status()
        j = json.loads(r.json()["message"]["content"])
        bias = (j.get("bias") or "").strip().lower()
        if bias not in _BIAS_CN:
            return None
        scope = (j.get("scope") or "").strip().lower()
        conf = min(max(float(j.get("conf") or 0.5), 0.0), 1.0)
        bias_cn, scope_cn = _BIAS_CN[bias], _SCOPE_CN.get(scope, scope or "")
        return {"bias": bias, "bias_cn": bias_cn, "conf": round(conf, 2),
                "scope": scope, "scope_cn": scope_cn, "src": "local",
                "reason": jev_reason(title, bias_cn, scope_cn)}
    except Exception as e:
        log("jev_judge_local FAIL %s" % str(e)[:100])
        return None


def jev_judge_one(title, stocks, timeout=15):
    """单条新闻 → JEV 判断 {"bias","bias_cn","conf","scope","scope_cn"[,"src"]}。

    路由：云优先（typesafe）；云失败起 300s 内后续条目直接走本地回落
    （防 451 期间每条空打两次探测）；回落失败返回 None（不抛，绝不影响推送）。
    """
    global _CLOUD_FAIL_UNTIL
    if time.time() >= _CLOUD_FAIL_UNTIL:
        r = _jev_judge_one_cloud(title, stocks, timeout)
        if r is not None:
            return r
        _CLOUD_FAIL_UNTIL = time.time() + 300
        log("jev_judge cloud FAIL -> local fallback for 300s")
    r = jev_judge_one_local(title, stocks)
    if r is not None:
        log("jev_judge LOCAL hit bias=%s conf=%s" % (r["bias"], r["conf"]))
    return r


def jev_judge_batch(items):
    """逐条判断（items 带 id/title/stocks），返回 {id: judge_dict}。
    条间 0.8s 防限流，失败重试一次。"""
    out = {}
    for i, it in enumerate(items[:8]):
        if i:
            time.sleep(0.8)
        j = jev_judge_one(it.get("title") or "", it.get("stocks") or [])
        if j is None:
            j = jev_judge_one(it.get("title") or "", it.get("stocks") or [])
        if j:
            out[it["id"]] = j
    return out


def jev_summary(db, hours=24):
    """近 N 小时已判断新闻的 JEV 分布文本（无数据返回空）。"""
    since = (now_cst() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    try:
        rows = db.execute(
            "SELECT jev FROM news WHERE jev IS NOT NULL AND jev!='' "
            "AND created_at>=?", (since,)).fetchall()
    except Exception as e:
        log("jev_summary 查询失败，JEV 分布降级为空: %s" % str(e)[:100])  # C2批2
        return ""
    bias_cnt, scope_cnt = {}, {}
    for (j,) in rows:
        try:
            d = json.loads(j)
        except Exception:
            continue  # 豁免（C2批2）：单条 jev 坏行跳过，不影响分布统计
        bias_cnt[d.get("bias", "?")] = bias_cnt.get(d.get("bias", "?"), 0) + 1
        sc = d.get("scope") or "?"
        scope_cnt[sc] = scope_cnt.get(sc, 0) + 1
    if not bias_cnt:
        return ""
    total = sum(bias_cnt.values())
    parts = ["%d 条已判：利多 %d / 利空 %d / 中性 %d" % (
        total, bias_cnt.get("bull", 0), bias_cnt.get("bear", 0),
        bias_cnt.get("neutral", 0))]
    for sc in ("cn", "us", "hk", "commodity", "crypto", "macro"):
        if scope_cnt.get(sc):
            parts.append("%s %d（多%d/空%d）" % (
                _SCOPE_CN[sc], scope_cnt[sc],
                sum(1 for (j,) in rows if _bias_of(j) == "bull"
                    and _scope_of(j) == sc),
                sum(1 for (j,) in rows if _bias_of(j) == "bear"
                    and _scope_of(j) == sc)))
    return " · ".join(parts)


def _bias_of(j):
    try:
        return json.loads(j).get("bias", "?")
    except Exception:
        return "?"  # 豁免（C2批2）：坏值按未知计，统计口径声明式兜底


def _scope_of(j):
    try:
        return json.loads(j).get("scope") or "?"
    except Exception:
        return "?"  # 豁免（C2批2）：坏值按未知计，统计口径声明式兜底


MODES = {
    "brief": {
        "title": "🤖 AI 金融晨报", "rules": BRIEF_RULES, "json": "ai_brief.json",
        "table": "ai_briefs", "kind": "ai", "win_cfg": "ai_window_hours", "win_def": 14,
        "span": "盘前",
    },
    "review": {
        "title": "📊 AI 收盘复盘", "rules": REVIEW_RULES, "json": "ai_review.json",
        "table": "ai_reviews", "kind": "review", "win_cfg": "review_window_hours", "win_def": 7,
        "span": "收盘",
    },
}


def log(msg):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("[%s] %s\n" % (datetime.now(CST).strftime("%m-%d %H:%M:%S"), msg))


def now_cst():
    return datetime.now(CST)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None  # 豁免（C2批2）：解析兜底，调用方按缺失值处理


def _pct(price, prev):
    if price is None or not prev:
        return None
    p = (price - prev) / prev * 100
    if abs(p) > 15:  # 合理性校验：异常值剔除
        return None
    return round(p, 2)


def fetch_markets(extra_codes=()):
    """单次请求拉全部行情，按代码类型解析；单项失败不影响其他。
    extra_codes: 自选股代码（追加为「自选」分组）。"""
    code_list = list(SINA_CODES) + [("自选", c) for c in extra_codes]
    r = requests.get(SINA_HQ % ",".join(c for _, c in code_list),
                     headers=HEADERS, timeout=12)
    r.raise_for_status()
    r.encoding = "gbk"
    raw = {}
    for line in r.text.strip().splitlines():
        if '="' not in line:
            continue
        code = line.split("var hq_str_", 1)[-1].split("=", 1)[0].strip()
        payload = line.split('="', 1)[-1].rstrip('";').split(",")
        raw[code] = payload

    out = []
    for grp, code in code_list:
        f = raw.get(code)
        if not f or len(f) < 9:
            continue
        rec = {"grp": grp, "name": code, "price": None, "pct": None, "dec": 2}
        try:
            if code.startswith(("sh", "sz", "bj")):
                rec.update(name=f[0], price=_f(f[3]), pct=_pct(_f(f[3]), _f(f[2])))
            elif code.startswith("rt_hk"):
                rec.update(name=f[1], price=_f(f[6]), pct=_f(f[8]), dec=3)
            elif code.startswith("gb_"):
                rec.update(name=f[0], price=_f(f[1]), pct=_f(f[2]))
            elif code.startswith("hf_"):
                rec.update(name=f[13], price=_f(f[0]), pct=_pct(_f(f[0]), _f(f[7])))
            elif code.startswith("fx_s"):
                rec.update(name=f[9], price=_f(f[8]), dec=4)
        except Exception as e:
            log("market parse %s FAIL %s" % (code, str(e)[:80]))
            continue
        if rec["price"] is not None:
            out.append(rec)
    return out


def format_markets(markets):
    lines, last_grp = [], None
    for m in markets:
        if m["grp"] != last_grp:
            lines.append("%s %s" % (GROUP_EMOJI.get(m["grp"], ""), m["grp"]))
            last_grp = m["grp"]
        arrow = ""
        if m["pct"] is not None:
            arrow = " ▲%+.2f%%" % m["pct"] if m["pct"] > 0 else (
                " ▼%.2f%%" % abs(m["pct"]) if m["pct"] < 0 else " 0.00%")
        lines.append("  %s %s%s" % (m["name"], ("%." + str(m["dec"]) + "f") % m["price"], arrow))
    return "\n".join(lines)


EM_SECTOR_URL = ("https://push2.eastmoney.com/api/qt/clist/get?pn=%d&pz=100&po=1"
                 "&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f12,f14,f3,f104,f105")
SINA_HY_URL = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"


def _sector_page(pn):
    """东财单页拉取：https 失败自动降级 http。"""
    url = EM_SECTOR_URL
    last = None
    for _ in range(2):
        try:
            r = requests.get(url % pn, headers=HEADERS, timeout=10)
            r.raise_for_status()
            return (r.json().get("data") or {}).get("diff") or []
        except Exception as e:
            last = e
            url = url.replace("https://", "http://")
    raise last


def _fetch_sectors_em():
    """东财全量行业板块（约500个，含细分）。失败抛异常。"""
    all_rows = []
    for pn in (1, 2, 3, 4, 5, 6):
        lst = _sector_page(pn)
        if not lst:
            break
        all_rows += lst
    if len(all_rows) < 50:
        raise ValueError("em sectors too few: %d" % len(all_rows))
    out = []
    for s in all_rows:
        name, pct = s.get("f14"), _f(s.get("f3"))
        if not name or pct is None:
            continue
        out.append({"name": name, "pct": pct,
                    "up": int(s.get("f104") or 0), "down": int(s.get("f105") or 0)})
    return out


def _fetch_sectors_sina():
    """新浪行业板块（约60个一级行业，降级源）。失败抛异常。"""
    import re
    r = requests.get(SINA_HY_URL, headers=HEADERS, timeout=12)
    r.raise_for_status()
    r.encoding = "gbk"
    m = re.search(r"=\s*(\{.*\})\s*;?\s*$", r.text.strip(), re.S)
    if not m:
        raise ValueError("sina hy format changed")
    data = json.loads(m.group(1))
    out = []
    for v in data.values():
        p = str(v).split(",")
        if len(p) < 6:
            continue
        name, pct = p[1], _f(p[5])
        if name and pct is not None:
            out.append({"name": name, "pct": pct, "up": 0, "down": 0})
    if len(out) < 20:
        raise ValueError("sina sectors too few: %d" % len(out))
    return out


def fetch_sectors():
    """行业板块涨跌：东财主源（含细分）→ 新浪降级源。失败返回 []。"""
    try:
        out = _fetch_sectors_em()
        log("sectors em ok=%d" % len(out))
    except Exception as e:
        log("sectors em FAIL %s -> sina" % str(e)[:100])
        try:
            out = _fetch_sectors_sina()
            log("sectors sina ok=%d" % len(out))
        except Exception as e2:
            log("sectors sina FAIL %s" % str(e2)[:100])
            out = []
    out.sort(key=lambda x: x["pct"], reverse=True)
    return out


def format_sectors(sectors, top_n=6):
    """板块热力文本：涨跌广度 + 领涨/领跌。"""
    if not sectors:
        return ""
    up_cnt = sum(1 for s in sectors if s["pct"] > 0)
    dn_cnt = sum(1 for s in sectors if s["pct"] < 0)
    breadth = "上涨占比 %d%%" % round(up_cnt * 100 / len(sectors))
    mood = "普涨" if up_cnt * 3 > len(sectors) * 2 else (
        "普跌" if up_cnt * 3 < len(sectors) else "分化")
    lines = ["全行业 %d 个：%d 涨 / %d 跌（%s，%s）" % (
        len(sectors), up_cnt, dn_cnt, breadth, mood)]
    for tag, grp in (("领涨", sectors[:top_n]),
                     ("领跌", list(reversed(sectors))[:top_n])):
        lines.append("%s：%s" % (tag, "，".join(
            "%s %+.2f%%" % (s["name"], s["pct"]) for s in grp)))
    return "\n".join(lines)


def fetch_news(db, window_hours, limit):
    since = (now_cst() - timedelta(hours=window_hours)).strftime("%Y-%m-%d %H:%M")
    rows = db.execute(
        "SELECT source, ts, title, hit, COALESCE(stocks,'') FROM news "
        "WHERE created_at>=? ORDER BY created_at DESC, ts DESC LIMIT ?",
        (since, limit)).fetchall()
    out = []
    for r in reversed(rows):
        try:
            stocks = json.loads(r[4]) if r[4] else []
        except Exception:
            stocks = []
        out.append(dict(source=r[0], ts=r[1], title=r[2], hit=r[3], stocks=stocks))
    return out


def format_news(items):
    src_name = {"wallstreetcn": "见闻", "sina": "新浪", "eastmoney": "东财"}
    lines = []
    for i, it in enumerate(items, 1):
        star = "⭐" if it["hit"] else "  "
        tag = ("【%s】" % "/".join(it["stocks"])) if it.get("stocks") else ""
        lines.append("%d. %s[%s %s]%s %s" % (
            i, star, src_name.get(it["source"], it["source"]),
            (it["ts"] or "")[-11:], tag, (it["title"] or "")[:70]))
    return "\n".join(lines)


def format_stock_block(items, watchlist):
    """按自选股标准名聚合相关新闻。"""
    names = [w.get("name") for w in watchlist if w.get("name")]
    if not names:
        return ""
    lines = []
    for nm in names:
        rel = [it for it in items if nm in (it.get("stocks") or [])]
        if not rel:
            continue
        lines.append("◆ %s" % nm)
        for it in rel[-4:]:
            lines.append("  · %s" % (it["title"] or "")[:70])
    return "\n".join(lines)


def call_llm(model, market_text, news_text, stock_text, rules, sector_text=""):
    blocks = ["以下是市场行情快照与财经快讯，请生成报告。\n\n"
              "== 行情快照 ==\n" + market_text]
    if sector_text:
        blocks.append("== 行业板块热力（当日涨跌）==\n" + sector_text)
    blocks.append("== 财经快讯（⭐为关注命中）==\n" + news_text)
    if stock_text:
        blocks.append("== 自选股相关 ==\n" + stock_text)
    user = "\n\n".join(blocks) + "\n\n" + rules + (STOCK_RULE if stock_text else "")
    r = requests.post(OLLAMA, json={
        "model": model,
        "messages": [{"role": "system", "content": SYS_PROMPT},
                     {"role": "user", "content": user}],
        "stream": False, "think": False,
        "options": {"temperature": 0.3, "num_predict": 1100},
    }, timeout=600)
    r.raise_for_status()
    text = r.json()["message"]["content"].strip()
    if "【" not in text:
        raise ValueError("unexpected llm output format")
    return text


def fallback_brief(news_items):
    lines = ["【提示】", "（本地 AI 模型暂不可用，以下为关注命中原文）", "",
             "【⭐ 关注命中】"]
    hits = [n for n in news_items if n["hit"]]
    for n in (hits or news_items[-8:]):
        tag = ("【%s】" % "/".join(n["stocks"])) if n.get("stocks") else ""
        lines.append("· %s%s" % (tag, (n["title"] or "")[:70]))
    if not hits and not news_items:
        lines.append("暂无新闻数据")
    return "\n".join(lines)


def atomic_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def push_message(text):
    tmp = os.path.join(HERE, "outbox", "pending_ai.txt")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    r = subprocess.run([sys.executable, os.path.join(HERE, "send_feishu.py"), tmp],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    out = (r.stdout or "").strip()
    log("push -> " + out)
    return out.startswith("SEND_OK")


def run(mode, dry):
    m = MODES[mode]
    cfg = json.load(open(CFG_PATH, encoding="utf-8"))
    model = cfg.get("ai_model", "qwen3:8b-8k")
    window = int(cfg.get(m["win_cfg"], m["win_def"]))
    limit = int(cfg.get("ai_max_news", 50))
    watchlist = cfg.get("watchlist", [])

    db = sqlite3.connect(DB)
    db.execute(
        "CREATE TABLE IF NOT EXISTS ai_briefs ("
        "date TEXT PRIMARY KEY, ts TEXT, model TEXT, ok INTEGER, "
        "markets TEXT, brief TEXT, pushed INTEGER DEFAULT 0)")
    db.execute(
        "CREATE TABLE IF NOT EXISTS ai_reviews ("
        "date TEXT PRIMARY KEY, ts TEXT, model TEXT, ok INTEGER, "
        "markets TEXT, brief TEXT, pushed INTEGER DEFAULT 0)")
    try:
        db.execute("ALTER TABLE news ADD COLUMN stocks TEXT")
    except sqlite3.OperationalError:
        pass  # 豁免（C2批2）：幂等迁移，列已存在即此异常，属预期
    try:
        db.execute("ALTER TABLE news ADD COLUMN jev TEXT")
    except sqlite3.OperationalError:
        pass  # 豁免（C2批2）：幂等迁移，列已存在即此异常，属预期

    try:
        from quote_snapshot import normalize_code
    except Exception:
        def normalize_code(c):
            return str(c).strip().lower()
    extra_codes = []
    for w in watchlist:
        c = normalize_code(w.get("code", ""))
        if c:
            extra_codes.append(c)

    try:
        markets = fetch_markets(extra_codes)
    except Exception as e:
        log("markets FAIL %s" % str(e)[:120])
        markets = []
    market_text = format_markets(markets) if markets else "（行情接口暂不可用）"

    sectors = []
    if mode == "review":
        try:
            sectors = fetch_sectors()
        except Exception as e:
            log("sectors FAIL %s" % str(e)[:120])
    sector_text = format_sectors(sectors) if sectors else ""
    if sectors:  # 落盘供 /bt-sectors 独立页渲染
        try:
            with open(os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "sectors.json"), "w", encoding="utf-8") as f:
                json.dump({"ts": now_cst().strftime("%Y-%m-%d %H:%M") + " 北京",
                           "count": len(sectors),
                           "up": sum(1 for s in sectors if s["pct"] > 0),
                           "items": sectors}, f, ensure_ascii=False)
        except Exception as e:
            log("sectors.json 落盘失败（/bt-sectors 页将 stale）: %s" % str(e)[:100])  # C2批2

    news_items = fetch_news(db, window, limit)
    news_text = format_news(news_items) if news_items else "（时间窗内暂无新闻）"
    stock_text = format_stock_block(news_items, watchlist)
    jev_text = jev_summary(db, 24)
    log("%s start markets=%d news=%d stocks_hits=%d model=%s" % (
        mode, len(markets), len(news_items),
        sum(1 for n in news_items if n.get("stocks")), model))

    ai_ok = 1
    try:
        brief = call_llm(model, market_text, news_text, stock_text,
                         m["rules"], sector_text)
    except Exception as e:
        ai_ok = 0
        brief = fallback_brief(news_items)
        log("llm FAIL %s -> fallback" % str(e)[:150])

    today = now_cst().strftime("%Y-%m-%d")
    header = "%s · %s（%s %d条快讯/%dh）" % (
        m["title"], now_cst().strftime("%m-%d"), m["span"], len(news_items), window)
    msg_parts = [header, "", "▍全球市场速览", market_text]
    if sector_text:
        msg_parts += ["", "▍行业板块热力", sector_text]
    if jev_text:
        msg_parts += ["", "▍JEV 新闻判断（近24h）", jev_text]
    msg_parts += ["", brief, "",
                  "—— 本地 %s 客观归纳 · 仅信息参考，不构成投资建议 · 信息采集域" % model]
    msg = "\n".join(msg_parts)

    sent = False
    if not dry:
        try:
            from card_build import build_card, push_card
            portal = cfg.get("portal_url", "")
            md_blocks = ["### ▍全球市场速览\n" + market_text]
            if sector_text:
                md_blocks.append("### ▍行业板块热力\n" + sector_text)
            if jev_text:
                md_blocks.append("### ▍JEV 新闻判断（近24h）\n" + jev_text)
            md_blocks.append(brief)
            doc = cfg.get("doc_url", "")
            btns = []
            if doc:
                btns.append(("📄 详情文档", doc, "default"))
            if portal:
                btns.append(("🚦 明日信号·内网", portal + "bt-signals", "default"))
                if jev_text:
                    btns.append(("⚖️ JEV 判断",
                                 cfg.get("jev_doc_url") or (portal + "bt-jev"),
                                 "primary"))
                if mode == "review" and sector_text:
                    btns.append(("🔥 板块热力·内网", portal + "bt-sectors", "default"))
                btns.append(("🖥 门户控制台·内网", portal + "#console", "default"))
            card = build_card(
                m["title"],
                "%s（%s %d条快讯/%dh）" % (m["span"],
                                           now_cst().strftime("%m-%d"),
                                           len(news_items), window),
                "turquoise" if mode == "brief" else "carmine",
                md_blocks,
                footer="本地 %s 客观归纳 · 仅信息参考，不构成投资建议 · 信息采集域"
                       % model,
                buttons=btns or None)
            sent = push_card(card, qr_portal=portal or None)
            if not sent:
                log("card FAIL -> text fallback")
        except Exception as e:
            log("card build FAIL %s" % str(e)[:120])
    sent = sent or push_message(msg) if not dry else True

    db.execute(
        "INSERT OR REPLACE INTO %s(date,ts,model,ok,markets,brief,pushed) "
        "VALUES(?,?,?,?,?,?,?)" % m["table"],
        (today, now_cst().strftime("%Y-%m-%d %H:%M"), model, ai_ok,
         json.dumps(markets, ensure_ascii=False), brief, int(bool(sent))))
    db.execute("INSERT INTO runs(kind,ts,ok,new_count,push_count,detail) VALUES(?,?,?,?,?,?)",
               (m["kind"], now_cst().strftime("%Y-%m-%d %H:%M"), ai_ok,
                len(news_items), int(bool(sent)),
                "markets=%d llm_ok=%d" % (len(markets), ai_ok)))
    db.commit()
    db.close()

    if sent and not dry:  # 同步刷新飞书简报文档（失败静默，不影响推送）
        try:
            import news_doc_board
            log("doc_board refresh %s"
                % ("OK" if news_doc_board.refresh() else "FAIL"))
        except Exception as e:
            log("doc_board refresh ERR %s" % str(e)[:100])
        try:  # 外网图表卡（后台跑，不阻塞晨报链路）
            # C2批3: 子进程 print 原先无通道全丢（含 JEV_CHART pushed/WECOM_FAIL），
            # 统一重定向 C:\Temp\news_jev_chart_out.log（对齐 alert_push 等启动器惯例）
            _jc_log = open(r"C:\Temp\news_jev_chart_out.log", "ab")
            subprocess.Popen(
                [sys.executable,
                 os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "news_jev_chart.py")],
                stdout=_jc_log, stderr=subprocess.STDOUT,
                creationflags=0x08000000)  # CREATE_NO_WINDOW
        except Exception as e:
            log("news_jev_chart 启动失败，本期图表不更新: %s" % str(e)[:100])  # C2批2
        try:  # 微信渠道：晨报/复盘 markdown 摘要（未配置自动跳过）
            import send_wecom
            md = "**%s · %s**\n" % (
                "🤖 AI 金融晨报" if mode == "brief" else "🤖 AI 收盘复盘",
                now_cst().strftime("%m-%d %H:%M"))
            if jev_text:
                md += "JEV 判断：%s\n" % jev_text
            md += "\n" + brief[:1500]
            send_wecom.send_markdown(md)
        except Exception as e:
            log("wecom 摘要推送失败（未配置走 send_wecom 内部跳过，异常即真失败）: %s"
                % str(e)[:100])  # C2批2

    headline = ""
    for ln in brief.splitlines():
        if ln.strip() and not ln.startswith("【"):
            headline = ln.strip()
            break
    payload = {
        "kind": mode, "date": today, "ts": now_cst().strftime("%Y-%m-%d %H:%M"),
        "model": model, "ok": bool(ai_ok), "pushed": bool(sent),
        "news_count": len(news_items), "market_count": len(markets),
        "headline": headline[:120],
    }
    if sectors:
        up_cnt = sum(1 for s in sectors if s["pct"] > 0)
        payload["sectors"] = {
            "total": len(sectors), "up": up_cnt, "down": len(sectors) - up_cnt,
            "top": sectors[:6],
            "bottom": list(reversed(sectors))[:6],
        }
    atomic_json(os.path.join(HERE, m["json"]), payload)
    log("%s done llm_ok=%d sent=%s" % (mode, ai_ok, sent))
    print("AI_%s %s llm_ok=%d markets=%d news=%d" % (
        mode.upper(), "SENT" if sent else "FAIL", ai_ok, len(markets), len(news_items)))
    if dry:
        print("=" * 60)
        print(msg)


def main():
    mode = "review" if "review" in sys.argv else "brief"
    run(mode, "--dry" in sys.argv)


if __name__ == "__main__":
    main()
