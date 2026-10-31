# -*- coding: utf-8 -*-
"""黄忠运维 CLI（P2，2026-10-31）：B 飞书推送开关管理 + 事件统计/轮转。

可审计原则（本工具只做运维动作，不改业务代码/审批）：
  · 只写 work/huangzhong/（push_config.json、archive/）与 dev_events 审计；
  · 所有写操作原子（同目录临时文件 + os.replace）且写后回读校验；
  · 审计 code_action fail-open：审计失败绝不影响命令结果；
  · events-stats 只读；events-rotate 默认 dry-run，--apply 才落盘，
    幂等、坏行/无时间戳行原样保留（不归档、不删除）。

路径覆盖（测试/迁移）：
  · HUANGZHONG_WORK_DIR：事件/配置目录（默认 <repo>/work/huangzhong）
  · HUANGZHONG_REPO_ROOT：repo 根（默认本脚本所在仓根；主仓 worktree 共用）
B 开关语义与 tools/huangzhong_daily.py 对齐：env HUANGZHONG_EVENT_PUSH 优先
（1/0/true/false/on/off/yes/no），否则读 push_config.json 的 enabled，缺省关。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUSH_CONFIG_NAME = "push_config.json"
EVENTS_NAME = "events.jsonl"
ARCHIVE_DIR_NAME = "archive"
PUSH_ENV = "HUANGZHONG_EVENT_PUSH"
DEFAULT_OPERATOR = "DSH-Lead"
AUDIT_TASK = "HUANGZHONG-OPS"
DEFAULT_ARCHIVE_DAYS = 30.0
STATS_WINDOW_HOURS = 24.0
_TRUE = {"1", "true", "on", "yes", "y"}
_FALSE = {"0", "false", "off", "no", "n"}


# --------------------------------------------------------------- 路径与环境

def _repo_root() -> Path:
    """repo 根：env HUANGZHONG_REPO_ROOT 优先，否则本脚本所在仓根。"""
    raw = str(os.environ.get("HUANGZHONG_REPO_ROOT") or "").strip()
    return Path(raw) if raw else ROOT


def _work_dir() -> Path:
    """运维目录：env HUANGZHONG_WORK_DIR 优先，否则 <repo>/work/huangzhong。"""
    raw = str(os.environ.get("HUANGZHONG_WORK_DIR") or "").strip()
    return Path(raw) if raw else (_repo_root() / "work" / "huangzhong")


def _push_path() -> Path:
    return _work_dir() / PUSH_CONFIG_NAME


def _events_path() -> Path:
    return _work_dir() / EVENTS_NAME


def _archive_dir() -> Path:
    return _work_dir() / ARCHIVE_DIR_NAME


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _operator(override: str = "") -> str:
    """操作人：显式 override > env AGENT_NAME > env DEVTOOL_AGENT > DSH-Lead。"""
    for cand in (override, os.environ.get("AGENT_NAME"),
                 os.environ.get("DEVTOOL_AGENT")):
        val = str(cand or "").strip()
        if val:
            return val
    return DEFAULT_OPERATOR


def _parse_flag(raw) -> bool | None:
    """解析开关值；不可识别返回 None（不误判）。"""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return None


def _parse_ts(raw) -> dt.datetime | None:
    """解析事件 ts：ISO8601（含 Z/偏移）/ 空格格式 / 纯日期；失败返回 None。"""
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
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = dt.datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:  # 无时区按本机时区解释
        parsed = parsed.astimezone()
    return parsed


# ------------------------------------------------------------------ 原子写盘

def _atomic_write_text(path: Path, text: str) -> None:
    """同目录临时文件 + fsync + os.replace（原子替换）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".%s." % path.name, suffix=".tmp",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_write_json(path: Path, data: dict) -> None:
    _atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def _append_lines_atomic(path: Path, lines: list[str]) -> None:
    """归档文件追加：读旧内容+新行整体写临时文件后原子替换（不丢不重）。"""
    existing = ""
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
        if existing and not existing.endswith("\n"):
            existing += "\n"
    _atomic_write_text(path, existing + "".join(ln + "\n" for ln in lines))


def _replace_lines_atomic(path: Path, lines: list[str]) -> None:
    _atomic_write_text(path, "".join(ln + "\n" for ln in lines))


# ------------------------------------------------------------------ 事件审计

def _fallback_events_file() -> Path:
    """events.py 不可用时的兜底事件文件（worktree 下落到主仓，与 events.py 一致）。"""
    raw = str(os.environ.get("HUANGZHONG_REPO_ROOT") or "").strip()
    if raw:
        return Path(raw) / "portal" / "data" / "dev_events.jsonl"
    try:  # worktree 隔离开发时，事件落主仓文件（否则回流分裂）
        import subprocess
        r = subprocess.run(
            ["git", "-C", str(_repo_root()), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15)
        if r.returncode == 0 and r.stdout.strip():
            common = Path(r.stdout.strip())
            if not common.is_absolute():
                common = (_repo_root() / common).resolve()
            return common.parent / "portal" / "data" / "dev_events.jsonl"
    except Exception:  # noqa: BLE001 —— 定位失败退回本仓根
        pass
    return _repo_root() / "portal" / "data" / "dev_events.jsonl"


def _audit(detail: str, audit_fn=None) -> dict:
    """写 dev_events code_action 审计（fail-open：任何异常都不影响命令结果）。

    audit_fn 可注入（测试用 fake，绝不写真实事件文件）；缺省惰性 import
    tools/events.py 的 emit，失败再兜底直写同 schema 的 dev_events.jsonl。
    """
    detail = str(detail or "")[:300]
    try:
        fn = audit_fn
        if fn is None:
            tools = str(_repo_root() / "tools")
            if tools not in sys.path:
                sys.path.insert(0, tools)
            import events as _events  # noqa: WPS433 —— 惰性导入，避免拉起事件层
            fn = _events.emit
        out = fn(kind="code_action", task=AUDIT_TASK, agent="HUANGZHONG",
                 ide="huangzhong-ops", model="huangzhong-ops", detail=detail)
        return {"ok": True, "rec": out}
    except Exception:  # noqa: BLE001 —— 继续走兜底直写
        pass
    try:
        p = _fallback_events_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        now = _now()
        rec = {"ts": now.strftime("%Y-%m-%d %H:%M:%S"),
               "day": now.date().isoformat(), "kind": "code_action",
               "task": AUDIT_TASK, "agent": "HUANGZHONG",
               "ide": "huangzhong-ops", "model": "huangzhong-ops",
               "detail": detail}
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return {"ok": True, "fallback": str(p)}
    except Exception:  # noqa: BLE001 —— 审计永远 fail-open
        return {"ok": False}


# -------------------------------------------------------------- B 推送开关

def push_status() -> dict:
    """只读：输出 {enabled, source, path}；env 优先于配置文件；默认 off。"""
    path = _push_path()
    env_raw = os.environ.get(PUSH_ENV)
    env_val = _parse_flag(env_raw)
    config = None
    config_error = None
    if path.is_file():
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                config_error = "push_config.json 顶层不是对象"
                config = None
        except (OSError, ValueError) as e:
            config_error = "%s: %s" % (type(e).__name__, e)
    if env_val is not None:
        enabled, source = env_val, "env"
    elif config is not None and isinstance(config.get("enabled"), bool):
        enabled, source = config["enabled"], "config"
    else:
        enabled, source = False, "default"
    rep = {"ok": True, "enabled": enabled, "source": source, "path": str(path),
           "env": env_raw, "config": config,
           "updated_at": (config or {}).get("updated_at"),
           "by": (config or {}).get("by"),
           "default": False}
    if config_error:
        rep["config_error"] = config_error
    return rep


def push_set(enabled: bool, *, operator: str = "", audit_fn=None,
             now: dt.datetime | None = None) -> dict:
    """原子写 push_config.json（enabled/updated_at/by）→ 回读校验 → code_action 审计。"""
    enabled = bool(enabled)
    action = "push-on" if enabled else "push-off"
    now = now or _now()
    op = _operator(operator)
    path = _push_path()
    rec = {"enabled": enabled,
           "updated_at": now.isoformat(timespec="seconds"),
           "by": op}
    try:
        _atomic_write_json(path, rec)
    except OSError as e:
        return {"ok": False, "action": action, "enabled": enabled,
                "path": str(path), "error": "写入失败：%s: %s" % (type(e).__name__, e)}
    readback = None
    readback_ok = False
    readback_error = None
    try:
        readback = json.loads(path.read_text(encoding="utf-8"))
        readback_ok = (isinstance(readback, dict)
                       and isinstance(readback.get("enabled"), bool)
                       and readback.get("enabled") is enabled
                       and readback.get("by") == op)
    except (OSError, ValueError) as e:
        readback_error = "%s: %s" % (type(e).__name__, e)
    audit = _audit("%s enabled=%s by=%s path=%s readback=%s"
                   % (action, "true" if enabled else "false", op, path,
                      "ok" if readback_ok else "FAIL"),
                   audit_fn=audit_fn)
    rep = {"ok": readback_ok, "action": action, "enabled": enabled,
           "path": str(path), "config": readback, "readback_ok": readback_ok,
           "audit": audit}
    if readback_error:
        rep["error"] = "回读失败：%s" % readback_error
    elif not readback_ok:
        rep["error"] = "回读校验失败（配置未按预期落盘）"
    return rep


# ---------------------------------------------------------- 事件统计/轮转

def events_stats(*, window_hours: float = STATS_WINDOW_HOURS,
                 now: dt.datetime | None = None) -> dict:
    """只读统计 work/huangzhong/events.jsonl：总数/近 24h/verdict/最近 ts/坏行。"""
    now = now or _now()
    path = _events_path()
    cutoff = now - dt.timedelta(hours=float(window_hours))
    rep = {"ok": True, "path": str(path), "exists": path.is_file(),
           "window_hours": float(window_hours), "total": 0, "recent_24h": 0,
           "verdicts": {}, "latest_ts": None, "bad_lines": 0,
           "generated_at": now.isoformat(timespec="seconds")}
    if not path.exists():
        return rep
    if not path.is_file():  # 路径存在但不是普通文件 → fail-visible
        return {"ok": False, "path": str(path),
                "error": "events.jsonl 不是普通文件（%s）" % path}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as e:  # 含 UnicodeDecodeError（不可读 → fail-visible）
        return {"ok": False, "path": str(path),
                "error": "读取失败：%s: %s" % (type(e).__name__, e)}
    latest = None
    for line in text.splitlines():
        if not line.strip():
            continue
        rep["total"] += 1
        try:
            rec = json.loads(line)
        except ValueError:
            rep["bad_lines"] += 1
            continue
        if not isinstance(rec, dict):
            rep["bad_lines"] += 1
            continue
        verdict = rec.get("verdict")
        key = str(verdict) if verdict is not None else "unknown"
        rep["verdicts"][key] = rep["verdicts"].get(key, 0) + 1
        ts = _parse_ts(rec.get("ts"))
        if ts is None:
            rep["bad_lines"] += 1  # 无合法 ts 的记录无法计年龄 → 计入坏行
            continue
        if latest is None or ts > latest:
            latest = ts
        if ts >= cutoff:
            rep["recent_24h"] += 1
    rep["latest_ts"] = latest.isoformat(timespec="seconds") if latest else None
    return rep


def events_rotate(days: float = DEFAULT_ARCHIVE_DAYS, apply: bool = False,
                  *, now: dt.datetime | None = None) -> dict:
    """把超过 N 天的事件行移入 archive/events_<YYYYMMDD>.jsonl。

    默认 dry-run（只统计不写盘）；apply=True 才落盘：归档文件原子追加 +
    主文件原子替换 + 回读校验；无可归档行时幂等（不写盘）。
    坏行/无合法 ts 行**原样保留**在主文件，不归档不删除。
    """
    now = now or _now()
    path = _events_path()
    archive = _archive_dir() / ("events_%s.jsonl" % now.strftime("%Y%m%d"))
    cutoff = now - dt.timedelta(days=float(days))
    rep = {"ok": True, "dry_run": not apply, "applied": False,
           "path": str(path), "archive": str(archive), "days": float(days),
           "cutoff": cutoff.isoformat(timespec="seconds"),
           "exists": path.is_file(), "total": 0, "archive_count": 0,
           "keep_count": 0, "bad_lines": 0, "archive_written": False,
           "readback_ok": True,
           "generated_at": now.isoformat(timespec="seconds")}
    if not path.exists():
        rep["note"] = "events.jsonl 不存在，无需轮转"
        return rep
    if not path.is_file():  # 路径存在但不是普通文件 → fail-visible
        return {"ok": False, "path": str(path), "dry_run": not apply,
                "error": "events.jsonl 不是普通文件（%s）" % path}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as e:
        return {"ok": False, "path": str(path), "dry_run": not apply,
                "error": "读取失败：%s: %s" % (type(e).__name__, e)}
    archive_lines: list[str] = []
    keep_lines: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            keep_lines.append(line)  # 空行原样保留
            continue
        rep["total"] += 1
        rec = None
        try:
            rec = json.loads(line)
        except ValueError:
            rec = None
        ts = _parse_ts(rec.get("ts")) if isinstance(rec, dict) else None
        if ts is None:
            rep["bad_lines"] += 1
            keep_lines.append(line)  # 坏行/无 ts 行原样保留
            continue
        if ts < cutoff:
            archive_lines.append(line)
        else:
            keep_lines.append(line)
    rep.update(archive_count=len(archive_lines), keep_count=len(keep_lines))
    if not archive_lines:
        rep["note"] = "无需轮转（无超过 %g 天的可判年龄事件行）" % float(days)
        return rep
    if not apply:
        rep["note"] = "dry-run：未写盘；确认后加 --apply 执行"
        return rep
    try:
        _append_lines_atomic(archive, archive_lines)
        _replace_lines_atomic(path, keep_lines)
    except OSError as e:
        return {**rep, "ok": False,
                "error": "写盘失败：%s: %s" % (type(e).__name__, e)}
    # 回读校验：主文件行数=保留行数；归档末尾=本次归档行
    try:
        main_lines = path.read_text(encoding="utf-8").splitlines()
        arc_lines = archive.read_text(encoding="utf-8").splitlines()
        readback_ok = (len(main_lines) == len(keep_lines)
                       and len(arc_lines) >= len(archive_lines)
                       and arc_lines[-len(archive_lines):] == archive_lines)
    except (OSError, ValueError) as e:
        readback_ok = False
        rep["readback_error"] = "%s: %s" % (type(e).__name__, e)
    rep.update(applied=True, archive_written=True, readback_ok=readback_ok)
    if not readback_ok:
        rep["ok"] = False
        rep["error"] = "回读校验失败（归档/主文件未按预期落盘）"
    else:
        rep["note"] = "已轮转 %d 行 → %s" % (len(archive_lines), archive)
    return rep


# --------------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="黄忠运维 CLI（B 开关/事件统计/轮转）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="JSON 输出")

    sub.add_parser("push-status", parents=[common], help="查看 B 飞书推送开关（只读）")
    for name, desc in (("push-on", "打开 B 飞书推送开关"),
                       ("push-off", "关闭 B 飞书推送开关")):
        p = sub.add_parser(name, parents=[common], help=desc)
        p.add_argument("--operator", default="",
                       help="操作人（默认 env AGENT_NAME/DEVTOOL_AGENT 或 DSH-Lead）")

    p_stats = sub.add_parser("events-stats", parents=[common],
                             help="事件统计（只读）")
    p_stats.add_argument("--window-hours", type=float, default=STATS_WINDOW_HOURS,
                         help="近期窗口小时数（默认 24）")

    p_rot = sub.add_parser("events-rotate", parents=[common],
                           help="事件轮转（默认 dry-run）")
    p_rot.add_argument("--days", type=float, default=DEFAULT_ARCHIVE_DAYS,
                       help="保留天数（默认 30）：超过即归档")
    grp = p_rot.add_mutually_exclusive_group()
    grp.add_argument("--dry-run", action="store_true", help="只统计不写盘（默认）")
    grp.add_argument("--apply", action="store_true", help="执行轮转（写盘）")

    args = ap.parse_args(argv)
    if args.cmd == "push-status":
        rep = push_status()
    elif args.cmd in ("push-on", "push-off"):
        rep = push_set(args.cmd == "push-on", operator=args.operator)
    elif args.cmd == "events-stats":
        rep = events_stats(window_hours=args.window_hours)
    else:
        rep = events_rotate(days=args.days, apply=bool(args.apply))
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print(_human(rep))
    return 0 if rep.get("ok") else 1


def _human(rep: dict) -> str:
    """人类可读摘要（单行）。"""
    if not rep.get("ok"):
        return "失败：%s" % rep.get("error", "unknown")
    if "enabled" in rep and "source" in rep:
        cfg = " by=%s updated_at=%s" % (rep.get("by"), rep.get("updated_at")) \
            if rep.get("config") else ""
        return "B 推送：%s（来源：%s）%s → %s" % (
            "on" if rep["enabled"] else "off", rep["source"], cfg, rep.get("path"))
    if "verdicts" in rep and "recent_24h" in rep:
        return "事件：total=%s recent_%dh=%s bad=%s verdicts=%s latest=%s → %s" % (
            rep.get("total"), int(float(rep.get("window_hours", 24))),
            rep.get("recent_24h"), rep.get("bad_lines"),
            json.dumps(rep.get("verdicts"), ensure_ascii=False),
            rep.get("latest_ts"), rep.get("path"))
    if "archive_count" in rep:
        return "%s：total=%s archive=%s keep=%s bad=%s archive_file=%s" % (
            "dry-run" if rep.get("dry_run") else "已轮转",
            rep.get("total"), rep.get("archive_count"),
            rep.get("keep_count"), rep.get("bad_lines"), rep.get("archive"))
    return json.dumps(rep, ensure_ascii=False)


if __name__ == "__main__":
    raise SystemExit(main())
