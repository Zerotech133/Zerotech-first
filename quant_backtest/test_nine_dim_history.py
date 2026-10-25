# -*- coding: utf-8 -*-
"""OS-FIX-NINEDIM-20261026：九维运行级历史账本回归。

背景（审计缺口）：`nine_dim.py:79` 声明 `data/nine_dim_history.jsonl` 持久账本，
但此前该路径只在 n_alert>0 时写"警报池行"，运行级信息（每轮 n_alert / n_cand /
n_full / 各 band 计数 / top20 名单）没有历史——`nine_dim_scores.json` 只有最新
一份，n_alert=0 时更是什么都留不下，无法复盘/审计。

本测试只使用 tmp_path 临时文件，**不读写生产数据根**；覆盖：
  ① 成功追加 1 行 + 固定 schema/bands/top20；
  ② 同一 generated_at 幂等（不重复追加）；
  ③ 不可写路径只告警不抛（主计算不受影响）；
  ④ 坏行跳过并计数（legacy 警报池行不计坏行、不混入）；
  ⑤ limit 只取最近 N 条；
  ⑥ update_perf 容忍"只有运行级行"的历史文件（不 KeyError、不覆盖战绩缓存）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nine_dim as nd  # noqa: E402


def _snap(gen="2026-10-26T08:10:00+08:00", trade_date="2026-10-24"):
    return {"generated_at": gen, "trade_date": trade_date,
            "n_alert": 2, "n_cand": 4, "n_full": 3}


def _rows():
    return [{"code": "600519.SH", "score": 88.0, "band": "alert2"},
            {"code": "000001.SZ", "score": 76.0, "band": "alert"},
            {"code": "600000.SH", "score": 61.0, "band": "mid"},
            {"code": "000002.SZ", "score": None, "band": "na"}]


def _lines(path):
    return [l for l in Path(path).read_text(encoding="utf-8").splitlines()
            if l.strip()]


# --- ① 成功追加 1 行 + schema ------------------------------------------------
def test_append_one_line_and_schema(tmp_path):
    p = tmp_path / "nine_dim_history.jsonl"
    rec = nd.build_history_record(_snap(), _rows())
    assert rec["schema"] == "nine_dim_history/v1"
    assert rec["generated_at"] == "2026-10-26T08:10:00+08:00"
    assert rec["trade_date"] == "2026-10-24"
    assert (rec["n_alert"], rec["n_cand"], rec["n_full"]) == (2, 4, 3)
    assert rec["bands"] == {"alert2": 1, "alert": 1, "mid": 1, "na": 1}
    assert len(rec["history"]) == 4
    assert rec["history"][0] == {"code": "600519.SH", "score": 88.0,
                                 "band": "alert2"}
    assert nd.append_history(rec, path=p) is True
    assert len(_lines(p)) == 1
    got = nd.load_history(path=p)
    assert len(got) == 1 and got[0]["schema"] == "nine_dim_history/v1"


# --- ② 同 generated_at 幂等 --------------------------------------------------
def test_same_generated_at_idempotent(tmp_path):
    p = tmp_path / "h.jsonl"
    snap = _snap()
    assert nd.record_run_history(snap, _rows(), path=p) is True
    assert nd.record_run_history(snap, _rows(), path=p) is False   # 幂等跳过
    assert len(_lines(p)) == 1
    # 换一个 generated_at → 正常追加第二行
    assert nd.record_run_history(
        _snap(gen="2026-10-27T08:10:00+08:00"), _rows(), path=p) is True
    assert len(nd.load_history(path=p)) == 2


# --- ③ 不可写路径不抛 --------------------------------------------------------
def test_unwritable_path_does_not_raise(tmp_path):
    as_dir = tmp_path / "as_dir"
    as_dir.mkdir()
    rec = nd.build_history_record(_snap(), _rows())
    assert nd.append_history(rec, path=as_dir) is False        # 目录不能当文件写
    assert nd.record_run_history(_snap(), _rows(), path=as_dir) is False
    assert nd.load_history(path=tmp_path / "missing.jsonl") == []


# --- ④ 坏行跳过并计数；legacy 警报池行不混入 ---------------------------------
def test_load_history_skips_bad_lines_and_counts(tmp_path, capsys):
    p = tmp_path / "h.jsonl"
    good = nd.build_history_record(_snap(), _rows())
    legacy = {"code": "600519.SH", "dt": "2026-10-24",
              "score": 88.0, "band": "alert2", "pool_type": "alert"}
    p.write_text("\n".join([
        json.dumps(good, ensure_ascii=False),
        "{not json",                                    # 坏行 1
        "[1, 2, 3]",                                    # 坏行 2（非对象）
        json.dumps(legacy, ensure_ascii=False),         # legacy 行（非坏行）
    ]) + "\n", encoding="utf-8")
    got = nd.load_history(path=p)
    assert [r["schema"] for r in got] == ["nine_dim_history/v1"]
    assert "坏行=2" in capsys.readouterr().err


# --- ⑤ limit 只取最近 N 条 ---------------------------------------------------
def test_load_history_limit_returns_latest(tmp_path):
    p = tmp_path / "h.jsonl"
    for day in (24, 25, 26):
        assert nd.record_run_history(
            _snap(gen="2026-10-%02dT08:00:00+08:00" % day),
            _rows(), path=p) is True
    assert [r["generated_at"][:10] for r in nd.load_history(path=p)] == \
        ["2026-10-24", "2026-10-25", "2026-10-26"]
    assert [r["generated_at"][:10] for r in
            nd.load_history(limit=2, path=p)] == ["2026-10-25", "2026-10-26"]
    assert nd.load_history(limit=0, path=p) == []


# --- ⑥ top20 上限 + bands 计数 ----------------------------------------------
def test_history_top20_cap():
    rows = [{"code": "c%02d" % i, "score": float(i), "band": "low"}
            for i in range(25)]
    rec = nd.build_history_record(_snap(), rows)
    assert len(rec["history"]) == 20
    assert rec["history"][0]["code"] == "c00"
    assert rec["bands"] == {"low": 25}


# --- ⑦ update_perf 容忍只有运行级行的历史文件 --------------------------------
def test_update_perf_tolerates_run_records_only(tmp_path, monkeypatch, capsys):
    p = tmp_path / "h.jsonl"
    assert nd.record_run_history(_snap(), _rows(), path=p) is True
    monkeypatch.setattr(nd, "HIST", str(p))
    nd.update_perf()          # 修复前：r["code"] → KeyError；修复后应正常返回
    assert "no pool rows yet" in capsys.readouterr().out
