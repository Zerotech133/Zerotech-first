# -*- coding: utf-8 -*-
"""提权服务重启监督（默认只检查；--apply 才触发重启）。

问题（2026-10-29 实证）：8796 是提权/会话级进程，交互 shell `Stop-Process` = Access denied；
重启依赖已批准的计划任务（LegionOps）消费 `C:\\Temp\\LEGION_8796_RESTART.flag`。
本工具把「置哨兵 → 触发任务 → 等 flag 被消费 → 健康 + 可选 probe 校验」固化为一步，
失败 fail-visible（不假装成功）；默认 dry-run，绝不自行杀进程。

用法：
  python tools/service_restart.py --check                     # 只报健康/哨兵状态
  python tools/service_restart.py --apply --timeout 240       # 真实重启（走已批准任务）
  python tools/service_restart.py --apply --probe /api/hq/meeting/stats
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.request
from pathlib import Path

HEALTH = "http://127.0.0.1:8796/api/zhaoyun"
FLAG = Path(r"C:\Temp\LEGION_8796_RESTART.flag")
TASK = "LegionOps"
POLL_SEC = 5


def health(url: str = HEALTH, timeout: float = 5) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read(400).decode("utf-8", errors="replace")
            return {"ok": 200 <= int(getattr(r, "status", 0)) < 300,
                    "status": getattr(r, "status", None), "body": body[:200]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:120])}


def start_task(task: str, runner=None) -> dict:
    # runner 注入（测试/自定义执行器）签名：runner(task) -> {"ok":bool}|CompletedProcess
    if runner is not None:
        try:
            r = runner(task)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:120])}
        if isinstance(r, dict):
            return r
        return {"ok": getattr(r, "returncode", 1) == 0,
                "rc": getattr(r, "returncode", None)}
    cmd = ["powershell", "-NoProfile", "-Command",
           "Start-ScheduledTask -TaskName '%s'" % task]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:120])}
    return {"ok": p.returncode == 0, "rc": p.returncode,
            "stderr": (p.stderr or "")[-200:]}


def check(flag: Path = FLAG, url: str = HEALTH) -> dict:
    return {"ok": True, "health": health(url),
            "flag_pending": Path(flag).exists(), "flag": str(flag)}


def restart(flag: Path = FLAG, task: str = TASK, url: str = HEALTH,
            probe: str | None = None, timeout: int = 240,
            apply: bool = False, starter=None, health_fn=None,
            sleep=time.sleep) -> dict:
    if not apply:
        return {"ok": True, "dry_run": True,
                "plan": {"flag": str(flag), "task": task, "health": url,
                         "probe": probe, "timeout": timeout},
                "note": "dry-run：--apply 才置哨兵并触发任务；不杀进程"}
    flag = Path(flag)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("", encoding="utf-8")
    st = start_task(task, runner=starter)
    if not st.get("ok"):
        return {"ok": False, "error": "task_start_failed", "detail": st,
                "flag": str(flag)}
    hp = health_fn or (lambda u: health(u))
    deadline = time.time() + max(5, timeout)
    last = {}
    while time.time() < deadline:
        last = hp(url)
        # 2026-10-30 审计修复：必须「当前」健康才能报成功；旧实现只要求曾经健康，
        # 可能在服务刚被杀死、尚未拉起时返回 ok=true + health=失败（假成功）。
        if last.get("ok") and not flag.exists():
            if probe:
                pb = hp(url.split("/api/")[0] + probe if probe.startswith("/") else probe)
                if not pb.get("ok"):
                    return {"ok": False, "error": "probe_failed", "probe": pb,
                            "health": last}
            final = hp(url)  # probe 后复查，避免返回旧快照
            if not final.get("ok"):
                last = final
                sleep(POLL_SEC)
                continue
            return {"ok": True, "restarted": True, "health": final,
                    "flag_consumed": True, "elapsed_sec": round(
                        timeout - (deadline - time.time()), 1)}
        sleep(POLL_SEC)
    return {"ok": False, "error": "timeout", "health": last,
            "flag_consumed": not flag.exists(), "flag": str(flag)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="提权服务重启监督（默认 dry-run）")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true", help="真实触发（经已批准计划任务）")
    ap.add_argument("--flag", default=str(FLAG))
    ap.add_argument("--task", default=TASK)
    ap.add_argument("--url", default=HEALTH)
    ap.add_argument("--probe", default="")
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.check or not args.apply:
        rep = check(Path(args.flag), args.url)
    else:
        rep = restart(Path(args.flag), args.task, args.url,
                      args.probe or None, args.timeout, apply=True)
    print(json.dumps(rep, ensure_ascii=False, indent=1) if args.json
          else json.dumps(rep, ensure_ascii=False)[:1000])
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
