# -*- coding: utf-8 -*-
"""morning_review_backfill.py —— 晨会纪要第④段回填器（W42，账本投影器，零自动定稿）。

口径（承《2026-10-19_W39晨会纪要回填机制预研施工单》（文件名 W39 为历史编号
保留，实义=W42）+ 2026-10-19 老田十一项拍板（执行单 f1b2c8d8 #10）：
挂载点=方案B 独立任务 daily 17:55）：

- 回填器 = 账本投影器，不是创作器：第④段（决议与教训）草稿全部内容只聚合/
  转抄真实账本，逐条带出处锚；账本里没有的结论不写、不估算、不「代拟」教训。
- 输入三源（逐源只读）：
  1. 当日复盘卡链 data/daily_review/<date>/：_summary.json（n/no_task/depts，
     名单口径承 2467cfeb 禁用活体 org.json）+ 个人四问卡③「我的进化」
     （缩引 ≤44 字符，legion_meeting L107 同款截断口径）；
  2. 工单账本 .worktrees/tasks.json 经 tools/validate_json.check_tasks_doc
     纯函数产出 (errors, warns)——WARN 逐条转抄为 decisions 候选；超期 doing
     >72h（legion_meeting L127-136 同源判据，锚时点=当日 17:55）；ERROR 单
     不出 decisions（数据质量事件另行走数据质量线，施工单开放问题 3）；
     周一另转抄 data/shadow/monday_checkpoint.log 当日 observe 行（只转抄
     不重跑巡检）；
  3. 既有晨会纪要骨架 data/meetings/<date>_<dept>_晨会.md——第④段草稿回填
     到同文件同段；academy 第⑤段按「决议与教训」标题定位（防段号漂移）。
- WARN/超期 → 部门归属判据（纯机械）：工单 desc 含「[<AGENT_ID>]」且该 ID
  在纪要骨架第①段与会名单中 → 归属该部门；否则=军团级，转抄进当日全部纪要
  草稿（军团账本事实各部同读，不漏转抄、不虚构域归属）。
- 三态（段级，W38 契约）：OK=三源齐备且勾稽一致 / INCONCLUSIVE=某源缺失或
  当日零素材（如实缺据，判例 6 空集≠通过）/ DEGRADED=多源冲突（骨架第③段
  「无阻塞」与 tasks.json 超期单>0 不一致，或反向——双列原文数据不取一舍一）。
- 零自动定稿红线：原「（待填）」占位替换为「（草稿 <三态>，待部务会确认）」
  头行 + 逐条素材；lessons 每条行尾固定标记「（候选，部务会拍板后入知识环）」；
  decisions 不因回填视为已决议，lessons 不因回填入知识环——入环唯一通道是
  部务会拍板（knowledge_distill 入口零触碰）。已回填/已人填的纪要幂等跳过
  不覆盖（回滚面=git 单文件 revert）。
- 守卫：当日无晨会骨架（09:00 引擎未产出）→ 自跳过 exit 0 不写文件；账本
  源缺失不跳过——三态 INCONCLUSIVE 落盘如实缺据。停用开关：环境变量
  LEGION_MEETING_BACKFILL_OFF=1 → exit 0（停用后次日军团晨会第④段回到
  「（待填）」原状，占位逻辑永不删除，保证可回退）。
- 禁创作语汇（check_report_isolation.py L34 同款黑名单）：建议/预计/据悉/
  约/或因/可能因——本工具生成语汇零命中；账本原文缩引按转抄纪律保留原样。

本脚本零账本写（只读 daily_review/、meetings/、.worktrees/tasks.json、
data/shadow/monday_checkpoint.log）；唯一落盘 = 晨会纪要第④段回填（施工单
指定写对象）或 --out-dir 指定的草稿预览副本（离线/样张模式，meetings 零写）。

CLI：
    python morning_review_backfill.py                       # 日常：回填今日
    python morning_review_backfill.py --date 2026-10-17     # 离线补跑指定日
    python morning_review_backfill.py --date 2026-10-17 --out-dir C:/Temp/w42_probe/
                                                            # 草稿预览，meetings 零写

零依赖：仅标准库（check_tasks_doc 复用仓内 tools/validate_json.py 纯函数，
施工单明示可直接复用）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

SCHEMA_VERSION = "meeting_backfill.v1"
TRI_ORDER = {"OK": 0, "INCONCLUSIVE": 1, "DEGRADED": 2}
LESSON_MARKER = "（候选，部务会拍板后入知识环）"
BLACKLIST_RE = re.compile(r"建议|预计|据悉|约|或因|可能因")  # check_report_isolation L34 同款
Q3_QUOTE_MAX = 44  # legion_meeting L107 同款截断口径
OVERDUE_SECS = 72 * 3600  # legion_meeting L127-136 同源判据
OBSERVE_HEADER_RE = re.compile(
    r"^M5 刀4 周一观察点护航巡检 (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", re.M)
SECTION4_TITLE = "决议与教训"  # academy 实为第五段，按标题定位防段号漂移
MEMBER_RE = re.compile(r"^-\s+.*?（([A-Z0-9_]+)）：")
AGENT_TAG_RE = re.compile(r"\[([A-Z0-9_]+)\]")


def repo_root() -> Path:
    """仓根 = agent_platform/tools/ -> agent_platform -> 仓根。"""
    return Path(__file__).resolve().parents[2]


def ap_root() -> Path:
    return Path(__file__).resolve().parents[1]


def worst(*states: str) -> str:
    return max(states, key=lambda s: TRI_ORDER.get(s, 2))


# ---------------------------------------------------------------- 三源采集（只读）

def load_tasks(root: Path) -> tuple[dict | None, list, list]:
    """tasks.json + check_tasks_doc 纯函数复算（施工单授权的唯一复算点）。
    返回 (doc, errors, warns)；doc=None=真源缺失/损坏（如实缺据，不编文）。"""
    f = root / ".worktrees" / "tasks.json"
    if not f.is_file():
        return None, [], []
    try:
        doc = json.loads(f.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None, [], []
    if not isinstance(doc, dict):
        return None, [], []
    sys.path.insert(0, str(root / "tools"))
    from validate_json import check_tasks_doc  # 纯函数：无 IO 无 print
    errors, warns = check_tasks_doc(doc)
    return doc, errors, warns


def load_daily(root: Path, day: str) -> dict | None:
    """当日复盘卡链。缺失/损坏=None（如实缺据，禁邻日插补）。"""
    base = root / "agent_platform" / "data" / "daily_review" / day
    summary = base / "_summary.json"
    if not (base.is_dir() and summary.is_file()):
        return None
    try:
        j = json.loads(summary.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return {
        "n": int(j.get("n", 0)),
        "no_task": list(j.get("no_task", [])),
        "depts": j.get("depts", {}) if isinstance(j.get("depts"), dict) else {},
        "base": base,
    }


def load_observe_lines(root: Path, day: date) -> list[str]:
    """周一 monday_checkpoint observe 行转抄（只转抄不重跑巡检）。
    取当日最后一个含 EXIT= 收尾行的完整块内的 [observe] 行。"""
    if day.weekday() != 0:
        return []
    log = root / "agent_platform" / "data" / "shadow" / "monday_checkpoint.log"
    if not log.is_file():
        return []
    text = log.read_text(encoding="utf-8", errors="replace")
    marks = [(m.start(), m.group(1)) for m in OBSERVE_HEADER_RE.finditer(text)]
    for i in range(len(marks) - 1, -1, -1):
        pos, ts = marks[i]
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[pos:end]
        if ts[:10] == day.isoformat() and "EXIT=" in body:
            return [ln.strip() for ln in body.splitlines() if ln.startswith("[observe]")]
    return []


def error_unit_ids(doc: dict | None) -> set:
    """ERROR 单 ID 集：check_tasks_doc 逐单复算（同一纯函数判据，不另立分级）。
    铁律（施工单开放问题 3）：ERROR 单不出 decisions/lessons 素材面，
    数据质量事件另行走数据质量线——同一单的 WARN 也一并排除。"""
    if not isinstance(doc, dict):
        return set()
    from validate_json import check_tasks_doc
    bad: set = set()
    for tid, t in doc.items():
        if not isinstance(t, dict) or check_tasks_doc({tid: t})[0]:
            bad.add(tid)
    return bad


def overdue_tasks(doc: dict, anchor_ts: float) -> list[tuple[str, str]]:
    """超期单：status=doing 且 created 超 72h（legion_meeting 同源判据）。
    created 不可解析的不判（不估算）；ERROR 单已由 check_tasks_doc 分级、
    本函数只看 doing 态（ERROR 单不出 decisions，见模块 docstring）。"""
    out = []
    for tid, t in doc.items():
        if not isinstance(t, dict) or t.get("status") != "doing":
            continue
        c = str(t.get("created", "")).replace("T", " ")
        ts = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                ts = time.mktime(time.strptime(c[:19 if "%H" in fmt else 10], fmt))
                break
            except ValueError:
                continue
        if ts is not None and anchor_ts - ts > OVERDUE_SECS:
            out.append((tid, str(t.get("created", ""))))
    return out


# ---------------------------------------------------------------- 骨架解析

def find_section(text: str, title: str) -> tuple[int, int]:
    """按标题定位段（返回 (段头行号, 段结束行号)），academy 五段同适。"""
    lines = text.splitlines()
    start = -1
    for i, ln in enumerate(lines):
        if ln.startswith("## ") and title in ln:
            start = i
            break
    if start < 0:
        return -1, -1
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    return start, end


def section_members(text: str) -> list[str]:
    """纪要骨架第①段逐行提取与会成员 AGENT ID（账本自带名单，不用 org.json）。"""
    s, e = find_section(text, "昨日完成")
    if s < 0:
        return []
    out = []
    for ln in text.splitlines()[s:e]:
        m = MEMBER_RE.match(ln)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def card_q3(base: Path, agent: str) -> str | None:
    """四问卡③「我的进化」原文缩引（≤44 字符）。卡缺失=None。"""
    f = base / f"{agent}.md"
    if not f.is_file():
        return None
    try:
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if "③" in ln and "：" in ln:
                return ln.split("：", 1)[-1].strip()[:Q3_QUOTE_MAX]
    except OSError:
        return None
    return None


# ---------------------------------------------------------------- 草稿构建

def _classify_warns(warns: list) -> dict[str, list[str]]:
    """WARN 按判据类型分组（validate_json 判据原文的机械子串归类）。"""
    kinds: dict[str, list[str]] = {"空壳": [], "created": [], "merged": [], "ID形态": []}
    for w in warns:
        tid = w.split(" ", 1)[0]
        if "空壳完工单" in w:
            kinds["空壳"].append(tid)
        elif "created=" in w:
            kinds["created"].append(tid)
        elif "merged=" in w:
            kinds["merged"].append(tid)
        else:
            kinds["ID形态"].append(tid)
    return kinds


def build_draft(text: str, fname: str, day: str, daily: dict | None,
                doc: dict | None, errors: list, warns: list,
                overdue: list, observe: list, anchor: str) -> tuple[str, str]:
    """构建第④段替换块。返回 (state, block)。只投影，不创作。"""
    members = section_members(text)
    dept_no_task = list(daily["depts"].get(_dept_of(fname), {}).get("no_task", [])) if daily else []
    members_set = set(members)

    # 铁律（施工单开放问题 3）：ERROR 单不出素材面（decisions/lessons 均排除，
    # 出处锚 meta 仍如实上报账本原始 ERROR/WARN 计数）
    error_ids = error_unit_ids(doc)
    material_warns = [w for w in warns
                      if w.split(" ", 1)[0].strip("'\"") not in error_ids]

    # WARN/超期按 [AGENT] 归属；无标记=军团级（转抄进全部当日草稿）
    dept_warns, corps_warns = [], []
    for w in material_warns:
        tid = w.split(" ", 1)[0].strip("'\"")
        tags = AGENT_TAG_RE.findall(str(doc.get(tid, {}).get("desc", ""))) if doc else []
        (dept_warns if any(a in members_set for a in tags) else corps_warns).append(w)
    dept_overdue, corps_overdue = [], []
    for tid, c in overdue:
        if tid in error_ids:
            continue
        tags = AGENT_TAG_RE.findall(str(doc.get(tid, {}).get("desc", ""))) if doc else []
        (dept_overdue if any(a in members_set for a in tags) else corps_overdue).append((tid, c))

    # 勾稽（③段「无阻塞」vs 超期单，双向不一致=DEGRADED 双列）
    s3, e3 = find_section(text, "阻塞求助")
    sec3 = text.splitlines()[s3:e3] if s3 >= 0 else []
    skeleton_no_block = any(ln.strip() == "- 无阻塞" for ln in sec3)
    skeleton_blocked = any("⚠️" in ln for ln in sec3)
    all_overdue = dept_overdue + corps_overdue
    conflict = (skeleton_no_block and all_overdue) or (skeleton_blocked and not all_overdue)

    state = "OK"
    notes = ["三源齐备且勾稽一致"]
    if daily is None:
        state = worst(state, "INCONCLUSIVE")
        notes.append(f"复盘卡链缺失（daily_review/{day}/ 无 _summary.json），如实缺据")
    if doc is None:
        state = worst(state, "INCONCLUSIVE")
        notes.append("tasks.json 不可读，如实缺据")
    if conflict:
        state = "DEGRADED"
        note = ("第③段「无阻塞」与 tasks.json 超期单 %d>0 冲突" if skeleton_no_block and all_overdue
                else "第③段列有超期阻塞而 tasks.json 当次实算超期单为 0")
        notes.append(note + "——双列原文数据不取一舍一")

    L: list[str] = []
    # decisions：事实性勾稽结论，逐条带出处锚
    L.append(f"- decisions:（草稿 {state}，待部务会确认）")
    dec_items: list[str] = []
    for w in dept_warns + corps_warns:
        tag = "" if w in dept_warns else "（军团级：desc 无 [AGENT] 归属标记）"
        dec_items.append(f"  - {w}——待确认{tag}（出处：.worktrees/tasks.json WARN）")
    for tid, c in dept_overdue + corps_overdue:
        dec_items.append(f"  - {tid} doing 超 72h（created={c}）待确认"
                         f"（出处：.worktrees/tasks.json，超期判据同 legion_meeting 72h）")
    for ln in observe:
        dec_items.append(f"  - {ln}（出处：agent_platform/data/shadow/monday_checkpoint.log {day} observe 行）")
    if not dec_items:
        dec_items.append("  - 当日无账本素材，非缺勤（INCONCLUSIVE）")
        state = worst(state, "INCONCLUSIVE")
        notes.append("当日零 decisions 素材，如实缺据")
    if conflict:
        sec3_q = "；".join(ln.strip() for ln in sec3 if ln.strip().startswith("-")) or "（③段空）"
        od_q = "、".join(tid for tid, _ in all_overdue) or "0 单"
        dec_items.append(f"  - 冲突双列：骨架③「{sec3_q}」 vs tasks.json 超期单 {od_q}"
                         f"（出处：agent_platform/data/meetings/{fname} ③段 + .worktrees/tasks.json）")
    L.extend(dec_items)

    # lessons：候选教训句，行尾固定标记；素材仅限四问卡③缩引/no_task 事实/WARN 模式归纳
    L.append(f"- lessons:（草稿 {state}，待部务会确认）")
    les_items: list[str] = []
    if daily is not None:
        for a in members:
            q3 = card_q3(daily["base"], a)
            if q3:
                les_items.append(f"  - {a} 复盘卡③「{q3}」{LESSON_MARKER}"
                                 f"（源：agent_platform/data/daily_review/{day}/{a}.md ③）")
        if dept_no_task:
            les_items.append(f"  - {_dept_of(fname)} 当日无任务 {len(dept_no_task)} 人："
                             f"{'、'.join(dept_no_task)}{LESSON_MARKER}"
                             f"（源：agent_platform/data/daily_review/{day}/_summary.json "
                             f"depts.{_dept_of(fname)}.no_task）")
    kinds = _classify_warns(material_warns)
    if kinds["空壳"]:
        les_items.append(f"  - 空壳完工单当日 {len(kinds['空壳'])} 单（{'、'.join(kinds['空壳'])}）"
                         f"——validate_json 判据原文「需人工确认非洗白」{LESSON_MARKER}"
                         f"（源：.worktrees/tasks.json WARN 空壳 {len(kinds['空壳'])} 单）")
    if kinds["created"]:
        les_items.append(f"  - created 手写近似时间不可排序当日 {len(kinds['created'])} 单"
                         f"（{'、'.join(kinds['created'])}）——validate_json 判据原文「状态机不依赖但不可排序」"
                         f"{LESSON_MARKER}（源：.worktrees/tasks.json WARN created {len(kinds['created'])} 单）")
    if kinds["merged"]:
        les_items.append(f"  - merged 字段不可识别当日 {len(kinds['merged'])} 单（{'、'.join(kinds['merged'])}）"
                         f"——validate_json 判据原文「须以 YYYY-MM-DD 开头」{LESSON_MARKER}"
                         f"（源：.worktrees/tasks.json WARN merged {len(kinds['merged'])} 单）")
    if kinds["ID形态"]:
        les_items.append(f"  - 工单 ID 形态可疑当日 {len(kinds['ID形态'])} 单（{'、'.join(kinds['ID形态'])}）"
                         f"——validate_json 判据原文「疑似 CLI 参数被当工单 ID」{LESSON_MARKER}"
                         f"（源：.worktrees/tasks.json WARN ID形态 {len(kinds['ID形态'])} 单）")
    if not les_items:
        les_items.append(f"  - 当日无账本素材，非缺勤{LESSON_MARKER}（INCONCLUSIVE）")
        state = worst(state, "INCONCLUSIVE")
        notes.append("当日零 lessons 素材，如实缺据")
    L.extend(les_items)

    if doc is not None:
        meta = (f"tasks.json（.worktrees/tasks.json，{anchor} 实算，"
                f"{len(doc)} 单/ERROR {len(errors)}/WARN {len(warns)}，"
                "ERROR 单按施工单开放问题3不入素材面）")
    else:
        meta = "tasks.json（.worktrees/tasks.json，真源缺失）"
    L.append(f"- 草稿三态：{state}（{'; '.join(notes)}；素材时点={anchor}）")
    L.append(f"- 出处锚：{meta}｜复盘卡链：agent_platform/data/daily_review/{day}/"
             f"｜骨架：agent_platform/data/meetings/{fname}")
    return state, "\n".join(L) + f"｜schema: {SCHEMA_VERSION}"


def _dept_of(fname: str) -> str:
    m = re.match(r"\d{4}-\d{2}-\d{2}_(.+)_晨会\.md", fname)
    return m.group(1) if m else fname


def backfill_file(path: Path, day: str, daily: dict | None, doc: dict | None,
                  errors: list, warns: list, overdue: list, observe: list,
                  anchor: str) -> tuple[str, str]:
    """对单份纪要回填第④段。返回 (state, 新全文)。占位不在=已回填/已人填，原样返回。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    s, e = find_section(text, SECTION4_TITLE)
    if s < 0:
        return "SKIP", text
    seg = lines[s:e]
    try:
        i_dec = next(i for i, ln in enumerate(seg) if ln.startswith("- decisions:（待填）"))
        i_les = next(i for i, ln in enumerate(seg) if ln.startswith("- lessons:（待填"))
    except StopIteration:
        return "SKIP", text  # 已回填或已人填：幂等跳过，不覆盖（回滚面=git revert）
    state, block = build_draft(text, path.name, day, daily, doc, errors, warns,
                               overdue, observe, anchor)
    new_seg = seg[:i_dec] + block.splitlines() + seg[i_les + 1:]
    new_text = "\n".join(lines[:s] + new_seg + lines[e:])
    if text.endswith("\n"):
        new_text += "\n"
    return state, new_text


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="晨会纪要第④段回填器（账本投影，零自动定稿）")
    ap.add_argument("--date", help="离线补跑日期（YYYY-MM-DD）；缺省=今日（daily 17:55 挂载口径）")
    ap.add_argument("--out-dir", help="草稿预览目录（整文副本，meetings 零写）；缺省=回填纪要本体")
    args = ap.parse_args(argv)

    if os.environ.get("LEGION_MEETING_BACKFILL_OFF") == "1":
        print("[skip] 回填器停用开关生效（LEGION_MEETING_BACKFILL_OFF=1）")
        return 0

    root = repo_root()
    ap = ap_root()
    if args.date:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", args.date):
            raise SystemExit(f"--date 格式非法：{args.date}（应为 YYYY-MM-DD）")
        day_s = args.date
        day_d = date.fromisoformat(day_s)
    else:
        now = datetime.now()
        day_s = now.strftime("%Y-%m-%d")
        day_d = now.date()
    anchor = datetime.now().isoformat(sep=" ", timespec="seconds")

    meet_dir = ap / "data" / "meetings"
    files = sorted(meet_dir.glob(f"{day_s}_*_晨会.md"))
    if not files:
        print(f"[skip] {day_s} 无晨会骨架（09:00 引擎未产出），自跳过不写文件")
        return 0

    daily = load_daily(ap, day_s)
    doc, errors, warns = load_tasks(root)
    overdue = overdue_tasks(doc, time.mktime(time.strptime(f"{day_s} 17:55:00",
                                                           "%Y-%m-%d %H:%M:%S"))) if doc else []
    observe = load_observe_lines(root, day_d)

    out_dir = Path(args.out_dir) if args.out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    tally: dict[str, int] = {}
    for p in files:
        state, new_text = backfill_file(p, day_s, daily, doc, errors, warns,
                                        overdue, observe, anchor)
        if state == "SKIP":
            print(f"[skip] {p.name} 第④段占位不在（已回填或已人填），幂等跳过")
            continue
        if out_dir:
            (out_dir / p.name).write_text(new_text, encoding="utf-8")
            print(f"[draft] {p.name} -> {out_dir / p.name}（态 {state}，meetings 零写）")
        else:
            p.write_text(new_text, encoding="utf-8")
            print(f"[ok] {p.name} 第④段已回填（态 {state}）")
        tally[state] = tally.get(state, 0) + 1
    print(f"[done] {day_s}：{dict(tally) or '零回填'}（素材时点 {anchor}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
