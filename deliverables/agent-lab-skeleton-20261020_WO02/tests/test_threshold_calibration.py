"""告警阈值标定工具测试（tools/threshold_calibration.py）。

守的是一条红线：**没有真实基线就不许出阈值**。因此重点不在"算得准"，
而在三件事——

1. 样本不足 / 跨度不足 / 无数值指标 → 必须拒绝（ok=False，退出码 2），
   且不得返回任何 suggested_threshold；
2. 坏基线（不存在、非法 JSON、缺 ts/metrics）→ 显式报错（退出码 1），
   不静默跳过坏行把样本悄悄缩小；
3. 够格时才出数，且必须带溯源（路径 + sha256 + 样本数 + 跨度），
   结果标 calibrated=True，仍注明"须人工确认后投用"。

本文件内的基线全部为**合成数据**（明确标注 sim），仅用于验证工具行为，
不代表任何真实生产基线，不得据此投用阈值。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.threshold_calibration import (  # noqa: E402
    BaselineError,
    calibrate,
    main,
    quantile,
    read_baseline,
)

# 合成基线（sim）：均匀递增指标 + 一个恒定指标，仅用于行为验证。
def synth_rows(n: int = 100, span_hours: float = 48.0,
               step: float = 60.0) -> list[dict]:
    total = span_hours * 3600
    rows = []
    for i in range(n):
        rows.append({
            "ts": 1_750_000_000.0 + total * i / max(n - 1, 1),
            "metrics": {"task_backlog_total": float(i),
                        "risk_alert_rate_per_hour": 2.0},
        })
    return rows


def test_quantile_matches_linear_interpolation() -> None:
    """分位数口径：线性插值（与 numpy 默认一致），边界值可复核。"""
    values = [0.0, 10.0, 20.0, 30.0, 40.0]
    assert quantile(values, 0.0) == 0.0
    assert quantile(values, 1.0) == 40.0
    assert quantile(values, 0.5) == 20.0
    assert quantile(values, 0.95) == pytest.approx(38.0)
    assert quantile([7.0], 0.99) == 7.0


def test_insufficient_samples_refuses_to_emit_numbers() -> None:
    """样本不足：拒绝出数，不返回任何 suggested_threshold（不造数）。"""
    result = calibrate(synth_rows(n=10), min_samples=60,
                       min_span_hours=24.0, safety=1.5)
    assert result["ok"] is False
    assert "样本数不足" in " ".join(result["gaps"])
    assert "thresholds" not in result


def test_insufficient_span_refuses_to_emit_numbers() -> None:
    """跨度不足：样本再多也不出数（防"一小时高峰"被当成全天基线）。"""
    rows = synth_rows(n=200, span_hours=2.0)
    result = calibrate(rows, min_samples=60, min_span_hours=24.0, safety=1.5)
    assert result["ok"] is False
    assert "时间跨度不足" in " ".join(result["gaps"])
    assert "thresholds" not in result


def test_no_numeric_metrics_refuses() -> None:
    """基线段无任何数值指标：拒绝，而不是返回空阈值表冒充成功。"""
    rows = [{"ts": 1_750_000_000.0 + i * 3600, "metrics": {"note": "sim"}}
            for i in range(80)]
    result = calibrate(rows, min_samples=60, min_span_hours=24.0, safety=1.5)
    assert result["ok"] is False
    assert any("数值型指标" in g for g in result["gaps"])


def test_sufficient_baseline_emits_thresholds_with_provenance() -> None:
    """够格：出建议阈值（p99×安全系数上取整），并说明须人工确认。"""
    result = calibrate(synth_rows(n=100, span_hours=48.0), min_samples=60,
                       min_span_hours=24.0, safety=1.5)
    assert result["ok"] is True
    assert result["sample_count"] == 100
    assert result["span_hours"] == pytest.approx(48.0, abs=0.01)
    th = result["thresholds"]["task_backlog_total"]
    # 0..99 共 100 点，p99 = 98.01（线性插值）→ ceil(98.01×1.5)=148
    assert th["p99"] == pytest.approx(98.01)
    assert th["suggested_threshold"] == 148
    assert th["calibrated"] is True
    assert th["p99"] > th["p95"] > th["p50"]
    assert "人工确认" in result["note"]
    # 恒定指标：建议阈值为 2×1.5 上取整 → 3，不因方差为 0 而失效
    assert result["thresholds"]["risk_alert_rate_per_hour"][
        "suggested_threshold"] == 3


def test_bad_json_line_raises_not_silently_skipped(tmp_path: Path) -> None:
    """坏行必须报错：静默跳过会把样本量悄悄缩小，等于偷换基线。"""
    path = tmp_path / "baseline.jsonl"
    path.write_text(
        json.dumps({"ts": 1.0, "metrics": {"a": 1}}) + "\n{坏行}\n",
        encoding="utf-8")
    with pytest.raises(BaselineError) as exc:
        read_baseline(path)
    assert "非法 JSON" in str(exc.value)


def test_missing_required_fields_raise(tmp_path: Path) -> None:
    """缺 ts 或 metrics：报错，不猜默认值。"""
    path = tmp_path / "baseline.jsonl"
    path.write_text(json.dumps({"metrics": {"a": 1}}) + "\n", encoding="utf-8")
    with pytest.raises(BaselineError):
        read_baseline(path)


def test_cli_missing_file_returns_code_1(tmp_path: Path, capsys) -> None:
    """文件不存在：退出码 1（输入错误），输出可解析 JSON。"""
    code = main(["--baseline", str(tmp_path / "nope.jsonl")])
    assert code == 1
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert "不存在" in payload["error"]


def test_cli_insufficient_returns_code_2_and_writes_nothing_useful(
        tmp_path: Path, capsys) -> None:
    """不够格：退出码 2，即使指定 --json 也不得写入建议阈值。"""
    baseline = tmp_path / "baseline.jsonl"
    baseline.write_text("\n".join(json.dumps(r) for r in synth_rows(n=5)),
                        encoding="utf-8")
    out = tmp_path / "out" / "thresholds.json"
    code = main(["--baseline", str(baseline), "--min-samples", "60",
                 "--json", str(out)])
    assert code == 2
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert "thresholds" not in payload
    assert payload["source"]["sha256"]          # 失败也留输入指纹


def test_cli_success_writes_traceable_output(tmp_path: Path, capsys) -> None:
    """够格：退出码 0，输出带路径与 sha256，可事后核"这批阈值从哪来"。"""
    baseline = tmp_path / "baseline.jsonl"
    baseline.write_text("\n".join(json.dumps(r) for r in synth_rows(n=120)),
                        encoding="utf-8")
    out = tmp_path / "thresholds.json"
    code = main(["--baseline", str(baseline), "--min-samples", "60",
                 "--min-span-hours", "24", "--json", str(out)])
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["source"]["baseline_path"].endswith("baseline.jsonl")
    assert len(payload["source"]["sha256"]) == 64
    assert payload["thresholds"]["task_backlog_total"]["calibrated"] is True
