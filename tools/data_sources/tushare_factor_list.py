# -*- coding: utf-8 -*-
"""Tushare `factor_list` 因子清单接入器（只读接口，一次拉全量）。

背景
----
Tushare Pro `factor_list` 返回自主研发因子列表：因子名 / 资产类别 / 因子分类 /
因子描述与算法逻辑。当前只提供 STK（A 股），约 272 个因子、9 个类别；
接口需单独开通「因子库」权限（个人 2000 元/年，机构 2 万元/年）。

本模块只做「清单接入 + 快照 + 离线校验」，不计算因子值、不写 Tushare 时序仓：
  - 拉取：HTTPS POST `https://api.tushare.pro`，api_name=factor_list；
  - 标准化：字段校验、factor_name 去重、按 (factor_type, factor_name) 排序；
  - 落盘：`quant_backtest/data/factor_library/tushare_factor_list/`
      snapshots/factor_list_YYYYMMDD.{json,parquet}
      latest.json / latest.parquet
      manifest.json（行数、分类计数、sha256、token sha8，不落明文 token）
  - 校验：`check` 离线读 latest，校验结构/重复/空描述，供门禁或计划任务调用。

为什么放在 `data/factor_library/` 而不是 `data/tushare/`：
  `quant_backtest/build_catalog.py` 会把 `data/tushare/<table>/` 扫成时序表并要求
  日期列/新鲜度；因子清单是**静态元数据**，放进去会污染 catalog 表数与 freshness
  口径。独立目录不进入现有 catalog 扫描，后续如要登记资产可显式加 catalog 条目。

用法
----
    python tools/data_sources/tushare_factor_list.py fetch
    python tools/data_sources/tushare_factor_list.py fetch --factor-type Risk --json
    python tools/data_sources/tushare_factor_list.py fetch --dry-run
    python tools/data_sources/tushare_factor_list.py check
    python tools/data_sources/tushare_factor_list.py status
    python tools/data_sources/tushare_factor_list.py show --limit 10

Token 解析优先级（不打印明文）：
  1) 环境变量 TUSHARE_TOKEN / XT_DATA_KEY
  2) quant_backtest/.env.local 的 XT_DATA_KEY=
  3) config/tushare_token.txt（整文件即 token）
  4) C:/Temp/tushare_token.txt（整文件即 token）

退出码：
  0=成功 / 2=无「因子库」权限 / 3=token 无效或过期 / 4=接口或 schema 错误 /
  5=网络错误 / 6=未找到 token / 7=本地快照缺失或校验失败
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT_DIR = ROOT / "quant_backtest" / "data" / "factor_library" / "tushare_factor_list"
API = "https://api.tushare.pro"
API_NAME = "factor_list"
FIELDS = ("factor_name", "asset_type", "factor_type", "factor_desc")
EXPECTED_TYPES = (
    "Alpha101", "Growth", "Liquidity", "Momentum", "Quality",
    "Reversal", "Risk", "Size", "Value",
)
SCHEMA_VERSION = 1
MIN_ROWS = 200  # 2026-11 实测 272；低于此值视为接口异常/权限降级

# Tushare 错误码语义（与 tools/tushare_probe.py 同口径）
CODE_MEANING = {
    0: "OK",
    2002: "参数错误",
    40001: "接口不存在或字段错误",
    40002: "请求过于频繁",
    40101: "token 无效",
    40102: "token 过期",
    40203: "无该接口访问权限（需开通因子库）",
    40204: "积分不足或权限已到期",
}


class TushareError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class TushareAuthError(TushareError):
    """40101/40102：token 无效或过期。"""


class TusharePermissionError(TushareError):
    """40203/40204：无因子库权限/积分不足。"""


class TushareNetworkError(TushareError):
    """网络/HTTP 失败。"""


class TushareSchemaError(TushareError):
    """接口返回结构不符合预期。"""


def _sha8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _read_env_value(path: Path, key: str) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def load_token(env: dict[str, str] | None = None) -> str:
    """按项目约定解析 token；找不到返回空串。绝不打印 token。"""
    env = dict(os.environ if env is None else env)
    for key in ("TUSHARE_TOKEN", "XT_DATA_KEY"):
        value = (env.get(key) or "").strip()
        if value:
            return value

    env_local = ROOT / "quant_backtest" / ".env.local"
    value = _read_env_value(env_local, "XT_DATA_KEY") or _read_env_value(env_local, "TUSHARE_TOKEN")
    if value:
        return value

    for path in (ROOT / "config" / "tushare_token.txt", Path(r"C:\Temp\tushare_token.txt")):
        try:
            value = path.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            continue
        if value:
            return value.splitlines()[0].strip()
    return ""


def _raise_for_code(code: int | None, msg: str) -> None:
    meaning = CODE_MEANING.get(int(code) if code is not None else -1, "")
    detail = f"{msg}" if msg else meaning
    if code in (40101, 40102):
        raise TushareAuthError(f"Tushare 认证失败（code={code}）：{detail}", code)
    if code in (40203, 40204):
        raise TusharePermissionError(
            f"Tushare 无『因子库』权限（code={code}）：{detail}。"
            "需在 Tushare 权限中心勾选「因子库」开通（个人 2000 元/年，机构 2 万元/年）。",
            code,
        )
    raise TushareError(f"Tushare 接口失败（code={code}）：{detail}", code)


def fetch_factor_list(
    token: str,
    *,
    params: dict[str, str] | None = None,
    fields: str = "",
    timeout: float = 30,
    retries: int = 2,
    sleep: float = 1.0,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """调用 factor_list，返回 {fields, items, code, msg, elapsed_seconds, params}。

    单次调用即可拉全量（接口不需要翻页）；限频错误自动退避重试。
    """
    if not token:
        raise TushareAuthError("未找到 Tushare token", None)
    sess = session or requests.Session()
    payload = {
        "api_name": API_NAME,
        "token": token,
        "params": dict(params or {}),
        "fields": fields or "",
    }
    started = time.monotonic()
    last_error = ""
    for attempt in range(retries + 1):
        try:
            resp = sess.post(API, json=payload, timeout=timeout)
        except requests.RequestException as exc:  # 网络层错误：重试
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(sleep * (2 ** attempt))
                continue
            raise TushareNetworkError(f"Tushare 网络错误：{last_error}") from exc
        if resp.status_code != 200:
            last_error = f"HTTP {resp.status_code}"
            if attempt < retries:
                time.sleep(sleep * (2 ** attempt))
                continue
            raise TushareNetworkError(f"Tushare HTTP 错误：{last_error}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise TushareSchemaError("Tushare 返回不是合法 JSON") from exc
        if not isinstance(body, dict):
            raise TushareSchemaError("Tushare 返回顶层不是 JSON 对象")
        code = body.get("code")
        msg = str(body.get("msg") or "")
        if code == 0:
            data = body.get("data")
            if not isinstance(data, dict):
                raise TushareSchemaError("Tushare data 字段缺失或不是对象")
            return {
                "code": 0,
                "msg": msg,
                "fields": data.get("fields") or [],
                "items": data.get("items") or [],
                "params": payload["params"],
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
        if code in (40002,) or "频率" in msg or "每分钟" in msg or "次/小时" in msg:
            last_error = f"code={code} {msg}"
            if attempt < retries:
                time.sleep(sleep * (2 ** attempt))
                continue
        _raise_for_code(code, msg)
    raise TushareError(f"Tushare 调用失败：{last_error}")


def normalize_factor_rows(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """校验 fields/items 并标准化；返回 (rows, warnings)。重复 factor_name 保留首条。

    兼容两种入参：Tushare 原始 body（含 data 子对象）或 fetch_factor_list 返回的
    扁平结构（直接含 fields/items）。
    """
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    fields = data.get("fields")
    items = data.get("items")
    if not isinstance(fields, list) or not fields:
        raise TushareSchemaError("factor_list 返回 fields 为空")
    if not isinstance(items, list):
        raise TushareSchemaError("factor_list 返回 items 不是列表")
    missing = [name for name in FIELDS if name not in fields]
    if missing:
        raise TushareSchemaError(f"factor_list 缺少必需字段：{missing}；实际 fields={fields}")

    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    dup = 0
    for idx, item in enumerate(items):
        if not isinstance(item, (list, tuple)) or len(item) != len(fields):
            raise TushareSchemaError(f"factor_list 第 {idx} 行与 fields 长度不一致")
        row = dict(zip(fields, item))
        name = str(row.get("factor_name") or "").strip()
        if not name:
            warnings.append(f"第 {idx} 行 factor_name 为空，已跳过")
            continue
        if name in seen:
            dup += 1
            continue
        seen.add(name)
        row["factor_name"] = name
        for key in ("asset_type", "factor_type", "factor_desc"):
            value = row.get(key)
            row[key] = "" if value is None else str(value).strip()
        rows.append(row)
    if dup:
        warnings.append(f"factor_name 重复 {dup} 行，已按首条去重")
    rows.sort(key=lambda r: (str(r.get("factor_type") or ""), str(r.get("factor_name") or "")))
    return rows, warnings


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    by_asset: dict[str, int] = {}
    for row in rows:
        ftype = str(row.get("factor_type") or "UNKNOWN")
        asset = str(row.get("asset_type") or "UNKNOWN")
        by_type[ftype] = by_type.get(ftype, 0) + 1
        by_asset[asset] = by_asset.get(asset, 0) + 1
    return {
        "total": len(rows),
        "unique_factor_name": len({str(r.get("factor_name")) for r in rows}),
        "by_type": dict(sorted(by_type.items())),
        "by_asset": dict(sorted(by_asset.items())),
    }


def validate_rows(
    rows: list[dict[str, Any]],
    *,
    expected_types: tuple[str, ...] = EXPECTED_TYPES,
    min_rows: int = MIN_ROWS,
) -> dict[str, Any]:
    """离线结构校验：返回 {errors, warnings, stats}，不抛异常。"""
    errors: list[str] = []
    warnings: list[str] = []
    stats = summarize_rows(rows) if isinstance(rows, list) else {
        "total": 0, "unique_factor_name": 0, "by_type": {}, "by_asset": {},
    }
    if not isinstance(rows, list) or not rows:
        errors.append("因子清单为空")
        return {"errors": errors, "warnings": warnings, "stats": stats}
    if stats["total"] < min_rows:
        errors.append(f"因子数量 {stats['total']} 低于下限 {min_rows}")
    names: set[str] = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"第 {idx} 行不是对象")
            continue
        name = str(row.get("factor_name") or "").strip()
        if not name:
            errors.append(f"第 {idx} 行 factor_name 为空")
        elif name in names:
            errors.append(f"factor_name 重复：{name}")
        else:
            names.add(name)
        if not str(row.get("factor_desc") or "").strip():
            warnings.append(f"{name or idx} 的 factor_desc 为空")
        ftype = str(row.get("factor_type") or "").strip()
        if not ftype:
            errors.append(f"{name or idx} 的 factor_type 为空")
        elif expected_types and ftype not in expected_types:
            warnings.append(f"{name or idx} 出现新分类 {ftype!r}（不在已知 9 类内）")
        if not str(row.get("asset_type") or "").strip():
            errors.append(f"{name or idx} 的 asset_type 为空")
    absent = [ftype for ftype in expected_types if ftype not in stats["by_type"]]
    if absent:
        warnings.append(f"已知分类缺失：{absent}")
    return {"errors": errors, "warnings": warnings, "stats": stats}


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def save_snapshot(
    rows: list[dict[str, Any]],
    meta: dict[str, Any],
    out_dir: Path | str = OUT_DIR,
    *,
    now: datetime | None = None,
    update_latest: bool = True,
) -> dict[str, Any]:
    """写当日快照；update_latest=False 时不覆盖 latest/manifest（筛选查询用）。

    返回路径与 sha256。
    """
    out = Path(out_dir)
    snap_dir = out / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    day = (now or datetime.now()).strftime("%Y%m%d")
    suffix = ""
    if not update_latest:
        params = meta.get("api_params") or {}
        if params:
            digest = hashlib.sha256(json.dumps(params, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:6]
            suffix = f"_{digest}"
    base = f"factor_list_{day}{suffix}"
    payload = dict(meta)
    payload.update(summarize_rows(rows))
    payload["rows"] = rows
    payload["schema_version"] = SCHEMA_VERSION
    raw = json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()

    snap_json = snap_dir / f"{base}.json"
    snap_parquet = snap_dir / f"{base}.parquet"
    _atomic_write_bytes(snap_json, raw + b"\n")
    df = pd.DataFrame(rows, columns=list(FIELDS))
    tmp_parquet = snap_parquet.with_suffix(".parquet.tmp")
    df.to_parquet(tmp_parquet, index=False)
    os.replace(tmp_parquet, snap_parquet)

    manifest = dict(meta)
    manifest.update(summarize_rows(rows))
    manifest.update({
        "schema_version": SCHEMA_VERSION,
        "fetched_at": payload.get("fetched_at") or (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
        "snapshot_json": str(snap_json.relative_to(out)),
        "snapshot_parquet": str(snap_parquet.relative_to(out)),
        "latest_json": "latest.json" if update_latest else None,
        "latest_parquet": "latest.parquet" if update_latest else None,
        "sha256": sha,
        "bytes": len(raw) + 1,
        "rows": len(rows),
    })
    if update_latest:
        shutil.copyfile(snap_json, out / "latest.json")
        shutil.copyfile(snap_parquet, out / "latest.parquet")
        manifest_path = out / "manifest.json"
    else:
        manifest_path = snap_dir / f"{base}.manifest.json"
    _atomic_write_bytes(manifest_path, (json.dumps(manifest, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    return {
        "snapshot_json": snap_json,
        "snapshot_parquet": snap_parquet,
        "latest_json": out / "latest.json",
        "manifest_json": manifest_path,
        "sha256": sha,
        "rows": len(rows),
    }


def load_latest(out_dir: Path | str = OUT_DIR) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    out = Path(out_dir)
    path = out / "latest.json"
    if not path.exists():
        raise FileNotFoundError(f"因子清单快照不存在：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise TushareSchemaError(f"latest.json 解析失败：{exc}") from exc
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise TushareSchemaError("latest.json 缺少 rows 列表")
    manifest: dict[str, Any] = {}
    manifest_path = out / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError:
            manifest = {}
    return rows, manifest


# ---------------------------------------------------------------- CLI
def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _default_out(value: str | None) -> Path:
    return Path(value) if value else OUT_DIR


def _cmd_fetch(args: argparse.Namespace) -> int:
    token = load_token()
    if not token:
        print("[factor_list] 未找到 token：设置 TUSHARE_TOKEN，或检查 quant_backtest/.env.local 的 XT_DATA_KEY")
        return 6
    params: dict[str, str] = {}
    if args.asset_type:
        params["asset_type"] = args.asset_type
    if args.factor_type:
        params["factor_type"] = args.factor_type
    if args.factor_name:
        params["factor_name"] = args.factor_name
    try:
        data = fetch_factor_list(token, params=params, fields=args.fields,
                                 timeout=args.timeout, retries=args.retries,
                                 sleep=0 if args.dry_run else 1.0)
        rows, warnings = normalize_factor_rows(data)
        # 带筛选条件时返回的是子集，不能用全量下限/全分类校验，也不能覆盖 latest。
        check = validate_rows(
            rows,
            expected_types=(() if params else EXPECTED_TYPES),
            min_rows=(1 if params else MIN_ROWS),
        )
    except TushareAuthError as exc:
        print(f"[factor_list] {exc}")
        return 3
    except TusharePermissionError as exc:
        print(f"[factor_list] {exc}")
        return 2
    except TushareSchemaError as exc:
        print(f"[factor_list] schema 错误：{exc}")
        return 4
    except TushareNetworkError as exc:
        print(f"[factor_list] 网络错误：{exc}")
        return 5
    except TushareError as exc:
        print(f"[factor_list] 接口错误：{exc}")
        return 4

    meta = {
        "api": API_NAME,
        "source": API,
        "asset_type": args.asset_type or "STK",
        "factor_type": args.factor_type or "",
        "factor_name": args.factor_name or "",
        "fields": data.get("fields") or [],
        "api_params": data.get("params") or {},
        "token_sha8": _sha8(token),
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_seconds": data.get("elapsed_seconds"),
        "note": "Tushare factor_list 静态元数据快照；不参与 catalog 时序扫描。",
        "warnings": list(warnings) + list(check["warnings"]),
        "errors": list(check["errors"]),
    }
    summary = {
        "ok": not check["errors"],
        **summarize_rows(rows),
        "warnings": check["warnings"],
        "errors": check["errors"],
        "token_sha8": meta["token_sha8"],
        "dry_run": bool(args.dry_run),
    }
    if args.dry_run:
        if args.json:
            _print_json(summary)
        else:
            print(f"[factor_list] dry-run rows={summary['total']} by_type={summary['by_type']}")
        return 0 if not check["errors"] else 4
    paths = save_snapshot(rows, meta, _default_out(args.out_dir), update_latest=not params)
    summary.update({
        "snapshot_json": str(paths["snapshot_json"]),
        "manifest_json": str(paths["manifest_json"]),
        "sha256": paths["sha256"],
        "latest_updated": not params,
    })
    if args.json:
        _print_json(summary)
    else:
        print(f"[factor_list] rows={summary['total']} -> {paths['snapshot_json']}")
        print(f"[factor_list] by_type={summary['by_type']}")
        print(f"[factor_list] sha256={paths['sha256']} token_sha8={meta['token_sha8']}")
        if params:
            print("[factor_list] 筛选查询：仅写 snapshots，不覆盖 latest/manifest")
        for warning in summary["warnings"]:
            print(f"[WARN] {warning}")
    return 0 if not check["errors"] else 4


def _cmd_check(args: argparse.Namespace) -> int:
    out = _default_out(args.out_dir)
    try:
        rows, manifest = load_latest(out)
    except (FileNotFoundError, TushareSchemaError) as exc:
        print(f"[factor_list] {exc}")
        return 7
    result = validate_rows(rows, min_rows=args.min_rows)
    result["manifest"] = {
        key: manifest.get(key)
        for key in ("fetched_at", "total", "by_type", "sha256", "token_sha8", "snapshot_json")
    }
    if args.json:
        _print_json(result)
    else:
        print(f"[factor_list] check rows={result['stats']['total']} by_type={result['stats']['by_type']}")
        for error in result["errors"]:
            print(f"[FAIL] {error}")
        for warning in result["warnings"]:
            print(f"[WARN] {warning}")
    return 0 if not result["errors"] else 7


def _cmd_status(args: argparse.Namespace) -> int:
    out = _default_out(args.out_dir)
    manifest_path = out / "manifest.json"
    if not manifest_path.exists():
        print(f"[factor_list] 尚无快照：{manifest_path}")
        return 7
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"[factor_list] manifest 解析失败：{exc}")
        return 7
    if args.json:
        _print_json(manifest)
    else:
        print(f"[factor_list] fetched_at={manifest.get('fetched_at')} rows={manifest.get('total')} "
              f"by_type={manifest.get('by_type')} sha256={manifest.get('sha256')}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    out = _default_out(args.out_dir)
    try:
        rows, manifest = load_latest(out)
    except (FileNotFoundError, TushareSchemaError) as exc:
        print(f"[factor_list] {exc}")
        return 7
    stats = summarize_rows(rows)
    if args.json:
        _print_json({"stats": stats, "manifest": manifest, "sample": rows[: args.limit]})
        return 0
    print(f"# factor_list {stats['total']} 个 / {len(stats['by_type'])} 类 / 资产={stats['by_asset']}")
    for ftype, count in stats["by_type"].items():
        print(f"{ftype:12s} {count:4d}")
    print(f"\n字段: {', '.join(FIELDS)}")
    for row in rows[: args.limit]:
        print(f"- {row.get('factor_name')} [{row.get('factor_type')}] {str(row.get('factor_desc'))[:80]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tushare factor_list 因子清单接入（拉取/快照/校验）")
    sub = parser.add_subparsers(dest="cmd")

    fetch = sub.add_parser("fetch", help="从 Tushare 拉取因子清单并写快照")
    fetch.add_argument("--out-dir", default=None)
    fetch.add_argument("--asset-type", default=None, help="资产类别，当前仅 STK")
    fetch.add_argument("--factor-type", default=None, help="仅拉某分类（如 Risk）")
    fetch.add_argument("--factor-name", default=None, help="仅拉某因子")
    fetch.add_argument("--fields", default="", help="自定义输出字段，默认全部")
    fetch.add_argument("--timeout", type=float, default=30)
    fetch.add_argument("--retries", type=int, default=2)
    fetch.add_argument("--dry-run", action="store_true", help="只拉取并打印统计，不写盘")
    fetch.add_argument("--json", action="store_true")
    fetch.set_defaults(func=_cmd_fetch)

    check = sub.add_parser("check", help="离线校验 latest 快照")
    check.add_argument("--out-dir", default=None)
    check.add_argument("--min-rows", type=int, default=MIN_ROWS)
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=_cmd_check)

    status = sub.add_parser("status", help="读 manifest 状态")
    status.add_argument("--out-dir", default=None)
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=_cmd_status)

    show = sub.add_parser("show", help="展示分类统计与前若干条")
    show.add_argument("--out-dir", default=None)
    show.add_argument("--limit", type=int, default=5)
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=_cmd_show)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"fetch", "check", "status", "show"}
    if argv and argv[0] in commands:
        pass
    elif not argv or argv[0] not in ("-h", "--help"):
        argv = ["fetch"] + argv
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
