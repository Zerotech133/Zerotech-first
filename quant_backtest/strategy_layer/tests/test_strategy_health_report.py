# -*- coding: utf-8 -*-
"""strategy_health_report 单测：周度报告 / 上游 Bias Ticket（WO-HEALTH-WEEKLY-20261031）。

全程离线：rows / 策略卡 / registry / 权益曲线 / 成本全部注入（tmp_path 假数据），
禁连真实 PG、禁读真实 data/ 生产档、禁跑生产任务。覆盖：
  · 多策略 / 多日窗口聚合、period 与窗口外行显式计数；
  · regime 分层 + regime/execution 偏差票；
  · 样本不足 / 收益缺失 → 显式 insufficient/null，不造数；
  · 上游 BiasTicket 字段（owner_upstream / evidence_refs / not_auto_apply）；
  · 策略卡自动读取 / 不可用显式降级；
  · rows JSON 文件、CLI JSON/MD、原子落盘、只读（输入文件不被改写）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from strategy_layer import strategy_health_report as rp

_MODULE = os.path.join(
    os.path.dirname(os.path.abspath(rp.__file__)), "strategy_health_report.py"
)
_QB_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(rp.__file__)))

_REGISTRY = {
    "enabled": ["kg_boost", "chan_monitor"],
    "weights": {"kg_boost": 0.5, "chan_monitor": 0.5},
    "top_n": {"kg_boost": 50},
}
_EMPTY_REGISTRY = {"enabled": [], "weights": {}, "top_n": {}}


@pytest.fixture(autouse=True)
def _isolate_default_registry(tmp_path, monkeypatch):
    """默认 registry 路径指到临时目录：测试不读真实 strategy_registry.json。"""
    monkeypatch.setattr(
        rp._health, "REGISTRY_PATH", str(tmp_path / "no_registry.json")
    )


# ── 合成数据工厂 ──────────────────────────────────────────────────────
def _contrib(strategy_id, weight=0.5):
    return {strategy_id: {"score": 50.0, "weight": weight, "action": "buy"}}


def _row(
    symbol,
    trade_date,
    contributions,
    ret_t1=None,
    ret_t5=None,
    divergence=False,
    regime=None,
    turnover=None,
    cost=None,
):
    row = {
        "trade_date": trade_date,
        "symbol": symbol,
        "action": "buy",
        "score": 99.0,
        "confidence": 0.9,
        "contributions": contributions,
        "divergence": divergence,
    }
    if ret_t1 is not None:
        row["ret_t1"] = ret_t1
    if ret_t5 is not None:
        row["ret_t5"] = ret_t5
    if regime is not None:
        row["regime"] = regime
    if turnover is not None:
        row["turnover"] = turnover
    if cost is not None:
        row["cost"] = cost
    return row


def _spread_rows(
    strategy_id,
    first_day,
    day_count,
    per_day,
    ret_t1=None,
    ret_t5=None,
    regime=None,
    symbol_base=600000,
):
    """first_day 起连续 day_count 天、每天 per_day 行（标的分散）。"""
    rows = []
    index = 0
    for offset in range(day_count):
        trade_date = "202601%02d" % (first_day + offset)
        for _ in range(per_day):
            rows.append(
                _row(
                    "%06d.SH" % (symbol_base + index),
                    trade_date,
                    _contrib(strategy_id),
                    ret_t1=ret_t1,
                    ret_t5=ret_t5,
                    regime=regime,
                )
            )
            index += 1
    return rows


def _cards(*specs):
    return [
        {"strategy_id": sid, "owner": owner, "display_name": name}
        for sid, owner, name in specs
    ]


# ── 1. 多策略 / 多日窗口：period、策略明细、等级汇总 ──────────────────
def test_weekly_multi_strategy_multi_day():
    rows = []
    rows += _spread_rows("kg_boost", 20, 5, 4, ret_t1=1.0, ret_t5=2.0)
    rows += _spread_rows("chan_monitor", 20, 5, 4, ret_t1=-2.0, ret_t5=-1.0)
    rows += _spread_rows("trend_follow", 25, 2, 2, ret_t1=1.0, ret_t5=2.0)
    # 窗口外一行（asof 20260126、7 天窗口 = 20260120~20260126）
    rows.append(
        _row("000001.SZ", "20260110", _contrib("kg_boost"), ret_t1=5.0, ret_t5=5.0)
    )
    cards = _cards(
        ("kg_boost", "quant-kg", "KG 助推"),
        ("chan_monitor", "quant-chan", "缠论监控"),
        ("trend_follow", "quant-classic", "趋势跟随"),
    )
    doc = rp.build_weekly_health_report(
        rows=rows, days=7, asof="20260126", cards=cards, registry=_REGISTRY
    )

    assert doc["schema"] == rp.SCHEMA
    assert doc["status"] == "OK"
    assert doc["readonly"] is True
    assert doc["side_effects"] == []
    assert doc["asof"] == "20260126"
    period = doc["period"]
    assert period["days_requested"] == 7
    assert period["start"] == "20260120"
    assert period["end"] == "20260126"
    assert period["trading_days"] == 7
    assert period["rows"] == 44  # 20 + 20 + 4
    assert period["rows_out_of_window"] == 1
    assert period["rows_missing_trade_date"] == 0
    assert doc["inputs"]["rows"]["count_input"] == 45
    assert doc["inputs"]["rows"]["invalid_rows"] == 0
    assert doc["inputs"]["cards"]["source"] == "injected"
    assert doc["inputs"]["cards"]["count"] == 3

    strategies = doc["strategies"]
    assert [entry["strategy_id"] for entry in strategies] == [
        "chan_monitor",
        "kg_boost",
        "trend_follow",
    ]
    by_id = {entry["strategy_id"]: entry for entry in strategies}

    kg = by_id["kg_boost"]
    assert kg["health_level"] == "green"
    assert kg["sample_count"] == 20
    assert kg["windows"]["t1"]["win_rate"] == 100.0
    assert kg["windows"]["t1"]["avg_ret"] == 1.0
    assert kg["windows"]["t5"]["avg_ret"] == 2.0
    assert kg["weekly"]["trading_days"] == 5
    assert kg["weekly"]["first_date"] == "20260120"
    assert kg["weekly"]["last_date"] == "20260124"
    assert len(kg["weekly"]["daily"]) == 5
    assert kg["weekly"]["daily"][0]["sample_count"] == 4
    assert kg["card"]["present"] is True
    assert kg["card"]["display_name"] == "KG 助推"
    assert kg["card"]["owner"] == "quant-kg"

    chan = by_id["chan_monitor"]
    assert chan["health_level"] == "red"
    assert chan["windows"]["t1"]["win_rate"] == 0.0
    assert chan["windows"]["t1"]["avg_ret"] == -2.0
    assert chan["bias_ticket_ids"] == []

    trend = by_id["trend_follow"]
    assert trend["health_level"] == "insufficient"
    assert trend["sample_count"] == 4
    assert trend["weekly"]["trading_days"] == 2

    assert doc["health_levels"] == {
        "green": 1,
        "yellow": 0,
        "red": 1,
        "insufficient": 1,
    }
    assert doc["summary"]["strategies"] == 3
    assert doc["summary"]["strategies_with_samples"] == 3
    assert doc["summary"]["bias_tickets"] == 1
    ticket = doc["bias_tickets"][0]
    assert ticket["bias_type"] == "insufficient"
    assert ticket["strategy_id"] == "trend_follow"
    assert ticket["owner_upstream"] == "quant-classic"
    assert doc["summary"]["upstream_owners"] == {"quant-classic": 1}


# ── 2. regime 偏差票 / execution 票：上游字段与证据引用 ────────────────
def test_regime_bias_ticket_weekly_schema():
    rows = []
    index = 0
    for offset in range(5):
        trade_date = "202601%02d" % (20 + offset)
        for _ in range(4):
            rows.append(
                _row(
                    "%06d.SH" % (600000 + index),
                    trade_date,
                    _contrib("kg_boost"),
                    ret_t1=3.0,
                    ret_t5=2.0,
                    regime="bull",
                )
            )
            index += 1
        for _ in range(4):
            rows.append(
                _row(
                    "%06d.SH" % (600000 + index),
                    trade_date,
                    _contrib("kg_boost"),
                    ret_t1=-3.0,
                    ret_t5=-2.0,
                    regime="bear",
                )
            )
            index += 1
    for row in rows[:10]:  # 10/40 = 25% 分歧 → execution 票
        row["divergence"] = True
    cards = _cards(("kg_boost", "quant-kg", "KG 助推"))
    doc = rp.build_weekly_health_report(
        rows=rows,
        days=7,
        asof="20260124",
        cards=cards,
        registry={"enabled": ["kg_boost"], "weights": {"kg_boost": 0.5}, "top_n": {}},
    )
    by_id = {entry["strategy_id"]: entry for entry in doc["strategies"]}
    entry = by_id["kg_boost"]
    assert len(doc["strategies"]) == 1
    assert entry["regime_status"] == "available"
    assert entry["regime_breakdown"]["bull"]["t1"]["win_rate"] == 100.0
    assert entry["regime_breakdown"]["bear"]["t1"]["win_rate"] == 0.0
    assert entry["health_level"] == "yellow"  # 分歧率 25% 触黄线，regime 差 100pp
    tickets = doc["bias_tickets"]
    assert {ticket["bias_type"] for ticket in tickets} == {"regime", "execution"}
    for ticket in tickets:
        assert ticket["schema"] == rp.TICKET_SCHEMA
        assert ticket["ticket_id"].startswith("BTW-20260124-kg_boost-")
        assert ticket["strategy_id"] == "kg_boost"
        assert ticket["owner_upstream"] == "quant-kg"
        assert ticket["owner_source"] == rp.OWNER_SOURCE_CARDS
        assert ticket["status"] == "draft"
        assert ticket["not_auto_apply"] is True
        assert ticket["priority"] in rp._health.PRIORITIES
        assert ticket["period"] == {
            "start": doc["period"]["start"],
            "end": doc["period"]["end"],
        }
        assert ticket["asof"] == "20260124"
        assert isinstance(ticket["evidence"], list) and ticket["evidence"]
        refs = ticket["evidence_refs"]
        assert isinstance(refs, list) and refs
        ref_types = {ref["type"] for ref in refs}
        assert {"health_card", "metric", "regime_breakdown", "strategy_card"} <= ref_types
        for ref in refs:
            assert ref["strategy_id"] == "kg_boost"
    regime_ticket = [t for t in tickets if t["bias_type"] == "regime"][0]
    assert "100.0 个百分点" in "".join(regime_ticket["evidence"])


# ── 3. 样本不足 → insufficient + 上游 owner 显式 null ─────────────────
def test_insufficient_samples_owner_null():
    # 4 行 / 4 个不同标的：单标的占比 25% < 集中度阈值，只触发样本不足票
    rows = _spread_rows("kg_boost", 24, 2, 2, ret_t1=1.0, ret_t5=2.0)
    doc = rp.build_weekly_health_report(
        rows=rows,
        days=7,
        asof="20260125",
        cards=[],
        registry=_EMPTY_REGISTRY,
    )
    assert doc["strategies"][0]["sample_count"] == 4
    assert doc["strategies"][0]["health_level"] == "insufficient"
    tickets = doc["bias_tickets"]
    assert len(tickets) == 1
    ticket = tickets[0]
    assert ticket["bias_type"] == "insufficient"
    assert ticket["owner_upstream"] is None
    assert ticket["owner_source"] is None
    assert ticket["not_auto_apply"] is True


# ── 4. 窗口内无行 → status=insufficient_data，不把空周报当 0 收益 ─────
def test_empty_window_insufficient_data():
    rows = _spread_rows("kg_boost", 1, 2, 3, ret_t1=1.0, ret_t5=2.0)
    doc = rp.build_weekly_health_report(
        rows=rows, days=7, asof="20260126", cards=[], registry=_REGISTRY
    )
    assert doc["status"] == "insufficient_data"
    assert doc["asof"] == "20260126"
    assert doc["period"]["start"] == "20260120"
    assert doc["period"]["end"] == "20260126"
    assert doc["period"]["rows"] == 0
    assert doc["period"]["trading_days"] == 0
    assert doc["period"]["rows_out_of_window"] == 6
    assert doc["period"]["rows_missing_trade_date"] == 0
    # registry enabled 的两策略仍出卡（无样本 → insufficient），不臆造收益
    assert [entry["strategy_id"] for entry in doc["strategies"]] == [
        "chan_monitor",
        "kg_boost",
    ]
    for entry in doc["strategies"]:
        assert entry["sample_count"] == 0
        assert entry["health_level"] == "insufficient"
        assert entry["windows"]["t1"]["win_rate"] is None
        assert entry["windows"]["t1"]["avg_ret"] is None
        assert entry["weekly"]["trading_days"] == 0
        assert entry["weekly"]["daily"] == []
    # 空窗口且 enabled → 只发 insufficient 票（不会造收益票）
    assert [t["bias_type"] for t in doc["bias_tickets"]] == [
        "insufficient",
        "insufficient",
    ]


# ── 5. 收益缺失 → 显式 null + data 票，不造 0 ─────────────────────────
def test_missing_returns_null_and_data_ticket():
    rows = _spread_rows("kg_boost", 20, 5, 5)
    cards = _cards(("kg_boost", "quant-kg", "KG 助推"))
    doc = rp.build_weekly_health_report(
        rows=rows,
        days=7,
        asof="20260124",
        cards=cards,
        registry=_EMPTY_REGISTRY,
    )
    assert doc["status"] == "OK"
    entry = doc["strategies"][0]
    assert entry["sample_count"] == 25
    assert entry["windows"]["t1"] == {"count": 0, "win_rate": None, "avg_ret": None}
    assert entry["windows"]["t5"] == {"count": 0, "win_rate": None, "avg_ret": None}
    assert entry["data_quality"]["coverage_t1"] == 0.0
    assert entry["regime_status"] == "insufficient"
    assert entry["regime_breakdown"] == {}
    assert entry["max_drawdown"]["status"] == "not_provided"
    assert entry["max_drawdown"]["max_drawdown"] is None
    assert entry["turnover"]["available"] is False
    assert entry["cost"]["available"] is False
    assert entry["health_level"] == "insufficient"
    assert entry["weekly"]["daily"][0]["t1"]["win_rate"] is None
    tickets = doc["bias_tickets"]
    assert [ticket["bias_type"] for ticket in tickets] == ["data"]
    assert tickets[0]["priority"] == "P1"


# ── 6. 策略卡自动读取（strategy_cards）：10 卡 / 2 enabled / 8 shadow ──
def test_cards_auto_from_strategy_cards(tmp_path, monkeypatch):
    registry_file = tmp_path / "registry.json"
    registry_file.write_text(json.dumps(_REGISTRY, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(rp._health, "REGISTRY_PATH", str(registry_file))
    rows = _spread_rows("kg_boost", 20, 5, 5, ret_t1=1.0, ret_t5=2.0)
    doc = rp.build_weekly_health_report(rows=rows, days=7, asof="20260124")
    cards = doc["inputs"]["cards"]
    assert cards["available"] is True
    assert cards["source"] == "strategy_cards"
    assert cards["count"] == 10
    assert len(cards["shadow_candidates"]) == 8
    assert cards["registry_view"]["total_cards"] == 10
    ids = [entry["strategy_id"] for entry in doc["strategies"]]
    assert len(ids) == 11  # 10 张卡 + registry-only kg_boost
    assert "kg_boost" in ids and "chan_monitor" in ids
    # kg_boost 有 25 样本 → green 无票；chan_monitor 无样本但 enabled → insufficient 票；
    # 8 张 shadow 无样本且未启用 → 只列卡不出票
    assert [ticket["strategy_id"] for ticket in doc["bias_tickets"]] == [
        "chan_monitor"
    ]
    assert doc["bias_tickets"][0]["bias_type"] == "insufficient"


# ── 7. CLI：JSON / MD / 原子写 / 参数校验 ─────────────────────────────
def _run_cli(args):
    return subprocess.run(
        [sys.executable, "-X", "utf8", _MODULE] + args,
        cwd=_QB_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def test_cli_json_and_md(tmp_path):
    rows = _spread_rows("kg_boost", 20, 5, 5, ret_t1=1.0, ret_t5=2.0)
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(
        json.dumps({"rows": rows}, ensure_ascii=False), encoding="utf-8"
    )

    out_json = tmp_path / "weekly.json"
    proc = _run_cli(
        ["--rows-file", str(rows_file), "--days", "7", "--out", str(out_json)]
    )
    assert proc.returncode == 0, proc.stderr
    assert "[strategy_health_report]" in proc.stdout
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["schema"] == rp.SCHEMA
    assert payload["period"]["rows"] == 25
    strategies = {entry["strategy_id"]: entry for entry in payload["strategies"]}
    assert strategies["kg_boost"]["sample_count"] == 25
    assert strategies["kg_boost"]["health_level"] == "green"
    assert not os.path.exists(str(out_json) + ".tmp")

    proc_md = _run_cli(["--rows-file", str(rows_file), "--days", "7", "--format", "md"])
    assert proc_md.returncode == 0, proc_md.stderr
    assert "# 策略健康周报" in proc_md.stdout
    assert "kg_boost" in proc_md.stdout
    assert "上游 Bias Ticket" in proc_md.stdout
    assert "not_auto_apply" in proc_md.stdout

    md_file = tmp_path / "weekly.md"
    proc_md_out = _run_cli(
        [
            "--rows-file",
            str(rows_file),
            "--format",
            "md",
            "--out",
            str(md_file),
        ]
    )
    assert proc_md_out.returncode == 0, proc_md_out.stderr
    assert "# 策略健康周报" in md_file.read_text(encoding="utf-8")

    assert _run_cli(["--rows-file", str(rows_file), "--days", "0"]).returncode == 2
    assert _run_cli(["--no-such-flag"]).returncode == 2


# ── 8. 只读：输入文件零改动 + 原子写失败不留半文件 ───────────────────
def test_readonly_inputs_and_atomic_write(tmp_path, monkeypatch):
    registry_file = tmp_path / "registry.json"
    registry_file.write_text(json.dumps(_REGISTRY, ensure_ascii=False), encoding="utf-8")
    cards_file = tmp_path / "cards.json"
    cards_file.write_text(
        json.dumps(
            [{"strategy_id": "kg_boost", "owner": "quant-kg", "display_name": "KG"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rows = _spread_rows("kg_boost", 20, 5, 5, ret_t1=1.0, ret_t5=2.0)
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    inputs = (registry_file, cards_file, rows_file)
    before = {path.name: path.read_bytes() for path in inputs}

    out_json = tmp_path / "weekly.json"
    doc = rp.build_weekly_health_report(
        rows_file=str(rows_file),
        cards_file=str(cards_file),
        registry_path=str(registry_file),
        days=7,
        asof="20260124",
        out_path=str(out_json),
    )
    after = {path.name: path.read_bytes() for path in inputs}
    assert before == after  # 输入文件零改动（只读）
    assert doc["readonly"] is True
    assert doc["side_effects"] == []
    assert doc["inputs"]["rows"]["source"].startswith("rows_file:")
    by_id = {entry["strategy_id"]: entry for entry in doc["strategies"]}
    assert by_id["kg_boost"]["card"]["owner"] == "quant-kg"
    assert os.path.exists(out_json)
    assert doc["out_path"] == str(out_json)
    assert "out_path" not in json.loads(out_json.read_text(encoding="utf-8"))
    assert not os.path.exists(str(out_json) + ".tmp")

    # 原子写失败：不产出半文件
    def boom(*args, **kwargs):
        raise RuntimeError("盘满")

    monkeypatch.setattr(rp.json, "dumps", boom)
    out_bad = tmp_path / "weekly_bad.json"
    with pytest.raises(RuntimeError):
        rp.build_weekly_health_report(
            rows_file=str(rows_file),
            cards_file=str(cards_file),
            registry_path=str(registry_file),
            days=7,
            asof="20260124",
            out_path=str(out_bad),
        )
    assert not os.path.exists(out_bad)
    assert not os.path.exists(str(out_bad) + ".tmp")


# ── 9. rows 注入 vs rows-file 等价 ────────────────────────────────────
def test_rows_file_equals_injection(tmp_path):
    rows = _spread_rows("kg_boost", 20, 5, 5, ret_t1=1.0, ret_t5=2.0)
    cards = _cards(("kg_boost", "quant-kg", "KG 助推"))
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(
        json.dumps({"rows": rows}, ensure_ascii=False), encoding="utf-8"
    )
    injected = rp.build_weekly_health_report(
        rows=rows, days=7, asof="20260124", cards=cards, registry=_REGISTRY
    )
    via_file = rp.build_weekly_health_report(
        rows_file=str(rows_file),
        days=7,
        asof="20260124",
        cards=cards,
        registry=_REGISTRY,
    )
    assert via_file["period"] == injected["period"]
    assert via_file["strategies"] == injected["strategies"]
    assert via_file["bias_tickets"] == injected["bias_tickets"]
    assert via_file["health_levels"] == injected["health_levels"]
    assert via_file["inputs"]["rows"]["source"].startswith("rows_file:")
    assert injected["inputs"]["rows"]["source"] == "injected"


# ── 10. 窗口/缺日期过滤 + 参数校验 ────────────────────────────────────
def test_window_filter_and_validation():
    rows = [
        _row("6000%02d.SH" % day, "202601%02d" % day, _contrib("kg_boost"), ret_t1=1.0)
        for day in range(1, 11)
    ]
    rows.append(_row("600099.SH", "", _contrib("kg_boost"), ret_t1=1.0))
    rows.append(_row("600098.SH", None, _contrib("kg_boost"), ret_t1=1.0))
    rows.append("garbage")
    doc = rp.build_weekly_health_report(
        rows=rows, days=7, asof="20260110", cards=[], registry=_EMPTY_REGISTRY
    )
    period = doc["period"]
    assert period["start"] == "20260104"
    assert period["end"] == "20260110"
    assert period["rows"] == 7
    assert period["rows_out_of_window"] == 3
    assert period["rows_missing_trade_date"] == 2
    assert doc["inputs"]["rows"]["count_input"] == 12
    assert doc["inputs"]["rows"]["invalid_rows"] == 1
    assert period["trading_days"] == 7
    assert doc["status"] == "OK"

    explicit = rp.build_weekly_health_report(
        rows=rows,
        start_date="20260101",
        end_date="20260103",
        cards=[],
        registry=_EMPTY_REGISTRY,
    )
    assert explicit["period"]["days_requested"] is None
    assert explicit["period"]["start"] == "20260101"
    assert explicit["period"]["end"] == "20260103"
    assert explicit["period"]["rows"] == 3

    with pytest.raises(ValueError):
        rp.build_weekly_health_report(rows=rows, cards=[], registry=_EMPTY_REGISTRY, days=0)
    with pytest.raises(ValueError):
        rp.build_weekly_health_report(rows=rows, cards=[], registry=_EMPTY_REGISTRY, asof="2026-13-99")
    with pytest.raises(ValueError):
        rp.build_weekly_health_report(
            rows=rows,
            cards=[],
            registry=_EMPTY_REGISTRY,
            start_date="20260110",
            end_date="20260101",
        )


# ── 11. strategy_cards 不可用 → 显式 unavailable，统计照常 ────────────
def test_cards_module_unavailable_explicit(monkeypatch):
    def boom():
        raise ImportError("strategy_cards 未落地")

    monkeypatch.setattr(rp, "_import_strategy_cards", boom)
    rows = _spread_rows("kg_boost", 20, 5, 5, ret_t1=1.0, ret_t5=2.0)
    doc = rp.build_weekly_health_report(rows=rows, days=7, asof="20260124")
    cards = doc["inputs"]["cards"]
    assert cards["available"] is False
    assert cards["source"] == "unavailable"
    assert "strategy_cards" in cards["error"]
    assert cards["count"] == 0
    assert cards["registry_view"] is None
    assert doc["status"] == "OK"
    assert [entry["strategy_id"] for entry in doc["strategies"]] == ["kg_boost"]
    assert doc["strategies"][0]["health_level"] == "green"
    assert doc["strategies"][0]["card"]["present"] is False


# ── 12. MD 渲染含关键小节与口径说明 ──────────────────────────────────
def test_render_markdown_sections():
    rows = _spread_rows("kg_boost", 20, 5, 5, ret_t1=1.0, ret_t5=2.0)
    cards = _cards(("kg_boost", "quant-kg", "KG 助推"))
    doc = rp.build_weekly_health_report(
        rows=rows, days=7, asof="20260124", cards=cards, registry=_EMPTY_REGISTRY
    )
    text = rp.render_markdown(doc)
    assert "# 策略健康周报" in text
    assert "## 一、健康总览" in text
    assert "## 二、策略周度明细" in text
    assert "## 四、上游 Bias Ticket（draft / not_auto_apply）" in text
    assert "## 五、regime 分层" in text
    assert "## 六、口径与数据缺口" in text
    assert "kg_boost" in text
    assert "不造数" in text
