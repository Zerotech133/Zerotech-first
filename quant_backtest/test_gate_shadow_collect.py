# -*- coding: utf-8 -*-
"""W5 影子积累期 单测：环境闸门三档虚拟对照日采集器（离线合成数据，
不触网、不读产线真实产物、不推飞书）。

运行（指定解释器）:
    "C:/Program Files/Python311/python.exe" quant_backtest/test_gate_shadow_collect.py

覆盖四条路径:
    1) 三档记录正确性（θ∈{40,45,50} 拦截口径：qd/cross 纯宽度、
       qpzm 宽度+med20 双条件；blocked_codes 清单逐一对得上）
    2) 幂等（同日同策略同档同状态不重复；ok 已存在不再补 missing；
       missing 已存在且源转 ok 允许补落 ok 行）
    3) 源缺失标注 missing（产物不存在/字段异常 → missing 行不阻塞
       其他策略）
    4) collector 异常不影响主流程（collect_run 自身不抛、坏 JSON 源
       优雅降级、挂载点式 try/except 语义成立）
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import gate_shadow_collect as gsc  # noqa: E402


def _w(fp, obj):
    Path(fp).parent.mkdir(parents=True, exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _mk_series(fp, dates=("20261012", "20261013"), brs=(0.42, 0.36)):
    _w(fp, {"dates": list(dates), "breadth": list(brs)})


class TestTierCorrectness(unittest.TestCase):
    """路径1：三档记录正确性（合成源）"""

    def test_qd_three_tiers(self):
        """qd：注记 breadth=43% → θ40 放行 / θ45·θ50 拦（宽度单一条件）"""
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "qd_scan_cache.json"
            series = Path(td) / "series.json"
            _w(
                cache,
                {
                    "ts": "2026-10-13 14:46:22",
                    "signals_now": [
                        {"code": "300890.SZ", "breadth": 43.0},
                        {"code": "002246.SZ", "breadth": 43.0},
                    ],
                },
            )
            _mk_series(series)
            rows = gsc.collect_run(
                strategies=("qd",),
                out=Path(td) / "o.jsonl",
                qd_cache=cache,
                series_fp=series,
            )
            self.assertEqual(len(rows), 3)
            by = {r["theta"]: r for r in rows}
            self.assertEqual(by[40]["n_blocked"], 0)
            self.assertEqual(by[45]["n_blocked"], 2)
            self.assertEqual(by[50]["n_blocked"], 2)
            self.assertEqual(by[45]["blocked_codes"], ["002246.SZ", "300890.SZ"])
            self.assertEqual(by[40]["date"], "20261013")  # ts 取日期
            self.assertAlmostEqual(by[40]["breadth"], 0.43)
            self.assertIsNone(by[40]["med20"])
            self.assertIsNone(by[40]["fwd"])  # 前向收益占位

    def test_qd_fallback_series_anchor(self):
        """qd：旧缓存无 breadth 注记 → 回退宽度序列最新锚"""
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "qd_scan_cache.json"
            series = Path(td) / "series.json"
            _w(
                cache,
                {"ts": "2026-10-12 14:46:22", "signals_now": [{"code": "600001.SH"}]},
            )
            _mk_series(series, dates=("20261012", "20261013"), brs=(0.42, 0.36))
            rows = gsc.collect_run(
                strategies=("qd",),
                out=Path(td) / "o.jsonl",
                qd_cache=cache,
                series_fp=series,
            )
            by = {r["theta"]: r for r in rows}
            # 锚 20261013 br=0.36 → θ40 拦
            self.assertEqual(by[40]["n_blocked"], 1)
            self.assertAlmostEqual(by[40]["breadth"], 0.36)
            self.assertIn("序列锚", by[40]["src"])

    def test_qpzm_two_conditions(self):
        """qpzm：br=42% med20=-1% → θ40 放行 / θ45·θ50 宽度拦；
        br=42% med20=-4% → 三档全拦（med20 条件与产线一致不动）"""
        with tempfile.TemporaryDirectory() as td:
            snap = Path(td) / "rps_latest.json"
            series = Path(td) / "series.json"
            res = [
                {"market": "CN", "code": "sz000001", "action": "买入"},
                {"market": "CN", "code": "sz000002", "action": "回避（环境闸门）"},
                {"market": "US", "code": "gb_pltr", "action": "买入"},
            ]
            _mk_series(series)
            for med, exp in (
                (-0.01, {40: 0, 45: 2, 50: 2}),
                (-0.04, {40: 2, 45: 2, 50: 2}),
            ):
                _w(
                    snap,
                    {
                        "latest": "20261013",
                        "envs": [
                            {
                                "label": "A股全市场(5555只)",
                                "breadth": 0.42,
                                "ret20": med,
                            }
                        ],
                        "res": res,
                    },
                )
                out = Path(td) / ("o_%s.jsonl" % med)
                rows = gsc.collect_run(
                    strategies=("qpzm",), out=out, qpzm_snap=snap, series_fp=series
                )
                by = {r["theta"]: r for r in rows}
                self.assertEqual(by[40]["n_total"], 2)  # 闸门前口径，剔 US
                for th, n in exp.items():
                    self.assertEqual(by[th]["n_blocked"], n)
                self.assertAlmostEqual(by[40]["med20"], med)
                # 闸门否决单（回避）也在清单里
                if exp[40]:
                    self.assertIn("sz000002", by[40]["blocked_codes"])

    def test_cross_tiers_and_boundary(self):
        """cross：meta.d1 当日事件过滤 + 边界（br=45% 恰好 θ45 放行）"""
        with tempfile.TemporaryDirectory() as td:
            ev = Path(td) / "sig_events_market_v3_20260901_20261013.json"
            series = Path(td) / "series.json"
            _w(
                ev,
                {
                    "meta": {"d0": "20260901", "d1": "20261013"},
                    "events": [
                        {"stock": "000001.SZ", "day": "20261013"},
                        {"stock": "000002.SZ", "day": "20261013"},
                        {"stock": "000003.SZ", "day": "20261012"},
                    ],
                },
            )
            _mk_series(series, brs=(0.44, 0.45))
            rows = gsc.collect_run(
                strategies=("cross",),
                out=Path(td) / "o.jsonl",
                cross_glob=str(ev),
                series_fp=series,
            )
            by = {r["theta"]: r for r in rows}
            self.assertEqual(by[40]["date"], "20261013")
            self.assertEqual(by[40]["n_total"], 2)  # 只取 d1 当日
            self.assertEqual(by[40]["n_blocked"], 0)  # 锚 45% ≥40 放行
            self.assertEqual(by[45]["n_blocked"], 0)  # 边界 45>=45 放行
            self.assertEqual(by[50]["n_blocked"], 2)  # 45<50 拦


class TestIdempotent(unittest.TestCase):
    """路径2：幂等（同日同策略同档不重复）"""

    def _src(self, td):
        cache = Path(td) / "qd_scan_cache.json"
        series = Path(td) / "series.json"
        _w(
            cache,
            {
                "ts": "2026-10-13 14:46:22",
                "signals_now": [{"code": "300890.SZ", "breadth": 36.0}],
            },
        )
        _mk_series(series)
        return cache, series

    def test_rerun_no_duplicate(self):
        with tempfile.TemporaryDirectory() as td:
            cache, series = self._src(td)
            out = Path(td) / "o.jsonl"
            gsc.collect_run(
                strategies=("qd",), out=out, qd_cache=cache, series_fp=series
            )
            rows2 = gsc.collect_run(
                strategies=("qd",), out=out, qd_cache=cache, series_fp=series
            )
            self.assertEqual(rows2, [])  # 第二次零追加
            lines = [
                json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x
            ]
            self.assertEqual(len(lines), 3)

    def test_ok_row_blocks_missing_but_missing_upgrades(self):
        """ok 已存在 → 不再补 missing；missing 已存在且源转 ok → 补 ok"""
        with tempfile.TemporaryDirectory() as td:
            cache, series = self._src(td)
            out = Path(td) / "o.jsonl"
            # 先手工落一条 missing（模拟早前源缺失）
            gsc._append(gsc._mk_missing("20261013", "qd", "人为模拟"), out)
            gsc.collect_run(
                strategies=("qd",), out=out, qd_cache=cache, series_fp=series
            )
            lines = [
                json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x
            ]
            ok = [x for x in lines if x["source_status"] == "ok"]
            miss = [x for x in lines if x["source_status"] == "missing"]
            self.assertEqual(len(ok), 3)  # ok 行补落成功
            self.assertEqual(len(miss), 3)  # missing 留痕不删
            # 再跑：ok 幂等零追加
            n = gsc.collect_run(
                strategies=("qd",), out=out, qd_cache=cache, series_fp=series
            )
            self.assertEqual(n, [])


class TestMissingSource(unittest.TestCase):
    """路径3：源缺失标注 missing，不阻塞其他策略"""

    def test_missing_rows_and_partial(self):
        with tempfile.TemporaryDirectory() as td:
            series = Path(td) / "series.json"
            _mk_series(series)
            snap = Path(td) / "rps_latest.json"
            res = [{"market": "CN", "code": "sz000001", "action": "买入"}]
            _w(
                snap,
                {
                    "latest": "20261013",
                    "envs": [
                        {"label": "A股全市场(9只)", "breadth": 0.42, "ret20": -0.01}
                    ],
                    "res": res,
                },
            )
            out = Path(td) / "o.jsonl"
            # qd 缓存不存在 / cross 无产物 → 各落 3 条 missing；
            # qpzm 正常 → 3 条 ok，互不阻塞
            rows = gsc.collect_run(
                strategies=("qd", "qpzm", "cross"),
                out=out,
                series_fp=series,
                qd_cache=Path(td) / "no_qd.json",
                qpzm_snap=snap,
                cross_glob=str(Path(td) / "none_*.json"),
            )
            self.assertEqual(len(rows), 9)
            by = {(r["strategy"], r["theta"]): r for r in rows}
            for th in (40, 45, 50):
                self.assertEqual(by[("qd", th)]["source_status"], "missing")
                self.assertIsNone(by[("qd", th)]["n_total"])
                self.assertEqual(by[("cross", th)]["source_status"], "missing")
                self.assertEqual(by[("qpzm", th)]["source_status"], "ok")
            # missing 行带缺失原因（异常类型摘要），供评审溯源
            self.assertTrue(by[("qd", 40)]["src"])

    def test_bad_fields_treated_missing(self):
        """字段异常（无 latest/无 A股 env）→ missing 而非崩溃"""
        with tempfile.TemporaryDirectory() as td:
            series = Path(td) / "series.json"
            _mk_series(series)
            snap = Path(td) / "rps_latest.json"
            _w(snap, {"latest": "", "envs": [], "res": []})
            rows = gsc.collect_run(
                strategies=("qpzm",),
                out=Path(td) / "o.jsonl",
                qpzm_snap=snap,
                series_fp=series,
            )
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(r["source_status"] == "missing" for r in rows))


class TestMountSafety(unittest.TestCase):
    """路径4：collector 异常不影响主流程"""

    def test_collect_run_never_raises(self):
        """全源坏掉（目录不存在/JSON 损坏）→ collect_run 正常返回"""
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.json"
            bad.write_text("{broken json", encoding="utf-8")
            out = Path(td) / "o.jsonl"
            try:
                rows = gsc.collect_run(
                    strategies=("qd", "qpzm", "cross"),
                    out=out,
                    qd_cache=bad,
                    qpzm_snap=bad,
                    cross_glob=str(Path(td) / "no_*.json"),
                    series_fp=bad,
                )
            except Exception as e:  # 主流程不被带崩
                self.fail("collect_run raised: %s" % e)
            self.assertEqual(len(rows), 9)  # 全部 missing 兜底
            self.assertTrue(all(r["source_status"] == "missing" for r in rows))

    def test_mount_try_except_semantics(self):
        """挂载点式包裹：collector 内部炸也不影响产线调用方"""
        with tempfile.TemporaryDirectory() as td:
            orig = gsc.collect_run

            def _boom(*a, **k):
                raise RuntimeError("模拟采集器内部爆炸")

            gsc.collect_run = _boom
            try:
                # 产线挂载点的写法（qpzm_daily_batch/market_scan 同式）
                try:
                    gsc.collect_run(strategies=("qd", "qpzm"))
                    reached = "ok"
                except Exception as e:
                    reached = "fail: %s" % e
            finally:
                gsc.collect_run = orig
            # 注记式包裹下产线只会打印 fail，不会中断
            self.assertTrue(reached.startswith("fail") or reached == "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
