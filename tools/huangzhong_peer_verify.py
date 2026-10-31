# -*- coding: utf-8 -*-
"""黄忠跨 Agent 互验报告（只读）：黄忠验收 vs 赵云抽查 vs 关羽修复批次。

用途：把「双守门官」的一致性变成可复核报告——对赵云抽查样本（
`work/huangzhong/audit_sample_3.json`）逐个 commit 交叉核对黄忠验收单
（`work/huangzhong/acceptance_*.json`）的 verdict，输出「一致 / 分歧 / 缺数据」+
分歧明细 + 数据源状态 + 关羽修复车道（repair_lane）探测结果。

数据源（缺失/坏文件均 fail-visible 标注，不编造）：
  · 赵云抽查：work/huangzhong/audit_sample_3.json（支持 samples/records/units 列表；
    样本含 sha / file / verdict|consistent|checks 等字段）；
  · 黄忠验收：work/huangzhong/acceptance_*.json（同 commit 多单取 generated_at 最新）；
  · 演练台账：work/huangzhong/drills/drill_ledger_*.json（标注匹配到演练单的情况）；
  · 关羽修复车道：按 REPAIR_CANDIDATES 探测 `agent_platform/data/repair_lane/`、
    `work/p1/repair_lane/`、`agent_platform/data/repair/`（实际 v1 路径，含 lane.json），
    存在才读，读不到如实报错。

判定口径：
  · 赵云判定：样本 verdict/judgment/result（PASS/FAIL 等）→ consistent bool →
    pass bool → checks（verdict_pass/all）逐级回退；无法解析 → 缺数据；
  · 黄忠判定：acceptance `verdict`（PASS vs 非 PASS）；
  · 一致 = 双方同为 PASS 或同为非 PASS；分歧 = 一方 PASS 一方非 PASS；
    样本无 sha 时按 file（文件名/sha8）尽力匹配，并在 missing_sha 列表如实标注。

输出：JSON（--json）+ md 报告（默认落 C:\\项目\\deliverables，
`2026-10-31_黄忠跨Agent互验_<ts>.md`；--out-dir 可改）。**只读**：不改验收/抽查/演练数据、
不写 work/。退出码：0=ok / 1=warn / 2=fail（分歧或核心数据源缺失=fail）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK_SUBDIR = Path("work") / "huangzhong"
DEFAULT_OUT_DIR = Path(r"C:\项目\deliverables")
AUDIT_NAME = "audit_sample_3.json"
REPAIR_CANDIDATES = (
    Path("agent_platform") / "data" / "repair_lane",
    Path("work") / "p1" / "repair_lane",
    Path("agent_platform") / "data" / "repair",   # 关羽修复车道 v1 实际数据路径
)
TOOL_VERSION = "v1 (2026-10-31)"
_SHA_RE = re.compile(r"^acceptance_([0-9a-fA-F]{8,40})\.json$")


# ------------------------------------------------------------------ 基础工具

def _now() -> dt.datetime:
    """当前时间（本机时区 aware）；测试可注入 now。"""
    return dt.datetime.now().astimezone()


def _iso(ts: dt.datetime | None) -> str | None:
    return ts.isoformat(timespec="seconds") if isinstance(ts, dt.datetime) else None


def _parse_ts(raw) -> dt.datetime | None:
    """解析时间戳（ISO8601/空格/斜杠格式/epoch 秒）；无时区按本机时区解释。"""
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
    if not s or s.upper() in ("N/A", "NA", "-", "无"):
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    parsed = None
    try:
        parsed = dt.datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
                    "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
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


def _read_text(path: Path) -> tuple[str | None, str | None]:
    try:
        return Path(path).read_text(encoding="utf-8-sig"), None
    except FileNotFoundError:
        return None, "missing"
    except OSError as e:
        return None, "%s: %s" % (type(e).__name__, e)


def _read_json(path: Path) -> tuple[object | None, str | None]:
    text, err = _read_text(path)
    if text is None:
        return None, err or "unreadable"
    try:
        return json.loads(text), None
    except ValueError as e:
        return None, "JSON 解析失败: %s" % e


def _sha8(sha) -> str:
    return (sha or "").strip().lower()[:8] if isinstance(sha, str) else ""


def _norm_sha(raw) -> str:
    """从字段提取 40/8 位 sha 字符串（容忍 dict commit 结构）。"""
    if isinstance(raw, dict):
        raw = raw.get("sha") or raw.get("commit") or raw.get("id")
    if isinstance(raw, str):
        s = raw.strip().lower()
        if len(s) >= 8 and all(c in "0123456789abcdef" for c in s):
            return s
    return ""


def _truthy(v) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "yes", "y", "pass", "通过", "一致", "ok"):
            return True
        if s in ("false", "0", "no", "n", "fail", "不通过", "不一致"):
            return False
    return None


def _judge_from_text(v) -> str | None:
    """显式 verdict/judgment 文本 → PASS / FAIL / None。"""
    truth = _truthy(v)
    if truth is not None and not isinstance(v, str):
        return "PASS" if truth else "FAIL"
    if not isinstance(v, str):
        return None
    s = v.strip().lower()
    if s in ("pass", "passed", "ok", "通过", "一致", "有效", "confirmed"):
        return "PASS"
    if s in ("fail", "failed", "reject", "observe", "不通过", "不一致", "无效",
             "作废", "fail_reject", "rejected"):
        return "FAIL"
    return None


# ---------------------------------------------------------- 赵云抽查（audit）

def load_audit(work: Path) -> dict:
    """读 audit_sample_3.json → {path, exists, ok, error, samples, meta}。"""
    path = Path(work) / AUDIT_NAME
    out = {"path": str(path), "exists": path.exists(), "ok": False, "error": None,
           "samples": [], "meta": {}}
    if not path.exists():
        out["error"] = "缺失（赵云抽查记录未落盘）"
        return out
    data, err = _read_json(path)
    if err or data is None:
        out["error"] = err or "空文件"
        return out
    raw_samples, meta = [], {}
    if isinstance(data, list):
        raw_samples = data
    elif isinstance(data, dict):
        for key in ("samples", "records", "units", "items", "checks_results"):
            if isinstance(data.get(key), list):
                raw_samples = data[key]
                break
        meta = {k: v for k, v in data.items() if k != "samples"}
    else:
        out["error"] = "顶层不是对象/数组"
        return out
    samples = [_norm_audit_sample(s, i) for i, s in enumerate(raw_samples)
               if isinstance(s, dict)]
    out.update({"ok": True, "samples": samples, "meta": meta})
    if not samples:
        out["error"] = "samples 为空或无可解析对象"
    return out


def _audit_verdict(raw: dict) -> tuple[str | None, str | None]:
    """赵云样本判定 → (PASS/FAIL/None, 来源字段)。"""
    for key in ("audit_verdict", "verdict", "judgment", "audit_result", "result"):
        if key in raw:
            got = _judge_from_text(raw.get(key))
            if got:
                return got, key
    consistent = _truthy(raw.get("consistent"))
    if consistent is not None:
        return ("PASS" if consistent else "FAIL"), "consistent"
    passed = _truthy(raw.get("pass"))
    if passed is not None:
        return ("PASS" if passed else "FAIL"), "pass"
    checks = raw.get("checks")
    if isinstance(checks, dict) and checks:
        values = [_truthy(v) for v in checks.values() if isinstance(
            v, (bool, int, float, str))]
        values = [v for v in values if v is not None]
        if values:
            return ("PASS" if all(values) else "FAIL"), "checks"
    return None, None


def _norm_audit_sample(raw: dict, idx: int) -> dict:
    raw_sha = raw.get("sha") or raw.get("sha8") or raw.get("commit") \
        or raw.get("target_commit")
    sha = _norm_sha(raw_sha)
    file = raw.get("file") or raw.get("acceptance_report") or raw.get("path") \
        or raw.get("report")
    file = Path(str(file)).name if file else ""
    sha_missing = not sha          # 原始样本无 sha → 如实标注（即使按文件名推断）
    inferred = False
    if not sha and file:
        m = _SHA_RE.match(file)
        if m:
            sha = m.group(1).lower()
            inferred = True         # sha 由文件名推断（标注 sha_inferred）
    verdict, verdict_source = _audit_verdict(raw)
    checks = raw.get("checks") if isinstance(raw.get("checks"), dict) else {}
    notes = raw.get("note") or raw.get("notes") or raw.get("detail") or ""
    return {"index": idx + 1, "sha": sha, "sha8": _sha8(sha), "file": file,
            "sha_missing": sha_missing, "sha_inferred": inferred,
            "audit_verdict": verdict, "audit_verdict_source": verdict_source,
            "consistent": _truthy(raw.get("consistent")), "checks": checks,
            "notes": str(notes)[:300]}


# --------------------------------------------------------------- 黄忠验收单

def _latest_of(entries: list[dict]) -> dict | None:
    if not entries:
        return None
    return max(entries, key=lambda e: e.get("ts") or dt.datetime.min.replace(
        tzinfo=dt.timezone.utc))


def load_acceptance(work: Path) -> dict:
    """读 acceptance_*.json → 按 sha 分组（同 commit 取最新）+ by_file。"""
    work = Path(work)
    files = sorted(work.glob("acceptance_*.json"))
    entries, invalid = [], []
    for p in files:
        data, err = _read_json(p)
        if err or not isinstance(data, dict):
            invalid.append({"file": p.name, "error": err or "JSON 顶层不是对象"})
            continue
        commit = data.get("commit") if isinstance(data.get("commit"), dict) else {}
        sha = (commit.get("sha") or "").strip().lower() \
            if isinstance(commit.get("sha"), str) else ""
        ts = _parse_ts(data.get("generated_at"))
        if ts is None:
            try:
                ts = dt.datetime.fromtimestamp(p.stat().st_mtime,
                                               tz=dt.timezone.utc)
            except OSError:
                ts = None
        entries.append({
            "file": p.name, "path": str(p), "sha": sha, "sha8": _sha8(sha),
            "verdict": (data.get("verdict") or "").upper()
            if isinstance(data.get("verdict"), str) else "",
            "generated_at": data.get("generated_at"), "ts": ts,
            "drill": bool(data.get("drill")) or data.get("mode") == "drill",
            "evidence_scope": data.get("evidence_scope"),
        })
    by_sha: dict[str, list[dict]] = {}
    for e in entries:
        if e["sha"]:
            by_sha.setdefault(e["sha"], []).append(e)
    latest_by_sha = {sha: _latest_of(rows) for sha, rows in by_sha.items()}
    by_file = {e["file"].lower(): e for e in entries}
    return {"files": [p.name for p in files], "entries": entries,
            "invalid": invalid, "by_sha": latest_by_sha, "by_file": by_file,
            "unique_commits": len(latest_by_sha),
            "duplicates": sum(len(v) - 1 for v in by_sha.values())}


# ------------------------------------------------------- 演练台账（数据源状态）

def load_drill_ledger(work: Path) -> dict:
    work = Path(work)
    paths: list[Path] = []
    for d in (work / "drills", work):
        if d.is_dir():
            for p in sorted(d.glob("drill_ledger_*.json")):
                if all(p.resolve() != q.resolve() for q in paths):
                    paths.append(p)
    files, entries, bad = [], 0, []
    for p in paths:
        data, err = _read_json(p)
        if err is not None:
            bad.append({"file": p.name, "error": err})
            continue
        rows = data if isinstance(data, list) else (
            data.get("drills") if isinstance(data, dict) else None)
        if not isinstance(rows, list):
            bad.append({"file": p.name, "error": "未找到 drills 数组"})
            continue
        files.append(p.name)
        entries += len(rows)
    return {"files": files, "entries": entries, "bad": bad}


# ------------------------------------------------- 关羽修复车道（repair_lane）

def _parse_lane_file(path: Path) -> dict:
    data, err = _read_json(path)
    if err or data is None:
        return {"ok": False, "error": err or "空文件", "file": path.name,
                "batches": []}
    if isinstance(data, list):
        return {"ok": True, "schema": None, "file": path.name,
                "batches": [b for b in data if isinstance(b, dict)], "error": None}
    if isinstance(data, dict):
        batches = data.get("batches")
        if not isinstance(batches, list):
            return {"ok": False, "error": "未找到 batches 数组", "file": path.name,
                    "batches": []}
        return {"ok": True, "schema": data.get("schema"), "file": path.name,
                "updated_at": data.get("updated_at"),
                "batches": [b for b in batches if isinstance(b, dict)],
                "error": None}
    return {"ok": False, "error": "顶层不是对象/数组", "file": path.name,
            "batches": []}


def probe_repair_lane(repo_root: Path) -> dict:
    """按候选路径探测关羽修复车道数据（存在才读；读不到如实报错）。"""
    repo_root = Path(repo_root)
    probes = []
    for rel in REPAIR_CANDIDATES:
        p = repo_root / rel
        info = {"path": str(p), "rel": rel.as_posix(),
                "exists": p.exists(),
                "kind": "dir" if p.is_dir() else "file" if p.is_file() else None,
                "files": []}
        if p.is_dir():
            try:
                info["files"] = sorted(f.name for f in p.iterdir()
                                       if f.is_file() and f.suffix.lower()
                                       in (".json", ".jsonl"))
            except OSError as e:
                info["error"] = "%s: %s" % (type(e).__name__, e)
        probes.append(info)
    found, source, parsed = False, None, None
    for info in probes:
        if not info["exists"]:
            continue
        p = Path(info["path"])
        candidates: list[Path] = []
        if p.is_file():
            candidates = [p]
        else:
            for name in ("lane.json", "repair_lane.json"):
                if (p / name).exists():
                    candidates.append(p / name)
            candidates += sorted(p.glob("*.json"))
            candidates += sorted(p.glob("*.jsonl"))
            dedup: list[Path] = []
            for c in candidates:
                if all(c.resolve() != q.resolve() for q in dedup):
                    dedup.append(c)
            candidates = dedup
        for c in candidates:
            got = _parse_lane_file(c) if c.suffix.lower() == ".json" else \
                {"ok": False, "error": "jsonl 暂不解析", "file": c.name,
                 "batches": []}
            if got.get("ok"):
                found, source, parsed = True, str(c), got
                break
        if found:
            break
    result = {"candidates": probes, "found": found, "source": source,
              "schema": parsed.get("schema") if parsed else None,
              "batches": [], "count": 0, "status_counts": {},
              "latest_created_at": None, "modules": [], "errors": []}
    if not parsed:
        result["errors"] = ["未发现可读的 repair_lane 数据（候选路径均缺失或不可读）"]
        return result
    batches = parsed.get("batches") or []
    status_counts: dict[str, int] = {}
    modules: list[str] = []
    latest = None
    for b in batches:
        status = b.get("status") or "pending"
        status_counts[status] = status_counts.get(status, 0) + 1
        mod = b.get("module")
        if isinstance(mod, str) and mod and mod not in modules:
            modules.append(mod)
        ts = _parse_ts(b.get("last_run_at") or b.get("created_at"))
        if ts is not None and (latest is None or ts > latest):
            latest = ts
    result.update({
        "batches": batches[-20:], "count": len(batches),
        "status_counts": status_counts, "latest_created_at": _iso(latest),
        "modules": modules[:50], "updated_at": parsed.get("updated_at"),
        "error": None,
    })
    return result


# ------------------------------------------------------------------ 交叉核对

def compare(audit: dict, acc: dict, warnings: list[str]) -> dict:
    """逐样本交叉核对 → rows + summary。"""
    rows = []
    for s in audit.get("samples") or []:
        target, basis, missing_reason = None, None, None
        if s["sha"]:
            target = acc["by_sha"].get(s["sha"].lower())
            basis = "sha"
            if target is None and len(s["sha"]) < 40:
                cands = [e for k, e in acc["by_sha"].items()
                         if k.startswith(s["sha"].lower())]
                if len(cands) == 1:
                    target = cands[0]
        if target is None and s["file"]:
            target = acc["by_file"].get(s["file"].lower())
            basis = basis or "file"
        if target is None:
            missing_reason = ("acceptance_not_found" if (s["sha"] or s["file"])
                              else "no_sha_no_file")
        audit_v = s.get("audit_verdict")
        huang_v = target["verdict"] if target else None
        kind, agreement, reason = "missing", "缺数据", missing_reason
        if target is not None and target.get("drill"):
            kind, agreement = "missing", "缺数据"
            reason = "matched_drill_sheet"
            warnings.append("审计样本 #%d 匹配到演练单（drill=true）：%s"
                            % (s["index"], target["file"]))
        elif audit_v is None:
            kind, agreement, reason = "missing", "缺数据", "audit_judgment_missing"
        elif target is None:
            kind, agreement = "missing", "缺数据"
        else:
            audit_pass = audit_v == "PASS"
            huang_pass = huang_v == "PASS"
            if audit_pass == huang_pass:
                kind, agreement = "agree", "一致"
            else:
                kind, agreement = "disagree", "分歧"
        row = {
            "index": s["index"], "sha8": s["sha8"], "sha": s["sha"],
            "audit_file": s["file"], "audit_verdict": audit_v,
            "audit_verdict_source": s.get("audit_verdict_source"),
            "sha_missing": s.get("sha_missing"), "sha_inferred": s.get("sha_inferred"),
            "huang_file": target["file"] if target else None,
            "huang_verdict": huang_v,
            "huang_generated_at": target.get("generated_at") if target else None,
            "match_basis": basis, "agreement": agreement, "kind": kind,
            "reason": reason,
            "evidence": (target.get("path") if target else None),
            "notes": s.get("notes") or "",
        }
        rows.append(row)
    agree = sum(1 for r in rows if r["kind"] == "agree")
    disagree = sum(1 for r in rows if r["kind"] == "disagree")
    missing = sum(1 for r in rows if r["kind"] == "missing")
    decidable = agree + disagree
    summary = {
        "total": len(rows), "agree": agree, "disagree": disagree,
        "missing": missing, "decidable": decidable,
        "agreement_rate": round(agree / decidable, 4) if decidable else None,
        "decidable_rate": round(decidable / len(rows), 4) if rows else None,
        "missing_sha": [{"index": r["index"], "file": r["audit_file"],
                         "sha": r["sha"] or None}
                        for r in rows if r.get("sha_missing")],
        "inferred_sha": [{"index": r["index"], "file": r["audit_file"],
                          "sha": r["sha"]}
                         for r in rows if r.get("sha_inferred")],
    }
    status = "ok"
    if disagree:
        status = "fail"
    elif missing or summary["missing_sha"] or not rows:
        status = "warn"
    return {"rows": rows, "summary": summary, "status": status}


# ------------------------------------------------------------------ 报告渲染

def _render_md(rep: dict) -> str:
    s = rep["summary"]
    src = rep["data_sources"]
    repair = src.get("repair_lane") or {}
    md = ["# 黄忠跨 Agent 互验报告（黄忠验收 vs 赵云抽查 vs 关羽修复批次）", "",
          "> 只读报告：交叉核对 `audit_sample_3.json`（赵云抽查）与 `acceptance_*.json`"
          "（黄忠验收）的 verdict，并探测关羽修复车道（repair_lane）；"
          "不改验收/抽查/演练数据、不写 work/。",
          "> 生成时间：%s ｜ 数据根：`%s` ｜ 工作目录：`%s`"
          % (rep["generated_at"], rep["repo_root"], rep["work_dir"]),
          "", "## 一、总体结论", "",
          "- 状态：**%s**（%s）" % (rep["status"], rep["status_label"]),
          "- 样本：%d（一致 **%d** / 分歧 **%d** / 缺数据 **%d**）"
          % (s["total"], s["agree"], s["disagree"], s["missing"]),
          "- 一致率（可判定样本）：%s；可判定覆盖率：%s"
          % (("%.1f%%" % (s["agreement_rate"] * 100))
             if s["agreement_rate"] is not None else "不可判定",
             ("%.1f%%" % (s["decidable_rate"] * 100))
             if s["decidable_rate"] is not None else "不可判定"),
          "- 赵云抽查台账：%s（result=%s，seed=%s，reviewer=%s）"
          % (src["audit"].get("path"), src["audit"].get("result"),
             src["audit"].get("seed"), src["audit"].get("reviewer")),
          "", "## 二、样本交叉核对", "",
          "| # | commit | 审计样本 | 赵云判定 | 黄忠文件 | 黄忠 verdict | 结论 | 依据 |",
          "|---|---|---|---|---|---|---|---|"]
    for r in rep["rows"]:
        md.append("| %d | %s | %s | %s | %s | %s | %s | %s |"
                  % (r["index"], r["sha8"] or "-", r["audit_file"] or "-",
                     r["audit_verdict"] or "-", r["huang_file"] or "-",
                     r["huang_verdict"] or "-", r["agreement"],
                     r["match_basis"] or "-"))
    md += ["", "## 三、分歧清单", ""]
    disagree = [r for r in rep["rows"] if r["kind"] == "disagree"]
    if disagree:
        for r in disagree:
            md.append("- #%d %s：赵云=%s vs 黄忠=%s（%s → %s；依据：%s）"
                      % (r["index"], r["sha8"], r["audit_verdict"],
                         r["huang_verdict"], r["audit_file"], r["huang_file"],
                         r["evidence"]))
    else:
        md.append("- 无")
    md += ["", "## 四、缺数据 / 标注", ""]
    missing = [r for r in rep["rows"] if r["kind"] == "missing"]
    if missing:
        for r in missing:
            md.append("- #%d %s（%s）：reason=%s；审计判定=%s"
                      % (r["index"], r["sha8"] or "-", r["audit_file"] or "-",
                         r["reason"], r["audit_verdict"]))
    else:
        md.append("- 无")
    if s["missing_sha"]:
        md.append("")
        md.append("- **样本缺 commit sha（如实标注）**：%s"
                  % "；".join("#%d %s" % (x["index"], x["file"] or "-")
                             for x in s["missing_sha"]))
    if s["inferred_sha"]:
        md.append("- sha 由文件名推断：%s"
                  % "；".join("#%d %s→%s" % (x["index"], x["file"], x["sha"])
                             for x in s["inferred_sha"]))
    md += ["", "## 五、数据源状态", "",
           "- 赵云抽查：`%s`（exists=%s，ok=%s%s）"
           % (src["audit"].get("path"), src["audit"].get("exists"),
              src["audit"].get("ok"),
              "；error=%s" % src["audit"].get("error")
              if src["audit"].get("error") else ""),
           "- 黄忠验收：`%s`（%d 份 JSON，%d 个唯一 commit，重复 %d，坏文件 %d）"
           % (src["acceptance"].get("dir"), src["acceptance"].get("files"),
              src["acceptance"].get("unique_commits"),
              src["acceptance"].get("duplicates"),
              len(src["acceptance"].get("invalid") or [])),
           "- 演练台账：%s（%d 条）"
           % ("、".join(src["drill_ledger"].get("files") or []) or "无",
              src["drill_ledger"].get("entries", 0)),
           "- 关羽修复车道：found=%s；source=`%s`；批次 %d（%s）"
           % (repair.get("found"), repair.get("source"),
              repair.get("count", 0),
              "、".join("%s=%d" % (k, v)
                       for k, v in (repair.get("status_counts") or {}).items())
              or "无批次")]
    for probe in repair.get("candidates") or []:
        md.append("  - 探测 `%s`：exists=%s kind=%s"
                  % (probe.get("rel"), probe.get("exists"), probe.get("kind")))
    md += ["", "## 六、建议", ""]
    suggestions = rep.get("suggestions") or []
    md += ["- " + x for x in suggestions] if suggestions else ["- 无"]
    md += ["", "## 七、复跑命令", "",
           "```powershell",
           "cd C:\\项目\\老田的AI助手",
           "C:\\项目\\py312\\python.exe -X utf8 tools\\huangzhong_peer_verify.py --json",
           "```",
           "",
           "> 免责声明：本报告为只读互验材料，不构成最终认证结论；"
           "分歧单应作废重验（赵云/老田裁定），本公司不代签。", ""]
    return "\n".join(md)


# ------------------------------------------------------------------ 主流程

def run(work_dir: Path | str | None = None, repo_root: Path | str | None = None,
        out_dir: Path | str | None = None, now: dt.datetime | None = None,
        write_md: bool = True) -> dict:
    """执行跨 Agent 互验；默认写 md 到 out_dir（默认 deliverables）。"""
    now = now or _now()
    root = Path(repo_root) if repo_root else ROOT
    work = Path(work_dir) if work_dir else root / DEFAULT_WORK_SUBDIR
    out = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    warnings: list[str] = []

    audit = load_audit(work)
    acc = load_acceptance(work)
    drills = load_drill_ledger(work)
    repair = probe_repair_lane(root)

    if not audit["exists"]:
        warnings.append("赵云抽查记录缺失：%s" % audit["path"])
    elif not audit["ok"]:
        warnings.append("赵云抽查记录不可读：%s" % audit.get("error"))
    for row in acc["invalid"]:
        warnings.append("验收单坏 JSON 已跳过：%s（%s）" % (row["file"], row["error"]))
    if not acc["entries"]:
        warnings.append("黄忠验收单缺失/不可读：%s/acceptance_*.json" % work)
    if repair.get("errors"):
        warnings.extend(repair["errors"])

    cmp = compare(audit, acc, warnings)
    status = cmp["status"]
    reasons = []
    if not audit.get("ok"):
        status, reasons = "fail", reasons + ["赵云抽查数据源不可用"]
    elif not acc["entries"]:
        status, reasons = "fail", reasons + ["黄忠验收数据源缺失"]
    elif cmp["summary"]["disagree"]:
        status, reasons = "fail", reasons + ["存在 %d 处分歧" % cmp["summary"]["disagree"]]
    if status == "warn":
        reasons.append("存在缺数据/缺 sha 样本（见缺数据清单）")
    if acc["invalid"] and status == "ok":
        status = "warn"
        reasons.append("验收单存在坏 JSON %d 份" % len(acc["invalid"]))
    status_label = {"ok": "通过（一致）", "warn": "关注（缺数据/待补证）",
                    "fail": "失败（存在分歧或核心数据源缺失）"}[status]
    suggestions = []
    if cmp["summary"]["disagree"]:
        suggestions.append("分歧样本须作废重验：按分歧清单逐单复核（赵云/老田裁定），"
                           "补证后重跑本工具")
    if cmp["summary"]["missing"]:
        suggestions.append("缺数据样本先补 acceptance 或抽查记录（不可编造），再复跑")
    if cmp["summary"]["missing_sha"]:
        suggestions.append("抽查样本缺 commit sha：按 file/notes 匹配，建议在 "
                           "audit_sample_3.json 补 sha 后重跑（本报告已按 file 尽力匹配）")
    if not repair.get("found"):
        suggestions.append("未发现关羽修复车道数据（repair_lane 候选路径均缺失）："
                           "如修复批次已落盘请核对路径/格式")
    if not suggestions:
        suggestions.append("一致率 100%：抽查与验收判定一致，可作认证材料附件；"
                           "建议保持抽样复跑")

    rep = {
        "ok": status != "fail",
        "tool": "huangzhong_peer_verify.py",
        "version": TOOL_VERSION,
        "generated_at": _iso(now),
        "status": status,
        "status_label": status_label,
        "reasons": reasons,
        "repo_root": str(root),
        "work_dir": str(work),
        "read_only": True,
        "rows": cmp["rows"],
        "summary": cmp["summary"],
        "suggestions": suggestions,
        "data_sources": {
            "audit": {
                "path": audit["path"], "exists": audit["exists"],
                "ok": audit["ok"], "error": audit.get("error"),
                "schema": audit["meta"].get("schema"),
                "generated_at": audit["meta"].get("generated_at"),
                "seed": audit["meta"].get("seed"),
                "result": audit["meta"].get("result"),
                "total": audit["meta"].get("total"),
                "consistent": audit["meta"].get("consistent"),
                "executor": audit["meta"].get("executor"),
                "reviewer": audit["meta"].get("reviewer"),
            },
            "acceptance": {
                "dir": str(work), "files": len(acc["files"]),
                "entries": len(acc["entries"]),
                "unique_commits": acc["unique_commits"],
                "duplicates": acc["duplicates"],
                "invalid": acc["invalid"],
            },
            "drill_ledger": drills,
            "repair_lane": repair,
        },
        "warnings": warnings,
    }
    rep["report_md"] = None
    if write_md:
        try:
            out.mkdir(parents=True, exist_ok=True)
            mp = out / ("2026-10-31_黄忠跨Agent互验_%s.md"
                        % now.strftime("%Y%m%d_%H%M%S"))
            mp.write_text(_render_md(rep), encoding="utf-8")
            rep["report_md"] = str(mp)
        except OSError as e:
            warnings.append("md 报告写入失败：%s: %s" % (type(e).__name__, e))
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="黄忠跨 Agent 互验报告（黄忠验收 vs 赵云抽查 vs 关羽修复批次；只读）")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON")
    ap.add_argument("--work-dir", default="",
                    help="数据目录（默认 <repo-root>/work/huangzhong）")
    ap.add_argument("--repo-root", default="",
                    help="仓库根（默认工具所在仓库；worktree 内用主仓数据时显式指定）")
    ap.add_argument("--out-dir", default="",
                    help="md 报告输出目录（默认 C:\\项目\\deliverables）")
    args = ap.parse_args(argv)
    rep = run(work_dir=args.work_dir or None,
              repo_root=args.repo_root or None,
              out_dir=args.out_dir or None)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print("黄忠跨 Agent 互验：status=%s；样本 %d（一致 %d / 分歧 %d / 缺数据 %d）"
              % (rep["status"], rep["summary"]["total"], rep["summary"]["agree"],
                 rep["summary"]["disagree"], rep["summary"]["missing"]))
        for r in rep["rows"]:
            print("  #%d %s：赵云=%s 黄忠=%s → %s"
                  % (r["index"], r["sha8"] or "-", r["audit_verdict"] or "-",
                     r["huang_verdict"] or "-", r["agreement"]))
        if rep.get("report_md"):
            print("报告：%s" % rep["report_md"])
    return {"ok": 0, "warn": 1, "fail": 2}.get(rep["status"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
