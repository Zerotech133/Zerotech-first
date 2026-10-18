# -*- coding: utf-8 -*-
"""周一观察点门判只读投影（驾驶舱·复盘舱数据面二期）。

数据源勘察实况：引擎侧 monday_checkpoint 巡检脚本把每次门判以文本块
追加写进 agent_platform/data/shadow/monday_checkpoint.log，块结构：

    ========================================================
    M5 刀4 周一观察点护航巡检 2026-10-17 23:16:35
    ========================================================
    [PASS] replay_trace 锚点回放（bc6d91172b0a） —— verdict=OK 命中=2 缺环=无
    [SKIP] schema_version/dk/degraded 三项（待首跑后验收）
    ...
    --------------------------------------------------------
    结论: 7/7 PASS——观察点门全过
    EXIT=0

本模块把该日志解析投影为 GET /api/gate 载荷（主会话负责挂载）：
latest（最近一次门判的 11 项门判明细）+ history（≤10 次走势）。

红线（沿用 api_tickets 判例）：
- **纯只读 + fail-visible**：日志缺失/空文件不报错——available:false +
  reason「周一 17:25 门判后自动点亮」（周日无产物是预期路径）；
- 不 import 引擎链，直接读日志文件面；
- 不提供任何写路径。
"""

import re
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: 周一门判日志（append-only 文本块）
GATE_LOG = _REPO_ROOT / "agent_platform" / "data" / "shadow" / "monday_checkpoint.log"

#: 门判标题行（块起始），如「M5 刀4 周一观察点护航巡检 2026-10-17 23:16:35」
_HEADER_RE = re.compile(
    r"^M5 刀4 周一观察点护航巡检\s+(\d{4}-\d{2}-\d{2})[ T]\d{2}:\d{2}:\d{2}")

#: 门判项行，如「[PASS] replay_trace 锚点回放（bc6d91172b0a） —— verdict=OK ...」
_ITEM_RE = re.compile(r"^\[(PASS|FAIL|SKIP)\]\s*(.+)$")

#: 结论行「结论: 7/7 PASS——观察点门全过」
_CONCLUSION_RE = re.compile(r"^结论:\s*(\d+)/(\d+)\s*PASS")

#: 退出码行「EXIT=0」
_EXIT_RE = re.compile(r"^EXIT=(\d+)")

#: 项名与明细的分隔（全角破折号对）
_DETAIL_SEP = "——"

#: history 条数上限
HISTORY_LIMIT = 10

#: 无产物时的预期 reason（周一门判前是常态）
PENDING_REASON = "周一 17:25 门判后自动点亮"


def _item_key(name, idx, seen):
    """从项名提炼稳定 key：取首个 ASCII 词元（replay_trace / AgentChiefPush），
    纯中文名退化为 item{i}；重复 key 追加序号去重。"""
    m = re.search(r"[A-Za-z][A-Za-z0-9_]*", name)
    key = m.group(0) if m else "item%d" % idx
    while key in seen:  # 同块内 key 冲突（同名门）→ 加序号
        seen[key] += 1
        key = "%s_%d" % (key, seen[key])
    seen[key] = seen.get(key, 1)
    return key


def _parse_block(text):
    """解析单次门判块 → {"date","verdict","items"}；无有效项返回 None。"""
    header = None
    items = []
    conclusion = None
    exit_code = None
    seen = {}
    for ln in text.splitlines():
        ln = ln.strip()
        if header is None:
            m = _HEADER_RE.match(ln)
            if m:
                header = m.group(1)
            continue
        m = _ITEM_RE.match(ln)
        if m:
            tag, rest = m.group(1), m.group(2)
            if _DETAIL_SEP in rest:
                name, detail = rest.split(_DETAIL_SEP, 1)
                name, detail = name.strip(), detail.strip()
            else:
                name, detail = rest.strip(), ""
            ok = tag != "FAIL"
            if tag != "PASS":  # SKIP/FAIL 的落选标签保留进 detail，卡面可见
                detail = ("[%s] %s" % (tag, detail)).strip()
            items.append({"key": _item_key(name, len(items) + 1, seen),
                          "name": name, "ok": ok, "detail": detail})
            continue
        m = _CONCLUSION_RE.match(ln)
        if m:
            conclusion = (int(m.group(1)), int(m.group(2)))
            continue
        m = _EXIT_RE.match(ln)
        if m:
            exit_code = int(m.group(1))
    if not items:
        return None
    # verdict：EXIT 优先 → 结论 x/y → 项级 FAIL 兜底
    if exit_code is not None:
        verdict = "pass" if exit_code == 0 else "fail"
    elif conclusion is not None:
        verdict = "pass" if conclusion[0] == conclusion[1] else "fail"
    else:
        verdict = "fail" if any(not i["ok"] for i in items) else "pass"
    return {"date": header, "verdict": verdict, "items": items}


def _parse_log(path):
    """整份日志 → 门判块列表（旧→新）。"""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    starts = []  # 每个标题行在全文中的行号
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if _HEADER_RE.match(ln.strip()):
            starts.append(i)
    blocks = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        blk = _parse_block("\n".join(lines[start:end]))
        if blk:
            blocks.append(blk)
    return blocks


def load_gate(log_path=None):
    """读周一门判日志，返回门判投影 dict（只读 + fail-visible）。

    契约字段（前端依赖，名不可改）：
    ok / available / reason? / generated_at / latest{date, verdict, items[]} /
    history[{date, verdict}]（≤10 条，新在前）
    """
    path = Path(log_path) if log_path else GATE_LOG
    if not path.is_file():
        return {
            "ok": True,
            "available": False,
            "reason": PENDING_REASON,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "latest": None,
            "history": [],
        }
    try:
        blocks = _parse_log(path)
    except OSError as e:
        return {
            "ok": False,
            "available": False,
            "reason": "门判日志读取失败: %s" % e,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "latest": None,
            "history": [],
        }
    if not blocks:
        return {
            "ok": True,
            "available": False,
            "reason": "门判日志暂无有效门判块",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "latest": None,
            "history": [],
        }
    latest = blocks[-1]  # 日志 append-only，末块即最新
    history = [{"date": b["date"], "verdict": b["verdict"]}
               for b in reversed(blocks[-HISTORY_LIMIT:])]  # 新在前
    return {
        "ok": True,
        "available": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "latest": latest,
        "history": history,
    }
