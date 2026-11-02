# -*- coding: utf-8 -*-
"""个股全景只读聚合：行情/策略命中/候选/ACAD1/卡/证据/反馈。

GET /api/v1/stock-panorama?market=CN&code=688004.SH
"""
from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query

from zhice_os_v2.api.dependencies import get_current_user
from zhice_os_v2.domain.security.user import User
from zhice_os_v2.config.settings import get_settings

router = APIRouter(prefix="/stock-panorama", tags=["stock-panorama"], dependencies=[Depends(get_current_user)])
EVIDENCE = Path(r"C:\Temp\acad1_evidence_bundle.json")
DW_CACHE = Path(r"C:\Temp\dw_multidim_cache.json")


def _variants(code: str) -> list[str]:
    raw = code.strip()
    out = {raw, raw.upper(), raw.lower()}
    base = raw.split(".")[0]
    out |= {base, base.upper(), base.lower()}
    if base.upper().startswith(("SH", "SZ")) and base[2:].isdigit():
        num = base[2:]
        suf = ".SH" if base.upper().startswith("SH") else ".SZ"
        out |= {num, num + suf, base.upper() + suf}
        base = num
    if base.isdigit() and len(base) == 6:
        suffix = ".SH" if base.startswith(("6", "68")) else ".SZ"
        out |= {base + suffix, "sh" + base, "sz" + base, "SH" + base, "SZ" + base}
    if base.isdigit() and len(base) <= 5:
        out |= {base.zfill(5), "0" + base.zfill(4), base.zfill(5) + ".HK", base.zfill(4) + ".HK"}
    return sorted(x for x in out if x)


def _clean(v):
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "__float__"):
        try:
            return float(v)
        except Exception:  # noqa: BLE001
            return str(v)
    return v


def _rows(rows):
    return [{k: _clean(v) for k, v in dict(r).items()} for r in rows]


@router.get("")
def panorama(market: str = Query(...), code: str = Query(...), _: User = Depends(get_current_user)):
    m = market.upper()
    variants = _variants(code)
    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    result = {"market": m, "code": code, "variants": variants, "read_only": True,
              "sections": {}, "gaps": []}
    with e.connect() as c:
        ident = c.execute(sa.text("""
            SELECT symbol, name FROM v2_instrument WHERE market=:m AND symbol IN :vars LIMIT 1
        """), {"m": m, "vars": tuple(variants)}).mappings().first()
        result["identity"] = {"code": code, "name": (ident or {}).get("name") or code,
                              "market": m, "source": "v2_instrument" if ident else "code"}

        # 行情
        bar = c.execute(sa.text("""
            SELECT symbol, trade_date, open, high, low, close, pre_close, volume, turnover, source
            FROM v2_bar_daily WHERE market=:m AND symbol IN :vars
            ORDER BY trade_date DESC LIMIT 1
        """), {"m": m, "vars": tuple(variants)}).mappings().first()
        result["sections"]["quote"] = {"status": "covered" if bar else "missing", "data": _clean(dict(bar)) if bar else None}
        if not bar:
            result["gaps"].append("行情缺失（v2_bar_daily）")

        # 候选资产
        cand = c.execute(sa.text("""
            SELECT a.candidate_id, a.pool_id, a.ts_code, a.candidate_score, a.candidate_reason,
                   p.pool_date, p.strategy_version_id, v.strategy_id
            FROM v2_candidate_asset a JOIN v2_candidate_pool p ON p.pool_id=a.pool_id
            LEFT JOIN v2_strategy_version v ON v.strategy_version_id=p.strategy_version_id
            WHERE a.market=:m AND a.ts_code IN :vars
            ORDER BY p.pool_date DESC LIMIT 10
        """), {"m": m, "vars": tuple(variants)}).mappings().all()
        result["sections"]["candidate"] = {"status": "covered" if cand else "missing", "items": _rows(cand)}
        if not cand:
            result["gaps"].append("候选资产缺失（v2_candidate_asset）")

        # 统一池/策略命中
        sel = c.execute(sa.text("""
            SELECT pool_date, market, code, name, source_strategies, signal_types, max_score, status
            FROM v2_selection_pool WHERE market=:m AND code IN :vars
            ORDER BY pool_date DESC LIMIT 10
        """), {"m": m, "vars": tuple(variants)}).mappings().all()
        result["sections"]["selection_pool"] = {"status": "covered" if sel else "missing", "items": _rows(sel)}
        if not sel:
            result["gaps"].append("统一池无命中（v2_selection_pool）")

        picks = c.execute(sa.text("""
            SELECT report_date, strategy_id, code, name, signal_type, score, reason
            FROM v2_selection_pick WHERE market=:m AND code IN :vars
            ORDER BY report_date DESC LIMIT 10
        """), {"m": m, "vars": tuple(variants)}).mappings().all()
        result["sections"]["picks"] = {"status": "covered" if picks else "missing", "items": _rows(picks)}
        if not picks:
            result["gaps"].append("策略选股无命中（v2_selection_pick）")

        # ACAD1
        acad = c.execute(sa.text("""
            SELECT pool_date, market, code, name, strategy_id, signal, score, data_date, status, selection_reason
            FROM v2_acad1_pool WHERE market=:m AND code IN :vars
            ORDER BY pool_date DESC LIMIT 5
        """), {"m": m, "vars": tuple(variants)}).mappings().all()
        result["sections"]["acad1"] = {"status": "covered" if acad else "missing", "items": _rows(acad)}
        if not acad:
            result["gaps"].append("未进 ACAD1 池")

        # 基础信号（先于增强）
        base = c.execute(sa.text("""
            SELECT market, code, trade_date, data_date, strategy_id, signal_type, base_state,
                   trigger_rule, trigger_price, entry_low, entry_high, stop_price, target_price, exit_rule
            FROM v2_base_signal WHERE market=:m AND code IN :vars
            ORDER BY trade_date DESC LIMIT 1
        """), {"m": m, "vars": tuple(variants)}).mappings().first()
        result["sections"]["base_signal"] = {"status": "covered" if base else "missing", "data": _clean(dict(base)) if base else None}

        # 研究卡
        card = c.execute(sa.text("""
            SELECT id, pool_date, strategy_id, code, name, direction, score, rating, thesis, risks,
                   invalidation, executable, status, reviewer, review_note, data_date
            FROM v2_stock_research WHERE code IN :vars
            ORDER BY pool_date DESC LIMIT 1
        """), {"vars": tuple(variants)}).mappings().first()
        result["sections"]["card"] = {"status": "covered" if card else "missing", "data": _clean(dict(card)) if card else None}
        if not card:
            result["gaps"].append("无研究卡")

        # 反馈
        fb = c.execute(sa.text("""
            SELECT pool_date, code, consumer, decision, reason, result, created_at
            FROM v2_selection_feedback WHERE code IN :vars ORDER BY created_at DESC LIMIT 10
        """), {"vars": tuple(variants)}).mappings().all()
        result["sections"]["feedback"] = {"status": "covered" if fb else "missing", "items": _rows(fb)}

        # 证据包
        ev = None
        if EVIDENCE.exists():
            try:
                doc = json.loads(EVIDENCE.read_text(encoding="utf-8"))
                for it in doc.get("items") or []:
                    if it.get("market") != m:
                        continue
                    codes = {it.get("code"), it.get("pool_code"), it.get("dw_code")}
                    if codes & set(variants):
                        ev = it
                        break
            except Exception:  # noqa: BLE001
                pass
        result["sections"]["evidence"] = {"status": "covered" if ev else "missing", "data": ev}
        if not ev:
            result["gaps"].append("证据包未覆盖（仅 ACAD1 30 只）")

        # 数据仓多维缓存（CN 估值/财务/行业/公告）
        dw = None
        if DW_CACHE.exists():
            try:
                cache = json.loads(DW_CACHE.read_text(encoding="utf-8"))
                for cand_code in variants:
                    if cand_code in (cache.get("stocks") or {}):
                        dw = cache["stocks"][cand_code]
                        break
            except Exception:  # noqa: BLE001
                dw = None
        if dw:
            result["sections"]["valuation"] = {"status": "covered" if dw.get("valuation") else "missing", "data": dw.get("valuation")}
            result["sections"]["income"] = {"status": "covered" if dw.get("income") else "missing", "data": dw.get("income")}
            result["sections"]["fina"] = {"status": "covered" if dw.get("fina") else "missing", "data": dw.get("fina")}
            result["sections"]["balance"] = {"status": "covered" if dw.get("balance") else "missing", "data": dw.get("balance")}
            result["sections"]["industry"] = {"status": "covered" if dw.get("industry") else "missing", "data": dw.get("industry")}
            result["sections"]["announcements"] = {"status": "covered" if dw.get("announcements") else "missing", "data": dw.get("announcements") or []}
            for key in ("flow", "margin", "limit", "dragon", "cyq", "research", "index_membership"):
                val = dw.get(key)
                result["sections"][key] = {"status": "covered" if val else "missing", "data": val}
        else:
            for key in ("valuation", "income", "fina", "balance", "industry", "announcements"):
                result["sections"][key] = {"status": "missing", "data": None}
            result["gaps"].append("数据仓多维缓存未覆盖（dw_multidim_cache）")

        # 中文策略名
        names = {}
        for r in c.execute(sa.text("SELECT strategy_id, strategy_name FROM v2_strategy UNION ALL SELECT strategy_id, display_name FROM v2_strategy_sidecar")).all():
            names[str(r[0])] = str(r[1])
        result["strategy_names"] = names

    covered = [k for k, v in result["sections"].items() if v.get("status") == "covered"]
    if not covered:
        result["scope"] = "unknown"
        result["note"] = "系统未找到该股票：行情、候选、统一池、ACAD1、研究卡均无记录。请核对市场/代码格式。"
    elif not any(k in covered for k in ("candidate", "selection_pool", "picks", "acad1", "card", "evidence")):
        result["scope"] = "market_only"
        result["note"] = "该股票仅有行情，尚未进入当前策略选股/候选池/ACAD1 范围；不是数据缺失。"
    else:
        result["scope"] = "in_scope"
    result["summary"] = {
        "sections_covered": covered,
        "sections_missing": [k for k, v in result["sections"].items() if v.get("status") != "covered"],
        "gaps": result["gaps"],
    }
    return result


@router.get("/selected")
def selected(market: str = Query(...), scope: str = Query("acad1"), limit: int = Query(30, le=100), _: User = Depends(get_current_user)):
    """选出来的股票 + 数据仓多维字段，供选股多维表使用。只读。"""
    m = market.upper()
    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    with e.connect() as conn:
        if scope == "picks":
            rows = conn.execute(sa.text("""
                SELECT pool_date, market, code, name, source_strategies AS strategy_id, signal_types AS signal,
                       max_score AS score, 'picks' AS scope, pool_date AS data_date, NULL::timestamp AS selected_at
                FROM v2_selection_pool WHERE market=:m
                AND pool_date=(SELECT max(pool_date) FROM v2_selection_pool WHERE market=:m)
                ORDER BY max_score DESC LIMIT :lim
            """), {"m": m, "lim": limit}).mappings().all()
        else:
            rows = conn.execute(sa.text("""
                SELECT pool_date, market, code, name, strategy_id, signal, score, 'acad1' AS scope,
                       data_date, selected_at
                FROM v2_acad1_pool WHERE market=:m
                AND pool_date=(SELECT max(pool_date) FROM v2_acad1_pool WHERE market=:m)
                ORDER BY score DESC LIMIT :lim
            """), {"m": m, "lim": limit}).mappings().all()

    names = {}
    try:
        with e.connect() as nc:
            for r in nc.execute(sa.text("SELECT strategy_id, strategy_name FROM v2_strategy UNION ALL SELECT strategy_id, display_name FROM v2_strategy_sidecar")).all():
                names[str(r[0])] = str(r[1])
    except Exception:  # noqa: BLE001
        names = {}

    cache = {}
    if DW_CACHE.exists():
        try:
            cache = json.loads(DW_CACHE.read_text(encoding="utf-8")).get("stocks") or {}
        except Exception:  # noqa: BLE001
            cache = {}
    evidence = {}
    if EVIDENCE.exists():
        try:
            for it in (json.loads(EVIDENCE.read_text(encoding="utf-8")).get("items") or []):
                evidence[(it.get("market"), it.get("code"))] = it
                if it.get("pool_code"):
                    evidence[(it.get("market"), it.get("pool_code"))] = it
        except Exception:  # noqa: BLE001
            pass

    stocks = []
    for r in rows:
        code = str(r["code"])
        dw = None
        for v in _variants(code):
            if v in cache:
                dw = cache[v]
                break
        val, fin, inc, ind = (dw or {}).get("valuation") or {}, (dw or {}).get("fina") or {}, (dw or {}).get("income") or {}, (dw or {}).get("industry") or {}
        ev = evidence.get((m, code))
        missing = []
        if not val:
            missing.append("估值")
        if not fin:
            missing.append("财务")
        if not ind:
            missing.append("行业")
        if not (dw or {}).get("announcements"):
            missing.append("公告")
        stocks.append({
            "market": m, "pool_date": _clean(r["pool_date"]), "code": code, "name": r["name"],
            "strategy_id": r["strategy_id"], "strategy_name": names.get(str(r["strategy_id"]), r["strategy_id"]),
            "signal": r["signal"], "score": _clean(r["score"]), "scope": r["scope"],
            "data_date": _clean(r.get("data_date")), "selected_at": _clean(r.get("selected_at")),
            "pe_ttm": val.get("pe_ttm"), "pb": val.get("pb"), "total_mv": val.get("total_mv"),
            "roe": fin.get("roe"), "debt_to_assets": fin.get("debt_to_assets"),
            "revenue": inc.get("total_revenue"), "net_income": inc.get("n_income_attr_p") or inc.get("n_income"),
            "industry_code": ind.get("index_code"),
            "net_mf_amount": ((dw or {}).get("flow") or {}).get("net_mf_amount"),
            "margin_balance": ((dw or {}).get("margin") or {}).get("rzrqye"),
            "winner_rate": ((dw or {}).get("cyq") or {}).get("winner_rate"),
            "research_count": len((dw or {}).get("research") or []),
            "announcement_count": len((dw or {}).get("announcements") or []),
            "evidence_level": (ev or {}).get("evidence_level"),
            "dw_covered": bool(dw), "missing_dimensions": missing, "panorama_path": f"/{m.lower()}/stock/{code}",
        })
    return {"market": m, "scope": scope, "pool_date": stocks[0]["pool_date"] if stocks else None,
            "stocks": stocks, "read_only": True,
            "coverage": {"total": len(stocks), "dw_covered": sum(1 for x in stocks if x["dw_covered"]),
                          "dw_missing": sum(1 for x in stocks if not x["dw_covered"])}}
