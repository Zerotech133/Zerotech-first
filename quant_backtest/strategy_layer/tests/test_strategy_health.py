# -*- coding: utf-8 -*-
"""strategy_health 单测：HealthCard / BiasTicket / 输出（WO-STRAT-HEALTH-20261031）。

全程离线：rows / 权益曲线 / 成本 / registry / 策略卡全部注入（tmp_path 假数据），
禁连真实 PG、禁读真实 data/ 生产档、禁跑生产任务。覆盖：
  · 健康（green）与红/黄等级、regime 分层、最大回撤、换手/成本、分歧率；
  · 偏差票（regime/overfit/cost/execution/concentration/data/insufficient）
    最小 schema 与 status=draft；
  · 样本不足 / 收益缺失 → 显式 null / insufficient，不造数；
  · rows JSON 文件、CLI JSON/MD、原子落盘、只读（registry 不被改写）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from strategy_layer import strategy_health as sh

_MODULE = os.path.join(os.path.dirname(os.path.abspath(sh.__file__)), "strategy_health.py")
_QB_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(sh.__file__)))


# ── 合成数据工厂 ──────────────────────────────────────────────────────
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


def _contrib(sid, weight=0.5):
    return {sid: {"score": 50.0, "weight": weight, "action": "buy"}}


def _rows_many(sid, count, ret_t1=1.0, ret_t5=2.0, **kwargs):
    """count 行单策略样本（标的/交易日分散，避免集中度票误触发）。"""
    rows = []
    for i in range(count):
        rows.append(
            _row(
                "%06d.SH" % (600000 + i),
                "202601%02d" % (1 + (i % 28)),
                _contrib(sid),
                ret_t1=ret_t1,
                ret_t5=ret_t5,
                **kwargs
            )
        )
    return rows


def _registry_view(**overrides):
    view = {
        "enabled": ["kg_boost", "chan_monitor"],
        "weights": {"kg_boost": 0.5, "chan_monitor": 0.5},
        "top_n": {"kg_boost": 50},
    }
    view.update(overrides)
    return view


# 行级场景默认不引入 registry 里的空样本策略（每个测试显式声明 universe）
_EMPTY_REGISTRY = {"enabled": [], "weights": {}, "top_n": {}}


@pytest.fixture(autouse=True)
def _isolate_default_registry(tmp_path, monkeypatch):
    """默认 registry 路径指到临时目录：测试不读真实 strategy_registry.json，
    行级场景的 strategy universe 只由注入的 registry/卡/rows 决定。"""
    monkeypatch.setattr(sh, "REGISTRY_PATH", str(tmp_path / "no_registry.json"))


# ── 1. 健康策略 → green，统计口径逐项核对 ─────────────────────────────
def test_healthy_strategy_green_and_metrics():
    doc = sh.build_strategy_health(
        rows=_rows_many("kg_boost", 40),
        registry=_registry_view(),
        cards=[],
    )
    assert doc["schema"] == sh.SCHEMA
    assert doc["status"] == "OK"
    assert doc["readonly"] is True
    assert doc["side_effects"] == []
    assert doc["total_rows"] == 40
    assert doc["invalid_rows"] == 0
    assert doc["asof"] == "20260128"
    assert doc["inputs"]["rows"]["source"] == "injected"

    card = doc["strategies"]["kg_boost"]
    assert card["sample_count"] == 40
    assert card["windows"]["t1"]["count"] == 40
    assert card["windows"]["t1"]["win_rate"] == 100.0
    assert card["windows"]["t1"]["avg_ret"] == 1.0
    assert card["windows"]["t5"]["win_rate"] == 100.0
    assert card["windows"]["t5"]["avg_ret"] == 2.0
    assert card["divergence_rate"] == 0.0
    assert card["health_level"] == "green"
    assert "样本 40" in card["health_reasons"][0]
    assert card["bias_ticket_ids"] == []
    # registry 展示口径（只读展示，不是改权重）
    assert card["registry"] == {
        "known": True,
        "enabled": True,
        "enabled_source": "registry",
        "weight": 0.5,
        "weight_group": "full",
        "top_n": 50,
        "registry_available": True,
    }
    # 未注入的维度显式 null / not_provided，不造数
    assert card["max_drawdown"]["status"] == "not_provided"
    assert card["max_drawdown"]["max_drawdown"] is None
    assert card["turnover"]["available"] is False
    assert card["turnover"]["mean"] is None
    assert card["cost"]["available"] is False
    assert card["cost"]["total"] is None
    # registry 里 chan_monitor 无样本 → 仍出卡，显式 insufficient
    chan = doc["strategies"]["chan_monitor"]
    assert chan["sample_count"] == 0
    assert chan["health_level"] == "insufficient"
    assert chan["windows"]["t1"]["win_rate"] is None
    assert doc["summary"]["green"] == 1
    assert doc["summary"]["insufficient"] == 1


# ── 2. 样本不足 → insufficient（真实统计照出，但不下结论） ─────────────
def test_insufficient_samples_ticket():
    doc = sh.build_strategy_health(rows=_rows_many("kg_boost", 5), cards=[])
    card = doc["strategies"]["kg_boost"]
    assert card["sample_count"] == 5
    assert card["windows"]["t1"]["win_rate"] == 100.0  # 真实值不是造数
    assert card["health_level"] == "insufficient"
    assert "样本数 5" in card["health_reasons"][0]
    tickets = doc["bias_tickets"]
    assert len(tickets) == 1
    ticket = tickets[0]
    assert ticket["bias_type"] == "insufficient"
    assert ticket["priority"] == "P1"
    assert ticket["status"] == "draft"
    assert ticket["ticket_id"] == "BT-kg_boost-insufficient"
    assert "样本数 5" in ticket["evidence"][0]
    assert "只读" in ticket["suggested_action"]


# ── 3. 收益缺失 → 显式 null + data 票，不拿 0 充数 ─────────────────────
def test_missing_returns_null_and_data_ticket():
    rows = _rows_many("kg_boost", 30, ret_t1=None, ret_t5=None)
    doc = sh.build_strategy_health(rows=rows, cards=[])
    card = doc["strategies"]["kg_boost"]
    assert card["sample_count"] == 30
    assert card["windows"]["t1"] == {"count": 0, "win_rate": None, "avg_ret": None}
    assert card["windows"]["t5"] == {"count": 0, "win_rate": None, "avg_ret": None}
    assert card["data_quality"]["coverage_t1"] == 0.0
    assert card["data_quality"]["coverage_t5"] == 0.0
    assert card["health_level"] == "insufficient"
    tickets = doc["bias_tickets"]
    assert [t["bias_type"] for t in tickets] == ["data"]
    assert tickets[0]["priority"] == "P1"
    assert "覆盖" in tickets[0]["evidence"][0]
    # 不造数：没有把缺失收益写成 0
    assert card["windows"]["t1"]["avg_ret"] is None
    assert card["windows"]["t1"]["win_rate"] is None


# ── 4. regime 分层偏差 → regime 票（胜率差达标才发） ──────────────────
def test_regime_bias_ticket():
    rows = []
    for i in range(20):
        rows.append(
            _row(
                "%06d.SH" % (600000 + i),
                "202601%02d" % (1 + i % 28),
                _contrib("kg_boost"),
                ret_t1=3.0,
                ret_t5=2.0,
                regime="bull",
            )
        )
    for i in range(20):
        rows.append(
            _row(
                "%06d.SZ" % (1 + i),
                "202601%02d" % (1 + i % 28),
                _contrib("kg_boost"),
                ret_t1=-3.0,
                ret_t5=-2.0,
                regime="bear",
            )
        )
    doc = sh.build_strategy_health(rows=rows, cards=[])
    card = doc["strategies"]["kg_boost"]
    assert card["regime_status"] == "available"
    assert card["regime_breakdown"]["bull"]["count"] == 20
    assert card["regime_breakdown"]["bull"]["t1"]["win_rate"] == 100.0
    assert card["regime_breakdown"]["bear"]["t1"]["win_rate"] == 0.0
    # T+1 胜率 50%、平均 0 → 不算红/黄，但 regime 胜率差 100pp → 偏差票
    assert card["health_level"] == "green"
    tickets = doc["bias_tickets"]
    assert [t["bias_type"] for t in tickets] == ["regime"]
    assert "100.0 个百分点" in tickets[0]["evidence"][0]
    assert tickets[0]["priority"] == "P2"


# ── 5. 短窗/长窗背离 → overfit 票 ─────────────────────────────────────
def test_overfit_bias_ticket():
    doc = sh.build_strategy_health(
        rows=_rows_many("kg_boost", 40, ret_t1=2.0, ret_t5=-3.0), cards=[]
    )
    card = doc["strategies"]["kg_boost"]
    assert card["windows"]["t1"]["win_rate"] == 100.0
    assert card["windows"]["t5"]["avg_ret"] == -3.0
    assert card["health_level"] == "green"  # 评级只看主口径 T+1
    tickets = doc["bias_tickets"]
    assert [t["bias_type"] for t in tickets] == ["overfit"]
    assert "T+5 平均收益" in tickets[0]["evidence"][0]


# ── 6. 分歧率高 → execution 票 + yellow ──────────────────────────────
def test_divergence_execution_ticket():
    rows = _rows_many("kg_boost", 40)
    for row in rows[:10]:
        row["divergence"] = True
    doc = sh.build_strategy_health(rows=rows, cards=[])
    card = doc["strategies"]["kg_boost"]
    assert card["divergence_count"] == 10
    assert card["divergence_rate"] == 25.0
    assert card["health_level"] == "yellow"
    tickets = doc["bias_tickets"]
    assert [t["bias_type"] for t in tickets] == ["execution"]
    assert "25.0%" in tickets[0]["evidence"][0]


# ── 7. 单标的集中 → concentration 票 ─────────────────────────────────
def test_concentration_ticket():
    rows = []
    for i in range(40):
        symbol = "600000.SH" if i < 30 else "%06d.SH" % (600001 + i)
        rows.append(
            _row(
                symbol,
                "202601%02d" % (1 + i % 28),
                _contrib("kg_boost"),
                ret_t1=1.0,
                ret_t5=2.0,
            )
        )
    doc = sh.build_strategy_health(rows=rows, cards=[])
    card = doc["strategies"]["kg_boost"]
    assert card["concentration"]["top_symbol"] == "600000.SH"
    assert card["concentration"]["top_symbol_share"] == 0.75
    tickets = doc["bias_tickets"]
    assert [t["bias_type"] for t in tickets] == ["concentration"]
    assert "75.0%" in tickets[0]["evidence"][0]


# ── 8. 行级换手 → 成本票；行级 unit 显式 null ─────────────────────────
def test_cost_ticket_from_row_turnover():
    doc = sh.build_strategy_health(
        rows=_rows_many("kg_boost", 40, turnover=4.0), cards=[]
    )
    card = doc["strategies"]["kg_boost"]
    assert card["turnover"]["available"] is True
    assert card["turnover"]["mean"] == 4.0
    assert card["turnover"]["rows_with_value"] == 40
    assert card["turnover"]["unit"] is None  # 来源没给单位就不猜
    assert card["cost"]["available"] is False
    tickets = doc["bias_tickets"]
    assert [t["bias_type"] for t in tickets] == ["cost"]
    assert "换手" in tickets[0]["evidence"][0]


# ── 9. 注入成本/收益比 → 成本票（combo_review ROI 口径接入点） ─────────
def test_cost_ticket_from_injected_cost_ratio():
    doc = sh.build_strategy_health(
        rows=_rows_many("kg_boost", 40),
        costs={"kg_boost": {"cost_ratio": 0.45, "unit": "CNY", "source": "combo_review"}},
        cards=[],
    )
    card = doc["strategies"]["kg_boost"]
    assert card["cost"]["available"] is True
    assert card["cost"]["cost_ratio"] == 0.45
    assert card["cost"]["source"] == "combo_review"
    assert card["cost"]["unit"] == "CNY"
    assert [t["bias_type"] for t in doc["bias_tickets"]] == ["cost"]
    assert "成本/收益比" in doc["bias_tickets"][0]["evidence"][0]


# ── 10. 最大回撤只在有权益曲线时算；回撤红线 → red ─────────────────────
def test_max_drawdown_only_with_equity_curve():
    doc = sh.build_strategy_health(
        rows=_rows_many("kg_boost", 40),
        equity_curves={"kg_boost": [100.0, 120.0, 90.0, 110.0]},
        cards=[],
    )
    card = doc["strategies"]["kg_boost"]
    assert card["max_drawdown"]["status"] == "computed"
    assert card["max_drawdown"]["max_drawdown"] == -25.0
    assert card["max_drawdown"]["points"] == 4
    assert card["health_level"] == "red"
    assert "最大回撤" in "".join(card["health_reasons"])
    # 另一策略没注入曲线 → 显式 null
    other = doc["strategies"].get("chan_monitor")
    assert other is None or other["max_drawdown"]["status"] == "not_provided"


def test_equity_curve_insufficient_points():
    doc = sh.build_strategy_health(
        rows=_rows_many("kg_boost", 40),
        equity_curves={"kg_boost": [100.0]},
        cards=[],
    )
    block = doc["strategies"]["kg_boost"]["max_drawdown"]
    assert block["status"] == "insufficient"
    assert block["max_drawdown"] is None


# ── 11. registry + 策略卡注入：10 卡视图可扩展，缺卡不臆造 ─────────────
def test_cards_and_registry_injection():
    cards = [
        {
            "strategy_id": "kg_boost",
            "display_name": "KG 助推",
            "owner": "quant",
            "version": "1.0",
            "regime_tags": ["bull", "range"],
            "known_failure_modes": ["震荡市假信号"],
        },
        {"strategy_id": "chan_monitor", "name": "缠论监控"},
        {"strategy_id": "trend_follow", "display_name": "趋势跟随"},
        {"strategy_id": "kg_boost", "display_name": "重复卡"},  # 重复 id 显式计数
    ]
    rows = []
    for i in range(40):
        rows.append(
            _row(
                "%06d.SH" % (600000 + i),
                "202601%02d" % (1 + i % 28),
                {"kg_boost": {"weight": 0.5}, "chan_monitor": {"weight": 0.3}},
                ret_t1=1.0,
                ret_t5=2.0,
            )
        )
    doc = sh.build_strategy_health(
        rows=rows,
        cards=cards,
        registry=_registry_view(weights={"kg_boost": 0.5, "chan_monitor": 0.3}),
    )
    assert doc["inputs"]["cards"]["source"] == "injected"
    assert doc["inputs"]["cards"]["count"] == 3
    assert doc["inputs"]["cards"]["duplicates"] == 1
    assert doc["inputs"]["registry"]["source"] == "injected"
    assert set(doc["strategies"]) == {"kg_boost", "chan_monitor", "trend_follow"}

    kg = doc["strategies"]["kg_boost"]
    assert kg["card"]["present"] is True
    assert kg["card"]["display_name"] == "KG 助推"
    assert kg["card"]["regime_tags"] == ["bull", "range"]
    assert "display_name" in kg["card"]["fields"]
    assert kg["registry"]["weight_group"] == "full"
    # 多源行跨桶：两个贡献策略各记 40 样本
    assert kg["sample_count"] == 40
    chan = doc["strategies"]["chan_monitor"]
    assert chan["card"]["display_name"] == "缠论监控"
    assert chan["sample_count"] == 40
    assert chan["registry"]["weight_group"] == "shadow"
    # 卡里存在但 registry 没有、行里也没有 → 显式 insufficient，不臆造数据
    trend = doc["strategies"]["trend_follow"]
    assert trend["card"]["present"] is True
    assert trend["sample_count"] == 0
    assert trend["health_level"] == "insufficient"
    assert trend["registry"]["known"] is False
    assert trend["windows"]["t1"]["win_rate"] is None


# ── 12. strategy_cards 不可用 → 显式 unavailable（不影响健康统计） ─────
def test_cards_module_unavailable_explicit(monkeypatch):
    def boom():
        raise ImportError("strategy_cards 未落地")

    monkeypatch.setattr(sh, "_import_strategy_cards", boom)
    doc = sh.build_strategy_health(
        rows=_rows_many("kg_boost", 21), registry=_registry_view()
    )
    cards = doc["inputs"]["cards"]
    assert cards["available"] is False
    assert cards["source"] == "unavailable"
    assert "strategy_cards" in cards["error"]
    assert cards["count"] == 0
    assert doc["strategies"]["kg_boost"]["health_level"] == "green"


# ── 13. 行级返回形态兼容：嵌套 returns、contributions JSON 串 ──────────
def test_nested_returns_and_json_contributions():
    rows = [
        {
            "trade_date": "20260105",
            "symbol": "600000.SH",
            "contributions": json.dumps({"kg_boost": {"weight": 0.5}}),
            "returns": {"t1": 1.5, "t5": 2.5},
            "regime": "bull",
        },
        {
            "symbol": "000001.SZ",  # 无 trade_date 但有 symbol → 仍有效
            "contributions": {"kg_boost": {"weight": 0.5}},
            "ret": {"t1": -0.5, "t5": -1.0},
        },
    ]
    doc = sh.build_strategy_health(rows=rows, cards=[])
    card = doc["strategies"]["kg_boost"]
    assert card["sample_count"] == 2
    assert card["windows"]["t1"]["count"] == 2
    assert card["windows"]["t1"]["avg_ret"] == 0.5
    assert card["windows"]["t5"]["avg_ret"] == 0.75


# ── 14. 无效行显式计数；行级样本不足也不伪造 ─────────────────────────
def test_invalid_rows_counted():
    rows = [
        "garbage",
        123,
        {},
        {"trade_date": "20260101", "contributions": {}},
        {"trade_date": "20260101", "contributions": {"kg_boost": {"weight": 0.5}}},
    ]
    doc = sh.build_strategy_health(rows=rows, cards=[])
    assert doc["invalid_rows"] == 4
    assert doc["total_rows"] == 1
    card = doc["strategies"]["kg_boost"]
    assert card["sample_count"] == 1
    assert card["windows"]["t1"]["win_rate"] is None  # 无收益，不造 0
    assert card["health_level"] == "insufficient"


# ── 15. 偏差票最小 schema（多规则命中场景） ────────────────────────────
def test_bias_ticket_min_schema():
    rows = []
    for i in range(40):
        if i < 20:
            row = _row(
                "600000.SH",
                "202601%02d" % (1 + i % 28),
                _contrib("kg_boost"),
                ret_t1=3.0,
                ret_t5=2.0,
                regime="bull",
            )
        else:
            row = _row(
                "600000.SH",
                "202601%02d" % (1 + i % 28),
                _contrib("kg_boost"),
                ret_t1=-3.0,
                ret_t5=-2.0,
                regime="bear",
            )
        row["divergence"] = i < 10
        rows.append(row)
    doc = sh.build_strategy_health(rows=rows, cards=[])
    tickets = doc["bias_tickets"]
    assert {t["bias_type"] for t in tickets} == {"regime", "execution", "concentration"}
    assert len({t["ticket_id"] for t in tickets}) == len(tickets)
    required = {
        "ticket_id",
        "strategy_id",
        "bias_type",
        "evidence",
        "suggested_action",
        "priority",
        "status",
    }
    for ticket in tickets:
        assert required <= set(ticket)
        assert ticket["bias_type"] in sh.BIAS_TYPES
        assert ticket["priority"] in sh.PRIORITIES
        assert ticket["status"] == "draft"
        assert isinstance(ticket["evidence"], list) and ticket["evidence"]
        assert all(isinstance(item, str) and item for item in ticket["evidence"])
        assert "只读" in ticket["suggested_action"]


# ── 16. 阈值覆盖：可解释调整；未知键/非法值显式 ValueError ─────────────
def test_threshold_overrides_and_validation():
    rows = _rows_many("kg_boost", 15)
    default_doc = sh.build_strategy_health(rows=rows, cards=[])
    assert default_doc["strategies"]["kg_boost"]["health_level"] == "insufficient"
    custom_doc = sh.build_strategy_health(
        rows=rows, cards=[], thresholds={"min_samples": 10}
    )
    assert custom_doc["strategies"]["kg_boost"]["health_level"] == "green"
    with pytest.raises(ValueError):
        sh.build_strategy_health(rows=rows, cards=[], thresholds={"not_a_threshold": 1})
    with pytest.raises(ValueError):
        sh.build_strategy_health(
            rows=rows, cards=[], thresholds={"min_samples": "abc"}
        )
    assert sh.merge_thresholds() == sh.DEFAULT_THRESHOLDS


# ── 17. rows-file 注入与 CLI：JSON / MD 两种输出 ─────────────────────
def _run_cli(args):
    return subprocess.run(
        [sys.executable, "-X", "utf8", _MODULE] + args,
        cwd=_QB_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def test_rows_file_and_cli_outputs(tmp_path):
    rows = _rows_many("kg_boost", 21)
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps({"rows": rows}, ensure_ascii=False), encoding="utf-8")

    direct = sh.build_strategy_health(rows=rows, cards=[])
    via_file = sh.build_strategy_health(rows_file=str(rows_file), cards=[])
    assert via_file["total_rows"] == 21
    assert via_file["strategies"] == direct["strategies"]
    assert via_file["inputs"]["rows"]["source"].startswith("rows_file:")
    assert direct["inputs"]["rows"]["source"] == "injected"

    out_json = tmp_path / "health.json"
    proc = _run_cli(["--rows-file", str(rows_file), "--out", str(out_json)])
    assert proc.returncode == 0, proc.stderr
    assert "[strategy_health]" in proc.stdout
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["schema"] == sh.SCHEMA
    assert payload["strategies"]["kg_boost"]["sample_count"] == 21
    assert not os.path.exists(str(out_json) + ".tmp")

    proc_md = _run_cli(["--rows-file", str(rows_file), "--format", "md"])
    assert proc_md.returncode == 0, proc_md.stderr
    assert "# 策略健康卡" in proc_md.stdout
    assert "kg_boost" in proc_md.stdout
    assert "偏差票" in proc_md.stdout

    md_file = tmp_path / "health.md"
    proc_md_out = _run_cli(
        ["--rows-file", str(rows_file), "--format", "md", "--out", str(md_file)]
    )
    assert proc_md_out.returncode == 0, proc_md_out.stderr
    text = md_file.read_text(encoding="utf-8")
    assert "# 策略健康卡" in text and "kg_boost" in text

    proc_bad = _run_cli(["--no-such-flag"])
    assert proc_bad.returncode == 2


# ── 18. 只读：registry/策略卡/rows 文件字节不变，产出档原子写 ──────────
def test_readonly_does_not_mutate_inputs(tmp_path):
    registry_file = tmp_path / "registry.json"
    registry_file.write_text(
        json.dumps(_registry_view(), ensure_ascii=False), encoding="utf-8"
    )
    cards_file = tmp_path / "cards.json"
    cards_file.write_text(
        json.dumps([{"strategy_id": "kg_boost", "name": "KG"}], ensure_ascii=False),
        encoding="utf-8",
    )
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(
        json.dumps(_rows_many("kg_boost", 21), ensure_ascii=False), encoding="utf-8"
    )
    before = {p.name: p.read_bytes() for p in (registry_file, cards_file, rows_file)}

    out_json = tmp_path / "health.json"
    doc = sh.build_strategy_health(
        rows_file=str(rows_file),
        cards_file=str(cards_file),
        registry_path=str(registry_file),
        out_path=str(out_json),
    )
    after = {p.name: p.read_bytes() for p in (registry_file, cards_file, rows_file)}
    assert before == after  # 输入文件零改动（只读）
    assert doc["readonly"] is True
    assert doc["side_effects"] == []
    assert os.path.exists(out_json)
    assert doc["out_path"] == str(out_json)
    assert "out_path" not in json.loads(out_json.read_text(encoding="utf-8"))


# ── 19. 行里出现 registry/卡都没有的策略：显式登记，不臆造卡片 ─────────
def test_rows_only_unknown_strategy():
    doc = sh.build_strategy_health(
        rows=_rows_many("mystery_strategy", 21),
        cards=[],
        registry={"enabled": [], "weights": {}, "top_n": {}},
    )
    card = doc["strategies"]["mystery_strategy"]
    assert card["discovered_from"] == ["rows"]
    assert card["card"]["present"] is False
    assert card["card"]["display_name"] is None
    assert card["registry"]["known"] is False
    assert card["registry"]["enabled"] is False
    assert card["registry"]["weight"] is None
    assert card["registry"]["weight_group"] is None
    assert card["health_level"] == "green"
