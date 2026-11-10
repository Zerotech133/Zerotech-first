# -*- coding: utf-8 -*-
"""候风端到端回归（P2-b）：把"改裁判""账本跨版本""页面 JSON 非法""固定 tmp"四条
最容易复发的问题变成会红的测试。

判据（任一不过即为回归）：
  1. 基线锁里有裁判层，且 md5 与当前文件一致 —— 改 portfolio_sim 不重冻会红。
  2. 篡改锁里的裁判 md5 时，verify_baseline 必须判 FAIL 并指出 judge。
  3. atomic_io.replacing 在多线程同写同一文件时 0 失败、无残留 .tmp。
  4. 页面 JSON 全部严格可解析（无裸 NaN）—— 浏览器 JSON.parse 同款判据。
  5. 账本每行有 rule_version；v1.0.4 并列账本与差异报告存在且自洽。
  6. 候风链路不得再出现固定 .tmp 写法。
  7. guardian 报告的 SL/TP 与执行口径同源（真实样本，1 笔）。
"""
import copy
import hashlib
import importlib.util
import json
import os
import sys
import threading

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PAPER = os.path.join(HERE, "_qpzm_rd_20261106", "paper")
DASH = os.path.join(HERE, "_qpzm_rd_20261106", "dashboard")
LOCK = os.path.join(HERE, "_qpzm_audit_20261105", "t7_lock", "baseline_lock.json")
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if PAPER not in sys.path:
    sys.path.insert(0, PAPER)


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def _md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


# ── 1. 裁判层在锁内且未被偷改 ─────────────────────────────────────────
def test_judge_file_is_frozen_in_lock():
    lock = json.load(open(LOCK, encoding="utf-8"))
    ef = lock["frozen_manifest"]["engine_files"]
    assert "portfolio_sim.py" in ef, "裁判层未纳入基线锁（L-01 回退）"
    e = ef["portfolio_sim.py"]
    assert e["role"] == "judge"
    cur = _md5(os.path.join(HERE, "portfolio_sim.py"))
    assert cur == e["md5"], (
        "portfolio_sim.py 已改但未重冻指纹：历史 total_return/max_drawdown "
        "不可与新结果混引。改完请更新 baseline_lock.json 再引用数字。")


# ── 2. 篡改裁判 md5 时校验器必须 FAIL ────────────────────────────────
def test_verify_flags_judge_change():
    vb = _load(os.path.join(HERE, "_qpzm_audit_20261105", "t7_lock",
                            "verify_baseline.py"), "verify_baseline_mod")
    lock = json.load(open(LOCK, encoding="utf-8"))
    tampered = copy.deepcopy(lock)
    tampered["frozen_manifest"]["engine_files"]["portfolio_sim.py"]["md5"] = "0" * 32
    res = vb.check_fingerprint(tampered)
    assert res["ok"] is False
    bad = [c for c in res["checks"] if not c["ok"]]
    assert bad and bad[0]["role"] == "judge", "裁判层不一致应排在首位报警"
    assert "裁判" in bad[0]["note"]


# ── 3. 原子写并发安全 ────────────────────────────────────────────────
def test_atomic_io_replacing_threads(tmp_path):
    import atomic_io
    out = str(tmp_path / "same.json")
    errs = []
    ok = [0]

    def worker(i):
        for k in range(40):
            try:
                with atomic_io.replacing(out) as f:
                    json.dump({"i": i, "k": k, "pad": "x" * 1500}, f)
                ok[0] += 1
            except Exception as e:      # noqa: BLE001
                errs.append("%s: %s" % (type(e).__name__, e))

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, "并发写失败 %d 次，首个：%s" % (len(errs), errs[0])
    assert ok[0] == 320
    json.load(open(out, encoding="utf-8"))            # 终态完整
    assert not [x for x in os.listdir(str(tmp_path)) if x.endswith(".tmp")]


# ── 4. 页面 JSON 严格可解析（裸 NaN 会让整页白屏）─────────────────────
def test_dashboard_json_are_strictly_parsable():
    def boom(c):
        raise ValueError("非法常量 %s（浏览器 JSON.parse 会失败）" % c)

    files = [f for f in os.listdir(DASH) if f.endswith(".json")]
    assert len(files) >= 5
    bad = []
    for f in files:
        try:
            json.loads(open(os.path.join(DASH, f), encoding="utf-8").read(),
                       parse_constant=boom)
        except Exception as e:      # noqa: BLE001
            bad.append("%s: %s" % (f, e))
    assert not bad, "页面 JSON 不合法：" + "; ".join(bad)


# ── 5. 账本版本标记与并列回放 ────────────────────────────────────────
def test_ledger_rows_carry_rule_version():
    led = os.path.join(PAPER, "execution_ledger.jsonl")
    vers = set()
    n = 0
    for line in open(led, encoding="utf-8"):
        if not line.strip():
            continue
        n += 1
        vers.add(json.loads(line).get("rule_version"))
    assert n == 3843, "原账行数被改动（应为 3843），审计记录不可静默变化"
    assert vers == {"v1.0.3"}, "原账应全部标 v1.0.3，实际 %s" % vers


def test_v104_replay_is_parallel_not_overwrite():
    lead = os.path.join(PAPER, "execution_ledger_v104.jsonl")
    diff = os.path.join(PAPER, "execution_ledger_diff_v103_v104.json")
    assert os.path.exists(lead) and os.path.exists(diff)
    rows = [json.loads(l) for l in open(lead, encoding="utf-8") if l.strip()]
    assert len(rows) == 3843
    assert {r["rule_version"] for r in rows} == {"v1.0.4"}
    d = json.load(open(diff, encoding="utf-8"))
    assert d["n_common"] == 3843 and d["n_only_old"] == 0 and d["n_only_new"] == 0
    assert d["n_reason_changed"] == len(d["reason_changes"])
    # 差异必须可解释：v1.0.4 只可能把 TP 降级为 hold（去掉了当日高点这一前置条件）
    for x in d["reason_changes"]:
        assert x["old"] == "TP" and x["new"] == "hold"


# ── 6. 候风链路禁止固定 .tmp ─────────────────────────────────────────
def test_houfeng_chain_has_no_fixed_tmp():
    hits = []
    for base in (PAPER, DASH, os.path.join(HERE, "_qpzm_rd_20261106", "maintenance")):
        if not os.path.isdir(base):
            continue
        for dp, dns, fns in os.walk(base):
            if os.path.basename(dp) == "bak":      # 备份目录保留原始写法，不算违规
                dns[:] = []
                continue
            for fn in fns:
                if not fn.endswith(".py") or fn.startswith("patch_") \
                        or fn.startswith("migrate_") or fn.startswith("backfill_"):
                    continue
                p = os.path.join(dp, fn)
                s = open(p, encoding="utf-8", errors="ignore").read()
                for i, line in enumerate(s.split("\n"), 1):
                    if '.tmp"' in line and "mkstemp" not in line and "临时" not in line:
                        hits.append("%s:%d %s" % (fn, i, line.strip()[:70]))
    assert not hits, "候风链路仍有固定 .tmp（并发会互踩）：\n" + "\n".join(hits)


# ── 7. 报告 SL/TP 与执行同源（真实样本）─────────────────────────────
@pytest.mark.skipif(not os.path.exists(os.path.join(PAPER, "_replay_12m_trades.json")),
                    reason="缺少回放逐笔样本")
def test_guardian_report_matches_execute_basis():
    import pandas as pd
    g = _load(os.path.join(PAPER, "guardian_agent_v1.0.4.py"), "guardian_e2e")
    trades = json.load(open(os.path.join(PAPER, "_replay_12m_trades.json"),
                            encoding="utf-8"))["trades"]
    # 头部样本可能贴着数据边界（上市不足 15 根日线），往后找第一个能算出 ATR 的
    got = t = None
    for cand in trades[:40]:
        day = int(cand["entry_date"]) + 1
        got = g.guard_one(cand, day)
        if got:
            t = cand
            break
    if not got:
        pytest.skip("前 40 笔样本都算不出 ATR，跳过")
    day = int(t["entry_date"]) + 1
    df = g.daily_df(t["code"].replace(".", "_"))
    hh = pd.to_numeric(df["high"], errors="coerce")
    seg = df.index[(df.td >= str(t["entry_date"])) & (df.td < str(day))]
    ep = float(t["entry_px"])
    hi_prior = float(hh.loc[seg].max()) if len(seg) else ep
    a = got["atr14"]
    assert abs(got["SL"] - max(hi_prior - 2.0 * a, ep * 0.96)) < 0.01
    assert abs(got["TP"] - (hi_prior - 1.0 * a)) < 0.01
    assert got["hi_prior"] == round(hi_prior, 2)
