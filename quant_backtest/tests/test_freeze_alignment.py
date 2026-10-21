"""N7 冻结时点对齐：确定性 + 业务日消费时点反例（先红后绿，合成临时 pool）。

隔离原则：用 tempfile 构造合成 pool，不依赖真实 chanlun_pool.jsonl / 不触生产。
固化「冻结锚定 business_day 消费时点、与运行墙钟解耦、幂等」的契约。
"""
import json
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

QBT = Path(__file__).resolve().parents[1]
if str(QBT / "tools") not in sys.path:
    sys.path.insert(0, str(QBT / "tools"))

from freeze_alignment import (  # noqa: E402
    business_day_consumption_timepoint, freeze_pool, _coerce_date, d_iso)


def _tmp_pool(records):
    d = tempfile.mkdtemp()
    p = Path(d) / "chanlun_pool.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
                 encoding="utf-8")
    return p


def test_consumption_timepoint_default_close():
    """默认 A 股收盘 15:00 Asia/Shanghai。"""
    ctp = business_day_consumption_timepoint("20261020")
    assert ctp.year == 2026 and ctp.month == 10 and ctp.day == 20
    assert (ctp.hour, ctp.minute, ctp.second) == (15, 0, 0)
    assert str(ctp.tzinfo) == "Asia/Shanghai"


def test_consumption_timepoint_provider_override():
    """传入 provider 优先用真实会话 close_at（mock，Shanghai 时区）。"""
    class _Sess:
        close_at = datetime(2026, 10, 20, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    class _Prov:
        def session_bounds(self, d):
            return _Sess()

    ctp = business_day_consumption_timepoint(date(2026, 10, 20), provider=_Prov())
    assert ctp.hour == 15 and ctp.minute == 0
    assert str(ctp.tzinfo) == "Asia/Shanghai"


def test_freeze_deterministic_sha_independent_of_runtime():
    """核心反例：同 pool + 同 business_day → sha256 稳定，与运行时刻无关（取消人工驱动）。"""
    recs = [{"code": "000001.SZ", "detect_date": "2026-10-20"}]
    p = _tmp_pool(recs)
    out = tempfile.mkdtemp()
    r1 = freeze_pool(p, "20261020", out)
    r2 = freeze_pool(p, "20261020", out)  # 模拟调度重复运行
    assert r1["sha256"] == r2["sha256"]
    assert r1["consumption_timepoint"] == r2["consumption_timepoint"]
    assert r1["business_day"] == "2026-10-20"
    assert Path(r1["frozen_copy"]).exists()


def test_freeze_anchored_to_business_day_not_mtime():
    """冻结记录锚定 business_day 消费时点，而非文件 mtime（消除人工驱动偏差）。"""
    recs = [{"code": "000001.SZ", "detect_date": "2026-10-20"}]
    p = _tmp_pool(recs)
    out = tempfile.mkdtemp()
    rec = freeze_pool(p, "20261020", out)
    ctp = datetime.fromisoformat(rec["consumption_timepoint"])
    assert (ctp.year, ctp.month, ctp.day) == (2026, 10, 20)
    assert (ctp.hour, ctp.minute) == (15, 0)
    # frozen_at（墙钟）与 consumption_timepoint（逻辑）解耦：一般不同（除非恰好 15:00 运行）
    assert rec["frozen_at"] != rec["consumption_timepoint"]


def test_freeze_record_persisted():
    recs = [{"code": "000001.SZ", "detect_date": "2026-10-20"}]
    p = _tmp_pool(recs)
    out = tempfile.mkdtemp()
    rec = freeze_pool(p, "20261020", out)
    rec_path = Path(out) / "freeze_record.json"
    assert rec_path.exists()
    loaded = json.loads(rec_path.read_text(encoding="utf-8"))
    assert loaded["sha256"] == rec["sha256"]
    assert loaded["business_day"] == "2026-10-20"


def test_coerce_date_variants():
    assert _coerce_date("20261020") == date(2026, 10, 20)
    assert _coerce_date("2026-10-20") == date(2026, 10, 20)
    assert _coerce_date(date(2026, 10, 20)) == date(2026, 10, 20)
    assert d_iso("20261020") == "2026-10-20"


def test_missing_pool_raises():
    import pytest
    with pytest.raises(FileNotFoundError):
        freeze_pool("nonexistent_pool.jsonl", "20261020", tempfile.mkdtemp())
