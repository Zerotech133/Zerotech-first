# -*- coding: utf-8 -*-
"""W5 影子积累期 · 环境闸门三档虚拟对照日采集器（2026-10-14 gate-shadow-dev）。

v1.1 计划第十三节：qpzm/cross/双刀三方互证「40% 宽度闸门不够紧」，
统一观察带 45%（下限）~50%（候选）；影子期做「三档虚拟对照，只记录
不改行为」，W8 末统一评审是否切换。

职责：每日一次，对 θ∈{40,45,50} 三档，各策略逐日记录「虚拟开关状态」——
该策略当日信号集在 θ 档下会被拦掉哪些（数量+代码清单）。只读产线产物、
只写影子档，不碰任何策略行为、不注册计划任务、不推飞书。

数据源（三策略，产物缺失标 missing 不阻塞）：
  qd    双刀  portal/qd_scan_cache.json signals_now（qd_scanner 已注记
            breadth/gate_would_block=40 档；45/50 档由本采集器按同一
            口径 breadth<θ 复算。注记缺失时回退宽度序列最新锚，
            qd 锚语义为 T-1，与 lake_breadth breadth_ref 一致）
  qpzm  青萍 reports/rps_latest.json（latest + envs[A股].breadth/ret20 +
            res 全信号集=买入/观察/回避（环境闸门），闸门前口径；
            虚拟闸门 = breadth<θ 或 med20<-3%，med20 条件与产线一致不动）
  cross 缠论 data/sig_events/sig_events_market_v3_*.json 最新产物
            （meta.d1 当日事件；blocked = breadth<θ，与 cross 网格
            envgate 口径一致）

落档格式 data/gate_shadow_3tier.jsonl（每行一个 JSON 对象）：
  {
    "ts":            采集时刻（"2026-10-14 09:00:00"），
    "date":          信号日 YYYYMMDD（策略各自产物自然日期），
    "strategy":      "qd"/"qpzm"/"cross"，
    "theta":         40/45/50（宽度档，百分比），
    "n_total":       当日信号集数量（闸门前口径；missing 时 null），
    "n_blocked":     θ 档虚拟拦截数量，
    "blocked_codes": 被拦代码清单（升序；missing 时 null），
    "breadth":       当日宽度（0-1 小数；如可得），
    "med20":         20 日涨幅中位（0-1 小数；仅 qpzm 可得），
    "upfrac":        20 日上涨占比（如可得；当前产线快照未落，恒 null），
    "source_status": "ok"/"missing"（源产物不可得即标 missing），
    "src":           产物路径（missing 时为缺失原因摘要），
    "fwd":           null（前向收益占位，由周报脚本回填）
  }

幂等：同 date+strategy+theta+source_status 不重复落（进程内缓存 +
锁内全文件复核）；源从 missing 升级为 ok 时允许补落 ok 行（W8 评审
以 ok 行为准，missing 行只是当日覆盖度留痕）。

原子追加：O_APPEND 单次 write + fsync，配 .lock 文件防并发交错
（判例 2026-10-14 linkage_collector：锁内二次复核防双入口重复）。

挂载点（注记式 try/except，采集失败不影响产线；不碰计划任务、不改
qd_scanner——双刀侧直接读 qd_scanner 产物）：
  · qpzm_daily_batch.archive_daily_batch() 尾部（rps_resonance.run_daily
    每日链调用，14:40 双刀产物此时已落盘）→ 采 qd+qpzm
  · market_scan.scan_market() 产物落盘后 → 采 cross

用法:
  "C:/Program Files/Python311/python.exe" gate_shadow_collect.py
  ... --strategies qd,qpzm          # 只采部分策略
  ... --out PATH                    # 落档路径覆盖（测试用）
"""

import argparse
import glob
import json
import os
import sys
import time
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SHADOW = os.path.join(HERE, "data", "gate_shadow_3tier.jsonl")
LOCK = SHADOW + ".lock"
LOG = "C:/Temp/gate_shadow_collect.log"
STALE_LOCK_SEC = 120
THETAS = (40, 45, 50)

QD_CACHE = os.path.abspath(os.path.join(HERE, "..", "portal", "qd_scan_cache.json"))
QPZM_SNAP = os.path.join(HERE, "reports", "rps_latest.json")
CROSS_GLOB = os.path.join(HERE, "data", "sig_events", "sig_events_market_v3_*.json")
SERIES_FP = os.path.join(HERE, "reports", "qd_breadth_series.json")

_seen = {}  # 进程内幂等缓存：{落档路径: set("date|strategy|theta|status")}


def _log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/gate_shadow_collect.py", exc)


def _key(rec):
    """幂等键：同日同策略同档同状态不重复。"""
    return "%s|%s|%s|%s" % (
        rec["date"],
        rec["strategy"],
        rec["theta"],
        rec["source_status"],
    )


def _load_seen(path):
    """按落档路径各存一份缓存（判例：全局单缓存会在多 out 路径下串档）。"""
    if path not in _seen:
        seen = set()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        seen.add(_key(json.loads(line)))
                    except Exception:
                        continue  # 坏行跳过，不阻断
        _seen[path] = seen
    return _seen[path]


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
                    os.remove(LOCK)  # 陈旧锁回收
                    continue
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/gate_shadow_collect.py", exc)
            if time.time() >= deadline:
                return False
            time.sleep(0.2)


def _release_lock():
    try:
        os.remove(LOCK)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/gate_shadow_collect.py", exc)


# ── 数据源读取（全部只读；异常由调用方转 missing） ────────────
def series_anchor(fp=SERIES_FP):
    """宽度序列最新锚 → (date, breadth 0-1)。判例：qd_breadth_series
    dates/breadth 平行数组，末位即最新锚（gate-dev W3-5b 产出）。"""
    with open(fp, encoding="utf-8") as f:
        d = json.load(f)
    if not d.get("dates") or not d.get("breadth"):
        raise ValueError("series empty")
    return str(d["dates"][-1]), float(d["breadth"][-1])


def read_qd(qd_cache=QD_CACHE, series_fp=SERIES_FP):
    """双刀源 → (date, codes, breadth 0-1, src)。当日信号集=signals_now。

    注记口径：qd_scanner.annotate_breadth 给每单加 breadth（百分比刻度）
    与 gate_would_block（40 档）；45/50 档由本采集器按 breadth<θ 同口径
    复算。注记缺失（旧缓存）→ 回退宽度序列锚（qd 锚语义=T-1，与
    lake_breadth breadth_ref 一致，评审时按 T-1 对齐理解）。
    """
    with open(qd_cache, encoding="utf-8") as f:
        c = json.load(f)
    date = str(c.get("ts", ""))[:10].replace("-", "")
    sigs = c.get("signals_now") or []
    codes = sorted(str(s.get("code")) for s in sigs if s.get("code"))
    br = None
    for s in sigs:
        if s.get("breadth") is not None:
            br = float(s["breadth"]) / 100.0  # 百分比 → 0-1
            break
    src = str(qd_cache)
    if br is None:
        date_s, br = series_anchor(series_fp)
        if not date:
            date = date_s
        src += " (注记缺→序列锚 %s)" % date_s
    if not date:
        raise ValueError("qd cache 无 ts 且序列锚不可用")
    return date, codes, br, src


def read_qpzm(snap_fp=QPZM_SNAP):
    """青萍源 → (date, codes, breadth, med20, upfrac, src)。闸门前口径。

    判例（rps_resonance.market_env）：env.breadth=close>MA20 占比、
    ret20=全市场 20 日涨幅中位（即 med20）、upfrac=20 日上涨占比
    [20261014_m 起 market_env 已落快照；旧快照无此字段 → None，
    待次日批自然落盘]。
    虚拟闸门 = breadth<θ 或 med20<-0.03（med20 条件与产线一致不动，
    只虚拟宽度档）。
    """
    with open(snap_fp, encoding="utf-8") as f:
        s = json.load(f)
    date = str(s.get("latest") or "")
    if not date:
        raise ValueError("rps_latest 无 latest")
    env_cn = next(
        (e for e in (s.get("envs") or []) if "A股" in str(e.get("label", ""))), None
    )
    if env_cn is None or env_cn.get("breadth") is None:
        raise ValueError("rps_latest 无 A股 env")
    br = float(env_cn["breadth"])
    med = env_cn.get("ret20")
    med = float(med) if med is not None else None
    # [20261014_m metrics-dev] upfrac 透传（缺失/非法 → None，不阻塞）
    try:
        upf = env_cn.get("upfrac")
        upf = float(upf) if upf is not None else None
    except Exception:
        upf = None
    codes = []
    for r in s.get("res") or []:
        if r.get("market") != "CN":
            continue
        # 闸门前信号全集：买入/观察/回避（环境闸门）都算命中过
        if str(r.get("action", "")).startswith(("买入", "观察", "回避")):
            codes.append(str(r.get("code")))
    return date, sorted(codes), br, med, upf, str(snap_fp)


def read_cross(cross_glob=CROSS_GLOB, series_fp=SERIES_FP):
    """cross 源 → (date, codes, breadth, src)。最新产物 meta.d1 当日事件。

    blocked 口径与 cross_grid_backtest envgate 一致：breadth<θ 拦、
    ≥θ 放。宽度取宽度序列锚（cross 网格同用湖价宽度口径）。
    """
    fps = sorted(glob.glob(cross_glob), key=os.path.getmtime)
    if not fps:
        raise ValueError("无 sig_events_market_v3_* 产物")
    fp = fps[-1]
    with open(fp, encoding="utf-8") as f:
        d = json.load(f)
    meta = d.get("meta") or {}
    date = str(meta.get("d1") or "")
    if not date:
        raise ValueError("cross 产物无 meta.d1")
    codes = sorted(
        str(e.get("stock"))
        for e in (d.get("events") or [])
        if str(e.get("day")) == date
    )
    date_s, br = series_anchor(series_fp)
    src = str(fp) + (" (序列锚 %s)" % date_s if date_s != date else "")
    return date, codes, br, src


# ── 三档虚拟对照：θ 档下会被拦掉的清单 ────────────────────────
def blocked_at(theta, codes, breadth=None, med20=None, strategy="qd"):
    """返回 (n_blocked, blocked_codes)。

    口径（三策略统一观察带虚拟化，只动宽度档 θ）：
      qd/cross：breadth < θ/100 → 拦（与 qd_scanner.gate_would_block、
                cross_grid envgate 同式）
      qpzm    ：breadth < θ/100 或 med20 < -0.03 → 拦（med20 条件与
                产线 env 闸门一致，保持不动）
    breadth 未知（None）→ 当日无法判定，视为不拦（记 n_blocked=0，
    breadth 字段为 null 供评审识别）。
    """
    if not codes:
        return 0, []
    if strategy == "qpzm":
        med_hit = med20 is not None and med20 < -0.03
        if breadth is None:
            return (len(codes), list(codes)) if med_hit else (0, [])
        hit = (breadth < theta / 100.0) or med_hit
    else:
        if breadth is None:
            return 0, []
        hit = breadth < theta / 100.0
    return (len(codes), list(codes)) if hit else (0, [])


def _mk_rows(date, strategy, n_total, codes, breadth, med20, src, upfrac=None):
    rows = []
    for th in THETAS:
        nb, bc = blocked_at(th, codes, breadth, med20, strategy)
        rows.append(
            {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "date": date,
                "strategy": strategy,
                "theta": th,
                "n_total": n_total,
                "n_blocked": nb,
                "blocked_codes": sorted(bc),
                "breadth": None if breadth is None else round(breadth, 4),
                "med20": None if med20 is None else round(med20, 4),
                # [20261014_m] qpzm 源透传 upfrac；qd/cross 无此字段保持 None
                "upfrac": None if upfrac is None else round(upfrac, 4),
                "source_status": "ok",
                "src": str(src),
                "fwd": None,
            }
        )
    return rows


def _mk_missing(date, strategy, reason):
    rows = []
    for th in THETAS:
        rows.append(
            {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "date": date,
                "strategy": strategy,
                "theta": th,
                "n_total": None,
                "n_blocked": None,
                "blocked_codes": None,
                "breadth": None,
                "med20": None,
                "upfrac": None,
                "source_status": "missing",
                "src": reason[:160],
                "fwd": None,
            }
        )
    return rows


def collect_run(
    strategies=("qd", "qpzm", "cross"),
    out=SHADOW,
    qd_cache=QD_CACHE,
    qpzm_snap=QPZM_SNAP,
    cross_glob=CROSS_GLOB,
    series_fp=SERIES_FP,
):
    """日采入口（挂载点调用）：采三档影子行，返回本次落档行列表。

    任一策略源读失败/缺失 → 该策略落 missing 行，不阻塞其他策略；
    本函数自身不抛异常（挂载点 try/except 之外的最后一道防线）。
    """
    appended = []
    try:
        for st in strategies:
            try:
                if st == "qd":
                    date, codes, br, src = read_qd(qd_cache, series_fp)
                    rows = _mk_rows(date, st, len(codes), codes, br, None, src)
                elif st == "qpzm":
                    date, codes, br, med, upf, src = read_qpzm(qpzm_snap)
                    rows = _mk_rows(
                        date, st, len(codes), codes, br, med, src, upfrac=upf
                    )
                elif st == "cross":
                    date, codes, br, src = read_cross(cross_glob, series_fp)
                    rows = _mk_rows(date, st, len(codes), codes, br, None, src)
                else:
                    continue
            except Exception as e:
                # 判例：源缺失标 missing 不阻塞（qd 缓存当日未跑/
                # cross 产物未生成等都走这里）
                _log("collect_run %s source fail: %s" % (st, e))
                try:
                    date = series_anchor(series_fp)[0]
                except Exception:
                    date = time.strftime("%Y%m%d")
                rows = _mk_missing(date, st, "%s: %s" % (type(e).__name__, e))
            appended += _append(rows, out)
    except Exception as e:
        _log("collect_run fatal: %s" % e)
        return appended
    if appended:
        _log("collect_run +%d -> %s" % (len(appended), out))
    return appended


def _append(rows, out=SHADOW):
    """原子追加（幂等）：先进程内查重，锁内全文件复核后单次写。"""
    if not rows:
        return []
    seen = _load_seen(out)
    rows = [r for r in rows if _key(r) not in seen]
    if not rows:
        return []
    d = os.path.dirname(out) or "."
    os.makedirs(d, exist_ok=True)
    if not _acquire_lock():
        _log("append: lock timeout, skip %d rows" % len(rows))
        return []
    try:
        # 追加前锁内复核（防另一进程刚写过同键）
        fresh = _load_seen(out)
        rows = [r for r in rows if _key(r) not in fresh]
        if not rows:
            return []
        fd = os.open(out, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
            os.write(fd, payload.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        for r in rows:
            seen.add(_key(r))
        return rows
    finally:
        _release_lock()


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/gate_shadow_collect.py", exc)
    ap = argparse.ArgumentParser(description="W5 环境闸门三档影子日采")
    ap.add_argument(
        "--strategies", default="qd,qpzm,cross", help="逗号分隔：qd,qpzm,cross"
    )
    ap.add_argument("--out", default=SHADOW, help="落档 jsonl 路径")
    a = ap.parse_args(argv)
    sts = [s.strip() for s in str(a.strategies).split(",") if s.strip()]
    rows = collect_run(strategies=tuple(sts), out=a.out)
    print("GATE_SHADOW +%d -> %s" % (len(rows), a.out))
    for r in rows:
        print(
            "  %s %s θ=%s total=%s blocked=%s br=%s [%s]"
            % (
                r["date"],
                r["strategy"],
                r["theta"],
                r["n_total"],
                r["n_blocked"],
                r["breadth"],
                r["source_status"],
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
