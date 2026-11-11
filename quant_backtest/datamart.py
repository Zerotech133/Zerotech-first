# -*- coding: utf-8 -*-
"""数据中台统一读层（只读）。替代消费方直读 parquet 的多入口并存。

按《数据中台架构设计.md》§6：
  read(table, start, end, codes, fields, limit) → pd.DataFrame
    · table 可为物理表名或别名（data/catalog.json aliases）
    · 路由：别名 → 真源优先级候选 → 跳过未就绪 → 按 layout 分派读取
    · 归一：代码列→ts_code、日期列→YYYYMMDD（分钟表 trade_time 保留原样）
    · 跨源补齐缺键，优先源覆盖；物理表按 catalog primary_key 保留明细
  latest(table)  本地最新日期（新鲜度探测）
  ready(table)   别名/物理表是否至少一个候选可读
  columns(table) 归一化 schema
  explain(table) 路由明细（诊断用）

布局分派：
  part          全分片 concat + 过滤（tdx 源委托 tdx_read，不重写）
  by_code       <code>.parquet 逐文件（6 位前缀代码自动通配）
  by_code_month <code>_<YYYYMM>.parquet 按 codes×月份命中
  by_date       d_YYYYMMDD.parquet 按日分片
  by_period     p_YYYYMMDD.parquet 按报告期分片，查询按数据日期列
  sqlite        tdx 信息库三表委托 tdx_read.read_info（参数化 SQL）

done 纪律：manifest done=false 或缺失的表拒绝读（防读到不完整数据），
别名自动降级到下一候选。
"""
import glob
import hashlib
import json
import logging
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

import pandas as pd

import tdx_read as tdxr
from warehouse_read_guard import PublicationPending, read_resources, publication_stamp

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(HERE, "data", "catalog.json")

_cat_cache = {"mtime": None, "data": None}
_LOG = logging.getLogger(__name__)
_READ_CATALOG = ContextVar('warehouse_read_catalog', default=None)
_PUBLICATION_STAMPS = {}


def _publication_roots(cat, names):
    """按 catalog 与实际委托路径锁住全部候选，未就绪源也不越过 pending。"""
    roots = []
    for name in names:
        e = cat.get('tables', {}).get(name) or {}
        if e.get('layout') == 'pg':
            continue  # 章程 §3：外部库无本地目录，发布锁不适用（库内事务自保证）
        if e.get('store'):
            roots.append(Path(e['store']).parent)
        if e.get('manifest'):
            roots.append(Path(e['manifest']).parent)
        if e.get('layout') == 'sqlite':
            roots.append(Path(tdxr.INFO_DB).parent)
        if e.get('layout') == 'part' and name.startswith('ts_'):
            # 原 latest 快路径会委托 TDX；锁的范围按实际 OUT 而非固定项目根。
            roots.append(tdxr.OUT)
    return roots


@contextmanager
def _publication_read(tables):
    cat = _catalog()
    names = []
    for table in tables:
        alias = cat.get('aliases', {}).get(table)
        names.extend((alias.get('resolves') or []) if alias else [table])
    with read_resources(_publication_roots(cat, names)) as roots:
        # 先获锁并排除 pending，再检查缓存版本，缓存不能绕过发布保护。
        catalog_version = (str(Path(CATALOG).resolve()), hashlib.sha256(
            json.dumps(cat, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest())
        changed = _PUBLICATION_STAMPS.get('catalog') != catalog_version
        _PUBLICATION_STAMPS['catalog'] = catalog_version
        for root in roots:
            stamp = publication_stamp(root)
            key = os.path.normcase(str(root))
            if _PUBLICATION_STAMPS.get(key) != stamp:
                changed = True
                _PUBLICATION_STAMPS[key] = stamp
        if changed:
            _latest_cache.clear()
            _prev_cache.clear()
            _fresh_cache.update(ts=0.0, data=None)
            _trade_cal_cache.update(ts=0.0, days=None)
        token = _READ_CATALOG.set(cat)
        try:
            yield
        finally:
            _READ_CATALOG.reset(token)


def _guard_table(function):
    @wraps(function)
    def wrapped(table, *args, **kwargs):
        with _publication_read([table]):
            return function(table, *args, **kwargs)
    return wrapped


def _guard_freshness(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _publication_read(['trade_cal'] + [row[0] for row in FRESHNESS_SPEC]):
            return function(*args, **kwargs)
    return wrapped


def _catalog():
    """catalog.json 读取（按 mtime 缓存；build_catalog 原子写保证完整性）。"""
    snapshot = _READ_CATALOG.get()
    if snapshot is not None:
        return snapshot
    try:
        mt = os.path.getmtime(CATALOG)
    except OSError:
        return {"tables": {}, "aliases": {}}
    if _cat_cache["mtime"] != mt or _cat_cache["data"] is None:
        try:
            with open(CATALOG, encoding="utf-8") as f:
                _cat_cache["data"] = json.load(f)
            _cat_cache["mtime"] = mt
        except PublicationPending:
            raise
        except Exception:
            return {"tables": {}, "aliases": {}}
    return _cat_cache["data"]


def _manifest(mpath):
    if not mpath or not os.path.exists(mpath):
        return {}
    try:
        with open(mpath, encoding="utf-8") as f:
            return json.load(f)
    except PublicationPending:
        raise
    except Exception:
        return {}


def _resolve(table):
    """别名/物理表名 → 真源优先级升序的物理表列表；未知表返回 []。"""
    cat = _catalog()
    a = cat.get("aliases", {}).get(table)
    if a:
        return list(a.get("resolves") or [])
    if table in cat.get("tables", {}):
        return [table]
    return []


def _ready_one(tbl):
    """物理表就绪 = catalog 登记 + manifest done（无 manifest 视为目录即真）。"""
    e = _catalog().get("tables", {}).get(tbl)
    if not e:
        return False
    mp = e.get("manifest")
    if not mp:
        return bool(e.get("done"))
    mf = _manifest(mp)
    if tbl in mf:
        return bool(mf[tbl].get("done"))
    return bool(e.get("done"))


@_guard_table
def ready(table):
    """别名/物理表是否至少一个候选就绪。"""
    return any(_ready_one(t) for t in _resolve(table))


@_guard_table
def explain(table):
    """路由明细：[{name, ready, priority, source, store, layout, freshness,
    live_latest}]。live_latest 为现算最新日期（freshness 是 build_catalog
    扫描快照，增量同步后现算值更准）。"""
    out = []
    for t in _resolve(table):
        e = _catalog().get("tables", {}).get(t) or {}
        out.append({"name": t, "ready": _ready_one(t),
                    "priority": e.get("priority"),
                    "source": e.get("source"), "store": e.get("store"),
                    "layout": e.get("layout"),
                    "freshness": e.get("freshness"),
                    "live_latest": _latest_one(t, e),
                    "deprecated": e.get("deprecated", False)})
    return out


@_guard_table
def columns(table):
    """归一化 schema：代码列统一为 ts_code。"""
    for t in _resolve(table):
        e = _catalog().get("tables", {}).get(t) or {}
        if e.get("schema"):
            cc = e.get("code_col")
            return ["ts_code" if c == cc else c for c in e["schema"]]
    return []


# ── 读取器（按 layout 分派）──────────────────────────────────
def _norm_day(v):
    """日期归一委托 tdx_read；额外消化 'YYYYMMDD HH:MM:SS' 形态
    （分钟表读路径 start/end 常带时间，原 _norm_day 无法归一导致过滤全失）。"""
    if isinstance(v, str) and " " in v:
        v = v.split(" ", 1)[0]
    return tdxr._norm_day(v)


def _code_candidates(codes):
    """精确代码与 6 位前缀分离（同 tdx_read._norm_codes）。"""
    return tdxr._norm_codes(codes)


def _pick_fields(df, fields, ccol, dcol):
    if not fields:
        return df
    want = set(fields) | {c for c in (ccol, dcol) if c}
    keep = [c for c in df.columns if c in want]
    return df[keep] if keep else df.iloc[:, :0]


def _read_columns(e, fields):
    """列裁剪下推：fields ∪ key 列与 catalog schema 交集。
    仅 fields 显式指定时生效（fields=None 全列读）；schema 未知 → None。
    """
    schema = e.get("schema")
    if not schema or not fields:
        return None
    key = {c for c in (e.get("code_col"), e.get("date_col")) if c}
    key.update(e.get("primary_key") or [])
    key.update(c for c in ("ts_code", "con_code", "code", "symbol") if c in schema)
    want = set(fields)
    keep = [c for c in schema if c in key or c in want]
    # catalog 的抽样 schema 不应屏蔽后续分片新增的请求列。
    keep.extend(c for c in fields if c not in keep)
    keep.extend(c for c in key if c not in keep)
    return keep or None


def _parquet_filters(pf, dcol, ccol, start, end, codes):
    """字符串日期/代码谓词下推；未知日期形态保守留给读后过滤。"""
    import pyarrow as pa
    schema = pf.schema_arrow
    common = []
    if dcol in schema.names and (start is not None or end is not None):
        j = schema.names.index(dcol)
        formats = set()
        dtype = schema.field(dcol).type
        if pa.types.is_string(dtype) or pa.types.is_large_string(dtype):
            for i in range(pf.metadata.num_row_groups):
                st = pf.metadata.row_group(i).column(j).statistics
                if st is None or not st.has_min_max:
                    formats.add(None)
                    continue
                for v in (st.min, st.max):
                    v = str(v)
                    formats.add("compact" if re.fullmatch(r"\d{8}", v) else
                                "dashed" if re.match(r"^\d{4}-\d{2}-\d{2}(?:$|[ T])", v) else None)
        if formats == {"compact"}:
            if start is not None:
                common.append((dcol, ">=", _norm_day(start)))
            if end is not None:
                common.append((dcol, "<=", _norm_day(end)))
        elif formats == {"dashed"}:
            from datetime import datetime, timedelta
            if start is not None:
                day = datetime.strptime(_norm_day(start), "%Y%m%d")
                common.append((dcol, ">=", day.strftime("%Y-%m-%d")))
            if end is not None:
                day_after = datetime.strptime(_norm_day(end), "%Y%m%d") + timedelta(days=1)
                common.append((dcol, "<", day_after.strftime("%Y-%m-%d")))
    exact, prefix = _code_candidates(codes)
    branches = []
    # 与 tdx_read 的多代码列匹配保持一致（例如指数及其 con_code 成分）。
    for col in dict.fromkeys((ccol, "ts_code", "con_code", "code", "symbol")):
        if col not in schema.names:
            continue
        dtype = schema.field(col).type
        if not (pa.types.is_string(dtype) or pa.types.is_large_string(dtype)):
            continue
        if exact:
            branches.append(common + [(col, "in", [str(c) for c in exact])])
        for c in prefix or []:
            branches.append(common + [(col, ">=", c), (col, "<=", c + "~")])
    if branches:
        return branches
    return common or None


def _concat_pq(paths, cols=None, dcol=None, ccol=None, start=None, end=None, codes=None):
    """多文件限列、限行读取；schema 漂移时仅选当前文件存在的请求列。"""
    if not paths:
        return pd.DataFrame()

    def _one(p):
        try:
            from pyarrow.parquet import ParquetFile
            pf = ParquetFile(p)
            c = ([name for name in cols if name in pf.schema_arrow.names]
                 if cols is not None else None)
            filters = _parquet_filters(pf, dcol, ccol, start, end, codes)
            try:
                return pd.read_parquet(p, columns=c, filters=filters)
            except PublicationPending:
                raise
            except Exception:
                return pd.read_parquet(p, columns=c)
        except PublicationPending:
            raise
        except Exception:
            return None

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        dfl = list(ex.map(_one, dict.fromkeys(paths)))
    dfl = [d for d in dfl if d is not None]
    return pd.concat(dfl, ignore_index=True) if dfl else pd.DataFrame()


_STATS_CACHE: dict = {}
_STATS_CACHE_MAX = 20000


def _part_stats_days(p, dcol):
    """pyarrow row-group 统计 → 分片 (min_day, max_day)，零数据读。
    任何 row-group 无统计或列缺失返回 None（调用方放弃剪枝）。

    2026-11-08 AIDE 性能：进程内按 (路径, mtime, size, 列) 缓存。
    单次个股查询要扫几千个分片，逐个重开文件读元数据是主耗时项
    （实测 8749 分片 / 8789 次调用 / 8.6 秒）。分片一经发布不再改写，
    mtime 或 size 变化即失效重读，判据与结果均不变。
    """
    try:
        _st = os.stat(p)
        _ck = (p, _st.st_mtime_ns, _st.st_size, dcol)
    except OSError:
        _ck = None
    if _ck is not None:
        _hit = _STATS_CACHE.get(_ck)
        if _hit is not None:
            return _hit[0]
    val = _part_stats_days_uncached(p, dcol)
    if _ck is not None:
        if len(_STATS_CACHE) >= _STATS_CACHE_MAX:
            _STATS_CACHE.clear()
        _STATS_CACHE[_ck] = (val,)
    return val


def _part_stats_days_uncached(p, dcol):
    try:
        import pyarrow.parquet as pq
        f = pq.ParquetFile(p)
        names = f.schema_arrow.names
        if dcol not in names:
            return None
        j = names.index(dcol)
        md = f.metadata
        mn = mx = None
        for i in range(md.num_row_groups):
            st = md.row_group(i).column(j).statistics
            if st is None:
                return None
            a, b = st.min, st.max
            if a is None or b is None:
                continue
            a = str(a)[:10].replace("-", "")
            b = str(b)[:10].replace("-", "")
            mn = a if mn is None or a < mn else mn
            mx = b if mx is None or b > mx else mx
        return (mn, mx) if mn else None
    except PublicationPending:
        raise
    except Exception:
        return None


_DNAME_RE = re.compile(r"(\d{8})")


def _fname_day(p):
    """日分片文件名里的 YYYYMMDD；命名不合规返回 None（调用方保留该分片）。"""
    m = _DNAME_RE.search(os.path.basename(p))
    return m.group(1) if m else None


def _read_part(store, dcol, ccol, start, end, codes, fields, cols=None,
               pattern="part_*.parquet", name_prune=False):
    parts = sorted(glob.glob(os.path.join(store, pattern)))
    if not parts:
        return pd.DataFrame()
    # 2026-11-08 AIDE 性能：by_date 日分片文件名自带日期，可先按名剪枝，
    # 只保留「文件名日期 >= start」的分片（8749 → 数十个）。语义安全前提：
    # 补采只会把旧数据写进新文件，不会把新数据写进旧文件，故「早于 start 的
    # 分片」必不含目标区间数据；end 一侧不按名剪（新分片仍可能含区间内补采
    # 行），照旧由下方读后过滤兜底。解析不出日期的分片一律保留。
    if name_prune and start is not None:
        s_name = _norm_day(start)[:8]
        kept_by_name = []
        for p in parts:
            d = _fname_day(p)
            if d is None or d >= s_name:
                kept_by_name.append(p)
        parts = kept_by_name or parts
    # 分片剪枝：row-group 统计零数据读；任一分片统计缺失则放弃剪枝全读
    if dcol and (start is not None or end is not None):
        s0 = _norm_day(start)[:8] if start is not None else None
        e0 = _norm_day(end)[:8] if end is not None else None
        kept = []
        for p in parts:
            st = _part_stats_days(p, dcol)
            if st is None:
                kept = parts
                break
            mn, mx = st
            if s0 and mx < s0:
                continue
            if e0 and mn > e0:
                continue
            kept.append(p)
        parts = kept
    df = _concat_pq(parts, cols, dcol, ccol, start, end, codes)
    if dcol and dcol in df.columns and (start is not None or end is not None):
        s = df[dcol].astype(str).str[:10].str.replace("-", "", regex=False).str[:8]
        if start is not None:
            df = df[s >= _norm_day(start)]
            s = df[dcol].astype(str).str[:10].str.replace("-", "", regex=False).str[:8]
        if end is not None:
            df = df[s <= _norm_day(end)]
    df = tdxr._apply_code_filter(df, None, codes)
    df = _pick_fields(df, fields, ccol, dcol)
    if dcol and dcol in df.columns:
        df = df.sort_values(dcol)
    return df.reset_index(drop=True)


def _read_by_period(store, dcol, ccol, start, end, codes, fields, cols=None):
    # 文件名是报告期，不能按公告日请求直接裁剪文件名。
    return _read_part(store, dcol, ccol, start, end, codes, fields, cols,
                      pattern="p_*.parquet")


def _filename_code(code):
    return str(code).replace(".", "_").replace("/", "_")


def _read_by_code(store, dcol, ccol, start, end, codes, fields, cols=None):
    exact, prefix = _code_candidates(codes)
    if exact is None and prefix is None:
        files = sorted(glob.glob(os.path.join(store, "*.parquet")))
    else:
        files = []
        for c in exact or []:
            for stem in dict.fromkeys((str(c), _filename_code(c))):
                f = os.path.join(store, "%s.parquet" % stem)
                if os.path.exists(f):
                    files.append(f)
        for c in prefix or []:
            files.extend(sorted(glob.glob(
                os.path.join(store, "%s*.parquet" % c))))
    if not files:
        return pd.DataFrame()
    df = _concat_pq(files, cols, dcol, ccol, start, end, codes)
    if dcol and dcol in df.columns and (start is not None or end is not None):
        s = df[dcol].astype(str).str[:10].str.replace("-", "", regex=False).str[:8]
        if start is not None:
            df = df[s >= _norm_day(start)]
            s = df[dcol].astype(str).str[:10].str.replace("-", "", regex=False).str[:8]
        if end is not None:
            df = df[s <= _norm_day(end)]
    df = tdxr._apply_code_filter(df, None, codes)
    df = _pick_fields(df, fields, ccol, dcol)
    if dcol and dcol in df.columns:
        df = df.sort_values(dcol)
    return df.reset_index(drop=True)


def _months(start, end):
    """[start, end] YYYYMMDD → 月份列表；start 缺省 → None=全部月份；
    end 缺省 → 当前月。"""
    if start is None:
        return None
    s = _norm_day(start)[:6]
    from datetime import datetime as _dt
    em = _norm_day(end)[:6] if end is not None else \
        _dt.now().strftime("%Y%m")
    y, m = int(s[:4]), int(s[4:6])
    out = []
    while True:
        ym = "%04d%02d" % (y, m)
        out.append(ym)
        if ym >= em:
            break
        m += 1
        if m > 12:
            y, m = y + 1, 1
        if len(out) > 600:                     # 防御：最多 50 年
            break
    return out


def _read_by_code_month(store, dcol, ccol, start, end, codes, fields, cols=None):
    exact, prefix = _code_candidates(codes)
    months = _months(start, end)
    files = []
    if exact is None and prefix is None:       # 全表：按月（或全仓）通配
        if months:
            for ym in months:
                files.extend(sorted(glob.glob(
                    os.path.join(store, "*_%s.parquet" % ym))))
        else:
            files = sorted(glob.glob(os.path.join(store, "*.parquet")))
    else:
        for c in prefix or []:
            if months:
                for ym in months:
                    files.extend(sorted(glob.glob(os.path.join(
                        store, "%s_*_%s.parquet" % (c, ym)))))
            else:
                files.extend(sorted(glob.glob(
                    os.path.join(store, "%s_*.parquet" % c))))
        for c in exact or []:
            st = _filename_code(c)
            if months:
                for ym in months:
                    f = os.path.join(store, "%s_%s.parquet" % (st, ym))
                    if os.path.exists(f):
                        files.append(f)
            else:
                files.extend(sorted(glob.glob(
                    os.path.join(store, "%s_??????.parquet" % st))))
    if not files:
        return pd.DataFrame()
    df = _concat_pq(files, cols, dcol, ccol, start, end, codes)
    if dcol and dcol in df.columns and (start is not None or end is not None):
        s = df[dcol].astype(str).str[:10].str.replace("-", "", regex=False)
        s = s.str[:8]
        if start is not None:
            df = df[s >= _norm_day(start)]
            s = df[dcol].astype(str).str[:10].str.replace("-", "", regex=False)
            s = s.str[:8]
        if end is not None:
            df = df[s <= _norm_day(end)]
    df = tdxr._apply_code_filter(df, None, codes)
    df = _pick_fields(df, fields, ccol, dcol)
    if dcol and dcol in df.columns:
        df = df.sort_values(dcol)
    return df.reset_index(drop=True)


def _read_by_date(store, dcol, ccol, start, end, codes, fields, cols=None):
    # 日文件也可能含补采日期；先按文件名剪掉早于 start 的分片（文件日期
    # 早于 start 者不可能含区间内数据），再按实际日期统计剪枝，并保留读后过滤。
    return _read_part(store, dcol, ccol, start, end, codes, fields, cols,
                      pattern="d_*.parquet", name_prune=True)


def _read_sqlite(tbl, start, end, codes, limit, after=None):
    # 2026-11-07 P0 RFC 方案②：end 透传为 until、after 透传游标；
    # 两者缺省 None 时 read_info 走原口径（信息三表现无 end 调用方，零影响）。
    return pd.DataFrame(tdxr.read_info(tbl, codes=codes,
                                       limit=limit or 200, since=start,
                                       until=end, after=after))


def _after_filter_rows(df, after, dcol):
    """非 sqlite 布局的游标兜底（P0 RFC 方案② R2 降级语义）：
    (日期, _fetched_at) 字典序严格大于游标；缺 _fetched_at 列时退化为
    仅日期严格大于（同日并列组跨页有丢行风险，现网 fallback 源 anns_d
    仅在真源不可用时才触达，风险已文档化）。sqlite 布局不走此函数
    （游标已在 SQL 层下推，rowid 防并列丢行）。"""
    if df is None or df.empty or not after:
        return df
    cd = str(after.get("date") or "").replace("-", "")[:8]
    if len(cd) != 8 or dcol not in df.columns:
        return df
    d = df[dcol].astype(str).str[:10].str.replace("-", "", regex=False)
    sub_col = "_fetched_at"
    if sub_col in df.columns:
        s = df[sub_col].astype(str)
        keep = (d > cd) | ((d == cd) & (s > str(after.get("sub") or "")))
    else:
        keep = d > cd
    return df[keep]


def _read_pg(store, dcol, ccol, start, end, codes, fields, cols=None,
             limit=None):
    """章程 §3：PG 表委托 data_loader（直连失败其内部 docker 兜底）；
    store 为描述性 DSN，实际连接配置在 data_loader/config.json。
    limit 下推（2026-10-16 审计修复）：避免 328 万行大表全量拉取后
    才在 DataFrame 层截断。"""
    del store, dcol, ccol, fields, cols  # 过滤由 read() 尾部统一归一/选列
    import data_loader
    return data_loader.load_table(start=start, end=end, symbols=codes,
                                  limit=limit)


_READERS = {"part": _read_part, "by_code": _read_by_code,
            "by_code_month": _read_by_code_month, "by_date": _read_by_date,
            "by_period": _read_by_period, "pg": _read_pg}


def _read_one(tbl, start, end, codes, fields, limit, after=None):
    e = _catalog().get("tables", {}).get(tbl) or {}
    if e.get("layout") == "sqlite":
        return _read_sqlite(tbl, start, end, codes, limit, after=after)
    fn = _READERS.get(e.get("layout"))
    if fn is None:
        return pd.DataFrame()
    if fields:
        filter_cols = [c for c in ("ts_code", "con_code", "code", "symbol")
                       if c in (e.get("schema") or [])]
        fields = list(dict.fromkeys(list(fields) + (e.get("primary_key") or []) + filter_cols))
    if e.get("layout") == "pg":  # 大表路由：limit 随 SQL 下推（审计修复）
        return _read_pg(e.get("store"), e.get("date_col"), e.get("code_col"),
                        start, end, codes, fields, _read_columns(e, fields),
                        limit=limit)
    return fn(e.get("store"), e.get("date_col"), e.get("code_col"),
              start, end, codes, fields, _read_columns(e, fields))


def _normalize(df, entries):
    """代码和日期归一；明细按声明主键去重，空主键表示保留事件行。"""
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    for e in entries:
        cc, dc = e.get("code_col"), e.get("date_col")
        if cc and cc != "ts_code" and cc in df.columns:
            df = df.rename(columns={cc: "ts_code"})
        if dc and dc in df.columns and dc != "trade_time":
            df[dc] = df[dc].astype(str).str[:10].str.replace("-", "",
                                                            regex=False)
            df[dc] = df[dc].str[:8]
    dcol = next((e.get("date_col") for e in entries
                 if e.get("date_col") and e.get("date_col") in df.columns),
                None)
    e = entries[0] if entries else {}
    declared = e.get("primary_key", [] if e.get("layout") == "sqlite" else None)
    if declared is not None:
        key = ["ts_code" if c == e.get("code_col") else c for c in declared]
        # 明细身份列缺失时不能缩减成粗键，否则会静默吞行。
        if any(c not in df.columns for c in key):
            key = []
    else:
        key = [c for c in ("ts_code", dcol) if c and c in df.columns]
    if key:
        complete = df[key].notna().all(axis=1)
        df = df[~df.duplicated(subset=key, keep="first") | ~complete]
    elif declared is None:
        df = df.drop_duplicates(keep="first")
    cols = ["ts_code"] + ([dcol] if dcol else []) + \
        [c for c in df.columns if c not in ("ts_code", dcol)]
    return df[[c for c in cols if c in df.columns]].reset_index(drop=True)


@_guard_table
def read_physical(table, start=None, end=None, codes=None, fields=None,
                  limit=None):
    """物理表直读（章程 §4.1）：跳过别名仲裁与跨源去重，table 必须是
    catalog.tables 里的物理表名。用于消费方需要特定源全部列的场景
    （如别名合并保真源整行时，另一源的补齐字段需读物理表获得）。"""
    cat = _catalog()
    e = cat.get("tables", {}).get(table)
    if e is None or not _ready_one(table):
        return pd.DataFrame()
    try:
        df = _read_one(table, start, end, codes, fields, limit)
    except PublicationPending:
        raise
    except Exception:
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df = _normalize(df, [e])
    dcol = e.get("date_col")
    if dcol and dcol in df.columns:
        df = df.sort_values(dcol, kind="stable")
    if limit is not None and not df.empty:
        df = df.head(limit)
    return _pick_fields(df, fields, "ts_code", dcol).reset_index(drop=True)


@_guard_table
def read(table, start=None, end=None, codes=None, fields=None, limit=None,
         after=None):
    """统一读。table 可为物理表名或别名；空结果返回空 DataFrame。

    after（2026-11-07 P0 RFC 方案② R2）：游标 dict
    {'date':'YYYYMMDD','sub':'<_fetched_at>','rid':<rowid>}——sqlite 布局
    SQL 层下推（升序 + rowid 防并列丢行），其余布局客户端严格大于过滤。
    缺省 None 行为逐字不变。"""
    got, entries = [], []
    alias = _catalog().get("aliases", {}).get(table) or {}
    merge_key = alias.get("merge_key")
    seen = None
    for t in _resolve(table):
        if not _ready_one(t):
            continue
        try:
            internal_fields = fields
            if fields and merge_key:
                internal_fields = list(dict.fromkeys(list(fields) + merge_key))
            df = _read_one(t, start, end, codes, internal_fields, limit,
                           after=after)
        except PublicationPending:
            raise
        except Exception:
            continue
        if df is not None and not df.empty:
            entry = _catalog()["tables"][t]
            df = _normalize(df, [entry])
            # 游标过滤按源施加：sqlite 布局已在 SQL 层精确过滤（rowid 三键），
            # 重放客户端谓词会误删同 (日期,_fetched_at) 并列组——绝不重放；
            # 其余布局此处过滤（降级语义）。
            if after and entry.get("layout") != "sqlite":
                df = _after_filter_rows(df, after,
                                        entry.get("date_col") or "trade_date")
            if merge_key is None:
                merge_key = entry.get("primary_key")
                if merge_key is None:
                    merge_key = [c for c in ("ts_code", entry.get("date_col")) if c]
            if merge_key:
                merge_key = ["ts_code" if c == entry.get("code_col") else c
                             for c in merge_key]
            if merge_key and all(c in df.columns for c in merge_key):
                keys = pd.MultiIndex.from_frame(df[merge_key])
                complete = df[merge_key].notna().all(axis=1)
                if seen is not None:
                    df = df[~keys.isin(seen) | ~complete]
                complete_keys = keys[complete].unique()
                seen = (complete_keys if seen is None else
                        seen.union(complete_keys, sort=False))
            elif merge_key:
                _LOG.warning("跨源合并缺少主键列，保留原行：table=%s source=%s missing=%s",
                             table, t, [c for c in merge_key if c not in df.columns])
            if df.empty:
                continue
            got.append(df)
            entries.append(entry)
            if alias.get("merge_mode") == "fallback":
                break
    if not got:
        return pd.DataFrame()
    df = got[0] if len(got) == 1 else pd.concat(got, ignore_index=True)
    out = df
    dcol = next((e.get("date_col") for e in entries
                 if e.get("date_col") in out.columns), None)
    if dcol:
        if after:
            # 游标模式：亚日稳定排序（存在列才加，保证跨源 concat 后仍有序）；
            # 过滤已按源完成（sqlite=SQL 下推 / 其余=客户端），此处不重放。
            keys = [dcol] + [c for c in ("_fetched_at", "_src_rowid")
                             if c in out.columns]
            out = out.sort_values(keys, kind="stable")
        else:
            out = out.sort_values(dcol, kind="stable")
    out = _pick_fields(out, fields, "ts_code", dcol).reset_index(drop=True)
    if limit is not None and not out.empty:
        out = out.head(limit)
    return out


def _d8(s):
    """日期 Series → YYYYMMDD 字符串 max（容错横杠/NaN）。"""
    v = s.astype(str).str[:10].str.replace("-", "", regex=False)
    v = v[~v.isin(["", "None", "nan", "NaT"])]
    if v.empty:
        return None
    mx = str(v.max())
    return mx[:8] if mx[:8].isdigit() else None


def _compute_latest(e):
    """按 layout 现算本地最新日期（口径对齐 build_catalog._freshness，
    by_code_month 用月份最大值定位文件比文件名 max 更准）。"""
    store, dcol, layout = e.get("store"), e.get("date_col"), e.get("layout")
    if not store or not dcol:
        return None
    try:
        if layout == "by_date":
            dated = sorted(f for f in glob.glob(os.path.join(store, "d_*.parquet"))
                           if re.search(r"d_(\d{8})", os.path.basename(f)))
            vals = []
            # 补采可能写入旧文件，文件名顺序不能代表实际日期水位。
            for f in reversed(dated):
                try:
                    df = pd.read_parquet(f, columns=[dcol])
                except PublicationPending:
                    raise
                except Exception:
                    continue
                v = _d8(df[dcol])
                if v:
                    vals.append(v)
            return max(vals) if vals else None
        if layout == "by_code_month":
            fs = glob.glob(os.path.join(store, "*.parquet"))
            yms = set()
            for f in fs:
                m = re.search(r"_(\d{6})\.parquet$", os.path.basename(f))
                if m:
                    yms.add(m.group(1))
            if not yms:
                return None
            # 同月各代码水位不同，取全月实际最大日期；空月回退更早月份。
            for ym in sorted(yms, reverse=True):
                cand = sorted(glob.glob(os.path.join(store, "*_%s.parquet" % ym)))
                vals = []
                for f in cand:
                    try:
                        df = pd.read_parquet(f, columns=[dcol])
                    except PublicationPending:
                        raise
                    except Exception:
                        continue
                    v = _d8(df[dcol])
                    if v:
                        vals.append(v)
                if vals:
                    return max(vals)
            return None
        if layout in ("by_code", "part", "by_period"):
            pat = {"part": "part_*.parquet", "by_period": "p_*.parquet"}.get(
                layout, "*.parquet")
            fs = glob.glob(os.path.join(store, pat))
            if not fs:
                return None
            if layout == "by_code":
                # mtime 最新 20 文件近似（单文件会被增量回写的退市码误导）
                fs = sorted(fs, key=os.path.getmtime, reverse=True)[:20]
            vals = [pd.read_parquet(p, columns=[dcol])[dcol] for p in fs]
            return _d8(pd.concat(vals, ignore_index=True))
    except PublicationPending:
        raise
    except Exception:
        return None
    return None


_LATEST_TTL = 300                       # 进程内最新日期缓存（秒）
_latest_cache = {}                      # tbl -> (ts, value)


def _latest_one(t, e):
    """单表现算最新日期：tdx part 委托 tdx_read 快速路径 → 通用现算 →
    catalog 快照兜底。结果按表缓存 TTL 300s（latest/explain 高频复用）。"""
    now = time.time()
    c = _latest_cache.get(t)
    if c and now - c[0] < _LATEST_TTL:
        return c[1]
    v = _latest_one_uncached(t, e)
    _latest_cache[t] = (now, v)
    return v


def _latest_one_uncached(t, e):
    layout = e.get("layout")
    try:
        if layout == "part" and t.startswith("ts_"):
            v = tdxr.latest_date(t)
            if v:
                return v
        elif layout == "sqlite":
            rows = tdxr.read_info(t, limit=1)
            if rows:
                v = str(rows[0].get(e.get("date_col") or ""))[:10]
                v = v.replace("-", "")
                return v or None
        v = _compute_latest(e)
        if v:
            return v
    except PublicationPending:
        raise
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/datamart.py", exc)
    return e.get("freshness")


@_guard_table
def latest(table):
    """别名/物理表的本地最新日期（现算优先，实时跟随增量同步）。

    别名多源时取候选源最大新鲜度：latest 语义是新鲜度探测，
    与 read 的真源优先不同——tdx 源滞后时应如实反映 tushare 源更新。
    """
    cands = _resolve(table)
    if len(cands) <= 1:
        if not cands or not _ready_one(cands[0]):
            return None
        t = cands[0]
        return _latest_one(t, _catalog().get("tables", {}).get(t) or {})
    vals = []
    for t in cands:
        if not _ready_one(t):
            continue
        v = _latest_one(t, _catalog().get("tables", {}).get(t) or {})
        if v:
            vals.append(v)
    return max(vals) if vals else None


# ── 数据时点校对（freshness）────────────────────────────────
# 每次消费数据前回答："数据停在几号？应该到几号？滞后几天？"
# 口径：expect = 最近一个【已收盘】A 股交易日（<17:30 视为当日未收盘）；
#       lag = 交易日历上 expect 与 latest 的位置差；容忍度按表设。
FRESHNESS_SPEC = [
    # (别名, 分组, 容忍交易日滞后, 说明)
    ("daily",         "A股行情", 0, "日线（数据底座主表）"),
    ("index_daily",   "A股行情", 0, "指数日线"),
    ("moneyflow",     "A股行情", 1, "个股资金流"),
    ("margin",        "A股行情", 1, "融资融券"),
    ("hk_daily",      "跨市场",  1, "港股日线"),
    ("us_daily",      "跨市场",  1, "美股日线（美东时差）"),
    ("cb_daily",      "跨市场",  2, "可转债日线"),
    ("crypto_daily",  "跨市场",  2, "加密货币 7×24"),
    ("news",          "信息流",  1, "金融新闻"),
    ("notice",        "信息流",  0, "公告"),
    ("report",        "信息流",  7, "研报（非每日必更）"),
    ("income",        "财报季频", 130, "利润表"),
    ("balancesheet",  "财报季频", 130, "资产负债表"),
    ("cashflow",      "财报季频", 130, "现金流量表"),
    ("fina_indicator", "财报季频", 130, "财务指标"),
]

_trade_cal_cache = {"ts": 0, "days": None}          # 交易日序列缓存 1h


@_guard_freshness
def _recent_trade_days(n_days=90):
    """升序最近交易日序列（YYYYMMDD，来自 trade_cal；失败返回 []）。"""
    now = time.time()
    if (_trade_cal_cache["days"] is not None
            and now - _trade_cal_cache["ts"] < 3600):
        return _trade_cal_cache["days"]
    days = []
    try:
        from datetime import datetime, timedelta
        start = (datetime.now() - timedelta(days=n_days + 40)
                 ).strftime("%Y%m%d")
        df = read("trade_cal", start=start,
                  fields=["cal_date", "is_open"])
        if df is not None and not df.empty:
            col = "cal_date" if "cal_date" in df.columns else df.columns[0]
            oc = "is_open" if "is_open" in df.columns else None
            sub = df[df[oc] == 1] if oc is not None else df
            days = sorted(str(v)[:8] for v in sub[col]
                          if str(v)[:8].isdigit())
    except PublicationPending:
        raise
    except Exception:
        days = []
    _trade_cal_cache.update(ts=now, days=days)
    return days


def _expected_trade_day(days):
    """期望日：最近已收盘交易日（当前 <17:30 视当日未收盘取前一日）。"""
    if not days:
        return None
    from datetime import datetime
    today = datetime.now().strftime("%Y%m%d")
    closed = [d for d in days if d <= today]
    if not closed:
        return None
    if closed[-1] == today and datetime.now().hour < 17:
        return closed[-2] if len(closed) > 1 else closed[-1]
    return closed[-1]


_fresh_cache = {"ts": 0.0, "data": None}   # freshness 结果缓存（TTL 内复用）
_prev_cache = {}                            # table_preview by_code 样本缓存


@_guard_freshness
def freshness_report(ttl_sec=300):
    """核心表时点校对：[{table, group, latest, expect, lag, tol, status,
    note}]。status: ok(≥期望-容忍) / lag-N / empty / nocal(无日历仅报 latest)。
    lag 口径：latest 先向下映射到 ≤latest 的最近 A 股交易日（跨市场表
    的非 A 股日期/周末/假期自然归位），再算与 expect 的交易日差。
    ttl_sec: 结果缓存秒数（默认 5min——15 表 latest 现算 ~25s，门户
    多次访问复用；传 0 强制现算）。供门户数据仓页、九维跑分 banner、
    任何消费入口复用。"""
    import bisect
    now = time.time()
    if (ttl_sec > 0 and _fresh_cache["data"] is not None
            and now - _fresh_cache["ts"] < ttl_sec):
        return _fresh_cache["data"]
    days = _recent_trade_days()
    expect = _expected_trade_day(days)
    out = []
    for table, group, tol, note in FRESHNESS_SPEC:
        try:
            lat = latest(table)
        except PublicationPending:
            raise
        except Exception:
            lat = None
        if not lat:
            st, lag = "empty", None
        elif not expect:
            st, lag = "nocal", None
        else:
            j = bisect.bisect_right(days, expect) - 1     # expect 位置
            i = bisect.bisect_right(days, lat) - 1        # ≤latest 最近交易日
            lag = max(0, j - i)
            st = "ok" if lag <= tol else "lag-%d" % lag
        out.append({"table": table, "group": group, "latest": lat,
                    "expect": expect, "lag": lag, "tol": tol,
                    "status": st, "note": note})
    result = {"expect": expect, "rows": out}
    _fresh_cache.update(ts=now, data=result)
    return result


@_guard_table
def table_preview(table, n=8):
    """K56 数据穿透：白名单表最新 n 行样本（{cols, rows[{列:文本}]}）。
    口径：by_code 布局（us/crypto 万级文件）走 mtime 最新 25 文件直读；
    其余按 latest 定位读 latest 前 12 天窗口（by_date 天然只 concat
    1-2 分片）；读空退化为 limit 兜底。供驾驶舱/数据仓页 🔍 就地
    展开——穿透的最后一跳：表名→数据本身。"""
    allowed = {t for t, _, _, _ in FRESHNESS_SPEC}
    if table not in allowed:
        return {"error": "table not in whitelist", "table": table}
    from datetime import datetime, timedelta
    lat = None
    try:
        lat = latest(table)
    except PublicationPending:
        raise
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/datamart.py", exc)
    df = pd.DataFrame()

    # by_code 快路径：read(start,end) 对 by_code 是全 glob+过滤（us_daily
    # 实测 84s）；scandir 目录条目直取 mtime（Windows 免二次 stat）取
    # 最新 25 文件直读 + _normalize 统一列名；结果 TTL 缓存 600s。
    try:
        _cat = _catalog()["tables"]
        for _t in _resolve(table):
            if not _ready_one(_t):
                continue
            _ent = _cat.get(_t) or {}
            if _ent.get("layout") == "by_code" and _ent.get("store"):
                _ck = ("by_code", _t)
                _c = _prev_cache.get(_ck)
                if _c and time.time() - _c[0] < 600:
                    df = _c[1]
                    break
                _ents = [f for f in os.scandir(_ent["store"])
                         if f.name.endswith(".parquet")]
                _ents.sort(key=lambda f: f.stat().st_mtime, reverse=True)
                if _ents[:25]:
                    _d2 = _concat_pq([f.path for f in _ents[:25]])
                    if _d2 is not None and not _d2.empty:
                        df = _normalize(_d2, [_ent])
                        _prev_cache[_ck] = (time.time(), df)
                        break
    except PublicationPending:
        raise
    except Exception:
        df = pd.DataFrame()

    if (df is None or df.empty) and lat and len(str(lat)) == 8:
        try:
            st = (datetime.strptime(str(lat), "%Y%m%d")
                  - timedelta(days=12)).strftime("%Y%m%d")
            df = read(table, start=st, end=str(lat))
        except PublicationPending:
            raise
        except Exception:
            df = pd.DataFrame()
    if df is None or df.empty:
        try:
            df = read(table, limit=300)
        except PublicationPending:
            raise
        except Exception:
            df = pd.DataFrame()
    if df is None or df.empty:
        return {"table": table, "latest": lat, "cols": [], "rows": [],
                "note": "无样本（表空或源未挂载）"}
    dcol = next((c for c in ("trade_date", "date", "ann_date", "pub_date",
                             "report_date", "end_date", "fdate", "dt")
                 if c in df.columns), None)
    if dcol:
        try:
            df = df.sort_values(dcol, ascending=False)
        except PublicationPending:
            raise
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/datamart.py", exc)
    df = df.head(max(1, min(int(n), 20)))
    cols = [c for c in df.columns][:12]

    def _txt(v):
        try:
            if v is None or v != v:            # None / NaN
                return "—"
            if isinstance(v, float):
                return ("%.4g" % v)
            return str(v)[:48]
        except PublicationPending:
            raise
        except Exception:
            return str(v)[:48]

    rows = [{c: _txt(r.get(c)) for c in cols}
            for _, r in df[cols].to_dict("index").items()]
    return {"table": table, "latest": lat, "date_col": dcol,
            "cols": cols, "rows": rows}


def freshness_banner(tables=None):
    """一行时点提示（控制台/日志）：默认全 ok 摘要，否则列异常项。"""
    r = freshness_report()
    rows = r["rows"] if tables is None else \
        [x for x in r["rows"] if x["table"] in set(tables)]
    bad = [x for x in rows if x["status"] not in ("ok", "nocal")]
    head = "[数据时点] 期望=%s" % (r["expect"] or "未知")
    if not bad:
        return "%s · %d 表全部就绪" % (head, len(rows))
    return "%s · 异常 %d/%d：%s" % (
        head, len(bad), len(rows),
        ", ".join("%s=%s(%s)" % (x["table"], x["latest"] or "空",
                                 x["status"]) for x in bad))


if __name__ == "__main__":
    import sys
    for t in (sys.argv[1:] or ["daily", "stk_mins", "us_daily", "notice"]):
        print("==", t)
        for r in explain(t):
            print("   ", r)
    print("latest daily =", latest("daily"),
          "| stk_mins =", latest("stk_mins"))
