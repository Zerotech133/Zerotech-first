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
import logging

BASE = Path(__file__).parent
SHADOW = BASE / "data" / "shadow"
LEDGER = SHADOW / "shadow_ledger.jsonl"
LOG = SHADOW / "last_run.log"
ALERT = SHADOW / "p0_alert_state.json"
PUSH_SCRIPT = BASE.parent / "news_collector" / "send_feishu.py"

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
    ap.add_argument(
        "--date",
        default=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
        help="核对目标日（默认昨天）",
    )
    ap.add_argument(
        "--push", action="store_true", help="简报推飞书（晨检 automation 用）"
    )
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
            except json.JSONDecodeError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/morning_check.py", exc)
    done = [r for r in rows if r.get("status") == "done"]
    failed = [r for r in rows if r.get("status") == "failed"]
    deg = [r for r in done if r.get("degraded")]
    print(
        "\n[1] 影子账本（%s）：%d done / %d failed / %d 降级"
        % (d, len(done), len(failed), len(deg))
    )
    for r in done:
        print(
            "    %s %s %s conf=%s run=%s"
            % (
                r["symbol"],
                r.get("recommendation", "?"),
                r.get("name", ""),
                r.get("confidence"),
                r.get("run_id", ""),
            )
        )
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
            print(
                "    最近推送：date=%s pushed=%s" % (st.get("date"), st.get("pushed"))
            )
        except json.JSONDecodeError:
            print("    （状态文件损坏，需人工检查）")
    else:
        print("    （无推送记录——影子期内属正常）")

    # 3.5 任务单看板统计（K-ORG-014）
    print("\n[3.5] 任务单：")
    try:
        sys.path.insert(0, str(BASE / "engine"))
        from tasks import TaskStore  # noqa: E402

        all_t = TaskStore(BASE / "data" / "org" / "tasks").ls()
        by_status = {}
        for t in all_t:
            by_status[t["status"]] = by_status.get(t["status"], 0) + 1
        print(
            "    合计 %d 单：%s"
            % (
                len(all_t),
                " / ".join("%s=%d" % (k, v) for k, v in sorted(by_status.items()))
                or "空",
            )
        )
        for t in all_t:
            if t["status"] == "blocked":
                print(
                    "    **[blocked]** %s %s：%s"
                    % (
                        t["task_id"],
                        t["title"][:30],
                        (t.get("closure_note") or "")[:60],
                    )
                )
    except Exception as e:  # noqa: BLE001
        print("    [warn] 任务单读取失败：%s" % e)

    # 4. 日报/点评文件
    print("\n[4] 产出文件：")
    for f, label in (
        (SHADOW / ("daily_report_%s.md" % d), "日报"),
        (SHADOW / ("a0_review_%s.md" % d), "A0 点评"),
    ):
        print(
            "    %s：%s"
            % (
                label,
                "存在（%s）"
                % datetime.fromtimestamp(f.stat().st_mtime).strftime("%H:%M")
                if f.exists()
                else "缺失",
            )
        )

    # 4.5 automation 产物 mtime 断言（K-STOCK-088 P0-3：automation「ACTIVE」≠「当日触发」，
    #     09:10 cowbridge 曾漏跑靠 V6-5 产物时间戳实锤——此处机器化，漏跑当日即知）
    print("\n[4.5] automation 产物断言（mtime 须≥目标日）：")
    stale = []
    for path, label in (
        (
            BASE.parent
            / "Hermes"
            / "cowbridge"
            / "shadow"
            / "to-cowagent"
            / ("daily-%s.sample.md" % d),
            "09:10 cowbridge 交换",
        ),
        (SHADOW / ("daily_report_%s.md" % d), "17:10 影子日报"),
        (BASE.parent / "quant_backtest" / "reports" / "rps_latest.json", "RPS 日更"),
    ):
        if not path.exists():
            print("    ❌ %s：%s 缺失" % (label, path.name))
            stale.append(label)
        elif datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d") < d:
            print(
                "    ❌ %s：停更于 %s（疑似漏跑）"
                % (
                    label,
                    datetime.fromtimestamp(path.stat().st_mtime).strftime(
                        "%m-%d %H:%M"
                    ),
                )
            )
            stale.append(label)
        else:
            print(
                "    ✅ %s（%s）"
                % (
                    label,
                    datetime.fromtimestamp(path.stat().st_mtime).strftime("%H:%M"),
                )
            )
    if stale:
        print("    **[警示]** %d 项产物断言未过：%s" % (len(stale), "、".join(stale)))

    # 4.6 lark-cli 解耦副本存在性（K-STOCK-396 A1：bin/ 固定副本不受 Trae
    #     插件目录升级/清除影响；缺失则 B 线回退插件扫描，插件目录一消失即断链）
    print("\n[4.6] lark-cli 解耦副本（bin/lark-cli.exe）：")
    _cli = BASE.parent / "bin" / "lark-cli.exe"
    if _cli.exists():
        print(
            "    ✅ 存在（%.1f MB，晨检口径只验存在性）"
            % (_cli.stat().st_size / 1048576)
        )
    else:
        print(
            "    ❌ 缺失——B 线回退插件目录，产线断链风险（10-13 巡检判例），"
            "从插件 bin 拷贝回 bin/ 即恢复"
        )
        stale.append("lark-cli 解耦副本缺失")

    # 5. 三判据快照（复用 eval_shadow CLI，联网取现价；审计修正 K-STOCK-021：
    #    eval 超时/异常不得崩掉整个晨检——否则 --push 简报也发不出）
    print("\n[5] 三判据快照：")
    r = None
    try:
        r = subprocess.run(
            [sys.executable, str(BASE / "eval_shadow.py"), "--min-days", "1"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        for ln in (r.stdout or "").splitlines():
            print("    " + ln)
        if r.returncode != 0:
            print(
                "    [warn] eval_shadow 非零退出 %s：%s"
                % (r.returncode, (r.stderr or "")[:120])
            )
    except Exception as e:  # noqa: BLE001
        print("    [warn] 三判据快照失败（不影响后续项）：%s" % e)

    # --push：压缩简报推飞书（影子期晨检 automation，K-STOCK-016）
    if args.push:
        eval_out = (r.stdout or "") if r is not None else ""
        j_lines = [
            ln.strip()
            for ln in eval_out.splitlines()
            if ln.strip().startswith(("[J1", "[J2", "[J3", "影子评估结论"))
        ]
        brief = [
            "**影子晨检简报 %s**" % d,
            "账本：%d done / %d failed / %d 降级" % (len(done), len(failed), len(deg)),
        ]
        for rr in done:
            brief.append(
                "- %s %s conf=%s"
                % (
                    rr["symbol"],
                    rr.get("recommendation", "?"),
                    rr.get("confidence", "?"),
                )
            )
        for rr in failed:
            brief.append(
                "- **[FAILED]** %s：%s" % (rr["symbol"], rr.get("error", "?")[:80])
            )
        if not rows:
            brief.append("- **[警示]** 该日无入账行（若为交易日需排查影子 automation）")
        if stale:
            brief.append(
                "- **[产物断言未过]** %s（漏跑当日补跑，勿拖到验收日）"
                % "、".join(stale)
            )
        brief += ["- " + x for x in j_lines]
        try:
            tmp = SHADOW / "_morning_brief_tmp.md"
            tmp.write_text("\n".join(brief), encoding="utf-8")
            pr = subprocess.run(
                [sys.executable, str(PUSH_SCRIPT), str(tmp)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
            out = (pr.stdout or pr.stderr or "").strip()
            print("\n[push] 晨检简报：%s" % (out.splitlines()[-1] if out else "无输出"))
            tmp.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            print("\n[push] 推送异常：%s" % e)

    print("\n[6] 今日 automation 预期时刻表：")
    for t, name in EXPECTED:
        print("    %s  %s" % (t, name))
    print("=" * 64)


if __name__ == "__main__":
    main()
