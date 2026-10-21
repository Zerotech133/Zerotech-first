# -*- coding: utf-8 -*-
"""strategy_layer.signal_pool_writer 单元测试（六度选股 / 工位 #372）。

策略：合成数据 + tempfile，只在临时文件中落账，**绝不触碰真实
data/signal_pool.jsonl**（该文件当前本就 ABSENT）。

关键断言（先红后绿）：
  - build_record 返回含必填字段的 dict
  - write_signals 写入 N 条后文件行数 == N 且每行可 json.loads
  - 写入内容 round-trip 正确
  - SIGNAL_POOL_AUTO_WRITE is False
  - default_output_path() 以 "signal_pool.jsonl" 结尾
"""
import sys
from pathlib import Path

# 注入路径：仓根 + strategy_layer（与 repos 内其它测试一致）
QBT = Path(__file__).resolve().parents[1]
for _p in (str(QBT), str(QBT / "strategy_layer")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from strategy_layer.signal_pool_writer import (  # noqa: E402
    SIGNAL_POOL_AUTO_WRITE,
    build_record,
    default_output_path,
    write_signals,
)


def test_signal_pool_auto_write_is_true():
    # N11 拍板并激活：决定后配置 trial 试跑显式落账 data/signal_pool.jsonl
    assert SIGNAL_POOL_AUTO_WRITE is True


def test_default_output_path_endswith_signal_pool_jsonl():
    # 约定落盘文件名
    assert default_output_path().endswith("signal_pool.jsonl")


def test_build_record_has_required_fields():
    rec = build_record("600519.SH", 0.87, "xunyou_pool", "20261021",
                       meta={"note": "smoke"})
    for key in ("symbol", "score", "source", "trade_date", "meta",
                "pool_stamp"):
        assert key in rec, "缺失必填字段: %s" % key
    assert rec["symbol"] == "600519.SH"
    assert rec["score"] == 0.87
    assert rec["source"] == "xunyou_pool"
    assert rec["trade_date"] == "20261021"
    assert rec["meta"] == {"note": "smoke"}
    assert rec["pool_stamp"]["pool_id"] == "signal_pool"
    assert rec["pool_stamp"]["pool_version"] == 0


def test_build_record_meta_default_empty():
    rec = build_record("000001.SZ", 0.5, "todays_picks", "20261021")
    assert rec["meta"] == {}


def test_write_signals_writes_n_lines_and_parseable(tmp_path):
    p = tmp_path / "signal_pool.jsonl"
    records = [
        build_record("600519.SH", 0.9, "xunyou_pool", "20261021",
                     meta={"rank": 1}),
        build_record("000001.SZ", 0.6, "todays_picks", "20261021"),
        build_record("300750.SZ", 0.42, "bt-signals", "20261021",
                     meta={"flag": "watch"}),
    ]
    n = write_signals(records, str(p))
    assert n == 3

    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    for line in lines:
        obj = __import__("json").loads(line)  # 每行可 json.loads
        assert "symbol" in obj and "pool_stamp" in obj


def test_write_signals_roundtrip(tmp_path):
    p = tmp_path / "signal_pool.jsonl"
    records = [
        build_record("600519.SH", 0.9, "xunyou_pool", "20261021",
                     meta={"rank": 1}),
        build_record("000001.SZ", 0.6, "todays_picks", "20261021"),
    ]
    write_signals(records, str(p))

    loaded = [__import__("json").loads(ln)
              for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    # round-trip：字段无损
    assert loaded[0]["symbol"] == "600519.SH"
    assert loaded[0]["meta"] == {"rank": 1}
    assert loaded[1]["source"] == "todays_picks"
    assert loaded[0]["pool_stamp"]["pool_id"] == "signal_pool"


def test_write_signals_empty_is_noop(tmp_path):
    p = tmp_path / "signal_pool.jsonl"
    n = write_signals([], str(p))
    assert n == 0
    assert not p.exists()  # 空列表不创建空文件


def test_write_signals_append_only(tmp_path):
    p = tmp_path / "signal_pool.jsonl"
    write_signals([build_record("A.SH", 0.1, "s", "20261021")], str(p))
    write_signals([build_record("B.SH", 0.2, "s", "20261021")], str(p))
    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2  # 追加而非覆盖
    assert __import__("json").loads(lines[0])["symbol"] == "A.SH"
    assert __import__("json").loads(lines[1])["symbol"] == "B.SH"


def test_write_signals_idempotent_same_key(tmp_path):
    p = tmp_path / "signal_pool.jsonl"
    # 同键重跑：模拟 trial 试跑重跑同一交易日
    n1 = write_signals([build_record("A.SH", 0.1, "xunyou_pool", "20261021")], str(p))
    n2 = write_signals([build_record("A.SH", 0.9, "xunyou_pool", "20261021",
                                     meta={"rank": 1})], str(p))
    assert n1 == 1 and n2 == 1
    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1, "同键重跑不应累积"
    obj = __import__("json").loads(lines[0])
    assert obj["score"] == 0.9  # 新值覆盖旧值
    assert obj["meta"] == {"rank": 1}


def test_write_signals_idempotent_rerun_no_accumulate(tmp_path):
    p = tmp_path / "signal_pool.jsonl"
    batch = [
        build_record("600519.SH", 0.9, "xunyou_pool", "20261021"),
        build_record("000001.SZ", 0.6, "xunyou_pool", "20261021"),
    ]
    write_signals(batch, str(p))
    write_signals(batch, str(p))  # 整批重跑
    write_signals(batch, str(p))
    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2, "整批重跑 3 次仍应只有 2 行，不得累积"
