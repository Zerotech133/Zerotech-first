# -*- coding: utf-8 -*-
"""黄忠健康告警联动（只读评估，**默认 dry-run**；--apply 才写 flag/state）。

把 `tools/huangzhong_health.py --json` 的工具链心跳失败转成可被既有 B 通道
（`jev-eval/alert_forward.ps1` 转发 *.flag → `backup_alert.py` → 飞书）消费的告警 flag，
带「连续失败阈值 + 去重窗口 + 演练隔离」，供后续定时化（计划任务变更需另行批准）。

流程：
  1. 经模块级 `_run(cmd, cwd, timeout)` 执行 `<repo>/tools/huangzhong_health.py --json`
     （cwd=repo，默认超时 180s）；解析 status/rc/失败检查项；
     **health 子进程失败/输出不可解析也计一次「健康失败」**（fail-visible，不吞掉）；
  2. 状态文件 `work/huangzhong/health_state.json`（**原子写**）：
     `{consecutive_fail, last_alert_at, last_status, updated_at}`；
     health 失败（rc>=2 或 status=fail 或执行/解析错误）→ consecutive_fail+1，否则清零；
  3. 决策：`consecutive_fail >= --threshold`（默认 2）且距 `last_alert_at`
     超过 `--dedupe-hours`（默认 6h）→ 触发告警；
  4. **默认 dry-run**：只输出「将写 flag 内容 / 是否触发 / state 将如何变化」，**零写入**；
     `--apply` 才写 `alerts_dir/huangzhong_health_fail_<ts>.flag`（正文含 status / 失败检查项 /
     时间 / 查看链接；env HUANGZHONG_DRILL=1 时加【演练】前缀），写后更新 state；
     去重窗口内只累计不再重复告警。
  5. 写盘边界：仅 `health_state.json` 与告警 flag；不改 health 工具、不写 work/ 其他文件、
     不发网络请求（flag 由既有 alert_forward 通道消费）。
退出码：0=无需告警/已告警成功；1=达到阈值但 dry-run；2=执行/解析/写盘错误（fail-visible）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK_SUBDIR = Path("work") / "huangzhong"
DEFAULT_ALERTS_DIR = Path(r"C:\aios\jev-eval\alerts")
DEFAULT_THRESHOLD = 2
DEFAULT_DEDUPE_HOURS = 6.0
DEFAULT_TIMEOUT = 180.0
DEFAULT_VIEW_URL = "http://127.0.0.1:8734/static/huangzhong_events.html"
STATE_NAME = "health_state.json"
FLAG_PREFIX = "huangzhong_health_fail"
TOOL_VERSION = "v1 (2026-11-01)"


# ------------------------------------------------------------------ 基础工具

def _now() -> dt.datetime:
    """当前时间（本机时区 aware）；测试可注入 now。"""
    return dt.datetime.now().astimezone()


def _iso(ts: dt.datetime | None) -> str | None:
    return ts.isoformat(timespec="seconds") if isinstance(ts, dt.datetime) else None


def _parse_ts(raw) -> dt.datetime | None:
    """解析时间戳（ISO8601/空格格式/epoch 秒）；无时区按本机时区解释。"""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        try:
            return dt.datetime.fromtimestamp(float(raw), tz=dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    parsed = None
    try:
        parsed = dt.datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
                    "%Y-%m-%d"):
            try:
                parsed = dt.datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _read_json(path: Path) -> tuple[object | None, str | None]:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None, "missing"
    except OSError as e:
        return None, "%s: %s" % (type(e).__name__, e)
    try:
        return json.loads(text), None
    except ValueError as e:
        return None, "JSON 解析失败: %s" % e


def _atomic_write_text(path: Path, text: str) -> None:
    """原子写：同目录临时文件 + os.replace（Windows 同卷可原子替换）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _run(cmd, cwd=None, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """执行外部命令（模块级可注入；测试用 fake）。→ {ok, rc, stdout, stderr}。"""
    try:
        r = subprocess.run(cmd, cwd=str(cwd or ROOT), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=float(timeout))
        return {"ok": getattr(r, "returncode", 1) == 0,
                "rc": getattr(r, "returncode", None),
                "stdout": getattr(r, "stdout", "") or "",
                "stderr": getattr(r, "stderr", "") or "",
                "timeout": False}
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else ""
        return {"ok": False, "rc": None, "stdout": out,
                "stderr": "TimeoutExpired: >%.0fs" % float(timeout),
                "timeout": True}
    except Exception as e:  # noqa: BLE001  fail-visible，不中断
        return {"ok": False, "rc": None, "stdout": "",
                "stderr": "%s: %s" % (type(e).__name__, e), "timeout": False}


def _parse_health_payload(stdout: str) -> dict | None:
    """解析 health --json 输出；容忍前后噪声（截取首 { 到末 }）。"""
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


# ------------------------------------------------------------------ 状态读写

def _default_state() -> dict:
    return {"consecutive_fail": 0, "last_alert_at": None,
            "last_status": None, "updated_at": None}


def load_state(path: Path) -> tuple[dict, str | None]:
    """读 health_state.json；缺失返回初始状态；坏 JSON 返回 (初始状态, 错误)。"""
    data, err = _read_json(Path(path))
    if err == "missing":
        return _default_state(), None
    if err or not isinstance(data, dict):
        return _default_state(), err or "顶层不是对象"
    state = _default_state()
    raw = data.get("consecutive_fail")
    try:
        state["consecutive_fail"] = max(0, int(raw)) if raw is not None else 0
    except (TypeError, ValueError):
        state["consecutive_fail"] = 0
    last_alert = data.get("last_alert_at")
    state["last_alert_at"] = last_alert if isinstance(last_alert, str) and \
        last_alert.strip() else None
    last_status = data.get("last_status")
    state["last_status"] = last_status if isinstance(last_status, str) else None
    updated = data.get("updated_at")
    state["updated_at"] = updated if isinstance(updated, str) else None
    return state, None


def state_to_json(state: dict) -> str:
    return json.dumps({k: state.get(k) for k in
                       ("consecutive_fail", "last_alert_at", "last_status",
                        "updated_at")},
                      ensure_ascii=False, indent=1) + "\n"


# ---------------------------------------------------------------- 告警 flag 构造

def _alerts_dir(explicit) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("HUANGZHONG_ALERTS_DIR", "").strip()
    return Path(env) if env else DEFAULT_ALERTS_DIR


def build_flag_content(health: dict, new_consecutive: int, threshold: int,
                       dedupe_hours: float, now: dt.datetime) -> str:
    """构造告警 flag 正文（含 status/失败检查项/时间/查看链接；演练前缀）。"""
    drill = str(os.environ.get("HUANGZHONG_DRILL", "")).strip().lower() in (
        "1", "true", "yes", "y", "on")
    prefix = "【演练】" if drill else ""
    status = health.get("status") or ("fail" if health.get("failure") else "unknown")
    lines = ["%s🔴 黄忠工具链健康告警" % prefix,
             "type=huangzhong_health_fail",
             "status=%s" % status,
             "consecutive_fail=%d/%d" % (new_consecutive, threshold),
             "threshold=%d" % int(threshold),
             "dedupe_hours=%g" % float(dedupe_hours),
             "时间=%s" % _iso(now),
             "health_rc=%s" % (health.get("rc") if health.get("rc") is not None
                               else "null")]
    if drill:
        lines.append("drill=true")
    failed = health.get("failed_checks") or []
    if failed:
        lines.append("失败检查项：")
        for c in failed[:10]:
            lines.append("- %s %s：%s" % (c.get("id") or "-",
                                          c.get("name") or "-",
                                          (c.get("detail") or "")[:300]))
    else:
        lines.append("失败检查项：（health 未给出失败项/执行或解析异常）")
    if health.get("parse_error"):
        lines.append("解析错误=%s" % health["parse_error"])
    if health.get("exec_error"):
        lines.append("执行错误=%s" % health["exec_error"])
    view = os.environ.get("HUANGZHONG_ALERT_VIEW_URL", "").strip() or DEFAULT_VIEW_URL
    lines.append("查看：%s" % view)
    return "\n".join(lines) + "\n"


def _unique_flag_path(alerts_dir: Path, now: dt.datetime) -> Path:
    """>>>huangzhong_health_fail_<YYYYmmdd_HHMMSS>.flag（同名冲突自动加序号）。"""
    base = "%s_%s" % (FLAG_PREFIX, now.strftime("%Y%m%d_%H%M%S"))
    candidate = Path(alerts_dir) / (base + ".flag")
    n = 2
    while candidate.exists():
        candidate = Path(alerts_dir) / ("%s_%d.flag" % (base, n))
        n += 1
    return candidate


# ------------------------------------------------------------------ 主流程

def run(apply: bool = False, dry_run_flag: bool = False,
        threshold: int = DEFAULT_THRESHOLD,
        dedupe_hours: float = DEFAULT_DEDUPE_HOURS,
        alerts_dir: Path | str | None = None,
        work_dir: Path | str | None = None,
        repo_root: Path | str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        now: dt.datetime | None = None) -> dict:
    """评估健康失败→告警；默认 dry-run 零写入，--apply 才写 flag/state。"""
    now = now or _now()
    root = Path(repo_root) if repo_root else ROOT
    work = Path(work_dir) if work_dir else root / DEFAULT_WORK_SUBDIR
    state_path = work / STATE_NAME
    alerts = _alerts_dir(alerts_dir)
    dry = not bool(apply)
    rep: dict = {
        "ok": True,
        "tool": "huangzhong_health_alert.py",
        "version": TOOL_VERSION,
        "generated_at": _iso(now),
        "dry_run": dry,
        "apply": bool(apply),
        "threshold": int(threshold),
        "dedupe_hours": float(dedupe_hours),
        "repo_root": str(root),
        "work_dir": str(work),
        "state_path": str(state_path),
        "alerts_dir": str(alerts),
        "health": {},
        "state_before": None,
        "state_after": None,
        "evaluation": {},
        "flag": {"path": None, "content": None, "written": False},
        "warnings": [],
        "error": None,
        "exit_code": 0,
    }
    # 参数校验（fail-visible；零写入）
    if apply and dry_run_flag:
        rep.update({"ok": False, "error": "--dry-run 与 --apply 不能同时使用（零写入）",
                    "exit_code": 2})
        return rep
    if int(threshold) < 1:
        rep.update({"ok": False, "error": "--threshold 必须 ≥1（实际：%r）" % threshold,
                    "exit_code": 2})
        return rep
    if float(dedupe_hours) < 0:
        rep.update({"ok": False,
                    "error": "--dedupe-hours 不能为负（实际：%r）" % dedupe_hours,
                    "exit_code": 2})
        return rep

    # 1) 跑 health
    script = Path(root) / "tools" / "huangzhong_health.py"
    cmd = [sys.executable or "python", str(script), "--json"]
    got = _run(cmd, cwd=root, timeout=float(timeout))
    rc = got.get("rc")
    stdout = got.get("stdout") or ""
    payload = _parse_health_payload(stdout)
    parse_error = None
    if payload is None:
        parse_error = "health --json 输出不可解析（stdout 长度 %d）" % len(stdout)
    exec_error = None
    if rc is None or (not got.get("ok") and rc not in (0, 1, 2)):
        exec_error = "health 子进程执行失败（rc=%r）：%s" % (
            rc, (got.get("stderr") or "")[:300])
    raw_status = payload.get("status") if payload else None
    failure = bool(parse_error or exec_error or (isinstance(rc, int) and rc >= 2)
                   or raw_status == "fail")
    healthy = not failure
    failed_checks = []
    if payload:
        for c in payload.get("checks") or []:
            if isinstance(c, dict) and c.get("status") == "fail":
                failed_checks.append({"id": c.get("id"), "name": c.get("name"),
                                      "status": c.get("status"),
                                      "detail": str(c.get("detail") or "")[:300]})
    health_status = raw_status or ("fail" if failure else "unknown")
    rep["health"] = {
        "cmd": cmd, "rc": rc, "status": raw_status,
        "evaluated_status": health_status, "healthy": healthy,
        "failure": failure, "failed_checks": failed_checks,
        "checks_total": len(payload.get("checks") or []) if payload else 0,
        "parse_error": parse_error, "exec_error": exec_error,
        "stderr": (got.get("stderr") or "")[:500],
        "timeout": bool(got.get("timeout")),
    }
    if parse_error:
        rep["warnings"].append(parse_error)
    if exec_error:
        rep["warnings"].append(exec_error)

    # 2) 状态读取 + 计数
    state, state_err = load_state(state_path)
    if state_err:
        rep["warnings"].append(
            "health_state 不可读/坏 JSON（%s）→ 按初始状态处理（apply 时自愈重写）"
            % state_err)
    prev_consecutive = int(state.get("consecutive_fail") or 0)
    new_consecutive = 0 if healthy else prev_consecutive + 1
    last_alert_raw = state.get("last_alert_at")
    last_alert_at = _parse_ts(last_alert_raw)
    dedupe_delta = (now - last_alert_at) if last_alert_at is not None else None
    within_dedupe = bool(dedupe_delta is not None and
                         dedupe_delta < dt.timedelta(hours=float(dedupe_hours)))
    reached = (not healthy) and new_consecutive >= int(threshold)
    would_alert = bool(reached and not within_dedupe)
    rep["state_before"] = dict(state)
    rep["evaluation"] = {
        "prev_consecutive_fail": prev_consecutive,
        "consecutive_fail": new_consecutive,
        "healthy": healthy,
        "failure": failure,
        "reached_threshold": reached,
        "dedupe_suppressed": bool(reached and within_dedupe),
        "would_alert": would_alert,
        "last_alert_at": last_alert_raw,
        "dedupe_hours": float(dedupe_hours),
        "threshold": int(threshold),
    }

    # 3) 构造 flag 内容（dry-run 也展示「将写什么」）
    content = build_flag_content(rep["health"], new_consecutive, int(threshold),
                                 float(dedupe_hours), now)
    flag_path = _unique_flag_path(alerts, now) if would_alert else None
    rep["flag"] = {"path": str(flag_path) if flag_path else None,
                   "content": content if would_alert else None,
                   "written": False}
    state_after = {
        "consecutive_fail": new_consecutive,
        "last_alert_at": (_iso(now) if (would_alert and not dry)
                          else (last_alert_raw if not would_alert else _iso(now))),
        "last_status": health_status,
        "updated_at": _iso(now),
    }
    if dry and would_alert:
        state_after["last_alert_at"] = _iso(now)   # 干跑显示「假如触发」会写什么
    rep["state_after"] = dict(state_after)
    rep["state_after_json"] = state_to_json(state_after)

    # 4) dry-run：到此为止，零写入
    if dry:
        rc_out = 2 if (parse_error or exec_error) else (1 if would_alert else 0)
        rep["ok"] = rc_out != 2
        rep["exit_code"] = rc_out
        rep["error"] = _join_errors(rep)
        return rep

    # 5) apply：先写 flag（若有），再原子写 state
    write_errors: list[str] = []
    if would_alert and flag_path is not None:
        try:
            _atomic_write_text(flag_path, content)
            rep["flag"]["written"] = True
        except OSError as e:
            write_errors.append("flag 写入失败：%s: %s（%s）"
                                % (type(e).__name__, e, flag_path))
    try:
        _atomic_write_text(state_path, state_to_json(state_after))
    except OSError as e:
        write_errors.append("state 写入失败：%s: %s（%s）"
                            % (type(e).__name__, e, state_path))
    if write_errors:
        rep["warnings"].extend(write_errors)
        rep["ok"] = False
        rep["error"] = "；".join(write_errors)
        rep["exit_code"] = 2
        return rep
    # 告警已成功写入（或无需告警/去重抑制）→ rc0；解析/执行错误仍 fail-visible rc2
    if parse_error or exec_error:
        rep["ok"] = False
        rep["error"] = _join_errors(rep)
        rep["exit_code"] = 2
    else:
        rep["ok"] = True
        rep["exit_code"] = 0
    return rep


def _join_errors(rep: dict) -> str | None:
    parts = [p for p in (rep["health"].get("exec_error"),
                         rep["health"].get("parse_error")) if p]
    return "；".join(parts) if parts else None


# ------------------------------------------------------------------- CLI 入口

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="黄忠健康告警联动（默认 dry-run；--apply 才写 flag/state）")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON")
    ap.add_argument("--dry-run", action="store_true",
                    help="只评估不写盘（默认行为）")
    ap.add_argument("--apply", action="store_true",
                    help="达到阈值且过去重窗口时写告警 flag 并更新 state")
    ap.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                    help="连续失败阈值（默认 2）")
    ap.add_argument("--dedupe-hours", type=float, default=DEFAULT_DEDUPE_HOURS,
                    help="告警去重窗口小时（默认 6）")
    ap.add_argument("--alerts-dir", default="",
                    help="告警 flag 目录（默认 env HUANGZHONG_ALERTS_DIR "
                         "或 C:\\aios\\jev-eval\\alerts）")
    ap.add_argument("--work-dir", default="",
                    help="状态/数据目录（默认 <repo-root>/work/huangzhong）")
    ap.add_argument("--repo-root", default="",
                    help="仓库根（默认工具所在仓库；worktree 内评估主仓时显式指定）")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help="health 子进程超时秒（默认 180）")
    args = ap.parse_args(argv)
    rep = run(apply=args.apply, dry_run_flag=args.dry_run,
              threshold=args.threshold, dedupe_hours=args.dedupe_hours,
              alerts_dir=args.alerts_dir or None,
              work_dir=args.work_dir or None,
              repo_root=args.repo_root or None,
              timeout=args.timeout)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        mode = "apply" if rep["apply"] else "dry-run"
        ev = rep.get("evaluation") or {}
        print("黄忠健康告警联动（%s）：exit=%s；health=%s（rc=%s）；"
              "consecutive=%s/%s；would_alert=%s"
              % (mode, rep.get("exit_code"), rep["health"].get("status"),
                 rep["health"].get("rc"), ev.get("consecutive_fail"),
                 ev.get("threshold"), ev.get("would_alert")))
        if rep.get("flag", {}).get("path"):
            print("flag（%s）：%s"
                  % ("已写" if rep["flag"].get("written") else "将写",
                     rep["flag"]["path"]))
        if rep.get("error"):
            print("错误：%s" % rep["error"])
        for w in rep.get("warnings") or []:
            print("  - warning: %s" % w)
    return int(rep.get("exit_code") or 0)


if __name__ == "__main__":
    raise SystemExit(main())
