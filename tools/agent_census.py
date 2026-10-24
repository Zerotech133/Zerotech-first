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
from datetime import datetime, timedelta
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
    except OSError:
        pass
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
    except ValueError:
        pass
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
    except OSError:
        pass
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


_LEVEL_CLASS = {"干活中": "work", "有单未动": "debt", "在岗待命": "idle", "静默": "silent"}


def write_html(card: dict, path: Path) -> None:
    """生成自包含看板：一部门一卡，一席一点。无外部依赖，双击即开。"""
    import html as _html

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
.legend{margin-top:16px;font-size:12px;color:var(--sub)}
</style><script src="/static/nav.js" defer></script></head><body>
<h1>零界 · 智能体点名册</h1>
<div class="sub">生成于 %s ｜ 判定窗口 %g 分钟 ｜ 数据源：HR 档案 · org.json · 审计账本 · traces · 工单账本</div>
<div class="chips">%s<span class="chip">在册 <b>%s</b></span><span class="chip">在手工单 <b>%s</b></span></div>
%s
<div class="grid">%s%s</div>
<div class="legend">色条：<b style="color:#12805c">绿=干活中</b> ·
<b style="color:#c0392b">红=有单未动</b> · <b style="color:#b7791f">黄=在岗待命</b> ·
<b style="color:#9aa4b1">灰=静默</b>。人名取自 HR 逐席档案 cn_name，括注为席码。</div>
</body></html>""" % (card["generated_at"], card["work_window_min"], chips,
                     _fmt_num(s.get("在册")), _fmt_num(s.get("在手工单")),
                     deliver_html, "".join(cards), extra_html)
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
