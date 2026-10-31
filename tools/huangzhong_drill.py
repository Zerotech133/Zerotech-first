# -*- coding: utf-8 -*-
"""黄忠受控演练 helper（复演/复证；**默认 dry-run**，--apply 才写盘）。

用途：按确认确认字符串生成一次「演练用」OBSERVE/REJECT 证据单 + drill 台账条目，
供认证 v2 第②条（≥1 次真实受控拒收演练）在 11-04 复演/复证；工具本身**只读
证据、只写演练产物**，严禁污染生产日更/事件/正式验收历史。

行为边界（硬约束）：
  · 默认 dry-run：只输出「将写文件 + 台账条目」计划，不落盘；
  · `--apply` 才写：`work/huangzhong/acceptance_drill_<kind>_<YYYYMMDD>.json`
    + 追加 `work/huangzhong/drills/drill_ledger_<YYYYMMDD>.json` 条目；
  · **不写** daily.jsonl / events.jsonl / incidents.jsonl；
  · **不覆盖**既有 acceptance_*.json（目标文件名带 drill 且已存在→skip 幂等）；
  · 确认字符串错误 / `--dry-run` 与 `--apply` 同时给 / 前置不满足 / 台账不可读 →
    **拒绝且零写入**（fail-visible，不编造数据）。

受控构造成立判定（与 tools/huangzhong_accept.py 同一判定口径，不另立标准）：
  · 红队 pass_rate<0.8 或 基线 failed>0/error>0 → 真实证据将判 **REJECT**；
  · 证据缺失/不可读/超龄（>48h）→ 真实证据将判 **OBSERVE**；
  · 全部满足且新鲜 → PASS（不可用于演练，拒绝）。
  因此 `--kind observe` 要求预期 verdict=OBSERVE、`--kind reject` 要求=REJECT；
  默认读全局最新基线/红队，也可用 `--baseline` / `--redteam` 显式绑定真实历史证据
  （v2 §5.2 手段1/手段2）。

CLI：
  python -X utf8 tools/huangzhong_drill.py --kind observe --confirm DRILL-OBSERVE --dry-run --json
  python -X utf8 tools/huangzhong_drill.py --kind reject --confirm DRILL-REJECT \
      --redteam work\\p1\\redteam\\redteam_20261027_150225.json --apply --json
  扩展参数：--work-dir / --repo-root / --baseline / --redteam / --commit / --witness

退出码：0=成功（dry-run 计划 / 已写入 / 幂等跳过）；1=拒绝（零写入）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK_SUBDIR = Path("work") / "huangzhong"
DEFAULT_BASELINE = Path("agent_platform") / "data" / "test_baseline" / "latest.json"
DEFAULT_REDTEAM_DIR = Path("work") / "p1" / "redteam"
CONFIRM = {"observe": "DRILL-OBSERVE", "reject": "DRILL-REJECT"}
VOTE_BY_KIND = {"observe": "OBSERVE", "reject": "REJECT"}
MIN_REDTEAM_RATE = 0.8
MAX_EVIDENCE_AGE_HOURS = 48.0
WITNESS_DEFAULT = "待老田/赵云签字"
METHOD_BY_KIND = {
    "observe": "证据超龄/缺失·显式绑定（v2 §5.2 手段2）",
    "reject": "历史失败证据·显式绑定（v2 §5.2 手段1）",
}
TOOL_VERSION = "v1 (2026-10-31)"


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
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M", "%Y-%m-%d"):
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


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")


def _rel(repo_root: Path, path: Path) -> str:
    """路径→仓库相对 posix 形式（不在仓库内则返回绝对路径）。"""
    try:
        return Path(path).resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except ValueError:
        return str(path)


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ------------------------------------------------------------ 证据读取与预期判定

def _load_baseline(repo_root: Path, explicit: str, now: dt.datetime) -> dict:
    path = Path(explicit) if explicit else Path(repo_root) / DEFAULT_BASELINE
    ev = {"kind": "baseline", "path": str(path),
          "source": "explicit" if explicit else "default",
          "ok": False, "error": None, "generated_at": None,
          "age_hours": None, "stale": None, "failed": None, "error_count": None}
    data, err = _read_json(path)
    if err or not isinstance(data, dict):
        ev["error"] = err or "JSON 顶层不是对象"
        return ev
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    gen_raw = data.get("generated_at")
    gen = _parse_ts(gen_raw)
    age = round((now - gen).total_seconds() / 3600.0, 2) if gen is not None else None
    ev.update({
        "ok": True, "generated_at": gen_raw, "age_hours": age,
        "stale": (age > MAX_EVIDENCE_AGE_HOURS) if age is not None else None,
        "passed": summary.get("passed"),
        "failed": summary.get("failed"),
        "error_count": summary.get("error"),
        "summary": summary,
    })
    return ev


def _load_redteam(repo_root: Path, explicit: str, now: dt.datetime) -> dict:
    path = Path(explicit) if explicit else Path(repo_root) / DEFAULT_REDTEAM_DIR
    ev = {"kind": "redteam", "path": str(path),
          "source": "explicit" if explicit else "default",
          "ok": False, "error": None, "generated_at": None,
          "age_hours": None, "stale": None, "pass_rate": None,
          "total": None, "passed": None}
    target = path
    if path.is_dir():
        try:
            files = sorted(path.glob("redteam_*.json"), key=lambda p: p.stat().st_mtime)
        except OSError as e:
            ev["error"] = "redteam_unreadable: %s" % e
            return ev
        if not files:
            ev["error"] = "目录下无 redteam_*.json"
            return ev
        target = files[-1]
        ev["path"] = str(target)
    data, err = _read_json(target)
    if err or not isinstance(data, dict):
        ev["error"] = err or "JSON 顶层不是对象"
        return ev
    total, passed = data.get("total"), data.get("passed")
    rate = data.get("pass_rate")
    if rate is None and _num(total) and total:
        rate = round((passed or 0) / float(total), 4)
        ev["pass_rate_derived"] = True
    gen_raw = data.get("generated_at")
    gen = _parse_ts(gen_raw)
    age = round((now - gen).total_seconds() / 3600.0, 2) if gen is not None else None
    ev.update({
        "ok": True, "generated_at": gen_raw, "age_hours": age,
        "stale": (age > MAX_EVIDENCE_AGE_HOURS) if age is not None else None,
        "pass_rate": rate, "total": total, "passed": passed,
        "failed": data.get("failed"),
    })
    return ev


def analyze_evidence(repo_root: Path, baseline: str, redteam: str,
                     now: dt.datetime) -> dict:
    """按 accept 工具同一口径推导「真实证据将判 verdict」＝演练预期 verdict。"""
    base = _load_baseline(repo_root, baseline, now)
    rt = _load_redteam(repo_root, redteam, now)
    hard, soft = [], []
    if not base["ok"]:
        soft.append("测试基线缺失/不可读（%s）" % (base.get("error") or "unknown"))
    else:
        if base.get("stale") is True:
            soft.append("基线证据超龄 %.1f h > 上限 %.0f h（generated_at=%s）"
                        % (base["age_hours"], MAX_EVIDENCE_AGE_HOURS,
                           base.get("generated_at") or "-"))
        if (base.get("failed") or 0) > 0 or (base.get("error_count") or 0) > 0:
            hard.append("基线失败：failed=%s error=%s"
                        % (base.get("failed"), base.get("error_count")))
    if not rt["ok"]:
        soft.append("红队报告缺失/不可读（%s）" % (rt.get("error") or "unknown"))
    else:
        if rt.get("stale") is True:
            soft.append("红队证据超龄 %.1f h > 上限 %.0f h（generated_at=%s）"
                        % (rt["age_hours"], MAX_EVIDENCE_AGE_HOURS,
                           rt.get("generated_at") or "-"))
        rate = rt.get("pass_rate")
        if rate is None:
            soft.append("红队报告缺 pass_rate，无法判定")
        elif rate < MIN_REDTEAM_RATE:
            hard.append("红队通过率 %.0f%% < %.0f%%"
                        % (rate * 100, MIN_REDTEAM_RATE * 100))
    if hard:
        expected = "REJECT"
    elif soft:
        expected = "OBSERVE"
    else:
        expected = "PASS"
    method = (METHOD_BY_KIND["reject"] if expected == "REJECT"
              else METHOD_BY_KIND["observe"] if expected == "OBSERVE"
              else "全部证据满足且新鲜（PASS，不可用于演练）")
    if expected == "REJECT":
        if (rt.get("ok") and _num(rt.get("pass_rate"))
                and rt["pass_rate"] < MIN_REDTEAM_RATE):
            primary = rt.get("path")
            gen, age = rt.get("generated_at"), rt.get("age_hours")
        else:
            primary = base.get("path")
            gen, age = base.get("generated_at"), base.get("age_hours")
        rate = rt.get("pass_rate")
    else:
        candidates = [ev for ev in (base, rt) if ev.get("ok") and ev.get("stale")]
        primary_ev = candidates[0] if candidates else (rt if not rt["ok"] else base)
        primary = primary_ev.get("path")
        gen, age = primary_ev.get("generated_at"), primary_ev.get("age_hours")
        rate = rt.get("pass_rate")
    return {
        "baseline": base, "redteam": rt,
        "hard_fail": hard, "soft": soft,
        "expected_verdict": expected,
        "method": method,
        "reasons": hard + soft if hard else soft,
        "primary_evidence_source": primary,
        "evidence_generated_at": gen,
        "evidence_age_hours": age,
        "evidence_pass_rate": rate,
        "max_age_hours": MAX_EVIDENCE_AGE_HOURS,
        "min_redteam_rate": MIN_REDTEAM_RATE,
        "evaluated_at": _iso(now),
        "rule": "REJECT=基线 failed/error>0 或 红队 pass_rate<0.8；"
                "OBSERVE=证据缺失/不可读/超龄(>48h)；PASS=全部满足且新鲜",
    }


# ------------------------------------------------------------------ 台账读写

def load_ledger(path: Path) -> dict:
    """读 drill 台账（dict/drills 或顶层数组）：{exists, ok, data, entries, error}。"""
    data, err = _read_json(path)
    if err == "missing":
        return {"path": str(path), "exists": False, "ok": True, "data": None,
                "entries": [], "error": None}
    if err or data is None:
        return {"path": str(path), "exists": True, "ok": False, "data": None,
                "entries": [], "error": err or "空文件"}
    if isinstance(data, list):
        entries = [e for e in data if isinstance(e, dict)]
        return {"path": str(path), "exists": True, "ok": True, "data": data,
                "entries": entries, "shape": "list", "error": None}
    if isinstance(data, dict):
        drills = data.get("drills")
        if drills is None:
            drills = []
        if not isinstance(drills, list):
            return {"path": str(path), "exists": True, "ok": False, "data": None,
                    "entries": [], "error": "drills 字段不是数组"}
        entries = [e for e in drills if isinstance(e, dict)]
        return {"path": str(path), "exists": True, "ok": True, "data": data,
                "entries": entries, "shape": "dict", "error": None}
    return {"path": str(path), "exists": True, "ok": False, "data": None,
            "entries": [], "error": "台账顶层不是对象/数组"}


def _append_ledger(ledger: dict, entry: dict, now: dt.datetime) -> object:
    """返回追加后的台账对象（不写盘）。"""
    data = ledger.get("data")
    if data is None:
        return {
            "schema": "huangzhong_drill_ledger/v1",
            "generated_at": _iso(now),
            "note": "受控拒收演练台账（v2 §5）：演练单标注 drill=true，不计入 PASS "
                    "样本与覆盖率 KPI；见证人待签字；本条目由 huangzhong_drill.py 复演 "
                    "helper 生成",
            "drills": [entry],
        }
    if isinstance(data, list):
        return list(data) + [entry]
    out = dict(data)
    drills = out.get("drills")
    if not isinstance(drills, list):
        drills = []
    out["drills"] = list(drills) + [entry]
    out.setdefault("schema", "huangzhong_drill_ledger/v1")
    out.setdefault("generated_at", _iso(now))
    out["updated_at"] = _iso(now)
    return out


def _ledger_has_file(ledger: dict, fname: str) -> dict | None:
    """台账中是否已有同 file 条目（幂等键=演练单文件名）。"""
    for e in ledger.get("entries") or []:
        f = e.get("file") or e.get("acceptance_report") or e.get("path")
        if isinstance(f, str) and Path(f).name == fname:
            return e
    return None


# ---------------------------------------------------------------- 计划与执行

def _resolve_commit(repo_root: Path, explicit: str) -> tuple[str, str, list[str]]:
    """解析演练目标 commit：显式 --commit 优先；否则 git rev-parse HEAD。"""
    warnings: list[str] = []
    if (explicit or "").strip():
        return explicit.strip(), "explicit", warnings
    try:
        r = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=20)
        sha = (r.stdout or "").strip()
        if r.returncode == 0 and sha:
            return sha, "git_head", warnings
        warnings.append("git rev-parse HEAD 失败（rc=%s），--commit 未提供"
                        % getattr(r, "returncode", "?"))
    except Exception as e:  # noqa: BLE001  fail-visible，不中断
        warnings.append("git rev-parse 异常：%s: %s" % (type(e).__name__, e))
    return "", "unresolved", warnings


def _evidence_for_sheet(ev: dict) -> dict:
    """证据对象瘦身（Sheet 用，保留判定依据，不复制整份 baseline 内容）。"""
    keep = ("ok", "kind", "path", "source", "generated_at", "age_hours", "stale",
            "error", "failed", "error_count", "passed", "total", "pass_rate",
            "pass_rate_derived", "summary")
    return {k: ev.get(k) for k in keep if k in ev}


def _build_drill(kind: str, analysis: dict, commit: str, work_dir: Path,
                 repo_root: Path, witness: str, now: dt.datetime) -> dict:
    vote = VOTE_BY_KIND[kind]
    date_s = now.strftime("%Y%m%d")
    fname = "acceptance_drill_%s_%s.json" % (kind, date_s)
    sheet_path = work_dir / fname
    ledger_path = work_dir / "drills" / ("drill_ledger_%s.json" % date_s)
    rel = _rel(repo_root, sheet_path)
    witness = (witness or "").strip() or WITNESS_DEFAULT
    reproduce = ("python -X utf8 tools\\huangzhong_accept.py --commit %s"
                 % (commit[:8] if commit else "<sha>"))
    if analysis["redteam"].get("ok"):
        reproduce += " --redteam %s" % analysis["redteam"].get("path")
    if analysis["baseline"].get("ok") and analysis["baseline"].get("source") == "explicit":
        reproduce += " --baseline %s" % analysis["baseline"].get("path")
    reproduce += " --json"
    sheet = {
        "ok": True,
        "acceptance_id": "HZ-DRILL-%s-%s" % (vote, date_s),
        "generated_at": _iso(now),
        "mode": "observe",
        "blocking": False,
        "drill": True,
        "kind": vote,
        "commit": {
            "ok": bool(commit),
            "sha": commit,
            "author": "drill-helper",
            "date": _iso(now),
            "subject": "受控拒收演练（%s；drill=true 不计入 PASS 样本）"
                       % analysis["method"],
        },
        "evidence": {
            "baseline": _evidence_for_sheet(analysis["baseline"]),
            "redteam": _evidence_for_sheet(analysis["redteam"]),
            "construction": {
                "kind": kind,
                "expected_verdict": analysis["expected_verdict"],
                "method": analysis["method"],
                "controlled_source": analysis.get("primary_evidence_source"),
                "rationale": analysis.get("reasons") or [],
                "max_evidence_age_hours": MAX_EVIDENCE_AGE_HOURS,
                "min_redteam_rate": MIN_REDTEAM_RATE,
                "rule": analysis.get("rule"),
            },
        },
        "evidence_scope": "explicit_drill",
        "max_evidence_age_hours": MAX_EVIDENCE_AGE_HOURS,
        "verdict": vote,
        "reasons": analysis.get("reasons") or [],
        "rollback_point": ("无生产变更（只读演练）：证据文件未改、代码未改、未 merge；"
                           "复位=经老田/赵云确认后用回收站删除本 drill 单与台账条目"),
        "closed_by": "老田（人工）",
        "witness": witness,
        "reproduce_command": reproduce,
        "tool": "huangzhong_drill.py",
        "tool_version": TOOL_VERSION,
        "read_only": True,
        "note": "受控演练单（drill=true）：由 huangzhong_drill.py 复演 helper 生成，"
                "verdict 依 huangzhong_accept.py 同一规则对真实证据推导；不计入 PASS "
                "样本与覆盖率 KPI；不写 daily/events/incidents。正式复核以 accept 工具"
                "复跑为准。",
    }
    entry = {
        "drill_id": "DRILL-%s-%s-01" % (date_s, vote),
        "drill": True,
        "kind": vote,
        "verdict": vote,
        "method": analysis["method"],
        "commit": commit,
        "target_commit": commit,
        "acceptance_report": rel,
        "file": rel,
        "reason": analysis.get("reasons") or [],
        "reasons": analysis.get("reasons") or [],
        "evidence_sources": {
            "baseline": analysis["baseline"].get("path"),
            "redteam": analysis["redteam"].get("path"),
        },
        "evidence_source": analysis.get("primary_evidence_source"),
        "evidence_generated_at": analysis.get("evidence_generated_at"),
        "evidence_age_hours": analysis.get("evidence_age_hours"),
        "evidence_pass_rate": analysis.get("evidence_pass_rate"),
        "witness": witness,
        "created_at": _iso(now),
        "generated_at": _iso(now),
        "result_at": _iso(now),
        "tool": "huangzhong_drill.py",
        "tool_version": TOOL_VERSION,
        "reproduce_command": reproduce,
        "dry_run": False,
    }
    return {"file_name": fname, "sheet_path": sheet_path,
            "ledger_path": ledger_path, "sheet": sheet, "entry": entry,
            "reproduce_command": reproduce}


def _refused(reason: str, base: dict) -> dict:
    out = dict(base)
    out.update({"ok": False, "refused": {"reason": reason},
                "warnings": list(base.get("warnings") or []) + [reason]})
    return out


def run(kind: str, confirm: str, apply: bool = False,
        dry_run_flag: bool = False, work_dir: Path | str | None = None,
        repo_root: Path | str | None = None, baseline: str = "",
        redteam: str = "", commit: str = "", witness: str = "",
        now: dt.datetime | None = None) -> dict:
    """生成演练计划/执行演练；拒绝路径一律零写入（fail-visible）。"""
    now = now or _now()
    root = Path(repo_root) if repo_root else ROOT
    work = Path(work_dir) if work_dir else root / DEFAULT_WORK_SUBDIR
    warnings: list[str] = []
    result: dict = {
        "ok": True,
        "tool": "huangzhong_drill.py",
        "version": TOOL_VERSION,
        "generated_at": _iso(now),
        "repo_root": str(root),
        "work_dir": str(work),
        "request": {
            "kind": kind, "confirm": confirm, "apply": bool(apply),
            "dry_run": bool(apply is False),
            "baseline": baseline or None, "redteam": redteam or None,
            "commit": commit or None, "witness": witness or None,
        },
        "dry_run": not bool(apply),
        "precondition": None,
        "plan": None,
        "written": {"acceptance_file": None, "ledger_file": None},
        "skipped": False,
        "skip_reason": None,
        "refused": None,
        "warnings": warnings,
    }
    # 1) 确认字符串/参数校验（任何错误都不写盘）
    kind_norm = (kind or "").strip().lower()
    if kind_norm not in CONFIRM:
        return _refused("--kind 必须是 observe|reject（实际：%r）" % kind, result)
    result["request"]["kind"] = kind_norm
    expected_confirm = CONFIRM[kind_norm]
    if (confirm or "").strip().upper() != expected_confirm:
        return _refused("确认确认字符串错误：--kind %s 必须配 --confirm %s（实际：%r）"
                        % (kind_norm, expected_confirm, confirm), result)
    if apply and dry_run_flag:
        return _refused("--dry-run 与 --apply 不能同时使用（拒绝且零写入）", result)
    result["request"]["confirm"] = expected_confirm

    # 2) commit + 证据分析 + 预期 verdict
    sha, sha_source, sha_warn = _resolve_commit(root, commit)
    warnings.extend(sha_warn)
    analysis = analyze_evidence(root, baseline, redteam, now)
    result["precondition"] = analysis
    vote_expected = VOTE_BY_KIND[kind_norm]
    satisfied = analysis["expected_verdict"] == vote_expected
    result["precondition"]["satisfied"] = satisfied
    result["precondition"]["required_verdict"] = vote_expected
    if not satisfied:
        msg = ("受控构造成立性不足：按 accept 同一规则，当前证据将判 %s，"
               "与请求的 %s 演练不符。请按 v2 §5.2 用 --baseline/--redteam 显式绑定"
               "真实历史证据（手段1=历史失败证据→REJECT；手段2=证据超龄/缺失→OBSERVE）"
               % (analysis["expected_verdict"], vote_expected))
        if apply:
            return _refused(msg, result)
        warnings.append(msg)

    # 3) 构造计划（干跑也展示「将写什么」）
    drill = _build_drill(kind_norm, analysis, sha, work, root,
                         witness, now)
    ledger = load_ledger(drill["ledger_path"])
    existing_sheet = drill["sheet_path"].exists()
    existing_entry = _ledger_has_file(ledger, drill["file_name"])
    if not ledger["ok"]:
        msg = ("已有 drill 台账不可读/结构异常（%s：%s）；为避免覆盖或损坏台账，"
               "拒绝写入，请人工修复后重试（本工具不自动覆盖坏台账）"
               % (ledger["path"], ledger.get("error")))
        warnings.append(msg)
        if apply:
            return _refused(msg, result)
    skip = existing_sheet or existing_entry is not None
    if skip:
        reason = ("目标演练单已存在（%s）" % drill["sheet_path"]
                  if existing_sheet else
                  "台账已存在同 file 条目（%s）" % drill["file_name"])
        result.update({"skipped": True,
                       "skip_reason": reason + "；幂等跳过，不重复写"})
        warnings.append(reason + "；幂等跳过，不重复写")
        if existing_sheet and existing_entry is None:
            warnings.append("注意：演练单存在但台账无对应条目，请人工核对台账完整性")
    result["plan"] = {
        "kind": vote_expected,
        "acceptance_file": str(drill["sheet_path"]),
        "ledger_file": str(drill["ledger_path"]),
        "ledger_entry": drill["entry"],
        "sheet": drill["sheet"],
        "idempotent_skip": skip,
        "apply_would_refuse": (not satisfied) or (not ledger["ok"]) or (not sha),
        "repo_relative_acceptance_file": _rel(root, drill["sheet_path"]),
    }
    if not apply:
        return result

    # 4) apply：写演练单 + 追加台账（严格顺序：先单后台账；失败可人工清理）
    if not sha:
        return _refused("无法解析目标 commit（git 不可用且未给 --commit）；拒绝写入",
                        result)
    if skip:
        result["written"] = {"acceptance_file": None, "ledger_file": None}
        return result
    new_ledger = _append_ledger(ledger, drill["entry"], now)
    try:
        _write_json(drill["sheet_path"], drill["sheet"])
        _write_json(drill["ledger_path"], new_ledger)
    except OSError as e:
        return _refused("写入失败：%s: %s（请检查 work 目录权限）"
                        % (type(e).__name__, e), result)
    result["written"] = {
        "acceptance_file": str(drill["sheet_path"]),
        "ledger_file": str(drill["ledger_path"]),
    }
    return result


# ------------------------------------------------------------------- CLI 入口

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="黄忠受控演练 helper（复演/复证；默认 dry-run，--apply 才写盘）")
    ap.add_argument("--kind", default="", help="observe | reject")
    ap.add_argument("--confirm", default="",
                    help="确认确认字符串：kind=observe 须为 DRILL-OBSERVE；"
                         "kind=reject 须为 DRILL-REJECT")
    ap.add_argument("--dry-run", action="store_true",
                    help="仅打印将写文件/台账内容，不写盘（默认行为）")
    ap.add_argument("--apply", action="store_true", help="真正写入演练单与台账条目")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON")
    ap.add_argument("--work-dir", default="",
                    help="演练产物目录（默认 <repo-root>/work/huangzhong）")
    ap.add_argument("--repo-root", default="",
                    help="仓库根（默认工具所在仓库；worktree 内用主仓数据时显式指定）")
    ap.add_argument("--baseline", default="",
                    help="显式绑定基线证据 JSON（默认全局最新 test_baseline/latest.json）")
    ap.add_argument("--redteam", default="",
                    help="显式绑定红队证据（JSON 文件或目录；默认 work/p1/redteam）")
    ap.add_argument("--commit", default="", help="演练目标 commit（默认 git HEAD）")
    ap.add_argument("--witness", default="", help="见证人（默认「待老田/赵云签字」）")
    args = ap.parse_args(argv)
    if not args.kind:
        print("错误：必须提供 --kind observe|reject（本工具拒绝无确认字符串写入）")
        return 1
    rep = run(kind=args.kind, confirm=args.confirm, apply=args.apply,
              dry_run_flag=args.dry_run,
              work_dir=args.work_dir or None,
              repo_root=args.repo_root or None,
              baseline=args.baseline, redteam=args.redteam,
              commit=args.commit, witness=args.witness)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        _print_human(rep)
    return 0 if rep.get("ok") else 1


def _print_human(rep: dict) -> None:
    mode = "apply（已写入）" if not rep["dry_run"] else "dry-run（零写入）"
    print("黄忠受控演练 helper · %s" % mode)
    if not rep.get("ok"):
        print("拒绝：%s" % ((rep.get("refused") or {}).get("reason") or "unknown"))
        for w in rep.get("warnings") or []:
            print("  - warning: %s" % w)
        return
    pre = rep.get("precondition") or {}
    print("- kind=%s/verdict=%s；预期 verdict=%s；构造成立=%s"
          % (rep["request"]["kind"], rep["request"]["kind"].upper(),
             pre.get("expected_verdict"), pre.get("satisfied")))
    print("- 方法：%s" % pre.get("method"))
    for r in pre.get("reasons") or []:
        print("  · reason: %s" % r)
    plan = rep.get("plan") or {}
    print("- 演练单：%s" % plan.get("acceptance_file"))
    print("- 台账：%s（追加条目：%s）"
          % (plan.get("ledger_file"),
             (plan.get("ledger_entry") or {}).get("drill_id")))
    if plan.get("apply_would_refuse"):
        print("- 注意：当前条件下 apply 将被拒绝（零写入），请先按提示补证据/修台账")
    if rep.get("skipped"):
        print("- 幂等跳过：%s" % rep.get("skip_reason"))
    wr = rep.get("written") or {}
    if wr.get("acceptance_file"):
        print("- 已写入：%s" % wr["acceptance_file"])
        print("- 已写入：%s" % wr["ledger_file"])
    for w in rep.get("warnings") or []:
        print("  - warning: %s" % w)


if __name__ == "__main__":
    raise SystemExit(main())
