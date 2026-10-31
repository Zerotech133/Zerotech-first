# -*- coding: utf-8 -*-
"""黄忠风险加权覆盖率 2.0（只读，纯 stdlib）。

补足「runs / merge_done」口径易被 docs/自动重建等低风险改动稀释的问题：
按 merge 改动的**风险类型**加权计算验收覆盖率，并给出 strict / 有效归属两个口径、
等权覆盖率、旧口径（runs/merge_done、unique-commit）对照与 11-04 投影。

工单：WO-HZ-RISKCOV-20261031（P0-3 第四轮）。

覆盖判定（fail-visible，不编造）：
  · merge 集合：``git log --merges --since=<窗口起点>``（只读）；
  · 每个 merge 的改动文件：``git log --name-only --first-parent``（批量 1 次取全窗口；
    对「相对第一父无改动」的 merge 再用 ``git show --name-only --first-parent``
    单提交复核，仍为空则记 empty/unknown，不猜）；
  · 验收单：``work/huangzhong/acceptance_*.json``，同一 commit.sha 多张取最新；
    最新结论 PASS 记「已覆盖」；演练单排除（drills/drill_ledger_*.json 文件名/sha8、
    单内 drill=true、同名 .md 首行 drill=true 兜底）；
  · **有效归属**（主口径）：验收目标 commit 若不是 merge 提交（实际 HEAD 常是
    merge 后钩子生成的 ``chore(ide): auto rebuild`` 提交），沿 first-parent 上溯
    至最近 merge 祖先（≤ --max-hops，默认 5），归属该 merge；
  · **strict 口径**（补充展示）：仅验收目标 sha 与 merge sha 完全相等的覆盖。

分类（merge 内取最高风险类别；未知路径 fail-visible 计数且**不进加权分母**）：
  docs    `docs/` 前缀或 `.md` 结尾（优先级最高，避免 docs 内 auth/secret 字样误判）
  data    `quant_backtest/data`、`*.sql`、`migration`/`migrations`
  security `auth` / `secret` / `permission` / `credential` / `token` / `password` 关键词
  code    `tools/` `portal/` `agent_platform/` `quant_backtest/`（其余部分）
  unknown 其它路径（含无改动文件的 merge）

风险权重默认：data=3.0、security=3.0、code=1.0、docs=0.5（``--weights`` 可覆盖）；
**风险加权覆盖率 = Σ(covered_weight) / Σ(merge_weight)**；等权覆盖率 = covered/merges。

用法：
  python -X utf8 tools/huangzhong_risk_coverage.py --days 7 --json
  python -X utf8 tools/huangzhong_risk_coverage.py --days 7 --weights data=4,security=4,code=1,docs=0.5 --json
  python -X utf8 tools/huangzhong_risk_coverage.py --days 7 --target 2026-11-04T10:00 --out-dir C:\\项目\\deliverables
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

VERSION = "v1"
DEFAULT_DAYS = 7
DEFAULT_WEIGHTS = {"data": 3.0, "security": 3.0, "code": 1.0, "docs": 0.5}
TYPES = ("data", "security", "code", "docs")
CATEGORY_PRIORITY = ("security", "data", "code", "docs")  # 同 merge 多类别取最高风险
SECURITY_KEYWORDS = ("auth", "secret", "permission", "credential", "token", "password")
CODE_PREFIXES = ("tools/", "portal/", "agent_platform/", "quant_backtest/")
DEFAULT_MAX_HOPS = 5
COVERAGE_THRESHOLD = 0.10  # Go 门槛（沿用 runbook，仅作提示）

REPO = Path(__file__).resolve().parents[1]
MERGE_MARK = "__MERGE__"

Runner = Callable[[list[str]], "tuple[int, str, str]"]


# --------------------------------------------------------------------- 基础

def _run(args: list[str], repo: Path | str | None = None) -> tuple[int, str, str]:
    """默认 git 执行器（只读；测试可注入 run_fn 或 monkeypatch 本函数）。"""
    repo_path = str(repo if repo is not None else REPO)
    cmd = ["git", "-C", repo_path, "-c", "core.quotepath=false"] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.SubprocessError) as exc:  # noqa: BLE001
        return 127, "", "%s: %s" % (type(exc).__name__, str(exc)[:200])


def _make_runner(repo: Path, run_fn: Runner | None) -> Runner:
    if run_fn is not None:
        return run_fn
    return lambda args: _run(args, repo)


def _parse_ts(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    try:
        out = dt.datetime.fromisoformat(s)
    except ValueError:
        out = None
    if out is None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                out = dt.datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if out is None:
        return None
    if out.tzinfo is None:
        out = out.astimezone()
    return out


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def _pct(value: float | None) -> str:
    return "—" if value is None else "%.2f%%" % (value * 100.0)


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return ("%d" % value) if value.is_integer() else ("%.2f" % value)
    return str(value)


def _read_json(p: Path) -> tuple[dict | None, str | None]:
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
    except OSError:
        return None, "read_error"
    except ValueError as exc:
        return None, "json_error: %s" % str(exc)[:80]
    if not isinstance(data, dict):
        return None, "top_level_not_object"
    return data, None


def _read_jsonl(p: Path) -> tuple[list[dict], int, str | None]:
    try:
        text = Path(p).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], 0, "missing"
    rows: list[dict] = []
    bad = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(obj, dict):
            rows.append(obj)
        else:
            bad += 1
    return rows, bad, None


# --------------------------------------------------------------------- 分类

def classify_file(path: str) -> str:
    """单文件路径 → 风险类别（规则顺序：docs > data > security > code > unknown）。"""
    p = str(path or "").strip().replace("\\", "/").lower()
    if not p:
        return "unknown"
    if p.startswith("docs/") or p.endswith(".md"):
        return "docs"
    if "quant_backtest/data" in p or p.endswith(".sql") or "migration" in p:
        return "data"
    if any(k in p for k in SECURITY_KEYWORDS):
        return "security"
    if p.startswith(CODE_PREFIXES):
        return "code"
    return "unknown"


def classify_merge(files: list[str]) -> str:
    """merge 级分类：取改动文件中的最高风险类别；无识别类别 → unknown。"""
    kinds = {classify_file(f) for f in files if str(f).strip()}
    for cat in CATEGORY_PRIORITY:
        if cat in kinds:
            return cat
    return "unknown"


def parse_weights(raw: str) -> dict:
    """``data=3,security=3,code=1,docs=0.5``（兼容 ``:`` 分隔）→ 权重 dict。"""
    weights = dict(DEFAULT_WEIGHTS)
    if not raw or not str(raw).strip():
        return weights
    for item in str(raw).replace("，", ",").split(","):
        item = item.strip()
        if not item:
            continue
        m = re.match(r"^([A-Za-z_]+)\s*[=:]\s*(-?\d+(?:\.\d+)?)$", item)
        if not m:
            raise ValueError("权重项无法解析：%r（示例 data=3,code=1）" % item)
        key, val = m.group(1).lower(), float(m.group(2))
        if key not in DEFAULT_WEIGHTS:
            raise ValueError("未知权重类别：%s（允许 data/security/code/docs）" % key)
        if val < 0 or val != val or val in (float("inf"), float("-inf")):
            raise ValueError("权重须为有限非负数：%s=%s" % (key, m.group(2)))
        weights[key] = val
    return weights


# --------------------------------------------------------------------- git 数据

def load_merges(runner: Runner, since_iso: str) -> dict:
    """窗口内 merge 提交 + 改动文件（批量 1 次；空文件 merge 单提交复核）。"""
    args = ["log", "--merges", "--since=" + since_iso, "--name-only", "--first-parent",
            "--pretty=format:%s%%H%%x09%%cI%%x09%%s" % MERGE_MARK]
    rc, out, err = runner(args)
    if rc != 0:
        return {"ok": False, "error": (err or "git_log_failed").strip()[:200],
                "merges": [], "raw_lines": 0, "fallback_used": 0, "fallback_failed": 0}
    merges: list[dict] = []
    cur: dict | None = None
    for line in out.splitlines():
        if line.startswith(MERGE_MARK):
            head = line[len(MERGE_MARK):]
            parts = head.split("\t")
            cur = {"sha": parts[0].strip(), "date": parts[1].strip() if len(parts) > 1 else "",
                   "subject": parts[2].strip() if len(parts) > 2 else "", "files": []}
            merges.append(cur)
        elif line.strip() and cur is not None:
            f = line.strip()
            if f not in cur["files"]:
                cur["files"].append(f)
    # 空文件 merge：逐提交 git show 复核（规格要求的 --first-parent 语义）
    fallback_used = fallback_failed = 0
    for m in merges:
        if m["files"]:
            continue
        fallback_used += 1
        rc2, out2, _err2 = runner(["show", "--name-only", "--first-parent",
                                   "--format=", m["sha"]])
        if rc2 == 0:
            files = [x.strip() for x in out2.splitlines() if x.strip()]
            if files:
                m["files"] = files
                continue
        fallback_failed += 1
    return {"ok": True, "error": None, "merges": merges, "raw_lines": len(out.splitlines()),
            "fallback_used": fallback_used, "fallback_failed": fallback_failed}


def _parents_of(runner: Runner, shas: list[str]) -> tuple[dict[str, list[str]], str | None]:
    """批量取父提交（rev-list --parents --no-walk，按 100 个一批）。"""
    out: dict[str, list[str]] = {}
    shas = [s for s in dict.fromkeys(shas) if s]
    for i in range(0, len(shas), 100):
        chunk = shas[i:i + 100]
        rc, stdout, err = runner(["rev-list", "--parents", "--no-walk"] + chunk)
        if rc != 0:
            return out, (err or "git_rev_list_failed").strip()[:200]
        for line in stdout.splitlines():
            parts = line.split()
            if parts:
                out[parts[0]] = parts[1:]
    return out, None


def attribute_targets(runner: Runner, targets: list[str], merge_shas: set[str],
                      max_hops: int = DEFAULT_MAX_HOPS) -> dict:
    """验收目标 sha → 最近 first-parent merge 祖先（≤ max_hops）。

    返回 {"map": {target: {"merge": sha, "hops": n}}, "strict": [...],
          "unresolved": [...], "errors": [...], "hops_hist": {...}}
    """
    targets = [t for t in dict.fromkeys(targets) if t]
    result: dict[str, dict] = {}
    strict = [t for t in targets if t in merge_shas]
    for t in strict:
        result[t] = {"merge": t, "hops": 0}
    walk = {t: (t, 0) for t in targets if t not in merge_shas}
    unresolved: list[str] = []
    errors: list[str] = []
    hops_hist: dict[str, int] = {}
    cache: dict[str, list[str]] = {}
    hop = 0
    while walk and hop < max_hops:
        hop += 1
        need = [sha for sha, _h in walk.values() if sha not in cache]
        if need:
            got, err = _parents_of(runner, need)
            cache.update(got)
            if err:
                errors.append(err)
        nxt: dict[str, tuple[str, int]] = {}
        for target, (sha, hops) in walk.items():
            parents = cache.get(sha) or []
            if not parents:
                unresolved.append(target)
                continue
            first = parents[0]
            if first in merge_shas:
                result[target] = {"merge": first, "hops": hops + 1}
                hops_hist[str(hops + 1)] = hops_hist.get(str(hops + 1), 0) + 1
            elif hops + 1 >= max_hops:
                unresolved.append(target)
            else:
                nxt[target] = (first, hops + 1)
        walk = nxt
    unresolved.extend(t for t in walk if t not in result)
    for t in strict:
        hops_hist["0"] = hops_hist.get("0", 0) + 1
    return {"map": result, "strict": strict, "unresolved": sorted(set(unresolved)),
            "errors": errors, "hops_hist": dict(sorted(hops_hist.items())),
            "max_hops": max_hops}


# --------------------------------------------------------------------- 数据源

def load_drill_ids(work_dir: Path) -> dict:
    """演练台账 → 排除集合（文件名 / commit sha8）；坏台账 fail-visible。"""
    work_dir = Path(work_dir)
    paths: list[Path] = []
    for d in (work_dir / "drills", work_dir):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("drill_ledger_*.json")):
            if all(p.resolve() != q.resolve() for q in paths):
                paths.append(p)
    files_lower: set[str] = set()
    sha8: set[str] = set()
    entries = 0
    bad: list[dict] = []
    for p in paths:
        data, err = _read_json(p)
        if err is not None:
            bad.append({"file": p.name, "error": err})
            continue
        rows = data if isinstance(data, list) else None
        if rows is None and isinstance(data, dict):
            for key in ("drills", "entries", "records", "items", "ledger"):
                if isinstance(data.get(key), list):
                    rows = data[key]
                    break
        if rows is None:
            bad.append({"file": p.name, "error": "no_entries_array"})
            continue
        for e in rows:
            if not isinstance(e, dict):
                bad.append({"file": p.name, "error": "entry_not_object"})
                continue
            entries += 1
            f = (e.get("acceptance_report") or e.get("file") or e.get("path")
                 or e.get("acceptance_file") or e.get("report"))
            if isinstance(f, str) and f.strip():
                files_lower.add(Path(f).name.lower())
            commit = e.get("commit") if isinstance(e.get("commit"), dict) else {}
            sha = (e.get("target_commit") or e.get("sha") or e.get("commit_sha")
                   or commit.get("sha")
                   or (e.get("commit") if isinstance(e.get("commit"), str) else ""))
            if isinstance(sha, str) and len(sha.strip()) >= 8:
                sha8.add(sha.strip().lower()[:8])
    return {"files": [str(p) for p in paths], "entries": entries,
            "files_lower": files_lower, "sha8": sha8, "bad": bad}


def _md_marked_drill(json_path: Path) -> bool:
    md = Path(json_path).with_suffix(".md")
    try:
        first = md.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
    except OSError:
        return False
    return bool(first) and "drill=true" in first[0]


def load_acceptance(work_dir: Path, drills: dict) -> dict:
    """验收单 → 按 commit.sha 取最新（排除演练单）；统计坏 JSON/缺 sha 等。"""
    work_dir = Path(work_dir)
    by_sha: dict[str, dict] = {}
    sheets = 0
    excluded_drills: list[str] = []
    invalid: list[dict] = []
    missing_sha: list[str] = []
    missing_ts: list[str] = []
    for p in sorted(work_dir.glob("acceptance_*.json")):
        data, err = _read_json(p)
        if err is not None:
            invalid.append({"file": p.name, "error": err})
            continue
        sheets += 1
        commit = data.get("commit") if isinstance(data.get("commit"), dict) else {}
        sha = commit.get("sha") if isinstance(commit.get("sha"), str) else ""
        sha = sha.strip().lower()
        name = p.name.lower()
        drill_flag = bool(data.get("drill")) or str(data.get("mode") or "").lower() == "drill"
        sha_hit = len(sha) >= 8 and sha[:8] in drills["sha8"]
        if drill_flag or name in drills["files_lower"] or sha_hit or _md_marked_drill(p):
            excluded_drills.append(p.name)
            continue
        if len(sha) < 8:
            missing_sha.append(p.name)
            continue
        stamp = _parse_ts(data.get("generated_at"))
        if stamp is None:
            missing_ts.append(p.name)
            continue
        verdict = str(data.get("verdict") or "").strip().upper()
        row = {"file": p.name, "sha": sha, "verdict": verdict,
               "generated_at": data.get("generated_at"), "stamp": stamp}
        prev = by_sha.get(sha)
        if prev is None or stamp > prev["stamp"]:
            by_sha[sha] = row
    return {"file": str(work_dir), "sheets": sheets, "by_sha": by_sha,
            "excluded_drills": sorted(excluded_drills), "invalid": invalid,
            "missing_sha": missing_sha, "missing_ts": missing_ts}


def load_daily_runs(work_dir: Path, since: dt.datetime, until: dt.datetime) -> dict:
    p = Path(work_dir) / "daily.jsonl"
    rows, bad, err = _read_jsonl(p)
    runs = 0
    missing_ts = 0
    for row in rows:
        ts = _parse_ts(row.get("ts"))
        if ts is None:
            missing_ts += 1
            continue
        if since <= ts <= until:
            runs += 1
    return {"file": str(p), "ok": err is None, "error": err, "rows": len(rows),
            "bad_lines": bad, "missing_ts": missing_ts, "runs_window": runs}


def load_merge_events(repo: Path, since: dt.datetime, until: dt.datetime) -> dict:
    p = Path(repo) / "portal" / "data" / "dev_events.jsonl"
    rows, bad, err = _read_jsonl(p)
    count = 0
    missing_ts = 0
    for row in rows:
        if str(row.get("kind") or "").strip() != "merge_done":
            continue
        ts = _parse_ts(row.get("ts"))
        if ts is None:
            missing_ts += 1
            continue
        if since <= ts <= until:
            count += 1
    return {"file": str(p), "ok": err is None, "error": err, "rows": len(rows),
            "bad_lines": bad, "missing_ts": missing_ts, "merge_done_window": count}


# --------------------------------------------------------------------- 汇总

def _empty_type(weight: float | None) -> dict:
    return {"merges": 0, "covered": 0, "uncovered": 0, "coverage": None,
            "runs": 0, "merge_weight": 0.0 if weight is not None else None,
            "covered_weight": 0.0 if weight is not None else None,
            "uncovered_weight": 0.0 if weight is not None else None,
            "weight": weight, "excluded_from_weighted": weight is None}


def compute(repo_root: Path | str | None = None,
            work_dir: Path | str | None = None,
            days: int = DEFAULT_DAYS,
            now: dt.datetime | str | None = None,
            target: str | dt.datetime | None = None,
            weights: dict | None = None,
            max_hops: int = DEFAULT_MAX_HOPS,
            out_dir: Path | str | None = None,
            run_fn: Runner | None = None) -> dict:
    """主计算（路径/时钟/权重/runner 全可注入）。"""
    repo = Path(repo_root) if repo_root else REPO
    work = Path(work_dir) if work_dir else repo / "work" / "huangzhong"
    if isinstance(now, str):
        now = _parse_ts(now)
    clock = now or dt.datetime.now().astimezone()
    if clock.tzinfo is None:
        clock = clock.astimezone()
    days = max(1, int(days))
    since = clock - dt.timedelta(days=days)
    runner = _make_runner(repo, run_fn)

    w = dict(DEFAULT_WEIGHTS)
    if weights:
        for k, v in weights.items():
            if k in w:
                w[k] = float(v)
    target_dt: dt.datetime | None = None
    if target not in (None, ""):
        if isinstance(target, dt.datetime):
            target_dt = target if target.tzinfo else target.replace(tzinfo=clock.tzinfo)
        else:
            target_dt = _parse_ts(target)
            if target_dt is None:
                raise ValueError("target 无法解析：%r（示例 2026-11-04T10:00）" % (target,))

    # 1) merge 集
    g = load_merges(runner, since.isoformat(timespec="seconds"))
    merges = g["merges"] if g["ok"] else []
    for m in merges:
        m["category"] = classify_merge(m["files"])

    # 2) 验收单 + 演练排除
    drills = load_drill_ids(work)
    acc = load_acceptance(work, drills)
    latest = acc["by_sha"]
    pass_targets = sorted(s for s, v in latest.items() if v["verdict"] == "PASS")
    merge_shas = {m["sha"] for m in merges}
    attr = attribute_targets(runner, pass_targets, merge_shas, max_hops)
    covered_attr = {row["merge"] for row in attr["map"].values()}
    covered_strict = {m["sha"] for m in merges if m["sha"] in attr["strict"]}
    attr_targets = set(attr["map"])
    unresolved_targets = [t for t in pass_targets if t not in attr_targets and
                          t not in set(attr["strict"])]

    # 3) 分类统计（覆盖按有效归属；strict 单列）
    types: dict[str, dict] = {t: _empty_type(w[t]) for t in TYPES}
    types["unknown"] = _empty_type(None)
    type_merges: dict[str, set] = {t: set() for t in types}
    type_covered: dict[str, set] = {t: set() for t in types}
    type_covered_strict: dict[str, set] = {t: set() for t in types}
    for m in merges:
        cat = m["category"]
        type_merges[cat].add(m["sha"])
        if m["sha"] in covered_attr:
            type_covered[cat].add(m["sha"])
        if m["sha"] in covered_strict:
            type_covered_strict[cat].add(m["sha"])
    for cat, rows in types.items():
        n = len(type_merges[cat])
        c = len(type_covered[cat])
        rows["merges"] = n
        rows["covered"] = c
        rows["uncovered"] = n - c
        rows["coverage"] = _round(c / n, 4) if n else None
        if rows["weight"] is not None:
            rows["merge_weight"] = _round(n * rows["weight"], 4)
            rows["covered_weight"] = _round(c * rows["weight"], 4)
            rows["uncovered_weight"] = _round((n - c) * rows["weight"], 4)

    # 4) 每类 acceptance runs（窗口内验收单，按归属 merge 归型；覆盖与否都计 runs）
    def _sheet_in_window(row: dict) -> bool:
        st = row.get("stamp")
        return st is not None and since <= st <= clock

    for sha, row in latest.items():
        if not _sheet_in_window(row):
            continue
        if sha in merge_shas:
            target_merge = sha
        else:
            info = attr["map"].get(sha)
            target_merge = info["merge"] if info else None
        if target_merge is None:
            continue
        cat = next((m["category"] for m in merges if m["sha"] == target_merge), "unknown")
        types[cat]["runs"] += 1
    # 窗口内验收单中无法归属到窗口 merge 的 runs（fail-visible，不丢数）
    runs_other = 0
    for sha, row in latest.items():
        if not _sheet_in_window(row):
            continue
        if sha in merge_shas or sha in attr["map"]:
            continue
        runs_other += 1

    known_types = [types[t] for t in TYPES]
    merge_weight = _round(sum(t["merge_weight"] or 0.0 for t in known_types), 4)
    covered_weight = _round(sum(t["covered_weight"] or 0.0 for t in known_types), 4)
    merges_known = sum(t["merges"] for t in known_types)
    covered_known = sum(t["covered"] for t in known_types)
    merges_total = len(merges)
    covered_total = len(covered_attr)
    risk_coverage = _round(covered_weight / merge_weight, 4) if merge_weight else None
    equal_known = _round(covered_known / merges_known, 4) if merges_known else None
    equal_all = _round(covered_total / merges_total, 4) if merges_total else None

    # strict 口径
    merge_weight_strict = merge_weight  # 分母不变
    covered_weight_strict = _round(
        sum((len(type_covered_strict[t]) * w[t]) for t in TYPES), 4)
    risk_strict = _round(covered_weight_strict / merge_weight, 4) if merge_weight else None

    # 5) 旧口径（runs/merge_done + unique-commit）
    daily = load_daily_runs(work, since, clock)
    ev = load_merge_events(repo, since, clock)
    legacy_rate = (_round(daily["runs_window"] / ev["merge_done_window"], 4)
                   if daily["runs_window"] and ev["merge_done_window"] else None)
    unique_window = {sha for sha, row in latest.items()
                     if row["verdict"] == "PASS" and _sheet_in_window(row)}
    unique_rate = (_round(len(unique_window) / ev["merge_done_window"], 4)
                   if ev["merge_done_window"] else None)
    legacy = {
        "runs_window": daily["runs_window"],
        "merge_done_window": ev["merge_done_window"],
        "runs_merge_done": legacy_rate,
        "unique_pass_targets_window": len(unique_window),
        "unique_commit_coverage": unique_rate,
        "note": "旧口径：runs=work/huangzhong/daily.jsonl 窗口内行数；merge_done="
                "portal/data/dev_events.jsonl 窗口内 kind=merge_done 计数；"
                "unique-commit=窗口内 PASS 验收单去重 commit.sha / merge_done",
    }

    # 6) 投影（可选 --target）
    projection = None
    if target_dt is not None:
        remaining = (target_dt - clock).total_seconds() / 86400.0
        past = remaining <= 0
        remaining = max(0.0, remaining)
        r_days = max(1.0, float(days))
        factor = 1.0 + remaining / r_days if not past else 1.0
        proj_types = {}
        for t in TYPES:
            mr = types[t]["merges"]
            cv = types[t]["covered"]
            proj_types[t] = {"projected_merges": _round(mr * factor, 1),
                             "projected_covered": _round(cv * factor, 1),
                             "merge_weight": types[t]["merge_weight"]}
        proj_merges = _round(merges_total * factor, 1)
        proj_covered = _round(covered_total * factor, 1)
        proj_mw = _round(merge_weight * factor, 4)
        proj_cw = _round(covered_weight * factor, 4)
        projection = {
            "target": target_dt.isoformat(),
            "now": clock.isoformat(timespec="seconds"),
            "remaining_days": _round(remaining, 4),
            "past_target": past,
            "factor": _round(factor, 4),
            "projected_merges": proj_merges,
            "projected_covered": proj_covered,
            "projected_merge_weight": proj_mw,
            "projected_covered_weight": proj_cw,
            # 平稳假设下比例与当前窗口一致（=风险加权覆盖率/等权覆盖率，不随外推变化）
            "projected_risk_weighted_coverage": risk_coverage,
            "projected_equal_coverage": equal_all,
            "types": proj_types,
            "note": ("target 已过去：factor=1，仅回看当前窗口" if past else
                     "平稳假设（近 %d 日类型构成与覆盖率水平不变，按日均 merge/覆盖外推）："
                     "比例不随规模变化，投影给出 11-04 的 merge/covered 绝对量" % days),
        }

    # 7) 汇总 / fail-visible
    warnings: list[str] = []
    if not g["ok"]:
        warnings.append("git 不可用/查询失败（%s）→ merge 集为空，覆盖率不可判定"
                        % (g["error"] or "?"))
    if g["ok"] and not merges:
        warnings.append("窗口内无 merge 提交（--since=%s）" % since.date().isoformat())
    if g["fallback_used"]:
        warnings.append("有 %d 个 merge 在批量 name-only 中无文件，已逐提交 git show 复核"
                        "（仍为空 %d 个，记 unknown 且不进加权分母）"
                        % (g["fallback_used"], g["fallback_failed"]))
    if types["unknown"]["merges"]:
        warnings.append("未知类型 merge %d 个（路径不在 data/security/code/docs 规则内），"
                        "已排除出风险加权分母、单列 unknown（fail-visible）"
                        % types["unknown"]["merges"])
    if drills["bad"]:
        for b in drills["bad"]:
            warnings.append("演练台账异常：%s（%s）" % (b["file"], b["error"]))
    if acc["invalid"]:
        warnings.append("验收单坏 JSON %d 份已跳过" % len(acc["invalid"]))
    if acc["missing_sha"]:
        warnings.append("有 %d 份验收单缺 commit.sha，已跳过" % len(acc["missing_sha"]))
    if acc["missing_ts"]:
        warnings.append("有 %d 份验收单缺/坏 generated_at，已跳过" % len(acc["missing_ts"]))
    if not latest:
        warnings.append("未读到任何有效验收单 → 覆盖数按 0 呈现（不编造）")
    if g["ok"] and latest and not pass_targets:
        warnings.append("验收单最新结论均非 PASS → 覆盖数 0")
    if attr["errors"]:
        warnings.append("验收目标归属查询异常：%s" % "；".join(attr["errors"][:3]))
    if unresolved_targets:
        warnings.append("有 %d 个 PASS 验收目标无法归属到窗口内 merge（越窗/无 merge 祖先）：%s"
                        % (len(unresolved_targets),
                           "、".join(unresolved_targets[:5])))
    if runs_other:
        warnings.append("有 %d 次窗口内验收无法归属到窗口内 merge（计入 runs_other，"
                        "不参与分型统计）" % runs_other)
    if not daily["ok"]:
        warnings.append("daily.jsonl 缺失/不可读（%s）→ 旧口径 runs 记 0" % daily["error"])
    if not ev["ok"]:
        warnings.append("dev_events.jsonl 缺失/不可读（%s）→ 旧口径 merge_done 记 0"
                        % ev["error"])
    if daily["bad_lines"]:
        warnings.append("daily.jsonl 坏行 %d 行已跳过" % daily["bad_lines"])
    if ev["bad_lines"]:
        warnings.append("dev_events.jsonl 坏行 %d 行已跳过" % ev["bad_lines"])
    if risk_coverage is None and g["ok"]:
        warnings.append("风险加权分母为 0（无已知类型 merge）→ 风险加权覆盖率不可判定")
    if risk_coverage is not None and risk_coverage < COVERAGE_THRESHOLD:
        warnings.append("风险加权覆盖率 %s < %.0f%% Go 门槛（观察期正常，如实报告）"
                        % (_pct(risk_coverage), COVERAGE_THRESHOLD * 100))
    if projection is not None and projection["past_target"]:
        warnings.append("target 已过去（now > target）：剩余天数按 0 处理，不做外推，"
                        "仅回看当前窗口")

    rep: dict = {
        "ok": bool(g["ok"]),
        "tool": "huangzhong_risk_coverage",
        "version": VERSION,
        "mode": "read_only",
        "generated_at": clock.isoformat(timespec="seconds"),
        "window": {"days": days, "since": since.isoformat(timespec="seconds"),
                   "until": clock.isoformat(timespec="seconds"),
                   "repo_root": str(repo), "work_dir": str(work)},
        "weights": w,
        "classification_rules": {
            "docs": "docs/ 前缀或 .md 结尾（优先级最高）",
            "data": "quant_backtest/data、*.sql、migration/migrations",
            "security": "auth/secret/permission/credential/token/password 关键词",
            "code": "tools/、portal/、agent_platform/、quant_backtest/（其余部分）",
            "unknown": "其它路径（不编造，不计入加权分母）",
            "merge_level": "同一 merge 取最高风险类别；无识别类别 → unknown",
        },
        "inputs": {
            "git_ok": g["ok"],
            "git_error": g["error"],
            "merges_total": merges_total,
            "merges_with_files": sum(1 for m in merges if m["files"]),
            "empty_file_merges": sum(1 for m in merges if not m["files"]),
            "fallback_used": g["fallback_used"],
            "fallback_failed": g["fallback_failed"],
            "raw_lines": g["raw_lines"],
            "acceptance": {"sheets": acc["sheets"],
                           "latest_by_sha": len(latest),
                           "latest_pass_targets": len(pass_targets),
                           "excluded_drills": acc["excluded_drills"],
                           "invalid": len(acc["invalid"]),
                           "missing_sha": len(acc["missing_sha"]),
                           "missing_generated_at": len(acc["missing_ts"])},
            "drill_ledger_files": drills["files"],
            "drill_entries": drills["entries"],
            "daily": {"ok": daily["ok"], "rows": daily["rows"],
                      "bad_lines": daily["bad_lines"], "runs_window": daily["runs_window"]},
            "events": {"ok": ev["ok"], "rows": ev["rows"],
                       "bad_lines": ev["bad_lines"],
                       "merge_done_window": ev["merge_done_window"]},
            "missing": [x for x in (
                ("daily.jsonl" if not daily["ok"] else ""),
                ("dev_events.jsonl" if not ev["ok"] else "")) if x],
        },
        "summary": {
            "merges_total": merges_total,
            "merges_known": merges_known,
            "merges_unknown": types["unknown"]["merges"],
            "covered_merges": covered_total,
            "covered_merges_strict": len(covered_strict),
            "merge_weight": merge_weight,
            "covered_weight": covered_weight,
            "uncovered_weight": _round(merge_weight - covered_weight, 4),
            "risk_weighted_coverage": risk_coverage,
            "equal_coverage_known": equal_known,
            "equal_coverage_all": equal_all,
            "strict": {
                "covered_merges": len(covered_strict),
                "covered_weight": covered_weight_strict,
                "risk_weighted_coverage": risk_strict,
                "note": "strict=仅验收目标 sha 与 merge sha 完全相等（不含 chore 归属）",
            },
            "attribution": {
                "pass_targets": len(pass_targets),
                "attributed": len(attr_targets),
                "strict_count": len(attr["strict"]),
                "unresolved": len(unresolved_targets),
                "runs_other": runs_other,
                "hops_hist": attr["hops_hist"],
                "max_hops": max_hops,
                "rule": "验收目标沿 first-parent 上溯至最近 merge 祖先（≤ max_hops）",
            },
            "threshold": COVERAGE_THRESHOLD,
            "threshold_pass": (risk_coverage >= COVERAGE_THRESHOLD
                               if risk_coverage is not None else None),
        },
        "types": types,
        "legacy": legacy,
        "projection": projection,
        "warnings": warnings,
        "assumptions": [
            "覆盖率分母=窗口内 merge 提交（git log --merges --since，按 committer date）；"
            "风险加权分母只含 data/security/code/docs 四类，unknown 单列不计入",
            "覆盖判定：acceptance_*.json 同一 commit.sha 取最新；最新为 PASS 记覆盖；"
            "演练单排除（台账文件名/sha8/单内 drill 标记/.md 标注）",
            "有效归属：验收目标若非 merge（常为 merge 后 chore(ide) 自动重建提交），"
            "沿 first-parent 上溯最近 merge 祖先（≤ --max-hops）；strict 口径单列对照",
            "风险权重默认 data=3.0、security=3.0、code=1.0、docs=0.5（--weights 覆盖）；"
            "风险加权覆盖率 = Σ(covered_weight)/Σ(merge_weight)",
            "旧口径对照：runs/merge_done（daily.jsonl / dev_events.jsonl 窗口内计数）、"
            "unique-commit（窗口内去重 PASS commit / merge_done）",
            "投影为平稳假设（近 N 日类型构成与覆盖水平不变）→ 比例不变，只给绝对量",
        ],
        "paths": {},
    }
    rep["inputs"]["missing"] = [x for x in rep["inputs"]["missing"] if x]

    if not g["ok"]:
        rep["warnings"] = warnings

    # 8) md+json 落盘（仅 --out-dir 时；默认 stdout，不写 work/ 与仓库）
    if out_dir:
        out = Path(out_dir)
        ts = clock.strftime("%Y%m%d_%H%M%S")
        prefix = target_dt.date().isoformat() if target_dt else clock.date().isoformat()
        base = "%s_黄忠风险加权覆盖率_%s" % (prefix, ts)
        md_path, json_path = out / (base + ".md"), out / (base + ".json")
        try:
            out.mkdir(parents=True, exist_ok=True)
            md_path.write_text(render_md(rep), encoding="utf-8")
            json_path.write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n",
                                 encoding="utf-8")
            rep["paths"] = {"md": str(md_path), "json": str(json_path), "out_dir": str(out)}
        except OSError as exc:
            rep["ok"] = False
            rep["warnings"].append("报告落盘失败：%s（%s）" % (out, exc))
            rep["paths"] = {"md": None, "json": None, "out_dir": str(out),
                            "write_error": str(exc)}
    return rep


# --------------------------------------------------------------------- 报告

def render_md(rep: dict) -> str:
    s = rep["summary"]
    win = rep["window"]
    types = rep["types"]
    legacy = rep["legacy"]
    lines: list[str] = []
    lines.append("# 黄忠风险加权覆盖率（%s）" % rep["generated_at"][:16])
    lines.append("")
    lines.append("> 工单：WO-HZ-RISKCOV-20261031 ｜ 工具：`tools/huangzhong_risk_coverage.py`"
                 "（只读，纯 stdlib）")
    lines.append("> 窗口：近 %d 天（%s ~ %s）｜ repo：`%s`"
                 % (win["days"], win["since"][:19], win["until"][:19], win["repo_root"]))
    lines.append("> 口径：按 merge 改动类型加权（补足 runs/merge_done 被 docs/自动重建稀释）")
    lines.append("")
    lines.append("## 一、结论摘要")
    lines.append("")
    lines.append("- merge 总数 **%d**（已知类型 %d / unknown %d / 无文件 %d）"
                 % (s["merges_total"], s["merges_known"], s["merges_unknown"],
                    rep["inputs"]["empty_file_merges"]))
    lines.append("- 已覆盖 merge：**%d**（有效归属）；strict 仅 **%d**"
                 % (s["covered_merges"], s["covered_merges_strict"]))
    lines.append("- **风险加权覆盖率 = %s**（Σcovered_weight %s / Σmerge_weight %s；"
                 "权重 %s）"
                 % (_pct(s["risk_weighted_coverage"]), _fmt(s["covered_weight"]),
                    _fmt(s["merge_weight"]),
                    "data=%s,security=%s,code=%s,docs=%s"
                    % (_fmt(rep["weights"]["data"]), _fmt(rep["weights"]["security"]),
                       _fmt(rep["weights"]["code"]), _fmt(rep["weights"]["docs"]))))
    lines.append("- 等权覆盖率：已知类型 **%s**（%d/%d）；含 unknown **%s**（%d/%d）"
                 % (_pct(s["equal_coverage_known"]), s["covered_merges"],
                    s["merges_known"], _pct(s["equal_coverage_all"]),
                    s["covered_merges"], s["merges_total"]))
    lines.append("- 旧口径对照：runs/merge_done = %s（%d/%d）；unique-commit = %s（%d/%d）"
                 % (_pct(legacy["runs_merge_done"]), legacy["runs_window"],
                    legacy["merge_done_window"], _pct(legacy["unique_commit_coverage"]),
                    legacy["unique_pass_targets_window"], legacy["merge_done_window"]))
    risk_vs_legacy = None
    if s["risk_weighted_coverage"] is not None and legacy["runs_merge_done"]:
        risk_vs_legacy = s["risk_weighted_coverage"] - legacy["runs_merge_done"]
    lines.append("- Go 门槛（≥%.0f%%）：风险加权 %s；较旧口径 %s"
                 % (s["threshold"] * 100,
                    ("达标" if s["threshold_pass"] else "未达标")
                    if s["threshold_pass"] is not None else "不可判定",
                    ("低 %.2f 个百分点" % (-risk_vs_legacy * 100)
                     if risk_vs_legacy is not None and risk_vs_legacy < 0 else
                     ("高 %.2f 个百分点" % (risk_vs_legacy * 100)
                      if risk_vs_legacy is not None else "—"))))
    lines.append("")
    lines.append("## 二、按类型的覆盖与风险权重")
    lines.append("")
    lines.append("| 类型 | merges | covered | 覆盖率 | 权重 | merge_weight | covered_weight | runs(窗口验收) |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for cat in list(TYPES) + ["unknown"]:
        t = types[cat]
        lines.append("| %s | %d | %d | %s | %s | %s | %s | %d |"
                     % (cat, t["merges"], t["covered"], _pct(t["coverage"]),
                        _fmt(t["weight"]) if t["weight"] is not None else "—(不计入)",
                        _fmt(t["merge_weight"]), _fmt(t["covered_weight"]), t["runs"]))
    lines.append("")
    lines.append("> 风险加权覆盖率 = Σ(covered_weight)/Σ(merge_weight)；unknown 不进加权分母，"
                 "单列 fail-visible。")
    lines.append("")
    lines.append("## 三、覆盖判定与归属（strict vs 有效归属）")
    lines.append("")
    a = s["attribution"]
    lines.append("- 验收单：有效 %d 张（按 sha 取最新）；演练单排除 %d 张%s；"
                 "最新结论非 PASS 的 sha 不记覆盖"
                 % (rep["inputs"]["acceptance"]["latest_by_sha"],
                    len(rep["inputs"]["acceptance"]["excluded_drills"]),
                    ("（%s）" % "、".join(rep["inputs"]["acceptance"]["excluded_drills"]))
                    if rep["inputs"]["acceptance"]["excluded_drills"] else ""))
    lines.append("- PASS 验收目标 **%d** 个：strict 命中 merge sha **%d** 个，"
                 "有效归属 **%d** 个（沿 first-parent 上溯最多 %d 跳），无法归属 **%d** 个"
                 % (a["pass_targets"], a["strict_count"], a["attributed"],
                    a["max_hops"], a["unresolved"]))
    lines.append("- 归属跳数分布：%s"
                 % ("、".join("%s跳×%s" % (k, v) for k, v in a["hops_hist"].items()) or "—"))
    lines.append("- strict 风险加权覆盖率：%s（strict 仅 %d 个 merge 覆盖：验收目标 sha "
                 "与 merge sha 完全相等；实际 HEAD 多为 merge 后 chore 自动重建提交，"
                 "故主口径用有效归属）"
                 % (_pct(s["strict"]["risk_weighted_coverage"]),
                    s["strict"]["covered_merges"]))
    lines.append("")
    lines.append("## 四、旧口径对照（runs/merge_done、unique-commit）")
    lines.append("")
    lines.append("| 口径 | 值 | 分子/分母 | 说明 |")
    lines.append("|---|---:|---|---|")
    lines.append("| **风险加权（本工具主口径）** | **%s** | %s / %s | 按类型加权 |"
                 % (_pct(s["risk_weighted_coverage"]), _fmt(s["covered_weight"]),
                    _fmt(s["merge_weight"])))
    lines.append("| 等权（已知类型） | %s | %d / %d | covered/merges |"
                 % (_pct(s["equal_coverage_known"]), s["covered_merges"], s["merges_known"]))
    lines.append("| runs/merge_done（旧主口径） | %s | %d / %d | daily.jsonl / dev_events |"
                 % (_pct(legacy["runs_merge_done"]), legacy["runs_window"],
                    legacy["merge_done_window"]))
    lines.append("| unique-commit（旧补充口径） | %s | %d / %d | 去重 PASS commit / merge_done |"
                 % (_pct(legacy["unique_commit_coverage"]),
                    legacy["unique_pass_targets_window"], legacy["merge_done_window"]))
    lines.append("")
    if rep.get("projection"):
        p = rep["projection"]
        lines.append("## 五、%s 投影" % p["target"][:16])
        lines.append("")
        lines.append("| 项 | 值 | 依据 |")
        lines.append("|---|---:|---|")
        lines.append("| 剩余天数 | %s | target − now |" % _fmt(p["remaining_days"]))
        lines.append("| 外推系数 factor | %s | 1 + 剩余天数 / 窗口天数 |" % _fmt(p["factor"]))
        lines.append("| projected_merges | %s | 窗口 merges × factor |" % _fmt(p["projected_merges"]))
        lines.append("| projected_covered | %s | 窗口 covered × factor |"
                     % _fmt(p["projected_covered"]))
        lines.append("| **projected_risk_weighted_coverage** | **%s** | "
                     "Σcovered_weight×factor / Σmerge_weight×factor（平稳假设下比例不变） |"
                     % _pct(p["projected_risk_weighted_coverage"]))
        lines.append("| projected_equal_coverage | %s | projected_covered / projected_merges |"
                     % _pct(p["projected_equal_coverage"]))
        lines.append("")
        lines.append("> %s" % p["note"])
        lines.append("")
    else:
        lines.append("## 五、投影")
        lines.append("")
        lines.append("- 未提供 `--target`：本报告仅覆盖当前窗口，不投影。")
        lines.append("")
    lines.append("## 六、假设与风险")
    lines.append("")
    lines.append("1. **分类启发式**：按路径关键词/前缀分类，merge 取最高风险类别；"
                 "误分类风险（如 docs 内 auth 字样已由 docs 优先规则规避）已在规则中注明。")
    lines.append("2. **覆盖归属近似**：验收目标沿 first-parent 上溯最近 merge 祖先（≤%d 跳）；"
                 "若验收后又有多个非 merge 提交，归属可能偏保守（取最近 merge）。" 
                 % DEFAULT_MAX_HOPS)
    lines.append("3. **验收滞后**：窗口尾部 merge 尚未被验收覆盖会压低覆盖率（观察期内正常），"
                 "旧口径同样存在该滞后。")
    lines.append("4. **unknown 排除**：不在四大类路径内的 merge（k_stock/ops/Hermes 等）不计入"
                 "风险加权分母，已在 unknown 行如实计数，避免编造权重。")
    lines.append("5. **投影为平稳假设**：类型构成与覆盖水平不变时比例不变，投影只给绝对量；"
                 "若提频/补验收，比例会随实际验收变化。")
    lines.append("")
    lines.append("## 七、数据源与 fail-visible")
    lines.append("")
    inp = rep["inputs"]
    lines.append("- git：%s（merge 提交 %d，含文件 %d，空文件 %d，逐提交复核 %d）"
                 % ("OK" if inp["git_ok"] else "FAIL：%s" % inp["git_error"],
                    inp["merges_total"], inp["merges_with_files"],
                    inp["empty_file_merges"], inp["fallback_used"]))
    lines.append("- acceptance_*.json：sheets=%d，有效 sha=%d（演练排除 %d、坏 JSON %d、"
                 "缺 sha %d、缺 generated_at %d）"
                 % (inp["acceptance"]["sheets"], inp["acceptance"]["latest_by_sha"],
                    len(inp["acceptance"]["excluded_drills"]),
                    inp["acceptance"]["invalid"], inp["acceptance"]["missing_sha"],
                    inp["acceptance"]["missing_generated_at"]))
    lines.append("- daily.jsonl：%s（窗口 runs=%d，坏行 %d）"
                 % ("OK" if inp["daily"]["ok"] else "MISSING",
                    inp["daily"]["runs_window"], inp["daily"]["bad_lines"]))
    lines.append("- dev_events.jsonl：%s（窗口 merge_done=%d，坏行 %d）"
                 % ("OK" if inp["events"]["ok"] else "MISSING",
                    inp["events"]["merge_done_window"], inp["events"]["bad_lines"]))
    lines.append("- 缺失数据源：%s" % ("、".join(inp["missing"]) or "无"))
    lines.append("")
    lines.append("**fail-visible 警告（%d 条）**" % len(rep["warnings"]))
    lines.append("")
    if rep["warnings"]:
        for w in rep["warnings"]:
            lines.append("- %s" % w)
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("## 八、复现命令")
    lines.append("")
    lines.append("```powershell")
    lines.append("cd C:\\项目\\老田的AI助手")
    lines.append("C:\\项目\\py312\\python.exe -X utf8 tools\\huangzhong_risk_coverage.py "
                 "--days %d --json" % win["days"])
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append("版本：%s｜工具：huangzhong_risk_coverage.py（只读）｜"
                 "口径：风险加权主口径 + strict/等权/旧口径对照" % rep["version"])
    return "\n".join(lines)


# --------------------------------------------------------------------- CLI

def _text_summary(rep: dict) -> str:
    s = rep["summary"]
    legacy = rep["legacy"]
    ty = rep["types"]
    lines = [
        "黄忠风险加权覆盖率：近 %d 天 merge=%d（已知 %d / unknown %d）"
        % (rep["window"]["days"], s["merges_total"], s["merges_known"], s["merges_unknown"]),
        "  风险加权覆盖率 = %s（covered_weight %s / merge_weight %s）"
        % (_pct(s["risk_weighted_coverage"]), _fmt(s["covered_weight"]),
           _fmt(s["merge_weight"])),
        "  有效归属 covered=%d；strict covered=%d（strict 风险加权 %s）"
        % (s["covered_merges"], s["covered_merges_strict"],
           _pct(s["strict"]["risk_weighted_coverage"])),
        "  等权：known %s / all %s"
        % (_pct(s["equal_coverage_known"]), _pct(s["equal_coverage_all"])),
        "  旧口径：runs/merge_done=%s（%d/%d）；unique-commit=%s"
        % (_pct(legacy["runs_merge_done"]), legacy["runs_window"],
           legacy["merge_done_window"], _pct(legacy["unique_commit_coverage"])),
        "  分类：" + "；".join(
            "%s %d/%d" % (cat, ty[cat]["merges"], ty[cat]["covered"])
            for cat in list(TYPES) + ["unknown"]),
    ]
    if rep.get("projection"):
        p = rep["projection"]
        lines.append("  投影 %s：projected_merges=%s / projected_risk_weighted=%s"
                     % (p["target"][:16], _fmt(p["projected_merges"]),
                        _pct(p["projected_risk_weighted_coverage"])))
    for w in rep["warnings"]:
        lines.append("  [warn] %s" % w)
    return "\n".join(lines)


def main(argv: list[str] | None = None, run_fn: Runner | None = None) -> int:
    ap = argparse.ArgumentParser(description="黄忠风险加权覆盖率（只读）")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--repo-root", default="")
    ap.add_argument("--work-dir", default="")
    ap.add_argument("--out-dir", default="", help="提供时落 md+json；缺省仅 stdout")
    ap.add_argument("--weights", default="",
                    help="覆盖权重，如 data=3,security=3,code=1,docs=0.5")
    ap.add_argument("--target", default="",
                    help="投影目标时刻，如 2026-11-04T10:00（缺省不投影）")
    ap.add_argument("--max-hops", type=int, default=DEFAULT_MAX_HOPS,
                    help="验收目标上溯 merge 祖先最大跳数（默认 %d）" % DEFAULT_MAX_HOPS)
    ap.add_argument("--now", default="",
                    help="固定时钟（ISO，测试/复算用；缺省当前时间）")
    args = ap.parse_args(argv)

    now = _parse_ts(args.now) if args.now else None
    if args.now and now is None:
        print("--now 无法解析：%r" % args.now, file=sys.stderr)
        return 2
    try:
        weights = parse_weights(args.weights)
    except ValueError as exc:
        print("参数错误：%s" % exc, file=sys.stderr)
        return 2
    try:
        rep = compute(repo_root=args.repo_root or None,
                      work_dir=args.work_dir or None,
                      days=args.days,
                      now=now,
                      target=args.target or None,
                      weights=weights,
                      max_hops=args.max_hops,
                      out_dir=args.out_dir or None,
                      run_fn=run_fn)
    except ValueError as exc:
        print("参数错误：%s" % exc, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print(_text_summary(rep))
        if rep.get("paths", {}).get("md"):
            print("  报告：%s" % rep["paths"]["md"])
    if not rep["ok"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
