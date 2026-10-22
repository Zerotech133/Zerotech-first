"""s0_cwd_variant_probe.py — 0x80070003 根因对照变体探针组。

04:38 基线：无 cwd 探针 PASS（rc=0）。04:40 变体一：cwd=%TEMP% 也 PASS。
本脚本跑变体二：cwd=news_collector（昨晚 ProbeStartA 失败形态，中文路径目录）。
判例：对照实验——单变量逐项排除。
用法：python s0_cwd_variant_probe.py [temp|news]
"""
import json
import os
import subprocess
import time

PROBE = "ProbeS0Cwd"


def ps(cmd: str, t: int = 60):
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                          capture_output=True, timeout=t, check=False)


def cleanup():
    ps(f"Unregister-ScheduledTask -TaskName '{PROBE}' -Confirm:$false -ErrorAction SilentlyContinue")


def main():
    import sys
    variant = sys.argv[1] if len(sys.argv) > 1 else "temp"
    cleanup()
    script = os.path.join(os.environ["TEMP"], "s0cwd_probe.py")
    with open(script, "w") as f:
        f.write("print('ok')\n")
    if variant == "temp":
        cwd = os.environ["TEMP"]
        label = "cwd=%TEMP%（昨晚 ProbeStartB/D 形态）"
    elif variant == "news":
        cwd = r"C:\项目\老田的AI助手\news_collector"
        label = "cwd=news_collector（昨晚 ProbeStartA 失败形态，中文路径）"
    else:
        raise SystemExit("variant 须为 temp|news")
    ps(f"$a=New-ScheduledTaskAction -Execute 'C:\\Program Files\\Python311\\python.exe' "
       f"-Argument '\"{script}\"' -WorkingDirectory '{cwd}'; "
       f"Register-ScheduledTask -TaskName '{PROBE}' -Action $a|Out-Null; "
       f"Start-ScheduledTask -TaskName '{PROBE}'; 'started'")
    time.sleep(20)
    info = ps(f"(Get-ScheduledTaskInfo -TaskName '{PROBE}').LastTaskResult")
    rc = info.stdout.decode("gbk", errors="replace").strip()
    cleanup()
    os.unlink(script)
    print(json.dumps({"variant": label, "launch_rc": rc,
                      "baseline_no_cwd_rc": 0,
                      "at": "2026-10-23T04:4x"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
