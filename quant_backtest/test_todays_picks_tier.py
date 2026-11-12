# -*- coding: utf-8 -*-
"""T1 聚合器口径金测（十五审 2026-10-10）：锁 tier_of 判定矩阵与
load_chanlun 坏行容错——一~十四审累计八条分支（共振计数/cross 旁路/
同批买卖并存/qpzm 回避/weak 否决/ST 封顶/veto 停挂/T2 三通道）此前
无任何自动化保护，全靠手跑回归。

挂 PytestWeekly（周一 05:30）每周复核；被测模块在 portal/，跨目录 import。
"""

import json
import os
import sys

import pytest

PORTAL = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "portal"))
sys.path.insert(0, PORTAL)

from build_todays_picks import _stale_meta, tier_of  # noqa: E402
from build_todays_picks import _backfill_quotes  # noqa: E402
from build_todays_picks import gate_failed_sources, _push_source_alert  # noqa: E402
import build_todays_picks as btp  # noqa: E402
from build_pick_verdicts import pct_between  # noqa: E402


def _e(name, *srcs):
    return {"code": "600000.SH", "name": name, "sources": list(srcs)}


STRONG_LEAD = {"s": "strong", "tag": "板块领涨"}
STRONG_COUNTER = {"s": "strong", "tag": "逆板块强势"}
CL_BUY = {"s": "chanlun", "tag": "3买", "dt": "2026-09-16"}
CL_SELL = {"s": "chanlun", "tag": "1卖", "dt": "2026-09-16"}
QPZM_BUY = {"s": "qpzm", "tag": "买入", "score": 85}
QPZM_BUY_LOW = {"s": "qpzm", "tag": "买入", "score": 70}
QPZM_AVOID = {"s": "qpzm", "tag": "回避"}
WEAK = {"s": "weak", "tag": "强失速"}
CROSS = {"s": "cross", "tag": "多策略共振"}
NINEDIM_ALERT = {"s": "ninedim", "tag": "九维出分", "score": 78, "band": "alert"}
NINEDIM_ALERT2 = {"s": "ninedim", "tag": "九维出分", "score": 86, "band": "alert2"}
NINEDIM_LOW = {"s": "ninedim", "tag": "九维出分", "score": 59, "band": "low"}


# ── T1 共振路径 ──────────────────────────────────────────────
def test_t1_strong_chanlun():
    assert tier_of(_e("亚邦股份", STRONG_LEAD, CL_BUY)) == "T1"


def test_t1_qpzm_chanlun():
    assert tier_of(_e("某票", QPZM_BUY, CL_BUY)) == "T1"


def test_t1_qpzm_strong():
    assert tier_of(_e("某票", QPZM_BUY, STRONG_LEAD)) == "T1"


def test_t1_cross_bypass():
    """cross 单源直接 T1（n_long>=2 派生，守卫在 load_cross 侧）。"""
    assert tier_of(_e("某票", CROSS)) == "T1"


def test_not_t1_counter_strong_chanlun():
    """逆板块强势不计 bull（三审方向盲区修正）→ 单 chanlun 买落 T2。"""
    assert tier_of(_e("某票", STRONG_COUNTER, CL_BUY)) == "T2"


# ── 否决闸门 ────────────────────────────────────────────────
def test_veto_cl_sell_same_batch():
    """同批买卖并存：买点不计共振（四审）→ chanlun 买通道 T2。"""
    assert tier_of(_e("某票", STRONG_LEAD, CL_BUY, CL_SELL)) == "T2"


def test_veto_qpzm_avoid():
    """qpzm 回避一票否决 T1（四审）。"""
    assert tier_of(_e("某票", STRONG_LEAD, CL_BUY, QPZM_AVOID)) == "T2"


def test_veto_weak():
    """weak 负面否决 T1（五审）。"""
    assert tier_of(_e("某票", STRONG_LEAD, CL_BUY, WEAK)) == "T2"


def test_veto_st_caps_t3():
    """ST/*ST 不进 T1/T2，封顶 T3（七审）。"""
    assert tier_of(_e("*ST某股", STRONG_LEAD, CL_BUY)) == "T3"
    assert tier_of(_e("ST某股", CL_BUY)) == "T3"


def test_veto_suspended_t1():
    """否决源 err 失败当日 T1 停挂（十一审）：共振票降 T2。"""
    assert tier_of(_e("某票", STRONG_LEAD, CL_BUY), veto_ok=False) == "T2"
    assert tier_of(_e("某票", CROSS), veto_ok=False) == "T3"  # 无单策略通道


# ── T2 三通道 / CROWD / T3 ──────────────────────────────────
def test_t2_single_chanlun():
    assert tier_of(_e("某票", CL_BUY)) == "T2"


def test_t2_single_strong_lead():
    assert tier_of(_e("某票", STRONG_LEAD)) == "T2"


def test_t2_qpzm_score80():
    assert tier_of(_e("某票", QPZM_BUY)) == "T2"
    assert tier_of(_e("某票", QPZM_BUY_LOW)) == "T3"  # score<80 不抬 T2


def test_crowd_alert_band():
    nd = {"s": "ninedim", "tag": "九维出分", "band": "alert"}
    assert tier_of(_e("某票", nd)) == "CROWD"


def test_crowd_coexists_with_st():
    """ST 封顶只拦 T1/T2；九维拥挤警报可并存（七审明确口径）。"""
    nd = {"s": "ninedim", "tag": "九维出分", "band": "alert"}
    assert tier_of(_e("*ST某股", nd)) == "CROWD"


def test_t3_chanlun_sell_only():
    assert tier_of(_e("某票", CL_SELL)) == "T3"


# ── load_chanlun 坏行容错（十一审）─────────────────────────
def test_load_chanlun_skips_corrupt_lines(tmp_path, monkeypatch):
    fp = tmp_path / "chanlun_pool.jsonl"
    rows = [
        {
            "code": "600000.SH",
            "name": "浦发银行",
            "type": "3买",
            "dt": "2026-09-16",
            "detect_date": "2026-10-09",
            "price": 8.5,
        },
        "NOT-JSON 坏行 {{{",
        {
            "code": "000001.SZ",
            "name": "平安银行",
            "type": "1卖",
            "dt": "2026-09-18",
            "detect_date": "2026-10-09",
        },
        {
            "code": "600519.SH",
            "name": "贵州茅台",
            "type": "2买",
            "dt": "2026-09-01",
            "detect_date": "2026-10-08",
        },  # 旧批次不入
    ]
    fp.write_text(
        "\n".join(
            r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) for r in rows
        )
        + "\n",
        encoding="utf-8",
    )
    import build_todays_picks as b

    monkeypatch.setattr(b, "QB", str(tmp_path.parent))
    # QB/data/chanlun_pool.jsonl 布局
    data_dir = tmp_path.parent / "data"
    data_dir.mkdir(exist_ok=True)
    fp2 = data_dir / "chanlun_pool.jsonl"
    fp2.write_text(fp.read_text(encoding="utf-8"), encoding="utf-8")
    stocks = {}
    meta = b.load_chanlun(stocks)
    assert meta["batch_date"] == "2026-10-09"
    assert meta["n"] == 2  # 坏行跳过、旧批次排除
    tags = {s["tag"] for e in stocks.values() for s in e["sources"]}
    assert tags == {"3买", "1卖"}


def test_load_chanlun_hard_fail_when_file_missing(tmp_path, monkeypatch):
    import build_todays_picks as b

    monkeypatch.setattr(b, "QB", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        b.load_chanlun({})  # 整文件缺失=源 err → main 捕获 → T1 停挂链路


# ── stale 闸门 fail-closed（二十三审 2026-10-10）────────────
def test_stale_gate_basis_missing_pass_all():
    """行情湖不可达（basis 空）= 设计内全放行降级，不拦截。"""
    assert _stale_meta(None, "20260101", 4, "L", "f", "；t") is None
    assert _stale_meta(None, "", 4, "L", "f", "；t") is None


def test_stale_gate_src_day_missing_blocks():
    """src_day 空（生产端格式变更）fail-closed 拦截，不再静默放行。"""
    r = _stale_meta("20261009", "", 4, "L", "f", "；t")
    assert r and r["stale"] is True and r["n"] == 0


def test_stale_gate_src_day_unparseable_blocks():
    r = _stale_meta("20261009", "2026/10/", 4, "L", "f", "；t")
    assert r and r["stale"] is True and "不可解析" in r["note"]


def test_stale_gate_fresh_pass_expired_block():
    assert _stale_meta("20261009", "20261009", 4, "L", "f", "；t") is None
    r = _stale_meta("20261009", "20261001", 4, "L", "f", "；t")
    assert r and r["stale"] is True and "旧信号" in r["note"]


# ── 归档首写锁定（二十六审 2026-10-10）──────────────────────
def test_archive_first_write_wins(tmp_path):
    """周末重跑（basis 仍=上个交易日）不得覆写首版快照——后验地基保真。"""
    from build_todays_picks import _archive_once

    d1 = {"scan_run_date": "2026-10-09", "stocks": [{"code": "600000.SH"}]}
    arch, w1 = _archive_once(str(tmp_path), "20261009", d1)
    assert w1 is True and os.path.exists(arch)
    # 周六重跑产出变体清单 → 同名归档保留首次快照
    d2 = {"scan_run_date": "2026-10-09", "stocks": []}
    arch2, w2 = _archive_once(str(tmp_path), "20261009", d2)
    assert w2 is False and arch2 == arch
    kept = json.load(open(arch, encoding="utf-8"))
    assert kept["stocks"] == d1["stocks"]
    assert not os.path.exists(arch + ".tmp")


def test_archive_new_day_writes(tmp_path):
    """新交易日（basis 前进）正常落新档，首写锁定不误伤正常节奏。"""
    from build_todays_picks import _archive_once

    arch, w = _archive_once(str(tmp_path), "20261012", {"stocks": []})
    assert w is True and os.path.basename(arch) == "todays_20261012.json"


# ── 九维 alert 拥挤封顶 T1/T2（二十九审 2026-10-10）─────────
def test_t1_alert_band_capped_to_crowd():
    """bull≥2 共振票撞九维 alert → 不进 T1，落 CROWD 警报层（K33 延伸）。"""
    assert tier_of(_e("共振票", STRONG_LEAD, CL_BUY, NINEDIM_ALERT)) == "CROWD"
    assert tier_of(_e("共振票", STRONG_LEAD, CL_BUY, NINEDIM_ALERT2)) == "CROWD"


def test_t2_alert_band_capped_to_crowd():
    """单通道 T2 票撞九维 alert → 同样封顶落 CROWD。"""
    assert tier_of(_e("缠论票", CL_BUY, NINEDIM_ALERT)) == "CROWD"
    assert tier_of(_e("强势票", STRONG_LEAD, NINEDIM_ALERT2)) == "CROWD"


def test_ninedim_low_band_not_cap():
    """low/na 九维（观察无警报）不影响 T1/T2 正常判定。"""
    assert tier_of(_e("共振票", STRONG_LEAD, CL_BUY, NINEDIM_LOW)) == "T1"
    assert tier_of(_e("缠论票", CL_BUY, NINEDIM_LOW)) == "T2"


# ── 现价回填：湖权威覆盖（三十七审 2026-10-10）─────────────
def test_backfill_overwrites_placeholder(monkeypatch):
    """loader 占位价（chanlun 批次价等）不得阻塞湖权威价——1009 档实证
    76/766 票「close=信号日旧价+pct_chg=最新日」混搭（亚邦 4.32→5.07）。"""
    import pandas as pd
    import tdx_read

    monkeypatch.setattr(tdx_read, "latest_date", lambda t: "20261009")
    monkeypatch.setattr(
        tdx_read,
        "read_day",
        lambda *a, **k: pd.DataFrame(
            {"ts_code": ["603188.SH"], "close": [5.07], "pct_chg": [0.6]}
        ),
    )
    stocks = {
        "603188.SH": {
            "code": "603188.SH",
            "name": "亚邦股份",
            "close": 4.32,
            "pct_chg": None,
        }
    }
    day, n = _backfill_quotes(stocks)
    assert day == "20261009" and n == 1
    assert stocks["603188.SH"]["close"] == 5.07
    assert stocks["603188.SH"]["pct_chg"] == 0.6


def test_backfill_keeps_placeholder_on_lake_miss(monkeypatch):
    """湖无行（停牌/退市）→ 保留 loader 占位不动，不误清空。"""
    import pandas as pd
    import tdx_read

    monkeypatch.setattr(tdx_read, "latest_date", lambda t: "20261009")
    monkeypatch.setattr(
        tdx_read,
        "read_day",
        lambda *a, **k: pd.DataFrame({"ts_code": [], "close": [], "pct_chg": []}),
    )
    stocks = {
        "000582.SZ": {
            "code": "000582.SZ",
            "name": "北部湾港",
            "close": 7.7,
            "pct_chg": None,
        }
    }
    day, n = _backfill_quotes(stocks)
    assert day == "20261009" and n == 0
    assert stocks["000582.SZ"]["close"] == 7.7  # 占位保留
    assert stocks["000582.SZ"]["pct_chg"] is None


# ── 五源全景闸 + 源级降级推送（P2-2/P2-3 2026-10-11）─────────
def _ok_metas():
    return {
        s: {"label": s, "ok": True, "n": 5}
        for s in ("strong", "weak", "chanlun", "qpzm", "ninedim", "cross")
    }


def test_gate_all_ok_empty():
    """五源全 ok → 停挂名单空。"""
    assert gate_failed_sources(_ok_metas()) == []


def test_a_share_env_extract():
    """P1 T1 功效评估（2026-10-11）：A 股闸门状态提取——取 envs 中
    「A股」开头一条（美股/港股/加密不误取），空/缺失返回 None
    （stale 拦截日 meta 不带 env_gate，后验按缺失跳过该日分层）。"""
    envs = [
        {
            "label": "美股全市场(5832只)",
            "safe": True,
            "state": "中性",
            "score": 43,
            "breadth": 0.404,
        },
        {
            "label": "A股全市场(5552只)",
            "safe": False,
            "state": "偏冷（宽度 39%，20日中位 -2.8%）— 闸门收紧",
            "score": 37,
            "breadth": 0.386,
        },
    ]
    g = btp._a_share_env(envs)
    assert g["safe"] is False
    assert g["score"] == 37
    assert g["breadth"] == 0.386
    assert "闸门收紧" in g["state"]
    assert btp._a_share_env([]) is None
    assert btp._a_share_env(None) is None
    assert btp._a_share_env([{"label": "港股全市场", "safe": False}]) is None


def test_gate_failed_fixed_order():
    """ninedim+chanlun 失败 → 固定顺序停挂名单（五源任一即停挂）。"""
    metas = _ok_metas()
    metas["ninedim"] = {"label": "ninedim", "ok": False, "err": "boom"}
    metas["chanlun"] = {"label": "chanlun", "ok": False, "err": "bad jsonl"}
    assert gate_failed_sources(metas) == ["chanlun", "ninedim"]


def test_gate_cross_linkage_not_gating():
    """cross（旁路设计内缺失）与 linkage（纯记录源）失败不停挂。"""
    metas = _ok_metas()
    metas["cross"] = {"label": "cross", "ok": False, "err": "探针失败"}
    metas["linkage"] = {"label": "linkage", "ok": False}
    assert gate_failed_sources(metas) == []


def test_gate_missing_meta_counts():
    """源键整体缺失（loader 未注册）按停挂计——fail-closed。"""
    metas = _ok_metas()
    del metas["weak"]
    assert gate_failed_sources(metas) == ["weak"]


def test_source_alert_silent_on_normal_day(monkeypatch, tmp_path):
    """正常日零推送（噪音控制）：不发消息、不落 outbox。"""
    monkeypatch.setattr(btp, "NEWS_DIR", str(tmp_path))
    called = []
    monkeypatch.setattr(btp.subprocess, "run", lambda *a, **k: called.append(a))
    _push_source_alert(_ok_metas(), [], "20261009")
    assert not called
    assert not (tmp_path / "outbox").exists()


def test_source_alert_skips_when_veto(monkeypatch, tmp_path):
    """停挂路径 rc=3 已走 alert_fail——本函数防双推直接短路。"""
    monkeypatch.setattr(btp, "NEWS_DIR", str(tmp_path))
    called = []
    monkeypatch.setattr(btp.subprocess, "run", lambda *a, **k: called.append(a))
    metas = _ok_metas()
    metas["cross"] = {"label": "cross", "ok": False, "err": "boom"}
    _push_source_alert(metas, ["ninedim"], "20261009")
    assert not called


def test_source_alert_pushes_cross_fail_and_weak_stale(monkeypatch, tmp_path):
    """rc=0 路径盲区（P2-2 核心场景）：cross 失败 + weak 过期拦截 →
    写 outbox 并调 send_feishu，消息含源名、原因、数据基准日。"""
    from types import SimpleNamespace

    monkeypatch.setattr(btp, "NEWS_DIR", str(tmp_path))
    sent = []

    def fake_run(args, **k):
        sent.append(args)
        return SimpleNamespace(stdout="SEND_OK")

    monkeypatch.setattr(btp.subprocess, "run", fake_run)
    metas = _ok_metas()
    metas["cross"] = {"label": "cross", "ok": False, "err": "探针失败"}
    metas["weak"] = {
        "label": "weak",
        "ok": True,
        "n": 0,
        "stale": True,
        "note": "数据基准落后行情湖 12 天",
    }
    _push_source_alert(metas, [], "20261009")
    assert len(sent) == 1
    assert sent[0][-1].endswith("pending_picks_sources.txt")
    msg = (tmp_path / "outbox" / "pending_picks_sources.txt").read_text(
        encoding="utf-8"
    )
    assert "cross 异常失败" in msg and "探针失败" in msg
    assert "weak 过期拦截" in msg and "20261009" in msg


def test_source_alert_send_error_swallowed(monkeypatch, tmp_path, capsys):
    """发送异常整体吞掉——告警器不反噬聚合主流程 rc。"""

    def boom(*a, **k):
        raise OSError("net down")

    monkeypatch.setattr(btp, "NEWS_DIR", str(tmp_path))
    monkeypatch.setattr(btp.subprocess, "run", boom)
    metas = _ok_metas()
    metas["cross"] = {"label": "cross", "ok": False, "err": "x"}
    _push_source_alert(metas, [], "20261009")  # 不抛异常即通过
    assert "推送失败" in capsys.readouterr().out


# ── pct_between 复权口径（2026-10-11 消费端升级）──────────────
import pandas as pd  # noqa: E402


def _mk_daily(rows):
    return pd.DataFrame(rows, columns=["ts_code", "trade_date", "open", "close"])


def _mk_adj(rows):
    return pd.DataFrame(rows, columns=["ts_code", "trade_date", "adj_factor"])


# ── 环境闸门差异化·影子标注（P3 2026-10-11）─────────────────
CL_BUY_REG_DOWN = {"s": "chanlun", "tag": "1买", "reg": "down"}
CL_BUY_REG_FLAT = {"s": "chanlun", "tag": "3买", "reg": "flat"}


def test_env_diff_shadow_two_level_independent():
    """收紧日买点(env_diff)与恐慌期买点(panic_buy)两级独立标注：
    调研依据 reg=down 期 T+10 中位 +0.32 vs flat -2.91。"""
    d, p, why = btp._env_diff_shadow(_e("共振票", CL_BUY_REG_DOWN), True)
    assert d and p and "闸门收紧" in why and "reg=down" in why
    d, p, why = btp._env_diff_shadow(_e("某票", CL_BUY_REG_FLAT), True)
    assert d and not p and "reg=down" not in why
    d, p, why = btp._env_diff_shadow(_e("某票", CL_BUY_REG_DOWN), False)
    assert not d and p and "闸门收紧" not in why


def test_env_diff_shadow_no_buy_no_mark():
    """无缠论买点（卖点/纯 strong）不打任何标注。"""
    d, p, why = btp._env_diff_shadow(_e("某票", CL_SELL), True)
    assert not d and not p and why is None
    d, p, why = btp._env_diff_shadow(_e("某票", STRONG_LEAD), True)
    assert not d and not p and why is None


def test_env_diff_shadow_wide_scope_with_sell():
    """买卖并存票仍标（影子宽口径：环境压制下的买点证据全记录，
    多空并存留给后验分层；tier 侧 cl_buy 严格口径不受影响——
    见 test_veto_cl_sell_same_batch）。"""
    d, p, _ = btp._env_diff_shadow(_e("多空票", CL_BUY_REG_DOWN, CL_SELL), True)
    assert d and p


def test_env_diff_shadow_reg_missing_not_panic():
    """reg 缺失（None/旧档无字段）≠ down——不误标恐慌层。"""
    d, p, why = btp._env_diff_shadow(_e("旧档票", {"s": "chanlun", "tag": "2买"}), True)
    assert d and not p and "reg=down" not in (why or "")


# ── 环境闸门差异化·豁免（P4 启用 2026-10-11）─────────────────
CL_1BUY = {"s": "chanlun", "tag": "1买", "reg": "flat"}
QPZM_ENV_AVOID = {"s": "qpzm", "tag": "回避", "action": "回避（环境闸门）", "score": 55}
QPZM_STOCK_AVOID = {"s": "qpzm", "tag": "回避", "action": "回避", "score": 55}
QPZM_AVOID_NO_ACTION = {"s": "qpzm", "tag": "回避", "score": 55}


def test_env_diff_exempt_lifts_t1():
    """收紧日 strong领涨+1买+环境闸门型回避 → 豁免否决进 T1（旧=T2）。"""
    e = _e("豁免票", STRONG_LEAD, CL_1BUY, QPZM_ENV_AVOID)
    assert btp.tier_of(e, True, gate_tight=True) == "T1"
    assert btp._env_diff_exempt(e, True) is True


def test_env_diff_exempt_gate_open_no_lift():
    """放行日同组合不豁免 → qpzm_avoid 否决 T1、chanlun 买走 T2。"""
    e = _e("对照票", STRONG_LEAD, CL_1BUY, QPZM_ENV_AVOID)
    assert btp.tier_of(e, True, gate_tight=False) == "T2"
    assert btp._env_diff_exempt(e, False) is False


def test_env_diff_exempt_stock_reason_still_blocks():
    """个股型回避（action 无环境闸门）仍否决——豁免只针对环境闸门型。"""
    e = _e("个股回避票", STRONG_LEAD, CL_1BUY, QPZM_STOCK_AVOID)
    assert btp.tier_of(e, True, gate_tight=True) == "T2"


def test_env_diff_exempt_action_missing_still_blocks():
    """action 缺失=型别不可判，维持否决（保守方向）。"""
    e = _e("旧档票", STRONG_LEAD, CL_1BUY, QPZM_AVOID_NO_ACTION)
    assert btp.tier_of(e, True, gate_tight=True) == "T2"


def test_env_diff_exempt_third_buy_not_lifted():
    """非 1买（3买）不豁免——豁免绑定质量分层唯一正 alpha 层。"""
    e = _e(
        "三买票",
        STRONG_LEAD,
        {"s": "chanlun", "tag": "3买", "reg": "down"},
        QPZM_ENV_AVOID,
    )
    assert btp.tier_of(e, True, gate_tight=True) == "T2"


def test_env_diff_exempt_no_resonance_no_t1():
    """豁免不绕过 bull>=2 共振：仅 1买+环境回避（无 strong/qpzm买入）仍 T2。"""
    e = _e("单源票", CL_1BUY, QPZM_ENV_AVOID)
    assert btp.tier_of(e, True, gate_tight=True) == "T2"


def test_env_diff_exempt_other_vetoes_untouched():
    """其余否决全部不动：weak 否决 / ST 封顶 / 同批卖点并存，
    满足豁免条件也不进 T1。"""
    e = _e("weak票", STRONG_LEAD, CL_1BUY, QPZM_ENV_AVOID, WEAK)
    assert btp.tier_of(e, True, gate_tight=True) == "T2"
    e = _e("ST票", STRONG_LEAD, CL_1BUY, QPZM_ENV_AVOID)
    e["name"] = "*ST豁免"
    assert btp.tier_of(e, True, gate_tight=True) == "T3"
    e = _e("多空票", STRONG_LEAD, CL_1BUY, CL_SELL, QPZM_ENV_AVOID)
    assert btp.tier_of(e, True, gate_tight=True) == "T2"


# ── P5 质量分层：毒层不计 T1 共振（2026-10-11 启用）──────────
CL_3BUY_BEI_F = {"s": "chanlun", "tag": "3买", "bei": False, "trend": "up"}
CL_3BUY_BEI_T = {"s": "chanlun", "tag": "3买", "bei": True, "trend": "up"}
CL_3BUY_BEI_N = {"s": "chanlun", "tag": "3买", "trend": "up"}  # bei 不可判
CL_2BUY_RANGE = {"s": "chanlun", "tag": "2买", "trend": "range"}


def test_toxic_buy_not_counted_for_t1():
    """毒层买点（3买bei=False / 2买range）+ strong → 不进 T1、落 T2。"""
    assert btp.tier_of(_e("毒层票", STRONG_LEAD, CL_3BUY_BEI_F)) == "T2"
    assert btp.tier_of(_e("毒层票", STRONG_LEAD, CL_2BUY_RANGE)) == "T2"


def test_toxic_switch_off_restores_old_behavior():
    """TOXIC_EXCLUDE=False 一键回退：毒层买点恢复同权计 T1 共振。"""
    old = btp.TOXIC_EXCLUDE
    try:
        btp.TOXIC_EXCLUDE = False
        assert btp.tier_of(_e("回退票", STRONG_LEAD, CL_3BUY_BEI_F)) == "T1"
        assert btp.tier_of(_e("回退票", STRONG_LEAD, CL_2BUY_RANGE)) == "T1"
    finally:
        btp.TOXIC_EXCLUDE = old


def test_nontoxic_buys_unaffected():
    """非毒层不受影响：1买/3买bei=True/3买bei=None + strong 均 T1。"""
    assert btp.tier_of(_e("一买票", STRONG_LEAD, CL_1BUY)) == "T1"
    assert btp.tier_of(_e("背驰票", STRONG_LEAD, CL_3BUY_BEI_T)) == "T1"
    assert btp.tier_of(_e("无标注票", STRONG_LEAD, CL_3BUY_BEI_N)) == "T1"


def test_toxic_with_qpzm_buy_also_blocked():
    """毒层 chanlun 腿 + qpzm 买入同样不凑成 T1（毒层是 chanlun 腿降权）。"""
    e = _e("毒腿票", CL_3BUY_BEI_F, {"s": "qpzm", "tag": "买入", "score": 85})
    assert btp.tier_of(e) == "T2"


def test_q_layer_marks_scope():
    """影子标注口径：q_toxic/q_first_buy 两级；无买点票双 False；
    毒层与 1买 可并存（多信号票）。"""
    qt, qf = btp._q_layer_marks(_e("一买票", CL_1BUY))
    assert (qt, qf) == (False, True)
    qt, qf = btp._q_layer_marks(_e("毒层票", CL_3BUY_BEI_F))
    assert (qt, qf) == (True, False)
    qt, qf = btp._q_layer_marks(_e("卖点票", CL_SELL))
    assert (qt, qf) == (False, False)
    qt, qf = btp._q_layer_marks(_e("并存票", CL_1BUY, CL_3BUY_BEI_F))
    assert (qt, qf) == (True, True)


# ── P5c 形态 C：tier 内 1买 置顶（2026-10-11 启用）───────────
def test_pick_sort_first_buy_top_within_tier():
    """同 tier 内 q_first_buy=True 排前；tier 主序不变。"""
    rows = [
        {"tier": "T1", "n_hit": 3, "code": "000001.SZ", "q_first_buy": True},
        {"tier": "T1", "n_hit": 3, "code": "000002.SZ"},  # 非一买
        {"tier": "T2", "n_hit": 3, "code": "000003.SZ", "q_first_buy": True},
        {"tier": "T3", "n_hit": 3, "code": "000004.SZ"},
    ]
    s = sorted(rows, key=btp._pick_sort_key)
    assert [r["code"] for r in s] == [
        "000001.SZ",
        "000002.SZ",
        "000003.SZ",
        "000004.SZ",
    ]


def test_pick_sort_n_hit_desc_after_first_buy():
    """1买 置顶后仍按 n_hit 降序；无标注字段（旧档兼容）不炸。"""
    rows = [
        {"tier": "T2", "n_hit": 2, "code": "B.SZ", "q_first_buy": True},
        {"tier": "T2", "n_hit": 3, "code": "A.SZ", "q_first_buy": True},
        {"tier": "T2", "n_hit": 3, "code": "C.SZ"},  # 非一买 n_hit=3
        {"tier": "T2", "n_hit": 1},  # 无 code 场景由上游保证，此处不测
    ]
    rows[3]["code"] = "D.SZ"
    s = sorted(rows, key=btp._pick_sort_key)
    assert [r["code"] for r in s] == ["A.SZ", "B.SZ", "C.SZ", "D.SZ"]


def test_pct_between_adjusted_exdiv_window():
    """因子齐备：送转除权窗口（10 送 10，close 20→open 10）复权修正后
    样本保留——买 10×adj1=10、卖 11×adj2=22 → +120%；旧缺口守卫会
    误判 -50% 缺口 void 剔除（三十一审）。"""
    import tdx_read

    def fake_rr(tbl, s, e, codes=None, columns=None):
        if tbl == "ts_daily":
            return _mk_daily(
                [
                    ("600000.SH", "20260501", 10.0, 20.0),
                    ("600000.SH", "20260502", 10.0, 11.0),
                ]
            )
        return _mk_adj([("600000.SH", "20260501", 1.0), ("600000.SH", "20260502", 2.0)])

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tdx_read, "read_range", fake_rr)
    monkeypatch.setattr(tdx_read, "table_ready", lambda t: True)
    try:
        assert pct_between("600000.SH", "20260501", "20260502") == 120.0
    finally:
        monkeypatch.undo()


def test_pct_between_no_factor_change_same_number():
    """因子恒定（无除权）时复权口径与旧口径数字一致。"""
    import tdx_read

    def fake_rr(tbl, s, e, codes=None, columns=None):
        if tbl == "ts_daily":
            return _mk_daily(
                [
                    ("600000.SH", "20260501", 10.0, 10.5),
                    ("600000.SH", "20260502", 11.0, 12.0),
                ]
            )
        return _mk_adj([("600000.SH", "20260501", 3.0), ("600000.SH", "20260502", 3.0)])

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tdx_read, "read_range", fake_rr)
    monkeypatch.setattr(tdx_read, "table_ready", lambda t: True)
    try:
        # 买 10 卖 12 → +20%（因子 3.0/3.0 约掉）
        assert pct_between("600000.SH", "20260501", "20260502") == 20.0
    finally:
        monkeypatch.undo()


def test_pct_between_degraded_gap_guard():
    """降级路径：因子表未就绪（table_ready=False）→ 三十一审缺口守卫，
    -50% 隔夜缺口返回 "exdiv" 哨兵由调用方 void。"""
    import tdx_read

    def fake_rr(tbl, s, e, codes=None, columns=None):
        return _mk_daily(
            [
                ("600000.SH", "20260501", 10.0, 20.0),
                ("600000.SH", "20260502", 10.0, 11.0),
            ]
        )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tdx_read, "read_range", fake_rr)
    monkeypatch.setattr(tdx_read, "table_ready", lambda t: False)
    try:
        assert pct_between("600000.SH", "20260501", "20260502") == "exdiv"
        # 无缺口窗口正常计算
        assert pct_between("600000.SH", "20260502", "20260502") is not None
    finally:
        monkeypatch.undo()


def test_pct_between_partial_factor_missing_degrades():
    """因子表就绪但该票窗口缺行（新票 lag）→ 降级守卫，不误算半复权。"""
    import tdx_read

    def fake_rr(tbl, s, e, codes=None, columns=None):
        if tbl == "ts_daily":
            return _mk_daily(
                [
                    ("600000.SH", "20260501", 10.0, 20.0),
                    ("600000.SH", "20260502", 10.0, 11.0),
                ]
            )
        return _mk_adj([("600000.SH", "20260502", 2.0)])  # 缺 0501 行

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tdx_read, "read_range", fake_rr)
    monkeypatch.setattr(tdx_read, "table_ready", lambda t: True)
    try:
        assert pct_between("600000.SH", "20260501", "20260502") == "exdiv"
    finally:
        monkeypatch.undo()


# ── 四十一审三闸（2026-11-12 盘后复盘落地）──────────────────
def test_bull_count_only_counts_bullish_sources():
    """bull 口径：qpzm买入/chanlun买(无同批卖)/strong领涨 计 1，其余 0。

    与 n_hit（源总数）区分的判例：weak:深度垫底+chanlun买 的票 n_hit=2
    但 bull=1——把源总数当共振会把超跌反弹标记误读成「共振保护」。
    """
    assert btp._bull_count([STRONG_LEAD, CL_BUY]) == 2
    assert btp._bull_count([QPZM_BUY, CL_BUY]) == 2
    assert btp._bull_count([CL_BUY]) == 1
    assert btp._bull_count([CL_BUY, CL_SELL]) == 0          # 同批买卖并存
    assert btp._bull_count([WEAK, CL_BUY]) == 1             # weak 不算看多
    assert btp._bull_count([QPZM_AVOID, CL_BUY]) == 1       # 回避不算看多
    assert btp._bull_count([NINEDIM_LOW]) == 0              # 九维影子不算
    assert btp._bull_count([CL_3BUY_BEI_F]) == 0            # 毒层买点不算


def test_sig_age_days_buy_only_and_fallback():
    """信号龄只看买点；无 chanlun 日期返回 None（stale 闸门已把关）。"""
    e = {"sources": [CL_BUY, {"s": "chanlun", "tag": "1卖", "dt": "2026-08-01"}]}
    # CL_BUY dt=2026-09-16，基准 20261010 → 24 天；卖点 08-01 不参与
    assert btp._sig_age_days(e, "20261010") == 24
    assert btp._sig_age_days({"sources": [STRONG_LEAD]}, "20261010") is None
    assert btp._sig_age_days({"sources": []}, "20261010") is None


def test_sig_age_days_takes_freshest_buy():
    """多买点取最新（min 龄）——旧买点不该把带新买点的票清出去。"""
    e = {"sources": [
        {"s": "chanlun", "tag": "2买", "dt": "2026-08-25"},   # 78 天旧
        CL_BUY,                                               # 2026-09-16
    ]}
    assert btp._sig_age_days(e, "20261010") == 24


def test_weak_market_state_gate(monkeypatch):
    """弱势日判定：快照日≠基准日 → None（fail-open）；字段齐 → weak 按
    score/prob 阈值判。11-12 判例：score=12 / prob=79 → weak=True。"""
    import builtins
    orig_open = builtins.open

    def fake_open(fp, *a, **k):
        if "premarket_latest" in str(fp):
            import io as _io
            return _io.StringIO(json.dumps({
                "ts": "2026-11-12T08:49:36+08:00", "trade_date": "20261112",
                "overall": 12, "prob": 79,
            }))
        return orig_open(fp, *a, **k)

    monkeypatch.setattr(builtins, "open", fake_open)
    assert btp._weak_market_state("20261112") == {
        "score": 12, "prob": 79, "weak": True}
    assert btp._weak_market_state("20261111") is None      # 快照不是这一天


# ── 四十一审三闸（2026-11-12 盘后复盘落地）──────────────────
def test_bull_count_only_counts_bullish_sources():
    """bull 口径：qpzm买入/chanlun买(无同批卖)/strong领涨 计 1，其余 0。

    与 n_hit（源总数）区分的判例：weak:深度垫底+chanlun买 的票 n_hit=2
    但 bull=1——把源总数当共振会把超跌反弹标记误读成「共振保护」。
    """
    assert btp._bull_count([STRONG_LEAD, CL_BUY]) == 2
    assert btp._bull_count([QPZM_BUY, CL_BUY]) == 2
    assert btp._bull_count([CL_BUY]) == 1
    assert btp._bull_count([CL_BUY, CL_SELL]) == 0          # 同批买卖并存
    assert btp._bull_count([WEAK, CL_BUY]) == 1             # weak 不算看多
    assert btp._bull_count([QPZM_AVOID, CL_BUY]) == 1       # 回避不算看多
    assert btp._bull_count([NINEDIM_LOW]) == 0              # 九维影子不算
    assert btp._bull_count([CL_3BUY_BEI_F]) == 0            # 毒层买点不算


def test_sig_age_days_buy_only_and_fallback():
    """信号龄只看买点；无 chanlun 日期返回 None（stale 闸门已把关）。"""
    e = {"sources": [CL_BUY, {"s": "chanlun", "tag": "1卖", "dt": "2026-08-01"}]}
    # CL_BUY dt=2026-09-16，基准 20261010 → 24 天；卖点 08-01 不参与
    assert btp._sig_age_days(e, "20261010") == 24
    assert btp._sig_age_days({"sources": [STRONG_LEAD]}, "20261010") is None
    assert btp._sig_age_days({"sources": []}, "20261010") is None


def test_sig_age_days_takes_freshest_buy():
    """多买点取最新（min 龄）——旧买点不该把带新买点的票清出去。"""
    e = {"sources": [
        {"s": "chanlun", "tag": "2买", "dt": "2026-08-25"},   # 78 天旧
        CL_BUY,                                               # 2026-09-16
    ]}
    assert btp._sig_age_days(e, "20261010") == 24


def test_weak_market_state_gate(monkeypatch):
    """弱势日判定：快照日≠基准日 → None（fail-open）；字段齐 → weak 按
    score/prob 阈值判。11-12 判例：score=12 / prob=79 → weak=True。"""
    import builtins
    orig_open = builtins.open

    def fake_open(fp, *a, **k):
        if "premarket_latest" in str(fp):
            import io as _io
            return _io.StringIO(json.dumps({
                "ts": "2026-11-12T08:49:36+08:00", "trade_date": "20261112",
                "overall": 12, "prob": 79,
            }))
        return orig_open(fp, *a, **k)

    monkeypatch.setattr(builtins, "open", fake_open)
    assert btp._weak_market_state("20261112") == {
        "score": 12, "prob": 79, "weak": True}
    assert btp._weak_market_state("20261111") is None      # 快照不是这一天
