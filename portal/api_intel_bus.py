# -*- coding: utf-8 -*-
"""情报总线唯一出口 mixin（六域→总线→双路径，2026-10-19 联调首包）。

架构口径（老田拍板 ASCII 图）：情报源六域（舆论/新闻/负面/正面/血缘/关联）
→ 情报总线（中台唯一出口，路由+结构化）→ 路径1 量化交易中心（结构化 JSON）
/ 路径2 飞书用户端（推送卡/通知，已通不动）。

本出口 = 总线路由层：把散点读取收拢为一个统一信封，量化侧读层 v1
（build_catalog 明注「JEV news.db 独立通道，读层 v1 未路由」）由此接线。

GET /api/intel/bus?domain=<news|jev|evidence|events>[&code=][&days=][&limit=]
                            [&since_id=]
  统一信封 {"ok","schema_ver","endpoint","domain","params","items","count",
            "source","errors"}；count=过滤后总数（不受 limit 截断）。
  news     原料流：news.db ro，窗口内 **ts 倒序**（ts DESC, created_at DESC,
            id DESC，2026-10-31 排序拍板：旧 id 倒序会让旧闻顶到第一屏），
            stocks 精确代码过滤，坏 jev → None 不炸（api_news_intel._parse_jev
            判例复用）；信封附新鲜度 as_of/latest_ts/age_s（数据截至）。
  jev      判定流：news.db ro，jev 非空且可解析 dict 才入选（量化对账基线
           = event_graph 的「jev IS NOT NULL AND jev!=''」读法），同 ts 倒序。
  since_id 水位模式（仅 news/jev，量化读点机器消费）：忽略 days/ts 窗口，
           改 SQL 级「rowid > since_id ORDER BY rowid ASC LIMIT limit」
           分页游标。items 各带 rid（news.rowid）且 jev=完整解析 dict
           （非前端投影，机器消费需 reason 等全键）；count=本页扫过行数
           （预过滤），errors.max_rowid=本页扫过末行 rid（含坏 jev 行，
           与 event_graph 原直读「坏行也推水位」语义 1:1）。分页终点：
           count < limit；消费方水位 = max_rowid 逐页推进，零丢行零滞留。
           水位语义依赖 rowid 升序，排序不随展示口径改成 ts（机器契约）。
  evidence 证据—标的流：委托 api_intel_evidence.load_evidence_targets
           （精确 ts_code 匹配口径不漂移），状态码透传 fail-visible
  events   事件流：quant_backtest/data/semantica/events.jsonl 只读投影
           （id/kind/ts/text/source/stocks/bias/conf/reason/tag），
           坏行跳过计数不炸；路径可用 env INTEL_BUS_EVENTS_PATH 覆盖（测试）

只读纪律（沿 api_intel_evidence / api_news_intel 判例）：sqlite mode=ro uri、
参数白名单、fail-visible（错误进 errors/ok=false，不整包假绿）、零写入。
命名说明：本文件是情报总线出口，与 K-ORG-207 新闻情报台（/api/news/intel
判读输出台视图）职责不同——那是前端视图聚合，这是机器消费统一出口。
"""

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import api_intel_evidence
import api_news_intel

_ENDPOINT = "/api/intel/bus"
_SCHEMA_VER = "1.0"
_CODE = api_intel_evidence._CODE  # A 股代码正则（同一口径不另造）
_DOMAINS = ("news", "jev", "evidence", "events")
_EVENTS_DEFAULT = (Path(__file__).resolve().parent.parent /
                   "quant_backtest" / "data" / "semantica" / "events.jsonl")


def _int_param(params, name, default, maximum):
    raw = params.get(name)
    if raw is None:
        return default
    if not re.fullmatch(r"[1-9]\d*", str(raw), re.ASCII) or len(str(raw)) > 3:
        raise ValueError("%s must be an integer 1..%d" % (name, maximum))
    n = int(raw)
    if n > maximum:
        raise ValueError("%s must be an integer 1..%d" % (name, maximum))
    return n


def validate_params(params):
    """白名单校验 → (domain, code, days, limit, since_id)；违规 ValueError。"""
    unknown = set(params) - {"domain", "code", "days", "limit", "since_id"}
    if unknown:
        raise ValueError("unknown parameter: %s" % sorted(unknown)[0])
    domain = params.get("domain")
    if domain not in _DOMAINS:
        raise ValueError("domain must be one of %s" % ",".join(_DOMAINS))
    code = params.get("code") or None
    if code is not None and not _CODE.fullmatch(code):
        raise ValueError("code must be an A-share symbol like 600000.SH")
    days = _int_param(params, "days", 7, 30)
    limit = _int_param(params, "limit", 50, 100)
    since_id = params.get("since_id")
    if since_id is not None:
        if domain not in ("news", "jev"):
            raise ValueError("since_id only supported for news/jev domains")
        raw = str(since_id)
        if not re.fullmatch(r"\d{1,12}", raw, re.ASCII):
            raise ValueError("since_id must be a non-negative integer")
        since_id = int(raw)
    return domain, code, days, limit, since_id


def _envelope(domain, params, items, count, source, errors=None, ok=True,
              freshness=None):
    out = {"ok": ok, "schema_ver": _SCHEMA_VER, "endpoint": _ENDPOINT,
           "domain": domain, "params": params, "items": items,
           "count": count, "source": source, "errors": errors or {}}
    if freshness:
        out.update(freshness)
    return out


def _db_path():
    return (Path(os.environ.get("AIOS_ROOT", "C:/aios"))
            / "news_collector" / "news.db")


def _window(days):
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    return since, datetime.now().strftime("%Y-%m-%d %H:%M")


def _freshness(latest_ts):
    """新鲜度信封：as_of=查询时刻，latest_ts=本域可见最新事件时间，age_s=滞后秒。

    无数据时 latest_ts/age_s 为 None（如实空态，不拿 as_of 冒充数据时间）。
    """
    now = datetime.now()
    as_of = now.strftime("%Y-%m-%d %H:%M:%S")
    latest = str(latest_ts or "").strip() or None
    age_s = None
    if latest:
        try:
            dt = datetime.strptime(latest[:16], "%Y-%m-%d %H:%M")
            age_s = max(0, int((now - dt).total_seconds()))
        except ValueError:
            age_s = None
    return {"as_of": as_of, "latest_ts": latest, "age_s": age_s}


def _domain_news(code, days, limit, jev_only, since_id=None):
    """news/jev 两域同源查询（一次 ro 连接；python 侧 stocks 精确过滤）。

    since_id 水位模式：忽略 days/ts 窗口，SQL 级 rowid>since_id 升序分页，
    items 带 rid + jev 完整 dict，errors.max_rowid=本页扫过末行 rid
    （含坏 jev 行——event_graph 原直读「坏行也推水位」判例语义）。
    """
    if since_id is not None:
        return _domain_news_since(code, limit, jev_only, since_id)
    items, count, bad_rows, latest_ts = [], 0, 0, None
    since, until = _window(days)
    uri = "file:" + _db_path().resolve().as_posix() + "?mode=ro"
    try:
        db = sqlite3.connect(uri, uri=True)
        try:
            # 2026-10-31 排序拍板：事件时间优先（ts DESC），同 ts 用入库时间/
            # id 兜底稳定；旧 ORDER BY id DESC 会让旧闻（补采/重判）顶到第一屏。
            rows = db.execute(
                "SELECT id,source,ts,created_at,title,content,stocks,jev "
                "FROM news WHERE ts>=? AND ts<=? "
                "ORDER BY ts DESC, created_at DESC, id DESC",
                (since, until)).fetchall()
        finally:
            db.close()
    except (sqlite3.Error, OSError) as e:
        return _envelope("jev" if jev_only else "news", {}, [], 0, "",
                         {"db": "news.db 读取失败: %r" % e}, ok=False), 503
    for nid, source, ts, ingested, title, content, stocks, raw_jev in rows:
        code_list = api_news_intel._parse_stocks(stocks)
        if code and code not in code_list:
            continue
        if jev_only:
            jev = api_news_intel._parse_jev(raw_jev)
            if jev is None:
                if raw_jev:  # 有值但坏 → 坏行计数；空值=未判定，静默略过
                    bad_rows += 1
                continue
        else:
            jev = api_news_intel._parse_jev(raw_jev)
        count += 1
        if latest_ts is None or str(ts or "") > str(latest_ts):
            latest_ts = ts
        if len(items) < limit:
            items.append({"id": nid, "ts": ts, "ingested_at": ingested,
                          "title": title or "", "content": content or "",
                          "source": source, "stocks": code_list, "jev": jev})
    domain = "jev" if jev_only else "news"
    return _envelope(
        domain, {"code": code, "days": days, "limit": limit}, items, count,
        "news.db@news_collector (sqlite mode=ro)",
        {"bad_rows": bad_rows} if bad_rows else {},
        freshness=_freshness(latest_ts)), 200


def _domain_news_since(code, limit, jev_only, since_id):
    """水位模式实现：rowid 游标分页（机器消费，jev=完整 dict + rid）。"""
    domain = "jev" if jev_only else "news"
    uri = "file:" + _db_path().resolve().as_posix() + "?mode=ro"
    sql = ("SELECT rowid AS rid, id, source, ts, created_at, title, "
           "content, stocks, jev FROM news WHERE rowid > ?")
    if jev_only:  # 已判定行口径（event_graph 直读基线同款 SQL 过滤）
        sql += " AND jev IS NOT NULL AND jev != ''"
    sql += " ORDER BY rowid ASC LIMIT ?"
    try:
        db = sqlite3.connect(uri, uri=True)
        try:
            rows = db.execute(sql, (since_id, limit)).fetchall()
        finally:
            db.close()
    except (sqlite3.Error, OSError) as e:
        return _envelope(domain, {"code": code, "since_id": since_id,
                                  "limit": limit}, [], 0, "",
                         {"db": "news.db 读取失败: %r" % e}, ok=False), 503
    items, scanned, bad_rows, latest_ts = [], len(rows), 0, None
    for rid, nid, source, ts, ingested, title, content, stocks, raw_jev in rows:
        code_list = api_news_intel._parse_stocks(stocks)
        if code and code not in code_list:
            continue
        if raw_jev:
            try:
                jev = json.loads(raw_jev)
                if not isinstance(jev, dict):
                    jev = None
            except ValueError:
                jev = None
        else:
            jev = None
        if jev_only and jev is None:
            bad_rows += 1  # 有值但坏（SQL 已滤空值）；水位由 max_rowid 兜住
            continue
        if latest_ts is None or str(ts or "") > str(latest_ts):
            latest_ts = ts
        items.append({"id": nid, "rid": rid, "ts": ts,
                      "ingested_at": ingested, "title": title or "",
                      "content": content or "", "source": source,
                      "stocks": code_list, "jev": jev})
    errors = {}
    if bad_rows:
        errors["bad_rows"] = bad_rows
    if scanned:
        errors["max_rowid"] = rows[-1][0]  # 本页扫过末行 rid（坏行含在内）
    return _envelope(domain, {"code": code, "since_id": since_id,
                              "limit": limit}, items, scanned,
                     "news.db@news_collector (sqlite mode=ro)", errors,
                     freshness=_freshness(latest_ts)), 200


def _domain_evidence(code, days, limit):
    """证据—标的域：委托既有出口，口径零漂移，状态码透传。"""
    qs = urlencode({"code": code, "days": days, "limit": limit})
    body, status = api_intel_evidence.load_evidence_targets(qs)
    if status != 200:
        return _envelope("evidence", {"code": code, "days": days,
                                      "limit": limit}, [], 0,
                         "news.db@news_collector (sqlite mode=ro)",
                         {"upstream": body.get("error") or body},
                         ok=False), status
    return _envelope("evidence", {"code": code, "days": days, "limit": limit},
                     body.get("items", []), body.get("count", 0),
                     "news.db@news_collector (精确 ts_code 匹配)",
                     {k: body[k] for k in ("excluded_count", "bad_rows")
                      if body.get(k)}), 200


def _domain_events(code, days, limit):
    """事件域：semantica events.jsonl 只读投影（追加型，文件序=时间序）。"""
    path = Path(os.environ.get("INTEL_BUS_EVENTS_PATH") or _EVENTS_DEFAULT)
    if not path.is_file():
        return _envelope("events", {"code": code, "days": days,
                                    "limit": limit}, [], 0, str(path.name),
                         {"events": "events.jsonl 缺失: %s" % path},
                         ok=False), 503
    items, count, bad_rows = [], 0, 0
    since, _until = _window(days)
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                bad_rows += 1
                continue
            ts = str(row.get("ts") or "")[:16]  # "YYYY-MM-DD HH:MM" 前缀
            if ts and ts < since:
                continue
            stocks = row.get("stocks") if isinstance(row.get("stocks"),
                                                     list) else []
            if code and code not in stocks:
                continue
            count += 1
            if len(items) < limit:
                items.append({k: row.get(k) for k in
                              ("id", "kind", "ts", "text", "source",
                               "stocks", "bias", "conf", "reason", "tag")})
    return _envelope("events", {"code": code, "days": days, "limit": limit},
                     items, count, path.name,
                     {"bad_rows": bad_rows} if bad_rows else {}), 200


def bus_query(params):
    """总线主入口：params 已是 str→str dict（Handler 解析 qs 后传入）。
    返回 (envelope, http_status)；参数违规 400，源失败 503，上游透传。"""
    try:
        domain, code, days, limit, since_id = validate_params(params)
    except ValueError as e:
        return _envelope(str(params.get("domain") or ""), params, [], 0, "",
                         {"invalid_query": str(e)}, ok=False), 400
    if domain in ("news", "jev"):
        return _domain_news(code, days, limit, jev_only=(domain == "jev"),
                            since_id=since_id)
    if domain == "evidence":
        return _domain_evidence(code, days, limit)
    return _domain_events(code, days, limit)


class Handler:  # Mixin 壳：经 portal_api 基座多继承并入
    def _intel_bus(self, qs):
        from urllib.parse import parse_qs
        try:
            parsed = {k: v[0] for k, v in parse_qs(
                qs or "", keep_blank_values=True, strict_parsing=True).items()}
        except ValueError:
            self._json(_envelope("", {}, [], 0, "",
                                 {"invalid_query": "malformed query string"},
                                 ok=False), 400)
            return
        body, status = bus_query(parsed)
        self._json(body, status)
