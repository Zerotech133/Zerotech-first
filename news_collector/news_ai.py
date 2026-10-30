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
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "news.db")
CFG_PATH = os.path.join(HERE, "news_config.json")
LOG_PATH = os.path.join(HERE, "logs", "ai.log")
CST = timezone(timedelta(hours=8))

OLLAMA = "http://127.0.0.1:11434/api/chat"
# 2026-10-25 EXEC-R5/EXEC-R6：NUM_PARALLEL=1 时 M3 批处理占满唯一 slot，
# 20s 内后端排不到 → 客户端主动 abort，Ollama 侧记 `POST /api/chat` 500
# （tuned.log: "aborting completion request due to client closing the connection"）。
# 本地 LLM 短请求统一用 45s：把「批处理排队」与「服务端真错误」区分开，
# 不再让排队超时伪装成服务端 500（健康卡 ai.error_rate 由此长期误红）。
LLM_TIMEOUT_S = 45
JEV_BASE = "https://api.typesafe.ai/v1"
JEV_ENV_PS1 = os.path.abspath(os.path.join(HERE, "..", "jev-eval", "jev-gate.env.ps1"))
SINA_HQ = "https://hq.sinajs.cn/list=%s"
SINA_CODES = [
    ("A股", "sh000001"),
    ("A股", "sz399001"),
    ("A股", "sz399006"),
    ("A股", "sh000300"),
    ("港股", "rt_hkHSI"),
    ("美股", "gb_dji"),
    ("美股", "gb_ixic"),
    ("美股", "gb_inx"),
    ("商品", "hf_CL"),
    ("商品", "hf_GC"),
    ("汇率", "fx_susdcny"),
]
GROUP_EMOJI = {
    "A股": "🇨🇳",
    "港股": "🇭🇰",
    "美股": "🇺🇸",
    "商品": "🛢",
    "汇率": "💱",
    "自选": "⭐",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://finance.sina.com.cn",
}

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
    "【今日重点关注】\n（2-4条事件/数据/风险点；新闻中没有就写「无明确新增事项」）\n"
    # K-ORG-161（M4）：引用溯源——claim 必须带快讯编号出处，无对应快讯写明
    "【引用规则】关键主题/今日重点关注 每条后标注（来源 #序号，序号=上方财经快讯编号）；"
    "确实无法对应任何快讯的（如纯行情驱动）写「暂无明确信息来源」。"
)

REVIEW_RULES = (
    "请严格按下面格式输出（不要输出其他内容）：\n"
    "【大盘复盘】\n（一句话，40字以内，概括今日A股/港股主要指数与行业板块表现、盘面情绪）\n"
    "【盘面驱动】\n（3-6条，每条一行：序号. 事件/主题 — 如何影响今日盘面或板块，"
    "须把新闻与指数/行业板块/商品涨跌对应起来）\n"
    "【明日关注】\n（2-4条，基于今日新闻与外盘提示明日需跟踪的事件、数据或风险）\n"
    # K-ORG-161（M4）：引用溯源
    "【引用规则】盘面驱动/明日关注 每条后标注（来源 #序号，序号=上方财经快讯编号）；"
    "确实无法对应任何快讯的（如纯行情驱动）写「暂无明确信息来源」。"
)
STOCK_RULE = (
    "\n另外，若「自选股相关」区块非空，在最后追加一节：\n"
    "【关注个股动态】\n（按股票名分条，客观转述相关新闻事实；没有新增信息的股票不要写）\n"
    "（关注个股动态每一条后标注（来源 #序号）；无对应快讯写「暂无明确信息来源」）"
)

QUICK_RULES = (
    "以下是刚采集到的关注命中财经快讯。请用简体中文输出对市场的即时判断，"
    "不要输出任何其他内容，只输出一段 60~120 字的紧凑判断："
    "先给整体倾向（偏多/偏空/中性/分化），点出主要影响的市场"
    "（A股/港股/美股/商品/加密/外汇中有涉及的才写），再给一句核心逻辑；"
    "如有明显风险或分歧，用分号补一句。禁止荐股措辞，禁止编造未出现的信息。"
)


def quick_judge(model, items, timeout=90):
    """即时速报轻量市场判断：仅命中标题 → 60~120 字。失败返回 None（不影响推送）。"""
    if not items:
        return None
    news = "\n".join(
        "· %s%s"
        % (
            ("【%s】" % "/".join(it["stocks"])) if it.get("stocks") else "",
            (it["title"] or "")[:60],
        )
        for it in items[:8]
    )
    try:
        r = requests.post(
            OLLAMA,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYS_PROMPT},
                    {
                        "role": "user",
                        "content": "== 关注命中快讯 ==\n" + news + "\n\n" + QUICK_RULES,
                    },
                ],
                "stream": False,
                "think": False,
                "options": {"temperature": 0.3, "num_predict": 300},
            },
            timeout=timeout,
        )
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
            "fx": "外汇（汇率/美元指数/央行干预等）",
            "macro": "宏观全局/跨市场",
        },
    },
    # K-ORG-159（M1）：事件类型（借鉴 FEED 金融事件抽取——事件维度是
    # 舆情分布监控最强载体）。choice 枚举，兼容 TypeSafe 限制。
    "event_type": {
        "type": "choice",
        "instructions": "这条财经快讯所属的事件类别（选最相关的一个）。",
        "criteria": {
            "earnings": "业绩/财报",
            "policy": "政策/监管",
            "rate": "利率/货币",
            "trade": "贸易/关税",
            "supply": "供给冲击",
            "demand": "需求扩张",
            "price": "价格调整",
            "ma": "并购/重组",
            "equity": "股权/回购/分红",
            "credit": "债务/信用",
            "risk": "诉讼/处罚/风险事件",
            "tech": "技术/研发进展",
            "macro": "宏观数据",
            "geo": "地缘/国际",
            "other": "其他",
        },
    },
    # K-ORG-173（承接 K-ORG-169）：事件冲击力，供 CAR 实证分层。
    "impact": {
        "type": "choice",
        "instructions": "这条快讯对相关资产价格的预期冲击强度（选最主要的一个）。",
        "criteria": {
            "low": "低：信息有限、影响短暂或已被市场充分预期",
            "medium": "中：可能影响下一交易日，但幅度和持续性有限",
            "high": "高：重大政策、业绩突变、信用/供给冲击等可能引发显著重定价",
        },
    },
    # 坑: JEV TypeSafe 仅支持 choice 问题，text 类型 → 400 Invalid request
    #     （已实测）。判断依据由 jev_reason() 用本地 Ollama 单独生成。
}
_BIAS_CN = {"bull": "利多", "bear": "利空", "neutral": "中性"}
_SCOPE_CN = {
    "cn": "A股",
    "hk": "港股",
    "us": "美股",
    "commodity": "商品",
    "crypto": "加密",
    "fx": "外汇",
    "macro": "宏观",
}
# 2026-10-26 OS-FIX-JEVSCOPE（JEV 审计 §四-①）：本地判读 prompt 原先没有 scope，
# 新判行 scope 恒空，而老云端行用 cn/hk/us/commodity/crypto/fx/macro 七值。
# 这里按既有 _SCOPE_CN 枚举做别名归一；未知值降级为空串（不猜、不伪造）。
_SCOPE_ALIASES = {
    "cn": "cn",
    "a股": "cn",
    "a-share": "cn",
    "ashare": "cn",
    "china": "cn",
    "中国": "cn",
    "国内": "cn",
    "hk": "hk",
    "港股": "hk",
    "hongkong": "hk",
    "hong kong": "hk",
    "us": "us",
    "美股": "us",
    "usa": "us",
    "美国": "us",
    "us-stock": "us",
    "commodity": "commodity",
    "commodities": "commodity",
    "商品": "commodity",
    "大宗商品": "commodity",
    "期货": "commodity",
    "crypto": "crypto",
    "加密货币": "crypto",
    "加密": "crypto",
    "fx": "fx",
    "外汇": "fx",
    "汇率": "fx",
    "forex": "fx",
    "macro": "macro",
    "宏观": "macro",
}


def _normalize_scope(value):
    """把本地模型返回的 scope 归一到既有七值枚举；未知/空 → ''（不伪造）。"""
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    return _SCOPE_ALIASES.get(raw, "")


# K-ORG-191：fx（外汇）为郭嘉预判对账司扩容的新市场枚举——自扩容日起
# 积累信号（历史无 fx 数据，不回溯），对账基准 fx_susdcnh（美元/离岸人民币）。
# K-ORG-157：低置信阈值与时效衰减（实证支撑 K-ORG-156——0.5-0.7 段
# 方向命中仅 32-43%，0.7+ 才稳定在 41-54%；净倾向按时衰衰减加权，
# 原对称半衰期 6h（λ=ln2/6≈0.1155/h，w=exp(-λ·Δt小时)）。
# K-ORG-167（E1）：不对称衰减——jev_decay.py 市场相对口径实证（n_dir=324，
# 2026-10-17T20:02）去市况污染后：利空高置信超额方向命中 T+1=55.0%、
# T+3 升至 60.7% 保持不衰（persist3=1.104，拟合半衰期触及 24h 上限钳制）；
# 利多各窗口≈50% 无超额边际（persist3=0.951）。故利空半衰期 24h（实证），
# 利多 12h 为去噪启发式（非拟合值，实证仅显示无边际、取快降权去噪）。
# 来源异质（E7）当前仅 announcement 达样本阈值且无超额边际，不下发
# （来源级零变化，随积累复评；sbt._load_samples 已保留 source 捕获）。
_JEV_LOW_CONF = 0.7
_JEV_HL_H = {"bull": 12.0, "bear": 24.0}  # bias → 半衰期（小时），w=2^(-Δt/HL)

# K-ORG-159（M1）：事件类型枚举 + 公司绑定（借鉴 FEED/DocFEE/FNSPID——
# 金融事件抽取、(公司,事件) 是舆情分布监控的最强载体）。云/本地判定
# 均输出 event_type（choice/枚举），本地回落另自报 companies（A股代码）。
_EVENT_TYPES = (
    "earnings",
    "policy",
    "rate",
    "trade",
    "supply",
    "demand",
    "price",
    "ma",
    "equity",
    "credit",
    "risk",
    "tech",
    "macro",
    "geo",
    "other",
)
_EVENT_CN = {
    "earnings": "业绩",
    "policy": "政策",
    "rate": "利率",
    "trade": "贸易",
    "supply": "供给",
    "demand": "需求",
    "price": "价格",
    "ma": "并购",
    "equity": "股权",
    "credit": "债务",
    "risk": "风险",
    "tech": "技术",
    "macro": "宏观",
    "geo": "地缘",
    "other": "其他",
}
_IMPACTS = ("low", "medium", "high")
_IMPACT_CN = {"low": "低", "medium": "中", "high": "高"}


_HORIZONS = ("intraday", "days", "weeks", "long", "unknown")


def _default_prob(bias, conf) -> dict:
    """v2 契约：缺 probabilities 时按 bias/conf 兜底成三概率分布（和=1）。

    bias=bull → bull=conf, bear=neutral=(1-conf)/2；其余类推。
    非数字/NaN 先退化到 0.5（与 conf 校验同口径）。
    """
    try:
        p = float(conf)
        if not math.isfinite(p):
            p = 0.5
    except (TypeError, ValueError):
        p = 0.5
    p = min(max(p, 0.0), 1.0)
    rest = round((1.0 - p) / 2.0, 3)
    if bias == "bull":
        return {"bull": round(p, 3), "bear": rest, "neutral": rest}
    if bias == "bear":
        return {"bull": rest, "bear": round(p, 3), "neutral": rest}
    return {"bull": rest, "bear": rest, "neutral": round(p, 3)}


def _validate_prob(raw, bias, conf) -> dict:
    """v2 契约：probabilities 规范化（bull/bear/neutral，有限数，clamp，归一）。

    接受 d["prob"] 或旧云侧 d["bias_probs"]。缺 key/全零/非法 → 走 _default_prob。
    """
    if not isinstance(raw, dict):
        return _default_prob(bias, conf)
    out = {}
    for k in ("bull", "bear", "neutral"):
        v = raw.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            return _default_prob(bias, conf)
        out[k] = min(max(float(v), 0.0), 1.0)
    s = sum(out.values())
    if s <= 0:
        return _default_prob(bias, conf)
    if abs(s - 1.0) > 0.02:
        out = {k: round(v / s, 3) for k, v in out.items()}
    else:
        out = {k: round(v, 3) for k, v in out.items()}
    return out


def _validate_jev(d):
    """K-ORG-159（M8）：JEV 判定 schema 校验（借鉴 Instructor/Outlines
    结构化校验哲学）。

    校验项：bias 枚举 / conf∈[0,1] / event_type 枚举（缺省 other）/
    impact 枚举（缺省 low）/ companies A股代码格式（6 位数字，滤非法）。
    不可修复项（bias 非法、
    conf 非数字）抛 ValueError → 判定侧走重发/降级链（C2批2：链路不断
    只退化）。可修复项（event_type 缺省/companies 清洗）就地修正返回。
    """
    bias = d.get("bias")
    if bias not in _BIAS_CN:
        raise ValueError("bias=%r 不在枚举内" % (bias,))
    conf = d.get("conf")
    # 2026-10-30 JEVSAFE：bool 是 int 子类（True→1.0）、NaN/Inf 也能通过
    # isinstance(float) 且 NaN<0.7 恒 False 被下游当高置信——统一拒绝。
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        raise ValueError("conf=%r 非数字" % (conf,))
    if not math.isfinite(float(conf)):
        raise ValueError("conf=%r 非有限数" % (conf,))
    d["conf"] = round(min(max(float(conf), 0.0), 1.0), 2)
    # 2026-10-30 JE VSAFE：原实现只用 lower 做"校验"，写回仍是原始大小写，
    # 下游 _EVENT_CN[event_type] 会 KeyError（如 EARNINGS）→ 整轮 backfill 崩溃。
    # 现统一写回归一化后的小写枚举值。
    et = str(d.get("event_type") or "other").strip().lower()
    if et not in _EVENT_TYPES:
        et = "other"  # 可修复：缺省/非法类别降级 other，不阻断判定
    d["event_type"] = et
    impact = str(d.get("impact") or "low").strip().lower()
    if impact not in _IMPACTS:
        impact = "low"
    d["impact"] = impact
    companies = []
    for c in (d.get("companies") or []) if isinstance(d.get("companies"), list) else []:
        if re.match(r"^\d{6}$", str(c)):
            companies.append(str(c))
    d["companies"] = companies
    # 2026-10-30 JEVV2 契约 v2：时间跨度 + 三概率分布（缺省可修复，不阻断判定）
    hz = str(d.get("horizon") or "unknown").strip().lower()
    d["horizon"] = hz if hz in _HORIZONS else "unknown"
    d["prob"] = _validate_prob(d.get("prob") or d.get("bias_probs"), bias, d["conf"])
    d["bias_probs"] = dict(d["prob"])  # 兼容旧消费方（云侧原字段名）
    return d


# K-ORG-163（A1）：自一致性重采样校准（借鉴 Self-Consistency / FinAbstain）。
# 实证（K-ORG-156/158）：conf 右偏 0.9+ 占 75%、与方向命中仅弱正相关，低置信
# 段命中率更不可信。对本地判定 conf<0.7 样本触发 K=3 重采样，以多数票一致率
# 重算 conf：3/3 一致→0.8，2:1→0.6，全分散→中性/0.3（分歧降权，治右偏）。
_JEV_RECALIB_CONF = 0.7
_JEV_AGREE_CONF = {3: 0.8, 2: 0.6}  # 多数票数 → 校准后 conf


def _resample_consistency(ask_fn, first_bias, k=3):
    """自一致性重采样：K 次判定多数票 bias + 一致率映射 conf。

    重采样失败票跳过（C2批2：链路不断只退化）；有效票不足 K 时按多数
    比例归档。返回 (bias, conf)。
    """
    votes = [first_bias]
    for _ in range(k - 1):
        try:
            j2 = ask_fn()
        except Exception:  # noqa: BLE001
            continue
        if isinstance(j2, dict):
            b2 = (j2.get("bias") or "").strip().lower()
            if b2 in _BIAS_CN:
                votes.append(b2)
    cnt = Counter(votes)
    maj, n = cnt.most_common(1)[0]
    # 2026-10-30 JE VSAFE：重采样全部失败时 votes 只有首票，旧逻辑把 len(votes)==1
    # 当成"全一致"返回 0.8 → 超时季把低置信伪装成高置信（10-30 日志 685 次升至 0.8）。
    if len(votes) < 2:
        return maj, 0.5  # 无法验证一致性：保留首票方向但按中低置信记
    if len(votes) >= 3 and len(cnt) == 1:
        return maj, _JEV_AGREE_CONF[3]  # 3 票全一致 → 高可信
    if n >= 2:
        return maj, _JEV_AGREE_CONF[2]  # 多数（2 票一致或 2:1）→ 中可信
    return "neutral", 0.3  # 全分散 → 中性低置信


_JEV_KEY_MEMO = None  # 2026-10-16 审计：进程内 memo（backfill 每判一条
# 调一次 _jev_key，原每次都 DPAPI 解密+正则读盘）


def _jev_key():
    """JEV_API_KEY：环境变量优先 → DPAPI 加密库 → 明文 ps1 兜底。"""
    global _JEV_KEY_MEMO
    if _JEV_KEY_MEMO:
        return _JEV_KEY_MEMO
    key = os.environ.get("JEV_API_KEY", "")
    if key:
        _JEV_KEY_MEMO = key
        return key
    # 2026-10-14 DPAPI 迁移（W1-2 任务A）：stub 化后本文件的正则解析
    # 读不到明文 key，故在明文兜底之前先走 .enc 加载器（解密失败返回
    # ""，自然落明文兜底——链路不断只退化，同 C2批2 豁免哲学）
    try:
        import importlib.util

        _loader_fp = os.path.join(os.path.dirname(JEV_ENV_PS1), "load_env_dpapi.py")
        spec = importlib.util.spec_from_file_location("load_env_dpapi", _loader_fp)
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)
        key = _mod.get_secret("JEV_API_KEY")
        if key:
            _JEV_KEY_MEMO = key
            return key
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/news_ai.py", exc)
    import re

    try:
        with open(JEV_ENV_PS1, encoding="utf-8") as f:
            txt = f.read()
        m = re.search(r'JEV_API_KEY"\s*=\s*"([^"]+)"', txt) or re.search(
            r'JEV_API_KEY\s*=\s*"([^"]+)"', txt
        )
        if m:
            _JEV_KEY_MEMO = m.group(1)
            return m.group(1)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/news_ai.py", exc)
    # K-STOCK-393（20261015 密钥收口）：env/DPAPI(.enc)/stub 全落空
    # 勿静默——审计实证 stub 化后此出口零提示，jev 云判定静默降级
    # 无人知晓。stderr WARN + env_fallback.flag（同 load_env_dpapi
    # 判例），仍按"无 key"豁免退出（链路不断只退化，C2批2 哲学不变）。
    print(
        "[WARN] JEV_API_KEY unresolved: env/DPAPI(.enc)/stub 全落空，"
        "jev 云判定将跳过（本地回落不受影响）",
        file=sys.stderr,
    )
    try:
        flag = os.path.join(os.path.dirname(JEV_ENV_PS1), "env_fallback.flag")
        if not os.path.exists(flag):
            with open(flag, "w", encoding="ascii") as f:
                f.write(
                    "news_ai._jev_key unresolved %s\n"
                    % time.strftime("%Y-%m-%d %H:%M:%S")
                )
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/news_ai.py", exc)
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
    _t0 = time.time()  # K-ORG-176：meter 计量（借鉴 grok-jev-router 的 meter 字段）
    ts_proxy = os.environ.get("TS_PROXY", "http://127.0.0.1:7890")
    ts_proxies = {"https": ts_proxy, "http": ts_proxy}
    timeout = max(timeout, 25)  # 7890 首次建连偶发 >15s（K-STOCK-084 实测）
    tag = ("（关联标的：%s）" % "/".join(stocks)) if stocks else ""
    state = "以下是一条刚采集的财经快讯，请判断其市场影响。\n· %s%s" % (title[:80], tag)
    body = {"model": "jev-latest", "state": state, "questions": JEV_NEWS_QUESTIONS}
    try:
        r = requests.post(
            JEV_BASE + "/systemone",
            json=body,
            timeout=timeout,
            headers={"Authorization": "Bearer %s" % key},
            proxies=ts_proxies,
        )
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
        d = {
            "bias": bias,
            "bias_cn": _BIAS_CN[bias],
            "conf": round(float(conf or 0), 2),
            "scope": scope,
            "scope_cn": _SCOPE_CN.get(scope, ""),
            "companies": list(stocks or []),  # K-ORG-159：云侧公司绑定=输入标的
            "src": "cloud",
        }
        # K-ORG-176：bias 概率全量落盘（借鉴 grok-jev-router signals 全量保留；
        # 供 E4 分歧/拥挤度哨兵用分布熵 > 单一 conf）
        d["bias_probs"] = {
            k: round(float(v), 3)
            for k, v in probs.items()
            if isinstance(v, (int, float))
        }
        d["impact"] = (ans.get("impact") or {}).get("choice", "low")
        try:
            d["event_type"] = (ans.get("event_type") or {}).get("choice", "other")
            d = _validate_jev(d)
        except ValueError as e:
            log("jev_judge cloud schema FAIL %s" % str(e)[:100])
            return None
        d["impact_cn"] = _IMPACT_CN[d["impact"]]
        d["meter"] = {
            "ms": int((time.time() - _t0) * 1000),
            "model": "jev-latest",
            "src": "cloud",
        }
        d["horizon"] = "unknown"  # 云端 schema 暂缺，契约 v2 允许 unknown
        d["reason"] = jev_reason(title, _BIAS_CN[bias], _SCOPE_CN.get(scope, scope))
        return d
    except Exception as e:
        log("jev_judge FAIL %s" % str(e)[:100])
        return None


def jev_reason(title, bias_cn, scope_cn, timeout=LLM_TIMEOUT_S):
    """本地 Ollama 生成一句话判断依据（30 字内）。失败返回 ""（不阻塞）。

    timeout 默认 45s（LLM_TIMEOUT_S）：旧值 20s 在 M3 批处理排队时必被客户端
    主动 abort，Ollama 会记 500 并污染 ai.error_rate（2026-10-25 EXEC-R6）。
    """
    model = ""
    try:
        with open(os.path.join(HERE, "news_config.json"), encoding="utf-8") as f:
            model = (json.load(f) or {}).get("ai_model") or ""
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/news_ai.py", exc)
    if not model:
        model = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
    try:
        r = requests.post(
            OLLAMA,
            json={
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": "财经快讯：%s\n判断：%s·%s。请用一句话（30字内）写出判断依据，"
                        "只输出依据本身，不要解释。" % (title[:80], bias_cn, scope_cn),
                    }
                ],
                "stream": False,
                "think": False,
                "options": {"temperature": 0.2, "num_predict": 80},
            },
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()["message"]["content"].strip().replace("\n", " ")[:60]
    except Exception:
        return ""  # 豁免（C2批2）：docstring 声明——Ollama 判断依据失败返回空串不阻塞


# K-STOCK-083（2026-10-10）：本地回落接线。盲测门已过（86.7% ENABLE，
# docs/plans/2026-10-10_A3_jev情绪源本地回落预研.md §三）。
_CLOUD_FAIL_UNTIL = 0.0  # 云失败后此 epoch 前直接走本地，防 451 期每条空打探测


def jev_judge_one_local(title, stocks, timeout=60):
    """本地回落判定（qwen3.5:9b + format json，v2 资产价格口径）。

    K-ORG-159（M1/M8）：在盲测 v2 口径之上扩展输出 event_type + companies
    （事件维度是分布监控最强载体）；解析失败带错重发 1 次（借鉴
    Instructor 重试哲学），schema 校验不过走降级链（C2批2：链路不断只退化）。
    失败返回 None（与云路径同契约，绝不抛出）。
    """
    if not title:
        return None
    _t0 = time.time()  # K-ORG-176：meter 计量（与云路径同契约）
    _model = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
    tag = ("（关联标的：%s）" % "/".join(stocks)) if stocks else ""
    base = (
        "财经快讯：%s%s\n判断该快讯对相关市场【资产价格】的短期影响方向，只输出 JSON："
        '{"bias":"bull|bear|neutral","conf":0.0到1.0,'
        '"scope":"cn|hk|us|commodity|crypto|fx|macro",'
        '"event_type":"<事件类别枚举>","impact":"low|medium|high",'
        '"companies":["相关A股代码"],"reason":"30字内判断依据（只写依据本身）",'
        '"horizon":"intraday|days|weeks|long",'
        '"prob":{"bull":0.0,"bear":0.0,"neutral":0.0}}，'
        "不要输出其他内容。"
        "scope 枚举（资产/市场范围）：cn A股、hk 港股、us 美股、commodity 商品、"
        "crypto 加密、fx 外汇、macro 宏观（无法确定时填空字符串，不要猜）。"
        "event_type 枚举：earnings业绩/policy政策/rate利率/trade贸易/supply供给/"
        "demand需求/price价格/ma并购/equity股权/credit债务/risk风险/tech技术/"
        "macro宏观/geo地缘/other其他。impact：low低/medium中/high高。"
        "companies 只填明确关联的A股代码（6位数字），"
        "无则空数组。"
        "口径：只看对资产价格的直接影响——外交会晤、一般性产业与技术进展、常规信息披露、"
        "国债/政策性金融债发行供给等属 neutral；明确的需求扩张、超预期业绩、降息等利好才判 bull；"
        "明确的供给冲击、业绩暴雷、加息、制裁等才判 bear。" % (title[:100], tag)
    )

    def _ask(prompt):
        r = requests.post(
            OLLAMA,
            json={
                "model": os.environ.get("OLLAMA_MODEL", "qwen3.5:9b"),
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "think": False,
                "format": "json",
                # 2026-10-28 JEVTRUNC: 80 token 对「长公司名单」类快讯会截断 JSON
                # （Unterminated string char~157 → 永久毒丸）。
                # 2026-10-30 BATCH4: reason 并入同一次输出（省一次模型调用）→ 预算 260。
                # 2026-10-30 JEVV2: 契约 v2 再加 horizon/prob → 预算 320（防 JSON 截断）。
                "options": {"temperature": 0.1, "num_predict": 320},
            },
            timeout=timeout,
        )
        r.raise_for_status()
        return json.loads(r.json()["message"]["content"])

    try:
        j = _ask(base)
    except Exception as e:
        log("jev_judge_local parse FAIL %s" % str(e)[:100])
        try:
            # M8：带错重发 1 次，仍败走降级链（C2批2）
            j = _ask(
                base + "（注意：上次输出无法解析，请只输出合法 JSON，不要任何多余文字）"
            )
        except Exception as e2:
            log("jev_judge_local retry FAIL %s" % str(e2)[:100])
            return None
    if not isinstance(j, dict):
        log("jev_judge_local schema FAIL 非对象")
        return None
    try:
        j["bias"] = (j.get("bias") or "").strip().lower()
        j["scope"] = _normalize_scope(j.get("scope"))
        j = _validate_jev(j)
    except ValueError as e:
        log("jev_judge_local schema FAIL %s" % str(e)[:100])
        return None
    # K-ORG-163（A1）：低置信本地判定触发自一致性重采样校准
    if j["conf"] < _JEV_RECALIB_CONF:
        _bias2, _conf2 = _resample_consistency(lambda: _ask(base), j["bias"])
        if _bias2 != j["bias"] or _conf2 != j["conf"]:
            log(
                "jev_judge_local recalib %s conf %.2f->%.2f bias %s->%s"
                % (title[:40], j["conf"], _conf2, j["bias"], _bias2)
            )
        j["bias"], j["conf"] = _bias2, _conf2
    scope = j.get("scope", "")
    d = {
        "bias": j["bias"],
        "bias_cn": _BIAS_CN[j["bias"]],
        "conf": j["conf"],
        "scope": scope,
        "scope_cn": _SCOPE_CN.get(scope, scope or ""),
        "event_type": j["event_type"],
        "event_cn": _EVENT_CN[j["event_type"]],
        "impact": j["impact"],
        "impact_cn": _IMPACT_CN[j["impact"]],
        "companies": j["companies"],
        "horizon": j.get("horizon", "unknown"),
        "prob": j.get("prob") or {},
        "src": "local",
    }
    d["bias_probs"] = dict(d["prob"])  # 契约 v2：旧字段名兼容副本
    d["meter"] = {
        "ms": int((time.time() - _t0) * 1000),
        "model": _model,
        "src": "local",
    }
    # 2026-10-30 BATCH4：reason 已由同一次判定调用返回（prompt 含 reason 字段）；
    # 只有模型漏给时才回落单独调用（旧行为每条多一次本地模型调用，实测中位 3.0s/条）。
    _reason = str(j.get("reason") or "").strip()[:60]
    d["reason"] = _reason or jev_reason(title, d["bias_cn"], d["scope_cn"])
    return d


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


def _jev_stats(db, hours=24):
    """近 N 小时已判断新闻的 JEV 分布统计 → (text, net, hi_dir)。

    K-ORG-157：低置信（conf<70%）单列计数、不参与净倾向；净倾向仅计入
    高置信方向判定（bull +1/bear -1），并按时效衰减加权 w=2^(-Δt/HL)
    K-ORG-167（E1）：半衰期按方向不对称——利空 24h（实证 T+3 超额命中
    升至 60.7% 不衰）、利多 12h（去噪启发式，实证各窗口≈50% 无边际）。
    K-ORG-168（E2）：net/hi_dir 供共振观察段（情绪×行情行为交叉）使用。
    """
    since = (now_cst() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    try:
        rows = db.execute(
            "SELECT jev, created_at FROM news WHERE jev IS NOT NULL "
            "AND jev!='' AND created_at>=?",
            (since,),
        ).fetchall()
    except Exception as e:
        log("jev_summary 查询失败，JEV 分布降级为空: %s" % str(e)[:100])  # C2批2
        return ""
    bias_cnt, scope_cnt, event_cnt = {}, {}, {}
    low_conf, hi_dir, net = 0, 0, 0.0
    now = now_cst()
    # 2026-10-30 JEVSAFE：now_cst() 带 tzinfo，而 created_at 是本地墙上时间；
    # tz-aware 与 naive 相减必抛 TypeError（被 except 吞掉→age 恒 0，时衰彻底失效）。
    now_naive = now.replace(tzinfo=None)
    for j, _ca in rows:
        try:
            d = json.loads(j)
        except Exception:
            continue  # 豁免（C2批2）：单条 jev 坏行跳过，不影响分布统计
        bias_cnt[d.get("bias", "?")] = bias_cnt.get(d.get("bias", "?"), 0) + 1
        sc = d.get("scope") or "?"
        scope_cnt[sc] = scope_cnt.get(sc, 0) + 1
        et = d.get("event_type")
        if et:  # K-ORG-159：仅统计携带 event_type 的新判定，
            # 存量旧数据无此键不污染「其他」桶
            event_cnt[et] = event_cnt.get(et, 0) + 1
        conf = d.get("conf")
        if isinstance(conf, (int, float)):
            if conf < _JEV_LOW_CONF:
                low_conf += 1
                continue  # 低置信不参与净倾向
            if d.get("bias") in ("bull", "bear"):
                # 2026-10-30 JEVSAFE：原实现读 d.get("created_at")——JEV JSON 无此键，
                # age 恒 0（时衰失效 + 每行 warning 刷日志）；应使用 SQL 取出的
                # news.created_at（_ca），缺失/坏值按 0 龄处理但不刷 warning。
                age_h = 0.0
                try:
                    age_h = max(
                        0.0,
                        (
                            now_naive
                            - datetime.strptime(str(_ca or "")[:16], "%Y-%m-%d %H:%M")
                        ).total_seconds()
                        / 3600.0,
                    )
                except (TypeError, ValueError):
                    age_h = 0.0
                net += math.exp(
                    -math.log(2) * age_h / _JEV_HL_H.get(d.get("bias"), 6.0)
                ) * (1.0 if d.get("bias") == "bull" else -1.0)
                hi_dir += 1
    if not bias_cnt:
        return "", 0.0, 0
    total = sum(bias_cnt.values())
    parts = [
        "%d 条已判：利多 %d / 利空 %d / 中性 %d"
        % (
            total,
            bias_cnt.get("bull", 0),
            bias_cnt.get("bear", 0),
            bias_cnt.get("neutral", 0),
        )
    ]
    for sc in ("cn", "us", "hk", "commodity", "crypto", "macro"):
        if scope_cnt.get(sc):
            parts.append(
                "%s %d（多%d/空%d）"
                % (
                    _SCOPE_CN[sc],
                    scope_cnt[sc],
                    sum(
                        1
                        for (j, _ca) in rows
                        if _bias_of(j) == "bull" and _scope_of(j) == sc
                    ),
                    sum(
                        1
                        for (j, _ca) in rows
                        if _bias_of(j) == "bear" and _scope_of(j) == sc
                    ),
                )
            )
    if low_conf:
        parts.append("低置信%d条(<70%%)" % low_conf)
    if hi_dir:
        parts.append("净倾向(时衰×高置信)%+.1f" % net)
    if event_cnt:
        top = sorted(event_cnt.items(), key=lambda kv: -kv[1])[:3]
        parts.append(
            "事件:%s" % " ".join("%s%d" % (_EVENT_CN.get(k, k), v) for k, v in top)
        )
    return " · ".join(parts), net, hi_dir


def jev_summary(db, hours=24):
    """兼容包装：仅返回 JEV 分布文本（外部契约不变）。"""
    text, _net, _hi = _jev_stats(db, hours)
    return text


# K-ORG-168（E2）：情绪×行情共振观察（借鉴 AI-CROWD/FinSPF 文本×价格
# z-score 交叉验证 + 经典量价行为判据：利多+上行=确认、利多+冲高回落=
# 分歧告警）。阈值由 jev_resonance.py 实证表校准后置位（数据驱动：
# 实证未达标档位不下发，与 E1/E7 同纪律）。
# 实证（2026-10-18T04:08，n=324 市场相对口径）：放量组命中率 45.9%
# 低于全体 53.1%（-7.2pp，放量为反向信号非确认）；冲高回落组 49.3%
# （-3.8pp 未达 5pp 阈值）；低开高走组 59.2%（+6.1pp 达阈值但样本小
# n=71 且与预设假设逻辑相反，谨慎未下发）。三档均不启用，留待样本
# 积累后重跑 jev_resonance.py 复评。
_JEV_RESONANCE_ON = False  # 实证 applied 达标后置位（当前未达标）
_RESO_RNG_PCT = 0.8  # 振幅%阈值（快照 high/low vs low）
_RESO_SHADOW = 0.5  # 上影/下影占振幅阈值（冲高回落/低开高走）
_RESO_MOVE = 0.4  # A股方向移动阈值%（涨跌幅绝对值）


def judge_resonance(net, hi_dir, markets):
    """E2：JEV 净倾向 × A股行情行为交叉 → 共振/分歧观察文本（空串=无信号）。

    net>0 利多主导 / net<0 利空主导；A 股行为取指数快照（上证优先）：
    冲高回落 = 振幅≥阈值且上影占比≥阈值（盘中冲高后回落）；
    低开高走 = 振幅≥阈值且下影占比≥阈值（收在高位）。
    """
    if not hi_dir or net == 0.0:
        return ""
    a = [m for m in markets if m.get("grp") == "A股" and m.get("pct") is not None]
    if not a:
        return ""
    avg = sum(m["pct"] for m in a) / len(a)
    main = next((m for m in a if m.get("name") == "sh000001"), a[0])
    hi, lo, price = main.get("high"), main.get("low"), main.get("price")
    up_shadow = dn_shadow = None
    if hi is not None and lo is not None and price is not None and hi > lo > 0:
        rng = (hi - lo) / lo * 100
        if rng >= _RESO_RNG_PCT:
            up_shadow = (hi - price) / (hi - lo)
            dn_shadow = (price - lo) / (hi - lo)
    if net > 0:
        if up_shadow is not None and up_shadow >= _RESO_SHADOW:
            return "分歧警示：JEV 利多主导 × A股冲高回落（上影%.0f%%）" % (
                up_shadow * 100
            )
        if avg >= _RESO_MOVE:
            return "共振确认：JEV 利多主导 × A股上行"
        if avg <= -_RESO_MOVE:
            return "背离观察：JEV 利多主导 × A股走弱"
    else:
        if dn_shadow is not None and dn_shadow >= _RESO_SHADOW:
            return "分歧警示：JEV 利空主导 × A股低开高走（收高位）"
        if avg <= -_RESO_MOVE:
            return "共振确认：JEV 利空主导 × A股下行"
        if avg >= _RESO_MOVE:
            return "背离观察：JEV 利空主导 × A股走强"
    return ""


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
        "title": "🤖 AI 金融晨报",
        "rules": BRIEF_RULES,
        "json": "ai_brief.json",
        "table": "ai_briefs",
        "kind": "ai",
        "win_cfg": "ai_window_hours",
        "win_def": 14,
        "span": "盘前",
    },
    "review": {
        "title": "📊 AI 收盘复盘",
        "rules": REVIEW_RULES,
        "json": "ai_review.json",
        "table": "ai_reviews",
        "kind": "review",
        "win_cfg": "review_window_hours",
        "win_def": 7,
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
    r = requests.get(
        SINA_HQ % ",".join(c for _, c in code_list), headers=HEADERS, timeout=12
    )
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
                # K-ORG-168（E2）：追加当日 high/low 快照（f[4]/f[5]），
                # 供共振观察段做冲高回落/低开高走的日内行为判定。
                rec.update(
                    name=f[0],
                    price=_f(f[3]),
                    pct=_pct(_f(f[3]), _f(f[2])),
                    high=_f(f[4]),
                    low=_f(f[5]),
                )
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
            arrow = (
                " ▲%+.2f%%" % m["pct"]
                if m["pct"] > 0
                else (" ▼%.2f%%" % abs(m["pct"]) if m["pct"] < 0 else " 0.00%")
            )
        lines.append(
            "  %s %s%s" % (m["name"], ("%." + str(m["dec"]) + "f") % m["price"], arrow)
        )
    return "\n".join(lines)


EM_SECTOR_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get?pn=%d&pz=100&po=1"
    "&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f12,f14,f3,f104,f105"
)
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
        out.append(
            {
                "name": name,
                "pct": pct,
                "up": int(s.get("f104") or 0),
                "down": int(s.get("f105") or 0),
            }
        )
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
    mood = (
        "普涨"
        if up_cnt * 3 > len(sectors) * 2
        else ("普跌" if up_cnt * 3 < len(sectors) else "分化")
    )
    lines = [
        "全行业 %d 个：%d 涨 / %d 跌（%s，%s）"
        % (len(sectors), up_cnt, dn_cnt, breadth, mood)
    ]
    for tag, grp in (
        ("领涨", sectors[:top_n]),
        ("领跌", list(reversed(sectors))[:top_n]),
    ):
        lines.append(
            "%s：%s"
            % (tag, "，".join("%s %+.2f%%" % (s["name"], s["pct"]) for s in grp))
        )
    return "\n".join(lines)


def fetch_news(db, window_hours, limit):
    since = (now_cst() - timedelta(hours=window_hours)).strftime("%Y-%m-%d %H:%M")
    rows = db.execute(
        # K-ORG-161（M4）：rowid 作 news_id 带出，供报告引用溯源映射
        "SELECT rowid, source, ts, title, hit, COALESCE(stocks,'') FROM news "
        "WHERE created_at>=? ORDER BY created_at DESC, ts DESC LIMIT ?",
        (since, limit),
    ).fetchall()
    out = []
    for r in reversed(rows):
        try:
            stocks = json.loads(r[5]) if r[5] else []
        except Exception:
            stocks = []
        out.append(
            dict(
                news_id=r[0], source=r[1], ts=r[2], title=r[3], hit=r[4], stocks=stocks
            )
        )
    return out


def format_news(items):
    src_name = {"wallstreetcn": "见闻", "sina": "新浪", "eastmoney": "东财"}
    lines = []
    for i, it in enumerate(items, 1):
        star = "⭐" if it["hit"] else "  "
        tag = ("【%s】" % "/".join(it["stocks"])) if it.get("stocks") else ""
        lines.append(
            "%d. %s[%s %s]%s %s"
            % (
                i,
                star,
                src_name.get(it["source"], it["source"]),
                (it["ts"] or "")[-11:],
                tag,
                (it["title"] or "")[:70],
            )
        )
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


# K-ORG-161（M4）：报告引用溯源——受审计的 claim 区块（这些区块的每条
# claim 必须带「来源 #序号」或已自声明无来源；其余区块为行情/提示性内容）
_AUDIT_SECS = {"关键主题", "盘面驱动", "关注个股动态", "今日重点关注", "明日关注"}


def _audit_citations(text, news_count):
    """引用溯源守卫：无「来源 #N」且未声明「暂无明确信息来源」的 claim
    补标「暂无明确信息来源」（LLM 偶忘引用规则的兜底，不重写原文）。

    借鉴开源 RAG 引用溯源哲学：可验证的 claim 必须带证据锚点，无证据
    的 claim 显式标记而非默认信任。
    """
    out = []
    sec = ""
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s:
            out.append(ln)
            continue
        if s.startswith("【") and "】" in s:
            sec = s[1 : s.index("】")]  # 仅受审计区块进入 claim 校验
            out.append(ln)
            continue
        if (
            sec in _AUDIT_SECS
            and not s.startswith("（")
            and "来源 #" not in s
            and "暂无明确信息来源" not in s
        ):
            out.append(ln + "（暂无明确信息来源）")
            continue
        out.append(ln)
    return "\n".join(out)


def call_llm(model, market_text, news_text, stock_text, rules, sector_text=""):
    blocks = [
        "以下是市场行情快照与财经快讯，请生成报告。\n\n== 行情快照 ==\n" + market_text
    ]
    if sector_text:
        blocks.append("== 行业板块热力（当日涨跌）==\n" + sector_text)
    blocks.append("== 财经快讯（⭐为关注命中）==\n" + news_text)
    if stock_text:
        blocks.append("== 自选股相关 ==\n" + stock_text)
    user = "\n\n".join(blocks) + "\n\n" + rules + (STOCK_RULE if stock_text else "")
    r = requests.post(
        OLLAMA,
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYS_PROMPT},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.3, "num_predict": 1100},
        },
        timeout=600,
    )
    r.raise_for_status()
    text = r.json()["message"]["content"].strip()
    if "【" not in text:
        raise ValueError("unexpected llm output format")
    return text


def fallback_brief(news_items):
    lines = [
        "【提示】",
        "（本地 AI 模型暂不可用，以下为关注命中原文）",
        "",
        "【⭐ 关注命中】",
    ]
    hits = [n for n in news_items if n["hit"]]
    for n in hits or news_items[-8:]:
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
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "send_feishu.py"), tmp],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    out = (r.stdout or "").strip()
    log("push -> " + out)
    return out.startswith("SEND_OK")


def run(mode, dry):
    m = MODES[mode]
    # 2026-10-25 SEC 收口（EXEC-R14）：敏感字段（portal/doc/jev_*）读 env/本地私有覆盖
    from config_local import load_news_config

    cfg = load_news_config()
    model = cfg.get("ai_model", "qwen3.5:9b")
    window = int(cfg.get(m["win_cfg"], m["win_def"]))
    limit = int(cfg.get("ai_max_news", 50))
    watchlist = cfg.get("watchlist", [])

    db = sqlite3.connect(DB)
    db.execute(
        "CREATE TABLE IF NOT EXISTS ai_briefs ("
        "date TEXT PRIMARY KEY, ts TEXT, model TEXT, ok INTEGER, "
        "markets TEXT, brief TEXT, pushed INTEGER DEFAULT 0)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS ai_reviews ("
        "date TEXT PRIMARY KEY, ts TEXT, model TEXT, ok INTEGER, "
        "markets TEXT, brief TEXT, pushed INTEGER DEFAULT 0)"
    )

    def _add_col(col):
        # 2026-10-16 审计：原 except OperationalError: pass 连 "database
        # is locked" 等真故障一并吞掉（迁移静默假成功）；仅豁免
        # duplicate column（幂等迁移的预期异常），其余上抛。
        try:
            db.execute("ALTER TABLE news ADD COLUMN %s TEXT" % col)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise

    _add_col("stocks")
    _add_col("jev")

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
            with open(
                os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "sectors.json"
                ),
                "w",
                encoding="utf-8",
            ) as f:
                json.dump(
                    {
                        "ts": now_cst().strftime("%Y-%m-%d %H:%M") + " 北京",
                        "count": len(sectors),
                        "up": sum(1 for s in sectors if s["pct"] > 0),
                        "items": sectors,
                    },
                    f,
                    ensure_ascii=False,
                )
        except Exception as e:
            log(
                "sectors.json 落盘失败（/bt-sectors 页将 stale）: %s" % str(e)[:100]
            )  # C2批2

    news_items = fetch_news(db, window, limit)
    news_text = format_news(news_items) if news_items else "（时间窗内暂无新闻）"
    stock_text = format_stock_block(news_items, watchlist)
    jev_text, _jev_net, _jev_hi = _jev_stats(db, 24)
    jev_reso = ""
    if _JEV_RESONANCE_ON and _jev_hi:
        try:
            jev_reso = judge_resonance(_jev_net, _jev_hi, markets)
        except Exception as e:
            log("resonance FAIL %s" % str(e)[:80])  # C2批2：共振段失败不阻断
    log(
        "%s start markets=%d news=%d stocks_hits=%d model=%s"
        % (
            mode,
            len(markets),
            len(news_items),
            sum(1 for n in news_items if n.get("stocks")),
            model,
        )
    )

    ai_ok = 1
    try:
        brief = call_llm(
            model, market_text, news_text, stock_text, m["rules"], sector_text
        )
    except Exception as e:
        ai_ok = 0
        brief = fallback_brief(news_items)
        log("llm FAIL %s -> fallback" % str(e)[:150])
    brief = _audit_citations(brief, len(news_items))  # K-ORG-161（M4）：引用溯源守卫

    today = now_cst().strftime("%Y-%m-%d")
    header = "%s · %s（%s %d条快讯/%dh）" % (
        m["title"],
        now_cst().strftime("%m-%d"),
        m["span"],
        len(news_items),
        window,
    )
    msg_parts = [header, "", "▍全球市场速览", market_text]
    if sector_text:
        msg_parts += ["", "▍行业板块热力", sector_text]
    if jev_text:
        msg_parts += ["", "▍JEV 新闻判断（近24h）", jev_text]
        if jev_reso:
            msg_parts += ["", "▍共振观察", jev_reso]
    msg_parts += [
        "",
        brief,
        "",
        "—— 本地 %s 客观归纳 · 仅信息参考，不构成投资建议 · 信息采集域" % model,
    ]
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
            if jev_reso:
                md_blocks.append("### ▍共振观察\n" + jev_reso)
            md_blocks.append(brief)
            doc = cfg.get("doc_url", "")
            btns = []
            if doc:
                btns.append(("📄 详情文档", doc, "default"))
            if portal:
                btns.append(("🚦 明日信号·内网", portal + "bt-signals", "default"))
                if jev_text:
                    btns.append(
                        (
                            "⚖️ JEV 判断",
                            cfg.get("jev_doc_url") or (portal + "bt-jev"),
                            "primary",
                        )
                    )
                if mode == "review" and sector_text:
                    btns.append(("🔥 板块热力·内网", portal + "bt-sectors", "default"))
                btns.append(("🖥 门户控制台·内网", portal + "#console", "default"))
            card = build_card(
                m["title"],
                "%s（%s %d条快讯/%dh）"
                % (m["span"], now_cst().strftime("%m-%d"), len(news_items), window),
                "turquoise" if mode == "brief" else "carmine",
                md_blocks,
                footer="本地 %s 客观归纳 · 仅信息参考，不构成投资建议 · 信息采集域"
                % model,
                buttons=btns or None,
            )
            # 2026-10-25 P2/P3：统一分装 + H5 按钮（失败回落旧卡）
            try:
                sys.path.insert(
                    0,
                    os.path.join(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "portal",
                    ),
                )
                from push_unified_adapter import adapt as _adapt
                from feishu_card_v2 import m_url as _m

                _h5 = _m("entry")
                _btns = [("📱 移动端总览（H5）", _h5, "primary")] if _h5 else None
                card = _adapt(
                    "ai",
                    card,
                    "brief",
                    m["title"],
                    md_blocks,
                    ts=now_cst().strftime("%Y-%m-%d %H:%M"),
                    source="AI 晨报",
                    footer="本地 %s 客观归纳 · 仅信息参考，不构成投资建议" % model,
                    buttons=_btns,
                    level="info",
                )
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/news_ai.py", exc)
            sent = push_card(card, qr_portal=portal or None)
            if not sent:
                log("card FAIL -> text fallback")
        except Exception as e:
            log("card build FAIL %s" % str(e)[:120])
    sent = sent or push_message(msg) if not dry else True

    if dry:
        # 2026-10-16 审计：dry 演练不落库——原 INSERT OR REPLACE 覆盖当日
        # 生产记录并虚标 pushed=1，还回灌 runs 表污染运行统计。
        db.close()
        log("dry 模式：跳过 DB 落库（%s）" % m["table"])
        return
    db.execute(
        "INSERT OR REPLACE INTO %s(date,ts,model,ok,markets,brief,pushed) "
        "VALUES(?,?,?,?,?,?,?)" % m["table"],
        (
            today,
            now_cst().strftime("%Y-%m-%d %H:%M"),
            model,
            ai_ok,
            json.dumps(markets, ensure_ascii=False),
            brief,
            int(bool(sent)),
        ),
    )
    db.execute(
        "INSERT INTO runs(kind,ts,ok,new_count,push_count,detail) VALUES(?,?,?,?,?,?)",
        (
            m["kind"],
            now_cst().strftime("%Y-%m-%d %H:%M"),
            ai_ok,
            len(news_items),
            int(bool(sent)),
            "markets=%d llm_ok=%d" % (len(markets), ai_ok),
        ),
    )
    db.commit()
    db.close()

    if sent and not dry:  # 同步刷新飞书简报文档（失败静默，不影响推送）
        try:
            import news_doc_board

            log("doc_board refresh %s" % ("OK" if news_doc_board.refresh() else "FAIL"))
        except Exception as e:
            log("doc_board refresh ERR %s" % str(e)[:100])
        try:  # 外网图表卡（后台跑，不阻塞晨报链路）
            # C2批3: 子进程 print 原先无通道全丢（含 JEV_CHART pushed/WECOM_FAIL），
            # 统一重定向 C:\Temp\news_jev_chart_out.log（对齐 alert_push 等启动器惯例）
            _jc_log = open(r"C:\Temp\news_jev_chart_out.log", "ab")
            subprocess.Popen(
                [
                    sys.executable,
                    os.path.join(
                        os.path.dirname(os.path.abspath(__file__)), "news_jev_chart.py"
                    ),
                ],
                stdout=_jc_log,
                stderr=subprocess.STDOUT,
                creationflags=0x08000000,
            )  # CREATE_NO_WINDOW
        except Exception as e:
            log("news_jev_chart 启动失败，本期图表不更新: %s" % str(e)[:100])  # C2批2
        try:  # 微信渠道：晨报/复盘 markdown 摘要（未配置自动跳过）
            import send_wecom

            md = "**%s · %s**\n" % (
                "🤖 AI 金融晨报" if mode == "brief" else "🤖 AI 收盘复盘",
                now_cst().strftime("%m-%d %H:%M"),
            )
            if jev_text:
                md += "JEV 判断：%s\n" % jev_text
            md += "\n" + brief[:1500]
            send_wecom.send_markdown(md)
        except Exception as e:
            log(
                "wecom 摘要推送失败（未配置走 send_wecom 内部跳过，异常即真失败）: %s"
                % str(e)[:100]
            )  # C2批2

    headline = ""
    for ln in brief.splitlines():
        if ln.strip() and not ln.startswith("【"):
            headline = ln.strip()
            break
    payload = {
        "kind": mode,
        "date": today,
        "ts": now_cst().strftime("%Y-%m-%d %H:%M"),
        "model": model,
        "ok": bool(ai_ok),
        "pushed": bool(sent),
        "news_count": len(news_items),
        "market_count": len(markets),
        "headline": headline[:120],
    }
    # K-ORG-161（M4）：引用溯源映射——报告「来源 #序号」→ 快讯 news_id(rowid)
    payload["news_refs"] = {
        str(i + 1): it["news_id"] for i, it in enumerate(news_items)
    }
    if sectors:
        up_cnt = sum(1 for s in sectors if s["pct"] > 0)
        payload["sectors"] = {
            "total": len(sectors),
            "up": up_cnt,
            "down": len(sectors) - up_cnt,
            "top": sectors[:6],
            "bottom": list(reversed(sectors))[:6],
        }
    atomic_json(os.path.join(HERE, m["json"]), payload)
    log("%s done llm_ok=%d sent=%s" % (mode, ai_ok, sent))
    print(
        "AI_%s %s llm_ok=%d markets=%d news=%d"
        % (
            mode.upper(),
            "SENT" if sent else "FAIL",
            ai_ok,
            len(markets),
            len(news_items),
        )
    )
    if dry:
        print("=" * 60)
        print(msg)


def main():
    mode = "review" if "review" in sys.argv else "brief"
    run(mode, "--dry" in sys.argv)
    # W40：AI 晨报（08:00 既有任务）成功后追加「推送日报」（每日一次，不新建任务）
    if (
        mode == "brief"
        and "--dry" not in sys.argv
        and str(os.environ.get("PUSH_DIGEST_ENABLED", "1")) != "0"
    ):
        try:
            import sys as _sys
            import os as _os

            _portal = _os.path.join(
                _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "portal"
            )
            if _portal not in _sys.path:
                _sys.path.insert(0, _portal)
            import push_daily_digest as _pd

            _ok, _why = _pd.send_once()
            print("PUSH_DIGEST", _ok, _why)
            # H3：军机驿日报（Hermes 只读草稿；定时归平台，Hermes 不当定时器）
            if str(os.environ.get("HERMES_REPORT_ENABLED", "1")) != "0":
                try:
                    import hermes_report_job as _hr

                    _hok, _hwhy = _hr.run_once("day")
                    print("HERMES_REPORT", _hok, _hwhy)
                except Exception as _he:  # noqa: BLE001
                    print("HERMES_REPORT_SKIP", str(_he)[:120])
        except Exception as _e:  # noqa: BLE001
            print("PUSH_DIGEST_SKIP", str(_e)[:120])


if __name__ == "__main__":
    main()
