# -*- coding: utf-8 -*-
"""任务失败飞书告警器（OPS-0930b：rc≠0 自动告警——P1-3 闭环最后一公里，
"launcher 不假绿了但没人被告知"的缺口）。

用法: python alert_fail.py <task> <exit_code> [log_path]
复用 news_collector/send_feishu.py 发送（与 chanlun_scan._push_feishu_text 同口径），
消息附日志尾 5 行便于定位。发送失败仅打印不抛错（告警器不能反噬任务 rc）。

K268（2026-10-08）：掉电击杀标注——本机近 7 天数十次 AC 掉电，Windows 计划任务
默认"用电池时停止任务"（StopIfGoingOnBatteries）会在掉电瞬间批量杀运行中任务
（动作码 0x8007050B，任务码 0x8007042B），导致同分钟多条"任务失败"独立误报。
发送前查 System 日志近 10 分钟 Kernel-Power EID=105(AcOnline=false)：
命中则消息头加"[疑似掉电击杀·电池策略]"标注并附佐证——不抑制告警，只归类语义。
查询失败按无掉电处理（诊断不能反噬告警）。五个受害任务电池策略已关闭
（CryptoSync/ChanlunScanUS/JEVBackfill-Hourly/RtdxRtMinPull/_sentinelEOD）。
"""

import os
import subprocess
import sys
from datetime import datetime, timedelta
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))


def recent_power_loss(window_min=10):
    """近 N 分钟内 AC 掉电事件数（Kernel-Power EID=105 且 AcOnline=false）。

    独立佐证"任务失败=环境击杀"。任何异常返回 0（告警器不能因诊断挂掉）。
    """
    try:
        since = (datetime.utcnow() - timedelta(minutes=window_min)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        q = (
            "*[System[Provider[@Name='Microsoft-Windows-Kernel-Power'] and EventID=105"
            " and TimeCreated[@SystemTime>='%s.000Z']]]" % since
        )
        r = subprocess.run(
            ["wevtutil", "qe", "System", "/q:" + q, "/f:xml", "/rd:true", "/c:10"],
            capture_output=True,
            timeout=20,
        )
        out = r.stdout.decode("utf-8", "replace")
        return out.count("AcOnline'>false")
    except Exception:
        return 0


def alert(task, code, log=""):
    """python 侧直接调用（launcher 走 main/argv 口径）。"""
    msg = "[QUANT ALERT] task failed: %s exit=%s %s" % (
        task,
        code,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    if log and os.path.isfile(log):
        try:
            # 二十一审：日志编码三态——Start-Process 重定向=raw UTF-8；PS5.1
            # `1> file` 重定向=UTF-16LE(BOM ff fe)；个别旧任务=GBK。原 utf-8
            # 硬解会让 UTF-16/GBK 的 tail 全变替换符（连英文关键字都丢）。
            raw = open(log, "rb").read()
            if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
                txt = raw.decode("utf-16")
            elif raw[:3] == b"\xef\xbb\xbf":
                txt = raw.decode("utf-8-sig")
            else:
                try:
                    txt = raw.decode("utf-8")
                except UnicodeDecodeError:
                    txt = raw.decode("gbk", errors="replace")
            # 内层 mojibake 逆转：PS 管道按 GBK 解过 python 的 UTF-8 输出
            # （锛=（/鏄=是/鏃=日·无 的 GBK 误读形），可无损还原；失败保持原文。
            # errors=replace：U+FFFD 不在 GBK 码表，strict 会让一个坏字符
            # 杀死整篇逆转（实测 miss_check "）"尾字节即中招），降级为 ? 换全局可读。
            if "锛" in txt or "鏄" in txt or "鏃" in txt:
                try:
                    txt = txt.encode("gbk", errors="replace").decode(
                        "utf-8", errors="replace"
                    )
                except UnicodeDecodeError as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/alert_fail.py", exc)
            tail = txt.splitlines()[-5:]
            if tail:
                msg += "\n--- log tail ---\n" + "\n".join(tail).rstrip()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/alert_fail.py", exc)
    n = recent_power_loss()
    if n:
        msg = (
            "[疑似掉电击杀·电池策略] 近10分钟 AC 掉电事件 %d 条，"
            "Windows 默认'用电池时停止任务'会批量终止运行中任务(0x8007050B)，"
            "非任务自身故障；供电恢复后下个触发器自动重跑。\n%s" % (n, msg)
        )
    tmp = os.path.join(NEWS_DIR, "outbox", "pending_alert_%s.txt" % task)
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(msg)
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"), tmp],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        print((r.stdout or "").strip()[:60])
    except Exception as e:
        print("alert send error: %s" % str(e)[:80])


def main():
    task, code = sys.argv[1], sys.argv[2]
    alert(task, code, sys.argv[3] if len(sys.argv) > 3 else "")


if __name__ == "__main__":
    main()
