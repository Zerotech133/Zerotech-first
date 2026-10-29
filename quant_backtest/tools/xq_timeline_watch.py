#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""雪球生产链时间线守望（只读证据收集器，2026-10-18）。

职责：19:35 一次性计划任务 XueqiuTimelineWatch 调用，对当晚生产链五项
产物做只读证据收集与三态判定（PASS/FAIL/UNKNOWN），落时间线报告：

  C:/aios/automation_runs/xq_report/timeline_YYYY-MM-DD.md   人读
  C:/aios/automation_runs/xq_report/timeline_YYYY-MM-DD.json 机读

红线：全程只读（SQLite mode=ro + busy_timeout；跨仓产物仅读文件），
绝不写/删/改任何生产数据文件；本脚本只写自己的 timeline_* 报告。
全绿不写任何告警文件；有 FAIL 仅在报告头部醒目列出（退出码 1）。

五项证据口径（路径均为读码确认，出处见 docs/2026-10-18_时间线守望注册.md）：
  1 crawl     今日爬虫产物：xq_status 当日 fetched/created 行数 + 库 mtime
              + logs/xueqiu_crawl_status.log 今日 exit 码
  2 report    今日日报 JSON：daily/xq_intel_YYYYMMDD.json 存在性 + 15 键
              齐备性（复用 tools/verify_daily_report.py 校验逻辑，非复制）
  3 alert     verify_alert.log 今日状态：不存在=UNKNOWN 偏 PASS；
              mtime<当日 19:00=UNKNOWN（历史残留，防假红）；
              含 FAIL 行=FAIL 并摘录（alert 为覆盖式落盘，带时间戳）
  4 shadow    19:10 日批产物（RiskGuardShadowDaily R1/R2 幂等批+R4 持仓批；
              R3 已归周六 06:40 周批 RiskGuardR3Weekly，此处措辞 2026-10-18
              ring-identity 考证勘误）：C:/aios/zerobound-kg/riskguard_ledger/
              ledger.jsonl 当日新行/新码计数（跨仓只读，拿不到=UNKNOWN）
  5 classify  分类追平轮进度：xq_status_classified 总数与今日 labeled_at 增量
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime
import logging

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 出处：xq_daily_report.py:29 DB_RO（同一库，此处独立只读连接）
XQ_DB = os.path.join(REPO, "data", "xueqiu", "xueqiu_info.db")
# 出处：xq_daily_report.py:30 OUT_ROOT
OUT_ROOT = r"C:/aios/automation_runs/xq_report"
# 出处：start_xueqiu_crawl.ps1:8 $log
CRAWL_STATUS_LOG = os.path.join(REPO, "logs", "xueqiu_crawl_status.log")
# 出处：tools/verify_daily_report.py:35-37
LATEST_JSON = r"C:/aios/automation_runs/xq_report/latest.json"
VERIFY_ALERT_LOG = r"C:/aios/automation_runs/xq_report/verify_alert.log"
# 出处：zerobound-kg/riskguard_shadow_daily.ps1:10 $here + :28 $led
SHADOW_LEDGER = r"C:/aios/zerobound-kg/riskguard_ledger/ledger.jsonl"
SHADOW_LOG = r"C:/aios/zerobound-kg/riskguard_ledger/shadow_daily.log"

STATUS_PASS, STATUS_FAIL, STATUS_UNKNOWN = "PASS", "FAIL", "UNKNOWN"


def _item(iid, title, status, detail, evidence, extra=None):
    d = {
        "id": iid,
        "title": title,
        "status": status,
        "detail": detail,
        "evidence": evidence,
    }
    if extra:
        d["extra"] = extra
    return d


def collect_crawl(day, now):
    """① 今日爬虫产物。库缺失/查询失败=UNKNOWN；今日 exit!=0=FAIL。"""
    ev = [XQ_DB, CRAWL_STATUS_LOG]
    try:
        if not os.path.exists(XQ_DB):
            return _item(
                "crawl",
                "今日爬虫产物",
                STATUS_UNKNOWN,
                "xueqiu_info.db 不存在（爬虫从未入库）",
                ev,
            )
        mtime = datetime.fromtimestamp(os.path.getmtime(XQ_DB))
        con = sqlite3.connect(
            "file:%s?mode=ro" % XQ_DB.replace("\\", "/"), uri=True, timeout=5
        )
        try:
            con.execute("PRAGMA busy_timeout=5000")
            lo, hi = day + " 00:00:00", day + " 23:59:59"
            fetched = con.execute(
                "SELECT COUNT(*) FROM xq_status WHERE fetched_at BETWEEN ? AND ?",
                (lo, hi),
            ).fetchone()[0]
            created = con.execute(
                "SELECT COUNT(*) FROM xq_status WHERE created_at BETWEEN ? AND ?",
                (lo, hi),
            ).fetchone()[0]
        finally:
            con.close()
        detail = "db mtime=%s ｜ 当日 fetched=%d 行 ｜ 当日 created=%d 行" % (
            mtime.strftime("%Y-%m-%d %H:%M:%S"),
            fetched,
            created,
        )
        # 爬虫 wrapper 状态日志今日 exit 码（ASCII 行：starting/exit=<rc>）
        crawl_rc, crawl_line = None, None
        if os.path.exists(CRAWL_STATUS_LOG):
            with open(CRAWL_STATUS_LOG, "r", encoding="ascii", errors="replace") as f:
                for line in f:
                    if day in line:
                        crawl_line = line.strip()
                        if crawl_line.startswith("exit="):
                            try:
                                crawl_rc = int(crawl_line.split()[0][5:])
                            except ValueError as exc:
                                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tools/xq_timeline_watch.py", exc)
        if crawl_rc is not None and crawl_rc != 0:
            return _item(
                "crawl",
                "今日爬虫产物",
                STATUS_FAIL,
                detail + " ｜ 今日爬虫 exit=%d（%s）" % (crawl_rc, crawl_line),
                ev,
            )
        if fetched > 0:
            return _item(
                "crawl",
                "今日爬虫产物",
                STATUS_PASS,
                detail + " ｜ 今日爬虫 exit=%s" % crawl_rc,
                ev,
            )
        return _item(
            "crawl",
            "今日爬虫产物",
            STATUS_UNKNOWN,
            detail + " ｜ 当日无 fetched 行（18:00 生产轮未跑或空窗口日）"
            " ｜ 今日爬虫 exit=%s" % crawl_rc,
            ev,
        )
    except Exception as e:  # 只读收集器绝不崩
        return _item("crawl", "今日爬虫产物", STATUS_UNKNOWN, "收集异常: %r" % e, ev)


def report_is_fresh(mtime_ts: float, day: str) -> bool:
    """日报 JSON 新鲜度：mtime >= 当日 18:50 视为今晚 19:00 日报新产物。

    防"旧 JSON 过 15 键 → 假绿"：18:00 爬虫前人工冒烟/残留产物同样能
    过键齐备性校验，但其数据不是当晚生产链产出。阈值取 18:50（18:00
    爬虫之后、19:00 日报之前的保守下界）。
    """
    threshold = datetime.strptime(day + " 18:50:00", "%Y-%m-%d %H:%M:%S")
    return datetime.fromtimestamp(mtime_ts) >= threshold


def collect_report(day):
    """② 今日日报 JSON。复用 tools/verify_daily_report.py 校验逻辑。

    判定序：文件缺失=UNKNOWN；15 键/语义不过=FAIL；过但 mtime<当日
    18:50=UNKNOWN（旧产物，19:00 日报生成未确认，不判 PASS 假绿）。
    """
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import verify_daily_report as vdr  # noqa: E402

    daily_json = os.path.join(
        OUT_ROOT, "daily", "xq_intel_%s.json" % day.replace("-", "")
    )
    ev = [daily_json, LATEST_JSON]
    try:
        if not os.path.exists(daily_json):
            return _item(
                "report",
                "今日日报 JSON",
                STATUS_UNKNOWN,
                "daily/xq_intel_%s.json 未生成（19:00 日报未跑或尚未到点）"
                % day.replace("-", ""),
                ev,
            )
        with open(daily_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        fails = vdr.check_payload(data, vdr.load_expected_pipeline())
        if fails:
            why = "; ".join("%s: %s" % (n, w) for n, w in fails)
            return _item(
                "report",
                "今日日报 JSON",
                STATUS_FAIL,
                "check_payload %d 项失败：%s" % (len(fails), why),
                ev,
            )
        mt = datetime.fromtimestamp(os.path.getmtime(daily_json))
        if not report_is_fresh(os.path.getmtime(daily_json), day):
            return _item(
                "report",
                "今日日报 JSON",
                STATUS_UNKNOWN,
                "15 键齐备但报告 JSON 为 18:50 前旧产物（冒烟/残留，"
                "mtime=%s），19:00 日报生成未确认——不判 PASS 防假绿"
                % mt.strftime("%Y-%m-%d %H:%M:%S"),
                ev,
            )
        return _item(
            "report",
            "今日日报 JSON",
            STATUS_PASS,
            "15 键齐备 + 语义校验全过 + mtime 新鲜（%s，>=当日 18:50）"
            "（verify_daily_report.check_payload）｜ posts_today=%s "
            "ingested_today=%s"
            % (
                mt.strftime("%Y-%m-%d %H:%M:%S"),
                data.get("posts_today"),
                data.get("ingested_today"),
            ),
            ev,
        )
    except Exception as e:
        return _item("report", "今日日报 JSON", STATUS_UNKNOWN, "收集异常: %r" % e, ev)


def alert_is_fresh(mtime_ts: float, day: str) -> bool:
    """verify_alert.log 新鲜度：mtime >= 当日 19:00 视为今晚 19:05 验收产物。

    防"残留 FAIL → 假红"：tools/verify_daily_report.py 仅在验收失败时
    覆盖式落盘本文件，验收成功不写文件、mtime 不变——故当晚验收即使
    成功，mtime 仍停留在残留时刻（如 14:07 人工冒烟）。阈值取 19:00
    （19:05 生产验收前的保守下界）；report 项用 18:50 是因为日报 19:00
    跑，两项验收时点不同、阈值语义不同，不可混用。
    """
    threshold = datetime.strptime(day + " 19:00:00", "%Y-%m-%d %H:%M:%S")
    return datetime.fromtimestamp(mtime_ts) >= threshold


def collect_alert(day):
    """③ verify_alert.log 今日状态（含新鲜度判定）。

    判定序：文件不存在=UNKNOWN 偏 PASS → mtime<当日 19:00=UNKNOWN
    （FAIL 行为 19:05 生产验收前的历史残留，非今晚验收结论，不判 FAIL
    防假红）→ mtime>=当日 19:00 且含 FAIL 行=FAIL 摘录 → 无 FAIL 行=PASS。
    """
    ev = [VERIFY_ALERT_LOG]
    try:
        if not os.path.exists(VERIFY_ALERT_LOG):
            return _item(
                "alert",
                "验收告警 verify_alert.log",
                STATUS_UNKNOWN,
                "文件不存在=验收从未失败（偏 PASS 说明；19:05 验收正常"
                "通过时不落告警文件）",
                ev,
            )
        mt_ts = os.path.getmtime(VERIFY_ALERT_LOG)
        mtime = datetime.fromtimestamp(mt_ts)
        with open(VERIFY_ALERT_LOG, "r", encoding="ascii", errors="replace") as f:
            content = f.read()
        fail_lines = [ln for ln in content.splitlines() if "FAIL" in ln]
        excerpt = content.strip()[:600]
        if not alert_is_fresh(mt_ts, day):
            return _item(
                "alert",
                "验收告警 verify_alert.log",
                STATUS_UNKNOWN,
                "文件存在（mtime=%s）但为当日 19:00 前旧产物：FAIL 行为 "
                "19:05 生产验收前的历史残留（冒烟/上一轮），19:05 验收"
                "成功时本工具不落盘故 mtime 不更新——非今晚验收结论，"
                "不判 FAIL 防假红：\n%s"
                % (mtime.strftime("%Y-%m-%d %H:%M:%S"), excerpt),
                ev,
            )
        if fail_lines:
            return _item(
                "alert",
                "验收告警 verify_alert.log",
                STATUS_FAIL,
                "文件存在（mtime=%s，>=当日 19:00 新鲜）且含 FAIL 行"
                "（alert 为覆盖式落盘）：\n%s"
                % (mtime.strftime("%Y-%m-%d %H:%M:%S"), excerpt),
                ev,
            )
        return _item(
            "alert",
            "验收告警 verify_alert.log",
            STATUS_PASS,
            "文件存在（mtime=%s，>=当日 19:00 新鲜）且无 FAIL 行"
            "（偏 PASS）：%s"
            % (mtime.strftime("%Y-%m-%d %H:%M:%S"), content.strip()[:300] or "(空)"),
            ev,
        )
    except Exception as e:
        return _item(
            "alert", "验收告警 verify_alert.log", STATUS_UNKNOWN, "收集异常: %r" % e, ev
        )


def collect_shadow(day):
    """④ 19:10 日批产物（跨仓只读，拿不到=UNKNOWN，绝不崩）。"""
    ev = [SHADOW_LEDGER, SHADOW_LOG]
    try:
        if not os.path.exists(SHADOW_LEDGER):
            return _item(
                "shadow",
                "19:10 日批产物",
                STATUS_UNKNOWN,
                "ledger.jsonl 不存在（日批未跑或部署路径漂移）",
                ev,
            )
        n_today, codes, rules = 0, set(), Counter()
        with open(SHADOW_LEDGER, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                # 粗筛必须整行匹配：ledger 键序 action/data_version/mode/reason
                # 在前，ts 键排在行尾——按 line[:64] 截断时当日日期永不在窗内，
                # 当日行全部被跳过 → n_today 恒 0 → shadow 项永远 UNKNOWN
                # （2026-10-18 复盘实证修复；ts[:10] 精判仍在下方兜底）。
                if not line or day not in line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if str(row.get("ts", ""))[:10] != day:
                    continue
                n_today += 1
                if row.get("security"):
                    codes.add(row["security"])
                rules[row.get("rule_id") or "(none)"] += 1
        detail = "当日新行=%d ｜ 当日新码=%d ｜ rule 分布=%s" % (
            n_today,
            len(codes),
            dict(rules) if rules else "{}",
        )
        # shadow_daily.log 今日 exit 行佐证
        log_line = None
        if os.path.exists(SHADOW_LOG):
            with open(SHADOW_LOG, "r", encoding="utf-8", errors="replace") as f:
                for ln in f:
                    if day in ln and "shadow_daily exit=" in ln:
                        log_line = ln.strip()
        if log_line:
            detail += " ｜ %s" % log_line
        if n_today > 0:
            return _item("shadow", "19:10 日批产物", STATUS_PASS, detail, ev)
        if log_line and "exit=1" in log_line:
            return _item(
                "shadow",
                "19:10 日批产物",
                STATUS_FAIL,
                detail + " ｜ 当日无新行且日批 exit=1",
                ev,
            )
        return _item(
            "shadow",
            "19:10 日批产物",
            STATUS_UNKNOWN,
            detail + " ｜ 当日无新行（19:10 未到 / skip 日 / 未跑）",
            ev,
        )
    except Exception as e:
        return _item("shadow", "19:10 日批产物", STATUS_UNKNOWN, "收集异常: %r" % e, ev)


def collect_classify(day):
    """⑤ 分类追平轮进度（只读查库，busy_timeout 保护）。"""
    ev = [XQ_DB]
    try:
        con = sqlite3.connect(
            "file:%s?mode=ro" % XQ_DB.replace("\\", "/"), uri=True, timeout=5
        )
        try:
            con.execute("PRAGMA busy_timeout=5000")
            total = con.execute("SELECT COUNT(*) FROM xq_status").fetchone()[0]
            classified = con.execute(
                "SELECT COUNT(*) FROM xq_status_classified"
            ).fetchone()[0]
            today_n = con.execute(
                "SELECT COUNT(*) FROM xq_status_classified WHERE labeled_at >= ?",
                (day + " 00:00:00",),
            ).fetchone()[0]
        finally:
            con.close()
        pct = (100.0 * classified / total) if total else 0.0
        return _item(
            "classify",
            "分类追平轮进度",
            STATUS_PASS,
            "库内总量=%d ｜ 已打标=%d（%.1f%%）｜ 今日 labeled_at 增量=%d"
            " ｜ 剩余 pending=%d"
            % (total, classified, pct, today_n, total - classified),
            ev,
        )
    except Exception as e:
        return _item(
            "classify",
            "分类追平轮进度",
            STATUS_UNKNOWN,
            "收集异常（库被写锁占用的概率高，重试即可）: %r" % e,
            ev,
        )


def render_md(day, mode, items, generated_at):
    fails = [it for it in items if it["status"] == STATUS_FAIL]
    L = []
    L.append("# 雪球生产链时间线守望 · %s" % day)
    L.append("")
    L.append(
        "> 生成 %s ｜ 模式 %s ｜ 收集器 tools/xq_timeline_watch.py（全程只读）"
        % (generated_at, mode)
    )
    L.append("")
    if mode == "dry-run":
        L.append(
            "**本报告为 dry-run 自验产物（生产链未启动时点收集），"
            "各 UNKNOWN/缺省值不代表当晚真实状态。**"
        )
        L.append("")
    if fails:
        L.append("## !! FAIL 项（%d）" % len(fails))
        L.append("")
        for it in fails:
            L.append("- **[%s] %s**：%s" % (it["id"], it["title"], it["detail"]))
        L.append("")
    L.append("## 五项证据")
    L.append("")
    for it in items:
        L.append("### %s. %s —— %s" % (it["id"], it["title"], it["status"]))
        L.append("")
        L.append("- 判定：%s" % it["detail"])
        for p in it["evidence"]:
            exists = os.path.exists(p)
            L.append("- 证据路径：%s（%s）" % (p, "存在" if exists else "不存在"))
        L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="雪球生产链时间线守望（只读）")
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d"), help="守望日")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="标注 dry-run（生产链未启动时点的自验运行）",
    )
    a = ap.parse_args()
    day = a.date
    now = datetime.now()
    generated_at = now.strftime("%Y-%m-%d %H:%M:%S")
    mode = "dry-run" if a.dry_run else "live"

    items = [
        collect_crawl(day, now),
        collect_report(day),
        collect_alert(day),
        collect_shadow(day),
        collect_classify(day),
    ]
    has_fail = any(it["status"] == STATUS_FAIL for it in items)

    md = render_md(day, mode, items, generated_at)
    payload = {
        "date": day,
        "generated_at": generated_at,
        "mode": mode,
        "overall": "HAS_FAIL" if has_fail else "ALL_GREEN",
        "items": items,
    }
    os.makedirs(OUT_ROOT, exist_ok=True)
    stem = os.path.join(OUT_ROOT, "timeline_%s" % day)
    with open(stem + ".md", "w", encoding="utf-8") as f:
        f.write(md)
    with open(stem + ".json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    # 全绿（含 UNKNOWN）不写任何告警文件；有 FAIL 仅打标退出码 1，
    # 由 wrapper 记入 xq_timeline 状态日志，绝不另写告警文件。
    print(
        "[timeline-watch] mode=%s overall=%s report=%s.md"
        % (mode, payload["overall"], stem)
    )
    for it in items:
        print("  %-8s %s | %s" % (it["id"], it["status"], it["title"]))
    return 1 if has_fail else 0


if __name__ == "__main__":
    sys.exit(main())
