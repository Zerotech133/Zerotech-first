# -*- coding: utf-8 -*-
"""IFS v1 F7 隔离全链端到端验收（WO-IFS-E2E-20261104）。

链路（全程 tmp 隔离，不写生产、不启服务、不实盘）::

    live /api/acad1/pool?view=full（只读取 1 只 code）
      → d1_decision（decision_id）
      → d1_handoff/v2（handoff_id + watch_plans[]）
      → watch_plan_consumer（watch_plan_id，armed）
      → watch_trigger_adapter / watch_plan_consumer.trigger_plan（confirmation_id）
      → trading_bot_flow.decide（paper receipt + trade_id）
      → training.outcome_chain（outcome_id，六 ID）
      → training.outcome_chain（training_episode_id，ids/metrics/errors）

行情口径（明示，不伪造真实行情）：
- **固定行情 fixture**：``QUOTE_FIXTURE``（E2E 验收专用固定值，非实时行情）；
  成交价/止损/目标价/次日收盘价全部来自该 fixture，报告 ``quote_fixture`` 字段披露；
- 生产 T+1 行情由 F5 生产接线注入，本脚本不访问行情源、不猜数。

只读/隔离硬约束：
- 池：只读 ``load_pool_snapshot``（PG ``set_session(readonly=True)`` / 只读 SQL），不写 PG；
- 所有写操作显式落到 ``--tmp-root``（默认系统 temp 下 ``ifs_e2e_*``），不落 ``agent_platform/data``；
- 运行前后对生产 artifact 做 stat/hash 快照，必须零变化（``production_untouched``）；
- 不调用任何 HTTP 写接口、不启停服务、不自动训练。

CLI::

    python -X utf8 tools/ifs_e2e_acceptance.py                      # 人类可读摘要
    python -X utf8 tools/ifs_e2e_acceptance.py --json               # JSON 报告到 stdout
    python -X utf8 tools/ifs_e2e_acceptance.py --json --out C:\\Temp\\ifs_e2e_report.json
    python -X utf8 tools/ifs_e2e_acceptance.py --pool fixture --json   # 离线 fixture 池（测试/无 PG）

退出码：0 = PASS（全链 ID 无断点且生产零变化）；1 = FAIL（报告含 failure/checks）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
PORTAL = REPO / "portal"
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))

REPORT_SCHEMA = "ifs_e2e_report/v1"
FIXTURE_SCHEMA = "ifs_e2e_quote_fixture/v1"

#: 固定行情 fixture（E2E 验收专用；非实时/非生产行情，报告显式披露）。
QUOTE_FIXTURE = {
    "schema": FIXTURE_SCHEMA,
    "disclosed": True,
    "note": "E2E 固定行情 fixture：非实时行情、非生产数据；仅用于覆盖 receipt→outcome 数值路径",
    "as_of": "2026-11-03",
    "next_trade_date": "2026-11-04",
    "entry_price": 100.0,
    "next_close": 102.5,
    "stop": 95.0,
    "target": 110.0,
    "expect_next_day_return": 0.025,
    "expect_hit": True,
}

FIXTURE_QUOTE_LOOKUP_RETURN = {
    "next_trade_date": QUOTE_FIXTURE["next_trade_date"],
    "next_close": QUOTE_FIXTURE["next_close"],
    "close": QUOTE_FIXTURE["next_close"],
}

#: 离线 fixture 池（仅 --pool fixture / 测试用；官方口径见 live full 视图）。
FIXTURE_POOL_SNAPSHOT = {
    "ok": True,
    "schema": "ifs_e2e_fixture_pool/v1",
    "mode": "fixture_offline",
    "view": "fixture",
    "source_mode": "fixture_offline",
    "official": False,
    "pool_id": "ACAD1-POOL-FIXTURE-E2E",
    "pool_date": QUOTE_FIXTURE["as_of"],
    "data_date": QUOTE_FIXTURE["as_of"],
    "published_at": None,
    "review_status": "fixture",
    "degraded_reason": "fixture_offline（非官方池；live 模式请去掉 --pool fixture）",
    "count": 1,
    "items": [{
        "code": "600519.SH",
        "name": "贵州茅台",
        "market": "CN",
        "pool_date": QUOTE_FIXTURE["as_of"],
        "data_date": QUOTE_FIXTURE["as_of"],
        "tier": "T1",
        "sources": ["fixture"],
    }],
}

#: 生产 artifact（运行前后必须零变化；worktree 中缺失记为 absent）。
PROD_ARTIFACTS = (
    "agent_platform/data/d1_decisions.jsonl",
    "agent_platform/data/d1_handoffs.jsonl",
    "agent_platform/data/watch_plans.jsonl",
    "agent_platform/data/paper/paper_ledger.jsonl",
    "agent_platform/data/training/episodes.jsonl",
    "agent_platform/data/trading_bot/receipts.jsonl",
    "agent_platform/data/shadow/decision_outcomes.jsonl",
)
PROD_DIRS = ("agent_platform/data/confirmations",)

#: IFS-05 禁止执行字段（递归扫描证据文件）。
FORBIDDEN_EXEC_KEYS = ("order", "order_id", "qty", "quantity", "broker",
                       "execute", "execution", "fill", "fills", "account",
                       "position_size")


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _sha256(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _snapshot_production():
    """生产 artifact 只读快照（stat/sha；缺文件显式 absent，不猜）。"""
    snap = {"files": {}, "dirs": {}}
    for rel in PROD_ARTIFACTS:
        p = REPO / rel
        if p.is_file():
            st = p.stat()
            snap["files"][rel] = {"exists": True, "bytes": st.st_size,
                                  "mtime_ns": st.st_mtime_ns,
                                  "sha256": _sha256(p) if st.st_size <= 8 << 20 else None}
        else:
            snap["files"][rel] = {"exists": False}
    for rel in PROD_DIRS:
        d = REPO / rel
        if d.is_dir():
            files = sorted(x.name for x in d.iterdir() if x.is_file())
            snap["dirs"][rel] = {"exists": True, "count": len(files),
                                 "names": files[-50:]}
        else:
            snap["dirs"][rel] = {"exists": False}
    return snap


def _find_forbidden(payload, path="$"):
    hits = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in FORBIDDEN_EXEC_KEYS and value not in (None, "", [], {}):
                hits.append("%s.%s" % (path, key))
            else:
                hits.extend(_find_forbidden(value, "%s.%s" % (path, key)))
    elif isinstance(payload, list):
        for i, item in enumerate(payload):
            hits.extend(_find_forbidden(item, "%s[%d]" % (path, i)))
    return hits


def _assert_under(path, root):
    p = Path(path).resolve()
    r = Path(root).resolve()
    if r != p and r not in p.parents:
        raise RuntimeError("路径越界（非 tmp 隔离根）: %s 不在 %s 下" % (p, r))


def _read_jsonl(path):
    rows = []
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return rows
    return rows


def _load_live_pool(view="full"):
    """只读获取官方池快照（默认 view=full 聚合 30；单日视图另计 12/30 现状）。"""
    import api_acad1_pool  # portal/ 模块（只读 PG；不写）

    snap = api_acad1_pool.load_pool_snapshot(view=view)
    if not isinstance(snap, dict) or not snap.get("ok"):
        raise RuntimeError("live pool 不可用/非官方: %r" % (
            (snap or {}).get("degraded_reason"),))
    return snap


def _pool_item(snap, code=None):
    items = [x for x in (snap.get("items") or [])
             if isinstance(x, dict) and str(x.get("code") or "").strip()]
    if code:
        for item in items:
            if str(item.get("code")).split(".")[0] == str(code).split(".")[0]:
                return item
        raise RuntimeError("指定 code 不在池内: %s" % code)
    if not items:
        raise RuntimeError("池快照无有效 code（reviewed/pending 均为空）")
    return items[0]


def _guard(step, fn, steps):
    """执行一步并记录（失败即抛，主流程捕获）。"""
    t0 = time.time()
    result = fn()
    steps.append({"step": step, "ok": True,
                  "elapsed_ms": round((time.time() - t0) * 1000, 1),
                  "evidence": result.get("evidence") if isinstance(result, dict) else None})
    return result


def run_acceptance(pool_mode="live", pool_snapshot=None, code=None, tmp_root=None):
    """跑隔离全链验收（纯函数式入口，供 CLI/测试复用）。

    :param pool_mode: "live"（默认，只读 PG）| "fixture"（离线 fixture 池）
    :param pool_snapshot: 直接注入池快照（测试用，优先于 pool_mode）
    :param code: 指定池内 code（可选；默认取第一只）
    :param tmp_root: 隔离根（缺省系统 temp/ifs_e2e_*；测试传 tmp_path）
    :return: report dict（``ok`` 为 True 即 PASS）
    """
    from agent_platform import d1_decision, d1_handoff, trading_bot_flow
    from agent_platform import watch_plan_consumer, watch_trigger_adapter
    from agent_platform.paper import trade_receipt
    from agent_platform.training import outcome_chain

    fixture_quote = dict(QUOTE_FIXTURE)
    started = time.time()
    if tmp_root:
        root = Path(tmp_root)
    else:
        root = Path(tempfile.mkdtemp(prefix="ifs_e2e_",
                                     dir=os.environ.get("TEMP") or tempfile.gettempdir()))
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "decisions": root / "d1_decisions.jsonl",
        "handoffs": root / "d1_handoffs.jsonl",
        "watch_plans": root / "watch_plans.jsonl",
        "bot": root / "bot",
        "paper_state": root / "paper_state.json",
        "paper_ledger": root / "paper_ledger.jsonl",
        "receipts": root / "receipts.jsonl",
        "outcomes": root / "outcomes.jsonl",
        "episode_json": root / "training_episode.json",
        "episodes": root / "episodes.jsonl",
    }
    paths["bot"].mkdir(parents=True, exist_ok=True)

    report = {
        "schema": REPORT_SCHEMA,
        "ok": False,
        "generated_at": _now(),
        "repo": str(REPO),
        "workdir": str(root),
        "pool_mode": "fixture" if pool_snapshot else pool_mode,
        "quote_fixture": fixture_quote,
        "steps": [],
        "id_chain": {},
        "checks": [],
        "residuals": [],
        "artifacts": [],
        "production_untouched": {},
        "failure": None,
        "repro": {
            "command": ("python -X utf8 tools/ifs_e2e_acceptance.py --json"
                        " --pool %s%s --tmp-root %s"
                        % (report_pool_mode(pool_snapshot, pool_mode),
                           (" --code %s" % code) if code else "", root)),
        },
    }
    prod_before = _snapshot_production()

    def check(name, ok, detail=None):
        report["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    ctx = {}

    def step_pool():
        snap = pool_snapshot if pool_snapshot else (
            _load_live_pool("full") if pool_mode == "live" else FIXTURE_POOL_SNAPSHOT)
        single_count = None
        single_error = None
        if not pool_snapshot and pool_mode == "live":
            try:
                single = _load_live_pool("single")
                single_count = single.get("count")
            except Exception as exc:  # noqa: BLE001 — 现状统计失败不阻断主链
                single_error = str(exc)[:200]
        item = _pool_item(snap, code)
        ctx["pool"] = {
            "view": snap.get("view"),
            "source_mode": snap.get("source_mode"),
            "official": snap.get("official"),
            "pool_id": snap.get("pool_id"),
            "pool_date": snap.get("pool_date"),
            "data_date": snap.get("data_date"),
            "published_at": snap.get("published_at"),
            "review_status": snap.get("review_status"),
            "count": snap.get("count"),
            "single_view_count": single_count,
            "single_view_error": single_error,
            "official_modes": ["pg_official", "pg_official_aggregated", "projection"],
            "chosen": {k: item.get(k) for k in
                       ("code", "name", "market", "pool_date", "data_date", "tier")},
        }
        report["pool"] = ctx["pool"]
        if pool_mode == "live" and not pool_snapshot:
            check("pool_official_readonly",
                  snap.get("official") is True
                  and snap.get("source_mode") in ("pg_official", "pg_official_aggregated"),
                  {"source_mode": snap.get("source_mode"),
                   "view": snap.get("view"), "count": snap.get("count")})
        return {"evidence": {"pool_id": snap.get("pool_id"),
                             "count": snap.get("count"),
                             "chosen_code": item.get("code")}}

    def step_decision():
        item = ctx["pool"]["chosen"]
        payload = {
            "code": item["code"],
            "name": item.get("name") or "",
            "market": item.get("market") or "CN",
            "tier": "BUY",
            "confidence": 0.70,
            "rationale": "IFS F7 E2E 隔离全链验收：池内研究结论（固定 fixture），paper-only",
            "evidence": ["pool_id=%s" % ctx["pool"]["pool_id"],
                         "pool_date=%s" % ctx["pool"]["pool_date"],
                         "quote_fixture=E2E_FIXED_FIXTURE"],
            "source": "d1_research",
            "paper_only": True,
        }
        rec = d1_decision.append_decision(payload, ledger=paths["decisions"])
        _assert_under(paths["decisions"], root)
        ctx["decision"] = rec
        return {"evidence": {"decision_id": rec["decision_id"],
                             "ledger": str(paths["decisions"])}}

    def step_handoff():
        item = ctx["pool"]["chosen"]
        decision = ctx["decision"]
        plan_id = "wp-%s-%s" % (
            fixture_quote["as_of"].replace("-", ""),
            hashlib.sha1(("%s|%s" % (decision["decision_id"], item["code"])).encode()).hexdigest()[:6])
        payload = {
            "schema": "d1_handoff/v2",
            "code": item["code"],
            "name": item.get("name") or "",
            "target": "WA1",
            "action": "watch",
            "rationale": "E2E：池内研究后交盯盘观察（固定 fixture，不实盘）",
            "decision_id": decision["decision_id"],
            "buy_zone": [fixture_quote["entry_price"] - 1.0,
                         fixture_quote["entry_price"] + 1.0],
            "stop_loss": fixture_quote["stop"],
            "paper_only": True,
            "watch_plans": [{
                "plan_id": plan_id,
                "seat": "WA1",
                "method": "E2E_FIXED_FIXTURE",
                "conditions": {
                    "symbol": item["code"],
                    "name": item.get("name") or "",
                    "action": "BUY",
                    "price": fixture_quote["entry_price"],
                    "stop": fixture_quote["stop"],
                    "target": fixture_quote["target"],
                    "reason": "E2E fixed fixture 触价（非实时行情）",
                },
                "expires_at": "2099-01-01T00:00:00+08:00",
            }],
        }
        row = d1_handoff.append_handoff(payload, ledger=paths["handoffs"])
        _assert_under(paths["handoffs"], root)
        ctx["handoff"] = {"row": row,
                          "handoff_id": row.get("handoff_id") or row.get("receipt_id"),
                          "receipt_id": row.get("receipt_id"),
                          "schema": d1_handoff.schema_version(row),
                          "plan_id": plan_id}
        return {"evidence": {"handoff_id": ctx["handoff"]["handoff_id"],
                             "schema": ctx["handoff"]["schema"],
                             "watch_plan_id": plan_id}}

    def step_watch_plan():
        consumed = watch_plan_consumer.consume_handoff(ctx["handoff"]["row"],
                                                       path=paths["watch_plans"])
        _assert_under(paths["watch_plans"], root)
        plans = consumed.get("plans") or []
        if len(plans) != 1 or plans[0].get("status") != "armed":
            raise RuntimeError("watch_plan 消费异常: %r" % (consumed,))
        plan = plans[0]
        ctx["plan"] = plan
        return {"evidence": {"watch_plan_id": plan["plan_id"],
                             "status": plan["status"],
                             "armed": consumed.get("armed")}}

    def step_trigger():
        triggered = watch_plan_consumer.trigger_plan(
            ctx["plan"]["plan_id"], path=paths["watch_plans"], data_dir=str(paths["bot"]))
        confirmation = triggered.get("confirmation") or {}
        if confirmation.get("status") != "pending":
            raise RuntimeError("trigger 未生成 pending confirmation: %r" % (confirmation,))
        ctx["confirmation"] = confirmation
        # 四 ID 传递（decision/handoff/watch_plan → confirmation）
        got = {k: confirmation.get(k) for k in
               ("source_decision_id", "source_handoff_id", "source_watch_plan_id")}
        want = {"source_decision_id": ctx["decision"]["decision_id"],
                "source_handoff_id": ctx["handoff"]["handoff_id"],
                "source_watch_plan_id": ctx["plan"]["plan_id"]}
        if got != want:
            raise RuntimeError("confirmation 四 ID 未贯通: got=%r want=%r" % (got, want))
        return {"evidence": {"confirmation_id": confirmation["confirmation_id"],
                             "ids": got}}

    def step_paper_receipt():
        executed = trading_bot_flow.decide(
            ctx["confirmation"]["confirmation_id"], "approve", by="E2E",
            note="IFS F7 E2E fixed fixture", mode="manual",
            data_dir=str(paths["bot"]),
            paper_state_path=str(paths["paper_state"]),
            paper_ledger_path=str(paths["paper_ledger"]),
            receipts_path=str(paths["receipts"]),
        )
        for p in (paths["paper_state"], paths["paper_ledger"], paths["receipts"]):
            _assert_under(p, root)
        if executed.get("status") != "executed" or not executed.get("trade_id"):
            raise RuntimeError("paper 执行失败: %r" % (executed,))
        receipts = trade_receipt.read_receipts(str(paths["receipts"]))
        if len(receipts) != 1:
            raise RuntimeError("receipt 数量异常: %d" % len(receipts))
        receipt = receipts[0]
        # 幂等：重复 approve 不重复执行/不换 trade_id
        repeat = trading_bot_flow.decide(
            ctx["confirmation"]["confirmation_id"], "approve", by="E2E",
            data_dir=str(paths["bot"]),
            paper_state_path=str(paths["paper_state"]),
            paper_ledger_path=str(paths["paper_ledger"]),
            receipts_path=str(paths["receipts"]),
        )
        receipts_after = trade_receipt.read_receipts(str(paths["receipts"]))
        ctx["receipt"] = receipt
        ctx["trade"] = executed
        if repeat.get("trade_id") != executed["trade_id"] or len(receipts_after) != 1:
            raise RuntimeError("paper 幂等失败: repeat=%r receipts=%d"
                               % (repeat.get("trade_id"), len(receipts_after)))
        ids = executed.get("ids") or {}
        want = {
            "decision_id": ctx["decision"]["decision_id"],
            "handoff_id": ctx["handoff"]["handoff_id"],
            "watch_plan_id": ctx["plan"]["plan_id"],
            "confirmation_id": ctx["confirmation"]["confirmation_id"],
            "trade_id": executed["trade_id"],
        }
        if {k: ids.get(k) for k in want} != want:
            raise RuntimeError("receipt 五 ID 未贯通: got=%r want=%r" % (ids, want))
        return {"evidence": {"trade_id": executed["trade_id"],
                             "confirmation_id": want["confirmation_id"],
                             "idempotent_repeat": True}}

    def step_outcome():
        result = outcome_chain.generate_outcomes(
            [ctx["receipt"]],
            quotes=lambda symbol, date: dict(FIXTURE_QUOTE_LOOKUP_RETURN),
        )
        if len(result.get("complete") or []) != 1:
            raise RuntimeError("outcome 未生成 complete: %r" % (result,))
        outcome = result["complete"][0]
        written = outcome_chain.append_outcomes(paths["outcomes"], [outcome])
        _assert_under(paths["outcomes"], root)
        if written != 1:
            raise RuntimeError("outcome 落盘异常: written=%r" % (written,))
        ctx["outcome"] = outcome
        if outcome.get("next_day_return") != round(
                fixture_quote["expect_next_day_return"], 6):
            raise RuntimeError("outcome 收益与 fixture 不一致: %r"
                               % (outcome.get("next_day_return"),))
        return {"evidence": {"outcome_id": outcome["outcome_id"],
                             "status": outcome.get("status"),
                             "next_day_return": outcome.get("next_day_return")}}

    def step_episode():
        episode = outcome_chain.build_training_episode(
            receipts=[ctx["receipt"]],
            outcomes=[ctx["outcome"]],
            combo_id="D1-E2E-F7-COMBO",
            data_snapshot={
                "as_of": fixture_quote["as_of"],
                "source": "E2E_FIXED_FIXTURE（非实时行情）",
                "note": "IFS F7 隔离全链验收；行情为固定 fixture，生产 T+1 由 F5 注入",
            },
        )
        violations = outcome_chain.validate_episode(episode)
        if violations:
            raise RuntimeError("episode 契约违规: %r" % (violations,))
        outcome_chain.write_episode_json(paths["episode_json"], episode)
        added = outcome_chain.append_episode(paths["episodes"], episode)
        for p in (paths["episode_json"], paths["episodes"]):
            _assert_under(p, root)
        if added is not True:
            raise RuntimeError("episode append 异常: %r" % (added,))
        ctx["episode"] = episode
        return {"evidence": {"training_episode_id": episode["training_episode_id"],
                             "metrics": episode.get("metrics"),
                             "contract_violations": violations}}

    steps_def = [
        ("pool_readonly_live", step_pool),
        ("decision", step_decision),
        ("handoff_v2", step_handoff),
        ("watch_plan", step_watch_plan),
        ("trigger_confirmation", step_trigger),
        ("paper_receipt", step_paper_receipt),
        ("outcome", step_outcome),
        ("training_episode", step_episode),
    ]
    for name, fn in steps_def:
        try:
            _guard(name, fn, report["steps"])
        except Exception as exc:  # noqa: BLE001 — fail-visible，不吞
            report["steps"].append({"step": name, "ok": False,
                                    "error": "%s: %s" % (type(exc).__name__, str(exc)[:500])})
            report["failure"] = {"step": name,
                                 "error": "%s: %s" % (type(exc).__name__, exc)}
            break

    # ── ID 链 + 残余 ────────────────────────────────────────────────
    def _id(obj, *keys):
        for k in keys:
            v = (obj or {}).get(k)
            if v:
                return str(v)
        return None

    pool = ctx.get("pool") or {}
    chain = {
        "pool_id": pool.get("pool_id"),
        "card_id": None,
        "decision_id": _id(ctx.get("decision"), "decision_id"),
        "handoff_id": ctx.get("handoff", {}).get("handoff_id"),
        "watch_plan_id": _id(ctx.get("plan"), "plan_id"),
        "trigger_id": None,
        "confirmation_id": _id(ctx.get("confirmation"), "confirmation_id"),
        "trade_id": _id(ctx.get("trade"), "trade_id"),
        "outcome_id": _id(ctx.get("outcome"), "outcome_id"),
        "training_episode_id": _id(ctx.get("episode"), "training_episode_id"),
    }
    report["id_chain"] = chain
    if ctx.get("handoff", {}).get("receipt_id") and \
            ctx["handoff"]["receipt_id"] != ctx["handoff"].get("handoff_id"):
        chain["handoff_id"] = ctx["handoff"]["receipt_id"]

    if not report["failure"]:
        check("id_chain_no_break",
              all(chain[k] for k in ("pool_id", "decision_id", "handoff_id",
                                     "watch_plan_id", "confirmation_id",
                                     "trade_id", "outcome_id",
                                     "training_episode_id")),
              {k: bool(chain[k]) for k in chain})
        decision = ctx["decision"]
        outcome = ctx["outcome"]
        episode = ctx["episode"]
        receipt = ctx.get("receipt") or {}
        confirmation = ctx["confirmation"]
        check("decision_paper_only", decision.get("paper_only") is True
              and decision.get("decided_by") == "D1")
        check("handoff_v2_schema", ctx["handoff"]["schema"] == "d1_handoff/v2"
              and bool(ctx["handoff"]["plan_id"]))
        check("watch_plan_armed", ctx["plan"].get("status") == "armed")
        check("confirmation_four_ids",
              confirmation.get("source_decision_id") == decision["decision_id"]
              and confirmation.get("source_handoff_id") == ctx["handoff"]["handoff_id"]
              and confirmation.get("source_watch_plan_id") == ctx["plan"]["plan_id"])
        check("receipt_five_ids",
              receipt.get("trade_id") == ctx["trade"]["trade_id"]
              and receipt.get("confirmation_id") == confirmation["confirmation_id"]
              and receipt.get("decision_id") == decision["decision_id"]
              and receipt.get("handoff_id") == ctx["handoff"]["handoff_id"]
              and receipt.get("watch_plan_id") == ctx["plan"]["plan_id"])
        check("receipt_paper_only",
              receipt.get("paper_only") is True
              and receipt.get("not_a_trade") is True
              and receipt.get("trading_permitted") is False)
        check("receipt_idempotent", True,
              {"repeat_trade_id_same": True})
        check("outcome_complete_six_ids",
              outcome.get("status") == "complete"
              and all(outcome.get(k) for k in ("outcome_id", "trade_id",
                                               "confirmation_id", "decision_id",
                                               "handoff_id", "watch_plan_id"))
              and outcome.get("quote_status") == "complete",
              {"next_day_return": outcome.get("next_day_return"),
               "hit": outcome.get("hit")})
        check("outcome_matches_fixture",
              outcome.get("next_day_return") == round(
                  fixture_quote["expect_next_day_return"], 6)
              and bool(outcome.get("hit")) == bool(fixture_quote["expect_hit"]),
              fixture_quote)
        check("episode_contract_valid",
              outcome_chain.validate_episode(episode) == []
              and episode.get("training_episode_id") == chain["training_episode_id"]
              and bool((episode.get("ids") or {}).get("outcome_ids")),
              {"ids": episode.get("ids"), "metrics": episode.get("metrics")})
        check("episode_readonly_flags",
              episode.get("paper_only") is True
              and episode.get("trading_permitted") is False
              and episode.get("read_only") is True
              and episode.get("auto_training") is False)

        # 证据文件 + 禁止执行字段 + 隔离（confirmation 的 execution 为 paper 执行记录，非真实订单，单独披露）
        files = [paths[k] for k in ("decisions", "handoffs", "watch_plans",
                                    "paper_ledger", "receipts", "outcomes",
                                    "episode_json", "episodes")]
        blob = []
        for p in files:
            if Path(p).is_file():
                _assert_under(p, root)
                blob.append(json.loads(Path(p).read_text(encoding="utf-8"))
                            if p.suffix == ".json"
                            else _read_jsonl(p))
        forbidden = _find_forbidden(blob)
        check("no_execution_fields", not forbidden, forbidden[:10])
        check("confirmation_is_paper_record",
              confirmation.get("paper_only") is True
              and confirmation.get("trading_permitted") is False,
              {"execution_keys": sorted((confirmation.get("execution") or {}).keys())})
        check("isolation_tmp_only", True, {"workdir": str(root)})

    # 证据清单（sha256/bytes）
    for p in sorted(root.rglob("*")):
        if p.is_file():
            report["artifacts"].append({
                "path": str(p), "bytes": p.stat().st_size,
                "sha256": _sha256(p),
            })
    report["production_untouched"] = {
        "before": prod_before,
        "after": _snapshot_production(),
    }
    report["production_untouched"]["unchanged"] = (
        report["production_untouched"]["before"] == report["production_untouched"]["after"])
    if not report["failure"]:
        check("production_untouched",
              report["production_untouched"]["unchanged"] is True)

    # 残余/已知偏差（诚实披露，不当作已闭环）
    report["residuals"] = [
        {"id": "trigger_id", "status": "absent",
         "reason": "watch_trigger_adapter 直建 confirmation、跳过独立 trigger 账本（ID_CHAIN gaps 已列）"},
        {"id": "card_id", "status": "absent",
         "reason": "池 items 尚未发布正式 ACAD1-CARD-*（当前 item_id 代理也是规划）；registry card-id-proxy 偏差"},
        {"id": "decision_id_format", "status": "deviation",
         "reason": "本链经 agent_platform/d1_decision.py 生成 d1-<14digits>-<hex6>；ID_CHAIN 权威引擎格式 D1-<date>-<sha10> 待 F3/F5 收敛（registry decision-id-dual-format）"},
        {"id": "handoff_id_alias", "status": "deviation",
         "reason": "d1_handoff 内部字段 receipt_id；对外别名 handoff_id（registry receipt-id-alias）"},
        {"id": "pool_view", "status": "info",
         "reason": ("live 池单日视图=12、full 聚合视图=30（每市场最新 pool_date）；本验收用 view=full 取 30 中 1 只"
                    if report.get("pool_mode") != "fixture"
                    else "fixture 离线池（非官方；仅测试/无 PG 场景）")},
        {"id": "outcome_episode_production_wiring", "status": "pending",
         "reason": "生产 episodes.jsonl/outcomes.jsonl 未生成；本脚本按 F5 契约在 tmp 生成（生产接线由 Lead 决定）"},
    ]
    report["ok"] = bool(report["failure"] is None
                        and report["checks"]
                        and all(item["ok"] for item in report["checks"]))
    report["elapsed_s"] = round(time.time() - started, 2)
    return report


def report_pool_mode(pool_snapshot, pool_mode):
    return "fixture(injected)" if pool_snapshot else pool_mode


def _human_summary(report):
    lines = ["IFS F7 E2E 隔离全链验收：%s" % ("PASS" if report["ok"] else "FAIL"),
             "workdir: %s" % report.get("workdir"),
             "pool: %s" % json.dumps(report.get("pool") or {}, ensure_ascii=False)]
    for step in report.get("steps") or []:
        lines.append("  [%s] %s" % ("OK" if step.get("ok") else "FAIL", step.get("step"))
                     + ("" if step.get("ok") else ": " + str(step.get("error"))[:200]))
    chain = report.get("id_chain") or {}
    lines.append("ID chain: " + " → ".join(
        "%s=%s" % (k, chain.get(k) or "∅") for k in
        ("pool_id", "decision_id", "handoff_id", "watch_plan_id",
         "trigger_id", "confirmation_id", "trade_id", "outcome_id",
         "training_episode_id")))
    for item in report.get("checks") or []:
        lines.append("  [%s] %s" % ("PASS" if item["ok"] else "FAIL", item["check"]))
    if report.get("failure"):
        lines.append("failure: %s" % json.dumps(report["failure"], ensure_ascii=False))
    lines.append("residuals: %d（明细见 JSON 报告）" % len(report.get("residuals") or []))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="IFS F7 隔离全链端到端验收（只读池 + tmp 写）")
    ap.add_argument("--json", action="store_true", help="stdout 输出 JSON 报告")
    ap.add_argument("--out", default="", help="报告落盘路径（显式；建议 C:\\Temp\\...）")
    ap.add_argument("--pool", choices=("live", "fixture"), default="live",
                    help="池来源：live=只读 PG；fixture=离线 fixture（测试/无 PG）")
    ap.add_argument("--code", default="", help="指定池内 code（缺省取第一只）")
    ap.add_argument("--tmp-root", default="", help="隔离根目录（默认系统 temp/ifs_e2e_*）")
    args = ap.parse_args(argv)
    report = run_acceptance(pool_mode=args.pool, code=args.code or None,
                            tmp_root=args.tmp_root or None)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        report["out"] = str(out)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_human_summary(report))
        if args.out:
            print("report: %s" % args.out)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
