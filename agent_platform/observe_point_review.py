# -*- coding: utf-8 -*-
"""周一观察点复查执行器（K-ORG 预约 2026-10-18 19:1x 条目的机械化执行件）。

预约口径：17:35 后逐件复查三件首跑日志 + 门3状态——
  1) data/shadow/last_run.log        （AgentShadowDaily 17:10 首跑）
  2) data/shadow/monday_checkpoint.log（AgentMondayCheckpoint 17:25 自然轮）
  3) data/shadow/chief_last_run.log  （AgentChiefPush 17:35 首跑）
  4) tools/gate3_verdict.py          （门3状态，只读重算+巡检交叉核对）
全过 → 输出「可开军团 M4 施工单」（拍板口径见
agent_platform/docs/audit/2026-10-18_军团M4M5规划稿.md 头部拍板段）；
任一件红 → 输出「SOP 异常矩阵处置，M4/M5 施工顺延」+ 红因明细。

红线：只读零写入（唯一产出=stdout；公告板通报由执行会话负责）。
退出码：0=全绿可开单；1=任一件红（顺延）；2=前置未到时点（中性，
17:35 前空跑验证用，不算红）。
"""
import os
import re
import subprocess
import sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SHADOW = os.path.join(HERE, "data", "shadow")
TODAY = date.today().isoformat()

results = []  # (name, "GREEN"/"RED"/"PENDING", detail)


def _now():
    return datetime.now()


def add(name, state, detail=""):
    results.append((name, state, detail))
    mark = {"GREEN": "[PASS]", "RED": "[FAIL]", "PENDING": "[wait]"}[state]
    print("%s %s %s" % (mark, name, detail))


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def judge_last_run():
    """1) last_run.log：今日 17: 块 + shadow day 今日 done 汇总行。"""
    txt = read(os.path.join(SHADOW, "last_run.log"))
    blocks = re.split(r"===== shadow run start ([0-9\-:. ]+) =====", txt)
    for i in range(1, len(blocks) - 1, 2):
        ts, body = blocks[i], blocks[i + 1]
        if ts.startswith(TODAY + " 17:"):
            if re.search(r"shadow day %s done:" % TODAY, body):
                n = re.search(r"'done': (\d+)", body)
                add("1) last_run.log（ShadowDaily 首跑）", "GREEN",
                    "块 %s，done=%s" % (ts.strip(), n.group(1) if n else "?"))
            else:
                add("1) last_run.log（ShadowDaily 首跑）", "RED",
                    "今日块 %s 无 shadow day 汇总行（疑似半途中断）" % ts.strip())
            return
    add("1) last_run.log（ShadowDaily 首跑）",
        "PENDING" if _now().hour < 17 else "RED",
        "无今日 17: 首跑块" + ("" if _now().hour < 17
                              else "（已过 17:10 仍缺失→SOP 异常矩阵）"))


def judge_checkpoint():
    """2) monday_checkpoint.log：当日 >=17:00 自然轮块 + 结论 PASS + EXIT=0。"""
    txt = read(os.path.join(SHADOW, "monday_checkpoint.log"))
    chunks = re.split(r"^=+$", txt, flags=re.M)
    for idx, seg in enumerate(chunks):
        stamps = re.findall(r"20\d\d-\d\d-\d\d \d\d:\d\d", seg)
        if not any(s.startswith(TODAY + " 17:") for s in stamps):
            continue
        # 真实轮结构：标题行与结论/EXIT 被等号行切成相邻块——窗口=本块+下一块
        window = seg + (chunks[idx + 1] if idx + 1 < len(chunks) else "")
        if "EXIT=0" in window and "结论:" in window and "PASS" in window:
            add("2) monday_checkpoint.log（17:25 自然轮）", "GREEN",
                "结论行=%s" % next(l.strip() for l in window.splitlines()
                                   if "结论:" in l))
        else:
            bad = [l.strip() for l in window.splitlines() if "[FAIL]" in l]
            add("2) monday_checkpoint.log（17:25 自然轮）", "RED",
                "存在自然轮但未全过 EXIT=0；FAIL 行=%s" % (bad[:3] or "见日志"))
        return
    add("2) monday_checkpoint.log（17:25 自然轮）",
        "PENDING" if _now().hour < 17 else "RED",
        "无今日 17: 自然轮块（今晨 04:11 护航巡检为手动轮，不作数）"
        if _now().hour < 17 else
        "已过 17:25 仍无自然轮块→SOP 异常矩阵")


def judge_chief():
    """3) chief_last_run.log：格式未定（首跑前无样本）→ mtime>=17:30 主判 +
    内容 SEND_OK/SEND_FAIL 辅判。"""
    p = os.path.join(SHADOW, "chief_last_run.log")
    if not os.path.exists(p):
        add("3) chief_last_run.log（ChiefPush 首跑）",
            "PENDING" if _now().hour < 17 else "RED",
            "文件不存在" + ("" if _now().hour < 17
                            else "（已过 17:35 仍缺失→SOP 异常矩阵）"))
        return
    mt = datetime.fromtimestamp(os.path.getmtime(p))
    txt = read(p)
    if mt.date().isoformat() != TODAY or mt.hour < 17:
        add("3) chief_last_run.log（ChiefPush 首跑）",
            "PENDING" if _now().hour < 17 else "RED",
            "mtime=%s 非今日 17:35 轮" % mt)
        return
    if "SEND_FAIL" in txt:
        add("3) chief_last_run.log（ChiefPush 首跑）", "RED",
            "mtime=%s 含 SEND_FAIL（飞书推送失败）" % mt)
        return
    add("3) chief_last_run.log（ChiefPush 首跑）", "GREEN",
        "mtime=%s%s" % (mt, "，含 SEND_OK" if "SEND_OK" in txt
                        else "（无 SEND_FAIL 标记，push 输出格式首跑样本待归档）"))


def judge_gate3():
    """4) 门3状态：tools/gate3_verdict.py 只读重算（退出码 0=绿 1=红）。"""
    r = subprocess.run(
        [sys.executable, "-X", "utf8",
         os.path.join(ROOT, "tools", "gate3_verdict.py")],
        capture_output=True, text=True, encoding="utf-8", timeout=300)
    tail = [l for l in (r.stdout or "").splitlines() if l.strip()][-3:]
    if r.returncode == 0:
        add("4) 门3状态（gate3_verdict）", "GREEN",
            " | ".join(tail)[:160])
    elif _now().hour < 17:
        # 首跑数据未产生时 gate3 按日历判「首跑已发生」实检必 FAIL
        # （与 checkpoint 的 PENDING 中性口径不同）——17:35 前属预期非红。
        add("4) 门3状态（gate3_verdict）", "PENDING",
            "exit=1 首跑未发生期实检不通过（预期形态），待首跑后重判")
    else:
        add("4) 门3状态（gate3_verdict）", "RED",
            "exit=%d | %s" % (r.returncode, " | ".join(tail)[:160]))


def main():
    print("===== 周一观察点复查 %s %s ====="
          % (TODAY, _now().strftime("%H:%M:%S")))
    judge_last_run()
    judge_checkpoint()
    judge_chief()
    judge_gate3()
    reds = [r for r in results if r[1] == "RED"]
    pend = [r for r in results if r[1] == "PENDING"]
    print("-" * 56)
    if pend and not reds:
        print("结论: 前置未到时点（17:35 前空跑）——%d 件待首跑，非异常" % len(pend))
        return 2
    if reds:
        print("结论: %d 件红 → 按既有 SOP 异常矩阵处置并通报，M4/M5 施工顺延"
              % len(reds))
        return 1
    print("结论: 三件首跑日志全过且门3状态绿 → 可开军团 M4 施工单"
          "（拍板口径 2026-10-18_军团M4M5规划稿.md 头部拍板段），M5 可并行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
