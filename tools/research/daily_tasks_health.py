# -*- coding: utf-8 -*-
"""每日任务健康监控 + 失败飞书告警（task-108）。

监控对象（三个已注册的每日任务）：
  ZeroBound_DailyKB        08:35  → portal/static/kb_learning_digest.json
  ZeroBound_DailyScreening 17:35  → research/reports/daily_screening_<date>.json
  ZeroBound_DailyPush      17:45  → research/reports/kb_daily_push_send_<date>.json

检查项：
  ① 计划任务 Last Run Time / Last Result（schtasks /query /V /FO LIST 解析，兼容中英文标签）；
  ② 任务日志最新一行时间戳与 rc（C:\\Temp\\zero_daily_*.log，rc=0/EXCEPTION）；
  ③ 产出文件新鲜度（按 stale_hours 阈值；缺失/过期→告警）。

告警：默认 **dry-run**（只写报告不发送）；`--send` 时调用 news_collector/send_feishu.send_text 发文本告警。
输出：research/reports/daily_tasks_health_<tag>.{json,md}
运行：python tools/research/daily_tasks_health.py [--send] [--out-dir DIR] [--tag 20261101]
返回码：只要监控流程本身成功即 0（告警与否见 JSON）；避免计划任务因「有告警」误判失败。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
OUT_DEFAULT = REPO / "research" / "reports"
SCHEMA_VERSION = "daily_tasks_health.v1"
DEFAULT_TAG = datetime.now().astimezone().strftime("%Y%m%d")

TASKS = [
    {"name": "ZeroBound_DailyKB", "label": "每日知识库", "log": r"C:\Temp\zero_daily_kb.log",
     "outputs": [{"path": "portal/static/kb_learning_digest.json"}], "stale_hours": 30},
    {"name": "ZeroBound_DailyScreening", "label": "每日筛选", "log": r"C:\Temp\zero_daily_screening.log",
     "outputs": [{"dir": "research/reports", "pattern": "daily_screening_????????.json"}],
     "stale_hours": 30},
    {"name": "ZeroBound_DailyPush", "label": "每日飞书推送", "log": r"C:\Temp\zero_daily_push.log",
     "outputs": [{"dir": "research/reports", "pattern": "kb_daily_push_send_*.json"}],
     "stale_hours": 30},
    # ---- 学习线 4 任务（task-133：rc_pipeline/rc_gate 双 rc + gate/dashboard 新鲜度 ≤36h）----
    {"name": "ZeroBound_LearningIntake", "label": "学习-入库", "log": r"C:\Temp\zero_learning_intake.log",
     "outputs": [{"dir": "research/reports", "pattern": "learning_gate_*.json"}],
     "stale_hours": 36, "gate": True},
    {"name": "ZeroBound_LearningPipeline", "label": "学习-流水线", "log": r"C:\Temp\zero_learning_pipeline.log",
     "outputs": [{"dir": "research/reports", "pattern": "learning_gate_*.json"},
                 {"path": "portal/static/learning_projects_dashboard.json"}],
     "stale_hours": 36, "gate": True},
    {"name": "ZeroBound_LearningGovernance", "label": "学习-治理", "log": r"C:\Temp\zero_learning_governance.log",
     "outputs": [{"dir": "research/reports", "pattern": "learning_gate_*.json"}],
     "stale_hours": 36, "gate": True},
    {"name": "ZeroBound_LearningStandardize", "label": "学习-标准化", "log": r"C:\Temp\zero_learning_standardize.log",
     "outputs": [{"dir": "research/reports", "pattern": "learning_gate_*.json"}],
     "stale_hours": 36, "gate": True},
]
_LOG_RC_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(\S+)\s+rc=(-?\d+)\s*$")
_LOG_RC2_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(\S+)\s+"
                         r"rc_pipeline=(-?\d+)\s+rc_gate=(-?\d+)\s*$")
_LOG_EXC_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(\S+)\s+EXCEPTION:\s*(.+)$")
_LAST_RUN_RE = re.compile(r"(?:Last Run Time|上次运行时间|上次執行時間)[:：]\s*(.+)", re.I)
_LAST_RESULT_RE = re.compile(r"(?:Last Result|上次结果|上次結果)[:：]\s*(-?\d+)", re.I)
_STATUS_RE = re.compile(r"(?:Status|状态|狀態)[:：]\s*(\S+)")


# ---------------------------------------------------------------------------
# 解析（纯函数，可单测）
# ---------------------------------------------------------------------------
def parse_schtasks_listing(text: str) -> dict:
    """解析 `schtasks /query /v /fo LIST` 文本（中英文标签兼容）。"""
    out = {"found": False, "last_run_time": None, "last_result": None, "status": None}
    if not text:
        return out
    for line in str(text).splitlines():
        m = _LAST_RUN_RE.search(line)
        if m:
            out["last_run_time"] = m.group(1).strip()
            out["found"] = True
            continue
        m = _LAST_RESULT_RE.search(line)
        if m:
            out["last_result"] = int(m.group(1))
            out["found"] = True
            continue
        m = _STATUS_RE.search(line)
        if m:
            out["status"] = m.group(1).strip()
    return out


def query_task(name: str, runner=None) -> dict:
    """查询计划任务（schtasks）；失败/未找到返回 found=False + error。"""
    cmd = ["schtasks", "/query", "/tn", name, "/v", "/fo", "LIST"]
    try:
        proc = (runner or subprocess.run)(cmd, capture_output=True, timeout=20)
        raw = proc.stdout or b""
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="replace")
            if "Last Run Time" not in text and "上次" not in text:
                text = raw.decode("gbk", errors="replace")
        else:
            text = str(raw)
    except Exception as exc:  # noqa: BLE001
        return {"found": False, "last_run_time": None, "last_result": None,
                "status": None, "error": f"{type(exc).__name__}: {exc}"}
    info = parse_schtasks_listing(text)
    if not info["found"]:
        info["error"] = "未解析到 Last Run Time/Last Result（任务可能不存在）"
    return info


def parse_last_log(path: str | Path, now: datetime | None = None) -> dict:
    """日志最新 rc/EXCEPTION 行 → {timestamp, rc, line, age_hours}；无日志返回 error。"""
    p = Path(path)
    now = now or datetime.now().astimezone()
    if not p.exists():
        return {"exists": False, "timestamp": None, "rc": None, "line": None,
                "age_hours": None, "error": f"日志不存在：{p}"}
    try:
        lines = p.read_text(encoding="utf-8-sig", errors="replace").splitlines()[-2000:]
    except OSError as exc:
        return {"exists": False, "timestamp": None, "rc": None, "line": None,
                "age_hours": None, "error": f"日志读取失败：{exc}"}
    for line in reversed(lines):
        line = line.lstrip("\ufeff")
        m = _LOG_RC2_RE.match(line.strip())
        if m:
            ts = datetime.fromisoformat(m.group(1)).astimezone()
            rcs = {"rc_pipeline": int(m.group(3)), "rc_gate": int(m.group(4))}
            return {"exists": True, "timestamp": ts.isoformat(timespec="seconds"),
                    "rc": 0 if all(v == 0 for v in rcs.values()) else 1,
                    "rcs": rcs, "line": line.strip(),
                    "age_hours": round((now - ts).total_seconds() / 3600, 2)}
        m = _LOG_RC_RE.match(line.strip())
        if m:
            ts = datetime.fromisoformat(m.group(1)).astimezone()
            return {"exists": True, "timestamp": ts.isoformat(timespec="seconds"),
                    "rc": int(m.group(3)), "line": line.strip(),
                    "age_hours": round((now - ts).total_seconds() / 3600, 2)}
        m = _LOG_EXC_RE.match(line.strip())
        if m:
            ts = datetime.fromisoformat(m.group(1)).astimezone()
            return {"exists": True, "timestamp": ts.isoformat(timespec="seconds"),
                    "rc": 1, "line": line.strip(),
                    "age_hours": round((now - ts).total_seconds() / 3600, 2),
                    "exception": m.group(3)}
    return {"exists": True, "timestamp": None, "rc": None, "line": None,
            "age_hours": None, "error": "日志中未找到 rc=/EXCEPTION 行"}


def latest_output(spec: dict, now: datetime | None = None) -> dict:
    """产出文件新鲜度：spec={path} 或 {dir, pattern} → 最新文件 + age_hours/fresh。"""
    now = now or datetime.now().astimezone()
    if spec.get("path"):
        candidates = [REPO / spec["path"]]
    else:
        base = REPO / spec.get("dir", "")
        candidates = sorted(base.glob(spec.get("pattern", "*")),
                            key=lambda p: p.stat().st_mtime, reverse=True) if base.exists() else []
    existing = [p for p in candidates if p.exists()]
    if not existing:
        return {"path": None, "exists": False, "mtime": None, "age_hours": None,
                "fresh": False, "spec": spec}
    p = existing[0]
    mtime = datetime.fromtimestamp(p.stat().st_mtime).astimezone()
    age = round((now - mtime).total_seconds() / 3600, 2)
    try:
        rel = str(p.relative_to(REPO))
    except ValueError:
        rel = str(p)
    return {"path": rel, "exists": True,
            "mtime": mtime.isoformat(timespec="seconds"), "age_hours": age,
            "fresh": age <= float(spec.get("stale_hours") or 30), "spec": spec}


# ---------------------------------------------------------------------------
# 检查 / 报告 / 告警
# ---------------------------------------------------------------------------
def gate_status(now: datetime | None = None, stale_hours: float = 36.0) -> dict:
    """学习 gate 最新产出（research/reports/learning_gate_*.json）状态与新鲜度。"""
    now = now or datetime.now().astimezone()
    files = sorted((REPO / "research" / "reports").glob("learning_gate_*.json"),
                   key=lambda p: (not re.fullmatch(r"learning_gate_\d{8}\.json", p.name),
                                  -p.stat().st_mtime))
    if not files:
        return {"exists": False, "ok": None, "age_hours": None, "fresh": False}
    p = files[0]
    age = round((now - datetime.fromtimestamp(p.stat().st_mtime).astimezone()).total_seconds() / 3600, 2)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return {"exists": True, "path": str(p.relative_to(REPO)), "ok": data.get("ok"),
            "generated_at": data.get("generated_at"), "summary": data.get("summary"),
            "age_hours": age, "fresh": age <= stale_hours}


def check_task(cfg: dict, now: datetime | None = None, *, info: dict | None = None,
               log: dict | None = None, outputs: list[dict] | None = None,
               gate: dict | None = None) -> dict:
    now = now or datetime.now().astimezone()
    info = info if info is not None else query_task(cfg["name"])
    if log is None:
        log = (parse_last_log(cfg["log"], now) if cfg.get("log")
               else {"exists": False, "timestamp": None, "rc": None, "rcs": None,
                     "line": None, "age_hours": None, "error": "未配置日志路径"})
    outputs = outputs if outputs is not None else [
        latest_output({**spec, "stale_hours": cfg.get("stale_hours")}, now)
        for spec in cfg.get("outputs") or []]
    issues: list[str] = []
    if not info.get("found"):
        issues.append("计划任务未找到")
    if info.get("last_result") not in (0, None):
        issues.append(f"Last Result={info['last_result']}")
    if log.get("rcs"):
        bad = {k: v for k, v in log["rcs"].items() if v != 0}
        if bad:
            issues.append("日志 " + " ".join(f"{k}={v}" for k, v in log["rcs"].items())
                          + f"（{log.get('line')}）")
    elif log.get("rc") not in (0, None):
        issues.append(f"日志 rc={log['rc']}（{log.get('line') or log.get('error')}）")
    if log.get("exists") is False:
        issues.append(log.get("error") or "日志缺失")
    if log.get("age_hours") is not None and log["age_hours"] > float(cfg.get("stale_hours") or 30):
        issues.append(f"日志过期 {log['age_hours']}h")
    for out in outputs:
        if not out.get("exists"):
            issues.append(f"产出缺失：{out.get('spec')}")
        elif not out.get("fresh"):
            issues.append(f"产出过期 {out.get('age_hours')}h：{out.get('path')}")
    gate_info = None
    if cfg.get("gate"):
        gate_info = gate if gate is not None else gate_status(now)
        if not gate_info.get("exists"):
            issues.append("gate 产出缺失（learning_gate_*.json）")
        else:
            if gate_info.get("ok") is not True:
                issues.append(f"gate 未通过：ok={gate_info.get('ok')} summary={gate_info.get('summary')}")
            if gate_info.get("age_hours") is not None and gate_info["age_hours"] > 36:
                issues.append(f"gate 产出过期 {gate_info['age_hours']}h（>36h）")
    return {"name": cfg["name"], "label": cfg.get("label"), "status": "ok" if not issues else "alert",
            "issues": issues, "task": info, "log": log, "outputs": outputs, "gate": gate_info}


def build_report(tasks: list[dict], now: datetime | None = None, *,
                 tag: str = DEFAULT_TAG, dry_run: bool = True,
                 mcp: dict | None = None) -> dict:
    now = now or datetime.now().astimezone()
    alerts = [{"task": t["name"], "label": t.get("label"), "issues": t["issues"],
               "last_result": (t.get("task") or {}).get("last_result"),
               "log_line": (t.get("log") or {}).get("line"),
               "log_rcs": (t.get("log") or {}).get("rcs"),
               "gate_ok": (t.get("gate") or {}).get("ok"),
               "gate_age_hours": (t.get("gate") or {}).get("age_hours"),
               "last_log": (t.get("log") or {}).get("timestamp")}
              for t in tasks if t["status"] == "alert"]
    # MCP 协议违规不是计划任务，但同样要进同一份告警——否则它永远不会被发现。
    # 只有 ok is False 才告警；ok=None（不可测）不冒充通过、也不制造噪音，
    # 记在 rep["mcp_protocol"] 里供人查。
    if mcp and mcp.get("ok") is False:
        alerts.append({"task": "MCP 协议门", "label": "工具握手", "issues": mcp.get("issues") or [],
                       "last_result": None, "log_line": None, "log_rcs": None,
                       "gate_ok": None, "gate_age_hours": None, "last_log": None})
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "daily_tasks_health",
        "tag": tag,
        "generated_at": now.isoformat(timespec="seconds"),
        "dry_run": bool(dry_run),
        "summary": {"total": len(tasks), "ok": sum(1 for t in tasks if t["status"] == "ok"),
                    "alert": len(alerts)},
        "tasks": tasks,
        "mcp_protocol": mcp,
        "alerts": alerts,
        "alert_text": format_alert(alerts) if alerts else "",
        "disclaimer": "研究/运维监控输出；告警默认 dry-run，--send 才发飞书。",
    }


def format_alert(alerts: list[dict]) -> str:
    """告警文本：任务名 / Last Result / 最后日志行 / 问题清单。"""
    if not alerts:
        return ""
    lines = ["【零界·每日任务健康告警】", ""]
    for a in alerts:
        lines.append(f"⚠️ {a['task']}（{a.get('label') or ''}）")
        lines.append(f"  Last Result: {a.get('last_result')}")
        if a.get("last_log"):
            lines.append(f"  最后日志: {a['last_log']}")
        if a.get("log_line"):
            lines.append(f"  日志行: {str(a['log_line'])[:180]}")
        if a.get("log_rcs"):
            lines.append(f"  rc: {a['log_rcs']}")
        if a.get("gate_ok") is not None:
            lines.append(f"  gate: ok={a['gate_ok']} age={a.get('gate_age_hours')}h")
        for issue in a.get("issues") or []:
            lines.append(f"  - {issue}")
        lines.append("")
    lines.append("请检查 C:\\Temp\\zero_daily_*.log 并重跑对应任务；本消息为自动告警。")
    return "\n".join(lines)


def render_md(rep: dict) -> str:
    L = [f"# 每日任务健康监控（{rep['tag']}）", ""]
    L.append(f"- 生成：{rep['generated_at']}；dry_run={rep['dry_run']}；"
             f"总任务 {rep['summary']['total']}，健康 {rep['summary']['ok']}，告警 {rep['summary']['alert']}")
    L.append("")
    L += ["| 任务 | 状态 | Last Result | 日志时间 | 日志 rc | 日志 age(h) | 产出 | 产出 age(h) |",
          "|---|---|---|---|---|---|---|---|"]
    for t in rep["tasks"]:
        log_t = t.get("log") or {}
        out = (t.get("outputs") or [{}])[0]
        L.append(f"| {t['name']}（{t.get('label') or ''}）| {'✅' if t['status'] == 'ok' else '🔴 告警'} | "
                 f"{(t.get('task') or {}).get('last_result')} | {log_t.get('timestamp') or '—'} | "
                 f"{log_t.get('rc')} | {log_t.get('age_hours')} | "
                 f"{out.get('path') or '缺失'} | {out.get('age_hours')} |")
    L.append("")
    L += ["## 告警", ""]
    if rep["alerts"]:
        for a in rep["alerts"]:
            L.append(f"### {a['task']}（{a.get('label') or ''}）")
            for issue in a["issues"]:
                L.append(f"- {issue}")
            L.append("")
        L += ["### 告警文本", "", "```", rep["alert_text"], "```", ""]
    else:
        L += ["- 无告警 ✅", ""]
    if rep.get("mcp_protocol"):
        m = rep["mcp_protocol"]
        state = "未验证" if m.get("ok") is None else ("合规" if m.get("ok") else "有协议违规")
        L += ["## MCP 协议门", "",
              f"- 状态：{state}；PASS {m.get('pass')} / FAIL {m.get('fail')} / SKIP {m.get('skip')}；"
              f"rc={m.get('rc')}",
              "- 判据：MCP 握手第二步 `notifications/initialized` 无 id，服务端**不得回包**；"
              "回了 `{\"id\": null, ...}` 严格客户端即判违规中止连接。",
              "- 范围：各 profile `config.yaml` 的 stdio 服务端；**SKIP 是「不可测」，不等于合格**。", ""]
        for it in (m.get("issues") or []):
            L.append(f"  - {it}")
        for it in (m.get("unverified") or []):
            L.append(f"  - [未验证] {it}")
        L.append("")
    L += ["## 口径", "",
          "- 计划任务：`schtasks /query /tn <name> /v /fo LIST` 解析 Last Run Time/Last Result；",
          "- 日志：`C:\\Temp\\zero_daily_*.log` 最新 `YYYY-MM-DD HH:mm:ss <task> rc=N` 行；",
          "- 新鲜度：产出文件 mtime 超过 `stale_hours`（默认 30h）即过期；",
          "- 默认 dry-run 不发送；`--send` 才调用 news_collector/send_feishu 发送文本告警。", ""]
    return "\n".join(L)


def write_outputs(rep: dict, out_dir: Path | str = OUT_DEFAULT) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jp = out / f"daily_tasks_health_{rep['tag']}.json"
    mp = out / f"daily_tasks_health_{rep['tag']}.md"
    jp.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    mp.write_text(render_md(rep), encoding="utf-8")
    return {"json": str(jp), "md": str(mp)}


def check_mcp_protocol(timeout: float = 90.0) -> dict:
    """MCP 协议门：配置里的 stdio 服务端必须对「通知」闭嘴。

    为什么并入本巡检
        2026-11-08~11-15 张松 profile 连续 7 天 `0 tool(s) from 0 server(s)`，
        根因是服务端对握手通知 `notifications/initialized` 回了一条 id=null 的
        -32601；Hermes 对冷却中的服务器不打印原始错误，日志只剩
        `not attempted (in retry cooldown)`，看着像服务挂了。这类故障**没有任何
        计划任务会红**，只能靠主动探测发现，所以挂到每日巡检。

    三态（不可测 ≠ 合格）
        ok=True    全部可测且合规
        ok=False   有协议违规（计入告警）
        ok=None    探针缺失/超时/无 python —— 记「未验证」，**不计入 ok**
    """
    probe = REPO.parent / "zerobound-kg" / "factor_proto" / "mcp_protocol_probe.py"
    py = Path(r"C:\Program Files\Python311\python.exe")
    if not probe.exists():
        return {"ok": None, "pass": 0, "fail": 0, "skip": 0, "rc": None,
                "issues": [f"探针缺失，未验证：{probe}"], "error": "probe_missing"}
    if not py.exists():
        return {"ok": None, "pass": 0, "fail": 0, "skip": 0, "rc": None,
                "issues": [f"解释器缺失，未验证：{py}"], "error": "python_missing"}
    try:
        r = subprocess.run([str(py), str(probe)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": None, "pass": 0, "fail": 0, "skip": 0, "rc": None,
                "issues": [f"探针超时 {timeout:.0f}s，未验证"], "error": "timeout"}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": None, "pass": 0, "fail": 0, "skip": 0, "rc": None,
                "issues": [f"探针未执行 {type(exc).__name__}: {exc}"], "error": "exec_failed"}

    lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    n_pass = sum(1 for ln in lines if ln.startswith("[PASS]"))
    n_fail = sum(1 for ln in lines if ln.startswith("[FAIL]"))
    n_skip = sum(1 for ln in lines if ln.startswith("[SKIP]"))
    issues = [ln[len("[FAIL] "):] for ln in lines if ln.startswith("[FAIL]")]
    unverified = [ln[len("[SKIP] "):] for ln in lines if ln.startswith("[SKIP]")]
    if n_fail:
        return {"ok": False, "pass": n_pass, "fail": n_fail, "skip": n_skip,
                "rc": r.returncode, "issues": issues, "unverified": unverified, "error": None}
    if n_pass == 0:
        # 一行 PASS 都没有：可能压根没找到服务端，也可能探针自己崩了。
        # 不判通过 —— 空集合不等于合格。
        tail = (r.stderr or "").strip().splitlines()[-1:] or [""]
        return {"ok": None, "pass": 0, "fail": 0, "skip": n_skip, "rc": r.returncode,
                "issues": [f"无 PASS 行，未验证：{tail[0][:120]}"], "error": "no_pass"}
    return {"ok": True, "pass": n_pass, "fail": 0, "skip": n_skip, "rc": r.returncode,
            "issues": [], "unverified": unverified, "error": None}


def send_alert(text: str) -> dict:
    """调用 news_collector.send_feishu 发文本告警；失败返回 error（不抛出）。"""
    try:
        sys.path.insert(0, str(REPO / "news_collector"))
        from send_feishu import send_text  # noqa: PLC0415
        ok = bool(send_text(text, kind="daily_health_alert"))
        return {"ok": ok, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="每日任务健康监控（默认 dry-run，不发送）")
    ap.add_argument("--send", action="store_true", help="有告警时发送飞书（默认 dry-run）")
    ap.add_argument("--skip-mcp", action="store_true",
                    help="跳过 MCP 协议门（默认跑；仅供离线调试，生产勿用）")
    ap.add_argument("--out-dir", default=str(OUT_DEFAULT))
    ap.add_argument("--tag", default=None, help="输出文件名日期标签（默认今天）")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    now = datetime.now().astimezone()
    tag = args.tag or now.strftime("%Y%m%d")
    tasks = [check_task(cfg, now) for cfg in TASKS]
    mcp = None if args.skip_mcp else check_mcp_protocol()
    rep = build_report(tasks, now, tag=tag, dry_run=not args.send, mcp=mcp)
    if args.send and rep["alerts"]:
        rep["send_result"] = send_alert(rep["alert_text"])
    rep["outputs"] = write_outputs(rep, args.out_dir)
    print(json.dumps({"tag": rep["tag"], "dry_run": rep["dry_run"],
                      "summary": rep["summary"],
                      "mcp": (None if rep.get("mcp_protocol") is None else
                              {k: rep["mcp_protocol"].get(k)
                               for k in ("ok", "pass", "fail", "skip", "rc", "error")}),
                      "alerts": [a["task"] for a in rep["alerts"]],
                      "send_result": rep.get("send_result"),
                      "outputs": rep["outputs"]}, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
