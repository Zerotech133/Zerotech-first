# -*- coding: utf-8 -*-
"""A0 总管日报聚合器（K-ORG-001 §4 日报通道落地，K-ORG-003）

纪律：影子期间决策链路冻结——本模块【只读】账本与 trace，不触碰任何决策组件。
输出：data/shadow/daily_report_YYYY-MM-DD.md（四段格式对齐 K-ORG-001 §4 日报契约）
严重度：failed → P0（立即推送：17:35 automation 带 --push / run_shadow 失败即时告警 K-STOCK-013）；degraded → P1；仅集中度观察 → P2 进日报。
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE / "engine"))
LEDGER = BASE / "data" / "shadow" / "shadow_ledger.jsonl"
DB = BASE / "data" / "checkpoints.db"
SHADOW_DAYS = 5
# 复用已验证推送链（K-PUSH）：OpenAPI 静态凭证直连+lark-cli 回退，10-08 实测双 SEND_OK
PUSH_SCRIPT = BASE.parent / "news_collector" / "send_feishu.py"
PYTHON = sys.executable


def push_to_feishu(md_path):
    """日报文件推飞书（text 通道）；返回 (ok, output)。P0 事件由调用方强制推送。"""
    try:
        r = subprocess.run(
            [PYTHON, str(PUSH_SCRIPT), str(md_path)],
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        out = (r.stdout or r.stderr or "").strip()
        ok = r.returncode == 0 and "SEND_OK" in out
        print("[A0] 飞书推送：%s" % out.splitlines()[-1] if out else "[A0] 飞书推送：无输出")
        return ok, out
    except Exception as e:  # noqa: BLE001
        print("[A0] 飞书推送异常：%s" % e)
        return False, str(e)


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
    except Exception as exc:  # noqa: BLE001
        # K-STOCK-393（20261015 告警补盲）：勿静默返假 0——DB 损坏/
        # 锁死时 0 与"真 0 轮"不可分，报告失真。stderr WARN 显形，
        # 返回形状不变（消费方元组解包兼容）。
        print("[WARN] debate_stats query failed: %s" % str(exc)[:120],
              file=sys.stderr)
        return 0, None


def debate_health(run_id):
    """单票辩论健康三指标（K-ORG-005 倒戈观测，Day0 基线报告定性）：T1/T2 final 立场 + 末轮相似度。"""
    import re
    import sqlite3
    out = {"t1": "?", "t2": "?", "sim_last": None}
    try:
        conn = sqlite3.connect(str(DB), check_same_thread=False)
        for agent, key in (("T1", "t1"), ("T2", "t2")):
            row = conn.execute(
                "SELECT output FROM traces WHERE run_id=? AND agent_id=? AND phase='debate_final'",
                (run_id, agent)).fetchone()
            if row:
                m = re.search(r"看多|看空", (row[0] or "")[:40])
                out[key] = m.group(0) if m else "?"
        row = conn.execute(
            "SELECT similarity FROM traces WHERE run_id=? AND phase='debate_rebuttal' "
            "AND similarity IS NOT NULL ORDER BY ts DESC LIMIT 1", (run_id,)).fetchone()
        if row:
            out["sim_last"] = row[0]
        conn.close()
    except Exception as exc:  # noqa: BLE001
        # K-STOCK-393：勿静默——显式 error 键（dict 加键对消费方安全）
        out["error"] = str(exc)[:80]
    return out


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
    if p0:
        lines.append("**[P0] %d 笔影子决策 failed（需立即处置）**：" % p0)
        for r in failed:
            lines.append("- %s：%s" % (r["symbol"], r.get("error", "?")[:120]))
    if p1:
        lines.append("**[P1] %d 笔链路降级**：%s" % (
            p1, "、".join(r["symbol"] for r in degraded)))
    if not p0 and not p1:
        lines.append("今日无异常（0×P0，0×P1）——链路健康，降级率 0%。")
    lines.append("")
    lines.append("## 三、观察项（A0 判读）")
    lines.append("")
    if rec_set == {"HOLD"} and len(recs) >= 3:
        lines.append("- **[P2] 决策集中度**：全篮子 HOLD——D1 保守度待影子结束后校准（影子期间冻结不改），"
                     "秩相关判据将依赖 confidence 备用排序。")
    if done:
        rounds_list, sims = [], []
        defected, sim_warn = [], []
        for r in done:
            n, s = debate_stats(r.get("run_id", ""))
            rounds_list.append(n)
            if s is not None:
                sims.append(s)
            h = debate_health(r.get("run_id", ""))
            if h["t1"] == "看空":
                defected.append("%s(T1转空)" % r["symbol"])
            if h["t2"] == "看多":
                defected.append("%s(T2转多)" % r["symbol"])
            if h["sim_last"] is not None and h["sim_last"] >= 0.7:
                sim_warn.append("%s(%.2f)" % (r["symbol"], h["sim_last"]))
        if rounds_list:
            lines.append("- 辩论轮次（rebuttal 轮数/票）：%s；平均末轮相似度 %s" % (
                "、".join(str(n // 2) for n in rounds_list),
                ("%.2f" % (sum(sims) / len(sims))) if sims else "无"))
        if defected:
            lines.append("- **[P2] 立场漂移（倒戈）**：%s——角色放弃本方立场，辩论质量受损"
                         "（Day0 基线已定性该模式，prompt 校准影子期满后执行）。" % "、".join(defected))
        if sim_warn:
            lines.append("- **[P2] 相似度警戒**：%s——末轮 sim≥0.7 逼近 0.8 收敛阈值，"
                         "存在立场塌缩被误判为共识的风险。" % "、".join(sim_warn))
    lines.append("")
    lines.append("## 四、明日计划")
    lines.append("")
    remain = SHADOW_DAYS - day_n
    if remain > 0:
        lines.append("- 影子继续：明日 17:10 自动运行（剩 %d 个交易日），决策链路冻结不改；" % remain)
    else:
        lines.append("- 影子期满：运行 eval_shadow.py 出三判据结论（J1 无人干预 / J2 秩相关 / J3 lineage）。")
    lines.append("- A0 日报通道：飞书已通（17:35 automation 定时 --push；P0 强制即时推）；A0 点评骨架 a0_agent.py 已立（automation 接入待拍板）。")
    lines.append("")
    lines.append("## 五、任务单看板（未闭环）")
    lines.append("")
    try:
        from tasks import TaskStore  # noqa: E402  engine/tasks.py（K-ORG-011）
        open_tasks = [t for t in TaskStore(BASE / "data" / "org" / "tasks").ls()
                      if t["status"] not in ("done", "cancelled", "rejected")]
        if open_tasks:
            for t in open_tasks:
                lines.append("- [%s] `%s` %s（→%s · %s）" % (
                    t.get("severity", "?"), t["task_id"], t.get("title", "")[:42],
                    t.get("assigned_dept", "?"), t["status"]))
        else:
            lines.append("无未闭环任务单。")
    except Exception as e:  # noqa: BLE001
        lines.append("- [warn] 任务单读取失败：%s" % e)
    lines.append("")
    lines.append("---")
    lines.append("*A0 总管聚合 · 数据源 shadow_ledger.jsonl + checkpoints.db（只读）· %s*" % datetime.now().strftime("%H:%M"))
    return "\n".join(lines), p0


def main():
    push = "--push" in sys.argv
    global LEDGER
    if "--ledger" in sys.argv:  # P0 演练通道：注入临时假账本，不碰真账本
        LEDGER = Path(sys.argv[sys.argv.index("--ledger") + 1])
        print("[A0] 演练模式：账本=%s" % LEDGER)
    today = datetime.now().strftime("%Y-%m-%d")
    report, p0 = build(today)
    out = BASE / "data" / "shadow" / ("daily_report_%s.md" % today)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print("[A0] 日报已生成：%s%s" % (out, "  [P0!]" if p0 else ""))
    # 推送策略：--push 显式推送；P0 存在时无论开关强制推（K-ORG-001 事件通道语义）
    if push or p0:
        ok, _ = push_to_feishu(out)
        if p0 and not ok:
            print("[A0] 警告：P0 事件但推送失败，需人工介入检查飞书链路")
    print(report)


if __name__ == "__main__":
    main()
