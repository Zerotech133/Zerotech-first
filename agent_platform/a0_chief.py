# -*- coding: utf-8 -*-
"""A0 总管日报聚合器（K-ORG-001 §4 日报通道落地，K-ORG-003）

纪律：影子期间决策链路冻结——本模块【只读】账本与 trace，不触碰任何决策组件。
输出：data/shadow/daily_report_YYYY-MM-DD.md（四段格式对齐 K-ORG-001 §4 日报契约）
严重度：failed → P0（需立即推送，通道待接）；degraded → P1；仅集中度观察 → P2 进日报。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
LEDGER = BASE / "data" / "shadow" / "shadow_ledger.jsonl"
DB = BASE / "data" / "checkpoints.db"
SHADOW_DAYS = 5


def load_today(today):
    if not LEDGER.exists():
        return [], []
    done, failed = [], []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("date") == today:
            (done if r.get("status") == "done" else failed).append(r)
    return done, failed


def shadow_progress():
    if not LEDGER.exists():
        return 0
    days = set()
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("status") == "done":
            days.add(r["date"])
    return len(days)


def debate_stats(run_id):
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB), check_same_thread=False)
        cur = conn.execute(
            "SELECT COUNT(*), AVG(similarity) FROM traces WHERE run_id=? AND phase='debate_rebuttal'",
            (run_id,))
        n, avg_sim = cur.fetchone()
        conn.close()
        return n or 0, avg_sim
    except Exception:  # noqa: BLE001
        return 0, None


def build(today):
    done, failed = load_today(today)
    day_n = shadow_progress()
    degraded = [r for r in done if r.get("degraded")]
    p0 = len(failed)
    p1 = len(degraded)

    recs = [r.get("recommendation") for r in done]
    rec_set = set(recs)
    confs = [r.get("confidence") or 0 for r in done]
    lines = []
    lines.append("# 投研部日报 %s（影子第 %d/%d 交易日）" % (today, day_n, SHADOW_DAYS))
    lines.append("")
    lines.append("## 一、今日产出")
    lines.append("")
    lines.append("| 票 | 决策 | 置信度 | 决策价 | 链路状态 | run_id |")
    lines.append("|---|---|---|---|---|---|")
    for r in done:
        st = "降级" if r.get("degraded") else "正常"
        lines.append("| %s %s | %s | %s | %s | %s | `%s` |" % (
            r["symbol"], r.get("name", ""), r.get("recommendation", "?"),
            r.get("confidence", "?"), r.get("price_at_decision", "?"), st, r.get("run_id", "")))
    if not done:
        lines.append("| - | 今日无完成决策 | - | - | - | - |")
    lines.append("")
    lines.append("决策分布：%s；平均置信度 %.2f" % (
        "、".join("%s×%d" % (k, recs.count(k)) for k in set(recs)) or "无",
        sum(confs) / len(confs) if confs else 0))
    lines.append("")
    lines.append("## 二、异常与风险")
    lines.append("")
    alerts = []
    if p0:
        lines.append("**[P0] %d 笔影子决策 failed（需立即处置）**：" % p0)
        for r in failed:
            lines.append("- %s：%s" % (r["symbol"], r.get("error", "?")[:120]))
    if p1:
        lines.append("**[P1] %d 笔链路降级**：%s" % (
            p1, "、".join(r["symbol"] for r in degraded)))
    if not alerts and not p0 and not p1:
        lines.append("今日无异常（0×P0，0×P1）——链路健康，降级率 0%。")
    lines.append("")
    lines.append("## 三、观察项（A0 判读）")
    lines.append("")
    if rec_set == {"HOLD"} and len(recs) >= 3:
        lines.append("- **[P2] 决策集中度**：全篮子 HOLD——D1 保守度待影子结束后校准（影子期间冻结不改），"
                     "秩相关判据将依赖 confidence 备用排序。")
    if done:
        rounds_list, sims = [], []
        for r in done:
            n, s = debate_stats(r.get("run_id", ""))
            rounds_list.append(n)
            if s is not None:
                sims.append(s)
        if rounds_list:
            lines.append("- 辩论轮次（rebuttal 轮数/票）：%s；平均末轮相似度 %s" % (
                "、".join(str(n // 2) for n in rounds_list),
                ("%.2f" % (sum(sims) / len(sims))) if sims else "无"))
    lines.append("")
    lines.append("## 四、明日计划")
    lines.append("")
    remain = SHADOW_DAYS - day_n
    if remain > 0:
        lines.append("- 影子继续：明日 17:10 自动运行（剩 %d 个交易日），决策链路冻结不改；" % remain)
    else:
        lines.append("- 影子期满：运行 eval_shadow.py 出三判据结论（J1 无人干预 / J2 秩相关 / J3 lineage）。")
    lines.append("- A0 日报通道：本地文件已通，飞书/微信推送待老田拍板后接入 SubPush 链。")
    lines.append("")
    lines.append("---")
    lines.append("*A0 总管聚合 · 数据源 shadow_ledger.jsonl + checkpoints.db（只读）· %s*" % datetime.now().strftime("%H:%M"))
    return "\n".join(lines), p0


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    report, p0 = build(today)
    out = BASE / "data" / "shadow" / ("daily_report_%s.md" % today)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print("[A0] 日报已生成：%s%s" % (out, "  [P0!]" if p0 else ""))
    print(report)


if __name__ == "__main__":
    main()
