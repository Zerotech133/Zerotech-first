# -*- coding: utf-8 -*-
"""黄忠事故台账 CLI（append-only）：add / list / verify / stats。

定位：认证 v2 第④条「漏放 = 0」的**可审计台账工具**。只写
`<work-dir>/incidents.jsonl`（JSONL，追加写，不删不改），供只读消费方：
  · `tools/huangzhong_review.py`（item4_leakage：满 7 天窗口 PASS 单的相关事故）
  · `tools/huangzhong_go_no_go.py`（load_incidents：缺失按「无已知事故」计）
  · `tools/huangzhong_11_04` 复核流程（runbook 1.3 事故台账核对）

路径优先级：`--work-dir` > env `HUANGZHONG_WORK_DIR` > `<repo>/work/huangzhong`。
零第三方依赖（纯 stdlib）；不写除台账外的任何文件；演练（kind=drill）不得写入
正规台账（默认 work/huangzhong/incidents.jsonl），须显式指到临时 work-dir。

字段（每行一条 JSON object）：
  ts      事故时间（ISO8601 可解析；写入时归一化为带偏移的秒级 ISO）
  commit  相关提交（7–40 位 hex；空串=未绑定 commit 的事故，复核侧单列告警）
  kind    事故类型（建议：incident / revert / outage / data / drill / other）
  desc    一句话描述（必填、非空）
  source  来源（可选，默认 manual；如 docs/audit/xxx.md）
  added_at 入账时间（工具自动写，ISO8601 带偏移）

fail-visible 约定：
  · 台账文件缺失 → OK（empty），不造数据；
  · 台账存在但不可读（权限/目录占位等）→ ok=false + exit 2，绝不静默当空；
  · 坏行不丢弃、不作废整文件 → 只计数并在 verify 报 issues；
  · 去重（同 ts+commit+kind 已存在）→ action=skipped，不重复追加。

退出码：0 正常（含 empty/skipped）；1 verify 不通过；2 参数/环境/写前校验错误。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK_DIR = ROOT / "work" / "huangzhong"
LEDGER_NAME = "incidents.jsonl"
ENV_WORK_DIR = "HUANGZHONG_WORK_DIR"
RECENT_DAYS = 30

# 建议 kind 词表（不强制：未知 kind 记入 stats 的 by_kind，便于人工归类）
KINDS = ("incident", "revert", "outage", "data", "drill", "other")
DRILL_MARKERS = ("drill", "演练")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_FALLBACK_TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


# --------------------------------------------------------------------- 路径/时间

def resolve_work_dir(work_dir=None) -> Path:
    """台账目录：显式参数 > env HUANGZHONG_WORK_DIR > 默认 work/huangzhong。"""
    if work_dir is not None and str(work_dir).strip():
        return Path(work_dir)
    env = str(os.environ.get(ENV_WORK_DIR) or "").strip()
    if env:
        return Path(env)
    return DEFAULT_WORK_DIR


def ledger_path(work_dir=None) -> Path:
    return resolve_work_dir(work_dir) / LEDGER_NAME


def parse_ts(raw) -> dt.datetime | None:
    """解析时间戳（ISO8601 / 常见空格格式 / epoch 秒）；失败返回 None。

    无时区按本机时区解释（与 huangzhong_review.py 的 _parse_ts 口径一致）。
    """
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
        for fmt in _FALLBACK_TS_FORMATS:
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


def iso(ts: dt.datetime | None) -> str:
    return ts.isoformat(timespec="seconds") if ts is not None else ""


def now_local() -> dt.datetime:
    return dt.datetime.now().astimezone()


# ------------------------------------------------------------------------- 读台账

def read_ledger(work_dir=None) -> dict:
    """读 JSONL 台账：{path, exists, rows, bad_lines, error}；坏行容错、不可读报错。"""
    path = ledger_path(work_dir)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {"path": str(path), "exists": False, "rows": [], "bad_lines": 0, "error": None}
    except OSError as exc:
        return {"path": str(path), "exists": True, "rows": [], "bad_lines": 0,
                "error": "%s: %s" % (type(exc).__name__, exc)}
    text = raw.decode("utf-8-sig", errors="replace")
    rows, bad = [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
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
    return {"path": str(path), "exists": True, "rows": rows,
            "bad_lines": bad, "error": None}


def _is_drill(kind: str, desc: str) -> bool:
    k = str(kind or "").strip().lower()
    d = str(desc or "").strip()
    return k in DRILL_MARKERS or d.startswith("【演练】") or d.startswith("演练")


# --------------------------------------------------------------------- add 追加

def add_record(ts, commit="", kind="", desc="", source="", work_dir=None, now=None) -> dict:
    """追加一条事故记录；同 (ts, commit, kind) 已存在则 skip（append-only，不改旧行）。"""
    path = ledger_path(work_dir)
    ts_parsed = parse_ts(ts)
    if ts_parsed is None:
        return {"ok": False, "error": "invalid_ts",
                "detail": "ts 必须可解析（ISO8601/常见空格格式/epoch 秒）：%r" % (ts,)}
    ts_norm = iso(ts_parsed)
    commit_norm = str(commit or "").strip().lower()
    if commit_norm and not COMMIT_RE.match(commit_norm):
        return {"ok": False, "error": "invalid_commit",
                "detail": "commit 须为 7-40 位 hex 或空串（无绑定事故）：%r" % (commit,)}
    kind_norm = str(kind or "").strip()
    desc_norm = str(desc or "").strip()
    if not kind_norm:
        return {"ok": False, "error": "invalid_kind", "detail": "kind 不能为空"}
    if not desc_norm:
        return {"ok": False, "error": "invalid_desc", "detail": "desc 不能为空（一句话描述）"}
    # 演练纪律：演练记录不得写入正规台账（默认目录或显式指向默认目录均拒绝）
    if _is_drill(kind_norm, desc_norm):
        try:
            in_production = path.resolve() == (DEFAULT_WORK_DIR / LEDGER_NAME).resolve()
        except OSError:
            in_production = False
        if in_production:
            return {"ok": False, "error": "drill_not_allowed_in_production_ledger",
                    "detail": "演练记录不入正规台账；如需演示，请用 --work-dir 指到临时目录",
                    "path": str(path)}

    got = read_ledger(work_dir)
    if got["error"]:
        return {"ok": False, "error": "ledger_unreadable", "detail": got["error"],
                "path": got["path"]}
    for rec in got["rows"]:
        same_ts = parse_ts(rec.get("ts"))
        if same_ts is not None and iso(same_ts) != ts_norm:
            continue
        if (same_ts is None) and str(rec.get("ts") or "").strip() != ts_norm:
            continue
        if str(rec.get("commit") or "").strip().lower() != commit_norm:
            continue
        if str(rec.get("kind") or "").strip().lower() != kind_norm.lower():
            continue
        return {"ok": True, "action": "skipped", "reason": "duplicate",
                "path": got["path"], "total": len(got["rows"]),
                "record": {"ts": ts_norm, "commit": commit_norm,
                           "kind": kind_norm, "desc": desc_norm,
                           "source": str(source or "").strip()}}
    record = {
        "ts": ts_norm,
        "commit": commit_norm,
        "kind": kind_norm,
        "desc": desc_norm,
        "source": str(source or "").strip(),
        "added_at": iso(now or now_local()),
    }
    try:
        Path(got["path"]).parent.mkdir(parents=True, exist_ok=True)
        with open(got["path"], "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        return {"ok": False, "error": "write_failed",
                "detail": "%s: %s" % (type(exc).__name__, exc), "path": got["path"]}
    return {"ok": True, "action": "appended", "path": got["path"],
            "total": len(got["rows"]) + 1, "bad_lines_before": got["bad_lines"],
            "record": record}


# ----------------------------------------------------------------------- list

def list_records(limit=20, since=None, work_dir=None) -> dict:
    """读台账并**倒序**（新→旧）返回；limit 截断，since 过滤（ISO8601）。"""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid_limit", "detail": "limit 须为整数"}
    if limit < 1:
        return {"ok": False, "error": "invalid_limit", "detail": "limit 须 >= 1"}
    since_ts = None
    if since is not None and str(since).strip():
        since_ts = parse_ts(since)
        if since_ts is None:
            return {"ok": False, "error": "invalid_since",
                    "detail": "since 须可解析为时间（建议 ISO8601）：%r" % (since,)}
    got = read_ledger(work_dir)
    if got["error"]:
        return {"ok": False, "error": "ledger_unreadable", "detail": got["error"],
                "path": got["path"]}
    total = len(got["rows"])
    if not got["exists"]:
        return {"ok": True, "exists": False, "empty": True, "path": got["path"],
                "total": 0, "bad_lines": 0, "matched": 0, "filtered_bad_ts": 0,
                "rows": [], "note": "台账缺失 → 按空台账返回（不造数据）"}
    indexed = []
    filtered_bad_ts = 0
    for idx, rec in enumerate(got["rows"], 1):
        ts = parse_ts(rec.get("ts"))
        if since_ts is not None and ts is None:
            filtered_bad_ts += 1
            continue
        indexed.append({"line": idx, "ts_parsed": ts, "record": rec})
    if since_ts is not None:
        indexed = [x for x in indexed if x["ts_parsed"] is not None and x["ts_parsed"] >= since_ts]
    indexed.sort(key=lambda x: (x["ts_parsed"].timestamp() if x["ts_parsed"] else 0.0,
                                x["line"]), reverse=True)
    shown = indexed[:limit]
    rows = []
    for x in shown:
        row = {"line": x["line"]}
        row.update(x["record"])
        rows.append(row)
    return {"ok": True, "exists": True, "empty": not indexed, "path": got["path"],
            "total": total, "bad_lines": got["bad_lines"], "matched": len(indexed),
            "filtered_bad_ts": filtered_bad_ts, "rows": rows,
            "note": "倒序（新→旧）；total=全部有效行；matched=since 过滤后行数"}


# --------------------------------------------------------------------- verify

def verify_ledger(work_dir=None) -> dict:
    """逐行校验：必填字段 / ts 可解析 / commit 为 7-40 hex 或空串。"""
    got = read_ledger(work_dir)
    if got["error"]:
        return {"ok": False, "error": "ledger_unreadable", "detail": got["error"],
                "path": got["path"], "exists": True, "rows": 0, "bad_lines": 0,
                "issues": []}
    if not got["exists"]:
        return {"ok": True, "exists": False, "path": got["path"], "rows": 0,
                "bad_lines": 0, "issues": [], "issue_count": 0,
                "note": "台账缺失 → 视为空台账（empty ok，不造数据）"}
    issues = []
    for idx, rec in enumerate(got["rows"], 1):
        if not isinstance(rec.get("ts"), str) or not str(rec.get("ts")).strip():
            issues.append({"line": idx, "code": "missing_ts", "detail": "ts 缺失或非字符串"})
        elif parse_ts(rec.get("ts")) is None:
            issues.append({"line": idx, "code": "bad_ts",
                           "detail": "ts 不可解析：%r" % (rec.get("ts"),)})
        if "commit" not in rec:
            issues.append({"line": idx, "code": "missing_commit",
                           "detail": "commit 字段缺失（应为空串表示无绑定事故）"})
        else:
            commit = rec.get("commit")
            if not isinstance(commit, str):
                issues.append({"line": idx, "code": "bad_commit",
                               "detail": "commit 须为字符串（7-40 hex 或空串）"})
            elif commit.strip() and not COMMIT_RE.match(commit.strip()):
                issues.append({"line": idx, "code": "bad_commit",
                               "detail": "commit 须为 7-40 位 hex：%r" % (commit,)})
        for field in ("kind", "desc"):
            val = rec.get(field)
            if not isinstance(val, str) or not val.strip():
                issues.append({"line": idx, "code": "missing_%s" % field,
                               "detail": "%s 缺失或为空" % field})
        added = rec.get("added_at")
        if added is not None and (not isinstance(added, str) or parse_ts(added) is None):
            issues.append({"line": idx, "code": "bad_added_at",
                           "detail": "added_at 不可解析：%r" % (added,)})
    ok = got["bad_lines"] == 0 and not issues
    return {"ok": ok, "exists": True, "path": got["path"], "rows": len(got["rows"]),
            "bad_lines": got["bad_lines"], "issues": issues,
            "issue_count": len(issues),
            "note": "ok=true 需 bad_lines=0 且 issues=[]；坏行只计数不丢弃"}


# ---------------------------------------------------------------------- stats

def _commit_key(commit: str) -> str:
    """commit 去重键：统一小写、取前 8 位（与复核工具的 _match_sha 8 位前缀口径一致）。"""
    c = str(commit or "").strip().lower()
    return c[:8] if c else ""


def stats_ledger(work_dir=None, now=None) -> dict:
    """总数 / 按 kind / 按 commit 去重数 / 最近 30 天计数。"""
    now = now or now_local()
    cutoff = now - dt.timedelta(days=RECENT_DAYS)
    got = read_ledger(work_dir)
    if got["error"]:
        return {"ok": False, "error": "ledger_unreadable", "detail": got["error"],
                "path": got["path"]}
    if not got["exists"]:
        return {"ok": True, "exists": False, "path": got["path"], "total": 0,
                "by_kind": {}, "commit_unique": 0, "recent_30d": 0,
                "unattributed": 0, "bad_ts": 0, "bad_lines": 0,
                "note": "台账缺失 → 全零（empty ok，不造数据）"}
    by_kind: dict = {}
    commits: set = set()
    recent = 0
    unattributed = 0
    bad_ts = 0
    for rec in got["rows"]:
        kind = str(rec.get("kind") or "").strip() or "unknown"
        by_kind[kind] = by_kind.get(kind, 0) + 1
        key = _commit_key(rec.get("commit") or "")
        if key:
            commits.add(key)
        else:
            unattributed += 1
        ts = parse_ts(rec.get("ts"))
        if ts is None:
            bad_ts += 1
        elif ts >= cutoff:
            recent += 1
    return {"ok": True, "exists": True, "path": got["path"],
            "total": len(got["rows"]), "by_kind": by_kind,
            "commit_unique": len(commits), "recent_30d": recent,
            "unattributed": unattributed, "bad_ts": bad_ts,
            "bad_lines": got["bad_lines"],
            "window_days": RECENT_DAYS,
            "note": "commit_unique 按 8 位前缀去重（与复核工具匹配口径一致）"}


# ------------------------------------------------------------------------ CLI

def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=1))


def _print_human(cmd: str, rep: dict) -> None:
    if cmd == "add":
        if rep.get("ok"):
            print("ok=true action=%s path=%s total=%s"
                  % (rep.get("action"), rep.get("path"), rep.get("total")))
            rec = rep.get("record") or {}
            print("  ts=%s commit=%s kind=%s desc=%s"
                  % (rec.get("ts"), rec.get("commit") or "(无绑定)",
                     rec.get("kind"), rec.get("desc")))
        else:
            print("ok=false error=%s detail=%s" % (rep.get("error"), rep.get("detail")))
        return
    if cmd == "list":
        if not rep.get("ok"):
            print("ok=false error=%s detail=%s" % (rep.get("error"), rep.get("detail")))
            return
        if rep.get("exists") is False:
            print("台账缺失（empty ok）：%s" % rep.get("path"))
            return
        print("台账 %d 条（bad_lines=%d，matched=%d）→ 倒序显示 %d 条"
              % (rep.get("total", 0), rep.get("bad_lines", 0),
                 rep.get("matched", 0), len(rep.get("rows") or [])))
        for row in rep.get("rows") or []:
            print("  line=%-4s ts=%s commit=%s kind=%s desc=%s"
                  % (row.get("line"), row.get("ts"), row.get("commit") or "(无绑定)",
                     row.get("kind"), row.get("desc")))
        return
    if cmd == "verify":
        print("ok=%s path=%s rows=%s bad_lines=%s issues=%s"
              % (rep.get("ok"), rep.get("path"), rep.get("rows"),
                 rep.get("bad_lines"), rep.get("issue_count")))
        for issue in rep.get("issues") or []:
            print("  line=%s code=%s detail=%s"
                  % (issue.get("line"), issue.get("code"), issue.get("detail")))
        return
    if cmd == "stats":
        if not rep.get("ok"):
            print("ok=false error=%s detail=%s" % (rep.get("error"), rep.get("detail")))
            return
        print("ok=%s total=%s commit_unique=%s recent_%sd=%s unattributed=%s bad_ts=%s"
              % (rep.get("ok"), rep.get("total"), rep.get("commit_unique"),
                 rep.get("window_days"), rep.get("recent_30d"),
                 rep.get("unattributed"), rep.get("bad_ts")))
        for kind, count in sorted((rep.get("by_kind") or {}).items()):
            print("  kind=%s count=%s" % (kind, count))


def _common(parent: argparse.ArgumentParser) -> None:
    parent.add_argument("--work-dir", default=None,
                        help="台账目录（默认 env HUANGZHONG_WORK_DIR 或 work/huangzhong）")
    parent.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="JSON 输出")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="黄忠事故台账（append-only）：add/list/verify/stats；服务④漏放=0")
    ap.add_argument("--work-dir", default=None,
                    help="台账目录（默认 env HUANGZHONG_WORK_DIR 或 work/huangzhong）")
    ap.add_argument("--json", action="store_true", default=False, help="JSON 输出")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="追加一条事故记录（去重）")
    _common(p_add)
    p_add.add_argument("--ts", required=True, help="事故时间（ISO8601）")
    p_add.add_argument("--commit", default="", help="相关提交 7-40 hex；空串=无绑定")
    p_add.add_argument("--kind", required=True, help="类型：incident/revert/outage/data/other")
    p_add.add_argument("--desc", required=True, help="一句话描述")
    p_add.add_argument("--source", default="manual", help="来源（如 docs/audit/xxx.md）")

    p_list = sub.add_parser("list", help="倒序列出台账（新→旧）")
    _common(p_list)
    p_list.add_argument("--limit", type=int, default=20, help="最多返回条数（默认 20）")
    p_list.add_argument("--since", default=None, help="只返回不早于该时间的记录（ISO8601）")

    p_verify = sub.add_parser("verify", help="逐行校验台账")
    _common(p_verify)

    p_stats = sub.add_parser("stats", help="统计：总数/按 kind/commit 去重/近 30 天")
    _common(p_stats)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    work_dir = getattr(args, "work_dir", None)
    if args.cmd == "add":
        rep = add_record(args.ts, args.commit, args.kind, args.desc,
                         source=args.source, work_dir=work_dir)
        code = 0 if rep.get("ok") else 2
    elif args.cmd == "list":
        rep = list_records(args.limit, args.since, work_dir=work_dir)
        code = 0 if rep.get("ok") else 2
    elif args.cmd == "verify":
        rep = verify_ledger(work_dir=work_dir)
        code = 0 if rep.get("ok") else (2 if rep.get("error") else 1)
    else:
        rep = stats_ledger(work_dir=work_dir)
        code = 0 if rep.get("ok") else 2
    if args.json:
        _print_json(rep)
    else:
        _print_human(args.cmd, rep)
    return code


if __name__ == "__main__":
    sys.exit(main())
