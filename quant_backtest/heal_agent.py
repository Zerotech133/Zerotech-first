# -*- coding: utf-8 -*-
"""自愈 Agent —— 只做「知道该怎么修」的那几件事。

为什么要有它（2026-11-12）
--------------------------
tdx_sector_daily 断供 25 天，四道防线全都没响。事后复盘发现：不是没人写检查，
是**检查报出来的东西没人能直接下手**——「dead」底下压着六种问题，修法互斥：

  上游没这张表 → 改契约        our_gap      → 自己补拉
  上游断供     → 等或切源      存量冻结     → 不该报
  自建表断供   → 修本地导出    账实不符     → 先修账

这个 Agent 只认「我方可控且有明确修法」的那三类，其余一律不动、只写进报告。
权限边界是硬的（用户 2026-11-12 裁定）：

  允许：resync（重跑增量）/ backfill（补缺口，含修账）/ switch（切备用源，只出建议）
  禁止：改容差、改口径、重建表、删数据、动 P0 名单。

三条保护（都是踩过的坑）
------------------------
1. **盘中不重跑**。11:30 重跑增量会拉到半截日线，把当天的半成品写进分片，
   收盘后同步又会 append 一次 → 同一天两条记录，图上的当日 K 线会跳。
   只允许 15:30 之后执行，盘前一律 dry-run。
2. **24h 防抖**。同表同动作一天最多一次。没有这个，一个持续断供的表会被
   每个小时重跑一次，日志被淹没，且真问题（上游没了）永远修不好。
3. **单轮最多 5 个动作**。防雪崩：真出大事时，20 张表同时重跑会把远端打挂，
   反而更晚恢复。

用法
----
  python heal_agent.py                 # 只看要做什么（默认 dry-run）
  python heal_agent.py --apply         # 真执行（仅 resync/backfill；switch 永远只出建议）
  python heal_agent.py --only ts_rt_min
  python heal_agent.py --history       # 看最近的执行记录

退出码恒 0：Task Scheduler 的退出码语义是「任务跑完了吗」，不是「业务结论好不好」。
业务结论写日志独立行 + JSON 字段（2026-11-12 判例，曾被 business rc 污染成假失败）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

REPORTS = os.path.join(HERE, "reports")
HEALTH = os.path.join(REPORTS, "contract_health.json")
LEDGER = os.path.join(REPORTS, "heal_log.jsonl")
MANIFEST = os.path.join(HERE, "data", "tdx", "_manifest.json")
DATA_DIR = os.path.join(HERE, "data", "tdx")
SYNC = os.path.join(HERE, "tdx_sync.py")

# 白名单：动作 -> 允许的 cause。不在表里的 cause 一律不动。
ALLOWED = {
    "resync": {"our_gap", "upstream_dead"},      # upstream_dead 也重跑一次：便宜，且能证伪
    "backfill": {"ledger_mismatch"},
    "switch": {"upstream_dead"},                 # 只出建议，不真切
}

# 永远不自动执行的动作原因（写清楚，免得下次有人以为忘了做）
MANUAL = {
    "upstream_frozen": "存量冻结：上游自己也停在很久以前，修不了也不该修",
    "local_built": "本地自建表：要修的是本地导出链路，不在同步任务里",
    "no_upstream_table": "契约写错表名：改名单，不是补数据",
    "contract_bug": "契约写错表名：改名单，不是补数据",
    "upstream_unreachable": "探不通上游：网络问题，重试无意义",
    "unknown": "判不出原因：先看清再说",
    "no_gap": "上游也没更多数据：本地不落后，无需动作",
}

# 切源候选：只出证据，不动口径（用户 2026-11-12 裁定：板块源先只做证据）
SWITCH_CANDIDATE = {
    "tdx_sector_daily": {"to": "ts_ths_daily",
                         "why": "同花顺板块日线，上游活跃（本地 lag=1），字段名不同需映射"},
    "tdx_sector_min": {"to": "ts_ths_daily",
                       "why": "分钟级无同源替代，只能退到日频"},
}

POST_CLOSE_HHMM = 1530
MAX_ACTIONS = 5
DEFER_HOURS = 24


def _now():
    return dt.datetime.now()


def _in_trading_hours(now):
    """盘中不重跑增量：会拉到半截数据，收盘后再 append 一次，当天两条记录。"""
    if now.weekday() >= 5:
        return False
    return 900 <= now.hour * 100 + now.minute < POST_CLOSE_HHMM


def _load_health():
    if not os.path.exists(HEALTH):
        return None
    try:
        return json.load(open(HEALTH, encoding="utf-8"))
    except Exception:
        return None


def _history():
    if not os.path.exists(LEDGER):
        return []
    rows = []
    for line in open(LEDGER, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _recent(tbl, action, hours=DEFER_HOURS):
    """24h 防抖：同表同动作只做一次。

    只认 apply=True 的记录 —— dry-run 写进日志的也算的话，第二天真跑时
    会被自己的计划挡掉（看得见「已执行过」却什么都没执行过）。
    """
    cut = _now() - dt.timedelta(hours=hours)
    for r in reversed(_history()):
        if r.get("table") != tbl or r.get("action") != action:
            continue
        if not r.get("apply"):
            continue
        try:
            if dt.datetime.strptime(r["at"], "%Y-%m-%d %H:%M:%S") >= cut:
                return True
        except Exception:
            continue
    return False


def _log(rec):
    os.makedirs(REPORTS, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _parts_empty(tbl):
    """分片目录里真的没有 parquet 才算「没落地」。"""
    d = os.path.join(DATA_DIR, tbl)
    if not os.path.isdir(d):
        return True
    return not any(x.endswith(".parquet") for x in os.listdir(d))


def _run(cmd, timeout=900):
    t0 = dt.datetime.now()
    try:
        p = subprocess.run(cmd, cwd=HERE, capture_output=True, timeout=timeout)
        out = (p.stdout or b"").decode("utf-8", "replace")[-800:]
        err = (p.stderr or b"").decode("utf-8", "replace")[-800:]
        return {"rc": p.returncode, "out": out, "err": err,
                "sec": round((dt.datetime.now() - t0).total_seconds(), 1)}
    except subprocess.TimeoutExpired:
        return {"rc": "timeout", "err": "超过 %ss" % timeout,
                "sec": round((dt.datetime.now() - t0).total_seconds(), 1)}
    except Exception as e:
        return {"rc": "exc", "err": str(e)[:300], "sec": 0}


def do_resync(rec, apply):
    """重跑增量：tdx_sync 自带进程锁，重复调用会静默退 0，安全。"""
    cmd = [sys.executable, SYNC, "--tables", rec["table"]]
    if not apply:
        return {"action": "resync", "ok": None, "cmd": " ".join(cmd), "skipped": "dry-run"}
    r = _run(cmd)
    return {"action": "resync", "ok": r.get("rc") == 0, "cmd": " ".join(cmd), **r}


def do_backfill(rec, apply):
    """补缺口 = 修账 + 重拉。

    ledger_mismatch 的成因是 manifest 记着行数、分片却不存在（可能被清理过）。
    增量任务看到 done=True 就不再拉，所以必须先把这个表的账改成「没拉过」。
    改账前强制备份 manifest —— 这一步动的是账本，不能没有回退点。
    """
    tbl = rec["table"]
    if not _parts_empty(tbl):
        return {"action": "backfill", "ok": False,
                "skipped": "分片目录非空，重置账本会造成重复写入，改人工处理"}
    cmd = [sys.executable, SYNC, "--tables", tbl]
    if not apply:
        return {"action": "backfill", "ok": None, "cmd": " ".join(cmd),
                "skipped": "dry-run（会先备份 manifest 再重置该表条目）"}

    stamp = _now().strftime("%Y%m%d-%H%M%S")
    bak = "%s.bak-heal-%s" % (MANIFEST, stamp)
    shutil.copy2(MANIFEST, bak)
    try:
        m = json.load(open(MANIFEST, encoding="utf-8"))
        m[tbl] = {"offset": 0, "part": 0, "rows": 0, "done": False,
                  "reset_by": "heal_agent", "reset_at": stamp}
        with open(MANIFEST, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False)
    except Exception as e:
        return {"action": "backfill", "ok": False, "err": "改账失败: %s" % str(e)[:200],
                "manifest_bak": bak}
    r = _run(cmd)
    return {"action": "backfill", "ok": r.get("rc") == 0, "cmd": " ".join(cmd),
            "manifest_bak": bak, **r}


def do_switch(rec, apply):
    """切备用源：**永远只出建议**。

    切源不是换个表读那么简单 —— 字段映射、板块代码体系、覆盖范围都要重验，
    一动就会改结论。用户 2026-11-12 明确裁定：板块源先只做证据、不动口径。
    """
    cand = SWITCH_CANDIDATE.get(rec["table"])
    if not cand:
        return {"action": "switch", "ok": None, "skipped": "无登记的备用源"}
    return {"action": "switch", "ok": None, "proposal": cand,
            "skipped": "只出建议，不自动切（改口径需人工裁定）"}


DOERS = {"resync": do_resync, "backfill": do_backfill, "switch": do_switch}


def plan(rows, only=None):
    """给每条红项定一个动作（或明确记为「不动 + 为什么」）。"""
    out = []
    for r in rows:
        if only and r["table"] not in only:
            continue
        if r["status"] not in ("dead", "absent", "late"):
            continue
        cause = r.get("cause")
        if not cause or cause in MANUAL:
            continue
        if (r.get("n_consumers") or 0) == 0:
            continue                      # 没有下游在用，修了也是白修
        for action, causes in ALLOWED.items():
            if cause in causes:
                out.append({"table": r["table"], "cause": cause, "action": action,
                            "tier": r["tier"], "lag": r["lag"],
                            "consumers": r["n_consumers"],
                            "owner": r["owner"]})
                break
    # 影响面大的先做
    out.sort(key=lambda x: (-x["consumers"], x["table"]))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真执行（默认只出计划）")
    ap.add_argument("--only", default="", help="只处理这些表，逗号分隔")
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--max-actions", type=int, default=MAX_ACTIONS)
    args = ap.parse_args(argv)

    if args.history:
        rows = _history()[-40:]
        if not rows:
            print("（无执行记录）")
            return 0
        for r in rows:
            print("%-19s %-9s %-9s ok=%-5s %s"
                  % (r.get("at"), r.get("table"), r.get("action"),
                     r.get("ok"), (r.get("skipped") or r.get("err") or "")[:60]))
        return 0

    h = _load_health()
    if not h:
        print("没有体检结果，先跑 contract_health.py")
        return 0

    now = _now()
    trading = _in_trading_hours(now)
    only = {x.strip() for x in args.only.split(",") if x.strip()} or None
    tasks = plan(h["tables"], only)

    print("自愈计划 · %s   体检基准=%s   红项=%s"
          % (now.strftime("%Y-%m-%d %H:%M"), h.get("base_asof"), h.get("counts")))
    if trading:
        left = POST_CLOSE_HHMM - (now.hour * 100 + now.minute)
        print("【盘中】还有 %d 小时 %d 分钟到收盘窗口。盘中重跑会拉到半截日线，"
              "收盘再 append 一次会写重当天记录 —— 本次一律 dry-run。"
              % (left // 100, left % 100))
    if not tasks:
        print("没有可自动处理的项（其余红项属于：上游断供/存量冻结/自建表/契约错，一律人工）")
        return 0

    applied, deferred = [], []
    for t in tasks:
        if len(applied) >= args.max_actions:
            deferred.append(t)
            continue
        if _recent(t["table"], t["action"]):
            t["skipped"] = "24h 内已执行过，防抖跳过"
            deferred.append(t)
            continue
        apply = args.apply and not trading
        res = DOERS[t["action"]]({"table": t["table"]}, apply)
        rec = {"at": now.strftime("%Y-%m-%d %H:%M:%S"), "table": t["table"],
               "cause": t["cause"], "action": t["action"], "apply": apply,
               "ok": res.get("ok"), "skipped": res.get("skipped"),
               "err": res.get("err") or (res.get("out") or "")[-200:] or res.get("proposal"),
               "sec": res.get("sec")}
        if apply:
            _log(rec)     # dry-run 不落 jsonl：否则日志里全是没发生的动作
        applied.append(rec)
        print("  %-8s %-22s cause=%-15s ok=%-5s %s"
              % (t["action"], t["table"], t["cause"], res.get("ok"),
                 (res.get("skipped") or res.get("err") or "")[:56]))

    if deferred:
        print("\n留给下一轮 %d 项（防抖 / 超单轮上限 %d）："
              % (len(deferred), args.max_actions))
        for t in deferred[:10]:
            print("  %-22s %-8s %s" % (t["table"], t["action"], t.get("skipped", "超上限")))

    manual = [r for r in h["tables"]
              if r["status"] in ("dead", "absent", "late")
              and r.get("cause") in MANUAL and (r.get("n_consumers") or 0) > 0]
    if manual:
        print("\n需要人工的 %d 项（Agent 不碰）：" % len(manual))
        seen = set()
        for r in sorted(manual, key=lambda x: -(x.get("n_consumers") or 0)):
            why = MANUAL[r["cause"]]
            if why in seen:
                continue
            seen.add(why)
            print("  %-22s %-16s %s" % (r["table"], r["cause"], why))

    print("\n本轮执行 %d 项（apply=%s）；记录 -> %s"
          % (len(applied), args.apply and not trading, LEDGER))
    return 0


if __name__ == "__main__":
    sys.exit(main())
