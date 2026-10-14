# -*- coding: utf-8 -*-
"""gatekeeper 环境闸门等价性黄金基线对照测试（baseline-dev 锁定，
2026-10-15 联调对齐 gatekeeper 实际接口：EnvGate+QpzmParquetFeeder）。

读 reports/_gatekeeper_golden_baseline.json（由 _tmp_gate_golden_baseline.py
用现行 qpzm_backtest.load_wide + env_gate 生成，242 日，自校验锚
20251009 breadth=0.45 三重通过），用 gatekeeper 全链（feeder 特征 ->
v3 policy 判定）重算同窗逐日，对照 breadth/med20/safe 逐值 diff=0。

首跑全量装载 5597 只 parquet（约 1-3 分钟），feeder 有缓存，之后
逐日切片。全量跑法：
  "C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe" \
      -m pytest tools/gatekeeper/tests/test_equivalence_golden.py -v
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
GK_DIR = os.path.abspath(os.path.join(HERE, ".."))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
for p in (GK_DIR, PROJECT_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

BASELINE_PATH = os.path.join(PROJECT_ROOT, "reports",
                             "_gatekeeper_golden_baseline.json")
TOL = 1e-9

pytest.importorskip("pandas")


@pytest.fixture(scope="module")
def baseline():
    if not os.path.exists(BASELINE_PATH):
        pytest.skip("黄金基线未落盘: %s" % BASELINE_PATH)
    with open(BASELINE_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def gate():
    from feeders.qpzm_parquet import QpzmParquetFeeder
    from policies.v3_breadth40_med20 import V3Policy
    from api import EnvGate
    return EnvGate(V3Policy(), QpzmParquetFeeder())


def test_baseline_meta(baseline):
    meta = baseline["meta"]
    assert meta["n_days"] >= 240
    assert meta["window"]["start"] == "20251015"
    assert meta["window"]["end"] == "20261014"


def test_full_window_equivalence(gate, baseline):
    """一年窗全链等价：feeder 特征+policy 判定 vs 现行 env_gate 逐值一致。"""
    rows = baseline["env_daily"]
    assert rows, "基线 env_daily 为空"
    bad = []
    # 容差口径：基线取自 env_gate 的 DataFrame 输出（breadth.round(3)/
    # med20.round(4)，qpzm_backtest.py:142-143），gatekeeper 记 raw 值
    # （safe 在 round 之前算，口径一致）。breadth 容差 5e-4、med20
    # 容差 5e-5 = 基线舍入半格；safe 无舍入问题，保持严格全等。
    tol = {"breadth": 5e-4, "med20": 5e-5}
    n_safe = 0
    for r in rows:
        d = str(r["date"])
        dec = gate.decide("CN", d)
        for k in ("breadth", "med20"):
            exp, got = r[k], dec.features.get(k)
            if got is None or abs(float(got) - float(exp)) > tol[k]:
                bad.append((d, k, exp, got))
        if bool(dec.safe) != bool(r["safe"]):
            bad.append((d, "safe", r["safe"], dec.safe))
        else:
            n_safe += 1
    assert not bad, "等价性破坏 %d 处（前10）: %s" % (len(bad), bad[:10])
    assert n_safe == len(rows), "safe 全等计数异常 %d/%d" % (n_safe, len(rows))


def test_policy_sha_frozen():
    from policies.v3_breadth40_med20 import V3Policy
    p = V3Policy()
    assert p.intact, "v3 policy.json sha 校验失败（被手工改参）"
    assert len(p.sha) == 16
