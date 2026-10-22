# -*- coding: utf-8 -*-
"""probe_task_start.py — 任务启动链路探针（80 行级，只读判据 + 一次性任务）。

为什么重写：2026-10-22 版（1,145 行）**判据用错了**——它读 Task Scheduler 201 事件
的 ResultCode，而那个字段把进程退出码编码成 `0x80070000 | exitCode`，于是
"脚本 exit 3（成功跑起来）"被它判成 "0x80070003 路径未找到（失败）"。

本版只做一件事，且只信一个来源：**`schtasks` 的 Last Result**。

判据（三步，任一步不符合就 FAIL）：
  1. 正例：建任务跑一个「写标记文件 + exit(3)」的脚本
     → 必须同时满足 Last Result == 3 **且** 标记文件存在
  2. 对照：把脚本路径改成不存在的文件
     → 标记文件必须**不存在**（证明这个检测器能区分"跑了"与"没跑"）
     注意：此时 Last Result 是 2（python 自己的"打不开文件"），**不是** -196608；
     -196608 是 PowerShell 的 `-File` 缺失码。退出码本身有歧义，所以用标记文件作权威信号。
  3. 清理：两个任务都删掉

用法：
    python -B tools/probe_task_start.py            # 人读输出
    python -B tools/probe_task_start.py --json     # 机器读
退出码：PASS=0 / FAIL=1 / UNKNOWN=2
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PY = sys.executable
TASK_POS = r"\ProbeTaskStartPos"
TASK_NEG = r"\ProbeTaskStartNeg"


def _run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
    out = (p.stdout or b"") + (p.stderr or b"")
    for enc in ("utf-8", "gbk"):
        try:
            return p.returncode, out.decode(enc)
        except UnicodeDecodeError:
            continue
    return p.returncode, out.decode("utf-8", "replace")


def _last_result(task: str) -> int | None:
    """读 schtasks 的 Last Result（权威来源）。取不到返回 None。"""
    code, text = _run(["schtasks", "/query", "/tn", task, "/fo", "CSV", "/nh", "/v"])
    if code != 0:
        return None
    cells = text.strip().strip('"').split('","')
    if len(cells) < 8:
        return None
    try:
        return int(cells[6])
    except ValueError:
        return None


def _make_Xml(path: Path, script: Path, task: str) -> None:
    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>probe_task_start: {task}</Description></RegistrationInfo>
  <Triggers><TimeTrigger><StartBoundary>2099-12-31T23:59:00</StartBoundary><Enabled>true</Enabled></TimeTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>{os.environ.get('USERNAME', '')}</UserId>
  <LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
  <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
  <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
  <AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled>
  <ExecutionTimeLimit>PT1M</ExecutionTimeLimit></Settings>
  <Actions Context="Author"><Exec>
    <Command>{PY}</Command>
    <Arguments>"{script}"</Arguments>
  </Exec></Actions>
</Task>
"""
    path.write_text(xml, encoding="utf-16")


def _probe(task: str, script_path: Path, workdir: Path, marker: Path) -> dict:
    """建任务 → run → 读 Last Result 与标记文件 → 删任务。"""
    xml = workdir / f"{task.strip(chr(92))}.xml"
    _make_Xml(xml, script_path, task)
    _run(["schtasks", "/delete", "/tn", task, "/f"])
    create_rc, create_out = _run(["schtasks", "/create", "/tn", task, "/xml", str(xml), "/f"])
    if create_rc != 0:
        return {"task": task, "ok": False, "stage": "create",
                "detail": create_out.strip()[:160] or f"rc={create_rc}"}
    _run(["schtasks", "/run", "/tn", task])
    result = None
    import time
    for _ in range(20):
        time.sleep(1)
        result = _last_result(task)
        if result not in (None, 267009):   # 267009 = 正在运行，等它结束
            break
    _run(["schtasks", "/delete", "/tn", task, "/f"])
    return {"task": task, "last_result": result, "stage": "run",
            "marker": marker.exists()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="任务启动链路探针（权威信号=标记文件，Last Result 为辅）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="probe_task_start_") as tmp:
        work = Path(tmp)
        marker_pos = work / "ran_positive.txt"
        marker_neg = work / "ran_negative.txt"
        script_pos = work / "positive.py"
        script_pos.write_text(
            "import sys\n"
            f"open(r'{marker_pos}', 'w').write('ran')\n"
            "sys.exit(3)\n", encoding="utf-8")
        script_neg = work / "negative.py"
        script_neg.write_text(
            "import sys\n"
            f"open(r'{marker_neg}', 'w').write('ran')\n"
            "sys.exit(3)\n", encoding="utf-8")

        pos = _probe(TASK_POS, script_pos, work, marker_pos)
        # 对照：脚本路径换成不存在的文件 → python 打不开，标记不会生成
        neg = _probe(TASK_NEG, work / "definitely_not_here.py", work, marker_neg)

    pos_ok = pos.get("last_result") == 3 and pos.get("marker") is True
    neg_ok = neg.get("marker") is False
    if pos_ok and neg_ok:
        verdict = "PASS"
    elif pos.get("stage") == "create" or neg.get("stage") == "create":
        verdict = "UNKNOWN"
    else:
        verdict = "FAIL"

    payload = {"verdict": verdict, "positive": pos, "negative": neg,
               "why": "权威信号是标记文件：正例必须写出标记且 exit=3；"
                      "对照必须写不出标记。只比退出码不够——"
                      "python 打不开文件时也是 2，与 CreateProcess 失败无法区分。"}
    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"verdict      : {verdict}")
        print(f"positive     : last_result={pos.get('last_result')} marker={pos.get('marker')} (期望 3/True)")
        print(f"negative     : last_result={neg.get('last_result')} marker={neg.get('marker')} (期望 非3/False)")
        print("mapping note : 201 事件把退出码编码成 0x80070000|N，所以 exit 3 在事件里"
              "显示为 2147942403 —— 那不是「路径未找到」。")
    return {"PASS": 0, "FAIL": 1}.get(verdict, 2)


if __name__ == "__main__":
    sys.exit(main())
