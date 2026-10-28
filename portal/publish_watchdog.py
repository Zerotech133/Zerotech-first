# -*- coding: utf-8 -*-
"""
publish_watchdog.py — picks 公网发布看门狗（本地定时兜底）

定位（2026-10-13 事故复盘后的兜底层）：
    平台自动化 4cbbeed8 每天 16:40 跑 sync+条件发布；同日更晚的快照刷新
    （如 22:53）不在其覆盖窗内，且 WorkBuddy 站点 deploy 只能由 agent 侧
    触发、automation 工具不可用。本脚本由 Windows 计划任务每日 23:30 拉起
    （或手动跑），做三件事：
      1. 跑 publish_picks.py --check-online（sync + hash + 多文件线上比对）
      2. 退出码 0（且 stdout 含正常结论佐证）→ 安静记日志；
         若此前有 DEPLOY_PENDING 旗标则清除（清除失败不发绿卡，留待重试）
      3. 退出码 2 且 stdout 含"[滞后]"佐证 → 线上滞后：原子写 DEPLOY_PENDING
         旗标 + 飞书卡片告警（提示对 agent 说"重新发布 picks"）
      其他（含 argparse 退出码 2 但无佐证）→ 链路异常：记日志 + 橙色告警

v1.1（2026-10-14，团队审计后修复）：
    - 子进程 stderr 落日志并在橙卡中带最后一行（P1-3）
    - rc==2 需 stdout"[滞后]"佐证才发红卡，防 argparse 退出码撞车（P2-3）
    - clear_flag 失败不再谎报"已恢复"（P1-4）
    - 旗标原子写；告警 hash 从 state 的 online_hashes 多文件取（P1-1 配套）
    - 子进程强制 UTF-8 管道（P2-1）；schtasks 输出按 GBK 解码（中文 Windows）

用法：
    python publish_watchdog.py              # 正常巡检
    python publish_watchdog.py --install    # 注册每日 23:30 计划任务（幂等，/F 覆盖）
    python publish_watchdog.py --uninstall  # 删除计划任务
零第三方依赖（feishu_card_v2 为项目内模块）。
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

WORK = os.path.join(HERE, "work")
LOG_PATH = os.path.join(WORK, "publish_watchdog.log")
FLAG_PATH = os.path.join(WORK, "DEPLOY_PENDING.json")
CHAIN = os.path.join(HERE, "publish_picks.py")

TASK_NAME = "x5-picks-publish-check"
TASK_TIME = "23:30"

LINK = "https://quant-picks-board.app.workbuddy.host/"

CHILD_ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")


def log(msg):
    os.makedirs(WORK, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))


def atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(path), prefix=os.path.basename(path) + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def run_chain():
    """跑一键链；返回 (退出码, stdout尾部, stderr尾部)。stderr 不再丢弃（P1-3）。"""
    proc = subprocess.run(
        [sys.executable, CHAIN, "--check-online"],
        cwd=HERE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=CHILD_ENV,
    )
    out_tail = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()][-8:]
    err_tail = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()][-5:]
    return proc.returncode, out_tail, err_tail


def read_state_compare():
    """从 publish_state.json 取本地/线上 hash（多文件），供告警文案。"""
    try:
        with open(os.path.join(WORK, "publish_state.json"), encoding="utf-8") as f:
            s = json.load(f)
        return s.get("hashes") or {}, s.get("online_hashes") or {}
    except (OSError, ValueError):
        return {}, {}


def compare_md(hashes, online_hashes):
    lines = []
    for rel, oh in online_hashes.items():
        lh = hashes.get(rel, "?")
        mark = "一致" if oh == lh else "**不一致**"
        lines.append(
            "- %s：%s（本地 `%s...` / 线上 `%s...`）" % (rel, mark, lh[:16], oh[:16])
        )
    return "\n".join(lines) or "（无比对数据）"


def send_feishu(title, template, md, critical=False):
    """发飞书卡片给老田；返回 (ok, msg)，失败不抛（2026-10-28 OS-FIX-L1-AUDIT）。"""
    try:
        from feishu_card_v2 import send_card
    except Exception as e:  # noqa: BLE001
        msg = "feishu_card_v2 导入失败: %s" % e
        log("[warn] " + msg)
        return False, msg
    body = {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": template,
        },
        "body": {"elements": [{"tag": "markdown", "content": md}]},
    }
    try:
        ok, msg = send_card(body, critical=critical)
        log("[feishu] ok=%s msg=%s" % (ok, msg))
        return bool(ok), str(msg)
    except Exception as e:  # noqa: BLE001
        msg = "飞书发送异常: %s" % e
        log("[warn] " + msg)
        return False, msg


def write_flag(hashes, online_hashes, mismatched):
    atomic_write_json(
        FLAG_PATH,
        {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mismatched": mismatched,
            "hashes": hashes,
            "online_hashes": online_hashes,
        },
    )


def clear_flag():
    try:
        os.remove(FLAG_PATH)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def inspect():
    rc, out_tail, err_tail = run_chain()
    for ln in out_tail:
        log("[chain] " + ln)
    for ln in err_tail:
        log("[chain-err] " + ln)

    # rc==2 需 stdout "[滞后]" 佐证，防 argparse 退出码撞车（P2-3）
    lag_confirmed = rc == 2 and any("[滞后]" in ln for ln in out_tail)

    if rc == 0 and not any("[滞后]" in ln for ln in out_tail):
        if os.path.exists(FLAG_PATH):
            if clear_flag():
                log("[恢复] 线上已追平，清除 DEPLOY_PENDING 旗标")
                send_feishu(
                    "picks 发布已恢复",
                    "green",
                    "线上比对文件已全部与本地快照一致，DEPLOY_PENDING 已清除。\n%s"
                    % LINK,
                )
            else:
                # P1-4：清除失败不发绿卡，留待下次重试
                log("[warn] 旗标清除失败（可能被占用），保留待下次重试，本次不发恢复卡")
        else:
            log("[ok] 线上已是最新")
        return 0

    if lag_confirmed:
        hashes, online_hashes = read_state_compare()
        mismatched = [rel for rel, oh in online_hashes.items() if hashes.get(rel) != oh]
        write_flag(hashes, online_hashes, mismatched)
        ok, msg = send_feishu(
            "⚠️ picks 线上滞后告警",
            "red",
            "本地快照与线上不一致，需要重新 deploy。\n\n"
            "**处置**：对 WorkBuddy agent 说「**重新发布 picks**」即可"
            "（目录/入口/appId 不变，链接不变）。\n\n"
            + compare_md(hashes, online_hashes)
            + "\n\n链接：%s" % LINK,
            critical=True,  # 预警类豁免静默窗（修复连续三晚被吞）
        )
        if ok:
            log("[滞后] 线上滞后，已落旗标并发送红卡")
        else:
            log("[FAIL] 线上滞后红卡未送达：%s" % msg)
            atomic_write_json(
                os.path.join(WORK, "PUBLISH_ALERT_FAILED.json"),
                {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "mismatched": mismatched, "error": msg},
            )
        return 2

    # 其余一律按链路异常处理（含 rc==2 无佐证）
    reason = err_tail[-1] if err_tail else (out_tail[-1] if out_tail else "无输出")
    log("[异常] 一键链退出码 %d" % rc)
    send_feishu(
        "⚠️ picks 发布链异常",
        "orange",
        "publish_picks.py 退出码 %d（非确认滞后、非正常），请人工查看\n"
        "portal/work/publish_watchdog.log。\n\n最后一行错误：`%s`" % (rc, reason),
    )
    return rc if rc != 2 else 1


def install():
    """注册每日计划任务（幂等）。用 schtasks + unicode 安全的参数列表。
    注意（审计 P2-5）：该路径被安全中心黑名单拦截、尚未实测，放行后需
    schtasks /Query /TN x5-picks-publish-check /V 核对 /TR 完整命令行。"""
    action = '"%s" "%s"' % (sys.executable, os.path.abspath(__file__))
    cmd = [
        "schtasks",
        "/Create",
        "/F",
        "/TN",
        TASK_NAME,
        "/SC",
        "DAILY",
        "/ST",
        TASK_TIME,
        "/TR",
        action,
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="gbk", errors="replace"
    )
    print(proc.stdout or "")
    if proc.returncode != 0:
        print(proc.stderr or "", file=sys.stderr)
        print("[失败] 计划任务注册退出码 %d" % proc.returncode, file=sys.stderr)
        return proc.returncode
    print("[ok] 计划任务 %s 已注册：每日 %s 运行 %s" % (TASK_NAME, TASK_TIME, action))
    return 0


def uninstall():
    proc = subprocess.run(
        ["schtasks", "/Delete", "/F", "/TN", TASK_NAME],
        capture_output=True,
        text=True,
        encoding="gbk",
        errors="replace",
    )
    print(proc.stdout or proc.stderr or "")
    return proc.returncode


def main():
    parser = argparse.ArgumentParser(description="picks 公网发布看门狗")
    parser.add_argument(
        "--install", action="store_true", help="注册每日 %s 计划任务" % TASK_TIME
    )
    parser.add_argument("--uninstall", action="store_true", help="删除计划任务")
    args = parser.parse_args()
    if args.install:
        return install()
    if args.uninstall:
        return uninstall()
    return inspect()


if __name__ == "__main__":
    sys.exit(main())

