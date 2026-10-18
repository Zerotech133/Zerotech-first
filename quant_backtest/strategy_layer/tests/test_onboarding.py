# -*- coding: utf-8 -*-
"""策略收编四件套测试（RC-1 模板工位 pipeline-fw）。

覆盖：
① manifest 模板真调 agent_platform 既有校验器（load_config / validate_for_run）
② classic_adapter 四策略合成数据：形状/索引正确、与 chan_monitor 输出同构
③ replay_reconcile 对账逻辑：100% 一致 → 退出码 0；95% 不一致 → 退出码 1
④ 零主链断言：import onboarding 全部模块后执行链未被拉起（子进程隔离）
"""
import dataclasses
import importlib.util
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_QB_ROOT = os.path.dirname(os.path.dirname(_HERE))          # quant_backtest/
_REPO_ROOT = os.path.dirname(_QB_ROOT)                      # 仓库根
_ONBOARDING = os.path.join(_QB_ROOT, "strategy_layer", "onboarding")
TEMPLATE_PATH = os.path.join(_ONBOARDING, "manifest_template.json")

CLASSIC_IDS = ["ma_cross", "rsi_reversal", "boll_breakout", "trend_follow"]

# agent_platform/engine/strategy_config.py 按路径加载（不污染 sys.path、
# 不触发 agent_platform 包 __init__）。
_AP_CFG_PATH = os.path.join(_REPO_ROOT, "agent_platform", "engine",
                            "strategy_config.py")
_spec = importlib.util.spec_from_file_location("ap_strategy_config",
                                               _AP_CFG_PATH)
ap_cfg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ap_cfg)


# ---------------------------------------------------------------------- #
# 合成数据与注入 loader
# ---------------------------------------------------------------------- #
def _synth_df(n=120):
    """合成日线：正弦震荡触发四策略的开/平仓事件（均线交叉/RSI 极值/
    布林突破/趋势斜率翻转全覆盖）。"""
    idx = pd.date_range("2026-04-01", periods=n, freq="B")
    close = pd.Series(100.0 + 30.0 * np.sin(np.arange(n) / 5.0), index=idx)
    df = pd.DataFrame({"open": close, "high": close, "low": close,
                       "close": close, "volume": 1000.0}, index=idx)
    return df


@pytest.fixture()
def synth_env():
    df = _synth_df()
    loader = lambda market, symbols, start, end: {s: df for s in symbols}  # noqa: E731
    return df, loader


def _load_onboarding(name):
    """按路径加载 onboarding 模块（与被测目录解耦，不依赖运行 cwd）。"""
    path = os.path.join(_ONBOARDING, name + ".py")
    spec = importlib.util.spec_from_file_location(
        "strategy_layer.onboarding." + name, path)
    mod = importlib.util.module_from_spec(spec)
    # 覆盖注册（先执行后覆盖亦可，但需在 exec 前占位以防环）：若此处用
    # setdefault，当先前用例已按包路径 import 同名模块时，本副本不进
    # sys.modules，形成 split-brain——monkeypatch 打在影子副本上，而
    # 模块内部 `from strategy_layer.onboarding.classic_adapter import ...`
    # 拿到的是 sys.modules 里的另一副本（实测令 replay_reconcile.main
    # 的 _default_loader patch 失效、连真 DB 致 56 处假不一致）。
    sys.modules["strategy_layer.onboarding." + name] = mod
    spec.loader.exec_module(mod)
    return mod


classic_adapter = _load_onboarding("classic_adapter")
replay_reconcile = _load_onboarding("replay_reconcile")
shadow_collect = _load_onboarding("shadow_collect")


def _adapter(name, loader, symbol="TEST.SH"):
    return classic_adapter.ClassicStrategyAdapter(
        name, [symbol], market="CN", loader=loader)


# ---------------------------------------------------------------------- #
# ① manifest 模板过 agent_platform 既有校验器（真调 load_config）
# ---------------------------------------------------------------------- #
class TestManifestTemplate:
    def test_template_passes_load_config(self):
        """模板整体真调 load_config：四条目全部过严格 6 字段校验。"""
        config = ap_cfg.load_config(TEMPLATE_PATH)
        assert sorted(config.list_ids()) == sorted(CLASSIC_IDS)
        # 再真调运行前校验：REPLAY 模式 + 正常 context 大小 → 授权快照
        for sid in CLASSIC_IDS:
            auth = config.validate_for_run(sid, "REPLAY", 1024)
            assert isinstance(auth, ap_cfg.RunAuthorization)
            assert set(auth.permissions) == {"propose", "read_context"}
            assert "SIM_MARKET" not in auth.allowed_modes

    def test_template_field_discipline(self):
        raw = json.load(open(TEMPLATE_PATH, encoding="utf-8"))
        assert set(raw) == {"strategies"}
        required = {"strategy_id", "version", "allowed_data",
                    "resource_budget", "allowed_modes", "permissions"}
        for entry in raw["strategies"]:
            assert set(entry) == required            # 多一字段都不过校验器
            assert set(entry["resource_budget"]) == {
                "timeout_s", "max_concurrency", "max_context_bytes"}

    def test_template_rejects_extra_field(self, tmp_path):
        """防回归：多塞一个字段必须被既有校验器拒绝（严格模式仍生效）。"""
        raw = json.load(open(TEMPLATE_PATH, encoding="utf-8"))
        raw["strategies"][0]["extra"] = 1
        p = tmp_path / "bad.json"
        p.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(ap_cfg.StrategyConfigError):
            ap_cfg.load_config(str(p))


# ---------------------------------------------------------------------- #
# ② classic_adapter：四策略输出形状/索引正确、与 chan_monitor 同构
# ---------------------------------------------------------------------- #
class TestClassicAdapter:
    @pytest.mark.parametrize("name", CLASSIC_IDS)
    def test_event_signal_shape(self, synth_env, name):
        """在真实事件日上打分：Signal 契约字段齐全、动作与仓位方向一致。"""
        df, loader = synth_env
        ad = _adapter(name, loader)
        fn = classic_adapter.strategy_fn(name)
        events = classic_adapter.position_change_events(fn(df, {}))
        assert events, "合成数据应触发至少一次仓位变化（%s）" % name
        dt, pos = next(iter(events.items()))
        td8 = dt.strftime("%Y%m%d")
        sigs = ad.score(td8)
        assert len(sigs) == 1
        s = sigs[0]
        assert s.symbol == "TEST.SH"
        assert s.strategy == name
        assert s.trade_date == td8
        assert s.action == ("buy" if pos == 1 else "sell")
        assert 0.0 <= s.score <= 100.0
        assert 0.0 <= s.confidence <= 1.0
        assert s.rationale.strip()

    @pytest.mark.parametrize("name", CLASSIC_IDS)
    def test_no_change_no_signal(self, synth_env, name):
        """非事件日不产信号（持有帧不重复产出）。"""
        _, loader = synth_env
        ad = _adapter(name, loader)
        fn = classic_adapter.strategy_fn(name)
        pos = fn(_synth_df(), {})
        events = set(classic_adapter.position_change_events(pos))
        quiet = [dt for dt in pos.index
                 if dt not in events][:1]
        td8 = quiet[0].strftime("%Y%m%d")
        assert ad.score(td8) == []

    def test_struct_isomorphic_with_chan_monitor(self, synth_env, tmp_path):
        """与 chan_monitor 输出同构：同为 Signal 契约、字段集合一致。"""
        from strategy_layer.signal import Signal
        from strategy_layer.adapters.chan_monitor import ChanMonitorAdapter
        _, loader = synth_env
        pool = tmp_path / "pool.jsonl"
        pool.write_text(json.dumps({
            "market": "CN", "code": "000338.SZ", "type": "2买",
            "dt": "2026-04-01", "detect_date": "2026-04-01",
            "price": 15.8}, ensure_ascii=False),
            encoding="utf-8")
        ref = ChanMonitorAdapter(pool_globs=[str(pool)]).score("20260402")[0]
        mine = _adapter("ma_cross", loader).score("20260401")
        fn = classic_adapter.strategy_fn("ma_cross")
        df, _ = synth_env
        events = classic_adapter.position_change_events(fn(df, {}))
        td8 = next(iter(events)).strftime("%Y%m%d")
        mine = _adapter("ma_cross", loader).score(td8)[0]
        assert type(mine) is type(ref) is Signal
        assert ([f.name for f in dataclasses.fields(mine)]
                == [f.name for f in dataclasses.fields(ref)])

    def test_unknown_strategy_rejected(self, synth_env):
        _, loader = synth_env
        with pytest.raises(ValueError):
            classic_adapter.strategy_fn("no_such_strategy")

    def test_degradation_never_raises(self, synth_env, capsys):
        """loader 抛异常 → 降级返回 []，不向调用方 raise（协议约定）。"""
        def bad_loader(*a, **k):
            raise RuntimeError("db down")
        ad = _adapter("ma_cross", bad_loader)
        assert ad.score("20260601") == []
        assert "降级" in capsys.readouterr().out

    def test_sorted_by_score_desc(self, synth_env):
        df, loader = synth_env
        ad = classic_adapter.ClassicStrategyAdapter(
            "ma_cross", ["A.SH", "B.SH"], loader=loader)
        fn = classic_adapter.strategy_fn("ma_cross")
        events = classic_adapter.position_change_events(fn(df, {}))
        td8 = next(iter(events)).strftime("%Y%m%d")
        sigs = ad.score(td8)
        assert [s.score for s in sigs] == sorted(
            [s.score for s in sigs], reverse=True)


# ---------------------------------------------------------------------- #
# ③ replay_reconcile：100% 一致 → 0；95% 不一致 → 1
# ---------------------------------------------------------------------- #
class TestReplayReconcile:
    SYM = "TEST.SH"

    def _baseline(self, df, name="ma_cross"):
        fn = classic_adapter.strategy_fn(name)
        pos = fn(df, {})
        return {self.SYM: {replay_reconcile._to_ymd8(dt): int(v)
                           for dt, v in pos.items()}}

    def test_full_consistency_exit_0(self, synth_env, tmp_path, monkeypatch):
        """合成数据构造 100% 一致例 → passed=True，main 退出码 0。"""
        df, loader = synth_env
        baseline = self._baseline(df)
        ad = _adapter("ma_cross", loader)
        dates = sorted(baseline[self.SYM])
        adapter_pos = replay_reconcile.adapter_positions_from_scores(
            ad, dates, [self.SYM])
        report = replay_reconcile.run_reconcile(baseline, adapter_pos)
        assert report["n_total"] == len(df)
        assert report["agree_rate"] == 1.0
        assert report["passed"] is True
        # CLI 全链路（--baseline 文件形态）退出码 0；
        # main 内部自建 adapter，注入合成 loader 隔离真实 DB
        monkeypatch.setattr(classic_adapter, "_default_loader", loader)
        bl = tmp_path / "baseline.jsonl"
        with open(bl, "w", encoding="utf-8") as f:
            for td, p in baseline[self.SYM].items():
                f.write(json.dumps({"strategy": "ma_cross",
                                    "symbol": self.SYM,
                                    "trade_date": td, "position": p}) + "\n")
        rc = replay_reconcile.main([
            "--strategy", "ma_cross", "--symbol", self.SYM,
            "--end", "2026-12-31", "--baseline", str(bl)])
        assert rc == 0

    def test_95pct_inconsistency_exit_1(self, synth_env, tmp_path,
                                        monkeypatch):
        """95% 一致（< 99% 阈值）→ passed=False，main 退出码 1。"""
        df, loader = synth_env
        baseline = self._baseline(df)
        days = sorted(baseline[self.SYM])
        flip = days[: max(1, int(len(days) * 0.05))]      # 翻转 5% 仓位
        for td in flip:
            baseline[self.SYM][td] = 1 - baseline[self.SYM][td]
        ad = _adapter("ma_cross", loader)
        adapter_pos = replay_reconcile.adapter_positions_from_scores(
            ad, days, [self.SYM])
        report = replay_reconcile.run_reconcile(baseline, adapter_pos)
        assert report["agree_rate"] < 0.99
        assert report["passed"] is False
        assert report["n_mismatch"] == len(flip)
        monkeypatch.setattr(classic_adapter, "_default_loader", loader)
        bl = tmp_path / "baseline_bad.jsonl"
        with open(bl, "w", encoding="utf-8") as f:
            for td, p in baseline[self.SYM].items():
                f.write(json.dumps({"strategy": "ma_cross",
                                    "symbol": self.SYM,
                                    "trade_date": td, "position": p}) + "\n")
        rc = replay_reconcile.main([
            "--strategy", "ma_cross", "--symbol", self.SYM,
            "--end", "2026-12-31", "--baseline", str(bl)])
        assert rc == 1

    def test_baseline_jsonl_tolerates_bad_rows(self, tmp_path):
        """基线文件坏行跳过；strategy 不符的行拒收（防拿错文件）。"""
        p = tmp_path / "b.jsonl"
        rows = [
            {"strategy": "ma_cross", "symbol": "A.SH",
             "trade_date": "2026-06-01", "position": 1},   # 日期横杠容忍
            {"strategy": "other", "symbol": "A.SH",
             "trade_date": "20260602", "position": 1},     # 策略不符 → 跳过
            "{broken",
            {"strategy": "ma_cross", "symbol": "A.SH",
             "trade_date": "20260603", "position": 0},
        ]
        p.write_text("\n".join(json.dumps(r) if isinstance(r, dict) else r
                               for r in rows) + "\n", encoding="utf-8")
        out = replay_reconcile.load_baseline_jsonl(str(p), "ma_cross")
        assert out == {"A.SH": {"20260601": 1, "20260603": 0}}

    def test_empty_baseline_fails_fast(self, tmp_path):
        p = tmp_path / "empty.jsonl"
        p.write_text("", encoding="utf-8")
        rc = replay_reconcile.main([
            "--strategy", "ma_cross", "--symbol", "A.SH",
            "--end", "2026-12-31", "--baseline", str(p)])
        assert rc == 2


# ---------------------------------------------------------------------- #
# ③' shadow_collect：统计口径 + 幂等采集
# ---------------------------------------------------------------------- #
class TestShadowCollect:
    def _rows(self, n=35, positive=True):
        """n 条影子行，score 与前向收益相关（positive 控制方向）。"""
        rows = []
        for i in range(n):
            score = 10.0 + (i % 10) * 8.0
            r = (0.3 * score - 10.0) * (1 if positive else -1)
            rows.append({"symbol": "A.SH", "trade_date": "202606%02d" % (i + 1),
                         "score": score, "confidence": 0.5,
                         "action": "buy"})
        return rows

    def _fwd(self, rows):
        table = {r["trade_date"]: {5: r["score"] * 0.3 - 10.0 + 15.0,
                                   20: r["score"] * 0.3 - 10.0}
                 for r in rows}
        return lambda symbol, td: table.get(td)

    def test_gate_three_states(self):
        rows = self._rows(35)
        # 显著正向 → 转正
        st = shadow_collect.compute_shadow_stats(rows, fwd_fn=self._fwd(rows))
        assert st["gate"]["status"].startswith("显著正向·建议转正评估")
        assert st["gate"]["corr"]["t"] >= 2.0
        # 显著反向 → 淘汰（负相关表：score 越高 r20 越低）
        neg_rows = self._rows(35, positive=False)
        neg_table = {r["trade_date"]: {5: 1.0,
                                       20: -(r["score"] - 50.0) * 0.5}
                     for r in neg_rows}
        st_neg = shadow_collect.compute_shadow_stats(
            neg_rows, fwd_fn=lambda s, td: neg_table.get(td))
        assert st_neg["gate"]["status"].startswith("显著反向·建议淘汰")
        # n<30 → 样本积累中（延期）
        st_few = shadow_collect.compute_shadow_stats(
            self._rows(10), fwd_fn=self._fwd(self._rows(10)))
        assert st_few["gate"]["status"].startswith("样本积累中")
        assert st_few["gate"]["need"] == shadow_collect.N_MIN

    def test_bucket_structure(self):
        rows = self._rows(35)
        st = shadow_collect.compute_shadow_stats(rows, fwd_fn=self._fwd(rows))
        assert [b["bucket"] for b in st["by_score_bucket"]] == \
            list(shadow_collect.BUCKETS)
        assert st["by_score_bucket"][3]["n"] > 0     # 高分桶有样本

    def test_collect_once_idempotent(self, synth_env, tmp_path):
        """重复采集幂等：第二轮全 replay（对齐 record_verdicts 幂等纪律）。"""
        df, loader = synth_env
        ad = _adapter("ma_cross", loader)
        fn = classic_adapter.strategy_fn("ma_cross")
        events = classic_adapter.position_change_events(fn(df, {}))
        td8 = next(iter(events)).strftime("%Y%m%d")
        out = str(tmp_path / "shadow.jsonl")
        r1 = shadow_collect.collect_once(ad, td8, out_path=out)
        assert r1["recorded"] >= 1
        r2 = shadow_collect.collect_once(ad, td8, out_path=out)
        assert r2["recorded"] == 0 and r2["replayed"] == r1["recorded"]
        row = json.loads(open(out, encoding="utf-8").readline())
        assert row["mode"] == "SHADOW"
        assert row["strategy_id"] == "ma_cross"


# ---------------------------------------------------------------------- #
# ④ 零主链断言：onboarding 不 import 执行链（子进程隔离验证）
# ---------------------------------------------------------------------- #
class TestZeroMainChain:
    def test_onboarding_imports_no_execution_chain(self):
        code = (
            "import sys\n"
            "import strategy_layer.onboarding.classic_adapter\n"
            "import strategy_layer.onboarding.replay_reconcile\n"
            "import strategy_layer.onboarding.shadow_collect\n"
            "banned = ['backtest', 'runner', 'execution_engine',\n"
            "          'xq_classify', 'agent_platform']\n"
            "bad = [m for m in banned if m in sys.modules]\n"
            "assert not bad, 'onboarding 拉起了执行链: %r' % bad\n"
            "print('OK')\n"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = _QB_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run([sys.executable, "-c", code], cwd=_QB_ROOT,
                              env=env, capture_output=True, text=True,
                              timeout=120)
        assert proc.returncode == 0, proc.stderr
        assert "OK" in proc.stdout
