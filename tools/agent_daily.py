# -*- coding: utf-8 -*-
"""agent_daily.py — 每日工作项点名：把"他们每天干什么"变成可检查的事。

为什么
------
军团 60 席、17 部门，但此前只有"巡检 + 催办"：A0 发现没人有任务活动，就催一遍，
催完还是没人有活。根子不是纪律，是**没有事**——没人定义过"某席位在某天该交出什么"。

本脚本配合 `agent_platform/registry/daily_work.json`（事项目录，单一真相源）：
  · 按频率算出"今天该做什么"（daily / trading_day / weekly / every_min）
  · 对每条事去核证据（文件新鲜度 / 日志里的成功标记 / 审计账本动作）
  · 给出每席位的"已交付 / 未交付 / 待接线"，并落一份当日台账

纪律：**没有可检查证据的事不收录**（目录里 type=manual 的条目会明确标"待接线"，
不算交付、也不算失败，避免用"做过了"糊弄）。

用法：
    python -B tools/agent_daily.py                 # 今日看板
    python -B tools/agent_daily.py --json
    python -B tools/agent_daily.py --agent D1
    python -B tools/agent_daily.py --all           # 含未到点/不适用
"""
from __future__ import annotations

import argparse
import glob as globmod
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:                      # 人名映射复用点名册的加载器，避免两处口径漂移
    from agent_census import load_names  # type: ignore
except Exception:         # 单文件运行/改名时不致命，退回席码
    def load_names() -> dict:
        return {}
CATALOG = ROOT / "agent_platform" / "registry" / "daily_work.json"
AUDIT = ROOT / "agent_platform" / "data" / "llm_tool_audit.jsonl"
ORDERS = ROOT / ".worktrees" / "tasks.json"
LEDGER_DIR = ROOT / "agent_platform" / "data"

DONE = "已交付"
MISSED = "未交付"
NOT_YET = "未到点"
NA = "不适用"
MANUAL = "待接线"
NO_ORDER = "无单可干"


# ---------------------------------------------------------------- 时间
def _parse_freq(freq: str) -> dict:
    """把 freq 解析成可判定结构。不认识的写法当手动项，不猜。"""
    text = (freq or "").strip()
    m = re.match(r"daily@(\d{1,2}):(\d{2})$", text)
    if m:
        return {"kind": "daily", "hour": int(m.group(1)), "minute": int(m.group(2))}
    m = re.match(r"trading_day@(\d{1,2}):(\d{2})$", text)
    if m:
        return {"kind": "trading_day", "hour": int(m.group(1)), "minute": int(m.group(2))}
    m = re.match(r"weekly@(\d)\s+(\d{1,2}):(\d{2})$", text)
    if m:
        return {"kind": "weekly", "weekday": int(m.group(1)),
                "hour": int(m.group(2)), "minute": int(m.group(3))}
    m = re.match(r"every_min\s+(\d+)$", text)
    if m:
        return {"kind": "every", "minutes": int(m.group(1))}
    return {"kind": "unknown"}


def due_state(spec: dict, now: datetime) -> tuple[str, float | None]:
    """返回 (状态, 允许的最大迟到小时)。状态 ∈ due / not_yet / na。"""
    if spec["kind"] == "unknown":
        return NA, None
    if spec["kind"] == "every":
        return "due", spec["minutes"] / 60.0
    if spec["kind"] == "weekly":
        if now.weekday() != spec["weekday"]:
            return NA, None
    if spec["kind"] == "trading_day" and now.weekday() >= 5:
        # 近似交易日：周末直接判不适用。真实交易日需查行情湖（后续接 tdx_read）
        return NA, None
    deadline = now.replace(hour=spec["hour"], minute=spec["minute"], second=0, microsecond=0)
    if now < deadline:
        return NOT_YET, None
    return "due", 24.0


# ---------------------------------------------------------------- 证据
def _resolve(path_text: str) -> Path:
    p = Path(path_text)
    return p if p.is_absolute() else (ROOT / p)


def check_file(ev: dict, now: float) -> tuple[str, str]:
    pattern = str(_resolve(ev.get("path") or ""))
    hits = globmod.glob(pattern)
    if not hits:
        return MISSED, "没有任何产物：%s" % (ev.get("path") or "?")
    newest = max(hits, key=lambda p: os.path.getmtime(p))
    age_h = (now - os.path.getmtime(newest)) / 3600.0
    max_age = float(ev.get("max_age_h") or 24)
    rel = os.path.relpath(newest, ROOT) if str(newest).startswith(str(ROOT)) else newest
    if age_h <= max_age:
        return DONE, "%s（%.1f 小时前）" % (rel, age_h)
    return MISSED, "%s 已过期：%.1f 小时未更新（允许 %.0f）" % (rel, age_h, max_age)


def check_log_regex(ev: dict, now: float) -> tuple[str, str]:
    path = _resolve(ev.get("path") or "")
    if not path.exists():
        return MISSED, "日志不存在：%s" % (ev.get("path") or "?")
    age_h = (now - path.stat().st_mtime) / 3600.0
    max_age = float(ev.get("max_age_h") or 24)
    pattern = str(ev.get("pattern") or "")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return MISSED, "日志读不出：%s" % exc
    tail = "\n".join(text.splitlines()[-400:])
    found = bool(re.search(pattern, tail, re.M)) if pattern else False
    if found and age_h <= max_age:
        return DONE, "%s 命中 /%s/（%.1f 小时前）" % (path.name, pattern, age_h)
    if not found:
        return MISSED, "%s 最近 400 行没命中 /%s/" % (path.name, pattern)
    return MISSED, "%s 命中但已 %.1f 小时未更新（允许 %.0f）" % (path.name, age_h, max_age)


def check_ledger(ev: dict, now: float) -> tuple[str, str]:
    if not AUDIT.exists():
        return MISSED, "审计账本不存在"
    pattern = str(ev.get("pattern") or "")
    max_age = float(ev.get("max_age_h") or 1) * 3600
    last_ts = None
    try:
        with AUDIT.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if pattern and pattern not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                ts = _iso_to_ts(rec.get("ts"))
                if ts and (last_ts is None or ts > last_ts):
                    last_ts = ts
    except OSError as exc:
        return MISSED, "账本读不出：%s" % exc
    if last_ts is None:
        return MISSED, "账本里从未出现 /%s/" % pattern
    age = now - last_ts
    if age <= max_age:
        return DONE, "账本命中（%.0f 分钟前）" % (age / 60)
    return MISSED, "账本命中但已 %.1f 小时（允许 %.0f 分钟）" % (age / 3600, max_age / 60)


def _iso_to_ts(value) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(text[:len(fmt) + 6], fmt).timestamp()
        except ValueError:
            continue
    return None


def check_command(ev: dict, now: float) -> tuple[str, str]:
    """跑一条守卫命令，看返回码。用于"岗位纪律"类事项（索引/导航/方法集）。"""
    raw = ev.get("cmd")
    if isinstance(raw, str):
        raw = raw.split()
    if not raw:
        return MISSED, "evidence.cmd 为空"
    timeout = float(ev.get("timeout_s") or 60)
    expect = int(ev.get("expect_rc") or 0)
    cmd = [sys.executable, "-X", "utf8", "-B"] + [str(x) for x in raw]
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return MISSED, "命令跑不起来：%s" % exec_short(exc)
    out = (proc.stdout or b"").decode("utf-8", "replace").splitlines()
    tail = out[-1].strip() if out else ""
    if proc.returncode == expect:
        return DONE, "命令 rc=%d%s" % (expect, ("：" + tail[:60]) if tail else "")
    return MISSED, "命令 rc=%d（期望 %d）%s" % (
        proc.returncode, expect, ("：" + tail[:80]) if tail else "")


_ORDER_CACHE: dict[str, float | None] = {}


def check_command_out(ev: dict, now: float) -> tuple[str, str]:
    """跑命令并检查**输出内容**（而不是返回码）。

    为什么需要这种类型：`check_ports.py --strict` 把"有未登记端口"和"在册服务离线"
    混成一个返回码——开发机上 Docker/手动型服务本来就常关，用 rc 判"零漂移"会天天
    误报（2026-10-24 实测：未登记已清零，strict 仍因 12 个离线口 FAIL）。
    本类型用 forbid/require 精确表达"真正要守的那一件事"。
    """
    raw = ev.get("cmd")
    if isinstance(raw, str):
        raw = raw.split()
    if not raw:
        return MISSED, "evidence.cmd 为空"
    timeout = float(ev.get("timeout_s") or 60)
    cmd = [sys.executable, "-X", "utf8", "-B"] + [str(x) for x in raw]
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return MISSED, "命令跑不起来：%s" % exec_short(exc)
    text = ((proc.stdout or b"") + (proc.stderr or b"")).decode("utf-8", "replace")
    forbid = ev.get("forbid")
    require = ev.get("require")
    if forbid and forbid in text:
        hit = [ln.strip() for ln in text.splitlines() if forbid in ln][:2]
        return MISSED, "输出命中禁止项「%s」：%s" % (forbid, " / ".join(hit))
    if require and require not in text:
        return MISSED, "输出缺少必需项「%s」" % require
    return DONE, "输出校验通过（rc=%d）" % proc.returncode


def _branch_commit_ts(branch: str) -> float | None:
    if branch in _ORDER_CACHE:
        return _ORDER_CACHE[branch]
    ts = None
    try:
        proc = subprocess.run(
            ["git", "-C", str(ROOT), "log", "-1", "--format=%ct", branch],
            capture_output=True, timeout=10, check=False,
        )
        text = (proc.stdout or b"").decode("utf-8", "replace").strip()
        if proc.returncode == 0 and text.isdigit():
            ts = float(text)
    except (OSError, subprocess.TimeoutExpired):
        ts = None
    _ORDER_CACHE[branch] = ts
    return ts


def check_order(ev: dict, now: float) -> tuple[str, str]:
    """查名下工单是否有推进：doing/review 单的分支在窗口内有没有提交。

    没有单 → 返回 NO_ORDER（**不是个人欠账，是派工缺口**），这点必须单独成一档，
    否则"没人派活"会被记成"这个人不干活"。
    """
    agent = str(ev.get("agent") or "")
    max_age = float(ev.get("max_age_h") or 48) * 3600
    if not ORDERS.exists():
        return MISSED, "工单账本不存在"
    try:
        data = json.loads(ORDERS.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return MISSED, "工单账本读不出：%s" % exec_short(exc)
    mine = [rec for rec in data.values()
            if isinstance(rec, dict)
            and str(rec.get("owner") or "").strip() == agent
            and str(rec.get("status") or "").lower() in ("doing", "review")]
    if not mine:
        return NO_ORDER, "名下无 doing/review 单——派工缺口，不计个人欠账"
    # 改派闸：单子换过主人时，前任的提交不能算新主人的产出。
    # 从 note 里读最近一次「YYYY-MM-DD HH:MM 改派」作为下界，只认之后的提交。
    floor_ts = 0.0
    floor_text = ""
    for rec in mine:
        for m in re.finditer(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*改派", str(rec.get("note") or "")):
            try:
                ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").timestamp()
            except ValueError:
                continue
            if ts > floor_ts:
                floor_ts, floor_text = ts, m.group(1)
    newest = None
    best = None
    for rec in mine:
        branch = str(rec.get("branch") or "")
        ts = _branch_commit_ts(branch) if branch else None
        if ts and ts > floor_ts and (newest is None or ts > newest):
            newest = ts
            best = branch
    if newest is None:
        if floor_ts:
            return MISSED, "%d 单在名下，但 %s 改派后无提交" % (len(mine), floor_text)
        return MISSED, "%d 单在名下，但分支都没有可读的提交记录" % len(mine)
    age = now - newest
    if age <= max_age:
        return DONE, "%d 单在名下，%s 最近 %.1f 小时内有提交" % (len(mine), best, age / 3600)
    return MISSED, "%d 单在名下，但分支 %s 已 %.1f 小时无提交" % (len(mine), best, age / 3600)


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 2.0) -> bool:
    """TCP 连一下端口（本机探活，不依赖外部网络）。"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def check_port(ev: dict) -> tuple[str, str]:
    """port 证据：声明的端口全部监听才算交付（2026-10-26 AGENTHEALTH 接线）。"""
    ports = [int(p) for p in (ev.get("ports") or []) if str(p).strip()]
    if not ports:
        return MISSED, "evidence.ports 为空"
    closed = [p for p in ports if not _port_open(p)]
    if closed:
        return MISSED, "端口未监听：%s" % ", ".join(str(p) for p in closed)
    return DONE, "端口均监听：%s" % ", ".join(str(p) for p in ports)


def exec_short(exc) -> str:
    return type(exc).__name__ + ": " + str(exc)[:80]


def evaluate_item(item: dict, now_dt: datetime) -> dict:
    now = now_dt.timestamp()
    spec = _parse_freq(item.get("freq") or "")
    state, _ = due_state(spec, now_dt)
    ev = item.get("evidence") or {}
    kind = ev.get("type")

    if state == NA:
        status, evidence = NA, "按频率今天不该做（%s）" % item.get("freq")
    elif state == NOT_YET:
        status, evidence = NOT_YET, "今天 %s 才到期" % item.get("freq")
    elif kind == "manual":
        status, evidence = MANUAL, str(ev.get("note") or "无自动判据")
    elif kind == "port":
        status, evidence = check_port(ev)
    elif kind == "on_demand":
        # 按决策/需求触发、无每日产物的事项：显式标不适用，而不是永久"待接线"。
        status, evidence = NA, str(ev.get("note") or "按需触发，无固定日产")
    elif kind == "file":
        status, evidence = check_file(ev, now)
    elif kind == "log_regex":
        status, evidence = check_log_regex(ev, now)
    elif kind == "command":
        status, evidence = check_command(ev, now)
    elif kind == "command_out":
        status, evidence = check_command_out(ev, now)
    elif kind == "order":
        status, evidence = check_order(ev, now)
    elif kind == "ledger":
        status, evidence = check_ledger(ev, now)
    else:
        status, evidence = MANUAL, "未知证据类型：%r" % (kind,)

    return {
        "id": item.get("id"),
        "agent": item.get("agent"),
        "dept": item.get("dept"),
        "title": item.get("title"),
        "freq": item.get("freq"),
        "why": item.get("why"),
        "status": status,
        "evidence": evidence,
    }


# ---------------------------------------------------------------- 看板
def build(agent_filter: str | None = None) -> dict:
    now_dt = datetime.now()
    try:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"error": "事项目录读不出：%s" % exc}
    rows = [evaluate_item(it, now_dt) for it in catalog.get("items") or []]
    names = load_names()
    for row in rows:                     # 人名来自 HR 档案；取不到就留空，界面回退席码
        row["name"] = names.get(row.get("agent") or "") or ""
    if agent_filter:
        rows = [r for r in rows if r["agent"] == agent_filter]

    by_agent: dict[str, list] = {}
    for r in rows:
        by_agent.setdefault(r["agent"] or "?", []).append(r)

    summary = {s: sum(1 for r in rows if r["status"] == s)
               for s in (DONE, MISSED, NOT_YET, NA, MANUAL, NO_ORDER)}
    summary["总事项"] = len(rows)
    summary["_note"] = "trading_day 按周末近似判定；真实交易日需接行情湖"
    return {
        "generated_at": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "weekday": now_dt.strftime("%A"),
        "summary": summary,
        "agents": {k: sorted(v, key=lambda x: (x["status"] != MISSED, x["id"]))
                   for k, v in sorted(by_agent.items())},
        "rows": rows,
    }


def render(card: dict, show_all: bool) -> None:
    if card.get("error"):
        print(card["error"], file=sys.stderr)
        return
    s = card["summary"]
    print("每日工作项点名  %s（%s）" % (card["generated_at"], card["weekday"]))
    print("共 %d 项 | 已交付 %d | 未交付 %d | 无单可干 %d | 未到点 %d | 不适用 %d | 待接线 %d\n"
          % (s["总事项"], s[DONE], s[MISSED], s[NO_ORDER], s[NOT_YET], s[NA], s[MANUAL]))
    for agent, items in card["agents"].items():
        miss = [i for i in items if i["status"] == MISSED]
        idle = [i for i in items if i["status"] == NO_ORDER]
        tag = "有欠账" if miss else ("无单可干" if idle else "无欠账")
        nm = next((i.get("name") for i in items if i.get("name")), "")
        print("● %s  %s" % (("%s(%s)" % (nm, agent)) if nm else agent, tag))
        for it in items:
            if not show_all and it["status"] in (NA, NOT_YET):
                continue
            mark = {DONE: "✓", MISSED: "✗", MANUAL: "…", NA: "-",
                    NOT_YET: "·", NO_ORDER: "◦"}.get(it["status"], "?")
            print("   %s [%s] %-8s %s" % (mark, it["id"], it["status"], it["title"]))
            if it["status"] != DONE:
                print("        %s" % it["evidence"])
        print()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="每日工作项点名（事项目录 → 交付判定）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--agent")
    ap.add_argument("--all", action="store_true", help="含未到点/不适用")
    ap.add_argument("--no-ledger", action="store_true", help="不落当日台账")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    card = build(args.agent)
    if card.get("error"):
        print(card["error"], file=sys.stderr)
        return 2

    if not args.no_ledger and not args.agent:
        LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        out = LEDGER_DIR / ("agent_daily_%s.json" % datetime.now().strftime("%Y%m%d"))
        try:
            out.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as exc:
            print("台账写盘失败：%s" % exc, file=sys.stderr)

    if args.json:
        print(json.dumps(card, ensure_ascii=False, indent=2))
    else:
        render(card, args.all)
    return 0


if __name__ == "__main__":
    sys.exit(main())
