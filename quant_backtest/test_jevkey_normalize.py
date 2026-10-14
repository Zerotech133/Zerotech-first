# -*- coding: utf-8 -*-
"""W5c 键形态修复单测：normalize_jev_key + load_jev_replay 键归一化。

覆盖:
  1. sh/sz/bj 前缀映射 → wide 全码形态（688025.SH / 301047.SZ / 430047.BJ）
  2. 已是全码形态输入幂等（含小写后缀）
  3. 无法识别键（gb_*/加密币/乱码）返回 None，load_jev_replay 跳过并计数
  4. 修复后 reindex 命中：合成数据上 replay 态与 missing 态不再逐元素等价，
     命中格点的 jev 分真实进复合分

运行:
  "C:/Program Files/Python311/python.exe" C:/aios/quant_backtest/test_jevkey_normalize.py
"""
import json
import os
import sys
import tempfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import qpzm_backtest as qb   # noqa: E402

PASS = []


def check(name, cond):
    PASS.append((name, bool(cond)))
    status = "PASS" if cond else "FAIL"
    print("[%s] %s" % (status, name))


# ── 1. sh/sz/bj 前缀映射 ────────────────────────────────────

check("sh 前缀 → .SH", qb.normalize_jev_key("sh688025") == "688025.SH")
check("sz 前缀 → .SZ", qb.normalize_jev_key("sz301047") == "301047.SZ")
check("bj 前缀 → .BJ", qb.normalize_jev_key("bj430047") == "430047.BJ")
check("大写前缀也可识别", qb.normalize_jev_key("SH600400") == "600400.SH")

# ── 2. 全码输入幂等 ─────────────────────────────────────────

check("全码大写幂等", qb.normalize_jev_key("688025.SH") == "688025.SH")
check("全码小写后缀归一", qb.normalize_jev_key("688025.sh") == "688025.SH")
check("全码 300 前缀", qb.normalize_jev_key("300684.sz") == "300684.SZ")

# ── 3. 无法识别键 ────────────────────────────────────────────

for bad in ("gb_nvda", "BTC/USDT", "ETH/USDT", "", "sh12345", "hk00700"):
    check("无法识别键 %r → None" % bad, qb.normalize_jev_key(bad) is None)

# ── 4. load_jev_replay：跳过计数 + reindex 命中（合成数据） ──

payload = {
    "meta": {"start": "2026-09-25", "end": "2026-09-26", "days": 2,
             "partial_last_day": False},
    "series": {
        "2026-09-25": {
            "sh688025": {"score": 88.0},
            "sz300684": {"score": 60.0},
            "gb_nvda": {"score": 99.0},
            "BTC/USDT": {"score": 50.0},
        },
        "2026-09-26": {
            "688025.sh": {"score": 70.0},
            "bj430047": {"score": 55.0},
        },
    },
}
tmpdir = tempfile.mkdtemp()
fpath = os.path.join(tmpdir, "jev_replay_series.json")
with open(fpath, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False)

mat, meta = qb.load_jev_replay(fpath)
check("列形态全为 wide 全码", set(mat.columns) == {
    "688025.SH", "300684.SZ", "430047.BJ"})
check("gb/币键未混入列", not any(
    ("nvda" in c or "BTC" in c or "USDT" in c) for c in mat.columns))
check("无法识别键计数=2", meta.get("unrecognized_keys_skipped") == 2)
check("无法识别键清单", set(meta.get("unrecognized_keys", [])) == {
    "gb_nvda", "BTC/USDT"})
check("日期键转 int64 对齐", list(mat.index) == [20260925, 20260926])
check("幂等键写入同一列（688025.sh→688025.SH）",
      abs(mat.loc[20260926, "688025.SH"] - 70.0) < 1e-9)

# ── 5. 修复后 reindex 命中：replay ≠ missing ────────────────

dates = np.array([20260925, 20260926], dtype=np.int64)
codes = ["688025.SH", "300684.SZ", "600000.SH"]
close = pd.DataFrame([[10.0, 20.0, 30.0], [11.0, 21.0, 29.0]],
                     index=dates, columns=codes)
longs = pd.DataFrame([[3.0, 3.0, 3.0], [4.0, 2.0, 4.0]],
                     index=dates, columns=codes)
above20 = pd.DataFrame([[True, True, True], [True, True, True]],
                       index=dates, columns=codes)
tech = qb.tech_score(longs, above20)
rps_mix = pd.DataFrame(70.0, index=dates, columns=codes)
env_df = pd.DataFrame({"score": [80.0, 80.0], "safe": [True, True],
                       "breadth": [0.8, 0.8], "med20": [2.0, 2.0],
                       "upfrac": [0.7, 0.7]}, index=dates)
eligible = pd.DataFrame(True, index=dates, columns=codes)

# 合成 jev_mat：直接用归档加载结果 reindex（688025.SH/300684.SZ 应命中）
jev_mat, _ = qb.load_jev_replay(fpath)

sc_missing, buy_miss, _ = qb.combine_score(
    tech, rps_mix, env_df, eligible=eligible, jev_mode="missing")
sc_replay, buy_rep, _ = qb.combine_score(
    tech, rps_mix, env_df, eligible=eligible, jev_mode="replay",
    jev_mat=jev_mat)

# 命中格点：(20260925, 688025.SH) score 88 与 (20260925, 300684.SZ) score 60
hit = ~np.isnan(jev_mat.reindex(index=dates, columns=codes).to_numpy(float))
check("reindex 命中格点数 ≥2", int(hit.sum()) >= 2)
diff = (sc_missing.to_numpy(float) - sc_replay.to_numpy(float))
check("replay 与 missing 复合分不再逐元素等价",
      np.nanmax(np.abs(diff)) > 1e-9)
# 命中格点 replay 分应更高（88 > 缺维 50 中性不进权重 → 分母变大但分子加成）
check("命中格点 replay 分与 missing 分不同",
      abs(sc_replay.loc[20260925, "688025.SH"]
          - sc_missing.loc[20260925, "688025.SH"]) > 1e-9)

# ── 汇总 ─────────────────────────────────────────────────────

def test_all():
    """pytest 入口：模块级 check 已在 import 时执行完。"""
    n_fail = sum(1 for _, ok in PASS if not ok)
    assert n_fail == 0, "%d/%d checks failed" % (n_fail, len(PASS))


if __name__ == "__main__":
    n_fail = sum(1 for _, ok in PASS if not ok)
    print("\n%d checks, %d failed" % (len(PASS), n_fail))
    sys.exit(1 if n_fail else 0)
