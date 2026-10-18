# -*- coding: utf-8 -*-
"""trigger_precheck_wrapper：周一触发链 T-30min 预检计划任务动作体（AgentTriggerChainPrecheck）。

## 职责

把 SOP §一第 5 项（`tools/check_trigger_chain.py --repo-root .`，T-30min 必跑）
从人工步骤收口为计划任务自动化：周一 16:40 由 AgentTriggerChainPrecheck 拉起本
包装器 → subprocess 调同目录 check_trigger_chain.py（cwd=agent_platform）→
捕获 stdout/stderr 落日志 + 写状态文件 → 异常时挂现有告警链。

## 产物落点（判例③：定时任务产物统一落 C:\\aios\\automation_runs\\<任务名>\\；
C:\\aios junction 即仓内 automation_runs，repo 路径写 = 同一落点）

- precheck_YYYY-MM-DD.log  追加式、当日分文件；每次 run 一个块，块首行为
  run 头（异常 run 的 [ALERT] 标记行在块首——append-only 日志无法回写文件头，
  「头部」语义按本次 run 块首落实）
- latest.json              单次覆写 {date, exit_code, pass, warn, fail,
  summary_line, ts, alert_pushed}（人工复核面）

## 告警面（不自创，挂现有链）

退出码非 0 或 fail>0 时：① 块首写 [ALERT] 标记行；② subprocess 调
news_collector/send_feishu.py（execution_watchdog.push_feishu 同款判例：
消息走 UTF-8 临时文件，不走命令行参数）推飞书 P0 卡。同日同退出码已推过
则不重推（防人工反复手跑刷屏；预检周频无 24h 节流必要）。
manifest 白名单（portal/execution_manifest.json，fail_scan→R3 必响）在
portal/ 禁区，未挂——移交方案见 2026-10-18 交付报告。

## 红线

- check_trigger_chain.py 只调用不修改（d9593586 刚收口）；
- 自身 fail-visible：任何异常都落日志 + latest.json（exit_code=-1），退出码非 0；
- 子进程 timeout=120s 包住（预检正常 ~10s）；零网络（飞书推送除外——
  复用 send_feishu.py 既有面，与 execution_watchdog 同源）。

用法（计划任务 Action 同款）：

    python.exe -X utf8 tools/trigger_precheck_wrapper.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# 本文件在 agent_platform/tools/ 下；被检工具同目录
_HERE = Path(__file__).resolve().parent
CHECK_SCRIPT = _HERE / "check_trigger_chain.py"
# cwd=agent_platform（SOP 人工口径：cwd=agent_platform + --repo-root .；
# 这里传绝对路径等价且更稳，data/shadow 取证面同一落点）
REPO_ROOT = _HERE.parent

# 判例③：统一落 C:\aios\automation_runs\<任务名>\
STATE_DIR = Path(r"C:\aios\automation_runs\trigger_precheck")
#: 子进程超时（秒）
SUBPROCESS_TIMEOUT_S = 120
#: 飞书推送 CLI（execution_watchdog.SEND_FEISHU 同源）
SEND_FEISHU = _HERE.parent.parent / "news_collector" / "send_feishu.py"

ALERT_TAG = "[ALERT]"


def _now() -> datetime:
    return datetime.now()


def _append_log(lines) -> None:
    """追加写当日日志（判例①：计划任务 Execute 不是 shell，重定向不生效，
    工具必须自写日志）。写失败向上抛（fail-visible）。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log = STATE_DIR / ("precheck_%s.log" % _now().strftime("%Y-%m-%d"))
    with open(log, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _write_latest(state: dict) -> None:
    """单次覆写 latest.json（人工复核面）。写失败向上抛。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "latest.json", "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def _parse_stdout(stdout: str) -> dict:
    """从 check_trigger_chain 输出提取 pass/warn/fail 与结论行；解析不出置 -1。"""
    m = re.search(r"汇总:\s*PASS\s+(\d+)\s*/\s*WARN\s+(\d+)\s*/\s*FAIL\s+(\d+)", stdout)
    concl = re.search(r"^结论:\s*(.+)$", stdout, re.M)
    return {
        "pass": int(m.group(1)) if m else -1,
        "warn": int(m.group(2)) if m else -1,
        "fail": int(m.group(3)) if m else -1,
        "summary_line": concl.group(1).strip() if concl else "（未解析到结论行）",
    }


def _alert_needed(exit_code: int, fail: int) -> bool:
    """退出码非 0 或 fail>0 → 告警（fail 解析失败 -1 也视为需告警，宁重报勿漏报）。"""
    return exit_code != 0 or fail > 0


def _same_alert_already_pushed(today: str, exit_code: int, prev: dict) -> bool:
    """同日同退出码已推过 → 不重推（防人工反复手跑刷屏）。"""
    return bool(prev) and prev.get("date") == today \
        and prev.get("exit_code") == exit_code \
        and prev.get("alert_pushed") is True


def _push_feishu(text: str) -> bool:
    """经 send_feishu.py 推送（UTF-8 临时文件判例）。返回 SEND_OK 判定，
    异常不抛（告警链自身失败不掩盖预检结果，推送结果落日志可见）。"""
    try:
        fd, tmp = tempfile.mkstemp(prefix="precheck_msg_", suffix=".txt")
        try:
            with open(fd, "w", encoding="utf-8") as f:
                f.write(text)
            r = subprocess.run([sys.executable, "-X", "utf8", str(SEND_FEISHU), tmp],
                               capture_output=True, timeout=120)
            return "SEND_OK" in (r.stdout or b"").decode("utf-8", errors="replace")
        finally:
            try:
                Path(tmp).unlink(missing_ok=True)
            except OSError:
                pass
    except Exception as exc:  # noqa: BLE001
        return False


def _read_prev_latest() -> dict:
    try:
        with open(STATE_DIR / "latest.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001  缺失/坏 JSON = 无历史（不节流，宁重报勿漏报）
        return {}


def run() -> int:
    now = _now()
    today = now.strftime("%Y-%m-%d")
    ts = now.isoformat(timespec="seconds")
    head = "===== %s AgentTriggerChainPrecheck run start =====" % ts

    # 1) 跑预检（timeout 包住；自身崩溃也留痕）
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", str(CHECK_SCRIPT),
             "--repo-root", str(REPO_ROOT)],
            capture_output=True, timeout=SUBPROCESS_TIMEOUT_S, cwd=str(REPO_ROOT))
        exit_code = proc.returncode
        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")
        wrapper_error = None
    except Exception as exc:  # noqa: BLE001  超时/拉起失败 = fail-visible
        exit_code, stdout, stderr = -1, "", ""
        wrapper_error = "wrapper 拉起 check_trigger_chain 失败: %r" % exc

    parsed = _parse_stdout(stdout)
    need_alert = _alert_needed(exit_code, parsed["fail"])

    # 2) 组日志块（[ALERT] 在块首）并落盘
    block = [head]
    if need_alert:
        block.append("%s 退出码=%s FAIL=%s —— 链路未就绪，按输出「→」指引处置，"
                     "17:10 前必须查清" % (ALERT_TAG, exit_code, parsed["fail"]))
    if wrapper_error:
        block.append(wrapper_error)
    block.append(stdout.rstrip())
    if stderr.strip():
        block.append("[stderr]")
        block.append(stderr.rstrip())
    block.append("===== %s run end =====" % _now().isoformat(timespec="seconds"))
    _append_log(block)

    # 3) 告警（挂现有链：send_feishu.py）+ 节流
    prev = _read_prev_latest()
    alert_pushed = False
    alert_note = ""
    if need_alert:
        if _same_alert_already_pushed(today, exit_code, prev):
            alert_note = "同日同退出码已推过，不重推"
        else:
            alert_pushed = _push_feishu(
                "🚨 [P0] 周一触发链预检异常（AgentTriggerChainPrecheck）\n"
                "- 时间：%s\n- 退出码：%s（PASS %s / WARN %s / FAIL %s）\n"
                "- 结论：%s\n- 日志：C:\\aios\\automation_runs\\trigger_precheck\\\n"
                "- 处置：按日志内「→」指引逐项处置，17:10 影子首跑前查清"
                % (ts, exit_code, parsed["pass"], parsed["warn"],
                   parsed["fail"], parsed["summary_line"]))
            alert_note = "飞书推送 %s" % ("SEND_OK" if alert_pushed else "失败（人工看日志）")

    # 4) latest.json（人工复核面）
    state = {"date": today, "exit_code": exit_code,
             "pass": parsed["pass"], "warn": parsed["warn"], "fail": parsed["fail"],
             "summary_line": parsed["summary_line"], "ts": ts,
             "alert_pushed": alert_pushed}
    if alert_note:
        state["alert_note"] = alert_note
    _write_latest(state)

    print("precheck exit_code=%s pass=%s warn=%s fail=%s alert=%s %s"
          % (exit_code, parsed["pass"], parsed["warn"], parsed["fail"],
             alert_pushed, alert_note))
    return 0 if exit_code == 0 else (exit_code if exit_code > 0 else 1)


def main() -> int:
    try:
        return run()
    except Exception as exc:  # noqa: BLE001  fail-visible：崩溃也留痕
        err = "wrapper 自身异常: %r" % exc
        try:
            _append_log(["===== %s WRAPPER CRASH =====" % _now().isoformat(timespec="seconds"),
                         err, "===== run end ====="])
            _write_latest({"date": _now().strftime("%Y-%m-%d"), "exit_code": -1,
                           "pass": -1, "warn": -1, "fail": -1,
                           "summary_line": err, "ts": _now().isoformat(timespec="seconds"),
                           "alert_pushed": False})
        except Exception:  # noqa: BLE001  连日志面都挂了只能 print（任务计划程序历史可见）
            pass
        print(err, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
