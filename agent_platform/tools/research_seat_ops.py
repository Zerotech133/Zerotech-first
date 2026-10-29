# -*- coding: utf-8 -*-
"""研究院研究值守双席（STRAT1 / BACK1）—— 策略假设值守 + 冻结/回测值守。

目的（WO-LEGION-ACAD1-P2）：
- STRAT1 策略研究员：周一 09:40 出「策略假设值守简报」——待补假设/八字段缺项/Spec 责任提醒，
  并 emit research_report（agent=task=STRAT1，供 seat_realness 审计维取证）；
- BACK1 回测研究员：周三 15:10 出「冻结/回测值守简报」——逐条检查回测登记的
  data_version/sample_in/sample_out/cost_model 四字段 + DATA1 冻结回执；
  四字段齐且回执在 → 明确报 "READY（可开跑回测）"，否则报 "WAITING_DATA1"；
- 周五 16:10 双席合并周报（weekly）：两席各 emit 一条 research_report。

冻结回执约定（DATA1 出）：
  agent_platform/data/academy/freeze_receipts/<topic_id>.json
  {
    "topic_id": "AC-20261017-LCS1",
    "data_version": "<源/版本/快照日期>",
    "sample_in": "<起止/池口径/n>",
    "sample_out": "<起止/池口径/n>",
    "cost_model": "<佣金/滑点/冲击参数与版本>",
    "sha256": "<数据快照指纹>",
    "frozen_at": "YYYY-MM-DDTHH:MM:SS+08:00",
    "by": "DATA1"
  }

纪律：
- 只读 research_topics.json / backtests.jsonl / freeze_receipts/，不写数据根、不回测、不交易；
- 输出只落 agent_platform/data/academy/ops/<seat>/，原子写；--dry-run 零写零 emit；
- emit 失败不阻塞（fail-open）；退出码 0 正常 / 2 自身错误。

用法：
  python -X utf8 agent_platform/tools/research_seat_ops.py strat1 [--root PATH] [--dry-run] [--json]
  python -X utf8 agent_platform/tools/research_seat_ops.py back1  [...]
  python -X utf8 agent_platform/tools/research_seat_ops.py weekly [...]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
import logging

HERE = Path(__file__).resolve()
ROOT_DEFAULT = HERE.parents[2]
ACADEMY_REL = Path("agent_platform") / "data" / "academy"
DISPATCH_REL = ACADEMY_REL / "dispatch_requests"
TASKS_REL = Path(".worktrees") / "tasks.json"

try:  # P0② trace（可选依赖，失败不阻断值守）
    from trace_cost import record as _trace_record
except Exception:  # noqa: BLE001
    _trace_record = None
FREEZE_FIELDS = ("data_version", "sample_in", "sample_out", "cost_model")
EIGHT_FIELDS = (
    ("question", "研究问题"),
    ("object", "研究对象"),
    ("hypothesis", "核心假设"),
    ("evidence_scope", "证据口径"),
    ("method", "验证方法"),
    ("conclusion", "结论等级"),
    ("action", "行动建议"),
    ("risks", "风险边界"),
)


def _today() -> str:
    return dt.date.today().isoformat()


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit("真源不可读: %s (%s)" % (path, type(exc).__name__))


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _safe_json(path: Path):
    """读 JSON，失败返回 None（派单跟踪只读，不因单文件坏而中断值守）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _dispatch_tracking(root: Path) -> list[dict]:
    """派单跟踪（只读）：读 dispatch_requests/*.json，对照 .worktrees/tasks.json 判定
    pending_dispatch→dispatched（建议单号命中，或需求单号出现在工单 desc 中）。"""
    req_dir = root / DISPATCH_REL
    if not req_dir.is_dir():
        return []
    tasks = _safe_json(root / TASKS_REL)
    if not isinstance(tasks, dict):
        tasks = {}
    out = []
    for f in sorted(req_dir.glob("*.json")):
        req = _safe_json(f)
        if not isinstance(req, dict):
            continue
        rid = str(req.get("request_id") or f.stem)
        suggested = str(req.get("suggested_ticket") or "")
        hit_id, hit_owner = "", ""
        for tid, t in tasks.items():
            desc = str((t or {}).get("desc") or "")
            if suggested and tid == suggested:
                hit_id, hit_owner = tid, str((t or {}).get("owner") or "")
                break
            if rid and rid in desc:
                hit_id, hit_owner = tid, str((t or {}).get("owner") or "")
                break
        out.append({
            "request_id": rid,
            "target": str(req.get("target") or ""),
            "suggested_ticket": suggested,
            "requirement_doc": str(req.get("requirement_doc") or ""),
            "created_at": str(req.get("created_at") or ""),
            "status": "dispatched" if hit_id else str(req.get("status") or "pending_dispatch"),
            "ticket": hit_id or str(req.get("dispatched_ticket") or ""),
            "owner": hit_owner or str(req.get("owner") or ""),
        })
    return out


def _overdue_doing(root: Path, hours: int = 24) -> list[dict]:
    """doing 超过 N 小时未完成 → 自动升级清单（只读 tasks.json，不写）。"""
    tasks = _safe_json(root / TASKS_REL)
    if not isinstance(tasks, dict):
        return []
    now = dt.datetime.now()
    out = []
    for tid, t in tasks.items():
        if not isinstance(t, dict) or t.get("status") != "doing":
            continue
        created = str(t.get("created") or "").strip()
        try:
            born = dt.datetime.strptime(created, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        age_h = (now - born).total_seconds() / 3600.0
        if age_h < hours:
            continue
        out.append({
            "tid": str(tid),
            "owner": str(t.get("owner") or ""),
            "age_h": round(age_h, 1),
            "desc": str(t.get("desc") or "")[:60],
        })
    return sorted(out, key=lambda x: x["age_h"], reverse=True)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _emit(root: Path, agent: str, detail: str, no_emit: bool = False) -> dict:
    if no_emit:
        return {"ok": False, "skipped": True}
    try:
        sys.path.insert(0, str(root / "tools"))
        import events  # noqa: WPS433

        return events.emit(
            kind="research_report", task=agent, agent=agent, detail=detail[:300]
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:100])}


def _missing_fields(topic: dict) -> list[str]:
    out = []
    for key, label in EIGHT_FIELDS:
        val = topic.get(key)
        if (
            isinstance(val, str)
            and val.strip()
            and val.strip() not in ("待验证", "待登记")
        ):
            continue
        out.append(label)
    return out


def _freeze_missing(bt: dict) -> list[str]:
    miss = []
    for key in FREEZE_FIELDS:
        val = str(bt.get(key) or "").strip()
        if not val or val.startswith("待"):
            miss.append(key)
    return miss


def _receipt(root: Path, topic_id: str) -> tuple[dict | None, list[str]]:
    """返回 (回执dict|None, 回执缺字段)。回执缺失=未冻结。"""
    path = root / ACADEMY_REL / "freeze_receipts" / ("%s.json" % topic_id)
    if not path.is_file():
        return None, list(FREEZE_FIELDS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, list(FREEZE_FIELDS)
    miss = [
        f
        for f in FREEZE_FIELDS
        if not str(data.get(f) or "").strip()
        or str(data.get(f) or "").strip().startswith("待")
    ]
    return data, miss


def _readiness(root: Path, bt: dict) -> dict:
    topic_id = str(bt.get("topic_id") or "?")
    fields_miss = _freeze_missing(bt)
    receipt, receipt_miss = _receipt(root, topic_id)
    ready = not fields_miss and receipt is not None and not receipt_miss
    return {
        "topic_id": topic_id,
        "fields_missing": fields_miss,
        "receipt": bool(receipt),
        "receipt_missing": receipt_miss,
        "ready": ready,
        "status": "READY" if ready else "WAITING_DATA1",
    }


def build_strat1(root: Path) -> tuple[str, dict]:
    topics = _read_json(root / ACADEMY_REL / "research_topics.json")
    topics = (
        [t for t in topics if isinstance(t, dict)] if isinstance(topics, list) else []
    )
    pipeline = [t for t in topics if str(t.get("status")) in ("intake", "hypothesis")]
    lines = [
        "# STRAT1 策略假设值守简报 %s" % _today(),
        "",
        "> 生成：agent_platform/tools/research_seat_ops.py strat1 ｜ 只读研究台账 ｜ 责任：STRAT1 徐庶",
        "",
        "## 一、待补假设的课题（intake/hypothesis）",
    ]
    if not pipeline:
        lines.append("- （暂无——所有课题已过 hypothesis 或已结题）")
    for t in pipeline:
        miss = _missing_fields(t)
        lines.append(
            "- **%s** %s ｜ 阶段=%s ｜ owner=%s ｜ 八字段缺项=%s"
            % (
                t.get("topic_id", "?"),
                t.get("title", "?"),
                t.get("status"),
                t.get("owner"),
                "、".join(miss) if miss else "无",
            )
        )
    lines += [
        "",
        "## 二、本周动作（STRAT1）",
        "1. 对每个 hypothesis 课题补齐八字段，产出可证伪假设与否定条件；",
        "2. 涉及 LCS-1：策略 Spec（图形算子/阈值/否定条件）待补，数据口径依赖 DATA1 冻结；",
        "3. 假设成型后交 ACAD1 评审，再交 BACK1 回测（阶段推进走 academy_wiring.py move）。",
        "",
        "## 三、输入依赖",
        "- 数据可用性：DATA1；回测执行：BACK1；评审：ACAD1。",
    ]
    summary = {
        "action": "strat1",
        "date": _today(),
        "topics": len(topics),
        "hypothesis_pipeline": [t.get("topic_id") for t in pipeline],
    }
    return "\n".join(lines) + "\n", summary


def build_back1(root: Path) -> tuple[str, dict]:
    bts = _read_jsonl(root / ACADEMY_REL / "backtests.jsonl")
    rows = [_readiness(root, b) for b in bts]
    lines = [
        "# BACK1 冻结/回测值守简报 %s" % _today(),
        "",
        "> 生成：agent_platform/tools/research_seat_ops.py back1 ｜ 只读回测登记 ｜ 责任：BACK1 陆逊",
        "",
        "## 一、冻结就绪检查（四字段 + DATA1 冻结回执）",
        "| 课题 | 状态 | data_version | sample_in | sample_out | cost_model | 回执 | 就绪 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    if not bts:
        lines.append("| （空） | - | - | - | - | - | - | WAITING_DATA1 |")
    by_topic = {str(b.get("topic_id") or "?"): b for b in bts}
    for r in rows:
        b = by_topic.get(r["topic_id"], {})
        lines.append(
            "| %s | %s | %s | %s | %s | %s | %s | %s |"
            % (
                r["topic_id"],
                b.get("status") or "?",
                b.get("data_version") or "（空）",
                b.get("sample_in") or "（空）",
                b.get("sample_out") or "（空）",
                b.get("cost_model") or "（空）",
                "有" if r["receipt"] else "缺失",
                r["status"],
            )
        )
    ready = [r for r in rows if r["ready"]]
    waiting = [r for r in rows if not r["ready"]]
    lines += [
        "",
        "- 冻结进度：**%d/%d 就绪**；就绪=%s；等待 DATA1=%s"
        % (
            len(ready),
            len(rows),
            "、".join(r["topic_id"] for r in ready) or "无",
            "、".join(r["topic_id"] for r in waiting) or "无",
        ),
    ]
    if waiting:
        lines += [
            "",
            "### DATA1 冻结回执模板（缺失时按此提交）",
            "```json",
            json.dumps(
                {
                    "topic_id": waiting[0]["topic_id"],
                    "data_version": "<源/版本/快照日期>",
                    "sample_in": "<起止/池口径/n>",
                    "sample_out": "<起止/池口径/n>",
                    "cost_model": "<佣金/滑点/冲击参数与版本>",
                    "sha256": "<数据快照指纹>",
                    "frozen_at": "YYYY-MM-DDTHH:MM:SS+08:00",
                    "by": "DATA1",
                },
                ensure_ascii=False,
                indent=1,
            ),
            "```",
            "路径：agent_platform/data/academy/freeze_receipts/<topic_id>.json",
        ]
    dispatch = _dispatch_tracking(root)
    if dispatch:
        lines += ["", "## 二、派单跟踪（研究前置，只读自动判定）",
                  "| 需求 | 受理方 | 建议单号 | 状态 | 工单 | 责任 |",
                  "|---|---|---|---|---|---|"]
        for d in dispatch:
            lines.append("| %s | %s | %s | %s | %s | %s |"
                         % (d["request_id"], d["target"] or "-",
                            d["suggested_ticket"] or "-", d["status"],
                            d["ticket"] or "-", d["owner"] or "-"))
        pend = [d for d in dispatch if d["status"] != "dispatched"]
        lines.append("- 派单进度：**pending=%d / dispatched=%d**（dispatched=建议单号命中或需求单号出现在工单 desc）"
                     % (len(pend), len(dispatch) - len(pend)))
        if pend:
            lines.append("- 催办：%s 未派单 → 请 ARCH1 按研发路由建单并回填 §9/%s"
                         % ("、".join(d["request_id"] for d in pend),
                            "dispatch_requests"))
    if ready:
        lines += [
            "",
            "## 三、可开跑（四字段齐 + 回执在）",
            "命令：python -X utf8 quant_backtest/backtest_engine_v2.py（按任务单参数执行）",
            "跑完登记 backtests.jsonl 的 status/go_no_go，并交 ACAD1 评审。",
        ]
    else:
        lines += [
            "",
            "## 三、结论",
            "- **WAITING_DATA1**：数据未冻结，BACK1 拒跑回测（研究纪律：无冻结不回测）。",
        ]
    overdue = _overdue_doing(root)
    if overdue:
        lines += [
            "",
            "## 四、超期 doing（>24h，自动升级 A0/ARCH1）",
            "| 工单 | owner | 停留小时 | 摘要 |",
            "|---|---|---:|---|",
        ]
        for o in overdue[:10]:
            lines.append("| %s | %s | %.1f | %s |"
                         % (o["tid"], o["owner"] or "-", o["age_h"], o["desc"]))
        lines.append("- **ESCALATE**：%d 单 doing 超 24h，需 A0/ARCH1 今日处置（关单/改派/缩范围）。"
                     % len(overdue))
    summary = {
        "action": "back1",
        "date": _today(),
        "backtests": len(bts),
        "ready": [r["topic_id"] for r in ready],
        "waiting": [r["topic_id"] for r in waiting],
        "dispatch": dispatch,
        "overdue_doing": overdue,
    }
    return "\n".join(lines) + "\n", summary



SEAT_META = {
    "comm1": ("COMM1", "马良", "商业化研究员", "commercialization_lab",
              ["商业模式卡/客户场景/交付边界/定价与试点", "不把概念包装成收入结论"]),
    "intel1": ("INTEL1", "郭嘉", "竞争情报研究员", "market_intelligence_lab",
               ["情报简报/竞品矩阵/来源分级/影响分析", "未核验消息只作线索"]),
    "ai1": ("AI1", "诸葛亮", "AI方法论研究员", "data_ai_lab",
            ["方法论提炼/评测设计/Prompt提案/路由提案/知识入库", "一次成功不升级通用规则"]),
    "prod1": ("PROD1", "产品经理", "策略产品经理", "commercialization_lab",
              ["产品定义/验收标准/试点设计/跨部门交付/产品指标", "不通过不签"]),
}


def build_research_seat(root: Path, seat: str) -> tuple[str, dict]:
    """4 席通用研究值守简报：只读研究台账，不编造课题/数据。"""
    aid, cn, role, direction, duties = SEAT_META[seat]
    topics = _read_json(root / ACADEMY_REL / "research_topics.json") or []
    if not isinstance(topics, list):
        topics = []
    relevant = [t for t in topics if str(t.get("direction") or "") == direction]
    active = [t for t in topics if str(t.get("status") or "") not in ("closed", "decision")]
    lines = [
        "# %s %s 研究值守简报 %s" % (aid, cn, _today()),
        "",
        "> 生成：agent_platform/tools/research_seat_ops.py %s ｜ 只读研究台账 ｜ 责任：%s %s" % (seat, aid, role),
        "",
        "## 一、本院课题",
        "- 在册课题：%d（其中本方向 %d）" % (len(topics), len(relevant)),
        "- 未终态课题：%d" % len(active),
    ]
    if relevant:
        for t in relevant[:5]:
            lines.append("- %s｜%s｜%s" % (t.get("topic_id"), t.get("title"), t.get("status")))
    else:
        lines.append("- 本方向暂无在册课题；可向 ACAD1 提议立项（八字段 + 可证伪假设）。")
    lines += [
        "",
        "## 二、本席职责动作",
        "- " + duties[0],
        "- " + duties[1],
        "- 产出落 deliverables/ 或 agent_platform/data/academy/；阶段推进走 academy_wiring.py（不得跳级）。",
        "",
        "## 三、依赖与下一步",
        "- 数据口径：DATA1；评审：ACAD1；联合评审：D1；裁决：A0/老田。",
        "- 当前无独立计划任务：触发走 SeatsWeekly（周五 16:10）；独立日/周任务需另批 L2。",
        "- 无数据冻结/验收证据时不得输出 Go 或产品化结论。",
    ]
    summary = {"action": seat, "date": _today(), "topics": len(topics),
               "relevant": [t.get("topic_id") for t in relevant], "active": len(active),
               "trigger": "SeatsWeekly"}
    return "\n".join(lines) + "\n", summary

def build_weekly(root: Path) -> tuple[str, dict]:
    s_text, s_sum = build_strat1(root)
    b_text, b_sum = build_back1(root)
    lines = [
        "# 研究值守双席周报 %s" % _today(),
        "",
        "> 生成：agent_platform/tools/research_seat_ops.py weekly ｜ 责任：STRAT1 / BACK1 ｜ 报 ACAD1",
        "",
        "## 一、STRAT1 摘要",
        "- 待补假设课题：%d" % len(s_sum["hypothesis_pipeline"]),
        "## 二、BACK1 摘要",
        "- 回测登记：%d ｜ 冻结就绪：%d ｜ 等待 DATA1：%d"
        % (b_sum["backtests"], len(b_sum["ready"]), len(b_sum["waiting"])),
    ]
    if b_sum["ready"]:
        lines.append("- **READY**：%s（可开跑回测）" % "、".join(b_sum["ready"]))
    else:
        lines.append("- **WAITING_DATA1**：数据未冻结，回测未开跑")
    disp = b_sum.get("dispatch") or []
    if disp:
        pend = [d for d in disp if d["status"] != "dispatched"]
        lines.append("- 研究前置派单：**pending=%d / dispatched=%d**（明细见 back1 值守简报）"
                     % (len(pend), len(disp) - len(pend)))
    lines += [
        "",
        "## 三、下周动作",
        "1. DATA1 出冻结回执（四字段 + sha256）；",
        "2. BACK1 收到回执后开跑并登记 status/go_no_go；",
        "3. STRAT1 补齐 LCS-1 策略 Spec，交 ACAD1 评审。",
    ]
    extra_sums = {}
    for seat in ("comm1", "intel1", "ai1", "prod1"):
        text, sm = build_research_seat(root, seat)
        extra_sums[seat] = sm
        aid, cn = SEAT_META[seat][0], SEAT_META[seat][1]
        lines += ["", "## %s %s 摘要" % (aid, cn),
                  "- 在册课题：%d ｜ 本方向：%d ｜ 触发：%s"
                  % (sm["topics"], len(sm["relevant"]), sm["trigger"])]
    summary = {"action": "weekly", "date": _today(), "strat1": s_sum,
               "back1": b_sum, "dispatch": b_sum.get("dispatch") or [],
               "seats": extra_sums}
    return "\n".join(lines) + "\n", summary


def _out_path(root: Path, action: str) -> Path:
    if action == "weekly":
        return root / ACADEMY_REL / "ops" / "weekly" / ("%s_weekly.md" % _today())
    return root / ACADEMY_REL / "ops" / action / ("%s_brief.md" % _today())


def run(
    action: str,
    root: Path,
    dry_run: bool = False,
    no_emit: bool = False,
    json_out: bool = False,
) -> dict:
    t0 = time.monotonic()
    builders = {"strat1": build_strat1, "back1": build_back1, "weekly": build_weekly,
                "comm1": lambda r: build_research_seat(r, "comm1"),
                "intel1": lambda r: build_research_seat(r, "intel1"),
                "ai1": lambda r: build_research_seat(r, "ai1"),
                "prod1": lambda r: build_research_seat(r, "prod1")}
    text, summary = builders[action](root)
    out = _out_path(root, action)
    written = ""
    emits = {}
    if not dry_run:
        _write_atomic(out, text)
        written = str(out.relative_to(root)).replace("\\", "/")
        if action == "weekly":
            emits["STRAT1"] = _emit(
                root, "STRAT1", "research_report weekly strat1", no_emit
            )
            emits["BACK1"] = _emit(
                root, "BACK1", "research_report weekly back1", no_emit
            )
            for seat, aid in (("comm1", "COMM1"), ("intel1", "INTEL1"),
                              ("ai1", "AI1"), ("prod1", "PROD1")):
                emits[aid] = _emit(root, aid, "research_report weekly %s" % seat, no_emit)
        else:
            agent = {"strat1": "STRAT1", "back1": "BACK1",
                     "comm1": "COMM1", "intel1": "INTEL1",
                     "ai1": "AI1", "prod1": "PROD1"}[action]
            emits[agent] = _emit(root, agent, "research_report %s" % action, no_emit)
    if not dry_run and _trace_record:
        try:
            agent_map = {"strat1": "STRAT1", "back1": "BACK1", "weekly": "ACAD1",
                             "comm1": "COMM1", "intel1": "INTEL1", "ai1": "AI1", "prod1": "PROD1"}
            _trace_record(
                agent_map.get(action, "ACAD1"),
                "research_%s" % action,
                status="ok",
                elapsed_ms=(time.monotonic() - t0) * 1000,
                root=root,
                detail="written=%s emits=%s" % (written, {k: v.get("ok") for k, v in emits.items()}),
            )
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/tools/research_seat_ops.py", exc)
    result = {
        "action": action,
        "dry_run": bool(dry_run),
        "written": written,
        "out": str(out.relative_to(root)).replace("\\", "/"),
        "summary": summary,
        "emits": emits,
    }
    if json_out or dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print(
            "[research_seat_ops] %s -> %s ｜ emits=%s"
            % (action, written, {k: v.get("ok") for k, v in emits.items()})
        )
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="研究院研究值守（STRAT1/BACK1/COMM1/INTEL1/AI1/PROD1）")
    ap.add_argument("action", choices=("strat1", "back1", "weekly", "comm1", "intel1", "ai1", "prod1"))
    ap.add_argument("--root", default=str(ROOT_DEFAULT))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-emit", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    if not (root / ACADEMY_REL / "research_topics.json").is_file():
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "research_topics.json missing",
                    "root": str(root),
                },
                ensure_ascii=False,
            )
        )
        return 2
    try:
        run(
            args.action,
            root,
            dry_run=args.dry_run,
            no_emit=args.no_emit,
            json_out=args.json,
        )
    except SystemExit as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])},
                ensure_ascii=False,
            )
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
