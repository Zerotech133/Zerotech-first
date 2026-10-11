import json

import nine_dim_backtest as backtest


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
