# -*- coding: utf-8 -*-
"""S1-05 L1 止血测试：日报「数据实证」块 48h 新鲜度门槛。

背景：sentiment_bt_result.json / jev_impact.json 的生成任务已缺失，产物停在
2026-10-18（诊断 353.1h），旧 _data_line() 会把 14 天前的回测/复评数据当"当日
实证"发飞书。本测试锁死门槛语义：
  * 新鲜（age ≤ 48h）→ 输出行格式与旧版逐字一致（不许改格式）
  * 单产物陈旧（age > 48h）→ 该行不外发，改发明确告警（含"已跳过（S1-05 止血）"）
  * 两产物均陈旧 → 整块跳过，只留一行告警
  * 文件缺失 → 维持旧行为：整块跳过 / 仅少一行，不抛异常、不误发

做法：monkeypatch daily_report.HERE 到 tmp_path，按需构造 data/*.json 并用
os.utime 伪造 mtime。不触网、不发送、不写仓库内文件。
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NEWS_DIR = REPO / "news_collector"
if str(NEWS_DIR) not in sys.path:
    sys.path.insert(0, str(NEWS_DIR))

import daily_report as dr  # noqa: E402

BT_NAME = "sentiment_bt_result.json"
IM_NAME = "jev_impact.json"
BT_PAYLOAD = {"n": 100, "corr_score_pct1": 3.5, "n_high_conf": 20,
              "corr_score_pct1_high_conf": 5.2, "conf_monotone": True}
IM_PAYLOAD = {"n": 40, "applied": {"medium": False, "high": False}}

BT_LINE = "情绪×次日回测 n=100 · corr=3.5 · 高置信 n=20 corr=5.2 · 单调=是"
IM_LINE = "impact 分层 n=40 · 复评门槛观察中(E3)"
HEADER = "📊 **数据实证**（多维数据日报）"


def _write(data_dir: Path, name: str, payload, age_hours):
    """写产物文件并把 mtime 调到 age_hours 前。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    p = data_dir / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    ts = time.time() - age_hours * 3600.0
    os.utime(p, (ts, ts))
    return p


@pytest.fixture()
def station(tmp_path, monkeypatch):
    """把日报的 HERE 指向临时站点目录（data/ 下自造产物）。"""
    monkeypatch.setattr(dr, "HERE", str(tmp_path))
    (tmp_path / "data").mkdir()
    return tmp_path


def test_stale_gate_constant_is_48h():
    assert dr.STALE_GATE_HOURS == 48


def test_fresh_products_keep_exact_legacy_format(station):
    _write(station / "data", BT_NAME, BT_PAYLOAD, age_hours=1.0)
    _write(station / "data", IM_NAME, IM_PAYLOAD, age_hours=2.0)
    out = dr._data_line()
    # 新鲜路径格式必须与旧版逐字一致（S1-05 要求：正常路径格式不变）
    assert out == "\n".join([HEADER, BT_LINE, IM_LINE])
    assert "陈旧" not in out and "S1-05" not in out


def test_fresh_boundary_47h_still_outputs(station):
    _write(station / "data", BT_NAME, BT_PAYLOAD, age_hours=47.0)
    _write(station / "data", IM_NAME, IM_PAYLOAD, age_hours=47.0)
    out = dr._data_line()
    assert BT_LINE in out and IM_LINE in out
    assert "已跳过" not in out


def test_stale_bt_skipped_with_explicit_alert(station):
    _write(station / "data", BT_NAME, BT_PAYLOAD, age_hours=72.0)
    _write(station / "data", IM_NAME, IM_PAYLOAD, age_hours=1.0)
    out = dr._data_line()
    assert BT_LINE not in out                      # 陈旧数据不外发
    assert IM_LINE in out                          # 新鲜的另一行正常出
    assert BT_NAME in out and "数据陈旧 72h" in out
    assert "已跳过（S1-05 止血）" in out


def test_stale_impact_skipped_with_explicit_alert(station):
    _write(station / "data", BT_NAME, BT_PAYLOAD, age_hours=1.0)
    _write(station / "data", IM_NAME, IM_PAYLOAD, age_hours=336.0)
    out = dr._data_line()
    assert IM_LINE not in out
    assert BT_LINE in out and HEADER in out
    assert IM_NAME in out and "数据陈旧 336h" in out
    assert "已跳过（S1-05 止血）" in out


def test_both_stale_whole_block_skipped_single_line(station):
    _write(station / "data", BT_NAME, BT_PAYLOAD, age_hours=336.0)
    _write(station / "data", IM_NAME, IM_PAYLOAD, age_hours=336.0)
    out = dr._data_line()
    assert out is not None and "\n" not in out     # 整块跳过，只记一行
    assert "均陈旧" in out and "整块跳过（S1-05 止血）" in out
    assert HEADER not in out and BT_LINE not in out and IM_LINE not in out
    assert "336h" in out


def test_missing_products_keep_legacy_skip(station):
    # 两个产物都缺失 → 整块 None（旧行为）
    assert dr._data_line() is None
    # bt 缺失、im 有 → 仍整块 None（旧行为：无回测基础不出块）
    _write(station / "data", IM_NAME, IM_PAYLOAD, age_hours=1.0)
    assert dr._data_line() is None
    # bt 有、im 缺失 → 只出 header+回测行，不抛异常、不误报陈旧
    (station / "data" / IM_NAME).unlink()
    _write(station / "data", BT_NAME, BT_PAYLOAD, age_hours=1.0)
    out = dr._data_line()
    assert out == "\n".join([HEADER, BT_LINE])
    assert "陈旧" not in out


def test_gate_does_not_write_or_touch_products(station):
    p1 = _write(station / "data", BT_NAME, BT_PAYLOAD, age_hours=1.0)
    p2 = _write(station / "data", IM_NAME, IM_PAYLOAD, age_hours=1.0)
    before = (p1.stat().st_size, p1.stat().st_mtime_ns, p2.stat().st_size, p2.stat().st_mtime_ns)
    dr._data_line()
    dr._data_line()
    after = (p1.stat().st_size, p1.stat().st_mtime_ns, p2.stat().st_size, p2.stat().st_mtime_ns)
    assert before == after  # 纯只读判定：不改产物、不写文件
