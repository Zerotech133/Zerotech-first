# -*- coding: utf-8 -*-
"""agent_census.py — 智能体点名册：谁在干活、在干嘛、手里有没有活。

为什么要它
----------
军团有 60 席、17 部门，但"谁在干活"此前只能靠翻三个地方：
  · `agent_platform/registry/org.json`      —— 谁在册（编制）
  · `agent_platform/data/llm_tool_audit.jsonl` —— 谁发过动作（事件）
  · `agent_platform/data/checkpoints.db`    —— 谁真的产出过（traces 里有 agent_id + 输出）
  · `.worktrees/tasks.json`                 —— 谁头上挂着单（工单账本）
四本账各说各话，合不起来就看不出"组织在转、业务空转"。

本脚本只读地把四本账合成一张表，给每个席位回答三个问题：
  1. 是否在干活（最近有没有"产出型"动作，而不是只有巡检/催办）
  2. 在干嘛（最近一条 trace 的输出摘要）
  3. 有没有工作（名下 doing/review 工单数）

判定口径（可调，见命令行参数）
  · 干活中   ：最近 --work-min 分钟内有过**产出型**动作（排除 patrol/escalated/heartbeat）
  · 在岗待命 ：最近 24h 有事件，但只有巡检/催办
  · 有单未动 ：名下有 doing/review 单，但最近 --work-min 分钟没有产出
  · 静默     ：24h 内既无事件也无 trace
  · 未在册   ：事件里出现、但 org.json 编制里没有这个 id（先报出来，不静默吞掉）

退出码：0=正常输出（有无空闲都不算失败）；2=数据源缺失/解析错误。

用法：
    python -B tools/agent_census.py
    python -B tools/agent_census.py --json
    python -B tools/agent_census.py --work-min 30 --top 20
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORG = ROOT / "agent_platform" / "registry" / "org.json"
AUDIT = ROOT / "agent_platform" / "data" / "llm_tool_audit.jsonl"
CKPT = ROOT / "agent_platform" / "data" / "checkpoints.db"
ORDERS = ROOT / ".worktrees" / "tasks.json"

# 非产出型动作：巡检/催办/心跳。这些出现不等于"在干活"。
IDLE_ACTIONS = {"patrol", "escalated", "heartbeat", "noop", "idle"}
WORK_WINDOW_MIN = 15.0     # 多久内算"正在干活"
STALE_HOURS = 24.0         # 多久没动静算"静默"


# ---------------------------------------------------------------- 数据源
def load_names() -> dict[str, str]:
    """HR 逐席档案里的 cn_name（三国人名）。取不到就返回空，界面回退到席码。"""
    names: dict[str, str] = {}
    hr = ROOT / "agent_platform" / "hr"
    if not hr.is_dir():
        return names
    import re as _re
    for d in hr.iterdir():
        if not d.is_dir():
            continue
        for f in d.rglob("*.yaml"):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = _re.search(r'cn_name"?\s*:\s*"?([^\s",]+)"?', text)
            if m:
                names[d.name] = m.group(1)
                break
    # org.json 里补漏（LUSU1 只在 org 里有 cn_name）
    try:
        import re as _re2
        text = ORG.read_text(encoding="utf-8")
        for aid, nm in _re2.findall(
                r'"agent_id"\s*:\s*"([^"]+)"[^{}]{0,200}?"cn_name"\s*:\s*"([^"]+)"', text):
            names.setdefault(aid, nm)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/agent_census.py", exc)
    names.setdefault("LUSU1", "阚泽")
    return names


def load_dept_meta() -> dict[str, dict]:
    """dept code → {name, cn_title, lead}。"""
    meta: dict[str, dict] = {}
    try:
        data = json.loads(ORG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return meta
    for dept in data.get("departments") or []:
        meta[dept.get("dept")] = {
            "name": dept.get("name"),
            "cn_title": dept.get("cn_title"),
            "lead": dept.get("lead"),
        }
    return meta


def load_roster() -> dict[str, dict]:
    """org.json → {agent_id: {dept, dept_name, lead}}"""
    roster: dict[str, dict] = {}
    try:
        data = json.loads(ORG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return roster
    for dept in data.get("departments") or []:
        for aid in dept.get("agents") or []:
            aid = str(aid).strip()
            if not aid:
                continue
            roster[aid] = {
                "dept": dept.get("dept"),
                "dept_name": dept.get("name"),
                "lead": dept.get("lead"),
                "cn_title": dept.get("cn_title"),
            }
    chief = (data.get("chief") or {}).get("agent_id")
    if chief:
        roster.setdefault(chief, {"dept": "chief", "dept_name": "总参谋长",
                                  "lead": chief, "cn_title": "参谋长"})
    return roster


def _parse_ts(value) -> float | None:
    """兼容三种时间：ISO 字符串 / epoch 浮点字符串 / epoch 数字。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        num = float(text)
        if num > 1e9:            # epoch 秒
            return num
    except ValueError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/agent_census.py", exc)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(text[:len(fmt) + 6], fmt).timestamp()
        except ValueError:
            continue
    return None


def load_events(now: float) -> dict[str, dict]:
    """llm_tool_audit.jsonl → 每个 caller 的最近动作 / 24h 计数 / 最近产出型动作。"""
    out: dict[str, dict] = {}
    if not AUDIT.exists():
        return out
    try:
        with AUDIT.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                caller = str(rec.get("caller") or "").strip()
                if not caller:
                    continue
                ts = _parse_ts(rec.get("ts"))
                action = str(rec.get("action") or "").strip()
                entry = out.setdefault(caller, {
                    "last_ts": None, "last_action": None, "last_detail": None,
                    "events_24h": 0, "last_work_ts": None, "last_work_action": None,
                })
                if ts and now - ts <= STALE_HOURS * 3600:
                    entry["events_24h"] += 1
                if ts and (entry["last_ts"] is None or ts > entry["last_ts"]):
                    entry["last_ts"] = ts
                    entry["last_action"] = action
                    entry["last_detail"] = str(rec.get("detail") or "")[:120]
                if action not in IDLE_ACTIONS and ts:
                    if entry["last_work_ts"] is None or ts > entry["last_work_ts"]:
                        entry["last_work_ts"] = ts
                        entry["last_work_action"] = action
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/agent_census.py", exc)
    return out


def load_traces(now: float) -> dict[str, dict]:
    """checkpoints.db traces → 每个 agent 的最近产出（时间 + 摘要）。"""
    out: dict[str, dict] = {}
    if not CKPT.exists():
        return out
    try:
        con = sqlite3.connect("file:%s?mode=ro" % CKPT.as_posix(), uri=True, timeout=5)
    except sqlite3.Error:
        return out
    try:
        rows = con.execute(
            "select agent_id, output, ts from traces where agent_id is not null "
            "order by cast(ts as real) desc limit 2000"
        ).fetchall()
    except sqlite3.Error:
        con.close()
        return out
    for agent_id, output, ts in rows:
        aid = str(agent_id or "").strip()
        if not aid:
            continue
        stamp = _parse_ts(ts)
        entry = out.setdefault(aid, {"last_ts": None, "last_output": None, "traces_24h": 0})
        if stamp and now - stamp <= STALE_HOURS * 3600:
            entry["traces_24h"] += 1
        if stamp and (entry["last_ts"] is None or stamp > entry["last_ts"]):
            entry["last_ts"] = stamp
            entry["last_output"] = str(output or "").replace("\n", " ")[:120]
    con.close()
    return out


def load_orders() -> dict[str, dict]:
    """tasks.json → 每个 owner 名下 doing/review 单数 + 标题样例。"""
    out: dict[str, dict] = {}
    if not ORDERS.exists():
        return out
    try:
        data = json.loads(ORDERS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    for tid, rec in data.items():
        if not isinstance(rec, dict):
            continue
        status = str(rec.get("status") or "").lower()
        if status not in ("doing", "review"):
            continue
        owner = str(rec.get("owner") or "unknown-agent").strip() or "unknown-agent"
        entry = out.setdefault(owner, {"open": 0, "samples": []})
        entry["open"] += 1
        if len(entry["samples"]) < 2:
            desc = str(rec.get("desc") or "")
            entry["samples"].append(desc[:60] or tid)
    return out


# ---------------------------------------------------------------- 判定
def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 90:
        return "%d 秒前" % int(seconds)
    if seconds < 5400:
        return "%d 分钟前" % int(seconds / 60)
    if seconds < 172800:
        return "%.1f 小时前" % (seconds / 3600)
    return "%.1f 天前" % (seconds / 86400)


def verdict(now: float, events: dict, traces: dict, orders: dict,
            work_window_min: float) -> tuple[str, str]:
    """返回 (判定, 依据)。判定优先级：干活中 > 有单未动 > 在岗待命 > 静默。"""
    work_ts = max([t for t in (events.get("last_work_ts"), traces.get("last_ts")) if t] or [0]) or None
    seen_ts = max([t for t in (events.get("last_ts"), traces.get("last_ts")) if t] or [0]) or None
    open_orders = orders.get("open", 0)

    if work_ts and now - work_ts <= work_window_min * 60:
        return "干活中", "最近产出 %s" % _fmt_age(now - work_ts)
    if open_orders:
        return "有单未动", "名下 %d 单，但 %s 无产出" % (
            open_orders, _fmt_age(now - work_ts) if work_ts else "从未")
    if seen_ts and now - seen_ts <= STALE_HOURS * 3600:
        return "在岗待命", "仅巡检/催办，最后活动 %s" % _fmt_age(now - seen_ts)
    return "静默", "24h 内无任何事件与产出"


def build(work_window_min: float = WORK_WINDOW_MIN, with_daily: bool = False) -> dict:
    now = time.time()
    roster = load_roster()
    names = load_names()
    dept_meta = load_dept_meta()
    events = load_events(now)
    traces = load_traces(now)
    orders = load_orders()

    ids = set(roster) | set(events) | set(traces)
    rows = []
    for aid in ids:
        ev = events.get(aid, {})
        tr = traces.get(aid, {})
        od = orders.get(aid, {})
        level, why = verdict(now, ev, tr, od, work_window_min)
        # 只展示"新鲜"的产出摘要：陈旧的 trace 输出会让人误以为它在干活
        # （DEV7 判静默却显示一条历史履历摘录就是这个坑）。
        trace_ts = tr.get("last_ts")
        trace_fresh = bool(trace_ts) and (now - trace_ts <= STALE_HOURS * 3600)
        last_out = (tr.get("last_output") if trace_fresh else None) or ev.get("last_detail") or ""
        meta = roster.get(aid) or {}
        rows.append({
            "agent": aid,
            "name": names.get(aid) or "",
            "dept": meta.get("dept_name") or "未在册",
            "dept_code": meta.get("dept") or ("未在册" if aid not in roster else ""),
            "dept_cn_title": (dept_meta.get(meta.get("dept") or "") or {}).get("cn_title") or "",
            "is_lead": bool(meta.get("dept") and (dept_meta.get(meta.get("dept")) or {}).get("lead") == aid),
            "in_roster": aid in roster,
            "level": level,
            "why": why,
            "last_action": ev.get("last_action"),
            "last_work_action": ev.get("last_work_action"),
            "events_24h": ev.get("events_24h", 0),
            "traces_24h": tr.get("traces_24h", 0),
            "open_orders": od.get("open", 0),
            "open_samples": od.get("samples", []),
            "doing": last_out,
            "last_seen_s": None if not ev.get("last_ts") and not tr.get("last_ts")
            else round(now - max([t for t in (ev.get("last_ts"), tr.get("last_ts")) if t] or [now]), 0),
        })

    order = {"干活中": 0, "有单未动": 1, "在岗待命": 2, "静默": 3}
    rows.sort(key=lambda r: (order.get(r["level"], 9), -r["events_24h"], r["agent"]))
    summary = {k: sum(1 for r in rows if r["level"] == k) for k in order}
    summary["在册"] = len(roster)
    summary["未在册"] = sum(1 for r in rows if not r["in_roster"])
    summary["在手工单"] = sum(r["open_orders"] for r in rows)
    summary["静默占比"] = round(summary["静默"] / max(len(rows), 1), 3)

    card = {"generated_at": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
            "work_window_min": work_window_min, "summary": summary, "rows": rows,
            "dept_meta": {k: {"name": v.get("name"), "cn_title": v.get("cn_title"),
                              "lead": v.get("lead"),
                              "lead_name": names.get(v.get("lead") or "") or ""}
                          for k, v in dept_meta.items()}}

    if with_daily:
        card["daily"] = _daily_rollup(rows)
    return card


def _daily_rollup(rows: list) -> dict:
    """把每日事项的判定结果挂到人/部门上（复用 agent_daily，不重写口径）。"""
    try:
        import agent_daily as daily  # 同目录，避免两处判定漂移
        dcard = daily.build(None)
    except Exception as exc:  # noqa: BLE001 - 任何失败都不能让看板整体挂掉
        return {"available": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
    if dcard.get("error"):
        return {"available": False, "reason": dcard["error"]}

    per_agent: dict[str, dict] = {}
    per_dept: dict[str, dict] = {}
    by_agent = {r.get("agent"): {"name": r.get("name"), "dept": r.get("dept_code")} for r in rows}
    for item in dcard.get("rows") or []:
        aid = item.get("agent") or "?"
        dept = (by_agent.get(aid) or {}).get("dept") or ""
        bucket = per_agent.setdefault(aid, {"应交付": 0, "已交付": 0, "未交付": 0,
                                           "待接线": 0, "无单可干": 0, "不适用": 0,
                                           "items": []})
        status = item.get("status")
        bucket["items"].append({"id": item.get("id"), "title": item.get("title"),
                                "status": status, "evidence": item.get("evidence")})
        if status in ("已交付", "未交付"):
            bucket["应交付"] += 1
            bucket["已交付" if status == "已交付" else "未交付"] += 1
        elif status == "待接线":
            bucket["待接线"] += 1
        elif status == "无单可干":
            bucket["无单可干"] += 1
        elif status in ("不适用", "未到点"):
            bucket["不适用"] += 1
        if dept:
            db = per_dept.setdefault(dept, {"应交付": 0, "已交付": 0, "未交付": 0, "待接线": 0})
            if status in ("已交付", "未交付"):
                db["应交付"] += 1
            if status == "已交付":
                db["已交付"] += 1
            elif status == "未交付":
                db["未交付"] += 1
            elif status == "待接线":
                db["待接线"] += 1

    return {"available": True,
            "generated_at": dcard.get("generated_at"),
            "summary": dcard.get("summary") or {},
            "per_agent": per_agent, "per_dept": per_dept}


def _label(row: dict) -> str:
    return ("%s(%s)" % (row["name"], row["agent"])) if row.get("name") else row["agent"]


def render(card: dict, top: int) -> None:
    s = card["summary"]
    print("智能体点名册  %s  （干活窗口 %g 分钟）" % (card["generated_at"], card["work_window_min"]))
    print("在册 %d 席 | 干活中 %d | 有单未动 %d | 在岗待命 %d | 静默 %d（%.0f%%）| 未在册 %d | 在手工单 %d\n"
          % (s["在册"], s["干活中"], s["有单未动"], s["在岗待命"], s["静默"],
             s["静默占比"] * 100, s["未在册"], s["在手工单"]))
    print("%-16s %-12s %-8s %-7s %-6s %-5s %s" % ("人名(席码)", "部门", "判定", "24h事件", "在手", "轨迹", "在干嘛"))
    print("-" * 120)
    shown = 0
    for row in card["rows"]:
        if shown >= top:
            break
        doing = (row["doing"] or row["why"] or "")[:50]
        print("%-16s %-12s %-8s %-7d %-6d %-5d %s" % (
            _label(row)[:16], (row["dept"] or "")[:12], row["level"],
            row["events_24h"], row["open_orders"], row["traces_24h"], doing))
        shown += 1
    if len(card["rows"]) > shown:
        print("… 其余 %d 席（--top 调大或 --json 看全量）" % (len(card["rows"]) - shown))


# ── 系统态势四卡（2026-10-26 K-ORG-20261026-BOARD2）──────────────────
# 看板原先只答"人"（谁在干活），不答"系统"（任务/备份/夜间/健康）。
# 这四块今天都出过事（任务审计误计、备份落后、夜间进程被杀、看板探活误判），
# 所以收进首页顶部，随 30 分钟刷新自动更新。全部只读；任一数据源缺失 → 该卡降级"不可用"。

import os  # 系统态势四卡需要读 TEMP
import logging
_REPO_ROOT = Path(__file__).resolve().parent.parent
_VAULT_DIR = _REPO_ROOT.parent / "_vault"
# 运行日志统一落 C:\Temp（运维约定）；TEMP 指向用户临时目录，不能替代。
_TMP_DIR = Path(r"C:\Temp") if Path(r"C:\Temp").is_dir() else Path(os.environ.get("TEMP") or r"C:\Temp")


def _parse_night_log(text: str) -> dict:
    """从 jev_night.log 文本提取：最近轮次、剩余积压、最近 judged、是否收尾。"""
    import re as _re
    out = {"round": None, "left": None, "judged": None, "batch": None, "ended": False}
    for line in text.splitlines():
        m = _re.search(r"ROUND (\d+) \| 剩余 JEV=(\d+)", line)
        if m:
            out["round"], out["left"] = int(m.group(1)), int(m.group(2))
        m = _re.search(r"judged=(\d+)/(\d+)", line)
        if m:
            out["judged"], out["batch"] = int(m.group(1)), int(m.group(2))
        if "窗口结束" in line or "收工" in line:
            out["ended"] = True
    return out


def _parse_manifest_entries(text: str) -> int:
    """MANIFEST.txt 的有效条目数（跳过注释/空行）。"""
    return sum(1 for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#"))


def _status_task() -> dict:
    p = _REPO_ROOT / "portal" / "data" / "task_exit_audit" / "task_exit_summary.json"
    try:
        import json as _json
        j = _json.loads(p.read_text(encoding="utf-8"))
        fails = j.get("failures") or []
        return {"ok": True, "failed": int(j.get("failed") or 0),
                "names": [f.get("name") for f in fails[:3]],
                "age_h": _age_hours_from_iso(j.get("generated_at"))}
    except Exception as exc:  # noqa: BLE001  fail-open：卡片降级
        return {"ok": False, "err": type(exc).__name__}


def _status_backup() -> dict:
    try:
        dirs = [d for d in _VAULT_DIR.iterdir()
                if d.is_dir() and len(d.name) == 10 and d.name[4] == "-" and d.name[7] == "-"]
        if not dirs:
            return {"ok": False, "err": "无日期快照"}
        latest = sorted(dirs, key=lambda d: d.name)[-1]
        man = latest / "MANIFEST.txt"
        entries = _parse_manifest_entries(man.read_text(encoding="utf-8")) if man.is_file() else 0
        age_h = (time.time() - man.stat().st_mtime) / 3600.0 if man.is_file() else None
        offsite = _REPO_ROOT.parent / "_offsite"
        has_offsite = bool(list(offsite.glob("*-offsite.txt"))) if offsite.is_dir() else False
        return {"ok": True, "date": latest.name, "entries": entries,
                "age_h": age_h, "offsite": has_offsite}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": type(exc).__name__}


def _status_night() -> dict:
    log = _TMP_DIR / "jev_night.log"
    pause = _TMP_DIR / "k266_m3.pause"
    try:
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        info = _parse_night_log(text)
        info["ok"] = bool(text)
        info["pause"] = pause.is_file()
        info["age_min"] = ((time.time() - log.stat().st_mtime) / 60.0) if log.is_file() else None
        return info
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": type(exc).__name__}


def _status_health() -> dict:
    """健康卡：优先读 30 分钟内的缓存，否则跑一次生成器（只读）。"""
    import json as _json
    import subprocess
    cache = _TMP_DIR / "health_card_cache.json"
    try:
        if cache.is_file():
            age = time.time() - cache.stat().st_mtime
            if age < 1800:
                j = _json.loads(cache.read_text(encoding="utf-8"))
                # 缓存必须属于同一个仓库根：工作树跑过一次会把"数据不可读"缓存下来，
                # master 再读就会显示一堆假红灯（2026-10-25 实测踩过）。
                if str(j.get("_cache_root") or "") != str(_REPO_ROOT):
                    j = None
            else:
                j = None
        else:
            j = None
        if j is None:
            gen = _REPO_ROOT / "tools" / "gen_health_card.py"
            r = subprocess.run([sys.executable, "-B", str(gen)], capture_output=True,
                               timeout=90, cwd=str(_REPO_ROOT))
            j = _json.loads(r.stdout.decode("utf-8", "replace"))
            j["_cache_root"] = str(_REPO_ROOT)
            cache.write_text(_json.dumps(j, ensure_ascii=False), encoding="utf-8")
        checks = j.get("checks") or []
        reds = [c.get("id") for c in checks if c.get("level") == "red"]
        yellows = [c for c in checks if c.get("level") == "yellow"]
        return {"ok": True, "status": j.get("status"), "red": len(reds),
                "yellow": len(yellows), "reds": reds[:3]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "err": type(exc).__name__}


def _age_hours_from_iso(value) -> float | None:
    if not value:
        return None
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return round((datetime.now(dt.tzinfo) - dt).total_seconds() / 3600.0, 1)
    except Exception:  # noqa: BLE001
        return None


def collect_system_status() -> dict:
    return {
        "task": _status_task(),
        "backup": _status_backup(),
        "night": _status_night(),
        "health": _status_health(),
    }


def _sys_card(title: str, tone: str, big: str, lines: list) -> str:
    import html as _html
    li = "".join("<div class=\"sysline\">%s</div>" % _html.escape(x) for x in lines if x)
    return ('<div class="syscard %s"><div class="systitle">%s</div>'
            '<div class="sysbig">%s</div>%s</div>' % (tone, _html.escape(title), _html.escape(big), li))


def render_system_status(st: dict) -> str:
    """四卡 HTML。tone: ok/warn/bad/mute。"""
    cards = []
    t = st.get("task") or {}
    if t.get("ok"):
        cards.append(_sys_card("任务失败", "ok" if not t.get("failed") else "bad",
                               str(t.get("failed")), [", ".join(t.get("names") or []) or "无",
                                                      "审计 %s 小时前" % t.get("age_h")]))
    else:
        cards.append(_sys_card("任务失败", "mute", "?", ["数据不可用"]))
    b = st.get("backup") or {}
    if b.get("ok"):
        age = b.get("age_h")
        tone = "ok" if (age is not None and age < 36) else ("warn" if (age is None or age < 72) else "bad")
        cards.append(_sys_card("备份", tone, "%s" % (("%.0fh" % age) if age is not None else "-"),
                               ["最新 %s · %d 件" % (b.get("date"), b.get("entries")),
                                "异地清单 %s" % ("已生成" if b.get("offsite") else "缺")]))
    else:
        cards.append(_sys_card("备份", "mute", "?", ["数据不可用"]))
    n = st.get("night") or {}
    if n.get("ok"):
        if n.get("pause"):
            tone, big = "warn", "独占中"
        elif n.get("ended"):
            tone, big = "ok", "已收工"
        else:
            tone, big = "mute", "未启动"
        lines = []
        if n.get("round") is not None:
            lines.append("第 %d 轮 · 剩 %s 条" % (n["round"], n.get("left")))
        if n.get("judged") is not None:
            lines.append("上轮 judged %d/%d" % (n["judged"], n.get("batch") or 0))
        if n.get("age_min") is not None:
            lines.append("日志 %.0f 分钟前" % n["age_min"])
        cards.append(_sys_card("夜间独占", tone, big, lines))
    else:
        cards.append(_sys_card("夜间独占", "mute", "?", ["日志不可用"]))
    h = st.get("health") or {}
    if h.get("ok"):
        cards.append(_sys_card("系统健康", "ok" if not h.get("red") else "bad",
                               "红 %d / 黄 %d" % (h.get("red", 0), h.get("yellow", 0)),
                               [", ".join(h.get("reds") or []) or "无红灯"]))
    else:
        cards.append(_sys_card("系统健康", "mute", "?", ["健康卡不可用"]))
    return ('<section class="sysgrid"><h2>系统态势（%s）</h2><div class="sysrow">%s</div></section>'
            % (time.strftime("%H:%M"), "".join(cards)))


_LEVEL_CLASS = {"干活中": "work", "有单未动": "debt", "在岗待命": "idle", "静默": "silent"}


def write_html(card: dict, path: Path) -> None:
    """生成自包含看板：一部门一卡，一席一点 + 顶部系统态势四卡。无外部依赖，双击即开。"""
    import html as _html

    try:
        _sys_html = render_system_status(collect_system_status())
    except Exception as _exc:  # noqa: BLE001  系统卡渲染失败不能拖垮看板
        _sys_html = '<section class="sysgrid"><h2>系统态势</h2><div class="sysrow">' \
                    '<div class="syscard mute"><div class="systitle">采集失败</div>' \
                    '<div class="sysbig">?</div></div></div></section>'
        print("系统态势采集失败（看板继续）：%r" % (_exc,), file=sys.stderr)

    s = card["summary"]
    meta = card.get("dept_meta") or {}
    depts: dict[str, list] = {}
    extra = []
    for row in card["rows"]:
        code = row.get("dept_code") or ""
        if not code or code == "未在册":
            extra.append(row)
        else:
            depts.setdefault(code, []).append(row)

    order = {"干活中": 0, "有单未动": 1, "在岗待命": 2, "静默": 3}
    dept_codes = sorted(depts, key=lambda c: (
        sum(order.get(r["level"], 9) for r in depts[c]) / max(len(depts[c]), 1), c))

    chips = "".join(
        '<span class="chip %s">%s <b>%s</b></span>' % (cls, name, s.get(key))
        for key, name, cls in (
            ("干活中", "干活中", "work"), ("有单未动", "有单未动", "debt"),
            ("在岗待命", "在岗待命", "idle"), ("静默", "静默", "silent"))
    )
    cards = []
    daily = card.get("daily") or {}
    daily_ok = bool(daily.get("available"))
    per_agent_daily = daily.get("per_agent") or {}
    per_dept_daily = daily.get("per_dept") or {}
    for code in dept_codes:
        m = meta.get(code) or {}
        members = sorted(depts[code], key=lambda r: (order.get(r["level"], 9), r["agent"]))
        rows_html = []
        for r in members:
            cls = _LEVEL_CLASS.get(r["level"], "silent")
            doing = _html.escape((r.get("doing") or r.get("why") or "")[:70])
            lead = ' <span class="lead">负责人</span>' if r.get("is_lead") else ""
            badge = ""
            if daily_ok:
                d = per_agent_daily.get(r["agent"]) or {}
                if d.get("未交付"):
                    badge = '<span class="badge red">欠 %d</span>' % d["未交付"]
                elif d.get("应交付"):
                    badge = '<span class="badge green">交 %d/%d</span>' % (d.get("已交付", 0), d["应交付"])
                elif d.get("无单可干"):
                    badge = '<span class="badge gray">无单</span>'
            rows_html.append(
                '<div class="seat %s"><div class="who"><span class="nm">%s</span>'
                '<span class="code">%s</span>%s%s</div>'
                '<div class="st">%s%s</div>'
                '<div class="doing" title="%s">%s</div></div>' % (
                    cls, _html.escape(r.get("name") or "-"), _html.escape(r["agent"]), lead, badge,
                    _html.escape(r["level"]),
                    (' · 在手工单 %d' % r["open_orders"]) if r.get("open_orders") else "",
                    doing, doing or "—"))
        dstat = ""
        if daily_ok and code in per_dept_daily:
            dd = per_dept_daily[code]
            cls2 = "red" if dd.get("未交付") else ("green" if dd.get("已交付") else "gray")
            dstat = '<div class="dstat %s">今日应交付 %d · 已交 %d%s</div>' % (
                cls2, dd.get("应交付", 0), dd.get("已交付", 0),
                (' · 欠 %d' % dd["未交付"]) if dd.get("未交付") else "")
        cards.append(
            '<section class="dept"><header><div class="cn">%s</div>'
            '<div class="dn">%s</div>'
            '<div class="boss">%s：%s(%s)</div></header>%s'
            '<div class="seats">%s</div></section>' % (
                _html.escape(m.get("cn_title") or ""), _html.escape(m.get("name") or code),
                "负责人", _html.escape(m.get("lead_name") or "-"), _html.escape(str(m.get("lead") or "-")),
                dstat, "".join(rows_html)))

    extra_html = ""
    if extra:
        items = "".join('<li><b>%s</b> <span class="code">%s</span> — %s：%s</li>' % (
            _html.escape(r.get("name") or "-"), _html.escape(r["agent"]),
            _html.escape(r["level"]), _html.escape((r.get("doing") or "")[:60])) for r in extra[:20])
        extra_html = ('<section class="dept extra"><header><div class="cn">编制外</div>'
                      '<div class="dn">未在册的事件来源</div></header><ul>%s</ul></section>' % items)

    deliver_html = ""
    if daily_ok:
        ds = daily.get("summary") or {}
        should = (ds.get("已交付") or 0) + (ds.get("未交付") or 0)
        name_of = {r["agent"]: r.get("name") for r in card["rows"]}
        missed, manual = [], []
        for aid, d in per_agent_daily.items():
            for it in d.get("items") or []:
                if it.get("status") == "未交付":
                    missed.append((aid, it))
                elif it.get("status") == "待接线":
                    manual.append((aid, it))
        miss_li = "".join('<li>%s <b>%s(%s)</b> — %s</li>' % (
            _html.escape(it.get("id") or ""), _html.escape(name_of.get(aid) or "-"),
            _html.escape(aid), _html.escape(it.get("title") or "")) for aid, it in missed)
        man_li = "".join('<li>%s <b>%s(%s)</b> — %s</li>' % (
            _html.escape(it.get("id") or ""), _html.escape(name_of.get(aid) or "-"),
            _html.escape(aid), _html.escape(it.get("title") or "")) for aid, it in manual)
        deliver_html = (
            '<section class="deliver"><h2>今日交付（按事项目录 daily_work.json 判定）</h2>'
            '<div class="nums">'
            '<div class="num"><b>%d</b><span>应交付</span></div>'
            '<div class="num done"><b>%d</b><span>已交付</span></div>'
            '<div class="num debt"><b>%d</b><span>欠账</span></div>'
            '<div class="num manual"><b>%d</b><span>待接线</span></div>'
            '<div class="num idle"><b>%d</b><span>无单可干</span></div>'
            '</div>%s%s</section>' % (
                should, ds.get("已交付") or 0, ds.get("未交付") or 0,
                ds.get("待接线") or 0, ds.get("无单可干") or 0,
                ('<ul class="debtlist">%s</ul>' % miss_li) if miss_li else "",
                ('<ul class="manuallist">%s</ul>' % man_li) if man_li else ""))

    doc = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>零界 · 智能体点名册</title>
<style>
:root{--bg:#f5f6f8;--card:#fff;--line:#e3e6ea;--txt:#1b2430;--sub:#6b7684;
--work:#12805c;--debt:#c0392b;--idle:#b7791f;--silent:#9aa4b1}
*{box-sizing:border-box}body{margin:0;padding:24px;background:var(--bg);color:var(--txt);
font:14px/1.6 "Microsoft YaHei","Segoe UI",sans-serif}
h1{font-size:20px;margin:0 0 4px}.sub{color:var(--sub);font-size:12px;margin-bottom:14px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px}
.chip{background:#fff;border:1px solid var(--line);border-radius:999px;padding:4px 12px;font-size:13px}
.chip b{font-size:15px;margin-left:4px}.chip.work b{color:var(--work)}
.chip.debt b{color:var(--debt)}.chip.idle b{color:var(--idle)}.chip.silent b{color:var(--silent)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.dept{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.dept header{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;
border-bottom:1px dashed var(--line);padding-bottom:8px;margin-bottom:8px}
.dept .cn{font-weight:700;font-size:15px}.dept .dn{color:var(--sub);font-size:12px}
.dept .boss{margin-left:auto;font-size:12px;color:var(--sub)}
.seats{display:flex;flex-direction:column;gap:6px}
.seat{border-left:3px solid var(--silent);padding:4px 8px;background:#fafbfc;border-radius:4px}
.seat.work{border-left-color:var(--work)}.seat.debt{border-left-color:var(--debt)}
.seat.idle{border-left-color:var(--idle)}.seat.silent{border-left-color:var(--silent)}
.who{display:flex;align-items:center;gap:6px}
.nm{font-weight:600}.code{color:var(--sub);font-size:11px}
.lead{font-size:10px;background:#eef2ff;color:#3956c0;border-radius:3px;padding:0 4px}
.st{font-size:12px;color:var(--sub)}
.doing{font-size:11px;color:#8a94a2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.extra ul{margin:6px 0 0 18px;padding:0;font-size:12px;color:var(--sub)}
.deliver{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin-bottom:16px}
.deliver h2{font-size:14px;margin:0 0 10px}
.nums{display:flex;gap:26px;flex-wrap:wrap}
.num span{display:block;font-size:11px;color:var(--sub)}
.num b{font-size:24px;line-height:1.2}
.num.done b{color:var(--work)}.num.debt b{color:var(--debt)}
.num.manual b{color:var(--idle)}.num.idle b{color:var(--silent)}
.debtlist,.manuallist{margin:10px 0 0 18px;padding:0;font-size:13px}
.debtlist li{color:var(--debt)}.manuallist li{color:var(--idle);font-size:12px}
.badge{font-size:10px;border-radius:3px;padding:0 5px;margin-left:4px}
.badge.red{background:#fdecea;color:var(--debt)}
.badge.green{background:#e8f6f0;color:var(--work)}
.badge.gray{background:#f0f2f5;color:var(--sub)}
.dstat{margin:0 0 8px;font-size:12px}
.dstat.red{color:var(--debt)}.dstat.green{color:var(--work)}.dstat.gray{color:var(--sub)}
.sysgrid{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin-bottom:16px}.sysgrid h2{font-size:14px;margin:0 0 10px}.sysrow{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}.syscard{border:1px solid var(--line);border-radius:8px;padding:8px 12px;background:#fafbfc}.systitle{font-size:12px;color:var(--sub)}.sysbig{font-size:20px;font-weight:700;line-height:1.3;margin:2px 0 4px}.sysline{font-size:11px;color:#8a94a2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.syscard.ok .sysbig{color:var(--work)}.syscard.warn .sysbig{color:var(--idle)}.syscard.bad .sysbig{color:var(--debt)}.syscard.mute .sysbig{color:var(--silent)}.legend{margin-top:16px;font-size:12px;color:var(--sub)}
</style><script src="/static/nav.js" defer></script></head><body>
<h1>零界 · 智能体点名册</h1>
<div class="sub">生成于 %s ｜ 判定窗口 %g 分钟 ｜ 数据源：HR 档案 · org.json · 审计账本 · traces · 工单账本</div>
<div class="chips">%s<span class="chip">在册 <b>%s</b></span><span class="chip">在手工单 <b>%s</b></span></div>
%s
%s
<div class="grid">%s%s</div>
<div class="legend">色条：<b style="color:#12805c">绿=干活中</b> ·
<b style="color:#c0392b">红=有单未动</b> · <b style="color:#b7791f">黄=在岗待命</b> ·
<b style="color:#9aa4b1">灰=静默</b>。人名取自 HR 逐席档案 cn_name，括注为席码。</div>
</body></html>""" % (card["generated_at"], card["work_window_min"], chips,
                     _fmt_num(s.get("在册")), _fmt_num(s.get("在手工单")),
                     _sys_html, deliver_html, "".join(cards), extra_html)
    path.write_text(doc, encoding="utf-8")


def _fmt_num(v) -> str:
    return "-" if v is None else str(v)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="智能体点名册：谁在干活/在干嘛/有没有活")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--work-min", type=float, default=WORK_WINDOW_MIN)
    ap.add_argument("--html", metavar="PATH", help="额外导出自包含看板 HTML（双击即看）")
    ap.add_argument("--daily", action="store_true",
                    help="同时加载每日事项判定（--html 时自动开启）")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    card = build(args.work_min, with_daily=bool(args.daily or args.html))
    if not card["rows"]:
        print("数据源全部缺失：org.json / llm_tool_audit.jsonl / checkpoints.db 都读不到", file=sys.stderr)
        return 2
    if args.html:
        try:
            target = Path(args.html)
            target.parent.mkdir(parents=True, exist_ok=True)
            write_html(card, target)
            print("看板已写出：%s" % target)
        except OSError as exc:
            print("写 HTML 失败：%s" % exc, file=sys.stderr)
    if args.json:
        print(json.dumps(card, ensure_ascii=False, indent=2))
    else:
        render(card, args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
