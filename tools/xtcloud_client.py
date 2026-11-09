# -*- coding: utf-8 -*-
"""TDX-Trading 数据查询客户端（默认 http://rookie.xtcloud-tech.com:48088）。

## 这个服务有什么

181 张表 / 约 225GB SQLite，Tushare Pro + akshare + 通达信协议三路采集。
对本项目最有价值的四类：

  分钟线   ts_rt_min(7166万) / ts_stk_mins(3.45亿) → 补本地 stk_mins 只覆盖
           中证800 的缺口（11-06 涨幅≥9.5% 的 43 只票本地覆盖 0）
  资金流   ts_moneyflow / ts_moneyflow_dc / ts_moneyflow_ind_dc
  研报公告 stock_report(14.9万, 带评级/机构/EPS/PE/PDF) / stock_notice(14.6万,
           带 notice_type) / stock_news(33万, 带全文) / ts_anns_d(573万)
  打板     ts_limit_step(晋级) / ts_hm_detail(游资) / ts_top_list(龙虎榜) /
           ts_kpl_concept(开盘啦题材+成分)

## 实测踩到的五个坑（都在这里兜住，业务代码别再踩）

1. **requests 默认走系统代理**：本机 7890 是给 github/pypi 用的，走代理访问
   内网白名单服务会拖到 18s 甚至读超时（实测 ts_rt_min 直接 timeout）。
   这里 `trust_env = False` + 显式空 proxies 强制直连。

2. **ts_stk_mins 的日期过滤完全失效**：start_date/end_date 传 `20261106`、
   `2026-11-06`、`20261106 00:00:00`~`20261106 23:59:59` 三种格式全部返回
   total=0。只能 `code + limit` 倒序取（最新在前），再本地按时间前缀筛。

3. **total 是「该 code 的全部历史行数」**，不是过滤后的行数（实测 002310
   total=401 但当日 0 行）。别拿它算分页或判断有没有当日数据。

4. **存量不同步**：有的票 ts_stk_mins 停在 11-05（002310/002973/300237/
   002578 都是 total=401）。同一个交易日 33 只涨停票里只有 18 只有当日分钟，
   所以兜底必须允许单票失败，不能一失败就整批放弃。

5. **TDX 板块接口是坏的**：`/api/tdx-sector/rank` 的 seg=industry /
   industry_detail / concept 全部返回 n=0，trade_date 停在 20261022。
   文档里写的「权威 56 一级行业 + 涨跌家数」实际取不到，板块口径继续用
   stock_basic.industry（110 类，非空 94.4%）。

## 用法

    from xtcloud_client import XtCloud
    c = XtCloud()
    c.status()                      # 采集器状态（rt_min 采了多少只）
    c.limit_up_stats(days=1)        # 涨停统计（口径与交易所一致）
    c.mins("300750", "20261106")    # 单票当日 241 根，按本地 parquet 列序
"""
from __future__ import annotations

import os
import sys
import threading
import time

DEFAULT_BASE = "http://rookie.xtcloud-tech.com:48088"
INTRANET_BASE = "http://192.168.203.48:8088"

# 本地 stk_mins parquet 的列序（xtcloud 返回的是 freq 在前、close 在 low 后，
# 顺序不同，写盘前必须重排，否则 datamart 读出来的 open/close 会错位）
MINS_COLS = ["ts_code", "trade_time", "close", "open", "high", "low", "vol", "amount"]


class XtCloudError(RuntimeError):
    pass


class XtCloud:
    """薄客户端。只负责取数与列对齐，落盘由调用方决定。"""

    def __init__(self, base=None, timeout=12, retries=1, min_interval=0.05):
        """timeout 默认 12s：正常单票 0.3–1.6s，实测偶发一只拖到 60s
        （服务端抖动），在接口路径上不能等——超时就当这只没数据（坑 4）。"""
        self.base = (base or os.environ.get("XTCLOUD_BASE") or DEFAULT_BASE).rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0
        self._sess = None

    # ── 底层 ────────────────────────────────────────────────
    def _session(self):
        # 双检锁：mins_many 是 8 线程并发，两个线程同时建 Session 会白丢一个
        # 连接池（不致命，但每只票都少一个池子）。
        if self._sess is None:
            with self._lock:
                if self._sess is None:
                    import requests
                    s = requests.Session()
                    # 坑 1：不走系统代理。7890 是给 github/pypi 的，内网服务走它会超时。
                    s.trust_env = False
                    s.proxies = {"http": None, "https": None}
                    s.headers.update({"User-Agent": "jev-xtcloud/1.0"})
                    self._sess = s
        return self._sess

    def _throttle(self):
        with self._lock:
            gap = time.time() - self._last
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
            self._last = time.time()

    def _get(self, path, **params):
        last = None
        for i in range(self.retries + 1):
            self._throttle()
            try:
                r = self._session().get(self.base + path, params=params,
                                        timeout=self.timeout)
                if r.status_code == 404:
                    raise XtCloudError("404 %s" % path)
                j = r.json()
                if isinstance(j, dict) and j.get("error"):
                    raise XtCloudError(str(j["error"]))
                return j
            except XtCloudError:
                raise
            except Exception as exc:  # noqa: BLE001
                last = exc
                if i < self.retries:
                    time.sleep(1.0 + i * 1.5)
        raise XtCloudError("%s 请求失败: %s" % (path, last))

    # ── 通用表查询 ──────────────────────────────────────────
    def table(self, tbl, code=None, start_date=None, end_date=None,
              limit=100, offset=0):
        """→ {"columns": [...], "rows": [[...]], "total": int}

        注意坑 3：total 是该 code 的全量行数，不是过滤后的行数。
        """
        p = {"limit": min(int(limit), 5000), "offset": int(offset)}
        if code:
            p["code"] = str(code).split(".")[0]
        if start_date:
            p["start_date"] = start_date
        if end_date:
            p["end_date"] = end_date
        return self._get("/api/data/table/%s" % tbl, **p)

    def rows(self, tbl, **kw):
        """只要 rows，列名与值拼成 dict 列表。"""
        j = self.table(tbl, **kw)
        cols = j.get("columns") or []
        return [dict(zip(cols, r)) for r in (j.get("rows") or [])]

    # ── 分钟线 ──────────────────────────────────────────────
    def mins(self, code6, day, limit=400, tbl="ts_stk_mins"):
        """单票当日 1 分钟线，**升序**，列序对齐本地 parquet。

        坑 2：日期过滤失效，只能取最新 limit 根再本地筛。
        坑 4：有的票当日没有数据 → 返回 []（不是异常），调用方要能接受。

        limit=400 覆盖一天 241 根 + 前一交易日尾巴；盘后当日必在头部。

        day 认 20261106 / 2026-11-06，格式错**抛异常**——旧实现直接切片，
        传横线格式会拼出 "2026--1-06" 然后一路 startswith 全部落空，
        静默返回空列表：「没数据」和「参数写错」长得一模一样，最难查。
        """
        code6 = str(code6).split(".")[0]
        d8 = _d8(day)
        j = self.table(tbl, code=code6, limit=limit)
        cols = j.get("columns") or []
        idx = {c: i for i, c in enumerate(cols)}
        # 时间列名两张表不同：ts_stk_mins 是 trade_time，rt_min 是 time；
        # 代码列也可能叫 ts_code / code。两者都自适应，不然换表就崩。
        tcol = next((k for k in ("trade_time", "time", "datetime") if k in idx), None)
        ccol = next((k for k in ("ts_code", "code") if k in idx), None)
        # OHLC 缺一不可：宁可抛错也不能拿 0 当收盘价（这一层错了，后面
        # 「首次异动 / 封板时刻」全错还看不出来）。量额允许缺席 → 降级 0。
        need = {"open", "close", "high", "low"}
        if not tcol or not ccol or not need.issubset(idx):
            raise XtCloudError("%s 列不符合预期（时间列=%s 代码列=%s 缺 %s）: %s"
                               % (tbl, tcol, ccol, sorted(need - set(idx)), cols))
        miss = [k for k in ("vol", "amount") if k not in idx]
        if miss and tbl not in _WARNED:
            _WARNED.add(tbl)
            sys.stderr.write("xtcloud %s 缺列 %s，按 0 填充\n" % (tbl, miss))
        # 本地 parquet 的 ts_code 带交易所后缀，xtcloud 也带（"300750.SZ"）
        out = []
        for r in (j.get("rows") or []):
            t = str(r[idx[tcol]])
            if not t.startswith(d8):
                continue
            out.append([r[idx[ccol]],
                        t,
                        _f(r[idx["close"]]), _f(r[idx["open"]]),
                        _f(r[idx["high"]]), _f(r[idx["low"]]),
                        _f(r[idx["vol"]]) if "vol" in idx else 0.0,
                        _f(r[idx["amount"]]) if "amount" in idx else 0.0])
        out.sort(key=lambda x: x[1])
        return out

    def mins_many(self, codes, day, workers=6, limit=400, tbl="ts_stk_mins",
                  deadline_s=None):
        """并发取多只 → {code6: rows}；失败/超预算的票不在结果里（坑 4）。

        deadline_s 给整体时间上限：到点就返回已拿到的，不等剩下的。接口
        路径上宁可少补几只，也不能因为一只票抖动把整个请求拖两分钟。
        """
        from concurrent.futures import ThreadPoolExecutor, wait
        res = {}
        codes = list(codes)
        if not codes:
            return res
        ex = ThreadPoolExecutor(max_workers=max(1, workers))
        try:
            futs = {ex.submit(self.mins, c, day, limit, tbl): c for c in codes}
            done, not_done = wait(list(futs), timeout=deadline_s)
            for f in not_done:
                f.cancel()
            for f in done:
                if f.cancelled() or f.exception():
                    continue
                r = f.result()
                if r:
                    res[futs[f]] = r
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        return res

    # ── 业务快捷接口 ────────────────────────────────────────
    def status(self):
        return self._get("/api/status")

    def health(self):
        try:
            j = self._get("/api/status")
            return {"ok": True, "connected": j.get("connected"),
                    "last_update": j.get("last_update")}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def limit_up_stats(self, days=1, min_count=1, top=200, code=None):
        """涨停统计（口径：close == ROUND_HALF_UP(pre_close×(1+档位),2)）。

        实测坑：它按 ST 5% 档判，**漏判「撤销 ST 未改名」的票**（600537
        *ST亿晶 当日 +10.22% 被它当非涨停）。所以本项目自算的 result 更准，
        这里只作交叉校验，不当权威。
        """
        p = {"days": days, "min_count": min_count, "top": top}
        if code:
            p["code"] = code
        return self._get("/api/backtest/limit-up-stats", **p)

    def moneyflow(self, day, code=None, limit=5000):
        """个股资金流（大小单买卖额）。day=YYYYMMDD。

        旧实现收了 day 却完全没用：传哪一天都返回「按行号最前面那屏」，
        日期错了不报错、结果看着还很合理。日期必须落到查询条件上。
        """
        d8 = _d8(day).replace("-", "")
        kw = {"limit": limit, "start_date": d8, "end_date": d8}
        if code:
            kw["code"] = code
        return self.rows("ts_moneyflow", **kw)

    def reports(self, code=None, limit=50):
        return self.rows("stock_report", code=code, limit=limit)

    def notices(self, code=None, limit=50):
        return self.rows("stock_notice", code=code, limit=limit)


def _d8(day):
    """20261106 / 2026-11-06 / 2026/11/06 → "2026-11-06"；不足 8 位抛错。"""
    s = "".join(ch for ch in str(day) if ch.isdigit())
    if len(s) < 8:
        raise XtCloudError("day 需为 YYYYMMDD（或 2026-11-06）: %r" % (day,))
    s = s[:8]
    return "%s-%s-%s" % (s[:4], s[4:6], s[6:])


def _f(v):
    try:
        f = float(v)
        return f if f == f else 0.0   # NaN → 0
    except (TypeError, ValueError):
        return 0.0


def client():
    """默认单例（复用连接池）。"""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = XtCloud()
    return _CLIENT


_CLIENT = None
_WARNED = set()   # 缺列告警只打一次，别在 60 只票的循环里刷屏
