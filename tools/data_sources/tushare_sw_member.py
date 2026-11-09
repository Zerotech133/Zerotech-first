# -*- coding: utf-8 -*-
"""申万行业历史成分股（L1/L2/L3）拉取工具。

数据源：Tushare ``index_member``（按申万行业指数逐个拉取，含 in_date/out_date/is_new）。
本地落盘：``quant_backtest/data/tushare/sw_<level>_member/<index_code>.parquet``。

仅拉取行业成分历史，不写 catalog、不影响生产选股链路。
用法：
    python tools/data_sources/tushare_sw_member.py fetch --level L2
    python tools/data_sources/tushare_sw_member.py status --level L2
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = REPO_ROOT / "quant_backtest" / "data" / "tushare"
DEFAULT_CLASSIFY_DIR = DEFAULT_DATA_ROOT / "sw_classify_l1"

API = "https://api.tushare.pro"
API_NAME = "index_member"
FIELDS = ("index_code", "con_code", "in_date", "out_date", "is_new")
LEVELS = ("L1", "L2", "L3")
MIN_ROWS_PER_INDEX = 1


class TushareError(RuntimeError):
    pass


class TushareAuthError(TushareError):
    pass


class TusharePermissionError(TushareError):
    pass


class TushareNetworkError(TushareError):
    pass


class TushareSchemaError(TushareError):
    pass


def _load_env_local() -> dict[str, str]:
    env_path = REPO_ROOT / "quant_backtest" / ".env.local"
    out: dict[str, str] = {}
    if not env_path.is_file():
        return out
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_token(env: dict[str, str] | None = None) -> str:
    env = dict(os.environ if env is None else env)
    token = env.get("TUSHARE_TOKEN") or env.get("XT_DATA_KEY")
    if token:
        return token
    token = _load_env_local().get("XT_DATA_KEY", "")
    if token:
        return token
    for p in (REPO_ROOT / "config" / "tushare_token.txt", Path("C:/Temp/tushare_token.txt")):
        if p.is_file():
            t = p.read_text(encoding="utf-8").strip()
            if t:
                return t
    raise TushareAuthError("未找到 TUSHARE_TOKEN/XT_DATA_KEY")


def _post(token: str, params: dict, *, timeout: float = 30.0, retries: int = 2, sleep: float = 1.0, session=None) -> dict:
    import urllib.request

    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            body = {"api_name": API_NAME, "token": token, "params": params, "fields": ""}
            if session is not None:
                resp = session.post(API, json=body, timeout=timeout)
                payload = resp.json()
            else:
                req = urllib.request.Request(
                    API,
                    data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json"},
                )
                payload = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
            code = payload.get("code")
            if code in (0, "0", None):
                return payload
            msg = str(payload.get("msg") or "")
            if code in (-2002, 40203, 40204) or "权限" in msg:
                raise TusharePermissionError(f"index_member 无权限：{code} {msg}")
            if code in (-2001, 40101, 40102) or "token" in msg.lower():
                raise TushareAuthError(f"index_member 认证失败：{code} {msg}")
            if code in (-2005, 40202) and attempt < retries:
                time.sleep(sleep * (attempt + 1))
                continue
            raise TushareError(f"index_member 失败：{code} {msg}")
        except (TusharePermissionError, TushareAuthError):
            raise
        except json.JSONDecodeError as exc:
            last = exc
        except Exception as exc:  # noqa: BLE001 - 网络层统一重试
            last = exc
        if attempt < retries:
            time.sleep(sleep * (attempt + 1))
    raise TushareNetworkError(f"index_member 网络失败：{last}")


def normalize_member_rows(payload: dict, index_code: str) -> pd.DataFrame:
    data = payload.get("data") or {}
    fields = data.get("fields") or list(FIELDS)
    items = data.get("items") or []
    if not items:
        return pd.DataFrame(columns=list(FIELDS))
    df = pd.DataFrame(items, columns=fields)
    for col in FIELDS:
        if col not in df.columns:
            df[col] = None
    df = df[list(FIELDS)].copy()
    df["index_code"] = df["index_code"].fillna(index_code).astype(str)
    df["con_code"] = df["con_code"].astype(str)
    for col in ("in_date", "out_date"):
        df[col] = df[col].where(df[col].notna(), None)
        df[col] = df[col].astype(object).map(lambda v: None if v is None or pd.isna(v) else str(v).strip() or None)
    df["is_new"] = df["is_new"].fillna("Y").astype(str).str.upper().str[:1]
    df = df.drop_duplicates(subset=["index_code", "con_code", "in_date"], keep="last")
    df = df.sort_values(["con_code", "in_date"], kind="mergesort").reset_index(drop=True)
    return df


def load_classify_codes(classify_dir: Path | str = DEFAULT_CLASSIFY_DIR, level: str = "L2") -> list[str]:
    level = level.upper()
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    files = sorted(Path(classify_dir).glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"未找到 sw_classify parquet：{classify_dir}")
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    if "level" not in df.columns or "index_code" not in df.columns:
        raise TushareSchemaError("sw_classify 缺少 level/index_code 列")
    codes = df.loc[df["level"].astype(str).str.upper() == level, "index_code"].dropna().astype(str).unique().tolist()
    return sorted(codes)


def member_dir(level: str, data_root: Path | str = DEFAULT_DATA_ROOT) -> Path:
    return Path(data_root) / f"sw_{level.lower()}_member"


def fetch_one(index_code: str, token: str, *, session=None, timeout: float = 30.0, retries: int = 2, sleep: float = 1.0) -> pd.DataFrame:
    payload = _post(token, {"index_code": index_code}, timeout=timeout, retries=retries, sleep=sleep, session=session)
    df = normalize_member_rows(payload, index_code)
    if len(df) < MIN_ROWS_PER_INDEX:
        raise TushareSchemaError(f"{index_code} 返回 0 行成分")
    return df


def fetch_level(level: str = "L2", *, classify_dir: Path | str = DEFAULT_CLASSIFY_DIR,
                out_dir: Path | str | None = None, token: str | None = None,
                resume: bool = True, sleep: float = 0.25, session=None,
                allow_empty: bool = True) -> dict:
    level = level.upper()
    codes = load_classify_codes(classify_dir, level)
    dest = Path(out_dir) if out_dir else member_dir(level)
    dest.mkdir(parents=True, exist_ok=True)
    token = token or load_token()
    summary = {"level": level, "index_count": len(codes), "fetched": 0, "skipped": 0,
               "empty": [], "failed": [], "rows": 0}
    for i, code in enumerate(codes, 1):
        path = dest / f"{code}.parquet"
        if resume and path.is_file():
            try:
                n = len(pd.read_parquet(path))
                if n > 0:
                    summary["skipped"] += 1
                    summary["rows"] += n
                    continue
            except Exception:  # noqa: BLE001 - 损坏则重拉
                pass
        try:
            df = fetch_one(code, token, session=session, sleep=max(sleep, 0.5))
            tmp = path.with_suffix(".parquet.tmp")
            df.to_parquet(tmp, index=False)
            os.replace(tmp, path)
            summary["fetched"] += 1
            summary["rows"] += len(df)
            print(f"[{i}/{len(codes)}] {code} rows={len(df)}")
        except TushareSchemaError as exc:
            if allow_empty and "返回 0 行" in str(exc):
                summary["empty"].append({"index_code": code, "error": str(exc)})
                print(f"[{i}/{len(codes)}] {code} EMPTY（已停用/无成分，跳过）")
            else:
                summary["failed"].append({"index_code": code, "error": str(exc)})
                print(f"[{i}/{len(codes)}] {code} FAILED: {exc}")
        except Exception as exc:  # noqa: BLE001 - 记录并继续
            summary["failed"].append({"index_code": code, "error": str(exc)})
            print(f"[{i}/{len(codes)}] {code} FAILED: {exc}")
        time.sleep(max(0.0, sleep))
    return summary


def status(level: str = "L2", *, out_dir: Path | str | None = None) -> dict:
    dest = Path(out_dir) if out_dir else member_dir(level)
    files = sorted(dest.glob("*.parquet"))
    rows = 0
    for f in files:
        try:
            rows += len(pd.read_parquet(f))
        except Exception:  # noqa: BLE001
            pass
    return {"level": level.upper(), "dir": str(dest), "files": len(files), "rows": rows}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="申万行业历史成分股拉取")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("fetch", "status"):
        p = sub.add_parser(name)
        p.add_argument("--level", default="L2", choices=LEVELS)
        p.add_argument("--classify-dir", default=str(DEFAULT_CLASSIFY_DIR))
        p.add_argument("--out-dir", default=None)
        if name == "fetch":
            p.add_argument("--no-resume", action="store_true")
            p.add_argument("--sleep", type=float, default=0.25)
            p.add_argument("--strict-empty", action="store_true",
                           help="存在返回 0 行的指数时按失败退出（默认仅记录 empty）")
    args = parser.parse_args(argv)
    if args.cmd == "status":
        print(json.dumps(status(args.level, out_dir=args.out_dir), ensure_ascii=False, indent=2))
        return 0
    summary = fetch_level(args.level, classify_dir=args.classify_dir, out_dir=args.out_dir,
                          resume=not args.no_resume, sleep=args.sleep,
                          allow_empty=not args.strict_empty)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
