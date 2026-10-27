# -*- coding: utf-8 -*-
"""
K-STOCK-299 · 自然语言回测解析器（nl_backtest）
老田 10-13 10:35 指令：回测框架加自然语言接口——说清楚想怎么回测、怎么选票。
双轨设计（透明可解释，不黑箱执行）：
  ① 规则解析（默认·零延迟·确定性）：正则覆盖常用表达，命中即记 note；
  ② LLM 兜底（use_llm=True）：本地 Ollama（默认 qwen3.5:4b）把规则没认全的
     说法映射成参数 JSON → 严格 schema 白名单校验，非法值丢弃记 unknown。
输出 params 只含 schema 内键，由前端回填表单供老田确认后再提交
（_bt2_start 还会再做一遍数值校验——双保险）。
"""

import json
import re
import datetime

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_MODEL = "qwen3.5:4b"  # 快档；35b 属重炮不用于表单解析
OLLAMA_TIMEOUT = 60

# 参数 schema：类型与合法域（与 backtest_engine_v2.run_job / portal _bt2_start 对齐）
SCHEMA = {
    "strategy": ("chanlun", "double_knife"),
    "universe": ("market", "pool76"),
    "buy_filter": ("3", "23", "2", "all"),
    "d0": "date8",
    "d1": "date8",
    "top_n": (1, 50),
    "slot_pct": (0.005, 0.5),
    "slot_mode": ("fixed", "random", "score"),
    "slot_min": (0.005, 0.5),
    "slot_max": (0.005, 0.5),
    "cap_mode": ("regime_8050", "fixed"),
    "cap_high": (0.05, 1.0),
    "cap_mid": (0.05, 1.0),
    "cap_low": (0.0, 1.0),
    "cap_fixed": (0.05, 1.0),
    "sig_expiry": (0, 60),
    "runup_cap": (0.0, 0.5),
    "three_fail_exit": (True, False),
    "fail_level": ("zg", "zd"),
    "fail_buffer": (0.0, 0.2),
    "stop_pct": (-0.5, 0.0),
    "holding_max_days": (0, 60),
    "regime_gate": (True, False),
    "manual_picks": "codes",
}


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _pct(m):
    """'8%'/'0.08' → 比例。"""
    v = float(m)
    return v / 100.0 if v > 1 else v


def _code_suf(code):
    """6 位代码 → 全市场宇宙后缀（6=SH·0/3=SZ·4/8=BJ 不在全市场宇宙，返回 None）。"""
    if code.startswith("6"):
        return code + ".SH"
    if code[0] in "03":
        return code + ".SZ"
    return None


# ──────────────────────────────────────────────
# ① 规则解析
# ──────────────────────────────────────────────
def _rule_parse(text, params, notes, unknown):
    t = text.replace("，", " ").replace("。", " ").replace("；", " ").replace("？", " ")

    # ── 窗口 ──
    m = re.search(
        r"(20\d{2})\s*年?\s*(\d{1,2})?\s*月?\s*(?:到|至|–|-|~|—)\s*(20\d{2})\s*年?\s*(\d{1,2})?\s*月?",
        t,
    )
    if m:
        y1, m1, y2, m2 = m.groups()
        params["d0"] = "%s%02d01" % (y1, int(m1 or 1))
        params["d1"] = "%s%02d28" % (y2, int(m2 or 12))
        notes.append("窗口：%s → %s" % (params["d0"], params["d1"]))
    else:
        m = re.search(r"(20\d{6})\s*(?:到|至|~|—|-)\s*(20\d{6})", t)
        if m:
            params["d0"], params["d1"] = m.group(1), m.group(2)
            notes.append("窗口：%s → %s" % (params["d0"], params["d1"]))
        else:
            m = re.search(r"(?:最近|近|过去)\s*(\d{1,2})\s*个?月", t)
            if m:
                n = int(m.group(1))
                today = datetime.date.today()
                y, mo = today.year, today.month - n
                while mo <= 0:
                    mo += 12
                    y -= 1
                params["d0"] = "%04d%02d01" % (y, mo)
                params["d1"] = today.strftime("%Y%m%d")
                notes.append("窗口：近 %d 月 → %s" % (n, params["d0"]))
            elif re.search(r"(?:最近|近|过去)\s*一?\s*年|近一年", t):
                today = datetime.date.today()
                params["d0"] = today.replace(year=today.year - 1).strftime("%Y%m%d")
                params["d1"] = today.strftime("%Y%m%d")
                notes.append("窗口：近一年 %s → %s" % (params["d0"], params["d1"]))
            elif re.search(r"今年|年初以来|ytd", t, re.I):
                today = datetime.date.today()
                params["d0"] = today.strftime("%Y") + "0101"
                params["d1"] = today.strftime("%Y%m%d")
                notes.append("窗口：今年以来 %s → %s" % (params["d0"], params["d1"]))

    # ── 策略 ──
    if re.search(r"双刀|量子双刀|日内", t):
        params["strategy"] = "double_knife"
        notes.append("策略：量子双刀日内纪律")
    elif re.search(r"缠论", t):
        params["strategy"] = "chanlun"
        notes.append("策略：缠论等机会")

    # ── 样本域 ──
    if re.search(r"全市场|全网|5000|所有股票|全部股票", t):
        params["universe"] = "market"
        notes.append("样本域：全市场 5000+（剔 ST/北交所）")
    elif re.search(r"76|静态池|老池", t):
        params["universe"] = "pool76"
        notes.append("样本域：静态 76 只池")

    # ── 买点等级 ──
    if re.search(r"只看三买|仅三买|只要三买", t):
        params["buy_filter"] = "3"
        notes.append("买点等级：仅三买")
    elif re.search(r"二买.{0,3}三买|三买.{0,3}二买", t):
        params["buy_filter"] = "23"
        notes.append("买点等级：二买+三买")
    elif re.search(r"仅二买|只看二买", t):
        params["buy_filter"] = "2"
        notes.append("买点等级：仅二买")
    elif re.search(r"全要|一买.{0,6}也|一二三", t):
        params["buy_filter"] = "all"
        notes.append("买点等级：一二三类全要")

    # ── 槽数 ──
    m = (
        re.search(r"(?:持仓|槽数|同时持有|最多)\s*(\d{1,2})\s*只", t)
        or re.search(r"(\d{1,2})\s*只?持仓", t)
        or re.search(r"(\d{1,2})\s*只\s*[，,]?\s*(?:单票|每股|每只|仓位|评分)", t)
    )
    if m:
        params["top_n"] = _clamp(int(m.group(1)), 1, 50)
        notes.append("持仓槽数：%d" % params["top_n"])

    # ── 单票仓位三模式 ──
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*%\s*(?:到|至|~|—|-)\s*(\d+(?:\.\d+)?)\s*%\s*(?:随机|之间|区间)?",
        t,
    )
    if m:
        params["slot_mode"] = "random"
        params["slot_min"] = _clamp(_pct(m.group(1)), 0.005, 0.5)
        params["slot_max"] = _clamp(_pct(m.group(2)), 0.01, 0.5)
        notes.append(
            "单票仓位：区间随机 %.1f%%~%.1f%%（固定种子可复现）"
            % (params["slot_min"] * 100, params["slot_max"] * 100)
        )
    elif re.search(r"(?:按|根据)评分|评分权重|权重自动|按权重", t):
        params["slot_mode"] = "score"
        notes.append("单票仓位：评分权重自动（评分占比×总仓位帽）")
    else:
        m = re.search(r"(?:单票|每股|每只|个股)\D{0,4}?(\d+(?:\.\d+)?)\s*%", t)
        if m:
            params["slot_mode"] = "fixed"
            params["slot_pct"] = _clamp(_pct(m.group(1)), 0.005, 0.5)
            notes.append("单票仓位：固定 %.1f%%" % (params["slot_pct"] * 100))

    # ── 总仓位 ──
    hi = re.search(r"(?:高位|行情好|强势)\D{0,4}?(\d{1,3})(?:\s*%)?", t)
    mid = re.search(r"中位\D{0,4}?(\d{1,3})(?:\s*%)?", t)
    lo = re.search(r"(?:低位|行情差|弱市|弱势)\D{0,4}?(\d{1,3})(?:\s*%)?", t)
    if hi or mid or lo:
        params["cap_mode"] = "regime_8050"
        if hi:
            params["cap_high"] = _clamp(_pct(hi.group(1)), 0.05, 1.0)
        if mid:
            params["cap_mid"] = _clamp(_pct(mid.group(1)), 0.05, 1.0)
        if lo:
            params["cap_low"] = _clamp(_pct(lo.group(1)), 0.0, 1.0)
        notes.append(
            "总仓位：regime 三档 %s/%s/%s"
            % (
                params.get("cap_high", 0.8),
                params.get("cap_mid", 0.5),
                params.get("cap_low", 0.3),
            )
        )
    else:
        m = re.search(
            r"(?:总仓位|总持仓|总体仓位)\s*(?:固定|保持|留)?\s*(\d{1,3})\s*%", t
        )
        if m:
            params["cap_mode"] = "fixed"
            params["cap_fixed"] = _clamp(_pct(m.group(1)), 0.05, 1.0)
            notes.append("总仓位：固定 %.0f%%" % (params["cap_fixed"] * 100))

    # ── 补仓规则 ──
    m = re.search(
        r"信号\s*(\d{1,2})\s*(?:天|日)\s*(?:内)?(?:有效|入场)|(\d{1,2})\s*(?:天|日)\s*(?:内)?有效",
        t,
    )
    if m:
        params["sig_expiry"] = _clamp(int(m.group(1) or m.group(2)), 0, 60)
        notes.append("信号有效期：%d 日" % params["sig_expiry"])
    m = re.search(r"(?:追高|追涨)(?:帽)?\s*(\d{1,2})\s*%|涨停.{0,4}不追", t)
    if m:
        params["runup_cap"] = _clamp(
            _pct(m.group(1)) if m.group(1) else 0.095, 0.0, 0.5
        )
        notes.append("追高帽：%.1f%%" % (params["runup_cap"] * 100))

    # ── 清仓规则 ──
    if re.search(r"失效离场.{0,3}(?:关|不|别)|不要?失效离场", t):
        params["three_fail_exit"] = False
        notes.append("三买失效离场：关")
    elif re.search(r"跌破.{0,3}下沿|中枢下沿", t):
        params["fail_level"] = "zd"
        notes.append("失效线：中枢下沿 zd（松）")
    m = re.search(r"(?:失效|跌破)\D{0,4}缓冲\s*(\d+(?:\.\d+)?)\s*%", t)
    if m:
        params["fail_buffer"] = _clamp(_pct(m.group(1)), 0.0, 0.2)
        notes.append("失效缓冲：%.1f%%" % (params["fail_buffer"] * 100))
    m = re.search(r"止损\s*(\d{1,2})\s*%", t)
    if m:
        params["stop_pct"] = -_clamp(_pct(m.group(1)), 0.005, 0.5)
        notes.append(
            "个股止损：-%.1f%%（默认关·显式设置即开）" % (-params["stop_pct"] * 100)
        )
    m = re.search(
        r"(?:持有|拿)(?:不?超过|上限|最多)\s*(\d{1,2})\s*(?:天|日|个交易日)", t
    ) or re.search(r"(?:持有|拿)\s*(\d{1,2})\s*(?:天|日|个交易日)(?:就|后|离场|卖)", t)
    if m:
        params["holding_max_days"] = _clamp(int(m.group(1)), 0, 60)
        notes.append("持有上限：%d 日" % params["holding_max_days"])
    if re.search(
        r"(?:弱市|行情差|弱势|熊市|大盘差)\D{0,8}?(?:不|别|禁|莫)\s*(?:开|加|买)|闸门.{0,3}开",
        t,
    ):
        params["regime_gate"] = True
        notes.append("弱市闸门：开（指数<MA60 不开新仓）")

    # ── 手动票池（6 位代码提取）──
    codes = re.findall(r"(?<!\d)(\d{6})(?!\d)", t)
    if codes and re.search(r"只(?:交易|买|做|看)|手动|指定|票池|名单", t):
        picks = []
        for c in codes:
            s = _code_suf(c)
            if s:
                picks.append(s)
            else:
                unknown.append("代码 %s 不在全市场宇宙（4/8 开头=北交所，已剔）" % c)
        if picks:
            params["manual_picks"] = ",".join(picks)
            notes.append("手动票池：%s（交易只吃名单内信号）" % ", ".join(picks))

    return params, notes, unknown


# ──────────────────────────────────────────────
# ② LLM 兜底（本地 Ollama·严格 schema 白名单）
# ──────────────────────────────────────────────
# LLM 兜底只允许「映射/日期/槽数」类键——数值类（仓位/止损/有效期等）禁止 LLM 编造，
# 一律走表单默认值（10-13 实测 4b 在模糊输入下会编造全部数值，危险）。
LLM_KEYS = {
    "strategy",
    "universe",
    "buy_filter",
    "d0",
    "d1",
    "top_n",
    "slot_mode",
    "cap_mode",
    "fail_level",
    "three_fail_exit",
    "regime_gate",
    "manual_picks",
}

_LLM_PROMPT = """你是量化回测参数解析器。把用户的自然语言回测需求转成 JSON 参数。
只输出 JSON，不要解释。只允许这些键（其它一律不要输出）：
- "strategy": "chanlun"(缠论) 或 "double_knife"(量子双刀日内)
- "universe": "market"(全市场) 或 "pool76"(静态76池)
- "buy_filter": "3"(仅三买) "23"(二买+三买) "2"(仅二买) "all"(全部买点)
- "d0"/"d1": 8位日期 YYYYMMDD（回测窗口，用户提到时间范围才输出）
- "top_n": 整数 1-50（持仓槽数，用户提到才输出）
- "slot_mode": "fixed"(单票固定仓位) "random"(区间随机) "score"(按评分权重)
- "cap_mode": "regime_8050"(随行情三档总仓位) 或 "fixed"
- "fail_level": "zg"(跌破中枢上沿离场·紧) "zd"(跌破下沿·松)
- "three_fail_exit": true/false（三买失效离场）
- "regime_gate": true/false（弱市不开新仓）
- "manual_picks": "代码,代码"（手动指定票池，代码形如 000004.SZ）
用户没明确提到的键一律不要输出；仓位百分比、止损、有效期等数值参数不要输出。
用户输入：
"""


def _llm_parse(text, params, have_keys, notes, unknown):
    """规则没认全（有未知说法或只有很少命中）时问本地 Ollama；严格白名单过滤。
    坑（10-13 实测）：qwen3.5 是 thinking 模型——默认思考 54s 且 JSON 落 thinking
    字段、content 为空 ⇒ 必须带 think:false（1.5s 出结果）+ thinking 兜底抽取。"""
    try:
        import urllib.request

        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({})
        )  # 127.0.0.1 直连·不走系统代理
        body = json.dumps(
            {
                "model": OLLAMA_MODEL,
                "stream": False,
                "format": "json",
                "think": False,
                "options": {"temperature": 0, "num_predict": 400},
                "messages": [{"role": "user", "content": _LLM_PROMPT + text}],
            }
        ).encode("utf-8")
        resp = opener.open(
            urllib.request.Request(
                OLLAMA_URL, data=body, headers={"Content-Type": "application/json"}
            ),
            timeout=OLLAMA_TIMEOUT,
        )
        raw = json.loads(resp.read().decode("utf-8"))
        msg = raw.get("message") or {}
        content = msg.get("content") or ""
        m = re.search(r"\{.*\}", content, re.S)
        if not m and msg.get("thinking"):
            m = re.search(
                r"\{.*\}", msg["thinking"], re.S
            )  # thinking 兜底（兼容旧行为）
        if not m:
            unknown.append("LLM 输出无 JSON，已忽略")
            return 0
        cand = json.loads(m.group(0))
        n = 0
        for k, v in cand.items():
            if k not in SCHEMA:
                unknown.append("LLM 给了未知键 %s，已丢弃" % k)
                continue
            if k not in LLM_KEYS:
                unknown.append("LLM 给了数值类键 %s，已丢弃（数值一律走表单默认）" % k)
                continue
            if k in have_keys:
                continue  # 规则结果优先，LLM 只补空
            ok = True
            dom = SCHEMA[k]
            if isinstance(dom, tuple):
                if isinstance(dom[0], str):
                    ok = v in dom
                else:
                    try:
                        v = _clamp(float(v), dom[0], dom[1])
                    except (TypeError, ValueError):
                        ok = False
            elif dom == "date8":
                ok = bool(re.fullmatch(r"20\d{6}", str(v)))
                v = str(v)
            elif dom == "codes":
                cs = re.findall(r"\d{6}", str(v))
                vs = [x for x in (_code_suf(c) for c in cs) if x]
                ok = bool(vs)
                v = ",".join(vs)
            if not ok:
                unknown.append("LLM 的 %s=%r 不合法，已丢弃" % (k, v))
                continue
            params[k] = v
            have_keys.add(k)
            notes.append("LLM 补充 %s=%s（qwen3.5:4b·白名单校验·表单里可改）" % (k, v))
            n += 1
        return n
    except Exception as e:
        unknown.append("大模型不可达（%s）——仅用规则解析结果" % repr(e)[:80])
        return 0


def parse_nl(text, use_llm=True):
    """自然语言 → 回测参数。返回 {ok, params, notes, unknown, engine}。"""
    text = str(text or "").strip()
    params, notes, unknown = {}, [], []
    params, notes, unknown = _rule_parse(text, params, notes, unknown)
    if use_llm and (len(notes) < 3 or unknown):
        _llm_parse(text, params, set(params.keys()), notes, unknown)
    if not params:
        return {
            "ok": False,
            "params": {},
            "notes": notes,
            "unknown": unknown,
            "error": "没能从这句话识别出任何回测参数——换个说法试试，或手动填表单",
        }
    if len(notes) <= 4:
        notes.append(
            "提示：这句话可解析的内容有限——解析结果仅供参考，请核对下方表单再回测"
        )
    return {
        "ok": True,
        "params": params,
        "notes": notes,
        "unknown": unknown,
        "engine": "规则"
        + ("+LLM(qwen3.5:4b)" if any("LLM 补充" in x for x in notes) else ""),
    }


if __name__ == "__main__":
    import sys

    r = parse_nl(
        sys.argv[1]
        if len(sys.argv) > 1
        else "最近一年全市场只看三买，持仓10只，单票5%到10%随机"
    )
    print(json.dumps(r, ensure_ascii=False, indent=1))
