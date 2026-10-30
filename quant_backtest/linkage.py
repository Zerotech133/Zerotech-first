# -*- coding: utf-8 -*-
"""K35 大盘-板块-个股 三线联动复盘 · 数据层（MVP，影子观察）。

选型（2026-10-02 用户确认：路线 A / TDX 880xxx / 日线-30m-5m）：
  · 个股: chanlun_data.load_kline（CN tushare 分钟重采样 + zhice/TDX 兜底）
  · 大盘: load_kline 指数（默认 000001.SH 上证；--index 可换 399006.SZ 等）
  · 板块: TDX 880xxx——日线 tdx_sector_daily；30m/5m 由 tdx_sector_min
    1 分钟重采样（label=right 对齐 tushare 分钟桶结束时刻惯例）
  · 个股→板块: ts_stock_basic.industry 名匹配 tdx_sector_def.name，
    seg 优先级 industry881 > industry_l1 > old_industry；别名表兜底；
    可显式 --block 881019 / --block-name 化纤 覆盖
  · 对齐: 个股时间轴为主，板块/大盘按 dt 精确匹配、缺失前向填充
  · 归一化: 三线公共首根 close 为 0% 基准，(c/c0-1)*100

板块分钟尾巴刷新: tdx_sector_min 月度拉取（止 2026-09-24），
refresh_min_tail() 走远端日期过滤增量补到最新交易日，落
data/tdx/tdx_sector_min_tail/part_0000.parquet（独立尾巴仓，不动月度分片）。

纪律：只读影子展示，不构成买卖依据；五类联动信号标注（抗跌/滞涨/
共振底/孤军/三共振）已由 linkage_signals.py v1 实现（判定+侧车+门户
展示，不推送；攒影子样本后再议升级）。

用法:
  python linkage.py 301216.SZ                     # 默认三周期摘要
  python linkage.py 301216.SZ --period 30m --last 480
  python linkage.py 301216.SZ --block 881019 --refresh-min
"""
import glob
import json
import os
import sys
import time

import pandas as pd
import logging
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TDX = os.path.join(HERE, "data", "tdx")
SEC_MIN_TAIL = os.path.join(TDX, "tdx_sector_min_tail")
REMOTE = "http://rookie.xtcloud-tech.com:48088/api/data/table/tdx_sector_min"
PAGE = 5000

# 归一化图配色/周期与 chanlun_plot 对齐
PERIODS = ("1d", "30m", "5m")
DEFAULT_LAST = {"1d": 250, "30m": 480, "5m": 480}
DEFAULT_INDEX = "000001.SH"
SEG_PRI = ("industry881", "industry_l1", "old_industry")

# tushare industry 名 → TDX 板块名（精确匹配失败时兜底；按需增补）
INDUSTRY_ALIAS = {
    "化学纤维": "化纤",
    "化学原料": "化学原料",
    "农药化肥": "农用化工",
    "农药": "农用化工",
    "日用化学": "日用化工",
    "石油贸易": "石油贸易",
}

_DEF_CACHE = {}


# ---------------------------------------------------------------- 板块定义
def sector_defs():
    """{code: {"name":…, "seg":…}}，懒加载缓存。"""
    if _DEF_CACHE:
        return _DEF_CACHE
    for p in sorted(glob.glob(os.path.join(TDX, "tdx_sector_def",
                                           "part_*.parquet"))):
        df = pd.read_parquet(p, columns=["code", "name", "seg"])
        for _, r in df.iterrows():
            _DEF_CACHE[str(r["code"])] = {"name": str(r["name"]),
                                          "seg": str(r["seg"])}
    return _DEF_CACHE


def _stock_industry(code):
    """ts_stock_basic.industry（cn 6 位/后缀码均可）。"""
    import tdx_read as tdxr
    for cand in (code, code.split(".")[0]):
        try:
            df = tdxr.read_all("ts_stock_basic", codes=[cand])
        except Exception:
            df = pd.DataFrame()
        if len(df) and "industry" in df.columns:
            return str(df.iloc[0]["industry"] or "") or None, \
                str(df.iloc[0].get("name") or "")
    return None, ""


def resolve_block(code, block=None, block_name=None):
    """个股 → TDX 板块。返回 {code,name,seg,how} 或 None。"""
    defs = sector_defs()
    if block:
        b = str(block).split(".")[0]
        if b in defs:
            return {"code": b, "name": defs[b]["name"],
                    "seg": defs[b]["seg"], "how": "explicit-code"}
        return None
    want = block_name
    how = "explicit-name"
    if not want:
        ind, _nm = _stock_industry(code)
        if not ind:
            return None
        want = ind
        how = "industry:" + ind
        want = INDUSTRY_ALIAS.get(want, want)
    # 精确名匹配（seg 优先级），失败退包含匹配
    for seg in SEG_PRI + ("concept", "style"):
        for c, d in defs.items():
            if d["name"] == want and d["seg"] == seg:
                return {"code": c, "name": d["name"], "seg": d["seg"],
                        "how": how}
    for seg in SEG_PRI:
        for c, d in defs.items():
            if d["seg"] == seg and len(want) >= 2 and (
                    want in d["name"] or d["name"] in want):
                return {"code": c, "name": d["name"], "seg": d["seg"],
                        "how": how + "(fuzzy)"}
    return None


# ---------------------------------------------------------------- 板块成员
_INDUSTRY_IDX = None
_RESOLVE_CACHE = {}
_MEMBERS_CACHE = {}


def _seg_pri(kv):
    seg = kv[1]["seg"]
    return SEG_PRI.index(seg) if seg in SEG_PRI else len(SEG_PRI)


def _industry_index():
    """{行业名: 板块code}，SEG_PRI 优先级一次构建（懒加载缓存）。"""
    global _INDUSTRY_IDX
    if _INDUSTRY_IDX is None:
        idx = {}
        for bc, d in sorted(sector_defs().items(), key=_seg_pri):
            if d["seg"] in SEG_PRI:
                idx.setdefault(d["name"], bc)
        _INDUSTRY_IDX = idx
    return _INDUSTRY_IDX


def resolve_industry_block(industry):
    """个股 industry 名 → 板块 code（与 resolve_block 同源同优先级）。

    resolve_block 是「个股 code 反查」，本函数是「行业名直查」——
    匹配逻辑一致（别名 → 精确名 → SEG_PRI 包含匹配），供批量
    成员归组用，避免逐股走 resolve_block 的全表扫描。
    """
    if not industry:
        return None
    if industry in _RESOLVE_CACHE:
        return _RESOLVE_CACHE[industry]
    w = INDUSTRY_ALIAS.get(industry, industry)
    idx = _industry_index()
    bc = idx.get(w)
    if not bc:
        for c, d in sorted(sector_defs().items(), key=_seg_pri):
            if d["seg"] in SEG_PRI and len(w) >= 2 and (
                    w in d["name"] or d["name"] in w):
                bc = c
                break
    _RESOLVE_CACHE[industry] = bc
    return bc


def block_members(block):
    """板块成员清单（strong 阶段2a 全池扫描用）。

    口径：TDX 880xxx 行业板块无成分股清单文件，成员归属以
    ts_stock_basic.industry（resolve_block 的同源归属字段）经
    resolve_industry_block 归组——每只个股归属唯一板块，与单股
    resolve_block 完全同口径。返回 [{code,name,industry}]
    （code 归一为 600519.SH 形式）；板块不在定义表返回 []。
    """
    b = str(block).split(".")[0]
    if b in _MEMBERS_CACHE:
        return _MEMBERS_CACHE[b]
    if b not in sector_defs():
        return []
    from codes import normalize_code
    import tdx_read as tdxr
    try:
        df = tdxr.read_all("ts_stock_basic",
                           columns=["ts_code", "name", "industry"])
    except Exception:
        return []
    out = []
    for _, r in df.iterrows():
        ind = str(r["industry"] or "") or None
        if resolve_industry_block(ind) == b:
            c = normalize_code(r["ts_code"])
            if c:
                out.append({"code": c, "name": str(r["name"] or ""),
                            "industry": str(r["industry"] or "")})
    _MEMBERS_CACHE[b] = out
    return out


def block_member_count(block):
    """板块成员数（strong_track.block_quorum 门槛输入；无数据 → None）。"""
    b = str(block).split(".")[0]
    if b not in sector_defs():
        return None
    return len(block_members(b))


# ---------------------------------------------------------------- 板块K线
def _read_parquet_code(tbl_dir, code, columns=None):
    """按 code 列下推过滤读全部分片（含尾巴仓），返回合并 df 或空。"""
    frames = []
    for pat in (os.path.join(tbl_dir, "part_*.parquet"),):
        for p in sorted(glob.glob(pat)):
            try:
                df = pd.read_parquet(p, columns=columns,
                                     filters=[("code", "=", code)])
            except Exception:
                df = pd.read_parquet(p, columns=columns)
                df = df[df["code"] == code]
            if len(df):
                frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else \
        pd.DataFrame(columns=columns or ["code", "trade_date", "close"])


def block_bars(block_code, period, limit=500):
    """板块 88x → [{dt,close,…}]（dt 与 load_kline 惯例对齐）。"""
    if period == "1d":
        df = _read_parquet_code(os.path.join(TDX, "tdx_sector_daily"),
                                block_code,
                                ["code", "trade_date", "open", "high",
                                 "low", "close", "vol", "amount"])
        if not len(df):
            return []
        df = df.sort_values("trade_date").tail(int(limit))

        def _d8(s):
            s = str(s)
            return "%s-%s-%s" % (s[:4], s[4:6], s[6:8]) if \
                len(s) == 8 and s.isdigit() else s[:10]

        return [{"dt": _d8(r["trade_date"]), "close": float(r["close"]),
                 "open": float(r["open"]), "high": float(r["high"]),
                 "low": float(r["low"]), "vol": float(r["vol"] or 0)}
                for _, r in df.iterrows()]
    # 分钟：月度分片 + 尾巴仓 → 1 分钟 → 重采样
    frames = []
    for sub in ("tdx_sector_min", "tdx_sector_min_tail"):
        d = os.path.join(TDX, sub)
        df = _read_parquet_code(d, block_code,
                                ["code", "trade_date", "trade_time",
                                 "open", "high", "low", "close", "vol"])
        if len(df):
            frames.append(df)
    if not frames:
        return []
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["trade_date", "trade_time"])
    ts = pd.to_datetime(df["trade_date"].astype(str).str.slice(0, 10)
                        + " " + df["trade_time"].astype(str))
    df = df.assign(dt=ts).set_index("dt").sort_index()
    # 量能缺失必须保留为未知，记录具体分钟供下游资格检查。
    df["vol_missing_minutes"] = [i.strftime("%Y-%m-%d %H:%M") if pd.isna(v) else None
                                 for i, v in df["vol"].items()]
    rule = "5min" if period == "5m" else "30min"
    agg = df[["open", "high", "low", "close", "vol", "vol_missing_minutes"]].resample(
        rule, label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last",
         "vol": lambda s: s.sum(min_count=len(s)),
         "vol_missing_minutes": lambda s: [v for v in s if pd.notna(v)]}).dropna(subset=["close"])
    agg = agg.tail(int(limit))
    return [{"dt": i.strftime("%Y-%m-%d %H:%M"), "close": float(r["close"]),
             "vol": None if pd.isna(r["vol"]) else float(r["vol"]),
             "vol_complete": not bool(r["vol_missing_minutes"]),
             "vol_missing_minutes": r["vol_missing_minutes"]} for i, r in agg.iterrows()]


def min_local_max_date():
    """tdx_sector_min（分片+尾巴）现有最大 trade_date。"""
    hi = ""
    for sub in ("tdx_sector_min", "tdx_sector_min_tail"):
        for p in sorted(glob.glob(os.path.join(TDX, sub, "part_*.parquet"))):
            try:
                v = pd.read_parquet(p, columns=["trade_date"])["trade_date"]
                v = v.astype(str).max()
                hi = max(hi, v)
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/linkage.py", exc)
    return hi


def _tail_snapshot():
    """仅读目录stat与小型manifest，不增加尾巴全表读取。"""
    path = os.path.join(TDX, '_manifest.json')
    body = None
    if os.path.exists(path):
        with open(path, 'rb') as handle:
            body = handle.read()
    parts = []
    for part in sorted(glob.glob(os.path.join(SEC_MIN_TAIL, 'part_*.parquet'))):
        stat = os.stat(part)
        parts.append((part, stat.st_size, stat.st_mtime_ns))
    return body, parts


def _commit_min_tail(new, fp, snapshot, provenance):
    """临时片+清单提交；失败时恢复已有尾片，保留恢复证据字段。"""
    import uuid
    import hashlib
    import pyarrow as pa
    import pyarrow.parquet as pq
    if _tail_snapshot() != snapshot:
        raise RuntimeError('尾巴仓或清单已变化，停止刷新')
    fields = ['code', 'trade_date', 'trade_time', 'open', 'high', 'low', 'close',
              'vol', 'amount', 'up_count', 'down_count', '_fetched_at']
    exists = os.path.exists(fp)
    if exists:
        previous = pq.ParquetFile(fp)
        schema = previous.schema_arrow
        group_size = previous.metadata.row_group(0).num_rows if previous.metadata.num_row_groups else 1048576
        old_rows = previous.metadata.num_rows
        previous.close()
    else:
        schema = pa.schema([(c, pa.large_string()) for c in fields[:3]]
            + [(c, pa.float64()) for c in fields[3:9]]
            + [(c, pa.int64()) for c in fields[9:11]] + [('_fetched_at', pa.large_string())])
        group_size, old_rows = 1048576, 0
    if set(schema.names) != set(fields):
        raise ValueError('尾片现有schema与12字段合同不一致')
    state = json.loads(snapshot[0].decode('utf-8')) if snapshot[0] is not None else {}
    entry = state.setdefault('tdx_sector_min_tail', {})
    physical_rows = sum(pq.ParquetFile(path).metadata.num_rows for path, _, _ in snapshot[1])
    entry['rows'] = physical_rows - old_rows + len(new)
    entry['part'] = max([int(os.path.basename(p).split('_')[1].split('.')[0]) + 1 for p, _, _ in snapshot[1]] + [1])
    entry.setdefault('done', True)
    entry['last_refresh'] = provenance
    manifest_body = json.dumps(state, ensure_ascii=False, indent=1).encode('utf-8')
    token = uuid.uuid4().hex
    temporary, backup = fp + '.' + token + '.tmp', fp + '.' + token + '.bak'
    manifest = os.path.join(TDX, '_manifest.json')
    manifest_tmp = manifest + '.' + token + '.tmp'
    published, completed = False, False
    preserve_backup = False
    def file_sha(path):
        digest = hashlib.sha256()
        with open(path, 'rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(block)
        return digest.hexdigest()
    try:
        new.to_parquet(temporary, index=False, schema=schema, row_group_size=group_size)
        check = pq.ParquetFile(temporary)
        if check.metadata.num_rows != len(new) or not check.schema_arrow.equals(schema):
            raise RuntimeError('临时尾片行数/schema验收失败')
        check.close()
        with open(temporary, 'r+b') as handle:
            os.fsync(handle.fileno())
        owned_sha = file_sha(temporary)
        owned_stat = os.stat(temporary)
        with open(manifest_tmp, 'xb') as handle:
            handle.write(manifest_body)
            handle.flush()
            os.fsync(handle.fileno())
        if _tail_snapshot() != snapshot:
            raise RuntimeError('尾巴仓或清单在准备期间变化，停止刷新')
        if exists:
            os.link(fp, backup)
        os.replace(temporary, fp)
        published = True
        after = _tail_snapshot()
        if (after[0] != snapshot[0] or [p for p in after[1] if p[0] != fp] != [p for p in snapshot[1] if p[0] != fp]):
            raise RuntimeError('发布后其他尾片或清单发生变化，停止刷新')
        final_stat = os.stat(fp)
        if (final_stat.st_size, final_stat.st_mtime_ns) != (owned_stat.st_size, owned_stat.st_mtime_ns):
            raise RuntimeError('发布后自有尾片发生变化，停止刷新')
        os.replace(manifest_tmp, manifest)
        completed = True
    except Exception:
        if published and os.path.exists(manifest):
            with open(manifest, 'rb') as handle:
                completed = handle.read() == manifest_body
        if published and not completed:
            if not os.path.exists(fp) or file_sha(fp) != owned_sha:
                preserve_backup = exists
                raise RuntimeError('尾片在提交失败后已变化；保留备份，不覆盖其他写入器')
            try:
                if exists:
                    os.replace(backup, fp)
                else:
                    os.remove(fp)
            except Exception as rollback_error:
                preserve_backup = exists
                raise RuntimeError('尾片恢复失败；保留备份供核对') from rollback_error
        if not completed:
            raise
    finally:
        for path in (temporary, manifest_tmp, backup):
            if path == backup and preserve_backup:
                continue
            if os.path.exists(path):
                os.remove(path)


def refresh_min_tail(quiet=False):
    """远端日期过滤增量补尾巴，成功后同步维护物理清单。"""
    import hashlib
    from datetime import datetime, timedelta, timezone
    hi = min_local_max_date()
    if not hi:
        return {'ok': False, 'error': '本地无 tdx_sector_min 基线'}
    start = (datetime.strptime(hi[:8], '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d')
    snapshot = _tail_snapshot()
    fields = {'code', 'trade_date', 'trade_time', 'open', 'high', 'low', 'close',
              'vol', 'amount', 'up_count', 'down_count', '_fetched_at'}
    unique, offset, declared, columns = {}, 0, None, None
    page_hashes = []
    while True:
        url = '%s?limit=%d&offset=%d&start_date=%s' % (REMOTE, PAGE, offset, start)
        with safe_urlopen(url, timeout=60) as response:
            body = response.read()
        received = datetime.now(timezone.utc).isoformat()
        data = json.loads(body.decode('utf-8'))
        got, cols, total = data.get('rows'), data.get('columns'), data.get('total')
        if not isinstance(got, list) or not isinstance(cols, list) or len(cols) != len(set(cols)) or set(cols) != fields:
            raise ValueError('分页12字段合同无效')
        if type(total) is not int or total < 0 or len(got) > PAGE or offset + len(got) > total:
            raise ValueError('分页total/行数合同无效')
        if declared is None:
            declared, columns = total, cols
        elif total != declared or cols != columns:
            raise ValueError('分页total或列在拉取期间变化')
        page_hashes.append(hashlib.sha256(body).hexdigest())
        if not got:
            if offset != declared:
                raise ValueError('分页未完成即返回空集')
            break
        for values in got:
            if not isinstance(values, list) or len(values) != len(cols):
                raise ValueError('分页行宽不合约')
            row = dict(zip(cols, values))
            code, day, minute = row['code'], row['trade_date'], row['trade_time']
            if not isinstance(code, str) or len(code) != 6 or not code.isdigit() or not code.startswith(('880', '881')):
                raise ValueError('板块代码无效')
            if not isinstance(day, str) or datetime.strptime(day, '%Y%m%d').strftime('%Y%m%d') != day or day < start:
                raise ValueError('日期过滤失效或日期无效')
            if not isinstance(minute, str) or datetime.strptime(minute, '%H:%M').strftime('%H:%M') != minute:
                raise ValueError('分钟标识无效')
            key = code, day, minute
            if key in unique and json.dumps(unique[key], sort_keys=True) != json.dumps(row, sort_keys=True):
                raise ValueError('同分钟键返回冲突，拒绝覆盖')
            unique.setdefault(key, row)
        offset += len(got)
        if offset == declared:
            break
        time.sleep(0.15)
    if not unique:
        return {'ok': True, 'from': start, 'rows': 0, 'note': '无新数据'}
    os.makedirs(SEC_MIN_TAIL, exist_ok=True)
    fp = os.path.join(SEC_MIN_TAIL, 'part_0000.parquet')
    new = pd.DataFrame(list(unique.values()))
    if os.path.exists(fp):
        old = pd.read_parquet(fp)
        if old.duplicated(subset=['code', 'trade_date', 'trade_time']).any():
            raise ValueError('已有尾片存在重复键，不自动清洗旧片')
        key_columns = ['code', 'trade_date', 'trade_time']
        if not old[key_columns].merge(new[key_columns], on=key_columns, how='inner').empty:
            raise ValueError('旧尾片与新供给存在三键重叠，停止刷新')
        new = pd.concat([old, new], ignore_index=True)
    provenance = {'provider': 'TDX-Trading', 'endpoint': REMOTE, 'start_date': start,
        'received_rows': offset, 'unique_received_rows': len(unique), 'actual_received_at': received,
        'response_body_sha256': page_hashes}
    _commit_min_tail(new, fp, snapshot, provenance)
    if not quiet:
        print('tail +%d rows (%s → %s)' % (offset, start, str(new['trade_date'].astype(str).max())))
    return {'ok': True, 'from': start, 'rows': offset}


# ---------------------------------------------------------------- 对齐归一
def _norm_dt(dt, period):
    s = str(dt)
    return s[:10] if period == "1d" else s[:16]


def series(code, period, limit=None, block=None, block_name=None,
           index_code=DEFAULT_INDEX, refresh=False):
    """三线取数+对齐+归一化 → 绘图包。"""
    from chanlun_data import load_kline
    limit = limit or DEFAULT_LAST.get(period, 480)
    rs = load_kline(code, period, int(limit))
    if not rs.get("ok"):
        return {"ok": False, "error": rs.get("error", "load_kline fail"),
                "code": code, "period": period}
    stock = {_norm_dt(b["dt"], period): float(b["c"])
             for b in rs["bars"]}
    dts = [_norm_dt(b["dt"], period) for b in rs["bars"]]

    lines, meta = {}, {}
    ri = load_kline(index_code, period, int(limit))
    if ri.get("ok") and ri["bars"]:
        lines["index"] = {_norm_dt(b["dt"], period): float(b["c"])
                          for b in ri["bars"]}
        meta["index"] = {"name": index_code,
                         "last": _norm_dt(ri["bars"][-1]["dt"], period)}
    meta["stock"] = {"last": dts[-1]}

    blk = resolve_block(code, block=block, block_name=block_name)
    if blk:
        bb = block_bars(blk["code"], period, int(limit))
        if bb:
            lines["block"] = {_norm_dt(b["dt"], period): b["close"]
                              for b in bb}
            meta["block"] = {"code": blk["code"], "name": blk["name"],
                             "seg": blk["seg"], "how": blk["how"],
                             "last": _norm_dt(bb[-1]["dt"], period)}
    meta["resolved_block"] = blk

    # 前向填充到个股时间轴
    filled, carry = {}, {}
    for key, mp in lines.items():
        out = []
        for d in dts:
            if d in mp:
                carry[key] = mp[d]
            out.append(carry.get(key))
        filled[key] = out

    # 公共首根（三线都有真实/已填充值）为 0% 基准
    keys = list(filled)
    base_i = 0
    for i in range(len(dts)):
        if all(filled[k][i] is not None for k in keys):
            base_i = i
            break
    def pct(arr):
        return None if arr[base_i] in (None, 0) else [
            None if v is None else round((v / arr[base_i] - 1) * 100, 3)
            for v in arr]

    n_ffill = {k: sum(1 for v in filled[k] if v is None) for k in keys}
    return {"ok": True, "code": code, "name": rs.get("name") or code,
            "period": period, "dts": dts,
            "stock": pct([stock[d] for d in dts]),
            "index": pct(filled.get("index", [])) if "index" in filled else None,
            "block": pct(filled.get("block", [])) if "block" in filled else None,
            "meta": meta, "base_dt": dts[base_i],
            "stock_last_close": stock[dts[-1]],
            "n": len(dts), "ffill_gaps": n_ffill}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="三线联动数据层")
    ap.add_argument("code")
    ap.add_argument("--period", default=",".join(PERIODS))
    ap.add_argument("--last", type=int, default=0)
    ap.add_argument("--block", default=None)
    ap.add_argument("--block-name", default=None)
    ap.add_argument("--index", default=DEFAULT_INDEX)
    ap.add_argument("--refresh-min", action="store_true")
    a = ap.parse_args()
    if a.refresh_min:
        print(json.dumps(refresh_min_tail(), ensure_ascii=False))
    for per in a.period.split(","):
        r = series(a.code, per, a.last or None, block=a.block,
                   block_name=a.block_name, index_code=a.index)
        slim = {k: v for k, v in r.items()
                if k in ("ok", "period", "n", "base_dt", "ffill_gaps",
                         "meta", "error")}
        if r.get("ok"):
            slim["last_pct"] = {k: r[k][-1] for k in ("stock", "index",
                                                      "block") if r.get(k)}
        print(json.dumps(slim, ensure_ascii=False))


if __name__ == "__main__":
    main()
