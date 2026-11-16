# -*- coding: utf-8 -*-
"""11-16 两处采集侧缺陷的回归测试。

判例一（taoguba）：北交所 92/43/83/87/88 前缀在淘股吧无个股版面，`/quotes/920xxx`
  返回「错误页面_淘股吧」（实测 29739 字节、无 headImg 块），被 parse_empty 护栏
  计成「解析 0 行」，13/16=81% > 30% → 每次 kstock 轮次 rc=2 假警报，掩盖真故障。

判例二（market_sentiment）：极性源的 `n` 为 0 时仍进权重，`sum(ws)==0` 导致
  `sum(ps)/sum(ws)` 除零，整个市场情绪段 rc=1 中断（08:13 实测）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import market_sentiment as M  # noqa: E402
import taoguba_collector as T  # noqa: E402


# ------------------------------------------------------------ taoguba 市场过滤
def test_split_unsupported_drops_bj_and_keeps_ab():
    keep, drop = T.split_unsupported([("920021", ""), ("600519", ""), ("000001", ""), ("430047", "")])
    assert [c[0] for c in keep] == ["600519", "000001"]
    assert [c[0] for c in drop] == ["920021", "430047"]


def test_split_unsupported_all_supported():
    keep, drop = T.split_unsupported([("600519", ""), ("300750", "")])
    assert len(keep) == 2 and drop == []


def test_split_unsupported_empty_queue():
    assert T.split_unsupported([]) == ([], [])


def test_unsupported_prefix_covers_all_bj_forms():
    for p in ("92", "43", "83", "87", "88"):
        assert p in T.UNSUPPORTED_PREFIX


# ------------------------------------------------- market_sentiment 除零回归
def _card(**src):
    return {"src": src}


def test_market_breadth_zero_n_does_not_crash():
    """n=0 的极性源不能进权重分母。修复前：ZeroDivisionError。"""
    cards = [_card(雪球={"polarity": 1.0, "n": 0}),
             _card(雪球={"polarity": -1.0, "n": 0})]
    assert M.market_breadth(cards) is None, "无有效样本应返回 None（不可测），而非 0 或崩溃"


def test_market_breadth_skips_zero_and_counts_valid():
    cards = [_card(雪球={"polarity": 1.0, "n": 0}, 股吧={"polarity": 1.0, "n": 5}),
             _card(雪球={"polarity": -1.0, "n": 3})]
    assert M.market_breadth(cards) == 50.0, "1 正 1 负 → 50%"


def test_market_breadth_none_polarity_ignored():
    cards = [_card(雪球={"polarity": None, "n": 9})]
    assert M.market_breadth(cards) is None


def test_market_breadth_missing_n_treated_as_zero():
    cards = [_card(雪球={"polarity": 1.0})]
    assert M.market_breadth(cards) is None


# ---------------------------------------------- 采集器失败账本（2026-11-16 判例）
def test_taoguba_record_blocked_writes_ledger(tmp_path, monkeypatch):
    """拦截/HTTP/异常落 _blocked.json；账本故障只打印不阻断采集。
    没有账本时，被拦截票与从未采过票在 mtime 上同形，缺口工具无法分辨。"""
    import json as _json
    import taoguba_collector as T
    f = str(tmp_path / "_blocked.json")
    monkeypatch.setattr(T, "BLOCKED_F", f)
    T.record_blocked("600519", "blocked", ts=1700000000.0)
    d = _json.load(open(f, encoding="utf-8"))
    assert d["600519"]["kind"] == "blocked"
    T.record_blocked("600519", "http_503", ts=1700000100.0)
    d = _json.load(open(f, encoding="utf-8"))
    assert d["600519"]["kind"] == "http_503"      # 覆盖旧记录


def test_guba_pw_record_blocked_writes_ledger(tmp_path, monkeypatch):
    import json as _json
    import guba_pw as GP
    f = str(tmp_path / "_blocked.json")
    monkeypatch.setattr(GP, "BLOCKED_F", f)
    GP.record_blocked("002737", "no_marker", ts=1700000000.0)
    d = _json.load(open(f, encoding="utf-8"))
    assert d["002737"]["status"] == "no_marker"
    # 预写损坏账本 → 不抛异常（账本故障不阻断采集）
    open(f, "w", encoding="utf-8").write("{broken")
    GP.record_blocked("002737", "no_marker", ts=1700000000.0)   # 内部吞掉异常


# ---------------------------------------------- batch_collect 熔断解析（2026-11-16 判例沉淀）
def test_parse_guba_done_new_format():
    import batch_collect as B
    ok, fail, blocked = B.parse_guba_done("DONE ok=12 fail=1 blocked=1 新增=960 耗时=161s")
    assert (ok, fail, blocked) == (12, 1, 1)


def test_parse_guba_done_legacy_format():
    """旧格式无 blocked= 字段 → 按 0 计，熔断靠 fail 兜底。"""
    import batch_collect as B
    ok, fail, blocked = B.parse_guba_done("DONE ok=13 fail=0 新增=1040 耗时=150s")
    assert (ok, fail, blocked) == (13, 0, 0)


def test_parse_guba_done_missing_done_counts_as_fail():
    import batch_collect as B
    ok, fail, blocked = B.parse_guba_done("no output")
    assert ok == 0 and fail >= 1


def test_parse_tgb_done_blocked_line():
    import batch_collect as B
    line = "[tgb] 完成: 20 标的, 新增 320 条, 失败 4, 拦截页 2 只, 首页解析0行 0/16 (0%), 结构失配 0, 耗时 152s"
    ok, fail, blocked = B.parse_tgb_done(line)
    assert fail == 4 and blocked == 1


def test_parse_tgb_done_no_blocked():
    import batch_collect as B
    line = "[tgb] 完成: 20 标的, 新增 398 条, 失败 0, 拦截页 -, 首页解析0行 0/20 (0%), 结构失配 0, 耗时 92s"
    ok, fail, blocked = B.parse_tgb_done(line)
    assert fail == 0 and blocked == 0


def test_batch_collect_rejects_dirty_codes(tmp_path):
    """CRLF 判例：清单脏字符必须显形退出码 2，不能静默当不适用。

    ⚠ 只测非法路径早退——首版本测试传合法票真跑了 guba_pw（2026-11-16
    16:27 实锤触发 2 个东财请求），测试触网是事故：凡是会触达外部系统的
    脚本，单测只锁「早退分支」，执行分支属集成验证，不进单测。"""
    import batch_collect as B
    f = tmp_path / "codes.txt"
    f.write_text("600519\r\nabc\r\n1234567\r\n", encoding="utf-8")
    rc = B.main(["--codes-file", str(f), "--sources", "guba"])
    assert rc == 2
    f2 = tmp_path / "empty.txt"
    f2.write_text("", encoding="utf-8")
    rc = B.main(["--codes-file", str(f2), "--sources", "guba"])
    assert rc == 2
