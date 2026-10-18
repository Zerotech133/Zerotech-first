# -*- coding: utf-8 -*-
"""R5 复盘回流生成器（联动层二期 R5）：计划任务「AgentRetrospectBrief」动作体。

职责：扫描 checkpoint 门判日志最新块（复用 portal/api_gate 解析实现）+ eval
产物（若有）→ 生成结构化「复盘简报」（结论 + J1/J2/J3 摘要 + 异常项）→
追加式落盘 automation_runs/retro_brief/briefs/YYYY-MM.jsonl（append-only，
同 date+kind 幂等跳过，历史可回看）→ 简报含 P1 级问题时走 TaskStore 自动
开 dev 部门 P1 单（execution_watchdog 开单判例：同日同标题去重）。

红线（沿 execution_watchdog 判例）：
- 只读数据源（checkpoint log / eval 产物），**不回写影子账本**、不碰引擎；
- 写路径仅四条，--dry-run 全部守卫为零副作用：
  ① 简报文件 briefs/YYYY-MM.jsonl  ② 工单（TaskStore org tasks）
  ③ 日志 run.log                    ④ 状态 last_run.json；
- P1 判定：checkpoint 有 FAIL 项或 verdict 非 pass → P1 问题（简报内写明
  映射理由），自动开单同日同标题去重；
- 写路径统一落 automation_runs/retro_brief/（2026-10-18 定时产物统一落点）；
- 零新增依赖（纯 stdlib）。

eval 产物勘察实况：agent_platform/data/shadow/ 下官方评估件
eval_shadow_YYYY-MM-DD_official.md 与实时落点 eval_last_run.log 同构：

    影子账本：done=25 failed=0 交易日=5 票数=5
    [J1 无人干预] PASS（篮子覆盖率 100%, done 占比 100%）
    [J2 秩相关] PASS（score-vs-ret rho=..., n=...）
    [J4 perf] 沉淀 recorded=0 replayed=23 skipped=0 findings=0
    [J3 lineage] PASS（25/25 决策可回查辩论 trace）
      [warn]/[skip]/[pending] ... 观察行
    --------------------------------------------------------------
    影子评估结论：PASS（满 5 交易日后 J2/J3 才具统计意义）

今天（周日）尚无当日 eval 产物属预期路径：生成器优雅跳过 eval 侧，
kind 退化为 checkpoint，不报错。

用法:
    python -X utf8 tools/retro_brief.py               # 正常生成（计划任务动作）
    python -X utf8 tools/retro_brief.py --dry-run     # 全路径零写副作用
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

# 门判解析复用（R1 判例：tools 侧经 portal sys.path 复用只读解析面）
sys.path.insert(0, str(_REPO / "portal"))
import api_gate  # noqa: E402

# TaskStore 开单底座（execution_watchdog / run_shadow 判例：
# engine path 注入后 from tasks import TaskStore）
sys.path.insert(0, str(_REPO / "agent_platform" / "engine"))

#: 门判日志（append-only 文本块，api_gate 同源）
CHECKPOINT_LOG = (_REPO / "agent_platform" / "data" / "shadow"
                  / "monday_checkpoint.log")
#: 影子产物目录（eval 产物扫描面）
SHADOW_DIR = _REPO / "agent_platform" / "data" / "shadow"
#: 简报落盘目录（append-only jsonl，按月分文件）
BRIEFS_DIR = _REPO / "automation_runs" / "retro_brief" / "briefs"
#: 状态目录（last_run.json + run.log）
STATE_DIR = _REPO / "automation_runs" / "retro_brief"
#: 工单库（TaskStore 按月账本）
TASKS_DIR = _REPO / "agent_platform" / "data" / "org" / "tasks"

#: eval 产物 glob（官方评估件 + 实时落点，取 mtime 最新一份）
_EVAL_GLOBS = ("eval_shadow_*.md", "eval_last_run.log")

#: eval J 项行「[J1 无人干预] PASS（...）」/「[J4 perf] 沉淀 ...」
_EVAL_J_RE = re.compile(r"^\[(J\d)\s+([^\]]+)\]\s*(.*)$")
#: J 项判定段「PASS（...）」/「FAIL（...）」
_EVAL_VERDICT_RE = re.compile(r"^(PASS|FAIL)\s*[（(](.*?)\s*[）)]\s*$")
#: 结论行「影子评估结论：PASS（...）」
_EVAL_CONCLUSION_RE = re.compile(r"^影子评估结论[:：]\s*(PASS|FAIL)")
#: 账本头「影子账本：done=25 failed=0 交易日=5 票数=5」
_EVAL_LEDGER_RE = re.compile(r"^影子账本：done=(\d+)\s+failed=(\d+)")
#: 观察行「[warn] / [skip] / [pending] ...」（保留缩进匹配故不吃 strip）
_EVAL_NOTE_RE = re.compile(r"^\s*\[(warn|skip|pending)\]\s*(.+)$")


# ── 数据源采集（只读，fail-visible） ─────────────────────────────────────

def _load_checkpoint(log_path=None):
    """读门判日志最新块（复用 api_gate._parse_log 解析）。

    Returns:
        dict | None: {"date","verdict","items"}；日志缺失/无有效块返回 None。
    """
    p = Path(log_path) if log_path else CHECKPOINT_LOG
    if not p.is_file():
        return None
    try:
        blocks = api_gate._parse_log(p)
    except OSError:
        return None
    return blocks[-1] if blocks else None  # append-only，末块即最新


def _eval_candidates(shadow_dir=None):
    """eval 产物候选（按 mtime 升序）。"""
    d = Path(shadow_dir) if shadow_dir else SHADOW_DIR
    out = []
    for pat in _EVAL_GLOBS:
        out.extend(d.glob(pat))
    return sorted(out, key=lambda f: f.stat().st_mtime)


def _parse_eval(path):
    """解析单个 eval 产物 → 结构化 dict；无有效信号返回 None（优雅跳过）。"""
    items, anomalies = [], []
    conclusion = None
    ledger = None
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for ln in text.splitlines():
        s = ln.strip()
        m = _EVAL_LEDGER_RE.match(s)
        if m:
            ledger = {"done": int(m.group(1)), "failed": int(m.group(2))}
            continue
        m = _EVAL_J_RE.match(s)
        if m:
            key, name, rest = m.group(1), m.group(2).strip(), m.group(3).strip()
            vm = _EVAL_VERDICT_RE.match(rest)
            if vm:
                ok, detail = vm.group(1) == "PASS", vm.group(2)
            else:
                # J4 perf 等信息项（无 PASS/FAIL 判定）→ 计入摘要但不判负
                ok, detail = True, rest
            items.append({"key": key, "name": name, "ok": ok,
                          "detail": detail})
            continue
        m = _EVAL_CONCLUSION_RE.match(s)
        if m:
            conclusion = m.group(1)
            continue
        m = _EVAL_NOTE_RE.match(ln)
        if m:
            anomalies.append({"source": "eval", "tag": m.group(1),
                              "text": m.group(2).strip()})
    if not items and conclusion is None:
        return None  # 无任何评估信号 → 视同无产物（宁少勿假）
    if ledger and ledger["failed"] > 0:
        anomalies.insert(0, {"source": "eval", "tag": "ledger",
                             "text": "影子账本 failed=%d（done=%d）"
                                     % (ledger["failed"], ledger["done"])})
    # verdict：结论行优先 → J 项 FAIL 兜底 → 账本 failed>0 强制 fail
    if conclusion is not None:
        verdict = "pass" if conclusion == "PASS" else "fail"
    else:
        verdict = "fail" if any(not i["ok"] for i in items) else "pass"
    if ledger and ledger["failed"] > 0:
        verdict = "fail"
    m = re.search(r"(\d{4}-\d{2}-\d{2})", Path(path).name)
    eval_date = m.group(1) if m else datetime.fromtimestamp(
        Path(path).stat().st_mtime).strftime("%Y-%m-%d")
    return {"file": Path(path).name, "date": eval_date, "verdict": verdict,
            "items": items, "anomalies": anomalies, "ledger": ledger}


def _load_eval(shadow_dir=None):
    """取 mtime 最新一份 eval 产物并解析；不存在/不可解析返回 None。"""
    cands = _eval_candidates(shadow_dir)
    if not cands:
        return None
    return _parse_eval(cands[-1])


# ── 简报构造 ──────────────────────────────────────────────────────────────

def build_brief(cp, ev, now=None):
    """由门判块（cp，可 None）与 eval 产物（ev，可 None）构造简报 dict。

    结构（api_brief 投影契约的源）：
        {date, kind('checkpoint'/'eval'/'both'), verdict,
         j_items:[{key,name,ok,detail}], j_summary:{pass,total},
         anomalies:[{source,tag,text}], p1_issues:[{source,title,reason}],
         brief_text, generated_at, sources{...}}
    """
    now = now or datetime.now()
    date = cp["date"] if cp else ev["date"]
    kind = "both" if (cp and ev) else ("checkpoint" if cp else "eval")
    j_items = (cp["items"] if cp else []) + (ev["items"] if ev else [])
    # SKIP 项（detail 带 [SKIP] 标签）不计入 PASS 数，与门判「x/y」口径对齐
    j_pass = sum(1 for it in j_items
                 if it["ok"] and not it["detail"].startswith("["))

    anomalies = []
    if cp:
        for it in cp["items"]:
            if not it["ok"]:
                anomalies.append({"source": "checkpoint", "tag": "FAIL",
                                  "text": "%s —— %s" % (it["name"],
                                                        it["detail"])})
    if ev:
        anomalies.extend(ev["anomalies"])

    verdict = "pass"
    if cp and cp["verdict"] == "fail":
        verdict = "fail"
    if ev and ev["verdict"] == "fail":
        verdict = "fail"

    # P1 判定（映射理由写进简报）：checkpoint 有 FAIL 项 或 verdict 非 pass
    p1_issues = []
    for it in (cp["items"] if cp else []):
        if not it["ok"]:
            p1_issues.append({
                "source": "checkpoint",
                "title": "复盘简报P1：%s 门判项FAIL" % date,
                "reason": ("checkpoint 门判项 FAIL（映射理由：门判 EXIT 非 0 / "
                           "项级 FAIL）：%s —— %s" % (it["name"], it["detail"])),
            })
    if verdict != "pass" and not p1_issues:
        if ev and ev["verdict"] == "fail":
            p1_issues.append({
                "source": "eval",
                "title": "复盘简报P1：%s 复盘未过" % date,
                "reason": "eval 评估结论非 PASS（%s）——映射理由：复盘 verdict "
                          "非 pass 且无项级 FAIL 明细" % ev["file"],
            })
        else:
            p1_issues.append({
                "source": "checkpoint",
                "title": "复盘简报P1：%s 复盘未过" % date,
                "reason": "checkpoint 门判 verdict=fail（无项级 FAIL 明细）——"
                          "映射理由：复盘 verdict 非 pass",
            })

    brief = {
        "date": date,
        "kind": kind,
        "verdict": verdict,
        "j_items": j_items,
        "j_summary": {"pass": j_pass, "total": len(j_items)},
        "anomalies": anomalies,
        "p1_issues": p1_issues,
        "generated_at": now.isoformat(timespec="seconds"),
        "sources": {
            "checkpoint_date": cp["date"] if cp else None,
            "checkpoint_verdict": cp["verdict"] if cp else None,
            "eval_file": ev["file"] if ev else None,
            "eval_date": ev["date"] if ev else None,
            "eval_verdict": ev["verdict"] if ev else None,
        },
    }
    brief["brief_text"] = _fmt_brief_text(brief)
    return brief


def _fmt_brief_text(b):
    """简报人读文本（console 观察季卡卡面 + P1 工单 body）。"""
    lines = ["【复盘简报 %s】kind=%s verdict=%s"
             % (b["date"], b["kind"], b["verdict"])]
    src = b["sources"]
    if src.get("checkpoint_date"):
        lines.append("■ 门判（%s，源 monday_checkpoint.log）：%s"
                     % (src["checkpoint_date"],
                        "PASS" if src["checkpoint_verdict"] == "pass"
                        else "FAIL"))
    if src.get("eval_file"):
        lines.append("■ 评估（%s，%s）：%s"
                     % (src["eval_file"], src["eval_date"],
                        (src["eval_verdict"] or "unknown").upper()))
    js = b["j_summary"]
    lines.append("■ J 项摘要：PASS %d / 共 %d" % (js["pass"], js["total"]))
    for it in b["j_items"]:
        if not it["ok"]:
            state = "FAIL"
        elif it["detail"].startswith("["):
            state = "SKIP"
        else:
            state = "PASS"
        lines.append("  [%s] %s：%s" % (state, it["name"], it["detail"]))
    if b["anomalies"]:
        lines.append("■ 异常项（%d）：" % len(b["anomalies"]))
        for i, a in enumerate(b["anomalies"], 1):
            lines.append("  %d. [%s:%s] %s" % (i, a["source"], a["tag"],
                                               a["text"]))
    else:
        lines.append("■ 异常项：无")
    if b["p1_issues"]:
        lines.append("■ P1 问题（%d）→ 自动开单：" % len(b["p1_issues"]))
        for i, p in enumerate(b["p1_issues"], 1):
            lines.append("  %d. %s" % (i, p["reason"]))
    else:
        lines.append("■ P1 问题：无")
    lines.append("生成时间：%s（AgentRetrospectBrief · append-only 可回看）"
                 % b["generated_at"])
    return "\n".join(lines)


def generate_brief(now=None):
    """采集数据源并生成简报。

    Returns:
        tuple[dict | None, str | None]: (简报 dict, 失败原因)——两者互斥；
        数据源全缺时 (None, reason)。
    """
    now = now or datetime.now()
    cp = _load_checkpoint()
    ev = _load_eval()
    if cp is None and ev is None:
        return None, ("无可用数据源（checkpoint 日志缺失/无有效块，"
                      "eval 产物不存在）")
    return build_brief(cp, ev, now), None


# ── 追加式落盘（同 date+kind 幂等） ───────────────────────────────────────

def _persist_brief(brief):
    """简报追加写月度 jsonl（append-only 不覆盖；同 date+kind 幂等跳过）。

    Returns:
        tuple[bool, str]: (是否新写, 说明)。
    """
    month = brief["date"][:7]
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    f = BRIEFS_DIR / ("%s.jsonl" % month)
    if f.is_file():
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue  # 坏行不挡幂等判定
            if (rec.get("date") == brief["date"]
                    and rec.get("kind") == brief["kind"]):
                return False, "同 date+kind 简报已存在，幂等跳过"
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(brief, ensure_ascii=False) + "\n")
    return True, "已追加 %s" % f.name


# ── P1 自动开单（TaskStore，execution_watchdog 判例） ─────────────────────

def p1_ticket_title(brief):
    """P1 工单标题（带简报日期 → 同日同标题去重自然成立）。"""
    return "复盘简报P1：%s（%d 项）" % (brief["date"],
                                       len(brief["p1_issues"]))


def open_p1_ticket(brief, tasks_dir=None):
    """TaskStore.open 开 dev 部门 P1 单；同日同标题去重（ls 标题比对）。

    Returns:
        tuple[str | None, str]: (task_id 或 None, 去重/异常说明)。
    """
    title = p1_ticket_title(brief)
    body = "复盘简报 P1 自动开单（R5 复盘回流，映射理由见正文）\n\n%s" \
        % brief["brief_text"]
    try:
        from tasks import TaskStore  # noqa: E402  engine path 已注入
        ts = TaskStore(Path(tasks_dir) if tasks_dir else TASKS_DIR)
        dup = [t for t in ts.ls(dept="dev") if t["title"] == title]
        if dup:
            return dup[0]["task_id"], "同日同标题工单已存在，跳过开单"
        tid = ts.open("dev", title, body, "P1",
                      opened_by="system/retro-brief")
        return tid, "已开单（P1，dev 部门）"
    except Exception as e:  # noqa: BLE001  开单 fail-open：不中断简报落盘
        return None, "开单异常（不中断简报落盘）: %s" % e


# ── 状态留痕（dry-run 一律不写） ──────────────────────────────────────────

def _write_last_run(payload):
    """写 last_run.json（N1 晨检可探复盘链自身失联）。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "last_run.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)


def _append_run_log(line):
    """追加 run.log（append-only 运行留痕）。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "run.log", "a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")


# ── 主流程 ────────────────────────────────────────────────────────────────

def main(argv=None):
    """生成器主入口。返回退出码：0=正常（含幂等跳过）、1=自身异常/无数据。

    --dry-run：四路径守卫全零写副作用——简报文件 / 工单 / run.log /
    last_run.json 一律不写，仅打印将生成的简报 JSON 样例与将开工单标题。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    dry = "--dry-run" in argv
    now = datetime.now()
    ran_at = now.isoformat(timespec="seconds")
    try:
        brief, reason = generate_brief(now=now)
        if brief is None:
            print("[retro] 无法生成简报：%s" % reason)
            if not dry:
                _write_last_run({"ran_at": ran_at, "exit": 1,
                                 "reason": reason, "brief_date": None,
                                 "ticket": None})
                _append_run_log("%s ERROR %s" % (ran_at, reason))
            else:
                print("[dry-run] last_run/run.log 未写（零副作用）")
            return 1
        if dry:
            print("[dry-run] 简报未落盘（零副作用）。样例：")
            print(json.dumps(brief, ensure_ascii=False, indent=2))
            written, note = False, "dry-run 不落盘"
        else:
            written, note = _persist_brief(brief)
        ticket_id, t_note = None, "无 P1 问题"
        if brief["p1_issues"]:
            if dry:
                print("[dry-run] 将开 P1 工单：%s" % p1_ticket_title(brief))
            else:
                ticket_id, t_note = open_p1_ticket(brief)
        print("[retro] %s 简报 verdict=%s P1=%d → %s；工单=%s（%s）"
              % (brief["date"], brief["verdict"], len(brief["p1_issues"]),
                 note, ticket_id, t_note))
        if not dry:
            _write_last_run({"ran_at": ran_at, "exit": 0, "reason": None,
                             "brief_date": brief["date"],
                             "brief_kind": brief["kind"],
                             "brief_written": written, "note": note,
                             "ticket": ticket_id, "ticket_note": t_note})
            _append_run_log("%s OK date=%s kind=%s verdict=%s written=%s "
                            "ticket=%s" % (ran_at, brief["date"],
                                           brief["kind"], brief["verdict"],
                                           written, ticket_id))
        else:
            print("[dry-run] 简报/工单/run.log/last_run 四路径全未写"
                  "（零副作用）")
        return 0
    except Exception as e:  # noqa: BLE001  自身异常 → exit 1（N1 可探）
        print("[retro] 生成器自身异常: %s" % e)
        if not dry:
            try:
                _write_last_run({"ran_at": ran_at, "exit": 1,
                                 "reason": str(e), "brief_date": None,
                                 "ticket": None})
                _append_run_log("%s ERROR %s" % (ran_at, e))
            except OSError:
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
