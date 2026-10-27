# -*- coding: utf-8 -*-
"""W3-4 单测：qpzm 推送分位数口径（test_qpzm_percentile）。

四条路径（全部合成数据，不触网、不推卡、不写生产文件）：
  1. 分位计算正确性：边界 0 / 100、并列同分、普通插值
  2. 60 日滚动窗口：>60 日只取末 60 日；replay 行/exclude_date 不入基准
  3. 开关关闭：score_percentile=false → 不加 percentile 字段
  4. 历史不足回退：live 日 <60 → 全部可得入池；零历史 → 不加字段

判例式注释沿用仓库风格。运行：
  "C:/Program Files/Python311/python.exe" test_qpzm_percentile.py
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rps_resonance as RR


def _write_batches(rows):
    """合成归档 → 临时 jsonl（一行一天，与产线同格式）。"""
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def _day(date, scores, source="live"):
    """一天 live/replay 行：score=None 的票模拟 replay 口径。"""
    return {
        "date": date,
        "source": source,
        "picks": [{"code": "sz%06d" % i, "score": s} for i, s in enumerate(scores)],
    }


def test_percentile_edges():
    """路径1：边界 0/100 + 并列同分 + 普通占比。"""
    hist = [50.0, 60.0, 70.0, 80.0]
    assert RR.score_percentile(40, hist) == 0.0, "低于全部历史 → P0"
    assert RR.score_percentile(90, hist) == 100.0, "高于全部历史 → P100"
    # 并列同分计高位：与 60 同分的票，严格低于它的只有 [50] → P25
    assert RR.score_percentile(60, hist) == 25.0, "并列同分不抬轿"
    # 普通插值：70 分严格低于它的有 [50,60] → P50
    assert RR.score_percentile(70, hist) == 50.0, "普通占比插值"
    assert RR.score_percentile(66, []) is None, "空基准 → None（不硬凑）"
    print("PASS 路径1 分位边界 0/100/并列/空基准")


def test_rolling_window():
    """路径2：60 日滚动窗口（>60 只取末 60；replay/当日不入基准）。"""
    # 造 70 个 live 日，每天 1 票：分数=日期序号（第 i 日 → 分 i+1）
    rows = [_day("d%03d" % i, [float(i + 1)]) for i in range(70)]
    rows.append(_day("d099", [999.0], source="replay"))  # replay 不入池
    path = _write_batches(rows)
    hist = RR.load_hist_scores(path, window=60, exclude_date="d069")
    # 排除当日剩 69 日 → 滚动取末 60 日（d009~d068），d000~d008 出窗
    assert len(hist) == 60, "70 日排除当日 → 滚动仍取 60 日"
    assert max(hist) == 69.0 and 999.0 not in hist, "replay 高分 999 不入池"
    assert min(hist) == 10.0, "滚动窗口裁掉最早 9 日（d000~d008 出窗）"
    # 再验：不排除当日时恰好 60 日
    hist2 = RR.load_hist_scores(path, window=60)
    assert len(hist2) == 60 and max(hist2) == 70.0, "末 60 日全含当日"
    os.remove(path)
    print("PASS 路径2 60日滚动窗口/replay剔除/当日排除")


def test_switch_off():
    """路径3：开关关闭 → res 不加 percentile 字段。"""
    path = _write_batches([_day("d001", [50.0, 80.0])])
    res = [{"name": "甲", "score": 85}, {"name": "乙", "score": 30}]
    off = {"qpzm": {"score_percentile": False}}
    out = RR.annotate_percentile(res, batches_path=path, cfg=off)
    assert all("percentile" not in r for r in out), "开关关 → 无字段"
    # 开关缺省（cfg 里无 qpzm 节）→ 默认开，字段应加上
    dflt = {"qpzm": {}}
    out2 = RR.annotate_percentile([dict(r) for r in res], batches_path=path, cfg=dflt)
    assert all(r.get("percentile") is not None for r in out2), "缺省 true：字段注入"
    os.remove(path)
    print("PASS 路径3 开关关闭不加字段/缺省true")


def test_history_fallback():
    """路径4：历史不足回退（live 日<60 全入池；零历史不加字段）。"""
    # 仅 3 个 live 日 → 不足 60，全部可得
    rows = [_day("d%03d" % i, [float(10 * (i + 1))]) for i in range(3)]
    path = _write_batches(rows)
    hist = RR.load_hist_scores(path)
    assert hist == [10.0, 20.0, 30.0], "不足 60 日 → 全部可得"
    res = [{"name": "甲", "score": 20}]  # 与 20 并列计高位 → P33.3
    out = RR.annotate_percentile(res, batches_path=path, cfg={"qpzm": {}})
    assert out[0]["percentile"] == 33.3, "小样本分位=33.3（一位小数）"
    os.remove(path)
    # 零历史（文件不存在）→ 不加字段不炸
    out2 = RR.annotate_percentile(
        [{"name": "乙", "score": 80}],
        batches_path=os.path.join(tempfile.gettempdir(), "_no_such.jsonl"),
        cfg={"qpzm": {}},
    )
    assert "percentile" not in out2[0], "零历史 fail-open 不加字段"
    print("PASS 路径4 历史不足回退/零历史兜底")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    test_percentile_edges()
    test_rolling_window()
    test_switch_off()
    test_history_fallback()
    print("ALL 4 PATHS PASS")
