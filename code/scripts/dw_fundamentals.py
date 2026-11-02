# -*- coding: utf-8 -*-
"""ACAD1 基本面证据只读适配器：申万 L1 行业 PIT / 公告 / 新闻。

数据来源（全部只读；本脚本唯一写操作 = --output 指定的 JSON）：
- 张松数据仓 quant_backtest/data/tushare：
  * sw_l1_member      申万 L1 成分 PIT 历史（index_code, con_code, in_date, out_date, is_new）
  * sw_classify_l1    申万 L1 行业代码 -> 名称（SW2021）
  * anns_d            日频公告（A 股，按 ann_date 分文件）
  * news              新闻滚动快照（无个股字段，用股票名/英文名/括号代码匹配归属）
  * stock_basic / hk_basic / us_basic  代码 -> 名称（仅做标识，不做结论）
  * index_member_all  当前申万成分快照（仅用于 PIT 交叉校验）
- 可选只读 SQLite（存在才用，--anns-history-db/--news-db 可覆盖或 none 关闭）：
  * ann_history.db    A 股公告全历史（张松数据仓旁 zerobound-kg/data/ann_history/）
  * news_collector/news.db  新闻库（老田的AI助手/news_collector/）
- 智策 PG v2_acad1_pool：只读会话 SELECT 最新研究池（默认），或用 --pool-json 指定静态池。

PIT 口径（如实标注，不推断）：
- as-of = ACAD1 池日（默认 v2_acad1_pool 最新 pool_date）；
- 行业 = sw_l1_member 中 in_date <= asof 且 (out_date 为空 或 out_date >= asof) 的最后一条，
  行业名取自 sw_classify_l1.level='L1'；sw_l1_member 最新成分事件 in_date=2026-08-27，
  相对 2026-10-31 池日滞后 65 天，2026-08-27 之后的申万成分调整不可见；
- 公告 = anns_d 中 ann_date ∈ [asof-lookback, asof]，只读，不晚于 asof；
- 新闻 = news / news.db 中时间 <= asof 23:59:59，只读；
- 非 A 股（HK/US/CRYPTO）无申万行业、无 A 股公告源：字段为 null 且带 reason（not_applicable），
  不编造替代分类；新闻归属为关键词匹配，可能漏配/误配，明示 match_method 与 caveat。

明确不做：不写生产表/研究卡/DB，不给评级/方向/买卖建议，不自动结论。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

CST = timezone(timedelta(hours=8))
DEFAULT_DATA_ROOT = Path(r"C:\项目\老田的AI助手\quant_backtest\data")
DEFAULT_OUTPUT = Path(r"C:\Temp\acad1_fundamentals.json")

MARKETS = ("CN", "HK", "US", "CRYPTO")
MARKET_ORDER = {m: i for i, m in enumerate(MARKETS)}
NEVER = 99999999  # out_date 为空 -> 视为无限期有效

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_HK_SUFFIX_RE = re.compile(r"[-－](W|SW|S|B|R)$", re.IGNORECASE)
_LEGAL_WORDS = {
    "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED",
    "PLC", "HOLDINGS", "HOLDING", "GROUP", "THE", "CLASS", "A", "B", "SA", "AG", "NV",
    "SE", "TRUST",
}
_CRYPTO_STATIC_ALIASES = {"ETH/USDT": ["以太坊"], "PROM/USDT": []}


# --------------------------------------------------------------------------- #
# 通用小工具
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(CST).replace(microsecond=0).isoformat()


def _to_int_date(value) -> int | None:
    """把 YYYYMMDD / 'YYYYMMDD' / date / None 统一成 int(YYYYMMDD)。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return None
    return int(text.replace("-", "").split(" ")[0].split("T")[0])


def _dedupe(items) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _latest_parquet(folder: Path) -> Path | None:
    files = sorted(folder.glob("*.parquet"))
    return files[-1] if files else None


def _dw_code(code: str, market: str) -> str:
    """智策池代码归一为数据仓代码：sh688004 -> 688004.SH；00700 -> 00700.HK。"""
    raw = str(code or "").strip()
    if market == "CN":
        match = re.fullmatch(r"(?i)(sh|sz|bj)(\d{6})", raw)
        if match:
            return f"{match.group(2)}.{match.group(1).upper()}"
        return raw.upper()
    if market == "HK":
        match = re.fullmatch(r"(?i)(\d{4,5})(?:\.HK)?", raw)
        if match:
            return f"{int(match.group(1)):05d}.HK"
        return raw.upper()
    return raw


def _bracket_code(code: str) -> str:
    """媒体常写「腾讯控股(00700)」：仅匹配括号内代码，避免裸数字误配。"""
    return rf"[（(]{re.escape(code)}[）)]"


def _cjk_variants(name: str) -> list[str]:
    """中文简称变体：带市场后缀（-W/-B…）或通用后缀（国际/股份/公司…）时补短名。"""
    base = (name or "").strip()
    if not base:
        return []
    out = [base]
    stripped = _HK_SUFFIX_RE.sub("", base)
    if stripped != base and len(stripped) >= 2:
        out.append(stripped)
    for suffix in ("有限公司", "国际", "股份", "集团", "控股", "科技", "公司"):
        if base.endswith(suffix) and len(base) - len(suffix) >= 3:
            out.append(base[: -len(suffix)])
    return _dedupe(out)


def _clean_en_name(enname: str | None) -> str | None:
    """英文全称去法律后缀，得到可用于新闻匹配的核心名（如 CROWN CASTLE / MAPLEBEAR）。"""
    if not enname:
        return None
    tokens = re.sub(r"['’,.&]", " ", str(enname).upper()).split()
    tokens = [token for token in tokens if token not in _LEGAL_WORDS]
    core = " ".join(tokens).strip()
    if len(core) >= 4:
        return core
    if len(core) >= 3:  # 允许 PTC 这类缩写
        return core
    return None


def _alias_pattern(aliases: list[str], regex_aliases: list[str]) -> str | None:
    """组织成正则：中文/英文别名按字面匹配，ASCII 别名加词边界；括号代码按正则。"""
    parts = []
    for alias in aliases:
        escaped = re.escape(alias)
        if re.search(r"[A-Za-z]", alias):
            escaped = rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])"
        parts.append(escaped)
    parts.extend(regex_aliases)
    return "|".join(parts) if parts else None


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


# --------------------------------------------------------------------------- #
# 智策 PG（只读）与静态池
# --------------------------------------------------------------------------- #
def _db_url(explicit: str | None) -> str:
    url = explicit or os.environ.get("DB_MAIN_URL")
    if not url:
        env_file = Path(__file__).resolve().parent.parent / ".env.local"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("DB_MAIN_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not url:
        raise SystemExit("未找到 DB_MAIN_URL（环境变量或 code/.env.local）；或改用 --pool-json 指定静态池")
    return url.replace("postgresql+psycopg2://", "postgresql://", 1)


def load_pool_db(db_url: str, pool_date: str | None) -> dict:
    """只读会话 SELECT v2_acad1_pool 最新（或指定）池日。"""
    import psycopg2

    conn = psycopg2.connect(db_url, connect_timeout=10)
    try:
        conn.set_session(readonly=True, autocommit=True)  # 只读事务，物理禁止写入
        conn.set_client_encoding("UTF8")
        with conn.cursor() as cur:
            if pool_date:
                day = date.fromisoformat(pool_date)
            else:
                cur.execute("SELECT max(pool_date) FROM v2_acad1_pool")
                day = cur.fetchone()[0]
            cur.execute(
                """
                SELECT pool_date, market, code, name, strategy_id, signal, score,
                       data_date, status
                FROM v2_acad1_pool
                WHERE pool_date = %s
                ORDER BY market, code
                """,
                (day,),
            )
            columns = [desc[0] for desc in cur.description]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()
    pool = []
    for row in rows:
        pool.append({
            "market": row["market"],
            "code": row["code"],
            "pool_name": row["name"],
            "strategy": row["strategy_id"],
            "signal": row["signal"],
            "score": float(row["score"]) if row["score"] is not None else None,
            "data_date": str(row["data_date"]) if row["data_date"] else None,
        })
    return {
        "source": "postgres:zhice_os_v2.v2_acad1_pool",
        "pool_date": str(day),
        "entries": pool,
    }


def load_pool_json(path: Path) -> dict:
    """静态池 JSON：兼容 cards[] / items[] / 裸 list；字段 code|ts_code|candidate_id + market。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        raw_rows, pool_date = data, None
    elif isinstance(data, dict):
        pool_date = data.get("pool_date") or data.get("date")
        raw_rows = data.get("cards") or data.get("items") or data.get("pool") or []
    else:
        raise SystemExit(f"无法解析池 JSON：{path}")
    entries = []
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        code = row.get("code") or row.get("ts_code") or row.get("candidate_id")
        market = row.get("market")
        if not code or not market:
            continue
        entries.append({
            "market": market,
            "code": code,
            "pool_name": row.get("name"),
            "strategy": row.get("strategy") or row.get("strategy_id"),
            "signal": row.get("signal_type") or row.get("signal"),
            "score": row.get("score"),
            "data_date": row.get("asof") or row.get("data_date"),
        })
    return {
        "source": f"json:{path}",
        "pool_date": str(pool_date) if pool_date else None,
        "entries": entries,
    }


# --------------------------------------------------------------------------- #
# 数据仓加载
# --------------------------------------------------------------------------- #
def load_sw(data_root: Path) -> dict:
    tushare = data_root / "tushare"
    member_files = sorted((tushare / "sw_l1_member").glob("*.parquet"))
    frames = [pd.read_parquet(path) for path in member_files]
    member = pd.concat(frames, ignore_index=True)
    member["ts_code"] = member["con_code"].astype(str)
    member["l1_code"] = member["index_code"].astype(str)
    member["in_dt"] = pd.to_numeric(member["in_date"], errors="coerce").fillna(NEVER).astype("int64")
    member["out_dt"] = pd.to_numeric(member["out_date"], errors="coerce").fillna(NEVER).astype("int64")
    member = member[["l1_code", "ts_code", "in_dt", "out_dt", "is_new"]]

    classify_frames = [pd.read_parquet(path) for path in sorted((tushare / "sw_classify_l1").glob("*.parquet"))]
    classify = pd.concat(classify_frames, ignore_index=True).drop_duplicates()
    l1 = classify[classify["level"] == "L1"].drop_duplicates("index_code")
    l1_names = {str(row["index_code"]): str(row["industry_name"]) for _, row in l1.iterrows()}

    in_dates = member["in_dt"][member["in_dt"] != NEVER]
    out_dates = member["out_dt"][member["out_dt"] != NEVER]
    return {
        "member": member,
        "l1_names": l1_names,
        "meta": {
            "path": str(tushare / "sw_l1_member"),
            "files": len(member_files),
            "rows": int(len(member)),
            "l1_industries": int(member["l1_code"].nunique()),
            "in_date_min": str(int(in_dates.min())) if len(in_dates) else None,
            "in_date_max": str(int(in_dates.max())) if len(in_dates) else None,
            "out_date_max": str(int(out_dates.max())) if len(out_dates) else None,
            "current_members": int((member["is_new"] == "Y").sum()),
            "classify_source": str(tushare / "sw_classify_l1"),
            "classify_l1_count": len(l1_names),
        },
    }


def load_industry_crosscheck(data_root: Path) -> dict:
    path = _latest_parquet(data_root / "tushare" / "index_member_all")
    if path is None:
        return {"meta": {"path": str(data_root / "tushare" / "index_member_all"), "exists": False}, "map": {}}
    frame = pd.read_parquet(path)
    frame["ts_code"] = frame["ts_code"].astype(str)
    frame["l1_code"] = frame["l1_code"].astype(str)
    frame = frame.drop_duplicates("ts_code", keep="last")
    in_dates = pd.to_numeric(frame["in_date"], errors="coerce").dropna()
    return {
        "map": dict(zip(frame["ts_code"], frame["l1_code"])),
        "meta": {
            "path": str(path),
            "exists": True,
            "rows": int(len(frame)),
            "in_date_max": str(int(in_dates.max())) if len(in_dates) else None,
            "role": "当前申万成分快照（非 PIT 历史），仅用于交叉校验，不替代 sw_l1_member",
        },
    }


def load_basics(data_root: Path) -> dict:
    tushare = data_root / "tushare"
    basics = {"cn": {}, "hk": {}, "us": {}, "meta": {}}

    path = _latest_parquet(tushare / "stock_basic")
    if path is not None:
        frame = pd.read_parquet(path).drop_duplicates("ts_code")
        basics["cn"] = {
            str(row["ts_code"]): {"name": row["name"], "industry": row["industry"], "list_date": str(row["list_date"])}
            for _, row in frame.iterrows()
        }
        basics["meta"]["stock_basic"] = {"path": str(path), "rows": int(len(frame))}

    path = _latest_parquet(tushare / "hk_basic")
    if path is not None:
        frame = pd.read_parquet(path).drop_duplicates("ts_code")
        basics["hk"] = {
            str(row["ts_code"]): {"name": row["name"], "fullname": row["fullname"]}
            for _, row in frame.iterrows()
        }
        basics["meta"]["hk_basic"] = {"path": str(path), "rows": int(len(frame))}

    path = _latest_parquet(tushare / "us_basic")
    if path is not None:
        frame = pd.read_parquet(path).drop_duplicates("ts_code")
        basics["us"] = {
            str(row["ts_code"]): {"name": row["name"], "enname": row["enname"]}
            for _, row in frame.iterrows()
        }
        basics["meta"]["us_basic"] = {"path": str(path), "rows": int(len(frame))}
    return basics


def load_anns_d(data_root: Path, start_int: int, end_int: int) -> dict:
    folder = data_root / "tushare" / "anns_d"
    files = sorted(folder.glob("d_*.parquet"))
    if not files:
        return {"frame": None, "meta": {"path": str(folder), "exists": False}}
    dated = [(int(path.stem[2:]), path) for path in files]
    latest_file = max(dated, key=lambda item: item[0])
    selected = [(day, path) for day, path in dated if start_int <= day <= end_int]
    frames = []
    for _, path in selected:
        frame = pd.read_parquet(path, columns=["ann_date", "ts_code", "title"])
        frames.append(frame)
    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["ann_date", "ts_code", "title"])
    frame["ann_int"] = pd.to_numeric(frame["ann_date"], errors="coerce")
    frame = frame.dropna(subset=["ann_int"])
    frame["ann_int"] = frame["ann_int"].astype("int64")
    frame["ts_code"] = frame["ts_code"].astype(str)
    return {
        "frame": frame,
        "meta": {
            "path": str(folder),
            "exists": True,
            "daily_files": len(files),
            "file_date_min": str(dated[0][0]),
            "file_date_max": str(latest_file[0]),
            "window_files": len(selected),
            "window_rows": int(len(frame)),
        },
    }


def load_ann_history(db_path: Path | None, cn_symbols: list[str]) -> dict:
    if db_path is None or not db_path.exists():
        return {"map": {}, "meta": {"path": str(db_path) if db_path else None, "exists": False}}
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        con.execute("PRAGMA query_only = ON")
        cur = con.cursor()
        rows_meta = cur.execute("SELECT count(*), min(notice_date), max(notice_date) FROM ann").fetchone()
        watermark = None
        try:
            watermark = cur.execute("SELECT max(finished_at) FROM stock_watermark").fetchone()[0]
        except sqlite3.Error:
            watermark = None
        result: dict[str, dict] = {}
        if cn_symbols:
            placeholders = ",".join("?" for _ in cn_symbols)
            rows = cur.execute(
                f"""
                SELECT s.stock_code, s.art_code, a.notice_date, a.title
                FROM ann_stock s JOIN ann a ON a.art_code = s.art_code
                WHERE s.stock_code IN ({placeholders})
                ORDER BY a.notice_date
                """,
                cn_symbols,
            ).fetchall()
            seen: set[tuple[str, str]] = set()
            for symbol, art_code, notice_date, title in rows:
                key = (symbol, art_code)
                if key in seen:  # ann_stock 偶有同一公告多条关联，按 art_code 去重
                    continue
                seen.add(key)
                item = result.setdefault(symbol, {"count": 0, "latest_date": None, "latest_title": None})
                item["count"] += 1
                if item["latest_date"] is None or str(notice_date) > str(item["latest_date"]):
                    item["latest_date"] = str(notice_date)
                    item["latest_title"] = title
    finally:
        con.close()
    return {
        "map": result,
        "meta": {
            "path": str(db_path),
            "exists": True,
            "rows": int(rows_meta[0]),
            "notice_date_min": str(rows_meta[1]),
            "notice_date_max": str(rows_meta[2]),
            "stock_watermark_finished_at": watermark,
            "role": "A 股公告全历史交叉校验（覆盖 2026-02-01 起）",
        },
    }


def load_news_tushare(data_root: Path) -> dict:
    folder = data_root / "tushare" / "news"
    parts = sorted(folder.glob("*.parquet"))
    if not parts:
        return {"frame": None, "meta": {"path": str(folder), "exists": False}}
    raw = pd.concat([pd.read_parquet(path) for path in parts], ignore_index=True)
    raw_rows = len(raw)
    raw = raw.drop_duplicates(subset=["datetime", "content"]).reset_index(drop=True)
    frame = pd.DataFrame({
        "dt": pd.to_datetime(raw["datetime"], errors="coerce", format="mixed"),
        "title": raw["title"].fillna("").astype(str),
        "text": (raw["title"].fillna("").astype(str) + "\n" + raw["content"].fillna("").astype(str)),
    })
    frame = frame.dropna(subset=["dt"]).reset_index(drop=True)
    return {
        "frame": frame,
        "meta": {
            "path": str(folder),
            "exists": True,
            "part_files": len(parts),
            "raw_rows": raw_rows,
            "unique_items": int(len(frame)),
            "datetime_min": frame["dt"].min().strftime("%Y-%m-%d %H:%M:%S"),
            "datetime_max": frame["dt"].max().strftime("%Y-%m-%d %H:%M:%S"),
            "attribution": "无个股字段；标题+正文关键词匹配",
        },
    }


def load_news_db(db_path: Path | None) -> dict:
    if db_path is None or not db_path.exists():
        return {"frame": None, "meta": {"path": str(db_path) if db_path else None, "exists": False}}
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        con.execute("PRAGMA query_only = ON")
        frame = pd.read_sql_query("SELECT ts, title, content FROM news", con)
    finally:
        con.close()
    frame["dt"] = pd.to_datetime(frame["ts"], errors="coerce", format="mixed")
    parsed = pd.DataFrame({
        "dt": frame["dt"],
        "title": frame["title"].fillna("").astype(str),
        "text": (frame["title"].fillna("").astype(str) + "\n" + frame["content"].fillna("").astype(str)),
    })
    parsed = parsed.dropna(subset=["dt"]).reset_index(drop=True)
    meta = {
        "path": str(db_path),
        "exists": True,
        "rows": int(len(frame)),
        "parsed_rows": int(len(parsed)),
        "ts_min": parsed["dt"].min().strftime("%Y-%m-%d %H:%M:%S"),
        "ts_max": parsed["dt"].max().strftime("%Y-%m-%d %H:%M:%S"),
        "attribution": "无个股字段；标题+正文关键词匹配",
    }
    return {"frame": parsed, "meta": meta}


# --------------------------------------------------------------------------- #
# 分析
# --------------------------------------------------------------------------- #
def analyze_industry(item: dict, sw: dict, crosscheck: dict, basics: dict) -> dict:
    market, dw_code = item["market"], item["dw_code"]
    if market != "CN":
        return {
            "status": "not_applicable",
            "reason": "申万 L1 仅覆盖 A 股；HK/US/CRYPTO 无行业分类源，不编造替代分类",
            "value": None,
        }
    asof_int = item["asof_int"]
    rows = sw["member"][sw["member"]["ts_code"] == dw_code]
    if rows.empty:
        return {
            "status": "missing",
            "reason": "sw_l1_member 无该 A 股代码的成分记录（未纳入历史或代码口径不符）",
            "value": None,
        }
    valid = rows[(rows["in_dt"] <= asof_int) & (rows["out_dt"] >= asof_int)].sort_values("in_dt")
    if valid.empty:
        latest = rows.sort_values("in_dt").iloc[-1]
        return {
            "status": "missing",
            "reason": "该股在 as-of 时点无有效申万 L1 成分（历史记录均不在有效期）",
            "value": None,
            "last_history": {
                "sw_l1_code": latest["l1_code"],
                "in_date": str(int(latest["in_dt"])),
                "out_date": None if int(latest["out_dt"]) == NEVER else str(int(latest["out_dt"])),
            },
        }
    row = valid.iloc[-1]
    l1_code = str(row["l1_code"])
    history = [
        {
            "sw_l1_code": str(hist_row["l1_code"]),
            "in_date": str(int(hist_row["in_dt"])),
            "out_date": None if int(hist_row["out_dt"]) == NEVER else str(int(hist_row["out_dt"])),
            "is_new": str(hist_row["is_new"]),
        }
        for _, hist_row in rows.sort_values("in_dt").iterrows()
    ]
    cross_l1 = crosscheck["map"].get(dw_code)
    static = basics["cn"].get(dw_code)
    cross_max = crosscheck["meta"].get("in_date_max")
    return {
        "status": "covered",
        "source": "tushare/sw_l1_member + sw_classify_l1",
        "pit_asof": item["asof"],
        "pit_method": "in_date <= asof 且 (out_date 为空 或 out_date >= asof)；取最后一条有效成分",
        "value": {
            "sw_l1_code": l1_code,
            "sw_l1_name": sw["l1_names"].get(l1_code),
            "member_in_date": str(int(row["in_dt"])),
            "member_out_date": None if int(row["out_dt"]) == NEVER else str(int(row["out_dt"])),
            "is_new": str(row["is_new"]),
        },
        "pit_lag_days": item["pit_lag_days"],
        "history": history,
        "crosscheck_index_member_all": {
            "source": f"tushare/index_member_all（当前成分快照，in_date max={cross_max}）",
            "l1_code": cross_l1,
            "match": None if cross_l1 is None else (cross_l1 == l1_code),
        },
        "static_industry": {
            "value": static["industry"] if static else None,
            "pit": False,
            "source": "tushare/stock_basic.industry（非申万、非 PIT 快照，仅参考）",
        },
        "reason": None,
    }


def analyze_announcements(item: dict, anns: dict, ann_history: dict) -> dict:
    market, dw_code = item["market"], item["dw_code"]
    if market != "CN":
        return {
            "status": "not_applicable",
            "reason": "anns_d / ann_history.db 仅覆盖 A 股；HK/US/CRYPTO 公告源在本数据仓缺失，不编造",
            "value": None,
        }
    frame = anns.get("frame")
    if frame is None:
        return {"status": "source_missing", "reason": "数据仓缺少 tushare/anns_d 公告源", "value": None}
    asof_int = item["asof_int"]
    window_start = item["ann_window_start_int"]
    rows = frame[
        (frame["ts_code"] == dw_code)
        & (frame["ann_int"] >= window_start)
        & (frame["ann_int"] <= asof_int)
    ].sort_values("ann_int")
    history = ann_history["map"].get(dw_code.split(".")[0])
    latest = rows.iloc[-1] if not rows.empty else None
    return {
        "status": "covered" if not rows.empty else "no_data",
        "source": "tushare/anns_d（ann_date 分文件，只读）",
        "window": {"start": str(window_start), "end": str(asof_int), "lookback_days": item["ann_lookback_days"]},
        "value": {
            "count": int(len(rows)),
            "latest_date": str(int(latest["ann_int"])) if latest is not None else None,
            "latest_title": str(latest["title"]) if latest is not None and pd.notna(latest["title"]) else None,
        },
        "pit_note": "ann_date 为公告日，口径上不晚于 as-of，可用作 PIT 事件",
        "history_check": {
            "source": "ann_history.db（A 股公告全历史，只读）",
            "count": history["count"] if history else None,
            "latest_date": history["latest_date"] if history else None,
            "latest_title": history["latest_title"] if history else None,
            "available": bool(history),
        },
        "reason": None if not rows.empty else "窗口内 0 条（真实无公告或未落仓；不是缺失值）",
    }


def analyze_news(item: dict, news_source: dict, win_start, cutoff) -> dict:
    aliases = item["aliases"]
    regex_aliases = item["regex_aliases"]
    pattern = _alias_pattern(aliases, regex_aliases)
    sources = {}
    latest = None
    for name, source in news_source.items():
        frame = source.get("frame")
        if frame is None:
            sources[name] = {"available": False, "count": None}
            continue
        window = frame[(frame["dt"] >= win_start) & (frame["dt"] <= cutoff)]
        if pattern is None:
            count, hit = 0, None
        else:
            mask = window["text"].str.contains(pattern, case=False, regex=True, na=False)
            hits = window.loc[mask]
            count = int(len(hits))
            hit = None
            if count:
                row = hits.loc[hits["dt"].idxmax()]
                snippet = row["title"].strip() or row["text"].strip().replace("\n", " ")
                hit = {
                    "time": row["dt"].strftime("%Y-%m-%d %H:%M:%S"),
                    "title": snippet[:120],
                    "source": name,
                }
        sources[name] = {"available": True, "count": count, "latest": hit}
        if hit is not None and (latest is None or hit["time"] > latest["time"]):
            latest = hit

    primary = "news_db" if sources.get("news_db", {}).get("available") else "tushare_news"
    primary_count = sources.get(primary, {}).get("count") or 0
    status = "no_alias" if not aliases and not regex_aliases else ("covered" if primary_count else "no_match")
    if status == "no_alias":
        reason = "无可用名称别名（基础表缺该代码），仅无法归属新闻"
    elif status == "no_match":
        reason = "窗口内关键词未命中；可能确无相关新闻，或别名不全（不推断）"
    else:
        reason = None
    return {
        "status": status,
        "match_method": "标题+正文关键词匹配（股票名/英文核心名/括号代码）；非数据仓原生个股映射",
        "aliases": aliases,
        "regex_aliases": regex_aliases,
        "window": {
            "start": win_start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": cutoff.strftime("%Y-%m-%d %H:%M:%S"),
            "lookback_days": item["news_lookback_days"],
        },
        "per_source": sources,
        "value": {
            "count": primary_count,
            "count_source": primary,
            "count_definition": "count 取 news_db（可用时）窗口内条数；tushare_news 单独列在 per_source；两源有重叠，不相加",
            "latest": latest,
            "latest_note": "latest 取两源中时间最新的一条，可能来自非 count_source",
        },
        "caveat": "关键词匹配可能有漏配/误配，需人工复核；两源有重叠，count 不做相加",
        "reason": reason,
    }


# --------------------------------------------------------------------------- #
# 覆盖与缺失统计
# --------------------------------------------------------------------------- #
def _coverage(items: list[dict]) -> dict:
    total = len(items)
    cn = [item for item in items if item["market"] == "CN"]
    non_cn = [item for item in items if item["market"] != "CN"]
    industry_covered = sum(1 for item in cn if item["industry"]["status"] == "covered")
    ann_covered = sum(1 for item in cn if item["announcements"]["status"] == "covered")
    ann_zero = sum(1 for item in cn if item["announcements"]["status"] == "no_data")
    news_covered = sum(1 for item in items if item["news"]["status"] == "covered")
    news_missing = total - news_covered
    name_present = sum(1 for item in items if item["name"])
    ann_latest_present = sum(1 for item in cn if (item["announcements"].get("value") or {}).get("latest_date"))
    news_latest_present = sum(
        1 for item in items if (item["news"].get("value") or {}).get("latest") is not None)

    def rate(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    return {
        "universe": total,
        "by_market": {market: sum(1 for item in items if item["market"] == market) for market in MARKETS},
        "industry": {
            "applicable_markets": ["CN"],
            "applicable": len(cn),
            "covered": industry_covered,
            "missing": len(cn) - industry_covered,
            "applicable_rate": rate(industry_covered, len(cn)),
            "non_applicable": len(non_cn),
            "non_applicable_reason": "申万 L1 仅覆盖 A 股，其他市场无行业分类源",
            "overall_covered": industry_covered,
            "overall_rate": rate(industry_covered, total),
            "missing_codes": [item["code"] for item in cn if item["industry"]["status"] != "covered"],
        },
        "announcements": {
            "applicable_markets": ["CN"],
            "applicable": len(cn),
            "covered": ann_covered,
            "zero_in_window": ann_zero,
            "missing": len(cn) - ann_covered - ann_zero,
            "applicable_rate": rate(ann_covered, len(cn)),
            "non_applicable": len(non_cn),
            "non_applicable_reason": "anns_d / ann_history.db 仅覆盖 A 股",
            "overall_covered": ann_covered,
            "overall_rate": rate(ann_covered, total),
            "missing_codes": [item["code"] for item in cn if item["announcements"]["status"] == "missing"],
        },
        "news": {
            "eligible": total,
            "covered": news_covered,
            "missing": news_missing,
            "rate": rate(news_covered, total),
            "missing_codes": [item["code"] for item in items if item["news"]["status"] != "covered"],
        },
        "fields": {
            "name": {
                "present": name_present, "missing": total - name_present,
                "rate": rate(name_present, total),
                "missing_codes": [item["code"] for item in items if not item["name"]],
            },
            "industry.sw_l1_name": {
                "applicable": len(cn), "present": industry_covered,
                "missing": len(cn) - industry_covered,
                "applicable_rate": rate(industry_covered, len(cn)),
                "not_applicable": len(non_cn),
                "overall_rate": rate(industry_covered, total),
            },
            "announcements.latest_date": {
                "applicable": len(cn), "present": ann_latest_present,
                "applicable_rate": rate(ann_latest_present, len(cn)),
                "not_applicable": len(non_cn),
                "overall_rate": rate(ann_latest_present, total),
            },
            "announcements.count": {
                "applicable": len(cn),
                "present": sum(1 for item in cn if (item["announcements"].get("value") or {}).get("count") is not None),
                "not_applicable": len(non_cn),
            },
            "news.count": {
                "applicable": total,
                "present": sum(1 for item in items if (item["news"].get("value") or {}).get("count") is not None),
            },
            "news.latest": {
                "applicable": total, "present": news_latest_present,
                "missing": total - news_latest_present,
                "rate": rate(news_latest_present, total),
                "missing_codes": [item["code"] for item in items if item["news"]["status"] != "covered"],
            },
        },
    }


_GAP_KIND = {
    "not_applicable": "not_applicable",
    "missing": "missing",
    "source_missing": "missing",
    "no_data": "no_data",
    "no_match": "no_match",
    "no_alias": "no_alias",
}


def _gaps(item: dict) -> dict:
    """缺口报告：kind 区分「真缺失 / 不适用 / 无记录 / 未匹配」，reason 如实。"""
    gaps = {}
    for field, block in (("industry", item["industry"]),
                         ("announcements", item["announcements"]),
                         ("news", item["news"])):
        status = block.get("status")
        if status != "covered":
            gaps[field] = {"kind": _GAP_KIND.get(status, status), "reason": block.get("reason")}
    return gaps


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def _missing_report(items: list[dict]) -> dict:
    """缺口清单：按 kind/字段汇总，并保留逐票明细（不含结论）。"""
    by_kind: dict[str, int] = {}
    by_field: dict[str, int] = {}
    rows = []
    for item in items:
        gaps = item["gaps"]
        if not gaps:
            continue
        for field, gap in gaps.items():
            by_kind[gap["kind"]] = by_kind.get(gap["kind"], 0) + 1
            by_field[field] = by_field.get(field, 0) + 1
        rows.append({
            "code": item["code"],
            "dw_code": item["dw_code"],
            "market": item["market"],
            "in_primary_pool": item["in_primary_pool"],
            "gaps": gaps,
        })
    return {
        "summary": {
            "items_with_gaps": len(rows),
            "by_kind": by_kind,
            "by_field": by_field,
            "kind_legend": {
                "not_applicable": "数据源不覆盖该市场（非抓取失败）",
                "missing": "数据源存在但该股无有效记录（真缺失）",
                "no_data": "窗口内 0 条（真实无记录/未落仓，非缺失值）",
                "no_match": "新闻关键词未命中（可能确无新闻或别名不全）",
                "no_alias": "无名称别名，无法归属",
            },
        },
        "items": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="ACAD1 基本面证据只读适配器（行业PIT/公告/新闻）")
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT), help="quant_backtest/data 根目录")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出 JSON（唯一写操作）")
    parser.add_argument("--pool-date", default=None, help="池日 YYYY-MM-DD；默认取 PG 最新 pool_date")
    parser.add_argument("--pool-json", default=None, help="静态池 JSON（替代 PG）；只读")
    parser.add_argument("--extra-pool-json", default=None, help="附加清单 JSON（如 ACAD1 复核清单），只读")
    parser.add_argument("--db-url", default=None, help="PG DSN；默认取 DB_MAIN_URL")
    parser.add_argument("--anns-history-db", default="auto", help="ann_history.db 路径或 none；默认自动探测")
    parser.add_argument("--news-db", default="auto", help="news.db 路径或 none；默认自动探测")
    parser.add_argument("--ann-lookback-days", type=int, default=90)
    parser.add_argument("--news-lookback-days", type=int, default=30)
    parser.add_argument("--codes", default=None, help="仅跑指定代码（逗号分隔，冒烟用）")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    data_root = Path(args.data_root)
    tushare = data_root / "tushare"
    if not tushare.exists():
        raise SystemExit(f"数据仓不存在：{tushare}")

    # 1) 池：PG 只读（默认）或静态 JSON
    pool_source = load_pool_db(_db_url(args.db_url), args.pool_date) if not args.pool_json \
        else load_pool_json(Path(args.pool_json))
    if not pool_source["entries"]:
        raise SystemExit(f"池为空：{pool_source['source']}")
    asof = pool_source["pool_date"] or (str(pool_source["entries"][0].get("data_date")) if pool_source["entries"][0].get("data_date") else None)
    if not asof:
        raise SystemExit("无法确定 as-of 池日；请用 --pool-date")
    asof_date = date.fromisoformat(asof)
    asof_int = int(asof_date.strftime("%Y%m%d"))

    extra_source = None
    if args.extra_pool_json:
        extra_path = Path(args.extra_pool_json)
        if extra_path.exists():
            extra_source = load_pool_json(extra_path)
            extra_source["label"] = "acad1_review_list"
        else:
            print(f"[warn] extra pool JSON 不存在，忽略：{extra_path}", file=sys.stderr)

    # 2) 数据源（全部只读）
    sw = load_sw(data_root)
    crosscheck = load_industry_crosscheck(data_root)
    basics = load_basics(data_root)
    ann_history_path = None
    if args.anns_history_db.lower() != "none":
        ann_history_path = Path(args.anns_history_db) if args.anns_history_db != "auto" else _first_existing([
            data_root / "ann_history" / "ann_history.db",
            data_root.parent.parent / "zerobound-kg" / "data" / "ann_history" / "ann_history.db",
            data_root.parent.parent.parent / "zerobound-kg" / "data" / "ann_history" / "ann_history.db",
        ])
    news_db_path = None
    if args.news_db.lower() != "none":
        news_db_path = Path(args.news_db) if args.news_db != "auto" else _first_existing([
            data_root / "news.db",
            data_root.parent / "news.db",
            data_root.parent.parent / "news_collector" / "news.db",
        ])

    # 3) 待覆盖代码集合：主池 + 附加清单（并按代码/市场去重）
    universe: dict[tuple[str, str], dict] = {}
    for rank, entry in enumerate(pool_source["entries"]):
        key = (entry["market"], _dw_code(entry["code"], entry["market"]))
        universe[key] = {
            "market": entry["market"],
            "code": str(entry["code"]),
            "dw_code": key[1],
            "pool_name": entry.get("pool_name"),
            "strategy": entry.get("strategy"),
            "signal": entry.get("signal"),
            "pool_data_date": entry.get("data_date"),
            "in_primary_pool": True,
            "pool_sources": [f"{pool_source['source']}@{pool_source['pool_date']}"],
            "_rank": rank,
        }
    extra_new = 0
    if extra_source:
        label = f"{extra_source['source']}@{extra_source['pool_date']}"
        for entry in extra_source["entries"]:
            key = (entry["market"], _dw_code(entry["code"], entry["market"]))
            if key in universe:
                universe[key]["pool_sources"].append(label)
                continue
            universe[key] = {
                "market": entry["market"],
                "code": str(entry["code"]),
                "dw_code": key[1],
                "pool_name": entry.get("pool_name"),
                "strategy": entry.get("strategy"),
                "signal": entry.get("signal"),
                "pool_data_date": entry.get("data_date"),
                "in_primary_pool": False,
                "pool_sources": [label],
                "_rank": len(pool_source["entries"]) + extra_new,
            }
            extra_new += 1
    if args.codes:
        wanted = {_dw_code(code.strip(), market) for code, market in
                  ((part.split(":") + ["CN"])[:2] for part in args.codes.split(","))}
        universe = {key: value for key, value in universe.items() if key[1] in wanted}
    items_meta = sorted(
        universe.values(),
        key=lambda meta: (0 if meta["in_primary_pool"] else 1, MARKET_ORDER[meta["market"]], meta["dw_code"]),
    )

    # 4) 公告/新闻源加载（只读）
    ann_window_start_int = int((asof_date - timedelta(days=args.ann_lookback_days)).strftime("%Y%m%d"))
    anns = load_anns_d(data_root, ann_window_start_int, asof_int)
    cn_symbols = sorted({meta["dw_code"].split(".")[0] for meta in items_meta if meta["market"] == "CN"})
    ann_history = load_ann_history(ann_history_path, cn_symbols)
    news_tushare = load_news_tushare(data_root)
    news_db = load_news_db(news_db_path)
    news_cutoff = datetime.combine(asof_date + timedelta(days=1), datetime.min.time()) - timedelta(seconds=1)
    news_cutoff = news_cutoff.replace(tzinfo=None)
    news_win_start = (news_cutoff - timedelta(days=args.news_lookback_days)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    news_source = {"tushare_news": news_tushare, "news_db": news_db}

    # PIT 滞后：sw_l1_member 最新成分事件 vs 池日
    sw_max_in_date = sw["meta"]["in_date_max"]
    pit_lag_days = None
    if sw_max_in_date:
        sw_max_dt = datetime.strptime(sw_max_in_date, "%Y%m%d").date()
        pit_lag_days = (asof_date - sw_max_dt).days

    # 5) 逐票组装证据（只做事实整理，不做结论）
    items = []
    for meta in items_meta:
        market, dw_code = meta["market"], meta["dw_code"]
        basic_cn = basics["cn"].get(dw_code) if market == "CN" else None
        basic_hk = basics["hk"].get(dw_code) if market == "HK" else None
        basic_us = basics["us"].get(meta["code"]) if market == "US" else None
        if market == "CN" and basic_cn:
            name, name_source = basic_cn["name"], "tushare/stock_basic"
        elif market == "HK" and basic_hk:
            name, name_source = basic_hk["name"], "tushare/hk_basic"
        elif market == "US" and basic_us:
            name, name_source = basic_us["name"], "tushare/us_basic"
        else:
            name, name_source = None, "missing_basic"

        # 新闻别名：中文名变体 + 英文核心名 + 括号代码；加密用代码前缀+静态常见名
        aliases: list[str] = []
        regex_aliases: list[str] = []
        alias_notes = []
        if market == "CN" and basic_cn:
            aliases += _cjk_variants(basic_cn["name"])
            regex_aliases.append(_bracket_code(dw_code.split(".")[0]))
        elif market == "HK":
            if basic_hk:
                aliases += _cjk_variants(basic_hk["name"])
            else:
                alias_notes.append("hk_basic 无该代码名称，只能代码括号匹配")
            regex_aliases.append(_bracket_code(meta["code"]))
        elif market == "US":
            if basic_us:
                if _has_cjk(basic_us["name"]):
                    aliases += _cjk_variants(basic_us["name"])
                english = _clean_en_name(basic_us["enname"] or basic_us["name"])
                if english:
                    aliases.append(english)
            regex_aliases.append(_bracket_code(meta["code"]))
        else:  # CRYPTO
            base = str(meta["code"]).split("/")[0].strip()
            if base:
                aliases.append(base)
            aliases += _CRYPTO_STATIC_ALIASES.get(str(meta["code"]), [])
            alias_notes.append("crypto 别名 = 代码前缀 + 静态常见中文名映射")
        aliases = _dedupe(aliases)

        item = {
            "code": meta["code"],
            "dw_code": dw_code,
            "market": market,
            "name": name,
            "name_source": name_source,
            "pool_name": meta["pool_name"],
            "in_primary_pool": meta["in_primary_pool"],
            "pool_sources": meta["pool_sources"],
            "pool_strategy": meta["strategy"],
            "pool_signal": meta["signal"],
            "pool_data_date": meta["pool_data_date"],
            "asof": asof,
            "asof_int": asof_int,
            "pit_lag_days": pit_lag_days,
            "ann_lookback_days": args.ann_lookback_days,
            "news_lookback_days": args.news_lookback_days,
            "ann_window_start_int": ann_window_start_int,
            "aliases": aliases,
            "regex_aliases": regex_aliases,
            "alias_notes": alias_notes,
        }
        item["industry"] = analyze_industry(item, sw, crosscheck, basics)
        item["announcements"] = analyze_announcements(item, anns, ann_history)
        item["news"] = analyze_news(item, news_source, news_win_start, news_cutoff)
        item["gaps"] = _gaps(item)
        items.append(item)

    # 6) 覆盖/缺失报告（主池、全集分别统计）
    primary_items = [item for item in items if item["in_primary_pool"]]
    coverage = {
        "primary_pool": _coverage(primary_items),
        "all_items": _coverage(items),
    }

    # 7) 交叉校验汇总（申万 PIT vs index_member_all 当前快照）
    checked = [item for item in items if item["market"] == "CN"
               and item["industry"]["status"] == "covered"
               and item["industry"]["crosscheck_index_member_all"]["l1_code"] is not None]
    mismatch = [item["code"] for item in checked
                if item["industry"]["crosscheck_index_member_all"]["match"] is False]

    warnings = []
    if pit_lag_days is not None:
        warnings.append(
            f"PIT 滞后：sw_l1_member 最新成分事件 in_date={sw_max_in_date}，池日 {asof}，"
            f"滞后 {pit_lag_days} 天；该日之后的申万 L1 成分调整不可见，本适配器不推断。")
    if anns.get("meta", {}).get("exists") and anns["meta"].get("file_date_max") != str(asof_int):
        warnings.append(
            f"公告源 anns_d 最新文件日={anns['meta'].get('file_date_max')}，"
            f"池日={asof_int}：池日当日公告文件未落仓，最近公告可能少 1 天。")
    if mismatch:
        warnings.append(f"sw_l1_member 与 index_member_all 行业不一致 {len(mismatch)} 只：{mismatch}（需人工复核）")
    if extra_source:
        warnings.append(
            f"附加清单 {extra_source['source']} 提供 {len(extra_source['entries'])} 只，"
            f"其中 {extra_new} 只不在主池；items 用 in_primary_pool 区分，池归属不因附加清单改变。")
    missing_hk = [item["code"] for item in items if item["market"] == "HK"
                  and item["name_source"] == "missing_basic"]
    if missing_hk:
        warnings.append(f"hk_basic 缺代码：{missing_hk}（名称/行业无映射，新闻仅括号代码匹配）")
    warnings.append(
        "新闻归属为关键词匹配（非数据仓原生个股映射），有漏配/误配风险；tushare/news 与 news.db "
        "为两个独立新闻源、内容有重叠，count 不相加，需人工复核。")
    warnings.append(
        "HK/US/CRYPTO 的行业、A 股公告字段为 not_applicable（无对应数据源），不是抓取失败；不编造替代值。")
    if ann_history.get("meta", {}).get("exists"):
        warnings.append(
            f"ann_history.db 覆盖 {ann_history['meta'].get('notice_date_min')}.."
            f"{ann_history['meta'].get('notice_date_max')}（stock_watermark 最后完成 "
            f"{ann_history['meta'].get('stock_watermark_finished_at')}），仅作公告全历史交叉校验。")

    output = {
        "schema": "acad1_fundamentals/v1",
        "generated_at": _now(),
        "read_only": True,
        "purpose": "为 ACAD1 研究卡提供只读基本面证据：申万 L1 行业 PIT、公告、新闻；不含评级/方向/建议。",
        "disclaimer": "证据适配器输出，不是投资结论；缺失字段为 null 且带 reason，不推断、不补造、不自动结论。",
        "generator": {
            "script": "scripts/dw_fundamentals.py",
            "command": " ".join([Path(sys.executable).name] + sys.argv),
            "data_root": str(data_root),
            "python": sys.version.split()[0],
            "pandas": pd.__version__,
        },
        "asof": {
            "pool_date": asof,
            "pit_asof": asof,
            "news_cutoff": news_cutoff.strftime("%Y-%m-%d %H:%M:%S"),
            "time_zone": "Asia/Shanghai（数据源本地时间，未做时区换算）",
            "ann_lookback_days": args.ann_lookback_days,
            "news_lookback_days": args.news_lookback_days,
        },
        "pool": {
            "source": pool_source["source"],
            "pool_date": pool_source["pool_date"],
            "count": len(pool_source["entries"]),
            "by_market": {market: sum(1 for entry in pool_source["entries"] if entry["market"] == market)
                          for market in MARKETS},
            "extra_pool": None if not extra_source else {
                "source": extra_source["source"],
                "pool_date": extra_source["pool_date"],
                "count": len(extra_source["entries"]),
                "new_codes": extra_new,
                "by_market": {market: sum(1 for entry in extra_source["entries"] if entry["market"] == market)
                              for market in MARKETS},
            },
            "universe": {"count": len(items), "primary": len(primary_items), "extra_only": extra_new},
        },
        "pit": {
            "asof": asof,
            "sw_member_source": sw["meta"]["path"],
            "sw_member_latest_event_date": sw_max_in_date,
            "sw_member_lag_days": pit_lag_days,
            "method": "in_date <= asof 且 (out_date 为空 或 out_date >= asof) 的最后一条有效成分；"
                      "行业名 = sw_classify_l1.level='L1' 映射",
            "caveat": "sw_l1_member 最新成分事件止于 2026-08-27；2026-08-27 之后调入/调出的个股仍按旧桶输出，"
                      "不推断新行业；如后续拿到更新快照需重跑。",
            "crosscheck": {
                "source": "tushare/index_member_all（当前成分快照）",
                "in_date_max": crosscheck["meta"].get("in_date_max"),
                "cn_covered_items": len(checked),
                "match": len(checked) - len(mismatch),
                "mismatch": mismatch,
                "absent": [item["code"] for item in items if item["market"] == "CN"
                           and item["industry"]["status"] == "covered"
                           and item["industry"]["crosscheck_index_member_all"]["l1_code"] is None],
                "note": "交叉校验仅为补充证据；行业主口径仍是 sw_l1_member（任务指定 PIT 源）",
            },
        },
        "sources": {
            "sw_l1_member": sw["meta"],
            "sw_classify_l1": {
                "path": str(tushare / "sw_classify_l1"),
                "l1_count": sw["meta"]["classify_l1_count"],
                "level": "L1",
            },
            "index_member_all": crosscheck["meta"],
            "anns_d": anns.get("meta"),
            "anns_history_db": ann_history.get("meta"),
            "news_tushare": news_tushare.get("meta"),
            "news_db": news_db.get("meta"),
            "basics": basics["meta"],
        },
        "coverage": coverage,
        "items": [_public_item(item) for item in items],
        "missing": _missing_report(items),
        "warnings": warnings,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    # 8) 控制台摘要（证据自检用；不构成结论）
    def _rate_text(section: dict, key: str = "applicable_rate") -> str:
        value = section.get(key)
        return "-" if value is None else f"{value:.2%}"

    print("== ACAD1 fundamentals 只读适配 ==")
    print(f"asof={asof} pool={pool_source['source']} primary={len(primary_items)} "
          f"universe={len(items)} (+{extra_new} extra)")
    print(f"sw_l1_member rows={sw['meta']['rows']} L1={sw['meta']['l1_industries']} "
          f"in_date_max={sw_max_in_date} PIT_lag={pit_lag_days}d")
    print(f"anns_d files={anns.get('meta', {}).get('daily_files')} "
          f"latest={anns.get('meta', {}).get('file_date_max')} window={anns.get('meta', {}).get('window_files')}files")
    print(f"ann_history.db exists={ann_history.get('meta', {}).get('exists')} "
          f"rows={ann_history.get('meta', {}).get('rows')} "
          f"range={ann_history.get('meta', {}).get('notice_date_min')}..{ann_history.get('meta', {}).get('notice_date_max')}")
    print(f"news tushare unique={news_tushare.get('meta', {}).get('unique_items')} | "
          f"news.db rows={news_db.get('meta', {}).get('rows')}")
    for name, section in coverage.items():
        print(f"coverage[{name}]: industry {section['industry']['covered']}/"
              f"{section['industry']['applicable']} ({_rate_text(section['industry'])}) CN适用 | "
              f"announcements {section['announcements']['covered']}/"
              f"{section['announcements']['applicable']} ({_rate_text(section['announcements'])}) | "
              f"news {section['news']['covered']}/{section['news']['eligible']} "
              f"({_rate_text(section['news'], 'rate')})")
    print(f"fields: name {coverage['all_items']['fields']['name']['present']}/{len(items)} | "
          f"ann_latest {coverage['all_items']['fields']['announcements.latest_date']['present']}/"
          f"{coverage['all_items']['fields']['announcements.latest_date']['applicable']}(CN) | "
          f"news_latest {coverage['all_items']['fields']['news.latest']['present']}/{len(items)}")
    print(f"PIT crosscheck index_member_all: {len(checked) - len(mismatch)}/{len(checked)} match")
    if not args.quiet:
        for item in items:
            industry = item["industry"]
            if industry["status"] == "covered":
                industry_text = industry["value"]["sw_l1_name"]
            else:
                industry_text = f"<{industry['status']}>"
            ann = item["announcements"].get("value") or {}
            news = item["news"].get("value") or {}
            news_latest = news.get("latest") or {}
            print(f"  {item['market']:6s} {item['code']:10s} {str(item['name'] or '-'):10s} "
                  f"{industry_text:10s} ann={str(ann.get('count')):>3s}@"
                  f"{ann.get('latest_date') or '-'} news={news.get('count')}@"
                  f"{news_latest.get('time') or '-'}")
    print(f"saved {output_path} ({output_path.stat().st_size} bytes)")
    return 0


def _public_item(item: dict) -> dict:
    """对外输出：去掉内部临时字段。"""
    return {key: value for key, value in item.items() if not key.startswith("_")}


if __name__ == "__main__":
    raise SystemExit(main())
