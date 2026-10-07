# -*- coding: utf-8 -*-
"""行业下钻 + 涨停图形化 单元测试。

覆盖：
1. money_effect 数据层：lu_all 全量 / top 截断5 / broke_all 炸板清单 /
   ladder 连板天梯
2. premarket.write_page 渲染：天梯条形图 / details 全量涨停+炸板 /
   行业名下钻链接
3. afterhours.write_page 渲染：行业资金流 + 行业涨跌榜下钻链接
4. portal /bt/ind_drill 页：全行业索引 / 成分涨跌全量(涨红跌绿) /
   主力资金个股明细 / 未知行业容错

纪律：datamart/_closed_trade_days 全 mock（fake DataFrame），渲染走
真实 write_page（REPORTS 落 tmp），portal Handler 用 object.__new__
绕过 socket，零真实产物。
"""
import os
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from unittest import mock
from urllib.parse import quote

import pandas as pd

# 预导入 parquet 引擎扩展类型：import portal_api 会在模块体里启动后台
# freshness 预热线程（真实 datamart.read → read_parquet），其懒加载若与
# sys.modules 补丁窗口竞态，模块被窗口快照恢复逐出后重导 → pyarrow
# 扩展类型二次注册（ArrowKeyError: pandas.period already defined）。
# 收集期一次性注册后，任何窗口/线程都只会命中缓存。
import pandas.core.arrays.arrow.extension_types  # noqa: F401

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import premarket as pm  # noqa: E402

_MISSING = object()


@contextmanager
def _fake_modules(**mods):
    """外科式 sys.modules 替换：只动指定键。
    不用 mock.patch.dict（它会 clear 整个 sys.modules 再恢复快照，
    窗口内其他线程新导入的模块会被误逐出）。"""
    saved = {k: sys.modules.get(k, _MISSING) for k in mods}
    sys.modules.update(mods)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is _MISSING:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


# ── fake 数据仓 ────────────────────────────────────────────
def _fake_dm(daily=None, sb=None, mf=None, lld=None, bb=None, tl=None,
             ti=None):
    store = {"daily": daily, "stock_basic": sb, "moneyflow": mf,
             "limit_list_d": lld, "bak_basic": bb, "ts_top_list": tl,
             "ts_top_inst": ti}

    def _read(table, **kw):
        df = store.get(table)
        if df is None:
            return None
        df = df.copy()
        codes = kw.get("codes")
        if codes and "ts_code" in df.columns:
            df = df[df["ts_code"].isin(codes)]
        return df
    return types.SimpleNamespace(read=_read)


# ≥6 天：money_effect 新股剔除窗口取 win[-6]，<6 天会 IndexError
_MO_DAYS = ["20260918", "20260921", "20260922", "20260924",
            "20260925", "20260928", "20260929", "20260930"]


def _mo_daily():
    """末 2 交易日小样本：A 两连板实体 / B 首板T字 / F 首板一字 /
    C 昨涨停今炸板 / D 下跌 / E 跌停。open/low/vol 供涨停方式与
    换手率/量比判定。"""
    cols = ["ts_code", "trade_date", "close", "high", "open", "low",
            "pre_close", "pct_chg", "amount", "vol"]
    return pd.DataFrame([
        # 0929（d1）
        ("600001.SH", "20260929", 11.0, 11.0, 10.8, 10.5, 10.0, 10.0,
         2_000_000, 60_000),
        ("600005.SH", "20260929", 5.00, 5.00, 4.95, 4.90, 4.90, 2.0,
         100_000, 8_000),
        ("600009.SH", "20260929", 8.80, 8.80, 8.40, 8.30, 8.00, 10.0,
         500_000, 30_000),
        ("600020.SH", "20260929", 10.0, 10.0, 9.95, 9.90, 9.90, 1.0,
         100_000, 9_000),
        # 0930（d0）
        # A：open 11.3 未触板 → 实体板
        ("600001.SH", "20260930", 12.1, 12.1, 11.3, 11.1, 11.0, 10.0,
         3_000_000, 88_000),
        # B：open 6.05 触板、low 5.60 盘中开 → T字板
        ("600005.SH", "20260930", 6.05, 6.05, 6.05, 5.60, 5.50, 10.0,
         1_500_000, 30_000),
        # C：high +10.2% 触板、收盘 +6% → 炸板
        ("600009.SH", "20260930", 9.33, 9.70, 9.00, 8.90, 8.80, 6.0,
         800_000, 40_000),
        ("600012.SH", "20260930", 4.85, 4.90, 4.95, 4.80, 5.00, -3.0,
         300_000, 20_000),
        # E：收盘 -10% → 跌停
        ("600020.SH", "20260930", 9.00, 9.90, 9.50, 9.00, 10.0, -10.0,
         300_000, 40_000),
        # F：open=low=close 全天顶死 → 一字板（bak_basic 未收录）
        ("600030.SH", "20260930", 11.0, 11.0, 11.0, 11.0, 10.0, 10.0,
         500_000, 5_000),
    ], columns=cols)


def _mo_sb():
    return pd.DataFrame([
        ("600001.SH", "票A", "20200101"),
        ("600005.SH", "票B", "20200101"),
        ("600009.SH", "票C", "20200101"),
        ("600012.SH", "票D", "20200101"),
        ("600020.SH", "票E", "20200101"),
        ("600030.SH", "票F", "20200101"),
    ], columns=["ts_code", "name", "list_date"])


def _mo_lld():
    """limit_list_d：板块（B/C/D 未收录 → ind=None 容错）。"""
    return pd.DataFrame([
        ("600001.SH", "半导体"),
        ("600020.SH", "房地产"),
    ], columns=["ts_code", "industry"])


def _mo_bb():
    """bak_basic：股本为亿股口径（总/流通）。"""
    return pd.DataFrame([
        ("600001.SH", 10.0, 8.0),
        ("600005.SH", 2.0, 1.5),
        ("600020.SH", 0.5, 0.4),
    ], columns=["ts_code", "total_share", "float_share"])


def _mo_tl():
    """ts_top_list：龙虎榜（net_amount 元；B/C/D 未上榜）。"""
    return pd.DataFrame([
        ("600001.SH", 100_000_000.0, "日涨幅偏离值达7%"),
        ("600020.SH", -50_000_000.0, "日跌幅偏离值达7%"),
    ], columns=["ts_code", "net_amount", "reason"])


class TestMoneyEffectFullLists(unittest.TestCase):
    """money_effect：lu_all/broke_all/ladder/top 四路输出。"""

    def _run(self, with_dims=True):
        dm = _fake_dm(
            daily=_mo_daily(), sb=_mo_sb(),
            lld=_mo_lld() if with_dims else None,
            bb=_mo_bb() if with_dims else None,
            tl=_mo_tl() if with_dims else None)
        with mock.patch.object(pm, "_closed_trade_days",
                               return_value=list(_MO_DAYS)), \
             _fake_modules(datamart=dm):
            return pm.money_effect()

    def test_lu_all_full_not_capped(self):
        """lu_all 全量 3 只（A/B/F），与 top 一致（不足 5 不截断）。"""
        out = self._run()
        self.assertEqual(out.get("limit_up"), 3)
        codes = {t["code"] for t in out["lu_all"]}
        self.assertEqual(codes,
                         {"600001.SH", "600005.SH", "600030.SH"})

    def test_lu_all_sorted_by_amount(self):
        out = self._run()
        self.assertEqual([t["code"] for t in out["lu_all"]],
                         ["600001.SH", "600005.SH", "600030.SH"])  # 30>15>5亿

    def test_top_capped_at_5(self):
        """top 仍为快览口径（≤5），lu_all 才是全量。"""
        out = self._run()
        self.assertLessEqual(len(out["top"]), 5)
        self.assertEqual(out["top"][0]["code"], "600001.SH")

    def test_broke_all_contains_touch_not_close(self):
        """炸板=盘中触板未收封：C 在列，A/B（收盘封住）不在。"""
        out = self._run()
        codes = [t["code"] for t in out.get("broke_all", [])]
        self.assertEqual(codes, ["600009.SH"])
        self.assertEqual(out["broke_n"], 1)

    def test_ladder_streaks(self):
        """A 两天连续涨停 → 2 板层；B 首板不进天梯。"""
        out = self._run()
        lad = out.get("ladder", [])
        self.assertEqual(len(lad), 1)
        self.assertEqual((lad[0]["k"], lad[0]["n"]), (2, 1))
        self.assertEqual(lad[0]["top"][0]["code"], "600001.SH")
        # lu_all 里 streak 标注
        a = next(t for t in out["lu_all"] if t["code"] == "600001.SH")
        b = next(t for t in out["lu_all"] if t["code"] == "600005.SH")
        self.assertEqual((a["streak"], b["streak"]), (2, 1))

    def test_ylu_and_promote(self):
        """昨涨停（0929：A、C）今表现均值 (10.0+6.0)/2；晋级率 A→1/2。"""
        out = self._run()
        self.assertEqual(out.get("ylu_avg"), 8.0)
        self.assertEqual(out.get("promote_rate"), 50.0)

    def test_lu_all_dims(self):
        """多维：A 板块半导体 / 总市值 121亿（10亿股×12.1）/
        龙虎榜净买 1亿；B 未上榜无板块 → None 容错。"""
        out = self._run()
        a = next(t for t in out["lu_all"] if t["code"] == "600001.SH")
        self.assertEqual(a["ind"], "半导体")
        self.assertEqual(a["mv"], 121.0)          # 10.0 亿股 × 12.1 元
        self.assertEqual(a["cmv"], 96.8)          # 8.0 × 12.1
        self.assertEqual(a["lhb"]["net_yi"], 1.0)
        self.assertEqual(a["lhb"]["reason"], "日涨幅偏离值达7%")
        b = next(t for t in out["lu_all"] if t["code"] == "600005.SH")
        self.assertIsNone(b["lhb"])
        self.assertIsNone(b["ind"])

    def test_ld_all_with_dims(self):
        """跌停清单：E 收盘 -10% 在列；含板块/市值/榜净卖/换手量比。"""
        out = self._run()
        self.assertEqual(out.get("limit_dn"), 1)
        self.assertEqual(len(out["ld_all"]), 1)
        e = out["ld_all"][0]
        self.assertEqual((e["code"], e["name"]), ("600020.SH", "票E"))
        self.assertEqual(e["pct"], -10.0)
        self.assertEqual(e["amt_yi"], 3.0)
        self.assertEqual(e["ind"], "房地产")
        self.assertEqual(e["mv"], 4.5)            # 0.5 亿股 × 9.0 元
        self.assertEqual(e["lhb"]["net_yi"], -0.5)
        self.assertEqual(e["turn"], 10.0)         # 40000手/0.4亿股=10%
        self.assertEqual(e["vr"], 3.0)            # 30万/10万

    def test_style_classification(self):
        """涨停方式三分：A 实体（开未触板）/ B T字（开触板盘中开）
        / F 一字（开低均顶死）。"""
        out = self._run()
        st = {t["code"]: t.get("style") for t in out["lu_all"]}
        self.assertEqual(st["600001.SH"], "实体")
        self.assertEqual(st["600005.SH"], "T字")
        self.assertEqual(st["600030.SH"], "一字")

    def test_turnover_and_volume_ratio(self):
        """换手率=vol手/(流通亿股×1e4)；量比=当日额/前5日均额；
        F 无股本/无前5日 → None 容错（新股常见）。"""
        out = self._run()
        d = {t["code"]: t for t in out["lu_all"]}
        self.assertEqual(d["600001.SH"]["turn"], 1.1)   # 88000/8.0/1e4
        self.assertEqual(d["600001.SH"]["vr"], 1.5)     # 300万/200万
        self.assertEqual(d["600005.SH"]["turn"], 2.0)   # 30000/1.5/1e4
        self.assertIsNone(d["600030.SH"]["turn"])       # bak_basic 未收录
        self.assertIsNone(d["600030.SH"]["vr"])         # 无前5日成交

    def test_broke_all_with_dims(self):
        """炸板多维：C 换手/量比带维度（无股本 turn=None，
        vr=80万/50万），不标涨停方式。"""
        out = self._run()
        c = out["broke_all"][0]
        self.assertEqual((c["code"], c["name"]), ("600009.SH", "票C"))
        self.assertEqual(c["vr"], 1.6)
        self.assertIsNone(c["turn"])
        self.assertNotIn("style", c)

    def test_dims_absent_tolerant(self):
        """三维数据源整表缺失 → 字段 None，不崩（表返回 None 路径）。"""
        out = self._run(with_dims=False)
        a = next(t for t in out["lu_all"] if t["code"] == "600001.SH")
        self.assertIsNone(a["ind"])
        self.assertIsNone(a["mv"])
        self.assertIsNone(a["lhb"])


# ── 渲染层 fixture（money_effect 输出形态） ─────────────────
def _mo_render():
    lu = [{"code": "6000%02d.SH" % i, "name": "票%d" % i,
           "streak": 2 if i < 3 else 1,
           "pct": 10.0, "amt_yi": 30.0 - i,
           "style": ("一字" if i == 1 else
                     ("T字" if i == 2 else "实体")),
           "turn": (1.3 if i == 1 else (4.6 if i == 2 else 9.9)),
           "vr": 1.8,
           "ind": "半导体" if i < 3 else "银行",
           "mv": 121.0 - i * 10, "cmv": 90.0 - i * 10,
           "lhb": ({"net_yi": 1.0, "reason": "日涨幅偏离值达7%"}
                   if i == 1 else None)} for i in range(1, 7)]
    return {
        "date": "20260930", "n_stock": 5000, "up": 3000, "dn": 1800,
        "flat": 200, "limit_up": 6, "limit_dn": 1, "max_streak": 2,
        "streak_name": "票1", "top": lu[:5], "lu_all": lu,
        "ladder": [{"k": 2, "n": 3,
                    "top": [{"code": "600001.SH", "name": "票1",
                             "pct": 10.0, "amt_yi": 30.0}]}],
        "broke_n": 2, "broke_rate": 25.0, "cm20_share": 16.7,
        "ld_all": [{"code": "600020.SH", "name": "跌停甲", "pct": -10.0,
                    "amt_yi": 6.6, "ind": "房地产", "mv": 44.5,
                    "cmv": 30.0, "turn": 8.8, "vr": 2.2,
                    "lhb": {"net_yi": -0.5, "reason": "日跌幅偏离值达7%"}}],
        "broke_all": [{"code": "600099.SH", "name": "炸板王",
                       "pct": 6.0, "amt_yi": 8.0, "ind": "光学光电",
                       "mv": 339.4, "cmv": 300.0, "turn": 6.4, "vr": 2.7,
                       "lhb": {"net_yi": -0.39, "reason": "日振幅值达15%"}},
                      {"code": "600098.SH", "name": "炸板乙",
                       "pct": 3.0, "amt_yi": 5.0}],
        "ylu_avg": 1.14, "ylu_med": 0.0, "promote_rate": 18.8,
        "amt_yi": 14489, "amt_pct": 2.0,
    }


_L4F = {"date": "20260930", "total_yi": 47.3, "n_ind": 111,
        "top": [{"ind": "银行", "yi": 47.3, "n": 42}],
        "bottom": [{"ind": "半导体", "yi": -12.0, "n": 80}]}


class TestPremarketRenderDrill(unittest.TestCase):
    """premarket.write_page：天梯条形 / details 全量 / 行业下钻链接。"""

    def _render(self):
        data = {"date": "20260930", "ts": "2026-10-05 09:00",
                "overall": 0, "verdict": "中性", "prob": 50, "vals": {},
                "alerts": [],
                "l1": {"rows": [], "score": 0, "note": ""},
                "l2": {"macro": [], "news": []},
                "l3": {}, "l4": [], "l4f": _L4F,
                "l5": {"money": _mo_render()}, "l6": {}}
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(pm, "REPORTS", td):
            pm.write_page(data, snap={})
            with open(os.path.join(td, "premarket.html"),
                      encoding="utf-8") as f:
                return f.read()

    def test_ladder_bar_chart(self):
        h = self._render()
        self.assertIn("连板天梯", h)
        self.assertIn("2板", h)                      # 层级标注
        self.assertIn("3家", h)                      # 家数
        self.assertIn("background:#f85149", h)       # A股红条形

    def test_details_full_limit_up(self):
        """details 收纳全量 6 只涨停（top 表只有 5 只）。"""
        h = self._render()
        self.assertIn("全部 6 只涨停", h)
        self.assertIn("点击展开全量清单", h)
        det = h.split("点击展开全量清单", 1)[1]
        det = det.split("</details>", 1)[0]
        lu_tbl = det.split("跌停明细", 1)[0]   # 涨停表在跌停/炸板表之前
        self.assertEqual(lu_tbl.count("/bt/stock?c="), 6)
        self.assertEqual(det.count("/bt/stock?c="), 9)   # 6涨+1跌+2炸
        self.assertIn("首板", lu_tbl)               # streak=1 标注
        self.assertIn("2 连板", lu_tbl)             # streak≥2 标注

    def test_limit_down_table(self):
        """跌停明细表：绿跌幅 + 板块/市值/龙虎榜多维 + summary 计数。"""
        h = self._render()
        self.assertIn("1 只跌停", h)                  # summary 计数
        blk = h.split("跌停明细", 1)[1].split("炸板明细", 1)[0]
        self.assertIn("跌停甲", blk)
        self.assertIn("class='avoid'>-10.00%</td>", blk)   # A股跌绿
        self.assertIn("房地产", blk)                  # 板块
        self.assertIn("44.5", blk)                    # 总市值(亿)
        self.assertIn("榜 -0.50亿", blk)              # 榜净卖（绿）
        self.assertIn("日跌幅偏离值达7%", blk)        # title 上榜原因

    def test_dim_columns_and_lhb(self):
        """涨停表多维列头 + 龙虎榜净买红（带 /bt/lhb 下钻链接）、
        未上榜 —。"""
        h = self._render()
        self.assertIn("板块</th>", h)
        self.assertIn("总市值(亿)</th>", h)
        self.assertIn("龙虎榜</th>", h)
        self.assertIn("半导体", h)
        self.assertIn(
            "class='buy' title='日涨幅偏离值达7%'>"
            "<a href='/bt/lhb?date=20260930'>榜 +1.00亿</a>", h)
        self.assertIn("class='dim' title=''>—</td>", h)   # 未上榜

    def test_style_column_three_states(self):
        """方式列三态配色：一字 buy / T字 avoid / 实体 dim。"""
        h = self._render()
        self.assertIn("<th>方式</th>", h)
        self.assertIn("<td class='buy'>一字</td>", h)
        self.assertIn("<td class='avoid'>T字</td>", h)
        self.assertIn("<td class='dim'>实体</td>", h)

    def test_turnover_volume_ratio_column(self):
        """换手/量比列：一字缩量 1.3% 与换手板 9.9% 对比；缺 —。"""
        h = self._render()
        self.assertIn("<th>换手/量比</th>", h)
        self.assertIn("1.3% / 1.8", h)     # 一字缩量秒板
        self.assertIn("9.9% / 1.8", h)     # 巨量换手
        self.assertIn("—% / —", h)         # 缺数据（炸板乙无股本/前5日）

    def test_no_name_duplication(self):
        """名称不再重复：代码列链接文本=代码，名称列=名称。"""
        h = self._render()
        self.assertIn("600001.SH</a></td><td>票1", h)
        self.assertNotRegex(h, r"票\d</a></td><td>票\d")

    def test_broke_detail_table(self):
        """炸板明细表：多维（板块/市值/龙虎榜）+ 换手量比列。"""
        h = self._render()
        self.assertIn("炸板明细", h)
        self.assertIn("炸板王", h)
        self.assertIn("2 只炸板", h)                  # summary 计数
        blk = h.split("炸板明细", 1)[1].split("</details>", 1)[0]
        self.assertIn("光学光电", blk)                # 板块
        self.assertIn("339.4", blk)                   # 总市值(亿)
        self.assertIn("榜 -0.39亿", blk)              # 龙虎榜净卖
        self.assertIn("6.4% / 2.7", blk)              # 换手/量比

    def test_industry_drill_links(self):
        """层4 行业名（含净流出侧）都变下钻链接，带报告日。"""
        h = self._render()
        for ind in ("银行", "半导体"):
            self.assertIn(
                "href='/bt/ind_drill?ind=%s&date=20260930'" % ind, h)


class TestAfterhoursRenderLinks(unittest.TestCase):
    """afterhours.write_page：行业资金流 + 行业涨跌榜下钻链接。"""

    def _render(self):
        import afterhours as ah
        d = {"ts": "2026-10-01 09:10",
             "ic": {"date": "20260930", "rows": [
                 {"name": "上证指数", "close": 3330, "pct": 0.31,
                  "amp": 0.8, "pos": 70, "amt_yi": 6000, "amt_pct": 2.0}]},
             "sh": {"name": "上证指数", "close": 3330, "pct": 0.31},
             "money": _mo_render(), "l4f": _L4F,
             "ip": {"date": "20260930", "n_ind": 111,
                    "top": [{"ind": "银行", "med": 1.5, "up_r": 95,
                             "n": 42}],
                    "bottom": [{"ind": "半导体", "med": -2.0, "up_r": 20,
                                "n": 80}]},
             "review": {"pre_ts": "t", "verdict": "中性", "prob": 47,
                        "sh_pct": 0.31, "hit": True}}
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(pm, "REPORTS", td), \
             mock.patch.object(ah, "REPORTS", td):
            ah.write_page(d)
            with open(os.path.join(td, "afterhours.html"),
                      encoding="utf-8") as f:
                return f.read()

    def test_flow_and_perf_drill_links(self):
        """行业资金流 + 行业涨跌榜共 4 处行业链接。"""
        h = self._render()
        self.assertEqual(h.count("/bt/ind_drill?ind="), 4)
        for ind in ("银行", "半导体"):
            self.assertEqual(h.count(
                "href='/bt/ind_drill?ind=%s&date=20260930'" % ind), 2)

    def test_ladder_rendered(self):
        self.assertIn("2 板", self._render())


class TestIndDrillPage(unittest.TestCase):
    """portal /bt/ind_drill：索引页 / 行业页 / 容错。"""

    @classmethod
    def setUpClass(cls):
        pdir = os.path.join(os.path.dirname(HERE), "portal")
        sys.path.insert(0, pdir)
        # 跳过 portal_api 模块体的 freshness 预热线程（真实 datamart.read
        # ~100s，测试零真实产物；且其后台懒加载会与 sys.modules 补丁
        # 窗口竞态触发 pyarrow 扩展类型二次注册）。
        os.environ["PORTAL_NO_WARMUP"] = "1"
        import portal_api as pa
        cls.pa = pa

    def _page(self, qs=""):
        sb = pd.DataFrame([
            ("600001.SH", "工商", "银行"),
            ("600002.SH", "建设", "银行"),
            ("300001.SZ", "半芯", "半导体"),
        ], columns=["ts_code", "name", "industry"])
        daily = pd.DataFrame([
            ("600001.SH", "20260930", 10.2, 2.5, 3_000_000),
            ("600002.SH", "20260930", 9.8, -1.2, 1_000_000),
            ("300001.SZ", "20260930", 21.6, 8.0, 2_000_000),
        ], columns=["ts_code", "trade_date", "close", "pct_chg",
                    "amount"])
        mf = pd.DataFrame([
            ("600001.SH", 300_000),      # 万元 → +30 亿
            ("600002.SH", -50_000),      # → -5 亿
        ], columns=["ts_code", "net_mf_amount"])
        dm = _fake_dm(daily=daily, sb=sb, mf=mf)
        fpm = types.SimpleNamespace(
            _closed_trade_days=lambda: ["20260930"])
        captured = []
        h = object.__new__(self.pa.Handler)
        h._html = captured.append
        with _fake_modules(datamart=dm, premarket=fpm):
            h._bt_ind_drill_page(qs)
        self.assertEqual(len(captured), 1)
        return captured[0]

    def test_index_page_without_ind(self):
        """无 ind：全行业索引，每个行业可点。"""
        h = self._page("")
        self.assertIn("全 2 个行业", h)
        self.assertIn("href='/bt/ind_drill?ind=银行'", h)
        self.assertIn("href='/bt/ind_drill?ind=半导体'", h)

    def test_industry_page_full_members(self):
        """银行页：2 只成分全量、涨红跌绿、成交额亿。"""
        h = self._page("ind=" + quote("银行"))
        self.assertIn("2 只成分股", h)
        self.assertIn("color:#f85149'><b>+2.50%</b>", h)   # 涨红
        self.assertIn("color:#3fb950'><b>-1.20%</b>", h)   # 跌绿
        self.assertIn("30.0 亿", h)                          # 千元→亿
        self.assertIn("涨 1</b> · 平 0 · <b style='color:#3fb950'>跌 1", h)
        self.assertIn("c=600001", h)                         # 个股下钻

    def test_industry_page_moneyflow_detail(self):
        """主力资金个股明细全量：正红负绿 + 条形 + 合计。"""
        h = self._page("ind=" + quote("银行"))
        self.assertIn("主力资金 · 个股明细", h)
        self.assertIn("+30.00 亿", h)
        self.assertIn("-5.00 亿", h)
        self.assertIn("+25.00 亿", h)                         # 行业合计
        self.assertIn("覆盖 2/2 只", h)

    def test_unknown_industry_fallback(self):
        h = self._page("ind=" + quote("不存在的行业"))
        self.assertIn("未找到行业", h)

    def test_date_param_validated(self):
        """date 缺省取最近收盘日（fake 20260930）。"""
        h = self._page("ind=" + quote("银行"))
        self.assertIn("（20260930 · 2 只成分股）", h)


class TestLhbPage(unittest.TestCase):
    """portal /bt/lhb：龙虎榜下钻页（当日全量/频次/扎堆/高频）。"""

    @classmethod
    def setUpClass(cls):
        pdir = os.path.join(os.path.dirname(HERE), "portal")
        sys.path.insert(0, pdir)
        os.environ["PORTAL_NO_WARMUP"] = "1"   # 同 TestIndDrillPage
        import portal_api as pa
        cls.pa = pa

    @staticmethod
    def _tl():
        """ts_top_list 全历史：A 三日上榜（0928/0929/0930，0930 双
        reason 同股）+ B/C 各一次；单位元。"""
        return pd.DataFrame([
            ("600001.SH", "20260928", "票A", 10.0, 30_000_000.0,
             20_000_000.0, 10_000_000.0, "日涨幅偏离值达7%"),
            ("600001.SH", "20260929", "票A", 10.0, 30_000_000.0,
             20_000_000.0, 10_000_000.0, "日涨幅偏离值达7%"),
            ("600001.SH", "20260930", "票A", 9.98, 100_000_000.0,
             60_000_000.0, 40_000_000.0, "日涨幅偏离值达7%"),
            ("600001.SH", "20260930", "票A", 9.98, 50_000_000.0,
             30_000_000.0, 20_000_000.0, "换手率达20%"),
            ("600005.SH", "20260930", "票B", -5.0, -80_000_000.0,
             10_000_000.0, 90_000_000.0, "日跌幅偏离值达7%"),
            ("600020.SH", "20260930", "票E", -9.9, -50_000_000.0,
             10_000_000.0, 60_000_000.0, "日振幅值达15%"),
        ], columns=["ts_code", "trade_date", "name", "pct_change",
                    "net_amount", "l_buy", "l_sell", "reason"])

    @staticmethod
    def _inst():
        """ts_top_inst 当日席位（buy/sell/net_buy 单位元）：机构/股通
        供机构列聚合，全量供当日活跃坐席榜。"""
        return pd.DataFrame([
            ("600001.SH", "机构专用", 80_000_000.0, 30_000_000.0,
             50_000_000.0),
            ("600001.SH", "沪股通专用", 30_000_000.0, 20_000_000.0,
             10_000_000.0),
            ("600005.SH", "某某游资", 20_000_000.0, 40_000_000.0,
             -20_000_000.0),   # 非机构不计机构列
        ], columns=["ts_code", "exalter", "buy", "sell", "net_buy"])

    @staticmethod
    def _bb():
        return pd.DataFrame([
            ("600001.SH", "半导体"), ("600005.SH", "半导体"),
            ("600020.SH", "房地产"),
        ], columns=["ts_code", "industry"])

    def _page(self, qs="", tl="default"):
        if tl == "default":
            tl = self._tl()
        dm = _fake_dm(tl=tl, ti=self._inst(), sb=self._bb())
        captured = []
        h = object.__new__(self.pa.Handler)
        h._html = captured.append
        with _fake_modules(datamart=dm):
            h._bt_lhb_page(qs)
        self.assertEqual(len(captured), 1)
        return captured[0]

    def test_summary_and_full_table(self):
        """当日 3 只（A 双 reason 合并 1 行）、净额合计 +0.20 亿、
        A 买卖净拆分与排序（净额降序 A/E/B）。"""
        h = self._page()
        self.assertIn("上榜 <b class='buy'>3 只</b>", h)
        self.assertIn("榜内净额合计 <b style='color:#f85149'>"
                      "+0.20 亿</b>", h)
        self.assertIn("票A", h)
        # A 合并行：买 0.90 卖 0.60 净 +1.50（元÷1e8）
        self.assertIn("<td class='dim'>0.90</td>", h)
        self.assertIn("<td class='dim'>0.60</td>", h)
        self.assertIn("+1.50</b></td>", h)
        # 净额降序：A(+1.5) → E(-0.5) → B(-0.8)
        self.assertLess(h.index("票A"), h.index("票E"))
        self.assertLess(h.index("票E"), h.index("票B"))

    def test_inst_and_freq_columns(self):
        """机构/股通净额聚合（仅 A +0.60 亿，B 游资不计）+ 近60日
        频次与上榜日期（A 3 次含 0929/0928）。"""
        h = self._page()
        self.assertIn("机构/股通席位覆盖 1 只", h)
        self.assertIn("+0.60", h)                       # A 机构净买
        self.assertIn(">3 次</td>", h)                  # A 近60日 3 次
        self.assertIn("20260930、20260929、20260928", h)

    def test_hot_industry_cluster(self):
        """板块扎堆：半导体 2 票（A/B 同板块）红条。"""
        h = self._page()
        self.assertIn("板块扎堆", h)
        self.assertIn("半导体", h.split("板块扎堆", 1)[1])
        self.assertIn("2 票", h)

    def test_high_freq_section(self):
        """高频上榜：A 3 次 ≥3 → 高频表出现。"""
        h = self._page()
        blk = h.split("高频上榜", 1)
        self.assertGreater(len(blk), 1)
        self.assertIn("600001", blk[1])

    def test_date_param(self):
        """date=20260929 → 当日仅 A 1 只。"""
        h = self._page("date=20260929")
        self.assertIn("上榜 <b class='buy'>1 只</b>", h)

    def test_empty_source_fail(self):
        """ts_top_list 缺失 → 容错提示不崩。"""
        h = self._page(tl=None)
        self.assertIn("无数据", h)

    def test_seat_links_in_lhb(self):
        """当日活跃坐席榜：坐席名可点击 → /bt/seat?ex=<urlencoded>。"""
        from urllib.parse import quote as _q
        h = self._page()
        self.assertIn("当日活跃坐席", h)
        self.assertIn("<a href='/bt/seat?ex=%s'" % _q("机构专用"), h)
        self.assertIn("<a href='/bt/seat?ex=%s'" % _q("某某游资"), h)
        # 净额绝对值排序：机构(+0.50) → 沪股通(+0.10) → 游资(-0.20)
        self.assertLess(h.index(_q("机构专用")), h.index(_q("某某游资")))


class TestSeatPage(unittest.TestCase):
    """portal /bt/seat：坐席下钻页（近60日上榜明细/常做票）。"""

    @classmethod
    def setUpClass(cls):
        pdir = os.path.join(os.path.dirname(HERE), "portal")
        sys.path.insert(0, pdir)
        os.environ["PORTAL_NO_WARMUP"] = "1"   # 同 TestIndDrillPage
        import portal_api as pa
        cls.pa = pa

    @staticmethod
    def _ti():
        """ts_top_inst 多日多坐席（单位元）：知春路 3 笔跨 2 日 2 票，
        深股通 1 笔。"""
        return pd.DataFrame([
            ("600001.SH", "20260929",
             "国泰海通证券股份有限公司北京知春路证券营业部",
             30_000_000.0, 5_000_000.0, 25_000_000.0),
            ("600001.SH", "20260930",
             "国泰海通证券股份有限公司北京知春路证券营业部",
             60_000_000.0, 10_000_000.0, 50_000_000.0),
            ("600005.SH", "20260930",
             "国泰海通证券股份有限公司北京知春路证券营业部",
             10_000_000.0, 40_000_000.0, -30_000_000.0),
            ("600001.SH", "20260930", "深股通专用",
             100_000_000.0, 60_000_000.0, 40_000_000.0),
        ], columns=["ts_code", "trade_date", "exalter", "buy", "sell",
                    "net_buy"])

    def _seat(self, qs="", ti="default"):
        if ti == "default":
            ti = self._ti()
        dm = _fake_dm(tl=TestLhbPage._tl(), ti=ti)
        captured = []
        h = object.__new__(self.pa.Handler)
        h._html = captured.append
        with _fake_modules(datamart=dm):
            h._bt_seat_page(qs)
        self.assertEqual(len(captured), 1)
        return captured[0]

    def test_detail_and_summary(self):
        """知春路：2 日 2 票，买 1.00/卖 0.55/净 +0.45 亿，明细
        日期降序（0930 在前）且带个股链接。"""
        from urllib.parse import quote as _q
        h = self._seat("ex=" + _q(
            "国泰海通证券股份有限公司北京知春路证券营业部"))
        self.assertIn("上榜 <b class='buy'>2 日</b> / "
                      "<b class='buy'>2 票</b>", h)
        self.assertIn("合计买 <b>1.00 亿</b>", h)
        self.assertIn("卖 <b>0.55 亿</b>", h)
        self.assertIn("+0.45 亿", h)
        # 日期降序：20260930 行在 20260929 前
        self.assertLess(h.index("20260930"), h.index("20260929"))
        # 同日净额降序：600001(+0.50) 在 600005(-0.30) 前
        d30 = h.index("20260930")
        self.assertLess(h.index("600001", d30), h.index("600005", d30))
        self.assertIn("<a href='/bt/stock?c=600001'>", h)
        # 票名来自 ts_top_list
        self.assertIn("票A", h)
        self.assertIn("票B", h)

    def test_regular_stocks(self):
        """常做票：知春路 × 600001 两日 → 节出现、2 次、累计净额
        +0.75 亿。"""
        from urllib.parse import quote as _q
        h = self._seat("ex=" + _q(
            "国泰海通证券股份有限公司北京知春路证券营业部"))
        blk = h.split("常做票", 1)
        self.assertGreater(len(blk), 1)
        self.assertIn("+0.75", blk[1])
        self.assertIn(">2</b>", blk[1])   # 次数 2

    def test_other_seat_isolated(self):
        """深股通：仅 1 行 1 票，不含知春路明细。"""
        from urllib.parse import quote as _q
        h = self._seat("ex=" + _q("深股通专用"))
        self.assertIn("上榜 <b class='buy'>1 日</b> / "
                      "<b class='buy'>1 票</b>", h)
        self.assertIn("+0.40 亿", h)
        self.assertNotIn("票B", h)

    def test_unknown_seat(self):
        """未知坐席 → 容错提示不崩。"""
        h = self._seat("ex=" + "notexist")
        self.assertIn("无上榜记录", h)

    def test_missing_param(self):
        """缺 ex 参数 → 提示从龙虎榜页进入。"""
        h = self._seat("")
        self.assertIn("缺少 ex 参数", h)


if __name__ == "__main__":
    unittest.main()
