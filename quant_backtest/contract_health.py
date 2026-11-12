# -*- coding: utf-8 -*-
"""契约体检 —— 按 data_contracts 的容差逐表核新鲜度，红项归因到「谁的问题」。

与已有的 data_health.py 分工（别混）
------------------------------------
  data_health.py        : 存量盘点（文件在不在、列对不对、完整性）。980 行，10-30 写，
                          覆盖 12 个 datamart 别名，容差全局写死 1/3 天，
                          **没有任何板块表**，且从未接过流水线/告警。
                          本次事故正是从它的覆盖缝里漏掉的。
  contract_health.py    : 契约体检（本文件）。覆盖数据湖全部表，容差按表定制
                          （季度财务 130 天 vs 日频 1 天），带责任方与下游消费方。

状态词表（分清楚才不会毁掉报警可信度）
--------------------------------------
  ok          容差内
  late        超容差 1~2 个交易日
  dead        超容差 ≥3 个交易日           ← 该报警
  absent      目录无分片，表从未落地        ← 该报警（登记了却没数据）
  reader_gap  有数据但读取层日期列读不出    ← 该报警（消费者静默拿到空）
  n/a         维度/字典表，无日期概念       ← 不报警
  unknown     算不出（日历缺失等）

判据（硬约束）
--------------
  · 只认「表内业务日期 asof」和「上游行数」，不认 rc、不认 mtime。
  · 落后按【交易日】算。
  · 快路径只读末两片的日期列（0.03s/表）；只有疑似红项才全片复核。

用法
----
  python contract_health.py                  # 体检并打印
  python contract_health.py --probe           # 红项探测上游：断供还是我们漏取
  python contract_health.py --all             # 打印全部表
  python contract_health.py --only ts_daily,tdx_sector_daily
  python contract_health.py --refresh-consumers
  python contract_health.py --strict          # 有 dead 项时退出码 2

退出码：0 = 跑完（红绿都算跑完）；1 = 体检自身崩溃；2 = --strict 且有 dead。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import data_contracts as DC  # noqa: E402

REPORTS = os.path.join(HERE, "reports")
MANIFEST = os.path.join(HERE, "data", "tdx", "_manifest.json")
UPSTREAM = "http://rookie.xtcloud-tech.com:48088"
CONS_CACHE = os.path.join(HERE, "reports", "_consumers_cache.json")
SKIP_DIRS = ("__pycache__", ".git", "data", "reports", "h5_daily",
             "node_modules", ".workbuddy", "logs")
_DATE_KEYS = ("trade_date", "cal_date", "end_date", "ann_date", "date", "datetime",
              "time", "pub_time", "ipo_date", "surv_date", "week_date", "break_time")
_TBL_RE = re.compile(r"\b((?:ts|tdx|stock|dknife)_[a-z0-9_]+)\b")


# ── 日期归一：兼容 '20261022' / '2026-10-23 13:48:00' / 月频 '2026-10' ──
def _norm(v):
    s = str(v)
    if len(s) >= 10 and s[4] == "-":
        s = s[:10].replace("-", "")
    else:
        s = s[:8]
    if len(s) == 8 and s.isdigit():
        return s
    # 月频/季频：'2026-10'、'202610' 一律补成当月 1 号。
    # 踩过：ts_cn_cpi 的日期列是 month（值 '2026-10'），_norm 只认 8 位数字，
    # 于是整表 asof 算成空 → 被判 absent → 再被归因成「账实不符」，一次误报 14 张。
    t = str(v).strip()
    if len(t) >= 7 and t[4] == "-" and t[:4].isdigit() and t[5:7].isdigit():
        return t[:4] + t[5:7] + "01"
    if len(t) == 6 and t.isdigit():
        return t + "01"
    return None


def _schema(tbl):
    import pyarrow.parquet as pq
    import tdx_read as T
    parts = T._parts(tbl)
    if not parts:
        return None
    try:
        return list(pq.read_schema(str(parts[0])).names)
    except Exception:
        return None


def _resolve_dcol(tbl, cols):
    """定这张表该用哪一列当业务日期。返回 (列名|None, 是否读取层原始猜错)。"""
    import tdx_read as T
    if not cols:
        return None, False
    hint = DC.DATE_COL_HINT.get(tbl)
    guessed = T.date_col_of(tbl)
    if hint and hint in cols:
        return hint, (hint != guessed)
    if guessed in cols:
        return guessed, False
    for k in _DATE_KEYS:
        if k in cols:
            return k, True
    return None, False


def _fast_asof(tbl, dcol):
    """只读末两片的日期列。实测末片 max == 全片 max（ts_daily 0.03s vs 6.05s）。"""
    import pandas as pd
    import tdx_read as T
    parts = [str(x) for x in T._parts(tbl)]
    if not parts or not dcol:
        return None
    for p in parts[-2:][::-1]:
        try:
            s = pd.read_parquet(p, columns=[dcol])[dcol].astype(str)
        except Exception:
            continue
        vals = [v for v in (_norm(x) for x in s) if v]
        if vals:
            return max(vals)
    return None


def _full_asof(tbl, dcol):
    import pandas as pd
    import tdx_read as T
    best = None
    for p in [str(x) for x in T._parts(tbl)]:
        try:
            s = pd.read_parquet(p, columns=[dcol])[dcol].astype(str)
        except Exception:
            continue
        for v in (_norm(x) for x in s):
            if v and (best is None or v > best):
                best = v
    return best


def _shrink(tbl, dcol, day):
    """asof 当天的行数只剩历史中位的一半以下 = 数据萎缩（假绿）。

    为什么必须有这条（2026-11-12 实证）
    ----------------------------------
    tdx_sector_daily 不是 10-22 突然停的。**10-13 起每天只剩 3 行**（此前 1119 行），
    上游每天照常写入、_fetched_at 一直很新、日期一直在前走。所有只看
    「最后日期 / 有没有新行 / rc」的检查全部通过 —— 一道防线都没响。
    真实缺口是 21 个交易日，表面上只缺 14 个，前面 8 天里有 3 个板块在「陪着走」。

    判据：**asof 那一天**的行数 / 末片各日行数中位数。低于 50% 报警。
    · 必须按 asof 取，不能取末片 max：分片不按日期排序，末片最大日期
      可能是 2021 年某天（ts_top10_floatholders 就这么误报过）。
    · asof 不在末片里就放弃判断（宁可漏报，不可误报）。
    · 门槛 med>=10 排除「一天一行」的表（宏观/日历）。
    """
    import pandas as pd
    import tdx_read as T
    parts = [str(x) for x in T._parts(tbl)]
    if not parts or not dcol or not day:
        return None
    try:
        # 分片不按日期排序，asof 那天可能不在最后一片里（_fast_asof 也是读末两片
        # 才拿到的），所以末两片都试一遍。
        s = None
        for p in parts[-2:][::-1]:
            try:
                t = pd.read_parquet(p, columns=[dcol])[dcol].astype(str).map(_norm)
            except Exception:
                continue
            if day in t.dropna().values:
                s = t
                break
        if s is None:
            return None
    except Exception:
        return None
    v = s.dropna().value_counts()
    if len(v) < 5 or day not in v.index:
        return None
    med = float(v.median())
    if med < 10:
        return None
    rows = int(v[day])
    ratio = round(rows / med, 3)
    return {"day": day, "rows": rows, "median": int(med), "ratio": ratio,
            "shrunk": ratio < 0.5}


# ── 上游探针 ─────────────────────────────────────────────
_UP_CACHE = os.path.join(REPORTS, "_upstream_cache.json")
_UP_TTL = 1800


def _up_load():
    try:
        return json.load(open(_UP_CACHE, encoding="utf-8"))
    except Exception:
        return {}


def _up_save(c):
    try:
        os.makedirs(REPORTS, exist_ok=True)
        with open(_UP_CACHE, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False)
    except Exception:
        pass


def _upstream(tbl, timeout=12, use_cache=True):
    """探上游：拿到「上游这批数据的最新业务日」。

    判据为什么是这个（2026-11-12 踩过两次）
    ----------------------------------------
    ① 上游 API **不返回** _fetched_at。早先硬编码 row[8] 当投喂时间，
       实际取到的是第 9 个业务列（ts_rt_min 的 time），看着像时间戳，
       于是一次误判整套归因都跟着歪。
    ② 用 total 差判「我方漏取」也不可靠：历史上漏过的量会永久差着，
       tdx_sector_daily 上游 374 万 vs 本地 361 万，差 12 万看着像我们的错，
       实际是上游 10-22 就停了 —— 差异是长期漏取的存量，不是当下的缺口。
    ③ 唯一可靠的问法：**上游这批数据 date_col 的首行值**（实测倒序）。
       ts_daily 首行 20261111（=基准日）、tdx_sector_daily 首行 20261022
       （=断供日）、ts_rt_min 首行 当天 10:59（还活着）—— 一目了然。
    """
    c = _up_load() if use_cache else {}
    hit = c.get(tbl)
    if hit and time.time() - hit.get("t", 0) < _UP_TTL:
        return hit["d"]

    url = "%s/api/data/table/%s?%s" % (
        UPSTREAM, tbl, urllib.parse.urlencode({"limit": 1, "offset": 0}))
    req = urllib.request.Request(url, headers={"User-Agent": "contract-health/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        out = ({"missing": True} if e.code in (400, 404)
               else {"err": "HTTP %s" % e.code})
        c[tbl] = {"t": time.time(), "d": out}
        _up_save(c)
        return out
    except Exception as e:
        return {"err": str(e)[:80]}          # 网络问题不缓存，下次重试

    cols = d.get("columns") or []
    dcol = d.get("date_col")
    if dcol not in cols:
        dcol = DC.DATE_COL_HINT.get(tbl) or next(
            (k for k in _DATE_KEYS if k in cols), None)
    row = (d.get("rows") or [None])[0]
    first = None
    if isinstance(row, list) and dcol in cols:
        first = _norm(row[cols.index(dcol)])
    out = {"total": d.get("total"), "date_col": dcol, "first": first,
           "name_cn": d.get("name_cn"), "n_cols": len(cols)}
    c[tbl] = {"t": time.time(), "d": out}
    _up_save(c)
    return out


CAUSES = ("our_gap", "upstream_dead", "upstream_frozen",
          "local_built", "no_upstream_table", "ledger_mismatch")


def attribute(rec, open_days=None, base=None):
    """归因：四种结论修法完全相反，混在一起就是「天天报警天天没人能动」。

      our_gap            上游比我们新        → 自己补拉
      upstream_dead      上游和我们一样停在同一天 → 上游断供，等或切源
      upstream_frozen    上游自己停在 >130 交易日前 → 存量冻结，不该报警
      local_built        上游没这张表但本地有分片 → 本方自建表，设计如此
      no_upstream_table  上游没这张表且本地也没分片 → 契约写错了，改名单
      ledger_mismatch    manifest 记账有量、实际无分片 → 账实不符，先修账
    """
    u = _upstream(rec["table"])
    rec["upstream"] = u

    if u.get("missing"):
        # 上游没这张表：本地有分片的是自建表（tdx_*/ts_emotion_* 等），
        # 本地也没分片的才是契约写错了表名（ts_limit_list_d 判例）。
        local = rec.get("rows") or 0
        rec["cause"] = "local_built" if local > 0 else "no_upstream_table"
        return rec
    if u.get("err") and u.get("total") is None:
        rec["cause"] = "upstream_unreachable"
        return rec

    if rec["status"] == "absent" and (rec.get("rows") or 0) > 0:
        rec["cause"] = "ledger_mismatch"
        return rec

    up_first = u.get("first")
    rec["upstream_asof"] = up_first
    local_asof = rec.get("asof")
    up_lag = (DC.lag_in_trade_days(open_days, up_first, base)
              if (up_first and open_days and base) else None)
    rec["upstream_lag"] = up_lag

    if not (up_first and local_asof):
        rec["cause"] = "unknown"
        return rec

    # 判据只有一条主线：**上游和本地谁更新**。
    # 早先按「上游是否到基准日」判，把「上游到 10-31、本地停在 1995」判成了断供，
    # 实际是本地二十年没拉（ts_stk_week_month_adj 判例）。
    if up_first > local_asof:
        rec["cause"] = "our_gap"          # 上游比我们新 → 自己补拉
        return rec

    # 上游不比我们新：看【双方一起停在哪】
    if up_lag is not None and up_lag > 130 and (rec.get("lag") or 0) > 130:
        rec["cause"] = "upstream_frozen"  # 双方都停在很久以前 = 一次性快照
    elif up_lag is not None and up_lag > 3:
        rec["cause"] = "upstream_dead"    # 一起停在最近某天 = 上游断供
    else:
        rec["cause"] = "no_gap"           # 上游也没更多数据（本地略新或持平）
    return rec


# ── 下游消费方（正则一次扫完 + 缓存）──────────────────────────────────
def build_consumers(tables, refresh=False, ttl=7 * 86400):
    """返回 {表: [py 文件]}。

    缓存必须覆盖【全部表】：曾因为只用一个 2 张表的子集调用把缓存覆盖了，
    导致后续全量体检的「下游消费方」全是 0，而这一列正是判断影响面的依据。
    所以缓存带 scope，请求的表不在 scope 内就重扫全量。
    """
    want = set(tables)
    try:
        all_tables = set((json.load(open(MANIFEST, encoding="utf-8"))
                          .get("tables") or {}).keys())
    except Exception:
        all_tables = set(want)
    if not refresh and os.path.exists(CONS_CACHE):
        try:
            c = json.load(open(CONS_CACHE, encoding="utf-8"))
            scope = set(c.get("scope") or [])
            if time.time() - c.get("t", 0) < ttl and want <= scope:
                m = c.get("m") or {}
                return {t: m.get(t, []) for t in want}
        except Exception:
            pass
    hits = {t: [] for t in all_tables | want}
    roots = [HERE, os.path.join(HERE, "..", "portal")]
    seen = set()
    for base in roots:
        base = os.path.abspath(base)
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(root, fn)
                if path in seen:
                    continue
                seen.add(path)
                try:
                    txt = open(path, encoding="utf-8", errors="ignore").read()
                except Exception:
                    continue
                for name in set(_TBL_RE.findall(txt)):
                    if name in hits:
                        hits[name].append(os.path.relpath(path, HERE))
    try:
        json.dump({"t": time.time(), "scope": sorted(hits), "m": hits},
                  open(CONS_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return {t: hits.get(t, []) for t in want}


# ── 主流程 ────────────────────────────────────────────────────────────
def check(probe=True, only=None, now=None, refresh_consumers=False):
    now = now or dt.datetime.now()
    t0 = time.time()

    open_days = DC.load_open_days()
    cd = DC.closed_days(open_days, now)
    base = cd[0] if cd else None

    man = json.load(open(MANIFEST, encoding="utf-8"))
    entries = man.get("tables") or man
    tables = sorted(entries.keys())
    if only:
        keep = {x.strip() for x in only.split(",") if x.strip()}
        tables = [t for t in tables if t in keep]

    cons = build_consumers(tables, refresh=refresh_consumers)

    def _one(tbl):
        c = DC.contract(tbl)
        cols = _schema(tbl)
        rec = {"table": tbl, "cn": c["cn"], "want": base, "allowed": c["lag"],
               "tier": c["tier"], "review": c["review"], "owner": c["owner"],
               "active_fetch": c["active_fetch"], "why": c["why"],
               "rows": entries.get(tbl, {}).get("rows"),
               "n_consumers": len(cons.get(tbl, [])),
               "consumers": cons.get(tbl, [])[:6]}

        if c["tier"] == "GHOST":
            # 上游确认无此表：报 dead/absent 都是噪音，要报「契约错」
            rec.update({"asof": None, "lag": None, "status": "contract_bug",
                        "date_col": None, "cause": "no_upstream_table",
                        "superseded_by": c["superseded_by"]})
            return rec
        if cols is None:
            rec.update({"asof": None, "lag": None, "status": "absent", "date_col": None})
            return rec
        dcol, mism = _resolve_dcol(tbl, cols)
        rec["date_col"] = dcol
        rec["reader_mismatch"] = mism
        if dcol is None:
            # 没有日期列：维度表算正常，其余是读取层无解
            rec.update({"asof": None, "lag": None,
                        "status": "n/a" if c["lag"] is None else "reader_gap"})
            return rec
        if c["lag"] is None:                      # 维度表：算 asof 只为展示
            rec.update({"asof": _fast_asof(tbl, dcol), "lag": None, "status": "n/a"})
            return rec

        # 快路径先出结论。分片【不按日期排序】（远端 append 到旧分片里），
        # 所以末片 max 可能小于全片 max —— 但只在【贴边】时复核。
        # 落后十几天的表复核也还是落后，全扫一遍 tdx_sector_min（2567 万行）
        # 能把整个体检拖到 80s。实测：只在 lag∈(容差+2, 容差+5] 时全扫。
        asof = _fast_asof(tbl, dcol)
        lag = DC.lag_in_trade_days(open_days, asof, base)
        nrows = entries.get(tbl, {}).get("rows") or 0
        # 复核条件：贴边必查；落后多的只查小表（大表全扫一次能拖 10s，
        # 而落后 20 天的表复核完还是落后，不值得）。
        edge = (lag is not None and c["lag"] + 2 < lag <= c["lag"] + 5)
        cheap = (lag is not None and lag > c["lag"] + 2 and nrows <= 2_000_000)
        if edge or cheap:
            full = _full_asof(tbl, dcol)
            if full and (asof is None or full > asof):
                asof, rec["how"] = full, "full"
                lag = DC.lag_in_trade_days(open_days, asof, base)
        if asof is None:
            status = "absent"
        elif lag is None:
            status = "unknown"
        elif lag <= c["lag"]:
            status = "ok"
        elif lag <= c["lag"] + 2:
            status = "late"
        else:
            status = "dead"
        rec.update({"asof": asof, "lag": lag, "status": status})
        # 假绿探测：日期在走、行数塌了。只对「看着没问题」的表改状态，
        # 已经红的表也查一次，把萎缩起点挂上去（dead 的成因常常就是这个）。
        if status in ("ok", "late", "dead"):
            sh = _shrink(tbl, dcol, asof)
            if sh:
                rec["shrink"] = sh
                if sh["shrunk"] and status in ("ok", "late"):
                    rec["was_status"] = status
                    rec["status"] = "shrink"
        return rec

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(_one, tables))

    if probe:
        todo = [r for r in rows if r["status"] in ALERT + ("late",)]
        # 上游探针是纯 I/O 等待（大表 count 慢），16 路比 8 路省一半时间
        with ThreadPoolExecutor(max_workers=16) as ex:
            list(ex.map(lambda r: attribute(r, open_days, base), todo))
        # 存量冻结：上游自己也早就不写了（落后 >130 交易日）、且没有下游依赖。
        # 这类表天天报 dead 的唯一效果是让报警慢性失效 —— 单列，不占报警额度。
        # 有下游消费的一律留在 dead（例如 tdx_sector_daily 有 37 个消费方）。
        for r in todo:
            if (r.get("cause") == "upstream_frozen" and r["n_consumers"] == 0
                    and r["status"] in ("dead", "absent")):
                r["was_status"] = r["status"]
                r["status"] = "frozen"
            elif r.get("cause") == "no_upstream_table":
                # 上游没这张表、本地也没分片 = 名单写错了，不该混在「数据没来」里
                r["was_status"] = r["status"]
                r["status"] = "contract_bug"

    return {"run_at": now.strftime("%Y-%m-%d %H:%M:%S"), "base_asof": base,
            "n_tables": len(rows), "elapsed_s": round(time.time() - t0, 1),
            "counts": _counts(rows), "tables": rows}


def _counts(rows):
    c = {}
    for r in rows:
        c[r["status"]] = c.get(r["status"], 0) + 1
    return c


ALERT = ("contract_bug", "shrink", "dead", "absent", "reader_gap")


def _table(items):
    if not items:
        print("  （无）")
        return
    print("  %-22s %-9s %4s %4s %-5s %-24s %-22s %s"
          % ("表", "asof", "落后", "容差", "级", "负责方", "归因", "下游"))
    for r in items:
        up = r.get("upstream") or {}
        cause = r.get("cause") or r["status"]
        if up.get("total") is not None:
            cause += "(%s/%s)" % (up["total"], r["rows"])
        print("  %-22s %-9s %4s %4s %-5s %-24s %-22s %s"
              % (r["table"], r["asof"] or "-",
                 r["lag"] if r["lag"] is not None else "?", r["allowed"],
                 r["tier"], (r["owner"] or "")[:24], cause[:22], r["n_consumers"]))


def report(out, show_all=False):
    rows = out["tables"]
    print("契约体检 · %s   该到=%s   表数=%d   耗时=%.1fs"
          % (out["run_at"], out["base_asof"], out["n_tables"], out["elapsed_s"]))
    print("分布:", out["counts"])

    bug = [r for r in rows if r["status"] == "contract_bug"]
    if bug:
        print("\n【契约错误】%d 张（上游确认无此表 —— 不是数据没来，是名单写错了）:"
              % len(bug))
        for r in bug:
            print("  %-22s -> 应为 %-18s 负责方=%s" %
                  (r["table"], r.get("superseded_by") or "?", (r["owner"] or "")[:22]))

    bad = [r for r in rows if r["status"] in ALERT + ("late", "unknown")
           and r["status"] != "contract_bug"]
    # 分两段：近期断供（能定位到某次上游事故）vs 存量遗留（落后两个月以上）
    fresh = [r for r in bad if (r["lag"] is None) or r["lag"] <= 60]
    legacy = [r for r in bad if (r["lag"] is not None) and r["lag"] > 60]
    fresh.sort(key=lambda r: (ALERT.index(r["status"]) if r["status"] in ALERT else 9,
                              -(r["lag"] or 0)))
    print("\n【近期断供】%d 张（shrink=日期在走但行数塌了 / dead=超容差3交易日+ / "
          "absent=无分片 / reader_gap=读不出）:" % len(fresh))
    _table(fresh)
    if legacy:
        print("\n【存量遗留】%d 张（落后 60 个交易日以上，多为一次性快照表，未修）:"
              % len(legacy))
        _table(legacy)

    frozen = [r for r in rows if r["status"] == "frozen"]
    if frozen:
        print("\n【存量冻结】%d 张（上游自己也停在 >130 交易日前、且无下游 —— 只登记不报警）:"
              % len(frozen))
        print("  " + "、".join("%s(%s)" % (r["table"], r["asof"]) for r in frozen))

    if show_all:
        print("\n全部 %d 张:" % len(rows))
        for r in rows:
            print("  %-24s %-9s lag=%-6s 容差=%-5s %-10s 列=%s"
                  % (r["table"], r["asof"], r["lag"], r["allowed"],
                     r["status"], r.get("date_col")))

    passive = [r for r in rows if not r["active_fetch"] and r["n_consumers"] >= 1
               and r["status"] != "n/a"]
    passive.sort(key=lambda r: -r["n_consumers"])
    print("\n无主动拉取任务、却有下游消费的表 %d 张（新鲜完全靠上游喂）:" % len(passive))
    for r in passive[:12]:
        print("  %-22s asof=%-9s lag=%-4s 消费方%-3d %s"
              % (r["table"], r["asof"], r["lag"], r["n_consumers"],
                 ",".join(r["consumers"][:2])))


def audit_catalog(tables=None, out_path=None):
    """逐表对上游自证：这张表上游到底有没有、叫什么、日期列是什么。

    P0 必须全绿 —— 名单写错的代价是永远修不好。判例：ts_limit_list_d 上游 400、
    本地 part=0，被当「数据没来」挂了很久，实际是名字错了。
    """
    from concurrent.futures import ThreadPoolExecutor
    tables = tables or ([t for t, _cn, _l, _w in DC.P0] + sorted(DC.GHOST))

    def _one(t):
        u = _upstream(t)
        return {"table": t, "upstream": u,
                "ok": u.get("total") is not None,
                "name_cn": u.get("name_cn"), "date_col": u.get("date_col"),
                "local_date_col": DC.DATE_COL_HINT.get(t)}

    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(_one, tables))

    print("\n上游自证 · %d 张" % len(rows))
    print("  %-22s %-9s %-22s %-14s %s" % ("表", "上游", "中文名", "上游date_col", "本地hint"))
    for r in rows:
        u = r["upstream"]
        print("  %-22s %-9s %-22s %-14s %s"
              % (r["table"], "OK" if r["ok"] else ("无此表" if u.get("missing") else "探不通"),
                 (r["name_cn"] or "-")[:22], (r["date_col"] or "-"),
                 r["local_date_col"] or "-"))
    bad = [r for r in rows if not r["ok"]]
    if bad:
        print("\n上游对不上的 %d 张：%s" %
              (len(bad), "、".join(r["table"] for r in bad)))
    out_path = out_path or os.path.join(REPORTS, "catalog_audit.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"run_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                   "n": len(rows), "n_bad": len(bad), "tables": rows},
                  f, ensure_ascii=False, indent=1)
    print("JSON -> %s" % out_path)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", default=True,
                    help="死项探上游归因（默认开）")
    ap.add_argument("--no-probe", dest="probe", action="store_false",
                    help="跳过上游探测，只做本地体检")
    ap.add_argument("--audit", action="store_true",
                    help="上游自证：P0 名单逐张核实（表存在性/中文名/日期列）")
    ap.add_argument("--json", default=os.path.join(REPORTS, "contract_health.json"))
    ap.add_argument("--only", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--refresh-consumers", action="store_true")
    args = ap.parse_args(argv)

    if args.audit:
        audit_catalog()

    out = check(probe=args.probe, only=args.only,
                refresh_consumers=args.refresh_consumers)
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    report(out, show_all=args.all)
    print("\nJSON -> %s" % args.json)
    if args.strict and out["counts"].get("dead"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
