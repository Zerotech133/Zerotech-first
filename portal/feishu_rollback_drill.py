# -*- coding: utf-8 -*-
"""W3 切换回滚演练（dry-run，2026-10-24）。

不执行任何停服/删文件动作；只做三件事：
  1. 逐项核验"回滚材料"是否齐全（凭据快照、拉起器、旧代码备份、计划任务…）；
  2. 输出回滚步骤清单（含确切命令）；
  3. 给出 READY / NOT-READY 结论与退出码。
真回滚在 W3 窗口内由 Lead 按本清单执行，并逐条回执。

用法：
    python feishu_rollback_drill.py
    python feishu_rollback_drill.py --json --out <path.md>
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import socket
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORK = os.path.join(HERE, "work")
SECRETS = os.path.join(WORK, "feishu_secrets")
CANONICAL = os.path.join(SECRETS, "canonical.json")
LOGS = os.path.join(HERE, "logs")
DSH_REMOTE_DIR = os.environ.get("DSH_REMOTE_DIR") or r"C:\项目\dsh_runtime"
BACKUP_GLOB = os.path.join(DSH_REMOTE_DIR, "feishu_bot_ws.py.bak.*")
TASKS = ("DSHBotWatchdog", "DSHExecBot")


def _check(name, ok, detail="", critical=True):
    return {"name": name, "ok": bool(ok), "critical": bool(critical),
            "detail": str(detail)[:240]}


def _task_exists(name):
    """计划任务存在性：schtasks 查询；沙箱拒绝时回退读任务 XML 文件。"""
    try:
        r = subprocess.run(["schtasks", "/query", "/tn", name],
                           capture_output=True, timeout=20)
        if r.returncode == 0:
            return True
    except Exception:  # noqa: BLE001
        pass
    return os.path.exists(os.path.join(r"C:\Windows\System32\Tasks", name))


def _listen(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def artifact_checks():
    checks = []
    # 1) canonical 凭据快照（回滚 = 删除该文件即回到 apps[0] 旧行为）
    canonical = ""
    try:
        with open(CANONICAL, encoding="utf-8") as f:
            canonical = str(json.load(f).get("app_id") or "")
        checks.append(_check("canonical 凭据快照", bool(canonical),
                             "app_id=%s；回滚=重命名/删除该文件" % canonical))
    except (OSError, ValueError) as e:
        checks.append(_check("canonical 凭据快照", False, "读取失败：%s" % e))

    # 2) 拉起器
    for fn in ("start_bot_watchdog_once.ps1", "start_feishu_exec_bot.ps1"):
        p = os.path.join(HERE, fn)
        checks.append(_check("拉起器 %s" % fn, os.path.exists(p),
                             "存在" if os.path.exists(p) else "缺失 %s" % p))

    # 3) 旧 bot 代码备份（回滚可还原）
    backups = sorted(glob.glob(BACKUP_GLOB))
    checks.append(_check("旧 bot 代码备份", bool(backups),
                         (os.path.basename(backups[-1]) if backups
                          else "未找到 %s" % BACKUP_GLOB)))

    # 4) push_targets 配置（加了 app 字段；回滚可删字段）
    cfg = os.path.join(ROOT, "news_collector", "news_config.json")
    try:
        with open(cfg, encoding="utf-8") as f:
            targets = json.load(f).get("push_targets") or []
        have_app = all(t.get("app") for t in targets)
        checks.append(_check("push_targets 配置快照", bool(targets),
                             "目标 %d 条；app 字段=%s（回滚可删字段）"
                             % (len(targets), "有" if have_app else "无")))
    except (OSError, ValueError) as e:
        checks.append(_check("push_targets 配置快照", False, "读取失败：%s" % e))

    # 5) 计划任务
    missing = [t for t in TASKS if not _task_exists(t)]
    checks.append(_check("计划任务在位", not missing,
                         "缺失=%s" % (missing or "无")))

    # 6) 基线服务
    checks.append(_check("8097 闸门在听", _listen(8097),
                         "up" if _listen(8097) else "down"))

    # 7) 验收脚本可用
    acc = os.path.join(HERE, "feishu_acceptance.py")
    checks.append(_check("验收脚本可用", os.path.exists(acc),
                         os.path.basename(acc)))
    return checks


def rollback_plan():
    """回滚步骤清单（命令为 Windows 原生；执行需人工确认）。"""
    return [
        {"step": 1, "action": "停看护与执行 bot",
         "cmd": 'schtasks /end /tn "DSHBotWatchdog"; schtasks /end /tn "DSHExecBot"'},
        {"step": 2, "action": "停 8097 闸门（按端口找 PID，勿盲杀）",
         "cmd": "Get-NetTCPConnection -LocalPort 8097 | "
                "Select-Object -ExpandProperty OwningProcess"},
        {"step": 3, "action": "还原 canonical 凭据选择（回滚 = 移走 canonical.json）",
         "cmd": 'Rename-Item "C:\\aios\\portal\\work\\feishu_secrets\\canonical.json" '
                'canonical.json.rollback'},
        {"step": 4, "action": "还原 bot 代码（如需）",
         "cmd": 'Copy-Item "C:\\项目\\dsh_runtime\\feishu_bot_ws.py.bak.<ts>" '
                '"C:\\aios\\portal\\feishu_bot_ws.py" -Force'},
        {"step": 5, "action": "重启看护循环（拉起旧 bot）",
         "cmd": 'powershell -NoProfile -File "C:\\aios\\portal\\start_bot_watchdog_once.ps1"'},
        {"step": 6, "action": "重启 8097 并复跑验收",
         "cmd": "python C:\\\\aios\\\\portal\\\\feishu_acceptance.py --skip-tests"},
    ]


def summarize(checks):
    failed = [c for c in checks if not c["ok"]]
    critical = [c for c in failed if c["critical"]]
    return {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total": len(checks), "passed": len(checks) - len(failed),
            "failed": len(failed), "critical_failed": len(critical),
            "verdict": "READY" if not critical else "NOT-READY",
            "checks": checks, "plan": rollback_plan()}


def render(report):
    lines = ["# W3 切换回滚演练（dry-run）", "",
             "时间：%s ｜ 结论：**%s**（材料 %d/%d）" % (
                 report["ts"], report["verdict"],
                 report["passed"], report["total"]), "",
             "## 回滚材料核验", "", "| 结果 | 项目 | 说明 |", "|---|---|---|"]
    for c in report["checks"]:
        lines.append("| %s | %s | %s |" % (
            "✅" if c["ok"] else "❌", c["name"], c["detail"]))
    lines += ["", "## 回滚步骤（执行前需人工确认）", ""]
    for s in report["plan"]:
        lines.append("%d. **%s**" % (s["step"], s["action"]))
        lines.append("   ```")
        lines.append("   " + s["cmd"])
        lines.append("   ```")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    rep = summarize(artifact_checks())
    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(render(rep))
    print(json.dumps(rep, ensure_ascii=False, indent=1) if args.json
          else render(rep))
    return 0 if rep["verdict"] == "READY" else 1


if __name__ == "__main__":
    sys.exit(main())
