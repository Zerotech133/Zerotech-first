# -*- coding: utf-8 -*-
"""郭嘉·预判对账司出口 mixin（K-ORG-191/193，2026-10-18）。

GET /api/guojia/data   零参数
    → 郭嘉对账聚合快照机读投影（news_collector/data/guojia/summary.json
      由 guojia_reconcile.py 跑批产出）+ verdicts 账本行数/mtime。
      产物缺失是合法空态（exists=false + hint），坏 JSON 带 error 不吞。

GET /api/guojia/drill  智能下钻（K-ORG-193）
    → 三模式纯文件聚合：A 排行（scope×verdict×day 过滤，|T+1 涨跌| 降序）
      B 个股（code=6位码或实体名 → 命中战绩 + 所属板块 + 历史对账行）
      C 板块（sector=板块名 → 成分股 + 标题含板块名或成分股实体的
      对账新闻，K-ORG-194 召回增强）。
      数据源：verdicts.jsonl（账本富化行，含 title/ents）+ tdx parquet
      板块/个股词典（懒加载 1h 进程缓存）。公网快照不暴露本出口消费的
      逐条明细（embed 占位，用户拍板 2026-10-18）。

只读纪律（沿 api_daily_data 判例）：mode=ro 语义（只读文件不触库）、
fail-visible、参数白名单、裸调/未知参数走 400（api_perf 判例）。
JSON 文件是唯一契约——门户不 import guojia_reconcile（依赖链隔离），
生成器换实现不影响出口；板块词典不 import api_common（并行会话在途
文件，写法独立实现不复用其符号）。
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(os.environ.get("AIOS_ROOT", "C:/aios"))
GUOJIA_DIR = REPO / "news_collector" / "data" / "guojia"
SUMMARY_JSON = GUOJIA_DIR / "summary.json"
VERDICTS_JSONL = GUOJIA_DIR / "verdicts.jsonl"

CST = timezone(timedelta(hours=8))


def _read_json(p: Path):
    if not p.exists():
        return None, None
    try:
        return json.loads(p.read_text(encoding="utf-8")), None
    except Exception as e:  # noqa: BLE001
        return None, "bad json: %r" % e


def _count_lines(p: Path):
    try:
        with open(p, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return None


def guojia_section() -> dict:
    """郭嘉对账快照投影（summary.json + 账本规模）。"""
    data, err = _read_json(SUMMARY_JSON)
    out = {"exists": data is not None, "source": SUMMARY_JSON.name}
    if err:
        out["error"] = err
        return out
    if data is None:
        out["hint"] = "由 guojia_reconcile.py 跑批产出（K-ORG-191）"
        return out
    for k in ("generated_at", "window_days", "overall", "markets",
              "pipeline"):
        if k in data:
            out[k] = data[k]
    if VERDICTS_JSONL.exists():
        out["verdict_lines"] = _count_lines(VERDICTS_JSONL)
        out["verdicts_mtime"] = datetime.fromtimestamp(
            VERDICTS_JSONL.stat().st_mtime, CST).strftime("%Y-%m-%d %H:%M:%S")
        out["age_hours"] = round(
            (time.time() - VERDICTS_JSONL.stat().st_mtime) / 3600.0, 1)
    return out


# ==== K-ORG-193 智能下钻 /api/guojia/drill ====

SCOPES_DRILL = ("cn", "hk", "us", "commodity", "crypto", "fx")
VERDICTS_SET = ("hit", "miss", "flat")
DRILL_LIMIT_MAX = 200
DRILL_USAGE = ("用法: /api/guojia/drill?scope=us&verdict=hit&limit=30 | "
               "?code=600519 | ?code=特斯拉 | ?sector=半导体；"
               "limit 1..200 缺省 30；code/sector 二选一")
TDX_DIR = REPO / "quant_backtest" / "data" / "tdx"
SECTOR_RPS_JSON = REPO / "quant_backtest" / "reports" / "sector_effect.json"
_SEC_TTL = 3600.0
_SEC_CACHE = {"ts": 0.0, "maps": None}
_ROW_KEYS = ("rowid", "day", "ts", "scope", "bias", "conf", "event_type",
             "t1_day", "t1_pct", "verdict", "title", "ents")


def _drill_rows():
    """verdicts.jsonl → 行列表（坏行跳过；旧行缺 title/ents 容错补默认）。"""
    rows = []
    try:
        with open(VERDICTS_JSONL, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    v = json.loads(ln)
                except Exception:  # noqa: BLE001  坏行容错口径同对账端
                    continue
                v.setdefault("title", None)
                if not isinstance(v.get("ents"), list):
                    v["ents"] = []
                rows.append(v)
    except OSError:
        return []
    return rows


def _tdx_parts(tbl, cols):
    """读 tdx parquet 分片（无分片返回 None，调用方按空词典降级）。"""
    import glob as _glob
    parts = sorted(_glob.glob(str(TDX_DIR / tbl / "part_*.parquet")))
    if not parts:
        return None
    import pandas
    return pandas.concat([pandas.read_parquet(p, columns=cols)
                          for p in parts], ignore_index=True)


def _sector_maps():
    """tdx parquet → {code2sec, sec2mem, name2code, code2name, rps}。

    懒加载 + 1h 进程缓存（写法独立实现，参照 _stock_sector_rows 判例但
    不 import api_common——该文件并行会话在途）。任何失败降级空映射：
    个股/板块下钻退化为按实体名/标题匹配，排行模式 A 不受影响。
    """
    now = time.time()
    if _SEC_CACHE["maps"] is not None and now - _SEC_CACHE["ts"] < _SEC_TTL:
        return _SEC_CACHE["maps"]
    code2sec, sec2mem, name2code, code2name, rps = {}, {}, {}, {}, {}
    try:
        seg = _tdx_parts("tdx_sector_def", ["code", "name", "seg"])
        name2idx = {}
        if seg is not None and len(seg):
            seg = seg[~seg["seg"].astype(str).isin({"stat", "region"})]
            name2idx = {str(n): str(c)
                        for c, n in zip(seg["code"], seg["name"])}
        mem = _tdx_parts("tdx_sector_member", ["block_name", "con_code"])
        if mem is not None and len(mem):
            for b, cc in zip(mem["block_name"], mem["con_code"]):
                b, cc = str(b), str(cc).strip().zfill(6)
                ic = name2idx.get(b)          # 只认有指数的板块（881 行业等）
                if ic:
                    code2sec.setdefault(cc, []).append(
                        {"sector": b, "idx": ic})
                    m = sec2mem.setdefault(b, {"idx": ic, "members": []})
                    if cc not in m["members"]:
                        m["members"].append(cc)
        sb = _tdx_parts("ts_stock_basic", ["ts_code", "name"])
        if sb is not None and len(sb):
            for tc, nm in zip(sb["ts_code"], sb["name"]):
                tc6 = str(tc)[:6]
                code2name[tc6] = str(nm)
                name2code.setdefault(str(nm), tc6)
        data, _ = _read_json(SECTOR_RPS_JSON)
        if isinstance(data, dict):
            rps = {s.get("sector"): s.get("rps50")
                   for s in (data.get("sectors") or []) if s.get("sector")}
    except Exception:  # noqa: BLE001  板块词典降级（fail-soft，页面安全）
        pass
    maps = {"code2sec": code2sec, "sec2mem": sec2mem, "name2code": name2code,
            "code2name": code2name, "rps": rps}
    _SEC_CACHE.update({"maps": maps, "ts": now})
    return maps


def _stat(rows):
    """方向样本战绩（hit/miss 分母，skip/no_bar 不入）。"""
    hit = sum(1 for r in rows if r.get("verdict") == "hit")
    miss = sum(1 for r in rows if r.get("verdict") == "miss")
    denom = hit + miss
    return {"n_hit": hit, "n_miss": miss, "n_dir": denom,
            "hit_rate": round(hit / denom, 4) if denom else None}


def _trim(r):
    return {k: r.get(k) for k in _ROW_KEYS}


def _sort_rank(rows):
    """排行序：|T+1 涨跌| 降序（flat/无 pct 沉底），同幅按日新在前。"""

    def key(r):
        p = r.get("t1_pct")
        a = abs(p) if isinstance(p, (int, float)) else -1.0
        return (a, r.get("day") or "", r.get("rowid") or 0)

    rows.sort(key=key, reverse=True)
    return rows


def _drill_stock(out, rows, code, scope, verdict, limit):
    """模式 B 个股：code=6位码或实体名 → 战绩+所属板块+历史对账行。"""
    maps = _sector_maps()
    if code.isdigit() and len(code) == 6:
        code6, name = code, maps["code2name"].get(code, code)
    else:
        code6, name = maps["name2code"].get(code), code
    if scope or verdict:
        rows = [r for r in rows
                if (not scope or r.get("scope") == scope)
                and (not verdict or r.get("verdict") == verdict)]
    hit_rows = [r for r in rows
                if name in (r.get("ents") or [])
                or (r.get("title") and name in r["title"])
                or (code6 and r.get("title") and code6 in r["title"])]
    total = len(hit_rows)
    _sort_rank(hit_rows)
    secs = [{"sector": s["sector"], "idx": s["idx"],
             "rps50": maps["rps"].get(s["sector"])}
            for s in (maps["code2sec"].get(code6) or [])] if code6 else []
    out.update({"mode": "stock",
                "stock": {"q": code, "name": name, "code": code6,
                          "mapped": code6 is not None},
                "sectors": secs, "stats": _stat(hit_rows),
                "filters": {"scope": scope or None,
                            "verdict": verdict or None},
                "total": total,
                "rows": [_trim(r) for r in hit_rows[:limit]]})
    return out, 200


def _drill_sector(out, rows, sector, scope, verdict, limit):
    """模式 C 板块：成分股（码序前10）+ 对账新闻（标题含板块名 或
    ents 命中任一成分股实体名——K-ORG-194 召回增强）。"""
    maps = _sector_maps()
    sec = maps["sec2mem"].get(sector)
    all_members = sorted((sec or {}).get("members") or [])
    members = [{"code": c, "name": maps["code2name"].get(c)}
               for c in all_members[:10]]
    if scope or verdict:
        rows = [r for r in rows
                if (not scope or r.get("scope") == scope)
                and (not verdict or r.get("verdict") == verdict)]
    member_names = {maps["code2name"].get(c) for c in all_members}
    member_names.discard(None)
    news_rows = [r for r in rows
                 if (r.get("title") and sector in r["title"])
                 or (set(r.get("ents") or []) & member_names)]
    total = len(news_rows)
    _sort_rank(news_rows)
    out.update({"mode": "sector",
                "sector": {"name": sector, "idx": (sec or {}).get("idx"),
                           "rps50": maps["rps"].get(sector),
                           "n_members": len((sec or {}).get("members") or [])},
                "members": members,
                "filters": {"scope": scope or None,
                            "verdict": verdict or None},
                "total": total,
                "rows": [_trim(r) for r in news_rows[:limit]]})
    return out, 200


def drill_data(params):
    """/api/guojia/drill 纯逻辑（params 已归一 dict[str,str]）→ (payload,
    http_code)。独立成函数便于单测直调（Handler 薄封装判例）。"""
    unknown = (set(params) - {"scope", "verdict", "code", "sector", "day",
                              "limit"})
    if unknown:
        return {"error": "未知参数 %s" % ",".join(sorted(unknown)),
                "usage": DRILL_USAGE}, 400
    if not params:
        return {"error": "缺参数", "usage": DRILL_USAGE}, 400
    code = (params.get("code") or "").strip()
    sector = (params.get("sector") or "").strip()
    if code and sector:
        return {"error": "code/sector 二选一", "usage": DRILL_USAGE}, 400
    scope = (params.get("scope") or "").strip().lower()
    if scope and scope not in SCOPES_DRILL:
        return {"error": "scope 须为 %s" % "|".join(SCOPES_DRILL)}, 400
    verdict = (params.get("verdict") or "").strip().lower()
    if verdict and verdict not in VERDICTS_SET:
        return {"error": "verdict 须为 hit|miss|flat"}, 400
    day = (params.get("day") or "").strip()
    if day and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return {"error": "day 须为 YYYY-MM-DD"}, 400
    limit = 30
    if "limit" in params:
        try:
            limit = int(params["limit"])
        except (TypeError, ValueError):
            return {"error": "limit 须为整数"}, 400
        if limit < 1 or limit > DRILL_LIMIT_MAX:
            return {"error": "limit 须在 1..%d" % DRILL_LIMIT_MAX}, 400
    out = {"endpoint": "/api/guojia/drill",
           "generated_at": datetime.now(CST).isoformat(timespec="seconds")}
    rows = _drill_rows()
    if code:
        return _drill_stock(out, rows, code, scope, verdict, limit)
    if sector:
        return _drill_sector(out, rows, sector, scope, verdict, limit)
    flt = [r for r in rows
           if (not scope or r.get("scope") == scope)
           and (not verdict or r.get("verdict") == verdict)
           and (not day or r.get("day") == day)]
    total = len(flt)
    _sort_rank(flt)
    out.update({"mode": "list",
                "filters": {"scope": scope or None, "verdict": verdict or None,
                            "day": day or None},
                "total": total, "rows": [_trim(r) for r in flt[:limit]]})
    return out, 200


class Handler:
    def _guojia_data(self, qs):
        # 零参数出口：参数白名单空集，带任何参数走 400（api_perf 判例）
        if self._norm_qs(qs):
            self._json({"error": "本出口不接受任何参数"}, 400)
            return
        self._json({
            "endpoint": "/api/guojia/data",
            "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
            "guojia": guojia_section(),
        })

    def _guojia_drill(self, qs):
        # 智能下钻（K-ORG-193）：薄封装——归一参数后交给 drill_data 纯函数
        qsd = self._norm_qs(qs) or {}
        payload, code_ = drill_data(
            {k: v[0] for k, v in qsd.items() if v})
        self._json(payload, code_)
