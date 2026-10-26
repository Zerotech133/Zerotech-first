# -*- coding: utf-8 -*-
"""研究院研究运营三件套（ACAD1 P0）：立项简报 / 评审清单 / 研究院周报 + 事件留痕。

三件套对应三个固定时点（计划任务 AcademyIntakeBrief / AcademyReview /
AcademyWeeklyReport）：
  intake  周一 09:30  立项简报：在册课题盘点 + 立项候选 + 缺项提醒
  review  周三 15:00  评审清单：待评审/联评课题 + 回测登记 + 八字段完备性检查
  weekly  周五 16:00  研究院周报：课题/评审/回测/事件统计 + KPI + 下周动作建议

纪律：
- 阶段推进不走本工具（唯一写路径=domains/academy_wiring.py），本工具只读真源、
  只写简报/评审/周报文件，不改 research_topics.json；
- 产出落 agent_platform/data/academy/{briefs,reviews,reports}/，原子替换写；
- 每次运行 emit 事件（research_intake / research_review / research_report），
  agent=task=ACAD1（供 seat_realness 审计维取证）；
- --dry-run：零文件写、零事件；--no-emit：只写文件不 emit（测试用）；
- 纯标准库、无网络、无 LLM；任何 emit 失败不阻塞主流程（事件层 fail-open）。

用法：
  python -X utf8 agent_platform/tools/academy_ops.py intake [--root PATH] [--dry-run] [--json]
  python -X utf8 agent_platform/tools/academy_ops.py review [--root PATH] [--dry-run] [--json]
  python -X utf8 agent_platform/tools/academy_ops.py weekly [--root PATH] [--dry-run] [--json]

退出码：0 = 正常产出（含空登记）；2 = 自身错误（真源缺失/坏 JSON/参数非法）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT_DEFAULT = HERE.parents[2]          # agent_platform/tools/academy_ops.py -> repo root
ACADEMY_REL = Path("agent_platform") / "data" / "academy"
EVENTS_REL = Path("portal") / "data" / "dev_events.jsonl"
NEW_KINDS = ("research_intake", "research_review", "research_report")
STAGES = ("intake", "hypothesis", "evidence", "validation", "review",
          "joint_review", "decision", "closed")
REVIEW_STAGES = ("validation", "review", "joint_review")

# 研究语言八字段（与 org.json academy.research_language.mandatory_fields 对齐）
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


def _iso_week(day: dt.date | None = None) -> str:
    day = day or dt.date.today()
    iso = day.isocalendar()
    return "%04d-W%02d" % (iso[0], iso[1])


def _week_start(day: dt.date | None = None) -> dt.date:
    day = day or dt.date.today()
    return day - dt.timedelta(days=day.weekday())


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


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _emit(root: Path, kind: str, detail: str, no_emit: bool = False) -> dict:
    if no_emit:
        return {"ok": False, "skipped": True}
    try:
        sys.path.insert(0, str(root / "tools"))
        import events  # noqa: WPS433  (平台事件层，纯标准库)
        return events.emit(kind=kind, task="ACAD1", agent="ACAD1",
                           detail=detail[:300])
    except Exception as exc:  # noqa: BLE001 事件层永不阻塞
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:100])}


def _topics(root: Path) -> list[dict]:
    data = _read_json(root / ACADEMY_REL / "research_topics.json")
    return [t for t in data if isinstance(t, dict)] if isinstance(data, list) else []


def _backtests(root: Path) -> list[dict]:
    return _read_jsonl(root / ACADEMY_REL / "backtests.jsonl")


def _topic_counts(topics: list[dict]) -> dict:
    counts = {s: 0 for s in STAGES}
    for t in topics:
        status = str(t.get("status") or "").strip()
        if status in counts:
            counts[status] += 1
    return counts


def _missing_fields(topic: dict) -> list[str]:
    out = []
    for key, label in EIGHT_FIELDS:
        val = topic.get(key)
        if isinstance(val, str) and val.strip() and val.strip() not in ("待验证", "待登记"):
            continue
        out.append(label)
    return out


def _fmt_ts() -> str:
    return dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")


def build_intake(root: Path) -> tuple[str, dict]:
    topics = _topics(root)
    counts = _topic_counts(topics)
    lines = ["# 研究院立项简报 %s" % _today(), "",
             "> 生成：agent_platform/tools/academy_ops.py intake ｜ 真源：research_topics.json（只读）",
             "> 阶段机：intake→hypothesis→evidence→validation→review→joint_review→decision→closed（推进走 domains/academy_wiring.py）",
             "",
             "## 一、在册概况",
             "- 课题总数：**%d**" % len(topics)]
    for stage in STAGES:
        if counts.get(stage):
            lines.append("- %s：%d" % (stage, counts[stage]))
    queued = [b for b in _backtests(root) if str(b.get("status")) == "queued"]
    lines.append("- 回测登记 queued：**%d**" % len(queued))
    lines += ["", "## 二、立项候选（intake/hypothesis）"]
    candidates = [t for t in topics if str(t.get("status")) in ("intake", "hypothesis")]
    if not candidates:
        lines.append("- （暂无——可通过投研/数据/研发/产品/市场/法务/田丰提交需求，或用 academy_wiring 登记）")
    for t in candidates:
        miss = _missing_fields(t)
        lines.append("- **%s** %s ｜ 方向=%s ｜ owner=%s ｜ 缺项=%s"
                     % (t.get("topic_id", "?"), t.get("title", "?"),
                        t.get("direction", "?"), t.get("owner", "?"),
                        ("、".join(miss) if miss else "无（八字段齐）")))
    lines += ["", "## 三、今日建议动作",
              "1. 对每个立项候选补齐八字段（问题/对象/假设/证据口径/验证方法/结论等级/行动建议/风险边界）；",
              "2. 数据口径冻结由 DATA1 登记（data_version / sample_in / sample_out）；",
              "3. 推进阶段只可通过 academy_wiring.py move（±1 步），→decision/→closed 需审批凭据。"]
    summary = {"action": "intake", "date": _today(), "topics": len(topics),
               "counts": counts, "queued_backtests": len(queued),
               "candidates": [t.get("topic_id") for t in candidates]}
    return "\n".join(lines) + "\n", summary


def build_review(root: Path) -> tuple[str, dict]:
    topics = _topics(root)
    counts = _topic_counts(topics)
    pending = [t for t in topics if str(t.get("status")) in REVIEW_STAGES]
    lines = ["# 研究院评审清单 %s" % _today(), "",
             "> 生成：agent_platform/tools/academy_ops.py review ｜ 只读真源 ｜ 评审结论须经评审后由院长/田丰签批",
             "",
             "## 一、待评审课题（validation/review/joint_review）"]
    if not pending:
        lines.append("- （暂无——先把候选推进到 validation 或 review，再提交评审）")
    for t in pending:
        miss = _missing_fields(t)
        lines += ["", "### %s %s" % (t.get("topic_id", "?"), t.get("title", "?")),
                  "- 阶段：%s ｜ 方向：%s ｜ owner：%s ｜ sponsor：%s"
                  % (t.get("status"), t.get("direction"), t.get("owner"), t.get("sponsor")),
                  "- 八字段缺项：%s" % ("、".join(miss) if miss else "无"),
                  "- 结论：%s ｜ 决策：%s（待评审记录）"
                  % (t.get("conclusion") or "待验证", t.get("decision") or "待签")]
    lines += ["", "## 二、回测登记"]
    bts = _backtests(root)
    if not bts:
        lines.append("- （空）")
    for b in bts:
        lines.append("- %s ｜ strategy=%s ｜ symbol=%s ｜ status=%s ｜ go_no_go=%s"
                     % (b.get("topic_id", "?"), b.get("strategy", "?"),
                        b.get("symbol", "?"), b.get("status", "?"),
                        b.get("go_no_go", "?")))
    lines += ["", "## 三、评审检查表（逐项必须给证据）",
              "- [ ] 数据冻结：data_version / sample_in / sample_out 已登记且可追溯；",
              "- [ ] 可复现：同一输入重跑结果一致（附命令/产物）；",
              "- [ ] 可证伪：假设与否定条件写明，证据分级（事实/佐证/证伪/存疑/未知）；",
              "- [ ] Go/No-Go 依据：无数据冻结与验收证据时不得输出 Go 或产品化结论；",
              "- [ ] 跨部门联评：投研/数据/研发/产品/市场/法务意见与责任人；",
              "- [ ] 行动建议：补证/No-Go/入库/试点/产品化/终止，禁止悬空。"]
    summary = {"action": "review", "date": _today(), "topics": len(topics),
               "counts": counts, "pending": [t.get("topic_id") for t in pending],
               "backtests": len(bts)}
    return "\n".join(lines) + "\n", summary


def _weekly_event_stats(root: Path) -> dict:
    start = _week_start().isoformat()
    stats = {"by_kind": {}, "acad1_events": 0}
    for rec in _read_jsonl(root / EVENTS_REL):
        day = str(rec.get("day") or rec.get("ts") or "")[:10]
        if day < start:
            continue
        kind = str(rec.get("kind") or "?")
        if kind in NEW_KINDS:
            stats["by_kind"][kind] = stats["by_kind"].get(kind, 0) + 1
        if str(rec.get("agent") or "") == "ACAD1":
            stats["acad1_events"] += 1
    return stats


def build_weekly(root: Path) -> tuple[str, dict]:
    today = dt.date.today()
    week = _iso_week(today)
    start = _week_start(today)
    topics = _topics(root)
    counts = _topic_counts(topics)
    bts = _backtests(root)
    stats = _weekly_event_stats(root)
    updated = []
    for t in topics:
        upd = str(t.get("updated") or "")[:10]
        if upd and upd >= start.isoformat():
            updated.append(t)
    lines = ["# 研究院周报 %s（%s ~ %s）" % (week, start.isoformat(), today.isoformat()), "",
             "> 生成：agent_platform/tools/academy_ops.py weekly ｜ 报 A0 田丰 ｜ 数据源：研究登记簿/回测登记/事件层（只读）",
             "",
             "## 一、本周产出",
             "- 课题在册：**%d**（本周登记/更新：%d）" % (len(topics), len(updated)),
             "- 阶段分布：" + " ｜ ".join("%s=%d" % (s, counts[s]) for s in STAGES if counts.get(s)),
             "- 回测登记：%d（queued=%d）"
             % (len(bts), len([b for b in bts if str(b.get("status")) == "queued"])),
             "- 研究事件（本周）：%s ｜ ACAD1 事件：%d"
             % (json.dumps(stats["by_kind"], ensure_ascii=False) if stats["by_kind"] else "无",
                stats["acad1_events"]),
             "",
             "## 二、KPI 对照（org.json academy.kpi）",
             "| KPI | 目标 | 本周实际 | 状态 |",
             "|---|---|---|---|",
             "| 可复现研究课题 | 每周 ≥1 | 本周新增/更新 %d 条（仅登记，需回测证据才算完成） | %s |"
             % (len(updated), "达标" if len(updated) >= 1 else "未达标"),
             "| 方法论/判例沉淀 | 每周 ≥2 | 未度量（尚无方法论账本，P1 建） | 未接线 |",
             "| 结论入库 | 结论须经评审 | 待评审 %d 条 | 进行中 |"
             % len([t for t in topics if str(t.get("status")) in REVIEW_STAGES]),
             "",
             "## 三、下周动作",
             "1. 推动本周登记课题补齐数据冻结与回测证据（责任：DATA1/BACK1）；",
             "2. 周三评审会前完成待评审课题的八字段与证据检查表（责任：ACAD1）；",
             "3. 未排程席位补领任务（STRAT1/COMM1/INTEL1/AI1/PROD1），周报下周统计履职率。",
             "",
             "## 四、口径说明",
             "- 本报告为**只读登记统计**，不替代评审记录与 Go/No-Go 结论；",
             "- 数据未冻结/回测未执行时，不得输出 Go 或产品化结论。"]
    summary = {"action": "weekly", "week": week, "topics": len(topics),
               "updated": len(updated), "backtests": len(bts),
               "events": stats["by_kind"], "acad1_events": stats["acad1_events"]}
    return "\n".join(lines) + "\n", summary


def _out_path(root: Path, action: str, day: str | None = None) -> Path:
    if action == "intake":
        return root / ACADEMY_REL / "briefs" / ("%s_intake.md" % (day or _today()))
    if action == "review":
        return root / ACADEMY_REL / "reviews" / ("%s_review.md" % (day or _today()))
    return root / ACADEMY_REL / "reports" / ("%s_weekly.md" % _iso_week())


def run(action: str, root: Path, dry_run: bool = False, no_emit: bool = False,
        json_out: bool = False) -> dict:
    builders = {"intake": build_intake, "review": build_review, "weekly": build_weekly}
    kinds = {"intake": "research_intake", "review": "research_review",
             "weekly": "research_report"}
    text, summary = builders[action](root)
    out = _out_path(root, action)
    emit = {"ok": False, "skipped": True}
    written = ""
    if not dry_run:
        _write_atomic(out, text)
        written = str(out.relative_to(root)).replace("\\", "/")
        emit = _emit(root, kinds[action],
                     "%s %s topics=%s" % (kinds[action], summary.get("date") or summary.get("week"),
                                          summary.get("topics", "?")),
                     no_emit=no_emit)
    result = {"action": action, "dry_run": bool(dry_run), "written": written,
              "out": str(out.relative_to(root)).replace("\\", "/"),
              "summary": summary, "emit": emit}
    if json_out:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    elif dry_run:
        print(json.dumps({"dry_run": True, "action": action,
                          "out": result["out"], "summary": summary},
                         ensure_ascii=False, indent=1))
    else:
        print("[academy_ops] %s -> %s ｜ topics=%s ｜ emit=%s"
              % (action, written, summary.get("topics"), emit.get("ok")))
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="研究院研究运营三件套（立项/评审/周报）")
    ap.add_argument("action", choices=("intake", "review", "weekly"))
    ap.add_argument("--root", default=str(ROOT_DEFAULT))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-emit", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    if not (root / ACADEMY_REL / "research_topics.json").is_file():
        print(json.dumps({"ok": False, "error": "research_topics.json missing",
                          "root": str(root)}, ensure_ascii=False))
        return 2
    try:
        run(args.action, root, dry_run=args.dry_run, no_emit=args.no_emit,
            json_out=args.json)
    except SystemExit as exc:  # _read_json 抛出的真源错误
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    except Exception as exc:  # noqa: BLE001 兜底：退出码 2，便于计划任务判读
        print(json.dumps({"ok": False, "error": "%s: %s"
                          % (type(exc).__name__, str(exc)[:160])}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
