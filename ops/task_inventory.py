# -*- coding: utf-8 -*-
r"""ops/task_inventory.py — 计划任务侧单一事实来源（只读）。

2026-11-05 建立，对应 docs/plans/20261105_data_backup_and_task_governance_plan.md
阶段 0 第 1 条。设计契约见 docs/plans/20261105_governance_project_dossier.md。

纪律（硬边界，勿放宽）：
- 全程只读任务定义。本模块**不**新增/修改/启用/禁用任何 Windows 计划任务，
  不接受 --apply / --host / --upload / --remote 参数。
- suggest-registry 只产候选，applied 恒为 False；模块内无任何以写模式打开
  quant_backtest/miss_check.py 的代码路径。
- diff.removed 只置软删除标记，绝不物理删除任何文件。
- 关键计数以治理方案 §1.3 为 golden；--assert-golden 会逐项做码点复核，
  避免本机打印通道把 10<->68、16<->91 串味。

口径修正（团长 2026-11-05 复核，见 ADR-13/ADR-14）：
- SYSTEM_PATH_PREFIXES 必须含 \Microsoft\ / \ZeroBound\ / \AIOS- 三前缀。
  只用 \Microsoft\ 会算成 system 180 / custom 312 / real_failed 36（错）。
- classify 中 real_failed 必须早于 state 判定，否则 Ready 且 rc!=0 的任务
  会被标成 ready，category 与审计计数不同源。
"""
from __future__ import annotations

import argparse
import ast
import datetime
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter

SCHEMA_VERSION = "1.0"

# Scheduler 状态字：调度器语义，不是业务失败，排除出 real_failed。
SCHEDULER_NOISE_RC = frozenset({
    267009, 267011, 267008, 267010,
    1073807364, 2147943140, 2147942402, 2147946720,
})
NEVER_RUN_SENTINELS = ("1999-11-30", "1999/11/30")
SYSTEM_PATH_PREFIXES = ("\\Microsoft\\", "\\ZeroBound\\", "\\AIOS-")
VALID_CATEGORIES = (
    "ready", "disabled", "running", "real_failed",
    "never_run", "scheduled_noise", "system",
)

# 治理方案 §1.3 的字节级复核值（2026-11-05 快照）。
GOLDEN = {
    "total": 492, "custom": 293, "system": 199,
    "Ready": 407, "Disabled": 76, "Running": 9,
    "Interactive": 185, "S4U": 123, "ServiceAccount": 107, "Group": 77,
    "custom_real_failed": 32, "custom_never_run": 24, "custom_disabled": 45,
    "ready_06_09": 76,
    "registry_entries": 23, "registry_matched_custom": 18,
}

FIELD_MAP = {
    "Name": "name", "Path": "path", "State": "state",
    "Author": "author", "Date": "created_at", "Last": "last_run_at",
    "RC": "last_rc", "Next": "next_run_at", "Missed": "missed_runs",
    "Exec": "exec", "Args": "args", "Triggers": "triggers",
    "User": "user", "Logon": "logon_type",
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_FILE = os.path.join(ROOT, "quant_backtest", "miss_check.py")

PEAK_HOURS = ("06", "07", "08", "09")


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _is_system(path):
    return (path or "\\").startswith(SYSTEM_PATH_PREFIXES)


def classify(t):
    """返回 VALID_CATEGORIES 之一。顺序即规则，勿随意调换。"""
    rc = t.get("last_rc")
    last = (t.get("last_run_at") or "").strip()
    st = (t.get("state") or "").strip()
    # 1) 从未运行哨兵最优先：1999-11-30 家族。
    if last and last.startswith(NEVER_RUN_SENTINELS):
        return "never_run"
    # 2) Scheduler 状态字不是业务失败。
    if rc in SCHEDULER_NOISE_RC:
        return "scheduled_noise"
    # 3) 系统任务另计，不进自定义分母。
    if _is_system(t.get("path")):
        return "system"
    # 4) 真实失败优先于 state：Ready 且 rc!=0 仍属真实失败（ADR-14）。
    if rc not in (None, 0):
        return "real_failed"
    if st == "Disabled":
        return "disabled"
    if st == "Running":
        return "running"
    return "ready"


def normalize_task(raw):
    """PowerShell 快照字段 / snake_case 字段 双向兼容 -> 规范 task dict。"""
    out = {}
    for src, dst in FIELD_MAP.items():
        v = raw.get(src, raw.get(dst))
        out[dst] = v
    for key in ("last_rc", "missed_runs"):
        v = out.get(key)
        try:
            out[key] = int(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            out[key] = None
    for key in ("name", "path", "state", "author", "created_at",
                "last_run_at", "next_run_at", "exec", "args",
                "triggers", "user", "logon_type"):
        out[key] = "" if out.get(key) is None else str(out[key])
    out["path"] = out["path"] or "\\"
    out["is_system"] = _is_system(out["path"])
    out["category"] = classify(out)
    out["noise"] = out["category"] == "scheduled_noise"
    out["never_run"] = out["category"] == "never_run"
    out["quarantined"] = False
    out["deleted_at"] = None
    out["notes"] = ""
    return out


def _decode(raw):
    for enc in ("utf-8-sig", "utf-8", "mbcs", "gbk"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _load_rows(path):
    with io.open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if isinstance(data, dict) and "tasks" in data:
        return data["tasks"]
    if isinstance(data, list):
        return data
    raise ValueError("快照格式不支持：%s" % path)


def query_schtasks(*, csv_path=None, timeout=60):
    """只读采集。file 模式用于复现与单测；schtasks 模式尽力而为。"""
    if csv_path:
        return _load_rows(csv_path)
    try:
        p = subprocess.run(
            ["schtasks", "/query", "/v", "/fo", "CSV"],
            capture_output=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("schtasks 不可用：%s" % exc)
    if p.returncode != 0:
        raise RuntimeError("schtasks rc=%s: %s" % (p.returncode, _decode(p.stderr)[:200]))
    import csv
    return list(csv.DictReader(io.StringIO(_decode(p.stdout))))


def build_snapshot(tasks, *, source, generated_at=None):
    # ADR-15 / 补丁 10 不变式（团长 2026-11-05 复核）：
    # snapshot_id 只哈希规范化后的 tasks 集合（canonical JSON），排除
    # generated_at / source / 主机名等「生成侧」元数据，保留任务自身全部属性
    # （含 created_at / last_run_at / next_run_at / last_rc），故内容不同则
    # id 必不同。id 仅作内容指纹与追溯标识，**不得作为跳过 diff 的判据**：
    # diff_snapshots() 始终逐条比对 tasks[] 内容，不读 snapshot_id。
    norm = [normalize_task(t) for t in tasks]
    payload = json.dumps(norm, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    sid = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "task_inventory_snapshot",
        "snapshot_id": sid,
        "generated_at": generated_at or _now(),
        "source": {"tool": source, "python": "3.11 -X utf8",
                   "host": os.environ.get("COMPUTERNAME", "")},
        "classification_rules": {
            "noise_rc": sorted(SCHEDULER_NOISE_RC),
            "never_run_sentinels": list(NEVER_RUN_SENTINELS),
            "system_path_prefixes": list(SYSTEM_PATH_PREFIXES),
        },
        "counts": {
            "total": len(norm),
            "custom": sum(1 for t in norm if not t["is_system"]),
            "system": sum(1 for t in norm if t["is_system"]),
        },
        "tasks": norm,
    }


def _read_registry_names():
    """只读解析 miss_check.REGISTRY 的键，绝不写回。"""
    try:
        with io.open(REGISTRY_FILE, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "REGISTRY":
                        return list(ast.literal_eval(node.value).keys())
    except (OSError, SyntaxError, ValueError, TypeError):
        pass
    return []


def _tasks_of(snap):
    if isinstance(snap, dict) and "tasks" in snap:
        return snap["tasks"]
    return [normalize_task(t) for t in snap]


def compute_stats(snap):
    tasks = _tasks_of(snap)
    custom = [t for t in tasks if not t["is_system"]]
    by_state = Counter(t.get("state") or "" for t in tasks)
    by_logon = Counter(t.get("logon_type") or "" for t in tasks)
    reg = _read_registry_names()
    custom_names = {t["name"] for t in custom}
    matched = sum(1 for n in reg if n in custom_names)
    blob = [(t.get("exec") or "") + " " + (t.get("args") or "") for t in tasks]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "task_inventory_stats",
        "generated_at": _now(),
        "total": len(tasks),
        "custom": len(custom),
        "system": len(tasks) - len(custom),
        "by_state": dict(by_state),
        "by_logon_type": dict(by_logon),
        "custom_real_failed": sum(1 for t in custom if t["category"] == "real_failed"),
        "custom_never_run": sum(1 for t in custom if t["category"] == "never_run"),
        "custom_disabled": sum(1 for t in custom if t["state"] == "Disabled"),
        "ready_06_09": sum(
            1 for t in custom
            if t["state"] == "Ready" and len(t["next_run_at"]) >= 13
            and t["next_run_at"][11:13] in PEAK_HOURS
        ),
        "registry_entries": len(reg),
        "registry_matched_custom": matched,
        "registry_coverage_ratio": round(matched / len(custom), 4) if custom else 0.0,
        "referenced": {
            "db_dump_daily": sum(1 for b in blob if "db_dump_daily" in b.lower()),
            "cainiao_pull": sum(1 for b in blob if "cainiao_pull" in b.lower()),
        },
        "excluded_noise_rc": sorted(SCHEDULER_NOISE_RC),
        "excluded_never_run_sentinel": list(NEVER_RUN_SENTINELS),
    }


def assert_golden(stats):
    """逐项比对 + 码点复核，返回 (ok, failures)。"""
    failures = []
    for key, expect in GOLDEN.items():
        if key in stats:
            got = stats[key]
        elif key in stats.get("by_state", {}):
            got = stats["by_state"][key]
        elif key in stats.get("by_logon_type", {}):
            got = stats["by_logon_type"][key]
        else:
            got = None
        if got != expect:
            failures.append("%s: got=%s expect=%s" % (key, got, expect))
        # 字节级复核：不凭打印结果判断数字
        print("  %-26s got=%-6s codes=%s" %
              (key, got, [ord(c) for c in str(got)]))
    return not failures, failures


def _task_key(t):
    """diff 的复合索引键：(path, name)。

    实测（2026-11-05 golden，492 条任务）：只有 97 个不同 path，其中根目录
    `\\` 聚集 291 条。若仅用 path 做键，同目录下不同 Name 的任务会被静默
    覆盖，diff --base X --cur X 会把 492 报成 97（unchanged 失真、added/
    removed/changed 漏报）。(path, name) 实测 492 个不同值、零重复。
    注意：tuple 仅在模块内部做字典键，绝不进入 JSON 输出。
    """
    return (t.get("path") or "\\", t.get("name") or "")


def diff_snapshots(base, cur):
    def index(snap):
        return {_task_key(t): t for t in _tasks_of(snap)}
    b, c = index(base), index(cur)
    added, removed, changed, unchanged = [], [], [], []
    for k in sorted(set(c) - set(b)):
        added.append({"path": k[0], "name": k[1],
                      "state": c[k].get("state")})
    for k in sorted(set(b) - set(c)):
        # 软删除：只标记，绝不物理删除
        removed.append({"path": k[0], "name": k[1],
                        "state": b[k].get("state"),
                        "deleted_at": None, "quarantined": True})
    for k in sorted(set(b) & set(c)):
        fields = {}
        for f in ("state", "last_rc", "last_run_at", "missed_runs", "category"):
            if b[k].get(f) != c[k].get(f):
                fields[f] = {"from": b[k].get(f), "to": c[k].get(f)}
        if fields:
            changed.append({"path": k[0], "name": k[1], "fields": fields})
        else:
            # 保持原契约：unchanged 仍是 path 字符串列表（可重复），
            # 计数以 len() 为准；name 只写进 added/removed/changed。
            unchanged.append(k[0])
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "task_inventory_diff",
        "generated_at": _now(),
        "base": {"total": len(b)}, "cur": {"total": len(c)},
        "added": added, "removed": removed, "changed": changed,
        "summary": {"added": len(added), "removed": len(removed),
                    "changed": len(changed), "unchanged": len(unchanged)},
    }


KEY_CHAINS = (
    ("JevRestoreDrill", "关键链：季度恢复演练（每月 1 日 03:00）"),
    ("BackupDaily", "关键链：加密主备份链（07:45 每日）"),
    ("ResticTrialDaily", "关键链：异地 restic 链（02:20 每日）"),
    ("DbDumpDaily", "pending：任务未排班，不得制造假 MISS"),
)


def suggest_registry(snap, include_disabled=False):
    """只产候选。applied 恒 False，expect 恒 None（须人工确认）。"""
    tasks = _tasks_of(snap)
    by_name = {t["name"]: t for t in tasks}
    cands = []
    for name, reason in KEY_CHAINS:
        t = by_name.get(name)
        if t and t["state"] == "Disabled" and not include_disabled:
            continue
        cands.append({
            "name": name,
            "path": t["path"] if t else "\\" + name,
            "expect": None,                 # 显式可审计，不从 NextRun 反推
            "needs_human_confirm": True,
            "status": "pending" if name == "DbDumpDaily" else "active",
            "reason": reason,
            "evidence": ({"state": t["state"], "last_rc": t["last_rc"],
                          "next_run_at": t["next_run_at"]} if t
                         else {"state": "NO_TASK"}),
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "registry_candidates",
        "generated_at": _now(),
        "write_target": "quant_backtest/miss_check.py::REGISTRY",
        "applied": False,
        "policy": "candidates-only; 老田逐条确认 expect 且 D1/D2 裁定后方可写入",
        "candidates": cands,
        "conflicts": [{
            "name": "CainiaoPull",
            "issue": "19:00 未排班，保留在册报 MISS 为设计意图，不得自动移除",
        }],
    }


def atomic_write_json(path, obj):
    """同目录 mkstemp + os.replace，杜绝半截 JSON。"""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".task_inv_", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        print("[warn] 写入失败，临时文件保留：%s" % tmp, file=sys.stderr)
        raise
    return path


def _load_snap(path):
    with io.open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _emit(obj, out, as_json):
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)
    if out:
        atomic_write_json(out, obj)
        print("written: %s" % out)
    elif as_json or True:
        print(text)


def main(argv=None):
    ap = argparse.ArgumentParser(description="计划任务台账（只读，无 --apply）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("export", help="导出规范快照")
    p.add_argument("--source", choices=("file", "schtasks"), default="file")
    p.add_argument("--file", help="file 模式的输入 JSON 快照")
    p.add_argument("--out"); p.add_argument("--json", action="store_true")

    p = sub.add_parser("snapshot", help="生成并落盘当日快照")
    p.add_argument("--source", choices=("file", "schtasks"), default="file")
    p.add_argument("--file"); p.add_argument("--out")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("diff", help="比对两份快照（软删除）")
    p.add_argument("--base", required=True); p.add_argument("--cur", required=True)
    p.add_argument("--out"); p.add_argument("--json", action="store_true")

    p = sub.add_parser("stats", help="统计并与 golden 比对")
    p.add_argument("--snap", required=True)
    p.add_argument("--assert-golden", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("suggest-registry", help="生成哨兵候选（不写回）")
    p.add_argument("--snap", required=True)
    p.add_argument("--include-disabled", action="store_true")
    p.add_argument("--out"); p.add_argument("--json", action="store_true")

    a = ap.parse_args(argv)
    try:
        if a.cmd in ("export", "snapshot"):
            if a.source == "file" and not a.file:
                print("[error] file 模式需要 --file", file=sys.stderr)
                return 2
            rows = query_schtasks(csv_path=a.file if a.source == "file" else None)
            snap = build_snapshot(rows, source=("file:" + (a.file or ""))
                                  if a.source == "file" else "schtasks")
            if a.cmd == "snapshot" and not a.out:
                a.out = os.path.join(ROOT, "ops", "data",
                                     "task_inventory_%s.json" %
                                     datetime.date.today().isoformat())
            _emit(snap, a.out, a.json)
        elif a.cmd == "diff":
            _emit(diff_snapshots(_load_snap(a.base), _load_snap(a.cur)),
                  a.out, a.json)
        elif a.cmd == "stats":
            st = compute_stats(_load_snap(a.snap))
            if a.assert_golden:
                ok, failures = assert_golden(st)
                print("GOLDEN_MATCH=%s failures=%s" % (ok, failures))
                if not ok:
                    return 1
            else:
                _emit(st, None, a.json)
        elif a.cmd == "suggest-registry":
            _emit(suggest_registry(_load_snap(a.snap), a.include_disabled),
                  a.out, a.json)
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print("[error] %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
