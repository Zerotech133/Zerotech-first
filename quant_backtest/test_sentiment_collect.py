# -*- coding: utf-8 -*-
"""sentiment_collect_daily 纯函数测试（不触网：fetch_day 打桩）。"""
import json
import os
import tempfile

import sentiment_collect_daily as sc


class _FakeRow:
    def __init__(self, cols_rows):
        self.columns = cols_rows[0]
        self._rows = cols_rows[1:]

    def __len__(self):
        return len(self._rows)

    @property
    def empty(self):
        return not self._rows

    def __iter__(self):
        return iter(self._rows)


def test_fetch_day_counts_and_rate(monkeypatch):
    """合成两池：炸板率与连板分布口径正确。"""
    import pandas as pd

    def fake_zt(date):
        return pd.DataFrame({"连板数": [1, 2, 2, 3, 10],
                             "代码": list("abcde")})

    def fake_zb(date):
        return pd.DataFrame({"代码": list("fg")})

    monkeypatch.setattr("akshare.stock_zt_pool_em", fake_zt)
    monkeypatch.setattr("akshare.stock_zt_pool_zbgc_em", fake_zb)
    r = sc.fetch_day("20261014")
    assert r["zt_count"] == 5 and r["zb_count"] == 2
    assert abs(r["zha_ban_rate"] - round(2 / 7, 4)) < 1e-9
    assert r["max_lb"] == 10
    assert r["lb_dist"] == {"2板": 2, "3板": 1, "10板": 1}
    assert r["status"] == "ok"


def test_fetch_day_zb_out_of_window_degrades(monkeypatch):
    """炸板池超 30 日窗抛错 → 降级为仅涨停口径（zb=None 不炸行）。"""
    import pandas as pd

    def fake_zt(date):
        return pd.DataFrame({"连板数": [1, 4], "代码": list("ab")})

    def fake_zb(date):
        raise ValueError("炸板股池只能获取最近 30 个交易日的数据")

    monkeypatch.setattr("akshare.stock_zt_pool_em", fake_zt)
    monkeypatch.setattr("akshare.stock_zt_pool_zbgc_em", fake_zb)
    r = sc.fetch_day("20260401")
    assert r["zt_count"] == 2 and r["zb_count"] is None
    assert r["zha_ban_rate"] is None and r["max_lb"] == 4


def test_fetch_day_both_empty_returns_none(monkeypatch):
    import pandas as pd

    monkeypatch.setattr(
        "akshare.stock_zt_pool_em", lambda date: pd.DataFrame())
    monkeypatch.setattr(
        "akshare.stock_zt_pool_zbgc_em", lambda date: pd.DataFrame())
    assert sc.fetch_day("20260101") is None


def test_append_idempotent_and_badline():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "s.jsonl")
        rows = [{"date": "20261014", "zt_count": 36, "status": "ok"},
                {"date": "20261015", "zt_count": 40, "status": "ok"}]
        assert sc.append_rows(rows, path) == 2
        with open(path, "a", encoding="utf-8") as f:
            f.write("{bad\n")
        assert sc.append_rows(rows, path) == 0          # 幂等+坏行容忍
        assert sc.append_rows([{"date": "20261016", "zt_count": 1,
                                "status": "ok"}], path) == 1
        n = 0
        for l in open(path, encoding="utf-8"):
            try:
                json.loads(l)
                n += 1
            except Exception:
                continue
        assert n == 3                                 # 坏行不计


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
