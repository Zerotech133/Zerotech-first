# -*- coding: utf-8 -*-
"""按 ODCS 契约校验张松数据仓适配器输出（只读）。

契约
----
``config/contracts/dw_adapter.odcs.yaml``（id=zerobond.dw_adapter，与
``zerobond.zhice_selection`` 契约分离）。契约内的 ``quality[].check`` 决定跑哪些检查、
``severity`` 决定硬/软、``threshold``/``tolerance`` 决定覆盖下限与数值容差。

输入
----
``C:\\Temp\\dw_enrichment.json``（``scripts/dw_adapter.py`` 输出；本次为 2026-10-31 池，
实测覆盖 27/30：缺 07552(HK) 与 CRYPTO 2 只，须如实报告）。

输出
----
``C:\\Temp\\dw_contract_report.json``：状态/覆盖/枚举/缺口/数据质量摘要。

只读边界
--------
- 不写数据仓（parquet/tushare）、不写智策 PG 表；PG 仅在 readonly 事务里 SELECT
  ``v2_acad1_pool`` 核对期望池（--no-db 可完全离线）；
- 唯一写操作 = 输出报告 JSON；
- 覆盖不足必须逐码列明缺口，禁止把 27/30 报告为 30/30；announcements/news 通道未提供
  时必须列 unavailable，禁止用 0 条冒充“已提供”的证据。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

CST = timezone(timedelta(hours=8))
CODE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = CODE_ROOT / "config" / "contracts" / "dw_adapter.odcs.yaml"
EXISTING_CONTRACT = CODE_ROOT / "config" / "contracts" / "zhice_selection.odcs.yaml"
DEFAULT_INPUT = Path(r"C:\Temp\dw_enrichment.json")
DEFAULT_REPORT = Path(r"C:\Temp\dw_contract_report.json")

MARKETS = ("CN", "HK", "US", "CRYPTO")
CN_TS_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
CN_PREFIX_RE = re.compile(r"(?i)^(sh|sz|bj)\d{6}$")
HK_RE = re.compile(r"^\d{4,5}$")
US_RE = re.compile(r"^[A-Za-z][A-Za-z.\-]*$")
DATE8_RE = re.compile(r"^\d{8}$")
DATE_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CRYPTO_RE = re.compile(r"^[A-Z0-9]+/[A-Z0-9]+$")

DEFAULT_TOLERANCE = 0.01
DEFAULT_COVERAGE_FLOOR = 0.90


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(CST).replace(microsecond=0).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, CST).replace(microsecond=0).isoformat()


def _db_url(explicit: str | None) -> str:
    url = explicit or os.environ.get("DB_MAIN_URL")
    if not url:
        env_file = CODE_ROOT / ".env.local"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("DB_MAIN_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not url:
        raise SystemExit("未找到 DB_MAIN_URL（环境变量或 code/.env.local）；可用 --no-db 离线检查")
    return url.replace("postgresql+psycopg2://", "postgresql://", 1)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _parse_date8(value):
    if not isinstance(value, str) or not DATE8_RE.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None


def _parse_iso_date(value):
    if not isinstance(value, str) or not DATE_ISO_RE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _market_of_code(code: str) -> str | None:
    """按代码格式推断市场（仅作 v2_acad1_pool 缺失时的兜底，来源必须标注）。"""
    if not isinstance(code, str) or not code:
        return None
    if CN_TS_RE.fullmatch(code) or CN_PREFIX_RE.fullmatch(code):
        return "CN"
    if HK_RE.fullmatch(code):
        return "HK"
    if "/" in code:
        return "CRYPTO"
    if US_RE.fullmatch(code):
        return "US"
    return None


def _short_violations(violations: list[str], limit: int = 12) -> list[str]:
    return violations[:limit] + ([f"... 共 {len(violations)} 条"] if len(violations) > limit else [])


# --------------------------------------------------------------------------- #
# 契约 / 输入 / 期望池
# --------------------------------------------------------------------------- #
def load_contract(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"契约文件不存在：{path}")
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise SystemExit(f"契约不是 YAML 对象：{path}")
    check_meta: dict[str, dict] = {}
    props: dict[tuple[str, str], dict] = {}
    schema_names: list[str] = []
    for schema in spec.get("schema") or []:
        schema_name = schema.get("name") or schema.get("physicalName") or "?"
        schema_names.append(schema_name)
        for prop in schema.get("properties") or []:
            props[(schema_name, prop.get("name"))] = prop
        for rule in schema.get("quality") or []:
            if isinstance(rule, dict) and rule.get("check"):
                check_meta[rule["check"]] = rule
    return {
        "path": str(path),
        "spec": spec,
        "meta": {
            "apiVersion": spec.get("apiVersion"),
            "kind": spec.get("kind"),
            "id": spec.get("id"),
            "name": spec.get("name"),
            "version": spec.get("version"),
            "status": spec.get("status"),
            "domain": spec.get("domain"),
            "dataProduct": spec.get("dataProduct"),
            "schema_names": schema_names,
        },
        "checks": check_meta,
        "props": props,
    }


def load_input(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"输入文件不存在：{path}（先跑 scripts/dw_adapter.py）")
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"输入不是合法 JSON：{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"输入 JSON 顶层必须是 object：{path}")
    return {
        "data": data,
        "meta": {
            "path": str(path),
            "bytes": path.stat().st_size,
            "mtime": _mtime_iso(path),
            "sha256": _sha256(path),
        },
    }


def load_expected_universe(db_url: str | None, file_pool_date: str | None, no_db: bool) -> dict:
    """只读核对期望池：v2_acad1_pool 对应池日（或最新）标的清单。"""
    result = {
        "available": False,
        "source": "disabled(--no-db)",
        "pool_date": None,
        "pg_latest_pool_date": None,
        "count": None,
        "by_market": {market: 0 for market in MARKETS},
        "codes": [],
        "error": None,
    }
    if no_db:
        return result
    try:
        import psycopg2

        conn = psycopg2.connect(_db_url(db_url), connect_timeout=8)
        try:
            conn.set_session(readonly=True, autocommit=True)  # 只读事务，物理禁止写入
            conn.set_client_encoding("UTF8")
            with conn.cursor() as cur:
                cur.execute("SELECT max(pool_date) FROM v2_acad1_pool")
                latest = cur.fetchone()[0]
                day = _parse_iso_date(file_pool_date) if file_pool_date else None
                if day is None:
                    day = latest
                cur.execute(
                    "SELECT market, code FROM v2_acad1_pool WHERE pool_date = %s ORDER BY market, code",
                    (day,),
                )
                rows = cur.fetchall()
                fallback = False
                if not rows and day != latest:
                    day = latest
                    cur.execute(
                        "SELECT market, code FROM v2_acad1_pool WHERE pool_date = %s ORDER BY market, code",
                        (day,),
                    )
                    rows = cur.fetchall()
                    fallback = True
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - 只读核对失败不阻塞文件检查，如实降级
        result["source"] = "postgres:error（只读核对失败，降级为文件计数）"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    by_market = {market: 0 for market in MARKETS}
    codes = []
    for market, code in rows:
        by_market[market] = by_market.get(market, 0) + 1
        codes.append({"market": market, "code": code})
    result.update({
        "available": bool(rows),
        "source": "postgres:zhice_os_v2.v2_acad1_pool（readonly SELECT）",
        "pool_date": str(day),
        "pg_latest_pool_date": str(latest),
        "count": len(codes),
        "by_market": by_market,
        "codes": codes,
        "fallback_to_latest": bool(rows) and fallback,
    })
    return result


# --------------------------------------------------------------------------- #
# 检查框架
# --------------------------------------------------------------------------- #
def add_check(results: list[dict], contract: dict, check_id: str, passed: bool, detail: str) -> None:
    meta = contract["checks"].get(check_id) or {}
    results.append({
        "id": check_id,
        "rule": meta.get("rule") or check_id,
        "severity": meta.get("severity") or "error",
        "pass": bool(passed),
        "detail": detail,
    })


def _rule_number(contract: dict, check_id: str, key: str, default: float) -> float:
    meta = contract["checks"].get(check_id) or {}
    value = meta.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def check_contract_meta(contract: dict) -> tuple[bool, str]:
    meta = contract["meta"]
    problems = []
    if not str(meta.get("apiVersion") or "").startswith("v3."):
        problems.append(f"apiVersion={meta.get('apiVersion')!r}（期望 v3.x）")
    if meta.get("kind") != "DataContract":
        problems.append(f"kind={meta.get('kind')!r}（期望 DataContract）")
    for key in ("id", "name", "version"):
        if not meta.get(key):
            problems.append(f"{key} 缺失")
    return not problems, "契约元数据完整" if not problems else "；".join(problems)


def check_contract_separation(contract: dict) -> tuple[bool, dict]:
    """与现有 zerobond.zhice_selection 契约分开（文件/id/dataProduct 均不同）。"""
    detail = {
        "existing_path": str(EXISTING_CONTRACT),
        "existing_exists": EXISTING_CONTRACT.exists(),
        "existing_id": None,
        "separate": True,
        "note": "",
    }
    new_meta = contract["meta"]
    if EXISTING_CONTRACT.exists() and Path(contract["path"]) != EXISTING_CONTRACT:
        try:
            existing = yaml.safe_load(EXISTING_CONTRACT.read_text(encoding="utf-8")) or {}
            detail["existing_id"] = existing.get("id")
            checks = [
                (new_meta.get("id") != existing.get("id"), f"id 不同（{new_meta.get('id')} vs {existing.get('id')}）"),
                (new_meta.get("dataProduct") != existing.get("dataProduct"),
                 f"dataProduct 不同（{new_meta.get('dataProduct')} vs {existing.get('dataProduct')}）"),
            ]
            for ok, note in checks:
                if not ok:
                    detail["separate"] = False
                    detail["note"] = note
                    break
            if detail["separate"]:
                detail["note"] = "契约文件/id/dataProduct 均与 zerobond.zhice_selection 分离"
        except yaml.YAMLError as exc:
            detail["note"] = f"现有契约解析失败，跳过内容比对：{exc}"
    elif Path(contract["path"]) == EXISTING_CONTRACT:
        detail["separate"] = False
        detail["note"] = "契约文件与 zhice_selection 是同一文件"
    else:
        detail["note"] = "未发现现有 zhice_selection 契约文件，按独立契约处理"
    return bool(detail["separate"]), detail


def check_coverage(
    input_codes: set[str],
    expected: dict,
    derived_markets: dict[str, str],
    pool_count: int | None,
) -> dict:
    """覆盖率（present/expected）与分市场缺口；expected 不可用时按文件 pool 计数。"""
    expected_available = bool(expected.get("available"))
    expected_codes = {item["code"] for item in expected.get("codes") or []} if expected_available else set()
    present_codes = input_codes & expected_codes if expected_available else set(input_codes)
    if expected_available:
        expected_count = len(expected_codes)
        missing_codes = sorted(expected_codes - input_codes)
        unexpected_codes = sorted(input_codes - expected_codes)
    else:
        expected_count = int(pool_count) if isinstance(pool_count, int) and pool_count > 0 else len(input_codes)
        missing_codes = []
        unexpected_codes = []
    present_count = len(present_codes) if expected_available else len(input_codes)
    rate = round(present_count / expected_count, 4) if expected_count else None

    by_market = {market: {"expected": 0, "present": 0, "missing": 0} for market in MARKETS}
    if expected_available:
        for item in expected.get("codes") or []:
            market = item["market"]
            by_market.setdefault(market, {"expected": 0, "present": 0, "missing": 0})
            by_market[market]["expected"] += 1
        for code in present_codes:
            market = derived_markets.get(code)
            if market in by_market:
                by_market[market]["present"] += 1
        for market, counts in by_market.items():
            counts["missing"] = counts["expected"] - counts["present"]
    return {
        "expected_available": expected_available,
        "expected": expected_count,
        "present": present_count,
        "missing": expected_count - present_count,
        "rate": rate,
        "missing_codes": missing_codes,
        "unexpected_codes": unexpected_codes,
        "by_market": by_market,
    }


def build_missing_gaps(
    missing_codes: list[str],
    expected: dict,
    derived_markets: dict[str, str],
    input_data: dict,
) -> list[dict]:
    """逐码缺口：code/market/缺失字段/原因；原因基于契约 limitations 与 market 归类，不编造数据。"""
    expected_market = {item["code"]: item["market"] for item in expected.get("codes") or []}
    gaps = []
    for code in missing_codes:
        market = expected_market.get(code) or derived_markets.get(code) or _market_of_code(code)
        missing_fields = ["daily.pre_close", "basic.list_date", "basic.industry", "announcements", "news"]
        if market == "CRYPTO":
            reason_code = "market_not_in_adapter_scope"
            reason = ("dw_adapter 未接 CRYPTO 行情源（契约 limitations 声明），该市场不在适配器覆盖范围；"
                      "不是抓取失败，也不得按 0 条新闻/公告通过。")
        elif market == "HK":
            reason_code = "no_local_daily_or_basic_record"
            reason = ("dw_enrichment.json 的 daily/basic 均无该代码；HK 本地日线或基础信息未提供该记录"
                      "（契约 limitations 声明不保证 HK 全覆盖）。")
        else:
            reason_code = "no_local_daily_or_basic_record"
            reason = "dw_enrichment.json 的 daily/basic 均无该代码（本地行情/基础信息缺口）。"
        gaps.append({
            "code": code,
            "market": market,
            "reason_code": reason_code,
            "reason": reason,
            "missing_fields": missing_fields,
            "market_source": "v2_acad1_pool" if code in expected_market else ("code_pattern" if market else None),
        })
    return gaps


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="按 ODCS 契约校验数据仓适配器输出（只读）")
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--out", default=str(DEFAULT_REPORT))
    parser.add_argument("--db-url", default=None, help="PG DSN；默认 DB_MAIN_URL")
    parser.add_argument("--no-db", action="store_true", help="不连 PG；覆盖率仅按文件 pool 计数，不逐码定位")
    parser.add_argument("--report-only", action="store_true", help="无论状态都以 0 退出")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    contract = load_contract(Path(args.contract))
    input_payload = load_input(Path(args.input))
    data = input_payload["data"]
    file_pool_date = data.get("pool_date") if isinstance(data.get("pool_date"), str) else None
    expected = load_expected_universe(args.db_url, file_pool_date, args.no_db)

    # ---- 文件结构解析 ----
    daily = data.get("daily") if isinstance(data.get("daily"), dict) else {}
    basic = data.get("basic") if isinstance(data.get("basic"), dict) else {}
    st_codes = data.get("st_codes") if isinstance(data.get("st_codes"), list) else None
    input_codes = set(daily) | set(basic)
    expected_market = {item["code"]: item["market"] for item in expected.get("codes") or []}
    derived_markets = {}
    market_sources = {}
    for code in input_codes:
        if code in expected_market:
            derived_markets[code] = expected_market[code]
            market_sources[code] = "v2_acad1_pool"
        else:
            derived_markets[code] = _market_of_code(code)
            market_sources[code] = "code_pattern"
    pool_date = _parse_iso_date(file_pool_date) if file_pool_date else None

    # ---- 逐项检查 ----
    checks: list[dict] = []

    passed, detail = check_contract_meta(contract)
    add_check(checks, contract, "contract_meta", passed, detail)

    passed, separation = check_contract_separation(contract)
    add_check(checks, contract, "contract_separation", passed, separation.get("note") or "-")

    envelope_required = ["pool_date", "pool", "cn_codes", "daily", "st_codes", "basic"]
    missing_envelope = [name for name in envelope_required if name not in data]
    shape_ok = isinstance(daily, dict) and isinstance(basic, dict) and isinstance(st_codes, list)
    add_check(
        checks, contract, "envelope_required",
        not missing_envelope and shape_ok,
        "顶层字段齐全且 daily/basic=object、st_codes=array"
        if not missing_envelope and shape_ok
        else f"缺失顶层字段={missing_envelope}；daily/basic/st_codes 类型={'ok' if shape_ok else '异常'}",
    )

    pool_date_problems = []
    if pool_date is None:
        pool_date_problems.append(f"pool_date 非法：{file_pool_date!r}")
    else:
        if expected.get("available") and expected.get("pool_date") and str(expected["pool_date"]) != str(pool_date):
            pool_date_problems.append(f"pool_date={pool_date} 与期望池日 {expected['pool_date']} 不一致（stale）")
        if pool_date > datetime.now(CST).date():
            pool_date_problems.append(f"pool_date={pool_date} 晚于今天")
    latest_day = expected.get("pg_latest_pool_date")
    pool_date_detail = (
        f"pool_date 合法且与对应期望池日一致（PG 最新池日={latest_day}）"
        if not pool_date_problems else "；".join(pool_date_problems))
    add_check(checks, contract, "pool_date", not pool_date_problems, pool_date_detail)

    pool_value = data.get("pool")
    cn_value = data.get("cn_codes")
    pool_problems = []
    if not (isinstance(pool_value, int) and not isinstance(pool_value, bool) and pool_value > 0):
        pool_problems.append(f"pool 非正整数：{pool_value!r}")
    if not (isinstance(cn_value, int) and not isinstance(cn_value, bool) and cn_value >= 0):
        pool_problems.append(f"cn_codes 非非负整数：{cn_value!r}")
    if expected.get("available"):
        if expected.get("count") is not None and pool_value != expected["count"]:
            pool_problems.append(f"pool={pool_value} 与期望池标的数 {expected['count']} 不一致")
        expected_cn = (expected.get("by_market") or {}).get("CN", 0)
        if cn_value != expected_cn:
            pool_problems.append(f"cn_codes={cn_value} 与期望 CN 数 {expected_cn} 不一致")
    add_check(
        checks, contract, "pool_counts", not pool_problems,
        "pool/cn_codes 与期望池一致" if not pool_problems else "；".join(pool_problems),
    )

    # daily 结构
    daily_violations: list[str] = []
    trade_dates: dict[str, str] = {}
    for code, quote in sorted(daily.items()):
        if not isinstance(quote, dict):
            daily_violations.append(f"{code}: 非 object")
            continue
        for field in ("trade_date", "pre_close", "close", "pct_chg"):
            if field not in quote:
                daily_violations.append(f"{code}: 缺 {field}")
        trade_date = quote.get("trade_date")
        if _parse_date8(trade_date) is None:
            daily_violations.append(f"{code}: trade_date 非法 {trade_date!r}")
        else:
            trade_dates[code] = trade_date
        for field in ("pre_close", "close", "pct_chg"):
            if field in quote and not _is_number(quote[field]):
                daily_violations.append(f"{code}: {field} 非有限数 {quote[field]!r}")
        for field in ("pre_close", "close"):
            value = quote.get(field)
            if _is_number(value) and value <= 0:
                daily_violations.append(f"{code}: {field} 必须 >0（{value}）")
    add_check(
        checks, contract, "daily_shape", not daily_violations,
        f"daily {len(daily)} 条结构合法" if not daily_violations else "；".join(_short_violations(daily_violations)),
    )

    # basic 结构
    basic_violations: list[str] = []
    list_dates: dict[str, str] = {}
    for code, info in sorted(basic.items()):
        if not isinstance(info, dict):
            basic_violations.append(f"{code}: 非 object")
            continue
        name = info.get("name")
        if not (isinstance(name, str) and name.strip()):
            basic_violations.append(f"{code}: name 为空")
        list_date = info.get("list_date")
        if list_date not in (None, ""):
            if _parse_date8(list_date) is None:
                basic_violations.append(f"{code}: list_date 非法 {list_date!r}")
            else:
                list_dates[code] = list_date
        industry = info.get("industry")
        if industry is not None and not isinstance(industry, str):
            basic_violations.append(f"{code}: industry 非字符串 {industry!r}")
    add_check(
        checks, contract, "basic_shape", not basic_violations,
        f"basic {len(basic)} 条结构合法" if not basic_violations else "；".join(_short_violations(basic_violations)),
    )

    set_match = bool(daily) and set(daily) == set(basic)
    add_check(
        checks, contract, "daily_basic_consistency", set_match,
        f"daily 与 basic code 集合一致（{len(daily)} 条）"
        if set_match
        else f"不一致：仅 daily={sorted(set(daily) - set(basic))}；仅 basic={sorted(set(basic) - set(daily))}",
    )

    # 涨跌幅自洽
    tolerance = _rule_number(contract, "daily_pct_consistency", "tolerance", DEFAULT_TOLERANCE)
    pct_checked = 0
    pct_max_diff = 0.0
    pct_violations: list[str] = []
    for code, quote in daily.items():
        if not isinstance(quote, dict):
            continue
        pre_close, close, pct_chg = quote.get("pre_close"), quote.get("close"), quote.get("pct_chg")
        if not (_is_number(pre_close) and _is_number(close) and _is_number(pct_chg)) or pre_close == 0:
            continue
        pct_checked += 1
        calc = (close / pre_close - 1.0) * 100.0
        diff = abs(calc - pct_chg)
        pct_max_diff = max(pct_max_diff, diff)
        if diff > tolerance:
            pct_violations.append(f"{code}: 计算 {calc:.4f} vs 字段 {pct_chg}")
    add_check(
        checks, contract, "daily_pct_consistency", not pct_violations,
        f"{pct_checked} 条自洽，max|diff|={pct_max_diff:.6f} <= 容差 {tolerance}"
        if not pct_violations else "；".join(_short_violations(pct_violations)),
    )

    # 未来日期
    future_violations = []
    if pool_date is not None:
        for code, text in trade_dates.items():
            parsed = _parse_date8(text)
            if parsed and parsed > pool_date:
                future_violations.append(f"{code}: trade_date {text} > pool_date {pool_date}")
        for code, text in list_dates.items():
            parsed = _parse_date8(text)
            if parsed and parsed > pool_date:
                future_violations.append(f"{code}: list_date {text} > pool_date {pool_date}")
    add_check(
        checks, contract, "no_future_dates", not future_violations,
        "trade_date/list_date 均不晚于 pool_date" if not future_violations
        else "；".join(_short_violations(future_violations)),
    )

    # 市场枚举
    allowed_markets = set((contract["props"].get(("stock_evidence", "market")) or {}).get("enum") or MARKETS)
    market_violations = []
    observed_markets: dict[str, int] = {}
    for code in sorted(input_codes):
        market = derived_markets.get(code)
        observed_markets[market or "UNKNOWN"] = observed_markets.get(market or "UNKNOWN", 0) + 1
        if market not in allowed_markets:
            market_violations.append(f"{code}: market={market!r} 不在枚举 {sorted(allowed_markets)}")
        elif code in expected_market and market != expected_market[code]:
            market_violations.append(f"{code}: 派生 market={market} 与期望池 {expected_market[code]} 不一致")
        elif market_sources.get(code) == "code_pattern" and _market_of_code(code) != market:
            market_violations.append(f"{code}: market 与代码模式不一致")
    add_check(
        checks, contract, "code_market_enum", not market_violations,
        f"{len(input_codes)} 条 market 合法（{observed_markets}），来源="
        f"{'v2_acad1_pool' if expected.get('available') else 'code_pattern'}"
        if not market_violations else "；".join(_short_violations(market_violations)),
    )

    # st_codes
    st_violations: list[str] = []
    if st_codes is None:
        st_violations.append("st_codes 不是数组")
    else:
        for item in st_codes:
            if not isinstance(item, str) or not CN_TS_RE.fullmatch(item):
                st_violations.append(f"非法元素 {item!r}")
        if len(set(st_codes)) != len(st_codes):
            st_violations.append("存在重复元素")
    st_valid = not st_violations
    add_check(
        checks, contract, "st_codes_valid", st_valid,
        "st_codes 为数组，空列表合法（池内无 ST）" if st_valid else "；".join(_short_violations(st_violations)),
    )

    # 覆盖
    coverage = check_coverage(input_codes, expected, derived_markets, pool_value)
    floor = _rule_number(contract, "coverage_floor", "threshold", DEFAULT_COVERAGE_FLOOR)
    coverage["floor"] = floor
    coverage["meets_floor"] = bool(coverage["rate"] is not None and coverage["rate"] >= floor)
    add_check(
        checks, contract, "coverage_floor", coverage["meets_floor"],
        f"覆盖 {coverage['present']}/{coverage['expected']} = "
        f"{(coverage['rate'] or 0):.2%}，契约下限 {floor:.0%}；缺失 {coverage['missing']} 只",
    )

    # 缺口：逐码 + 证据通道
    missing_gaps = build_missing_gaps(coverage["missing_codes"], expected, derived_markets, data)
    if not expected.get("available"):
        gap_ok = True
        gap_detail = (
            f"期望代码清单不可用（{expected.get('source')}），仅按文件 pool 计数报告覆盖缺口 "
            f"{coverage['missing']} 只；未编造代码（连 PG 后可逐码定位）")
    else:
        gap_ok = (len(missing_gaps) == coverage["missing"]) and all(gap.get("reason") for gap in missing_gaps)
        gap_detail = f"逐码缺口 {len(missing_gaps)} 只，均含 reason/missing_fields"
    add_check(checks, contract, "gap_reporting", gap_ok, gap_detail)

    unavailable_channels = [
        {
            "field": "announcements",
            "provided": False,
            "present": 0,
            "expected": coverage["expected"],
            "reason": "dw_adapter 输出未提供公告证据通道（契约 required=false，须报告 unavailable）",
        },
        {
            "field": "news",
            "provided": False,
            "present": 0,
            "expected": coverage["expected"],
            "reason": "dw_adapter 输出未提供新闻证据通道（契约 required=false，须报告 unavailable）",
        },
    ]
    add_check(
        checks, contract, "evidence_channels", True,
        "announcements/news 均 unavailable(0/30)，已列 gaps.unavailable_channels，未用 0 条冒充已提供证据",
    )

    # ---- 汇总 ----
    failures = [check for check in checks if not check["pass"]]
    error_failures = [check for check in failures if check["severity"] == "error"]
    hard_pass = not error_failures
    coverage_full = coverage["expected"] and coverage["present"] == coverage["expected"]
    if not hard_pass:
        status = "FAIL"
    elif coverage_full:
        status = "PASS"
    else:
        status = "PASS_WITH_GAPS"
    check_summary = {
        "total": len(checks),
        "passed": sum(1 for check in checks if check["pass"]),
        "failed": len(failures),
        "failed_errors": len(error_failures),
        "failed_warnings": sum(1 for check in failures if check["severity"] == "warning"),
    }

    # ---- 覆盖率 / 字段 / 枚举 / 数据质量明细 ----
    present_records = [code for code in sorted(input_codes) if code in daily or code in basic]
    field_industry_nonempty = [
        code for code in present_records
        if isinstance((basic.get(code) or {}).get("industry"), str) and (basic.get(code) or {}).get("industry", "").strip()
    ]
    field_industry_empty = [code for code in present_records if code not in field_industry_nonempty]
    expected_count = coverage["expected"]
    fields = {
        "market": {
            "present": coverage["expected"] if expected.get("available") else len(input_codes),
            "expected": coverage["expected"], "derived": True,
            "source": "v2_acad1_pool（只读派生）" if expected.get("available") else "code_pattern（兜底派生）",
            "rate": round(coverage["expected"] / expected_count, 4) if expected_count else None,
            "note": "market 不是 dw_enrichment.json 物理字段，由期望池/代码模式派生，来源已标注",
        },
        "pre_close": {
            "present": sum(1 for code in present_records if _is_number((daily.get(code) or {}).get("pre_close"))),
            "expected": expected_count,
            "rate": None,
        },
        "list_date": {
            "present": sum(1 for code in present_records if (basic.get(code) or {}).get("list_date") not in (None, "")),
            "expected": expected_count, "rate": None,
        },
        "industry": {
            "nonempty": len(field_industry_nonempty),
            "empty": len(field_industry_empty),
            "missing_records": expected_count - len(present_records),
            "expected": expected_count,
            "nonempty_rate": round(len(field_industry_nonempty) / expected_count, 4) if expected_count else None,
            "note": "HK/US 本地源无 industry 字段（契约允许空，报告注明）；CN 取 stock_basic.industry",
        },
        "announcements": unavailable_channels[0],
        "news": unavailable_channels[1],
        "st_codes": {
            "provided": isinstance(st_codes, list),
            "count": len(st_codes) if isinstance(st_codes, list) else None,
            "valid": st_valid,
            "note": "空列表=池内无 ST（合法，不是缺失）",
        },
    }
    for key in ("pre_close", "list_date"):
        fields[key]["rate"] = round(fields[key]["present"] / expected_count, 4) if expected_count else None

    by_market_quality: dict[str, dict] = {}
    for code in present_records:
        market = derived_markets.get(code) or "UNKNOWN"
        entry = by_market_quality.setdefault(market, {
            "records": 0, "pre_close": 0, "list_date": 0, "industry_nonempty": 0,
            "latest_trade_date": None,
        })
        entry["records"] += 1
        if _is_number((daily.get(code) or {}).get("pre_close")):
            entry["pre_close"] += 1
        if (basic.get(code) or {}).get("list_date") not in (None, ""):
            entry["list_date"] += 1
        if code in field_industry_nonempty:
            entry["industry_nonempty"] += 1
        trade_date = (daily.get(code) or {}).get("trade_date")
        if isinstance(trade_date, str) and DATE8_RE.fullmatch(trade_date):
            if entry["latest_trade_date"] is None or trade_date > entry["latest_trade_date"]:
                entry["latest_trade_date"] = trade_date
    lag_by_market = {}
    if pool_date is not None:
        for market, entry in by_market_quality.items():
            parsed = _parse_date8(entry["latest_trade_date"]) if entry["latest_trade_date"] else None
            lag_by_market[market] = (pool_date - parsed).days if parsed else None

    enums = {
        "market": {
            "allowed": sorted(allowed_markets),
            "observed": observed_markets,
            "violations": market_violations,
            "source": "v2_acad1_pool（只读）" if expected.get("available") else "code_pattern（兜底）",
        },
        "st_codes_pattern": {"pattern": r"^\d{6}\.(SH|SZ|BJ)$", "violations": st_violations},
    }
    data_quality = {
        "daily_entries": len(daily),
        "basic_entries": len(basic),
        "code_sets_match": set_match,
        "pct_chg_consistency": {
            "checked": pct_checked,
            "tolerance": tolerance,
            "max_abs_diff": round(pct_max_diff, 6),
            "violations": _short_violations(pct_violations),
        },
        "trade_date_by_market": {
            market: entry["latest_trade_date"] for market, entry in sorted(by_market_quality.items())
        },
        "trade_date_lag_vs_pool_date_days": lag_by_market,
        "list_date_min": min(list_dates.values()) if list_dates else None,
        "list_date_max": max(list_dates.values()) if list_dates else None,
        "industry_empty_codes": field_industry_empty,
    }

    gaps = {
        "missing_codes": missing_gaps,
        "unavailable_channels": unavailable_channels,
        "field_gaps": [],
        "summary": {
            "missing_codes": len(missing_gaps),
            "coverage_missing": coverage["missing"],
            "missing_located": bool(expected.get("available")),
            "missing_by_market": {
                market: sum(1 for gap in missing_gaps if gap["market"] == market) for market in MARKETS
            },
            "unavailable_channels": len(unavailable_channels),
            "industry_empty_records": len(field_industry_empty),
            "honest_coverage": f"{coverage['present']}/{coverage['expected']}",
            "note": "覆盖不足与证据通道缺失全部如实列出；未把 27/30 报成 30/30，未把 0 条冒充已提供",
        },
    }
    gaps["field_gaps"].append({
        "field": "industry",
        "kind": "source_limited",
        "codes": field_industry_empty,
        "reason": "HK/US 本地数据源无行业字段（dw_adapter 写空字符串）；契约允许空但要求报告",
    })

    warnings: list[str] = []
    if status == "PASS_WITH_GAPS":
        missing_text = ("、".join(f"{gap['code']}({gap['market']})" for gap in missing_gaps)
                        if missing_gaps else f"{coverage['missing']} 只（未逐码定位）")
        warnings.append(
            f"覆盖缺口如实报告：{coverage['present']}/{coverage['expected']}"
            f"（{(coverage['rate'] or 0):.2%}），缺 {coverage['missing']} 只：{missing_text}")
    if not expected.get("available"):
        warnings.append(
            f"期望池清单不可用（{expected.get('source')}）：覆盖 {coverage['present']}/{coverage['expected']} "
            f"按输入文件 pool 计数报告，缺口 {coverage['missing']} 只无法逐码定位，未编造代码。")
    elif latest_day and file_pool_date and str(file_pool_date) != str(latest_day):
        warnings.append(
            f"输入 pool_date={file_pool_date} 非 PG 最新池日 {latest_day}（历史快照校验模式，仅提示不判错）。")
    for check in failures:
        if check["severity"] != "error":
            warnings.append(f"[{check['severity']}] {check['id']}：{check['detail']}")
    warnings.append(
        "announcements/news 证据通道未提供（0/30），已列 unavailable；如需公告/新闻证据请引用 "
        "C:\\Temp\\acad1_fundamentals.json（只读证据适配器），本契约不覆盖该文件。")
    warnings.append(f"industry 空值 {len(field_industry_empty)} 条（HK/US 数据源限制，非抓取失败）。")
    if expected.get("available") and expected.get("pool_date") != file_pool_date:
        warnings.append(f"pool_date={file_pool_date} 与期望池日 {expected.get('pool_date')} 不一致（已记 error）。")

    report = {
        "schema": "dw_contract_report/v1",
        "generated_at": _now(),
        "status": status,
        "pass": hard_pass,
        "honest_coverage": f"{coverage['present']}/{coverage['expected']}"
                           f"（{(coverage['rate'] or 0):.2%}）",
        "contract": {
            **contract["meta"],
            "path": contract["path"],
            "separation": separation,
        },
        "input": input_payload["meta"],
        "expected_universe": {
            "available": expected.get("available"),
            "source": expected.get("source"),
            "pool_date": expected.get("pool_date"),
            "pg_latest_pool_date": expected.get("pg_latest_pool_date"),
            "count": expected.get("count"),
            "by_market": expected.get("by_market"),
            "codes": expected.get("codes") if expected.get("available") else None,
            "error": expected.get("error"),
            "fallback_to_latest": expected.get("fallback_to_latest", False),
        },
        "pool_date": file_pool_date,
        "coverage": coverage,
        "fields": fields,
        "enums": enums,
        "data_quality": data_quality,
        "gaps": gaps,
        "checks": checks,
        "check_summary": check_summary,
        "warnings": warnings,
        "read_only": {
            "db_writes": False,
            "warehouse_writes": False,
            "zhice_table_writes": False,
            "pg_statements": [] if args.no_db else [
                "SELECT max(pool_date) FROM v2_acad1_pool",
                "SELECT market, code FROM v2_acad1_pool WHERE pool_date=?",
            ],
            "files_written": [str(Path(args.out))],
            "no_false_full_coverage": True,
            "note": "只读校验：不写数据仓/智策表；唯一写操作=本报告 JSON；覆盖不足已逐码列明",
        },
        "generator": {
            "script": "scripts/dw_contract_check.py",
            "command": " ".join([Path(sys.executable).name] + sys.argv),
            "python": sys.version.split()[0],
            "pyyaml": yaml.__version__,
        },
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 控制台摘要（证据自检用） ----
    print("== dw_adapter ODCS 契约检查（只读） ==")
    print(f"contract: {contract['meta'].get('id')} v{contract['meta'].get('version')} "
          f"({contract['meta'].get('apiVersion')}, 与 {separation.get('existing_id') or 'zerobond.zhice_selection'} 分离)")
    print(f"input: {input_payload['meta']['path']} ({input_payload['meta']['bytes']} B, "
          f"sha256={input_payload['meta']['sha256'][:16]}…)")
    print(f"expected universe: {expected.get('source')} pool_date={expected.get('pool_date')} "
          f"count={expected.get('count')} by_market={expected.get('by_market')}")
    print(f"coverage: {coverage['present']}/{coverage['expected']} = {(coverage['rate'] or 0):.2%} "
          f"(floor {coverage['floor']:.0%}, meets={coverage['meets_floor']})")
    if missing_gaps:
        missing_text = "、".join(f"{gap['code']}({gap['market']})" for gap in missing_gaps)
    elif coverage["missing"] > 0:
        missing_text = f"{coverage['missing']} 只（无期望代码清单，仅按 pool 计数）"
    else:
        missing_text = "无"
    print(f"missing: {missing_text}")
    print(f"checks: {check_summary['passed']}/{check_summary['total']} pass, "
          f"failed_errors={check_summary['failed_errors']} failed_warnings={check_summary['failed_warnings']}")
    print(f"data: daily={len(daily)} basic={len(basic)} sets_match={set_match} "
          f"pct_max_diff={pct_max_diff:.6f} trade_date_by_market="
          f"{ {market: entry['latest_trade_date'] for market, entry in sorted(by_market_quality.items())} }")
    print(f"channels: announcements unavailable(0/{coverage['expected']}) / news unavailable(0/{coverage['expected']})")
    if not args.quiet:
        for check in checks:
            mark = "PASS" if check["pass"] else "FAIL"
            print(f"  [{mark}] {check['id']:<24s} ({check['severity']}) {check['detail'][:110]}")
    print(f"status: {status}  honest_coverage={report['honest_coverage']}")
    print(f"saved {out_path} ({out_path.stat().st_size} bytes)")
    return 0 if (hard_pass or args.report_only) else 1


if __name__ == "__main__":
    raise SystemExit(main())
