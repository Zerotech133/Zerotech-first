# -*- coding: utf-8 -*-
"""郭嘉对账司核心单测（K-ORG-191，纯离线不触网不读真实库）。

覆盖：pct 计算 / T+1 选取 / 命中·未中·中性·pending·no_bar 判定 /
幂等与 rowid 复用防串账 / 滚动窗口小样本不出点 / 账本读写容错。
运行：python -X utf8 -m pytest news_collector/test_guojia_reconcile.py -q
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import guojia_reconcile as gr  # noqa: E402


def _bars(dates_closes):
    return gr._bars_from_closes(dates_closes)


class TestBars:
    def test_pct_and_order(self):
        bars = _bars(
            [("2026-10-13", "100"), ("2026-10-14", "101"), ("2026-10-15", "99")]
        )
        assert [b["date"] for b in bars] == ["2026-10-13", "2026-10-14", "2026-10-15"]
        assert bars[0]["pct"] is None
        assert bars[1]["pct"] == 1.0
        assert bars[2]["pct"] == -1.98

    def test_bad_close_filtered(self):
        bars = _bars([("2026-10-13", "100"), ("2026-10-14", ""), ("2026-10-15", "102")])
        assert len(bars) == 2 and bars[0]["pct"] is None


class TestT1:
    def test_strict_next(self):
        bars = _bars([("2026-10-13", "100"), ("2026-10-16", "101")])
        assert gr._t1_of(bars, "2026-10-13")["date"] == "2026-10-16"
        assert gr._t1_of(bars, "2026-10-14")["date"] == "2026-10-16"
        assert gr._t1_of(bars, "2026-10-16") is None
        assert gr._t1_of(bars, "2026-10-20") is None


def _sig(rowid, day, scope="cn", bias="bull", conf=0.9):
    return {
        "rowid": rowid,
        "day": day,
        "ts": day + " 10:00:00",
        "scope": scope,
        "bias": bias,
        "conf": conf,
        "event_type": None,
    }


class TestReconcile:
    def _setup(self):
        return {
            "cn": _bars(
                [
                    ("2026-10-13", "100"),
                    ("2026-10-14", "102"),
                    ("2026-10-15", "99"),
                    ("2026-10-16", "100.5"),
                ]
            )
        }

    def test_hit_miss_skip(self):
        bars = self._setup()
        sigs = [
            _sig(1, "2026-10-13", bias="bull"),  # T+1 +2% → hit
            _sig(2, "2026-10-13", bias="bear"),  # T+1 +2% → miss
            _sig(3, "2026-10-14", bias="bear"),  # T+1 -1.47% → hit
            _sig(4, "2026-10-14", bias="neutral"),  # skip
            _sig(5, "2026-10-16", bias="bull"),
        ]  # 无 T+1 → pending
        merged, pending = gr.reconcile(sigs, bars, {})
        assert merged[(1, "2026-10-13", "cn")]["verdict"] == "hit"
        assert merged[(2, "2026-10-13", "cn")]["verdict"] == "miss"
        assert merged[(3, "2026-10-14", "cn")]["verdict"] == "hit"
        assert merged[(4, "2026-10-14", "cn")]["verdict"] == "skip"
        assert (5, "2026-10-16", "cn") not in merged
        assert pending == 1

    def test_no_bar_grace(self):
        bars = {"cn": _bars([("2026-09-01", "100"), ("2026-10-16", "101")])}
        merged, pending = gr.reconcile(
            [_sig(9, "2026-08-01", bias="bull"), _sig(10, "2026-10-16", bias="bull")],
            bars,
            {},
        )
        assert merged[(9, "2026-08-01", "cn")]["verdict"] == "no_bar"
        assert (10, "2026-10-16", "cn") not in merged

    def test_idempotent_and_rowid_reuse(self):
        bars = self._setup()
        old = {
            (1, "2026-10-13", "cn"): gr._verdict(
                _sig(1, "2026-10-13"), "2026-10-14", 2.0, "hit"
            )
        }
        merged, _ = gr.reconcile([_sig(1, "2026-10-13", bias="bear")], bars, old)
        # 已对账不重算（bias 不同也保留原判定）
        assert merged[(1, "2026-10-13", "cn")]["verdict"] == "hit"
        # rowid 复用防串账：同 rowid 不同日 → 两笔并存（bear 对 +1.52% → miss）
        merged2, _ = gr.reconcile([_sig(1, "2026-10-15", bias="bear")], bars, merged)
        assert merged2[(1, "2026-10-13", "cn")]["verdict"] == "hit"
        assert merged2[(1, "2026-10-15", "cn")]["verdict"] == "miss"


class TestVerdictsIO:
    def test_roundtrip_and_bad_lines(self, tmp_path):
        p = str(tmp_path / "verdicts.jsonl")
        v1 = gr._verdict(_sig(1, "2026-10-13"), "2026-10-14", 2.0, "hit")
        gr.write_verdicts(p, {(1, "2026-10-13", "cn"): v1})
        assert gr.load_verdicts(p)[(1, "2026-10-13", "cn")]["verdict"] == "hit"
        with open(p, "a", encoding="utf-8") as f:
            f.write("{broken\n")
        assert len(gr.load_verdicts(p)) == 1

    def test_missing_file_empty(self, tmp_path):
        assert gr.load_verdicts(str(tmp_path / "nope.jsonl")) == {}


def _merged_for_agg():
    """cn: 10-13 两多一空(T+1+)、10-14 一空(T+1-)、10-15 两中性。"""
    mk = lambda rid, day, bias, pct, vd: dict(
        gr._verdict(_sig(rid, day, bias=bias), "nx", pct, vd)
    )
    vs = [
        mk(1, "2026-10-13", "bull", 2.0, "hit"),
        mk(2, "2026-10-13", "bull", 2.0, "hit"),
        mk(3, "2026-10-13", "bear", 2.0, "miss"),
        mk(4, "2026-10-14", "bear", -1.5, "hit"),
        mk(5, "2026-10-15", "neutral", 0.3, "skip"),
        mk(6, "2026-10-15", "neutral", 0.3, "skip"),
    ]
    return {(r, v["day"], "cn"): v for r, v in enumerate(vs)}


class TestAggregate:
    def test_rates_and_days(self):
        s = gr.aggregate(_merged_for_agg(), {}, {}, 180)
        cn = s["markets"]["cn"]
        assert (cn["hit"], cn["miss"], cn["neutral"]) == (3, 1, 2)
        assert cn["hit_rate"] == 0.75
        o = s["overall"]
        assert (o["n_hit"], o["n_miss"], o["n_reconciled"], o["hit_rate"]) == (
            3,
            1,
            4,
            0.75,
        )
        d13 = [d for d in cn["days"] if d["day"] == "2026-10-13"][0]
        assert d13["dir"] == "bull" and d13["verdict"] == "hit"
        d15 = [d for d in cn["days"] if d["day"] == "2026-10-15"][0]
        assert d15["dir"] == "none" and d15["verdict"] == "flat"

    def test_rolling_min_samples(self):
        s = gr.aggregate(_merged_for_agg(), {}, {}, 180)
        # 方向样本仅 4 个 < ROLLING_MIN=5 → 不出滚动点（小样本铁律）
        assert s["markets"]["cn"]["rolling"] == []

    def test_rolling_window_capped(self):
        vs = []
        rid = 0
        for i in range(1, 31):  # 30 天每日 1 样本，前 10 全 hit 后 20 全 miss
            vd = "hit" if i <= 10 else "miss"
            vs.append(
                dict(
                    gr._verdict(
                        _sig(rid, "2026-09-%02d" % i, bias="bull"), "nx", 1.0, vd
                    )
                )
            )
            rid += 1
        merged = {("r%d" % i, v["day"], "cn"): v for i, v in enumerate(vs)}
        s = gr.aggregate(merged, {}, {}, 180)
        roll = s["markets"]["cn"]["rolling"]
        assert len(roll) == 26  # 样本 i=4..29 → 26 点（窗口 5→20 封顶滑窗）
        assert roll[0] == {"day": "2026-09-05", "n": 5, "rate": 1.0}
        assert roll[-1]["rate"] == 0.0  # 末窗全 miss
        assert roll[10] == {"day": "2026-09-15", "n": 15, "rate": 0.6667}
        assert roll[15] == {"day": "2026-09-20", "n": 20, "rate": 0.5}


class TestEnrich:
    """K-ORG-193 账本富化：title 截断 + ents 解析（stocks 主源 ∪
    companies 兜底）+ 富化不改变对账数学。tmp sqlite 全 schema 建表，
    created_at 取当前时间避免用例时钟耦合。"""

    def _db(self, tmp_path, news_rows):
        import sqlite3

        p = tmp_path / "news.db"
        con = sqlite3.connect(p)
        # 列序与生产 news.db 实证一致（id..jev）
        con.execute(
            "CREATE TABLE news (id INTEGER PRIMARY KEY, source TEXT,"
            " ts TEXT, title TEXT, content TEXT, hit INTEGER, pushed"
            " INTEGER, digested INTEGER, created_at TEXT, stocks TEXT,"
            " jev TEXT)"
        )
        con.executemany(
            "INSERT INTO news (id, source, ts, title, content, hit,"
            " pushed, digested, created_at, stocks, jev)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            news_rows,
        )
        con.commit()
        con.close()
        return str(p)

    @staticmethod
    def _news(rid, title, stocks, jev_raw):
        created = gr.now_iso()[:19].replace("T", " ")
        return (
            rid,
            "sina",
            "2026-10-13 09:59:00",
            title,
            "正文",
            0,
            0,
            0,
            created,
            stocks,
            jev_raw,
        )

    @staticmethod
    def _jev(scope="cn", bias="bull", conf=0.9, companies=None):
        j = {"scope": scope, "bias": bias, "conf": conf, "event_type": None}
        if companies is not None:
            j["companies"] = companies
        return json.dumps(j, ensure_ascii=False)

    def _load(self, tmp_path, rows):
        sigs, _ = gr.load_signals(self._db(tmp_path, rows), 180)
        return sigs

    def test_title_truncated(self, tmp_path):
        sigs = self._load(tmp_path, [self._news(1, "涨" * 100, "[]", self._jev())])
        assert sigs[0]["title"] == "涨" * gr.TITLE_MAX

    def test_ents_dedup_order(self, tmp_path):
        sigs = self._load(
            tmp_path,
            [
                self._news(
                    1,
                    "标题",
                    '["特斯拉","宁德时代","特斯拉"]',
                    self._jev(companies=["特斯拉", "比亚迪"]),
                )
            ],
        )
        assert sigs[0]["ents"] == ["特斯拉", "宁德时代", "比亚迪"]

    def test_bad_stocks_json_fallback(self, tmp_path):
        sigs = self._load(
            tmp_path, [self._news(1, "标题", "特斯拉概念股", self._jev())]
        )
        assert sigs[0]["ents"] == ["特斯拉概念股"]

    def test_empty_and_none_stocks(self, tmp_path):
        sigs = self._load(
            tmp_path,
            [
                self._news(1, "a", "[]", self._jev()),
                self._news(2, "b", None, self._jev()),
            ],
        )
        assert sigs[0]["ents"] == [] and sigs[0]["title"] == "a"
        assert sigs[1]["ents"] == [] and sigs[1]["title"] == "b"

    def test_companies_fallback_when_no_stocks(self, tmp_path):
        sigs = self._load(
            tmp_path, [self._news(1, "标题", None, self._jev(companies=["英伟达"]))]
        )
        assert sigs[0]["ents"] == ["英伟达"]

    def test_ents_cap8(self, tmp_path):
        names = ["实体%d号" % i for i in range(10)]
        sigs = self._load(
            tmp_path,
            [self._news(1, "标题", json.dumps(names, ensure_ascii=False), self._jev())],
        )
        assert sigs[0]["ents"] == names[: gr.ENTS_MAX]

    def test_verdict_math_unchanged_with_enrich(self):
        # 富化字段不改变判定数学：同输入 → 同 verdict/t1_pct，title/ents 带出
        bars = {"cn": _bars([("2026-10-13", "100"), ("2026-10-14", "102")])}
        sig = _sig(1, "2026-10-13", bias="bull")
        sig.update({"title": "某公司利好", "ents": ["某公司"]})
        merged, _ = gr.reconcile([sig], bars, {})
        v = merged[(1, "2026-10-13", "cn")]
        assert v["verdict"] == "hit" and v["t1_pct"] == 2.0
        assert v["title"] == "某公司利好" and v["ents"] == ["某公司"]
        old = gr._verdict(_sig(2, "2026-10-13"), "nx", 2.0, "hit")
        assert old["title"] is None and old["ents"] == []  # 旧夹具容错
