# -*- coding: utf-8 -*-
"""知识库每日自动化编排器（daily_kb）：按序刷新学习卡/研报面板/入库状态（可选问答检索冒烟）。

编排顺序（可用 --steps 过滤，默认全部）：
  1. learning_digest run        → portal/static/kb_learning_digest.json （📚 学习卡 / 📰 今日简报）
  2. panel_daily run            → portal/static/report_panel.json       （研报候选面板）
     （panel_daily 自身还会追加 research/reports/report_panel_daily_status.json）
  3. kb_ingest_status           → portal/static/kb_ingest.json          （知识入库状态页）
  4. kb_qa ask --retrieval-only → 仅检索冒烟（--with-qa 时执行；不调 LLM，安全快速）

产物：
  research/reports/daily_kb_status.json
  {schema_version, generated_at, mode, ok, rc, steps(rc/耗时/输出 mtime), latest, portal_refresh, errors, warnings, disclaimer}

用法::

    python tools/kb_corpus/daily_kb.py run                 # 全量编排（默认）
    python tools/kb_corpus/daily_kb.py run --with-qa       # 额外跑一次问答检索冒烟
    python tools/kb_corpus/daily_kb.py --check-only        # 轻量校验现有状态 JSON（不执行子脚本）
    python tools/kb_corpus/daily_kb.py check --json

失败隔离：单步失败只记录到 errors/steps，不中断后续步骤；最终 rc 非 0（有错误时）。
本编排只刷新研究/学习材料，不构成投资建议；子脚本是否写盘由各自负责。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
STATUS_PATH = REPO / "research" / "reports" / "daily_kb_status.json"

SCHEMA_VERSION = 1
DEFAULT_STEP_TIMEOUT = 900  # 单步上限 15 分钟（面板/问答最坏情况兜底）
DISCLAIMER = (
    "本编排仅刷新研究/学习材料（学习卡、研报面板、入库状态、问答检索冒烟），"
    "全部为内部研究输出，不构成投资建议；单步失败已隔离记录，不影响后续步骤。"
)

# QA 检索冒烟默认问题（取自 kb_qa 内置示范问题，保证知识库可检索命中）
DEFAULT_QA_QUESTION = "M9 级覆铜板对 AI 服务器 PCB 的价值量有什么影响？"

DOCS = "knowledge_docs"
STATIC = REPO / "portal" / "static"


def _script(*parts: str) -> Path:
    return REPO.joinpath(*parts)


# ---------------------------------------------------------------------------
# 步骤定义
# ---------------------------------------------------------------------------
def build_steps(with_qa: bool = False) -> list[dict]:
    """返回编排步骤（每步：name/script/args/outputs/optional 说明）。"""
    steps = [
        {
            "name": "learning_digest",
            "script": _script("tools", "kb_corpus", "learning_digest.py"),
            "args": ["run"],
            "outputs": [STATIC / "kb_learning_digest.json"],
            "desc": "学习卡/简报/策略结论 → 每日学习摘要",
        },
        {
            "name": "panel_daily",
            "script": _script("tools", "research", "panel_daily.py"),
            "args": ["run"],
            "outputs": [STATIC / "report_panel.json"],
            "desc": "研报面板刷新门户静态 JSON 并记录状态",
        },
        {
            "name": "kb_ingest_status",
            "script": _script("tools", "kb_corpus", "kb_ingest_status.py"),
            "args": [],
            "outputs": [STATIC / "kb_ingest.json"],
            "desc": "知识入库状态 → 门户静态 JSON",
        },
    ]
    if with_qa:
        steps.append(
            {
                "name": "kb_qa_retrieval",
                "script": _script("tools", "kb_corpus", "kb_qa.py"),
                "args": ["ask", DEFAULT_QA_QUESTION, "--retrieval-only", "--json"],
                "outputs": [],
                "desc": "问答检索冒烟（--retrieval-only，不调用 LLM）",
            }
        )
    return steps


def select_steps(steps: list[dict], names: list[str] | None = None) -> list[dict]:
    """按 --steps 过滤（保持定义顺序）；未知名称抛 ValueError。"""
    if not names:
        return list(steps)
    unknown = [n for n in names if n not in {s["name"] for s in steps}]
    if unknown:
        raise ValueError(f"未知步骤: {unknown}（可选: {[s['name'] for s in steps]}）")
    wanted = set(names)
    return [s for s in steps if s["name"] in wanted]


# ---------------------------------------------------------------------------
# 文件/输出快照
# ---------------------------------------------------------------------------
def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def describe_file(path: str | Path) -> dict:
    """文件快照：exists/mtime/mtime_epoch/size。"""
    p = Path(path)
    info = {"path": str(p), "exists": False, "mtime": None, "mtime_epoch": None, "size": None}
    try:
        if p.is_file():
            st = p.stat()
            info.update(
                exists=True,
                mtime=_iso(st.st_mtime),
                mtime_epoch=round(st.st_mtime, 3),
                size=int(st.st_size),
            )
    except OSError:
        pass
    return info


def _tail(text: str, limit: int = 1200) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return "…" + text[-limit:]


# ---------------------------------------------------------------------------
# 单步执行（runner 可注入，供测试 mock）
# ---------------------------------------------------------------------------
def run_step(step: dict, runner=None, timeout: float = DEFAULT_STEP_TIMEOUT) -> dict:
    """执行单个步骤；任何异常都收敛为记录（不抛出，保证失败隔离）。"""
    command = [PYTHON, "-X", "utf8", str(step["script"]), *step["args"]]
    started = time.monotonic()
    rc, stdout, stderr, error = 0, "", "", None
    try:
        runner_fn = runner or subprocess.run
        proc = runner_fn(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(REPO),
        )
        rc = int(getattr(proc, "returncode", 0))
        stdout = getattr(proc, "stdout", "") or ""
        stderr = getattr(proc, "stderr", "") or ""
    except subprocess.TimeoutExpired as exc:
        rc, error = 124, f"步骤超时（>{timeout}s）: {exc}"
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
    except FileNotFoundError as exc:
        rc, error = 127, f"脚本/解释器不存在: {exc}"
    except Exception as exc:  # noqa: BLE001 - 编排边界需兜住任意子进程异常
        rc, error = -1, f"步骤执行异常: {type(exc).__name__}: {exc}"
    elapsed = round(time.monotonic() - started, 3)
    outputs = [describe_file(p) for p in step.get("outputs") or []]
    ok = rc == 0
    if not ok and not error:
        error = f"rc={rc}（stderr: {_tail(stderr, 300) or '—'}）"
    return {
        "name": step["name"],
        "desc": step.get("desc", ""),
        "script": str(step["script"]),
        "args": list(step.get("args") or []),
        "command": command,
        "rc": rc,
        "ok": ok,
        "elapsed_seconds": elapsed,
        "started_at": _iso(time.time() - elapsed),
        "outputs": outputs,
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
        "error": error,
    }


# ---------------------------------------------------------------------------
# latest / 门户刷新清单
# ---------------------------------------------------------------------------
def build_latest(steps: list[dict]) -> dict:
    """各产物最新快照（含步骤输出 + 面板状态台账 + 问答样本兜底）。"""
    latest: dict[str, dict] = {}
    for step in steps:
        for out in step.get("outputs") or []:
            latest[step["name"]] = describe_file(out)
    latest["panel_daily_status"] = describe_file(REPO / "research" / "reports" / "report_panel_daily_status.json")
    latest["kb_qa_samples"] = describe_file(STATIC / "kb_qa_samples.json")
    return latest


PORTAL_REFRESH = [
    {
        "path": "portal/static/kb_learning_digest.json",
        "producer": "learning_digest",
        "page": "/kb_reports.html",
        "section": "📚 学习卡（每日 5 张）/ 📰 今日简报",
    },
    {
        "path": "portal/static/report_panel.json",
        "producer": "panel_daily",
        "page": "/selection.html",
        "section": "研报候选（多智能体）",
    },
    {
        "path": "portal/static/kb_ingest.json",
        "producer": "kb_ingest_status",
        "page": "/kb_ingest.html",
        "section": "知识入库状态",
    },
]


def build_portal_refresh() -> list[dict]:
    """门户刷新清单：哪些 static JSON 需要写盘、被哪个页面消费、当前快照。"""
    items = []
    for item in PORTAL_REFRESH:
        snapshot = describe_file(REPO / item["path"])
        items.append({**item, "exists": snapshot["exists"], "mtime": snapshot["mtime"], "size": snapshot["size"]})
    # 问答样本：当前无页面消费，列为「备用/手动」
    qa = describe_file(STATIC / "kb_qa_samples.json")
    items.append(
        {
            "path": "portal/static/kb_qa_samples.json",
            "producer": "kb_qa samples（手动/可选）",
            "page": "（暂无页面消费，备用）",
            "section": "问答示范样本",
            "exists": qa["exists"],
            "mtime": qa["mtime"],
            "size": qa["size"],
        }
    )
    return items


# ---------------------------------------------------------------------------
# 编排主流程
# ---------------------------------------------------------------------------
def run_all(
    *,
    with_qa: bool = False,
    step_names: list[str] | None = None,
    status_path: str | Path = STATUS_PATH,
    runner=None,
    timeout: float = DEFAULT_STEP_TIMEOUT,
    write: bool = True,
) -> dict:
    """按序执行步骤（失败隔离），组装并写出 daily_kb_status.json。"""
    all_steps = build_steps(with_qa=with_qa)
    steps = select_steps(all_steps, step_names)
    started = time.monotonic()
    records: list[dict] = []
    errors: list[str] = []
    warnings: list[str] = []
    for step in steps:
        if not Path(step["script"]).is_file():
            warnings.append(f"步骤脚本不存在（仍会记录失败）: {step['script']}")
        record = run_step(step, runner=runner, timeout=timeout)
        records.append(record)
        if not record["ok"]:
            errors.append(f"{record['name']}: rc={record['rc']} {record.get('error') or ''}".strip())
    elapsed = round(time.monotonic() - started, 3)
    status = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "run",
        "ok": not errors,
        "rc": 0 if not errors else 1,
        "with_qa": bool(with_qa),
        "steps_requested": [s["name"] for s in steps],
        "duration_seconds": elapsed,
        "steps": records,
        "latest": build_latest(steps),
        "portal_refresh": build_portal_refresh(),
        "errors": errors,
        "warnings": warnings,
        "disclaimer": DISCLAIMER,
    }
    status["_status_path"] = str(status_path)
    if write:
        out = Path(status_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(status, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        status["_status_written"] = True
    return status


# ---------------------------------------------------------------------------
# --check-only：轻量校验现有状态 JSON
# ---------------------------------------------------------------------------
REQUIRED_TOP = ("schema_version", "generated_at", "mode", "ok", "steps", "latest", "errors", "disclaimer")
REQUIRED_STEP = ("name", "rc", "ok", "elapsed_seconds", "outputs")


def validate_status(payload) -> list[str]:
    """校验 daily_kb_status.json 的 schema/免责声明/输出结构，返回错误列表。"""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return [f"顶层必须是 object，实际 {type(payload).__name__}"]
    for key in REQUIRED_TOP:
        if key not in payload:
            errors.append(f"缺少顶层字段 '{key}'")
    if not isinstance(payload.get("schema_version"), int):
        errors.append("schema_version 必须是整数")
    generated_at = payload.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at.strip():
        errors.append("generated_at 必须是非空字符串")
    else:
        try:
            datetime.fromisoformat(generated_at)
        except ValueError:
            errors.append(f"generated_at 不是 ISO 时间: {generated_at!r}")
    if payload.get("mode") not in ("run", "check_only"):
        errors.append(f"mode 必须是 run/check_only，实际 {payload.get('mode')!r}")
    if not isinstance(payload.get("ok"), bool):
        errors.append("ok 必须是布尔值")
    if not isinstance(payload.get("errors"), list):
        errors.append("errors 必须是数组")

    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("steps 必须是非空数组")
    else:
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                errors.append(f"steps[{i}] 必须是 object")
                continue
            for key in REQUIRED_STEP:
                if key not in step:
                    errors.append(f"steps[{i}] 缺少字段 '{key}'")
            if not isinstance(step.get("rc"), int):
                errors.append(f"steps[{i}].rc 必须是整数")
            if not isinstance(step.get("elapsed_seconds"), (int, float)):
                errors.append(f"steps[{i}].elapsed_seconds 必须是数值")
            outputs = step.get("outputs")
            if not isinstance(outputs, list):
                errors.append(f"steps[{i}].outputs 必须是数组")
            else:
                for j, out in enumerate(outputs):
                    if not isinstance(out, dict):
                        errors.append(f"steps[{i}].outputs[{j}] 必须是 object")
                        continue
                    for key in ("path", "exists"):
                        if key not in out:
                            errors.append(f"steps[{i}].outputs[{j}] 缺少字段 '{key}'")
                    if "mtime" not in out:
                        errors.append(f"steps[{i}].outputs[{j}] 缺少输出 mtime")

    latest = payload.get("latest")
    if not isinstance(latest, dict) or not latest:
        errors.append("latest 必须是非空 object")

    disclaimer = payload.get("disclaimer")
    if not isinstance(disclaimer, str) or not disclaimer.strip():
        errors.append("disclaimer 必须是非空字符串")
    elif "不构成投资建议" not in disclaimer:
        errors.append("disclaimer 必须包含「不构成投资建议」免责声明")
    return errors


def check_only(status_path: str | Path = STATUS_PATH) -> dict:
    """轻量校验：读现有状态 JSON（不跑子脚本），校验 schema/免责声明/产物存在性。"""
    path = Path(status_path)
    errors: list[str] = []
    warnings: list[str] = []
    payload = None
    if not path.is_file():
        errors.append(f"状态文件不存在: {path}（先运行 run 生成）")
    else:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"状态文件解析失败: {exc}")
    if isinstance(payload, dict):
        errors.extend(validate_status(payload))
        # 产物存在性（轻量，仅告警）
        for step in payload.get("steps") or []:
            if not isinstance(step, dict):
                continue
            for out in step.get("outputs") or []:
                if isinstance(out, dict) and not out.get("exists"):
                    warnings.append(f"步骤 {step.get('name')} 输出缺失: {out.get('path')}")
    return {
        "ok": not errors,
        "mode": "check_only",
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status_path": str(path),
        "errors": errors,
        "warnings": warnings,
        "steps_checked": len((payload or {}).get("steps") or []) if isinstance(payload, dict) else 0,
        "generated_at": (payload or {}).get("generated_at") if isinstance(payload, dict) else None,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="知识库每日自动化编排器（learning_digest→panel_daily→kb_ingest_status[→kb_qa]）")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "check"], help="run=编排（默认），check=校验现有状态")
    parser.add_argument("--check-only", action="store_true", help="等价于 check：只做轻量校验，不执行子脚本")
    parser.add_argument("--with-qa", action="store_true", help="附加问答检索冒烟（kb_qa ask --retrieval-only）")
    parser.add_argument("--steps", default=None, help="只跑指定步骤（逗号分隔），如 learning_digest,kb_ingest_status")
    parser.add_argument("--status", default=str(STATUS_PATH), help="状态 JSON 路径")
    parser.add_argument("--timeout", type=float, default=DEFAULT_STEP_TIMEOUT, help="单步超时秒数")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    return parser


def _print_run_summary(status: dict) -> None:
    print(f"每日知识库编排完成：ok={status['ok']} rc={status['rc']} 用时 {status['duration_seconds']}s")
    for step in status["steps"]:
        flag = "✅" if step["ok"] else "❌"
        outs = ", ".join(f"{Path(o['path']).name}({o['size'] if o['exists'] else '缺失'})" for o in step["outputs"]) or "（无文件输出）"
        print(f"  {flag} {step['name']}: rc={step['rc']} {step['elapsed_seconds']}s | {outs}")
    print(f"状态台账：{status.get('_status_path')}")
    print("门户刷新清单：")
    for item in status["portal_refresh"]:
        print(f"  - {item['path']} → {item['page']}（{item['section']}）exists={item['exists']}")
    if status["errors"]:
        print("错误：", file=sys.stderr)
        for err in status["errors"]:
            print(f"  - {err}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    check_mode = args.check_only or args.command == "check"
    if check_mode:
        report = check_only(args.status)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif report["ok"]:
            print(f"校验通过：{report['status_path']}（steps={report['steps_checked']}，generated_at={report['generated_at']}）")
            for warning in report["warnings"]:
                print(f"警告：{warning}", file=sys.stderr)
        else:
            print(f"校验失败：{len(report['errors'])} 个错误（{report['status_path']}）", file=sys.stderr)
            for err in report["errors"]:
                print(f"  - {err}", file=sys.stderr)
        return 0 if report["ok"] else 1

    step_names = [s.strip() for s in (args.steps or "").split(",") if s.strip()] or None
    try:
        status = run_all(
            with_qa=args.with_qa,
            step_names=step_names,
            status_path=args.status,
            timeout=args.timeout,
        )
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        _print_run_summary(status)
    return status["rc"]


if __name__ == "__main__":
    sys.exit(main())
