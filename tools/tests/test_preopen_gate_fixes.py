# -*- coding: utf-8 -*-
"""盘前链路 P0/P1 修复的行为测试（2026-11-12 夜）。

日期字面量一律用码点构造：Write/Edit 通道与 stdout 都会串味数字，
直接写 '20261112' 可能查到另一天而不自知。

变异检验指引（每条断言都要能失败，否则是假通过）：
  · test_t_plus_1_lag_degrades_not_blocks：把 audit() 里的
    `elif sp["role"] in ("soft", "t_plus_1")` 改回 `== "soft"`，本例必须 FAIL。
  · test_stale_official_table_is_history_not_today_problem：把
    limit_cross_check() 里 `if not is_target_day:` 分支去掉、改成无条件告警，
    本例必须 FAIL。
  · test_reuse_rejection_blocks_stale_day：把 reuse_rejection() 里第一条
    data_day 判断删掉，本例必须 FAIL。
"""
import importlib.util
import io
import json
import os
import re
import sys

import pandas as pd
import pytest

ROOT = r"C:\项目\老田的AI助手"
PREOPEN = os.path.join(ROOT, "tools", "preopen")
KG = os.path.join(ROOT, "zerobound-kg", "factor_proto")
# 口径版本的下界（只增不减）。升级口径时只改这里和生产代码，不要改下面那些
# 断言里的数字 —— 它们是「当前值相对量」（rp.POLICY_V ± 1），不写死。
MIN_POLICY_V = 4
sys.path.insert(0, PREOPEN)
import audit_preopen as audit  # noqa: E402

D = lambda *cps: "".join(chr(c) for c in cps)
DAY = D(50, 48, 50, 54, 49, 49, 49, 50)      # 20261112
PREV = D(50, 48, 50, 54, 49, 49, 49, 48)     # 20261110
STALE = D(50, 48, 50, 54, 49, 49, 49, 49)    # 20261111


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_preopen():
    return _load("rp_gate_test", os.path.join(PREOPEN, "run_preopen.py"))


def _num_in(path, pattern):
    s = io.open(path, encoding="utf-8").read()
    m = re.search(pattern, s)
    assert m, "未找到口径版本声明：%s" % path
    return int(m.group(1))


# ---------------------------------------------------------------------------
# P0-6 三处口径版本一致
# ---------------------------------------------------------------------------
def test_policy_version_consistent():
    rp = _run_preopen()
    v_pick = _num_in(os.path.join(KG, "_preopen_pick15_v2_adj.py"),
                     r'"chain_policy_v"\s*:\s*(\d+)')
    v_chain = _num_in(os.path.join(KG, "fg_daily_chain.py"),
                      r"REFINE_POLICY_V\s*=\s*(\d+)")
    # 三处一致才是本测试要守的规矩（防「只改一处」）。
    #
    # 不能写死 `== 3`：2026-11-13 pick15 真的升到 v4（新增 ratio>1.20 硬否决），
    # 这条断言就红了 —— 而它红的原因是「数字变了」，不是「不一致」。一条会因为
    # 正确变更而失败的断言，结局只有一种：被人顺手改成新数字，然后失去作用。
    # 所以改成两条不随版本漂移的约束：一致 + 不得倒退。
    assert rp.POLICY_V == v_pick == v_chain
    assert rp.POLICY_V >= MIN_POLICY_V, (
        "口径版本回退了：%d < %d —— 是不是有人回滚了产出方却留着消费方？"
        % (rp.POLICY_V, MIN_POLICY_V))


# ---------------------------------------------------------------------------
# P0-4 复用选票必须证明「属于今天、属于当前口径」
# ---------------------------------------------------------------------------
def test_reuse_rejection_blocks_stale_day():
    rp = _run_preopen()
    # 用 POLICY_V 而不是字面量：本例要测的是「数据日不符」，口径值只是干扰项。
    # 写死 3 时，一旦口径升到 4，这条会因**另一个原因**（口径不符）过了 ——
    # 断言通过但测的不是它声称要测的东西。
    msg = rp.reuse_rejection({"data_day": STALE, "chain_policy_v": rp.POLICY_V}, DAY)
    assert msg is not None and "数据日" in msg


def test_reuse_rejection_blocks_stale_policy():
    rp = _run_preopen()
    msg = rp.reuse_rejection({"data_day": DAY, "chain_policy_v": rp.POLICY_V - 1}, DAY)
    assert msg is not None and "口径" in msg


def test_reuse_rejection_allows_current_ticket():
    rp = _run_preopen()
    assert rp.reuse_rejection({"data_day": DAY, "chain_policy_v": rp.POLICY_V},
                              DAY) is None


# ---------------------------------------------------------------------------
# P0-5 涨停交叉验证：空表 / 历史日 / 当日 三种情形
# ---------------------------------------------------------------------------
def _fu(codes):
    return {"up": len(codes), "dn": 0, "new_listing": 0,
            "up_codes": list(codes), "dn_codes": []}


def _off(codes):
    return pd.DataFrame({"ts_code": list(codes), "limit": ["U"] * len(codes)})


def test_official_empty_is_not_official_zero():
    cc, warns = audit.limit_cross_check(DAY, DAY, 0, _fu(["A", "B"]), _off([]))
    assert cc["official_empty"] is True
    assert cc["official_up"] is None
    assert any("0 行" in w for w in warns)
    assert not any("自算比官方多" in w for w in warns)


def test_stale_official_table_is_history_not_today_problem():
    cc, warns = audit.limit_cross_check(DAY, PREV, 2,
                                        _fu(["A", "B", "C"]), _off(["A"]))
    assert cc["is_target_day"] is False
    assert cc["lag_days"] == 2
    assert cc["mine_only"] == 2
    assert warns == [], "历史日的口径差异不能被当成当日问题"
    assert "历史比对" in cc["note"]


def test_same_day_difference_still_warns():
    cc, warns = audit.limit_cross_check(DAY, DAY, 0,
                                        _fu(["A", "B", "C"]), _off(["A"]))
    assert cc["is_target_day"] is True
    assert any("自算比官方多 2 只" in w for w in warns)


def test_same_day_official_only_warns_about_missed():
    cc, warns = audit.limit_cross_check(DAY, DAY, 0, _fu(["A"]), _off(["A", "B"]))
    assert cc["official_only"] == 1
    assert any("漏判" in w for w in warns)


# ---------------------------------------------------------------------------
# P0-2 0 行分片三因区分
# ---------------------------------------------------------------------------
def test_shard_diagnostics_distinguishes_empty_and_absent(tmp_path, monkeypatch):
    sub = tmp_path / "limit_list_d"
    sub.mkdir()
    pd.DataFrame({"trade_date": pd.Series(dtype="int64")}).to_parquet(
        sub / ("d_%s.parquet" % DAY))
    monkeypatch.setattr(audit, "shards",
                        lambda s: sorted(str(p) for p in sub.glob("*.parquet")))
    diag = audit.shard_diagnostics("x", "trade_date", DAY, [PREV, STALE, DAY])
    assert DAY in diag["empty_days"], "有分片但 0 行 = 上游给空"
    assert PREV in diag["absent_days"] and STALE in diag["absent_days"], \
        "连分片都没有 = 本地漏跑"
    assert DAY not in diag["absent_days"]


def test_shard_diagnostics_does_not_guess_without_dated_shards(monkeypatch):
    monkeypatch.setattr(audit, "shards", lambda s: ["/nowhere/part_0.parquet"])
    assert audit.shard_diagnostics("x", "trade_date", DAY, [PREV, DAY]) == {
        "empty_days": [], "absent_days": []}


# ---------------------------------------------------------------------------
# P0-1 T+1 源落后只能降级，不能阻断
# ---------------------------------------------------------------------------
def test_t_plus_1_lag_degrades_not_blocks(monkeypatch):
    monkeypatch.setattr(audit, "trade_cal", lambda: [PREV, STALE, DAY])
    monkeypatch.setattr(audit, "trading_lag", lambda mx, tgt, cal: 2)
    monkeypatch.setattr(audit, "shard_diagnostics",
                        lambda *a, **k: {"empty_days": [], "absent_days": []})
    monkeypatch.setattr(audit, "load_day", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(audit, "structure_checks", lambda day, cal: ({"rows": 0}, []))
    monkeypatch.setattr(audit, "limit_counts", lambda df: {
        "up": 0, "dn": 0, "new_listing": 0, "up_codes": [], "dn_codes": []})
    monkeypatch.setattr(audit, "SPECS", [{
        "id": "margin", "name": "两融", "role": "t_plus_1", "use": "情绪",
        "path": "tushare/margin", "date_col": "trade_date"}])

    def fake_max_date(sub, col):
        # 官方涨跌停表无数据 → 跳过交叉验证那段 IO
        return (None, 0) if "limit_list_d" in sub else (PREV, 1)

    monkeypatch.setattr(audit, "max_date", fake_max_date)
    res = audit.audit(DAY)
    src = res["sources"][0]
    assert src["state"] == "warn", "T+1 落后 2 日应降级，不是 missed"
    assert not any("应到" in p for p in res["problems"]), \
        "辅助源不得产生阻断项"


# ---------------------------------------------------------------------------
# P0-7 精排日/共识包不再写死，且滞后可见
# ---------------------------------------------------------------------------
def test_pick_defaults_are_not_hardcoded_dates():
    p = os.path.join(KG, "_preopen_pick15_v2_adj.py")
    s = io.open(p, encoding="utf-8").read()
    assert re.search(r"^PKG_DIR\s*=\s*None", s, re.M), "共识包不得写死日期"
    assert re.search(r"^ASOF_LAST\s*=\s*None", s, re.M), "精排日不得写死日期"
    assert "package_lag_days" in s, "池子滞后必须写进产物，不能静默"
    assert '"data_day": ASOF_LAST' in s, "复用校验需要 data_day"


def test_latest_pkg_dir_picks_newest_real_package():
    mod = _load("pick15_gate_test", os.path.join(KG, "_preopen_pick15_v2_adj.py"))
    got = mod.latest_pkg_dir()
    assert got == D(50, 48, 50, 54, 49, 49, 49, 48), \
        "应取最新一个真实含 push_package.json 的目录"


def test_latest_asof_takes_real_max_date(monkeypatch):
    mod = _load("pick15_gate_test2", os.path.join(KG, "_preopen_pick15_v2_adj.py"))
    monkeypatch.setattr(mod.glob, "glob", lambda p: ["/fake/a.parquet", "/fake/b.parquet"])
    seq = [pd.DataFrame({"trade_date": [PREV]}),
           pd.DataFrame({"trade_date": [DAY]})]
    monkeypatch.setattr(mod.pd, "read_parquet", lambda p, **k: seq.pop(0))
    assert mod.latest_asof() == DAY


# ---------------------------------------------------------------------------
# P1 文案口径：改回去就失败
# ---------------------------------------------------------------------------
def test_report_wording_no_longer_misleads():
    p = os.path.join(PREOPEN, "build_preopen_report.py")
    s = io.open(p, encoding="utf-8").read()
    assert "评分由四项加权" not in s, "权重与本票取值必须分开"
    assert "<span>量比 <b>" not in s, "vr 不是分时量比"
    assert "流动性无区分度已剔除" not in s, "权重置 0 不等于剔除"
    assert "82 → 79 → 15" not in s, "注释里不写死示例数字"
    assert "动量名次" in s and "mom_rank" in s, "名次必须与 r10 分开"
    assert "official_empty" in s, "H5 要能处理官方表 0 行，否则 %d 格式化会崩"


def test_v5_precheck_is_abandon():
    p = os.path.join(ROOT, "quant_backtest", "experiments",
                     "P71_v5_backfill", "precheck.yaml")
    s = io.open(p, encoding="utf-8").read()
    assert re.search(r"verdict:\s*abandon", s)
    assert re.search(r"total_trials:\s*7", s), "参数层同族第 7 组"
    assert re.search(r"upper_bound_pct:\s*-0\.30", s)


# ---------------------------------------------------------------------------
# P0-8（11-13 反哺抓到）行业数据断供不能干掉整份报告
# ---------------------------------------------------------------------------
def _report_mod():
    return _load("report_gate_test", os.path.join(PREOPEN, "build_preopen_report.py"))


def test_derive_survives_empty_sector():
    """变异检验：把 derive() 的行业分支改回 `if d.get("sector")`，本例必须 FAIL。"""
    rp = _report_mod()
    d = {
        "breadth": {"up": 1, "dn": 1, "n": 2, "median_pct": 0.0,
                    "amount_yi": 1.0},
        "limit": {"up": 0, "dn": 0},
        "index": [],
        "sector": {"n": 0, "top": [], "bottom": [], "up_n": 0},
        "sector_missing": "行业强弱数据最新只到 X，未接上数据日 Y；本节略过",
        "picks": [],
        "ledger": None,
    }
    out = rp.derive(d)
    assert any("行业" in p for p in out["points"]), "缺数据要如实说，不能静默跳过"


def test_report_marks_sector_missing_instead_of_empty_dict():
    s = io.open(os.path.join(PREOPEN, "build_preopen_report.py"),
                encoding="utf-8").read()
    assert "sector_missing" in s
    assert "if not len(cur)" in s, "collect() 必须识别「文件在但没有目标日」"
    assert 'd["sector"]["top"] and d["sector"]["bottom"]' in s, \
        "render/derive 两层都要挡住空列表"


# ---------------------------------------------------------------------------
# 收盘复盘 → 事件账（11-13 起自动攒观测）
# ---------------------------------------------------------------------------
# 日期字面量按铁律用码点构造，Write/Edit 与 stdout 都会串味数字串。
D = lambda *c: "".join(chr(c) for c in c)
DAY_EXEC = D(50, 48, 50, 54, 49, 49, 49, 51)   # 执行日
DAY_SIG = D(50, 48, 50, 54, 49, 49, 49, 50)    # 信号日（数据日）


def _attach_mod():
    return _load("attach_close_review_test",
                 os.path.join(ROOT, "tools", "review", "attach_close_review.py"))


def _fake_review():
    rows = []
    for i in range(15):
        rows.append({"code": "T%02d.SZ" % i, "name": "票%02d" % i,
                     "score": 1.0 - i / 20.0, "o2c": (1.0 if i % 3 else -1.0) * (i + 1) / 10.0,
                     "c2c": -0.5 + i / 30.0, "miss": False})
    return {"exec_day": DAY_EXEC, "data_day": DAY_SIG, "rows": rows,
            "top_o2c": -0.8, "pool_o2c": -1.4, "quote_source": "unit-test",
            "index": {"上证指数": -0.7, "深证成指": -1.6}}


def test_attach_signal_day_is_data_day(monkeypatch):
    """变异检验：signal_day 改取 exec_day，本例必须 FAIL。"""
    m = _attach_mod()
    monkeypatch.setattr(m, "market_breadth", lambda d: (None, "unit-test"))
    monkeypatch.setattr(m, "pool_ic", lambda rev, day: (None, "unit-test"))
    f = m.build_finding(_fake_review(), "var/audit/unit.json")
    assert f["signal_day"] == DAY_SIG, "必须挂到信号日，不能另开一天流程账"
    assert f["exec_day"] == DAY_EXEC
    assert f["signal_day"] != f["exec_day"]


def test_attach_single_independent_observation(monkeypatch):
    """变异检验：把 independent_observations 写成 3，本例必须 FAIL。"""
    m = _attach_mod()
    monkeypatch.setattr(m, "market_breadth", lambda d: (None, "unit-test"))
    monkeypatch.setattr(m, "pool_ic", lambda rev, day: (None, "unit-test"))
    f = m.build_finding(_fake_review(), "var/audit/unit.json")
    assert f["independent_observations"] == 1
    assert f["sample_kind"] == "single_day"
    assert f["severity"] == "low"


def test_spearman_is_rank_not_raw():
    """变异检验：把 spearman 里的 _ranks 去掉，本例必须 FAIL。"""
    m = _attach_mod()
    x, y = [1, 2, 3, 4, 5], [1, 4, 9, 16, 25]
    assert m.spearman(x, y) == pytest.approx(1.0), "秩相关只看顺序，不看数值形状"
    assert m._pearson(x, y) != pytest.approx(1.0), "原始值 Pearson 必然不同"


def test_attach_no_prior_day_hardcoded_conclusion(monkeypatch):
    m = _attach_mod()
    monkeypatch.setattr(m, "market_breadth", lambda d: (None, "unit-test"))
    monkeypatch.setattr(m, "pool_ic", lambda rev, day: (None, "unit-test"))
    f = m.build_finding(_fake_review(), "var/audit/unit.json")
    txt = f["evidence"] + f["so_what"]
    assert "前 5 名胜率 0%" not in txt and "60%" not in txt
    assert "0.25 个百分点" not in txt, "11-12 那天的数字不能贴到新一天"
    assert f["regime_basis"] == "index_equal_weight_fallback"
    assert "不可直接比较" in f["evidence"], "口径降级必须留痕"
    assert "全市场广度未就绪" in f["evidence"]


def test_attach_idempotent_under_event_log(monkeypatch, tmp_path):
    """同一天重复跑复盘不能凑出多条独立观测。"""
    m = _attach_mod()
    monkeypatch.setattr(m, "market_breadth", lambda d: (None, "unit-test"))
    monkeypatch.setattr(m, "pool_ic", lambda rev, day: (None, "unit-test"))
    f = m.build_finding(_fake_review(), "var/audit/unit.json")
    import event_log
    monkeypatch.setattr(event_log, "_p", lambda day: str(tmp_path / (day + ".json")))
    monkeypatch.setattr(event_log, "_rebuild_index", lambda: None)
    io.open(str(tmp_path / (DAY_SIG + ".json")), "w", encoding="utf-8").write(
        json.dumps({"date": DAY_SIG, "data_day": DAY_SIG, "drift": []}))
    ev1, added1 = event_log.attach_drift(DAY_SIG, f)
    ev2, added2 = event_log.attach_drift(DAY_SIG, f)
    assert added1 is True and added2 is False
    assert len(ev2["drift"]) == len(ev1["drift"]) == 1


def test_fallback_id_differs(monkeypatch):
    """变异检验：降级时把 id 改回主 pattern，本例必须 FAIL。"""
    m = _attach_mod()
    monkeypatch.setattr(m, "market_breadth", lambda d: (None, "unit-test"))
    monkeypatch.setattr(m, "pool_ic", lambda rev, day: (None, "unit-test"))
    f = m.build_finding(_fake_review(), "var/audit/unit.json")
    assert f["id"] == m.PATTERN_TOP15_FALLBACK
    assert f["id"] != m.PATTERN
    assert f["comparable_with_prior"] is False
    assert f["rho_scope"] == "top15_only_fallback"
    assert "不并入同类观测计数" in f["evidence"]


def test_full_pool_scope_is_marked_comparable(monkeypatch):
    m = _attach_mod()
    fake_ic = {"n": 82, "rho_b": -0.08, "rho_a": -0.14,
               "quotes": "q.json", "pick": "pick.json"}
    monkeypatch.setattr(m, "market_breadth", lambda d: (None, "unit-test"))
    monkeypatch.setattr(m, "pool_ic", lambda rev, day: (fake_ic, None))
    f = m.build_finding(_fake_review(), "var/audit/unit.json")
    assert f["id"] == m.PATTERN
    assert f["rho_scope"] == "consensus_pool_v1"
    assert f["comparable_with_prior"] is True
    assert f["n_pool"] == 82
    assert f["rho_open_to_close"] == -0.08


# 已知结果反证：IC 必须能复现台账里的旧值，否则说明口径漂了。
# 数值基准同样用码点构造，避免 Write/Edit 通道的数字串味。
BENCH_B = -float(D(48, 46, 48, 56, 51))
BENCH_A = -float(D(48, 46, 49, 52, 53))
TOL = float(D(48, 46, 48, 49))


def test_pool_ic_reproduces_prior_day_benchmark():
    m = _attach_mod()
    prev = D(50, 48, 50, 54, 49, 49, 49, 49)
    rev = {"exec_day": DAY_SIG, "data_day": prev,
           "evidence": {"path": "var/preopen/close_review/quotes/q_"
                                + DAY_SIG + ".json"}}
    ic, why = m.pool_ic(rev, DAY_SIG)
    assert why is None, "全池口径必须可复算：%s" % why
    assert ic["n"] == 82
    assert abs(ic["rho_b"] - BENCH_B) < TOL, "开盘→收盘 rho 与台账不一致"
    assert abs(ic["rho_a"] - BENCH_A) < TOL, "昨收→今收 rho 与台账不一致"
