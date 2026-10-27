# -*- coding: utf-8 -*-
"""W42 晨会纪要第④段（决议与教训）回填器 —— 拍板执行单 #10 方案B：独立脚本，错峰 17:55（A0A 17:50 复盘链尾之后），本脚本自身不注册任何计划任务（注册须另行签准）。

口径总纲（继承 docs/legion/2026-10-19_W39晨会纪要回填机制预研施工单.md，编号已终裁改 W42）：
- 草稿 = 账本投影：全部内容为真实账本的纯勾稽转抄，每一条带出处锚（账本路径 + 工单 ID/日期锚）；
  账本里没有的结论不写、不估算、不代拟教训。
- 零自动定稿：产出永远是草稿——decisions 不因回填而视为已决议，lessons 不因回填而入知识环；
  入环唯一通道是部务会拍板（knowledge 环入口本脚本零触碰）。
- 段级三态：OK（三源齐备且勾稽一致）/ INCONCLUSIVE（部门级零素材或缺据，空集显式声明，非缺勤）/
  DEGRADED（多源勾稽冲突，双列原文数据不取一舍一）。
- 文件级处置三态：待填（本次回填）/ 已回填（检测到草稿头行，幂等跳过）/ 失源（账本源不可读，
  保留「（待填）」占位原状不动——closed on failure：回填失败绝不阻断骨架，占位逻辑永不删除）。
- 禁创作语汇 grep 机审（继承 W36 口径）：BANNED_PHRASES 命中的条目一律拒载（拒载行不复述命中词，
  保证全文 grep 零命中），只留拒载标记 + 出处锚。

输入（全部只读）：
  1. 工单账本 .worktrees/tasks.json —— WARN 判据唯一权威 = tools/validate_json.check_tasks_doc 纯函数
     （本脚本只复用不重写）；超期判据 = status=doing 且 created 超 72h（与 tools/legion_meeting.py L127-136 同源）；
  2. 当日复盘卡链 data/daily_review/<date>/ —— _summary.json（名单口径继承 2467cfeb：取 _summary.json，
     禁用活体 org.json）+ 个人四问卡③「我的进化」+ _corps_daily.md 催办名单；
  3. 当日晨会骨架 data/meetings/<date>_<dept>_晨会.md —— 第④段按「决议与教训」标题定位（防段号漂移：
     academy 部门为第五段），只替换「（待填）」占位两行。

写对象：仅上述骨架文件的两行占位（且须显式 --write / write=True 才落盘；默认 dry-run 零写入）。
回滚方式：meetings/ 已入 git，单文件 revert 即回到「（待填）」原状。
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from validate_json import check_tasks_doc  # noqa: E402  WARN 判据唯一权威，禁重写

# 禁创作语汇清单（grep 机审口径继承 W36 施工单）。作用对象=生成的草稿全文；
# 命中即拒载，拒载行不复述命中词，保证草稿全文 grep 本清单零命中。
BANNED_PHRASES = ("建议", "预计", "据悉", "约", "或因", "可能因")

CANDIDATE_MARK = "（候选，部务会拍板后入知识环）"
ANCHOR_MARK = "（源："
REJECT_MARK = "[拒载]"
OVERDUE_SECONDS = 72 * 3600

SECTION_TITLE_RE = re.compile(r"^## .*决议与教训.*$")
DRAFT_LINE_RE = re.compile(
    r"^- decisions:（草稿 (OK|INCONCLUSIVE|DEGRADED)），待部务会确认"
)
PLACEHOLDER_DECISIONS = "- decisions:（待填）"
PLACEHOLDER_LESSONS_PREFIX = "- lessons:（待填"
MEMBER_LINE_RE = re.compile(r"^- .+?（([A-Za-z][A-Za-z0-9_]{1,15})）：")
AGENT_TAG_RE = re.compile(r"\[([A-Za-z][A-Za-z0-9_]{1,15})\]")
CARD_Q_RE = re.compile(r"^[*＊]*\s*③")

_CREATED_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def _parse_created(value):
    """created 时间戳解析（tasks.json 三代格式并存，见 validate_json _TS_RE 注释）。"""
    s = str(value)[:19].strip()
    for fmt in _CREATED_FORMATS:
        try:
            return time.mktime(time.strptime(s, fmt))
        except ValueError:
            continue
    return None


def load_tasks(tasks_path: Path):
    """读工单账本。返回 (tasks_dict, error)；error 非 None 即失源。"""
    try:
        obj = json.loads(tasks_path.read_text(encoding="utf-8"))
    except Exception as exc:  # 失源：读不了 / JSON 损坏
        return None, "tasks.json 不可读：%s" % exc
    if not isinstance(obj, dict):
        return None, "tasks.json 顶层不是 dict"
    return obj, None


def find_overdue(tasks: dict, now: float) -> list:
    """超期 doing 单（与 legion_meeting.py L127-136 同源判据：doing 且 created 超 72h）。

    created 不可解析的工单无法判超期（已由 check_tasks_doc 出 WARN），此处跳过。
    """
    rows = []
    for tid, t in tasks.items():
        if not isinstance(t, dict) or t.get("status") != "doing":
            continue
        ts = _parse_created(t.get("created"))
        if ts is not None and now - ts > OVERDUE_SECONDS:
            rows.append((tid, t, str(t.get("created", ""))))
    return rows


def _task_blob(t: dict) -> str:
    return " ".join(
        str(t.get(k, "")) for k in ("desc", "title", "note", "plan", "owner")
    )


def attribute_dept(t: dict, members: set):
    """工单→部门归属：desc/title/note/plan/owner 中 [AGENT] 标签命中该部门成员即部门级；
    否则为军团级（未归属部门，条目如实标注）。"""
    for tag in AGENT_TAG_RE.findall(_task_blob(t)):
        if tag in members:
            return tag
    return None


def parse_skeleton(text: str) -> dict:
    """解析晨会骨架：成员名单（第①段行内全角括号里的 ID）、§三是否「无阻塞」、
    决议与教训段占位状态。按标题定位，不依赖段序号。"""
    members = []
    no_block = False
    section_idx = None
    placeholder = False
    backfilled = False
    for i, ln in enumerate(text.splitlines()):
        if SECTION_TITLE_RE.match(ln):
            section_idx = i
            continue
        if section_idx is None:
            m = MEMBER_LINE_RE.match(ln)
            if m and m.group(1) not in members:
                members.append(m.group(1))
            if ln.strip() == "- 无阻塞":
                no_block = True
        else:
            if ln.strip() == PLACEHOLDER_DECISIONS:
                placeholder = True
            elif ln.startswith(PLACEHOLDER_LESSONS_PREFIX):
                placeholder = True
            elif DRAFT_LINE_RE.match(ln):
                backfilled = True
    return {
        "members": members,
        "no_block": no_block,
        "section_idx": section_idx,
        "placeholder": placeholder,
        "backfilled": backfilled,
    }


def parse_cards(review_dir: Path, members: list) -> dict:
    """个人四问卡③「我的进化」行（解析口径同 legion_meeting latest_reviews：
    行含 ③，按全角冒号切一次）。缺卡/缺行如实跳过。"""
    out = {}
    for aid in members:
        p = review_dir / ("%s.md" % aid)
        if not p.exists():
            continue
        for ln in p.read_text(encoding="utf-8").splitlines():
            if CARD_Q_RE.match(ln) and "：" in ln:
                out[aid] = ln.split("：", 1)[-1].strip()
                break
    return out


def parse_corps_daily(path: Path) -> dict:
    """_corps_daily.md 催办名单：## <dept>（<lead>）小节内「催办 X、Y明日领单」。"""
    out = {}
    dept = None
    for ln in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^## (\S+?)（", ln)
        if m:
            dept = m.group(1)
            continue
        if dept and "催办" in ln:
            seg = ln.split("催办", 1)[-1]
            seg = seg.split("明日领单", 1)[0].strip(" ：:、 ")
            if seg:
                out[dept] = [x.strip() for x in seg.split("、") if x.strip()]
    return out


def _banned_hit(text: str):
    for w in BANNED_PHRASES:
        if w in text:
            return w
    return None


def _entry(content: str, anchor: str) -> str:
    """decisions 条目组装：禁语汇机审 → 命中拒载（拒载行不复述命中词、保锚）。"""
    if _banned_hit(content):
        return "  - %s 条目文案命中禁创作语汇清单，不载入草稿，请查账本原文%s%s）" % (
            REJECT_MARK,
            ANCHOR_MARK,
            anchor,
        )
    return "  - %s%s%s）" % (content, ANCHOR_MARK, anchor)


def _lesson(content: str, anchor: str) -> str:
    """lessons 条目：行尾固定「（候选，部务会拍板后入知识环）」（零自动定稿红线，
    grep 机审 100% 命中）；禁语汇命中同样拒载。"""
    if _banned_hit(content):
        return "  - %s 条目文案命中禁创作语汇清单，不载入草稿，请查账本原文%s%s）%s" % (
            REJECT_MARK,
            ANCHOR_MARK,
            anchor,
            CANDIDATE_MARK,
        )
    return "  - %s%s%s）%s" % (content, ANCHOR_MARK, anchor, CANDIDATE_MARK)


def _clamp44(text: str) -> str:
    """四问卡③缩引 ≤44 字符（legion_meeting.py L107 同款截断口径）。"""
    return text[:44]


def build_section(
    dept: str,
    meet_file: str,
    skel: dict,
    tasks: dict,
    warns: list,
    overdue: list,
    summary,
    review_dir: Path,
    cards: dict,
    corps: dict,
    review_rel: str,
    tasks_rel: str,
    now_ts: str,
):
    """生成第④段替换块。返回 (state, lines)。"""
    members = set(skel["members"])
    no_task = (
        set(summary.get("depts", {}).get(dept, {}).get("no_task", []))
        if summary
        else set()
    )
    duties = [m for m in skel["members"] if m not in no_task]

    warn_rows = []
    for w in warns:
        tid = w.split(" ")[0]
        t = tasks.get(tid)
        tag = attribute_dept(t, members) if isinstance(t, dict) else None
        warn_rows.append((tid, w, tag))
    overdue_rows = [
        (tid, created, attribute_dept(t, members)) for tid, t, created in overdue
    ]

    dept_warns = [r for r in warn_rows if r[2]]
    dept_overdue = [r for r in overdue_rows if r[2]]

    conflicts = []
    # 勾稽①：骨架§三「无阻塞」 vs 部门级超期 doing —— 冲突即 DEGRADED，双列原文
    if skel["no_block"] and dept_overdue:
        det = "；".join(
            "%s（created=%s，[%s]）" % (tid, created, tag)
            for tid, created, tag in dept_overdue
        )
        conflicts.append(
            _entry(
                "[DEGRADED 勾稽冲突] 骨架§三原文「- 无阻塞」 vs tasks.json 部门级超期 doing：%s"
                "——双列原文数据不取一舍一，待部务会裁决" % det,
                "%s §三｜%s" % (meet_file, tasks_rel),
            )
        )
    # 勾稽②：_summary.json n vs 个人卡文件数（分母口径：个人卡数，2467cfeb）
    card_cnt = (
        len([p for p in review_dir.glob("*.md") if not p.name.startswith("_")])
        if summary is not None
        else None
    )
    if summary is not None and card_cnt is not None and summary.get("n") != card_cnt:
        conflicts.append(
            _entry(
                "[DEGRADED 勾稽冲突] _summary.json n=%s vs 个人卡文件数 %d"
                "——双列原文数据不取一舍一，待部务会裁决" % (summary.get("n"), card_cnt),
                "%s/_summary.json｜%s/ 个人卡" % (review_rel, review_rel),
            )
        )

    d_lines = []
    for tid, w, tag in dept_warns:
        d_lines.append(
            _entry("%s 待确认（部门级，[%s]）" % (w, tag), "%s WARN" % tasks_rel)
        )
    for tid, w, tag in [r for r in warn_rows if not r[2]]:
        d_lines.append(
            _entry("%s 待确认（军团级，未归属部门）" % w, "%s WARN" % tasks_rel)
        )
    for tid, created, tag in dept_overdue:
        d_lines.append(
            _entry(
                "%s doing 超72h（created=%s，[%s]）待确认" % (tid, created, tag),
                tasks_rel,
            )
        )

    l_lines = []
    for aid in duties:
        q3 = cards.get(aid)
        if q3:
            l_lines.append(
                _lesson(
                    "四问卡③[%s]：%s" % (aid, _clamp44(q3)),
                    "%s/%s.md ③" % (review_rel, aid),
                )
            )
    urge = corps.get(dept, [])
    if no_task:
        names = "、".join(sorted(no_task))
        if urge:
            l_lines.append(
                _lesson(
                    "%s 当日 no_task %d 人（%s）——催办 %s 明日领单"
                    % (dept, len(no_task), names, "、".join(urge)),
                    "%s/_summary.json no_task｜%s/_corps_daily.md 催办名单"
                    % (review_rel, review_rel),
                )
            )
        else:
            l_lines.append(
                _lesson(
                    "%s 当日 no_task %d 人（%s）" % (dept, len(no_task), names),
                    "%s/_summary.json no_task" % review_rel,
                )
            )
    # WARN 模式归纳：只按 WARN 文案分组计数纯转抄（去掉逐单变量部分），不加判语
    groups = {}
    for tid, w, tag in warn_rows:
        body = w.split(" ", 1)[1] if " " in w else w
        body = re.split(r"[（(]", body, maxsplit=1)[0].strip("—- ")
        body = re.sub(r"created=\S+", "created=<值>", body)
        groups.setdefault(body, []).append(tid)
    for body, tids in sorted(groups.items()):
        l_lines.append(
            _lesson(
                "账本 WARN 模式：「%s」×%d（%s）" % (body, len(tids), "、".join(tids)),
                "%s WARN×%d" % (tasks_rel, len(tids)),
            )
        )

    zero_material = not (dept_warns or dept_overdue or l_lines)
    if zero_material:
        d_lines.append(
            _entry(
                "当日无账本素材（INCONCLUSIVE），非缺勤",
                "%s depts.%s" % (review_rel, dept),
            )
        )
    if summary is None:
        d_lines.append(_entry("当日 _summary.json 缺失，复盘卡链缺据", review_rel))

    if conflicts:
        state = "DEGRADED"
    elif zero_material or summary is None:
        state = "INCONCLUSIVE"
    else:
        state = "OK"

    lines = []
    lines.append("- decisions:（草稿 %s），待部务会确认" % state)
    lines.extend(d_lines)
    lines.extend(conflicts)
    lines.append("- lessons:（草稿候选，待部务会拍板后入知识环）")
    lines.extend(l_lines)
    lines.append(
        "- 素材时点：%s（回填器当次实算）｜出处锚：%s｜复盘卡链（%s）｜骨架（%s）"
        % (now_ts, tasks_rel, review_rel, meet_file)
    )
    return state, lines


def backfill_meeting(
    path: Path,
    tasks: dict,
    warns: list,
    overdue: list,
    review_dir: Path,
    summary,
    corps: dict,
    now_ts: str,
    write: bool,
) -> dict:
    text = path.read_text(encoding="utf-8")
    skel = parse_skeleton(text)
    dept = path.stem.split("_", 1)[1].rsplit("_", 1)[0]
    if skel["backfilled"]:
        return {"dept": dept, "disposition": "已回填", "state": "-", "written": False}
    if not skel["placeholder"] or skel["section_idx"] is None:
        return {
            "dept": dept,
            "disposition": "无占位（跳过）",
            "state": "-",
            "written": False,
        }
    lines = text.splitlines()
    start = None
    end = None
    for i in range(skel["section_idx"] + 1, len(lines)):
        ln = lines[i].strip()
        if ln == PLACEHOLDER_DECISIONS or ln.startswith(PLACEHOLDER_LESSONS_PREFIX):
            if start is None:
                start = i
            end = i
        elif ln.startswith("- ") and start is not None:
            break
    if start is None:
        return {
            "dept": dept,
            "disposition": "无占位（跳过）",
            "state": "-",
            "written": False,
        }
    date = path.name.split("_", 1)[0]
    meet_rel = "data/meetings/%s" % path.name
    review_rel = "data/daily_review/%s" % date
    tasks_rel = ".worktrees/tasks.json"
    cards = parse_cards(review_dir, skel["members"])
    state, block = build_section(
        dept,
        meet_rel,
        skel,
        tasks,
        warns,
        overdue,
        summary,
        review_dir,
        cards,
        corps,
        review_rel,
        tasks_rel,
        now_ts,
    )
    new_lines = lines[:start] + block + lines[end + 1 :]
    written = False
    if write:
        path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        written = True
    return {
        "dept": dept,
        "disposition": "已回填" if write else "待填→草稿已生成（dry-run）",
        "state": state,
        "written": written,
    }


def run(date=None, *, root=None, now=None, write: bool = False) -> dict:
    """主入口。默认 dry-run（零写入）；write=True 才替换占位行。

    失源（tasks.json / 复盘卡链目录不可读）→ 全局失源报告、骨架原状不动；
    单文件回填异常 → 该文件降级保留「（待填）」占位（closed on failure，绝不阻断其他文件）。
    """
    root = Path(root) if root else ROOT
    date = date or time.strftime("%Y-%m-%d")
    now = now if now is not None else time.time()
    now_ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    meet_dir = root / "agent_platform" / "data" / "meetings"
    review_dir = root / "agent_platform" / "data" / "daily_review" / date
    tasks_path = root / ".worktrees" / "tasks.json"

    tasks, err = load_tasks(tasks_path)
    if err:
        return {"date": date, "disposition": "失源", "detail": err, "files": {}}
    if not review_dir.exists():
        return {
            "date": date,
            "disposition": "失源",
            "detail": "复盘卡链目录缺失：%s" % review_dir,
            "files": {},
        }
    _errors, warns = check_tasks_doc(
        tasks
    )  # ERROR 单不出 decisions（施工单开放问题3：ERROR 走数据质量线）
    overdue = find_overdue(tasks, now)
    summary_path = review_dir / "_summary.json"
    summary = None
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            summary = None  # 缺据如实声明，段级 INCONCLUSIVE
    corps_path = review_dir / "_corps_daily.md"
    corps = parse_corps_daily(corps_path) if corps_path.exists() else {}

    report = {"date": date, "disposition": "OK", "files": {}}
    for path in sorted(meet_dir.glob("%s_*_晨会.md" % date)):
        try:
            res = backfill_meeting(
                path, tasks, warns, overdue, review_dir, summary, corps, now_ts, write
            )
        except Exception as exc:  # closed on failure：单文件失败降级保留占位
            res = {
                "dept": path.name,
                "disposition": "失源",
                "detail": "回填异常（骨架保留待填原状）：%s" % exc,
                "state": "-",
                "written": False,
            }
        report["files"][path.name] = res
    return report


def _self_test() -> int:
    """阳性对照自检（判例 6：空集≠通过，三态化 + 阳性对照）。零真实数据触碰。"""
    import tempfile

    ok = True

    def check(name, cond):
        nonlocal ok
        print(("PASS" if cond else "FAIL") + " self-test · " + name)
        ok = ok and bool(cond)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        now = time.mktime(time.strptime("2026-10-20 17:55:00", "%Y-%m-%d %H:%M:%S"))
        old = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 4 * 86400))
        tasks = {
            "K-TEST-001": {"status": "done", "created": "2026-10-19"},  # 空壳 WARN
            "K-TEST-002": {
                "status": "doing",
                "created": old,
                "desc": "超期单 [DEV7]",
            },  # 部门级超期 → 与§三「无阻塞」冲突
            "K-TEST-003": {
                "status": "done",
                "created": "20:1x",
            },  # created 不可解析 WARN
        }
        meet = root / "agent_platform" / "data" / "meetings"
        rev = root / "agent_platform" / "data" / "daily_review" / "2026-10-20"
        meet.mkdir(parents=True)
        rev.mkdir(parents=True)
        wt = root / ".worktrees"
        wt.mkdir(parents=True)
        (wt / "tasks.json").write_text(
            json.dumps(tasks, ensure_ascii=False), encoding="utf-8"
        )
        (rev / "_summary.json").write_text(
            json.dumps(
                {
                    "day": "2026-10-20",
                    "n": 2,
                    "no_task": [],
                    "depts": {"dev": {"n": 2, "no_task": []}},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (rev / "DEV7.md").write_text(
            "# DEV7 每日复盘 2026-10-20\n\n**③ 我的进化**：把重复动作固化为工具。\n",
            encoding="utf-8",
        )
        # 禁语汇阳性对照卡：③ 注入清单内语汇 → 该条目须被拒载（字面量经清单索引拼装，正文零出现）
        bad = "下阶段" + BANNED_PHRASES[1] + "一倍"
        (rev / "DEV8.md").write_text(
            "# DEV8 每日复盘 2026-10-20\n\n**③ 我的进化**：%s。\n" % bad,
            encoding="utf-8",
        )
        (rev / "_corps_daily.md").write_text(
            "## dev（ARCH1）\ndev日报：催办 DEV1明日领单。\n", encoding="utf-8"
        )
        skel_text = (
            "\n".join(
                [
                    "# 研发部 晨会 2026-10-20",
                    "## 一、昨日完成（读复盘卡）",
                    "- DEV7（DEV7）：✅ 有任务｜x",
                    "- DEV8（DEV8）：✅ 有任务｜x",
                    "## 三、阻塞求助（超期/依赖）",
                    "- 无阻塞",
                    "## 四、决议与教训（会后人填/提炼器自动回填）",
                    "- decisions:（待填）",
                    "- lessons:（待填——须可证伪可复用，部务会拍板后入知识环）",
                ]
            )
            + "\n"
        )
        (meet / "2026-10-20_dev_晨会.md").write_text(skel_text, encoding="utf-8")

        rep = run("2026-10-20", root=root, now=now, write=False)
        f = rep["files"]["2026-10-20_dev_晨会.md"]
        check("超期冲突判 DEGRADED", f["state"] == "DEGRADED")
        check("dry-run 零写入", not f["written"])
        check(
            "dry-run 骨架占位原状保留",
            "（待填）" in (meet / "2026-10-20_dev_晨会.md").read_text(encoding="utf-8"),
        )

        rep2 = run("2026-10-20", root=root, now=now, write=True)
        text = (meet / "2026-10-20_dev_晨会.md").read_text(encoding="utf-8")
        check("真写入后占位已替换", "（待填）" not in text)
        check("草稿三态头行在案", "草稿 DEGRADED），待部务会确认" in text)
        entries = [ln for ln in text.splitlines() if ln.startswith("  - ")]
        check(
            "出处锚覆盖率 100%",
            bool(entries) and all(ANCHOR_MARK in ln for ln in entries),
        )
        lidx = text.splitlines().index("- lessons:（草稿候选，待部务会拍板后入知识环）")
        lessons = [
            ln
            for i, ln in enumerate(text.splitlines())
            if i > lidx and ln.startswith("  - ")
        ]
        check(
            "lessons 行尾候选标记 100%",
            bool(lessons) and all(ln.endswith(CANDIDATE_MARK) for ln in lessons),
        )
        check("禁语汇阳性对照已拒载", any(REJECT_MARK in ln for ln in lessons))
        check(
            "草稿全文禁语汇 grep 零命中", not [w for w in BANNED_PHRASES if w in text]
        )
        check(
            "幂等：二次跑判已回填",
            rep2["files"]["2026-10-20_dev_晨会.md"]["disposition"] == "已回填",
        )

        # 失源对照：tasks.json 缺失 → 不动骨架
        root2 = Path(td) / "r2"
        m2 = root2 / "agent_platform" / "data" / "meetings"
        m2.mkdir(parents=True)
        (m2 / "2026-10-20_dev_晨会.md").write_text(skel_text, encoding="utf-8")
        rep3 = run("2026-10-20", root=root2, now=now)
        check("tasks.json 缺失判失源", rep3["disposition"] == "失源")
        check(
            "失源不动骨架",
            "（待填）" in (m2 / "2026-10-20_dev_晨会.md").read_text(encoding="utf-8"),
        )

        # INCONCLUSIVE 对照：源齐但零部门级素材
        (root2 / ".worktrees").mkdir()
        (root2 / ".worktrees" / "tasks.json").write_text("{}", encoding="utf-8")
        (root2 / "agent_platform" / "data" / "daily_review" / "2026-10-20").mkdir(
            parents=True
        )
        rep4 = run("2026-10-20", root=root2, now=now)
        check(
            "零素材判 INCONCLUSIVE",
            rep4["files"]["2026-10-20_dev_晨会.md"]["state"] == "INCONCLUSIVE",
        )

    print("self-test %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="W42 晨会纪要第④段回填器（方案B；草稿=账本投影，零自动定稿）"
    )
    ap.add_argument("--date", help="回填日期 YYYY-MM-DD（缺省当天）")
    ap.add_argument("--root", help="仓库根目录（缺省按脚本位置推断）")
    ap.add_argument(
        "--write", action="store_true", help="真写入（缺省 dry-run 零写入）"
    )
    ap.add_argument(
        "--self-test", action="store_true", help="阳性对照自检（离线夹具，零真实数据）"
    )
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    rep = run(args.date, root=Path(args.root) if args.root else None, write=args.write)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
