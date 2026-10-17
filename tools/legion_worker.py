# -*- coding: utf-8 -*-
"""数字军团 Agent Worker（K-ORG-123）。

只执行显式标记 ``legion_worker=true`` 的工单，避免把传统 devtask 产码单
误交给模型。闭环：queued -> claimed -> doing -> review/done 或 failed。
每个状态变化都写入 tasks.json 和 observe 心跳账本；产出统一落到
agent_platform/data/worker_outputs/<tid>.md。

用法：
  python tools/legion_worker.py --once --dry-run
  python tools/legion_worker.py --once
  python tools/legion_worker.py --loop --interval 30
"""
import argparse
import json
import os
import re
import sys
import time
import uuid
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / ".worktrees" / "tasks.json"
OUT = ROOT / "agent_platform" / "data" / "worker_outputs"
HR = ROOT / "agent_platform" / "hr"
DEFAULT_LEASE_SECONDS = 15 * 60
# 保持旧任务的单次执行语义；任务显式设置 max_attempts 后才启用自动重试。
DEFAULT_MAX_ATTEMPTS = 1
RETRYABLE_FAILURE_CODES = {"model_error", "empty_output", "output_write_error", "acceptance_failed"}
TERMINAL_EXECUTION_STATUSES = {"done", "blocked", "cancelled"}
sys.path.insert(0, str(ROOT / "tools"))
from legion_pulse import heartbeat  # noqa: E402


def load_tasks():
    try:
        data = json.loads(TASKS.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_tasks(tasks):
    TASKS.parent.mkdir(parents=True, exist_ok=True)
    tmp = TASKS.with_suffix(".worker.tmp")
    tmp.write_text(json.dumps(tasks, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, TASKS)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _parse_time(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _lease_expired(task, now=None):
    lease_until = _parse_time(task.get("lease_until"))
    if lease_until is None:
        return False
    return lease_until <= (now or datetime.now())


def _lease_until(now=None, seconds=DEFAULT_LEASE_SECONDS):
    current = now or datetime.now()
    return (current + timedelta(seconds=max(1, int(seconds)))).strftime("%Y-%m-%dT%H:%M:%S")


def _trace_id(task, tid):
    return str(task.get("trace_id") or ("worker-%s-%s" % (tid, uuid.uuid4().hex[:12])))


def _failure(exc):
    if isinstance(exc, TimeoutError):
        code = "timeout"
    elif "空产出" in str(exc):
        code = "empty_output"
    elif isinstance(exc, OSError):
        code = "output_write_error"
    elif isinstance(exc, (ImportError, KeyError)):
        code = "worker_configuration"
    else:
        code = "model_error"
    return code, str(exc)[:300]


def _failure_record(code, detail, now=None):
    return {"failure_code": str(code), "failure_detail": str(detail)[:300],
            "failed_at": now or _now()}


def _max_attempts(task):
    try:
        return max(1, int(task.get("max_attempts", DEFAULT_MAX_ATTEMPTS)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_ATTEMPTS


def _attempts(task):
    try:
        return max(0, int(task.get("execution_attempts", task.get("attempt", 0)) or 0))
    except (TypeError, ValueError):
        return 0


def _retry_status(task, failure_code):
    if failure_code in RETRYABLE_FAILURE_CODES and _attempts(task) < _max_attempts(task):
        return "retry"
    return "failed"


def _output_ref(path):
    file_path = Path(path)
    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
    return {"path": str(file_path), "sha256": digest, "size": file_path.stat().st_size}


def _hr_context(agent):
    """只读取岗位档案中的必要字段，不把整份 HR 档案注入模型。"""
    f = HR / agent / "resume.yaml"
    if not f.exists():
        return "（无 HR 档案）"
    text = f.read_text(encoding="utf-8", errors="replace")
    wanted = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("name:", "cn_name:", "role_profile:", "grade:", "skills:", "charter:")):
            wanted.append(s[:500])
    return "\n".join(wanted)[:2500] or "（HR 档案未登记摘要字段）"


def _prompt(tid, task):
    agent = str(task.get("agent") or task.get("owner") or "").strip().upper()
    acceptance = task.get("acceptance") or ["产出文件存在且非空", "结论引用输入事实", "列出限制和后续动作"]
    return (
        "你是数字军团成员 %s。你正在执行工单 %s。\n"
        "岗位档案摘要：\n%s\n\n"
        "工单要求：\n%s\n\n"
        "请只输出可交付的中文工作产出，必须包含：\n"
        "1. 执行结论；2. 证据或依据；3. 风险/限制；4. 后续动作。\n"
        "验收标准：\n- %s\n"
        "不要声称调用了不存在的数据或接口，不要编造已完成的外部动作。"
    ) % (agent, tid, _hr_context(agent), task.get("desc", ""), "\n- ".join(str(x) for x in acceptance))


def validate_output(task, result):
    """最小自动验收：结构、文件、内容和声明式标准均可重复判断。"""
    if not result.get("output"):
        return False, ["缺少产出路径"]
    path = Path(result["output"])
    if not path.is_file():
        return False, ["产出文件不存在"]
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    findings = []
    if len(text) < 120:
        findings.append("产出少于120字符")
    required = ("执行结论", "证据", "风险", "后续")
    for word in required:
        if word not in text:
            findings.append("缺少结构段: %s" % word)
    for criterion in task.get("acceptance") or []:
        c = str(criterion).strip()
        if not c:
            continue
        aliases = {
            "产出文件存在且非空": lambda: bool(text),
            "执行过程有 heartbeat 记录": lambda: True,
            "结论引用输入事实": lambda: any(x in text for x in ("依据", "数据", "事实", "证据")),
            "列出限制和后续动作": lambda: (("限制" in text or "风险" in text) and "后续" in text),
        }
        check = aliases.get(c)
        if check and not check():
            findings.append("验收未通过: %s" % c)
    return not findings, findings


def _claim_one(tasks, now=None, lease_seconds=DEFAULT_LEASE_SECONDS):
    now = now or datetime.now()
    now_text = now.strftime("%Y-%m-%dT%H:%M:%S")
    for tid, task in sorted(tasks.items()):
        if not isinstance(task, dict) or not task.get("legion_worker"):
            continue
        status = task.get("execution_status", "queued")
        if task.get("status") in ("blocked", "cancelled") or status in ("blocked", "cancelled", "done"):
            continue
        if status in ("claimed", "doing") and _lease_expired(task, now):
            status = "retry"
            task["execution_status"] = status
            task["recovery_reason"] = "lease_expired"
        if status not in ("queued", "retry"):
            continue
        next_retry = _parse_time(task.get("next_retry_at"))
        if next_retry and next_retry > now:
            continue
        attempts = _attempts(task) + 1
        token = uuid.uuid4().hex
        lease = {"token": token, "claimed_at": now_text,
                 "lease_until": _lease_until(now, lease_seconds)}
        task["execution_status"] = "claimed"
        task["execution_attempts"] = attempts
        task["attempt"] = attempts
        task["claim_lease"] = lease
        task["claim_token"] = token
        task["claimed_at"] = now_text
        task["lease_until"] = lease["lease_until"]
        task["trace_id"] = _trace_id(task, tid)
        task["execution_started"] = now_text
        task["updated"] = now_text
        return tid, task
    return None, None


def recover_task(task, execution_status="retry", detail=""):
    """返回可人工恢复的副本，不直接写盘或触发执行。"""
    if execution_status not in ("retry", "blocked", "cancelled", "queued"):
        raise ValueError("不支持的恢复状态: %s" % execution_status)
    recovered = dict(task)
    recovered["execution_status"] = execution_status
    if execution_status in ("retry", "queued"):
        recovered.pop("claim_lease", None)
        recovered.pop("claim_token", None)
        recovered.pop("claimed_at", None)
        recovered.pop("lease_until", None)
        recovered["next_retry_at"] = _now()
    if detail:
        recovered["recovery_detail"] = str(detail)[:300]
    recovered["updated"] = _now()
    return recovered


def execute_one(tid, task, dry_run=False):
    agent = str(task.get("agent") or task.get("owner") or "A0A").strip().upper()
    heartbeat(agent, "worker_claimed", "%s 已接单" % tid, {"tid": tid})
    if dry_run:
        return {"ok": True, "dry_run": True, "agent": agent, "prompt_chars": len(_prompt(tid, task))}
    import agent_coder
    meta = agent_coder.chat_auto_meta(agent, {"label": "worker:%s" % tid, "prompt": _prompt(tid, task)})
    content = (meta.content or "").strip()
    if not content:
        raise RuntimeError("模型返回空产出")
    OUT.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", tid)
    path = OUT / (safe + ".md")
    path.write_text(
        "# 工单产出：%s\n\n- 执行成员：%s\n- 实际通道：%s\n- 生成时间：%s\n\n%s\n"
        % (tid, agent, meta.channel, _now(), content), encoding="utf-8")
    return {"ok": True, "agent": agent, "output": str(path), "output_ref": _output_ref(path),
            "channel": meta.channel, "chars": len(content), "trace_id": task.get("trace_id")}


def run_once(dry_run=False):
    tasks = load_tasks()
    tid, task = _claim_one(tasks)
    if not tid:
        return {"ok": True, "claimed": False, "message": "没有待执行的 Legion Worker 工单"}
    save_tasks(tasks)
    try:
        result = execute_one(tid, task, dry_run=dry_run)
        tasks = load_tasks()
        current = tasks.get(tid, task)
        if dry_run:
            current["execution_status"] = "queued"
            current.pop("claim_lease", None)
            current.pop("claim_token", None)
            current.pop("claimed_at", None)
            current.pop("lease_until", None)
        else:
            passed, findings = validate_output(current, result)
            current["output"] = result.get("output")
            current["output_ref"] = result.get("output_ref")
            current["output_meta"] = {k: v for k, v in result.items() if k not in ("output", "output_ref")}
            current["acceptance_result"] = {"passed": passed, "findings": findings, "checked_at": _now()}
            if passed:
                current["execution_status"] = "done"
                current["status"] = "done"
            else:
                current["execution_status"] = "review"
                current["status"] = "review"
                current.update(_failure_record("acceptance_failed", "; ".join(findings)))
            current["execution_finished"] = _now()
        current["updated"] = _now()
        tasks[tid] = current
        save_tasks(tasks)
        heartbeat(current.get("agent", "A0A"), "worker_finished", "%s 产出完成" % tid,
                   {"tid": tid, "trace_id": current.get("trace_id"),
                    "execution_status": current["execution_status"],
                    "accepted": current.get("acceptance_result", {}).get("passed")})
        return {"ok": True, "tid": tid, "result": result, "status": current["execution_status"]}
    except Exception as exc:
        tasks = load_tasks()
        current = tasks.get(tid, task)
        code, detail = _failure(exc)
        status = _retry_status(current, code)
        current.update(_failure_record(code, detail))
        current["execution_error"] = "%s: %s" % (type(exc).__name__, detail)
        current["execution_status"] = status
        current["status"] = "review" if status == "failed" else current.get("status", "doing")
        current["execution_finished"] = _now()
        current["updated"] = _now()
        current.pop("claim_lease", None)
        current.pop("claim_token", None)
        current.pop("claimed_at", None)
        current.pop("lease_until", None)
        if status == "retry":
            current["next_retry_at"] = _now()
        tasks[tid] = current
        save_tasks(tasks)
        heartbeat(current.get("agent", "A0A"), "worker_failed", "%s 执行失败" % tid,
                   {"tid": tid, "trace_id": current.get("trace_id"),
                    "failure_code": code, "error": current["execution_error"],
                    "execution_status": status})
        return {"ok": False, "tid": tid, "error": current["execution_error"],
                "failure_code": code, "status": status}


def main():
    ap = argparse.ArgumentParser(description="数字军团 Agent Worker")
    ap.add_argument("--once", action="store_true", help="只执行一单")
    ap.add_argument("--loop", action="store_true", help="持续轮询")
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true", help="只验证接单和提示词，不调用模型")
    args = ap.parse_args()
    if not args.once and not args.loop:
        args.once = True
    while True:
        print(json.dumps(run_once(args.dry_run), ensure_ascii=False))
        if not args.loop:
            return 0
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
