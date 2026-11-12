# -*- coding: utf-8 -*-
"""把后峰盘前席位装到 Hermes profile。**默认只预演，不写入。**

为什么单独一个脚本而不是手工 cp
--------------------------------
装席位有三件事容易漏，漏了都要返工：
  1. **名号撞车** —— 体系里名号是锁定资源（典韦=VALIDATOR 已登记，禁止他处复用）。
     手工建 profile 时没人会去扫 11 个 SOUL 的第一行。
  2. **记忆种子的口径** —— MEMORY.md 一旦写错，之后每轮都会带着错的前提跑。
  3. **cron 与 Windows 计划任务双跑** —— 两套调度同时触发，同一天出两份报告。

所以本脚本把 1、2 做成硬检查，3 做成显式警告（不自动注册 cron）。

预演：
    python tools/scheduled/install_seat_houfeng.py
    python tools/scheduled/install_seat_houfeng.py --name 糜竺        # 指定名号后预演查重
    python tools/scheduled/install_seat_houfeng.py --name 糜竺 --apply # 真写入

不改任何 ~/.hermes 之外的东西；不注册计划任务；不碰已有 profile。
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import shutil
import sys
from datetime import datetime

ROOT = r"C:\项目\老田的AI助手"
SEAT_SRC = os.path.join(ROOT, "agent_platform", "seats", "houfeng-preopen")
HERMES = os.path.join(os.path.expanduser("~"), ".hermes")
PROFILES = os.path.join(HERMES, "profiles")
SEAT_ID = "houfeng-preopen"


def existing_seats() -> dict[str, str]:
    """{名号: profile 名}。优先 SEAT_CARD.cn_name（权威字段），否则从 SOUL 首行取。

    首行取法必须用「第一个中文串」，不能按分隔符切：
    `# SOUL — 张松（数据智能部 · 兰台令）` 里有两个分隔符候选，
    按后者切会得到「兰台令」，撞车检测就整个失效了。
    """
    out: dict[str, str] = {}
    for d in sorted(glob.glob(os.path.join(PROFILES, "*"))):
        if not os.path.isdir(d):
            continue
        name = os.path.basename(d)
        cn = ""

        card = os.path.join(d, "SEAT_CARD.json")
        if os.path.exists(card):
            try:
                c = (json.load(io.open(card, encoding="utf-8")).get("cn_name") or "")
                if c and "待定" not in c and "】" not in c:
                    cn = re.split(r"[（(]", c)[0].strip()
            except Exception:
                pass

        if not cn:
            soul = os.path.join(d, "SOUL.md")
            if os.path.exists(soul):
                first = io.open(soul, encoding="utf-8", errors="replace").readline()
                m = re.search(r"[\u4e00-\u9fa5]{2,5}", first)
                if m:
                    cn = m.group(0)
        if cn:
            out[cn] = name
    return out


def name_conflict(name: str) -> str | None:
    if not name or "待定" in name:
        return None
    seats = existing_seats()
    for cn, prof in seats.items():
        if not cn:
            continue
        # 双向包含即视为撞车：既防「糜竺」撞「糜竺」，也防「糜竺」撞「糜竺·粮官」
        if cn == name or cn in name or name in cn:
            return prof
    return None


MEMORY_TEMPLATE = """# {cn} · 盘前守门席 长期记忆（MEMORY）

## 角色
后峰量化平台盘前质检位（HOUFENG_PREOPEN）。点粮 / 出简报 / 记账。
不选股、不交易、不改配方、不改阈值。放过去的坏数据下游不会二次校验，所以我是最后一关。

## 我的入口
- 唯一入口：`python tools/preopen/run_preopen.py`（校对→选票→报告→记账）
- 记账：`event_log.py --from-run`；事后纠错：`event_log.py --amend <日> --note "..."`
- 巡检：`detect_drift.py`（关系级）；提建议：`evolve.py --propose` / `--set <PID> <status>`

## 三种裁定（永不合并输出）
pass 出报告 / warn 照出但写进报告 / block 不出报告并告警。
另须区分：上游给空 ≠ 采到无新增 ≠ 采集漏跑。

## 进化纪律（五闸）
观察 → 聚合（event≥3 / state≥5）→ 立假设 → precheck → 老田签字。
缺一不采信、不进台账。只往时点/闸门方向提，参数方向拒提（六次实验全否）。

## 席位关系
张松（兰台令·采集）供数；赵云（进化官）收我的候选做对照实验；
老田 approval_gate 签字；我不写赵云 experience/、不替他签字。

## 名号锁定
{cn} = HOUFENG_PREOPEN（后峰用量化分册）。已用名号：魏延/庞德/典韦/张松/黄忠/田丰/马谡/阚泽/赵云。
新增名号先查主库防撞。

## 知识源
- `docs/2026-11-12_后峰盘前Agent职责与日报SOP_v1.md`（做什么）
- `docs/2026-11-12_后峰盘前Agent_自进化与Hermes底座_v2.md`（怎么变强）
- `docs/2026-11-11_调参实验预判规范_v1.md`（提建议前必读）
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="装后峰盘前席位到 Hermes profile（默认预演）")
    ap.add_argument("--name", default=None, help="中文名号；不填则沿用席位卡里的占位")
    ap.add_argument("--apply", action="store_true", help="真正写入（不加则只预演）")
    a = ap.parse_args()

    if not os.path.isdir(SEAT_SRC):
        raise SystemExit("找不到席位源目录：%s" % SEAT_SRC)

    card_p = os.path.join(SEAT_SRC, "SEAT_CARD.json")
    soul_p = os.path.join(SEAT_SRC, "SOUL.md")
    if not (os.path.exists(card_p) and os.path.exists(soul_p)):
        raise SystemExit("席位源缺文件（需 SOUL.md + SEAT_CARD.json）")

    card = json.load(io.open(card_p, encoding="utf-8"))
    name = a.name or (card.get("cn_name") or "")
    if not name or "待定" in name:
        print("× 名号未定。体系里名号是锁定资源，不能先用占位建 profile。")
        print("  先定名（推荐：糜竺），再跑： --name 糜竺")
        return 2

    conflict = name_conflict(name)
    if conflict:
        print("× 名号撞车：「%s」已被 profile `%s` 使用。" % (name, conflict))
        print("  名号一席一号，禁止复用。换一个再试。")
        return 2

    dest = os.path.join(PROFILES, SEAT_ID)
    print("=" * 60)
    print("席位安装 %s（%s）" % (SEAT_ID, name))
    print("=" * 60)
    print("源      %s" % SEAT_SRC)
    print("目标    %s" % dest)
    print("名号查重 通过（现有 %d 席，无「%s」）" % (len(existing_seats()), name))

    if os.path.exists(dest):
        print("\n! 目标 profile 已存在，本脚本不覆盖已存在的 profile。")
        print("  如需重建，先自行备份并移除 %s" % dest)
        return 2

    plan = [
        ("SOUL.md", soul_p, os.path.join(dest, "SOUL.md")),
        ("SEAT_CARD.json", card_p, os.path.join(dest, "SEAT_CARD.json")),
        ("memories/MEMORY.md", None, os.path.join(dest, "memories", "MEMORY.md")),
    ]
    print("\n将要写入：")
    for label, _, to in plan:
        print("  %-20s → %s" % (label, to))

    print("\n注意（不自动做，需你另行决定）：")
    print("  · 不注册 cron。若已注册 Windows 计划任务 preopen_daily，")
    print("    再给这个 profile 加 cron 会导致同一天跑两遍、出两份报告。二选一。")
    print("  · 不填 config.yaml 的 model —— 用哪个模型是你的选择，不由脚本代定。")

    if not a.apply:
        print("\n【预演】未写入任何文件。加 --apply 才执行。")
        return 0

    os.makedirs(os.path.join(dest, "memories"), exist_ok=True)
    shutil.copy2(soul_p, os.path.join(dest, "SOUL.md"))

    card2 = dict(card)
    card2["cn_name"] = name
    card2["cn_name_note"] = "名号已由老田确认（%s），已过查重。" % datetime.now().strftime("%Y-%m-%d")
    card2["generated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    io.open(os.path.join(dest, "SEAT_CARD.json"), "w", encoding="utf-8").write(
        json.dumps(card2, ensure_ascii=False, indent=1))

    io.open(os.path.join(dest, "memories", "MEMORY.md"), "w",
            encoding="utf-8", newline="\n").write(MEMORY_TEMPLATE.format(cn=name))

    print("\n已写入。校验：")
    for label, _, to in plan:
        print("  %-20s %s" % (label, "OK" if os.path.exists(to) else "缺失"))
    print("\n下一步：在 profile 里设模型（hermes model），再决定用 cron 还是计划任务。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
