# -*- coding: utf-8 -*-
"""K-STOCK-163 · 交易计划审查层回归（被测：k_stock/paper/plan_review.py）

覆盖：collect_context 摘要（产物+tier+Top）/ 空清单空串 / review_plan 正常 JSON
解析 / 解析失败降级（整文存 body、approved 空表、parsed=false）/ LLM 失败
fail-closed 不落半截 / 智谱兜底复用 meeting 链（provider=zhipu-fallback）/
原子写（无 tmp 残留）/ CLI show。
chat_for_hire 与 LLMClient 全部 monkeypatch，绝不真触网；
manager 账本注入同 test_meeting.py（临时目录，真账本零触碰）。
"""

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PAPER = HERE / ".." / "paper"
sys.path.insert(0, str(PAPER))

import gaorta_client  # noqa: E402
import manager  # noqa: E402
import meeting  # noqa: E402
import plan_review  # noqa: E402
from plan_review import PlanReviewError, collect_context, review_plan  # noqa: E402

MID, PF = "bull_desk", "pf_001"
TODAY = datetime.now().strftime("%Y%m%d")  # 产物/审查单日期随真实时钟
STALE = "20000101"  # 永远过期的一天

_GOOD_JSON = json.dumps(
    {
        "approved": [
            {"stock": "000820.SZ", "reason": "chanlun 3买 T1 高信心", "weight": "8%"},
            {"stock": "601127.SH", "reason": "趋势延续", "weight": "6%"},
        ],
        "rejected": [
            {"stock": "600593.SH", "reason_code": "信心不足"},
            {"stock": "300750.SZ", "reason_code": "行业集中"},
        ],
        "summary": "今日批准 2 只买入，其余驳回",
    },
    ensure_ascii=False,
)


def _write_run(
    data_dir: Path, items: list[dict], pf: str = PF, tag: str = TODAY
) -> Path:
    fp = data_dir / f"poc_{pf}_{tag}_0900.json"
    fp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return fp


def _run_items() -> list[dict]:
    def it(stock, tier, conf, strategy="chanlun", action="BUY"):
        return {
            "stock": stock,
            "_tier": tier,
            "_src_strategy": strategy,
            "asof": "2026-10-13 15:10:07",
            "outputs": {
                "D1": {
                    "action": action,
                    "confidence": conf,
                    "reason": f"{strategy} 信号",
                }
            },
        }

    return [
        it("000820.SZ", "T1", 0.9),
        it("601127.SH", "T1", 0.85),
        it("600593.SH", "T2", 0.6),
        it("300750.SZ", "T2", 0.55),
        it("688981.SH", "T3", 0.5, action="SELL"),
    ]  # SELL 不算买入意图


def _mk_pending_db(db_fp: Path) -> None:
    con = sqlite3.connect(str(db_fp))
    con.executescript(
        "CREATE TABLE orders(id INTEGER PRIMARY KEY, stock TEXT, side TEXT,"
        " qty INTEGER, conf REAL, strategy TEXT, plan_date TEXT,"
        " status TEXT, account_id TEXT);"
    )
    con.executemany(
        "INSERT INTO orders(stock, side, qty, conf, strategy, plan_date,"
        " status, account_id) VALUES (?,?,?,?,?,?,?,?)",
        [
            ("000820.SZ", "BUY", 500, 0.9, "chanlun", "20261014", "pending", "chaser"),
            ("600593.SH", "SELL", 300, 0.7, "ai_team", "20261014", "pending", "chaser"),
            (
                "603210.SH",
                "BUY",
                800,
                0.75,
                "double_knife",
                "20261014",
                "pending",
                "chaser",
            ),
            ("601127.SH", "BUY", 400, 0.85, "chanlun", "20261013", "filled", "chaser"),
        ],
    )
    con.commit()
    con.close()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """manager 账本注入临时区（同 test_meeting.py）+ 数据目录/兜底门控隔离。"""
    mdir = tmp_path / "managers"
    mdir.mkdir()
    monkeypatch.setattr(manager, "HIRES_PATH", tmp_path / "manager_hires.json")
    monkeypatch.setattr(manager, "MANAGERS_DIR", mdir)
    monkeypatch.setattr(manager, "HERE", tmp_path)
    (tmp_path / "models.json").write_text(
        json.dumps({"models": [{"model": "GLM-5.2", "coef_vs_glm52": 1.0}]}),
        encoding="utf-8",
    )
    (mdir / f"{MID}.json").write_text(
        json.dumps(
            {
                "id": MID,
                "name": "牛市台",
                "status": "live",
                "model": "GLM-5.2",
                "role_prompt": "你是测试经理角色提示词RP-MARKER。",
            }
        ),
        encoding="utf-8",
    )
    manager.hire(MID, PF)

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(plan_review, "DATA_DIR", data_dir)
    monkeypatch.setattr(plan_review, "OUT_DIR", data_dir / "plan_review")
    monkeypatch.setattr(
        plan_review, "SUBS_PATH", tmp_path / "strategy_subscriptions.json"
    )
    monkeypatch.setattr(plan_review, "DB_PATH", data_dir / "paper.db")
    (tmp_path / "strategy_subscriptions.json").write_text(
        json.dumps(
            {"portfolios": [{"id": PF, "name": "我的组合", "trader": "chaser"}]}
        ),
        encoding="utf-8",
    )
    # 兜底门控文件隔离到临时区（默认关）
    monkeypatch.setattr(
        meeting, "FALLBACK_CONFIG_FP", tmp_path / "meeting_fallback.json"
    )
    return data_dir


# ── collect_context ──────────────────────────────────────────────


def test_collect_context_full(env):
    _write_run(env, _run_items())
    _mk_pending_db(env / "paper.db")
    ctx = collect_context(PF)
    assert "【今日买入意图清单摘要】" in ctx and PF in ctx
    assert "意图总笔数 4" in ctx  # 5 条产物里 1 条 SELL 被滤掉
    assert "pending 买入 2 笔" in ctx  # 3 条 pending 里 1 条 SELL 被滤掉
    assert "T1=2" in ctx and "T2=2" in ctx
    assert "000820.SZ conf=0.90" in ctx and "chanlun" in ctx
    assert ctx.index("000820.SZ") < ctx.index("600593.SH")  # Top 按信心降序
    assert "688981.SH" not in ctx, "SELL 意图不入买入清单"
    print("PASS test_collect_context_full")


def test_collect_context_empty_when_nothing(env):
    assert collect_context(PF) == ""
    print("PASS test_collect_context_empty_when_nothing")


def test_collect_context_pending_only(env):
    _mk_pending_db(env / "paper.db")
    ctx = collect_context(PF)
    assert "意图总笔数 0" in ctx and "无当日 merged run 产物" in ctx
    assert "603210.SH" in ctx
    print("PASS test_collect_context_pending_only")


def test_collect_context_stale_run_ignored(env):
    _write_run(env, _run_items(), tag=STALE)  # 非当日产物
    assert collect_context(PF) == ""
    print("PASS test_collect_context_stale_run_ignored")


# ── review_plan：解析/降级/fail-closed/兜底 ────────────────────────


def test_review_plan_normal_parse(env, monkeypatch):
    _write_run(env, _run_items())
    calls = []

    def fake_chat(mid, source, messages, timeout=60):
        calls.append({"mid": mid, "source": source, "messages": messages})
        return {
            "content": "前置废话\n```json\n" + _GOOD_JSON + "\n```\n后置废话",
            "model": "GLM-5.2",
        }

    monkeypatch.setattr(gaorta_client, "chat_for_hire", fake_chat)
    r = review_plan(PF, MID)
    assert calls and calls[0]["mid"] == MID and calls[0]["source"] == PF
    sys_msg = calls[0]["messages"][0]["content"]
    assert "RP-MARKER" in sys_msg and "审查指令" in sys_msg
    assert "意图总笔数 4" in calls[0]["messages"][1]["content"]
    assert r["parsed"] is True and r["provider"] == "GLM-5.2"
    assert [a["stock"] for a in r["approved"]] == ["000820.SZ", "601127.SH"]
    assert r["approved"][0]["weight"] == "8%"
    assert [x["reason_code"] for x in r["rejected"]] == ["信心不足", "行业集中"]
    assert "批准 2 只" in r["summary"]
    # 产物落盘（当日文件名口径）+ 原文留痕
    fp = env / "plan_review" / "20261014_pf_001.json"
    if fp.exists():  # 日期随真实时钟；文件名口径以产物内 date 为准
        disk = json.loads(fp.read_text(encoding="utf-8"))
    else:
        disk = json.loads(
            (env / "plan_review" / f"{r['date']}_{PF}.json").read_text(encoding="utf-8")
        )
    assert disk["schema"] == "k-stock-163/plan-review-v0.1"
    assert "前置废话" in disk["body"]  # 整文留痕
    assert not list((env / "plan_review").glob("*.tmp*")), "原子写不留 tmp 残留"
    print("PASS test_review_plan_normal_parse")


def test_review_plan_parse_failure_degrades(env, monkeypatch):
    _write_run(env, _run_items())
    monkeypatch.setattr(
        gaorta_client,
        "chat_for_hire",
        lambda *a, **k: {"content": "这不是JSON，是散文审查意见", "model": "GLM-5.2"},
    )
    r = review_plan(PF, MID)
    assert r["parsed"] is False
    assert r["approved"] == [] and r["rejected"] == []
    assert "散文审查意见" in r["body"], "解析失败整文留痕（不静默丢）"
    disk = json.loads(
        (env / "plan_review" / f"{r['date']}_{PF}.json").read_text(encoding="utf-8")
    )
    assert disk["parsed"] is False and "散文审查意见" in disk["body"]
    print("PASS test_review_plan_parse_failure_degrades")


def test_review_plan_llm_failure_fail_closed(env, monkeypatch):
    _write_run(env, _run_items())
    monkeypatch.setattr(meeting, "_zhipu_fallback_allowed", lambda fp=None: False)

    def boom(*a, **k):
        raise RuntimeError("未配置 GAORTA_API_KEY，无法调用 gaorta 通道")

    monkeypatch.setattr(gaorta_client, "chat_for_hire", boom)
    with pytest.raises(PlanReviewError, match="LLM 审查失败"):
        review_plan(PF, MID)
    assert not (env / "plan_review").exists() or not list(
        (env / "plan_review").glob("*.json")
    ), "不落半截审批单"
    print("PASS test_review_plan_llm_failure_fail_closed")


def test_review_plan_empty_context_rejected(env, monkeypatch):
    monkeypatch.setattr(
        gaorta_client,
        "chat_for_hire",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应触网")),
    )
    with pytest.raises(PlanReviewError, match="无可用买入意图清单"):
        review_plan(PF, MID)
    print("PASS test_review_plan_empty_context_rejected")


def test_review_plan_no_active_hire_rejected(env, monkeypatch):
    _write_run(env, _run_items())
    manager.fire(MID, PF)
    with pytest.raises(PlanReviewError, match="active 雇佣"):
        review_plan(PF, MID)
    print("PASS test_review_plan_no_active_hire_rejected")


def test_review_plan_zhipu_fallback_provider(env, monkeypatch):
    _write_run(env, _run_items())
    monkeypatch.setattr(meeting, "_zhipu_fallback_allowed", lambda fp=None: True)

    class FakeLLM:
        def __init__(self, *a, **k):
            pass

        def chat(self, messages, **k):
            return _GOOD_JSON, "glm"

    monkeypatch.setattr(meeting, "_LLMClient", FakeLLM)

    def boom(*a, **k):
        raise RuntimeError("未配置 GAORTA_API_KEY，无法调用 gaorta 通道")

    monkeypatch.setattr(gaorta_client, "chat_for_hire", boom)
    r = review_plan(PF, MID)
    assert r["provider"] == "zhipu-fallback"
    assert r["parsed"] is True and len(r["approved"]) == 2
    print("PASS test_review_plan_zhipu_fallback_provider")


def test_review_plan_source_defaults_to_pf(env, monkeypatch):
    _write_run(env, _run_items())
    seen = {}

    def fake_chat(mid, source, messages, timeout=60):
        seen["source"] = source
        return {"content": _GOOD_JSON, "model": "GLM-5.2"}

    monkeypatch.setattr(gaorta_client, "chat_for_hire", fake_chat)
    review_plan(PF, MID)  # 不传 source → 缺省=pf_id（组合雇佣口径）
    assert seen["source"] == PF
    print("PASS test_review_plan_source_defaults_to_pf")


# ── CLI ──────────────────────────────────────────────────────────


def test_cli_show(env, monkeypatch, capsys):
    _write_run(env, _run_items())
    monkeypatch.setattr(
        gaorta_client,
        "chat_for_hire",
        lambda *a, **k: {"content": _GOOD_JSON, "model": "GLM-5.2"},
    )
    r = review_plan(PF, MID)
    # show 当日（date 口径随真实时钟，直接传产物 date）
    assert plan_review._main(["show", PF, r["date"]]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out[out.index("{") :])
    assert payload["pf_id"] == PF and payload["parsed"] is True
    # show 无产物日 → exit 1
    assert plan_review._main(["show", PF, "19990101"]) == 1
    print("PASS test_cli_show")


def test_cli_review_failure_exit2(env, monkeypatch, capsys):
    monkeypatch.setattr(
        gaorta_client,
        "chat_for_hire",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应触网")),
    )
    assert plan_review._main(["review", PF, MID]) == 2
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False and "意图" in payload["error"]
    print("PASS test_cli_review_failure_exit2")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
