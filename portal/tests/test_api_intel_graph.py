"""K-MDC-E2 tests: dossier endpoint — honest empty, candidate-only, traceable."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "quant_backtest"))
from api_intel_graph import _repo_root, _seat_stock_code, build_view, dossier  # noqa: E402
from intel_seat_shadow import load_seat_rows, seat_pairs  # noqa: E402


def test_dossier_returns_candidate_only_with_traceable_keys():
    g = build_view(limit=60)
    stats = g.stats()
    assert stats["edges"] > 0 and stats["stocks"] > 0
    some_stock = next(iter(g._by_stock))  # noqa: SLF001 — 测试取任一有边票码
    d = g.dossier(some_stock)
    assert d["verdict"] == "candidate_only" and d["total_edges"] >= 1
    for e in d["edges"]:
        assert e["evidence_key"] and e["question"]


def test_seat_stock_code_parsing_covers_all_candidate_shapes():
    """E2FIX: 三类席位候选的股票码都必须取到——basis 形态各异, 只认 evidence_key."""
    confirmed = seat_pairs(pd.DataFrame([
        {"ts_code": "600001.SH", "trade_date": "20261021", "exalter": "A部", "side": "0"},
        {"ts_code": "600001.SH", "trade_date": "20261021", "exalter": "B部", "side": "1"},
    ]))[0]
    assert confirmed.basis.startswith("lhb_same_day_same_stock:")  # 前缀不匹配 basis 路线
    assert _seat_stock_code(confirmed) == "600001.SH"
    group = seat_pairs(pd.DataFrame([
        {"ts_code": "600009.SH", "trade_date": "20261021", "exalter": f"营业部{i}", "side": "0"}
        for i in range(12)
    ]))[0]
    assert group.basis.endswith(":12")  # 群体边 basis 尾段是席位数, 不是代码
    assert _seat_stock_code(group) == "600009.SH"
    s2 = seat_pairs(pd.DataFrame([
        {"ts_code": "600001.SH", "trade_date": "20261021",
         "exalter": "甲证券北京第1营业部", "side": "0"},
        {"ts_code": "600001.SH", "trade_date": "20261021",
         "exalter": "甲证券北京第2营业部", "side": "1"},
    ]))[0]
    assert s2.basis.startswith("seat_S2_candidate:")
    assert _seat_stock_code(s2) == "600001.SH"
    assert _seat_stock_code(None) is None


def test_seat_edges_queryable_by_stock_real_data():
    """E2FIX 集成断言: 席位共现边必须按票可查.

    历史缺口: 旧测试只取 ``next(iter(g._by_stock))``——该票码来自 holdings,
    seat 边从未按票登记也无断言, 端点对有席位数据的票恒返回空案卷.
    """
    seats = load_seat_rows(_repo_root(), limit_parts=2)
    per_stock = seats.groupby(["trade_date", "ts_code"])["exalter"].nunique()
    hot = per_stock[per_stock >= 2]
    assert len(hot) > 0, "末 2 分区应存在同日同票多席位数据"
    code = str(hot.index[0][1])
    d = build_view().dossier(code)
    assert d["total_edges"] > 0, f"{code} 有席位数据但案卷 0 边——席位边未按票登记"
    assert any("seat:tdx:" in e["pair"] for e in d["edges"]), \
        f"{code} 案卷应含席位共现边(seat:tdx: 键)"


def test_dossier_empty_is_honest_for_unknown_code():
    d = dossier("999999.XX")
    assert d["total_edges"] == 0 and d["edges"] == [] and not d["truncated"]


def test_bad_input_rejected_softly():
    assert dossier("")["error"]
    assert dossier(None)["error"]


def test_all_edges_carry_question_not_fact_words():
    g = build_view(limit=50)
    for idx_list in list(g._by_stock.values())[:5]:  # noqa: SLF001
        for i in idx_list:
            e = g._edges[i]  # noqa: SLF001
            # 红线本义: 不宣称事实 (显式禁词表, 全边适用)
            assert e.question and e.question.strip()
            for banned in ("坐实", "确认是", "即为", "事实证明"):
                assert banned not in e.question
            # 调查问句模板只适用于真候选; 截断告示(top_inst_trunc)是可见性
            # 通知而非问句——E2FIX 后席位边按票可查, 该告示随之进入断言面,
            # 旧代理("是否/须")对它误报, 按语义豁免而非放软红线.
            if not e.basis.startswith("top_inst_trunc:"):
                assert "是否" in e.question or "须" in e.question
