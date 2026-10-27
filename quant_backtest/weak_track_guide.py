# -*- coding: utf-8 -*-
"""§17 产线导航卡（weak 产线第 17 节 · K35-ad 新增）。

产线已 17 节，隔月回看自己也陌生——一卡列全各节功能+命令+任务节奏，
手机一翻即知全貌、知道「什么时候会发生什么、该做什么」。

  按节奏分组（比节号序更实用）：每日自动 / 每周自动 / 每月自动 /
  手动按需 / 你唯一要做的日常。

SECTIONS 为结构化配置（数据即配置，改文案改这里，不搞外置模板——
导航是结构化数据非文案流）。默认预览；--send 推送（--text 回退文本）。

用法:
  python weak_track_guide.py                # 预览
  python weak_track_guide.py --send         # 推送导航卡
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# (组名, [(节号, 名称, 一句话, 命令或任务, 操作性说明)])
GROUPS = [
    (
        "每日自动（无需操作）",
        [
            (
                "§0-§8",
                "跟踪产线",
                "立案-每日打点-复评判断-结案引导",
                "15:30 WeakTrackCheck / 15:35 WeakScan",
                "",
            ),
            (
                "§16",
                "守护",
                "任务群健康巡检（看守守夜人），全绿静默",
                "15:55 WeakDailyHealth",
                "",
            ),
        ],
    ),
    (
        "每周自动",
        [
            (
                "§15",
                "周报",
                "巡检+审计+本周动态+环比，全绿也推",
                "周一 15:50 WeakWeeklyReport",
                "看一眼趋势即可",
            ),
        ],
    ),
    (
        "每月自动",
        [
            (
                "§17",
                "导航",
                "产线全貌导航（本卡），月度链开场",
                "每月 1 日 08:20 WeakMonthlyGuide",
                "",
            ),
            (
                "§10",
                "汇总",
                "结案案例事实汇总（区间涨跌/超额/触发）",
                "每月 1 日 08:30 WeakMonthlySummary",
                "",
            ),
            (
                "§11",
                "学习",
                "规律提炼（兑现率/时长/触发关联），n<5 只积累",
                "每月 1 日 08:40 WeakMonthlyLearn",
                "",
            ),
            (
                "§14",
                "归档",
                "案例库全量快照进飞书文档（含在案节）",
                "每月 1 日 08:50 WeakMonthlyArchive",
                "需授权，未开通 rc=2 等待",
            ),
        ],
    ),
    (
        "手动按需",
        [
            (
                "§9",
                "结案",
                "单案收尾三选一（A 兑现/B 立案/C 实证）",
                "weak_track_close.py <code> <A|B|C>",
                "结案链起点，B 类自动建新案",
            ),
            (
                "§12",
                "巡检",
                "在案超龄/停滞/任务失联，全绿静默",
                "weak_track_watch.py --send",
                "",
            ),
            (
                "§13",
                "审计",
                "已结案产物五项对账，全绿静默",
                "weak_track_audit.py --send",
                "",
            ),
        ],
    ),
    (
        "你唯一要做的日常",
        [
            (
                "§8",
                "复评响应",
                "触发提醒卡到了 → 看 §5 情景推演 → §9 结案三选一或继续跟踪",
                "按提醒卡行动",
                "",
            ),
        ],
    ),
]


def build_guide():
    """分组结构 → 文本文案（结构化数据直渲染）。"""
    lines = ["🗺️ [WEAK GUIDE] weak 产线导航（§17）· 节奏 × 17 节全貌", ""]
    for gname, items in GROUPS:
        lines.append("【%s】" % gname)
        for sec, name, desc, cmd, note in items:
            lines.append("- %s %s：%s" % (sec, name, desc))
            lines.append("  命令/任务：%s%s" % (cmd, ("｜" + note) if note else ""))
        lines.append("")
    lines.append(
        "生成：%s（改文案改 weak_track_guide.py 的 GROUPS）"
        % time.strftime("%Y-%m-%d %H:%M")
    )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="§17 产线导航卡推送")
    ap.add_argument("--send", action="store_true", help="执行推送（默认仅预览）")
    ap.add_argument("--text", action="store_true", help="文本版推送（默认飞书卡片）")
    a = ap.parse_args()
    msg = build_guide()
    print(msg)
    if not a.send:
        print("\n（预览模式：加 --send 执行推送）")
        if not a.text:
            import weak_track_card as wcard

            print("\n（卡片版预览，--text 可回退文本）")
            print(json.dumps(wcard.guide_card(GROUPS), ensure_ascii=False, indent=1))
        return 0
    if not a.text:
        import weak_track_card as wcard

        print("\n推送(卡片)：" + wcard.send_card(wcard.guide_card(GROUPS)))
    else:
        NEWS = os.path.abspath(os.path.join(HERE, "..", "news_collector"))
        tmp = os.path.join(NEWS, "outbox", "pending_alert_weak_guide.txt")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fo:
            fo.write(msg)
        import subprocess

        r = subprocess.run(
            [sys.executable, os.path.join(NEWS, "send_feishu.py"), tmp],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        print("\n推送：" + ((r.stdout or "").strip()[:60] or "sent"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
