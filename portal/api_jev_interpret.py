# -*- coding: utf-8 -*-
"""AI 解读结论接口 —— `/api/jev/interpret`（2026-11-07）。

老田口径：数据解读后要有 AI 结论——看到这组数据解读出什么结果，要告知人。

    GET  /api/jev/interpret?kind=story&code=300750&days=30
    GET  /api/jev/interpret?kind=intraday&code=300750
    GET  /api/jev/interpret?kind=evidence&q=回购&days=7

流程：复用既有 API 的 compute()（同进程直调，零 HTTP）→ 把结构化数据
压成紧凑事实摘要 → 本地 Ollama（qwen3.5，判读栈同款底座）生成结构化
中文结论（态势/验证/风险/建议四段）→ 模型不可用时降级为规则结论
（模板拼装，永远有话说）。

纪律：
    - 只读；不改任何库；prompt 只喂聚合统计（不含新闻全文，防注入面）；
    - 超时 60s 硬顶（Ollama 慢时用户可等可刷新走规则兜底）；
    - 结论标注生成方式（engine=local / cloud / rule）——谁说的可区分；
    - 本地算力不足（超时/不可用）自动降级云端：读 portal/cloud_llm.json
      或环境变量 JEV_CLOUD_LLM_URL / _KEY / _MODEL / _KIND（openai|anthropic）；
      云端也没配/也失败 → 规则结论。任何情况下都有结论。
"""
from __future__ import annotations

import source_names  # 渠道中文名
import datetime as dt
import io
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from urllib.parse import parse_qs

import api_jev_story
import api_jev_evidence
import api_jev_intraday

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLLAMA = "http://127.0.0.1:11434"
MODEL = "qwen3.5:4b"          # 轻模型先行（快）；4b 不够再换 9b
TIMEOUT_S = 150
BRIEF_DB = os.path.join(STATION, "agent_platform", "data", "intel",
                        "intel_facts.db")
BRIEF_KIND_DAYS = 30          # 沉淀卡固定按 30 日窗生成，其它窗口走在线

SYSTEM_PROMPT = (
    "你是情报分析员，不是行情解说员。给你一只标的在窗口内的全部事实："
    "时间线上的事件（含事件类型与人/地/事/物/组织五要素）、消息与价格的"
    "互相验证统计、量价形态。你要从情报学角度研判：这些事件在时间上如何"
    "排布、在空间上涉及哪些地域与产业链环节、彼此如何关联，最后给出态势"
    "判断与下一个观察点。输出固定五段，全文不超过 300 字：\n"
    "【事件面】窗口内发生了什么，按事件类型归类，点出最关键的一两个事件；\n"
    "【时间线】事件的先后与节奏——谁先动、谁跟随、间隔多久、有无密集爆发；\n"
    "【空间面】涉及哪些地域 / 板块 / 产业链环节，影响扩散在哪一层；\n"
    "【关联面】事件彼此之间、以及与价格之间的印证或背离关系；\n"
    "【研判】当前态势定性 + 下一步该盯什么（给出一个具体观察点）。\n"
    "只依据给定事实推断，不臆造未出现的公司、地点或数字。直接输出，不要客套。"
)


def _facts(kind: str, code: str = "", q: str = "", days: int = 30) -> str:
    """聚合既有计算 → 紧凑事实摘要（给模型的全部输入）。"""
    if kind == "story":
        d = api_jev_story.compute(code, days, 60)
        if not d.get("ok", True):
            raise ValueError(d.get("reason", "story 不可用"))
        cf = d.get("confirm", {})
        t3 = (d.get("impact_stats", {}).get("by_window", {}) or {}).get("t3")
        s0 = (d.get("sources") or [{}])[0]
        qs = d.get("quotes") or []
        ev = d.get("timeline") or []

        # ── 形态事实 ──
        shape = ""
        if len(qs) >= 3:
            closes = [q["close"] for q in qs]
            hi, lo = max(closes), min(closes)
            hi_i, lo_i = closes.index(hi), closes.index(lo)
            cum = round((closes[-1] / closes[0] - 1) * 100, 1)
            # 简单结构判定
            half = len(closes) // 2
            trend = ("上行" if cum > 3 else "下行" if cum < -3 else "横盘")
            if hi_i > lo_i and hi_i > half and cum > 0:
                struct = "低点在前高点在后（先抑后扬）"
            elif lo_i > hi_i and lo_i > half:
                struct = "高点在前低点在后（冲高回落）"
            else:
                struct = "高低点交错（震荡）"
            hi_d, lo_d = qs[hi_i]["date"], qs[lo_i]["date"]
            shape = ("窗口 %d 天累计 %s%.1f%%；最高 %.2f（%s）最低 %.2f（%s），"
                     "振幅 %.1f%%；结构：%s，趋势偏%s。" % (
                         d.get("window_days"), "+" if cum > 0 else "", cum,
                         hi, str(hi_d), lo, str(lo_d),
                         round((hi / lo - 1) * 100, 1), struct, trend))

        # ── 量价事实 ──
        vp = ""
        if qs and any(q.get("vol") for q in qs):
            vols = [q.get("vol") or 0 for q in qs]
            up_v = [v for q, v in zip(qs, vols) if (q["pct_chg"] or 0) > 0]
            dn_v = [v for q, v in zip(qs, vols) if (q["pct_chg"] or 0) < 0]
            avg_up = sum(up_v) / len(up_v) if up_v else 0
            avg_dn = sum(dn_v) / len(dn_v) if dn_v else 0
            ratio = avg_up / avg_dn if avg_dn else None
            mv_i = vols.index(max(vols))
            mv_day = qs[mv_i]
            vp_kind = ("放量上涨为主（涨日均量/跌日均量≈%.1f，量价配合）" % ratio
                       if ratio and ratio > 1.2 else
                       "缩量上涨/放量下跌（涨日均量/跌日均量≈%.1f，量价背离）" % ratio
                       if ratio and ratio < 0.85 else "量能均衡（涨跌日量比≈1）")
            vp = ("量价：%s；最大量能日 %s（量 %s 万手，当日 %s%.2f%%）。" % (
                vp_kind, str(mv_day["date"]),
                round(mv_day["vol"] / 100) / 100 if mv_day.get("vol") else "?",
                "+" if (mv_day["pct_chg"] or 0) > 0 else "",
                mv_day["pct_chg"] or 0))

        # ── 消息归因事实 ──
        news = ""
        if ev:
            bull_n = sum(1 for e in ev if e["bias"] == "bull")
            bear_n = sum(1 for e in ev if e["bias"] == "bear")
            et_cnt = {}
            for e in ev:
                et = e.get("event_type") or "other"
                et_cnt[et] = et_cnt.get(et, 0) + 1
            top_et = sorted(et_cnt.items(), key=lambda kv: -kv[1])[:3]
            # 五要素聚合（情报学口径：人 / 地 / 事 / 物 / 组织）——研判的地基。
            # 来源于 intel_facts.db（tools/intel_facts_backfill.py 抽取）；
            # 该库未回填时各要素为空，句子退化为"无"，不影响其余事实。
            agg = {"org": {}, "place": {}, "thing": {}}
            for e in ev:
                for k in agg:
                    for w in (e.get(k) or []):
                        agg[k][w] = agg[k].get(w, 0) + 1

            def _top(dd, n=5):
                return "、".join(
                    "%s(%d)" % (k, v) for k, v in
                    sorted(dd.items(), key=lambda kv: -kv[1])[:n]) or "无"
            # 代表性标题：最高置信利多/利空各一
            rep = {}
            for e in sorted(ev, key=lambda x: -(x.get("conf") or 0)):
                if e["bias"] not in rep and e["bias"] in ("bull", "bear"):
                    rep[e["bias"]] = e
            conf_rate = (cf.get("confirmed", 0) /
                         max(1, cf.get("confirmed", 0) + cf.get("refuted", 0)) * 100)
            parts = ["消息：窗口内事件 %d 个（利多 %d/利空 %d），类型分布 %s。" % (
                len(ev), bull_n, bear_n,
                "、".join("%s×%d" % k for k in top_et))]
            parts.append("要素：组织 %s；地域 %s；品种 %s。" % (
                _top(agg["org"]), _top(agg["place"]), _top(agg["thing"])))
            for b, e in rep.items():
                parts.append("最高置信%s：%s（%s，%s，conf=%s）。" % (
                    "利多" if b == "bull" else "利空",
                    (e.get("title") or "")[:34],
                    (e.get("ts") or "")[:10],
                    source_names.cn(e.get("source")), e.get("conf")))
            parts.append("消息可信度：证实率 %.0f%%（证实 %d/证伪 %d）；"
                         "事件后3日同向概率 %s。" % (
                             conf_rate, cf.get("confirmed", 0), cf.get("refuted", 0),
                             t3 and "%.0f%%" % (t3.get("same_dir_rate", 0) * 100)))
            parts.append("最可信信源 %s（准确率 %s）。" % (
                s0.get("source_cn") or source_names.cn(s0.get("source")),
                s0.get("accuracy") and "%.0f%%" % (s0["accuracy"] * 100)))
            news = "".join(parts)

        facts = ("标的 %s（%s）：%s%s%s" % (
            d.get("ts_code"), d.get("verdict", "")[:0] or "", shape, vp, news)
        ).replace("：：", "：")
        return facts

    if kind == "intraday":
        d = api_jev_intraday.compute(code, "")
        if not d.get("available"):
            # 非交易日/无数据：直接规则结论告知（不 503）
            return "标的 %s：%s。建议改查 story（日线级叙事）或换日期。" % (
                code, d.get("reason", "当日无分钟数据"))
        rx = d.get("reactions", [])
        m5 = [r["chg"]["m5"] for r in rx]
        bars = d.get("bars", [])
        close_pct = bars[-1]["cum_pct"] if bars else None
        facts = (
            "标的 %s，交易日 %s。5分钟棒 %d 根，收盘累计 %s%%（基准%s）。"
            "当日方向性事件 %d 个；事件后5分钟方向归一变化：同向 %d/%d，"
            "最大即时反应 %s%%。规则结论：%s" % (
                d.get("ts_code"), d.get("date"), len(bars),
                close_pct, d.get("base", {}).get("kind"),
                len(d.get("events", [])),
                sum(1 for x in m5 if x > 0), len(m5),
                ("%.2f" % max(m5, key=abs)) if m5 else "无",
                d.get("verdict", ""))
        )
        return facts

    if kind == "evidence":
        d = api_jev_evidence.compute(q, days, "day")
        f = d.get("flow", {})
        b = f.get("bias", {})
        facts = (
            "消息主题「%s」，窗口 %d 天。总量 %s 条，持续 %s 小时"
            "（%s → %s）。已判：利多 %d/利空 %d/中性 %d。"
            "主扩散渠道 %s（%s 条）。重叠交易日价格反应：%s。"
            "规则结论：%s" % (
                f.get("q"), days, f.get("total"), f.get("duration_h"),
                (f.get("first") or "")[:16], (f.get("last") or "")[:16],
                b.get("bull", 0), b.get("bear", 0), b.get("neutral", 0),
                (f.get("sources") or [{}])[0].get("source_cn")
                or source_names.cn((f.get("sources") or [{}])[0].get("source")),
                (f.get("sources") or [{}])[0].get("n"),
                "涨跌互见" if len(d.get("impact", [])) > 2 else "样本少",
                d.get("verdict", ""))
        )
        return facts

    raise ValueError("kind 须为 story / intraday / evidence")


def _rule_conclusion(kind: str, facts: str) -> str:
    """规则兜底结论：从 facts 里抽规则 verdict 尾句重排（永远可用）。"""
    m = re.search(r"规则结论：(.+)$", facts)
    base = (m.group(1) if m else facts)[:300]
    return ("【态势】%s\n【验证】见上判定与价格对齐数据（规则汇总）。\n"
            "【风险】消息面与价格面若背离，以价格为准。\n"
            "【建议】查看叙事线/举证页核对细节后再决策。\n"
            "（本结论由规则引擎生成——AI 模型暂不可用）"
            % base.split("。")[0] + "。")


def _ollama_conclude(facts: str, timeout: float = None) -> str:
    """本地 Ollama 生成结论；失败抛异常由上层降级。"""
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "数据事实摘要如下：\n" + facts},
        ],
        "stream": False,
        "options": {"temperature": 0.3},
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA + "/api/chat", data=body,
        headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout or TIMEOUT_S) as r:
        d = json.loads(r.read().decode("utf-8"))
    return (d.get("message", {}).get("content") or "").strip()


# ── 本机 LLM 门面通道（2026-11-08：实测本地 Ollama 已不可用，改走门面）──
# 背景：本机无独显（AMD 8060S 核显），qwen3.5:4b 单条 30 日研判实测 151 秒
# 仍超时降级；而 agent_platform/gateway/llm_gateway 的 deepseek-flash 同一
# prompt 5.4 秒返回。故把门面通道提到首选，本地 Ollama 退为断网兜底。
#
# binding 可配（env JEV_GATEWAY_BINDING）：
#   deepseek/deepseek-flash  默认（实测 ~5s，席位级降级链主力）
#   glm/glm-5.3              云端（2026-11-08 实测 429 限流，备用）
#   openai/gpt-6.0           OpenClaw 载体（实测 ~33s，慢）
#   ollama/qwen3.5:9b        本地 9b（更慢，仅离线用）
GATEWAY_BINDING = (os.environ.get("JEV_GATEWAY_BINDING")
                   or "deepseek/deepseek-flash").strip()
GATEWAY_ENABLED = (os.environ.get("JEV_GATEWAY") or "1").strip().lower() not in (
    "0", "false", "no", "off")


def _gateway_conclude(facts: str, retry: int = 1):
    """走本机 LLM 门面（不直连外网，key/路由由门面自己管）。返回 (文本, 信封)。

    门面自身有重试，但实测偶发 429 / 超时仍会漏出（2026-11-08 一次批量试跑
    里 1/3 落到规则结论），故外层再兜一次；批量预生成时这点重试能显著提高
    研判卡出「真 AI 结论」的比例。
    """
    p = os.path.join(STATION, "agent_platform")
    if p in sys.path:
        sys.path.remove(p)
    sys.path.insert(0, p)          # 必须前置：portal/quant_backtest 也在 path 里
    from gateway.llm_gateway import chat
    last = ""
    for i in range(retry + 1):
        try:
            r = chat(GATEWAY_BINDING, SYSTEM_PROMPT,
                     "数据事实摘要如下：\n" + facts)
            if r.get("ok"):
                txt = ((r.get("data") or {}).get("text") or "").strip()
                if len(txt) >= 20:
                    return txt, r
                last = "网关输出过短"
            else:
                last = (r.get("error") or {}).get("message") or "网关返回失败"
        except Exception as e:  # noqa: BLE001
            last = "%s: %s" % (type(e).__name__, str(e)[:70])
        if i < retry:
            time.sleep(1.5)
    raise RuntimeError(last or "网关失败")


# ── 云端降级通道（2026-11-07：老田令——本地算力不够可调云端） ──
# 配置放在 webroot 之外（portal/ 是 nginx 站点根，key 不能落在那儿被下载）
CLOUD_CFG = os.path.join(STATION, "config", "cloud_llm.json")


def _cloud_providers() -> list:
    """云端模型配置：环境变量优先，其次 portal/cloud_llm.json；无配置返回 []。

    环境变量（单通道，最省事）：
        JEV_CLOUD_LLM_URL    如 https://api.example.com/v1
        JEV_CLOUD_LLM_KEY    API Key（只进环境变量/本地文件，不落日志）
        JEV_CLOUD_LLM_MODEL  模型名
        JEV_CLOUD_LLM_KIND   openai（默认，兼容 /chat/completions）| anthropic
    """
    url = (os.environ.get("JEV_CLOUD_LLM_URL") or "").strip()
    if url:
        return [{"name": "env", "kind": (os.environ.get("JEV_CLOUD_LLM_KIND")
                                        or "openai").strip().lower(),
                 "base_url": url.rstrip("/"),
                 "api_key": (os.environ.get("JEV_CLOUD_LLM_KEY") or "").strip(),
                 "model": (os.environ.get("JEV_CLOUD_LLM_MODEL")
                           or "gpt-4o-mini").strip(),
                 "timeout_s": float(os.environ.get("JEV_CLOUD_LLM_TIMEOUT") or 60)}]
    try:
        with io.open(CLOUD_CFG, encoding="utf-8") as f:
            cfg = json.load(f) or {}
    except Exception:  # noqa: BLE001 —— 没配置文件 = 未启用云端
        return []
    if not cfg.get("enabled"):
        return []
    out = []
    for p in (cfg.get("providers") or []):
        if not p.get("base_url") or not p.get("model"):
            continue
        key = (p.get("api_key") or "").strip()
        if not key:
            key = (os.environ.get(p.get("api_key_env") or "") or "").strip()
        if not key:
            continue
        out.append({"name": p.get("name") or p["model"],
                    "kind": (p.get("kind") or "openai").lower(),
                    "base_url": p["base_url"].rstrip("/"),
                    "api_key": key, "model": p["model"],
                    "timeout_s": float(p.get("timeout_s") or 60)})
    return out


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _cloud_conclude(p: dict, facts: str) -> str:
    """按 provider 类型调云端；返回正文，失败抛异常。"""
    user = "数据事实摘要如下：\n" + facts
    if p["kind"] == "anthropic":
        url = p["base_url"] + "/v1/messages" if not p["base_url"].endswith(
            "/messages") else p["base_url"]
        body = json.dumps({
            "model": p["model"], "max_tokens": 900, "temperature": 0.3,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user}],
        }).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   "x-api-key": p["api_key"],
                   "anthropic-version": "2023-06-01"}
    else:  # openai 兼容（含各类国内兼容网关）
        url = (p["base_url"] + "/chat/completions"
               if p["base_url"].endswith("/v1")
               else p["base_url"] + "/v1/chat/completions")
        body = json.dumps({
            "model": p["model"], "temperature": 0.3, "max_tokens": 900,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": user}],
        }).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   "Authorization": "Bearer " + p["api_key"]}
    req = urllib.request.Request(url, data=body, headers=headers)
    with _opener().open(req, timeout=p["timeout_s"]) as r:
        d = json.loads(r.read().decode("utf-8"))
    if p["kind"] == "anthropic":
        blocks = d.get("content") or []
        return "".join(b.get("text") or "" for b in blocks
                       if isinstance(b, dict)).strip()
    return ((d.get("choices") or [{}])[0].get("message", {})
            .get("content") or "").strip()


def _cloud_config_hint() -> str:
    if _cloud_providers():
        return "云端已配置但调用失败"
    return ("云端未配置（写 config/cloud_llm.json 或设 "
            "JEV_CLOUD_LLM_URL/KEY/MODEL 即启用）")


_RESULT_CACHE: dict = {}
_RESULT_TTL = 300  # 5 分钟：同一数据组合的结论不重复推理


def _sediment(code: str, days: int):
    """读「盘后预生成」的研判卡（tools/intel_brief_pregen.py 产出）。

    为什么要先读它：在线链路 = facts 聚合 + 本地模型推理（实测 >100s/只），
    770 只关注池不可能现算。研判卡在盘后就绪，打开页面直接命中 → 0 秒。

    口径：
      · 只认 30 日窗（研判卡按 30 日生成），其它窗口照旧走在线；
      · 取最近一张，且交易日距今 ≤1 天——超过即视为过期，宁可在线重算，
        也不给用户看隔夜失效的结论；
      · 库/表不存在、字段坏 → 返回 None，静默回落在线，绝不抛。
    """
    if days != BRIEF_KIND_DAYS:
        return None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % BRIEF_DB.replace("\\", "/"),
                              uri=True, timeout=10)
        try:
            row = con.execute(
                "SELECT trade_date, facts, conclusion, engine, engine_label,"
                " model, degrade_note, name, built_at FROM intel_briefs"
                " WHERE code=? AND kind='story'"
                " ORDER BY trade_date DESC LIMIT 1", (code,)).fetchone()
        finally:
            con.close()
    except Exception:  # noqa: BLE001 —— 研判卡库可重建，缺失即回落在线
        return None
    if not row:
        return None
    td, facts, concl, eng, elabel, model, note, name, built = row
    try:
        age = (dt.date.today() - dt.date.fromisoformat(str(td))).days
    except Exception:  # noqa: BLE001
        return None
    if age > 1:
        return None
    return {
        "ok": True, "schema": "jev_interpret.v1",
        "kind": "story", "code": code, "name": name,
        "engine": eng or "sediment",
        "engine_label": "预生成研判卡",
        "model": model,
        "degrade_note": note,
        "facts": facts,
        "conclusion": concl,
        "generated_at": built,
        "sediment": {"trade_date": td, "built_at": built, "stale": age > 0,
                     "source": "intel_briefs"},
    }


def interpret(kind: str, code: str = "", q: str = "", days: int = 30,
              use_sediment: bool = True, force: bool = False) -> dict:
    # ① 沉淀卡（浏览前已梳理好）——命中即 0 延迟
    if use_sediment and not force and kind == "story":
        s = _sediment(code, days)
        if s:
            return s
    ck = (kind, code, q, days)
    hit = _RESULT_CACHE.get(ck)
    if hit and (dt.datetime.now() - hit[0]).total_seconds() < _RESULT_TTL:
        return hit[1]
    facts = _facts(kind, code, q, days)
    # ① 本机 LLM 门面（首选：实测同任务 ~5s，本地 Ollama >150s 仍超时）
    engine, text, model_used, note = "local", None, MODEL, None
    if GATEWAY_ENABLED:
        try:
            text, _env = _gateway_conclude(facts)
            engine, model_used = "cloud", "gateway:" + GATEWAY_BINDING
        except Exception as e:  # noqa: BLE001 —— 门面失败 → 本地兜底
            note = "门面失败：%s" % str(e)[:60]

    # ② 本地模型（断网兜底：不出网、零成本；超时按 env 收紧，别让用户干等）
    if not text:
        engine, model_used = "local", MODEL
        try:
            text = _ollama_conclude(
                facts, timeout=float(os.environ.get("JEV_OLLAMA_TIMEOUT") or 45))
            if not text or len(text) < 20:
                raise RuntimeError("模型输出过短")
        except Exception as e:  # noqa: BLE001 —— 本地不可用 → 转云端
            note = (note + "；" if note else "") + "本地模型失败：%s" % str(e)[:50]
            text = None

    # ③ 云端直连（config/cloud_llm.json 或 env，独立于门面的直连通道）
    if not text:
        for p in _cloud_providers():
            try:
                t = _cloud_conclude(p, facts)
                if t and len(t) >= 20:
                    engine, text = "cloud", t
                    model_used = "%s@%s" % (p["model"], p["name"])
                    note = (note or "") + "；已切云端 %s" % p["name"]
                    break
                note = (note or "") + "；云端 %s 输出过短" % p["name"]
            except Exception as e:  # noqa: BLE001
                note = (note or "") + "；云端 %s 失败：%s" % (
                    p["name"], str(e)[:50])

    # ③ 规则兜底（永远有结论）
    if not text:
        engine, text = "rule", _rule_conclusion(kind, facts)
        model_used = None
        note = (note or "") + "；" + _cloud_config_hint()

    ENGINE_CN = {"local": "本地模型", "cloud": "云端模型", "rule": "规则引擎"}
    result = {
        "ok": True, "schema": "jev_interpret.v1",
        "kind": kind, "code": code or q,
        "engine": engine,
        "engine_label": ENGINE_CN.get(engine, engine),
        "model": model_used,
        "degrade_note": note,
        "facts": facts,
        "conclusion": text,
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _RESULT_CACHE[ck] = (dt.datetime.now(), result)
    return result


def handle_get(handler, path: str, qs_raw: str) -> None:
    qs = parse_qs(qs_raw or "")
    try:
        kind = (qs.get("kind") or [""])[-1].strip()
        code = (qs.get("code") or [""])[-1].strip()
        q = (qs.get("q") or [""])[-1].strip()
        days = int((qs.get("days") or ["30"])[-1])
        if not 1 <= days <= 120:
            raise ValueError("days 1..120")
        if kind == "story" or kind == "intraday":
            if not re.match(r"^\d{6}(\.(SH|SZ|BJ))?$", code):
                raise ValueError("code 须为 6 位代码")
        elif kind == "evidence":
            if not q:
                raise ValueError("q 必填（关键词或代码）")
        else:
            raise ValueError("kind 须为 story / intraday / evidence")
        # peek=1：只探沉淀卡，绝不触发推理。前端页面加载时用它判断
        # 「打开即有结论」还是「需要用户点一下现算」，避免把用户拖进
        # 100 秒以上的等待。
        if (qs.get("peek") or ["0"])[-1].strip().lower() in (
                "1", "true", "yes") and kind == "story":
            s = _sediment(code, days)
            handler._json(s if s else {"ok": False, "peek": True,
                                       "reason": "no_sediment"}, 200)
            return
        refresh = (qs.get("refresh") or ["0"])[-1].strip().lower() in (
            "1", "true", "yes", "y")
        result = interpret(kind, code, q, days, force=refresh)
    except ValueError as exc:
        handler._json({"ok": False, "error": "invalid_params",
                       "message": str(exc)}, 400)
        return
    except Exception as exc:  # noqa: BLE001
        handler._json({"ok": False, "error": "unavailable",
                       "message": str(exc)[:200]}, 503)
        return
    handler._json(result)
