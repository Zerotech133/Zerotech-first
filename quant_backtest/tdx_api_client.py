# -*- coding: utf-8 -*-
"""TDX-Trading API thin client：ts_stk_limit（每日涨跌停价）拉取与本地缓存。

设计（p0_limit 研发，20261106）：
- 只读 GET /api/data/table/ts_stk_limit（2026-11-16 起需 X-API-Key）；
  limit 上限 5000，单日全市场
  ~5500 行，按 offset 分页拉齐。
- 缓存 data/tdx_api/ts_stk_limit/{YYYYMM}.parquet 按月分片，列
  (trade_date int64, ts_code str, up_limit float, down_limit float)。
- 本地缓存命中（月分片含该日）不重复拉取；网络重试 3 次指数退避。
- 公网优先，失败回退内网。
"""

# ── 口径注记 v2（2026-11-07，QPZM_RD_PLAN.md v2，纯注释、不含任何逻辑）──
# 1) 本客户端只服务 --limit-mode exact：交易所精确涨跌停价替代 K254 的
#    「开=高=收」近似判定。缓存覆盖 20200102 起，审计窗口 243/243 无回退。
# 2) 实测：近似判定漏杀率 29.6%（ST 5% 板 547/547 全漏），但落到策略层面
#    exact vs approx 差 ≤0.4bp/笔、不翻结论——不得据此夸大改造收益。
# 3) 数据红线：ts_stk_limit 是当日快照、无未来信息，可安全用于 T+1 可成交性
#    判定；张松 API 的 qfq 系技术指标为追溯调整，回测禁用（PIT）。
# 4) 安全：服务端 2026-11-16 起 auth_mode=enforce，所有请求需带 X-API-Key
#    （公网 rookie.xtcloud-tech.com:48088 / 内网 192.168.203.13:8088）；
#    Key 由环境变量或仓库根 .env.local 提供，勿写入代码与文档。
# 5) 缓存缺失自动回退 approx 并在 meta.limit_exact_meta.fallback_dates 留痕，
#    引用回测结论前先查该字段是否为空。

import os
import time

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "data", "tdx_api", "ts_stk_limit")

BASES = (
    "http://rookie.xtcloud-tech.com:48088",
    # 2026-11-11 起内网直连机由 192.168.203.48 迁到 192.168.203.13（旧机仅留回滚备份）
    "http://192.168.203.13:8088",
)
PAGE_LIMIT = 5000
RETRIES = 3
TIMEOUT = 90

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "qpzm-p0/1.0 (tdx_api_client)"


def _api_key():
    """TDX 服务 API Key。

    2026-11-16 起服务端 `auth_mode=enforce`：无凭证 `GET /api/*` 一律 401。
    （本文件早期注释里关于凭证的表述已于 2026-11-16 作废。）

    来源：环境变量 `TDX_API_KEY` > 向上找到的仓库根 `.env.local`（已 gitignore）。
    刻意不设默认值：缺 Key 立刻报错，而不是让请求静默 401 后被当成「没有数据」
    （同 2026-11-16 抑制令判例：读不到 ≠ 没有）。
    """
    k = (os.environ.get("TDX_API_KEY") or "").strip()
    if k:
        return k
    d = HERE
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
            # 不 break：本层 .env.local 没有该键就继续向上（就近优先，
            # 避免浅层空文件遮住深层配置 —— quant_backtest/.env.local 就踩过这个）
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    raise RuntimeError(
        "未配置 TDX_API_KEY —— 2026-11-16 起 TDX 服务强制鉴权（auth_mode=enforce）。"
        "请在环境变量或仓库根 .env.local 设置 TDX_API_KEY=<tdx_...>"
    )


def _get(path, params):
    """带重试 + 双 base 回退的 GET，返回 dict。"""
    last = None
    headers = {"X-API-Key": _api_key()}
    for base in BASES:
        for i in range(RETRIES):
            try:
                r = SESSION.get(
                    base + path, params=params, headers=headers, timeout=TIMEOUT
                )
                if r.status_code == 401:
                    raise RuntimeError(
                        "TDX 401 unauthorized —— API Key 无效/已停用/已过期"
                        "（服务端 auth_mode=enforce）")
                if r.status_code == 403:
                    raise RuntimeError("TDX 403 forbidden —— 凭证有效但权限不足（需 read scope）")
                r.raise_for_status()
                return r.json()
            except (RuntimeError, ValueError):
                # 鉴权/解码失败不是网络抖动，重试无意义，直接抛出
                raise
            except Exception as ex:  # noqa: BLE001 网络类异常统一退避重试
                last = ex
                time.sleep(2 ** i)
        break  # 公网连续 3 次失败再试内网
    raise RuntimeError("ts_stk_limit GET failed after retries: %s" % last)


def _month_path(date_int):
    return os.path.join(CACHE_DIR, "%d.parquet" % (date_int // 100))


def _load_month(date_int):
    p = _month_path(date_int)
    if os.path.exists(p):
        return pd.read_parquet(p)
    return None


def fetch_day(date_int, log=None):
    """拉单日全市场涨跌停价，返回 DataFrame(trade_date, ts_code, up_limit, down_limit)。

    优先本地缓存命中；未命中才走 API 并回写月分片（原子替换）。
    返回 None 表示当日库内无数据（非交易日或未采集）。
    """
    hit = _load_month(date_int)
    if hit is not None and (hit["trade_date"] == date_int).any():
        return hit[hit["trade_date"] == date_int].reset_index(drop=True)

    rows, offset = [], 0
    while True:
        d = _get(
            "/api/data/table/ts_stk_limit",
            {
                "start_date": str(date_int),
                "end_date": str(date_int),
                "limit": PAGE_LIMIT,
                "offset": offset,
            },
        )
        got = d.get("rows", [])
        rows.extend(got)
        if len(got) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["trade_date", "ts_code", "up_limit", "down_limit"])
    df["trade_date"] = df["trade_date"].astype("int64")
    # 回写月分片（与既有日合并，原子替换）
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = _month_path(date_int)
    if hit is not None:
        df = (
            pd.concat([hit[hit["trade_date"] != date_int], df], ignore_index=True)
            .sort_values(["trade_date", "ts_code"])
            .reset_index(drop=True)
        )
    tmp = p + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, p)
    if log:
        log("  [tdx_api] %s fetched %d rows -> %s" % (date_int, len(rows), os.path.basename(p)))
    return df


def trade_dates(start, end):
    """交易日历（SZSE，is_open=1），本地过滤 [start, end]，返回升序 int64 列表。

    注意：ts_trade_cal 接口忽略 start_date/end_date 过滤参数，必须
    全量翻页后本地过滤（全表 ~22k 行，5 页）。
    """
    rows, offset = [], 0
    while True:
        d = _get(
            "/api/data/table/ts_trade_cal",
            {"limit": PAGE_LIMIT, "offset": offset},
        )
        got = d.get("rows", [])
        rows.extend(got)
        if len(got) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
    cols = d["columns"]
    di, oi = cols.index("cal_date"), cols.index("is_open")
    dates = sorted(
        {
            int(r[di])
            for r in rows
            if int(float(r[oi])) == 1 and int(start) <= int(r[di]) <= int(end)
        }
    )  # 多交易所日历行去重
    return dates


def ensure_cache(start, end, log=None):
    """确保 [start, end] 全部交易日涨跌停价已缓存，返回 (ok_dates, missing_dates)。"""
    dates = trade_dates(start, end)
    ok, missing = [], []
    for d in dates:
        try:
            df = fetch_day(d, log=log)
            (ok if df is not None else missing).append(d)
        except Exception as ex:  # noqa: BLE001 单日失败不阻断整体
            missing.append(d)
            if log:
                log("  [tdx_api] %s FAILED: %s" % (d, str(ex)[:80]))
    if log:
        log("[tdx_api] cache ok %d days, missing %d" % (len(ok), len(missing)))
    return ok, missing


def load_cache(start, end):
    """读 [start, end] 缓存并集，返回 DataFrame 或 None。"""
    frames = []
    months = [m for m in range(int(start) // 100, int(end) // 100 + 1)]
    for m in months:
        p = os.path.join(CACHE_DIR, "%d.parquet" % m)
        if os.path.exists(p):
            df = pd.read_parquet(p)
            df = df[(df["trade_date"] >= int(start)) & (df["trade_date"] <= int(end))]
            if len(df):
                frames.append(df)
    if not frames:
        return None
    out = pd.concat(frames, ignore_index=True)
    return out.drop_duplicates(["trade_date", "ts_code"]).reset_index(drop=True)


if __name__ == "__main__":
    import sys

    s, e = sys.argv[1], sys.argv[2]
    ok, miss = ensure_cache(s, e, log=print)
    print("ok=%d missing=%s" % (len(ok), miss[:10]))
