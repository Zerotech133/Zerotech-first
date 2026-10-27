"""K-MDC-E3 tests: window gate, digest honesty, four-account receipt. 全离线不真发."""

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intel_feishu_export import (  # noqa: E402
    ExportRejected,
    MAX_EDGES,
    ExportReceipt,
    append_ledger,
    export,
    in_window,
    render_digest,
)

TZ = ZoneInfo("Asia/Shanghai")
DOSSIER = {
    "stock_code": "600001.SH",
    "total_edges": 8,
    "shown": 5,
    "truncated": True,
    "edges": [
        {"rel_type": "co_occurrence", "pair": f"A×B{i}", "question": f"是否共谋{i}？"}
        for i in range(5)
    ],
}


def at(h, m, dow_friday=False):
    d = datetime(2026, 10, 23, h, m, tzinfo=TZ)  # 周五
    return d


def test_window_gate_open_closed_weekend():
    assert in_window(at(8, 45))  # 盘前中
    assert in_window(at(15, 30))  # 盘后中
    assert not in_window(at(12, 0))  # 午间关
    assert not in_window(at(7, 0))  # 早外
    assert not in_window(at(22, 0))
    sat = datetime(2026, 10, 24, 8, 45, tzinfo=TZ)
    assert not in_window(sat)  # 周六关


def test_naive_now_rejected():
    with pytest.raises(ExportRejected, match="TIME_INVALID"):
        in_window(datetime(2026, 10, 23, 8, 45))


def test_export_outside_window_never_attempts(fake_sender=None):
    r = export(DOSSIER, now=at(12, 0), sender="unused")
    assert not r.attempted and not r.accepted and r.window_state == "closed"
    assert "窗口外拒发" in r.note


def test_export_in_window_renders_and_sends_via_channel(tmp_path):
    send = tmp_path / "send_feishu.py"
    send.write_text("print('SEND_OK 20261023 test')", encoding="utf-8")
    r = export(DOSSIER, now=at(8, 45), sender=str(send))
    assert r.attempted and r.accepted and r.window_state == "open"
    assert r.candidate == 8 and r.shown == MAX_EDGES  # 四分账: 候选8显示5


def test_accepted_false_when_channel_says_not_ok(tmp_path):
    send = tmp_path / "send_feishu.py"
    send.write_text("print('SEND_FAIL')", encoding="utf-8")
    r = export(DOSSIER, now=at(15, 30), sender=str(send))
    assert r.attempted and not r.accepted  # HTTP/退出零≠送达


def test_digest_honesty_no_raw_text_truncation_visible():
    text = render_digest(DOSSIER)
    assert "candidate" in text and "非事实" in text
    assert "已截断" in text and "共 8 条" in text
    empty = render_digest({"stock_code": "X", "total_edges": 0})
    assert "空态" in empty


def test_digest_rejects_bad_payload():
    with pytest.raises(ExportRejected, match="DOSSIER_INVALID"):
        render_digest({"foo": 1})


def test_ledger_appends_four_accounts(tmp_path):
    r1 = ExportReceipt("t1", "600001.SH", 8, 5, True, True, "open")
    r2 = ExportReceipt("t2", "600002.SH", 0, 0, False, False, "closed")
    append_ledger(tmp_path, r1)
    append_ledger(tmp_path, r2)
    lines = (
        (tmp_path / "intel_export_ledger.jsonl")
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()
    )
    assert (
        len(lines) == 2
        and '"accepted": true' in lines[0]
        and '"accepted": false' in lines[1]
    )
