# -*- coding: utf-8 -*-
"""L-08：kg_sector_flow 回放的行业/月份两维分布。

判据（对应遗留清单 L-08 的 accept「报告含两维分布」）：
  1. 分布是**纯函数**——不连图也能算，故可在无 neo4j 的机器上回归；
  2. 计数口径唯一：按触发单数（一单一行），不是上榜股数、不是去重行业日；
  3. replay 编排在 dryrun 下不落任何文件（历史回放「只打印」语义不破）。
"""

import json
import os

import pytest

import kg_sector_flow as KS


# ── ym_of：月份键 ─────────────────────────────────────────────
@pytest.mark.parametrize(
    "raw,expect",
    [
        ("20260929", "202609"),
        ("20251028", "202510"),
        ("", "unknown"),
        (None, "unknown"),
        ("2026", "unknown"),  # 短串不得被截成「2026」当月份
        ("2026092", "unknown"),  # 7 位不得补零
        ("20260929 ", "unknown"),  # 带空白不猜
        ("abcdefgh", "unknown"),
    ],
)
def test_ym_of(raw, expect):
    assert KS.ym_of(raw) == expect


# ── dist_of：两维分布 ────────────────────────────────────────
def test_dist_of_empty_is_wellformed():
    d = KS.dist_of([])
    ind = d["industry_dist"]
    assert ind["top"] == [] and ind["total"] == 0
    assert ind["other_n"] == 0 and ind["industry_tail_n"] == 0
    assert ind["industry_total_n"] == 0
    assert d["month_dist"] == []


def test_dist_of_counts_tickets_not_distinct_days():
    """同日同行业 3 单 → 行业计数 3（口径：触发单数）。"""
    pairs = [("20260929", "元器件")] * 3
    d = KS.dist_of(pairs)
    assert d["industry_dist"]["top"] == [{"industry": "元器件", "n": 3}]
    assert d["industry_dist"]["total"] == 3
    assert d["month_dist"] == [{"ym": "202609", "n": 3}]


def test_dist_of_industry_topk_and_tail():
    # 12 个行业，触发次数 12,11,...,1；topk=10 → 尾部 2 个（2+1=3 单）
    pairs = []
    for i in range(12, 0, -1):
        pairs += [("20260929", "IND%02d" % i)] * i
    d = KS.dist_of(pairs, topk=10)
    ind = d["industry_dist"]
    assert len(ind["top"]) == 10
    assert [x["n"] for x in ind["top"]] == list(range(12, 2, -1))
    assert ind["other_n"] == 3
    assert ind["industry_tail_n"] == 2
    assert ind["industry_total_n"] == 12
    assert ind["total"] == sum(range(1, 13)) == 78


def test_dist_of_industry_tie_broken_by_name():
    pairs = [("20260929", "B")] * 2 + [("20260929", "A")] * 2
    top = KS.dist_of(pairs)["industry_dist"]["top"]
    assert [x["industry"] for x in top] == ["A", "B"]  # 同频按名称稳定排序


def test_dist_of_month_is_ascending_not_by_freq():
    # 输入乱序且低频在前；输出必须按月升序（时间序），跨年不得倒置
    pairs = [
        ("20260105", "X"),
        ("20251201", "X"),
        ("20251215", "X"),
        ("20260120", "X"),
    ]
    md = KS.dist_of(pairs)["month_dist"]
    assert [x["ym"] for x in md] == ["202512", "202601"]
    assert [x["n"] for x in md] == [2, 2]


def test_dist_of_unknown_date_bucket_is_visible():
    """坏日期不能被静默丢掉——必须落在 unknown 桶里可见。"""
    d = KS.dist_of([("20260929", "X"), ("bad", "X")])
    md = d["month_dist"]
    assert {"ym": "unknown", "n": 1} in md
    assert sum(x["n"] for x in md) == d["industry_dist"]["total"] == 2


def test_dist_of_is_strict_json_serializable():
    d = KS.dist_of([("20260929", "X")])
    json.dumps(d, ensure_ascii=False, allow_nan=False)


# ── replay 编排：dryrun 不落盘 ───────────────────────────────
def test_replay_dryrun_writes_no_file(monkeypatch, tmp_path):
    days = ["20260929", "20260930"]
    monkeypatch.setattr(KS.KP, "_q", lambda cy, **kw: [{"d": d} for d in days[: kw.get("n", 2)]])
    monkeypatch.setattr(
        KS,
        "load_day",
        lambda d: [
            {"tc": "00000%d.SZ" % i, "nm": "S%d" % i, "ind": "电气设备", "yz": 1e7}
            for i in range(6)
        ],
    )
    monkeypatch.setattr(KS, "CALIB_F", str(tmp_path / "calib.json"))
    doc = KS.replay(2, dryrun=True)
    assert not os.path.exists(str(tmp_path / "calib.json"))
    assert doc["n_days"] == 2
    assert len(doc["grid"]) == 12
    assert doc["month_dist"] == [{"ym": "202609", "n": doc["industry_dist"]["total"]}]
    assert doc["industry_dist"]["top"][0]["industry"] == "电气设备"


def test_replay_persists_calib_when_not_dryrun(monkeypatch, tmp_path):
    monkeypatch.setattr(KS.KP, "_q", lambda cy, **kw: [{"d": "20260929"}])
    monkeypatch.setattr(
        KS,
        "load_day",
        lambda d: [
            {"tc": "000001.SZ", "nm": "S", "ind": "元器件", "yz": 1e7} for _ in range(6)
        ],
    )
    target = str(tmp_path / "calib.json")
    monkeypatch.setattr(KS, "CALIB_F", target)
    KS.replay(1, dryrun=False)
    with open(target, encoding="utf-8") as f:
        back = json.load(f)
    assert set(back) >= {"industry_dist", "month_dist", "grid", "rule", "window"}
    assert back["rule"] == {"n_min": KS.N_MIN, "m_min": KS.M_MIN}


# ── 已生成的档案（若存在）：契约 ─────────────────────────────
def test_calib_artifact_has_two_dimensions():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "reports", "sector_flow_calib.json")
    if not os.path.exists(p):
        pytest.skip("尚无 calib 档案（先跑 --replay）")
    with open(p, encoding="utf-8") as f:
        raw = f.read()
    json.loads(raw)  # 严格解析
    d = json.loads(raw)
    assert set(d) >= {"industry_dist", "month_dist", "grid", "count_basis"}
    assert len(d["grid"]) == 12
    assert d["industry_dist"]["total"] == sum(x["n"] for x in d["month_dist"])
    assert d["industry_dist"]["total"] == sum(x["n_tickets"] for x in d["grid"]
                                              if (x["n_min"], x["m_min"]) ==
                                              (d["rule"]["n_min"], d["rule"]["m_min"]))
    assert d["industry_dist"]["top"] == sorted(
        d["industry_dist"]["top"], key=lambda x: (-x["n"], x["industry"])
    )
    assert [x["ym"] for x in d["month_dist"]] == sorted(x["ym"] for x in d["month_dist"])
