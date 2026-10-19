# -*- coding: utf-8 -*-
"""xueqiu_sync.py 单测（mock pysnowball，不发真实网络请求）。

覆盖：快照 by_date 布局 / kline 逐码去重+断点 / manifest checkpoint
（设计 §2.1 测试项 test_snap_layout/test_kline_dedup/
test_manifest_checkpoint）。数据落临时目录，不污染真实仓。

mock 形态对齐 pysnowball 0.1.8 实测口径（utls.py 源码核验）：
所有接口返回完整 JSON dict {"error_code":0, "data":{…}}。
"""
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

import pandas as pd

# 预热 parquet 引擎链（pandas→pyarrow→extension_types）。
# mock.patch.dict(sys.modules) 退出时会删除块内新增键：若引擎链在
# 块内首次导入，退出后被剥离，后续 read_parquet 二次执行
# extension_types 触发 pyarrow "pandas.period already defined"
# 双注册错误（探针实证 2026-10-16，存量 bug 与三级链改造无关）。
from pandas.io.parquet import get_engine as _warm_engine
_warm_engine("pyarrow")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import xueqiu_common as X
import xueqiu_sync as S

MS_DAY1 = 1760582400000        # 2026-10-16（本地时区 UTC+8 下当日下午）
MS_DAY0 = 1760496000000        # 前一交易日


def _fake_ball(monkey_attr: dict) -> types.ModuleType:
    """构造 fake pysnowball 模块（属性可覆写）。"""
    m = types.ModuleType("pysnowball")
    for k, v in monkey_attr.items():
        setattr(m, k, v)
    return m


def _quotec_j() -> dict:
    """quotec 标准返回（data.items，雪球真实形态）。"""
    return {"error_code": 0, "data": {"items": [
        {"symbol": "SZ000338", "current": 15.2, "percent": 1.2,
         "chg": 0.18, "volume": 1234500, "amount": 1.88e7,
         "pe": 12.5, "pb": 1.5, "market_capital": 1.3e10,
         "float_market_capital": 1.1e10, "timestamp": MS_DAY1},
        {"symbol": "SH600519", "current": 1520.0, "percent": -0.5,
         "chg": -7.6, "volume": 22000, "amount": 3.3e8,
         "pe": 20.1, "pb": 7.2, "market_capital": 1.9e12,
         "float_market_capital": 1.9e12, "timestamp": MS_DAY1},
    ]}}


def _kline_j() -> dict:
    """kline 标准返回（data.column+data.item 二维数组，雪球真实形态）。"""
    cols = ["timestamp", "volume", "open", "high", "low", "close", "chg",
            "percent", "turnoverrate", "amount"]
    return {"error_code": 0, "data": {
        "symbol": "SH600519", "column": cols,
        "item": [
            [MS_DAY0, 100, 15.0, 15.5, 14.9, 15.2, 0.2, 1.33, 0.5, 1.5e7],
            [MS_DAY1, 110, 15.2, 15.6, 15.1, 15.4, 0.2, 1.32, 0.55, 1.6e7],
        ]}}


class _TmpEnv(unittest.TestCase):
    """把 X.OUT_DIR 指到临时目录（隔离真实 data/xueqiu）。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = (X.OUT_DIR, X.MANIFEST, X.INFO_DB)
        X.OUT_DIR = os.path.join(self._tmp, "xueqiu")
        X.MANIFEST = os.path.join(X.OUT_DIR, "_manifest.json")
        X.INFO_DB = os.path.join(X.OUT_DIR, "xueqiu_info.db")
        os.environ.pop("XUEQIUTOKEN", None)
        # 全套测试隔离 token 池、动态代理与替代源；单测均不得触网。
        self._offline = [
            mock.patch("xq_tokens.get_pool",
                       return_value=types.SimpleNamespace(enabled=lambda: False)),
            mock.patch("xq_proxy.apply_proxy_env", return_value=""),
            mock.patch("xq_proxy.notify_blocked", return_value=None),
            mock.patch("xq_quote_router.fetch_daily",
                       return_value=({}, "", ["offline fallback"])),
        ]
        for patcher in self._offline:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self._offline):
            patcher.stop()
        X.OUT_DIR, X.MANIFEST, X.INFO_DB = self._orig
        os.environ.pop("XUEQIUTOKEN", None)


class TestSnapLayout(_TmpEnv):
    """test_snap_layout：快照落 d_YYYYMMDD.parquet + 列口径 + 去重。"""

    def test_by_date_layout(self):
        fake = _fake_ball({"quotec": lambda s: _quotec_j(),
                           "set_token": lambda t: None})
        with mock.patch.dict(sys.modules, {"pysnowball": fake}):
            mf = X.XqManifest(X.MANIFEST)
            n = S.sync_quote_snap(["000338.SZ", "SH600519"], "20261016", mf)
        self.assertGreaterEqual(n, 2)        # 请求的 2 码均落库
        path = os.path.join(X.OUT_DIR, "xq_quote_snap", "d_20261016.parquet")
        self.assertTrue(os.path.exists(path))
        df = pd.read_parquet(path)
        for col in ("symbol", "ts_code", "current", "percent", "chg",
                    "volume", "amount", "pe", "pb", "market_capital",
                    "float_market_capital", "trade_date", "timestamp"):
            self.assertIn(col, df.columns, msg=col)
        self.assertIn("000338.SZ", df["ts_code"].tolist())
        # manifest 记录
        ent = mf.get("xq_quote_snap")
        self.assertTrue(ent["done"])
        self.assertEqual(ent["file"], "d_20261016.parquet")
        self.assertEqual(ent["rows"], len(df))

    def test_no_token_still_runs_snap(self):
        """免 token 批：无 XUEQIUTOKEN 也照跑（降级矩阵）。"""
        fake = _fake_ball({"quotec": lambda s: _quotec_j()})
        with mock.patch.dict(sys.modules, {"pysnowball": fake}):
            n = S.sync_quote_snap(["000338.SZ"], "20261016")
        self.assertGreaterEqual(n, 1)

    def test_all_fail_marks_failed(self):
        """全批失败 → manifest failed，不 raise。"""
        def boom(s):
            raise RuntimeError("400016")
        fake = _fake_ball({"quotec": boom})
        with mock.patch.dict(sys.modules, {"pysnowball": fake}):
            mf = X.XqManifest(X.MANIFEST)
            n = S.sync_quote_snap(["000338.SZ"], "20261016", mf)
        self.assertEqual(n, 0)
        self.assertEqual(mf.get("xq_quote_snap")["status"], "failed")


class TestKlineDedup(_TmpEnv):
    """test_kline_dedup：逐码 by_code 布局 + 增量合并去重（后写覆盖）。"""

    def test_merge_dedup(self):
        os.environ["XUEQIUTOKEN"] = "t" * 40
        fake = _fake_ball({"kline": lambda s, period="day", count=-284:
                           _kline_j()})
        with mock.patch.dict(sys.modules, {"pysnowball": fake}):
            n1 = S.sync_kline(["SH600519"])
            path = os.path.join(X.OUT_DIR, "xq_kline_d", "600519.SH.parquet")
            self.assertTrue(os.path.exists(path))     # by_code 布局
            df1 = pd.read_parquet(path)
            self.assertEqual(len(df1), 2)
            # 二次拉取同数据：合并去重不增行（幂等）
            n2 = S.sync_kline(["SH600519"])
            df2 = pd.read_parquet(path)
            self.assertEqual(len(df2), 2)
            self.assertEqual(n1, n2)
            self.assertEqual(df2.loc[0, "ts_code"], "600519.SH")
            self.assertIn("timestamp", df2.columns)

    def test_no_token_skips(self):
        """token 批缺 token：skip_no_token，不落文件不 raise。"""
        fake = _fake_ball({"kline": lambda *a, **k: _kline_j()})
        with mock.patch.dict(sys.modules, {"pysnowball": fake}):
            mf = X.XqManifest(X.MANIFEST)
            n = S.sync_kline(["SH600519"], mf=mf)
        self.assertEqual(n, 0)
        self.assertEqual(mf.get("xq_kline_d")["status"], "skip_no_token")
        self.assertFalse(os.path.exists(os.path.join(
            X.OUT_DIR, "xq_kline_d", "600519.SH.parquet")))

    def test_kline_count_positive(self):
        """BUG-1 修复自证：ball.kline 收到 count=284（正数）。

        api_ref.kline URL 模板自带负号（count=-{}），传负数会拼出
        count=--284 畸形参数（QA Round-1 用 api_ref.format 离线坐实）。
        """
        calls = {}

        def spy_kline(sym, period="day", count=284):
            calls["count"] = count
            calls["period"] = period
            return _kline_j()

        os.environ["XUEQIUTOKEN"] = "t" * 40
        fake = _fake_ball({"kline": spy_kline})
        with mock.patch.dict(sys.modules, {"pysnowball": fake}):
            S.sync_kline(["SH600519"])
        self.assertEqual(calls["count"], 284)     # 不是 -284
        self.assertEqual(calls["period"], "day")

    def test_kline_400016_failed_no_parquet(self):
        """BUG-2 修复自证：token 失效（200+400016）→ failed，不落空片。

        修复前：空数据仍写空 parquet + manifest ok/done=True（伪成功）。
        """
        os.environ["XUEQIUTOKEN"] = "t" * 40
        bad = {"error_code": 400016, "error_description": "token 失效",
               "data": None}
        fake = _fake_ball({"kline": lambda *a, **k: bad})
        # T4 契约同步：替代源兜底 mock 为亦挂（本用例只验雪球域 failed 语义）
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch("xq_quote_router.fetch_daily",
                           return_value=({}, "", ["alt down"])):
            mf = X.XqManifest(X.MANIFEST)
            n = S.sync_kline(["SH600519"], mf=mf)
        self.assertEqual(n, 0)
        ent = mf.get("xq_kline_d")
        self.assertEqual(ent["status"], "failed")     # 不再是 ok
        self.assertFalse(ent["done"])                 # 不再 done=True
        self.assertIn("400016", ent["note"])          # 错码可见
        # 目录下无任何 parquet（含空片）
        kd = os.path.join(X.OUT_DIR, "xq_kline_d")
        if os.path.isdir(kd):
            self.assertEqual([f for f in os.listdir(kd)
                              if f.endswith(".parquet")], [])

    def test_kline_bj_no_empty_filename(self):
        """BUG-3 修复自证：BJ 股不产生空名 .parquet，归入 fail 可见。"""
        os.environ["XUEQIUTOKEN"] = "t" * 40
        fake = _fake_ball({"kline": lambda *a, **k: _kline_j()})
        with mock.patch.dict(sys.modules, {"pysnowball": fake}):
            mf = X.XqManifest(X.MANIFEST)
            n = S.sync_kline(["430047.BJ"], mf=mf)
        self.assertEqual(n, 0)
        ent = mf.get("xq_kline_d")
        self.assertEqual(ent["status"], "failed")
        self.assertIn("BJ430047", ent["note"])
        kd = os.path.join(X.OUT_DIR, "xq_kline_d")
        if os.path.isdir(kd):
            self.assertNotIn(".parquet", os.listdir(kd))   # 无空名文件


class TestKlineFallbackGuard(_TmpEnv):
    """T4 落片端只消费请求股票的同码日线，错误进入 manifest。"""

    def _run_fallback(self, codes: list[str], payload: tuple):
        """雪球域离线失败并将 mock 路由回包交给同步端。"""
        os.environ["XUEQIUTOKEN"] = "t" * 40
        fake = _fake_ball({"kline": mock.Mock(
            side_effect=RuntimeError("snow down"))})
        mf = X.XqManifest(X.MANIFEST)
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch("xq_quote_router.fetch_daily",
                           return_value=payload) as fetch, \
                mock.patch.object(X, "jitter_sleep", return_value=None):
            count = S.sync_kline(codes, mf=mf)
        return count, mf.get("xq_kline_d"), fetch, fake.kline

    def test_rejects_unrequested_and_index_keys_and_rows(self):
        """即便上游给恶意键及混码行，落片端不写、不计成功。"""
        rows = [
            {"symbol": "SH600519", "timestamp": "2026-10-16 00:00:00",
             "ts_code": "600519.SH", "close": 100},
            {"symbol": "SH000001", "timestamp": "2026-10-16 00:00:00",
             "ts_code": "000001.SH", "close": 10},
            {"symbol": "SZ000338", "timestamp": "2026-10-16 00:00:00",
             "ts_code": "000338.SZ", "close": 20},
            {"symbol": "SZ000001", "timestamp": "2026-10-16 00:00:00",
             "ts_code": "600519.SH", "close": 30},
            "bad row",
        ]
        count, entry, fetch, kline = self._run_fallback(
            ["SZ000001", "SH000001"],
            ({"SH600519": rows, "SH000001": rows,
              "SZ000001": rows}, "bad", ["router tainted"]))
        self.assertEqual(count, 0)
        self.assertFalse(entry["done"])
        self.assertEqual(entry["status"], "failed")
        self.assertIn("router tainted", entry["note"])
        fetch.assert_called_once_with(["SZ000001"], days=284)
        kline.assert_called_once_with("SZ000001", period="day", count=284)
        self.assertFalse(os.path.exists(os.path.join(X.OUT_DIR, "xq_kline_d")))

    def test_all_sources_fail_preserves_router_errors(self):
        """雪球失败 + 替代源全败：错误摘要入账、done=False。"""
        count, entry, _, _ = self._run_fallback(
            ["SH600519"], ({}, "", ["tushare: timeout", "akshare: offline"]))
        self.assertEqual(count, 0)
        self.assertEqual(entry["status"], "failed")
        self.assertFalse(entry["done"])
        self.assertIn("tushare: timeout", entry["note"])
        self.assertIn("akshare: offline", entry["note"])

    def test_no_success_and_no_bad_codes_is_failed(self):
        """兜底回包无有效行且无 bad codes 时仍判全败。"""
        self.assertEqual(S._kline_status(False, False, False), "failed")
        count, entry, _, _ = self._run_fallback(
            ["SH600519"], ({"SH600519": [
                {"symbol": "SZ000338"}]}, "",
                           ["tushare: outage", "akshare: outage"]))
        self.assertEqual(count, 0)
        self.assertFalse(entry["done"])
        self.assertEqual(entry["status"], "failed")
        self.assertIn("tushare: outage", entry["note"])
        self.assertIn("akshare: outage", entry["note"])
        self.assertNotIn("替代源警告", entry["note"])

    def test_ip_block_without_success_stays_ip_blocked(self):
        """403 熔断且兜底全败时冷却态不被普通 failed 覆盖。"""
        self.assertEqual(S._kline_status(True, False, False), "ip_blocked")

    def test_success_with_router_errors_reports_warning(self):
        """兜底成功时失败的其他来源仅是警告，不宣称兜底全败。"""
        row = {"symbol": "SZ000001", "ts_code": "000001.SZ",
               "timestamp": "2026-10-16 00:00:00", "close": 10.5}
        count, entry, _, _ = self._run_fallback(
            ["SZ000001"], ({"SZ000001": [row]}, "akshare_qfq×1",
                           ["tushare: outage"]))
        self.assertEqual(count, 1)
        self.assertTrue(entry["done"])
        self.assertIn("替代源兜底", entry["note"])
        self.assertIn("替代源警告: tushare: outage", entry["note"])
        self.assertNotIn("替代源失败", entry["note"])

    def test_valid_sz000001_lands_only_safe_rows(self):
        """SZ000001 是个股，不被 SH000001 指数锚误杀。"""
        good = {"symbol": "SZ000001", "ts_code": "000001.SZ",
                "timestamp": "2026-10-16 00:00:00", "trade_date": "20261016",
                "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
                "volume": 100, "amount": 1000}
        bad = dict(good, ts_code="000001.SH", timestamp="2026-10-17 00:00:00")
        count, entry, fetch, _ = self._run_fallback(
            ["SZ000001", "SH000001"],
            ({"SZ000001": [good, bad]}, "tushare_qfq×1", []))
        self.assertEqual(count, 1)
        self.assertTrue(entry["done"])
        self.assertEqual(entry["status"], "partial")  # 雪球域失败仍可见
        self.assertIn("替代源兜底", entry["note"])
        fetch.assert_called_once_with(["SZ000001"], days=284)
        path = os.path.join(X.OUT_DIR, "xq_kline_d", "000001.SZ.parquet")
        self.assertEqual(pd.read_parquet(path).to_dict("records"), [good])
        self.assertFalse(os.path.exists(os.path.join(
            X.OUT_DIR, "xq_kline_d", "000001.SH.parquet")))

    def test_invalid_rows_do_not_republish_old_file(self):
        """只有污染行时，旧片仅保留在磁盘，不能伪装本轮成功。"""
        path = os.path.join(X.OUT_DIR, "xq_kline_d", "000001.SZ.parquet")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        old = pd.DataFrame([{"symbol": "SZ000001",
                             "timestamp": "2026-10-15 00:00:00",
                             "ts_code": "000001.SZ", "close": 10.0}])
        old.to_parquet(path)
        with mock.patch.object(X, "atomic_parquet") as writer:
            count, entry, _, _ = self._run_fallback(
                ["SZ000001"], ({"SZ000001": [
                    {"symbol": "SH000001", "ts_code": "000001.SH",
                     "timestamp": "2026-10-16 00:00:00"}]}, "", []))
        writer.assert_not_called()
        self.assertEqual(count, 0)
        self.assertFalse(entry["done"])
        self.assertEqual(entry["status"], "failed")
        self.assertEqual(pd.read_parquet(path).to_dict("records"),
                         old.to_dict("records"))


class TestManifestCheckpoint(_TmpEnv):
    """test_manifest_checkpoint：断点语义（by_date 以当日片为 checkpoint）。"""

    def test_roundtrip_checkpoint(self):
        fake = _fake_ball({"quotec": lambda s: _quotec_j()})
        with mock.patch.dict(sys.modules, {"pysnowball": fake}):
            mf = X.XqManifest(X.MANIFEST)
            S.sync_quote_snap(["000338.SZ"], "20261016", mf)
        mf2 = X.XqManifest(X.MANIFEST)
        data = mf2.load()
        ent = data["xq_quote_snap"]
        self.assertTrue(ent["done"])
        self.assertEqual(ent["status"], "ok")
        self.assertIn("last_run", ent)      # 断点字段齐备
        # by_date checkpoint：当日片已存在 → 重跑覆盖式落片（同路径）
        with mock.patch.dict(sys.modules, {"pysnowball": fake}):
            mf3 = X.XqManifest(X.MANIFEST)
            mf3.load()
            S.sync_quote_snap(["000338.SZ"], "20261016", mf3)
        self.assertTrue(os.path.exists(os.path.join(
            X.OUT_DIR, "xq_quote_snap", "d_20261016.parquet")))

    def test_northbound_empty_no_file(self):
        """北向空返回：不落片 + manifest 记 empty（R4 对策）。"""
        fake = _fake_ball({"northbound_shareholding_sh": lambda d=None: [],
                           "northbound_shareholding_sz": lambda d=None: []})
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch.object(X, "jitter_sleep", lambda *a: None):
            mf = X.XqManifest(X.MANIFEST)
            n = S.sync_northbound("20261016", mf)
        self.assertEqual(n, 0)
        self.assertEqual(mf.get("xq_northbound")["status"], "empty")
        self.assertFalse(os.path.exists(os.path.join(
            X.OUT_DIR, "xq_northbound", "d_20261016.parquet")))

    def test_northbound_rows_and_checkpoint(self):
        """北向有数（HKEX list[dict] 形态）→ 落片；当日片存在则跳过。"""
        rows = [{"code": "70001", "name": "某某", "shareholding": 123,
                 "shareholding_percent": "1.5"},
                {"code": "90001", "name": "某某2", "shareholding": 456,
                 "shareholding_percent": "2.5"}]
        fake = _fake_ball({
            "northbound_shareholding_sh": lambda d=None: rows,
            "northbound_shareholding_sz": lambda d=None: rows})
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch.object(X, "jitter_sleep", lambda *a: None):
            n = S.sync_northbound("20261016")
        self.assertEqual(n, 2)  # 沪深两源同码，(symbol,trade_date) 去重后 2
        path = os.path.join(X.OUT_DIR, "xq_northbound", "d_20261016.parquet")
        self.assertTrue(os.path.exists(path))
        df = pd.read_parquet(path)
        self.assertIn("trade_date", df.columns)
        self.assertIn("SZ000001", df["symbol"].tolist())   # HKEX 码已转符号
        # 当日片已存在 → checkpoint 跳过
        with mock.patch.dict(sys.modules, {"pysnowball": fake}):
            n2 = S.sync_northbound("20261016")
        self.assertEqual(n2, 0)

    def test_flow_by_date(self):
        """资金流逐码→取最新行落当日片。"""
        os.environ["XUEQIUTOKEN"] = "t" * 40
        flow_j = {"error_code": 0, "data": {"items": [
            {"timestamp": MS_DAY0, "main_net_inflow": 1.2e7,
             "main_net_inflow_rate": 5.5, "retail_net_inflow": -3e6},
            {"timestamp": MS_DAY1, "main_net_inflow": 2.4e7,
             "main_net_inflow_rate": 8.1, "retail_net_inflow": -5e6},
        ]}}
        fake = _fake_ball({"capital_flow": lambda s: flow_j})
        with mock.patch.dict(sys.modules, {"pysnowball": fake}):
            n = S.sync_capital_flow(["SZ000338"], "20261016")
        self.assertEqual(n, 1)              # 取最新一行
        df = pd.read_parquet(os.path.join(
            X.OUT_DIR, "xq_capital_flow", "d_20261016.parquet"))
        self.assertEqual(df.loc[0, "main_net_inflow"], 2.4e7)
        self.assertEqual(df.loc[0, "ts_code"], "000338.SZ")

    def test_forecast_by_code(self):
        """盈利预测逐码落库 + (symbol,ann_date,broker) 去重。"""
        os.environ["XUEQIUTOKEN"] = "t" * 40
        fc_j = {"error_code": 0, "data": {"items": [
            {"created_at": "2026-10-10", "org_name": "中信",
             "target_price": 18.0, "eps_next_year": 1.2,
             "eps_this_year": 1.0, "pe_next_year": 15.0},
        ]}}
        fake = _fake_ball({"earningforecast": lambda s: fc_j})
        with mock.patch.dict(sys.modules, {"pysnowball": fake}):
            n = S.sync_earningforecast(["SZ000338"])
        self.assertEqual(n, 1)
        path = os.path.join(X.OUT_DIR, "xq_earningforecast",
                            "000338.SZ.parquet")
        self.assertTrue(os.path.exists(path))       # by_code 布局
        df = pd.read_parquet(path)
        self.assertEqual(df.loc[0, "ann_date"], "20261010")
        self.assertEqual(df.loc[0, "broker"], "中信")


class TestMainCli(_TmpEnv):
    """CLI 冒烟：--status 无 token 不崩溃；probe 失败 degraded rc=0。"""

    def test_status_no_crash(self):
        rc = S.main(["--status"])
        self.assertEqual(rc, 0)

    def test_probe_fail_degraded(self):
        """quotec 探测失败 → 全批 degraded，rc=0（降级≠失败）。"""
        probe_fail = {"ok": False, "latency_ms": 0, "ts": "x",
                      "err": "network"}
        with mock.patch.object(X, "quotec_probe",
                               return_value=probe_fail), \
                mock.patch.object(X, "load_token", lambda *a: ""):
            rc = S.main(["--batch", "snap"])
        self.assertEqual(rc, 0)
        mf = X.XqManifest(X.MANIFEST)
        data = mf.load()
        self.assertEqual(data["xq_quote_snap"]["status"], "degraded")


class TestRcSemantics(_TmpEnv):
    """P1 回归（审计）：全批业务失败（manifest status=failed，如 400016
    token 失效）→ rc=1 触发 wrapper 告警，不再伪成功 rc=0；
    部分失败/降级/skip 仍 rc=0（降级矩阵口径不变）。"""

    _PROBE_OK = {"ok": True, "latency_ms": 1, "ts": "x", "err": ""}
    _TP_VALID = {"ok": True, "valid": True, "latency_ms": 1, "ts": "x",
                 "err": ""}

    def test_kline_all_fail_rc1(self):
        os.environ["XUEQIUTOKEN"] = "t" * 40
        bad = {"error_code": 400016, "error_description": "token 失效",
               "data": None}
        fake = _fake_ball({"kline": lambda *a, **k: bad})
        # T4 契约同步：替代源兜底 mock 为亦挂（本用例只验 rc=1 旧契约）
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch.object(X, "load_token",
                                  return_value="t" * 40), \
                mock.patch.object(X, "quotec_probe",
                                  return_value=self._PROBE_OK), \
                mock.patch.object(X, "token_probe",
                                  return_value=self._TP_VALID), \
                mock.patch("xq_quote_router.fetch_daily",
                           return_value=({}, "", ["alt down"])), \
                mock.patch.object(X, "jitter_sleep", lambda *a: None):
            rc = S.main(["--batch", "kline", "--codes", "SH600519"])
        self.assertEqual(rc, 1)
        mf = X.XqManifest(X.MANIFEST)
        mf.load()
        self.assertEqual(mf.get("xq_kline_d")["status"], "failed")
        self.assertIn("400016", mf.get("xq_kline_d")["note"])

    def test_partial_fail_still_rc0(self):
        """--batch all：snap 成功 + kline/flow 失败 → 部分失败 rc=0。"""
        os.environ["XUEQIUTOKEN"] = "t" * 40
        bad = {"error_code": 400016, "error_description": "token 失效",
               "data": None}
        fake = _fake_ball({"quotec": lambda s: _quotec_j(),
                           "kline": lambda *a, **k: bad,
                           "capital_flow": lambda s: bad})
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch.object(X, "load_token",
                                  return_value="t" * 40), \
                mock.patch.object(X, "quotec_probe",
                                  return_value=self._PROBE_OK), \
                mock.patch.object(X, "token_probe",
                                  return_value=self._TP_VALID), \
                mock.patch.object(X, "jitter_sleep", lambda *a: None):
            rc = S.main(["--batch", "all", "--codes", "SH600519"])
        self.assertEqual(rc, 0)

    def test_skip_no_token_still_rc0(self):
        """缺 token 降级 skip_no_token → rc=0（降级≠失败，口径不变）。

        P1-5 隔离修正：patch X.load_token 返回空（此前仅 pop
        XUEQIUTOKEN 无效——load_token 读真实 .env.local，测试实际
        一直拿到真 token，skip 分支从未被走到）。
        """
        fake = _fake_ball({"quotec": lambda s: _quotec_j(),
                           "kline": lambda *a, **k: _kline_j()})
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch.object(X, "load_token", return_value=""), \
                mock.patch.object(X, "quotec_probe",
                                  return_value=self._PROBE_OK), \
                mock.patch.object(X, "jitter_sleep", lambda *a: None):
            rc = S.main(["--batch", "all", "--codes", "SH600519"])
        self.assertEqual(rc, 0)
        mf = X.XqManifest(X.MANIFEST)
        mf.load()
        self.assertEqual(mf.get("xq_kline_d")["status"], "skip_no_token")

    def test_batch_interval_pacing(self):
        """P0-2：--batch all 每批派发前走节奏闸（批衔接不再裸奔）。

        单码批内循环尾不睡（i<len 短路），sleeps = 1（init_ball 内
        token_probe 前，P1-5 两级探测节奏纪律）+ 批数 4 = 5；northbound
        内部抓取函数在 fake ball 缺属性时 continue 不触发额外节奏。
        """
        os.environ["XUEQIUTOKEN"] = "t" * 40
        fake = _fake_ball({"quotec": lambda s: _quotec_j(),
                           "kline": lambda *a, **k: _kline_j()})
        sleeps: list[int] = []
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch.object(X, "load_token",
                                  return_value="t" * 40), \
                mock.patch.object(X, "quotec_probe",
                                  return_value=self._PROBE_OK), \
                mock.patch.object(X, "token_probe",
                                  return_value=self._TP_VALID), \
                mock.patch.object(X, "jitter_sleep",
                                  lambda *a, **k: sleeps.append(1)):
            S.main(["--batch", "all", "--codes", "SH600519"])
        self.assertEqual(len(sleeps), 5)


class TestTokenProbe(_TmpEnv):
    """P1-5：登录态预检（MediaCrawler pong 等价物）——失效跳批 rc=1、
    网络故障不跳批、缺 token 不探测（RESEARCH_MEDIACRAWLER §E P1-5）。"""

    _PROBE_OK = {"ok": True, "latency_ms": 1, "ts": "x", "err": ""}
    _TP_DEAD = {"ok": True, "valid": False, "latency_ms": 1, "ts": "x",
                "err": "error_code=400016 token 失效"}
    _TP_VALID = {"ok": True, "valid": True, "latency_ms": 1, "ts": "x",
                 "err": ""}
    _TP_NET = {"ok": False, "valid": False, "latency_ms": 1, "ts": "x",
               "err": "net: timeout"}

    def test_token_dead_skips_batches_rc1(self):
        """400016 预检失效 → token 批跳过（manifest failed）+ snap 照跑
        + rc=1 告警（token 死亡必须人工介入）。"""
        fake = _fake_ball({"quotec": lambda s: _quotec_j(),
                           "kline": lambda *a, **k: _kline_j()})
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch.object(X, "load_token",
                                  return_value="t" * 40), \
                mock.patch.object(X, "quotec_probe",
                                  return_value=self._PROBE_OK), \
                mock.patch.object(X, "token_probe",
                                  return_value=self._TP_DEAD), \
                mock.patch.object(X, "jitter_sleep", lambda *a: None):
            rc = S.main(["--batch", "all", "--codes", "SH600519"])
        self.assertEqual(rc, 1)
        mf = X.XqManifest(X.MANIFEST)
        mf.load()
        for tbl in ("xq_kline_d", "xq_capital_flow", "xq_northbound",
                    "xq_earningforecast"):
            self.assertEqual(mf.get(tbl)["status"], "failed", tbl)
            self.assertIn("token_probe", mf.get(tbl)["note"])
        self.assertEqual(mf.get("xq_quote_snap")["status"], "ok")
        self.assertIn("400016",
                      (mf.health().get("token_probe") or {}).get("err", ""))

    def test_token_dead_single_batch_rc1(self):
        """--batch kline 单 token 批 + 失效 → names 过滤空、rc=1。"""
        fake = _fake_ball({"quotec": lambda s: _quotec_j(),
                           "kline": lambda *a, **k: _kline_j()})
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch.object(X, "load_token",
                                  return_value="t" * 40), \
                mock.patch.object(X, "quotec_probe",
                                  return_value=self._PROBE_OK), \
                mock.patch.object(X, "token_probe",
                                  return_value=self._TP_DEAD), \
                mock.patch.object(X, "jitter_sleep", lambda *a: None):
            rc = S.main(["--batch", "kline", "--codes", "SH600519"])
        self.assertEqual(rc, 1)
        mf = X.XqManifest(X.MANIFEST)
        mf.load()
        self.assertEqual(mf.get("xq_kline_d")["status"], "failed")
        self.assertIn("token_probe", mf.get("xq_kline_d")["note"])

    def test_token_net_fail_no_skip(self):
        """ok=False（网络故障，token 状态未知）→ 不跳批，沿用各批如实
        失败路径（kline 真执行且因 fake 缺数据如实记录）。"""
        os.environ["XUEQIUTOKEN"] = "t" * 40   # load_token 被 patch，env 自备
        fake = _fake_ball({"quotec": lambda s: _quotec_j(),
                           "kline": lambda *a, **k: _kline_j()})
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch.object(X, "load_token",
                                  return_value="t" * 40), \
                mock.patch.object(X, "quotec_probe",
                                  return_value=self._PROBE_OK), \
                mock.patch.object(X, "token_probe",
                                  return_value=self._TP_NET), \
                mock.patch.object(X, "jitter_sleep", lambda *a: None):
            rc = S.main(["--batch", "all", "--codes", "SH600519"])
        # 不跳批：kline 正常执行成功（fake 返回合法数据）→ rc=0
        self.assertEqual(rc, 0)
        mf = X.XqManifest(X.MANIFEST)
        mf.load()
        self.assertEqual(mf.get("xq_kline_d")["status"], "ok")

    def test_no_token_no_probe(self):
        """缺 token → token_probe 不调（肯定无效，零浪费请求）。

        patch X.load_token 返回空（load_token 读真实 .env.local，
        pop XUEQIUTOKEN 无法隔离 token 源）。
        """
        fake = _fake_ball({"quotec": lambda s: _quotec_j(),
                           "kline": lambda *a, **k: _kline_j()})
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch.object(X, "load_token", return_value=""), \
                mock.patch.object(X, "quotec_probe",
                                  return_value=self._PROBE_OK), \
                mock.patch.object(X, "token_probe") as m_tp, \
                mock.patch.object(X, "jitter_sleep", lambda *a: None):
            S.main(["--batch", "snap", "--codes", "SH600519"])
        m_tp.assert_not_called()


class TestIpBlockedSync(_TmpEnv):
    """P0-1：sync 侧熔断——main 冷却入口 rc=1 / 批内 403 trip+break
    （RESEARCH_MEDIACRAWLER_XUEQIU_20261017.md §E P0-1）。"""

    def test_main_cooldown_entry_rc1(self):
        """冷却生效 → 五表 ip_blocked、init_ball 不执行（连 probe 都不发）。"""
        mf = X.XqManifest(X.MANIFEST)
        mf.load()
        X.ip_block_trip(mf)
        mf.save()
        with mock.patch.object(S, "init_ball") as m_init:
            rc = S.main(["--batch", "all"])
        self.assertEqual(rc, 1)                 # 告警语义：缺口必须可见
        m_init.assert_not_called()              # 零雪球请求（含 probe）
        mf2 = X.XqManifest(X.MANIFEST)
        mf2.load()
        for tbl in ("xq_quote_snap", "xq_kline_d", "xq_capital_flow",
                    "xq_northbound", "xq_earningforecast"):
            self.assertEqual(mf2.get(tbl)["status"], "ip_blocked", tbl)

    def test_kline_403_trip_breaks(self):
        """kline 逐码 403 拉黑 → trip 写 health + break 停轮（后续码不发）。"""
        os.environ["XUEQIUTOKEN"] = "t" * 40
        calls = {"n": 0}

        def _kline_403(*a, **k):
            calls["n"] += 1
            raise RuntimeError("Seek IP Blacklisted (IP = 1.2.3.4)")

        fake = _fake_ball({"kline": _kline_403})
        # T4 契约同步：替代源兜底 mock 为亦挂（本用例只验 403 熔断停轮）
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch("xq_quote_router.fetch_daily",
                           return_value=({}, "", ["alt down"])), \
                mock.patch.object(X, "jitter_sleep", lambda *a: None):
            mf = X.XqManifest(X.MANIFEST)
            n = S.sync_kline(["SH600519", "SZ000338", "SH600036"], mf=mf)
        self.assertEqual(n, 0)
        self.assertEqual(calls["n"], 1)         # 首码即熔断，未打后续码
        ent = mf.get("xq_kline_d")
        self.assertEqual(ent["status"], "ip_blocked")
        self.assertTrue((mf.health() or {}).get("ip_blocked_until"))
        self.assertTrue(mf.save())              # manifest 可落盘

    def test_sync_kline_alt_source_rescue(self):
        """T4（2026-10-19）：雪球侧全挂（403）+ 替代源供数 → 落片成功。

        熔断只停雪球域轮次，不拦替代源接管——status=partial（雪球败
        + 替代源成）、note 带替代源兜底说明（接管事实 manifest 可见）。
        """
        os.environ["XUEQIUTOKEN"] = "t" * 40
        fake = _fake_ball({"kline": lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("Seek IP Blacklisted (IP = 1.2.3.4)"))})
        alt_rows = [{"symbol": "SH600519", "ts_code": "600519.SH",
                     "timestamp": "2026-10-16 00:00:00", "volume": 100,
                     "open": 10.0, "high": 11.0, "low": 9.9,
                     "close": 10.5, "chg": 0.5, "percent": 5.0,
                     "turnoverrate": None, "amount": 1.0e6,
                     "trade_date": "20261016"}]
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch("xq_quote_router.fetch_daily",
                           return_value=({"SH600519": alt_rows},
                                         "tushare_qfq×1", [])), \
                mock.patch.object(X, "jitter_sleep", lambda *a: None):
            mf = X.XqManifest(X.MANIFEST)
            n = S.sync_kline(["SH600519"], mf=mf)
        self.assertEqual(n, 1)                  # 替代源 1 行落片
        ent = mf.get("xq_kline_d")
        self.assertEqual(ent["status"], "partial")
        self.assertIn("替代源兜底", ent["note"])   # 接管事实落账可见
        self.assertTrue(ent["done"])            # 数据不断供 → done=True

    def test_quote_snap_403_trip(self):
        """quotec 批 403 → status=ip_blocked + trip。"""
        def _quotec_403(*a, **k):
            raise RuntimeError("seek ip blacklisted")
        fake = _fake_ball({"quotec": _quotec_403})
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch.object(X, "jitter_sleep", lambda *a: None):
            mf = X.XqManifest(X.MANIFEST)
            n = S.sync_quote_snap(["SH600519", "SZ000338"],
                                  "20261017", mf)
        self.assertEqual(n, 0)
        self.assertEqual(mf.get("xq_quote_snap")["status"], "ip_blocked")
        self.assertTrue((mf.health() or {}).get("ip_blocked_until"))

    def test_flow_403_partial_keeps_rows(self):
        """资金流部分成功后熔断 → 已采行落片 + status=partial。"""
        os.environ["XUEQIUTOKEN"] = "t" * 40
        good = {"error_code": 0, "data": {"items": [
            {"timestamp": 1760582400000, "main_net_inflow": 1.0,
             "main_net_inflow_rate": 0.1, "retail_net_inflow": -1.0}]}}
        calls = {"n": 0}

        def _flow(sym, *a, **k):
            calls["n"] += 1
            if str(sym).startswith("SZ"):
                raise RuntimeError("Seek IP Blacklisted")
            return good

        fake = _fake_ball({"capital_flow": _flow})
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch.object(X, "jitter_sleep", lambda *a: None):
            mf = X.XqManifest(X.MANIFEST)
            n = S.sync_capital_flow(["SH600519", "SZ000338"],
                                    "20261017", mf)
        self.assertGreater(n, 0)                # 已采行保留
        self.assertEqual(mf.get("xq_capital_flow")["status"], "partial")
        self.assertTrue((mf.health() or {}).get("ip_blocked_until"))


class TestInitBallCrash(_TmpEnv):
    """P2-9：init_ball 意外异常（token 池坏序列化等理论外路径）→
    全批 degraded rc=0（与 probe 失败同语义），manifest 必落盘，
    绝不让异常出 main 拖垮整轮。"""

    def test_init_ball_crash_degraded_rc0(self):
        with mock.patch.object(S, "init_ball",
                               side_effect=RuntimeError("boom")):
            rc = S.main(["--batch", "all"])
        self.assertEqual(rc, 0)
        mf = X.XqManifest(X.MANIFEST)
        mf.load()
        for tbl in ("xq_quote_snap", "xq_kline_d", "xq_capital_flow",
                    "xq_northbound", "xq_earningforecast"):
            self.assertEqual(mf.get(tbl)["status"], "degraded")
            self.assertIn("init_ball crash", mf.get(tbl)["note"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
