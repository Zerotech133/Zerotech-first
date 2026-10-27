# -*- coding: utf-8 -*-
"""K35 三线联动 · 五信号影子采集器（2026-10-14 linkage-dev）。

职责：每日五信号命中逐条落 data/linkage_shadow.jsonl 档案——只记不推、
不判真假、不改信号逻辑。挂账与否由 linkage_hitrate_backtest.py 用
影子样本 + 历史回放对照 55% 判据给出数字依据。

落档格式（每行一个 JSON 对象）：
  {
    "ts":        采集时刻 ISO（"2026-10-14 15:05:00"），
    "code":      "301216.SZ"，
    "name":      个股名（可缺失），
    "period":    "1d"/"30m"/"5m"（判定周期），
    "signal":    "1".."5"（信号编号），
    "signal_name":"抗跌"/…（linkage_signals.NAMES 同源），
    "note":      触发说明（judge_series note 原文），
    "block":     {"code","name","seg","how"}（linkage.resolve_block，可 null），
    "snapshot":  归一化指标快照 {last_pct, excess_vs_block, prev_low,
                 prev_high, break_low, break_high}（judge_series ctx 原样），
    "ref_date":  信号参考日 = 个股线末根 dt 前 10 位（"2026-10-14"），
    "source":    "linkage_signals"/"linkage_plot"（挂载入口）
  }

幂等：同 ref_date+code+period+signal 不重复落（进程内缓存 + 全文件扫描）。
原子追加：O_APPEND 单次 write + fsync，配 .lock 文件防并发交错。

挂载点（注记式，不改信号逻辑）：
  · linkage_signals.main() 判定报告产出后调 collect()
  · linkage_plot.gen_all() 每周期侧车产出后调 collect()
  两处都包 try/except，采集失败不影响产线；幂等保证双入口不重复。

用法:
  python linkage_collector.py 301216.SZ              # 现判现收（三周期）
  python linkage_collector.py 301216.SZ --periods 1d
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SHADOW = os.path.join(HERE, "data", "linkage_shadow.jsonl")
LOCK = SHADOW + ".lock"
LOG = "C:/Temp/linkage_collector.log"
STALE_LOCK_SEC = 120

_seen = None  # 进程内幂等缓存：set("ref|code|period|signal")
_block_cache = {}


def _log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _key(rec):
    return "%s|%s|%s|%s" % (rec["ref_date"], rec["code"], rec["period"], rec["signal"])


def _load_seen():
    global _seen
    if _seen is None:
        seen = set()
        if os.path.exists(SHADOW):
            with open(SHADOW, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        seen.add(_key(json.loads(line)))
                    except Exception:
                        continue  # 坏行跳过，不阻断
        _seen = seen
    return _seen


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


def _resolve_block(code):
    if code not in _block_cache:
        try:
            import linkage as LK

            _block_cache[code] = LK.resolve_block(code)
        except Exception as e:
            _log("resolve_block %s fail: %s" % (code, e))
            _block_cache[code] = None
    return _block_cache[code]


def collect(code, res, name=None, source="linkage_signals"):
    """把 judge() / 侧车形态的判定结果落档。返回落档记录列表。

    res 形态：{period: {"signals": [{"tag","name","note"}…], "ctx": {…},
              "last_pct": {…}, "stock_last_dt": "YYYY-MM-DD …"(可缺,
              缺时退回今日)}} —— linkage_signals.judge() 原样输出，
              linkage_plot 侧车（含 sig_ctx/meta）同样兼容。
    """
    seen = _load_seen()
    blk = _resolve_block(code)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    out = []
    for per, d in (res or {}).items():
        if not isinstance(d, dict) or not d.get("signals"):
            continue
        sig_ctx = d.get("ctx") or d.get("sig_ctx") or {}
        ref = (
            d.get("stock_last_dt")
            or (d.get("meta", {}) or {}).get("stock", {}).get("last")
            or ""
        )
        rec_base = {
            "ts": now,
            "code": code,
            "name": name,
            "period": per,
            "block": blk,
            "snapshot": {
                "last_pct": d.get("last_pct"),
                "excess_vs_block": sig_ctx.get("excess_vs_block"),
                "prev_low": sig_ctx.get("prev_low"),
                "prev_high": sig_ctx.get("prev_high"),
                "break_low": sig_ctx.get("break_low"),
                "break_high": sig_ctx.get("break_high"),
            },
            "ref_date": str(ref)[:10] or time.strftime("%Y-%m-%d"),
            "source": source,
        }
        for s in d["signals"]:
            rec = dict(rec_base)
            rec["signal"] = str(s.get("tag"))
            rec["signal_name"] = s.get("name")
            rec["note"] = s.get("note")
            k = _key(rec)
            if k in seen:
                continue  # 幂等：同日同股同周期同信号
            out.append(rec)
            seen.add(k)
    if not out:
        return []
    if not _acquire_lock():
        _log("collect %s: lock timeout, skip %d records" % (code, len(out)))
        return []
    try:
        # 追加前在锁内复核（防另一进程刚写过同键）
        fresh = set()
        if os.path.exists(SHADOW):
            with open(SHADOW, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        fresh.add(_key(json.loads(line)))
                    except Exception:
                        continue
        out = [r for r in out if _key(r) not in fresh]
        if not out:
            return []
        fd = os.open(SHADOW, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out)
            os.write(fd, payload.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        _release_lock()
    _log(
        "collect %s: +%d (%s)"
        % (code, len(out), ",".join(r["period"] + "#" + r["signal"] for r in out))
    )
    return out


def _judge_and_collect(code, periods, source="linkage_collector"):
    from linkage_signals import judge

    res = judge(code, periods)
    recs = collect(code, res, source=source)
    return res, recs


def main():
    ap = argparse.ArgumentParser(description="三线联动五信号影子采集")
    ap.add_argument("code")
    ap.add_argument("--periods", default="1d,30m,5m")
    a = ap.parse_args()
    res, recs = _judge_and_collect(a.code, a.periods.split(","))
    from linkage_signals import report

    print(report(res, a.code))
    print("shadow +%d -> %s" % (len(recs), SHADOW))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
