# -*- coding: utf-8 -*-
"""黄忠阻断分级模拟（P3-D · WO-HZ-BLOCKSIM-20261101，只读）。

用途：用现有「改动风险分类 + 历史验收单」，模拟「若启用阻断分级」会拦下哪些
merge、其中有多少属于误杀候选，为老田 L3 阻断决策提供数据。

**只读模拟**：本工具不接入 merge、不改变 devtask/merge 行为、不阻断任何东西；
唯一落盘为 Markdown 报告（`--no-write` 可关）。

流程：
  1) 取窗口内 merge：`git log --merges --first-parent --since="<days> days ago"
     --format=@@%H%x1f%P%x1f%cI%x1f%s --name-only --diff-merges=first-parent`
     （git 失败时回退为「merge 列表 + 逐 merge diff」，fail-visible）；
  2) 改动分类（复用 tools/huangzhong_impact.py 的 impact 思路）：docs→low；
     路径 token 命中高关键词（data/security/auth/secret/token/migration/sql/
     database/credential/payment/schema/backup/delete/drop 等）或高影响目录
     （quant_backtest）→high；tools/portal/agent_platform→medium；其他→medium。
     多文件取最高风险；
  3) 验收匹配（内置默认规则；`--rules` 可整体覆盖）：
       · 高风险：无 PASS 验收单 **或** 证据超龄/年龄不可判定 → 模拟 block；
       · 中风险：无 PASS 验收单 → 模拟 block；
       · 低风险：不拦；
       · 默认模拟口径 sim_match="merge_sha"（严格按 merge commit 验收）；
         实际覆盖口径 actual_match="merge_range"（merge 带入的提交区间验收），
         可用 --rules 改成 merge_sha / branch_tip / merge_range；
  4) 每个 merge 输出：风险等级/原因、模拟结论（block/pass）、原因、真实验收
     情况（是否 PASS、覆盖口径、证据年龄/新鲜度）、是否误杀候选
     （模拟 block，但实际区间口径存在新鲜 PASS 验收 → 误杀候选）；
  5) 汇总：merge 数/风险分布/模拟拦截数/误杀候选数/误杀率；数据不足如实报告。

CLI：
  python -X utf8 tools/huangzhong_blocking_sim.py --json --days 7
  [--rules <policy.json>] [--work-dir DIR] [--repo-root DIR] [--out-dir DIR]
  [--no-write] [--max-merges N]

退出码：0=正常出报告；1=有数据缺口（坏验收单/部分 merge 取不到改动，结果标注
fail-visible）；2=无法出报告（git 取 merge 失败/规则文件坏）。
所有 git 命令经模块级 `_run(cmd, cwd, timeout)`，测试可 monkeypatch。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = Path(r"C:\项目\deliverables")
DEFAULT_WORK_SUBDIR = Path("work") / "huangzhong"
REPORT_PREFIX = "2026-11-01_黄忠阻断分级模拟"
GIT_TIMEOUT = 120.0
MAX_LIST = 50
RISK_KEYS = ("high", "medium", "low")
RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}
MATCH_MODES = ("merge_sha", "branch_tip", "merge_range")

# 改动分类口径：复用 tools/huangzhong_impact.py（P1-D）的 impact 思路
HIGH_KEYWORDS = {
    "data", "security", "auth", "secret", "token", "tokenize",
    "migration", "migrate", "migrations", "credential", "credentials",
    "password", "passwords", "payment", "payments", "schema", "schemas",
    "sql", "database", "db", "backup", "delete", "drop", "private_key",
    "apikey", "api_key",
}
HIGH_DIRS = ("quant_backtest",)
MEDIUM_DIRS = ("tools", "portal", "agent_platform")


# ------------------------------------------------------------------ 基础工具

def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _iso(ts: dt.datetime | None) -> str | None:
    return ts.isoformat(timespec="seconds") if isinstance(ts, dt.datetime) else None


def _parse_ts(raw) -> dt.datetime | None:
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
        return path.read_text(encoding="utf-8-sig"), None
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


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _cap(rows: list, limit: int = MAX_LIST) -> list:
    return rows[:limit]


def _log(msg: str) -> None:
    print("[huangzhong_blocking_sim] " + str(msg), file=sys.stderr)


def _run(cmd: list, cwd: Path | str | None = None,
         timeout: float = GIT_TIMEOUT) -> dict:
    """执行外部命令（模块级；测试可 monkeypatch）。不抛异常，失败如实返回。"""
    cmd = [str(c) for c in cmd]
    started = _now()
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "rc": None, "stdout": "", "stderr": "",
                "error": "%s: %s" % (type(e).__name__, e), "cmd": cmd,
                "cwd": str(cwd) if cwd else None,
                "elapsed_s": round((_now() - started).total_seconds(), 2)}
    return {"ok": True, "rc": proc.returncode,
            "stdout": proc.stdout or "", "stderr": proc.stderr or "",
            "error": None, "cmd": cmd, "cwd": str(cwd) if cwd else None,
            "elapsed_s": round((_now() - started).total_seconds(), 2)}


def _git(repo: Path, *args, timeout: float = GIT_TIMEOUT) -> dict:
    """git 调用（-C repo + core.quotepath=false 保非 ASCII 路径原样）。"""
    cmd = ["git", "-C", str(repo), "-c", "core.quotepath=false"]
    cmd += [str(a) for a in args]
    return _run(cmd, cwd=repo, timeout=timeout)


def _norm_files(lines) -> list[str]:
    out = []
    for line in lines or []:
        s = str(line).strip().replace("\\", "/")
        if not s:
            continue
        if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
            s = s[1:-1]
        if s and s not in out:
            out.append(s)
    return out


# -------------------------------------------------------- 改动风险分类（impact）

def _tokens(path: str) -> set:
    return set(re.split(r"[^a-z0-9]+", path.replace("\\", "/").lower()))


def _risk_of(path: str) -> tuple[str, str]:
    """单文件风险：docs→low；高关键词/高影响目录→high；tools 等→medium。"""
    p = path.replace("\\", "/").lower()
    top = p.split("/", 1)[0]
    if top == "docs":
        return "low", "docs 文档"
    hit = sorted(_tokens(p) & HIGH_KEYWORDS)
    if hit:
        return "high", "关键词 %s" % "/".join(hit)
    if top in HIGH_DIRS:
        return "high", "高影响目录 %s" % top
    if top in MEDIUM_DIRS:
        return "medium", "目录 %s" % top
    return "medium", "其他目录 %s（保守）" % top


def risk_level(files: list[str]) -> tuple[str, list[str]]:
    """多文件取最高风险；无改动文件 → none（按低风险不拦，报告注明）。"""
    level = "none"
    reasons: list[str] = []
    for f in files:
        lv, why = _risk_of(f)
        reasons.append("%s：%s" % (f, why))
        if RISK_ORDER[lv] > RISK_ORDER[level]:
            level = lv
    return level, reasons


# ------------------------------------------------------------------ 规则加载

def builtin_rules() -> dict:
    """内置默认规则：高风险无验收/证据超龄→block；中风险无验收→block；低风险不拦。"""
    return {
        "version": "v1",
        "max_evidence_age_hours": 48.0,
        "rules": {
            "high": {"block_if_no_acceptance": True,
                     "block_if_evidence_stale": True},
            "medium": {"block_if_no_acceptance": True,
                       "block_if_evidence_stale": False},
            "low": {"block_if_no_acceptance": False,
                    "block_if_evidence_stale": False},
        },
        "sim_match": "merge_sha",
        "actual_match": "merge_range",
        "note": ("默认模拟口径：sim 严格按 merge commit 验收；actual 按 merge 带入的"
                 "提交区间验收（用于识别误杀候选）"),
    }


def _validate_rules(data: dict) -> tuple[dict | None, list[str]]:
    """校验/归一化规则结构；返回 (rules, errors)。errors 非空 → rc2。"""
    if not isinstance(data, dict):
        return None, ["规则顶层不是 JSON 对象"]
    errors: list[str] = []
    version = data.get("version", "v1")
    if not isinstance(version, str) or not version.strip():
        errors.append("version 须为非空字符串")
        version = "v1"
    max_age = data.get("max_evidence_age_hours", 48.0)
    if not _num(max_age) or float(max_age) <= 0:
        errors.append("max_evidence_age_hours 须为正数")
        max_age = 48.0
    raw_rules = data.get("rules")
    if raw_rules is None:
        raw_rules = {}
    elif not isinstance(raw_rules, dict):
        errors.append("rules 须为对象（high/medium/low）")
        raw_rules = {}
    rules = {}
    for key in RISK_KEYS:
        one = raw_rules.get(key, {})
        if not isinstance(one, dict):
            errors.append("rules.%s 须为对象" % key)
            one = {}
        flags = {}
        for field in ("block_if_no_acceptance", "block_if_evidence_stale"):
            val = one.get(field, builtin_rules()["rules"][key][field])
            if not isinstance(val, bool):
                errors.append("rules.%s.%s 须为布尔" % (key, field))
                val = False
            flags[field] = val
        rules[key] = flags
    sim_match = data.get("sim_match", "merge_sha")
    if sim_match not in MATCH_MODES:
        errors.append("sim_match 须为 %s" % "/".join(MATCH_MODES))
        sim_match = "merge_sha"
    actual_match = data.get("actual_match", "merge_range")
    if actual_match not in MATCH_MODES:
        errors.append("actual_match 须为 %s" % "/".join(MATCH_MODES))
        actual_match = "merge_range"
    note = data.get("note", "")
    if not isinstance(note, str):
        errors.append("note 须为字符串")
        note = ""
    if errors:
        return None, errors
    return {"version": version.strip(),
            "max_evidence_age_hours": float(max_age),
            "rules": rules, "sim_match": sim_match,
            "actual_match": actual_match, "note": note}, []


def load_rules(rules_path: Path | str | None) -> dict:
    """加载规则：无 --rules → 内置默认；指定但坏/不存在 → ok=False（rc2，不静默回退）。"""
    if not rules_path:
        return {"ok": True, "source": "builtin", "path": None,
                "rules": builtin_rules(), "error": None}
    path = Path(rules_path)
    if not path.exists():
        return {"ok": False, "source": "file", "path": str(path),
                "rules": None, "error": "规则文件不存在：%s" % path}
    data, err = _read_json(path)
    if err is not None or not isinstance(data, dict):
        return {"ok": False, "source": "file", "path": str(path),
                "rules": None,
                "error": "规则文件不可读/顶层不是对象：%s（%s）"
                         % (path, err or "结构错误")}
    parsed, errors = _validate_rules(data)
    if errors:
        return {"ok": False, "source": "file", "path": str(path),
                "rules": None,
                "error": "规则文件结构非法：%s" % "；".join(errors)}
    return {"ok": True, "source": "file", "path": str(path),
            "rules": parsed, "error": None}


# ------------------------------------------------------------ 验收单（证据面）

def _sheet_evidence_ages(sheet: dict) -> dict:
    """取验收单记录的证据年龄：兼容 evidence.baseline/redteam.age_hours 与 evidence_age。"""
    ev = sheet.get("evidence") if isinstance(sheet.get("evidence"), dict) else {}
    ages = {}
    for kind in ("baseline", "redteam"):
        one = ev.get(kind) if isinstance(ev.get(kind), dict) else {}
        age = one.get("age_hours")
        if not _num(age):
            block = sheet.get("evidence_age")
            block = block if isinstance(block, dict) else {}
            age = block.get(kind)
        ages[kind] = float(age) if _num(age) else None
    return ages


def load_acceptance(work_dir: Path, max_age_hours: float) -> dict:
    """读 work/huangzhong/acceptance_*.json → PASS 验收单 + 证据年龄/新鲜度。

    坏文件容错（记 invalid 供 fail-visible）；演练单不计入覆盖口径。
    """
    work_dir = Path(work_dir)
    files = sorted(work_dir.glob("acceptance_*.json"))
    sheets, invalid = [], []
    for p in files:
        data, err = _read_json(p)
        if err is not None or not isinstance(data, dict):
            invalid.append({"file": p.name, "error": err or "JSON 顶层不是对象"})
            continue
        if str(data.get("verdict") or "").upper() != "PASS":
            continue
        if data.get("drill") or data.get("mode") == "drill":
            continue
        commit = data.get("commit") if isinstance(data.get("commit"), dict) else {}
        sha = commit.get("sha") if isinstance(commit.get("sha"), str) else ""
        sha = sha.strip()
        if len(sha) < 8:
            invalid.append({"file": p.name, "error": "commit.sha 缺失/过短"})
            continue
        ages = _sheet_evidence_ages(data)
        known = [a for a in ages.values() if _num(a)]
        max_age = max(known) if known else None
        fresh = (max_age <= max_age_hours) if max_age is not None else None
        sheets.append({
            "file": p.name, "sha": sha, "sha8": sha[:8].lower(),
            "generated_at": data.get("generated_at"),
            "evidence_ages": ages,
            "max_evidence_age_hours": max_age,
            "fresh": fresh,
            "fresh_note": ("新鲜（≤%.0fh）" % max_age_hours if fresh is True
                           else ("超龄（>%.0fh）" % max_age_hours if fresh is False
                                 else "证据年龄不可判定（v0 旧单/缺字段）")),
        })
    return {"files": len(files), "sheets": sheets,
            "invalid": _cap(invalid), "invalid_count": len(invalid)}


# ------------------------------------------------------------ merge 列表/git

def _format_merge_log(args: list) -> dict:
    """git log --merges --first-parent --name-only --diff-merges=first-parent。"""
    cmd = ["log", "--merges", "--first-parent"] + args + [
        "--format=@@%H%x1f%P%x1f%cI%x1f%s", "--name-only",
        "--diff-merges=first-parent"]
    return cmd


def _parse_merge_log(stdout: str) -> list[dict]:
    """解析 @@sha\\x1fparents\\x1fdate\\x1fsubject + 文件行 的 git log 输出。"""
    merges, cur = [], None
    for line in (stdout or "").splitlines():
        if line.startswith("@@"):
            payload = line[2:]
            parts = payload.split("\x1f")
            sha = parts[0].strip() if parts else ""
            parents = parts[1].split() if len(parts) > 1 else []
            date = parts[2].strip() if len(parts) > 2 else ""
            subject = "\x1f".join(parts[3:]).strip() if len(parts) > 3 else ""
            cur = {"sha": sha, "sha8": sha[:8].lower(),
                   "parents": [p.strip() for p in parents if p.strip()],
                   "date": date, "subject": subject, "files": []}
            merges.append(cur)
        elif line.strip() and cur is not None:
            cur["files"].extend(_norm_files(line.splitlines()))
    # 去重 + 清空空 sha 行
    out = []
    for m in merges:
        if not m["sha"]:
            continue
        m["files"] = _norm_files(m["files"])
        out.append(m)
    return out


def _fallback_merge_files(repo: Path, merges: list[dict],
                          warnings: list[str]) -> list[dict]:
    """回退：逐个 merge `git diff --name-only <第一父> <sha>` 取改动（fail-visible）。"""
    for m in merges:
        parents = m.get("parents") or []
        base = parents[0] if parents else "%s^" % m["sha"]
        res = _git(repo, "diff", "--name-only", base, m["sha"],
                   timeout=GIT_TIMEOUT)
        if res.get("ok") and res.get("rc") == 0:
            m["files"] = _norm_files((res.get("stdout") or "").splitlines())
        else:
            m["files"] = []
            m["files_error"] = ("git diff 失败（%s）"
                                % (res.get("error") or ("rc=%s" % res.get("rc"))))
            warnings.append("merge %s 取改动失败：%s"
                            % (m["sha"][:8], m["files_error"]))
    return merges


def load_merges(repo: Path, days: int, max_merges: int,
                warnings: list[str]) -> dict:
    """取窗口内 first-parent merge（含改动文件；失败回退/如实标注）。"""
    since = "%d days ago" % max(int(days), 1)
    cmd = _format_merge_log(["--since=" + since])
    if max_merges > 0:
        cmd.insert(cmd.index("--format=@@%H%x1f%P%x1f%cI%x1f%s"),
                   "--max-count=%d" % max_merges)
    res = _git(repo, *cmd)
    info = {"cmd": ["git", "-C", str(repo), "-c", "core.quotepath=false"] + cmd,
            "source": "git-log", "returncode": res.get("rc"),
            "error": res.get("error"), "count": 0, "fallback": None}
    if not (res.get("ok") and res.get("rc") == 0):
        # 1) 回退：不带 --name-only/--diff-merges 取 merge 列表，再逐个 diff
        warnings.append("git log --diff-merges 取改动失败（%s），回退逐 merge diff"
                        % (res.get("error") or ("rc=%s" % res.get("rc"))))
        cmd2 = _format_merge_log(["--since=" + since])
        cmd2 = [c for c in cmd2 if c not in ("--name-only",
                                             "--diff-merges=first-parent")]
        res2 = _git(repo, *cmd2)
        if not (res2.get("ok") and res2.get("rc") == 0):
            return {"merges": [], "info": {**info, "error": (
                "git log 取 merge 失败：%s" % (res2.get("error")
                                              or ("rc=%s" % res2.get("rc"))))}}
        merges = _parse_merge_log(res2.get("stdout") or "")
        merges = merges[:max_merges] if max_merges > 0 else merges
        merges = _fallback_merge_files(repo, merges, warnings)
        info.update({"fallback": "per-merge-diff", "count": len(merges)})
        return {"merges": merges, "info": info}
    merges = _parse_merge_log(res.get("stdout") or "")
    if max_merges > 0:
        merges = merges[:max_merges]
    info["count"] = len(merges)
    return {"merges": merges, "info": info}


def map_introducing_merges(repo: Path, sheets: list[dict], merge_by_sha: dict,
                           warnings: list[str]) -> dict:
    """PASS 验收单 sha → 引入它的 first-parent merge（用于 merge_range 覆盖口径）。

    对每张单执行 `git rev-list --first-parent --ancestry-path <sha>..HEAD`，
    取列表最后一个（最老的 first-parent 后代）= 引入该提交的 merge。
    """
    mapping = {}
    errors = 0
    for sheet in sheets:
        sha = sheet["sha"]
        res = _git(repo, "rev-list", "--first-parent", "--ancestry-path",
                   "%s..HEAD" % sha, timeout=GIT_TIMEOUT)
        if not (res.get("ok") and res.get("rc") == 0):
            errors += 1
            warnings.append("取 %s 的引入 merge 失败（%s）"
                            % (sha[:8], res.get("error")
                               or ("rc=%s" % res.get("rc"))))
            continue
        lines = [ln.strip() for ln in (res.get("stdout") or "").splitlines()
                 if ln.strip()]
        if not lines:
            continue
        intro = lines[-1]
        if intro in merge_by_sha:
            mapping[sheet["sha8"]] = intro
        else:
            # 最老后代不是窗口内 merge（窗口外/直接提交）——记录但不计覆盖
            mapping[sheet["sha8"]] = intro if intro in merge_by_sha else None
    return {"mapping": mapping, "errors": errors}


# ------------------------------------------------------------------ 模拟评估

def _matched_sheets(merge: dict, match_mode: str, sheets: list[dict],
                    intro_map: dict) -> list[dict]:
    """按匹配口径取该 merge 的 PASS 验收单。"""
    by_sha = {s["sha8"]: s for s in sheets}
    if match_mode == "merge_sha":
        hit = by_sha.get(merge["sha8"])
        return [hit] if hit else []
    if match_mode == "branch_tip":
        keys = {merge["sha8"]}
        keys.update(p[:8].lower() for p in merge.get("parents", [])[1:]
                    if len(p) >= 8)
        return [by_sha[k] for k in keys if k in by_sha]
    if match_mode == "merge_range":
        out = []
        for s in sheets:
            # 验收单即 merge commit 本身（罕见于日常）或由该 merge 引入的提交 → 覆盖
            if s["sha8"] == merge["sha8"] or \
                    intro_map.get(s["sha8"]) == merge["sha"]:
                out.append(s)
        return out
    return []


def evaluate_merge(merge: dict, rules: dict, sheets: list[dict],
                   intro_map: dict, now: dt.datetime) -> dict:
    """单个 merge：风险分类 → 模拟 block/pass → 真实验收情况 → 误杀候选。"""
    files = merge.get("files") or []
    risk, risk_reasons = risk_level(files)
    rule_risk = risk if risk in RISK_KEYS else "low"
    rule = rules["rules"][rule_risk]
    sim_matched = _matched_sheets(merge, rules["sim_match"], sheets, intro_map)
    sim_pass = [s for s in sim_matched]
    sim_has_pass = bool(sim_pass)
    sim_fresh = [s for s in sim_pass if s["fresh"] is True]
    sim_has_fresh = bool(sim_fresh)
    no_acceptance = not sim_has_pass
    evidence_stale = sim_has_pass and not sim_has_fresh
    reasons: list[str] = []
    block = False
    if no_acceptance and rule["block_if_no_acceptance"]:
        block = True
        reasons.append("无 PASS 验收单（模拟口径 %s）" % rules["sim_match"])
    if evidence_stale and rule["block_if_evidence_stale"]:
        block = True
        stale_notes = "、".join(
            "%s %s" % (s["sha8"], s["fresh_note"]) for s in sim_pass
            if s["fresh"] is not True)
        reasons.append("验收证据超龄/年龄不可判定（%s）" % stale_notes)
    if not block:
        if risk == "none":
            reasons.append("无改动文件 → 低风险不拦")
        else:
            reasons.append("%s 风险按规则不拦" % rule_risk)
    actual_matched = _matched_sheets(merge, rules["actual_match"], sheets,
                                     intro_map)
    actual_pass = bool(actual_matched)
    actual_fresh = any(s["fresh"] is True for s in actual_matched)
    false_kill = bool(block and actual_pass and actual_fresh)
    if false_kill:
        reasons.append("误杀候选：实际 %s 口径存在新鲜 PASS 验收"
                       % rules["actual_match"])
    return {
        "merge_sha": merge["sha"],
        "sha8": merge["sha8"],
        "date": merge.get("date"),
        "subject": merge.get("subject") or "",
        "risk": risk,
        "rule_risk": rule_risk,
        "risk_reasons": _cap(risk_reasons),
        "changed_files": _cap(files),
        "changed_files_count": len(files),
        "sim": {"decision": "block" if block else "pass",
                "reasons": reasons,
                "match": rules["sim_match"],
                "has_pass_acceptance": sim_has_pass,
                "has_fresh_evidence": sim_has_fresh,
                "matched_sheets": _cap([
                    {"file": s["file"], "sha8": s["sha8"],
                     "fresh": s["fresh"], "fresh_note": s["fresh_note"],
                     "max_evidence_age_hours": s["max_evidence_age_hours"]}
                    for s in sim_pass])},
        "actual": {"match": rules["actual_match"],
                   "has_pass_acceptance": actual_pass,
                   "has_fresh_evidence": actual_fresh,
                   "matched_sheets": _cap([
                       {"file": s["file"], "sha8": s["sha8"],
                        "fresh": s["fresh"], "fresh_note": s["fresh_note"],
                        "max_evidence_age_hours": s["max_evidence_age_hours"]}
                       for s in actual_matched])},
        "false_kill_candidate": false_kill,
        "files_error": merge.get("files_error"),
    }


def _summarize(results: list[dict], days: int, rules: dict,
               acceptance: dict, warnings: list[str]) -> dict:
    total = len(results)
    risk_counts = {k: 0 for k in RISK_KEYS}
    risk_counts["none"] = 0
    for r in results:
        risk_counts[r["risk"]] = risk_counts.get(r["risk"], 0) + 1
    blocked = [r for r in results if r["sim"]["decision"] == "block"]
    false_kills = [r for r in results if r["false_kill_candidate"]]
    actual_pass = [r for r in results if r["actual"]["has_pass_acceptance"]]
    actual_fresh = [r for r in results if r["actual"]["has_fresh_evidence"]]
    sim_blocked = len(blocked)
    fk = len(false_kills)
    sim_block_rate = (sim_blocked / total) if total else None
    notes = []
    data_sufficient = bool(total > 0 and acceptance["sheets"])
    if total == 0:
        notes.append("窗口内无 merge 数据 → 无法给出模拟拦截/误杀率（数据不足）")
    if not acceptance["sheets"]:
        notes.append("窗口内无 PASS 验收单 → 误杀候选无法判定（数据不足）")
    if total and acceptance["sheets"] and not false_kills:
        if rules["sim_match"] == rules["actual_match"]:
            notes.append("sim 与 actual 同口径（%s）→ 误杀候选按构造为 0"
                         "（不适用独立误杀检测）" % rules["sim_match"])
        else:
            notes.append("模拟拦截中无误杀候选（actual 口径无新鲜 PASS 验收覆盖）")
    if total and sim_blocked and sim_block_rate >= 0.5:
        notes.append("模拟拦截率 %.1f%%：当前 PASS 验收仅覆盖 %d/%d 个 merge；"
                     "直接启用阻断会大面积拦截，需先补验收覆盖/对齐验收口径"
                     % (sim_block_rate * 100, len(actual_pass), total))
    if total and sim_blocked == 0:
        notes.append("模拟拦截 0 单 → 误杀率无分母（不适用）")
    return {
        "window_days": days,
        "merges": total,
        "risk_counts": risk_counts,
        "sim_blocked": sim_blocked,
        "sim_block_rate": round(sim_blocked / total, 4) if total else None,
        "false_kill_candidates": fk,
        "false_kill_rate": round(fk / sim_blocked, 4) if sim_blocked else None,
        "actual_pass_merges": len(actual_pass),
        "actual_fresh_pass_merges": len(actual_fresh),
        "acceptance_sheets": len(acceptance["sheets"]),
        "acceptance_files": acceptance["files"],
        "acceptance_invalid": acceptance["invalid_count"],
        "sim_match": rules["sim_match"],
        "actual_match": rules["actual_match"],
        "max_evidence_age_hours": rules["max_evidence_age_hours"],
        "data_sufficient": data_sufficient,
        "notes": notes,
    }


# ------------------------------------------------------------------ MD 报告

def _render_md(rep: dict) -> str:
    s = rep["summary"]
    md = ["# 黄忠阻断分级模拟报告（P3-D，只读模拟）", "",
          "- 生成时间：%s（Asia/Shanghai）" % rep["generated_at"],
          "- 仓库根：`%s`；工作目录：`%s`" % (rep["repo_root"], rep["work_dir"]),
          "- 窗口：近 **%d** 天（first-parent merge）" % s["window_days"],
          "- 规则来源：**%s**%s"
          % (rep["rules"]["source"],
             "（`%s`）" % rep["rules"]["path"] if rep["rules"].get("path") else
             "（内置默认规则）"),
          "- 模拟口径：sim_match=**%s**（模拟 block 判定）；actual_match=**%s**"
            "（真实验收/误杀判定）；证据年龄门 ≤%.0fh"
          % (s["sim_match"], s["actual_match"], s["max_evidence_age_hours"]),
          "- **只读模拟：不接入 merge、不改变 devtask/merge 行为、不阻断任何东西**",
          "",
          "## 一、汇总", "",
          "| 指标 | 值 |", "|---|---|",
          "| 窗口内 merge 数 | %d |" % s["merges"],
          "| 风险分布 | high=%d / medium=%d / low=%d / none=%d |"
          % (s["risk_counts"].get("high", 0),
             s["risk_counts"].get("medium", 0),
             s["risk_counts"].get("low", 0),
             s["risk_counts"].get("none", 0)),
          "| 模拟拦截数 | **%d**（拦截率 %s） |"
          % (s["sim_blocked"],
             ("%.1f%%" % (s["sim_block_rate"] * 100))
             if _num(s["sim_block_rate"]) else "?"),
          "| 误杀候选数 | **%d**（误杀率 %s） |"
          % (s["false_kill_candidates"],
             ("%.1f%%" % (s["false_kill_rate"] * 100))
             if _num(s["false_kill_rate"]) else "?"),
          "| 真实验收 PASS 覆盖的 merge | %d（其中证据新鲜 %d） |"
          % (s["actual_pass_merges"], s["actual_fresh_pass_merges"]),
          "| PASS 验收单 | %d 张（坏文件 %d） |"
          % (s["acceptance_sheets"], s["acceptance_invalid"]),
          "| 数据充分性 | %s |"
          % ("充分" if s["data_sufficient"] else "**数据不足（如实报告）**"),
          ""]
    if s["notes"]:
        md += ["- 数据说明：%s" % "；".join(s["notes"]), ""]
    md += ["## 二、模拟拦截清单", ""]
    blocked = [r for r in rep["results"] if r["sim"]["decision"] == "block"]
    if blocked:
        md += ["| merge | 日期 | 风险 | 原因 |", "|---|---|---|---|"]
        for r in blocked[:60]:
            md.append("| %s | %s | %s | %s |"
                      % (r["sha8"], (r["date"] or "")[:19], r["risk"],
                         "；".join(r["sim"]["reasons"])))
        if len(blocked) > 60:
            md.append("| … | | | 其余 %d 单见 JSON |" % (len(blocked) - 60))
    else:
        md.append("- 无（模拟拦截 0 单）")
    md += ["", "## 三、误杀候选清单（模拟 block 但实际新鲜 PASS 验收）", ""]
    fk = [r for r in rep["results"] if r["false_kill_candidate"]]
    if fk:
        md += ["| merge | 日期 | 风险 | 实际验收 |", "|---|---|---|---|"]
        for r in fk[:60]:
            sheets = "、".join(x["file"] for x in
                               r["actual"]["matched_sheets"][:3]) or "—"
            md.append("| %s | %s | %s | %s |"
                      % (r["sha8"], (r["date"] or "")[:19], r["risk"], sheets))
        if len(fk) > 60:
            md.append("| … | | | 其余 %d 单见 JSON |" % (len(fk) - 60))
        md.append("")
        md.append("> 误杀候选 = 严格模拟口径（%s）判 block，但实际覆盖口径"
                  "（%s）存在新鲜 PASS 验收。启用阻断前需先对齐验收覆盖口径"
                  "（如 merge_range/branch_tip），否则会误拦已完成验收的 merge。"
                  % (s["sim_match"], s["actual_match"]))
    else:
        md.append("- 无")
    md += ["", "## 四、未拦截 merge（抽样）", ""]
    passed = [r for r in rep["results"] if r["sim"]["decision"] == "pass"]
    if passed:
        md += ["| merge | 日期 | 风险 | 理由 |", "|---|---|---|---|"]
        for r in passed[:30]:
            md.append("| %s | %s | %s | %s |"
                      % (r["sha8"], (r["date"] or "")[:19], r["risk"],
                         "；".join(r["sim"]["reasons"])))
    else:
        md.append("- 无")
    md += ["", "## 五、规则口径", "",
           "- sim_match=`%s` / actual_match=`%s` / 证据年龄门 ≤%.0fh"
           % (s["sim_match"], s["actual_match"],
              s["max_evidence_age_hours"]),
           "- high：无验收→block=%s；证据超龄→block=%s"
           % (rep["rules"]["config"]["rules"]["high"]["block_if_no_acceptance"],
              rep["rules"]["config"]["rules"]["high"]["block_if_evidence_stale"]),
           "- medium：无验收→block=%s；证据超龄→block=%s"
           % (rep["rules"]["config"]["rules"]["medium"]["block_if_no_acceptance"],
              rep["rules"]["config"]["rules"]["medium"]["block_if_evidence_stale"]),
           "- low：不拦",
           "- 改动风险分类：复用 tools/huangzhong_impact.py 的 impact 思路"
           "（docs→low；高关键词/quant_backtest→high；tools/portal/"
           "agent_platform→medium；其他→medium 保守）"]
    if rep.get("warnings"):
        md += ["", "## 六、Warnings（fail-visible）", ""]
        md += ["- " + w for w in rep["warnings"]]
    else:
        md += ["", "## 六、Warnings（fail-visible）", "", "- 无"]
    md += ["", "## 七、复跑命令", "",
           "```powershell",
           "cd C:\\项目\\老田的AI助手",
           "C:\\项目\\py312\\python.exe -X utf8 tools\\huangzhong_blocking_sim.py "
           "--days 7 --json",
           "```",
           "",
           "> 本报告只是模拟数据，不改变 merge 行为；是否启用阻断分级由老田 L3 "
           "决策，验收覆盖口径需先对齐。", ""]
    return "\n".join(md)


# ------------------------------------------------------------------ 主流程

def run(days: int = 7, rules_path: Path | str | None = None,
        work_dir: Path | str | None = None,
        repo_root: Path | str | None = None,
        out_dir: Path | str | None = None, write_report: bool = True,
        max_merges: int = 0, now: dt.datetime | None = None) -> dict:
    """执行阻断分级模拟（只读），返回 JSON 结构并可选落 MD。"""
    now = now or _now()
    root = Path(repo_root) if repo_root else ROOT
    work = Path(work_dir) if work_dir else root / DEFAULT_WORK_SUBDIR
    out = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    days = int(days) if int(days) > 0 else 7
    warnings: list[str] = []

    loaded = load_rules(rules_path)
    if not loaded["ok"]:
        return {"ok": False, "tool": "huangzhong_blocking_sim.py",
                "version": "v1 (2026-11-01)", "generated_at": _iso(now),
                "repo_root": str(root), "work_dir": str(work),
                "read_only": True, "error": loaded["error"],
                "exit_code": 2,
                "rules": {"source": loaded["source"],
                          "path": loaded["path"], "error": loaded["error"]},
                "warnings": warnings, "report_md": None}
    rules = loaded["rules"]
    _log("规则加载：source=%s；窗口 %d 天" % (loaded["source"], days))

    got = load_merges(root, days, max_merges, warnings)
    if got.get("info", {}).get("error"):
        return {"ok": False, "tool": "huangzhong_blocking_sim.py",
                "version": "v1 (2026-11-01)", "generated_at": _iso(now),
                "repo_root": str(root), "work_dir": str(work),
                "read_only": True, "error": got["info"]["error"],
                "exit_code": 2,
                "rules": {"source": loaded["source"],
                          "path": loaded["path"],
                          "config": rules},
                "warnings": warnings, "report_md": None}
    merges = got["merges"]
    _log("窗口内 merge：%d 个" % len(merges))

    acceptance = load_acceptance(work, rules["max_evidence_age_hours"])
    for row in acceptance["invalid"]:
        warnings.append("验收单不可读已跳过：%s（%s）"
                        % (row["file"], row["error"]))
    _log("PASS 验收单：%d 张（坏 %d）"
         % (len(acceptance["sheets"]), acceptance["invalid_count"]))

    merge_by_sha = {m["sha"]: m for m in merges}
    intro = map_introducing_merges(root, acceptance["sheets"], merge_by_sha,
                                   warnings) if merges else {"mapping": {},
                                                             "errors": 0}
    intro_map = intro["mapping"]
    results = []
    for merge in merges:
        results.append(evaluate_merge(merge, rules, acceptance["sheets"],
                                      intro_map, now))
    summary = _summarize(results, days, rules, acceptance, warnings)
    if intro["errors"]:
        warnings.append("有 %d 张验收单的引入 merge 无法定位（merge_range "
                        "覆盖口径可能偏小，fail-visible）" % intro["errors"])
    if got["info"].get("fallback"):
        warnings.append("git log --diff-merges 不可用，已回退逐 merge diff"
                        "（慢但结果一致）")
    partial = any(r.get("files_error") for r in results)
    if partial:
        warnings.append("有 %d 个 merge 取改动失败（已计入 low/无法分类，"
                        "fail-visible）" % sum(1 for r in results
                                              if r.get("files_error")))
    data_issues = bool(acceptance["invalid_count"] or intro["errors"] or partial
                       or got["info"].get("fallback"))
    exit_code = 1 if data_issues else 0

    report_md = None
    if write_report:
        report_md = str(out / ("%s_%s.md"
                               % (REPORT_PREFIX, now.strftime("%Y%m%d"))))
    rep = {
        "ok": True,
        "tool": "huangzhong_blocking_sim.py",
        "version": "v1 (2026-11-01)",
        "generated_at": _iso(now),
        "repo_root": str(root),
        "work_dir": str(work),
        "read_only": True,
        "window_days": days,
        "exit_code": exit_code,
        "summary": summary,
        "results": results,
        "warnings": warnings,
        "merges_input": {
            "source": got["info"].get("source"),
            "count": len(merges),
            "fallback": got["info"].get("fallback"),
            "since": "%d days ago" % days,
        },
        "acceptance": {"files": acceptance["files"],
                       "pass_sheets": len(acceptance["sheets"]),
                       "invalid": acceptance["invalid"],
                       "invalid_count": acceptance["invalid_count"],
                       "work_dir": str(work)},
        "intro_map": {"mapped": len([v for v in intro_map.values() if v]),
                      "errors": intro["errors"]},
        "rules": {"source": loaded["source"], "path": loaded["path"],
                  "config": rules},
        "report_md": report_md,
        "report_written": False,
    }
    if write_report:
        try:
            out.mkdir(parents=True, exist_ok=True)
            Path(report_md).write_text(_render_md(rep), encoding="utf-8")
            rep["report_written"] = True
            _log("报告已落盘：%s" % report_md)
        except OSError as e:
            msg = "报告落盘失败：%s: %s" % (type(e).__name__, e)
            rep["report_error"] = msg
            warnings.append(msg)
            _log(msg)
    _log("模拟完成：merge=%d 模拟拦截=%d 误杀候选=%d exit=%d"
         % (summary["merges"], summary["sim_blocked"],
            summary["false_kill_candidates"], rep["exit_code"]))
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="黄忠阻断分级模拟（P3-D，只读；不改变 merge 行为）")
    ap.add_argument("--json", action="store_true", help="输出完整模拟 JSON")
    ap.add_argument("--days", type=int, default=7, help="模拟窗口天数（默认 7）")
    ap.add_argument("--rules", default="",
                    help="规则 JSON（可选；缺省用内置默认规则）")
    ap.add_argument("--work-dir", default="",
                    help="验收单目录（默认 <repo-root>/work/huangzhong）")
    ap.add_argument("--repo-root", default="",
                    help="仓库根（默认工具所在仓库；worktree 内用主仓数据时显式指定）")
    ap.add_argument("--out-dir", default="",
                    help="Markdown 报告输出目录（默认 C:\\项目\\deliverables）")
    ap.add_argument("--no-write", action="store_true", help="不落盘 Markdown 报告")
    ap.add_argument("--max-merges", type=int, default=0,
                    help="最多处理 merge 数（0=不限制；调试/控时用）")
    args = ap.parse_args(argv)
    rep = run(days=args.days, rules_path=args.rules or None,
              work_dir=args.work_dir or None,
              repo_root=args.repo_root or None,
              out_dir=args.out_dir or None,
              write_report=not args.no_write,
              max_merges=args.max_merges)
    if args.json or not rep.get("ok"):
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        s = rep.get("summary") or {}
        print("阻断分级模拟：merge=%s；模拟拦截=%s（%s）；误杀候选=%s（%s）；"
              "报告：%s"
              % (s.get("merges"), s.get("sim_blocked"),
                 ("%.1f%%" % (s["sim_block_rate"] * 100))
                 if _num(s.get("sim_block_rate")) else "?",
                 s.get("false_kill_candidates"),
                 ("%.1f%%" % (s["false_kill_rate"] * 100))
                 if _num(s.get("false_kill_rate")) else "?",
                 rep.get("report_md")))
    return int(rep.get("exit_code", 2))


if __name__ == "__main__":
    raise SystemExit(main())
