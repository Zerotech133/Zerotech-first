# -*- coding: utf-8 -*-
"""研发派工路由审计（只读工具，WO-DRILL1-ROUTEAUDIT）。

只读两个来源，全程不写任何文件：
  * ``.worktrees/tasks.json``：工单账本；文件缺失视为 ``{}``，JSON 尾逗号
    自动修复（仅在内存里，不改原文件）；
  * ``agent_platform/config/dev_routing_rules.json``：研发席路由规则，取
    ``dev_seats``。

审计口径（只审 ``dept == "dev"`` 的工单，非 dev 单完全不进统计）：
  1. ``owner`` 不在 ``dev_seats``（大小写不敏感）→ 记「owner 不在 dev_seats」；
  2. ``acceptance_cmd`` 为空（缺失/空串/纯空白）→ 记「缺验收命令」。

配置缺失/坏 JSON 时 ``dev_seats`` 为空，此时无法判定 owner，**跳过 owner
校验、不误报**（验收命令仍照常检查）——与 devtask 试点门的 fail-safe 口径一致。

输出：
  * ``--json``：``{schema, checked, violations: [{tid, owner, reasons}], ok}``；
  * ``--md``：Markdown 表；
  * 两者都不给时默认等价 ``--json``（机读报告）。

退出码（报告与门禁分离，便于先观察后收紧）：
  * ``0`` —— 默认报告模式：报告生成成功即返回 0，**即使存在违规**；
  * ``1`` —— 加 ``--strict`` 且存在违规（供 CI/巡检当门禁用）。

用法：
  python -X utf8 tools/dev_route_audit.py --json
  python -X utf8 tools/dev_route_audit.py --md --strict
  python -X utf8 tools/dev_route_audit.py --tasks <tasks.json> --routing <routing.json>

边界：只用标准库、无网络；worktree 内运行时按 ``.git`` 文件自动锚定主仓根
（``.worktrees/tasks.json`` 只存在于主工作树，与 ``tools/_repo_paths.py`` 同口径）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
import logging

SCHEMA = "dev_route_audit/v1"
REASON_OWNER = "owner 不在 dev_seats"
REASON_ACCEPTANCE = "缺验收命令"

# 审计目标部门（工单口径固定为 dev；路由配置里的 dev_dept 仅作说明字段）。
TARGET_DEPT = "dev"


def _main_root() -> Path:
    """返回主工作树根：普通 checkout 返回自身；linked worktree 按 ``.git`` 回落主仓。"""
    root = Path(__file__).resolve().parents[1]
    marker = root / ".git"
    try:
        if marker.is_file():
            text = marker.read_text(encoding="utf-8", errors="replace").strip()
            if text.lower().startswith("gitdir:"):
                gitdir = Path(text.split(":", 1)[1].strip())
                if not gitdir.is_absolute():
                    gitdir = (root / gitdir).resolve()
                # <主仓>/.git/worktrees/<name> → parents[2] 即 <主仓> 根
                return gitdir.parents[2]
    except (OSError, IndexError, ValueError) as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/dev_route_audit.py", exc)
    return root


ROOT = _main_root()
DEFAULT_TASKS = ROOT / ".worktrees" / "tasks.json"
DEFAULT_ROUTING = ROOT / "agent_platform" / "config" / "dev_routing_rules.json"


def _strip_trailing_commas(text: str) -> str:
    """删除字符串字面量之外的尾逗号：``{"a": 1,}`` → ``{"a": 1}``。

    逐字符扫描并跟踪字符串/转义状态，因此 ``"desc": "x, }"`` 里的逗号不会
    被误删（正则 ``re.sub`` 会破坏这类内容）。
    """
    out = []
    in_str = False
    escaped = False
    i = 0
    size = len(text)
    while i < size:
        ch = text[i]
        if in_str:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < size and text[j] in " \t\r\n":
                j += 1
            if j < size and text[j] in "}]":
                i += 1  # 丢弃尾逗号
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _load_json(path, default):
    """读 JSON（utf-8-sig 容忍 BOM）；缺失/坏 JSON 返回 default，坏 JSON 告警到 stderr。"""
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return default
    except OSError as exc:
        print("warn: 无法读取 {}: {}".format(p, exc), file=sys.stderr)
        return default
    try:
        return json.loads(raw)
    except ValueError:
        try:
            return json.loads(_strip_trailing_commas(raw))
        except ValueError as exc:
            print("warn: JSON 解析失败 {}: {}".format(p, exc), file=sys.stderr)
            return default


def load_tasks(path=DEFAULT_TASKS):
    """读工单账本；缺失/不可读/非 dict 一律返回 ``{}``，尾逗号自愈。"""
    data = _load_json(path, {})
    return data if isinstance(data, dict) else {}


def load_dev_seats(path=DEFAULT_ROUTING):
    """读路由规则里的 ``dev_seats``，返回归一化（去空白/大写）的字符串集合。"""
    data = _load_json(path, {})
    raw = data.get("dev_seats") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return set()
    return {
        str(item).strip().upper()
        for item in raw
        if item is not None and str(item).strip()
    }


def audit(tasks, dev_seats, dept=TARGET_DEPT):
    """纯计算：对 ``dept`` 工单做 owner 路由与验收命令审计，返回报告 dict。

    ``tasks`` 为 ``{tid: task_dict}``；``dev_seats`` 为席位集合/列表。
    ``dev_seats`` 为空时跳过 owner 校验（配置不可判定 → 不误报）。
    """
    seats = {
        str(item).strip().upper()
        for item in (dev_seats or [])
        if item is not None and str(item).strip()
    }
    target = str(dept or "").strip().lower()
    checked = 0
    violations = []
    for tid in sorted(tasks or {}):
        info = tasks[tid]
        if not isinstance(info, dict):
            continue
        if str(info.get("dept") or "").strip().lower() != target:
            continue
        checked += 1
        owner = info.get("owner")
        owner_text = str(owner).strip() if owner is not None else ""
        reasons = []
        if seats and (not owner_text or owner_text.upper() not in seats):
            reasons.append(REASON_OWNER)
        acceptance = info.get("acceptance_cmd")
        if not (isinstance(acceptance, str) and acceptance.strip()):
            reasons.append(REASON_ACCEPTANCE)
        if reasons:
            violations.append(
                {"tid": tid, "owner": owner_text, "reasons": reasons}
            )
    return {
        "schema": SCHEMA,
        "checked": checked,
        "violations": violations,
        "ok": not violations,
    }


def run(tasks_path=DEFAULT_TASKS, routing_path=DEFAULT_ROUTING):
    """读两个只读来源并返回审计报告（不写盘）。"""
    return audit(load_tasks(tasks_path), load_dev_seats(routing_path))


def _md_cell(value) -> str:
    """Markdown 表格单元格转义（竖线换行都压平）。"""
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_markdown(report):
    """把报告渲染为 Markdown 表。"""
    violations = report.get("violations") or []
    lines = [
        "# 研发派工路由审计",
        "",
        "- schema：`{}`".format(report.get("schema", SCHEMA)),
        "- 审计工单：{}（仅 dept=dev）".format(report.get("checked", 0)),
        "- 违规工单：{}".format(len(violations)),
        "- 结论：{}".format("OK" if report.get("ok") else "FAIL"),
        "",
        "| 工单 | owner | 违规原因 |",
        "| --- | --- | --- |",
    ]
    if violations:
        for item in violations:
            reasons = "；".join(item.get("reasons") or [])
            lines.append(
                "| {} | {} | {} |".format(
                    _md_cell(item.get("tid")),
                    _md_cell(item.get("owner")) or "（空）",
                    _md_cell(reasons),
                )
            )
    else:
        lines.append("| （无） | - | 无违规 |")
    return "\n".join(lines)


def main(argv=None):
    """CLI 入口；返回进程退出码（默认 0，``--strict`` 有违规时 1）。"""
    parser = argparse.ArgumentParser(
        description="研发派工路由审计（只读：tasks.json + dev_routing_rules.json）"
    )
    parser.add_argument("--tasks", default=str(DEFAULT_TASKS), help="tasks.json 路径")
    parser.add_argument(
        "--routing", default=str(DEFAULT_ROUTING), help="dev_routing_rules.json 路径"
    )
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="输出 JSON 报告（默认）")
    fmt.add_argument("--md", action="store_true", help="输出 Markdown 表")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="有违规时退出码 1（默认报告模式始终 0）",
    )
    args = parser.parse_args(argv)

    report = run(args.tasks, args.routing)
    if args.md:
        print(render_markdown(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and report["violations"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
