# -*- coding: utf-8 -*-
"""黄忠 11-04 终版回执生成器 v1（只读汇总 + 写终版回执模板）。

用途：把 11-04 复核的三类输入合并为**终版回执模板**（含签字表），减少当日手工拼装：
  ① runner 汇总：`deliverables/2026-11-04_黄忠复核执行汇总_*.md`（或 runner `--json` 输出）
  ② receipt 草案：`deliverables/*_黄忠观察期复核回执_草案_*.md`
  ③ 事故人工确认：`work/huangzhong/incidents_confirm_*.md`（或 deliverables 下同名文件）

输出：`<out-dir>/2026-11-04_黄忠观察期复核回执.md`（九段结构 + 三口径覆盖率 + 签字表）
      已存在则报错不覆盖，除非 `--force`。

九段：①复核时间/环境 ②六条表 ③三口径覆盖率（runs/merge、unique、风险加权）
     ④④满窗 ⑤事故台账+人工确认 ⑥Go/No-Go 建议（保留 L3 提示）⑦证据路径清单
     ⑧待人工项（老田 L3 签字位、赵云 3/3）⑨签字表（黄忠/赵云/老田，占位待签）

边界：
  · 只读输入：不改 receipt/runner/台账/配置，不写 `work/`；唯一写盘 = 终版回执模板；
  · **不自动发消息、不改判定**：六条/覆盖率/建议均原样搬运，不重新判定；
  · 明确标注「本文件为终版模板，未签字前不生效」；
  · fail-visible：输入缺失/解析失败如实标注（如汇总缺风险加权覆盖率 → 该项标缺失），不编造；
  · 退出码：0 = 生成成功；2 = 数据缺失（显式输入不存在/汇总与草案都缺）/ 终版已存在（未 --force）/ 参数或写盘错误。

CLI：`--json` / `--summary <md|json>` / `--receipt <md>` / `--confirm <md>` /
     `--out-dir`（默认 C:\\项目\\deliverables）/ `--work-dir` / `--repo-root` /
     `--now`（ISO，测试复算）/ `--target-date`（默认 2026-11-04；显式 2026-11-10
     时输入/输出文件名与九段内日期引用切到目标日期）/ `--force` / `--no-write`

目标日期参数化（HZ-FINAL-DATE-20261104）：默认 2026-11-04 行为与 v1 完全一致
（终版文件名 2026-11-04_黄忠观察期复核回执.md、汇总 glob、签字位日期均不变）；
显式 --target-date 2026-11-10 用于 11-10 最终验收复算（输出 11-10 同名结构）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = Path(r"C:\项目\deliverables")
DEFAULT_TARGET_DATE = "2026-11-04"
TARGET_HOUR = 10
FINAL_NAME = "2026-11-04_黄忠观察期复核回执.md"
SUMMARY_GLOB = "2026-11-04_黄忠复核执行汇总_*.md"
SUMMARY_JSON_GLOB = "2026-11-04_黄忠复核执行汇总_*.json"
RECEIPT_GLOB = "*_黄忠观察期复核回执_草案_*.md"
CONFIRM_GLOBS = ("incidents_confirm_*.md", "*_incidents_confirm_*.md")
COVERAGE_THRESHOLD = 0.10
MAX_LIST = 30

TOOL_NAME = "huangzhong_1104_finalize.py"
VERSION = "v1 (2026-11-01)"
OK, MISSING, BAD = "ok", "missing", "bad"

SECTION_KEYS = ("一", "二", "三", "四", "五", "六", "七", "八", "九")
SIGN_ROLES = (
    ("黄忠（验收守门官）", "自证六条与证据链", "DSH-Lead 代执行（2026-11-04）"),
    ("赵云（平台质量门/审计）", "抽查 3/3 一致（seed 可复现）", "（待签）"),
    ("老田", "Go/No-Go 最终决策（L3）", "（待签）"),
)


# ------------------------------------------------------------------ 基础工具

def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _iso(ts: dt.datetime | None) -> str | None:
    return ts.isoformat(timespec="seconds") if ts is not None else None


def _parse_ts(raw) -> dt.datetime | None:
    if isinstance(raw, dt.datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=dt.timezone.utc)
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def _target_date_text(raw=None) -> str:
    """解析目标日期（默认 2026-11-04）→ 'YYYY-MM-DD'。

    支持：None/空串（默认）、'2026-11-10'、ISO 日期时间（取日期部分）。
    非法输入抛 ValueError（CLI 捕获后按参数错误 rc=2 处理）。
    """
    text = str(raw).strip() if raw is not None else ""
    if not text:
        return DEFAULT_TARGET_DATE
    if "T" in text or " " in text:
        iso = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            return dt.datetime.fromisoformat(iso).date().isoformat()
        except ValueError as exc:
            raise ValueError(
                "--target-date 无法解析：%r（支持 2026-11-10 或 ISO 日期时间）"
                % text) from exc
    try:
        return dt.date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(
            "--target-date 无法解析：%r（支持 2026-11-10 或 ISO 日期时间）"
            % text) from exc


def _target_label(date_text: str) -> str:
    """目标日期短标签：默认 '11-04'，显式 11-10 → '11-10'。"""
    return date_text[5:] if len(date_text) >= 10 else date_text


def _target_label_hm(date_text: str) -> str:
    """目标日期+时刻标签：默认 '11-04 10:00'（复盘会标准时点）。"""
    return "%s %02d:00" % (_target_label(date_text), TARGET_HOUR)


def _sign_roles(date_text: str) -> tuple:
    """签字表行：默认沿用 SIGN_ROLES；显式目标日期只替换「代执行（日期）」。"""
    rows = []
    for role, duty, slot in SIGN_ROLES:
        if "代执行" in slot and "（" in slot:
            slot = re.sub(r"（[^（）]*）\s*$", "（%s）" % date_text, slot)
        rows.append((role, duty, slot))
    return tuple(rows)


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fmt_num(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return ("%d" % value) if value.is_integer() else ("%.2f" % value)
    return str(value)


def _fmt_pct(value, digits: int = 2) -> str:
    if not _num(value):
        return "—"
    return ("%." + str(digits) + "f%%") % (value * 100)


def _fmt_pct_text(text) -> str:
    """把 '5.45' / '5.45%' 统一成 '5.45%'；不可解析返回 —。"""
    if text is None:
        return "—"
    raw = str(text).strip().rstrip("%")
    try:
        number = float(raw)
    except ValueError:
        return "—"
    return "%.2f%%" % number


def _cell(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _cap(rows: list, limit: int = MAX_LIST) -> list:
    return list(rows[:limit])


def _log(msg: str) -> None:
    print("[finalize] " + str(msg), file=sys.stderr)


def _read_text(path: Path) -> tuple[str | None, str | None]:
    try:
        return Path(path).read_text(encoding="utf-8"), None
    except OSError as exc:
        return None, "读取失败：%s" % exc
    except UnicodeDecodeError as exc:
        return None, "解码失败：%s" % exc


def _read_json(path: Path) -> tuple[object | None, str | None]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, "读取失败：%s" % exc
    except ValueError as exc:
        return None, "JSON 解析失败：%s" % exc


def _latest_in(dirs: list[Path], globs: tuple) -> Path | None:
    """在候选目录中按 mtime 取最新匹配文件（目录不存在则跳过）。"""
    found: list[Path] = []
    for directory in dirs:
        directory = Path(directory)
        if not directory.is_dir():
            continue
        for pattern in globs:
            found.extend(p for p in directory.glob(pattern) if p.is_file())
    if not found:
        return None
    # 去重（同一文件可能被多个候选目录匹配到）
    uniq = {str(p.resolve()): p for p in found}
    files = list(uniq.values())
    files.sort(key=lambda p: (p.stat().st_mtime, p.name))
    return files[-1]


# ------------------------------------------------------------------ runner 汇总解析

def parse_summary(path: Path) -> dict:
    """解析 runner 汇总（md 或 --json 输出）。"""
    row = {"path": str(path), "kind": None, "status": MISSING,
           "generated_at": None, "steps": {}, "values": {},
           "error": None, "warnings": []}
    if path.suffix.lower() == ".json":
        data, err = _read_json(path)
        if err is not None or not isinstance(data, dict):
            row["status"] = BAD
            row["error"] = err or "JSON 顶层不是对象"
            return row
        row["kind"] = "json"
        row["status"] = OK
        row["generated_at"] = data.get("generated_at")
        for step in data.get("steps") or []:
            if not isinstance(step, dict):
                continue
            name = str(step.get("name") or "")
            row["steps"][name] = {"rc": step.get("rc"),
                                  "ok": bool(step.get("ok")),
                                  "extra": step.get("summary") or {},
                                  "error": step.get("error")}
        values = row["values"]
        for name, step in row["steps"].items():
            extra = step.get("extra") or {}
            if name == "preflight":
                values.update({"ready": extra.get("ready"),
                               "blockers": extra.get("blockers"),
                               "warnings": extra.get("warnings"),
                               "mature_at_1030": extra.get("mature_at_1030"),
                               "earliest_window_end": extra.get("earliest_window_end")})
            elif name == "review":
                values.update({"review_passed": extra.get("passed"),
                               "review_total": extra.get("total"),
                               "review_overall": extra.get("overall")})
            elif name == "weekly":
                values.update({"weekly_runs": extra.get("runs"),
                               "weekly_merge_done": extra.get("merge_done"),
                               "weekly_coverage": extra.get("coverage")})
            elif name == "receipt":
                values["receipt_draft"] = extra.get("draft")
            elif name == "risk_coverage":
                values.update({"risk_weighted": extra.get("risk_weighted"),
                               "equal_coverage": extra.get("equal"),
                               "merges_total": extra.get("merges")})
            elif name == "corpus":
                values.update({"corpus_total": extra.get("total"),
                               "legacy": extra.get("legacy"),
                               "drill": extra.get("drill")})
            elif name == "evidence_lint":
                values.update({"evidence_completeness": extra.get("completeness"),
                               "evidence_scanned": extra.get("scanned")})
            elif name == "incidents":
                values.update({"incidents_exists": extra.get("exists"),
                               "incidents_rows": extra.get("rows"),
                               "incidents_issues": extra.get("issues")})
            elif name == "peer_verify":
                values.update({"peer_status": extra.get("status"),
                               "peer_agreement": extra.get("agreement")})
            elif name in ("health", "perf_signal", "security_signal"):
                values["%s_status" % name] = extra.get("status")
        return row
    text, err = _read_text(path)
    if err is not None:
        row["status"] = BAD
        row["error"] = err
        return row
    row["kind"] = "md"
    row["status"] = OK
    m = re.search(r"状态：\*\*(\w+)\*\*", text)
    if m:
        row["values"]["runner_status"] = m.group(1)
    m = re.search(r"（(\d{4}-\d{2}-\d{2}T[^）]+)）", text)
    if m:
        row["generated_at"] = m.group(1)
    # 步骤表行：| name | rc | ✓/✗ | extra |
    for line in text.splitlines():
        m = re.match(r"^\|\s*([A-Za-z_]+)\s*\|\s*([\w—]+)\s*\|\s*(✓|✗)\s*\|\s*(.*?)\s*\|$",
                     line.strip())
        if not m:
            continue
        name, rc, ok, extra = m.group(1), m.group(2), m.group(3), m.group(4)
        try:
            rc_val = int(rc)
        except ValueError:
            rc_val = rc
        row["steps"][name] = {"rc": rc_val, "ok": ok == "✓", "extra": extra,
                              "error": None}
    values = row["values"]
    text_map = {
        "mature_at_1030": r"mature_at_1030=(\d+)",
        "blockers": r"blockers=(\d+)",
        "warnings": r"warnings=(\d+)",
        "weekly_runs": r"runs=(\d+) merge=",
        "weekly_merge_done": r"merge=(\d+) coverage=",
        "weekly_coverage": r"coverage=([\d.]+)",
        "risk_weighted": r"风险加权=([\d.]+)",
        "equal_coverage": r"等权=([\d.]+)",
        "evidence_completeness": r"完整率=([\d.]+)",
        "evidence_scanned": r"scanned=(\d+)",
        "legacy": r"legacy=(\d+)",
        "drill": r"drill=(\d+)",
        "incidents_rows": r"rows=(\d+)",
    }
    for key, pattern in text_map.items():
        m = re.search(pattern, text)
        if m:
            value = m.group(1)
            try:
                value = int(value)
            except ValueError:
                try:
                    value = float(value)
                except ValueError:
                    pass
            values[key] = value
    m = re.search(r"ready=(\w+)", text)
    if m:
        values["ready"] = m.group(1)
    m = re.search(r"exists=(\w+)", text)
    if m:
        values["incidents_exists"] = m.group(1).lower() == "true"
    m = re.search(r"六条\s*(\d+)/(\d+)（overall=(\w+)）", text)
    if m:
        values.update({"review_passed": int(m.group(1)),
                       "review_total": int(m.group(2)),
                       "review_overall": m.group(3)})
    m = re.search(r"draft=(.+?)\s*\|?\s*$", text, re.M)
    if m:
        values["receipt_draft"] = m.group(1).strip()
    m = re.search(r"status=(\w+)\s*\|", text)
    if m and "peer_status" not in values:
        values["peer_status"] = m.group(1)
    return row


# ------------------------------------------------------------------ receipt 草案解析

def _split_sections(text: str) -> dict[str, str]:
    """按 '## 一、...' 拆章节，返回 {一: body, ...}。"""
    sections: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+([一二三四五六七八九十])、\s*(.*)$", line)
        if m:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = m.group(1)
            buf = []
            continue
        if current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def _table_rows(body: str) -> list[list[str]]:
    """解析 markdown 表格行为 cells 列表（跳过表头/分隔行）。"""
    rows: list[list[str]] = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if re.match(r"^\|[\s:\-|]+\|$", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        if cells[0] in ("#", "口径", "角色", "日期", "预计满窗 commit", "项"):
            continue
        rows.append(cells)
    return rows


def parse_receipt(path: Path) -> dict:
    """解析 receipt 草案（九段）。"""
    row = {"path": str(path), "status": MISSING, "error": None,
           "generated_at": None, "target_time": None, "overall": None,
           "six_items": [], "kpi": {}, "coverage": {}, "maturity": {},
           "incidents": {}, "suggestion": {}, "evidence_paths": [],
           "risks": {}, "signature": [], "sections": {}, "warnings": []}
    text, err = _read_text(path)
    if err is not None:
        row["status"] = BAD
        row["error"] = err
        return row
    row["status"] = OK
    sections = _split_sections(text)
    row["sections"] = sections
    m = re.search(r"生成：(\d{4}-\d{2}-\d{2}T[^\s｜]+)", text)
    if m:
        row["generated_at"] = m.group(1)
    m = re.search(r"目标复核：(\d{4}-\d{2}-\d{2}T[^\s｜]+)", text)
    if m:
        row["target_time"] = m.group(1)
    # 二、六条
    body2 = sections.get("二") or ""
    m = re.search(r"overall_pass\s*=\s*(\w+)（(\d+)/(\d+)\s*✓）", body2)
    if m:
        row["overall"] = {"overall_pass": m.group(1) == "True",
                          "passed": int(m.group(2)),
                          "total": int(m.group(3))}
    for cells in _table_rows(body2):
        if not cells or cells[0] not in ("①", "②", "③", "④", "⑤", "⑥"):
            continue
        if len(cells) < 4:
            continue
        row["six_items"].append({
            "id": cells[0], "title": cells[1],
            "status": cells[2], "measured": cells[3],
            "evidence": cells[4] if len(cells) > 4 else "—"})
    m = re.search(r"KPI（review）：runs=(\d+)\s*/\s*merge_done=(\d+)\s*/\s*"
                  r"覆盖率=([\d.]+)%\s*/\s*窗口=(\d+)\s*天", body2)
    if m:
        row["kpi"] = {"runs": int(m.group(1)),
                      "merge_done": int(m.group(2)),
                      "coverage": float(m.group(3)) / 100.0,
                      "window_days": int(m.group(4))}
    # 三、覆盖率
    body3 = sections.get("三") or ""
    coverage: dict = {}
    for cells in _table_rows(body3):
        label = cells[0] if cells else ""
        joined = " | ".join(cells)
        if "主口径" in label:
            m = re.search(r"(\d+)\s*/\s*(\d+)\s*=\s*\*\*([\d.]+)%\*\*", joined)
            if m:
                coverage["runs"] = int(m.group(1))
                coverage["merge_done"] = int(m.group(2))
                coverage["run_merge_pct"] = float(m.group(3)) / 100.0
        elif "unique-commit" in label:
            m = re.search(r"unique_commit=(\d+)", joined)
            if m:
                coverage["unique_commit"] = int(m.group(1))
            m = re.search(r"unique_coverage=\*{0,2}([\d.]+)%", joined)
            if m:
                coverage["unique_pct"] = float(m.group(1)) / 100.0
        elif "投影" in label:
            m = re.search(r"=\s*\*\*([\d.]+)%\*\*", joined)
            if m:
                coverage["projected_pct"] = float(m.group(1)) / 100.0
            m = re.search(r"unique≈\*\*([\d.]+)%\*\*", joined)
            if m:
                coverage["projected_unique_pct"] = float(m.group(1)) / 100.0
    row["coverage"] = coverage
    # 四、满窗
    body4 = sections.get("四") or ""
    maturity: dict = {}
    m = re.search(r"now 满窗：\*\*(\d+) 单\*\*；跟踪中：\*\*(\d+) 单\*\*；"
                  r"[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2} 预计满窗：\*\*(\d+) 单\*\*",
                  body4)
    if m:
        maturity.update({"mature_count": int(m.group(1)),
                         "tracking_count": int(m.group(2)),
                         "mature_at_1030": int(m.group(3))})
    m = re.search(r"最早满窗：`([^`]+)`", body4)
    if m:
        maturity["earliest_window_end"] = m.group(1)
    m = re.search(r"④ 当前状态：`(\w+)`", body4)
    if m:
        maturity["status"] = m.group(1)
    samples = []
    for cells in _table_rows(body4):
        if len(cells) >= 4 and re.fullmatch(r"[0-9a-f]{8}", cells[0]):
            samples.append({"sha8": cells[0], "file": cells[1],
                            "generated_at": cells[2], "window_end": cells[3]})
    maturity["expected_samples"] = samples
    row["maturity"] = maturity
    # 五、事故台账/人工确认
    body5 = sections.get("五") or ""
    incidents: dict = {"lines": _cap([l for l in body5.splitlines()
                                      if l.strip()])}
    m = re.search(r"exists=(\w+)\s*｜\s*行数=(\d+)", body5)
    if m:
        incidents["exists"] = m.group(1).lower() == "true"
        incidents["rows"] = int(m.group(2))
    m = re.search(r"人工确认[^\n]*?：\s*\*\*(.+?)\*\*", body5)
    if m:
        incidents["confirm_note"] = m.group(1)
    m = re.search(r"记录\s*`([^`]+)`", body5)
    if m:
        incidents["confirm_path"] = m.group(1)
    row["incidents"] = incidents
    # 六、建议
    body6 = sections.get("六") or ""
    suggestion: dict = {}
    m = re.search(r"\*\*建议：(.+?)\*\*（code=`(\w+)`）", body6)
    if m:
        suggestion["suggestion"] = m.group(1).strip()
        suggestion["code"] = m.group(2)
    m = re.search(r"- 结论：(.+)", body6)
    if m:
        suggestion["conclusion"] = m.group(1).strip()
    m = re.search(r"- 决策来源：(.+)", body6)
    if m:
        suggestion["source"] = m.group(1).strip()
    m = re.search(r"- \*\*L3 提示\*\*：(.+)", body6)
    if m:
        suggestion["l3_note"] = m.group(1).strip()
    suggestion["body"] = body6
    row["suggestion"] = suggestion
    # 七、证据路径
    body7 = sections.get("七") or ""
    row["evidence_paths"] = [l.strip("- ").strip() for l in body7.splitlines()
                             if l.strip().startswith("-")]
    # 八、风险/待人工
    body8 = sections.get("八") or ""
    risks = {"warnings": [], "todo": []}
    for line in body8.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            risks["warnings"].append(stripped[2:].strip())
        elif re.match(r"^\d+\.\s", stripped):
            risks["todo"].append(re.sub(r"^\d+\.\s*", "", stripped))
    risks["body"] = body8
    row["risks"] = risks
    # 九、签字表
    body9 = sections.get("九") or ""
    row["signature"] = _table_rows(body9)
    return row


# ------------------------------------------------------------------ incidents 人工确认

def parse_confirm(path: Path) -> dict:
    """解析 incidents_confirm_*.md（人工确认）。"""
    row = {"path": str(path), "status": MISSING, "error": None,
           "confirmed": False, "confirmer": None, "confirmed_at": None,
           "basis": None, "conclusion": None, "key_lines": [],
           "content": None}
    text, err = _read_text(path)
    if err is not None:
        row["status"] = BAD
        row["error"] = err
        return row
    row["status"] = OK
    row["content"] = text
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            row["key_lines"].append(stripped[2:].strip())
    m = re.search(r"确认人[：:]\s*(\S+)", text)
    if m:
        row["confirmer"] = m.group(1)
    m = re.search(r"确认时间[：:]\s*([0-9: T\-]+)", text)
    if m:
        row["confirmed_at"] = m.group(1).strip()
    m = re.search(r"依据[：:]\s*(.+)", text)
    if m:
        row["basis"] = m.group(1).strip()
    m = re.search(r"结论[：:]\s*\*\*(.+?)\*\*", text)
    if m:
        row["conclusion"] = m.group(1).strip()
    if "无已知事故" in text or "已确认" in text or "确认" in (row["conclusion"] or ""):
        row["confirmed"] = True
    return row


# ------------------------------------------------------------------ 合并渲染

def _coverage_three(summary: dict, receipt: dict) -> dict:
    """三口径覆盖率：runs/merge、unique、风险加权。"""
    s_val = summary.get("values") or {}
    r_cov = receipt.get("coverage") or {}
    out: dict = {}

    # ① runs / merge
    runs = r_cov.get("runs")
    if runs is None:
        runs = s_val.get("weekly_runs")
    merge_done = r_cov.get("merge_done")
    if merge_done is None:
        merge_done = s_val.get("weekly_merge_done")
    pct = r_cov.get("run_merge_pct")
    if pct is None and _num(s_val.get("weekly_coverage")):
        pct = float(s_val["weekly_coverage"])
    out["run_merge"] = {"label": "① runs / merge_done",
                        "runs": runs, "merge_done": merge_done,
                        "pct": pct,
                        "source": ("汇总 weekly + 草案主口径"
                                   if _num(pct) else "缺失"),
                        "threshold": COVERAGE_THRESHOLD,
                        "pass": bool(_num(pct) and pct >= COVERAGE_THRESHOLD)}

    # ② unique-commit
    unique_commit = r_cov.get("unique_commit")
    unique_pct = r_cov.get("unique_pct")
    out["unique"] = {"label": "② unique-commit 补充",
                     "unique_commit": unique_commit, "pct": unique_pct,
                     "source": "草案 unique-commit 行" if _num(unique_pct) else "缺失",
                     "pass": None}

    # ③ 风险加权（runner risk_coverage）
    risk_pct = None
    if _num(s_val.get("risk_weighted")):
        risk_pct = float(s_val["risk_weighted"])
    equal_pct = None
    if _num(s_val.get("equal_coverage")):
        equal_pct = float(s_val["equal_coverage"])
    out["risk_weighted"] = {"label": "③ 风险加权（runner）",
                            "pct": risk_pct, "equal_pct": equal_pct,
                            "merges_total": s_val.get("merges_total"),
                            "source": "汇总 risk_coverage 步骤" if _num(risk_pct) else "缺失",
                            "pass": None}
    r_target = _parse_ts(receipt.get("target_time"))
    r_date_text = (r_target.date().isoformat()
                   if isinstance(r_target, dt.datetime) else DEFAULT_TARGET_DATE)
    out["projected"] = {"pct": r_cov.get("projected_pct"),
                        "unique_pct": r_cov.get("projected_unique_pct"),
                        "source": ("草案 %s 投影行" % _target_label(r_date_text)
                                   if _num(r_cov.get("projected_pct")) else "缺失")}
    return out


def build_context(summary: dict | None, receipt: dict | None,
                  confirm: dict | None) -> dict:
    values = (summary or {}).get("values") or {}
    receipt = receipt or {}
    confirm = confirm or {}
    coverage = _coverage_three(summary or {}, receipt)
    six_items = list(receipt.get("six_items") or [])
    overall = receipt.get("overall") or {}
    if not six_items and _num(values.get("review_passed")):
        overall = {"overall_pass": values.get("review_overall") == "True",
                   "passed": values.get("review_passed"),
                   "total": values.get("review_total")}
    maturity = dict(receipt.get("maturity") or {})
    if not _num(maturity.get("mature_at_1030")) and _num(values.get("mature_at_1030")):
        maturity["mature_at_1030"] = int(values["mature_at_1030"])
    if not maturity.get("earliest_window_end") and values.get("earliest_window_end"):
        maturity["earliest_window_end"] = values.get("earliest_window_end")
    return {
        "runner": {"status": (summary or {}).get("status"),
                   "generated_at": (summary or {}).get("generated_at"),
                   "path": (summary or {}).get("path"),
                   "values": values,
                   "steps": (summary or {}).get("steps") or {}},
        "receipt": {"status": receipt.get("status"),
                    "path": receipt.get("path"),
                    "generated_at": receipt.get("generated_at"),
                    "target_time": receipt.get("target_time"),
                    "overall": overall,
                    "six_items": six_items,
                    "kpi": receipt.get("kpi") or {},
                    "coverage": receipt.get("coverage") or {},
                    "maturity": maturity,
                    "incidents": receipt.get("incidents") or {},
                    "suggestion": receipt.get("suggestion") or {},
                    "evidence_paths": receipt.get("evidence_paths") or [],
                    "risks": receipt.get("risks") or {},
                    "signature": receipt.get("signature") or []},
        "confirm": confirm,
        "coverage": coverage,
    }


def render_md(rep: dict) -> str:
    ctx = rep["context"]
    runner = ctx["runner"]
    receipt = ctx["receipt"]
    values = runner.get("values") or {}
    coverage = ctx["coverage"]
    confirm = ctx["confirm"] or {}
    warnings = rep.get("warnings") or []
    now_text = rep.get("generated_at") or "—"
    date_text = rep.get("target_date") or _target_date_text(None)
    date_label = _target_label(date_text)
    date_label_hm = _target_label_hm(date_text)

    lines: list[str] = []
    lines.append("# 黄忠观察期复核回执（终版模板）· %s" % date_text)
    lines.append("")
    lines.append("> **本文件为终版模板，未签字前不生效**；数据来自 runner 汇总 + receipt 草案 + "
                 "incidents 人工确认，只搬运不改判。")
    lines.append("> 生成：%s（Asia/Shanghai）｜工具：`tools/%s` %s｜工单：`WO-HZ-1104FINAL-20261101`"
                 % (now_text, TOOL_NAME, VERSION))
    lines.append("> 输入：汇总 `%s`｜草案 `%s`｜人工确认 `%s`"
                 % (runner.get("path") or "（缺失）",
                    receipt.get("path") or "（缺失）",
                    confirm.get("path") or "（缺失）"))
    lines.append("")

    # 一、复核时间与环境
    lines.append("## 一、复核时间与环境")
    lines.append("")
    lines.append("- 终版生成时间：`%s`（Asia/Shanghai）" % now_text)
    lines.append("- runner 汇总：`%s`（生成 %s；状态 **%s**）"
                 % (runner.get("path") or "（缺失）",
                    runner.get("generated_at") or "—",
                    runner.get("status") or "—"))
    lines.append("- receipt 草案：`%s`（生成 %s；目标复核 %s）"
                 % (receipt.get("path") or "（缺失）",
                    receipt.get("generated_at") or "—",
                    receipt.get("target_time") or "—"))
    lines.append("- 事故人工确认：`%s`（%s）"
                 % (confirm.get("path") or "（缺失）",
                    "已确认" if confirm.get("confirmed") else "待确认/缺失"))
    lines.append("- 时区：Asia/Shanghai ｜ 判据：认证标准 v2 + runbook v6 §十 + 一键流程 v1.1 §六")
    steps = runner.get("steps") or {}
    if steps:
        lines.append("")
        lines.append("| 步骤 | rc | 状态 |")
        lines.append("|---|---|---|")
        for name in ("preflight", "review", "weekly", "receipt", "risk_coverage",
                     "corpus", "peer_verify", "evidence_lint", "incidents",
                     "health", "perf_signal", "security_signal"):
            step = steps.get(name)
            if step is None:
                continue
            lines.append("| %s | %s | %s |"
                         % (_cell(name), _cell(step.get("rc")),
                            "✓" if step.get("ok") else "✗"))
    lines.append("")

    # 二、六条表
    lines.append("## 二、六条判据（认证标准 v2）")
    lines.append("")
    overall = receipt.get("overall") or {}
    six_items = receipt.get("six_items") or []
    if overall:
        lines.append("**overall_pass = %s（%s/%s ✓）**；仅六条全 ✓ 才通过。"
                     % ("True" if overall.get("overall_pass") else "False",
                        overall.get("passed"), overall.get("total")))
        lines.append("")
    if six_items:
        lines.append("| # | 判据 | 状态 | 实测 | 证据 |")
        lines.append("|---|---|---|---|---|")
        for item in six_items:
            lines.append("| %s | %s | %s | %s | %s |"
                         % (_cell(item.get("id")), _cell(item.get("title")),
                            _cell(item.get("status")),
                            _cell(item.get("measured")),
                            _cell(item.get("evidence"))))
    else:
        lines.append("- 六条明细缺失（receipt 草案缺失或解析失败）；汇总口径："
                     "review %s/%s（overall=%s），**需人工补六条表**。"
                     % (_fmt_num(values.get("review_passed")),
                        _fmt_num(values.get("review_total")),
                        _fmt_num(values.get("review_overall"))))
    lines.append("")
    kpi = receipt.get("kpi") or {}
    if kpi:
        lines.append("- KPI（review）：runs=%s / merge_done=%s / 覆盖率=%s / 窗口=%s 天"
                     % (_fmt_num(kpi.get("runs")), _fmt_num(kpi.get("merge_done")),
                        _fmt_pct(kpi.get("coverage")),
                        _fmt_num(kpi.get("window_days"))))
        lines.append("")

    # 三、三口径覆盖率
    lines.append("## 三、三口径覆盖率")
    lines.append("")
    lines.append("| 口径 | 值 | 来源 | 判读 |")
    lines.append("|---|---|---|---|")
    rm = coverage.get("run_merge") or {}
    rm_text = "%s / %s = **%s**" % (_fmt_num(rm.get("runs")),
                                    _fmt_num(rm.get("merge_done")),
                                    _fmt_pct(rm.get("pct")))
    rm_judge = ("达标（门槛 ≥%s）" % _fmt_pct(rm.get("threshold"))
                if rm.get("pass") else "未达标/不可判定（门槛 ≥%s）"
                % _fmt_pct(rm.get("threshold")))
    if not _num(rm.get("pct")):
        rm_judge = "**缺失**（汇总/草案均未提供）"
    lines.append("| ① runs / merge_done（主口径） | %s | %s | %s |"
                 % (rm_text, _cell(rm.get("source")), rm_judge))
    uniq = coverage.get("unique") or {}
    lines.append("| ② unique-commit（补充） | unique_commit=%s / **%s** | %s | 防同 HEAD 重跑注水；仅补充说明 |"
                 % (_fmt_num(uniq.get("unique_commit")),
                    _fmt_pct(uniq.get("pct")), _cell(uniq.get("source"))))
    risk = coverage.get("risk_weighted") or {}
    lines.append("| ③ 风险加权（runner） | **%s**（等权 %s；merges=%s） | %s | 补充口径（权重口径见 risk_coverage 报告） |"
                 % (_fmt_pct(risk.get("pct")), _fmt_pct(risk.get("equal_pct")),
                    _fmt_num(risk.get("merges_total")), _cell(risk.get("source"))))
    proj = coverage.get("projected") or {}
    if _num(proj.get("pct")):
        lines.append("| ④ %s 投影（参考） | %s（unique≈%s） | %s | 趋势工具投影，非实测 |"
                     % (date_label,
                        _fmt_pct(proj.get("pct")),
                        _fmt_pct(proj.get("unique_pct")),
                        _cell(proj.get("source"))))
    lines.append("")
    if not _num((coverage.get("risk_weighted") or {}).get("pct")):
        lines.append("- **风险加权覆盖率缺失**（runner 汇总未提供 risk_coverage 结果）——"
                     "按 fail-visible 标注，需补跑 `tools/huangzhong_risk_coverage.py --json`。")
        lines.append("")

    # 四、④ 满窗
    lines.append("## 四、④ 满窗核对（漏放=0 前置）")
    lines.append("")
    maturity = receipt.get("maturity") or {}
    lines.append("- now 满窗：**%s 单**；跟踪中：**%s 单**；%s 预计满窗：**%s 单**"
                 % (_fmt_num(maturity.get("mature_count")),
                    _fmt_num(maturity.get("tracking_count")),
                    date_label_hm,
                    _fmt_num(maturity.get("mature_at_1030"))))
    lines.append("- 最早满窗：`%s`；④ 当前状态：`%s`"
                 % (maturity.get("earliest_window_end") or "—",
                    maturity.get("status") or "—"))
    samples = maturity.get("expected_samples") or []
    if samples:
        lines.append("")
        lines.append("| 预计满窗 commit | 验收单 | generated_at | window_end |")
        lines.append("|---|---|---|---|")
        for sample in _cap(samples, 10):
            lines.append("| %s | %s | %s | %s |"
                         % (_cell(sample.get("sha8")), _cell(sample.get("file")),
                            _cell(sample.get("generated_at")),
                            _cell(sample.get("window_end"))))
    lines.append("")
    lines.append("- 判定细则见 runbook v6 §7.3；④ tracking 属预期（时间未满窗），不阻塞 ②③⑤⑥。")
    lines.append("")

    # 五、事故台账 + 人工确认
    lines.append("## 五、事故台账 + 人工确认")
    lines.append("")
    incidents = receipt.get("incidents") or {}
    lines.append("- 台账：`%s` ｜ exists=%s ｜ 行数=%s ｜ issues=%s"
                 % (incidents.get("path") or "work/huangzhong/incidents.jsonl",
                    _fmt_num(values.get("incidents_exists")),
                    _fmt_num(values.get("incidents_rows")
                             if values.get("incidents_rows") is not None
                             else incidents.get("rows")),
                    _fmt_num(values.get("incidents_issues"))))
    if confirm.get("status") == OK:
        lines.append("- **人工确认（%s，%s）**：%s"
                     % (confirm.get("confirmer") or "—",
                        confirm.get("confirmed_at") or "—",
                        confirm.get("conclusion") or "（结论未解析）"))
        if confirm.get("basis"):
            lines.append("- 依据：%s" % confirm["basis"])
        for line in _cap(confirm.get("key_lines") or [], 6):
            lines.append("- %s" % line)
        lines.append("- 确认记录：`%s`" % confirm.get("path"))
    else:
        lines.append("- **未找到 incidents 人工确认文件**（`incidents_confirm_*.md`）→ "
                     "**待人工确认**（fail-visible；不得默认「无已知事故」）。")
    lines.append("")

    # 六、Go/No-Go 建议（保留 L3）
    lines.append("## 六、Go/No-Go 建议（含 L3 提示）")
    lines.append("")
    suggestion = receipt.get("suggestion") or {}
    if suggestion.get("suggestion") or suggestion.get("code") or suggestion.get("body"):
        label = suggestion.get("suggestion") or "—"
        display = label[2:].strip() if label.startswith("建议") else label
        lines.append("**建议：%s**（code=`%s`）"
                     % (display, suggestion.get("code") or "—"))
        lines.append("")
        if suggestion.get("conclusion"):
            lines.append("- 结论：%s" % suggestion["conclusion"])
        if suggestion.get("source"):
            lines.append("- 决策来源：%s" % suggestion["source"])
        body = suggestion.get("body") or ""
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("**建议") or stripped.startswith("## "):
                continue
            if stripped.startswith("- 结论") or stripped.startswith("- 决策来源"):
                continue
            if stripped.startswith("- **L3 提示**"):
                continue
            lines.append(line)
    else:
        lines.append("- 建议缺失（receipt 草案缺失/解析失败）；runner 状态：%s"
                     % (runner.get("status") or "—"))
    lines.append("")
    lines.append("- **L3 提示**：Go（接入 merge 前置·观察不阻断）需**老田 L3 单独批准**；"
                 "阻断模式需另行评估误杀率；本终版模板**不构成批准**。")
    lines.append("")

    # 七、证据路径清单
    lines.append("## 七、证据路径清单")
    lines.append("")
    for path in receipt.get("evidence_paths") or []:
        lines.append("- %s" % path)
    lines.append("- runner 汇总：`%s`" % (runner.get("path") or "（缺失）"))
    lines.append("- receipt 草案：`%s`" % (receipt.get("path") or "（缺失）"))
    if confirm.get("path"):
        lines.append("- 事故人工确认：`%s`" % confirm["path"])
    lines.append("- 终版模板（本文件）：`%s`" % (rep.get("final_path") or "—"))
    lines.append("")

    # 八、待人工项
    lines.append("## 八、待人工项")
    lines.append("")
    risks = receipt.get("risks") or {}
    runner_warnings = []
    for name, step in (runner.get("steps") or {}).items():
        if step.get("ok") is False:
            runner_warnings.append("runner 步骤失败：%s（rc=%s）"
                                   % (name, step.get("rc")))
    todo: list[str] = []
    todo.append("**老田 L3 签字位**：Go/No-Go 最终决策（本模板不构成批准）")
    todo.append("**赵云 3/3 确认**：抽查 3 单一致（签名位见 §九）")
    if not confirm.get("confirmed"):
        todo.append("**事故人工确认缺失**：补 `incidents_confirm_*.md` 并在 §五 引用")
    if not _num((coverage.get("risk_weighted") or {}).get("pct")):
        todo.append("风险加权覆盖率缺失：补跑 `tools/huangzhong_risk_coverage.py --json`")
    for line in _cap(todo, 10):
        lines.append("- %s" % line)
    if risks.get("todo"):
        lines.append("")
        lines.append("**草案待人工项（搬运）**")
        lines.append("")
        for idx, item in enumerate(_cap(risks.get("todo") or [], 10), 1):
            lines.append("%d. %s" % (idx, item))
    lines.append("")
    lines.append("**fail-visible / 告警**")
    lines.append("")
    risk_warnings = list(risks.get("warnings") or []) + runner_warnings
    if risk_warnings:
        for warn in _cap(risk_warnings, 15):
            lines.append("- %s" % warn)
    else:
        lines.append("- 无（输入均已解析）" )
    for warn in _cap(warnings, 15):
        lines.append("- %s" % warn)
    lines.append("")

    # 九、签字表
    lines.append("## 九、签字表（终版模板，未签字前不生效）")
    lines.append("")
    lines.append("| 角色 | 职责 | 签字位 | 签字时间 |")
    lines.append("|---|---|---|---|")
    for role, duty, slot in _sign_roles(date_text):
        lines.append("| %s | %s | %s | — |" % (_cell(role), _cell(duty),
                                                _cell(slot)))
    lines.append("")
    lines.append("- **本文件为终版模板，未签字前不生效**；签字后归档为 "
                 "`deliverables/%s_黄忠观察期复核回执.md`（本文件即该路径）。"
                 % date_text)
    lines.append("- 关联：认证标准 v2、runbook v6 §十、一键流程 v1.1 §六、"
                 "`deliverables/%s_黄忠复核_READ_ME_FIRST.md`。" % date_text)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("版本：%s 终版模板 ｜ 生成器：`tools/%s` %s ｜ "
                 "工单：WO-HZ-1104FINAL-20261101 ｜ 状态：**未签字，不生效**"
                 % (date_text, TOOL_NAME, VERSION))
    lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ 编排

def discover_inputs(dirs: list[Path],
                    date_text: str = DEFAULT_TARGET_DATE) -> dict:
    """自动发现三类输入；date_text 决定汇总/草案文件名前缀（默认 11-04=v1 口径）。"""
    candidates = []
    seen = set()
    for directory in dirs:
        if directory is None:
            continue
        key = str(Path(directory).resolve())
        if key in seen:
            continue
        seen.add(key)
        candidates.append(Path(directory))
    summary_globs = ("%s_黄忠复核执行汇总_*.md" % date_text,
                     "%s_黄忠复核执行汇总_*.json" % date_text)
    if date_text == DEFAULT_TARGET_DATE:
        receipt_globs = (RECEIPT_GLOB,)   # 默认路径：保持 v1 glob 完全不变
    else:
        # 显式目标日期：优先同日草案，回退通用 glob（兼容历史命名）
        receipt_globs = ("%s_黄忠观察期复核回执_草案_*.md" % date_text,
                         RECEIPT_GLOB)
    summary = _latest_in(candidates, summary_globs)
    receipt = _latest_in(candidates, receipt_globs)
    confirm = _latest_in(candidates, CONFIRM_GLOBS)
    return {"summary": summary, "receipt": receipt, "confirm": confirm,
            "dirs": [str(d) for d in candidates]}


def run(summary_path: Path | str | None = None,
        receipt_path: Path | str | None = None,
        confirm_path: Path | str | None = None,
        out_dir: Path | str | None = None,
        work_dir: Path | str | None = None,
        repo_root: Path | str | None = None,
        now: dt.datetime | None = None,
        target_date: str | None = None,
        force: bool = False,
        write: bool = True) -> dict:
    """合并三类输入并生成终版回执模板（返回 JSON 摘要）。

    target_date 缺省 = 2026-11-04（v1 行为：终版文件名/汇总 glob/签字位日期不变）；
    显式 '2026-11-10' 时按目标日期发现输入并输出 11-10 终版回执模板（11-10 复算）。
    """
    clock = now or _now()
    date_text = _target_date_text(target_date)
    repo = Path(repo_root) if repo_root else ROOT
    work = Path(work_dir) if work_dir else repo / "work" / "huangzhong"
    out = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    final_path = out / ("%s_黄忠观察期复核回执.md" % date_text)
    rep: dict = {
        "ok": True,
        "tool": TOOL_NAME,
        "version": VERSION,
        "generated_at": _iso(clock),
        "target_date": date_text,
        "repo_root": str(repo),
        "work_dir": str(work),
        "out_dir": str(out),
        "final_path": str(final_path),
        "written": False,
        "force": bool(force),
        "read_only_inputs": True,
        "inputs": {},
        "context": {},
        "warnings": [],
        "error": None,
    }
    warnings: list[str] = []

    # ---- 显式输入优先，否则自动发现（out → 默认 deliverables → work）
    explicit_summary = bool(summary_path)
    explicit_receipt = bool(receipt_path)
    if summary_path:
        summary_p = Path(summary_path)
    else:
        summary_p = None
    if receipt_path:
        receipt_p = Path(receipt_path)
    else:
        receipt_p = None
    if confirm_path:
        confirm_p = Path(confirm_path)
    else:
        confirm_p = None
    if not (summary_p or receipt_p or confirm_p):
        discovered = discover_inputs([out, DEFAULT_OUT_DIR, work], date_text)
        summary_p = discovered["summary"]
        receipt_p = discovered["receipt"]
        confirm_p = discovered["confirm"]
        rep["inputs"]["discovered_from"] = discovered["dirs"]
    else:
        # 只补缺的（显式给的优先）
        discovered = discover_inputs([out, DEFAULT_OUT_DIR, work], date_text)
        if summary_p is None:
            summary_p = discovered["summary"]
        if receipt_p is None:
            receipt_p = discovered["receipt"]
        if confirm_p is None:
            confirm_p = discovered["confirm"]
        rep["inputs"]["discovered_from"] = discovered["dirs"]

    inputs_meta: dict = {"summary": None, "receipt": None, "confirm": None}
    summary_row = {"path": str(summary_p) if summary_p else None,
                   "status": MISSING, "kind": None, "generated_at": None,
                   "steps": {}, "values": {}, "error": None, "warnings": []}
    receipt_row = {"path": str(receipt_p) if receipt_p else None,
                   "status": MISSING, "error": None}
    confirm_row = {"path": str(confirm_p) if confirm_p else None,
                   "status": MISSING, "error": None, "confirmed": False}

    if summary_p is not None:
        if not summary_p.is_file():
            rep["ok"] = False
            rep["error"] = "汇总文件不存在：%s" % summary_p
            return rep
        summary_row = parse_summary(summary_p)
        if summary_row.get("status") != OK:
            warnings.append("汇总解析失败：%s（%s）"
                            % (summary_p.name, summary_row.get("error")))
        inputs_meta["summary"] = summary_row.get("status")
    else:
        warnings.append("未找到 runner 汇总（%s_黄忠复核执行汇总_*.md）"
                        % date_text)

    if receipt_p is not None:
        if not receipt_p.is_file():
            rep["ok"] = False
            rep["error"] = "receipt 草案不存在：%s" % receipt_p
            return rep
        receipt_row = parse_receipt(receipt_p)
        if receipt_row.get("status") != OK:
            warnings.append("receipt 草案解析失败：%s（%s）"
                            % (receipt_p.name, receipt_row.get("error")))
        inputs_meta["receipt"] = receipt_row.get("status")
    else:
        warnings.append("未找到 receipt 草案（*_黄忠观察期复核回执_草案_*.md）")

    if confirm_p is not None:
        if not confirm_p.is_file():
            if confirm_path:
                rep["ok"] = False
                rep["error"] = "人工确认文件不存在：%s" % confirm_p
                return rep
            warnings.append("incidents 人工确认文件不存在：%s" % confirm_p)
        else:
            confirm_row = parse_confirm(confirm_p)
            if confirm_row.get("status") != OK:
                warnings.append("人工确认解析失败：%s（%s）"
                                % (confirm_p.name, confirm_row.get("error")))
            inputs_meta["confirm"] = confirm_row.get("status")
            if not confirm_row.get("confirmed"):
                warnings.append("人工确认文件未明确「无已知事故」：%s" % confirm_p.name)
    else:
        warnings.append("未找到 incidents 人工确认文件（incidents_confirm_*.md）")

    # 显式路径缺失直接失败（fail-visible 由调用方处理）
    if (explicit_summary and summary_row.get("status") == MISSING) or \
            (explicit_receipt and receipt_row.get("status") == MISSING):
        rep["ok"] = False
        rep["error"] = "显式指定的输入不可用"
        return rep

    # 核心数据至少一份：汇总或草案
    has_summary = summary_row.get("status") == OK
    has_receipt = receipt_row.get("status") == OK
    if not has_summary and not has_receipt:
        rep["ok"] = False
        rep["error"] = "数据缺失：runner 汇总与 receipt 草案均缺失/不可读"
        return rep

    # 汇总与草案批次不一致提示
    values = summary_row.get("values") or {}
    draft_ref = values.get("receipt_draft")
    if has_receipt and draft_ref:
        ref_name = Path(str(draft_ref).replace("\\", "/")).name
        if ref_name and ref_name != receipt_row.get("path") and \
                Path(receipt_row["path"]).name != ref_name:
            warnings.append("汇总引用的草案（%s）与最新草案（%s）不同批次，"
                            "按最新草案生成" % (ref_name,
                                              Path(receipt_row["path"]).name))

    rep["inputs"] = {"summary": summary_row.get("path"),
                     "receipt": receipt_row.get("path"),
                     "confirm": confirm_row.get("path"),
                     "summary_status": inputs_meta["summary"],
                     "receipt_status": inputs_meta["receipt"],
                     "confirm_status": inputs_meta["confirm"],
                     "discovered_from": rep["inputs"].get("discovered_from")}
    rep["context"] = build_context(summary_row if has_summary else None,
                                   receipt_row if has_receipt else None,
                                   confirm_row)
    ctx = rep["context"]
    rep["three_coverage"] = ctx["coverage"]
    rep["six_overall"] = ctx["receipt"]["overall"]
    rep["suggestion"] = ctx["receipt"]["suggestion"]
    rep["maturity"] = ctx["receipt"]["maturity"]
    # 解析质量 fail-visible（不阻塞生成，但要标出缺口）
    if not _num((ctx["coverage"].get("risk_weighted") or {}).get("pct")):
        warnings.append("风险加权覆盖率缺失：runner 汇总未提供 risk_coverage 结果"
                        "（需补跑 tools/huangzhong_risk_coverage.py --json）")
    if has_receipt and (not receipt_row.get("sections")
                        or (not receipt_row.get("six_items")
                            and not (receipt_row.get("suggestion") or {}).get("body"))):
        warnings.append("receipt 草案解析异常：未解析到九段/六条内容"
                        "（六条表回退汇总口径，需人工补齐）")
    if has_summary and not (summary_row.get("steps") or {}):
        warnings.append("runner 汇总解析异常：未解析到步骤表（三口径部分字段可能缺失）")
    rep["warnings"] = warnings

    # ---- 已存在保护
    if final_path.exists() and not force:
        rep["ok"] = False
        rep["error"] = ("终版回执已存在：%s（未覆盖；如需重建请加 --force）"
                        % final_path)
        _log("已存在，未覆盖：%s" % final_path)
        return rep

    if write:
        try:
            out.mkdir(parents=True, exist_ok=True)
            final_path.write_text(render_md(rep), encoding="utf-8")
            rep["written"] = True
            _log("终版回执模板已落盘：%s" % final_path)
        except OSError as exc:
            rep["ok"] = False
            rep["error"] = "终版回执落盘失败：%s: %s" % (type(exc).__name__, exc)
            _log(rep["error"])
    return rep


def _summary(rep: dict) -> dict:
    ctx = rep.get("context") or {}
    receipt = ctx.get("receipt") or {}
    coverage = rep.get("three_coverage") or {}
    return {
        "ok": bool(rep.get("ok")),
        "tool": rep.get("tool"),
        "version": rep.get("version"),
        "generated_at": rep.get("generated_at"),
        "final_path": rep.get("final_path"),
        "written": rep.get("written"),
        "force": rep.get("force"),
        "read_only_inputs": rep.get("read_only_inputs"),
        "inputs": rep.get("inputs"),
        "runner_status": (ctx.get("runner") or {}).get("status"),
        "runner_generated_at": (ctx.get("runner") or {}).get("generated_at"),
        "receipt_generated_at": receipt.get("generated_at"),
        "target_time": receipt.get("target_time"),
        "six_overall": rep.get("six_overall"),
        "six_items_count": len(receipt.get("six_items") or []),
        "maturity": rep.get("maturity"),
        "incidents": receipt.get("incidents"),
        "confirm": {"path": (ctx.get("confirm") or {}).get("path"),
                    "status": (ctx.get("confirm") or {}).get("status"),
                    "confirmed": (ctx.get("confirm") or {}).get("confirmed"),
                    "conclusion": (ctx.get("confirm") or {}).get("conclusion")},
        "three_coverage": coverage,
        "suggestion": rep.get("suggestion"),
        "signature_rows": len(SIGN_ROLES),
        "sections": list(SECTION_KEYS),
        "warnings": rep.get("warnings") or [],
        "error": rep.get("error"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="黄忠终版回执生成器（汇总+草案+确认 → 九段终版模板；"
                    "默认目标 2026-11-04）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    ap.add_argument("--summary", default="",
                    help="runner 汇总文件（md 或 json；默认自动发现最新）")
    ap.add_argument("--receipt", default="",
                    help="receipt 草案 md（默认自动发现最新）")
    ap.add_argument("--confirm", default="",
                    help="incidents 人工确认 md（默认自动发现最新）")
    ap.add_argument("--out-dir", default="",
                    help="终版回执输出目录（默认 C:\\项目\\deliverables）")
    ap.add_argument("--work-dir", default="",
                    help="数据目录（默认 <repo-root>/work/huangzhong）")
    ap.add_argument("--repo-root", default="",
                    help="仓库根（默认工具所在仓库；worktree 内用主仓数据时显式指定）")
    ap.add_argument("--now", default="",
                    help="固定生成时钟（ISO；默认当前时间，测试/复算用）")
    ap.add_argument("--target-date", default="",
                    help="显式目标日期（默认 2026-11-04；支持 2026-11-10 或 "
                         "ISO 日期时间；11-10 复算用）")
    ap.add_argument("--force", action="store_true",
                    help="终版回执已存在时覆盖（默认不覆盖）")
    ap.add_argument("--no-write", action="store_true",
                    help="只解析并输出摘要，不落终版 md（只读冒烟用）")
    args = ap.parse_args(argv)
    fixed_now = None
    if args.now:
        fixed_now = _parse_ts(args.now)
        if fixed_now is None:
            print("参数错误：--now 无法解析：%r" % args.now, file=sys.stderr)
            return 2
    try:
        target_date = _target_date_text(args.target_date)
    except ValueError as exc:
        print("参数错误：%s" % exc, file=sys.stderr)
        return 2
    rep = run(summary_path=args.summary or None,
              receipt_path=args.receipt or None,
              confirm_path=args.confirm or None,
              out_dir=args.out_dir or None,
              work_dir=args.work_dir or None,
              repo_root=args.repo_root or None,
              now=fixed_now,
              target_date=target_date,
              force=args.force,
              write=not args.no_write)
    summary = _summary(rep)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    else:
        if rep.get("ok"):
            print("%s 终版回执模板：%s"
                  % (_target_label(target_date), rep.get("final_path") or "—"))
            print("  写入：%s｜六条：%s/%s｜三口径：runs/merge=%s unique=%s 风险加权=%s"
                  % (rep.get("written"),
                     (rep.get("six_overall") or {}).get("passed"),
                     (rep.get("six_overall") or {}).get("total"),
                     _fmt_pct(((rep.get("three_coverage") or {}).get("run_merge")
                               or {}).get("pct")),
                     _fmt_pct(((rep.get("three_coverage") or {}).get("unique")
                               or {}).get("pct")),
                     _fmt_pct(((rep.get("three_coverage") or {}).get("risk_weighted")
                               or {}).get("pct"))))
        else:
            print("生成失败：%s" % (rep.get("error") or "未知错误"), file=sys.stderr)
        for warn in _cap(rep.get("warnings") or [], 10):
            print("  [warn] %s" % warn)
    return 0 if rep.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
