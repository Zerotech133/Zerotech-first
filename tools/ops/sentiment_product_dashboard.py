# -*- coding: utf-8 -*-
"""情绪数据产品化：每日看板生成器（离线、自包含、只读状态）。

用法：
    python -X utf8 tools/ops/sentiment_product_dashboard.py --render
    python -X utf8 tools/ops/sentiment_product_dashboard.py --set-signoff mada=signed --note "马岱已签收"
    python -X utf8 tools/ops/sentiment_product_dashboard.py --set-milestone m2=done --note "签收完成"

状态文件：deliverables/2026-11-12_情绪数据产品_看板状态.json
输出页面：deliverables/2026-11-12_情绪数据产品_每日看板.html

原则：
- 不伪造签收/批准；只有显式 set 或状态文件修改才会变成 signed/approved；
- 不读生产库、不联网、不重启服务；
- 页面自包含（无外部 JS/CSS/CDN），可直接 file:// 打开，也可作为静态页托管。
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
DELIV = BASE / "deliverables"
STATE_FILE = DELIV / "2026-11-12_情绪数据产品_看板状态.json"
HTML_FILE = DELIV / "2026-11-12_情绪数据产品_每日看板.html"

TASK_ID = "ZB-DATA-SENT-20261112-T01"


def now() -> datetime:
    return datetime.now()


DEFAULT_STATE = {
    "task_id": TASK_ID,
    "title": "情绪数据产品化",
    "subtitle": "数据仓一等数据产品 · KG 只读接入的前置工程",
    "generated_at": "",
    "signoffs": [
        {
            "id": "mada",
            "name": "马岱",
            "role": "数据仓主办",
            "kind": "signoff",
            "status": "sent",
            "due": "2026-11-12 18:00",
            "evidence": "任务单 ZB-DATA-SENT-20261112-T01；MSG-20261112052759-463970",
            "note": "经 DATA1 席位送达；hr 下暂无马岱独立席位",
        },
        {
            "id": "zhangsong",
            "name": "张松",
            "role": "DATA1 / Owner",
            "kind": "signoff",
            "status": "sent",
            "due": "2026-11-12 18:00",
            "evidence": "契约 §13 签收请求；MSG-20261112053642-2b3b14",
            "note": "确认 e互动缺口、个股 forward-only、市场 60 天回填",
        },
        {
            "id": "majun",
            "name": "马钧",
            "role": "DEV1 / KG",
            "kind": "input",
            "status": "hold",
            "due": "2026-11-14 18:00",
            "evidence": "只读消费通知 ZB-DATA-SENT-20261112-N01；MSG-20261112052759-dda750",
            "note": "只交查询需求，不写代码、不建节点、不写图",
        },
        {
            "id": "laotian",
            "name": "老田",
            "role": "A0 / 发布批准",
            "kind": "approval",
            "status": "pending",
            "due": "2026-11-18 18:00",
            "evidence": "发布闸门：验收回执齐备后提交批准",
            "note": "不代替批准；发布前必须显式确认",
        },
    ],
    "milestones": [
        {
            "id": "m1",
            "date": "2026-11-12 18:00",
            "title": "签收：马岱 / 张松",
            "owner": "马岱 / 张松",
            "status": "in_progress",
            "gate": True,
            "evidence": "签收回复 + 契约 §13 确认",
        },
        {
            "id": "m2",
            "date": "2026-11-13 18:00",
            "title": "建表 + loader 干跑",
            "owner": "马岱",
            "status": "pending",
            "gate": True,
            "evidence": "DDL、dry-run 日志、manifest 样例行",
        },
        {
            "id": "m3",
            "date": "2026-11-14 18:00",
            "title": "KG 只读查询需求",
            "owner": "马钧",
            "status": "pending",
            "gate": False,
            "evidence": "一页查询需求 + 查询草案；不写代码",
        },
        {
            "id": "m4",
            "date": "2026-11-16 18:00",
            "title": "市场 60 天回填 + 个股首日",
            "owner": "马岱",
            "status": "pending",
            "gate": True,
            "evidence": "回填 manifest、行数、覆盖率、异常清单",
        },
        {
            "id": "m5",
            "date": "2026-11-17 18:00",
            "title": "契约 §13 验收",
            "owner": "张松 / 陈群",
            "status": "pending",
            "gate": True,
            "evidence": "验收记录 + 质量查询结果",
        },
        {
            "id": "m6",
            "date": "2026-11-18 18:00",
            "title": "回执 + 数据仓发布",
            "owner": "马岱 / 张松",
            "status": "pending",
            "gate": True,
            "evidence": "发布回执、catalog 登记、回滚点",
        },
        {
            "id": "m7",
            "date": "2026-11-18 18:00",
            "title": "老田批准发布",
            "owner": "老田",
            "status": "pending",
            "gate": True,
            "evidence": "显式批准记录",
        },
        {
            "id": "m8",
            "date": "发布后",
            "title": "另发 KG 接入实施单",
            "owner": "马钧 / 张松",
            "status": "blocked",
            "gate": False,
            "evidence": "数据仓 v1 发布回执之后",
        },
    ],
    "blockers": [
        {"level": "high", "text": "签收未完成：马岱、张松均未签收，禁止建表。"},
        {"level": "medium", "text": "e互动当前无生产者：权重 0.10 但 sentiment_card.py 无采集；允许空，不补 0 行。"},
        {"level": "medium", "text": "个股/来源历史无法回填：只能从 2026-11-12 起 forward-only；市场级最多回填 60 天。"},
        {"level": "low", "text": "马岱无独立 hr 席位：任务经 DATA1 送达，标题/正文明确主办。"},
    ],
    "notes": [
        {"at": "2026-11-12 05:28", "text": "三份文书 + 回执提交：136359878 / 7e2506e45。"},
        {"at": "2026-11-12 05:36", "text": "张松签收请求送达 DATA1：MSG-20261112053642-2b3b14。"},
        {"at": "2026-11-12 05:46", "text": "执行跟踪表建立并提交：e549f28d6；五个检查点已设。"},
    ],
}

STATUS_LABEL = {
    "pending": "未开始",
    "sent": "已送达·待签收",
    "hold": "HOLD·待需求",
    "signed": "已签收",
    "approved": "已批准",
    "done": "已完成",
    "in_progress": "进行中",
    "blocked": "阻塞",
    "overdue": "逾期",
}

STATUS_CLASS = {
    "pending": "muted",
    "sent": "warn",
    "hold": "warn",
    "signed": "ok",
    "approved": "ok",
    "done": "ok",
    "in_progress": "info",
    "blocked": "bad",
    "overdue": "bad",
}


def parse_dt(value: str) -> datetime | None:
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def load_state() -> dict:
    if STATE_FILE.is_file():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("signoffs") and data.get("milestones"):
                return data
        except (OSError, ValueError):
            pass
    state = json.loads(json.dumps(DEFAULT_STATE, ensure_ascii=False))
    state["generated_at"] = now().strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)
    return state


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["generated_at"] = now().strftime("%Y-%m-%d %H:%M:%S")
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def derive_status(item: dict, now_dt: datetime) -> str:
    status = str(item.get("status") or "pending").lower()
    if status in {"signed", "approved", "done"}:
        return status
    due = parse_dt(str(item.get("due") or item.get("date") or ""))
    if due and now_dt > due and status not in {"blocked"}:
        return "overdue"
    return status


def human_due(value: str) -> str:
    dt = parse_dt(value)
    return dt.strftime("%m-%d %H:%M") if dt else value


def days_left(value: str, now_dt: datetime) -> str:
    dt = parse_dt(value)
    if not dt:
        return "发布后"
    delta = dt.date() - now_dt.date()
    if delta.days == 0:
        return "今天"
    if delta.days > 0:
        return f"{delta.days} 天后"
    return f"逾期 {-delta.days} 天"


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def render(state: dict) -> str:
    now_dt = now()
    signoffs = state.get("signoffs", [])
    milestones = state.get("milestones", [])
    blockers = state.get("blockers", [])
    notes = sorted(state.get("notes", []), key=lambda x: x.get("at", ""), reverse=True)

    for item in signoffs + milestones:
        item["_state"] = derive_status(item, now_dt)

    gate_items = [x for x in milestones if x.get("gate")]
    gate_signs = [x for x in signoffs if x.get("kind") in {"signoff", "approval"}]
    gate_done = sum(1 for x in gate_items + gate_signs if x["_state"] in {"done", "signed", "approved"})
    gate_total = len(gate_items) + len(gate_signs)
    ready = gate_done == gate_total

    all_items = signoffs + milestones
    all_done = sum(1 for x in all_items if x["_state"] in {"done", "signed", "approved"})
    progress_pct = round(all_done * 100 / max(1, len(all_items)))

    next_items = [x for x in all_items if x["_state"] not in {"done", "signed", "approved", "blocked"}]
    next_items.sort(key=lambda x: parse_dt(str(x.get("due") or x.get("date") or "2999-12-31")) or datetime.max)
    focus = next_items[0] if next_items else None

    def pill(item: dict) -> str:
        st = item["_state"]
        return f'<span class="pill {STATUS_CLASS.get(st, "muted")}">{esc(STATUS_LABEL.get(st, st))}</span>'

    def signoff_card(item: dict) -> str:
        st = item["_state"]
        icon = "✓" if st in {"signed", "approved"} else ("!" if st in {"overdue", "blocked"} else "…")
        return f"""
        <article class="sign-card {STATUS_CLASS.get(st, "muted")}">
          <div class="sign-top"><span class="sign-icon">{icon}</span>{pill(item)}</div>
          <h3>{esc(item.get("name"))}</h3>
          <div class="role">{esc(item.get("role"))}</div>
          <div class="due">截止 <b>{esc(human_due(item.get("due", "")))}</b> · {esc(days_left(item.get("due", ""), now_dt))}</div>
          <div class="ev">{esc(item.get("evidence"))}</div>
          <div class="note">{esc(item.get("note"))}</div>
        </article>"""

    def milestone_row(item: dict, idx: int) -> str:
        st = item["_state"]
        gate = "闸门" if item.get("gate") else "并行"
        return f"""
        <tr class="row-{STATUS_CLASS.get(st, "muted")}">
          <td class="num">{idx:02d}</td>
          <td><div class="m-title">{esc(item.get("title"))}</div><div class="m-owner">{esc(item.get("owner"))} · {gate}</div></td>
          <td class="m-date">{esc(human_due(item.get("date", "")))}<div class="m-left">{esc(days_left(item.get("date", ""), now_dt))}</div></td>
          <td>{pill(item)}</td>
          <td class="m-ev">{esc(item.get("evidence"))}</td>
        </tr>"""

    blocker_html = "".join(
        f'<li class="block-{esc(x.get("level", "low"))}"><span>{esc(x.get("text"))}</span></li>'
        for x in blockers
    )
    notes_html = "".join(
        f'<li><time>{esc(x.get("at"))}</time><span>{esc(x.get("text"))}</span></li>'
        for x in notes
    )

    if focus:
        focus_title = focus.get("title") or f'{focus.get("name", "")}签收'
        focus_owner = focus.get("owner") or focus.get("role") or ""
        focus_due = str(focus.get("due") or focus.get("date") or "")
        focus_html = (
            f'<b>{esc(focus_title)}</b>'
            f'<span>{esc(focus_owner)} · {esc(human_due(focus_due))} · {esc(days_left(focus_due, now_dt))}</span>'
        )
    else:
        focus_html = '<b>无待办</b><span>所有闸门已完成</span>'

    ready_text = "可发布" if ready else "不可发布"
    ready_sub = "全部签收/验收闸门已完成" if ready else f"闸门完成 {gate_done}/{gate_total}，未签收/未验收前不得发布"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>情绪数据产品 · 每日看板</title>
<style>
:root{{
  --bg:#08101c;--panel:#0e1a2b;--panel2:#122238;--line:#20344d;--text:#e8f1ff;--muted:#8fa5c0;
  --cyan:#41d1ff;--green:#3ddc97;--amber:#ffb84d;--red:#ff6b7a;--violet:#a78bfa;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:radial-gradient(1200px 700px at 80% -10%,#123454 0%,var(--bg) 55%);color:var(--text);font:14px/1.55 "Segoe UI","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1420px;margin:0 auto;padding:28px 26px 54px}}
.top{{display:flex;justify-content:space-between;gap:18px;align-items:flex-end;border-bottom:1px solid var(--line);padding-bottom:18px}}
.kicker{{color:var(--cyan);font-size:12px;letter-spacing:.18em;text-transform:uppercase}}
h1{{font-size:34px;line-height:1.15;margin:6px 0 8px;letter-spacing:-.02em}}
.sub{{color:var(--muted);max-width:760px}}
.meta{{text-align:right;color:var(--muted);font-size:12px;white-space:nowrap}}
.meta b{{color:var(--text);font-weight:600}}
.banner{{margin:20px 0 16px;padding:18px 22px;border:1px solid var(--line);border-radius:16px;background:linear-gradient(120deg,rgba(65,209,255,.10),rgba(167,139,250,.06));display:flex;justify-content:space-between;gap:18px;align-items:center}}
.banner.bad{{border-color:rgba(255,107,122,.42);background:linear-gradient(120deg,rgba(255,107,122,.13),rgba(255,184,77,.06))}}
.banner.ok{{border-color:rgba(61,220,151,.45);background:linear-gradient(120deg,rgba(61,220,151,.13),rgba(65,209,255,.06))}}
.banner h2{{margin:0;font-size:24px}}
.banner p{{margin:4px 0 0;color:var(--muted)}}
.big{{font-size:36px;font-weight:800;letter-spacing:-.03em;color:var(--amber);white-space:nowrap}}
.banner.ok .big{{color:var(--green)}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:14px 0 22px}}
.sign-card{{border:1px solid var(--line);border-radius:14px;padding:15px 15px 12px;background:linear-gradient(180deg,var(--panel2),var(--panel));min-height:198px}}
.sign-card.ok{{border-color:rgba(61,220,151,.42)}} .sign-card.bad{{border-color:rgba(255,107,122,.45)}}
.sign-top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}}
.sign-icon{{display:inline-flex;width:26px;height:26px;border-radius:50%;align-items:center;justify-content:center;background:rgba(255,255,255,.06);color:var(--amber);font-weight:800}}
.sign-card.ok .sign-icon{{color:var(--green)}} .sign-card.bad .sign-icon{{color:var(--red)}}
.sign-card h3{{margin:2px 0 0;font-size:19px}}
.role{{color:var(--cyan);font-size:12px;margin-bottom:7px}}
.due{{color:var(--muted);font-size:12px;margin-bottom:7px}} .due b{{color:var(--text)}}
.ev{{color:#c6d6e8;font-size:12px;border-left:2px solid var(--line);padding-left:8px;margin:7px 0}}
.note{{color:var(--muted);font-size:12px;margin-top:7px}}
.pill{{display:inline-block;border-radius:999px;padding:3px 9px;font-size:11px;font-weight:700;border:1px solid transparent;white-space:nowrap}}
.pill.ok{{color:var(--green);border-color:rgba(61,220,151,.4);background:rgba(61,220,151,.10)}}
.pill.warn{{color:var(--amber);border-color:rgba(255,184,77,.4);background:rgba(255,184,77,.10)}}
.pill.bad{{color:var(--red);border-color:rgba(255,107,122,.4);background:rgba(255,107,122,.10)}}
.pill.info{{color:var(--cyan);border-color:rgba(65,209,255,.4);background:rgba(65,209,255,.10)}}
.pill.muted{{color:var(--muted);border-color:var(--line);background:rgba(255,255,255,.03)}}
.section{{margin-top:20px;border:1px solid var(--line);border-radius:16px;background:rgba(14,26,43,.82);overflow:hidden}}
.section-h{{display:flex;justify-content:space-between;align-items:center;padding:14px 18px;border-bottom:1px solid var(--line)}}
.section-h h2{{margin:0;font-size:16px;letter-spacing:.02em}}
.section-h .hint{{color:var(--muted);font-size:12px}}
table{{width:100%;border-collapse:collapse}}
th,td{{padding:11px 14px;text-align:left;border-bottom:1px solid rgba(32,52,77,.65);vertical-align:top}}
th{{color:var(--muted);font-size:11px;letter-spacing:.08em;text-transform:uppercase;font-weight:600}}
tr:last-child td{{border-bottom:0}}
.num{{color:var(--muted);font-variant-numeric:tabular-nums;width:46px}}
.m-title{{font-weight:700;font-size:14px}} .m-owner{{color:var(--muted);font-size:12px;margin-top:2px}}
.m-date{{white-space:nowrap;font-variant-numeric:tabular-nums}} .m-left{{color:var(--muted);font-size:11px;margin-top:2px}}
.m-ev{{color:var(--muted);font-size:12px;max-width:420px}}
.two{{display:grid;grid-template-columns:1.1fr .9fr;gap:16px;margin-top:20px}}
.panel{{border:1px solid var(--line);border-radius:16px;background:rgba(14,26,43,.82);padding:16px 18px}}
.panel h2{{margin:0 0 10px;font-size:16px}}
.blockers{{margin:0;padding:0;list-style:none;display:grid;gap:8px}}
.blockers li{{display:flex;gap:9px;padding:9px 10px;border-radius:9px;background:rgba(255,255,255,.025);color:#c9d8ea;font-size:12px}}
.blockers li:before{{content:"";flex:0 0 7px;width:7px;height:7px;border-radius:50%;margin-top:6px;background:var(--amber)}}
.blockers .block-high:before{{background:var(--red)}} .block-low:before{{background:var(--muted)}}
.focus{{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:14px 16px;border-radius:12px;background:linear-gradient(90deg,rgba(65,209,255,.12),rgba(167,139,250,.06));border:1px solid rgba(65,209,255,.24)}}
.focus b{{display:block;font-size:15px}} .focus span{{color:var(--muted);font-size:12px}}
.notes{{list-style:none;margin:0;padding:0;display:grid;gap:9px;max-height:230px;overflow:auto}}
.notes li{{display:grid;grid-template-columns:94px 1fr;gap:10px;font-size:12px;color:#c9d8ea}}
.notes time{{color:var(--muted);font-variant-numeric:tabular-nums}}
.footer{{display:flex;justify-content:space-between;gap:14px;margin-top:22px;color:var(--muted);font-size:11px;border-top:1px solid var(--line);padding-top:12px}}
code{{background:rgba(255,255,255,.06);border:1px solid var(--line);border-radius:5px;padding:1px 5px;color:#cfe4ff}}
@media(max-width:1100px){{.grid{{grid-template-columns:repeat(2,1fr)}}.two{{grid-template-columns:1fr}}}}
@media(max-width:700px){{.wrap{{padding:18px 14px 40px}}h1{{font-size:26px}}.top{{display:block}}.meta{{text-align:left;margin-top:10px}}.grid{{grid-template-columns:1fr}}.banner{{display:block}}.big{{margin-top:8px}}th,td{{padding:9px}}.m-ev{{display:none}}}}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div>
      <div class="kicker">零界 · 数据仓 · 每日看板</div>
      <h1>{esc(state.get("title"))}</h1>
      <div class="sub">{esc(state.get("subtitle"))}</div>
    </div>
    <div class="meta">
      <div>任务 <b>{esc(state.get("task_id"))}</b></div>
      <div>生成 <b>{esc(now_dt.strftime("%Y-%m-%d %H:%M:%S"))}</b></div>
      <div>数据源 <b>看板状态 JSON</b></div>
    </div>
  </div>

  <div class="banner {"ok" if ready else "bad"}">
    <div><h2>{ready_text}</h2><p>{ready_sub}</p></div>
    <div class="big">{gate_done}/{gate_total}</div>
  </div>

  <div class="focus">
    <div><b>今日焦点</b><span>{focus_html}</span></div>
    <div style="color:var(--muted);font-size:12px">闸门完成度 {progress_pct}%</div>
  </div>

  <div class="grid">
    {''.join(signoff_card(x) for x in signoffs)}
  </div>

  <div class="section">
    <div class="section-h"><h2>里程碑</h2><div class="hint">红=逾期/阻塞 · 黄=待办 · 绿=完成 · 蓝=进行中</div></div>
    <table>
      <thead><tr><th>#</th><th>里程碑</th><th>截止</th><th>状态</th><th>验收证据</th></tr></thead>
      <tbody>{''.join(milestone_row(x, i) for i, x in enumerate(milestones, 1))}</tbody>
    </table>
  </div>

  <div class="two">
    <div class="panel"><h2>阻断项 / 边界</h2><ul class="blockers">{blocker_html}</ul></div>
    <div class="panel"><h2>最近动作</h2><ul class="notes">{notes_html}</ul></div>
  </div>

  <div class="footer">
    <div>每日 08:05 自动重生成 · <code>python -X utf8 tools/ops/sentiment_product_dashboard.py --render</code></div>
    <div>状态文件 <code>deliverables/2026-11-12_情绪数据产品_看板状态.json</code></div>
  </div>
</div>
</body>
</html>
"""


def parse_pair(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise SystemExit(f"expected id=status, got {value!r}")
    key, status = value.split("=", 1)
    return key.strip(), status.strip().lower()


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="情绪数据产品每日看板")
    p.add_argument("--render", action="store_true", help="生成 HTML（默认）")
    p.add_argument("--set-signoff", action="append", default=[], metavar="ID=STATUS")
    p.add_argument("--set-milestone", action="append", default=[], metavar="ID=STATUS")
    p.add_argument("--note", action="append", default=[], help="追加一条动态，格式 YYYY-MM-DD HH:MM|文本")
    args = p.parse_args(argv)

    state = load_state()
    changed = False

    for raw in args.set_signoff:
        key, status = parse_pair(raw)
        for item in state["signoffs"]:
            if item["id"] == key:
                item["status"] = status
                changed = True
                break
        else:
            raise SystemExit(f"unknown signoff: {key}")

    for raw in args.set_milestone:
        key, status = parse_pair(raw)
        for item in state["milestones"]:
            if item["id"] == key:
                item["status"] = status
                changed = True
                break
        else:
            raise SystemExit(f"unknown milestone: {key}")

    for raw in args.note:
        if "|" in raw:
            at, text = raw.split("|", 1)
        else:
            at, text = now().strftime("%Y-%m-%d %H:%M"), raw
        state.setdefault("notes", []).append({"at": at.strip(), "text": text.strip()})
        changed = True

    if changed:
        save_state(state)

    html_text = render(state)
    HTML_FILE.write_text(html_text, encoding="utf-8")
    if changed:
        save_state(state)
    print(f"rendered: {HTML_FILE}")
    print(f"state:    {STATE_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
