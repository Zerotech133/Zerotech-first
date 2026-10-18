# -*- coding: utf-8 -*-
"""缠论学习环 L1-L4 全链单测（K-ORG-190）。

tmp 根隔离：全部注入点（_analyze/_enrich/_fingerprint/_load_close）
替换为确定性假实现，不依赖真实缠论引擎 / parquet / 真仓数据。
守卫覆盖（设计 §6）：
  · 幂等重放零副作用（ledger/verify/attribution/记忆/草案逐字节一致）
  · 同键异值拒绝（ValueError）
  · 坏行 fail-open
  · 显式阈值判读（n<30 观察 / 降置信候选 / 升置信候选）
  · L4 草案校验（调幅 ≤±0.05、shadow_until 必填、无生产参数写路径）
  · MemoryStore RBAC 越权降级
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_HERE = Path(__file__).resolve().parents[2] / "agent_platform" / "engine"
sys.path.insert(0, str(_HERE))
import chanlun_learn as cl


# ── 合成数据 ────────────────────────────────────────────────
N_DAYS = 400
DTS = [20240101 + i for i in range(N_DAYS)]      # 整数日键（8 位定长，字典序==数值序）

# 信号锚点：dt 索引 → 信号类型（analyze 在 bars 尾日 > dt 后可见；
# 窗口 d_start=150 处已有 151 根 > MIN_SEG=125，fresh 日=dt+1 过门槛）
SIG_AT = {150: "1买", 200: "2买", 250: "1卖"}
SIG_CONF = {"1买": (0.65, 2), "2买": (0.45, 3), "1卖": (0.55, 1)}


def _fake_analyze(bars):
    if not bars:
        return {}
    last = bars[-1]["dt"]
    sigs = [{"type": t, "dt": DTS[k], "price": 10.0,
             "mbei": t == "1买", "vv": t == "2卖"}
            for k, t in SIG_AT.items() if DTS[k] < last]
    return {"signals": sigs, "bis": [], "pivots": []}


def _fake_enrich(bars, st):
    for s in st.get("signals") or []:
        conf, n_prior = SIG_CONF[s["type"]]
        s["conf"] = conf
        s["n_prior"] = n_prior
        s["div_area"] = s["mbei"]


def _fake_fingerprint(st, sig):
    k = next((k for k, t in SIG_AT.items() if t == sig.get("type")), None)
    npv = 3 if k == 150 else (2 if k is not None else None)
    return {"bis6": "UDUDUD", "amp": "exp", "pv": "above", "npv": npv,
            "fp": "UDUDUD|exp|above|%d" % (npv or 0)}


def _fake_close_map():
    return {"600001.SH": {d: 10.0 + 0.05 * i for i, d in enumerate(DTS)},
            "000002.SZ": {d: 20.0 - 0.02 * i for i, d in enumerate(DTS)}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """tmp 全链环境：注入点替换 + learn_dir/data_dir 隔离。"""
    monkeypatch.setattr(cl, "_analyze", _fake_analyze)
    monkeypatch.setattr(cl, "_enrich", _fake_enrich)
    monkeypatch.setattr(cl, "_fingerprint", _fake_fingerprint)
    cm = _fake_close_map()
    monkeypatch.setattr(
        cl, "_load_bars",
        lambda code, data_dir=None: (
            [{"dt": d, "o": v, "h": v, "l": v, "c": v}
             for d, v in sorted(cm[code].items())], cm[code])
        if code in cm else (None, None))
    monkeypatch.setattr(cl, "_load_close",
                        lambda code, data_dir=None: cm.get(code))
    learn = tmp_path / "learn"
    learn.mkdir()
    return {"learn": str(learn), "tmp": tmp_path, "close_map": cm}


def _make_ledger_json(path, events, regime="mid", d_start=150, d_end=390):
    """daily_ledger JSON 假件：days[]（regime 全覆盖）+ sig 事件。"""
    days = {}
    for i in range(d_start, d_end + 1):
        days[str(DTS[i])] = {"regime": regime, "actions": []}
    for day_i, code, cat in events:
        t = "sig_buy" if cat == "buy" else "sig_sell"
        days[str(DTS[day_i])]["actions"].append(
            {"type": t, "stock": code, "note": "x"})
    json.dump({"meta": {}, "days": days},
              open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return str(path)


def _mem_store(tmp_path, write_extra=()):
    from memory import MemoryStore
    grants = {"ST1": {
        "read": ["agent/ST1/*", "dept/invest/*"],
        "write": ["agent/ST1/*", "dept/invest/observation",
                  "dept/invest/thesis", *write_extra]}}
    return MemoryStore(str(tmp_path / "mem"), grants_by_agent=grants,
                       enforce=True)


def _file_hash(p):
    import hashlib
    return hashlib.sha1(open(p, "rb").read()).hexdigest()


# ── L1 ──────────────────────────────────────────────────────
def test_l1_records_fresh_signals_and_memory(env):
    lj = _make_ledger_json(env["tmp"] / "lj.json", [
        (151, "600001.SH", "buy"),   # 1买@DTS[150] fresh@151
        (201, "600001.SH", "buy"),   # 2买@DTS[200] fresh@201
        (251, "600001.SH", "sell"),  # 1卖@DTS[250] fresh@251
    ])
    ms = _mem_store(env["tmp"])
    rep = cl.l1_run(lj, learn_dir=env["learn"], ms=ms)
    assert rep["recorded"] == 3 and not rep["findings"]
    rows = cl._read_jsonl(cl.ledger_path(env["learn"]))
    assert [r["type"] for r in rows] == ["1买", "2买", "1卖"]
    r0 = rows[0]
    assert r0["sig_id"] == cl._sig_id("600001.SH", "1买", DTS[150])
    assert r0["regime"] == "mid" and r0["level"] == "bi"
    assert r0["conf"] == 0.65 and r0["conf_parts"]["beichi"] is True
    assert r0["fingerprint"]["npv"] == 3            # 假指纹：1买@k150→npv=3
    assert r0["entry_px"] == pytest.approx(10.0 + 0.05 * 150)
    assert r0["declared_at"].endswith("T17:10:00+08:00")
    # 记忆腿：observation 已落 + 自述语
    obs = ms.read("ST1", "agent/ST1/observation")
    assert len(obs) == 3
    assert "影子记账" in obs[-1]["payload"]["note"]


def test_l1_replay_byte_identical(env):
    lj = _make_ledger_json(env["tmp"] / "lj.json", [
        (151, "600001.SH", "buy"), (251, "600001.SH", "sell")])
    cl.l1_run(lj, learn_dir=env["learn"])
    h1 = _file_hash(cl.ledger_path(env["learn"]))
    rep2 = cl.l1_run(lj, learn_dir=env["learn"])
    assert rep2["recorded"] == 0 and rep2["replayed"] == 2
    assert _file_hash(cl.ledger_path(env["learn"])) == h1  # 逐字节一致


def test_l1_same_key_diff_value_rejected(env):
    lj = _make_ledger_json(env["tmp"] / "lj.json",
                           [(151, "600001.SH", "buy")])
    cl.l1_run(lj, learn_dir=env["learn"])
    # 篡改已有行语义字段（conf）→ 重放同 sig_id 异内容必须拒绝
    lp = cl.ledger_path(env["learn"])
    rows = cl._read_jsonl(lp)
    rows[0]["conf"] = 0.99
    with open(lp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    with pytest.raises(ValueError, match="同键异值拒绝"):
        cl.l1_run(lj, learn_dir=env["learn"])


def test_l1_fallback_when_replay_not_fresh(env):
    # JSON 记账日晚于 fresh 日（迟到记账口径漂移）→ fallback + finding
    lj = _make_ledger_json(env["tmp"] / "lj.json",
                           [(170, "600001.SH", "buy")])
    rep = cl.l1_run(lj, learn_dir=env["learn"])
    assert rep["recorded"] == 1                     # fallback 仍记账
    assert any("fallback" in f for f in rep["findings"])


def test_l1_missing_market_data_finding(env, monkeypatch):
    monkeypatch.setattr(cl, "_load_bars",
                        lambda code, data_dir=None: (None, None))
    lj = _make_ledger_json(env["tmp"] / "lj.json",
                           [(151, "600001.SH", "buy")])
    rep = cl.l1_run(lj, learn_dir=env["learn"])
    assert rep["recorded"] == 0
    assert any("缺行情" in f for f in rep["findings"])


# ── L2 ──────────────────────────────────────────────────────
def _seed_ledger(env, n=1):
    rows = []
    for i in range(n):
        rows.append({
            "sig_id": "sig%03d" % i,
            "dt": DTS[100 + i], "found_day": str(DTS[100 + i]),
            "code": "600001.SH" if i % 2 == 0 else "000002.SZ",
            "type": "1买" if i % 2 == 0 else "1卖",
            "level": "bi", "regime": "mid",
            "fingerprint": {"bis6": "UDUDUD", "amp": "exp",
                            "pv": "above", "npv": 3},
            "conf": 0.65, "conf_parts": {"beichi": True, "vv": False,
                                         "multi_hub": True},
            "n_prior": 3, "entry_px": 10.0,
            "declared_at": "%sT17:10:00+08:00" % DTS[100 + i]})
    for r in rows:
        cl._append_line(cl.ledger_path(env["learn"]), r)
    return rows


def test_l2_verify_direction_idempotent_pending(env):
    _seed_ledger(env, n=2)                          # 0=1买(涨), 1=1卖(跌)
    rep = cl.l2_run(learn_dir=env["learn"],
                    close_map=env["close_map"])
    assert rep["recorded"] == 6 and rep["pending"] == 0
    vr = cl._read_jsonl(cl.verify_path(env["learn"]))
    by = {(r["sig_id"], r["horizon"]): r["ret_pct"] for r in vr}
    # 卖点反向口径：000002.SZ 收盘递减（看跌兑现）→ 反向后为正
    assert by[("sig000", 5)] > 0                    # 买类上涨 → 正
    assert by[("sig001", 5)] > 0                    # 卖类下跌兑现 → 反向为正
    # 幂等重放
    h1 = _file_hash(cl.verify_path(env["learn"]))
    rep2 = cl.l2_run(learn_dir=env["learn"], close_map=env["close_map"])
    assert rep2["recorded"] == 0 and rep2["replayed"] == 6
    assert _file_hash(cl.verify_path(env["learn"])) == h1


def test_l2_pending_when_horizon_beyond_data(env):
    rows = _seed_ledger(env, n=1)
    rows[0]["dt"] = DTS[N_DAYS - 1]                 # 尾日信号，T+5 出界
    lp = cl.ledger_path(env["learn"])
    with open(lp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    rep = cl.l2_run(learn_dir=env["learn"], close_map=env["close_map"])
    assert rep["recorded"] == 0 and rep["pending"] == 3   # 顺延不落行


def test_l2_same_key_diff_value_rejected(env):
    _seed_ledger(env, n=1)
    cl.l2_run(learn_dir=env["learn"], close_map=env["close_map"])
    vp = cl.verify_path(env["learn"])
    rows = cl._read_jsonl(vp)
    rows[0]["ret_pct"] = rows[0]["ret_pct"] + 1.0   # 后验漂移
    with open(vp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    with pytest.raises(ValueError, match="同键异值拒绝"):
        cl.l2_run(learn_dir=env["learn"], close_map=env["close_map"])


def test_l2_bad_line_fail_open(env):
    _seed_ledger(env, n=1)
    cl.l2_run(learn_dir=env["learn"], close_map=env["close_map"])
    with open(cl.verify_path(env["learn"]), "a", encoding="utf-8") as f:
        f.write("{broken json\n")
    rep = cl.l2_run(learn_dir=env["learn"], close_map=env["close_map"])
    assert rep["replayed"] == 3                     # 坏行跳过不拖账


# ── L3 ──────────────────────────────────────────────────────
def _seed_attr_env(env):
    """两组 n=31 + 一组 n=3：
    cellX=1买（31 行 ret=+5，npv=3p）→ 全域基线被压低 → 升置信候选；
    cellY=1卖（31 行 ret=-15，npv=3p）→ avg<0 且 p10≤-10 → 降置信候选；
    cellZ=2买（3 行）→ n<30 只观察。"""
    for i in range(31):
        r = {"sig_id": "x%02d" % i, "code": "600001.SH",
             "type": "1买", "regime": "mid", "conf": 0.65,
             "fingerprint": {"npv": 3, "bis6": "U", "amp": "exp",
                             "pv": "above"},
             "dt": DTS[i], "found_day": str(DTS[i]),
             "entry_px": 10.0, "level": "bi",
             "conf_parts": {"beichi": True, "vv": False, "multi_hub": True},
             "n_prior": 3, "declared_at": ""}
        v = {"verify_key": "vk_x%02d" % i, "sig_id": "x%02d" % i,
             "code": "600001.SH", "type": "1买", "horizon": 10,
             "dt": DTS[i], "verify_dt": DTS[i + 10], "ret_pct": 5.0,
             "declared_at": ""}
        cl._append_line(cl.ledger_path(env["learn"]), r)
        cl._append_line(cl.verify_path(env["learn"]), v)
    for i in range(31):
        r = {"sig_id": "y%02d" % i, "code": "000002.SZ",
             "type": "1卖", "regime": "low", "conf": 0.45,
             "fingerprint": {"npv": 3, "bis6": "D", "amp": "con",
                             "pv": "below"},
             "dt": DTS[i], "found_day": str(DTS[i]),
             "entry_px": 20.0, "level": "bi",
             "conf_parts": {"beichi": False, "vv": False, "multi_hub": True},
             "n_prior": 3, "declared_at": ""}
        v = {"verify_key": "vk_y%02d" % i, "sig_id": "y%02d" % i,
             "code": "000002.SZ", "type": "1卖", "horizon": 10,
             "dt": DTS[i], "verify_dt": DTS[i + 10], "ret_pct": -15.0,
             "declared_at": ""}
        cl._append_line(cl.ledger_path(env["learn"]), r)
        cl._append_line(cl.verify_path(env["learn"]), v)
    for i in range(3):
        r = {"sig_id": "z%02d" % i, "code": "600001.SH",
             "type": "2买", "regime": "high", "conf": 0.55,
             "fingerprint": {"npv": 2, "bis6": "U", "amp": "flat",
                             "pv": "in"},
             "dt": DTS[i], "found_day": str(DTS[i]),
             "entry_px": 10.0, "level": "bi",
             "conf_parts": {"beichi": False, "vv": True, "multi_hub": False},
             "n_prior": 2, "declared_at": ""}
        v = {"verify_key": "vk_z%02d" % i, "sig_id": "z%02d" % i,
             "code": "600001.SH", "type": "2买", "horizon": 10,
             "dt": DTS[i], "verify_dt": DTS[i + 10], "ret_pct": 1.0,
             "declared_at": ""}
        cl._append_line(cl.ledger_path(env["learn"]), r)
        cl._append_line(cl.verify_path(env["learn"]), v)


def test_l3_verdict_thresholds(env):
    _seed_attr_env(env)
    rep = cl.l3_run(learn_dir=env["learn"], as_of="20261018")
    assert rep["recorded"] > 0
    rows = cl._read_jsonl(cl.attribution_path(env["learn"]))
    by = {r["cell"]: r for r in rows if r["horizon"] == 10}
    up = by["type=1买|regime=mid|npv=3p"]
    assert up["verdict"] == "升置信候选" and up["n"] == 31
    assert up["evidence"]["baseline_win"] is not None
    assert len(up["evidence"]["samples"]) <= 5
    dn = by["type=1卖|regime=low|npv=3p"]
    assert dn["verdict"] == "降置信候选"
    assert dn["avg"] < 0 and dn["p10"] <= -10
    obs = by["type=2买|regime=high|npv=le2"]
    assert obs["verdict"] == "observation_only" and obs["n"] == 3
    # 全部 verdict 行带 evidence 与 n（设计 §6 回放验收）
    assert all(r.get("evidence") and r.get("n") is not None for r in rows)


def test_l3_replay_idempotent(env):
    _seed_attr_env(env)
    cl.l3_run(learn_dir=env["learn"], as_of="20261018")
    h1 = _file_hash(cl.attribution_path(env["learn"]))
    rep2 = cl.l3_run(learn_dir=env["learn"], as_of="20261019")  # as_of 不同
    assert rep2["recorded"] == 0                    # 同统计指纹零副作用
    assert _file_hash(cl.attribution_path(env["learn"])) == h1


# ── L4 ──────────────────────────────────────────────────────
def test_l4_drafts_step_bound_and_shadow_until(env):
    _seed_attr_env(env)
    cl.l3_run(learn_dir=env["learn"], as_of="20261018")
    from datetime import datetime
    rep = cl.l4_drafts(learn_dir=env["learn"],
                       now=datetime(2026, 10, 18))
    assert rep["drafted"] >= 1
    ddir = cl.drafts_dir(env["learn"])
    drafts = [json.load(open(os.path.join(ddir, f), encoding="utf-8"))
              for f in os.listdir(ddir)]
    assert all(d["param"] == "multi_hub_decay" for d in drafts)
    assert all(abs(d["to"] - d["from"]) <= 0.05 + 1e-9 for d in drafts)
    assert all(d["shadow_until"] == "2026-11-15" for d in drafts)  # +28 天
    assert all(d["rationale"] and "draft 建议权" in d["rationale"]
               for d in drafts)
    down = [d for d in drafts if d["to"] < d["from"]]
    assert down and all(d["from"] == 0.85 and d["to"] == 0.80
                        for d in down)
    # 幂等：未过期同 fingerprint 跳过
    rep2 = cl.l4_drafts(learn_dir=env["learn"],
                        now=datetime(2026, 10, 18))
    assert rep2["drafted"] == 0 and rep2["skipped"] >= 1
    # 无映射 cell 全部留人工判读
    assert rep["manual"] and all("无对应可学习权重位" in m["why"]
                                 for m in rep["manual"])


def test_l4_no_production_param_writer(env):
    """影子铁律：模块不得提供直改生产参数的函数。"""
    assert not any("writ" in n.lower() and "param" in n.lower()
                   for n in dir(cl))
    src = Path(cl.__file__).read_text(encoding="utf-8")
    assert "chanlun_gray" in src.replace("from chanlun_gray import enrich",
                                         "")  # 仅 enrich 导入
    assert "PARAM_MAP" in src and "0.85" in src  # 参数值只出现在映射表/草案


def test_l4_thesis_left_tail_streak_and_dept(env):
    _seed_attr_env(env)
    cl.l3_run(learn_dir=env["learn"], as_of="20261018")
    ms = _mem_store(env["tmp"])
    rep = cl.l4_thesis(learn_dir=env["learn"], ms=ms)
    assert rep["st_thesis"] >= 1 and rep["dept_thesis"] >= 1
    th = ms.read("ST1", "agent/ST1/thesis")
    kinds = {t["payload"]["kind"] for t in th}
    assert "left_tail" in kinds and "streak_loss" in kinds
    dept = ms.read("ST1", "dept/invest/thesis")
    assert any("降置信候选" in t["payload"]["note"] for t in dept)
    # 判重幂等：重跑零新增
    h_before = len(th) + len(dept)
    cl.l4_thesis(learn_dir=env["learn"], ms=ms)
    assert len(ms.read("ST1", "agent/ST1/thesis")) + \
        len(ms.read("ST1", "dept/invest/thesis")) == h_before


def test_l4_thesis_rbac_denied_degrades(env):
    """越权写 dept/invest/thesis → 捕获为 finding 不拖账。"""
    _seed_attr_env(env)
    cl.l3_run(learn_dir=env["learn"], as_of="20261018")
    from memory import MemoryStore
    ms = MemoryStore(str(env["tmp"] / "mem2"),
                     grants_by_agent={"ST1": {"read": ["agent/ST1/*"],
                                              "write": ["agent/ST1/*"]}},
                     enforce=True)
    rep = cl.l4_thesis(learn_dir=env["learn"], ms=ms)
    assert rep["dept_thesis"] == 0
    assert any("RBAC" in f or "写入失败" in f for f in rep["findings"])


def test_l4_weekly_output(env):
    _seed_attr_env(env)
    cl.l3_run(learn_dir=env["learn"], as_of="20261018")
    text = cl.l4_weekly(learn_dir=env["learn"])
    assert "缠论结构观察" in text and "影子铁律" in text


# ── memory_store() 构造 ─────────────────────────────────────
def test_memory_store_loads_grants_from_org(tmp_path):
    org = {"departments": [{"dept": "invest", "name": "量化交易部",
                            "agent_profiles": [
                                {"agent_id": "ST1",
                                 "memory_grants": {
                                     "read": ["domain/*"],
                                     "write": ["agent/ST1/*"]}}]}]}
    org_p = tmp_path / "org.json"
    org_p.write_text(json.dumps(org), encoding="utf-8")
    ms = cl.memory_store(str(tmp_path / "mem"), str(org_p))
    ms.write("ST1", "agent/ST1/observation", {"x": 1})   # 不抛=grants 生效
    with pytest.raises(Exception):
        ms.write("ST1", "dept/quality/observation", {"x": 1})


# ── CLI 冒烟 ────────────────────────────────────────────────
def test_cli_all_smoke(env, capsys):
    lj = _make_ledger_json(env["tmp"] / "lj.json",
                           [(151, "600001.SH", "buy")])
    rc = cl.main(["all", "--ledger", lj, "--learn-dir", env["learn"],
                  "--memory-dir", str(env["tmp"] / "mem_cli"),
                  "--no-memory"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[L1]" in out and "[L2]" in out and "[L3]" in out
