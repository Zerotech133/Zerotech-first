# -*- coding: utf-8 -*-
"""TDX-Trading 远端数据仓全量/增量同步器（断点续传）。

数据源：http://rookie.xtcloud-tech.com:48088（TDX-Trading 数据查询 API，
30 张表 · Tushare + akshare + 通达信采集，每日 17:00 增量更新）。

落库布局（与现有数据仓对齐）：
· K线/财务/板块等结构化表 → data/tdx/<table>/part_NNNN.parquet（分片，
  读取用 pd.concat(map(pd.read_parquet, glob))）
· 公告/个股新闻/研报 → data/tdx/tdx_info.db 三张表（独立库，不进
  news.db——22 万条个股新闻会淹没 JEV 判断队列，JEV 按需选择性接入）
· 进度 → data/tdx/_manifest.json（每表 offset/分片号/done，断点续传）

增量：表 done 后重跑按本地最新日期/时间戳只拉新增（远端每日 17:00 更新，
配合每日定时任务即为增量同步）。

用法：
  python tdx_sync.py --all                # 全部结构化表（不含 ts_rt_min）
  python tdx_sync.py --tables ts_daily,stock_notice
  python tdx_sync.py --include-rtmin      # 追加 4752 万行分钟线（3-4h）
  python tdx_sync.py --status             # 查看同步进度
"""

import argparse
import glob
import json
import os
import socket
import sqlite3
import time

import pandas as pd
import requests
import logging

BASE = "http://rookie.xtcloud-tech.com:48088"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "tdx")
INFO_DB = os.path.join(OUT, "tdx_info.db")
MANIFEST = os.path.join(OUT, "_manifest.json")

PAGE = 5000  # API 单次上限
FLUSH_PAGES = 40  # 每 40 页（20 万行）落一个 parquet 分片
RETRY = 3  # 单页失败重试
RETRY_DELAYS = (2, 8)  # 重试间隔秒（T-I4：2s/8s 退避，覆盖 DNS
# 抖动窗口——10-17 实证 1.5s/3s 太短，
# rookie DNS 故障窗口内 3 次重试全灭）
SLEEP = 0.15  # 请求间隔（保护远端）
SQLITE_TABLES = {"stock_notice", "stock_news", "stock_report"}
# ts_rt_min 4752 万行单独开关，默认跳过
SKIP_DEFAULT = {
    "ts_rt_min",
    "tdx_sector_min",
}  # K53: ts_moneyflow 移出（done 表走增量续拉，不再全量翻页）

SESS = requests.Session()

DNS_HOST = "rookie.xtcloud-tech.com"  # 10-17 实证该域名 DNS 间歇故障


def _dns_probe():
    """显式 getaddrinfo 复核（T-I4）：把'本机解析失败'与'远端不可达'
    分开留痕——错误码随页级错误进 err.log，才能判断该钉 hosts 还是
    该等远端。返回 None=解析已恢复，否则 ERR_DNS_RESOLVE 串。"""
    try:
        socket.getaddrinfo(DNS_HOST, None)
        return None
    except socket.gaierror as e:
        return "ERR_DNS_RESOLVE %s errno=%s %s" % (DNS_HOST, e.errno, e)


def _get(path, params=None, timeout=60):
    err = None
    for i in range(RETRY):
        try:
            r = SESS.get(BASE + path, params=params or {}, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            # 诊断增强（K-STOCK-425）：保留状态码与响应体头部，
            # 不再让 r.json() 二次抛异常吞掉真实原因（曾致日志只见
            # "Expecting value" 无法区分 502/超时页/空响应）。
            err = "HTTP %s body=%r" % (r.status_code, r.content[:120])
        except requests.RequestException as e:
            msg = "%s: %s" % (type(e).__name__, str(e)[:150])
            # T-I4：DNS 类失败显式复核留痕（10-17 NameResolutionError
            # 全表连败，日志只见异常名，不知本机解析当时是否恢复）。
            ename = type(e).__name__
            if (
                "NameResolution" in ename
                or "NameResolution" in str(e)
                or "getaddrinfo failed" in str(e)
            ):
                probe = _dns_probe()
                msg = "ERR_DNS %s | probe=%s" % (
                    msg,
                    probe or "resolve-ok-now(瞬时抖动)",
                )
            err = msg
        except ValueError as e:
            err = "non-JSON response: %s" % str(e)[:80]
        if i < RETRY - 1:
            time.sleep(RETRY_DELAYS[min(i, len(RETRY_DELAYS) - 1)])
    raise RuntimeError("GET %s failed: %s" % (path, err))


def _load_manifest():
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_manifest(m):
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    os.replace(tmp, MANIFEST)


def _remote_tables():
    d = _get("/api/data/status")
    return {t["table"]: t for t in d.get("tables", [])}


# ── parquet 分片表 ──────────────────────────────────────────
def _part_path(tbl, idx):
    d = os.path.join(OUT, tbl)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "part_%04d.parquet" % idx)


def _parquet_latest_date(tbl):
    """本地最新日期（增量起点）：全分片取日期列 max（分片不保证按日期
    有序，如 sector_daily 窗口补齐分片存放更早数据）。"""
    parts = sorted(glob.glob(os.path.join(OUT, tbl, "part_*.parquet")))
    if not parts:
        return None
    try:
        dcol = _date_col_of(tbl)
        vals = []
        for p in parts:
            vals.extend(pd.read_parquet(p, columns=[dcol])[dcol].astype(str))
        vals = [v.replace("-", "").split(" ")[0][:8] for v in vals]
        vals = [v for v in vals if len(v) == 8 and v.isdigit()]
        return max(vals) if vals else None
    except Exception:
        return None


def _date_col_of(tbl):
    return {
        "ts_income": "end_date",
        "ts_balancesheet": "end_date",
        "ts_cashflow": "end_date",
        "ts_fina_indicator": "end_date",
        "ts_index_weight": "trade_date",
    }.get(tbl, "trade_date")


def sync_parquet_table(tbl, meta, m, missing_keys=None):
    """分页拉全量 → 分片 parquet；done 后重跑按最新日期增量续拉。"""
    if missing_keys is not None:
        if tbl != "tdx_sector_daily":
            raise ValueError("显式补缺仅支持通达信板块日线")
        return _sync_sector_missing(missing_keys, m)
    date_col = _date_col_of(tbl)
    total = meta.get("rows", 0)
    st = m.setdefault(tbl, {"offset": 0, "part": 0, "rows": 0, "done": False})
    buf = []
    start_time = time.time()
    rows_before_run = st[
        "rows"
    ]  # A3 闸门基线：st["rows"] 为跨会话累计值，本次实拉=差值

    def flush():
        if not buf:
            return
        df = pd.DataFrame(buf)
        df.to_parquet(_part_path(tbl, st["part"]), index=False)
        st["part"] += 1
        buf.clear()

    # 增量：已 done 的表，从本地最新日期继续
    params = {}
    latest = _parquet_latest_date(tbl) if st.get("done") else None
    if latest:
        ld = latest.replace("-", "").split(" ")[0]
        params["start_date"] = ld  # 重叠一天由去重兜底
        mode = "incremental from %s" % ld
    else:
        mode = "full"
    # 增量模式从 offset 0 翻页（远端 offset 基于 start_date 过滤后结果集，
    # 月度拉取表 manifest offset=累计行数非全表 offset，必须重置 0 否则秒过空）
    if latest:
        st["offset"] = 0
    print("[%s] %s rows=%s" % (tbl, mode, total))
    while True:
        p = dict(params)
        p["limit"] = PAGE
        p["offset"] = st["offset"]
        d = _get("/api/data/table/" + tbl, p)
        rows = d.get("rows", [])
        cols = d.get("columns", [])
        if not rows:
            # A3 加固：空页重探一次（沿用既有退避参数），区分对端真空与瞬时空页
            time.sleep(RETRY_DELAYS[0])
            d2 = _get("/api/data/table/" + tbl, p)
            rows = d2.get("rows", [])
            cols = d2.get("columns", cols)
            if not rows:
                break
        if latest:
            # 增量去重：丢掉本地已有的最新日（重叠窗口内）
            di = cols.index(date_col) if date_col in cols else None
            if di is not None:
                ld = latest.replace("-", "").split(" ")[0]
                rows = [r for r in rows if str(r[di]).replace("-", "")[:8] > ld]
                if not rows:
                    break
        buf.extend(dict(zip(cols, r)) for r in rows)
        st["offset"] += len(rows)
        st["rows"] += len(rows)
        if len(buf) >= FLUSH_PAGES * PAGE:
            flush()
            _save_manifest(m)
        if st["offset"] >= d.get("total", total) and d.get("total"):
            break
        time.sleep(SLEEP)
    flush()
    # 增量模式下 offset 语义还原（下次重跑仍从头翻页，靠 start_date 过滤）
    if latest:
        st["offset"] = 0
    # A3 治本闸门（20261020 老田批 A 组，方向1）：空页/截断不得无条件等同完成——
    # closed on failure：可疑完成态不盖 done，raise 进表级 failed 聚合 → 非零退出，
    # 进度已随翻页存 manifest，重跑续传。
    pulled_this_run = st["rows"] - rows_before_run
    if latest is None:
        # 全量模式三查（total 为远端广告行数；total<=0 时远端元数据异常，跳过比对）
        if total > 0 and pulled_this_run <= 0:
            raise RuntimeError(
                "[%s] 假完成拦截：全量模式实拉 0 行但远端广告 rows=%s（HTTP 200 零行软失败，不盖 done）"
                % (tbl, total)
            )
        n_parts_now = len(glob.glob(os.path.join(OUT, tbl, "part_*.parquet")))
        if total > 0 and pulled_this_run > 0 and n_parts_now == 0:
            raise RuntimeError(
                "[%s] 假完成拦截：实拉 %d 行但零分片落盘（不盖 done）"
                % (tbl, pulled_this_run)
            )
        if total > 0 and abs(pulled_this_run - total) > max(total * 0.01, 1000):
            raise RuntimeError(
                "[%s] 完整性闸门：实拉 %d 行 vs 远端广告 %s 行，差异超 1%%（不盖 done，重跑续传）"
                % (tbl, pulled_this_run, total)
            )
    else:
        # 增量模式：total 是全表行数，与 start_date 过滤后的结果集不可比，
        # 广告行数比对仅在全量模式执行；增量零新行=正常完成，不设闸不误杀。
        pass
    st["done"] = True
    _save_manifest(m)
    n_parts = len(glob.glob(os.path.join(OUT, tbl, "part_*.parquet")))
    print(
        "[%s] DONE rows=%d parts=%d %.0fs"
        % (tbl, st["rows"], n_parts, time.time() - start_time)
    )


# ── SQLite 信息表（公告/新闻/研报） ─────────────────────────
DATE_COL_SQLITE = {
    "stock_notice": "notice_date",
    "stock_news": "pub_date",
    "stock_report": "pub_date",
}


def _sqlite_latest_date(tbl):
    """本地 sqlite 表最新日期（增量起点）。"""
    if not os.path.exists(INFO_DB):
        return None
    dcol = DATE_COL_SQLITE[tbl]
    try:
        conn = sqlite3.connect(INFO_DB, timeout=30)
        v = conn.execute("SELECT MAX(%s) FROM %s" % (dcol, tbl)).fetchone()[0]  # nosec B608 - 值参数化/内部标识符（已抽查）
        conn.close()
        return str(v).replace("-", "")[:8] if v else None
    except sqlite3.OperationalError:
        return None


def sync_sqlite_table(tbl, meta, m):
    """K92 修复：远端按日期 DESC 排序且滚动窗口（rows 守恒，新数据插
    头部、旧尾部滚出）——旧 offset 续传永远拉旧尾部，INSERT OR IGNORE
    全忽略，本地停在首拉水位。改日期增量（与 parquet 同款）：done 后
    带 start_date=本地最新日重翻，重叠日由 IGNORE 幂等兜底。"""
    st = m.setdefault(tbl, {"offset": 0, "rows": 0, "done": False})
    params = {}
    if st.get("created"):
        latest = _sqlite_latest_date(tbl)
        if latest:
            params["start_date"] = latest
            st["offset"] = 0
            print("[%s] incremental from %s" % (tbl, latest))
    conn = sqlite3.connect(INFO_DB, timeout=30)
    start_time = time.time()
    while True:
        p = dict(params)
        p.update({"limit": PAGE, "offset": st["offset"]})
        d = _get("/api/data/table/" + tbl, p)
        rows = d.get("rows", [])
        cols = d.get("columns", [])
        if not rows:
            break
        ph = ",".join("?" * len(cols))
        if not st.get("created"):
            conn.execute(
                "CREATE TABLE IF NOT EXISTS %s (%s)"
                % (tbl, ",".join("%s TEXT" % c for c in cols))
            )
            conn.execute("CREATE INDEX IF NOT EXISTS ix_%s ON %s(ts_code)" % (tbl, tbl))
            st["created"] = True
        conn.executemany(
            "INSERT OR IGNORE INTO %s (%s) VALUES (%s)" % (tbl, ",".join(cols), ph),
            [tuple(str(v) for v in r) for r in rows],
        )
        conn.commit()
        st["offset"] += len(rows)
        st["rows"] += len(rows)
        _save_manifest(m)
        if st["offset"] >= d.get("total", 0) and d.get("total"):
            break
        time.sleep(SLEEP)
    conn.close()
    st["offset"] = 0  # 日期增量模式每次重翻，offset 语义还原
    st["done"] = True
    _save_manifest(m)
    print("[%s] DONE rows=%d %.0fs" % (tbl, st["rows"], time.time() - start_time))


def sync_rtmin(m):
    """ts_rt_min：按日期切片（每日全市场 ~120 万行 → 240 页/日）。"""
    meta = _remote_tables().get("ts_rt_min", {})
    latest = _parquet_latest_date("ts_rt_min")
    print("[ts_rt_min] remote=%s from=%s" % (meta.get("rows", "?"), latest or "start"))
    st = m.setdefault("ts_rt_min", {"offset": 0, "part": 0, "rows": 0, "done": False})
    buf = []

    def flush():
        if buf:
            pd.DataFrame(buf).to_parquet(
                _part_path("ts_rt_min", st["part"]), index=False
            )
            st["part"] += 1
            buf.clear()

    # 以单日为切分单位（trade_date 精确过滤），已 done 从最新日期续
    cur_date = (latest or "").replace("-", "")[:8]
    dates = []
    if latest:
        dates = [cur_date]  # 增量：仅补最新日（滚动窗口）
    else:
        dates = ["FULL"]  # 全量：不带日期翻页
    for tag in dates:
        while True:
            p = {"limit": PAGE, "offset": st["offset"]}
            if tag != "FULL":
                p["start_date"] = tag
                p["end_date"] = tag
            d = _get("/api/data/table/ts_rt_min", p, timeout=120)
            rows = d.get("rows", [])
            cols = d.get("columns", [])
            if not rows:
                break
            buf.extend(dict(zip(cols, r)) for r in rows)
            st["offset"] += len(rows)
            st["rows"] += len(rows)
            if len(buf) >= FLUSH_PAGES * PAGE:
                flush()
                _save_manifest(m)
            if st["offset"] >= d.get("total", 0) and d.get("total"):
                break
            time.sleep(SLEEP)
    flush()
    st["offset"] = 0
    st["done"] = True
    _save_manifest(m)
    print("[ts_rt_min] DONE rows=%d" % st["rows"])


def status():
    m = _load_manifest()
    remote = _remote_tables()
    print(
        "%-22s %10s %10s %8s %6s %s"
        % ("table", "remote", "local", "part", "done", "mode")
    )
    for tbl, meta in sorted(remote.items()):
        st = m.get(tbl, {})
        parts = len(glob.glob(os.path.join(OUT, tbl, "part_*.parquet")))
        if tbl in SQLITE_TABLES:
            local = st.get("rows", "-")
            parts = "-"
        else:
            local = st.get("rows", "-")
        print(
            "%-22s %10s %10s %8s %6s %s"
            % (
                tbl,
                meta.get("rows", "?"),
                local,
                parts,
                "Y" if st.get("done") else "-",
                "skip(rtmin)" if tbl in SKIP_DEFAULT else "",
            )
        )
    if os.path.exists(INFO_DB):
        conn = sqlite3.connect(INFO_DB)
        for t in SQLITE_TABLES:
            try:
                n = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]  # nosec B608 - 值参数化/内部标识符（已抽查）
                print("tdx_info.db %-14s %d rows" % (t, n))
            except sqlite3.OperationalError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tdx_sync.py", exc)
        conn.close()
    print(
        "db: %.1f MB" % (os.path.getsize(INFO_DB) / 1e6)
        if os.path.exists(INFO_DB)
        else "no info db yet"
    )


CORE_LOCK = os.path.join(os.environ.get("TEMP", r"C:\Temp"), "tdx_sync_core.lock")
_CORE_LOCK_MAX_AGE_S = 2 * 3600  # 2h 视为陈锁（任务上限 2h）


def _pid_alive(pid):
    """Windows 安全的进程存活探测（判例：ollama_lock._alive——勿用
    os.kill(pid,0)，Windows 下会真杀进程）。查询失败按死进程处理
    （宁可重抢，代价只是一轮并行；同 ollama_lock 口径）。"""
    if pid <= 0:
        return False
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            k32.GetExitCodeProcess(h, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    except Exception:
        return False


def _core_lock_acquire():
    """进程级互斥锁：防多链路并发直跑本脚本（K-STOCK-391 实证冲突：
    ps1 锁只护 v2 启动器，外部直跑绕锁抢写同一 out.log）。

    返回 True=拿到锁；False=已有活跃实例（打印 SKIP、调用方应静默退 0
    ——与 start_tdx_sync.ps1 v2 的 SKIP-already-running 判例一致，
    不触发失败告警链）。

    陈锁自愈（T-I4）：锁残留 age>=2h **且**锁内 pid 无存活佐证才清理，
    清理留痕 LOCK-CLEAR（对齐 ps1 v2 措辞）；pid 仍存活则 SKIP——
    ts_rt_min 全量 3-4h 超阈值，仅凭 age 强夺会误杀长任务（T-Q3
    实证 10-18 起三次会话无 exit 行锁残留，正是本函数要自愈的场景）。
    旧版锁无 pid 行/损坏时回退纯 age 判定保底。"""
    import time as _t

    while True:
        try:
            fd = os.open(CORE_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write("%d %d\n" % (os.getpid(), int(_t.time())))
            return True
        except FileExistsError:
            try:
                age = _t.time() - os.path.getmtime(CORE_LOCK)
            except OSError:
                continue  # 锁刚好被释放，重试
            # 读锁内 pid（本锁写入格式 "pid epoch"；读不到=None 回退 age）
            try:
                with open(CORE_LOCK, encoding="ascii") as handle:
                    pid = int(handle.readline().split()[0])
            except Exception:
                pid = 0
            if pid > 0 and _pid_alive(pid):
                print(
                    "[lock] SKIP: another tdx_sync running "
                    "(pid %d alive, lock age %ds)" % (pid, age)
                )
                return False
            if pid > 0 and age < _CORE_LOCK_MAX_AGE_S:
                # pid 已死但年龄未超阈值：同为死锁残留，即刻回收
                # （对齐 ps1 v2 判例 LOCK-CLEAR dead pid，不等满 2h）
                print("[lock] LOCK-CLEAR dead pid=%d (age %ds)" % (pid, age))
            elif pid <= 0 and age < _CORE_LOCK_MAX_AGE_S:
                print(
                    "[lock] SKIP: another tdx_sync running "
                    "(lock age %ds < %ds, no pid line)" % (age, _CORE_LOCK_MAX_AGE_S)
                )
                return False
            else:
                print(
                    "[lock] LOCK-CLEAR stale lock age=%ds "
                    "(pid=%d not alive)" % (age, pid)
                )
            try:
                os.remove(CORE_LOCK)
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tdx_sync.py", exc)


def _core_lock_release():
    try:
        os.remove(CORE_LOCK)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tdx_sync.py", exc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--tables", default="")
    ap.add_argument("--include-rtmin", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--missing-keys-json")
    ap.add_argument("--backfill-receipt")
    a = ap.parse_args()
    if a.missing_keys_json:
        if (
            not a.backfill_receipt
            or a.all
            or a.status
            or a.include_rtmin
            or a.tables not in ("", "tdx_sector_daily")
        ):
            ap.error("显式补缺需回执路径，且不能混用其他任务")
        keys = _parse_missing_keys(a.missing_keys_json)
        audit = {"requested_keys": sorted(keys), "rejected": []}
        m = _load_manifest()
        try:
            added = _sync_sector_missing(keys, m, audit=audit)
        except Exception as error:
            audit["error"] = str(error)
            _write_backfill_receipt(a.backfill_receipt, audit, None)
            raise
        _write_backfill_receipt(a.backfill_receipt, audit, added)
        return
    if a.backfill_receipt:
        ap.error("回执参数需与显式缺键JSON一起使用")
    os.makedirs(OUT, exist_ok=True)
    if a.status:
        status()
        return
    remote = _remote_tables_with_retry()
    if not _core_lock_acquire():
        return  # 已有实例在跑：静默退 0（SKIP 判例）
    try:
        rc = _run_sync(a, remote)
    finally:
        _core_lock_release()
    if rc:
        raise SystemExit(rc)  # 有表失败：非零退出，交 wrapper 重试与告警链


def _remote_tables_with_retry(tries=5, gap=30):
    """远端元数据探针（K-STOCK-425 根因修复）。

    慢性病实证：10-13~14 的 exit=1 全部死于本探针未被包裹——远端
    /api/data/status 抖动一次整个进程即死（wrapper 3 attempt 亦全灭
    同处）。表级失败 _run_sync 本就吞错续跑，探针才是唯一断链点。
    现加长重试：5 次 × 30s 间隔（覆盖远端重启窗口），仍失败才抛。
    """
    last = None
    for i in range(tries):
        try:
            return _remote_tables()
        except Exception as e:  # noqa: BLE001
            last = e
            print(
                "[probe] /api/data/status attempt %d/%d failed: %s"
                % (i + 1, tries, str(e)[:200])
            )
            if i < tries - 1:
                time.sleep(gap)
    raise last


def _run_sync(a, remote):
    if a.tables:
        tables = [t.strip() for t in a.tables.split(",") if t.strip()]
    else:
        tables = [t for t in remote if t not in SKIP_DEFAULT]
        if a.include_rtmin:
            tables.append("ts_rt_min")
    m = _load_manifest()
    # 先小表后大表（尽快见到产出；大表中断可续传）
    tables.sort(key=lambda t: remote.get(t, {}).get("rows", 0))
    failed = []  # 表级失败聚合（closed on failure：不再吞错后仍退 0）
    for tbl in tables:
        meta = remote.get(tbl)
        if not meta:
            print("[%s] not in remote, skip" % tbl)
            continue
        try:
            if tbl == "ts_rt_min":
                sync_rtmin(m)
            elif tbl in SQLITE_TABLES:
                sync_sqlite_table(tbl, meta, m)
            else:
                sync_parquet_table(tbl, meta, m)
        except Exception as e:
            print("[%s] ERROR %s (进度已存，重跑续传)" % (tbl, e))
            failed.append(tbl)
    print("ALL DONE. manifest=%s" % MANIFEST)
    if failed:
        print("[exit] %d table(s) FAILED: %s" % (len(failed), ", ".join(failed)))
        return 1
    return 0


def _parse_missing_keys(path):
    import json
    from datetime import datetime

    with open(path, encoding="utf-8") as handle:
        values = json.load(handle)
    if not isinstance(values, list) or not 1 <= len(values) <= 128:
        raise ValueError("缺键JSON必须为1至128项数组")
    keys = set()
    for value in values:
        if not isinstance(value, dict) or set(value) != {"code", "trade_date"}:
            raise ValueError("每项必须仅含code/trade_date")
        code, day = value["code"], value["trade_date"]
        if (
            not isinstance(code, str)
            or len(code) != 6
            or not code.isdigit()
            or not code.startswith(("880", "881"))
        ):
            raise ValueError("无效通达信板块代码")
        if (
            not isinstance(day, str)
            or len(day) != 8
            or datetime.strptime(day, "%Y%m%d").strftime("%Y%m%d") != day
        ):
            raise ValueError("无效交易日")
        if (code, day) in keys:
            raise ValueError("缺键JSON有重复键")
        keys.add((code, day))
    return keys


def _row_rejection(row):
    import math

    fields = ("open", "high", "low", "close", "vol", "amount")
    if any(field not in row for field in fields):
        return "six_fields_missing"
    for field in fields:
        value = row[field]
        if value is None:
            return "six_fields_unknown"
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            return "six_fields_nonfinite_or_nonnumeric"
    if row["vol"] < 0 or row["amount"] < 0:
        return "negative_volume_or_amount"
    if min(row[f] for f in ("open", "high", "low", "close")) <= 0:
        return "nonpositive_price"
    if row["low"] > min(row["open"], row["close"]) or row["high"] < max(
        row["open"], row["close"]
    ):
        return "ohlc_range_invalid"
    return None


def _write_backfill_receipt(path, audit, added):
    import json
    import math

    def safe(value):
        if isinstance(value, float) and not math.isfinite(value):
            return {"raw_nonfinite": repr(value)}
        if isinstance(value, dict):
            return {k: safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [safe(v) for v in value]
        return value

    audit["added"] = added
    audit["qualification"] = "仅六字段合同；源身份与相邻控制另行验收"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(safe(audit), handle, ensure_ascii=False, indent=2, allow_nan=False)


def _sector_store_snapshot():
    """仅读目录/元数据与manifest指纹；作为串行补缺的变化检测。"""
    import hashlib
    import re
    import pyarrow.parquet as pq

    files = []
    rows = 0
    next_part = 0
    for path in sorted(
        glob.glob(os.path.join(OUT, "tdx_sector_daily", "part_*.parquet"))
    ):
        stat = os.stat(path)
        files.append((path, stat.st_size, stat.st_mtime_ns))
        rows += pq.ParquetFile(path).metadata.num_rows
        match = re.fullmatch(r"part_(\d+)\.parquet", os.path.basename(path))
        if not match:
            raise RuntimeError("仓分片名称不合约")
        next_part = max(next_part, int(match.group(1)) + 1)
    with open(MANIFEST, "rb") as handle:
        manifest_sha = hashlib.sha256(handle.read()).hexdigest()
    return {
        "files": files,
        "rows": rows,
        "next_part": next_part,
        "manifest_sha256": manifest_sha,
    }


def _validate_sector_store(m, snapshot):
    import json

    st = m.get("tdx_sector_daily")
    if not st or type(st.get("rows")) is not int or st["rows"] != snapshot["rows"]:
        raise RuntimeError("仓物理行数与manifest不一致，先核对再补缺")
    if type(st.get("part")) is not int or st["part"] < 0:
        raise RuntimeError("仓manifest分片序号无效")
    with open(MANIFEST, encoding="utf-8") as handle:
        if json.load(handle) != m:
            raise RuntimeError("仓manifest与调用方状态不一致，停止补缺")


def _commit_sector_missing(staged, m, audit, snapshot):
    """临时片验收、排他发布；manifest提交失败时回滚本次新片。"""
    import copy
    import hashlib
    import json
    import uuid
    import pyarrow as pa
    import pyarrow.parquet as pq

    if _sector_store_snapshot() != snapshot:
        raise RuntimeError("仓在读取源后发生变化，停止补缺")
    _validate_sector_store(m, snapshot)
    st = m["tdx_sector_daily"]
    index = max(st.get("part", 0), snapshot["next_part"])
    final = _part_path("tdx_sector_daily", index)
    temporary = final + "." + uuid.uuid4().hex + ".tmp"
    schema = pa.schema(
        [(name, pa.large_string()) for name in ("code", "trade_date")]
        + [
            (name, pa.float64())
            for name in ("open", "high", "low", "close", "vol", "amount")
        ]
        + [
            (name, pa.large_string())
            for name in (
                "_fetched_at",
                "actual_received_at",
                "source_provider",
                "source_endpoint",
                "source_request_parameters",
                "source_response_json_sha256",
            )
        ]
    )
    table = pa.Table.from_pylist(staged, schema=schema)
    published = False
    digest = None
    proposed = copy.deepcopy(m)
    proposed["tdx_sector_daily"]["part"] = index + 1
    proposed["tdx_sector_daily"]["rows"] = snapshot["rows"] + len(staged)
    try:
        pq.write_table(table, temporary)
        if pq.ParquetFile(temporary).metadata.num_rows != len(
            staged
        ) or not pq.ParquetFile(temporary).schema_arrow.equals(schema):
            raise RuntimeError("新片临时文件验收失败")
        with open(temporary, "r+b") as handle:
            os.fsync(handle.fileno())
        with open(temporary, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        if _sector_store_snapshot() != snapshot:
            raise RuntimeError("仓在临时片准备期间发生变化，停止补缺")
        # NTFS硬链接具有目标已存在时失败的语义，不覆盖现有片。
        os.link(temporary, final)
        published = True
        after_publish = _sector_store_snapshot()
        if (
            after_publish["manifest_sha256"] != snapshot["manifest_sha256"]
            or [f for f in after_publish["files"] if f[0] != final] != snapshot["files"]
            or after_publish["rows"] - len(staged) != snapshot["rows"]
        ):
            raise RuntimeError("仓在新片发布期间发生变化，停止补缺")
        _save_manifest(proposed)
    except Exception as error:
        with open(MANIFEST, encoding="utf-8") as handle:
            disk_manifest = json.load(handle)
        if published and disk_manifest == proposed:
            m.clear()
            m.update(proposed)
            audit["commit"] = {
                "part": final,
                "part_sha256": digest,
                "added": len(staged),
                "rows_after": proposed["tdx_sector_daily"]["rows"],
                "manifest_acknowledgement_error": str(error),
            }
            return len(staged)
        if published:
            with open(final, "rb") as handle:
                still_owned = hashlib.sha256(handle.read()).hexdigest() == digest
            if still_owned:
                os.remove(final)
        raise
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    m.clear()
    m.update(proposed)
    audit["commit"] = {
        "part": final,
        "part_sha256": digest,
        "added": len(staged),
        "rows_after": proposed["tdx_sector_daily"]["rows"],
    }
    return len(staged)


def _existing_sector_keys(keys):
    """只读现有键列；任何分片读取失败均中止，避免误当不存在。"""
    requested = set(keys)
    found = set()
    for part in glob.glob(os.path.join(OUT, "tdx_sector_daily", "part_*.parquet")):
        frame = pd.read_parquet(part, columns=["code", "trade_date"])
        found.update(
            (str(c), str(d))
            for c, d in frame.itertuples(index=False, name=None)
            if (str(c), str(d)) in requested
        )
    return found


def _sync_sector_missing(keys, m, audit=None):
    """每缺键仅一次小请求，先核响应合同；过滤失效立即中止。"""
    from datetime import datetime, timezone
    import hashlib
    import json

    requested = {(str(c), str(d)) for c, d in keys}
    if len(requested) > 128:
        raise ValueError("显式缺键最多128项")
    for c, d in requested:
        if len(c) != 6 or not c.isdigit() or not c.startswith(("880", "881")):
            raise ValueError("无效通达信板块代码")
        if datetime.strptime(d, "%Y%m%d").strftime("%Y%m%d") != d:
            raise ValueError("无效日期")
    snapshot = _sector_store_snapshot()
    _validate_sector_store(m, snapshot)
    pending = requested - _existing_sector_keys(requested)
    audit = audit if audit is not None else {}
    audit.setdefault("rejected", [])
    staged = []
    required = {"code", "trade_date"}
    for code, day in sorted(pending):
        reply = _get(
            "/api/data/table/tdx_sector_daily",
            {"code": code, "start_date": day, "end_date": day, "limit": 2, "offset": 0},
        )
        received_at = datetime.now(timezone.utc).isoformat()
        rows, cols = reply.get("rows"), reply.get("columns")
        total = reply.get("total")
        if (
            not isinstance(rows, list)
            or not isinstance(cols, list)
            or len(cols) != len(set(cols))
        ):
            raise ValueError("响应行列合同不完整")
        if (
            not isinstance(total, int)
            or isinstance(total, bool)
            or total != len(rows)
            or total > 1
        ):
            raise ValueError("过滤失效/重复键/分页响应：不继续扫全市场")
        if not rows:
            continue
        if not required <= set(cols) or len(rows[0]) != len(cols):
            raise ValueError("响应字段合同不完整")
        row = dict(zip(cols, rows[0]))
        if (str(row["code"]), str(row["trade_date"])) != (code, day):
            raise ValueError("响应代码日期与显式缺键不匹配")
        reason = _row_rejection(row)
        if (
            reason is None
            and row.get("_fetched_at") is not None
            and not isinstance(row["_fetched_at"], str)
        ):
            reason = "source_timestamp_invalid"
        if reason:
            audit["rejected"].append(
                {"code": code, "trade_date": day, "reason": reason, "source_row": row}
            )
            continue
        row.setdefault("_fetched_at", None)
        row["actual_received_at"] = received_at
        row["source_provider"] = "tdx-trading-table"
        row["source_endpoint"] = "/api/data/table/tdx_sector_daily"
        row["source_request_parameters"] = json.dumps(
            {"code": code, "start_date": day, "end_date": day, "limit": 2, "offset": 0},
            sort_keys=True,
        )
        row["source_response_json_sha256"] = hashlib.sha256(
            json.dumps(
                reply, sort_keys=True, ensure_ascii=False, allow_nan=False
            ).encode("utf-8")
        ).hexdigest()
        staged.append(row)
    # 写前再次排除已有键，保留原片与旧价格，原量额NULL不转换为0。
    existing = _existing_sector_keys(requested)
    staged = [
        r for r in staged if (str(r["code"]), str(r["trade_date"])) not in existing
    ]
    if not staged:
        return 0
    return _commit_sector_missing(staged, m, audit, snapshot)


if __name__ == "__main__":
    main()
