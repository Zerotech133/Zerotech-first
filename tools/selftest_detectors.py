# -*- coding: utf-8 -*-
"""selftest_detectors.py — 检测器自证（判例 04 的工具化）。

任何一个"读数"在被用来判定之前，都必须先用**已知为真的样本**验一遍：
它能给出预期的正例吗？能给反例吗？给不出反例的检测器，结论只能记"未知"。

本工具把 2026-10-22/23 一晚踩到的 5 个坑固化成断言。**如果哪天系统行为变了
（补丁、策略、新版 Windows），这些断言会先红，而不是让错误读数悄悄流进结论。**

  1. schtasks_query      — 查询一个确定存在的任务必须返回 0（防止"查不到"被误当"不存在"）
  2. schtasks_csv        — /fo CSV /nh /v 的第 7 列必须是整数（Last Result）
  3. event_exit_mapping  — 201 的 ResultCode 对 1..65535 的退出码应等于 0x80070000|N
  4. wevtutil_count      — /c:N 是**过滤前**截断：加 /c 取到的最新时间不应晚于不加 /c
  5. task_folder_readable— 已知任务定义文件可读（否则"读不到"不代表"不存在"）

用法：
    python -B tools/selftest_detectors.py            # 人读
    python -B tools/selftest_detectors.py --json
退出码：全部 PASS=0 / 有 FAIL=1 / 环境不允许=2
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
import logging

KNOWN_TASK = r"\QuantWatchdog"          # 每分钟跑一次，长期存在
TASKS_DIR = Path(r"C:\Windows\System32\Tasks")
LOG = "Microsoft-Windows-TaskScheduler/Operational"


def _run(cmd: list[str], timeout: int = 90) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    raw = (p.stdout or b"") + (p.stderr or b"")
    for enc in ("utf-8", "gbk"):
        try:
            return p.returncode, raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return p.returncode, raw.decode("utf-8", "replace")


def check_schtasks_query() -> dict:
    """确定性检查：查询不存在的任务应报错，查询已知任务必须成功。"""
    rc_missing, _ = _run(["schtasks", "/query", "/tn", r"\DefinitelyNotATask_xyz"])
    rc_known, out = _run(["schtasks", "/query", "/tn", KNOWN_TASK, "/fo", "LIST"])
    ok = (rc_known == 0) and (rc_missing != 0)
    return {
        "name": "schtasks_query",
        "ok": ok,
        "detail": f"已知任务 rc={rc_known}（应 0）；不存在任务 rc={rc_missing}（应非 0）",
        "counterexample": "若已知任务查询返回非 0，则后续所有'查不到'判定都不可信",
        "sample": (out.splitlines() or [""])[0][:60],
    }


def check_schtasks_csv() -> dict:
    """/fo CSV /nh /v 的第 7 列（索引 6）必须能解析为整数。"""
    rc, out = _run(["schtasks", "/query", "/fo", "CSV", "/nh", "/v"], timeout=180)
    parsed = 0
    total = 0
    if rc == 0:
        for line in out.splitlines():
            cells = line.strip().strip('"').split('","')
            if len(cells) < 8:
                continue
            total += 1
            try:
                int(cells[6])
                parsed += 1
            except ValueError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "tools/selftest_detectors.py", exc)
    ok = rc == 0 and total > 0 and parsed == total
    return {
        "name": "schtasks_csv",
        "ok": ok,
        "detail": f"rc={rc} 行数={total} 第7列可解析为整数={parsed}",
        "counterexample": "若某些行第 7 列不是整数，说明列位假设失效（曾把'下次运行'当'上次结果'）",
    }


def check_event_exit_mapping() -> dict:
    """201 的 ResultCode == 0x80070000|exitCode（1..65535）。用 Last Result 对照。"""
    rc, csv = _run(["schtasks", "/query", "/fo", "CSV", "/nh", "/v"], timeout=180)
    last: dict[str, int] = {}
    if rc == 0:
        for line in csv.splitlines():
            cells = line.strip().strip('"').split('","')
            if len(cells) < 8:
                continue
            try:
                last[cells[1].strip().lstrip("\\")] = int(cells[6])
            except ValueError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "tools/selftest_detectors.py", exc)
    rc2, xml = _run(["wevtutil", "qe", LOG, "/q:*[System[(EventID=201)]]", "/f:xml"])
    latest: dict[str, tuple[str, int]] = {}
    for block in xml.split("<Event ")[1:]:
        mn = re.search(r"<Data Name='TaskName'>([^<]*)</Data>", block)
        mc = re.search(r"<Data Name='ResultCode'>([^<]*)</Data>", block)
        mt = re.search(r"<TimeCreated SystemTime='([^']+)'/>", block)
        if not (mn and mc and mt):
            continue
        name = mn.group(1).strip().lstrip("\\")
        try:
            code = int(mc.group(1))
        except ValueError:
            continue
        if name not in latest or latest[name][0] < mt.group(1):
            latest[name] = (mt.group(1), code)
    hit = miss = 0
    examples = []
    for name, code in last.items():
        if not (0 < code <= 0xFFFF) or name not in latest:
            continue
        pred = 0x80070000 + code
        if latest[name][1] == pred:
            hit += 1
        else:
            miss += 1
            if len(examples) < 3:
                examples.append(f"{name}: exit={code} 事件={latest[name][1]} 预期={pred}")
    ok = rc2 == 0 and hit > 0 and miss == 0
    return {
        "name": "event_exit_mapping",
        "ok": ok,
        "detail": f"符合 {hit} / 不符合 {miss}" + ("；反例: " + "；".join(examples) if examples else ""),
        "counterexample": "若出现不符合，说明 0x8007000N 与退出码的映射变了，"
                          "所有'退出码 vs 错误码'的判断都要重做",
    }


def check_wevtutil_count() -> dict:
    """/c:N 是过滤前截断：加 /c 取到的最新时间不应晚于不加 /c。"""
    def newest(args: list[str]) -> str | None:
        rc, xml = _run(args)
        if rc != 0:
            return None
        stamps = re.findall(r"<TimeCreated SystemTime='([^']+)'/>", xml)
        return max(stamps) if stamps else None

    base = ["wevtutil", "qe", LOG, "/q:*[System[(EventID=201)]]", "/f:xml"]
    full = newest(base)
    capped = newest(base + ["/c:4"])
    ok = full is not None and capped is not None and capped <= full
    return {
        "name": "wevtutil_count",
        "ok": ok,
        "detail": f"不加 /c 最新={full}；/c:4 最新={capped}（应 <= 前者）",
        "counterexample": "若 /c:4 给出更晚的时间，说明截断语义变了；"
                          "曾因它取到日志开头而误判'最近无事件'",
    }


def check_task_folder_readable() -> dict:
    """已知存在任务的 XML 必须可读；否则"读不到"不能等同"不存在"。"""
    p = TASKS_DIR / KNOWN_TASK.lstrip("\\")
    try:
        size = p.stat().st_size
        readable = True
    except OSError:
        size = 0
        readable = False
    return {
        "name": "task_folder_readable",
        "ok": readable and size > 0,
        "detail": f"{p} 可读={readable} 大小={size}",
        "counterexample": "若连已知任务都读不到，'任务不存在'的结论必须记未知（权限问题）",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="检测器自证：用已知样本验证读数的可信度")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    checks = [
        check_schtasks_query(),
        check_schtasks_csv(),
        check_event_exit_mapping(),
        check_wevtutil_count(),
        check_task_folder_readable(),
    ]
    failed = [c for c in checks if not c["ok"]]
    verdict = "PASS" if not failed else "FAIL"

    if a.json:
        print(json.dumps({"verdict": verdict, "checks": checks}, ensure_ascii=False, indent=2))
    else:
        for c in checks:
            mark = "PASS" if c["ok"] else "FAIL"
            print(f"[{mark}] {c['name']}")
            print(f"       {c['detail']}")
            if not c["ok"]:
                print(f"       反例意义: {c['counterexample']}")
        print()
        print(f"verdict: {verdict}（{len(checks)-len(failed)}/{len(checks)} 通过）")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
