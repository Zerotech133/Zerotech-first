# -*- coding: utf-8 -*-
r"""个股批量监控引擎（工单 K-STOCK-ANALYSIS-MON-20261104）。

职责：
  1. 股票池管理：pools.json（默认 watchlist 从 analysis/watchlist.txt 初始化）；
  2. 规则评估：涨跌幅异动 / 成交额放量 / 涨跌停 / 新增事件 / 评级变化 / 数据滞后；
  3. 告警持久化：SQLite（monitor.db），dedupe_key 主键 + INSERT OR IGNORE 去重；
  4. CLI：python -m analysis.monitor --run/--list-alerts/--pools/--add/--remove，输出 JSON。

数据源（只读，不写生产 curated / 分析库）：
  * curated 行情 parquet：<MAIN_ROOT>/quant_backtest/data/tushare_curated/daily.parquet
    （ts_code, trade_date, close, pct_chg, amount）与 daily_basic.parquet；
  * 分析库 SQLite：<ANALYSIS_WORK>/analysis.db 的 runs（评级台账）与
    events（事件库：code, date, category, category_final, reviewed, title）；
  * 市场最新交易日：daily.parquet 中 trade_date <= as_of 的 max（PIT 截断）。

PIT 口径：as_of（YYYY-MM-DD，默认今天）及之前（含当日）的行情才参与评估；
  events 窗口同样截止到 as_of；runs 台账按 run date <= as_of 读取（评级变化不得读未来 run）；
  任何告警 trade_date 越界时夹到 as_of，dedupe_key 同步重建（键内不得出现未来日期）。

路径约定（均可环境变量覆盖，便于隔离测试；不覆盖时用下面模块常量）：
  WORK    = $ANALYSIS_WORK    默认 C:/项目/analysis_work
  MON_DIR = $WORK/monitor
  DB      = $MON_DIR/monitor.db
  POOLS   = $MON_DIR/pools.json

离线测试：evaluate()/run_monitor() 支持注入 data_provider(spec) -> dict；
  spec = {"codes": [...], "as_of": "YYYY-MM-DD"}，
  返回 {"daily": DataFrame, "basic": DataFrame, "runs": list, "events": list,
        "market_latest": "YYYY-MM-DD"}。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
import uuid
from datetime import date, datetime, timedelta

import pandas as pd

try:  # Windows 跨进程文件锁
    import msvcrt  # type: ignore
except ImportError:  # pragma: no cover - 非 Windows
    msvcrt = None
try:  # POSIX 跨进程文件锁兜底
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows
    fcntl = None

log = logging.getLogger(__name__)

# ============================ 路径与常量 ============================
MAIN_ROOT = os.environ.get("ANALYSIS_MAIN_ROOT", r"C:\项目\老田的AI助手")
# 数据工作根：行情缓存/分析库/监控库都在其下（生产 = C:\项目\analysis_work）
WORK = os.environ.get("ANALYSIS_WORK", r"C:\项目\analysis_work")
MON_DIR = os.path.join(WORK, "monitor")
DB = os.path.join(MON_DIR, "monitor.db")
POOLS = os.path.join(MON_DIR, "pools.json")
# curated 行情与默认观察池文件（只读）
CURATED = os.environ.get(
    "ANALYSIS_CURATED",
    os.path.join(MAIN_ROOT, "quant_backtest", "data", "tushare_curated"),
)
WATCHLIST = os.environ.get(
    "ANALYSIS_WATCHLIST",
    os.path.join(MAIN_ROOT, "analysis", "watchlist.txt"),
)
# 记录导入时的原始值：用于区分「测试直接 monkeypatch 常量」与「默认派生路径」
_ORIG_DB = DB
_ORIG_POOLS = POOLS

# 代码校验正则：与工单规格逐字一致（沪主板60 / 科创68 / 深主板00 / 创业板30 / 北交所8开头，含 SH/SZ/BJ）
CODE_PATTERN = r"^(60[0-9]{4}|68[0-9]{4}|00[0-9]{4}|30[0-9]{4}|8[0-9]{6})\.(SH|SZ|BJ)$"
CODE_RE = re.compile(CODE_PATTERN)

# 规则常量
ALL_RULES = ("price_move", "volume_spike", "limit_move", "new_event", "rating_change", "data_stale")
SEVERITIES = ("high", "medium", "low")
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
PRICE_MOVE_THRESHOLD = 5.0      # 单日涨跌幅异动阈值（%）
LIMIT_MOVE_THRESHOLD = 9.5      # 涨跌停幅度阈值（%）
VOLUME_SPIKE_RATIO = 2.5        # 成交额放量倍数阈值
VOLUME_BASE_DAYS = 20           # 成交额均额基准天数
EVENT_WINDOW_DAYS = 3  # 回看偏移：as_of-3 ~ as_of 含两端，实际窗口 = 4 个自然日（P2 标注真实）
# 风险事件关键词：重整/退市/ST/诉讼/停牌/控制权/问询/监管（+ 同义的风险警示/立案/处罚/仲裁）
RISK_EVENT_RE = re.compile(r"重整|退市|风险警示|ST|诉讼|仲裁|停牌|控制权|问询|监管|立案|处罚")

# 路径非法/数据缺失等统一异常类型（CLI 捕获后转 {"ok": False, "error": ...}）
class MonitorError(Exception):
    """监控引擎业务异常（fail-visible）。"""


# ============================ 路径解析 ============================
# 设计说明：优先取环境变量（pytest / 多环境隔离），其次用模块常量。
# 这样三种隔离方式都生效：
#   1) 设 ANALYSIS_WORK 环境变量（推荐，测试/临时目录）；
#   2) 直接 monkeypatch 模块常量 DB / POOLS（测试常用）；
#   3) 直接 monkeypatch 模块常量 MON_DIR（派生 DB/POOLS）。
def _work_dir():
    """当前数据工作根（环境变量优先）。"""
    return os.environ.get("ANALYSIS_WORK") or WORK


def _mon_dir():
    """监控数据目录：$ANALYSIS_WORK/monitor（未设环境变量时用模块常量）。"""
    env = os.environ.get("ANALYSIS_WORK")
    if env:
        return os.path.join(env, "monitor")
    return MON_DIR


def _db_path():
    """监控库路径：环境变量派生 > 显式 monkeypatch 的 DB > MON_DIR 派生。"""
    env = os.environ.get("ANALYSIS_WORK")
    if env:
        return os.path.join(env, "monitor", "monitor.db")
    if DB != _ORIG_DB:
        return DB
    return os.path.join(MON_DIR, "monitor.db")


def _pools_path():
    """股票池文件路径：环境变量派生 > 显式 monkeypatch 的 POOLS > MON_DIR 派生。"""
    env = os.environ.get("ANALYSIS_WORK")
    if env:
        return os.path.join(env, "monitor", "pools.json")
    if POOLS != _ORIG_POOLS:
        return POOLS
    return os.path.join(MON_DIR, "pools.json")


def _watchlist_path():
    """默认 watchlist 文件路径（不存在时 load_pools 退化为空池并告警）。"""
    return os.environ.get("ANALYSIS_WATCHLIST") or WATCHLIST


def _curated_dir():
    """curated parquet 目录（测试可 monkeypatch 模块常量 CURATED）。"""
    return os.environ.get("ANALYSIS_CURATED") or CURATED


def _analysis_db_path():
    """分析库（runs/events）路径：默认 $ANALYSIS_WORK/analysis.db。"""
    return os.environ.get("ANALYSIS_DB") or os.path.join(_work_dir(), "analysis.db")


# ============================ 通用小工具 ============================

def _today_iso():
    """本地今天（YYYY-MM-DD）。"""
    return date.today().isoformat()


def _norm_date(value):
    """把 20261103 / 2026-11-03 / 2026/11/03 / date / datetime 归一为 YYYY-MM-DD。

    非法值（None / NaN / 空串 / 非法日期）返回空串，调用方按「无日期」处理。
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text or text.lower() in ("nan", "none", "nat", "null"):
        return ""
    match = re.match(r"^(\d{4})[-/]?(\d{2})[-/]?(\d{2})", text)
    if not match:
        return ""
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return ""


def _compact(iso_date):
    """YYYY-MM-DD -> YYYYMMDD（curated parquet 的 trade_date 是紧凑格式）。"""
    return str(iso_date or "").replace("-", "")


def _shift_iso(iso_date, days):
    """ISO 日期平移 N 个自然日。"""
    return (date.fromisoformat(iso_date) + timedelta(days=days)).isoformat()


def _empty_severity():
    """固定返回 high/medium/low 三键，便于接口稳定（无告警时计数为 0）。"""
    return {name: 0 for name in SEVERITIES}


def _split_tokens(values):
    """把 ["a,b", "c"] / "a b" 这类输入拆成扁平 token 列表。"""
    if isinstance(values, str):
        values = [values]
    out = []
    for item in values or []:
        for part in re.split(r"[,;\s]+", str(item).strip()):
            if part:
                out.append(part)
    return out


def _chunks(values, size=500):
    """SQLite IN 查询分块（规避变量数量上限）。"""
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _placeholders(values):
    return ",".join("?" * len(values))


def _is_missing_table_error(exc):
    return isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc).lower()


# ============================ 股票池管理 ============================

def is_valid_code(code):
    """代码是否命中工单校验正则（非法代码拒绝入库）。"""
    return bool(CODE_RE.match(str(code or "").strip()))


def _as_code_list(codes):
    """归一化代码列表：接受 str/list，去空去重保序，任一非法即 ValueError（拒绝整批）。"""
    if isinstance(codes, str):
        codes = [codes]
    result = []
    for raw in codes or []:
        code = str(raw).strip()
        if not code:
            continue
        if not is_valid_code(code):
            raise ValueError("非法股票代码: %s（应形如 600337.SH）" % code)
        if code not in result:
            result.append(code)
    return result


def _default_watchlist_codes():
    """读取默认观察池文件（每行一个代码，支持 # 注释/行内空白）。"""
    path = _watchlist_path()
    if not os.path.exists(path):
        log.warning("默认观察池文件不存在，watchlist 初始化为空: %s", path)
        return []
    codes = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                code = line.split()[0]
                if is_valid_code(code):
                    codes.append(code)
                else:
                    log.warning("watchlist 中忽略非法代码: %s", code)
    except OSError as exc:
        log.warning("读取默认观察池失败: %s", exc)
        return []
    return list(dict.fromkeys(codes))


def _read_pools_file():
    """读 pools.json；文件不存在返回 None（触发自动初始化）。"""
    path = _pools_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise MonitorError("股票池文件解析失败 %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise MonitorError("股票池文件格式错误（应为 {池名: [代码]}）: %s" % path)
    pools = {}
    for name, codes in data.items():
        if isinstance(codes, dict):  # 兼容 {"codes": [...]} 结构
            codes = codes.get("codes") or []
        if not isinstance(codes, list):
            raise MonitorError("股票池 %s 的代码列表格式错误: %r" % (name, codes))
        valid = []
        for code in codes:
            text = str(code).strip()
            if not text:
                continue
            if not is_valid_code(text):
                log.warning("股票池 %s 中忽略非法代码: %s", name, text)
                continue
            if text not in valid:
                valid.append(text)
        pools[str(name)] = valid
    return pools


# ---------------- pools.json 并发写保护（P1-3） ----------------
# 整文件 RMW 在 ThreadingHTTPServer 多线程 + 多进程下会丢更新；统一用
# 「进程内 threading.RLock + 跨进程文件锁（pools.json.lock）」串行化，
# 锁内重读-合并-校验；临时文件唯一（pid+uuid），写后 os.replace + fsync。

POOLS_LOCK_TIMEOUT = 15.0   # 等锁上限（秒）
POOLS_LOCK_POLL = 0.05      # 轮询间隔（秒）
_POOLS_THREAD_LOCK = threading.RLock()


def _pools_lock_path():
    return _pools_path() + ".lock"


def _fsync_dir(path):
    """目录项落盘（Windows 不支持目录 fsync，失败忽略）。"""
    try:
        fd = os.open(path or ".", os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


@contextlib.contextmanager
def _pools_file_lock(timeout=None):
    """跨进程文件锁 + 进程内 RLock；等锁超时抛 MonitorError（fail-visible）。"""
    timeout = POOLS_LOCK_TIMEOUT if timeout is None else float(timeout)
    if not _POOLS_THREAD_LOCK.acquire(timeout=timeout):
        raise MonitorError("获取股票池写锁超时（进程内）")
    handle = None
    try:
        lock_path = _pools_lock_path()
        os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        deadline = time.time() + timeout
        while True:
            try:
                if msvcrt is not None:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                elif fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() >= deadline:
                    raise MonitorError("获取股票池文件锁超时: %s" % lock_path)
                time.sleep(POOLS_LOCK_POLL)
        handle = fd
        yield lock_path
    finally:
        if handle is not None:
            try:
                if msvcrt is not None:
                    os.lseek(handle, 0, os.SEEK_SET)
                    msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
                elif fcntl is not None:
                    fcntl.flock(handle, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(handle)
        _POOLS_THREAD_LOCK.release()


def _write_pools(pools):
    """原子写 pools.json：唯一 tmp（pid+uuid）+ flush/fsync + os.replace；调用方须持锁。"""
    mon = _mon_dir()
    os.makedirs(mon, exist_ok=True)
    path = _pools_path()
    tmp_path = "%s.tmp-%d-%s" % (path, os.getpid(), uuid.uuid4().hex[:12])
    fd = os.open(tmp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(pools, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _fsync_dir(os.path.dirname(path))
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _update_pools(mutator):
    """锁内读-改-写：重读最新 pools.json -> mutator(pools) -> 原子写；返回 mutator 结果。"""
    with _pools_file_lock():
        pools = _read_pools_file()
        if pools is None:
            pools = {"watchlist": _default_watchlist_codes()}
        result = mutator(pools)
        _write_pools(pools)
        return result


def load_pools():
    """读取全部股票池 -> {池名: [代码]}；pools.json 不存在时用默认 watchlist 自动初始化。"""
    pools = _read_pools_file()
    if pools is not None:
        return pools
    # 初始化也走锁内双重检查：避免并发 add 刚写好的池被默认值覆盖
    try:
        with _pools_file_lock():
            pools = _read_pools_file()
            if pools is None:
                pools = {"watchlist": _default_watchlist_codes()}
                _write_pools(pools)
                log.info("已初始化股票池文件 %s（watchlist %d 只）",
                         _pools_path(), len(pools["watchlist"]))
            return pools
    except MonitorError:
        raise
    except OSError as exc:
        # 初始化落盘失败不阻断读取（例如只读环境），但要 fail-visible 留痕
        log.warning("股票池文件初始化落盘失败: %s", exc)
        return {"watchlist": _default_watchlist_codes()}


def list_pools():
    """列出股票池及每个池的代码数 -> {池名: 代码数}。"""
    return {name: len(codes) for name, codes in load_pools().items()}


def set_pool(name, codes):
    """整体覆盖一个股票池（不存在则新建）；返回归一化后的代码列表。"""
    pool_name = str(name or "").strip()
    if not pool_name:
        raise ValueError("股票池名称不能为空")
    code_list = _as_code_list(codes)

    def _mutate(pools):
        pools[pool_name] = code_list
        return code_list

    return _update_pools(_mutate)


def add_codes(name, codes):
    """向股票池追加代码（池不存在则自动新建）；返回本次新增的代码列表。"""
    pool_name = str(name or "").strip()
    if not pool_name:
        raise ValueError("股票池名称不能为空")
    code_list = _as_code_list(codes)

    def _mutate(pools):
        current = pools.setdefault(pool_name, [])
        added = [code for code in code_list if code not in current]
        current.extend(added)
        return added

    return _update_pools(_mutate)


def remove_codes(name, codes):
    """从股票池移除代码；返回本次实际移除的代码列表（池不存在抛 ValueError）。"""
    pool_name = str(name or "").strip()
    if not pool_name:
        raise ValueError("股票池名称不能为空")
    code_list = _as_code_list(codes)

    def _mutate(pools):
        if pool_name not in pools:
            raise ValueError("股票池不存在: %s" % pool_name)
        current = pools[pool_name]
        removed = [code for code in code_list if code in current]
        if removed:
            pools[pool_name] = [code for code in current if code not in removed]
        return removed

    return _update_pools(_mutate)


# ============================ 数据归一化（provider 输出） ============================

_DAILY_COLUMNS = ["ts_code", "trade_date", "close", "pct_chg", "amount"]


def _empty_daily():
    """空行情表（列与 curated daily 对齐，另带内部列 _date）。"""
    frame = pd.DataFrame(columns=_DAILY_COLUMNS)
    frame["_date"] = []
    return frame


def _prep_daily(raw):
    """把 data_provider 返回的 daily 归一化：补列、日期转 ISO、数值强转。"""
    if raw is None:
        return _empty_daily()
    frame = raw.copy() if isinstance(raw, pd.DataFrame) else pd.DataFrame(raw)
    if frame.empty:
        return _empty_daily()
    for column in _DAILY_COLUMNS:
        if column not in frame.columns:
            if column in ("close", "pct_chg", "amount"):
                frame[column] = float("nan")
            else:  # 缺 ts_code / trade_date 视为无效数据源
                log.warning("daily 数据缺少关键列 %s，按空数据处理", column)
                return _empty_daily()
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip()
    frame["_date"] = frame["trade_date"].map(_norm_date)
    frame = frame[frame["_date"] != ""].copy()
    for column in ("close", "pct_chg", "amount"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _prep_runs(raw):
    """把 data_provider 返回的 runs 归一化为 dict 列表（非法 code 丢弃）。"""
    if raw is None:
        return []
    rows = raw.to_dict("records") if isinstance(raw, pd.DataFrame) else list(raw)
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip()
        if not is_valid_code(code):
            continue
        result.append({
            "code": code,
            "as_of": _norm_date(row.get("as_of")),
            "started": str(row.get("started") or ""),
            "status": str(row.get("status") or ""),
            "rating": str(row.get("rating") or "").strip(),
        })
    return result


def _prep_events(raw):
    """把 data_provider 返回的 events 归一化为 dict 列表（date 转 ISO，空日期丢弃）。"""
    if raw is None:
        return []
    rows = raw.to_dict("records") if isinstance(raw, pd.DataFrame) else list(raw)
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip()
        event_date = _norm_date(row.get("date"))
        if not is_valid_code(code) or not event_date:
            continue
        result.append({
            "code": code,
            "date": event_date,
            "category": str(row.get("category") or "").strip(),
            "category_final": str(row.get("category_final") or "").strip(),
            "reviewed": row.get("reviewed"),
            "title": str(row.get("title") or "").strip(),
        })
    return result


# ============================ 规则评估 ============================

def _alert(code, rule, severity, title, detail, trade_date):
    """构造统一告警结构（dedupe_key = code|rule|trade_date）。"""
    date_iso = _norm_date(trade_date)
    return {
        "code": code,
        "rule": rule,
        "severity": severity,
        "title": title,
        "detail": detail,
        "trade_date": date_iso,
        "dedupe_key": "%s|%s|%s" % (code, rule, date_iso),
    }


def _latest_price_move_alert(code, frame):
    """规则1：最新交易日 |pct_chg| >= 5% -> high(>=9.5) / medium。"""
    if frame.empty:
        return []
    row = frame.iloc[-1]
    pct = float(row["pct_chg"]) if pd.notna(row["pct_chg"]) else 0.0
    if abs(pct) < PRICE_MOVE_THRESHOLD:
        return []
    severity = "high" if abs(pct) >= LIMIT_MOVE_THRESHOLD else "medium"
    action = "上涨" if pct > 0 else "下跌"
    title = "单日%s %.2f%%" % (action, abs(pct))
    close = float(row["close"]) if pd.notna(row["close"]) else float("nan")
    detail = "%s 收盘 %.2f，涨跌幅 %+.2f%%（异动阈值 ±%.0f%%）" % (
        row["_date"], close, pct, PRICE_MOVE_THRESHOLD)
    return [_alert(code, "price_move", severity, title, detail, row["_date"])]


def _volume_spike_alert(code, frame):
    """规则2：当日 amount / 前 20 个交易日均额 >= 2.5 -> medium。"""
    if frame.empty:
        return []
    amount = frame["amount"] if "amount" in frame.columns else None
    if amount is None or amount.empty:
        return []
    today_amount = amount.iloc[-1]
    if pd.isna(today_amount):
        return []
    base = amount.iloc[:-1].dropna().tail(VOLUME_BASE_DAYS)
    if base.empty:
        return []  # 没有历史基准，无法判定放量
    base_mean = float(base.mean())
    today_value = float(today_amount)
    if base_mean <= 0 or today_value <= 0:
        return []
    ratio = today_value / base_mean
    if ratio < VOLUME_SPIKE_RATIO:
        return []
    row = frame.iloc[-1]
    title = "成交额放量 %.2f 倍" % ratio
    detail = "%s 成交额 %.2f，前 %d 日均额 %.2f，放量 %.2f 倍（阈值 %.1f）" % (
        row["_date"], today_value, len(base), base_mean, ratio, VOLUME_SPIKE_RATIO)
    return [_alert(code, "volume_spike", "medium", title, detail, row["_date"])]


def _limit_move_alert(code, frame):
    """规则3：|pct_chg| >= 9.5% -> high（标题注明 触及涨停/跌停）。"""
    if frame.empty:
        return []
    row = frame.iloc[-1]
    if pd.isna(row["pct_chg"]):
        return []
    pct = float(row["pct_chg"])
    if abs(pct) < LIMIT_MOVE_THRESHOLD:
        return []
    action = "涨停" if pct > 0 else "跌停"
    close = float(row["close"]) if pd.notna(row["close"]) else float("nan")
    title = "触及%s（%+.2f%%）" % (action, pct)
    detail = "%s 收盘 %.2f，涨跌幅 %+.2f%%，触及%s幅度阈值 %.1f%%" % (
        row["_date"], close, pct, action, LIMIT_MOVE_THRESHOLD)
    return [_alert(code, "limit_move", "high", title, detail, row["_date"])]


def _event_is_risk(event):
    """事件是否属风险类（分类优先 category_final，命中标题/分类关键词）。"""
    text = " ".join([
        event.get("category") or "",
        event.get("category_final") or "",
        event.get("title") or "",
    ])
    return bool(RISK_EVENT_RE.search(text))


def _new_event_alert(code, events, as_of_iso):
    """规则4：as_of 及前 3 个自然日（含两端，共 4 天）事件；风险类 -> high，其余 -> medium；每代码只出一条。"""
    if not events:
        return []
    start_iso = _shift_iso(as_of_iso, -EVENT_WINDOW_DAYS)
    window = [ev for ev in events if start_iso <= ev["date"] <= as_of_iso]
    if not window:
        return []
    risky = [ev for ev in window if _event_is_risk(ev)]
    severity = "high" if risky else "medium"
    # 风险类优先展示，其次按日期倒序；detail 最多列 3 条标题
    ordered = sorted(window, key=lambda ev: ev["date"], reverse=True)
    ordered.sort(key=lambda ev: 0 if _event_is_risk(ev) else 1)
    titles = [ev["title"] for ev in ordered if ev["title"]][:3]
    if risky:
        title = "新增事件 %d 条（含风险类 %d 条）" % (len(window), len(risky))
    else:
        title = "新增事件 %d 条" % len(window)
    detail = "近 %d 个自然日新增事件 %d 条：%s" % (
        EVENT_WINDOW_DAYS + 1, len(window), "；".join(titles) if titles else "（无标题）")
    latest_date = max(ev["date"] for ev in window)
    return [_alert(code, "new_event", severity, title, detail, latest_date)]


def _rating_change_alert(code, runs):
    """规则5：最近两次 run 的 rating 不同 -> medium（标题「评级变化: A→B」）。"""
    if not runs:
        return []
    rated = [run for run in runs if run["rating"]]
    if len(rated) < 2:
        return []
    ordered = sorted(rated, key=lambda run: (run["as_of"], run["started"]), reverse=True)
    latest, previous = ordered[0], ordered[1]
    if latest["rating"] == previous["rating"]:
        return []
    title = "评级变化: %s→%s" % (previous["rating"], latest["rating"])
    detail = "%s %s → %s %s" % (
        previous["as_of"] or "未知日期", previous["rating"],
        latest["as_of"] or "未知日期", latest["rating"])
    trade_date = latest["as_of"] or previous["as_of"] or _today_iso()
    return [_alert(code, "rating_change", "medium", title, detail, trade_date)]


def _data_stale_alert(code, frame, market_latest):
    """规则6：最新 daily 日期 < 市场最新交易日 -> low（标题「数据滞后」）。"""
    if not market_latest:
        return []
    latest = frame["_date"].iloc[-1] if not frame.empty else ""
    if latest and latest >= market_latest:
        return []
    detail = "个股最新行情 %s，市场最新交易日 %s（行情未更新到最新交易日）" % (
        latest or "无行情数据", market_latest)
    return [_alert(code, "data_stale", "low", "数据滞后", detail, market_latest)]


def _eval_rule(rule, code, frame, as_of_iso, code_events, code_runs, market_latest):
    """按规则名分发到具体评估函数。"""
    if rule == "price_move":
        return _latest_price_move_alert(code, frame)
    if rule == "volume_spike":
        return _volume_spike_alert(code, frame)
    if rule == "limit_move":
        return _limit_move_alert(code, frame)
    if rule == "new_event":
        return _new_event_alert(code, code_events, as_of_iso)
    if rule == "rating_change":
        return _rating_change_alert(code, code_runs)
    if rule == "data_stale":
        return _data_stale_alert(code, frame, market_latest)
    return []


def evaluate(codes, as_of=None, rules=None, data_provider=None):
    """规则评估主入口 -> list[alert dict]。

    参数：
      codes: 代码列表（也接受单个代码字符串）；
      as_of: 评估基准日 YYYY-MM-DD（默认今天），仅用 <= as_of 的行情；
      rules: 规则名列表（None=全部），如 ["price_move", "data_stale"]；
      data_provider: 可选 callable(spec) -> dict（离线测试注入），
                     spec = {"codes": [...], "as_of": "YYYY-MM-DD"}。

    告警字段：code / rule / severity(high|medium|low) / title / detail /
             trade_date / dedupe_key(=code|rule|trade_date)。
    """
    as_of_iso = _norm_date(as_of) if as_of else _today_iso()
    if not as_of_iso:
        raise ValueError("as_of 日期非法: %r（应为 YYYY-MM-DD）" % (as_of,))
    code_list = _as_code_list(codes)
    if not code_list:
        return []
    if rules is None:
        rule_set = set(ALL_RULES)
    else:
        rule_set = set(_split_tokens(rules))
        unknown = rule_set - set(ALL_RULES)
        if unknown:
            raise ValueError("未知规则: %s（可用: %s）" % (",".join(sorted(unknown)), ",".join(ALL_RULES)))
    provider = data_provider if data_provider is not None else _default_provider
    payload = provider({"codes": list(code_list), "as_of": as_of_iso}) or {}
    daily = _prep_daily(payload.get("daily"))
    runs = _prep_runs(payload.get("runs"))
    events = _prep_events(payload.get("events"))
    market_latest = _norm_date(payload.get("market_latest"))

    # PIT：只保留 trade_date <= as_of 的行情
    if not daily.empty:
        daily = daily[daily["_date"] <= as_of_iso]
    # P1-2：注入数据同样截断——事件/评级台账不得越界，市场最新日不得晚于 as_of
    events = [event for event in events if event["date"] <= as_of_iso]
    runs = [run for run in runs if run["as_of"] and run["as_of"] <= as_of_iso]
    if market_latest and market_latest > as_of_iso:
        market_latest = as_of_iso
    frames = {}
    if not daily.empty:
        subset = daily[daily["ts_code"].isin(code_list)]
        for code, part in subset.groupby("ts_code", sort=False):
            frames[code] = part.sort_values("_date", kind="stable")
    events_by_code = {}
    for event in events:
        events_by_code.setdefault(event["code"], []).append(event)
    runs_by_code = {}
    for run in runs:
        runs_by_code.setdefault(run["code"], []).append(run)

    alerts = []
    for code in code_list:
        frame = frames.get(code, _empty_daily())
        for rule in ALL_RULES:
            if rule not in rule_set:
                continue
            alerts.extend(_eval_rule(rule, code, frame, as_of_iso,
                                     events_by_code.get(code, []),
                                     runs_by_code.get(code, []),
                                     market_latest))
    # PIT 收敛：任何告警 trade_date 不得越过 as_of（兜底注入数据/规则实现疏漏），
    # 越界时夹到 as_of 并重建 dedupe_key（键内不得含未来日期）。
    for item in alerts:
        if item["trade_date"] and item["trade_date"] > as_of_iso:
            item["trade_date"] = as_of_iso
            item["dedupe_key"] = "%s|%s|%s" % (item["code"], item["rule"], as_of_iso)
    # 稳定排序：high -> medium -> low，再按 code/rule
    alerts.sort(key=lambda item: (_SEVERITY_RANK.get(item["severity"], 9), item["code"], item["rule"]))
    return alerts


# ============================ 默认数据源（curated parquet + 分析库） ============================

def _parquet_path(name):
    return os.path.join(_curated_dir(), name)


def _read_curated_daily(codes, as_of_iso):
    """读取指定代码的 curated 行情（只读到 as_of，向前多取 60 天覆盖 20 日均额基准）。"""
    path = _parquet_path("daily.parquet")
    if not os.path.exists(path):
        raise MonitorError("行情数据不存在: %s" % path)
    columns = ["ts_code", "trade_date", "close", "pct_chg", "amount"]
    start_compact = _compact(_shift_iso(as_of_iso, -60))
    end_compact = _compact(as_of_iso)
    try:
        frame = pd.read_parquet(path, columns=columns, filters=[("ts_code", "in", list(codes))])
    except Exception as exc:  # 有些 parquet 引擎不支持 filters，回退全量后过滤
        log.warning("按代码过滤读取 daily.parquet 失败，回退全量读取: %s", exc)
        frame = pd.read_parquet(path, columns=columns)
        frame = frame[frame["ts_code"].isin(list(codes))]
    if frame.empty:
        return frame
    sample = str(frame["trade_date"].iloc[0])
    if "-" in sample:  # 兼容 ISO 日期的临时 parquet
        date_iso = frame["trade_date"].map(_norm_date)
        return frame[(date_iso >= _shift_iso(as_of_iso, -60)) & (date_iso <= as_of_iso)].copy()
    trade_date = frame["trade_date"].astype(str)
    return frame[(trade_date >= start_compact) & (trade_date <= end_compact)].copy()


def _read_curated_basic(codes, as_of_iso):
    """读取指定代码的 daily_basic（估值/换手辅助数据；缺失不阻断监控）。"""
    path = _parquet_path("daily_basic.parquet")
    if not os.path.exists(path):
        log.warning("daily_basic.parquet 不存在，估值辅助数据置空: %s", path)
        return pd.DataFrame(columns=["ts_code", "trade_date", "close"])
    columns = ["ts_code", "trade_date", "close"]
    end_compact = _compact(as_of_iso)
    try:
        frame = pd.read_parquet(path, columns=columns,
                                filters=[("ts_code", "in", list(codes)),
                                         ("trade_date", "<=", end_compact)])
    except Exception as exc:
        log.warning("读取 daily_basic.parquet 失败，估值辅助数据置空: %s", exc)
        return pd.DataFrame(columns=columns)
    return frame


def _read_market_latest(as_of_iso):
    """市场最新交易日 = daily.parquet 中 trade_date <= as_of 的 max（返回 YYYY-MM-DD）。

    P1-2：回看历史日期时不得把 as_of 之后才发生的行情当成「市场最新」，
    否则所有个股都会被误判 data_stale（且去重键落成未来日期）。
    """
    path = _parquet_path("daily.parquet")
    if not os.path.exists(path):
        return ""
    try:
        series = pd.read_parquet(path, columns=["trade_date"])["trade_date"]
        if series.empty:
            return ""
        sample = str(series.iloc[0])
        if "-" in sample:  # ISO 日期（临时 parquet）
            values = series.map(_norm_date)
            eligible = values[(values != "") & (values <= as_of_iso)]
        else:  # 生产紧凑格式 YYYYMMDD
            compact = series.astype(str)
            eligible = compact[compact <= _compact(as_of_iso)]
            return _norm_date(eligible.max()) if not eligible.empty else ""
        return str(eligible.max()) if not eligible.empty else ""
    except Exception as exc:
        log.warning("读取市场最新交易日失败: %s", exc)
        return ""


def _open_analysis_db():
    """只读打开分析库；不存在返回 None（评级/事件规则退化为无数据）。"""
    path = _analysis_db_path()
    if not os.path.exists(path):
        log.info("分析库不存在，评级/事件规则无数据: %s", path)
        return None
    try:
        return sqlite3.connect(path, timeout=10)
    except sqlite3.Error as exc:
        log.warning("分析库打开失败: %s", exc)
        return None


def _query_dicts(connection, sql, params):
    cursor = connection.execute(sql, params)
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _read_runs(codes, as_of_iso):
    """读取指定代码、run date <= as_of 的 run 台账（rating 非空才被规则消费）。

    P1-2：评级变化规则不得读到 as_of 之后才产生的 run；as_of 兼容 ISO/紧凑两种格式
    （replace 归一后按 8 位日期比较），NULL/空日期无法定位时间，按不可见处理。
    """
    connection = _open_analysis_db()
    if connection is None:
        return []
    try:
        rows = []
        bound = _compact(as_of_iso)
        for chunk in _chunks(codes):
            sql = ("SELECT code, as_of, started, status, rating FROM runs "
                   "WHERE code IN (%s) AND replace(as_of, '-', '') <= ?" % _placeholders(chunk))
            rows.extend(_query_dicts(connection, sql, list(chunk) + [bound]))
        return rows
    except sqlite3.Error as exc:
        log.warning("读取 runs 台账失败（按无数据处理）: %s", exc)
        return []
    finally:
        connection.close()


def _read_events(codes, as_of_iso):
    """读取 as_of 及前 3 个自然日（共 4 天）窗口内的事件；兼容无 category_final 列的旧库。"""
    connection = _open_analysis_db()
    if connection is None:
        return []
    start_iso = _shift_iso(as_of_iso, -EVENT_WINDOW_DAYS)
    base_sql = ("SELECT code, date, category, {extra}reviewed, title FROM events "
                "WHERE code IN (%s) AND date >= ? AND date <= ?")
    rows = []
    try:
        try:
            for chunk in _chunks(codes):
                sql = base_sql.format(extra="category_final, ") % _placeholders(chunk)
                rows.extend(_query_dicts(connection, sql, list(chunk) + [start_iso, as_of_iso]))
        except sqlite3.OperationalError as exc:
            if not _is_missing_table_error(exc) and "category_final" not in str(exc).lower():
                raise
            log.info("events 表无 category_final 列，降级读取: %s", exc)
            rows = []
            for chunk in _chunks(codes):
                sql = base_sql.format(extra="'' AS category_final, ") % _placeholders(chunk)
                rows.extend(_query_dicts(connection, sql, list(chunk) + [start_iso, as_of_iso]))
        return rows
    except sqlite3.Error as exc:
        log.warning("读取 events 失败（按无数据处理）: %s", exc)
        return []
    finally:
        connection.close()


def _default_provider(spec):
    """默认数据源：curated parquet + 分析库（只读）；异常向上抛给 fail-visible 处理。"""
    codes = list(spec.get("codes") or [])
    as_of_iso = _norm_date(spec.get("as_of")) or _today_iso()
    if not codes:
        return {"daily": _empty_daily(), "basic": pd.DataFrame(), "runs": [],
                "events": [], "market_latest": ""}
    daily = _read_curated_daily(codes, as_of_iso)
    basic = _read_curated_basic(codes, as_of_iso)
    runs = _read_runs(codes, as_of_iso)
    events = _read_events(codes, as_of_iso)
    market_latest = _read_market_latest(as_of_iso)
    return {"daily": daily, "basic": basic, "runs": runs, "events": events,
            "market_latest": market_latest}


# ============================ 持久化与运行 ============================

SCHEMA = """
CREATE TABLE IF NOT EXISTS monitor_runs (
  run_id TEXT PRIMARY KEY,
  ts TEXT,
  pool TEXT,
  scanned INTEGER DEFAULT 0,
  alerts INTEGER DEFAULT 0,
  new_alerts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS monitor_alerts (
  dedupe_key TEXT PRIMARY KEY,
  ts TEXT,
  code TEXT,
  rule TEXT,
  severity TEXT,
  title TEXT,
  detail TEXT,
  trade_date TEXT
);
CREATE INDEX IF NOT EXISTS idx_monitor_alerts_ts ON monitor_alerts(ts);
CREATE INDEX IF NOT EXISTS idx_monitor_alerts_code ON monitor_alerts(code);
"""


def _connect():
    """打开（必要时创建）监控库并建表。"""
    mon = _mon_dir()
    os.makedirs(mon, exist_ok=True)
    connection = sqlite3.connect(_db_path(), timeout=15)
    connection.executescript(SCHEMA)
    return connection


def _existing_dedupe_keys(keys):
    """查询已存在告警 key（dry-run 预演 new_alerts 用；库不存在视为全新增）。"""
    db_path = _db_path()
    if not keys or not os.path.exists(db_path):
        return set()
    try:
        connection = sqlite3.connect(db_path, timeout=10)
        try:
            found = set()
            for chunk in _chunks(keys):
                sql = "SELECT dedupe_key FROM monitor_alerts WHERE dedupe_key IN (%s)" % _placeholders(chunk)
                found.update(row[0] for row in connection.execute(sql, chunk))
            return found
        finally:
            connection.close()
    except sqlite3.Error as exc:
        log.warning("dry-run 查询已有告警失败（按全部新增处理）: %s", exc)
        return set()


def _persist_run(run_id, pool_name, scanned, items):
    """落库：告警 INSERT OR IGNORE 去重，再写 run 台账；返回新增告警数。"""
    connection = _connect()
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        new_alerts = 0
        with connection:  # 单事务
            for alert in items:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO monitor_alerts"
                    "(dedupe_key, ts, code, rule, severity, title, detail, trade_date)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (alert["dedupe_key"], ts, alert["code"], alert["rule"], alert["severity"],
                     alert["title"], alert["detail"], alert["trade_date"]))
                if cursor.rowcount and cursor.rowcount > 0:
                    new_alerts += 1
            connection.execute(
                "INSERT OR REPLACE INTO monitor_runs"
                "(run_id, ts, pool, scanned, alerts, new_alerts) VALUES (?,?,?,?,?,?)",
                (run_id, ts, pool_name, scanned, len(items), new_alerts))
        return new_alerts
    finally:
        connection.close()


def _resolve_pool_codes(pool):
    """把 pool 参数解析成 (显示名, 代码列表)：字符串=池名，列表=自定义代码。"""
    if isinstance(pool, str):
        pool_name = pool.strip() or "watchlist"
        pools = load_pools()
        if pool_name not in pools:
            raise ValueError("股票池不存在: %s（已有: %s）" % (
                pool_name, ",".join(sorted(pools)) or "无"))
        return pool_name, list(pools[pool_name])
    return "custom", _as_code_list(pool)


def run_monitor(pool="watchlist", as_of=None, rules=None, dry_run=False, data_provider=None):
    """运行一次监控 -> 结果 dict（ok/run_id/pool/scanned/alerts/new_alerts/by_severity/items）。

    dry_run=True 时只评估不落库（run_id 为 None），new_alerts 为「相对已有库的新增」预演数；
    任何异常都 fail-visible：返回 {"ok": False, "error": "..."}，不抛给调用方。
    """
    result = {
        "ok": False,
        "run_id": None,
        "pool": pool if isinstance(pool, str) else "custom",
        "as_of": None,
        "scanned": 0,
        "alerts": 0,
        "new_alerts": 0,
        "by_severity": _empty_severity(),
        "items": [],
    }
    try:
        as_of_iso = _norm_date(as_of) if as_of else _today_iso()
        if not as_of_iso:
            raise ValueError("as_of 日期非法: %r" % (as_of,))
        pool_name, codes = _resolve_pool_codes(pool)
        result["pool"] = pool_name
        result["as_of"] = as_of_iso
        result["scanned"] = len(codes)
        items = evaluate(codes, as_of=as_of_iso, rules=rules, data_provider=data_provider)
        by_severity = _empty_severity()
        for alert in items:
            by_severity[alert["severity"]] = by_severity.get(alert["severity"], 0) + 1
        result["items"] = items
        result["alerts"] = len(items)
        result["by_severity"] = by_severity
        if dry_run:
            existing = _existing_dedupe_keys([alert["dedupe_key"] for alert in items])
            result["new_alerts"] = sum(1 for alert in items if alert["dedupe_key"] not in existing)
            result["dry_run"] = True
            result["ok"] = True
            log.info("监控 dry-run：池 %s 扫描 %d 只，告警 %d 条（预演新增 %d 条）",
                     pool_name, len(codes), len(items), result["new_alerts"])
            return result
        run_id = "mon-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6])
        new_alerts = _persist_run(run_id, pool_name, len(codes), items)
        result.update({"ok": True, "run_id": run_id, "new_alerts": new_alerts})
        log.info("监控运行完成：run_id=%s 池=%s 扫描=%d 告警=%d 新增=%d",
                 run_id, pool_name, len(codes), len(items), new_alerts)
        return result
    except Exception as exc:  # fail-visible：任何失败都返回错误结构
        log.exception("监控运行失败: %s", exc)
        result["ok"] = False
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        return result


# ============================ 查询接口 ============================

def _open_monitor_db_readonly():
    """只读打开已有监控库；不存在返回 None（读接口不落盘、不建库）。"""
    db_path = _db_path()
    if not os.path.exists(db_path):
        return None
    try:
        return sqlite3.connect(db_path, timeout=10)
    except sqlite3.Error as exc:
        raise MonitorError("监控库打开失败 %s: %s" % (db_path, exc))


def list_alerts(pool=None, limit=50, severity=None):
    """按 ts 倒序查询告警；可按股票池（按池内代码过滤）与 severity 过滤。"""
    if limit is None:
        limit = 50
    limit = int(limit)
    if limit <= 0:
        return []
    pool_codes = None
    if pool is not None:
        pool_name = str(pool).strip()
        pools = load_pools()
        if pool_name not in pools:
            return []
        pool_codes = list(pools[pool_name])
        if not pool_codes:
            return []
    severity_value = None
    if severity is not None:
        severity_value = str(severity).strip().lower()
        if severity_value not in SEVERITIES:
            raise ValueError("非法 severity: %r（可用: high/medium/low）" % (severity,))
    connection = _open_monitor_db_readonly()
    if connection is None:
        return []
    try:
        conditions = []
        params = []
        if severity_value:
            conditions.append("severity = ?")
            params.append(severity_value)
        if pool_codes is not None:
            conditions.append("code IN (%s)" % _placeholders(pool_codes))
            params.extend(pool_codes)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = ("SELECT dedupe_key, ts, code, rule, severity, title, detail, trade_date "
               "FROM monitor_alerts" + where + " ORDER BY ts DESC, rowid DESC LIMIT ?")
        params.append(limit)
        rows = _query_dicts(connection, sql, params)
        return rows
    except sqlite3.Error as exc:
        if _is_missing_table_error(exc):
            return []
        raise MonitorError("监控告警查询失败: %s" % exc)
    finally:
        connection.close()


def summary():
    """监控总览 -> {"pools": {池名: 代码数}, "alerts_total", "by_severity", "last_run"}。"""
    pools = list_pools()
    by_severity = _empty_severity()
    alerts_total = 0
    last_run = None
    db_path = _db_path()
    if os.path.exists(db_path):
        try:
            connection = sqlite3.connect(db_path, timeout=10)
            connection.row_factory = sqlite3.Row
            try:
                try:
                    for row in connection.execute(
                            "SELECT severity, COUNT(*) AS count FROM monitor_alerts GROUP BY severity"):
                        severity = str(row["severity"] or "")
                        count = int(row["count"] or 0)
                        if severity in by_severity:
                            by_severity[severity] = count
                        alerts_total += count
                except sqlite3.OperationalError as exc:
                    if not _is_missing_table_error(exc):
                        raise
                try:
                    row = connection.execute(
                        "SELECT run_id, ts, pool, scanned, alerts, new_alerts FROM monitor_runs "
                        "ORDER BY ts DESC, rowid DESC LIMIT 1").fetchone()
                    last_run = dict(row) if row else None
                except sqlite3.OperationalError as exc:
                    if not _is_missing_table_error(exc):
                        raise
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise MonitorError("监控总览查询失败: %s" % exc)
    return {
        "pools": pools,
        "alerts_total": alerts_total,
        "by_severity": by_severity,
        "last_run": last_run,
    }


# ============================ CLI ============================

def _build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m analysis.monitor",
        description="个股批量监控引擎：规则评估 / 股票池管理 / 告警查询（输出 JSON）")
    parser.add_argument("--run", action="store_true", help="运行一次监控")
    parser.add_argument("--list-alerts", action="store_true", help="查询告警（ts 倒序）")
    parser.add_argument("--pools", action="store_true", help="列出股票池及代码数")
    parser.add_argument("--add", action="append", default=[], metavar="CODE[,CODE]",
                        help="向股票池追加代码（可重复/逗号分隔）")
    parser.add_argument("--remove", action="append", default=[], metavar="CODE[,CODE]",
                        help="从股票池移除代码（可重复/逗号分隔）")
    parser.add_argument("--pool", default=None, help="股票池名（run/add/remove 默认 watchlist）")
    parser.add_argument("--as-of", default=None, help="评估基准日 YYYY-MM-DD（默认今天）")
    parser.add_argument("--limit", type=int, default=20, help="--list-alerts 返回条数（默认 20）")
    parser.add_argument("--severity", default=None, help="按严重级别过滤告警: high/medium/low")
    parser.add_argument("--rules", default=None, help="只跑指定规则（逗号分隔，默认全部）")
    parser.add_argument("--dry-run", action="store_true", help="只评估不落库（配合 --run）")
    return parser


def _dispatch(args):
    """CLI 动作分发：返回结果 dict（"ok" 字段决定进程退出码）。"""
    if args.add or args.remove:
        pool_name = (args.pool or "watchlist").strip()
        result = {"ok": True, "pool": pool_name}
        if args.remove:
            result["removed"] = remove_codes(pool_name, _split_tokens(args.remove))
        if args.add:
            result["added"] = add_codes(pool_name, _split_tokens(args.add))
        result["pools"] = list_pools()
        return result
    if args.run:
        rules = _split_tokens(args.rules) if args.rules else None
        return run_monitor(pool=(args.pool or "watchlist"), as_of=args.as_of,
                           rules=rules, dry_run=args.dry_run)
    if args.list_alerts:
        items = list_alerts(pool=args.pool, limit=args.limit,
                            severity=args.severity)
        return {"ok": True, "items": items, "total": len(items)}
    if args.pools:
        return {"ok": True, "pools": list_pools(), "detail": load_pools()}
    raise ValueError("未指定动作：请使用 --run / --list-alerts / --pools / --add / --remove")


def main(argv=None):
    """CLI 入口：输出单份 JSON，成功 rc=0，失败 fail-visible rc=1。"""
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _build_parser().parse_args(argv)
    try:
        result = _dispatch(args)
        rc = 0 if result.get("ok") else 1
    except Exception as exc:
        log.exception("CLI 执行失败: %s", exc)
        result = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
        rc = 1
    print(json.dumps(result, ensure_ascii=False, default=str))
    return rc


if __name__ == "__main__":
    sys.exit(main())
