# -*- coding: utf-8 -*-
"""K-STOCK-163 · 交易计划审查层（经理盘前审批单）

背景：页面/账本此前把全量 pending 买入意图（几十笔）直接倾泻，「太长了」。
交易计划改为先审查再出计划：经理盘前把全量意图审一遍，产出「今日交易
计划审批单」——批准买入 Top-N（5-8 只，每只附理由与建议仓位）+ 驳回清单
（归类原因：信心不足/行业集中/风险敞口/流动性）。

数据流：
  collect_context(pf_id)
    → 读当日最新 merged run 产物（k_stock/data/poc_<pf_id>_<date>_*.json 取最新，
      shape 为 list，项含 stock/_tier/_src_strategy/outputs.D1.confidence）
    + 该组合交易员账本 pending 买入单（portal/data/strategy_subscriptions.json
      的 trader → paper.db orders WHERE account_id=<trader> AND status='pending'）
    → 压缩成文本摘要（总笔数/tier 分布/Top 信心标的 15 只）
  review_plan(pf_id, manager_id, source)
    → 组装 LLM 消息（system=经理档案 role_prompt+审查指令，user=意图清单摘要）
    → gaorta_client.chat_for_hire 生成审查意见（要求输出 JSON 审批单）
    → 宽松解析（fenced JSON 块/首尾大括号截取）；解析失败降级：整文存 body、
      approved 空表（不静默丢），parsed=false 如实标记
    → 原子写 k_stock/data/plan_review/<YYYYMMDD>_<pf_id>.json
    → 返回审批单 dict

fail-closed：意图清单为空 / 经理档案或雇佣校验拒绝 / LLM 不可达 →
PlanReviewError，不落半截审批单。解析失败不 raise（降级留痕）。

智谱兜底：与 meeting 同款——gaorta key 类失败按 meeting_fallback.json 门控
走 LLMClient 智谱（直接复用 meeting._zhipu_fallback_allowed/_zhipu_model_name/
_try_zhipu_fallback，不复制逻辑）。

独立产物：审批单不走 minutes 投递（与会议纪要解耦），portal 只读端点
GET /api/plan_review?pf_id= 直接投影当日 JSON。

用法：
  python plan_review.py review pf_001 laotian_pm   # 审查并落审批单
  python plan_review.py show   pf_001 [YYYYMMDD]   # 查看审批单
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import gaorta_client  # noqa: E402
import manager  # noqa: E402
import meeting  # noqa: E402  # 复用智谱兜底链（勿复制逻辑）

DATA_DIR = HERE.parent / "data"
OUT_DIR = DATA_DIR / "plan_review"
SUBS_PATH = HERE.parent.parent / "portal" / "data" / "strategy_subscriptions.json"
DB_PATH = DATA_DIR / "paper.db"

TOP_CONTEXT = 15      # 意图清单摘要里的 Top 信心标的数
TOP_APPROVED = 8      # 审批单建议批准上限（写入指令，LLM 可少批不可超）
REASON_CODES = ("信心不足", "行业集中", "风险敞口", "流动性")

SCHEMA = "k-stock-163/plan-review-v0.1"

_REVIEW_INSTRUCTIONS = (
    "现在进行今日交易计划审查。输入是全量买入意图清单摘要（策略产物 + 待撮合挂单）。"
    f"请审查并输出今日审批单：批准买入 Top-N（最多 {TOP_APPROVED} 只，须给出理由与"
    "建议仓位，如 8%）；其余全部归入驳回清单（reason_code 从"
    "「信心不足/行业集中/风险敞口/流动性」四类中选）。最后一句话总结。\n"
    "输出要求：只输出一个 JSON 对象，不要多余文字，格式：\n"
    '{"approved": [{"stock": "代码", "reason": "理由", "weight": "建议仓位如 8%"}],'
    ' "rejected": [{"stock": "代码", "reason_code": "四类之一"}],'
    ' "summary": "一句话总结"}'
)


class PlanReviewError(Exception):
    """计划审查线业务失败（清单为空/校验拒绝/LLM 不可达），fail-closed 不落半截。"""


# ── collect_context：意图清单文本摘要 ────────────────────────────

def _today_tag(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y%m%d")


def _latest_merged_run(pf_id: str, now: datetime | None = None) -> Path | None:
    """当日最新 merged run 产物（poc_<pf_id>_<YYYYMMDD>_<HHMM>.json 口径）。"""
    tag = _today_tag(now)
    pat = re.compile(rf"^poc_{re.escape(pf_id)}_(\d{{8}})_\d{{4}}\.json$")
    hits = [p for p in DATA_DIR.glob(f"poc_{pf_id}_*.json")
            if (m := pat.match(p.name)) and m.group(1) == tag]
    return max(hits, key=lambda p: p.name) if hits else None


def _pf_trader(pf_id: str) -> str | None:
    """组合 → 交易员账号（订阅清单口径；缺清单/缺组合静默 None）。"""
    try:
        d = json.loads(SUBS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for p in d.get("portfolios") or []:
        if p.get("id") == pf_id:
            return p.get("trader")
    return None


def _pending_buys(trader: str) -> list[dict]:
    """交易员账本 pending 买入单（sqlite ro；失败静默空表——摘要缺一块不致命）。"""
    if not DB_PATH.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT stock, qty, conf, strategy, plan_date FROM orders "
                "WHERE account_id=? AND status='pending' AND side='BUY' "
                "ORDER BY id", (trader,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    return [{"stock": r[0], "qty": r[1], "conf": r[2],
             "strategy": r[3], "plan_date": r[4]} for r in rows]


def collect_context(pf_id: str, now: datetime | None = None) -> str:
    """全量买入意图 → 文本摘要（总笔数/tier 分布/Top 信心标的）。

    无当日产物且无 pending → 返回 ""（调用方自行决定降级；conductor 静默跳过）。
    """
    now = now or datetime.now()
    run = _latest_merged_run(pf_id, now)
    items: list[dict] = []
    if run:
        try:
            raw = json.loads(run.read_text(encoding="utf-8"))
            items = [it for it in raw
                     if isinstance(it, dict) and it.get("stock")
                     and ((it.get("outputs") or {}).get("D1") or {}).get("action") == "BUY"]
        except (OSError, ValueError):
            items = []
    trader = _pf_trader(pf_id)
    pend = _pending_buys(trader) if trader else []

    if not items and not pend:
        return ""

    lines = [f"【今日买入意图清单摘要】组合 {pf_id}（{now:%Y-%m-%d}）"]
    src_note = f"产物 {run.name}" if run else "无当日 merged run 产物"
    lines.append(f"意图总笔数 {len(items)}（{src_note}）+ 待撮合 pending 买入 "
                 f"{len(pend)} 笔（账本 {trader or '未知'}）")
    if items:
        tiers: dict[str, int] = {}
        for it in items:
            t = str(it.get("_tier") or "未分诊")
            tiers[t] = tiers.get(t, 0) + 1
        lines.append("tier 分布：" + " ".join(
            f"{t}={n}" for t, n in sorted(tiers.items())))

        def _conf(it):
            return float(((it.get("outputs") or {}).get("D1") or {})
                         .get("confidence") or 0.0)
        top = sorted(items, key=_conf, reverse=True)[:TOP_CONTEXT]
        lines.append(f"Top 信心标的（{len(top)}）：")
        for it in top:
            d1 = (it.get("outputs") or {}).get("D1") or {}
            lines.append(f"  {it['stock']} conf={_conf(it):.2f} "
                         f"src={it.get('_src_strategy') or '-'}"
                         + (f"｜{str(d1.get('reason'))[:40]}"
                            if d1.get("reason") else ""))
    if pend:
        lines.append("pending 买入单（原样列出，供审查去重）：")
        for o in pend[:TOP_CONTEXT]:
            lines.append(f"  {o['stock']} qty={o['qty']} conf={o['conf']} "
                         f"strategy={o['strategy']}")
    return "\n".join(lines)


# ── review_plan：审查 → 审批单产物 ───────────────────────────────

def _parse_review_json(text: str) -> dict | None:
    """宽松提取 LLM 输出里的审批单 JSON（fenced 块优先，其次首尾大括号截取）。"""
    if not text:
        return None
    candidates = []
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        candidates.append(m.group(1))
    i, j = text.find("{"), text.rfind("}")
    if i != -1 and j > i:
        candidates.append(text[i:j + 1])
    candidates.append(text.strip())
    for c in candidates:
        try:
            d = json.loads(c)
        except (ValueError, TypeError):
            continue
        if isinstance(d, dict) and isinstance(d.get("approved"), list):
            return d
    return None


def _atomic_write_json(fp: Path, obj: dict) -> None:
    """原子写：同目录 tmp + os.replace（进程被杀不留半截文件）。"""
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.with_suffix(fp.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, fp)


def review_plan(pf_id: str, manager_id: str,
                source: str | None = None) -> dict:
    """审查当日买入意图，生成并落盘审批单。返回审批单 dict。"""
    source = source or pf_id
    context = collect_context(pf_id)
    if not context:
        raise PlanReviewError(
            f"plan_review {pf_id}: 无可用买入意图清单"
            f"（无当日 poc_{pf_id}_* merged run 产物且无 pending 挂单），拒绝空审")

    # 经理档案 + active 雇佣校验（同 meeting.hold_meeting 口径）
    try:
        profile = manager.load_profile(manager_id)
    except manager.Reject as e:
        raise PlanReviewError(f"plan_review {pf_id}: 经理校验拒绝 → {e}") from e
    active = [h for h in manager.hires_list()
              if h.get("manager") == manager_id and h.get("source") == source
              and h.get("status") == "active"]
    if not active:
        raise PlanReviewError(
            f"plan_review {pf_id}: 无 {manager_id}×{source} 的 active 雇佣"
            f"（账本 {manager.HIRES_PATH}），须先由经理雇佣建立")

    role_prompt = profile.get("role_prompt") or (
        f"你是 K-STOCK 模拟仓投资部经理 {profile.get('name', manager_id)}。")
    messages = [
        {"role": "system",
         "content": role_prompt + "\n\n【审查指令】\n" + _REVIEW_INSTRUCTIONS},
        {"role": "user", "content": context},
    ]
    try:
        out = gaorta_client.chat_for_hire(manager_id, source, messages)
    except (RuntimeError, ValueError, OSError) as e:
        # 智谱兜底与 meeting 同款（复用 meeting._try_zhipu_fallback，不复制逻辑）
        body = meeting._try_zhipu_fallback(messages, active[0].get("model"), e)
        if body is None:
            raise PlanReviewError(
                f"plan_review {pf_id}: LLM 审查失败（不落半截审批单）→ {e}") from e
        provider = "zhipu-fallback"
    else:
        body = (out or {}).get("content") or ""
        provider = (out or {}).get("model") or active[0].get("model") or "unknown"
    if not body.strip():
        raise PlanReviewError(f"plan_review {pf_id}: LLM 返回空审查（不落半截审批单）")

    # 宽松解析；失败降级整文留痕（不静默丢）
    parsed = _parse_review_json(body)
    if parsed is not None:
        approved = [{"stock": str(x.get("stock", "")),
                     "reason": str(x.get("reason", "")),
                     "weight": str(x.get("weight", ""))}
                    for x in parsed.get("approved", []) if isinstance(x, dict)]
        rejected = [{"stock": str(x.get("stock", "")),
                     "reason_code": str(x.get("reason_code", ""))}
                    for x in parsed.get("rejected", []) if isinstance(x, dict)]
        summary = str(parsed.get("summary", ""))
        parsed_ok = True
    else:
        approved, rejected, summary, parsed_ok = [], [], "", False

    now = datetime.now()
    artifact = {
        "schema": SCHEMA,
        "pf_id": pf_id,
        "manager_id": manager_id,
        "source": source,
        "date": _today_tag(now),
        "generated_at": now.isoformat(timespec="seconds"),
        "provider": provider,
        "parsed": parsed_ok,
        "approved": approved,
        "rejected": rejected,
        "summary": summary,
        "body": body,   # LLM 原文全程留痕（含降级场景）
    }
    _atomic_write_json(OUT_DIR / f"{artifact['date']}_{pf_id}.json", artifact)
    return artifact


def load_review(pf_id: str, date: str | None = None) -> dict | None:
    """读审批单产物；缺省当日，无则 None（portal 投影同口径）。"""
    tag = date or _today_tag()
    fp = OUT_DIR / f"{tag}_{pf_id}.json"
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ── CLI ──────────────────────────────────────────────────────────
def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="plan_review",
                                 description="交易计划审查线（经理盘前审批单）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("review", help="审查当日买入意图并落审批单")
    p.add_argument("pf_id")
    p.add_argument("manager_id")
    p.add_argument("source", nargs="?", default=None,
                   help="雇佣 source（缺省=pf_id，组合雇佣口径）")
    p = sub.add_parser("show", help="查看审批单")
    p.add_argument("pf_id")
    p.add_argument("date", nargs="?", default=None, help="YYYYMMDD（缺省当日）")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "review":
            r = review_plan(args.pf_id, args.manager_id, args.source)
            n_ap, n_rj = len(r["approved"]), len(r["rejected"])
            print(f"OK 审批单已落盘：批准 {n_ap} 只 / 驳回 {n_rj} 只"
                  f" / parsed={r['parsed']} / provider={r['provider']}")
        else:
            r = load_review(args.pf_id, args.date)
            if r is None:
                print(f"无审批单（{args.pf_id} 当日未生成）")
                return 1
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0
    except PlanReviewError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
