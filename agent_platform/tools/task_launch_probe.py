"""task_launch_probe.py — S-0①-b 能力正测试探针（2026-10-23）。

依据：互审单#001 回执#2/#3 双条件门禁——计数门禁可被"停用故障任务"做绿，
能力门禁必须每 24h 用一次性正测试证明「调度器能启动 python 脚本任务」。
技能卡：~/.hermes/profiles/zhaoyun/skills/task-launch-probe/SKILL.md

纪律（判例集）：
- 探针用后即清（不留残任务）；
- 判定读数先自证（检测器自证判例）；
- 失败如实记录返回码（0x80070003 = 根因仍未解，门禁红，不重试掩盖）；
- 纯 ASCII 路径（中文路径编码判例）；不改任何既有任务。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import tempfile
import uuid

PROBE_NAME = "ProbeS0Capacity"  # 固定名：重复运行先清旧探针
RESULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "..", "portal", "data", "task_exit_audit",
                           "s0_capacity_probe.json")
OK_CODES = {0, 3}  # rc=3 是 AlertWatch 语义的上游不可信拒跑，也算"能启动"


def _ps(cmd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True, timeout=timeout, check=False)


def _cleanup() -> None:
    _ps(f"Unregister-ScheduledTask -TaskName '{PROBE_NAME}' -Confirm:$false "
        "-ErrorAction SilentlyContinue")


def _write_probe_script() -> str:
    """临时 python 脚本落 %TEMP%（纯 ASCII 路径），内容=写一行时间戳即退出。"""
    fd, path = tempfile.mkstemp(suffix=".py", prefix="s0probe_", dir=os.environ.get("TEMP", tempfile.gettempdir()))
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write("import datetime, pathlib, sys\n"
                "p = pathlib.Path(sys.argv[1])\n"
                "p.write_text(datetime.datetime.now().isoformat(), encoding='ascii')\n")
    return path


def run_probe() -> dict:
    """一次性正测试全流程：建→跑→断言→清→落账。"""
    _cleanup()  # 幂等：清旧探针
    marker = os.path.join(os.environ.get("TEMP", tempfile.gettempdir()),
                          f"s0probe_{uuid.uuid4().hex[:8]}.out")
    script = _write_probe_script()
    started = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        # ① 注册一次性任务（python 脚本，ASCII 路径，无触发器=手动起）
        reg = _ps(
            f"$a = New-ScheduledTaskAction -Execute 'C:\\Program Files\\Python311\\python.exe' "
            f"-Argument '\"{script}\" \"{marker}\"'; "
            f"Register-ScheduledTask -TaskName '{PROBE_NAME}' -Action $a | Out-Null; 'registered'")
        if reg.returncode != 0:
            return {"ok": False, "stage": "register", "rc": reg.returncode,
                    "err": reg.stderr.decode("gbk", errors="replace")[:200],
                    "at": started}
        # ② 立即运行
        _ps(f"Start-ScheduledTask -TaskName '{PROBE_NAME}'", timeout=30)
        # ③ 轮询结果（最长 60s）
        rc = None
        for _ in range(12):
            import time
            time.sleep(5)
            info = _ps(f"(Get-ScheduledTaskInfo -TaskName '{PROBE_NAME}').LastTaskResult")
            out = info.stdout.decode("gbk", errors="replace").strip()
            try:
                rc = int(out)
                if rc != 267009:  # 267009=正在运行，等它结束
                    break
            except ValueError:
                continue
        # ④ 产物断言（比退出码更强：文件真被写=脚本真跑通）
        marker_written = os.path.isfile(marker)
        if marker_written:
            os.unlink(marker)
        verdict = {
            "at": started,
            "finished": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "launch_rc": rc,
            "marker_written": marker_written,
            "ok": (rc in OK_CODES) and marker_written,
            "note": "ok=能启动且产物落盘；launch_rc 0x80070003=根因未解门禁红；marker 比退出码更强（判例:信号落在被改对象上）",
        }
        return verdict
    finally:
        _cleanup()
        if os.path.isfile(script):
            os.unlink(script)


def record(v: dict) -> None:
    os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(RESULT_PATH), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(v, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RESULT_PATH)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="S-0①-b 能力正测试探针（用后即清）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    v = run_probe()
    record(v)
    if args.json:
        print(json.dumps(v, ensure_ascii=False, indent=2))
    else:
        print(("[PASS] " if v["ok"] else "[FAIL] ") +
              f"S-0 能力正测试：launch_rc={v.get('launch_rc')} marker={v.get('marker_written')}")
        if not v["ok"] and v.get("stage") != "register":
            print("  判定：门禁红——调度器未能启动 python 脚本任务（详见 s0_capacity_probe.json）")
    return 0 if v["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
