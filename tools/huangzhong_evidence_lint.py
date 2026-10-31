# -*- coding: utf-8 -*-
"""黄忠验收单证据 schema lint（P8，2026-10-31，**只读**）。

扫描 `work/huangzhong/acceptance_*.json`，对历史验收单做证据 schema 卫生检查：
  · 按 commit.sha 去重（同 commit 多单只 lint 最新一单，统计 dup）；
  · 分类：legacy(v0，缺证据年龄字段，沿用 review v1.1 口径) / v1；
  · v1 必需字段 lint（缺字段/类型不符 → 每文件 issues）：
      commit.sha、verdict、mode=="observe"、blocking is False、rollback_point 非空、
      evidence.baseline.{generated_at,age_hours,stale}、
      evidence.redteam.{generated_at,age_hours,stale}；
  · 输出总数/legacy 数/v1 数/v1 完整率/每文件 issues/坏文件清单；坏文件逐条列出不编造；
  · md 报告落 `deliverables/2026-10-31_黄忠验收单证据lint_<ts>.md`（汇总表+legacy 清单+v1 问题清单）；
  · --strict：legacy 或 v1 缺失字段或坏文件存在 → 退出码 1（默认仅报告 rc=0）。

路径（均可覆盖）：
  · HUANGZHONG_WORK_DIR / --work-dir：验收单目录（默认<主仓>/work/huangzhong）
  · HUANGZHONG_DELIVERABLES_DIR / --out-dir：md 报告目录（默认主仓同级的平台级
    deliverables，即 C:\\项目\\deliverables；避免写脏仓库工作区）
只读：不改验收单、不写 work/、不写 dev_events。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[1]
PATTERN = "acceptance_*.json"
REPORT_NAME = "黄忠验收单证据lint"
VERDICTS = ("PASS", "REJECT", "OBSERVE")


# ------------------------------------------------------------------ 路径/时间

def _main_repo() -> Path:
    """主仓根（worktree 隔离开发时经 git-common-dir 定位；失败退回脚本所在仓根）。"""
    try:
        r = subprocess.run(
            ["git", "-C", str(HERE.parent), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15)
        if r.returncode == 0 and r.stdout.strip():
            common = Path(r.stdout.strip())
            if not common.is_absolute():
                common = (HERE.parent / common).resolve()
            return common.parent
    except Exception:  # noqa: BLE001 —— 定位失败退回本仓根
        pass
    return ROOT


def _work_dir(arg=None) -> Path:
    if arg:
        return Path(arg)
    raw = str(os.environ.get("HUANGZHONG_WORK_DIR") or "").strip()
    return Path(raw) if raw else (_main_repo() / "work" / "huangzhong")


def _default_out_dir() -> Path:
    raw = str(os.environ.get("HUANGZHONG_DELIVERABLES_DIR") or "").strip()
    if raw:
        return Path(raw)
    return _main_repo().parent / "deliverables"


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _parse_ts(raw) -> dt.datetime | None:
    """解析 ISO8601（含 Z/偏移）/ 空格格式；失败返回 None。"""
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


# -------------------------------------------------------------- schema 检查

def _get(data: dict, dotted: str):
    """按点路径取嵌套值；任一层缺失返回 None。"""
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _is_nonempty_str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _evidence_age_fields(data: dict) -> list[str]:
    """缺失的证据年龄字段（legacy 判定用；同 review v1.1 口径）。"""
    missing: list[str] = []
    for kind in ("baseline", "redteam"):
        ev = _get(data, "evidence." + kind)
        for field in ("age_hours", "stale"):
            if not isinstance(ev, dict) or field not in ev:
                missing.append("evidence.%s.%s" % (kind, field))
    return missing


def _v1_issues(data: dict) -> list[str]:
    """v1 必需字段 lint：返回缺失/不合法字段清单（空=完整）。"""
    issues: list[str] = []
    if not _is_nonempty_str(_get(data, "commit.sha")):
        issues.append("commit.sha")
    verdict = data.get("verdict")
    if not _is_nonempty_str(verdict):
        issues.append("verdict")
    elif verdict not in VERDICTS:
        issues.append("verdict=%s（非 PASS/REJECT/OBSERVE）" % verdict)
    if data.get("mode") != "observe":
        issues.append("mode（应为 observe，实际 %r）" % data.get("mode"))
    if data.get("blocking") is not False:
        issues.append("blocking（应为 false，实际 %r）" % data.get("blocking"))
    if not _is_nonempty_str(data.get("rollback_point")):
        issues.append("rollback_point")
    for kind in ("baseline", "redteam"):
        prefix = "evidence." + kind
        ev = _get(data, prefix)
        if not isinstance(ev, dict):
            ev = {}
            issues.append(prefix)
        if not _is_nonempty_str(ev.get("generated_at")):
            issues.append(prefix + ".generated_at")
        age = ev.get("age_hours")
        if not _is_number(age):
            issues.append(prefix + ".age_hours")
        elif age < 0:
            issues.append(prefix + ".age_hours（负值）")
        if not isinstance(ev.get("stale"), bool):
            issues.append(prefix + ".stale")
    return issues


def _recency_key(rec: dict) -> dt.datetime:
    """去重排序键：generated_at（可解析）优先，否则用文件 mtime。"""
    ts = _parse_ts(rec.get("generated_at"))
    if ts is not None:
        return ts
    return dt.datetime.fromtimestamp(rec.get("mtime") or 0, tz=dt.timezone.utc)


# ---------------------------------------------------------------- 扫描/lint

def _scan(work_dir: Path) -> tuple[list[dict], list[dict]]:
    """读全部 acceptance_*.json：返回 (可解析记录, 坏文件清单)。"""
    records: list[dict] = []
    errors: list[dict] = []
    try:
        files = sorted(work_dir.glob(PATTERN), key=lambda p: p.name)
    except OSError as e:
        return records, [{"file": "*", "path": str(work_dir),
                          "error": "目录扫描失败：%s: %s" % (type(e).__name__, e)}]
    for p in files:
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        rec = {"file": p.name, "path": str(p),
               "mtime": mtime, "generated_at": None,
               "sha": None, "data": None}
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, ValueError) as e:
            rec["error"] = "读取失败：%s: %s" % (type(e).__name__, e)
            errors.append({"file": p.name, "path": str(p), "error": rec["error"]})
            continue
        try:
            data = json.loads(text)
        except ValueError as e:
            rec["error"] = "JSON 解析失败：%s" % e
            errors.append({"file": p.name, "path": str(p), "error": rec["error"]})
            continue
        if not isinstance(data, dict):
            rec["error"] = "JSON 顶层不是对象"
            errors.append({"file": p.name, "path": str(p), "error": rec["error"]})
            continue
        rec["data"] = data
        rec["generated_at"] = data.get("generated_at")
        sha = _get(data, "commit.sha")
        rec["sha"] = sha.strip().lower() if _is_nonempty_str(sha) else None
        records.append(rec)
    return records, errors


def _dedupe(records: list[dict]) -> tuple[list[dict], list[dict], int]:
    """按 commit.sha 去重保留最新；无 sha 的按文件独立（不参与跨文件去重）。"""
    groups: dict[str, list[dict]] = {}
    for rec in records:
        key = rec["sha"] or ("FILE:" + rec["file"])
        groups.setdefault(key, []).append(rec)
    kept: list[dict] = []
    duplicates: list[dict] = []
    dup_count = 0
    for key, recs in groups.items():
        if len(recs) == 1:
            kept.append(recs[0])
            continue
        ordered = sorted(recs, key=_recency_key)
        keep = ordered[-1]
        dropped = [r["file"] for r in ordered[:-1]]
        kept.append(keep)
        dup_count += len(dropped)
        duplicates.append({"sha": keep["sha"] or key,
                           "kept": keep["file"], "dropped": dropped})
    kept.sort(key=lambda r: r["file"])
    duplicates.sort(key=lambda d: d["kept"])
    return kept, duplicates, dup_count


def _build_md(rep: dict, now: dt.datetime) -> str:
    legacy = rep["legacy"]
    v1_issues = rep["v1_issues"]
    lines = [
        "# 黄忠验收单证据 schema lint（P8）", "",
        "- 生成时间：%s" % rep["generated_at"],
        "- 扫描目录：`%s`" % rep["work_dir"],
        "- 扫描文件数：%d（去重后 %d；同 sha 重复丢弃 %d）"
        % (rep["scanned"], rep["unique"], rep["duplicates"]),
        "- 分类：legacy(v0) **%d** / v1 **%d**" % (rep["legacy_count"], rep["v1_count"]),
        "- v1 完整率：**%s**（%d/%d）"
        % (_pct(rep["completeness"]), rep["v1_complete_count"], rep["v1_count"]),
        "- 坏文件：%d" % rep["error_count"],
        "- strict 判据：%s（--strict %s）"
        % ("PASS" if rep["strict_pass"] else "FAIL",
           "已启用，失败即 rc1" if rep["strict"] else "未启用，仅报告 rc0"),
        "",
        "## 汇总", "",
        "| 指标 | 值 |",
        "|---|---|",
        "| 扫描文件数 | %d |" % rep["scanned"],
        "| 去重后单数 | %d |" % rep["unique"],
        "| 同 sha 重复丢弃 | %d |" % rep["duplicates"],
        "| legacy(v0) 单 | %d |" % rep["legacy_count"],
        "| v1 单 | %d |" % rep["v1_count"],
        "| v1 完整单 | %d |" % rep["v1_complete_count"],
        "| v1 完整率 | %s |" % _pct(rep["completeness"]),
        "| 坏文件 | %d |" % rep["error_count"],
        "",
        "## legacy 清单（缺证据年龄字段，v0 口径）", "",
    ]
    if legacy:
        lines += ["| 文件 | sha | 缺失字段 |", "|---|---|---|"]
        for r in legacy:
            lines.append("| %s | %s | %s |"
                         % (r["file"], _short_sha(r["sha"]),
                            ", ".join(r["missing"]) or "-"))
    else:
        lines.append("无")
    lines += ["", "## v1 问题清单（必需字段缺失/不合法）", ""]
    if v1_issues:
        lines += ["| 文件 | sha | 问题字段 |", "|---|---|---|"]
        for r in v1_issues:
            lines.append("| %s | %s | %s |"
                         % (r["file"], _short_sha(r["sha"]),
                            ", ".join(r["issues"]) or "-"))
    else:
        lines.append("无（v1 单必需字段齐全）")
    lines += ["", "## 坏文件（坏 JSON/不可读，逐条列出）", ""]
    if rep["errors"]:
        for e in rep["errors"]:
            lines.append("- `%s`：%s" % (e["file"], e["error"]))
    else:
        lines.append("无")
    if rep["duplicates_detail"]:
        lines += ["", "## 同 sha 去重明细", ""]
        for d in rep["duplicates_detail"]:
            lines.append("- sha=%s 保留 `%s`，丢弃：%s"
                         % (_short_sha(d["sha"]), d["kept"],
                            ", ".join("`%s`" % x for x in d["dropped"])))
    lines += [
        "", "## 口径", "",
        "- 必需字段（v1）：`commit.sha` / `verdict` / `mode==\"observe\"` / "
        "`blocking is False` / `rollback_point` 非空 / "
        "`evidence.baseline.generated_at+age_hours+stale` / "
        "`evidence.redteam.generated_at+age_hours+stale`；",
        "- legacy 判定：缺 `evidence.baseline|redteam` 的 `age_hours`/`stale` 字段"
        "（同 review v1.1 口径）；",
        "- 去重：同 `commit.sha` 保留 `generated_at` 最新一单（不可解析时按文件 mtime），"
        "其余计入 dup；无 sha 的坏单不参与跨文件去重；",
        "- 只读检查：不修改验收单、不写 work/、不写 dev_events；坏文件不编造、逐条列出。",
        "",
    ]
    return "\n".join(lines)


def _pct(v) -> str:
    return "-" if v is None else "%.1f%%" % (v * 100)


def _short_sha(sha) -> str:
    return (sha or "-")[:12]


def lint(work_dir=None, out_dir=None, strict: bool = False,
         now: dt.datetime | None = None) -> dict:
    """执行 lint（默认写 md 报告）；返回结构化结果（ok/严格模式见 strict_pass）。"""
    now = now or _now()
    wd = _work_dir(work_dir)
    od = Path(out_dir) if out_dir else _default_out_dir()
    rep = {
        "ok": True,
        "generated_at": now.isoformat(timespec="seconds"),
        "work_dir": str(wd),
        "out_dir": str(od),
        "pattern": PATTERN,
        "scanned": 0, "unique": 0, "total": 0, "duplicates": 0,
        "duplicates_detail": [],
        "legacy_count": 0, "v1_count": 0, "v1_complete_count": 0,
        "completeness": None,
        "files": [], "legacy": [], "v1_issues": [], "errors": [],
        "error_count": 0,
        "strict": bool(strict), "strict_pass": True,
        "strict_fail_reasons": [],
        "md_report": None,
    }
    if not wd.is_dir():
        rep.update(ok=False, error="work 目录不存在：%s" % wd)
        rep["strict_pass"] = False
        rep["strict_fail_reasons"] = ["work 目录不存在"]
        return rep
    records, errors = _scan(wd)
    rep["scanned"] = len(records) + len(errors)
    rep["errors"] = errors
    rep["error_count"] = len(errors)
    kept, duplicates, dup_count = _dedupe(records)
    rep["unique"] = len(kept)
    rep["total"] = rep["scanned"]
    rep["duplicates"] = dup_count
    rep["duplicates_detail"] = duplicates
    for rec in kept:
        data = rec["data"]
        base = {"file": rec["file"], "sha": rec["sha"],
                "generated_at": rec["generated_at"]}
        missing_age = _evidence_age_fields(data)
        if missing_age:  # legacy：缺证据年龄字段 → 不参与 v1 完整率
            rep["legacy_count"] += 1
            rep["legacy"].append({**base, "missing": missing_age})
            rep["files"].append({**base, "class": "legacy", "issues": missing_age})
            continue
        rep["v1_count"] += 1
        issues = _v1_issues(data)
        if issues:
            rep["v1_issues"].append({**base, "issues": issues})
            rep["files"].append({**base, "class": "v1", "issues": issues})
        else:
            rep["v1_complete_count"] += 1
            rep["files"].append({**base, "class": "v1", "issues": []})
    if rep["v1_count"]:
        rep["completeness"] = round(rep["v1_complete_count"] / rep["v1_count"], 4)
    else:
        rep["completeness"] = 1.0 if not rep["legacy_count"] else None
    fail_reasons = []
    if rep["legacy_count"]:
        fail_reasons.append("存在 legacy(v0) 单 %d 个" % rep["legacy_count"])
    if rep["v1_issues"]:
        fail_reasons.append("v1 缺字段/不合法单 %d 个" % len(rep["v1_issues"]))
    if rep["error_count"]:
        fail_reasons.append("坏文件 %d 个" % rep["error_count"])
    rep["strict_fail_reasons"] = fail_reasons
    rep["strict_pass"] = not fail_reasons
    # md 报告（落 deliverables；写失败 fail-visible）
    try:
        od.mkdir(parents=True, exist_ok=True)
        mp = od / ("%s_%s_%s.md" % (now.strftime("%Y-%m-%d"), REPORT_NAME,
                                    now.strftime("%H%M%S")))
        mp.write_text(_build_md(rep, now), encoding="utf-8")
        rep["md_report"] = str(mp)
    except OSError as e:
        rep["ok"] = False
        rep["error"] = "md 报告写入失败：%s: %s" % (type(e).__name__, e)
    return rep


def _summary_line(rep: dict) -> str:
    if not rep.get("ok"):
        return "证据lint 失败：%s" % rep.get("error", "unknown")
    return ("证据lint：扫描 %d（去重后 %d，dup %d）｜legacy %d / v1 %d｜"
            "完整率 %s｜坏文件 %d｜strict=%s(%s) → %s"
            % (rep["scanned"], rep["unique"], rep["duplicates"],
               rep["legacy_count"], rep["v1_count"], _pct(rep["completeness"]),
               rep["error_count"], "on" if rep["strict"] else "off",
               "PASS" if rep["strict_pass"] else "FAIL",
               rep.get("md_report") or "-"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="黄忠验收单证据 schema lint（只读，P8）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--work-dir", default="",
                    help="验收单目录（默认<主仓>/work/huangzhong；env HUANGZHONG_WORK_DIR）")
    ap.add_argument("--out-dir", default="",
                    help="md 报告目录（默认平台级 deliverables；env HUANGZHONG_DELIVERABLES_DIR）")
    ap.add_argument("--strict", action="store_true",
                    help="legacy/缺字段/坏文件存在 → 退出码 1")
    args = ap.parse_args(argv)
    rep = lint(args.work_dir or None, args.out_dir or None, strict=args.strict)
    print(json.dumps(rep, ensure_ascii=False, indent=1) if args.json
          else _summary_line(rep))
    if not rep.get("ok"):
        return 1
    if rep.get("strict") and not rep.get("strict_pass"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
