# -*- coding: utf-8 -*-
"""采集即服务平台 · 实时面板构建器（原型态、不触网）

读取真实：
  - registry/sources.json        （源注册表 / RightsGate 授权口径）
  - store/observations.db        （obs_store 持久库；无则诚实空态）
  - 运行 pytest 取真实通过数
渲染为自包含 dashboard.html（light 主题 fallback，可离线双击打开）。

用法：python dashboard_build.py
"""
from __future__ import annotations

import html
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.contracts import Observation, SourceClock, UseCase
from core.canonical import rollup_by_entity
from core.quality_sla import compute_sla
from core.pii import scan_store_pii
from core.pit import pit_entity_state
from core.feedback import compute_sla_feedback, compute_pii_feedback, FeedbackActionType

ROOT = os.path.dirname(os.path.abspath(__file__))
SOURCES_JSON = os.path.join(ROOT, "registry", "sources.json")
DB = os.path.join(ROOT, "store", "observations.db")
SCHEDULER_STATUS = os.path.join(ROOT, "scheduler_status.json")
OUT = os.path.join(ROOT, "dashboard.html")

USE_CASES = [uc.value for uc in UseCase]


def load_sources() -> dict:
    if not os.path.exists(SOURCES_JSON):
        return {}
    with open(SOURCES_JSON, encoding="utf-8") as f:
        return json.load(f)


def load_store_rows() -> list[tuple]:
    if not os.path.exists(DB):
        return []
    try:
        conn = sqlite3.connect(DB)
        rows = conn.execute(
            "SELECT source_id, snapshot_id, entity_type, entity_id, collected_at, status, "
            "completeness, allowed_use_cases, source_field, payload_json, raw_sha256, "
            "source_payload_sha256 FROM observations"
        ).fetchall()
        conn.close()
        return rows
    except sqlite3.OperationalError:
        return []


def rebuild_observations(rows: list[tuple]) -> list[Observation]:
    out: list[Observation] = []
    for r in rows:
        (sid, snap, et, eid, collected_at, status, completeness, allowed,
         source_field, payload_json, raw_sha256, source_payload_sha256) = r
        try:
            payload = json.loads(payload_json)
        except Exception:
            payload = {}
        allowed_t = tuple(x for x in (allowed.split(",") if allowed else []) if x)
        out.append(Observation(
            source_id=sid, snapshot_id=snap, entity_type=et, entity_id=eid,
            clock=SourceClock(collected_at=collected_at),
            status=status, completeness=completeness,
            allowed_use_cases=allowed_t, source_field=source_field,
            payload=payload, raw_sha256=raw_sha256,
            source_payload_sha256=source_payload_sha256,
        ))
    return out


def run_pytest_passed() -> str:
    try:
        res = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"], cwd=ROOT,
            capture_output=True, text=True, timeout=120,
        )
        txt = res.stdout + res.stderr
        m = re.search(r"(\d+)\s+passed", txt)
        if m:
            passed = int(m.group(1))
            failed = re.search(r"(\d+)\s+failed", txt)
            if failed:
                return f"{passed} passed / {failed.group(1)} failed"
            return f"{passed} passed"
    except Exception:
        pass
    return "—"


def esc(x) -> str:
    return html.escape(str(x), quote=True)


def render_metrics(sources: dict, db_rows: list, domains: set, test_str: str) -> str:
    n_src = len(sources)
    # 统计被任意源开启的用途（rights 中显式 true）
    on_uc = set()
    for s in sources.values():
        for uc, v in (s.get("rights") or {}).items():
            if v is True:
                on_uc.add(uc)
    n_rights = len(on_uc)
    ns_label = f"{len(domains)}" if domains else "待采集"
    cards = [
        ("已登记源", str(n_src)),
        ("闸门授权用途", f"{n_rights} / {len(USE_CASES)}"),
        ("跨源实体命名空间", ns_label),
        ("测试", test_str),
    ]
    out = ['<div class="grid4">']
    for label, num in cards:
        cls = "num"
        if "passed" in num and "failed" not in num:
            cls = "num ok"
        out.append(
            f'<div class="metric"><div class="label">{esc(label)}</div>'
            f'<div class="{cls}">{esc(num)}</div></div>'
        )
    out.append("</div>")
    return "\n".join(out)


def render_sources(sources: dict) -> str:
    out = ['<div style="background:var(--color-background-primary);border:0.5px solid var(--color-border-tertiary);'
           'border-radius:var(--border-radius-lg);padding:1rem 1.25rem;margin-bottom:16px;">']
    out.append('<div style="font-size:14px;font-weight:500;margin-bottom:12px;">源注册表 · SourceRegistry</div>')
    out.append('<div class="row hdr"><div>源</div><div>owner</div><div>已授权用途</div><div>状态</div></div>')
    for sid, s in sources.items():
        name = s.get("name", sid)
        owner = s.get("owner", "UNOWNED")
        rights = [k for k, v in (s.get("rights") or {}).items() if v is True]
        enabled = s.get("enabled", True)
        status = "ok" if enabled else "disabled"
        status_cls = "ok" if enabled else "danger"
        rights_txt = " · ".join(rights) if rights else "（无）"
        out.append(
            f'<div class="row src"><div><b>{esc(sid)}</b><br><span class="sub">{esc(name)}</span></div>'
            f'<div>{esc(owner)}</div><div>{esc(rights_txt)}</div>'
            f'<div class="{status_cls}">{esc(status)}</div></div>'
        )
    out.append("</div>")
    return "\n".join(out)


def render_gate(sources: dict) -> str:
    on_uc = set()
    for s in sources.values():
        for uc, v in (s.get("rights") or {}).items():
            if v is True:
                on_uc.add(uc)
    out = ['<div style="background:var(--color-background-primary);border:0.5px solid var(--color-border-tertiary);'
           'border-radius:var(--border-radius-lg);padding:1rem 1.25rem;">']
    out.append('<div style="font-size:14px;font-weight:500;margin-bottom:12px;">RightsGate 闸门状态 · 默认拒绝</div>')
    out.append('<div style="display:flex;flex-wrap:wrap;gap:6px;">')
    for uc in USE_CASES:
        if uc in on_uc:
            out.append(f'<span class="badge b-on">{esc(uc)}</span>')
        else:
            out.append(f'<span class="badge b-off">{esc(uc)} · 锁定</span>')
    out.append('</div>')
    out.append('<div class="note">training / redistribution / commercial 等高风险用途须 RightsGate 显式批准，不随入湖自动开放</div>')
    out.append("</div>")
    return "\n".join(out)


def render_runs(rows: list) -> str:
    if not rows:
        return ('<div style="background:var(--color-background-primary);border:0.5px solid var(--color-border-tertiary);'
                'border-radius:var(--border-radius-lg);padding:1rem 1.25rem;color:var(--color-text-secondary);">'
                '最近采集运行：暂无记录（原型态，未触网）。运行 seed_demo_store.py 或真实采集后此处出现状态分账。</div>')
    conn = sqlite3.connect(DB)
    data = conn.execute(
        "SELECT source_id, status, COUNT(*) FROM observations GROUP BY source_id, status ORDER BY source_id"
    ).fetchall()
    conn.close()
    by_src: dict[str, Counter] = {}
    for sid, status, cnt in data:
        by_src.setdefault(sid, Counter())[status] += cnt
    out = ['<div style="background:var(--color-background-primary);border:0.5px solid var(--color-border-tertiary);'
           'border-radius:var(--border-radius-lg);padding:1rem 1.25rem;">']
    out.append('<div style="font-size:14px;font-weight:500;margin-bottom:12px;">最近采集运行 · 状态分账</div>')
    out.append('<div class="row hdr"><div>源</div><div>状态分布</div><div>观测数</div></div>')
    status_color = {"complete": "ok", "empty": "muted", "unavailable": "danger",
                    "incomplete": "muted", "disabled": "muted"}
    for sid, cntr in sorted(by_src.items()):
        total = sum(cntr.values())
        dist = " ".join(f'{st}×{c}' for st, c in cntr.most_common())
        out.append(
            f'<div class="row run"><div>{esc(sid)}</div>'
            f'<div class="muted">{esc(dist)}</div><div>{total}</div></div>'
        )
    out.append('</div>')
    return "\n".join(out)


def load_scheduler() -> dict | None:
    if not os.path.exists(SCHEDULER_STATUS):
        return None
    try:
        with open(SCHEDULER_STATUS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def render_scheduler(status: dict | None) -> str:
    if not status:
        return ('<div style="background:var(--color-background-primary);border:0.5px solid var(--color-border-tertiary);'
                'border-radius:var(--border-radius-lg);padding:1rem 1.25rem;color:var(--color-text-secondary);">'
                '调度与熔断：未运行（原型态）。运行 examples/run_scheduler_demo.py 生成 scheduler_status.json 后此处显示多源节奏与熔断态。</div>')
    circ_cls = {"closed": "circ-closed", "half_open": "circ-half_open",
                "open": "circ-open", "unknown": "muted"}
    out = ['<div style="background:var(--color-background-primary);border:0.5px solid var(--color-border-tertiary);'
           'border-radius:var(--border-radius-lg);padding:1rem 1.25rem;">']
    out.append(f'<div style="font-size:14px;font-weight:500;margin-bottom:12px;">调度与熔断 · Scheduler（tick={esc(status.get("tick_count","—"))} · ledger={esc(status.get("ledger_count","—"))}）</div>')
    out.append('<div class="row hdr"><div>源</div><div>熔断态</div><div>连续失败</div><div>freshness/max_fail</div></div>')
    for s in status.get("sources", []):
        c = circ_cls.get(s.get("circuit", "unknown"), "muted")
        out.append(
            f'<div class="row sched"><div><b>{esc(s["source_id"])}</b><br>'
            f'<span class="sub">{esc(s.get("name",""))}</span></div>'
            f'<div class="{c}">{esc(s.get("circuit",""))}</div>'
            f'<div>{esc(s.get("consecutive_failures",0))}</div>'
            f'<div class="muted">{esc(s.get("freshness_minutes","—"))}m / {esc(s.get("max_failures","—"))}</div></div>'
        )
    out.append('<div class="note">熔断态：closed=健康 / half_open=有过失败未达阈值 / open=已禁用或达 max_failures（连续失败指数退避，封顶基线×8）。本状态为合成调度演示，不触网。</div>')
    out.append("</div>")
    return "\n".join(out)


def build_circuit_map(status: dict | None) -> dict:
    if not status:
        return {}
    out: dict = {}
    for s in status.get("sources", []):
        out[s.get("source_id")] = {
            "circuit": s.get("circuit", "unknown"),
            "consecutive_failures": s.get("consecutive_failures", 0),
        }
    return out


def render_quality_sla(sla_reports: dict, pii_counts: dict) -> str:
    state_cls = {"ok": "state-ok", "stale": "state-stale",
                 "breach": "state-breach", "no_data": "state-no_data"}
    out = ['<div style="background:var(--color-background-primary);border:0.5px solid var(--color-border-tertiary);'
           'border-radius:var(--border-radius-lg);padding:1rem 1.25rem;">']
    out.append('<div style="font-size:14px;font-weight:500;margin-bottom:12px;">质量 SLA / 合规 · 新鲜度与 PII 脱敏</div>')
    out.append('<div class="row hdr"><div>源</div><div>新鲜度态</div><div>age/fresh</div>'
               '<div>空观测比</div><div>熔断</div><div>PII命中</div></div>')
    for sid, r in sla_reports.items():
        rd = r.to_dict()
        sc = state_cls.get(rd["state"], "muted")
        age = "—" if rd["age_minutes"] is None else f'{rd["age_minutes"]:.0f}m'
        fresh = f'{rd["freshness_minutes"]}m' if rd["freshness_minutes"] else "不限"
        empty = f'{rd["empty_ratio"]*100:.0f}%' if rd["observation_count"] else "—"
        pii = pii_counts.get(sid, 0)
        pii_cls = "danger" if pii else "muted"
        out.append(
            f'<div class="row qa"><div><b>{esc(sid)}</b></div>'
            f'<div class="{sc}">{esc(rd["state_label"])}</div>'
            f'<div class="muted">{esc(age)}/{esc(fresh)}</div>'
            f'<div class="muted">{esc(empty)}</div>'
            f'<div class="muted">{esc(rd["circuit"])}</div>'
            f'<div class="{pii_cls}">{pii}</div></div>'
        )
    pii_total = sum(pii_counts.values())
    out.append(f'<div class="note">新鲜度态：达标≤freshness / 滞后≤2×freshness / 违约&gt;2×freshness（按 sources.json 真实阈值）。'
               f'PII：入湖前对 email/手机号/身份证脱敏，仅计数不落明文；本面板 PII 命中合计 {pii_total}（合成数据通常 0，接真实采集后反映真实命中）。'
               f'注：seed 时间戳为 2026-10-21/22，相对真实 now 已超 freshness，故多源正确显示违约——证明告警引擎有效。</div>')
    out.append("</div>")
    return "\n".join(out)


_ACTION_LABEL = {
    FeedbackActionType.EXPEDITE_RETRY.value: "加速补采",
    FeedbackActionType.OPEN_CIRCUIT.value: "熔断开闸",
    FeedbackActionType.DOWNGRADE_RIGHTS.value: "合规降级",
    FeedbackActionType.FLAG_REVIEW.value: "人工复核",
    FeedbackActionType.CLEAR_REVIEW.value: "解除复核",
}
_ACTION_CLS = {
    FeedbackActionType.EXPEDITE_RETRY.value: "state-stale",
    FeedbackActionType.OPEN_CIRCUIT.value: "state-breach",
    FeedbackActionType.DOWNGRADE_RIGHTS.value: "circ-open",
    FeedbackActionType.FLAG_REVIEW.value: "muted",
}


def render_feedback_and_cron(sla_reports: dict, pii_counts: dict) -> str:
    """分析→采集反馈环 + 统一 cron 实体章节。

    仅调用纯 compute（compute_sla_feedback / compute_pii_feedback），不落盘、不污染真实
    sources.json；真实副作用只在生产 CronRunner 的 engine.run 内发生。
    """
    sla_actions, _ = compute_sla_feedback(sla_reports, {})
    pii_actions = compute_pii_feedback(pii_counts)
    all_actions = sla_actions + pii_actions

    out = ['<div style="background:var(--color-background-primary);border:0.5px solid var(--color-border-tertiary);'
           'border-radius:var(--border-radius-lg);padding:1rem 1.25rem;margin-bottom:16px;">']
    out.append('<div style="font-size:14px;font-weight:500;margin-bottom:12px;">分析→采集反馈环 · 闭环治理</div>')

    if not all_actions:
        out.append('<div class="muted" style="font-size:13px;">本期分析层未产出反馈动作：各源新鲜度达标且无 PII 命中，采集层无需干预。</div>')
    else:
        out.append('<div class="row hdr"><div>源</div><div>反馈动作</div><div>触发原因</div></div>')
        for a in all_actions:
            cls = _ACTION_CLS.get(a.action.value, "muted")
            out.append(
                f'<div class="row fb"><div><b>{esc(a.source_id)}</b></div>'
                f'<div class="{cls}">{esc(_ACTION_LABEL.get(a.action.value, a.action.value))}</div>'
                f'<div class="muted">{esc(a.reason)}</div></div>'
            )

    out.append('<div class="note">闭环逻辑：质量 SLA 违约/滞后/无数据 → <b>加速补采</b>（强制下次到期=现在）；'
               '连续违约达阈值 → <b>熔断开闸</b>自动禁用 + 人工复核；PII 命中 → <b>合规降级</b>'
               '（即便曾授权也撤 training/redistribution/commercial）+ 人工复核。strike 计数等会话态由生产 '
               'CronRunner 的 FeedbackEngine 持有，本面板仅做单帧推算、不写源文件。</div>')

    # 统一 cron 实体说明
    out.append('<div style="font-size:14px;font-weight:500;margin:14px 0 10px;">统一 cron 实体 · CronRunner</div>')
    out.append('<div class="row hdr"><div>职责</div><div>实现</div></div>')
    cron_rows = [
        ("到期派发", "tick(now) 取 Scheduler.due_sources() → 经 CollectDispatcher 采集 → mark_collected 推进"),
        ("分析反馈环", "每 tick 末跑 analyzer(SLA/PII) → FeedbackEngine.run 落反馈动作，闭环"),
        ("部署形态", "run_forever(poll_interval) 真实轮询；now_provider/sleep_fn 可注入，单测用虚拟时钟"),
        ("收敛范围", "替代 Orchestrator.as_scheduler 与分散 demo 循环，单一实体驱动采集"),
    ]
    for role, impl in cron_rows:
        out.append(f'<div class="row cron"><div><b>{esc(role)}</b></div>'
                   f'<div class="muted">{esc(impl)}</div></div>')
    out.append('<div class="note">CronRunner 自身不触网；触网只在注入的 dispatcher 内，且须 RightsGate 授权。'
               '同一份代码既能单元测试（手动 tick + 注入 now），也能生产部署。</div>')
    out.append('</div>')
    return "\n".join(out)


def render_xq_consolidation() -> str:
    """雪球收口终局章节：三层收敛 + 归档盘点 + B1-b 待批状态（静态事实，读自代码结构）。"""
    layers = [
        ("解析（嵌套/扁平归一化）", "xq_pump.extractors._parse", "XqAdapter._normalize 委托", "8cabea0e"),
        ("传输（HTTP 栈/限速/token）", "xq_pump.core.LiveSession", "LiveXqFetcher 传输委托+治理壳", "609294b4"),
        ("调度/存储（行级+治理双写）", "bridges.XqPumpDispatcher", "CollectDispatcher 协议+Observation", "34d816b2"),
    ]
    out = ['<div style="background:var(--color-background-primary);border:0.5px solid var(--color-border-tertiary);'
           'border-radius:var(--border-radius-lg);padding:1rem 1.25rem;margin-bottom:16px;">']
    out.append('<div style="font-size:14px;font-weight:500;margin-bottom:4px;">雪球收口终局 · 三层单源化（2026-11-01）</div>')
    out.append('<div class="sub" style="margin-bottom:10px;">数据面（行级增量）= xq_pump；治理面（Observation/RightsGate/SLA/PII/反馈环）= intel_collector</div>')
    out.append('<div class="row hdr"><div>层</div><div>单一真源</div><div>intel 侧</div><div>提交</div></div>')
    for name, src, deleg, commit in layers:
        out.append(
            f'<div class="row" style="grid-template-columns:1.5fr 1.8fr 2fr 0.8fr;">'
            f'<div>{name}</div><div style="font-family:monospace;font-size:12px;color:var(--color-text-info);">{src}</div>'
            f'<div class="muted">{deleg}</div><div class="sub">{commit}</div></div>')
    arch = [
        ("A 类·治理外壳", "xq_grab_browser / xq_login / xq_token_refresh", "e579d9b5"),
        ("D①·归一化合一", "xq_ticker_norm 单源 + tools shim + 迁移脚本×2", "24b3ed92"),
        ("B1-a·comments 迁移", "extractors/comments.py（search.json 分页+复合 seen）", "113880cc"),
        ("D 组·死代码/探针", "backfill_auto/seed_parallel + 时间线探针×5", "728f83bc / a2617e4a"),
    ]
    out.append('<div style="font-size:13px;font-weight:500;margin:12px 0 6px;">归档盘点 · _archive_legacy_2026-11/（19 .py，全部带随迁测试可复验）</div>')
    out.append('<div class="row hdr" style="grid-template-columns:1.4fr 2.6fr 0.9fr;"><div>批次</div><div>内容</div><div>提交</div></div>')
    for name, content, commit in arch:
        out.append(
            f'<div class="row" style="grid-template-columns:1.4fr 2.6fr 0.9fr;">'
            f'<div>{name}</div><div class="muted">{content}</div><div class="sub">{commit}</div></div>')
    out.append(
        '<div style="margin-top:12px;padding:8px 12px;border-radius:8px;background:var(--color-background-info);'
        'font-size:13px;color:var(--color-text-info);">'
        'B1-b（调度切换 xueqiu_crawl → xq_pump）执行件已就绪（start_xq_pump.ps1，BOM/语法/DRY 自测通过）；'
        '三前置闸：XQ_COOKIE 凭据 → aios 真实冒烟 → 老田五要素签字（见归档决策文档 runbook）。</div>')
    out.append('</div>')
    return "\n".join(out)


def render_rollup(groups: dict) -> str:
    if not groups:
        return ('<div style="background:var(--color-background-primary);border:0.5px solid var(--color-border-tertiary);'
                'border-radius:var(--border-radius-lg);padding:1rem 1.25rem;color:var(--color-text-secondary);">'
                '跨源实体归并：待采集。运行采集后 core/canonical 将按 (domain,key) 聚合多源覆盖。</div>')
    out = ['<div style="background:var(--color-background-primary);border:0.5px solid var(--color-border-tertiary);'
           'border-radius:var(--border-radius-lg);padding:1rem 1.25rem;">']
    out.append('<div style="font-size:14px;font-weight:500;margin-bottom:12px;">跨源实体归并预览 · canonical rollup</div>')
    out.append('<div class="row hdr"><div>规范实体</div><div>跨源覆盖</div><div>观测数</div></div>')
    for (domain, key), r in sorted(groups.items(), key=lambda kv: (-kv[1].observation_count, kv[0][0])):
        d = r.to_dict()
        srcs = " + ".join(d["sources"])
        out.append(
            f'<div class="row roll"><div style="font-weight:500;color:var(--color-text-info);">'
            f'{esc(domain)}:{esc(key)}</div><div>{esc(srcs)}</div><div>{d["observation_count"]}</div></div>'
        )
    out.append('<div class="note">命名空间隔离：A 股归 astock，系统外归 yt，杜绝跨域误并。下游只认 (domain,key) 聚合键。</div>')
    out.append("</div>")
    return "\n".join(out)


def _demo_pit_states():
    """合成时点演示：构造跨 10-21/10-22 的 SH600519 观测，演示时间旅行。"""
    def mk(sid, payload, ts, status="complete"):
        return Observation(
            source_id=sid, snapshot_id=f"pit-{sid}-{ts}", entity_type="post",
            entity_id="SH600519", clock=SourceClock(collected_at=ts), status=status,
            completeness="exhausted", payload=payload, raw_sha256="x",
            source_payload_sha256="y", allowed_use_cases=("internal_retention",),
            source_field="feed",
        )
    demo = [
        mk("guba", {"code6": "SH600519"}, "2026-10-21T09:00:00Z"),
        mk("news", {"items": [{"title": "贵州茅台 600519 三季报超预期"}]}, "2026-10-21T10:00:00Z"),
        mk("xueqiu", {"target": "SH600519"}, "2026-10-22T09:00:00Z"),
        mk("guba", {"code6": "SH600519"}, "2026-10-22T12:00:00Z", status="empty"),
    ]
    e = pit_entity_state(demo, "2026-10-21T23:59:59Z", "astock", "SH600519")
    l = pit_entity_state(demo, "2026-10-22T23:59:59Z", "astock", "SH600519")
    return e, l


def render_pit(early, late) -> str:
    def col(st, as_of):
        d = st.to_dict()
        srcs = " + ".join(d["sources"]) if d["sources"] else "—"
        sts = " ".join(f"{k}×{v}" for k, v in d["statuses"].items()) if d["statuses"] else "—"
        return (f'<div class="pitcol"><div class="pit-asof">as_of {esc(as_of)}</div>'
                f'<div class="pit-row"><span>存在</span><b>{esc(st.exists)}</b></div>'
                f'<div class="pit-row"><span>观测数</span><b>{d["observation_count"]}</b></div>'
                f'<div class="pit-row"><span>覆盖源</span><b>{esc(d["source_count"])}</b></div>'
                f'<div class="pit-row"><span>源集合</span><b>{esc(srcs)}</b></div>'
                f'<div class="pit-row"><span>状态</span><b>{esc(sts)}</b></div>'
                f'<div class="pit-row"><span>时间窗</span><b>{esc(d["first_collected"])}~{esc(d["last_collected"])}</b></div>'
                f'</div>')
    gained = sorted(set(late.sources) - set(early.sources))
    gained_txt = "、".join(gained) if gained else "无新增"
    out = ['<div style="background:var(--color-background-primary);border:0.5px solid var(--color-border-tertiary);'
           'border-radius:var(--border-radius-lg);padding:1rem 1.25rem;">']
    out.append(f'<div style="font-size:14px;font-weight:500;margin-bottom:12px;">'
               f'时点回溯 · PIT 时间旅行（实体 astock:SH600519）</div>')
    out.append('<div class="pitgrid">')
    out.append(col(early, "2026-10-21T23:59:59Z"))
    out.append(col(late, "2026-10-22T23:59:59Z"))
    out.append('</div>')
    out.append(f'<div class="note">时间旅行：给定 as_of 只可见该时点及之前已采集的观测。'
               f'晚时点相对早时点<b>新增覆盖源：{esc(gained_txt)}</b>，观测数 '
               f'{early.observation_count}→{late.observation_count}。这是对 obs_store.as_of（单源单实体）的升级——'
               f'面向下游的「全局时点快照」。本块为合成演示数据，不触网。</div>')
    out.append("</div>")
    return "\n".join(out)


def build() -> None:
    sources = load_sources()
    rows = load_store_rows()
    obs = rebuild_observations(rows)
    groups = rollup_by_entity(obs) if obs else {}
    domains = {k[0] for k in groups.keys()}
    test_str = run_pytest_passed()
    sched_status = load_scheduler()
    circuit_map = build_circuit_map(sched_status)
    now_dt = datetime.now(timezone.utc)
    sla_reports = compute_sla(sources, obs, now_dt, circuit_map) if sources else {}
    pii_counts = scan_store_pii(obs)
    now = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    feedback_html = render_feedback_and_cron(sla_reports, pii_counts)
    pit_early, pit_late = _demo_pit_states()

    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>采集即服务平台 · 实时面板</title>
<style>
  :root{{
    --color-background-primary:#ffffff;
    --color-background-secondary:#f1efea;
    --color-background-tertiary:#e9e7e1;
    --color-background-info:#e6f1fb;
    --color-background-success:#eaf3de;
    --color-background-danger:#fcebeb;
    --color-text-primary:#2c2c2a;
    --color-text-secondary:#5f5e5a;
    --color-text-tertiary:#888780;
    --color-text-info:#0c447c;
    --color-text-success:#3b6d11;
    --color-text-danger:#a32d2d;
    --color-border-tertiary:rgba(0,0,0,0.15);
    --color-border-secondary:rgba(0,0,0,0.3);
    --border-radius-md:8px;
    --border-radius-lg:12px;
    --font-sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  }}
  body{{font-family:var(--font-sans);color:var(--color-text-primary);margin:0;padding:24px;background:#f7f6f3;}}
  h1{{font-size:18px;font-weight:500;margin:0 0 4px;}}
  .lead{{font-size:13px;color:var(--color-text-secondary);margin:0 0 16px;}}
  .grid4{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px;}}
  .metric{{background:var(--color-background-secondary);border-radius:var(--border-radius-md);padding:1rem;}}
  .metric .label{{font-size:13px;color:var(--color-text-secondary);}}
  .metric .num{{font-size:24px;font-weight:500;margin-top:4px;}}
  .row{{display:grid;gap:8px;font-size:13px;padding:8px 0;border-bottom:0.5px solid var(--color-border-tertiary);}}
  .row:last-child{{border-bottom:none;}}
  .hdr{{grid-template-columns:1.4fr 1fr 2fr 0.7fr;font-size:12px;color:var(--color-text-secondary);padding-bottom:6px;font-weight:500;}}
  .src{{grid-template-columns:1.4fr 1fr 2fr 0.7fr;}}
  .run{{grid-template-columns:1.3fr 1fr 0.8fr;}}
  .roll{{grid-template-columns:1.2fr 2fr 0.8fr;}}
  .sched{{grid-template-columns:1.4fr 1fr 0.8fr 1.2fr;}}
  .qa{{grid-template-columns:1.6fr 0.9fr 1.2fr 0.9fr 0.8fr 0.8fr;}}
  .fb{{grid-template-columns:1.2fr 0.9fr 2.4fr;}}
  .cron{{grid-template-columns:1fr 3fr;}}
  .state-ok{{color:var(--color-text-success);font-weight:500;}}
  .state-stale{{color:#b5790b;font-weight:500;}}
  .state-breach{{color:var(--color-text-danger);font-weight:500;}}
  .state-no_data{{color:var(--color-text-tertiary);}}
  .circ-closed{{color:var(--color-text-success);font-weight:500;}}
  .circ-half_open{{color:#b5790b;font-weight:500;}}
  .circ-open{{color:var(--color-text-danger);font-weight:500;}}
  .sub{{font-size:11px;color:var(--color-text-tertiary);}}
  .pitgrid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:4px;}}
  .pitcol{{background:var(--color-background-secondary);border-radius:var(--border-radius-md);padding:0.75rem 0.9rem;}}
  .pit-asof{{font-size:12px;color:var(--color-text-info);font-weight:500;margin-bottom:8px;}}
  .pit-row{{display:flex;justify-content:space-between;gap:10px;font-size:13px;padding:2px 0;}}
  .pit-row span{{color:var(--color-text-secondary);}}
  .pit-row b{{color:var(--color-text-primary);font-weight:500;}}
  .muted{{color:var(--color-text-secondary);}}
  .ok{{color:var(--color-text-success);}}
  .danger{{color:var(--color-text-danger);}}
  .badge{{font-size:12px;padding:3px 8px;border-radius:6px;}}
  .b-on{{background:var(--color-background-success);color:var(--color-text-success);}}
  .b-off{{background:var(--color-background-tertiary);color:var(--color-text-tertiary);}}
  .note{{font-size:12px;color:var(--color-text-secondary);margin-top:10px;}}
  .cols2{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;}}
  footer{{font-size:12px;color:var(--color-text-tertiary);margin-top:24px;border-top:0.5px solid var(--color-border-tertiary);padding-top:12px;}}
</style>
</head>
<body>
<h1>采集即服务平台 · 实时面板</h1>
<p class="lead">数据来自真实 registry/sources.json 与 store/observations.db（原型态 · 不触网）。生成于 {esc(now)}</p>

{render_metrics(sources, rows, domains, test_str)}

{render_sources(sources)}

<div class="cols2">
  {render_gate(sources)}
  {render_runs(rows)}
</div>

{render_rollup(groups)}

{render_pit(pit_early, pit_late)}

{render_scheduler(sched_status)}

{render_quality_sla(sla_reports, pii_counts)}

{feedback_html}

{render_xq_consolidation()}

<footer>
  采集即服务平台 intel_collector 原型控制台 · 数据均为 synthetic 或登记元数据，未触网、不含真实外部来源内容。
  RightsGate 默认拒绝；training / redistribution / commercial 须显式批准。xueqiu_live 真实采集需 XQ_LIVE_ARMED + 真实 cookie 并经 RightsGate 授权。
</footer>
</body>
</html>
"""
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html_doc)
    n_rights_total = sum(
        1 for s in sources.values() for k, v in (s.get("rights") or {}).items() if v
    )
    print(f"已生成面板：{OUT}")
    print(f"  源={len(sources)} 授权用途={n_rights_total} "
          f"观测={len(rows)} 实体={len(groups)} 测试={test_str}")


if __name__ == "__main__":
    build()
