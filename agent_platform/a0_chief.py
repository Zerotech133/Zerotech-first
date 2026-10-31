# -*- coding: utf-8 -*-
"""A0 总管日报聚合器（K-ORG-001 §4 日报通道落地，K-ORG-003）

纪律：影子期间决策链路冻结——本模块【只读】账本与 trace，不触碰任何决策组件。
输出：data/shadow/daily_report_YYYY-MM-DD.md（四段格式对齐 K-ORG-001 §4 日报契约）
严重度：failed → P0（立即推送：17:35 automation 带 --push / run_shadow 失败即时告警 K-STOCK-013）；degraded → P1；仅集中度观察 → P2 进日报。

2026-10-31 WO-D1-B123-20261031（D1 证据链 P0 / B3）：账本行已由
run_shadow.build_ledger_row 落 decision_id / watch_reason / evidence_refs；
日报「今日产出」表透传展示这三列（旧行缺字段降级为 `-`/`0 条`，不崩、不猜）。
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
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        out = (r.stdout or r.stderr or "").strip()
        ok = r.returncode == 0 and "SEND_OK" in out
        print(
            "[A0] 飞书推送：%s" % out.splitlines()[-1]
            if out
            else "[A0] 飞书推送：无输出"
        )
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
            (run_id,),
        )
        n, avg_sim = cur.fetchone()
        conn.close()
        return n or 0, avg_sim
    except Exception as exc:  # noqa: BLE001
        # K-STOCK-393（20261015 告警补盲）：勿静默返假 0——DB 损坏/
        # 锁死时 0 与"真 0 轮"不可分，报告失真。stderr WARN 显形，
        # 返回形状不变（消费方元组解包兼容）。
        print("[WARN] debate_stats query failed: %s" % str(exc)[:120], file=sys.stderr)
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
                (run_id, agent),
            ).fetchone()
            if row:
                m = re.search(r"看多|看空", (row[0] or "")[:40])
                out[key] = m.group(0) if m else "?"
        row = conn.execute(
            "SELECT similarity FROM traces WHERE run_id=? AND phase='debate_rebuttal' "
            "AND similarity IS NOT NULL ORDER BY ts DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if row:
            out["sim_last"] = row[0]
        conn.close()
    except Exception as exc:  # noqa: BLE001
        # K-STOCK-393：勿静默——显式 error 键（dict 加键对消费方安全）
        out["error"] = str(exc)[:80]
    return out


def _evidence_refs_summary(refs, limit=4):
    """账本 evidence_refs → 日报单格短摘要（B3，只读、容错、不抛）。

    正常行（run_shadow.build_ledger_row）是 EvidenceRef.to_dict() 列表；
    旧行可能缺该字段/为字符串/为脏数据，一律降级为可读占位——日报是只读
    聚合器，不得因单行脏数据崩掉。
    """
    if isinstance(refs, str):
        text = refs.strip().replace("|", "/")
        return "1 条:%s" % text if text else "0 条"
    if not isinstance(refs, list) or not refs:
        return "0 条"
    sources = []
    for ref in refs:
        if isinstance(ref, dict):
            name = str(ref.get("source") or ref.get("uri") or "").strip()
        elif isinstance(ref, str):
            name = ref.strip()
        else:
            name = ""
        if name:
            sources.append(name.replace("|", "/"))
    if not sources:
        return "%d 条" % len(refs)
    shown = ",".join(sources[:limit])
    tail = "…" if len(sources) > limit else ""
    return "%d 条:%s%s" % (len(refs), shown, tail)


def build(today):
    done, failed = load_today(today)
    day_n = shadow_progress()
    # M5 刀 1：degraded 扩义＝数据源异常 ∪ 决策解析失败（run_shadow.build_ledger_row
    # 起 m5.v1 行生效；v0 旧行语义不变）——P1 计数自此覆盖 parse 失败行，不再失明
    degraded = [r for r in done if r.get("degraded")]
    p0 = len(failed)
    p1 = len(degraded)

    recs = [r.get("recommendation") for r in done]
    rec_set = set(recs)
    confs = [r.get("confidence") or 0 for r in done]
    lines = []
    if day_n <= SHADOW_DAYS:
        lines.append(
            "# 投研部日报 %s（影子第 %d/%d 交易日）" % (today, day_n, SHADOW_DAYS)
        )
    else:
        lines.append(
            "# 投研部日报 %s（影子续观察 第%d日·基线%d日已满）"
            % (today, day_n, SHADOW_DAYS)
        )
    lines.append("")
    lines.append("## 一、今日产出")
    lines.append("")
    lines.append("| 票 | 决策 | 置信度 | 决策价 | 链路状态 | 决策ID | WATCH原因 | 证据引用 | run_id |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in done:
        st = "降级" if r.get("degraded") else "正常"
        decision_id = str(r.get("decision_id") or "").strip().replace("|", "/")
        watch_reason = str(r.get("watch_reason") or "").strip().replace("|", "/")
        lines.append(
            "| %s %s | %s | %s | %s | %s | %s | %s | %s | `%s` |"
            % (
                r["symbol"],
                r.get("name", ""),
                r.get("recommendation", "?"),
                r.get("confidence", "?"),
                r.get("price_at_decision", "?"),
                st,
                "`%s`" % decision_id if decision_id else "-",
                watch_reason or "-",
                _evidence_refs_summary(r.get("evidence_refs")),
                r.get("run_id", ""),
            )
        )
    if not done:
        lines.append("| - | 今日无完成决策 | - | - | - | - | - | - | - |")
    lines.append("")
    lines.append(
        "决策分布：%s；平均置信度 %.2f"
        % (
            "、".join("%s×%d" % (k, recs.count(k)) for k in set(recs)) or "无",
            sum(confs) / len(confs) if confs else 0,
        )
    )
    lines.append("")
    lines.append("## 二、异常与风险")
    lines.append("")
    if p0:
        lines.append("**[P0] %d 笔影子决策 failed（需立即处置）**：" % p0)
        for r in failed:
            lines.append("- %s：%s" % (r["symbol"], r.get("error", "?")[:120]))
    if p1:
        lines.append(
            "**[P1] %d 笔链路降级**：%s"
            % (p1, "、".join(r["symbol"] for r in degraded))
        )
    if not p0 and not p1:
        lines.append("今日无异常（0×P0，0×P1）——链路健康，降级率 0%。")
    lines.append("")
    lines.append("## 三、观察项（A0 判读）")
    lines.append("")
    if rec_set == {"HOLD"} and len(recs) >= 3:
        lines.append(
            "- **[P2] 决策集中度**：全篮子 HOLD——D1 保守度待影子结束后校准（影子期间冻结不改），"
            "秩相关判据将依赖 confidence 备用排序。"
        )
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
            lines.append(
                "- 辩论轮次（rebuttal 轮数/票）：%s；平均末轮相似度 %s"
                % (
                    "、".join(str(n // 2) for n in rounds_list),
                    ("%.2f" % (sum(sims) / len(sims))) if sims else "无",
                )
            )
        if defected:
            lines.append(
                "- **[P2] 立场漂移（倒戈）**：%s——角色放弃本方立场，辩论质量受损"
                "（Day0 基线已定性该模式，prompt 校准影子期满后执行）。"
                % "、".join(defected)
            )
        if sim_warn:
            lines.append(
                "- **[P2] 相似度警戒**：%s——末轮 sim≥0.7 逼近 0.8 收敛阈值，"
                "存在立场塌缩被误判为共识的风险。" % "、".join(sim_warn)
            )
    lines.append("")
    lines.append("## 四、明日计划")
    lines.append("")
    remain = SHADOW_DAYS - day_n
    if remain > 0:
        lines.append(
            "- 影子继续：明日 17:10 自动运行（剩 %d 个交易日），决策链路冻结不改；"
            % remain
        )
    else:
        lines.append(
            "- 影子基线已满（%d 交易日）：三判据已 PASS（2026-10-17 eval_shadow："
            "J1 无人干预/J2 秩相关/J3 lineage），续影子攒 J2 显著性"
            "（K-ORG-129 连续化 score 新样本）；转产/收口拍板待老田。" % SHADOW_DAYS
        )
    lines.append(
        "- A0 日报通道：飞书已通（17:35 automation 定时 --push；P0 强制即时推）；A0 点评骨架 a0_agent.py 已立（automation 接入待拍板）。"
    )
    lines.append("")
    lines.append("## 五、任务单看板（未闭环）")
    lines.append("")
    try:
        from tasks import TaskStore  # noqa: E402

        open_tasks = [
            t
            for t in TaskStore(BASE / "data" / "org" / "tasks").ls()
            if t["status"] not in ("done", "cancelled", "rejected")
        ]
        if open_tasks:
            for t in open_tasks:
                lines.append(
                    "- [%s] `%s` %s（→%s · %s）"
                    % (
                        t.get("severity", "?"),
                        t["task_id"],
                        t.get("title", "")[:42],
                        t.get("assigned_dept", "?"),
                        t["status"],
                    )
                )
        else:
            lines.append("无未闭环任务单。")
    except Exception as e:  # noqa: BLE001
        lines.append("- [warn] 任务单读取失败：%s" % e)
    lines.append("")
    lines.append("---")
    lines.append(
        "*A0 总管聚合 · 数据源 shadow_ledger.jsonl + checkpoints.db（只读）· %s*"
        % datetime.now().strftime("%H:%M")
    )
    return "\n".join(lines), p0


def main():
    push = "--push" in sys.argv
    global LEDGER
    rehearsal = False
    if "--ledger" in sys.argv:  # P0 演练通道：注入临时假账本，不碰真账本
        # P2 清仓（2026-10-18）：--ledger 为末参数（无值）此前 IndexError
        # 裸 traceback——显式检查并打印用法退出非零
        idx = sys.argv.index("--ledger")
        if idx + 1 >= len(sys.argv):
            print(
                "用法：a0_chief.py [--push] [--ledger <path>]"
                "（--ledger 需要账本路径参数）",
                file=sys.stderr,
            )
            return 2
        LEDGER = Path(sys.argv[idx + 1])
        rehearsal = True
        print("[A0] 演练模式：账本=%s" % LEDGER)
    today = datetime.now().strftime("%Y-%m-%d")
    report, p0 = build(today)
    # 演练隔离必须完整：账本不碰真仓只是半个隔离——日报文件若仍写真名，
    # 演练内容会覆盖当日真日报（17:35 生产日报被假数据顶掉）；且演练账本
    # 里的 failed 会触发 p0>0 → 强制真飞书推送假 P0 告警（外部副作用不可撤回）。
    # 判例同源：opt-out 非 fail-safe，演练侧默认拒绝外部副作用。
    suffix = "_rehearsal" if rehearsal else ""
    out = BASE / "data" / "shadow" / ("daily_report_%s%s.md" % (today, suffix))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print("[A0] 日报已生成：%s%s" % (out, "  [P0!]" if p0 else ""))
    # 推送策略：--push 显式推送；P0 存在时无论开关强制推（K-ORG-001 事件通道语义）；
    # 演练模式一律不推（真飞书是外部副作用，演练数据无推送资格）
    # P2 清仓（2026-10-18）：main 恒返回 None→exit 0，P0 推送失败计划任务
    # 无法暴露——P0 且推送失败 return 1（其余路径维持 0）
    exit_code = 0
    if rehearsal:
        print("[A0] 演练模式：跳过飞书推送（含 P0 强推语义，外部副作用禁发）")
    elif push or p0:
        ok, _ = push_to_feishu(out)
        if p0 and not ok:
            print("[A0] 警告：P0 事件但推送失败，需人工介入检查飞书链路")
            exit_code = 1
    print(report)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
