#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S2 Q2 验收编排（只读）：data-warehouse 登录/SLO 探针 + 4 个 Q1 军团 Q2 证据包状态汇总。

安全边界（P0 S2）：
- **只读**：不修改 Q2 证据包、不写生产数据、不启停服务；唯一写点是显式 `--out`。
- **不伪造登录态**：无会话文件 → `PENDING-LOGIN`；工具只读 cookie，不在任何输出中打印其内容。
- 验收结论显式区分 `AUTO-EVIDENCE`（本工具/探针自动证据）与 `OWNER-SIGNOFF`（人工签署，本工具不代签）。

用法：
  python -X utf8 tools/q2_acceptance.py                       # Markdown 打印（默认）
  python -X utf8 tools/q2_acceptance.py --json                # JSON 打印
  python -X utf8 tools/q2_acceptance.py --cookie-file C:\\Temp\\zb_session.txt
  python -X utf8 tools/q2_acceptance.py --json --out C:\\Temp\\q2_acceptance.json

退出码：0=AUTO-EVIDENCE-PASS（owner 签字仍 PENDING，语义见输出）；2=PENDING；3=ERROR（fail-visible）。
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import dw_q2_probe  # noqa: E402  （复用 cookie 解析与 DW 探针）
import q2_evidence  # noqa: E402  （复用 docs/q2 产物路径口径）

SCHEMA = "q2/acceptance/v1"
RC_PASS, RC_PENDING, RC_ERROR = 0, 2, 3
DEFAULT_COOKIE_FILE = Path(r"C:\Temp\zb_session.txt")
Q1_LEGIONS = (
    "huangzhong-acceptance",
    "lusu-d1-flow",
    "quant-domain",
    "quant-strategy-line",
)
MATRIX_JSON = q2_evidence.OUT / "q2_evidence_matrix_v1.json"
Q1_DIR = q2_evidence.OUT

_STATEMENTS = {
    "AUTO-EVIDENCE-PASS": "自动证据齐（AUTO-EVIDENCE=PASS）；仍须 OWNER-SIGNOFF 方为 Q2 PASS，本工具不代签。",
    "PENDING": "自动证据/PENDING（含登录态或 Q1 证据缺口）；不伪造 PASS，待补齐后重跑。",
    "ERROR": "无法完成验收编排（fail-visible）；请先修复输入/探针错误。",
}


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def resolve_session(cookie_file=None, cookie: str = "", explicit: bool = False) -> dict:
    """解析会话来源；返回 {provided, source, cookie, error}。

    `explicit=False` 且默认文件缺失 = 无会话（PENDING-LOGIN，不报错）；
    `explicit=True` 且文件缺失/不可读 = error（main 层升级为 ERROR/fail-visible）。
    """
    if cookie:
        return {"provided": True, "source": "--cookie", "cookie": str(cookie), "error": ""}
    path = Path(cookie_file) if cookie_file else DEFAULT_COOKIE_FILE
    if not path.is_file():
        why = "cookie 文件不存在" if explicit else "默认会话文件不存在（未登录）"
        return {"provided": False, "source": str(path), "cookie": "", "error": why}
    try:
        value = dw_q2_probe.cookie_from_file(str(path))
    except OSError as exc:  # noqa: BLE001
        return {"provided": False, "source": str(path), "cookie": "",
                "error": "cookie 文件不可读: %s" % str(exc)[:120]}
    if not value:
        return {"provided": False, "source": str(path), "cookie": "",
                "error": "会话文件为空"}
    low = value.lower()
    if "<value>" in low or "your_cookie" in low or value.strip() in ("zb_session=", "zb_session=xxx"):
        return {"provided": False, "source": str(path), "cookie": "",
                "error": "会话文件仍是占位符（如 <value>），请替换为真实 zb_session"}
    return {"provided": True, "source": str(path), "cookie": value, "error": ""}


def _read_matrix(matrix_path: Path) -> tuple[dict | None, list[str]]:
    try:
        raw = matrix_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return None, ["Q2 证据矩阵不可读（%s）：%s" % (matrix_path, str(exc)[:120])]
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return None, ["Q2 证据矩阵 JSON 解析失败（%s）：%s" % (matrix_path, str(exc)[:120])]
    if not isinstance(data, dict) or not isinstance(data.get("legions"), list):
        return None, ["Q2 证据矩阵结构非法（缺 legions 数组）：%s" % matrix_path]
    return data, []


def _signoff_state(text: str) -> tuple[bool, str]:
    """从证据包「签署」节判断 owner 是否已签（含占位符/待 owner = PENDING；本工具不代签）。"""
    if "## 签署" not in text:
        return False, "证据包无签署节"
    part = text.split("## 签署", 1)[-1].strip()
    if not part:
        return False, "签署节为空"
    if "____" in part or "待 owner" in part:
        return False, "签署节含占位符/待签"
    if re.search(r"结论[:：]\s*(通过|PASS|Pass)", part) and \
            re.search(r"日期[:：]\s*\d{4}-\d{2}-\d{2}", part):
        return True, "签署节含 owner/日期/结论"
    return False, "签署节无可判定签署"


def load_q1_evidence(matrix_path=None, q1_dir=None) -> tuple[list[dict], list[str]]:
    """只读汇总 4 个 Q1 军团的 Q2 证据包状态（矩阵 JSON + docs/q2 证据文件）。"""
    matrix_path = Path(matrix_path) if matrix_path else MATRIX_JSON
    q1_dir = Path(q1_dir) if q1_dir else Q1_DIR
    data, errors = _read_matrix(matrix_path)
    by_id = {str(x.get("id")): x for x in (data or {}).get("legions") or []
             if isinstance(x, dict)}
    rows = []
    for cid in Q1_LEGIONS:
        raw = by_id.get(cid)
        ev_file = q1_dir / ("%s_Q2_evidence_v1.md" % cid)
        row = {
            "id": cid,
            "owner": (raw or {}).get("owner"),
            "in_matrix": raw is not None,
            "auto_ok": bool((raw or {}).get("auto_ok")),
            "q2_status": (raw or {}).get("q2_status"),
            "manual_gate": (raw or {}).get("manual_gate"),
            "evidence_file": str(ev_file),
            "evidence_file_exists": ev_file.is_file(),
            "owner_signoff": "PENDING",
            "owner_signoff_source": "证据包缺失/未签名",
            "gaps": [],
        }
        if raw is None:
            row["gaps"].append("matrix-missing")
        if not row["auto_ok"]:
            row["gaps"].append("auto-evidence-partial")
        if not ev_file.is_file():
            row["gaps"].append("evidence-file-missing")
        else:
            try:
                text = ev_file.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:  # noqa: BLE001
                row["gaps"].append("evidence-file-unreadable")
                row["owner_signoff_source"] = "读取失败: %s" % str(exc)[:80]
            else:
                signed, note = _signoff_state(text)
                row["owner_signoff"] = "SIGNED" if signed else "PENDING"
                row["owner_signoff_source"] = note
        rows.append(row)
    return rows, errors


def _auto_evidence_state(dw: dict, q1: list[dict]) -> str:
    if dw.get("auth_state") == "PENDING-LOGIN":
        return "PENDING"
    if dw.get("ok") is not True:
        return "FAIL" if dw.get("auth_state") == "FAIL" else "PENDING"
    if any(not r["in_matrix"] or not r["auto_ok"] or not r["evidence_file_exists"]
           for r in q1):
        return "PENDING"
    return "PASS"


def _owner_signoff_state(q1: list[dict]) -> str:
    return "SIGNED" if q1 and all(r["owner_signoff"] == "SIGNED" for r in q1) else "PENDING"


def build(cookie: str = "", *, cookie_source: str = "none", cookie_blocker: str = "",
          cookie_explicit: bool = False, dw_builder=None, matrix_path=None,
          q1_dir=None) -> dict:
    """构造验收编排结果（纯函数；dw_builder 可注入用于测试，不连网）。"""
    errors: list[str] = []
    if cookie_explicit and not cookie:
        errors.append("会话文件不可用（fail-visible）：%s" % (cookie_blocker or "未知原因"))
    dw_builder = dw_builder or dw_q2_probe.build
    if cookie:
        try:
            dw = dict(dw_builder(cookie) or {})
        except Exception as exc:  # noqa: BLE001 — fail-visible
            errors.append("DW 探针异常：%s: %s" % (type(exc).__name__, str(exc)[:160]))
            dw = {"auth_state": "ERROR", "ok": False, "checks": [],
                  "authenticated_pages": [], "slo": {}}
    else:
        dw = {
            "auth_state": "PENDING-LOGIN",
            "ok": False,
            "checks": [],
            "authenticated_pages": [],
            "slo": {"api_p95_ms": None, "slo_ok": None},
            "note": "无会话 cookie：页面登录验收 PENDING-LOGIN（不伪造登录）",
            "skip_reason": cookie_blocker or "无会话 cookie",
        }
    q1, q1_errors = load_q1_evidence(matrix_path=matrix_path, q1_dir=q1_dir)
    errors.extend(q1_errors)
    auto = _auto_evidence_state(dw, q1)
    signoff = _owner_signoff_state(q1)
    state = "ERROR" if errors else ("AUTO-EVIDENCE-PASS" if auto == "PASS" else "PENDING")
    return {
        "schema": SCHEMA,
        "generated_at": _now(),
        "read_only": True,
        "session": {"provided": bool(cookie), "source": cookie_source,
                    "blocker": cookie_blocker or ""},
        "dw": dw,
        "q1": q1,
        "summary": {
            "q1_total": len(Q1_LEGIONS),
            "q1_auto_pass": sum(1 for r in q1 if r["auto_ok"] and r["evidence_file_exists"]),
            "q1_signoff_signed": sum(1 for r in q1 if r["owner_signoff"] == "SIGNED"),
            "errors": len(errors),
        },
        "conclusion": {
            "state": state,
            "auto_evidence": auto,
            "owner_signoff": signoff,
            "statement": _STATEMENTS[state],
        },
        "errors": errors,
    }


def exit_code(payload: dict) -> int:
    state = (payload.get("conclusion") or {}).get("state")
    if state == "ERROR":
        return RC_ERROR
    if state == "AUTO-EVIDENCE-PASS":
        return RC_PASS
    return RC_PENDING


def render_markdown(payload: dict) -> str:
    """Markdown 打印（不打印 cookie 内容）。"""
    c = payload.get("conclusion") or {}
    s = payload.get("summary") or {}
    sess = payload.get("session") or {}
    dw = payload.get("dw") or {}
    lines = [
        "# Q2 验收编排 · S2（DW + 4 Q1）",
        "",
        "- generated_at：%s｜schema：%s｜read_only：%s"
        % (payload.get("generated_at"), payload.get("schema"), payload.get("read_only")),
        "- 结论：state=**%s**｜AUTO-EVIDENCE=**%s**｜OWNER-SIGNOFF=**%s**"
        % (c.get("state"), c.get("auto_evidence"), c.get("owner_signoff")),
        "- 说明：%s" % c.get("statement", ""),
        "",
        "## 会话（不打印 cookie 内容）",
        "",
        "| 项 | 值 |",
        "|---|---|",
        "| 会话提供 | %s |" % ("是" if sess.get("provided") else "否（PENDING-LOGIN）"),
        "| 来源 | %s |" % sess.get("source", "-"),
        "| 阻塞原因 | %s |" % (sess.get("blocker") or "-"),
        "| DW auth_state | %s |" % dw.get("auth_state"),
        "",
        "## data-warehouse 自动探针",
        "",
    ]
    checks = dw.get("checks") or []
    if checks:
        lines += ["| 检查 | 实际 | ms | 结果 |", "|---|---:|---:|---|"]
        for x in checks:
            lines.append("| %s | %s | %s | %s |" % (
                x.get("name"), x.get("got"), x.get("ms"),
                "PASS" if x.get("ok") else "FAIL"))
        lines.append("")
        pages = dw.get("authenticated_pages") or []
        if pages:
            lines += ["登录态页面（cookie 验证）：", ""]
            for p in pages:
                lines.append("- `%s`：%s %s" % (p.get("path"), p.get("got"),
                                                "PASS" if p.get("ok") else "FAIL"))
            lines.append("")
    else:
        lines += ["> 无会话/未执行 DW 探针：%s" % (dw.get("note") or dw.get("skip_reason") or "-"), ""]
    slo = dw.get("slo") or {}
    lines += [
        "## SLO",
        "",
        "- API p95：%s ms（<800ms：%s）"
        % (slo.get("api_p95_ms"), slo.get("slo_ok")),
        "",
        "## 4 个 Q1 军团 Q2 证据包（只读汇总）",
        "",
        "| 军团 | owner | 自动证据 | 证据包 | OWNER-SIGNOFF | manual gate | 缺口 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in payload.get("q1") or []:
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            r["id"], r.get("owner") or "-",
            "AUTO-EVIDENCE-COLLECTED" if r["auto_ok"] else "PARTIAL",
            "存在" if r["evidence_file_exists"] else "缺失",
            r["owner_signoff"], r.get("manual_gate") or "-",
            ",".join(r.get("gaps") or []) or "-"))
    lines += [
        "",
        "> AUTO-EVIDENCE = 自动探针/证据包状态；OWNER-SIGNOFF = 人工签署（本工具不代签）。",
        "> 未 Q2 的军团不得标 active（见 docs/ops/2026-11-03_六军团Q2矩阵_v1.md）。",
        "",
        "## 汇总",
        "",
        "- Q1 总数：%s｜自动证据齐：%s｜已签字：%s｜错误：%s"
        % (s.get("q1_total"), s.get("q1_auto_pass"),
           s.get("q1_signoff_signed"), s.get("errors")),
    ]
    if payload.get("errors"):
        lines += ["", "## 错误（fail-visible）", ""] + \
            ["- %s" % e for e in payload["errors"]]
    lines += ["", "## 边界", "",
              "- 只读：不写 Q2 证据包/生产数据；唯一写点是显式 `--out`。",
              "- 不启停服务、不改 nginx、不代签；无会话时 PENDING-LOGIN。"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="S2 Q2 验收编排（DW + 4 Q1；只读，不伪造登录）")
    ap.add_argument("--cookie", default="", help="直接传入 Cookie（优先于文件；不落盘）")
    ap.add_argument("--cookie-file", default="", help="会话文件，默认 C:\\Temp\\zb_session.txt")
    out = ap.add_mutually_exclusive_group()
    out.add_argument("--json", action="store_true", help="JSON 输出")
    out.add_argument("--markdown", action="store_true", help="Markdown 输出（默认）")
    ap.add_argument("--out", default="", help="写入指定文件（默认只打印）")
    ap.add_argument("--matrix", default="", help="Q2 证据矩阵 JSON 路径（测试/审计用）")
    ap.add_argument("--q1-dir", default="", help="Q1 证据包目录（测试/审计用）")
    args = ap.parse_args(argv)
    explicit = bool(args.cookie_file)
    session = resolve_session(cookie_file=(args.cookie_file or None),
                              cookie=args.cookie, explicit=explicit)
    payload = build(session["cookie"], cookie_source=session["source"],
                    cookie_blocker=session["error"], cookie_explicit=explicit,
                    matrix_path=args.matrix or None, q1_dir=args.q1_dir or None)
    text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n" \
        if args.json else render_markdown(payload)
    if args.out:
        try:
            Path(args.out).write_text(text, encoding="utf-8")
        except OSError as exc:
            print("ERROR: 无法写入 --out 文件（%s）：%s" % (args.out, exc), file=sys.stderr)
            return RC_ERROR
        print("written: %s" % args.out)
    else:
        print(text, end="")
    return exit_code(payload)


if __name__ == "__main__":
    raise SystemExit(main())
