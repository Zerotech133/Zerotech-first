# -*- coding: utf-8 -*-
r"""TDX ts_rt_min 滚动 1 日增量（盘中/当日使用）。

远端 ts_rt_min 是降序快照（offset=0 为最新），不认 start_date 过滤。
本脚本每次从 offset=0 取最新页，只保留目标交易日、且 time 大于本地水位的
新行，写入 `data/tdx/ts_rt_min_roll/d_YYYYMMDD/part_HHMMSS.parquet`。

- 首次运行会拉取当日全量（约 120 万行 / 240 页）；
- 之后每 5 分钟只拉新增页（通常 1–3 页）；
- 只保留最近 2 个交易日目录，避免无限膨胀；
- 不覆盖旧文件，不参与 tdx_sync 夜批；供盘中/当日只读消费。
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

BASE = "http://rookie.xtcloud-tech.com:48088"
REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "quant_backtest" / "data" / "tdx" / "ts_rt_min_roll"
STATE = ROOT / "_state.json"
LOG = Path(r"C:\Temp\tdx_rtmin_roll.log")
TZ = ZoneInfo("Asia/Shanghai")
PAGE = 5000
SLEEP = 0.12
KEEP_DAYS = 2
LOCK = ROOT / ".lock"
LOCK_STALE_SECONDS = 900
def _api_key():
    """TDX 服务 API Key（2026-11-16 起服务端 auth_mode=enforce，无 Key 一律 401）。

    来源：环境变量 TDX_API_KEY > 向上找到的 .env.local（已 gitignore）。
    缺 Key 立刻抛错 —— 不让请求静默 401 后被当成「没有数据」
    （同 2026-11-16 抑制令判例：读不到 ≠ 没有）。
    """
    k = (os.environ.get("TDX_API_KEY") or "").strip()
    if k:
        return k
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        f = os.path.join(d, ".env.local")
        if os.path.isfile(f):
            try:
                with open(f, encoding="utf-8-sig") as fh:
                    for raw in fh:
                        raw = raw.strip()
                        if raw.startswith("TDX_API_KEY="):
                            v = raw.split("=", 1)[1].strip().strip('"').strip("'")
                            if v:
                                return v
            except OSError:
                pass
            # 不 break：本层没有该键就继续向上（就近优先）
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    raise RuntimeError(
        "未配置 TDX_API_KEY —— 2026-11-16 起 TDX 服务强制鉴权。"
        "请在环境变量或仓库根 .env.local 设置 TDX_API_KEY=<tdx_...>")


SESS = requests.Session()
SESS.trust_env = False
SESS.headers.update({"X-API-Key": _api_key()})


def log(msg: str) -> None:
    line = f"[{datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def get(params: dict, timeout: int = 90) -> dict:
    last = ""
    backoff = (2, 5, 10, 20, 30)
    for attempt in range(len(backoff) + 1):
        try:
            r = SESS.get(BASE + "/api/data/table/ts_rt_min", params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code} {r.text[:120]}"
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        if attempt < len(backoff):
            time.sleep(backoff[attempt])
    raise RuntimeError(f"ts_rt_min fetch failed after retries: {last}")


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"date": "", "latest_time": "", "rows": 0, "updated_at": ""}


def save_state(state: dict) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)


def acquire_lock() -> bool:
    ROOT.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        return True
    except FileExistsError:
        try:
            age = time.time() - LOCK.stat().st_mtime
            if age > LOCK_STALE_SECONDS:
                LOCK.unlink(missing_ok=True)
                return acquire_lock()
        except OSError:
            pass
        return False


def release_lock() -> None:
    try:
        LOCK.unlink(missing_ok=True)
    except OSError:
        pass


def cleanup(keep_date: str) -> None:
    if not ROOT.exists():
        return
    cutoff = datetime.strptime(keep_date, "%Y%m%d").date() - timedelta(days=KEEP_DAYS - 1)
    for p in ROOT.glob("d_*"):
        try:
            d = datetime.strptime(p.name[2:], "%Y%m%d").date()
        except ValueError:
            continue
        if d < cutoff:
            shutil.rmtree(p, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="目标交易日 YYYYMMDD；默认取远端最新交易日")
    ap.add_argument("--full", action="store_true", help="忽略本地水位，重拉指定日全量")
    ap.add_argument("--max-pages", type=int, default=400)
    ap.add_argument("--force", action="store_true", help="非交易时段也跑")
    args = ap.parse_args()

    if not acquire_lock():
        log("another tdx_rtmin_roll is running, skip")
        return 0
    atexit.register(release_lock)
    ROOT.mkdir(parents=True, exist_ok=True)
    state = load_state()
    first = get({"limit": PAGE, "offset": 0})
    rows = first.get("rows") or []
    cols = first.get("columns") or []
    if not rows:
        log("remote returned no rows")
        return 0

    # 远端 offset=0 是最新一分钟；日期以远端 time 为准（避免周末/盘前空跑）
    remote_latest = str(rows[0][cols.index("time")])
    target = args.date or remote_latest[:10].replace("-", "")
    target_iso = f"{target[:4]}-{target[4:6]}-{target[6:8]}"
    if not args.force:
        now = datetime.now(TZ)
        if now.strftime("%Y%m%d") != target and not args.full:
            log(f"target={target} not today={now:%Y%m%d}, skip (use --force)")
            return 0

    if state.get("date") == target and not state.get("complete", True) and not args.full:
        log("previous run incomplete, restarting full")
        args.full = True
    if state.get("date") != target or args.full:
        state = {"date": target, "latest_time": "", "rows": 0, "complete": False, "updated_at": ""}
    resume_latest = "" if args.full else str(state.get("latest_time") or "")
    latest_time = resume_latest
    out_dir = ROOT / f"d_{target}"
    if args.full and out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    buf: list[dict] = []
    offset = 0
    new_rows = 0
    pages = 0
    batch_latest = ""

    def flush() -> None:
        nonlocal buf, new_rows, latest_time
        if not buf:
            return
        df = pd.DataFrame(buf, columns=cols)
        df = df.drop_duplicates(subset=["ts_code", "time"], keep="last")
        stamp = datetime.now(TZ).strftime("%H%M%S")
        part = out_dir / f"part_{stamp}.parquet"
        tmp = part.with_suffix(".parquet.tmp")
        df.to_parquet(tmp, index=False)
        os.replace(tmp, part)
        new_rows += len(df)
        if batch_latest > latest_time:
            latest_time = batch_latest
        state["latest_time"] = latest_time
        state["rows"] = int(state.get("rows") or 0) + len(df)
        state["updated_at"] = datetime.now(TZ).isoformat(timespec="seconds")
        save_state(state)
        log(f"wrote {part.name} rows={len(df)} total_today={state['rows']}")
        buf = []

    while pages < args.max_pages:
        d = get({"limit": PAGE, "offset": offset})
        page = d.get("rows") or []
        if not page:
            break
        page_cols = d.get("columns") or cols
        if not cols:
            cols = page_cols
        mapped = [dict(zip(cols, r)) for r in page]
        page_dates = [str(x.get("time", ""))[:10] for x in mapped]
        keep = []
        for x, ds in zip(mapped, page_dates):
            if ds != target_iso:
                continue
            t = str(x.get("time", ""))
            if resume_latest and t <= resume_latest:
                continue
            keep.append(x)
            if t > batch_latest:
                batch_latest = t
        buf.extend(keep)
        if len(buf) >= 100_000:
            flush()
        pages += 1
        if any(ds and ds < target_iso for ds in page_dates):
            break
        if page and resume_latest and str(page[-1][cols.index("time")]) <= resume_latest:
            break
        offset += PAGE
        time.sleep(SLEEP)

    flush()
    if batch_latest > latest_time:
        latest_time = batch_latest
    state["latest_time"] = latest_time
    state["complete"] = True
    state["updated_at"] = datetime.now(TZ).isoformat(timespec="seconds")
    save_state(state)
    if new_rows:
        log(f"done date={target} new_rows={new_rows} total={state['rows']} latest_time={latest_time}")
    else:
        log(f"no new rows date={target} latest_time={latest_time}")
    cleanup(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
