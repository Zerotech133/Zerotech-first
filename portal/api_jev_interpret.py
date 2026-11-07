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
import urllib.request
from urllib.parse import parse_qs

import api_jev_story
import api_jev_evidence
import api_jev_intraday

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLLAMA = "http://127.0.0.1:11434"
MODEL = "qwen3.5:4b"          # 轻模型先行（快）；4b 不够再换 9b
TIMEOUT_S = 150

SYSTEM_PROMPT = (
    "你是资深 A 股量价分析师。用户给你一只股票一段历史窗口的完整事实："
    "走势形态、量价关系、期间的消息与财报事件、以及消息与价格互相验证的"
    "统计。你要把这段行情『讲明白』——它走出了什么形态、量价如何配合、"
    "是什么消息驱动的、消息可信吗。输出固定四段：\n"
    "【形态】这段走势的技术形态定性（趋势方向/结构，如震荡上行、冲高回落、"
    "单边下挫），带关键数字；\n"
    "【量价】量价关系定性（放量上攻/缩量阴跌/量价背离等），指出最大量能日"
    "发生了什么；\n"
    "【消息与验证】期间什么消息/财报在驱动（利好利空类型+代表事件），消息面"
    "与价格互相验证还是背离（引用证实率/同向概率）；\n"
    "【结论】综合判断：这段行情的定性 + 对看数据的人的一条可执行建议。"
    "全文不超过 220 字，直接输出，不要客套。"
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


def _ollama_conclude(facts: str) -> str:
    """本地 Ollama 生成四段结论；失败抛异常由上层降级。"""
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
    with opener.open(req, timeout=TIMEOUT_S) as r:
        d = json.loads(r.read().decode("utf-8"))
    return (d.get("message", {}).get("content") or "").strip()


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


def interpret(kind: str, code: str = "", q: str = "", days: int = 30) -> dict:
    ck = (kind, code, q, days)
    hit = _RESULT_CACHE.get(ck)
    if hit and (dt.datetime.now() - hit[0]).total_seconds() < _RESULT_TTL:
        return hit[1]
    facts = _facts(kind, code, q, days)
    # ① 本地模型（算力够时首选：不出网、零成本）
    engine, text, model_used, note = "local", None, MODEL, None
    try:
        text = _ollama_conclude(facts)
        if not text or len(text) < 20:
            raise RuntimeError("模型输出过短")
    except Exception as e:  # noqa: BLE001 —— 本地不可用 → 转云端
        note = "本地模型失败：%s" % str(e)[:60]
        text = None

    # ② 云端模型（本地算力不够时的通道）
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
        result = interpret(kind, code, q, days)
    except ValueError as exc:
        handler._json({"ok": False, "error": "invalid_params",
                       "message": str(exc)}, 400)
        return
    except Exception as exc:  # noqa: BLE001
        handler._json({"ok": False, "error": "unavailable",
                       "message": str(exc)[:200]}, 503)
        return
    handler._json(result)
