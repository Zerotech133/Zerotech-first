# -*- coding: utf-8 -*-
"""[20261021_wo-qt-078] 许褚营·双池触发监控台 /pool-watch 渲染层单元测试。

覆盖（原型 v1 拍板五默认 + 容错）：
· 监控集默认只出 T1/T2 且买入源票（口径委托 pool_watch.pick_entry，
  与触发器 load_watchlist 同口径）；T3/无买入源进「未纳监控」折叠区
· 回踩线/突破线 = entry_px × PULL_FACTOR/BREAK_FACTOR（复用常量）
· 事件流：signal_id/方向标签/reg_flag_only 徽标/影子期·仅落池
· 今日触发计数卡：只数 triggered_at 为今日的事件
· 涨红跌绿（class up/down）
· 影子期红线常驻（不入 combo 文案 + 阈值数字）
· 容错：picks 缺失/坏 JSON → 空态照常出页；events 缺失/坏行 → 不炸
· write_html 落盘

纪律：不触网、不读真实产物——pool_path/pool_jsonl 全部注入 tmp_path；
零真实行情；不 import 消费端/engine（影子期铁律）。

运行：仓根 pytest quant_backtest/tests/test_pool_watch_page.py -v
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))        # quant_backtest/tests
QBT = os.path.dirname(HERE)                              # quant_backtest
sys.path.insert(0, HERE)
sys.path.insert(0, QBT)

import pool_watch          # noqa: E402
import pool_watch_page     # noqa: E402

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 21, 10, 30, 0, tzinfo=CST)
TODAY = "2026-10-21"


def _buy_src(entry_px, reg="flat", dt="2026-10-19", tag="1买"):
    return {"s": "chanlun", "tag": tag, "dt": dt, "entry_px": entry_px,
            "reg": reg}


def _stock(code, name, tier, sources, close=13.07, pct=1.24):
    return {"code": code, "name": name, "tier": tier, "sources": sources,
            "close": close, "pct_chg": pct, "n_hit": 1}


def _picks(stocks):
    return {"generated_at": "2026-10-21 06:00", "scan_run_date": "20261021",
            "counts": {"total": len(stocks)}, "stocks": stocks}


def _event(code, name, direction, trig, entry, *, date=TODAY,
           reg="flat", rfo=False, n=1):
    return {
        "signal_id": "todays_picks.%s.%s.%s.r%d" % (
            code, direction, date.replace("-", ""), n),
        "kind": "trigger", "stock": code, "name": name, "action": "BUY",
        "rule_id": "pool_watch_%s" % direction, "rule_version": "1",
        "triggered_at": "%sT09:47:00" % date,
        "evidence": {"trigger_price": trig, "entry_px": entry,
                     "direction": direction, "reg": reg,
                     "reg_flag_only": rfo},
        "direction": direction, "reg_flag_only": rfo,
    }


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pwp78_")
        self.picks_p = os.path.join(self.tmp, "todays_picks.json")
        self.pool_p = os.path.join(self.tmp, "signal_pool.jsonl")

    def write_picks(self, obj):
        with open(self.picks_p, "w", encoding="utf-8") as f:
            if isinstance(obj, str):
                f.write(obj)                    # 坏 JSON 用
            else:
                json.dump(obj, f, ensure_ascii=False)

    def write_pool(self, events):
        with open(self.pool_p, "w", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")


class TestWatchlistRender(_Tmp):
    """监控集口径 + 触发线数值（委托 pool_watch 常量）。"""

    def test_default_watch_rows_and_lines(self):
        self.write_picks(_picks([
            _stock("002007.SZ", "华兰生物", "T2", [_buy_src(13.07)]),
            _stock("603160.SH", "汇顶科技", "T1", [_buy_src(48.32, "up")]),
        ]))
        html = pool_watch_page.render_html(
            pool_watch_page.load_picks(self.picks_p), [], now=NOW)
        self.assertIn("华兰生物", html)
        self.assertIn("汇顶科技", html)
        # 触发线 = entry_px × 复用常量（与触发器零漂移）
        self.assertIn("%.2f" % (13.07 * pool_watch.PULL_FACTOR), html)
        self.assertIn("%.2f" % (13.07 * pool_watch.BREAK_FACTOR), html)

    def test_t3_and_sell_only_excluded_from_watch(self):
        self.write_picks(_picks([
            _stock("002007.SZ", "华兰生物", "T2", [_buy_src(13.07)]),
            _stock("111111.SZ", "纯T3票", "T3", [_buy_src(5.00)]),
            _stock("222222.SZ", "只卖票", "T2", [_buy_src(8.00, tag="2卖")]),
        ]))
        html = pool_watch_page.render_html(
            pool_watch_page.load_picks(self.picks_p), [], now=NOW)
        # 默认监控集只含华兰生物；另两只进未纳监控折叠区（诚实列示）
        self.assertIn("纯T3票", html)
        self.assertIn("只卖票", html)
        self.assertIn("未纳监控", html)
        wrow = html.split("参考买 entry_px")[1].split("</table>")[0]
        self.assertIn("华兰生物", wrow)
        self.assertNotIn("纯T3票", wrow)
        self.assertNotIn("只卖票", wrow)
        self.assertIn("展开未纳监控 2 只", html)


class TestEventsRender(_Tmp):
    """事件流展示 + 今日计数 + reg_flag_only。"""

    def test_events_rows_and_labels(self):
        evs = [_event("002434.SZ", "万里扬", "pull", 5.87, 5.94,
                      reg="down", rfo=False),
               _event("002007.SZ", "华兰生物", "break", 13.48, 13.07,
                      rfo=True, n=2)]
        self.write_pool(evs)
        html = pool_watch_page.render_html(
            pool_watch_page.load_picks(self.picks_p) if
            os.path.exists(self.picks_p) else {},
            pool_watch_page.signal_pool.read_events(path=self.pool_p),
            now=NOW)
        self.assertIn(evs[0]["signal_id"], html)
        self.assertIn("回踩", html)
        self.assertIn("突破", html)
        self.assertIn("reg_flag_only", html)
        self.assertIn("影子期·仅落池", html)
        self.assertIn("reg=down", html)

    def test_today_count_only_counts_today(self):
        self.write_pool([
            _event("002434.SZ", "万里扬", "pull", 5.87, 5.94),
            _event("002007.SZ", "华兰生物", "break", 13.48, 13.07,
                   date="2026-10-20"),                 # 昨日旧事件
        ])
        html = pool_watch_page.render_html(
            {}, pool_watch_page.signal_pool.read_events(path=self.pool_p),
            now=NOW)
        # 今日卡=1，累计=2
        self.assertIn("<div class=\"v\" style=\"color:var(--accent)\">1 ", html)
        self.assertIn("累计落池 2 条", html)

    def test_hit_status_marked(self):
        self.write_picks(_picks([
            _stock("002434.SZ", "万里扬", "T2", [_buy_src(5.94)]),
        ]))
        self.write_pool([_event("002434.SZ", "万里扬", "pull", 5.87, 5.94)])
        html = pool_watch_page.render_html(
            pool_watch_page.load_picks(self.picks_p),
            pool_watch_page.signal_pool.read_events(path=self.pool_p),
            now=NOW)
        self.assertIn("今日已触发", html)


class TestColorsAndBanner(_Tmp):
    """涨红跌绿 + 影子期红线常驻。"""

    def test_updown_colors(self):
        self.write_picks(_picks([
            _stock("002007.SZ", "华兰生物", "T2", [_buy_src(13.07)],
                   pct=1.24),
            _stock("603230.SH", "内蒙新华", "T2", [_buy_src(12.77)],
                   pct=-6.92),
        ]))
        html = pool_watch_page.render_html(
            pool_watch_page.load_picks(self.picks_p), [], now=NOW)
        self.assertIn('class="up">+1.24%', html)
        self.assertIn('class="down">-6.92%', html)

    def test_shadow_banner_persistent(self):
        html = pool_watch_page.render_html({}, [], now=NOW)
        self.assertIn("影子期红线（常驻）", html)
        self.assertIn("不进 combo", html)
        self.assertIn("entry_px×0.99", html)
        self.assertIn("entry_px×1.03", html)
        self.assertIn("/bt-signals", html)


class TestFaultTolerance(_Tmp):
    """容错：picks 缺失/坏 JSON；events 缺失/坏行。"""

    def test_missing_picks_graceful(self):
        html = pool_watch_page.render_html(
            pool_watch_page.load_picks(self.picks_p), [], now=NOW)
        self.assertIn("选票池数据不可用", html)
        self.assertIn("</html>", html)               # 页面照常出

    def test_bad_picks_json_graceful(self):
        self.write_picks("{oops not json")
        html = pool_watch_page.render_html(
            pool_watch_page.load_picks(self.picks_p), [], now=NOW)
        self.assertIn("选票池数据不可用", html)

    def test_missing_events_graceful(self):
        html = pool_watch_page.render_html(
            _picks([_stock("002007.SZ", "华兰生物", "T2",
                           [_buy_src(13.07)])]),
            pool_watch_page.signal_pool.read_events(path=self.pool_p),
            now=NOW)
        self.assertIn("暂无触发事件", html)

    def test_bad_jsonl_lines_skipped(self):
        self.write_pool([_event("002007.SZ", "华兰生物", "pull",
                                12.94, 13.07)])
        with open(self.pool_p, "a", encoding="utf-8") as f:
            f.write("{broken line\n")                # 坏行
            f.write("\n")                            # 空行
        evs = pool_watch_page.signal_pool.read_events(path=self.pool_p)
        self.assertEqual(len(evs), 1)
        html = pool_watch_page.render_html({}, evs, now=NOW)
        self.assertIn("华兰生物", html)


class TestWriteHtml(_Tmp):
    """write_html 落盘（全注入 tmp，不触真实 reports/）。"""

    def test_write_html_writes_file(self):
        self.write_picks(_picks([
            _stock("002007.SZ", "华兰生物", "T2", [_buy_src(13.07)]),
        ]))
        self.write_pool([_event("002007.SZ", "华兰生物", "break",
                                13.48, 13.07)])
        out = os.path.join(self.tmp, "pool_watch.html")
        got = pool_watch_page.write_html(now=NOW, pool_path=self.picks_p,
                                         pool_jsonl=self.pool_p,
                                         out_path=out)
        self.assertEqual(got, out)
        with open(out, encoding="utf-8") as f:
            body = f.read()
        self.assertIn("</html>", body)
        self.assertIn("华兰生物", body)
        self.assertIn("todays_picks.002007.SZ.break.20261021.r1",
                      body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
