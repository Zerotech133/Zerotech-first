# -*- coding: utf-8 -*-
"""li122 每日影子采集接入面测试（li122-shadow 工位）。

覆盖：
① Signal 垫片三态映射：buy → action="buy" + score=confidence*100；
   拦截 hold → action="hold" + score=0.0（collect_once 事件帧过滤自动
   skip 计数）；fail-closed 中性 → 同 hold 收口；
② 采集链路：Li122ShadowAdapter（注入合成 agent + 合成 fetcher）→
   collect_li122_shadow → collect_once 幂等落档（pytest tmp_path 唯一
   写点），recorded/skipped 计数与影子行字段逐项校验；
③ as_of 截断语义：默认=前一交易日收盘（"T 15:00" 编码，日线 T 日 bar
   与 T 日会话桶全部入选、T+1 与未收盘当日全排）；禁止 as_of=当日/
   未收盘交易日 fail-visible；日线 dt 当日分钟桶全排基线口径（构造器
   喂足全序列，截断语义由 Agent/_cut_bars 保真）；
④ 周期选择按 ruleset：档 A 请求 30m/5m/1m、档 B 请求 1d/30m/5m
   （档 B 零 1m 请求，不白拉）；
⑤ 零写副作用：context/bars 输入零变更、采集写点仅限 out_path 覆写、
   仓库影子档目录零触碰 + 源码红线静态断言 + 导入期隔离。

chanlun 依赖用例沿用 test_li122_agent 的 chanlun_ready fixture 模式
（缺 config.json fail-visible SKIP，绝不静默）；其余用例全部合成数据
零 DB 依赖。
"""
import importlib.util
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_QB_ROOT = os.path.dirname(os.path.dirname(_HERE))          # quant_backtest/
_AGENTS_DIR = os.path.join(_QB_ROOT, "strategy_layer", "onboarding", "agents")

if _QB_ROOT not in sys.path:
    sys.path.insert(0, _QB_ROOT)

SYMBOL = "TEST.SH"
TD8 = "20261016"                       # T 日（相对 T+1 盘前采集而言的"前一交易日"）
AS_OF = "2026-10-16 15:00"             # T 日收盘编码（dt 字典序口径）


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 被测接入面（先加载 li122_agent：其单例化 agent_platform 契约模块）
li122_agent = _load_module(
    "li122_agent_for_shadow_test",
    os.path.join(_AGENTS_DIR, "li122_agent.py"))
li122_shadow = _load_module(
    "li122_shadow_under_test",
    os.path.join(_AGENTS_DIR, "li122_shadow.py"))
# agent_platform 契约（真 Verdict 类，isinstance 身份一致）
_ap_registry = li122_agent.ap_registry_module()
Verdict = _ap_registry.Verdict

# 档 B 变体（test_li122_agent 同款类属性参数化子类）
Li122B = type("Li122B", (li122_agent.Li122Agent,), {"ruleset_key": "B_D_30F_5F"})

from strategy_layer.onboarding import shadow_collect as sc  # noqa: E402
from strategy_layer.signal import Signal                    # noqa: E402

# chanlun 本体可用性闸门（test_li122_agent 同款模式）
try:
    from chanlun_li122 import RULESETS as _RULESETS
    _CHANLUN_ERR = None
except Exception as _exc:  # pragma: no cover - 仅缺 config.json 环境触发
    _CHANLUN_ERR = _exc


@pytest.fixture
def chanlun_ready():
    """chanlun 依赖可用性闸门：缺失时 fail-visible 跳过（原因写明根因）。"""
    if _CHANLUN_ERR is not None:
        pytest.skip("chanlun config.json 缺失（chanlun_data.py 导入期读取 "
                    "quant_backtest/config.json），chanlun 本体链不可用，"
                    "相关用例跳过——导入异常：%r" % (_CHANLUN_ERR,))


# ---------------------------------------------------------------------- #
# 合成数据（dt 全为字符串，逐日标签以 15:00 收尾——与真实会话桶标
# 结束时刻口径一致，字典序语义等价）
# ---------------------------------------------------------------------- #
def _minute_labels(step_min, n_per_day):
    """单交易日 n_per_day 根桶标签（15:00 收尾、向前按 step 等距）。"""
    out = []
    for i in range(n_per_day):
        m = 15 * 60 - step_min * (n_per_day - 1 - i)
        out.append("%02d:%02d" % (m // 60, m % 60))
    return out


D30M = _minute_labels(30, 8)
D5M = _minute_labels(5, 48)
D1M = _minute_labels(1, 240)
DAYS = ["2026-10-14", "2026-10-15", "2026-10-16"]


def _make_bars(days=DAYS, labels=None, amp=1.0):
    labels = labels or D30M
    return [{"dt": "%s %s" % (d, lab), "o": 100.0, "h": 100.0 + amp,
             "l": 100.0 - amp, "c": 100.0, "v": 1.0}
            for d in days for lab in labels]


def _daily_bars(days=DAYS):
    return [{"dt": d, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0, "v": 1.0}
            for d in days]


def _fake_fetcher(by_period, requests):
    """注入式 fetcher：按周期返回合成 bars 并记录请求（周期选择断言用）。"""

    def fetch(code, period, as_of):
        requests.append((code, period, as_of))
        return list(by_period.get(period, [])), "fake"
    return fetch


CALENDAR = ["20261009", "20261012", "20261013", "20261014",
            "20261015", "20261016", "20261019"]  # 末位为未来交易日（当日拒绝判例）
TIER_A_BARS = {"30m": _make_bars(), "5m": _make_bars(labels=D5M),
               "1m": _make_bars(labels=D1M)}
TIER_B_BARS = {"1d": _daily_bars(), "30m": _make_bars(),
               "5m": _make_bars(labels=D5M)}


# ---------------------------------------------------------------------- #
# 合成 agent（propose 返回预置 Verdict，不经 chanlun 链）
# ---------------------------------------------------------------------- #
def _verdict(rec, conf, **extra):
    return Verdict(strategy_id="chanlun_li122", symbol=SYMBOL,
                   recommendation=rec, confidence=conf, stance="bull",
                   score=70.0 if rec == "buy" else 0.0,
                   rationale="合成 Verdict（垫片映射测试）", extras=extra)


def _fake_agent_cls(verdict):
    """类属性参数化 + 无参构造范式（strategy_discovery 兼容）。"""

    class _FakeLi122(li122_agent.Li122Agent):
        strategy_id = "chanlun_li122"
        ruleset_key = "A_30F_5F_1F"
        _verdict = verdict

        def propose(self, context):
            # 可观测性：把 agent 实际收到的 as_of 记回 extras（dict 可变）
            self._verdict.extras["seen_as_of"] = context.get("as_of")
            return self._verdict
    return _FakeLi122


class _AgentHolder:
    """供 collect_li122_shadow 使用的 agent_cls（每次调用新实例）。"""
    strategy_id = "chanlun_li122"
    ruleset_key = "A_30F_5F_1F"

    def __init__(self, verdict):
        self._cls = _fake_agent_cls(verdict)

    def __call__(self):
        return self._cls()


# ---------------------------------------------------------------------- #
# ① Signal 垫片三态映射
# ---------------------------------------------------------------------- #
def test_shim_buy_maps_score_from_confidence():
    sig = li122_shadow.verdict_to_signal(_verdict("buy", 0.7), TD8)
    assert isinstance(sig, Signal)
    assert sig.action == "buy" and sig.score == 70.0
    assert sig.confidence == 0.7 and sig.symbol == SYMBOL
    assert sig.strategy == "chanlun_li122" and sig.trade_date == TD8
    assert sig.rationale


def test_shim_hold_intercept_maps_non_event(tmp_path):
    """拦截 hold → hold/score=0：collect_once 事件帧过滤自动 skip 计数。"""
    sig = li122_shadow.verdict_to_signal(
        _verdict("hold", 0.0, no="upper_type"), TD8)
    assert sig.action == "hold" and sig.score == 0.0
    assert sig.confidence == 0.0
    # 收口验证：collect_once 对 hold 只计数不落档
    out = str(tmp_path / "hold.jsonl")

    class _AdHoc:
        strategy_id = "chanlun_li122"

        def score(self, trade_date):
            return [sig]
    res = sc.collect_once(_AdHoc(), TD8, out_path=out)
    assert res["recorded"] == 0 and res["skipped_non_event"] == 1
    assert sc.load_rows(out) == []


def test_shim_fail_closed_maps_neutral():
    """fail-closed 中性 Verdict → hold 收口（不产信号，仅计数）。"""
    sig = li122_shadow.verdict_to_signal(
        _verdict("hold", 0.0, fail_closed=True, reason="中级窗口不足"), TD8)
    assert sig.action == "hold" and sig.score == 0.0
    assert sig.confidence == 0.0


# ---------------------------------------------------------------------- #
# ② 采集链路（注入合成 agent + 合成 fetcher，tmp_path 落档）
# ---------------------------------------------------------------------- #
def test_collect_chain_buy_recorded_with_score(tmp_path):
    """buy Verdict → 落档 buy 行，score=confidence*100；二次采集幂等。"""
    out = str(tmp_path / "shadow.jsonl")
    v = _verdict("buy", 0.7)
    holder = _AgentHolder(v)
    requests = []
    fetcher = _fake_fetcher(TIER_A_BARS, requests)
    res = li122_shadow.collect_li122_shadow(
        symbols=[SYMBOL], trade_date=TD8, agent_cls=holder,
        fetcher=fetcher, calendar_days=CALENDAR, out_path=out)
    assert res["recorded"] == 1 and res["skipped_non_event"] == 0
    rows = sc.load_rows(out)
    assert len(rows) == 1
    row = rows[0]
    assert row["strategy_id"] == "chanlun_li122" and row["symbol"] == SYMBOL
    assert row["action"] == "buy" and row["score"] == 70.0
    assert row["confidence"] == 0.7 and row["trade_date"] == TD8
    assert row["mode"] == "SHADOW"
    # as_of 语义：agent 收到的 context as_of = T 日收盘编码
    assert v.extras["seen_as_of"] == AS_OF
    # 幂等：同日重采全部 replayed，不追加新行
    res2 = li122_shadow.collect_li122_shadow(
        symbols=[SYMBOL], trade_date=TD8, agent_cls=holder,
        fetcher=fetcher, calendar_days=CALENDAR, out_path=out)
    assert res2["recorded"] == 0 and res2["replayed"] == 1
    with open(out, "r", encoding="utf-8") as f:
        assert sum(1 for line in f if line.strip()) == 1


def test_collect_chain_hold_skipped_but_counted(tmp_path):
    """拦截 hold → 不落档仅 skipped 计数（事件帧口径收口验证）。"""
    out = str(tmp_path / "shadow.jsonl")
    v = _verdict("hold", 0.0, fail_closed=True, reason="中级窗口不足")
    fetcher = _fake_fetcher(TIER_A_BARS, [])
    res = li122_shadow.collect_li122_shadow(
        symbols=[SYMBOL], trade_date=TD8, agent_cls=_AgentHolder(v),
        fetcher=fetcher, calendar_days=CALENDAR, out_path=out)
    assert res["recorded"] == 0 and res["skipped_non_event"] == 1
    assert sc.load_rows(out) == []


def test_collect_strategy_id_mismatch_fail_visible():
    with pytest.raises(ValueError, match="不一致"):
        li122_shadow.collect_li122_shadow(
            strategy="other", symbols=[SYMBOL], trade_date=TD8,
            agent_cls=_AgentHolder(_verdict("hold", 0.0)),
            calendar_days=CALENDAR)


def test_collect_requires_trade_date():
    with pytest.raises(ValueError, match="trade_date"):
        li122_shadow.collect_li122_shadow(
            symbols=[SYMBOL], trade_date=None,
            agent_cls=_AgentHolder(_verdict("hold", 0.0)),
            calendar_days=CALENDAR)


# ---------------------------------------------------------------------- #
# ③ as_of 截断语义（前一交易日收盘，dt 字典序）
# ---------------------------------------------------------------------- #
def test_resolve_as_of_default_prev_close():
    """默认=最近已收盘交易日收盘编码 "T 15:00"。"""
    as_of = li122_shadow.resolve_as_of(None, CALENDAR)
    assert as_of == "2026-10-16 15:00"


def test_resolve_as_of_rejects_same_day_and_holiday():
    """禁止 as_of=当日/未收盘；非交易日 fail-visible。"""
    with pytest.raises(ValueError, match="当日"):
        li122_shadow.resolve_as_of("20261019", CALENDAR)  # 未来交易日（≥当日）
    with pytest.raises(ValueError, match="非交易日"):
        li122_shadow.resolve_as_of("20261010", CALENDAR)  # 不在日历
    assert li122_shadow.resolve_as_of("20261015", CALENDAR) == "2026-10-15 15:00"
    # 短横线日期形态等价
    assert li122_shadow.resolve_as_of("2026-10-15", CALENDAR) == "2026-10-15 15:00"


def test_build_context_truncates_to_as_of():
    """全序列截断：as_of 之后与当日盘中未完成桶全排（dt 字典序）。"""
    bars30 = _make_bars(days=DAYS + ["2026-10-19"])
    bars5 = _make_bars(days=DAYS + ["2026-10-19"], labels=D5M)
    bars1 = _make_bars(days=DAYS + ["2026-10-19"], labels=D1M)
    bars1.append({"dt": "2026-10-16 15:01", "o": 100.0, "h": 101.0,
                  "l": 99.0, "c": 100.0, "v": 1.0})  # 收盘后杂桶
    fetcher = _fake_fetcher({"30m": bars30, "5m": bars5, "1m": bars1}, [])
    ctx, meta = li122_shadow.build_li122_context(
        SYMBOL, as_of=TD8, fetcher=fetcher, calendar_days=CALENDAR)
    assert ctx["as_of"] == AS_OF
    for period, bars in ctx["bars"].items():
        assert all(b["dt"] <= AS_OF for b in bars), period
        assert all(b["dt"] < "2026-10-19" for b in bars), period
    assert not any(b["dt"] == "2026-10-16 15:01" for b in ctx["bars"]["1m"])
    # T 日最后一个会话桶入选（全序列截至 T 收盘，而非 T-1）
    assert max(b["dt"] for b in ctx["bars"]["30m"]) == "2026-10-16 15:00"
    assert meta["ruleset"] == "A_30F_5F_1F"


def test_build_context_includes_daily_bar_of_trade_date():
    """as_of="T 15:00" 字典序下日线 T 日 bar 入选（收盘全序列喂足）。"""
    daily = _daily_bars(days=DAYS + ["2026-10-19"])
    fetcher = _fake_fetcher({"1d": daily, "30m": _make_bars(),
                             "5m": _make_bars(labels=D5M)}, [])
    ctx, meta = li122_shadow.build_li122_context(
        SYMBOL, as_of=TD8, agent_cls=Li122B, fetcher=fetcher,
        calendar_days=CALENDAR)
    assert ctx["bars"]["1d"][-1]["dt"] == "2026-10-16"
    assert all(b["dt"] <= AS_OF for b in ctx["bars"]["1d"])
    assert meta["ruleset"] == "B_D_30F_5F"


def test_daily_upper_dt_excludes_same_day_minutes_baseline(chanlun_ready):
    """日线 dt 当日分钟桶全排（回测基线口径逐字保留，不得"聪明地"修正）：
    构造器负责把 T 收盘前全序列喂足（日线 T bar 与 T 日 30m 桶都在
    context 内），Agent 拿日线信号 dt="T" 自行按字典序把 T 当日分钟
    桶全排（复用 li122_agent._cut_bars 验证截断语义）。"""
    fetcher = _fake_fetcher(TIER_B_BARS, [])
    ctx, _ = li122_shadow.build_li122_context(
        SYMBOL, as_of=TD8, agent_cls=Li122B, fetcher=fetcher,
        calendar_days=CALENDAR)
    # 构造器侧：T 日线 bar 与 T 日 30m 桶全部在序列内（喂足全序列）
    assert any(b["dt"] == "2026-10-16" for b in ctx["bars"]["1d"])
    assert any(b["dt"].startswith("2026-10-16") for b in ctx["bars"]["30m"])
    # Agent/本体侧：日线信号 dt="T" → T 当日分钟桶全排（前交易日桶保留，
    # 实际评估截至 T-1 收盘）
    cut = li122_agent._cut_bars(ctx["bars"]["30m"], "2026-10-16")
    assert not any(b["dt"].startswith("2026-10-16") for b in cut)
    assert cut[-1]["dt"] == "2026-10-15 15:00"


def test_1m_tail_slice_keeps_three_trade_days():
    """1m 预算纪律：构造器层尾部切片恰 3 交易日 + 字节数入 meta。"""
    many_days = (["2026-09-%02d" % d for d in range(7, 21)]   # 14 个历史自然日
                 + DAYS)                                      # 尾部 3 交易日
    bars1 = _make_bars(days=many_days, labels=D1M)
    fetcher = _fake_fetcher({"30m": _make_bars(),
                             "5m": _make_bars(labels=D5M),
                             "1m": bars1}, [])
    ctx, meta = li122_shadow.build_li122_context(
        SYMBOL, as_of=TD8, fetcher=fetcher, calendar_days=CALENDAR)
    got_days = sorted({b["dt"][:10] for b in ctx["bars"]["1m"]})
    assert got_days == ["2026-10-14", "2026-10-15", "2026-10-16"]
    assert meta["bars_meta"]["1m"]["n_raw"] > meta["bars_meta"]["1m"]["n"]
    assert meta["bars_meta"]["1m"]["bytes"] > 0
    assert meta["bars_meta"]["1m"]["over_budget"] is False


# ---------------------------------------------------------------------- #
# ④ 周期选择按 ruleset（档 B 零 1m 请求）
# ---------------------------------------------------------------------- #
def test_periods_by_ruleset_tier_a():
    requests = []
    fetcher = _fake_fetcher(TIER_A_BARS, requests)
    li122_shadow.build_li122_context(SYMBOL, as_of=TD8, fetcher=fetcher,
                                     calendar_days=CALENDAR)
    periods = [p for _, p, _ in requests]
    assert periods == ["30m", "5m", "1m"]


def test_periods_by_ruleset_tier_b_no_1m():
    requests = []
    fetcher = _fake_fetcher(TIER_B_BARS, requests)
    li122_shadow.build_li122_context(SYMBOL, as_of=TD8,
                                     agent_cls=Li122B,
                                     fetcher=fetcher, calendar_days=CALENDAR)
    periods = [p for _, p, _ in requests]
    assert periods == ["1d", "30m", "5m"]
    assert "1m" not in periods                               # 不白拉 1m


# ---------------------------------------------------------------------- #
# ⑤ 零写副作用 + 源码红线 + 导入期隔离
# ---------------------------------------------------------------------- #
def test_build_context_no_mutation_of_fetched_bars():
    """fetcher 返回的原始 bars 零原地修改（截断/切片走新列表）。"""
    bars30 = _make_bars(days=DAYS + ["2026-10-19"])
    snapshot = json.dumps(bars30, sort_keys=True, ensure_ascii=False)
    fetcher = _fake_fetcher({"30m": bars30, "5m": _make_bars(labels=D5M),
                             "1m": _make_bars(labels=D1M)}, [])
    li122_shadow.build_li122_context(SYMBOL, as_of=TD8, fetcher=fetcher,
                                     calendar_days=CALENDAR)
    assert json.dumps(bars30, sort_keys=True, ensure_ascii=False) == snapshot


def test_collect_writes_only_declared_out_path(tmp_path):
    """采集唯一写点=覆写 out_path（tmp_path）；仓库影子档目录零触碰。"""
    out = str(tmp_path / "shadow.jsonl")
    li122_shadow.collect_li122_shadow(
        symbols=[SYMBOL], trade_date=TD8,
        agent_cls=_AgentHolder(_verdict("hold", 0.0)),
        fetcher=_fake_fetcher(TIER_A_BARS, []),
        calendar_days=CALENDAR, out_path=out)
    assert os.path.exists(out)
    # 仓库内真实影子档未被触碰（chanlun_li122 未真实采集过）
    assert not os.path.exists(sc.shadow_path("chanlun_li122"))
    repo_dir = os.path.join(_QB_ROOT, "data", "shadow_onboarding")
    if os.path.isdir(repo_dir):
        assert all("test" not in n.lower() for n in os.listdir(repo_dir))


def test_adapter_degrades_without_raise():
    """fetcher 抛错 → adapter 降级返回 []（不 raise 到 collect_once）。"""

    def bad_fetcher(code, period, as_of):
        raise RuntimeError("db 不可用")
    adapter = li122_shadow.Li122ShadowAdapter(
        [SYMBOL], agent_cls=li122_agent.Li122Agent, fetcher=bad_fetcher,
        calendar_days=CALENDAR)
    assert adapter.score(TD8) == []


def test_module_source_free_of_execution_chain():
    """模块源码不引用执行链/账本/下单（红线静态断言）。"""
    src = open(os.path.join(_AGENTS_DIR, "li122_shadow.py"),
               encoding="utf-8").read()
    for forbidden in ("execution_engine", "place_order", "cancel_order",
                      "order_book", "ledger_write", "write_ledger",
                      "account", "broker", "trade_api"):
        assert forbidden not in src, "源码含禁用标识: %s" % forbidden


def test_module_import_does_not_pull_chanlun_or_datamart():
    """导入期零 chanlun/datamart 依赖（config.json 隐藏 IO 与 DB 配置
    全部隔离到调用期，对齐 li122_agent 导入期隔离范式）。"""
    before = set(sys.modules)
    fresh = _load_module(
        "li122_shadow_fresh_import_check",
        os.path.join(_AGENTS_DIR, "li122_shadow.py"))
    assert hasattr(fresh, "build_li122_context")
    new = set(sys.modules) - before
    pulled = [k for k in new
              if k.startswith("chanlun") or k == "datamart"
              or k.startswith("strategy_layer")]
    assert not pulled, "模块导入期拉起了重依赖: %s" % pulled


# ---------------------------------------------------------------------- #
# ⑥ chanlun 依赖用例：真实 Li122Agent 全链（缺 config.json 时 SKIP）
# ---------------------------------------------------------------------- #
def test_full_chain_with_real_agent_self_sufficient(chanlun_ready, tmp_path):
    """真实 Li122Agent 自足模式全链：构造器合成数据 → propose → 垫片 →
    collect_once。合成数据无真实买向结构（30m 仅 24 根 < 预热 100）→
    fail-closed 中性 hold，不落档（recorded=0），链路不 raise。"""
    out = str(tmp_path / "shadow.jsonl")
    fetcher = _fake_fetcher(TIER_A_BARS, [])
    res = li122_shadow.collect_li122_shadow(
        symbols=[SYMBOL], trade_date=TD8, fetcher=fetcher,
        calendar_days=CALENDAR, out_path=out)
    assert res["recorded"] == 0
    ctx_meta = res["contexts"][0]
    assert ctx_meta["as_of"] == AS_OF
    assert ctx_meta["verdict"]["recommendation"] in ("buy", "hold")
    assert ctx_meta["verdict"]["confidence"] == 0.0
    assert ctx_meta["signal"]["action"] == "hold"
    rows = sc.load_rows(out)
    assert all(r["action"] in sc.EVENT_ACTIONS for r in rows)  # 事件帧口径
