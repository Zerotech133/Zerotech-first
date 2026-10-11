import json

import numpy as np
import pandas as pd

import nine_dim_backtest as backtest
import nine_dim as nd


def test_force_replay_replaces_only_requested_anchors(tmp_path):
    path = tmp_path / "replay.jsonl"
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False) + "\n"
            for row in (
                {"anchor": "20260930", "code": "000001.SZ"},
                {"anchor": "20260924", "code": "000002.SZ"},
            )
        ),
        encoding="utf-8",
    )

    kept, done = backtest._load_replay_rows(
        str(path), replace_anchors={"20260930"}
    )

    assert kept == [{"anchor": "20260924", "code": "000002.SZ"}]
    assert done == {"20260924"}


def test_coarse_filter_excludes_code_missing_active_basic():
    """主数据不在活跃 stock_basic 中的历史代码不应进入当前候选池。"""
    n = 140
    base = {
        "close": pd.Series(np.linspace(10.0, 11.0, n)),
        "volume": pd.Series([1.0] * (n - 1) + [2.0]),
        "amount": pd.Series([300000.0] * n),
    }
    frames = {
        "000001.SZ": pd.DataFrame(base),
        "603056.SH": pd.DataFrame(base),
    }
    cand, _funnel = nd.coarse_filter(
        frames,
        {"000001.SZ": ["有效股票", "行业"]},
        {"000001.SZ": 90.0, "603056.SH": 90.0},
    )

    assert cand == ["000001.SZ"]
