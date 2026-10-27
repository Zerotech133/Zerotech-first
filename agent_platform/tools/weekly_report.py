# -*- coding: utf-8 -*-
"""weekly_report.py —— 军团周报生成器（W36，账本投影器，只读）。

口径（承《2026-10-19_周报月报立项预研施工单》f5e112ca W36 条款 +
2026-10-19 老田拍板：实装授权生效、挂载点=方案B 周一 08:00 独立任务、
周窗=上周一~上周日）：

- 报告 = 账本投影，不是创作：全部内容只聚合/转抄真实账本，每段首行带出处锚
  与段级三态；账本里没有的字段不写、不估算、不「代拟」。
- 输入三源（逐源只读）：
  1. 7 日 daily_review 链（_summary.json / _corps_daily.md / 个人四问卡）
     ——缺日=该日报段 DEGRADED，报头缺日清单汇总，禁邻日插补（插补=编造）；
  2. data/shadow/monday_checkpoint.log ——只采信含 EXIT= 收尾行的完整块，
     门1~门4 结论原文转抄，不重跑巡检、不复算判据（巡检结论唯一权威在
     monday_checkpoint 本身）；
  3. data/shadow/eval_last_run.log（AgentShadowEvalWeekly 周五 17:45 出件，
     实证单 43d18029）+ data/shadow/shadow_ledger.jsonl 周窗行计数
     ——影子内容只转抄不解读（铁律②），出件为单文件覆盖式日志，转抄即留存，
     段内显式标注数据截止（mtime）。
- 周窗 = 上周一 ~ 上周日（方案B 错位：周一 08:00 出报引用的是上周一巡检
  出件，schema 显式声明，防读者误读为「本周」）。
- 守卫（调度器仅支持 daily cron 的实装口径）：无 --week 调用时，非周一
  自跳过（exit 0 不写文件）；周一默认投影最近完整周。--week 显式给参时
  （离线/补跑）不做周一守卫。

三态（W38 契约）：OK=全部出处锚在账且勾稽过 / INCONCLUSIVE=某源账本缺失或
为空（如实缺据）/ DEGRADED=账本可读但缺日（部分缺失，双列数据不取一舍一）。
报告级三态=段级最差态，头部列降级段清单。账本缺失时禁止以任何文字补写该段
内容（编文=红线）。

本脚本零账本写：只读 data/daily_review/、data/shadow/、data/meetings/、
data/schedules.json；唯一落盘 = --out 指定的报告产物。

CLI：
    python weekly_report.py [--week 2026-W42 | --week 2026-10-12] [--out <path>]

零依赖：仅标准库。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA_VERSION = "weekly.v1"
# 三态释义（W38 契约固定句，随报告头部输出；本身无数据，不违反投影纪律）
TRI_EXPLAIN = ("三态释义：INCONCLUSIVE=如实缺据，见缺件清单；"
               "DEGRADED=账实不符，见双列数据。")
TRI_ORDER = {"OK": 0, "INCONCLUSIVE": 1, "DEGRADED": 2}

WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 巡检块头（monday_checkpoint.log）
CHK_HEADER_RE = re.compile(
    r"^M5 刀4 周一观察点护航巡检 (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", re.M)


# ---------------------------------------------------------------- 基础面

def repo_root() -> Path:
    """仓根 = tools/ -> agent_platform -> 仓根。"""
    return Path(__file__).resolve().parents[2]


def ap_root() -> Path:
    return Path(__file__).resolve().parents[1]


def week_window(week: str | None, today: date | None = None) -> tuple[date, date, str]:
    """解析 --week（ISO 周 YYYY-Www 或周内任一日期 YYYY-MM-DD）；
    缺省=最近完整周（相对 today，缺省今天）。返回 (周一, 周日, ISO 周标)。"""
    if week:
        m = WEEK_RE.match(week)
        if m:
            monday = date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
        elif DATE_RE.match(week):
            d = date.fromisoformat(week)
            monday = d - timedelta(days=d.weekday())
        else:
            raise SystemExit(f"--week 格式非法：{week}（应为 YYYY-Www 或 YYYY-MM-DD）")
    else:
        today = today or date.today()
        if today.weekday() != 0:
            # 非周一自跳过守卫（调度器 daily 08:00 挂载口径，见模块 docstring）
            print(f"[skip] 今日 {today} 非周一，周报生成器自跳过（--week 可显式指定周）")
            raise SystemExit(0)
        monday = today - timedelta(days=7)
    sunday = monday + timedelta(days=6)
    iso = f"{monday.isocalendar().year}-W{monday.isocalendar().week:02d}"
    return monday, sunday, iso


def worst(*states: str) -> str:
    return max(states, key=lambda s: TRI_ORDER.get(s, 2))


# ---------------------------------------------------------------- 三源采集

def load_daily_reviews(root: Path, monday: date, sunday: date) -> dict:
    """7 日 daily_review 链。缺日记 None（禁邻日插补）。"""
    days: dict[str, dict | None] = {}
    d = monday
    while d <= sunday:
        key = d.isoformat()
        base = root / "data" / "daily_review" / key
        summary = base / "_summary.json"
        if base.is_dir() and summary.is_file():
            try:
                j = json.loads(summary.read_text(encoding="utf-8"))
                days[key] = {
                    "n": int(j.get("n", 0)),
                    "no_task": list(j.get("no_task", [])),
                    "depts": j.get("depts", {}),
                    "corps": base / "_corps_daily.md",
                    "cards": sorted(p.stem for p in base.glob("*.md")
                                    if not p.name.startswith("_")),
                }
            except (ValueError, OSError):
                days[key] = None  # 账本损坏按缺日处理，不编文
        else:
            days[key] = None
        d += timedelta(days=1)
    return days


def load_checkpoint_block(root: Path, monday: date, sunday: date) -> dict | None:
    """取周窗内最后一个「含 EXIT= 收尾行的完整块」。只转抄不复算。"""
    log = root / "data" / "shadow" / "monday_checkpoint.log"
    if not log.is_file():
        return None
    text = log.read_text(encoding="utf-8", errors="replace")
    # 按「巡检头行」切块
    marks = [(m.start(), m.group(1)) for m in CHK_HEADER_RE.finditer(text)]
    blocks: list[tuple[str, str]] = []
    for i, (pos, ts) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        blocks.append((ts, text[pos:end]))
    complete = []
    for ts, body in blocks:
        if monday.isoformat() <= ts[:10] <= sunday.isoformat() and "EXIT=" in body:
            complete.append((ts, body))
    if not complete:
        return None
    ts, body = complete[-1]  # 周窗内最后一个完整块
    gate_lines = [ln for ln in body.splitlines()
                  if ln.startswith("[") or ln.startswith("结论:")
                  or ln.startswith("EXIT=")]
    return {"ts": ts, "gates": gate_lines}


def load_eval(root: Path, monday: date, sunday: date) -> dict | None:
    """影子周评估出件（覆盖式单文件，mtime=数据截止）+ 周窗影子账本行计数。"""
    log = root / "data" / "shadow" / "eval_last_run.log"
    if not log.is_file():
        return None
    quoted = [ln.rstrip() for ln in log.read_text(encoding="utf-8",
              errors="replace").splitlines()
              if ln.startswith("[J") or ln.startswith("影子评估结论")
              or ln.startswith("影子账本") or ln.startswith("[skip]")]
    ledger = root / "data" / "shadow" / "shadow_ledger.jsonl"
    per_day: dict[str, int] = {}
    if ledger.is_file():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            d = str(row.get("date", ""))
            if monday.isoformat() <= d <= sunday.isoformat():
                per_day[d] = per_day.get(d, 0) + 1
    mtime = datetime.fromtimestamp(log.stat().st_mtime)
    return {"quoted": quoted, "per_day": per_day, "cutoff": mtime.isoformat(sep=" ", timespec="minutes")}


def load_meetings(root: Path, monday: date, sunday: date) -> dict[str, list[Path]]:
    """晨会四段纪要（按日分组）。阻塞与教训段只转抄三/四两段原文。"""
    mdir = root / "data" / "meetings"
    out: dict[str, list[Path]] = {}
    if not mdir.is_dir():
        return out
    d = monday
    while d <= sunday:
        key = d.isoformat()
        out[key] = sorted(mdir.glob(f"{key}_*_晨会.md"))
        d += timedelta(days=1)
    return out


def load_schedules(root: Path) -> list[dict] | None:
    f = root / "data" / "schedules.json"
    if not f.is_file():
        return None
    try:
        rows = json.loads(f.read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else None
    except (ValueError, OSError):
        return None


def meeting_block34(path: Path) -> str:
    """转抄晨会三（阻塞求助）/四（决议与教训）两段原文。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    keep: list[str] = []
    grabbing = False
    for ln in text.splitlines():
        if ln.startswith("## 三、") or ln.startswith("## 四、"):
            grabbing = True
            keep.append(ln)
            continue
        if ln.startswith("## ") and grabbing:
            grabbing = False
            continue
        if grabbing:
            keep.append(ln)
    return "\n".join(keep).rstrip()


# ---------------------------------------------------------------- 七段投影

def _anchor(title: str, state: str, sources: list[str], note: str = "") -> str:
    return (f"## {title}\n> 锚: {' ; '.join(sources)} | 态: {state}"
            + (f"（{note}）" if note else ""))


def build_report(root: Path, monday: date, sunday: date, iso_week: str) -> str:
    dr = load_daily_reviews(root, monday, sunday)
    chk = load_checkpoint_block(root, monday, sunday)
    ev = load_eval(root, monday, sunday)
    meetings = load_meetings(root, monday, sunday)
    scheds = load_schedules(root)

    missing = [d for d, v in dr.items() if v is None]
    present = {d: v for d, v in dr.items() if v is not None}
    w = f"{monday.isoformat()}~{sunday.isoformat()}"
    L: list[str] = []

    # 报头（报告级三态=段级最差态 + 降级段清单 + 缺日清单 + 释义）
    hdr_state = "OK"
    if missing:
        hdr_state = "DEGRADED"   # daily_review 链缺日
    if chk is None:
        hdr_state = worst(hdr_state, "INCONCLUSIVE")
    if ev is None:
        hdr_state = worst(hdr_state, "INCONCLUSIVE")
    if scheds is None:
        hdr_state = worst(hdr_state, "INCONCLUSIVE")

    L.append(f"# 军团周报 {iso_week}（周窗 {w}）")
    L.append(f"> report_schema_version: {SCHEMA_VERSION} | 报告级三态: {hdr_state} | "
             f"生成时刻: {datetime.now().isoformat(sep=' ', timespec='seconds')}")
    L.append("> 周窗口径：周一 08:00 出报，投影上周一~上周日（巡检段引用上周出件，非「本周」）；"
             "daily_review 为交易日口径，周末无账本日如实列缺")
    L.append(f"> {TRI_EXPLAIN}")
    if missing:
        L.append(f"> 缺日清单：{'、'.join(missing)}")
        degraded_secs = "①周概览 ②履职与催办 ③部门趋势（缺日 DEGRADED）"
        if any(meetings.values()) and any(not ps for ps in meetings.values()):
            degraded_secs += "；⑥阻塞与教训（缺晨会日 DEGRADED）"
        L.append(f"> 降级段：{degraded_secs}")
    if chk is None:
        L.append("> 缺件清单：monday_checkpoint.log 周窗内无含 EXIT= 的完整块（④段 INCONCLUSIVE）")
    if ev is None:
        L.append("> 缺件清单：eval_last_run.log 缺失（⑤段 INCONCLUSIVE）")
    L.append("")

    # ① 周概览（5 交易日聚合，全部来自 _summary.json 勾稽；缺日不插补）
    if present:
        tot_n = sum(v["n"] for v in present.values())
        tot_duty = sum(v["n"] - len(v["no_task"]) for v in present.values())
        dept_days = sorted({k for v in present.values() for k in v["depts"]})
        L.append(_anchor("①周概览", "DEGRADED" if missing else "OK",
                         ["agent_platform/data/daily_review/"],
                         note=f"周窗 {w}；实有账本日 {'、'.join(sorted(present))}"
                              + (f"；缺日 {'、'.join(missing)}" if missing else "")))
        for d in sorted(present):
            v = present[d]
            L.append(f"- {d}：在册 {v['n']} 人，无任务 {len(v['no_task'])} 人，"
                     f"履职 {v['n'] - len(v['no_task'])} 人（出处：daily_review/{d}/_summary.json n/no_task 字段）")
        L.append(f"- 周聚合（仅实有账本日勾稽，缺日不计）：在册人日 {tot_n}，"
                 f"履职人日 {tot_duty}，无任务人日 {tot_n - tot_duty}")
        L.append(f"- 出现过日报的部门（{len(dept_days)} 个）：{'、'.join(dept_days)}"
                 f"（出处：_summary.json depts 键并集）")
    else:
        L.append(_anchor("①周概览", "INCONCLUSIVE", ["agent_platform/data/daily_review/"],
                         note="7 日全缺，无账本可投影——不编文"))
    L.append("")

    # ② 履职与催办（逐人逐日矩阵，只数不评）
    if present:
        L.append(_anchor("②履职与催办", "DEGRADED" if missing else "OK",
                         ["agent_platform/data/daily_review/"],
                         note=f"仅实有账本日 {'、'.join(sorted(present))}；缺日 {'、'.join(missing)}"))
        no_task_acc: dict[str, int] = {}
        for d in sorted(present):
            for name in present[d]["no_task"]:
                no_task_acc[name] = no_task_acc.get(name, 0) + 1
        for d in sorted(present):
            v = present[d]
            # 矩阵行：按部门展开在册/无任务计数（depts 勾稽，不解读）
            rows = []
            for dept, info in v["depts"].items():
                nt = set(info.get("no_task", []))
                rows.append(f"{dept}:{info.get('n', 0)}员/无任务{len(nt)}")
            L.append(f"- {d}：" + "；".join(rows)
                     + f"（出处：daily_review/{d}/_summary.json depts）")
        L.append("- 无任务催办累计名单（人日口径，按账本原样计数）："
                 + "、".join(f"{k}×{no_task_acc[k]}" for k in sorted(no_task_acc)))
    else:
        L.append(_anchor("②履职与催办", "INCONCLUSIVE", ["agent_platform/data/daily_review/"],
                         note="7 日全缺——不编文"))
    L.append("")

    # ③ 部门趋势（逐日计数序列，不自评不解读）
    if present:
        L.append(_anchor("③部门趋势", "DEGRADED" if missing else "OK",
                         ["agent_platform/data/daily_review/"],
                         note="逐日 n 计数序列原样投影；缺日以「缺」标注，不插补"))
        dept_names = sorted({dept for v in present.values() for dept in v["depts"]})
        for dept in dept_names:
            series = []
            d = monday
            while d <= sunday:
                v = present.get(d.isoformat())
                series.append(str(v["depts"][dept]["n"]) if v and dept in v["depts"] else "缺")
                d += timedelta(days=1)
            L.append(f"- {dept}：{' | '.join(series)}"
                     f"（出处：daily_review/_summary.json depts.{dept}.n）")
    else:
        L.append(_anchor("③部门趋势", "INCONCLUSIVE", ["agent_platform/data/daily_review/"],
                         note="7 日全缺——不编文"))
    L.append("")

    # ④ 平台健康（巡检原文转抄；周窗内最后一个含 EXIT= 的完整块）
    if chk:
        L.append(_anchor("④平台健康", "OK",
                         ["agent_platform/data/shadow/monday_checkpoint.log"],
                         note=f"周窗内最后一个完整块（含 EXIT= 收尾行）{chk['ts']}；"
                              "门1~门4 结论转抄，未重跑巡检"))
        for ln in chk["gates"]:
            L.append(f"    {ln}")
    else:
        L.append(_anchor("④平台健康", "INCONCLUSIVE",
                         ["agent_platform/data/shadow/monday_checkpoint.log"],
                         note="周窗内无含 EXIT= 收尾行的完整块（进行中/作废块不采信）——不编文"))
    L.append("")

    # ⑤ 影子链周段（影子内容只转抄不解读，铁律②；标注数据截止）
    if ev:
        ledger_txt = ("；".join(f"{d}：{n} 行" for d, n in sorted(ev["per_day"].items()))
                      if ev["per_day"] else "周窗内无影子账本行")
        L.append(_anchor("⑤影子链周段", "OK",
                         ["agent_platform/data/shadow/eval_last_run.log",
                          "agent_platform/data/shadow/shadow_ledger.jsonl"],
                         note=f"数据截止 {ev['cutoff']}（覆盖式单文件，转抄即留存）"))
        for ln in ev["quoted"]:
            L.append(f"    {ln}")
        L.append(f"- 周窗影子账本行计数：{ledger_txt}（出处：shadow_ledger.jsonl date 字段过滤）")
    else:
        L.append(_anchor("⑤影子链周段", "INCONCLUSIVE",
                         ["agent_platform/data/shadow/eval_last_run.log"],
                         note="出件缺失——不编文"))
    L.append("")

    # ⑥ 阻塞与教训（仅转抄晨会三/四两段原文；提炼器未回填处显式标「空，非缺」）
    have_meetings = {d: ps for d, ps in meetings.items() if ps}
    no_meeting = [d for d, ps in meetings.items() if not ps]
    if have_meetings:
        L.append(_anchor("⑥阻塞与教训", "DEGRADED" if no_meeting else "OK",
                         ["agent_platform/data/meetings/"],
                         note=f"实有晨会日 {'、'.join(sorted(have_meetings))}"
                              + (f"；缺晨会日 {'、'.join(no_meeting)}（按日如实列缺，不插补）"
                                 if no_meeting else "")))
        for d in sorted(have_meetings):
            for p in have_meetings[d]:
                L.append(f"- {p.name}（出处：agent_platform/data/meetings/{p.name}）：")
                L.append("    " + meeting_block34(p).replace("\n", "\n    "))
    else:
        L.append(_anchor("⑥阻塞与教训", "INCONCLUSIVE", ["agent_platform/data/meetings/"],
                         note="周窗内无晨会纪要——不编文"))
    L.append("")

    # ⑦ 下周排程对表（schedules.json 现状转抄）
    if scheds is not None:
        L.append(_anchor("⑦下周排程对表", "OK", ["agent_platform/data/schedules.json"],
                         note="排程现状原样转抄，含本单新增周报条目（若在）"))
        for r in scheds:
            L.append(f"- {r.get('id')} | {r.get('agent')} | {r.get('desc')} | "
                     f"cron={r.get('cron')} | weekdays_only={r.get('weekdays_only')} | "
                     f"enabled={r.get('enabled')}")
    else:
        L.append(_anchor("⑦下周排程对表", "INCONCLUSIVE", ["agent_platform/data/schedules.json"],
                         note="排程账本缺失——不编文"))
    return "\n".join(L).rstrip() + "\n"


# ---------------------------------------------------------------- 数字反查钩子

def recheck(text: str, base: Path) -> dict:
    """W38 查3 复算钩子（check_report_isolation 调用）：对周报全部派生数字
    与逐行转抄重算比对。返回 {violations, checked, notes}。
    base=仓根；数据根 = base/agent_platform（与 build_report 的 ap_root 一致）。"""
    import check_report_isolation as cri  # 仅复用其解析面，无写路径

    ap = base / "agent_platform"
    rep = cri.parse_report(text)
    secs = {s["title"]: "\n".join(s["lines"]) for s in rep["sections"]}

    def sec_of(prefix: str) -> str:
        for t, body in secs.items():
            if t.startswith(prefix):
                return body
        return ""

    violations: list[str] = []
    checked = 0
    notes: list[str] = []
    monday, sunday, _iso = week_window_from_text(text)

    dr = load_daily_reviews(ap, monday, sunday)
    present = {d: v for d, v in dr.items() if v is not None}
    missing = [d for d, v in dr.items() if v is None]

    # ①周概览
    s1 = sec_of("## ①")
    if present:
        for d, v in present.items():
            nt = len(v["no_task"])
            exp = f"- {d}：在册 {v['n']} 人，无任务 {nt} 人，履职 {v['n'] - nt} 人"
            if exp in s1:
                checked += 1
            else:
                violations.append(f"查3-①：逐日勾稽行不符或缺失 -> {exp}")
        tot_n = sum(v["n"] for v in present.values())
        tot_duty = sum(v["n"] - len(v["no_task"]) for v in present.values())
        dept_days = sorted({k for v in present.values() for k in v["depts"]})
        for exp in (f"在册人日 {tot_n}，履职人日 {tot_duty}，无任务人日 {tot_n - tot_duty}",
                    f"出现过日报的部门（{len(dept_days)} 个）"):
            if exp in s1:
                checked += 1
            else:
                violations.append(f"查3-①：聚合数不符或缺失 -> {exp}")
    if missing and "缺日清单" not in text:
        violations.append("查3-报头：账本存在缺日但报头无缺日清单")

    # ②履职与催办
    s2 = sec_of("## ②")
    if present:
        acc: dict[str, int] = {}
        for d, v in present.items():
            for dept, info in v["depts"].items():
                exp = f"{dept}:{info.get('n', 0)}员/无任务{len(info.get('no_task', []))}"
                if exp in s2:
                    checked += 1
                else:
                    violations.append(f"查3-②：部门矩阵不符 -> {d} {exp}")
            for name in v["no_task"]:
                acc[name] = acc.get(name, 0) + 1
        for name, cnt in acc.items():
            if f"{name}×{cnt}" in s2:
                checked += 1
            else:
                violations.append(f"查3-②：催办累计名单不符 -> {name}×{cnt}")

    # ③部门趋势
    s3 = sec_of("## ③")
    dept_names = sorted({dept for v in present.values() for dept in v["depts"]})
    for dept in dept_names:
        series = []
        d = monday
        while d <= sunday:
            v = present.get(d.isoformat())
            series.append(str(v["depts"][dept]["n"]) if v and dept in v["depts"] else "缺")
            d += timedelta(days=1)
        exp = f"- {dept}：{' | '.join(series)}"
        if exp in s3:
            checked += 1
        else:
            violations.append(f"查3-③：部门趋势序列不符 -> {dept}")

    # ④平台健康（转抄行必须逐字在账）
    s4 = sec_of("## ④")
    log_f = ap / "data" / "shadow" / "monday_checkpoint.log"
    log_txt = log_f.read_text(encoding="utf-8", errors="replace") if log_f.is_file() else ""
    for ln in s4.splitlines():
        t = ln.strip()
        if t.startswith("[") or t.startswith("结论:") or t.startswith("EXIT="):
            checked += 1
            if t not in log_txt:
                violations.append(f"查3-④：巡检转抄行非账本原文 -> {t[:60]}")

    # ⑤影子链周段
    s5 = sec_of("## ⑤")
    ev_f = ap / "data" / "shadow" / "eval_last_run.log"
    ev_txt = ev_f.read_text(encoding="utf-8", errors="replace") if ev_f.is_file() else ""
    for ln in s5.splitlines():
        t = ln.strip()
        if t.startswith("[J") or t.startswith("影子"):
            checked += 1
            if t not in ev_txt:
                violations.append(f"查3-⑤：影子转抄行非账本原文 -> {t[:60]}")
    ledger = ap / "data" / "shadow" / "shadow_ledger.jsonl"
    per_day: dict[str, int] = {}
    if ledger.is_file():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                d = str(row.get("date", ""))
                if monday.isoformat() <= d <= sunday.isoformat():
                    per_day[d] = per_day.get(d, 0) + 1
    for d, n in per_day.items():
        if f"{d}：{n} 行" in s5:
            checked += 1
        else:
            violations.append(f"查3-⑤：影子账本行计数不符 -> {d}：{n}")

    # ⑥阻塞与教训（转抄行必须逐字在其出处账本内）
    s6 = sec_of("## ⑥")
    cur_txt = None
    for ln in s6.splitlines():
        t = ln.strip()
        if not t or t.startswith(">"):
            continue
        if t.startswith("- 2026-") and "（出处：" in t:
            fname = t.split("（出处：")[-1].split("）")[0]
            f = base / fname
            cur_txt = f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""
            checked += 1
            if not cur_txt:
                violations.append(f"查3-⑥：出处文件缺失或不可读 -> {fname}")
            continue
        if cur_txt is None:
            continue
        checked += 1
        if t not in cur_txt:
            violations.append(f"查3-⑥：晨会转抄行非账本原文 -> {t[:60]}")

    # ⑦下周排程对表
    s7 = sec_of("## ⑦")
    rows = load_schedules(ap) or []
    for r in rows:
        exp_prefix = f"- {r.get('id')} | {r.get('agent')} | {r.get('desc')} | cron={r.get('cron')}"
        if exp_prefix in s7:
            checked += 1
        else:
            violations.append(f"查3-⑦：排程行不符 -> {r.get('id')}")
    return {"violations": violations, "checked": checked, "notes": notes}


def week_window_from_text(text: str) -> tuple[date, date, str]:
    """从报告头「周窗 YYYY-MM-DD~YYYY-MM-DD」反解周窗。"""
    m = re.search(r"周窗 (\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})", text)
    if not m:
        raise ValueError("报告头缺周窗声明，无法复算")
    monday = date.fromisoformat(m.group(1))
    sunday = date.fromisoformat(m.group(2))
    iso = f"{monday.isocalendar().year}-W{monday.isocalendar().week:02d}"
    return monday, sunday, iso


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="军团周报生成器（账本投影，只读）")
    ap.add_argument("--week", help="ISO 周（YYYY-Www）或周内任一日期（YYYY-MM-DD）；"
                                    "缺省=周一自动投影最近完整周，非周一自跳过")
    ap.add_argument("--out", help="报告产物路径；缺省 agent_platform/data/reports/weekly_<ISO周>.md")
    args = ap.parse_args(argv)

    monday, sunday, iso = week_window(args.week)
    out = Path(args.out) if args.out else ap_root() / "data" / "reports" / f"weekly_{iso}.md"
    text = build_report(ap_root(), monday, sunday, iso)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"[ok] 周报已投影 -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
