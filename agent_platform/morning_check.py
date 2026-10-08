# -*- coding: utf-8 -*-
"""影子期晨检一键脚本（K-STOCK-016）：每天早上一条命令核对隔夜自动化产出

用法：
    python morning_check.py                 # 核对昨天（交易日）
    python morning_check.py --date 2026-10-09

核对项：
  1. 影子账本：目标日新增行（done/failed/降级分布）
  2. last_run.log：影子 automation 执行痕迹（尾部）
  3. failed 即时告警状态（p0_alert_state.json）
  4. 日报/A0 点评文件存在性
  5. 三判据快照（子进程复用 eval_shadow.py）
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent
SHADOW = BASE / "data" / "shadow"
LEDGER = SHADOW / "shadow_ledger.jsonl"
LOG = SHADOW / "last_run.log"
ALERT = SHADOW / "p0_alert_state.json"

EXPECTED = [
    ("07:05", "k_stock PoC 每日无人干预跑（本地团队线，守卫在岗）"),
    ("07:50", "pytest 临时产物清理"),
    ("08:30", "B4 备份 v9 首跑验收（10-09 once）+ A6 正式 Go 判定"),
    ("08:40", "每日融合巡检（含计划锚点 diff）"),
    ("17:10", "投研部影子运行（Day1 起五票真推理）"),
    ("17:35", "A0 投研日报（--push 推飞书）"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
                    help="核对目标日（默认昨天）")
    args = ap.parse_args()
    d = args.date

    print("=" * 64)
    print("影子晨检 %s（生成于 %s）" % (d, datetime.now().strftime("%H:%M")))
    print("=" * 64)

    # 1. 账本目标日统计
    rows = []
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                if r.get("date") == d:
                    rows.append(r)
            except json.JSONDecodeError:
                pass
    done = [r for r in rows if r.get("status") == "done"]
    failed = [r for r in rows if r.get("status") == "failed"]
    deg = [r for r in done if r.get("degraded")]
    print("\n[1] 影子账本（%s）：%d done / %d failed / %d 降级" % (d, len(done), len(failed), len(deg)))
    for r in done:
        print("    %s %s %s conf=%s run=%s" % (
            r["symbol"], r.get("recommendation", "?"), r.get("name", ""),
            r.get("confidence"), r.get("run_id", "")))
    for r in failed:
        print("    [FAILED] %s：%s" % (r["symbol"], r.get("error", "?")[:90]))
    if not rows:
        print("    （该日无入账行——若为交易日且已过 17:10，需排查影子 automation）")

    # 2. last_run.log 尾部
    print("\n[2] last_run.log 尾部：")
    if LOG.exists():
        tail = LOG.read_text(encoding="utf-8").splitlines()[-8:]
        for ln in tail:
            print("    " + ln)
    else:
        print("    （无日志文件）")

    # 3. failed 告警状态
    print("\n[3] failed 即时告警状态：")
    if ALERT.exists():
        try:
            st = json.loads(ALERT.read_text(encoding="utf-8"))
            print("    最近推送：date=%s pushed=%s" % (st.get("date"), st.get("pushed")))
        except json.JSONDecodeError:
            print("    （状态文件损坏，需人工检查）")
    else:
        print("    （无推送记录——影子期内属正常）")

    # 4. 日报/点评文件
    print("\n[4] 产出文件：")
    for f, label in ((SHADOW / ("daily_report_%s.md" % d), "日报"),
                     (SHADOW / ("a0_review_%s.md" % d), "A0 点评")):
        print("    %s：%s" % (label, "存在（%s）" % datetime.fromtimestamp(
            f.stat().st_mtime).strftime("%H:%M") if f.exists() else "缺失"))

    # 5. 三判据快照（复用 eval_shadow CLI，联网取现价）
    print("\n[5] 三判据快照：")
    r = subprocess.run(
        [sys.executable, str(BASE / "eval_shadow.py"), "--min-days", "1"],
        capture_output=True, text=True, encoding="utf-8", timeout=120)
    for ln in (r.stdout or "").splitlines():
        print("    " + ln)

    print("\n[6] 今日 automation 预期时刻表：")
    for t, name in EXPECTED:
        print("    %s  %s" % (t, name))
    print("=" * 64)


if __name__ == "__main__":
    main()
