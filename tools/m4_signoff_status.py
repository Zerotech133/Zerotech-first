# -*- coding: utf-8 -*-
"""M4 签收状态只读看板（工具，2026-10-31 WO-M4-SIGNOFF-STATUS-20261031）。

把「M4 到底签完没有」从翻文件变成一条命令：读 approval 台账 + 签字角色映射，
复用 ``portal/approval_control.evaluate_signoff``（纯函数单一真源）算出
signed / missing / state / complete，并汇总 M4 证据文件的存在性/时间/摘要。

只读纪律（不写签字）：
  - 不改 ``approvals.jsonl``、不改 ``approval_signers.json``、不写审批台账；
  - 不伪造签字/证据：映射未命中一律 ``roles_unresolved``，证据缺失照实记；
  - 不重启 bot/服务、不改 org/权限。

判定口径：
  - 签字：``evaluate_signoff`` 默认要求四签 QA1/GUANYU/VERIFY1/A0（``--required``
    可覆盖）；annotate 的 refs 指向的试点点击不计签；同 operator 重复只算 1 签；
    角色映射缺失 → roles_unresolved（incomplete，绝不猜谁是谁）。
  - 证据：M4 独立复验评估 md / m4_tests.txt / m4_smoke.txt 为必检（另有 S2 卡
    payload/回执为参考项）；先查仓根再查平台根（C:\\项目），默认路径见
    ``EVIDENCE_SPECS``，``--evidence-root`` 可隔离（测试/异地核验）。
  - complete = 签字齐 **且** 必检证据齐；否则 incomplete。

用法::

    python -X utf8 tools/m4_signoff_status.py
    python -X utf8 tools/m4_signoff_status.py --json
    python -X utf8 tools/m4_signoff_status.py --task "M4/v1.1 正式验收签字"
    python -X utf8 tools/m4_signoff_status.py --report-dir deliverables
    python -X utf8 tools/m4_signoff_status.py --evidence-root C:\\Temp\\m4ev

输出：JSON 报告默认 ``agent_platform/data/shadow/m4_signoff_status_<date>.json``
（``--report-dir`` 可指 deliverables；``--no-write`` 只打印）+ 人类可读摘要。

退出码：0=complete（签字齐且证据齐）/ 1=incomplete / 2=自身错误（参数/异常）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLATFORM_ROOT = REPO.parent
PORTAL = REPO / "portal"
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))

import approval_control as AC  # noqa: E402  —— 签收判定纯函数真源（只 import 不写）

DEFAULT_LEDGER = AC.LEDGER
DEFAULT_CONFIG = REPO / "agent_platform" / "config" / "approval_signers.json"
DEFAULT_REPORT_DIR = REPO / "agent_platform" / "data" / "shadow"
DEFAULT_TASK = "M4/v1.1 正式验收签字"
SCHEMA = "m4_signoff_status/v1"
DEFAULT_REQUIRED = list(AC.REQUIRED_SIGNERS)

# 证据清单：required=True 的缺失 → 看板 incomplete。candidates 相对证据根解析
# （默认查 [仓根, 平台根]；C:\项目\deliverables 与 C:\项目\work 均在平台根下）。
EVIDENCE_SPECS = (
    {"key": "m4_eval", "label": "M4 独立复验与收口评估",
     "required": True,
     "candidates": ("deliverables/2026-10-31_M4独立复验与收口评估_v1.md",)},
    {"key": "m4_tests", "label": "M4 独立测试输出（30 passed）",
     "required": True,
     "candidates": ("work/audit-20261031/m4_tests.txt",)},
    {"key": "m4_smoke", "label": "M4 作战屏冒烟输出",
     "required": True,
     "candidates": ("work/audit-20261031/m4_smoke.txt",)},
    {"key": "m4_card", "label": "S2 签字卡最终 payload（参考）",
     "required": False,
     "candidates": ("work/audit-20261031/m4_card_final.json",)},
    {"key": "m4_card_receipt", "label": "S2 卡发送回执（参考）",
     "required": False,
     "candidates": ("deliverables/2026-10-31_M4_S2卡发送回执_v1.md",)},
)


def _read_text_any(path):
    """读文本：UTF-8(BOM)/UTF-16/GBK 依次尝试（PowerShell 重定向常写 UTF-16）。"""
    data = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_jsonl(path):
    """读 JSONL（坏行跳过；缺失 → []）。只读，不创建/不修改。"""
    rows = []
    try:
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def load_signals(path):
    """读 signer 角色映射（{map:{open_id:role}}）；坏配置 → 空映射 + 错误，不猜。"""
    path = Path(path)
    meta = {"path": str(path), "exists": path.exists(), "valid": False,
            "size": 0, "error": ""}
    if not path.exists():
        meta["error"] = "signers_config_missing"
        return {}, meta
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        meta["error"] = "signers_config_unreadable:%s" % type(exc).__name__
        return {}, meta
    except ValueError:
        meta["error"] = "signers_config_bad_json"
        return {}, meta
    if not isinstance(doc, dict) or not isinstance(doc.get("map"), dict):
        meta["error"] = "signers_config_schema_invalid（需 {map:{open_id:role}}）"
        return {}, meta
    mapping = {}
    for operator, role in doc["map"].items():
        operator = str(operator or "").strip()
        role = str(role).strip().upper() if isinstance(role, str) else ""
        if operator and role:
            mapping[operator] = role
    meta.update({"valid": True, "size": len(mapping)})
    return mapping, meta


def _sha16(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def _summary_line(path):
    """证据摘要：txt（测试输出）取末个非空行，其它取首个非空行；不伪造。"""
    try:
        lines = [ln.strip() for ln in _read_text_any(path).splitlines()]
    except OSError:
        return ""
    lines = [ln for ln in lines if ln]
    if not lines:
        return ""
    pick = lines[-1] if Path(path).suffix.lower() in (".txt", ".log") else lines[0]
    return pick[:200]


def collect_evidence(roots, specs=EVIDENCE_SPECS):
    """逐个证据项按 roots×candidates 查找；返回 (entries, missing_required_keys)。"""
    entries = []
    for spec in specs:
        entry = {"key": spec["key"], "label": spec["label"],
                 "required": bool(spec["required"]), "exists": False,
                 "path": "", "root": "", "mtime": None, "size": None,
                 "sha16": "", "summary": ""}
        for root in roots:
            for rel in spec["candidates"]:
                candidate = Path(root) / rel
                if not candidate.exists():
                    continue
                st = candidate.stat()
                entry.update({
                    "exists": True, "path": str(candidate),
                    "root": str(root),
                    "mtime": time.strftime("%Y-%m-%dT%H:%M:%S",
                                           time.localtime(st.st_mtime)),
                    "size": st.st_size, "sha16": _sha16(candidate),
                    "summary": _summary_line(candidate)})
                break
            if entry["exists"]:
                break
        if not entry["exists"]:
            entry["path"] = str(Path(roots[0]) / spec["candidates"][0])
        entries.append(entry)
    missing = [e["key"] for e in entries if e["required"] and not e["exists"]]
    return entries, missing


def _roles_covered(mapping, required):
    values = set(mapping.values())
    return [role for role in required if role in values]


def _role_missing(mapping, required):
    values = set(mapping.values())
    return [role for role in required if role not in values]


def _available_m4_tasks(rows):
    """台账里所有含 M4 的 task 及行数（便于 operator 确认 --task 是否选对）。"""
    counter = Counter()
    for row in rows:
        task = str(row.get("task") or "")
        if "M4" in task.upper():
            counter[task] += 1
    return dict(sorted(counter.items()))


def _resolve_report_path(report_dir, run_date):
    folder = Path(report_dir)
    if not folder.is_absolute():
        folder = REPO / folder
    return folder / ("m4_signoff_status_%s.json" % run_date.replace("-", ""))


def default_evidence_roots():
    """默认证据搜索根：仓根 → （worktree 时含主仓）→ 平台根（C:\\项目）。

    M4 证据分布在仓内 deliverables/、平台级 C:\\项目\\deliverables 与
    C:\\项目\\work\\audit-20261031；在 worktree 内跑时需回溯主仓/平台根，
    否则只会看到 worktree 空壳（误报缺证据）。
    """
    roots = [REPO]
    parts = REPO.parts
    main_repo = REPO
    if ".worktrees" in parts:
        main_repo = Path(*parts[:parts.index(".worktrees")])
    for candidate in (main_repo, main_repo.parent):
        if candidate not in roots:
            roots.append(candidate)
    return roots


def _write_json_atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
    Path(tmp).replace(path)


def build_report(task, required, ledger, config, evidence_roots,
                 report_dir=DEFAULT_REPORT_DIR, run_date=None):
    """组装只读看板报告（不写任何台账/配置）。"""
    run_date = str(run_date or date.today().isoformat())
    rows = _read_jsonl(ledger)
    mapping, config_meta = load_signals(config)
    signoff = AC.evaluate_signoff(rows, task, required_signers=required,
                                  role_of=mapping or None)
    evidence, evidence_missing = collect_evidence(evidence_roots)
    task_rows = [r for r in rows if str(r.get("task") or "") == task]
    approve_rows = [r for r in task_rows if r.get("action") == "approve"]
    covered = _roles_covered(mapping, required)
    role_missing = _role_missing(mapping, required)

    reasons = []
    if signoff["state"] != "complete":
        reasons.append("签字未齐：%s" % (signoff.get("reason") or "unknown"))
    if evidence_missing:
        reasons.append("必备证据缺失：%s" % ",".join(evidence_missing))
    if config_meta.get("error"):
        reasons.append("签字角色映射配置错误：%s" % config_meta["error"])
    warnings = []
    available = _available_m4_tasks(rows)
    if not task_rows and available:
        warnings.append("task=%r 在台账无记录；台账候选 M4 任务：%s"
                        % (task, ",".join(available)))
    if role_missing and not config_meta.get("error"):
        warnings.append("角色映射未覆盖要求签角色：%s（未命中的点击按 "
                        "roles_unresolved 处理，不猜）" % ",".join(role_missing))

    path = _resolve_report_path(report_dir, run_date)
    complete = not reasons
    return {
        "schema": SCHEMA,
        "date": run_date,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "verdict": "complete" if complete else "incomplete",
        "complete": complete,
        "reasons": reasons,
        "warnings": warnings,
        "task": task,
        "required": list(required),
        "state": signoff["state"],
        "signoff_complete": signoff["state"] == "complete",
        "signed": list(signoff.get("signed_roles") or []),
        "missing": list(signoff.get("missing_signers") or []),
        "signed_count": signoff.get("distinct_signers", 0),
        "signers": [dict(item) for item in (signoff.get("signers") or [])],
        "reason": signoff.get("reason") or "",
        "excluded_ts": list(signoff.get("excluded_ts") or []),
        "signoff": dict(signoff),
        "role_map": dict(config_meta, covered_roles=covered,
                         missing_roles=role_missing),
        "approvals": {
            "ledger": str(ledger),
            "exists": Path(ledger).exists(),
            "rows": len(rows),
            "task_rows": len(task_rows),
            "approve_rows": len(approve_rows),
            "annotate_rows": len([r for r in task_rows
                                  if r.get("action") == "annotate"]),
            "available_m4_tasks": available,
        },
        "evidence": evidence,
        "evidence_required": [e["key"] for e in evidence if e["required"]],
        "evidence_missing": evidence_missing,
        "report_path": str(path),
        "read_only": True,
    }


def _print_summary(report):
    print("[m4_signoff_status] task=%s verdict=%s state=%s complete=%s"
          % (report["task"], report["verdict"], report["state"],
             report["complete"]))
    print("  signed=%s | missing=%s | reason=%s | signed_count=%d"
          % (",".join(report["signed"]) or "-",
             ",".join(report["missing"]) or "-",
             report["reason"] or "-", report["signed_count"]))
    required_evidence = [e for e in report["evidence"] if e["required"]]
    ok_n = sum(1 for e in required_evidence if e["exists"])
    print("  证据(必检)=%d/%d | report=%s"
          % (ok_n, len(required_evidence), report["report_path"]))
    for entry in report["evidence"]:
        mark = "OK " if entry["exists"] else "缺 "
        head = "[%s] %s（%s）" % (mark, entry["key"], entry["label"])
        if entry["exists"]:
            print("  %s mtime=%s size=%s summary=%s"
                  % (head, entry["mtime"], entry["size"],
                     entry["summary"] or "-"))
        else:
            print("  %s expected=%s" % (head, entry["path"]))
    for reason in report["reasons"]:
        print("  [FAIL] %s" % reason)
    for warning in report["warnings"]:
        print("  [warn] %s" % warning)
    if report["reason"] == "roles_unresolved":
        print("  [hint] 角色映射未命中/未配置：补 agent_platform/config/"
              "approval_signers.json 的 map（open_id→角色），不代签。")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="M4 签收状态只读看板（不写签字）：signed/missing/state + 证据汇总")
    parser.add_argument("--task", default=DEFAULT_TASK,
                        help="approval 台账 task 字段（默认 %r）" % DEFAULT_TASK)
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER),
                        help="approval JSONL（默认 agent_platform/data/approvals.jsonl）")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG),
                        help="signer 角色映射（默认 agent_platform/config/approval_signers.json）")
    parser.add_argument("--required", default=",".join(DEFAULT_REQUIRED),
                        help="要求签角色（逗号分隔，默认 QA1,GUANYU,VERIFY1,A0）")
    parser.add_argument("--evidence-root", default=None,
                        help="只在指定目录下找证据（默认先仓根再平台根）")
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR),
                        help="JSON 报告目录（默认 agent_platform/data/shadow）")
    parser.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                        help="报告日期（默认今天，用于报告文件名）")
    parser.add_argument("--json", action="store_true", help="stdout 输出报告 JSON")
    parser.add_argument("--no-write", action="store_true",
                        help="只打印，不写报告文件")
    args = parser.parse_args(argv)
    run_date = args.date or date.today().isoformat()
    try:
        date.fromisoformat(str(run_date))
    except ValueError:
        parser.error("--date 需 YYYY-MM-DD 格式（如 2026-10-31）")
    required = [x.strip().upper()
                for x in str(args.required or "").replace("，", ",").split(",")
                if x.strip()]
    if not required:
        parser.error("--required 不能为空（如 QA1,GUANYU,VERIFY1,A0）")
    roots = ([Path(args.evidence_root)]
             if args.evidence_root else default_evidence_roots())
    try:
        report = build_report(args.task.strip(), required, args.ledger,
                              args.config, roots, report_dir=args.report_dir,
                              run_date=run_date)
        if not args.no_write:
            _write_json_atomic(report["report_path"], report)
    except Exception as exc:  # noqa: BLE001 - 看板 fail-visible，不静默
        if args.json:
            print(json.dumps({"ok": False, "error": type(exc).__name__,
                              "detail": str(exc)[:400]},
                             ensure_ascii=False, indent=2))
        else:
            print("[m4_signoff_status] 自身错误：%s: %s"
                  % (type(exc).__name__, str(exc)[:300]), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_summary(report)
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
