# -*- coding: utf-8 -*-
"""新闻情报台聚合出口 mixin（K-ORG-207 三分栏新闻视图，2026-10-19）。

GET /api/news/intel（零参数）→ 三栏聚合：
  flash     速报栏：news.db 只读（sqlite mode=ro uri），近 48h news 表按 id
            倒序 top 80（ts/title/source/hit/pushed；hit=1 表示自选股/
            关键词命中并已推送）；ts 解析出 hm（HH:MM）供前端展示。
  policy    政策栏：读时分类不改库——标题或 content 命中 policy_keywords
            （news_collector/news_config.json 新键，读不到时用内置默认表
            兜底）；命中者再按 policy_authority_sources 白名单打
            authority=true/false 分层（营养度：权威源 vs 一般源）。
  sentiment 舆情栏：复用 api_xq_intel.read_xq_intel("") 读最新
            xq_intel 报告做摘要投影（report_exists=false 是合法空态，
            只报 hint 不记 error；坏 JSON 才 fail-visible 记 error）。

只读纪律（沿 api_xq_intel / api_notify 判例）：mode=ro / no-store（_json
统一注入）/ fail-visible——任一数据源失败填 errors 对应键，其余栏照常
返回，不假绿、不整包 500。
口径澄清：sources.yaml（zerobound-kg/ingestion/conf/）是 KG 采集域权威
清单；新闻采集源权威 = news_collector/news_config.json +
news_collector.SOURCES，本出口不读 sources.yaml、也无人读它定新闻源。
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import api_xq_intel

# REPO 锚：env AIOS_ROOT 可覆盖（仅测试用，调用时读取——沿 api_notify 判例，
# 模块级常量会让 monkeypatch env 失效）
FLASH_HOURS = 48       # 速报/政策回看窗
FLASH_LIMIT = 80       # 速报栏条目上限（拍板值）
POLICY_SCAN = 500      # 政策栏扫描深度（id 倒序，防止全表扫）
POLICY_LIMIT = 80      # 政策栏条目返回上限
SENTIMENT_ITEMS = 10   # 舆情栏高置信命中展示条数

# 政策关键词内置默认表（news_config.json 读不到时兜底；与配置新键同步维护）
DEFAULT_POLICY_KEYWORDS = (
    "国务院", "国常会", "央行", "中国人民银行", "证监会", "金融监管总局",
    "发改委", "财政部", "工信部", "商务部", "政治局", "人行", "部委",
    "条例", "管理办法", "指导意见", "工作要点", "白皮书")

# 权威源白名单默认（news_collector.SOURCES 新注册的政策权威采集源；
# news_config.json 读不到时兜底）
DEFAULT_AUTHORITY_SOURCES = ("govpolicy", "chinanews")


def _repo():
    return Path(os.environ.get("AIOS_ROOT", "C:/aios"))


def load_policy_cfg():
    """读 news_config.json 的政策关键词表与权威源白名单。

    读不到/坏 JSON/键缺失 → 原样回退内置默认表（拍板口径：内置表兜底，
    不算错误，不进 errors）。
    """
    kw, auth = DEFAULT_POLICY_KEYWORDS, DEFAULT_AUTHORITY_SOURCES
    try:
        cfg = json.loads(
            (_repo() / "news_collector" / "news_config.json")
            .read_text(encoding="utf-8"))
        v = cfg.get("policy_keywords")
        if isinstance(v, list) and v:
            kw = tuple(str(x) for x in v if x)
        v = cfg.get("policy_authority_sources")
        if isinstance(v, list) and v:
            auth = tuple(str(x) for x in v if x)
    except Exception:
        pass  # 兜底路径，fail-visible 不适用（拍板明示内置默认表兜底）
    return kw, auth


def _connect_ro():
    """news.db 只读连接（mode=ro uri，读侧绝不建表/写库）。"""
    p = _repo() / "news_collector" / "news.db"
    uri = "file:" + p.as_posix() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def read_news_columns(kw, auth):
    """速报栏 + 政策栏：一次只读连接产出两栏投影（fail-visible）。

    返回 (flash, policy, error)；error 非 None 时两栏为空态占位，
    由调用方填入 errors。ts 统一解析出 hm（HH:MM）供前端展示。
    """
    flash = {"items": [], "total": 0}
    policy = {"items": [], "total": 0, "authority_count": 0}
    try:
        since = (datetime.now() - timedelta(hours=FLASH_HOURS)
                 ).strftime("%Y-%m-%d %H:%M")
        db = _connect_ro()
        try:
            # 速报栏：近 48h 按 id 倒序 top 80（拍板口径；content 供前端点击展开）
            flash["total"] = db.execute(
                "SELECT COUNT(*) FROM news WHERE ts>=?", (since,)).fetchone()[0]
            for ts, title, content, source, hit, pushed in db.execute(
                    "SELECT ts,title,content,source,hit,pushed FROM news "
                    "WHERE ts>=? ORDER BY id DESC LIMIT ?", (since, FLASH_LIMIT)):
                flash["items"].append({
                    "ts": ts, "hm": (ts or "")[-5:], "title": title,
                    "content": content or "", "source": source,
                    "hit": bool(hit), "pushed": bool(pushed)})
            # 政策栏：同一窗口深扫描后读时分类（不改库）
            matches, pol_hits = [], 0
            for ts, title, content, source in db.execute(
                    "SELECT ts,title,content,source FROM news "
                    "WHERE ts>=? ORDER BY id DESC LIMIT ?", (since, POLICY_SCAN)):
                text = (title or "") + (content or "")
                if any(k in text for k in kw):
                    is_auth = source in auth
                    if is_auth:
                        pol_hits += 1
                    matches.append({
                        "ts": ts, "hm": (ts or "")[-5:], "title": title,
                        "content": (content or "")[:300], "source": source,
                        "authority": is_auth})
            policy["items"] = matches[:POLICY_LIMIT]
            policy["total"] = len(matches)
            policy["authority_count"] = pol_hits
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        return flash, policy, "news.db 读取失败: %r" % e
    return flash, policy, None


def read_sentiment():
    """舆情栏：复用 api_xq_intel 的加载函数读最新日报，摘要投影。

    report_exists=false（未生成/首跑前）是合法观测态 → 返回空态 + hint，
    不记 error（api_xq_intel 判例）；坏 JSON 才带 error fail-visible。
    """
    rep = api_xq_intel.read_xq_intel("")
    if not rep.get("report_exists"):
        return {"report_exists": False,
                "hint": rep.get("hint", "日报未生成，由 xq_daily_report.py 每日 19:00 产出")}
    if rep.get("error"):
        return {"report_exists": True, "error": rep["error"]}
    # 摘要投影：计数 + 情绪分布 + 主题/标的 Top + 高置信命中 + 异动信号
    def _top(d, n):
        try:
            return [[k, v] for k, v in
                    sorted(((k, v) for k, v in (d or {}).items()),
                           key=lambda kv: -kv[1])[:n]]
        except Exception:
            return []
    hc = []
    for r in (rep.get("high_confidence") or [])[:SENTIMENT_ITEMS]:
        if isinstance(r, dict):
            hc.append({"code": r.get("code"), "title": r.get("title"),
                       "sentiment": r.get("sentiment"),
                       "confidence": r.get("confidence"),
                       "event_type": r.get("event_type")})
    anom = []
    for r in (rep.get("anomaly_signals") or [])[:5]:
        if isinstance(r, dict):
            anom.append({"code": r.get("code"), "n": r.get("n"),
                         "bearish": r.get("bearish"),
                         "baseline6d": r.get("baseline6d")})
    return {"report_exists": True, "date": rep.get("date"),
            "generated_at": rep.get("generated_at"),
            "ingested_today": rep.get("ingested_today", 0),
            "classified_today": rep.get("classified_today", 0),
            "sentiment": rep.get("sentiment") or {},
            "top_themes": _top(rep.get("top_themes"), 8),
            "top_codes": _top(rep.get("top_codes"), 8),
            "high_confidence": hc, "anomaly": anom}


def news_intel_payload():
    """三栏聚合总装（fail-visible：单栏失败填 errors 对应键不假绿）。"""
    kw, auth = load_policy_cfg()
    errors = {}
    flash, policy, err = read_news_columns(kw, auth)
    if err:
        errors["flash"] = err
        errors["policy"] = err
    try:
        sentiment = read_sentiment()
    except Exception as e:  # noqa: BLE001
        sentiment = {"report_exists": False}
        errors["sentiment"] = repr(e)
    if sentiment.get("error"):
        errors["sentiment"] = sentiment["error"]
    return {"flash": flash, "policy": policy, "sentiment": sentiment,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "errors": errors}


class Handler:  # Mixin 壳：经 portal_api 基座多继承并入
    def _news_intel(self, qs):
        # 零参数出口：参数白名单为空集，带任何参数走 400（api_perf 判例）
        if self._norm_qs(qs):
            self._json({"error": "本出口不接受任何参数"}, 400)
            return
        self._json(news_intel_payload())
