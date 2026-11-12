# -*- coding: utf-8 -*-
"""05:30 每日检查链路的变异检验：改回旧行为，断言必须失败。

按项目铁律：断言通过前先确认它走的是预期分支；变异后若不失败 = 测试漏拦。
多行补丁落盘执行（不走 heredoc）；换行按文件字节自适应（CRLF/LF 都踩过）。
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"C:\项目\老田的AI助手")
PREOPEN = ROOT / "tools" / "preopen" / "daily_check_dispatch.py"
GATEWAY = ROOT / "agent_platform" / "gateway" / "internal_notify.py"
TEST = ROOT / "tools" / "tests" / "test_daily_check_dispatch.py"
PY = r"C:\Program Files\Python311\python.exe"

CASES = [
    ("目标日改回「取日历最后一天」而非今天之前的开市日", PREOPEN,
     '    prior = [d for d in cal if d < today]',
     '    prior = list(cal)',
     "test_resolve_target_uses_calendar_not_subject_max"),
    ("分派改回不按数据源、只靠关键词与兜底", PREOPEN,
     '        if text.startswith(sp["name"] + "：") or text.startswith(sp["name"] + ":"):',
     '        if False:',
     "test_classify_by_source_then_keyword_then_fallback"),
    ("红告警放宽到告警级也发达人", PREOPEN,
     '    if res["verdict"] == "fail" and not args.dry and not args.no_feishu:',
     '    if res["verdict"] != "ok" and not args.dry and not args.no_feishu:',
     "test_run_dry_does_not_emit_and_feishu_only_on_fail"),
    ("增量链路异常被抬成阻断级", PREOPEN,
     '            "severity": WARNING,\n'
     '            "text": "盘后增量链路逐表巡检 verdict=%s（%d/%d 张表未达标）；"',
     '            "severity": BLOCKER,\n'
     '            "text": "盘后增量链路逐表巡检 verdict=%s（%d/%d 张表未达标）；"',
     "test_aux_chain_is_independent_and_marked_not_downtime"),
    ("部门信件改回秒级时间戳（幂等冲突复发）", GATEWAY,
     '               "created_at": str(created_at) if created_at else time.strftime("%Y-%m-%dT%H:%M:%S%z")}',
     '               "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}',
     "test_internal_notify_fixed_created_at_is_idempotent"),
    ("任务退出码改回业务码（warn 报任务失败）", PREOPEN,
     '    print("BUSINESS_VERDICT=%s STATE=%s N_ISSUES=%d"\n'
     '          % (rec.get("verdict"), rec.get("state"), rec.get("n_issues", 0)))\n'
     '    return 0',
     '    print("BUSINESS_VERDICT=%s STATE=%s N_ISSUES=%d"\n'
     '          % (rec.get("verdict"), rec.get("state"), rec.get("n_issues", 0)))\n'
     '    return 1 if rec.get("verdict") == "warn" else 0',
     "test_main_exit_code_is_completion_not_business_verdict"),
]


def adapt(raw: bytes, text: str) -> bytes:
    nl = b"\r\n" if b"\r\n" in raw else b"\n"
    return text.replace("\n", nl.decode()).encode("utf-8")


def main() -> int:
    killed = 0
    for name, path, old, new, node in CASES:
        raw = io.open(path, "rb").read()
        ob, nb = adapt(raw, old), adapt(raw, new)
        if raw.count(ob) != 1:
            print("ANCHOR_FAIL %s count=%d（拒绝变异，文件未动）" % (name, raw.count(ob)))
            return 9
        io.open(path, "wb").write(raw.replace(ob, nb, 1))
        try:
            r = subprocess.run(
                [PY, "-m", "pytest", "-q", "%s::%s" % (TEST, node)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=str(ROOT), timeout=120,
            )
            ok = r.returncode != 0
            print("%s %s  (pytest rc=%d)" %
                  ("KILLED " if ok else "NOT_KILLED", name, r.returncode))
            if not ok:
                print("   ", (r.stdout or "")[-400:].replace("\n", " | "))
            killed += int(ok)
        finally:
            io.open(path, "wb").write(raw)
        assert io.open(path, "rb").read() == raw, "恢复后字节不一致：" + name
    print("=== 变异检验 %d/%d KILLED ===" % (killed, len(CASES)))
    if killed != len(CASES):
        return 9
    r = subprocess.run([PY, "-m", "pytest", "-q", str(TEST)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(ROOT), timeout=180)
    print("恢复后全套：", (r.stdout or "").strip().splitlines()[-1])
    return 0 if r.returncode == 0 else 9


if __name__ == "__main__":
    sys.exit(main())
