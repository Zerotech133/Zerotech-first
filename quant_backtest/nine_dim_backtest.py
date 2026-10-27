# -*- coding: utf-8 -*-
"""九维 P2 历史回放回测（一次性研究脚本；评分规则与 P1 引擎单一来源）。

对每个历史锚点用「截至该日」的数据重跑 P1 两段式评分（本地粗筛 →
候选池 → API 因子 → score_one），把全部完整评分入样本库，按阈值分桶
对照 r5/r20 前向收益 → 验证 ≥90 阈值的历史胜率/左尾并支持调参。

防未来函数（与 chanlun 截断重放同纪律）：
· bars/RPS/行业聚合/两市成交额全部截断至锚点（as-of 260 根窗口）；
· fina 按 ann_date≤锚点 取当期+上期（同期多公告取最新）；report_rc
  按 report_date、forecast 按 announce_date ≤锚点 且落在锚点前
  30/120 日历日窗口（同 live 窗口宽）；
· flow/daily_basic/top_list/top_inst/block_trade 按 trade_date=锚点；
· hsgt/科创50/沪深300 指数序列截断至锚点；财报期档用锚点月份。

诚实不可判：news.db 自 2026-09-25 才有数据 → 历史锚点的情绪因子
（8分）与政策项（s2, 4分）整体 None 不计分（cov 分母同步剔除），
与「None 不计分不扣分」铁律一致；最新锚点起随 news 积累自动可用。

对照与口径：
· 基准=同锚点沪深300 r5/r20，分桶报「超额」= 个股 - 同锚点基准；
· 前向窗口跨锚点重叠（步长<持有期）属重复观测 → 另给「同票 20 交易
  日内去重（thinned，按交易日历位置）」口径；
· 左尾：各桶 r20 的 min/p5/p10 + 最差 5 条明细；
· 跨除权（pre_close 缺口>0.5%）剔出统计（与 P1 update_perf 同口径）。

用法：
  python nine_dim_backtest.py --dryrun            # 只跑本地粗筛阶段
  python nine_dim_backtest.py --last 4            # 最近 4 个锚点试跑
  python nine_dim_backtest.py                     # 全窗口回放（步长10）
  python nine_dim_backtest.py --step 5            # 周频
  python nine_dim_backtest.py --force             # 忽略已回放锚点重跑
"""
import argparse
import logging
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nine_dim as nd                     # noqa: E402  单一规则来源
import rps_resonance as rps               # noqa: E402
import db                                 # noqa: E402  SQLite 数据层（Phase 2）
from nine_dim_source_snapshot import (    # noqa: E402
    ApiRecordStore, SourceSnapshot, SnapshotFormatError,
)

DATA = nd.DATA
REPLAY_JSONL = os.path.join(DATA, "nine_dim_replay.jsonl")
REPLAY_EVAL = os.path.join(DATA, "nine_dim_replay_eval_cache.json")
BT_FIN = os.path.join(DATA, "nine_dim_bt_fina_cache.json")
BT_RC = os.path.join(DATA, "nine_dim_bt_rc_cache.json")
BT_FC = os.path.join(DATA, "nine_dim_bt_fc_cache.json")
BT_IDX = os.path.join(DATA, "nine_dim_bt_idx_cache.json")

WINDOW_BARS = 500       # 回放窗口（交易日，近 2 年）
STEP = 10               # 锚点步长（交易日）；5=周频
MIN_ANCHOR_BARS = 260   # 锚点前最少 bar 数（对齐 live 260 根窗口）
FWD = 20                # 前向持有窗口（交易日）
KEEP = 260              # as-of 切片长度（对齐 live _load_market tail）
BENCH = "000300.SH"     # 超额基准
MIN_FR = 3000           # 锚点本地数据少于该股票数 → 跳过（数据缺口）
RC_WIN_DAYS = 30        # report_rc 窗口（日历日，同 live）
FC_WIN_DAYS = 120       # forecast 窗口（日历日，同 live）


def _snapshot_stores(path, codes):
    """加载 as-of staging 载荷，适配为回放缓存使用的 per-code stores。"""
    snapshot = SourceSnapshot(path)
    missing = sorted(set(codes) - set(snapshot.codes))
    if missing:
        raise SnapshotFormatError("来源快照缺少候选代码: %s" %
                                  ",".join(missing[:5]))
    apis = list(SourceSnapshot.APIS)
    # fina_indicator 为可选扩展：旧快照没有该回执时保持原有 SQLite/JSON
    # 路径；新 staging 快照带回执时，回放必须优先消费同一份 as-of 载荷。
    if any(snapshot.get("fina_indicator", c)["source_status"] !=
           "missing_receipt" for c in snapshot.codes):
        apis.append("fina_indicator")
    return {api: ApiRecordStore(snapshot, api) for api in apis}


def _i8(v):
    try:
        return int(str(v)[:10].replace("-", ""))
    except (TypeError, ValueError):
        return 0


def _cal_shift(anchor_i, days):
    """YYYYMMDD 整数 → 减去 N 个日历日的 YYYYMMDD 整数（防整减法坑）。"""
    d = datetime.strptime(str(anchor_i), "%Y%m%d") - timedelta(days=days)
    return int(d.strftime("%Y%m%d"))


def _atomic_rewrite(path, rows):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n"
                        for r in rows))
    os.replace(tmp, path)


def _load_replay_rows(path, replace_anchors=()):
    """读取研究回放，并仅移除本次要强制重跑的锚点。"""
    replace = {str(a) for a in replace_anchors}
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict) and str(row.get("anchor", "")) not in replace:
                    rows.append(row)
    done = {str(row["anchor"]) for row in rows if row.get("anchor")}
    return rows, done


# ── ① 本地全量装载 + as-of 切片 ────────────────────────────
def _load_full():
    """{code: (df[全深度], td_int64数组)}，一次装载全程复用。"""
    frames, tds, latest = {}, {}, ""
    for f in os.listdir(nd.LOCAL):
        if not f.endswith(".parquet") or f.startswith("_"):
            continue
        try:
            df = pd.read_parquet(os.path.join(nd.LOCAL, f))
            if len(df) < 30:
                continue
            keepc = [c for c in ("trade_date", "close", "volume",
                                 "amount", "pct_chg", "pre_close")
                     if c in df.columns]
            d = df[keepc].drop_duplicates("trade_date", keep="last") \
                         .sort_values("trade_date").reset_index(drop=True)
            t = d["trade_date"].map(_i8)
            d = d.assign(td8=t)
            d = d[d["td8"] > 0].reset_index(drop=True)
            if d.empty:
                continue
            code = f[:-8].replace("_", ".")
            frames[code] = d
            tds[code] = d["td8"].to_numpy()
            latest = max(latest, "%08d" % int(d["td8"].iloc[-1]))
        except Exception:
            continue
    return frames, tds, latest


def _asof(code, frames, tds, anchor_i, min_rows=30):
    j = int(np.searchsorted(tds[code], anchor_i, side="right"))
    if j < min_rows:
        return None
    return frames[code].iloc[max(0, j - KEEP):j]


# ── ② 交易日历 / 指数 / 北向（窗口级一次拉取） ──────────────
def _trade_days(latest):
    st = (datetime.strptime(latest, "%Y%m%d")
          - timedelta(days=int((MIN_ANCHOR_BARS + WINDOW_BARS + 400)
                               * 1.55))).strftime("%Y%m%d")
    # 本地中台读层优先（trade_cal 全量日历，免 API 配额）
    try:
        import datamart
        df = datamart.read("trade_cal", start=st, end=latest)
        if df is not None and not df.empty and \
                {"cal_date", "is_open"} <= set(df.columns):
            if "exchange" in df.columns:
                df = df[df["exchange"].astype(str) == "SSE"]
            days = sorted(df.loc[pd.to_numeric(df["is_open"],
                                               errors="coerce") == 1,
                                 "cal_date"].astype(str))
            if days:
                return days
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "nine_dim_backtest.py", exc)
    rows, fields = nd._ts("trade_cal", exchange="SSE",
                          start_date=st, end_date=latest)
    i_o, i_c = fields.index("is_open"), fields.index("cal_date")
    return sorted(r[i_c] for r in rows if r[i_o] == 1)


def _idx_series(code, st):
    cache = {}
    if os.path.exists(BT_IDX):
        try:
            cache = json.load(open(BT_IDX, encoding="utf-8"))
        except Exception:
            cache = {}
    if cache.get(code, {}).get("st") != st:
        rows, fields = nd._ts("index_daily", ts_code=code,
                              start_date=st, end_date="20991231")
        i = {f: k for k, f in enumerate(fields)}
        pts = sorted(((_i8(r[i["trade_date"]]), float(r[i["close"]]))
                      for r in rows), key=lambda x: x[0])
        cache[code] = {"st": st,
                       "tds": [p[0] for p in pts],
                       "closes": [p[1] for p in pts]}
        nd._write_json(BT_IDX, cache)
    return cache[code]


def _hsgt_series(st):
    """[(td8, north_money)] 全窗口分片拉（tushare 单次 300 行上限，
    不分片早期锚点会误判北向不可判）。"""
    pts, seen, cur, guard = [], set(), st, 0
    today = int(datetime.now().strftime("%Y%m%d"))
    while guard < 20:
        guard += 1
        nxt = (datetime.strptime(str(cur), "%Y%m%d")
               + timedelta(days=180)).strftime("%Y%m%d")
        rows, fields = nd._ts("moneyflow_hsgt", start_date=cur,
                              end_date=nxt)
        i_d, i_n = fields.index("trade_date"), fields.index("north_money")
        for r in rows:
            v, t = nd._f(r[i_n]), _i8(r[i_d])
            if v is not None and t not in seen:
                seen.add(t)
                pts.append((t, v))
        if int(nxt) >= today:
            break
        cur = int(nxt) + 1
    return sorted(pts, key=lambda x: x[0])


# ── ③ API 因子缓存（per-code 窗口级一次拉，锚点切片） ───────
def _load_cache(path):
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "nine_dim_backtest.py", exc)
    return {}


def _fina_bt(codes):
    """fina_indicator 近 12 期（含 ann_date），缓存 30 天。

    Phase 3：SQLite 为主（per-code blob + fetched_at 列支持 30 天 TTL 的
    SQL 过滤），避免 57.6MB 全量加载/序列化；新拉取只写 SQLite，JSON 降级
    为冻结 fallback 快照。SQLite 不可用时退回 _fina_bt_json。
    返回 _FinaStore（.get(code) 同 dict 语义，score_anchor 无感消费）。"""
    try:
        conn = db.get_conn()
    except Exception as e:
        print("  fina_bt: sqlite open failed (%s), fallback to json" % e,
              flush=True)
        return _fina_bt_json(codes)
    try:
        cutoff = (datetime.now() - timedelta(days=30)
                  ).strftime("%Y-%m-%d %H:%M:%S")
        need = db.nd_fina_need(conn, codes, cutoff)
        if need:
            flds = ("ts_code,ann_date,end_date,or_yoy,netprofit_yoy,"
                    "grossprofit_margin,roe,ocf_to_or,ebitda,debt_to_assets")
            conn.execute("BEGIN")
            for k, c in enumerate(need):
                rec = {"fetched_at": nd._now_iso(), "rows": [],
                       "fields": flds.split(",")}
                try:
                    rows, fields = nd._ts("fina_indicator", ts_code=c,
                                          limit=12, fields=flds)
                    i = {f: j for j, f in enumerate(fields)}
                    rec["rows"] = sorted(rows, key=lambda r: (
                        nd._td8(r[i["end_date"]]), nd._td8(r[i["ann_date"]])))
                    rec["fields"] = fields
                except Exception as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "nine_dim_backtest.py", exc)
                db.nd_fina_upsert(conn, c, rec["rows"], rec["fields"],
                                  rec["fetched_at"])
                time.sleep(0.12)
                if (k + 1) % 50 == 0:
                    conn.commit()
                    conn.execute("BEGIN")
                    print("  fina_bt(sqlite) %d/%d" % (k + 1, len(need)),
                          flush=True)
            conn.commit()
            print("  fina_bt(sqlite) done: +%d codes" % len(need), flush=True)
        return _FinaStore(conn)
    except Exception as e:
        try:
            conn.close()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "nine_dim_backtest.py", exc)
        print("  fina_bt: sqlite path failed (%s), fallback to json" % e,
              flush=True)
        return _fina_bt_json(codes)


def _fina_bt_json(codes):
    """JSON 全量路径（fallback）：原 _fina_bt 实现。"""
    cache = _load_cache(BT_FIN)
    need = [c for c in codes if not cache.get(c)
            or (datetime.now() - datetime.strptime(
                cache[c].get("fetched_at", "2000-01-01"),
                "%Y-%m-%d %H:%M:%S")).days >= 30]
    flds = ("ts_code,ann_date,end_date,or_yoy,netprofit_yoy,"
            "grossprofit_margin,roe,ocf_to_or,ebitda,debt_to_assets")
    for k, c in enumerate(need):
        rec = {"fetched_at": nd._now_iso(), "rows": [],
               "fields": flds.split(",")}
        try:
            rows, fields = nd._ts("fina_indicator", ts_code=c,
                                  limit=12, fields=flds)
            i = {f: j for j, f in enumerate(fields)}
            rec["rows"] = sorted(rows, key=lambda r: (
                nd._td8(r[i["end_date"]]), nd._td8(r[i["ann_date"]])))
            rec["fields"] = fields
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "nine_dim_backtest.py", exc)
        cache[c] = rec
        time.sleep(0.12)
        if (k + 1) % 50 == 0:
            nd._write_json(BT_FIN, cache)
            print("  fina_bt(json) %d/%d" % (k + 1, len(need)), flush=True)
    nd._write_json(BT_FIN, cache)
    return cache


class _FinaStore:
    """SQLite-backed 懒加载 fina rec 容器。.get(code) 同 dict 语义。"""
    def __init__(self, conn):
        self._conn = conn
        self._mem = {}
        self._json = None

    def get(self, code, default=None):
        if code in self._mem:
            return self._mem[code]
        try:
            rec = db.nd_fina_query(self._conn, code)
        except Exception:
            rec = None
        if rec is None:
            if self._json is None:
                self._json = _load_cache(BT_FIN)
            rec = self._json.get(code)
        self._mem[code] = rec
        return rec if rec is not None else default


def _fina_asof(rec, anchor_i):
    """ann_date≤锚点的最新一期 cur + 上一期 prev（同期多公告取最新）。"""
    rows, fields = rec.get("rows") or [], rec.get("fields") or []
    if not rows:
        return {}
    i = {f: k for k, f in enumerate(fields)}
    per = {}
    for r in rows:
        ann = _i8(r[i["ann_date"]] or 0)
        if not ann or ann > anchor_i:
            continue
        ed = nd._td8(r[i["end_date"]])
        if ed not in per or ann >= per[ed][0]:
            per[ed] = (ann, r)
    if not per:
        return {}
    eds = sorted(per, reverse=True)

    def pick(r):
        return {f: r[i[f]] for f in fields if f != "ts_code"}

    out = {"cur": pick(per[eds[0]][1])}
    if len(eds) > 1:
        out["prev"] = pick(per[eds[1]][1])
    return out


def _rc_bt(codes, st):
    """report_rc 全窗口明细（ed 开放，锚点切片做过滤），
    缓存键=st（月末取整防逐日失效）。

    Phase 2：SQLite 为主：按 code 存 blob，懒加载，避免 105MB 全量
    加载/序列化；新拉取的 code 只写 SQLite（不再回写 JSON，JSON 降级
    为冻结快照 fallback）。SQLite 不可用时整体退回 _rc_bt_json。
    返回 _RcStore（.get(code) 同 dict 语义，score_anchor 无感消费）。"""
    try:
        conn = db.get_conn()
    except Exception as e:
        print("  rc_bt: sqlite open failed (%s), fallback to json" % e,
              flush=True)
        return _rc_bt_json(codes, st)
    try:
        # st 失效 → 清表重建（与原 cache 重置同义）
        if db.nd_rc_get_st(conn) != st:
            conn.execute("BEGIN")
            db.nd_rc_clear(conn)
            db.nd_rc_set_st(conn, st)
            conn.commit()
        need = db.nd_rc_missing(conn, codes)
        if need:
            conn.execute("BEGIN")
            for k, c in enumerate(need):
                try:
                    rows, fields = nd._ts("report_rc", ts_code=c,
                                          start_date=st, end_date="20991231",
                                          limit=600)
                    err = 0
                except Exception:
                    rows, fields, err = [], [], 1
                db.nd_rc_upsert(conn, c, rows, fields, err)
                time.sleep(0.1)
                if (k + 1) % 50 == 0:
                    conn.commit()
                    conn.execute("BEGIN")
                    print("  rc_bt(sqlite) %d/%d" % (k + 1, len(need)),
                          flush=True)
            conn.commit()
            print("  rc_bt(sqlite) done: +%d codes" % len(need), flush=True)
        return _RcStore(conn)
    except Exception as e:
        try:
            conn.close()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "nine_dim_backtest.py", exc)
        print("  rc_bt: sqlite path failed (%s), fallback to json" % e,
              flush=True)
        return _rc_bt_json(codes, st)


def _rc_bt_json(codes, st):
    """JSON 全量路径（fallback）：原 _rc_bt 实现，保留以备 SQLite 不可用。"""
    cache = _load_cache(BT_RC)
    if (cache.get("_meta") or {}).get("st") != st:
        cache = {"_meta": {"st": st}}
    need = [c for c in codes if c not in cache]
    for k, c in enumerate(need):
        try:
            rows, fields = nd._ts("report_rc", ts_code=c,
                                  start_date=st, end_date="20991231",
                                  limit=600)
            cache[c] = {"rows": rows, "fields": fields}
        except Exception:
            cache[c] = {"rows": [], "fields": [], "err": 1}
        time.sleep(0.1)
        if (k + 1) % 50 == 0:
            nd._write_json(BT_RC, cache)
            print("  rc_bt(json) %d/%d" % (k + 1, len(need)), flush=True)
    nd._write_json(BT_RC, cache)
    return cache


class _RcStore:
    """SQLite-backed 懒加载 rc rec 容器。.get(code) 同 dict 语义，
    供 score_anchor 的 `rc_bt_c.get(c)` 无感消费。
    持有 conn 至回测结束（一次性研究脚本，进程退出即释放）。"""
    def __init__(self, conn):
        self._conn = conn
        self._mem = {}
        self._json = None  # 仅在 SQLite 缺该 code 时懒加载 JSON 兜底

    def get(self, code, default=None):
        if code in self._mem:
            return self._mem[code]
        try:
            rec = db.nd_rc_query(self._conn, code)
        except Exception:
            rec = None
        if rec is None:
            # 理论上 _rc_bt 已 ensure 全部 codes；兜底防遗漏
            if self._json is None:
                self._json = _load_cache(BT_RC)
            rec = self._json.get(code)
        self._mem[code] = rec
        return rec if rec is not None else default


def _rc_at(rec, anchor_i):
    """锚点前 30 日历日窗内明细 → _rc_stats（同 live 口径与语义；
    拉取异常 rec.err → 全 None 不可判，同 live 异常分支）。"""
    if rec.get("err"):
        return {"n_rep": None, "up": None, "down": None, "up_q4": None}
    fields = rec.get("fields") or []
    rows_all = rec.get("rows") or []
    if not rows_all:
        return {"n_rep": 0, "up": 0, "down": 0, "up_q4": 0}
    i = {f: k for k, f in enumerate(fields)}
    di = i.get("report_date", 0)
    lo = _cal_shift(anchor_i, RC_WIN_DAYS)
    rows = [r for r in rows_all if lo <= _i8(r[di]) <= anchor_i]
    return nd._rc_stats(rows, fields)


def _fc_bt(codes, st):
    """forecast 全窗口明细（announce_date 过滤在锚点切片做）。

    Phase 4：SQLite 为主（per-code blob，无 err 字段，同 rc 思路），
    避免全量加载；新拉取只写 SQLite，JSON 降级为冻结 fallback 快照。
    返回 _FcStore（.get(code) 同 dict 语义，score_anchor 无感消费）。"""
    try:
        conn = db.get_conn()
    except Exception as e:
        print("  fc_bt: sqlite open failed (%s), fallback to json" % e,
              flush=True)
        return _fc_bt_json(codes, st)
    try:
        if db.nd_fc_get_st(conn) != st:
            conn.execute("BEGIN")
            db.nd_fc_clear(conn)
            db.nd_fc_set_st(conn, st)
            conn.commit()
        need = db.nd_fc_missing(conn, codes)
        if need:
            conn.execute("BEGIN")
            for k, c in enumerate(need):
                try:
                    rows, fields = nd._ts("forecast", ts_code=c,
                                          start_date=st, end_date="20991231",
                                          limit=10)
                except Exception:
                    rows, fields = [], []
                db.nd_fc_upsert(conn, c, rows, fields)
                time.sleep(0.1)
                if (k + 1) % 50 == 0:
                    conn.commit()
                    conn.execute("BEGIN")
                    print("  fc_bt(sqlite) %d/%d" % (k + 1, len(need)),
                          flush=True)
            conn.commit()
            print("  fc_bt(sqlite) done: +%d codes" % len(need), flush=True)
        return _FcStore(conn)
    except Exception as e:
        try:
            conn.close()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "nine_dim_backtest.py", exc)
        print("  fc_bt: sqlite path failed (%s), fallback to json" % e,
              flush=True)
        return _fc_bt_json(codes, st)


def _fc_bt_json(codes, st):
    """JSON 全量路径（fallback）：原 _fc_bt 实现。"""
    cache = _load_cache(BT_FC)
    if (cache.get("_meta") or {}).get("st") != st:
        cache = {"_meta": {"st": st}}
    need = [c for c in codes if c not in cache]
    for k, c in enumerate(need):
        try:
            rows, fields = nd._ts("forecast", ts_code=c,
                                  start_date=st, end_date="20991231",
                                  limit=10)
            cache[c] = {"rows": rows, "fields": fields}
        except Exception:
            cache[c] = {"rows": [], "fields": []}
        time.sleep(0.1)
        if (k + 1) % 50 == 0:
            nd._write_json(BT_FC, cache)
            print("  fc_bt(json) %d/%d" % (k + 1, len(need)), flush=True)
    nd._write_json(BT_FC, cache)
    return cache


class _FcStore:
    """SQLite-backed 懒加载 fc rec 容器。.get(code) 同 dict 语义。"""
    def __init__(self, conn):
        self._conn = conn
        self._mem = {}
        self._json = None

    def get(self, code, default=None):
        if code in self._mem:
            return self._mem[code]
        try:
            rec = db.nd_fc_query(self._conn, code)
        except Exception:
            rec = None
        if rec is None:
            if self._json is None:
                self._json = _load_cache(BT_FC)
            rec = self._json.get(code)
        self._mem[code] = rec
        return rec if rec is not None else default


def _fc_at(rec, anchor_i):
    rows_all = rec.get("rows") or []
    fields = rec.get("fields") or []
    if not rows_all:
        return None
    i = {f: k for k, f in enumerate(fields)}
    dk = next((k for k in ("announce_date", "ann_date") if k in i), None)
    di = i.get(dk, 0) if dk else 0
    lo = _cal_shift(anchor_i, FC_WIN_DAYS)
    rows = [r for r in rows_all
            if (not dk and True) or (lo <= _i8(r[di]) <= anchor_i)]
    return nd._fc_pick(rows, fields)


# ── ④ 新闻情绪/政策（锚点窗口版；同 live 缺数据语义） ─────────
def _news_stats_at(codes, basic, anchor_i):
    """同 nd._news_stats，窗口改为锚点前 7/14 日历日。"""
    stats = {c: {"n7": 0, "n_prev7": 0, "bull": 0, "bear": 0, "n": 0}
             for c in codes}
    nm = {c: (basic.get(c) or ["", ""])[0] for c in codes}
    nm = {c: n for c, n in nm.items() if n}
    if not os.path.exists(nd.NEWS_DB) or not nm:
        return stats
    a = datetime.strptime(str(anchor_i), "%Y%m%d")
    since = (a - timedelta(days=14)).strftime("%Y-%m-%d %H:%M")
    cut = (a - timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
    try:
        db = sqlite3.connect(nd.NEWS_DB)
        for title, j, ca in db.execute(
                "SELECT title, jev, created_at FROM news "
                "WHERE jev IS NOT NULL AND created_at>=? "
                "ORDER BY id DESC LIMIT 8000", (since,)):
            if not title:
                continue
            for c, n in nm.items():
                if n in title:
                    s = stats.get(c)
                    if s is None:
                        continue
                    s["n"] += 1
                    if ca >= cut:
                        s["n7"] += 1
                    else:
                        s["n_prev7"] += 1
                    try:
                        b = json.loads(j).get("bias")
                    except Exception:
                        b = None
                    if b == "bull":
                        s["bull"] += 1
                    elif b == "bear":
                        s["bear"] += 1
        db.close()
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "nine_dim_backtest.py", exc)
    return stats


def _pol_titles_at(anchor_i):
    """政策关键词标题集（V3 诚实不可判：返回 None=news 未覆盖——
    锚点政策窗口早于 news.db 最早记录，属体系性缺失而非未命中，
    s2 记 None 豁免 cov；返回 set=已覆盖，可能空集=覆盖但无命中）。
    live 在 news.db 缺表/异常时也传 None（V3 同步改动）。"""
    a = datetime.strptime(str(anchor_i), "%Y%m%d")
    since = (a - timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
    pol_kw = ("政策", "部委", "规划", "央行", "监管", "降准", "降息",
              "补贴", "试点", "条例", "意见")
    out = set()
    try:
        db = sqlite3.connect(nd.NEWS_DB)
        row = db.execute("SELECT MIN(created_at) FROM news").fetchone()
        first = str(row[0])[:16] if row and row[0] else None
        if first is None or since < first:
            db.close()
            return None                  # 未开始收集 → 诚实不可判
        for (t,) in db.execute(
                "SELECT title FROM news WHERE created_at>=? "
                "ORDER BY id DESC LIMIT 3000", (since,)):
            if t and any(k in t for k in pol_kw):
                out.add(t)
        db.close()
    except Exception:
        return None
    return out


# ── ⑤ 粗筛阶段（全锚点，零 API） ───────────────────────────
def pass_a(frames, tds, basic, anchors, idx688):
    """每锚点：as-of 切片 → RPS50 → 粗筛 → 环境/调权/行业聚合。"""
    out = {}
    for k, dt in enumerate(anchors):
        t0 = time.time()
        ai = int(dt)
        fr = {}
        for code in frames:
            s = _asof(code, frames, tds, ai)
            if s is not None:
                fr[code] = s
        if len(fr) < MIN_FR:
            print("  [%s] skip: only %d frames" % (dt, len(fr)))
            continue
        closes = {c: d["close"].to_numpy(dtype=float)
                  for c, d in fr.items()}
        rdf = rps.batch_rps(closes)
        rps50 = {kk: float(v) for kk, v in
                 (rdf["rps_50"] if "rps_50" in rdf.columns
                  else pd.Series(dtype=float)).items()}
        cand, funnel = nd.coarse_filter(fr, basic, rps50)
        amt_sum = sum(float(d["amount"].iloc[-1] or 0) * 1000
                      for d in fr.values())
        cl = [c for t, c in zip(idx688["tds"], idx688["closes"])
              if t <= ai][-300:]
        env = nd.market_env(amt_sum, closes=np.array(cl, dtype=float),
                            as_of_month=int(dt[4:6]))
        wd = nd.dynamic_weight(env)
        ind_of = {c: (basic.get(c) or ["", ""])[1] for c in fr}
        buf5, bufr = {}, {}
        for c, d in fr.items():
            g = ind_of.get(c) or ""
            if not g or len(d) < 10:
                continue
            cc = d["close"].to_numpy(dtype=float)
            if len(cc) >= 6 and cc[-6] > 0:
                buf5.setdefault(g, []).append(cc[-1] / cc[-6] - 1)
            if rps50.get(c) is not None:
                bufr.setdefault(g, []).append(rps50[c])
        ind_ret5 = {g: float(np.median(v)) for g, v in buf5.items()}
        out[dt] = {
            "cand": cand, "rps50": rps50, "env": env, "wdelta": wd,
            "funnel": funnel, "n_cand": len(cand),
            "ind_top3": sorted(ind_ret5,
                               key=lambda g: -ind_ret5[g])[:3],
            "ind_rps": {g: round(float(np.median(v)), 1)
                        for g, v in bufr.items()},
            "amt_yi": round(amt_sum / 1e8)}
        print("  [%d/%d] %s cand=%d env(vol=%s,bull=%s) %.1fs"
              % (k + 1, len(anchors), dt, len(cand),
                 env.get("vol_pct"), env.get("bull"),
                 time.time() - t0), flush=True)
    return out


# ── ⑥ 评分阶段（锚点 × 候选 → 样本行） ─────────────────────
def score_anchor(dt, meta, frames, tds, basic, prev5, fina_bt_c,
                 rc_bt_c, fc_bt_c, hsgt, source_stores=None):
    ai = int(dt)
    cand = meta["cand"]
    fr = {}
    for c in cand:
        s = _asof(c, frames, tds, ai)
        if s is not None:
            fr[c] = s
    if not fr:
        return []

    block_store = source_stores.get("block_trade") \
        if source_stores else None
    flow = nd._fetch_flow(dt, source_snapshot=block_store)
    marg = nd._fetch_margin(dt, prev5)
    pts = [(t, v) for t, v in hsgt if t <= ai]
    north3 = None
    if len(pts) >= 3:
        north3 = all(v > 0 for _t, v in pts[-3:])
    fina_map = {c: _fina_asof(fina_bt_c.get(c) or {}, ai) for c in fr}
    rc_map = {c: _rc_at(rc_bt_c.get(c) or {}, ai) for c in fr}
    fc_map = {c: _fc_at(fc_bt_c.get(c) or {}, ai) for c in fr}
    nstats = _news_stats_at(list(fr), basic, ai)
    pol = _pol_titles_at(ai)

    # 行业分位上下文（候选池内近似，同 live）
    gm_by, ev_by, pe_by = {}, {}, {}
    for c in fr:
        g = (basic.get(c) or ["", ""])[1] or ""
        fcur = fina_map.get(c, {}).get("cur", {})
        gm = nd._f(fcur.get("grossprofit_margin"))
        if gm is not None:
            gm_by.setdefault(g, []).append(gm)
        pe = nd._f((flow["basic"].get(c) or {}).get("pe_ttm"))
        if pe:
            pe_by.setdefault(g, []).append(pe)
        ebd = nd._f(fcur.get("ebitda"))
        tmv = nd._f((flow["basic"].get(c) or {}).get("total_mv"))
        if ebd and ebd > 0 and tmv:
            ev_by.setdefault(g, []).append(tmv * 1e4 / (ebd * 1e8))
    ctx = {"gm_p70": {g: round(float(np.percentile(v, 70)), 2)
                      for g, v in gm_by.items()},
           "ev_mean": {g: round(float(np.mean(v)), 2)
                       for g, v in ev_by.items()},
           "pe_med": {g: round(float(np.median(v)), 2)
                      for g, v in pe_by.items()},
           "ind_top3": meta["ind_top3"], "ind_rps": meta["ind_rps"],
           "north_buy3": north3,
           "margin_global_growth": marg.get("global_growth")}

    out = []
    for c in fr:
        s = nd._build_stock(c, fr, basic, meta["rps50"], flow, marg,
                            north3, fina_map, rc_map, fc_map, nstats,
                            pol, ctx, meta["env"])
        res = nd.score_one(s, meta["wdelta"])
        if res["score"] is None:
            continue
        d = fr[c]
        out.append({"anchor": dt, "code": c, "name": s["name"],
                    "industry": s["industry"],
                    "rps50": meta["rps50"].get(c),
                    "entry_px": round(float(
                        d["close"].to_numpy(dtype=float)[-1]), 2),
                    "score": res["score"], "cov": res["cov"],
                    "v": {it["id"]: it["state"]
                          for g in res["detail"] for it in g["items"]},
                    "r5": None, "r20": None, "mature5": False,
                    "mature20": False, "adj": False,
                    "bench5": None, "bench20": None})
    return out


# ── ⑦ 前向收益 + 基准（样本落库后统一计算） ─────────────────
def _bars_of(code, frames):
    d = frames.get(code)
    if d is None:
        return []
    pc = d["pre_close"] if "pre_close" in d.columns \
        else pd.Series([np.nan] * len(d))
    return [{"dt": int(t), "c": float(c),
             "pc": None if (pcv != pcv or not pcv) else float(pcv)}
            for t, c, pcv in zip(d["td8"], d["close"], pc)]


def fill_forward(rows, frames, tds, bench):
    """逐条算 r5/r20（成熟实现/未成熟盯市）+ 同锚点基准。"""
    bt = np.array(bench["tds"])
    bc = np.array(bench["closes"])
    bcache = {}
    for r in rows:
        code = r["code"]
        ai = int(r["anchor"])
        if code not in bcache:
            bars = _bars_of(code, frames)
            bpos = int(np.searchsorted(
                np.array([b["dt"] for b in bars]), ai,
                side="right")) - 1 if bars else -1
            bcache[code] = (bars, bpos)
        bars, b = bcache[code]
        if 0 <= b < len(bars):
            px = r["entry_px"] or bars[b]["c"]
            for w, rk, mk in ((nd.R5, "r5", "mature5"),
                              (nd.R20, "r20", "mature20")):
                j = b + w
                if j < len(bars):
                    r[rk] = round((bars[j]["c"] / px - 1) * 100, 2)
                    r[mk] = True
                    if nd._exdiv(bars, b, j):
                        r["adj"] = True
                elif bars:
                    r[rk] = round((bars[-1]["c"] / px - 1) * 100, 2)
        p = int(np.searchsorted(bt, ai, side="right")) - 1
        if p >= 0:
            for w, bk in ((nd.R5, "bench5"), (nd.R20, "bench20")):
                k = p + w
                r[bk] = round((bc[k] / bc[p] - 1) * 100, 2) \
                    if k < len(bc) else None
    return rows


# ── ⑧ 聚合：分桶 × 前向收益 × 超额 × 左尾 ──────────────────
BUCKETS = [("ge90", 90, 1e9), ("85_90", 85, 90), ("80_85", 80, 85),
           ("70_80", 70, 80), ("lt70", -1e9, 70)]


def _stat(vals, exc):
    if not vals:
        return None
    n = len(vals)
    d = {"n": n,
         "win": round(sum(1 for x in vals if x > 0) * 100.0 / n, 1),
         "avg": round(sum(vals) / n, 2),
         "med": round(float(np.median(vals)), 2)}
    ev = [e for e in exc if e is not None]
    if len(ev) >= 3:
        d["exc"] = round(sum(ev) / len(ev), 2)
    a = np.array(vals)
    d["p5"] = round(float(np.percentile(a, 5)), 2)
    d["p10"] = round(float(np.percentile(a, 10)), 2)
    d["min"] = round(float(a.min()), 2)
    return d


def aggregate(all_rows, pass_a_meta, cal_pos):
    ev = {"generated_at": nd._now_iso(),
          "bench": BENCH, "step": STEP, "fwd": FWD}
    for w, bk in (("r5", "bench5"), ("r20", "bench20")):
        bk_out = {}
        for name, lo, hi in BUCKETS:
            sel = [r for r in all_rows if r[w] is not None
                   and not r["adj"] and lo <= (r["score"] or 0) < hi]
            st = _stat([r[w] for r in sel],
                       [r[w] - r[bk] for r in sel
                        if r.get(bk) is not None])
            if st:
                worst = sorted(sel, key=lambda r: r[w])[:5]
                st["worst"] = [{"anchor": r["anchor"], "code": r["code"],
                                "name": r["name"], w: r[w]}
                               for r in worst]
            # thinned：同票 20 交易日内去重（按锚点在交易日历的位置）
            seen, thin = {}, []
            for r in sorted(sel, key=lambda x: x["anchor"]):
                p = cal_pos.get(r["anchor"], -10 ** 9)
                last = seen.get(r["code"])
                if last is None or p - last >= FWD:
                    thin.append(r)
                    seen[r["code"]] = p
            st2 = _stat([r[w] for r in thin],
                        [r[w] - r[bk] for r in thin
                         if r.get(bk) is not None]) if thin else None
            bk_out[name] = {"raw": st, "thinned": st2}
        ev[w] = bk_out
    by_anchor = {}
    for r in all_rows:
        a = by_anchor.setdefault(r["anchor"], {"n_full": 0, "ge90": 0,
                                               "ge85": 0})
        a["n_full"] += 1
        a["ge90"] += 1 if (r["score"] or 0) >= 90 else 0
        a["ge85"] += 1 if (r["score"] or 0) >= 85 else 0
    ev["by_anchor"] = [
        {"dt": dt, "n_cand": (pass_a_meta.get(dt) or {}).get("n_cand"),
         "amt_yi": (pass_a_meta.get(dt) or {}).get("amt_yi"),
         "vol_pct": ((pass_a_meta.get(dt) or {}).get("env") or {})
         .get("vol_pct"),
         "bull": ((pass_a_meta.get(dt) or {}).get("env") or {})
         .get("bull"),
         **(by_anchor.get(dt) or {"n_full": 0, "ge90": 0, "ge85": 0})}
        for dt in sorted(pass_a_meta)]
    ev["n_rows"] = len(all_rows)
    ev["note"] = ("V3评分(78分制,去f1/v3/e1/f3/t2,q_p>35%,s_p<50%) · "
                  "样本=锚点日完整评分(cov≥0.75,sv体系性缺失豁免)全部入样"
                  " · 成熟窗口入统计 · 跨除权(adj)>0.5%剔除 · "
                  "基准=同锚点沪深300 · thinned=同票20交易日去重 · "
                  "历史锚点情绪/政策/大宗/融资明细=体系性缺失(sv豁免cov)"
                  " · n<10 不构成结论 · 影子研究不推送")
    return ev


# ── ⑨ 编排 ────────────────────────────────────────────────
def run(args):
    t0 = time.time()
    print("9DIM-BT: loading full market ...", flush=True)
    frames, tds, latest = _load_full()
    if not frames:
        print("9DIM-BT no local data")
        return
    basic = nd._basic()
    cal = _trade_days(latest)
    if len(cal) < MIN_ANCHOR_BARS + 30:
        print("9DIM-BT calendar too short: %d" % len(cal))
        return
    pos_start = max(MIN_ANCHOR_BARS, len(cal) - 1 - WINDOW_BARS)
    anchors = cal[pos_start::args.step]
    if anchors[-1] != cal[-1]:
        anchors.append(cal[-1])
    if args.anchor:
        anchors = [a for a in anchors if a == args.anchor]
        if not anchors:
            print("9DIM-BT anchor %s not in anchor list" % args.anchor)
            return
    if args.last:
        anchors = anchors[-args.last:]
    cal_pos = {a: pos_start + i * args.step for i, a in
               enumerate(cal[pos_start::args.step])}
    cal_pos[cal[-1]] = len(cal) - 1
    print("anchors=%d (%s .. %s, step=%d)"
          % (len(anchors), anchors[0], anchors[-1], args.step))

    idx_st = cal[max(0, pos_start - 350)]
    idx688 = _idx_series(nd.INDEX_CODE, idx_st)
    bench = _idx_series(BENCH, idx_st)
    hsgt = _hsgt_series(cal[max(0, pos_start - 20)])

    print("pass A: coarse per anchor ...", flush=True)
    meta = pass_a(frames, tds, basic, anchors, idx688)
    anchors = [a for a in anchors if a in meta]
    union = sorted({c for a in anchors for c in meta[a]["cand"]})
    print("union candidates=%d over %d anchors" % (len(union),
                                                   len(anchors)))

    # 样本库已有锚点（幂等）
    old_rows, done = _load_replay_rows(
        REPLAY_JSONL, replace_anchors=anchors if args.force else ()
    )
    todo = list(anchors) if args.force else [a for a in anchors if a not in done]
    print("todo anchors=%d (done=%d, force=%s)"
          % (len(todo), len(done), args.force))

    if args.dryrun:
        for dt in anchors[-3:]:
            m = meta[dt]
            print("  %s cand=%d env=%s" % (dt, m["n_cand"],
                  {k: m["env"].get(k) for k in
                   ("idx_above_ma20", "vol_pct", "amount_yi", "bull",
                    "high_vol", "report_season")}))
        print("9DIM-BT dryrun done %.1fs" % (time.time() - t0))
        return

    # API 缓存（窗口取全部锚点范围，st 月末取整防逐日失效）
    win_first = int(anchors[0]) if anchors else int(latest)
    rc_st = "%s01" % datetime.strptime(str(_cal_shift(win_first,
                                                    RC_WIN_DAYS + 10)),
                                       "%Y%m%d").strftime("%Y%m")
    fc_st = "%s01" % datetime.strptime(str(_cal_shift(win_first,
                                                    FC_WIN_DAYS + 10)),
                                       "%Y%m%d").strftime("%Y%m")
    snapshot_stores = (_snapshot_stores(args.source_snapshot, union)
                       if args.source_snapshot else None)
    fina_c = (snapshot_stores["fina_indicator"]
              if snapshot_stores and "fina_indicator" in snapshot_stores
              else _fina_bt(union) if todo else _load_cache(BT_FIN))
    rc_c = (snapshot_stores["report_rc"] if snapshot_stores
            else _rc_bt(union, rc_st) if todo else _load_cache(BT_RC))
    fc_c = (snapshot_stores["forecast"] if snapshot_stores
            else _fc_bt(union, fc_st) if todo else _load_cache(BT_FC))

    new_rows = []
    for k, dt in enumerate(todo):
        t1 = time.time()
        prev5 = cal[cal_pos[dt] - 5] if cal_pos[dt] >= 5 else None
        rows = score_anchor(dt, meta[dt], frames, tds, basic, prev5,
                            fina_c, rc_c, fc_c, hsgt, snapshot_stores)
        n90 = sum(1 for r in rows if (r["score"] or 0) >= 90)
        print("  [%d/%d] %s scored=%d ge90=%d  %.1fs"
              % (k + 1, len(todo), dt, len(rows), n90,
                 time.time() - t1), flush=True)
        new_rows.extend(rows)
        if new_rows and (k + 1) % 5 == 0:
            _atomic_rewrite(REPLAY_JSONL, old_rows + new_rows)
    _atomic_rewrite(REPLAY_JSONL, old_rows + new_rows)

    all_rows = old_rows + new_rows
    fill_forward(all_rows, frames, tds, bench)
    _atomic_rewrite(REPLAY_JSONL, all_rows)
    ev = aggregate(all_rows, meta, cal_pos)
    nd._write_json(REPLAY_EVAL, ev)

    print("\n=== 9DIM-BT summary (r20, raw) ===")
    for name, _lo, _hi in BUCKETS:
        st = (ev["r20"][name] or {}).get("raw")
        if st:
            print("  %-6s n=%-5d win=%-6s avg=%-7s exc=%-7s p5=%-7s "
                  "min=%s" % (name, st["n"], st["win"], st["avg"],
                              st.get("exc"), st["p5"], st["min"]))
    print("9DIM-BT done: rows=%d anchors=%d  %.1fs"
          % (len(all_rows), len(meta), time.time() - t0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=STEP)
    ap.add_argument("--last", type=int, default=0)
    ap.add_argument("--anchor", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dryrun", action="store_true")
    ap.add_argument("--source-snapshot", default="",
                    help="使用 staging as-of 完整载荷，禁止公网回源")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
