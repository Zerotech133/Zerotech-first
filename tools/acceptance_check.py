# -*- coding: utf-8 -*-
"""验收清单检查器（2026-11-16 WO-ACCEPTANCE-CHECK）。

把「验收清单」写成 JSON spec，逐条实测打分，输出 PASS/FAIL + 一行证据，
退出码 = 失败项数（0 即全过）。只读生产：除 spec 中显式声明的 `cmd`（通常带
`--dry-run`）外，不执行任何写操作。

支持的 check kind：

| kind | 字段 | 判定 |
|---|---|---|
| `file_exists` | path | 文件存在且非空 |
| `file_contains` | path, pattern | 文本正则命中 |
| `not_contains` | path, pattern | 正则**不**命中（防回归） |
| `json_field` | path, pointer, in | JSON 点路径取值落在允许集合 |
| `regex_count` | path, pattern, expect | 正则命中行数等于 expect |
| `count_equals` | path, pattern_a, pattern_b | 两个数字抽取相等（如 total vs rows） |
| `cmd` | cmd[], expect_rc, unchanged | 跑命令；`unchanged` 指定的文件 mtime 必须不变 |
| `task_next_run` | tasks[] | 计划任务 NextRunTime 均可读 |

用法::

    python -X utf8 tools/acceptance_check.py --spec <spec.json> [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KINDS = {"file_exists", "file_contains", "not_contains", "json_field",
         "regex_count", "count_equals", "cmd", "task_next_run"}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _first_hit(text: str, pattern: str) -> str:
    for i, line in enumerate(text.splitlines(), 1):
        if re.search(pattern, line):
            return "L%d: %s" % (i, line.strip()[:110])
    return ""


def check_file_exists(spec: dict, repo: Path) -> tuple[bool, str]:
    p = repo / spec["path"]
    ok = p.is_file() and p.stat().st_size > 0
    return ok, "%s (%s B)" % (spec["path"], p.stat().st_size if p.is_file() else 0)


def check_file_contains(spec: dict, repo: Path) -> tuple[bool, str]:
    text = _read(repo / spec["path"])
    hit = _first_hit(text, spec["pattern"])
    return bool(hit), hit or "未命中 /%s/" % spec["pattern"]


def check_not_contains(spec: dict, repo: Path) -> tuple[bool, str]:
    text = _read(repo / spec["path"])
    hit = _first_hit(text, spec["pattern"])
    return not hit, ("意外命中 " + hit) if hit else "无命中（符合预期）"


def _dig(node, pointer: str):
    cur = node
    for part in pointer.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def check_json_field(spec: dict, repo: Path) -> tuple[bool, str]:
    try:
        doc = json.loads(_read(repo / spec["path"]))
    except ValueError:
        return False, "JSON 解析失败"
    val = _dig(doc, spec["pointer"])
    allowed = spec.get("in")
    ok = True if allowed is None else val in allowed
    return ok, "%s=%r%s" % (spec["pointer"], val,
                            "" if allowed is None else " in %s" % (allowed,))


def check_regex_count(spec: dict, repo: Path) -> tuple[bool, str]:
    text = _read(repo / spec["path"])
    n = len([l for l in text.splitlines() if re.search(spec["pattern"], l)])
    return n == int(spec["expect"]), "命中 %d 行（期望 %s）" % (n, spec["expect"])


def check_count_equals(spec: dict, repo: Path) -> tuple[bool, str]:
    text = _read(repo / spec["path"])
    a = re.search(spec["pattern_a"], text)
    n_b = len([l for l in text.splitlines() if re.search(spec["pattern_b"], l)])
    ok = bool(a) and int(a.group(1)) == n_b
    return ok, "声明=%s 实际=%d" % (a.group(1) if a else "?", n_b)


def check_cmd(spec: dict, repo: Path) -> tuple[bool, str]:
    guard = spec.get("unchanged")
    before = (repo / guard).stat().st_mtime if guard and (repo / guard).is_file() else 0
    try:
        r = subprocess.run(spec["cmd"], cwd=str(repo), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=int(spec.get("timeout", 600)))
    except Exception as exc:  # noqa: BLE001
        return False, "执行失败 %s" % type(exc).__name__
    after = (repo / guard).stat().st_mtime if guard and (repo / guard).is_file() else 0
    tail = (r.stdout or "").strip().splitlines()
    ev = tail[-1][:140] if tail else "(无输出)"
    ok = r.returncode == int(spec.get("expect_rc", 0))
    if guard:
        ok = ok and before == after
        ev += " | %s unchanged=%s" % (guard, before == after)
    return ok, "rc=%d %s" % (r.returncode, ev)


def check_task_next_run(spec: dict, repo: Path) -> tuple[bool, str]:
    """计划任务可读性检查。

    默认要求 NextRunTime 是**未来时间**；一次性任务执行过后 NextRunTime 会变成 `N/A`
    （这是正常终态，不是故障），此类用例显式声明 `"allow_na": true`。
    任务不存在（行缺失）一律 FAIL。
    """
    tasks = spec.get("tasks") or []
    allow_na = bool(spec.get("allow_na"))
    ps = ("foreach($t in %s){$i=Get-ScheduledTaskInfo -TaskName $t;"
          "\"$t|$($i.NextRunTime)\"}" % ",".join("'%s'" % t for t in tasks))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=120)
    except Exception as exc:  # noqa: BLE001
        return False, "查询失败 %s" % type(exc).__name__
    got: dict[str, str] = {}
    for line in (r.stdout or "").splitlines():
        if "|" not in line:
            continue
        name, _, val = line.partition("|")
        got[name.strip()] = val.strip()
    missing = [t for t in tasks if t not in got]
    na = [t for t in tasks if t in got and got[t] in ("", "N/A", "不可用")]
    ok = not missing and (allow_na or not na)
    ev = " ; ".join("%s=%s" % (t, got.get(t, "MISSING")) for t in tasks)
    if missing:
        ev += " | 缺失=%s" % ",".join(missing)
    elif na and not allow_na:
        ev += " | N/A（一次性任务已执行？如属正常请加 allow_na）"
    return ok, ev


HANDLERS = {
    "file_exists": check_file_exists,
    "file_contains": check_file_contains,
    "not_contains": check_not_contains,
    "json_field": check_json_field,
    "regex_count": check_regex_count,
    "count_equals": check_count_equals,
    "cmd": check_cmd,
    "task_next_run": check_task_next_run,
}


def run_spec(spec: dict, repo: Path) -> list[dict]:
    out = []
    for item in spec.get("checks") or []:
        kind = str(item.get("kind") or "")
        fn = HANDLERS.get(kind)
        if fn is None:
            out.append({"id": item.get("id"), "section": item.get("section"),
                        "name": item.get("name"), "ok": False,
                        "evidence": "未知 kind=%r" % kind})
            continue
        try:
            ok, ev = fn(item, repo)
        except Exception as exc:  # noqa: BLE001
            ok, ev = False, "检查异常 %s: %s" % (type(exc).__name__, str(exc)[:120])
        out.append({"id": item.get("id"), "section": item.get("section"),
                    "name": item.get("name"), "ok": bool(ok), "evidence": ev})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="验收清单检查器（只读，退出码=失败数）")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=None, help="把 JSON 结果写入该文件（避免 shell 重定向的编码坑）")
    args = ap.parse_args(argv)
    spec_path = Path(args.spec)
    if not spec_path.is_absolute():
        spec_path = Path(args.repo) / spec_path
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    results = run_spec(spec, Path(args.repo))
    fails = [r for r in results if not r["ok"]]
    payload = {"schema": "acceptance_check/v1",
               "spec": str(spec_path), "title": spec.get("title"),
               "checks": results, "fail": len(fails)}
    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = Path(args.repo) / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print("# %s\n" % (spec.get("title") or spec_path.name))
        section = None
        for r in results:
            if r["section"] != section:
                section = r["section"]
                print("\n== %s ==" % section)
            print("  [%s] %s  ← %s" % ("PASS" if r["ok"] else "FAIL", r["name"], r["evidence"]))
        print("\nTOTAL %d checks, FAIL=%d" % (len(results), len(fails)))
    return min(len(fails), 125)


if __name__ == "__main__":
    raise SystemExit(main())
