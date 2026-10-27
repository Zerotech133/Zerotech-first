# -*- coding: utf-8 -*-
"""情绪影子日采器（sentiment-shadow 2026-10-14：regime 第二传感器落地）。

出处：akshare 探测（tools/probe_akshare_sentiment.py，三接口全可用）+
开源借鉴 #4。职责：每日收盘后一次，采 A 股涨停/炸板情绪三指标落影子档，
**只记录不改任何策略行为**（与 gate_shadow_collect 同定位）：

  zt_count     涨停家数（stock_zt_pool_em）
  zb_count     炸板家数（stock_zt_pool_zbgc_em）
  zha_ban_rate 炸板率 = zb / (zt + zb)（与探测报告口径一致，
               20261013=22.09% 对账通过）
  max_lb       连板高度 = max(涨停池.连板数)
  lb_dist      连板分布 {"2板":n,...}（≥2 板才计）

落档 data/sentiment_shadow.jsonl（幂等键 date；O_APPEND 单次 write+fsync
+锁，判例 gate_shadow_collect）。两池当日均为空 = 非交易日/接口异常，
不落行（failed 时落 status=error 行便于发现断流，成功空日跳过）。

⚠️ 依赖 akshare（受管 venv）：必须用
  C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe
运行；系统 python311 无 akshare。

用法:
  ... --backfill 20260401 20261014   # 历史回补（逐日探测，空日跳过）
  ...                                # 默认采当日
"""

import argparse
import io
import json
import os
import sys
import time
from contextlib import redirect_stdout

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SHADOW = os.path.join(HERE, "data", "sentiment_shadow.jsonl")
LOCK = SHADOW + ".lock"
STALE_LOCK_SEC = 120
SERIES_FP = os.path.join(HERE, "reports", "qd_breadth_series.json")


def _log(msg):
    try:
        with open("C:/Temp/sentiment_collect.log", "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _no_proxy():
    """akshare 走 requests，直连保险：清代理环境变量（判例 09-26 502）。"""
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        os.environ.pop(k, None)
    os.environ.setdefault("NO_PROXY", "*")


def fetch_day(ymd):
    """采一天 → dict（date=YYYYMMDD）。两池全空返回 None（非交易日）。"""
    import akshare as ak

    with redirect_stdout(io.StringIO()):  # akshare 啰嗦进度打印
        zt = ak.stock_zt_pool_em(date=ymd)
        try:
            zb = ak.stock_zt_pool_zbgc_em(date=ymd)
        except Exception:
            # 炸板池只保留最近 30 个交易日（akshare 1.19.1 实测抛
            # ValueError）：超窗历史降级为仅涨停口径，zb 字段记 None
            zb = None
    zt = zt if isinstance(zt, pd.DataFrame) else pd.DataFrame()
    zb_missing = zb is None  # 超窗降级标记（None 不转空表）
    zb = zb if isinstance(zb, pd.DataFrame) else pd.DataFrame()
    if zt.empty and (zb_missing or zb.empty):
        return None
    zt_n = len(zt)
    zb_n = None if zb_missing else len(zb)
    lb_col = next((c for c in zt.columns if "连板" in str(c)), None)
    max_lb, lb_dist = 0, {}
    if lb_col is not None and not zt.empty:
        lbs = pd.to_numeric(zt[lb_col], errors="coerce").dropna().astype(int)
        if len(lbs):
            max_lb = int(lbs.max())
            lb_dist = {
                "%d板" % k: int(v)
                for k, v in lbs[lbs >= 2].value_counts().sort_index().items()
            }
    rate = (
        None
        if zb_n is None
        else (round(zb_n / (zt_n + zb_n), 4) if (zt_n + zb_n) else None)
    )
    return {
        "date": str(ymd),
        "zt_count": zt_n,
        "zb_count": zb_n,
        "zha_ban_rate": rate,
        "max_lb": max_lb,
        "lb_dist": lb_dist,
        "status": "ok",
    }


def _acquire_lock(timeout=30):
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - os.stat(LOCK).st_mtime > STALE_LOCK_SEC:
                    os.remove(LOCK)
                    continue
            except OSError:
                pass
            if time.time() >= deadline:
                return False
            time.sleep(0.2)


def _release_lock():
    try:
        os.remove(LOCK)
    except OSError:
        pass


def _load_dates(path=SHADOW):
    seen = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    seen.add(str(json.loads(line).get("date")))
                except Exception:
                    continue
    return seen


def append_rows(rows, path=SHADOW):
    """幂等原子追加（锁内复核）。返回实写行数。"""
    rows = [r for r in rows if r]
    if not rows:
        return 0
    seen = _load_dates(path)
    rows = [r for r in rows if str(r.get("date")) not in seen]
    if not rows:
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not _acquire_lock():
        _log("append: lock timeout, skip %d" % len(rows))
        return 0
    try:
        fresh = _load_dates(path)
        rows = [r for r in rows if str(r.get("date")) not in fresh]
        if not rows:
            return 0
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
            os.write(fd, payload.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        return len(rows)
    finally:
        _release_lock()


def collect_today(path=SHADOW):
    ymd = time.strftime("%Y%m%d")
    try:
        row = fetch_day(ymd)
        if row is None:
            _log("today %s empty (非交易日或未收盘)" % ymd)
            return 0
        n = append_rows([row], path)
        _log(
            "today +%d %s zt=%s zb=%s rate=%s maxlb=%s"
            % (
                n,
                ymd,
                row["zt_count"],
                row["zb_count"],
                row["zha_ban_rate"],
                row["max_lb"],
            )
        )
        return n
    except Exception as e:
        _log("today fail %s: %s" % (ymd, str(e)[:160]))
        append_rows([{"date": ymd, "status": "error", "err": str(e)[:160]}], path)
        return 0


def backfill(d0, d1, path=SHADOW):
    """区间逐日回补（空日跳过不落行）。返回 (实写, 跳过空日)。"""
    seen = _load_dates(path)
    days = [
        d.strftime("%Y%m%d")
        for d in pd.bdate_range(pd.to_datetime(d0), pd.to_datetime(d1))
    ]
    rows, skipped = [], 0
    for d in days:
        if d in seen:
            continue
        try:
            row = fetch_day(d)
        except Exception as e:
            _log("backfill %s fail: %s" % (d, str(e)[:120]))
            row = None
        if row is None:
            skipped += 1
            continue
        rows.append(row)
        time.sleep(0.35)  # 温和限速
    n = append_rows(rows, path)
    _log("backfill %s~%s +%d skip=%d" % (d0, d1, n, skipped))
    return n, skipped


def corr_with_breadth(path=SHADOW, series_fp=SERIES_FP, min_n=20):
    """影子 × 湖口径宽度 相关初析（只读，min_n 不足则返回提示）。"""
    try:
        rows = [
            r
            for r in (json.loads(l) for l in open(path, encoding="utf-8"))
            if r.get("status") == "ok"
        ]
        with open(series_fp, encoding="utf-8") as f:
            s = json.load(f)
        br = {
            str(d): float(b) for d, b in zip(s["dates"], s["breadth"]) if b is not None
        }
        pairs = [
            (r["zha_ban_rate"], br[r["date"]])
            for r in rows
            if r.get("zha_ban_rate") is not None and r["date"] in br
        ]
        pairs_lb = [
            (r["max_lb"], br[r["date"]])
            for r in rows
            if r.get("max_lb") is not None and r["date"] in br
        ]
        if len(pairs) < min_n:
            return {"n": len(pairs), "note": "重叠样本 <%d，攒数据后再算" % min_n}
        import numpy as np

        a = np.array(pairs, dtype=float)
        b = np.array(pairs_lb, dtype=float)
        return {
            "n": len(pairs),
            "corr_zhaban_breadth": round(float(np.corrcoef(a.T)[0, 1]), 3),
            "corr_maxlb_breadth": round(float(np.corrcoef(b.T)[0, 1]), 3),
        }
    except Exception as e:
        return {"error": str(e)[:160]}


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    _no_proxy()
    ap = argparse.ArgumentParser(description="情绪影子日采（涨停/炸板/连板）")
    ap.add_argument(
        "--backfill", nargs=2, metavar=("D0", "D1"), help="回补区间 YYYYMMDD YYYYMMDD"
    )
    a = ap.parse_args(argv)
    if a.backfill:
        n, skipped = backfill(a.backfill[0], a.backfill[1])
        print("SENTIMENT backfill +%d skip=%d -> %s" % (n, skipped, SHADOW))
    else:
        n = collect_today()
        print("SENTIMENT today +%d -> %s" % (n, SHADOW))
    print("corr:", json.dumps(corr_with_breadth(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
