# -*- coding: utf-8 -*-
"""新闻情报台聚合出口 mixin（K-ORG-208 判读输出台视图，2026-10-19）。

GET /api/news/intel（零参数）→ 判读结论在前、原料流水在后的聚合：
  overview   总览区：近 48h jev 非空条目计数 {total_judged, bull, bear,
             neutral, high_impact}（impact_cn='高' 计高冲击）。
  verdicts   关键判读区：近 48h jev 非空条目按（impact 权重降序, conf 降序,
             id 降序）排序 top 20，字段 ts/hm/title/content/source/stocks/
             jev；content 截 300 字（政策栏同口径，K-ORG-209 行卡展开用）；
             排序口径拍板依据：JEV 置信度右偏（真实库 conf>=0.9 占 34%）
             分辨率差，冲击为主、conf 只做次级（K-ORG-208）。
  flash      速报栏（原料流）：news.db 只读（sqlite mode=ro uri），近 48h
             news 表按 **ts 倒序**（ts DESC, created_at DESC, id DESC；2026-10-31
             排序拍板：旧 id 倒序会让旧闻顶第一屏）top 80（ts/title/source/
             hit/pushed/jev）；hit=1 表示自选股/关键词命中并已推送；ts 解析
             出 hm（HH:MM）；jev 为解析投影 dict（坏 JSON/空值置 null 不炸）。
  policy     政策栏：读时分类不改库——标题或 content 命中 policy_keywords
             （news_collector/news_config.json 新键，读不到时用内置默认表
             兜底）；命中者再按 policy_authority_sources 白名单打
             authority=true/false 分层（营养度：权威源 vs 一般源）；同 ts 倒序。
  sentiment  舆情栏：复用 api_xq_intel.read_xq_intel("") 读最新
             xq_intel 报告做摘要投影（report_exists=false 是合法空态，
             只报 hint 不记 error；坏 JSON 才 fail-visible 记 error）。

jev 列解析口径（沿 api_backtest._bt_jev / api_data._news_db 判例）：过滤
jev IS NOT NULL AND jev!=''；bias 三值 bull/bear/neutral；坏 JSON 条目
不进 overview/verdicts 统计，flash 条目 jev 置 null——绝不因单条坏数据炸
整包。

只读纪律（沿 api_xq_intel / api_notify 判例）：mode=ro / no-store（_json
统一注入）/ fail-visible——任一分区失败填 errors 对应键，其余区照常
返回，不假绿、不整包 500。overview/verdicts 同源一次连接产出，失败时
共用一个 error 文案填两键（沿 read_news_columns 的 flash/policy 同款）。
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
import logging

# REPO 锚：env AIOS_ROOT 可覆盖（仅测试用，调用时读取——沿 api_notify 判例，
# 模块级常量会让 monkeypatch env 失效）
FLASH_HOURS = 48       # 速报/政策/判读回看窗
FLASH_LIMIT = 80       # 速报栏条目上限（拍板值）
POLICY_SCAN = 500      # 政策栏扫描深度（id 倒序，防止全表扫）
POLICY_LIMIT = 80      # 政策栏条目返回上限
SENTIMENT_ITEMS = 10   # 舆情栏高置信命中展示条数
VERDICT_LIMIT = 20     # 今日关键判读条数上限（K-ORG-208 拍板值）

# impact_cn → 排序权重（真实 news.db 近 48h 抽样确认取值集为 高/中/低/None，
# 2026-10-19 只读探测；None/未知值权重 0 落末位）
IMPACT_WEIGHT = {"高": 3, "中": 2, "低": 1}

# bias 英文键 → 中文（bias_cn 缺失时兜底；沿 api_backtest._bt_jev 判例）
BIAS_CN = {"bull": "利多", "bear": "利空", "neutral": "中性"}

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
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_news_intel.py", exc)
    return kw, auth


def _connect_ro():
    """news.db 只读连接（mode=ro uri，读侧绝不建表/写库）。"""
    p = _repo() / "news_collector" / "news.db"
    uri = "file:" + p.as_posix() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _parse_jev(raw):
    """解析 news.jev JSON → 前端徽标组所需最小投影；坏值一律返回 None 不炸。

    投影键：bias/bias_cn/conf/scope_cn/impact_cn（K-ORG-208 徽标组口径）。
    空串/坏 JSON/非 dict → None；conf 强转 float 失败置 None；
    bias_cn 缺失时按 BIAS_CN 由 bias 兜底（沿 api_backtest 判例）。
    """
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(d, dict):
        return None
    try:
        conf = float(d.get("conf"))
    except (TypeError, ValueError):
        conf = None
    bias = d.get("bias")
    return {"bias": bias if bias in BIAS_CN else None,
            "bias_cn": d.get("bias_cn") or BIAS_CN.get(bias, ""),
            "conf": conf,
            "scope_cn": d.get("scope_cn") or "",
            "impact_cn": d.get("impact_cn") or ""}


def _parse_stocks(raw):
    """news.stocks JSON 数组字符串 → 代码 list；坏 JSON/空/非数组 → []。"""
    if not raw:
        return []
    try:
        v = json.loads(raw)
    except Exception:  # noqa: BLE001
        return []
    return [str(x) for x in v] if isinstance(v, list) else []


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
            # 速报栏：近 48h 按事件时间 ts 倒序 top 80（2026-10-31 排序拍板：
            # ts DESC, created_at DESC, id DESC；content 供前端点击展开，
            # jev 随行带出供原料流条目展示判读徽标）
            flash["total"] = db.execute(
                "SELECT COUNT(*) FROM news WHERE ts>=?", (since,)).fetchone()[0]
            for ts, title, content, source, hit, pushed, raw in db.execute(
                    "SELECT ts,title,content,source,hit,pushed,jev FROM news "
                    "WHERE ts>=? ORDER BY ts DESC, created_at DESC, id DESC "
                    "LIMIT ?", (since, FLASH_LIMIT)):
                flash["items"].append({
                    "ts": ts, "hm": (ts or "")[-5:], "title": title,
                    "content": content or "", "source": source,
                    "hit": bool(hit), "pushed": bool(pushed),
                    "jev": _parse_jev(raw)})
            # 政策栏：同一窗口深扫描后读时分类（不改库）；同 ts 倒序
            matches, pol_hits = [], 0
            for ts, title, content, source in db.execute(
                    "SELECT ts,title,content,source FROM news "
                    "WHERE ts>=? ORDER BY ts DESC, created_at DESC, id DESC "
                    "LIMIT ?", (since, POLICY_SCAN)):
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


def read_verdicts():
    """判读区：近 48h jev 非空条目 → 总览计数 + 关键判读 top 20（fail-visible）。

    overview/verdicts 同源数据，一次只读连接产出两区（失败时共用一个
    error 文案，由调用方填 errors["overview"]/errors["verdicts"] 两键，
    沿 read_news_columns 的 flash/policy 同款模式）。

    排序口径（K-ORG-208 拍板）：impact 权重降序 > conf 降序 > id 降序——
    JEV 置信度右偏（真实库 conf>=0.9 占 34%）分辨率差，冲击为主、conf
    只做次级。坏 JSON 条目跳过，不计入 total_judged（非有效判读）。

    返回 (overview, verdicts, error)。
    """
    overview = {"total_judged": 0, "bull": 0, "bear": 0, "neutral": 0,
                "high_impact": 0}
    verdicts = {"items": [], "total": 0}
    try:
        since = (datetime.now() - timedelta(hours=FLASH_HOURS)
                 ).strftime("%Y-%m-%d %H:%M")
        db = _connect_ro()
        try:
            # 判例过滤：jev IS NOT NULL AND jev!=''（api_backtest._bt_jev 同款）；
            # SQL 基础序 = 事件时间倒序（2026-10-31 排序拍板：
            # ORDER BY ts DESC, created_at DESC, id DESC）；下方 python 稳定
            # 排序再按 impact/conf 降序，同分条目保持该基础序；
            # content 随行带出供前端行卡展开正文（K-ORG-209）
            rows = db.execute(
                "SELECT id,ts,title,content,source,stocks,jev FROM news "
                "WHERE ts>=? AND jev IS NOT NULL AND jev!='' "
                "ORDER BY ts DESC, created_at DESC, id DESC",
                (since,)).fetchall()
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        return overview, verdicts, "news.db 读取失败: %r" % e
    # 第一遍：解析 + 计数（遍历顺序即 ts 倒序基础序，供下方稳定排序保序）
    # 投影元组：(impact_weight, conf, id, ts, title, content, source,
    # stocks, jev)——content 截 300 字（政策栏 (content or "")[:300] 同款），
    # 供前端行卡展开正文
    scored = []
    for _id, ts, title, content, source, stocks, raw in rows:
        jev = _parse_jev(raw)
        if jev is None:
            continue  # 坏 JSON / 非 dict：不进判读统计，不炸
        overview["total_judged"] += 1
        b = jev["bias"]
        if b in overview:
            overview[b] += 1
        if jev["impact_cn"] == "高":
            overview["high_impact"] += 1
        scored.append((IMPACT_WEIGHT.get(jev["impact_cn"], 0),
                       jev["conf"] or 0, _id, ts, title,
                       (content or "")[:300], source,
                       _parse_stocks(stocks), jev))
    # 第二遍：impact 权重/conf 数值降序稳定排序（同分保持 id 降序）后取 top N
    scored.sort(key=lambda r: (-r[0], -r[1]))
    verdicts["total"] = len(scored)
    for (_w, _c, _id, ts, title, content,
         source, stocks, jev) in scored[:VERDICT_LIMIT]:
        verdicts["items"].append({
            "ts": ts, "hm": (ts or "")[-5:], "title": title,
            "content": content, "source": source, "stocks": stocks,
            "jev": jev})
    return overview, verdicts, None


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
    """判读输出台总装（fail-visible：分区失败填 errors 对应键不假绿）。"""
    kw, auth = load_policy_cfg()
    errors = {}
    flash, policy, err = read_news_columns(kw, auth)
    if err:
        errors["flash"] = err
        errors["policy"] = err
    overview, verdicts, err = read_verdicts()
    if err:
        errors["overview"] = err
        errors["verdicts"] = err
    try:
        sentiment = read_sentiment()
    except Exception as e:  # noqa: BLE001
        sentiment = {"report_exists": False}
        errors["sentiment"] = repr(e)
    if sentiment.get("error"):
        errors["sentiment"] = sentiment["error"]
    return {"overview": overview, "verdicts": verdicts,
            "flash": flash, "policy": policy, "sentiment": sentiment,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "errors": errors}


class Handler:  # Mixin 壳：经 portal_api 基座多继承并入
    def _news_intel(self, qs):
        # 零参数出口：参数白名单为空集，带任何参数走 400（api_perf 判例）
        if self._norm_qs(qs):
            self._json({"error": "本出口不接受任何参数"}, 400)
            return
        self._json(news_intel_payload())
