# -*- coding: utf-8 -*-
"""P2.6 FE-C · 8097 观测债三项 · 离线契约测试（2026-10-20）。

全部离线（不起服务、不发网络请求、不碰真实 push_flow.jsonl），覆盖：
1. /api/public/ops 分支内含 N6 主动巡检挂点 _dead_letter_alert_if_needed()
   （锚点断言 count==1，注释「天然探针」在位）；
2. api_send docstring 乱码词已修正：全文无 fail-closed（禁用措辞），
   「按 closed on failure 语义不回落直发」count==1；
3. /api/public/ops 响应含 fallback_24h 段（{"count","total"} 两键）；
4. _fallback_stats_24h 对样例 jsonl 计数正确（降级行 / 总行 / count 合计）；
5. 坏行（半行交错、count 字段损坏）跳过不中断；
6. 24h 窗外行不计（count 与 total 均排除）；
7. 读异常 → count/total 全 -1（诚实不猜数）；
8. 写读契约：真实 push_flow.record 写 channel="openapi-fallback" 行可被统计
   识别（feishu_client 降级分支与统计函数的端到端约定）；
9. 隐私红线：响应零 open_id/target/kind 明文键，fallback_24h 值均为 int；
10. feishu_client 降级分支落账标记锚点：channel="openapi-fallback" 在
    _openapi_direct_send 内 count==4，_flow_record 缺省 channel 仍为
    "openapi"（非降级行结构零变化，向后兼容）。

P2.7 G-B 追加（11~15）：
11. sent_total 纯发送口径：ok=true 有效行 count 合计，与 total 差值
    =被拒/失败行量（单次读同轮算三值）；
12. 读异常 → sent_total 与 count/total 同步 -1；
13. 隐私红线复验：加 sent_total 后响应仍零 ou_/oc_ 明文；
14. customer 域 observe 静默窗预埋：窗内 kind 加 "|silent-window" 后缀、
    窗外不加，observe 不拦截不发送（零行为变更）；
15. enforce 口径注释锚点：customer 域 critical 不豁免（老田建议口径，
    M3 另批确认）注释在位。

偏差声明（G-B）：既有例 3/4/5/6/7/8/9 中对 fallback_24h 的精确键集
/整字典相等断言，因本批加法扩展 sent_total 必然失效，已做最小机械化
扩展（纳入新键，断言强度不降，未删未弱化任何既有语义）。

侦察结论（P2.6 FE-C）：降级 why（openapi_fallback: 前缀）只存在于
send_msg 返回值，不落 push_flow.jsonl；落账行 channel="openapi" 与 Agent
webhook 路径共用、kind 不特化 → 原行不可识别，走派工单 b 路径；evidence
键有白名单（非白名单值强制改写 dead_letter）不可用，故采用 channel 值
标记（push_flow 零消费方按 channel 过滤，向后兼容）。
"""
import json
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parent
REPO = PORTAL.parent
sys.path.insert(0, str(PORTAL))
sys.path.insert(0, str(REPO))

import push_admin_server as admin  # noqa: E402
import push_flow  # noqa: E402

ADMIN_SRC = (PORTAL / "push_admin_server.py").read_text(encoding="utf-8")
FEISHU_SRC = (REPO / "news_collector" / "feishu_client.py").read_text(
    encoding="utf-8")


def _ops_branch_src():
    """/api/public/ops 分支源码片段（至下一个 branch 为止）。"""
    start = ADMIN_SRC.index('p == "/api/public/ops"')
    end = ADMIN_SRC.index('if p == "/api/state":')
    return ADMIN_SRC[start:end]


class _FakeHandler:
    """只够跑 do_GET 的 /api/public/ops 分支的最小桩。"""

    def __init__(self):
        self.path = "/api/public/ops"
        self.payload = None
        self.code = None

    def _json(self, obj, code=200):
        self.payload = obj
        self.code = code


def _call_public_ops(monkeypatch, count=3):
    """以桩替 dead_letter，直呼 do_GET 的公开摘要分支，返回捕获的载荷。"""
    fake_dl = types.ModuleType("dead_letter")
    fake_dl.count_since = lambda hours=24: count
    monkeypatch.setitem(sys.modules, "dead_letter", fake_dl)
    handler = _FakeHandler()
    admin.Handler.do_GET(handler)
    return handler


# ── 1. N6 挂点 ──

def test_n6_hook_in_public_ops_branch():
    """/api/public/ops 分支内、构造响应前有 _dead_letter_alert_if_needed()
    恰好一次，注释「天然探针」在位（ops_board 60s 自动刷新即天然探针）。"""
    branch = _ops_branch_src()
    assert branch.count("_dead_letter_alert_if_needed(stats={") == 1
    assert "天然探针" in branch
    # 挂点在响应构造（return self._json）之前，且复用同一次 count_since
    # 读取（stats 传入，同请求不双读死信账）
    assert branch.index("_dead_letter_alert_if_needed(stats={") < branch.index(
        "return self._json(")
    assert branch.count("dead_letter.count_since(24)") == 1


# ── 2. 乱码词修正 ──

def test_garbled_word_fixed():
    """api_send docstring 乱码词已修正：全文无 fail-closed（写盘转乱码
    判例禁用措辞），新措辞「按 closed on failure 语义不回落直发」count==1。"""
    assert ADMIN_SRC.count("fail-closed") == 0
    assert ADMIN_SRC.count("按 closed on failure 语义\n    不回落直发") == 1


# ── 3. fallback_24h 段 ──

def test_public_ops_has_fallback_segment(monkeypatch, tmp_path):
    """/api/public/ops 响应含 fallback_24h 段，键恰为
    {"count","total","sent_total"}（G-B 加法扩展），值均为 int
    （读不到账本时为 -1，不猜数）。"""
    monkeypatch.setattr(push_flow, "FLOW_PATH",
                        str(tmp_path / "no_such_flow.jsonl"))
    handler = _call_public_ops(monkeypatch, count=7)
    assert handler.code == 200
    fb = handler.payload["fallback_24h"]
    assert set(fb.keys()) == {"count", "total", "sent_total"}
    assert all(isinstance(v, int) for v in fb.values())
    # 账本文件不存在（读异常）→ 诚实 -1
    assert fb == {"count": -1, "total": -1, "sent_total": -1}


# ── 4~7. _fallback_stats_24h 统计函数 ──

def _write_flow(tmp_path, rows):
    p = tmp_path / "flow.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                         for r in rows), encoding="utf-8")
    return str(p)


def _row(channel, count=1, hours_ago=0.0, ok=True, target="oc_x"):
    ts = (datetime.now() - timedelta(hours=hours_ago)).isoformat(
        timespec="seconds")
    return {"ts": ts, "date": ts[:10], "kind": "auto:probe", "channel":
            channel, "target": target, "count": count, "ok": ok,
            "tenant": "default"}


def test_fallback_stats_counts_sample_jsonl(monkeypatch, tmp_path):
    """样例 jsonl：2 个降级行（count 1+2=3）、其余行只进 total 分母；
    sent_total=ok=true 行合计 1+5+0=6（G-B）。"""
    rows = [
        _row("openapi-fallback", count=1, hours_ago=1),
        _row("openapi-fallback", count=2, hours_ago=2, ok=False),
        _row("openapi", count=5, hours_ago=1),
        _row("hub-gate", count=1, hours_ago=3, ok=False),
        _row("openapi-fallback", count=0, hours_ago=0.5),  # count=0 也计入行数口径
    ]
    monkeypatch.setattr(push_flow, "FLOW_PATH", _write_flow(tmp_path, rows))
    fb = admin._fallback_stats_24h()
    assert fb == {"count": 3, "total": 9, "sent_total": 6}


def test_fallback_stats_skips_bad_lines(monkeypatch, tmp_path):
    """坏行（半行交错残片 / 非 JSON / count 字段损坏）跳过不中断。"""
    p = tmp_path / "flow.jsonl"
    good = json.dumps(_row("openapi-fallback", count=2), ensure_ascii=False)
    p.write_text("\n".join([
        good[:30],                # 半行交错残片
        "{not-json",              # 非 JSON
        json.dumps(_row("openapi", count=3), ensure_ascii=False),
        json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                    "channel": "openapi-fallback", "count": "bad"}),  # count 损坏
        good,
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(push_flow, "FLOW_PATH", str(p))
    assert admin._fallback_stats_24h() == {"count": 2, "total": 5,
                                           "sent_total": 5}


def test_fallback_stats_excludes_outside_24h(monkeypatch, tmp_path):
    """24h 窗外行不计（count 与 total 均排除；30h 前的降级行不虚增）。"""
    rows = [
        _row("openapi-fallback", count=9, hours_ago=30),
        _row("openapi", count=4, hours_ago=48),
        _row("openapi-fallback", count=1, hours_ago=23),
    ]
    monkeypatch.setattr(push_flow, "FLOW_PATH", _write_flow(tmp_path, rows))
    assert admin._fallback_stats_24h() == {"count": 1, "total": 1,
                                           "sent_total": 1}


def test_fallback_stats_read_error_minus_one(monkeypatch, tmp_path):
    """读异常（文件不存在 / 路径是目录）→ count/total/sent_total 全 -1，
    不猜数（G-B：sent_total 同步 -1）。"""
    monkeypatch.setattr(push_flow, "FLOW_PATH",
                        str(tmp_path / "missing.jsonl"))
    assert admin._fallback_stats_24h() == {"count": -1, "total": -1,
                                           "sent_total": -1}
    monkeypatch.setattr(push_flow, "FLOW_PATH", str(tmp_path))  # 目录 → OSError
    assert admin._fallback_stats_24h() == {"count": -1, "total": -1,
                                           "sent_total": -1}


# ── 8. 写读契约（真实 push_flow.record）──

def test_fallback_marker_writer_reader_contract(monkeypatch, tmp_path):
    """端到端约定：真实 push_flow.record 写 channel="openapi-fallback" 行，
    _fallback_stats_24h 可识别；channel 缺省行进 total 不进 count。"""
    monkeypatch.setattr(push_flow, "FLOW_PATH", str(tmp_path / "flow.jsonl"))
    push_flow.record("auto:probe", "openapi-fallback", "oc_a", 1, True)
    push_flow.record("auto:probe", "openapi-fallback", "oc_a", 1, False)
    push_flow.record("auto:probe", "openapi", "oc_b", 2, True)  # 非降级行
    assert admin._fallback_stats_24h() == {"count": 2, "total": 4,
                                           "sent_total": 3}


# ── 9. 隐私红线 ──

def test_public_ops_no_plaintext(monkeypatch, tmp_path):
    """响应零 open_id/target/kind 明文键；fallback_24h 只含三计数键
    （G-B 扩展后）；整包序列化后无 open_id（ou_）/chat_id（oc_）明文泄漏。"""
    monkeypatch.setattr(push_flow, "FLOW_PATH",
                        str(tmp_path / "missing.jsonl"))
    payload = _call_public_ops(monkeypatch).payload
    banned = {"open_id", "target", "kind", "receivers"}
    for key in payload:
        assert key not in banned, f"公开摘要泄露敏感键: {key}"
    assert set(payload["fallback_24h"]) == {"count", "total", "sent_total"}
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "ou_" not in dumped and "oc_" not in dumped


# ── 10. feishu_client 降级分支标记锚点 ──

def test_feishu_client_fallback_marker_anchors():
    """_openapi_direct_send 内 channel="openapi-fallback" 恰 4 处落账；
    _flow_record 缺省 channel 仍为 "openapi"（非降级行零变化）。"""
    seg = FEISHU_SRC[FEISHU_SRC.index("def _openapi_direct_send("):]
    assert seg.count('channel="openapi-fallback"') == 4
    assert 'def _flow_record(ok, target, channel="openapi"' in FEISHU_SRC
    # why 前缀证据链锚点仍在（降级语义未动）
    assert seg.count("openapi_fallback: ") >= 3


# ── 11~15. P2.7 G-B：sent_total 纯发送口径 + 静默窗预埋 ──

def test_fallback_stats_sent_total_pure_send_scope(monkeypatch, tmp_path):
    """G-B 纯发送口径：sent_total=近 24h ok=true 有效行 count 合计；
    与 total 差值=被拒/失败行量（hub-gate 拒绝行、降级失败行均不进
    sent_total）；单次读 jsonl 同轮算三值。"""
    rows = [
        _row("openapi-fallback", count=1, hours_ago=1),            # sent +1
        _row("hub-gate", count=3, hours_ago=1, ok=False),          # 拒绝不进 sent
        _row("openapi", count=5, hours_ago=2),                     # sent +5
        _row("openapi-fallback", count=2, hours_ago=3, ok=False),  # 失败不进 sent
    ]
    monkeypatch.setattr(push_flow, "FLOW_PATH", _write_flow(tmp_path, rows))
    fb = admin._fallback_stats_24h()
    assert fb == {"count": 3, "total": 11, "sent_total": 6}


def test_fallback_stats_sent_total_read_error_minus_one(monkeypatch, tmp_path):
    """G-B 读异常口径：sent_total 与 count/total 同步 -1，不猜数。"""
    monkeypatch.setattr(push_flow, "FLOW_PATH",
                        str(tmp_path / "missing.jsonl"))
    assert admin._fallback_stats_24h() == {"count": -1, "total": -1,
                                           "sent_total": -1}


def test_public_ops_sent_total_privacy_recheck(monkeypatch, tmp_path):
    """G-B 隐私红线复验：加 sent_total 后响应仍零明文——fallback_24h
    三键均 int、真实账本行含 ou_/oc_ target 但整包序列化后零泄漏。"""
    rows = [
        _row("openapi-fallback", count=2, target="ou_secret123"),
        _row("openapi", count=1, target="oc_secret456"),
    ]
    monkeypatch.setattr(push_flow, "FLOW_PATH", _write_flow(tmp_path, rows))
    payload = _call_public_ops(monkeypatch).payload
    fb = payload["fallback_24h"]
    assert set(fb.keys()) == {"count", "total", "sent_total"}
    assert all(isinstance(v, int) for v in fb.values())
    assert fb["sent_total"] == 3
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "ou_" not in dumped and "oc_" not in dumped
    assert "secret" not in dumped


def test_customer_observe_silent_window_marker(monkeypatch):
    """G-B 静默窗预埋（零行为变更）：customer 域 observe 路径在全局
    静默窗内（load_schedule 同源）给 observe 行 kind 加
    "|silent-window" 后缀；窗外不加。observe 不拦截不发送——缺
    content 早退、零发送调用，纯留痕增强。"""
    cap = []
    monkeypatch.setattr(push_flow, "record",
                        lambda *a, **kw: cap.append((a, kw)))
    monkeypatch.setattr(admin, "_dead_letter_alert_if_needed",
                        lambda *a, **kw: None)
    # 窗内：静默窗覆盖全天（同日窗口 00:00-23:59，任意 now 恒命中）
    monkeypatch.setattr(admin, "load_schedule",
                        lambda: {"sources": [],
                                 "silent_start": "00:00",
                                 "silent_end": "23:59"})
    r = admin.api_send({"domain": "customer", "receive_id": "ou_w"})
    assert r["ok"] is False  # 缺 content 早退，不发送
    assert len(cap) == 1
    a, kw = cap[0]
    assert a[0] == "hub|silent-window"
    assert a[1] == "domain-observe"
    assert kw.get("domain") == "customer"
    # 窗外：窗口宽 0（同日判定 cur<b 恒假）→ kind 不加后缀
    monkeypatch.setattr(admin, "load_schedule",
                        lambda: {"sources": [],
                                 "silent_start": "00:00",
                                 "silent_end": "00:00"})
    cap.clear()
    r2 = admin.api_send({"domain": "customer", "receive_id": "ou_w"})
    assert r2["ok"] is False
    assert len(cap) == 1
    assert cap[0][0][0] == "hub"  # 纯时间窗外无标记


def test_customer_enforce_scope_comment_anchor():
    """G-B 预埋口径注释锚点：enforce 切换后 customer 域 critical 不豁免
    （老田建议口径，M3 另批确认）注释在位；observe 判定走纯时间窗
    （kind="" 不做 per-kind 豁免）。"""
    assert ADMIN_SRC.count("customer 域 critical 不豁免") >= 1
    assert ADMIN_SRC.count("M3 另批确认") >= 1
    assert ADMIN_SRC.count('"customer": "observe"') == 1
    # 代码调用恰 1 处（另一处为本锚点注释文本）
    assert ADMIN_SRC.count('if _in_silent_window(kind=""):') == 1
