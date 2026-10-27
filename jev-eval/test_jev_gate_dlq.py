# -*- coding: utf-8 -*-
# K-STOCK-401 守卫测试：subscribe 状态机三修 + jev_gate 死信升级/dry 污染
# （monkeypatch 驱动，不真发飞书、不碰 DB/生产状态文件）
#
# D4 修复（2026-10-18）：原为脚本式测试——主体在模块级直接执行并以
# sys.exit(1 if FAIL else 0) 收尾，全仓 pytest 收集期 import 即抛
# SystemExit → INTERNALERROR。现改为：主体包进 _run_script()
# （收集期零执行），pytest 经 test_jev_gate_dlq() 驱动；
# 脚本单跑能力保留（python test_jev_gate_dlq.py 仍以退出码报告结果）。
import json
import os
import sys
import tempfile

# K-STOCK-406：路径锚相对测试文件定位（worktree 开发期/merge 后主仓均跑得动；
# 原 .worktrees/K-STOCK-401 硬编码在 worktree 清场后失效）
WT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_MISSING = object()


def _snapshot(obj, names):
    """记录 obj 上将被替换属性的原值（缺失以 _MISSING 标记）。"""
    return {n: getattr(obj, n, _MISSING) for n in names}


def _restore(obj, saved):
    """还原 _snapshot 记录的属性（pytest 套件内多脚本共享进程，防串扰）。"""
    for n, v in saved.items():
        if v is _MISSING:
            if hasattr(obj, n):
                delattr(obj, n)
        else:
            setattr(obj, n, v)


def _run_script():
    """执行原脚本主体；返回失败项数量（0=全过）。"""
    sys.path.insert(0, os.path.join(WT, "quant_backtest"))
    old_cwd = os.getcwd()
    os.chdir(os.path.join(WT, "quant_backtest"))
    FAIL = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            FAIL.append(name)

    SB_ = None
    G_ = None
    saved_sb = {}
    saved_g = {}
    try:
        # ── Part A：subscribe.run_scan 状态机 ─────────────────────
        import subscribe as SB

        # 先存后还原：套件内多脚本共享进程，防 monkeypatch 串扰
        saved_sb = _snapshot(SB, ("STATE_PATH", "SUB_PATH", "RECV_PATH",
                                  "_BUILDERS", "_build_card", "_resolve_targets",
                                  "_push_card"))
        SB_ = SB

        tmp_state = os.path.join(tempfile.gettempdir(), "_k401_sub_state.json")
        SB.STATE_PATH = tmp_state
        if os.path.exists(tmp_state):
            os.remove(tmp_state)

        # 假订阅 + 假 builder（回写 _fp_new，模拟 quote_move/buy_point 修复后行为）
        FAKE = {"subscriptions": [{"id": "s1", "kind": "quote_move",
                                   "when": ["intraday"], "enabled": True,
                                   "receivers": ["个人"]}]}
        SB.SUB_PATH = os.path.join(tempfile.gettempdir(), "_k401_subs.json")
        json.dump(FAKE, open(SB.SUB_PATH, "w", encoding="utf-8"))
        SB.RECV_PATH = os.path.join(tempfile.gettempdir(), "_k401_recv.json")
        json.dump({"receivers": [{"name": "个人", "type": "open_id",
                                  "id": "ou_test"}]},
                  open(SB.RECV_PATH, "w", encoding="utf-8"))

        _push_calls = []

        def _fake_builder(sub, mode):
            fp = "600519:1.5"
            if mode == "intraday" and sub.get("_last_fp") == fp:
                return None  # 与真实 builder 同款去重短路
            sub["_fp_new"] = fp
            return ("📈 T", "s", ["b1"], "purple")

        SB._BUILDERS = {"quote_move": _fake_builder}
        # K-PUSH-002：run_scan 现以 url_button=... 调 _build_card（看板直达深链），
        # 桩签名需吞掉该 kwarg，否则 TypeError 被吞成 card_err → A1/A2 误判
        SB._build_card = lambda title, sub2, blocks, template="blue", footer=None, **kw: {"c": 1}
        SB._resolve_targets = lambda sub, recv: [{"name": "个人", "type": "open_id",
                                                  "id": "ou_test"}]

        # A1：推送失败 → 指纹不落盘（修复前失败也写 → 下轮漏推）
        SB._push_card = lambda card, targets: {"个人": False}
        r1 = SB.run_scan("intraday")
        check("A1 fail 不落指纹", r1["s1"][0] == "fail"
              and "last_fp" not in (SB._load_state().get("s1") or {}))

        # A2：推送成功 → 真实指纹落盘（_fp_new 回环，不再是块文本）
        SB._push_card = lambda card, targets: {"个人": True}
        r2 = SB.run_scan("intraday")
        st = SB._load_state()["s1"]
        check("A2 成功落真实指纹", r2["s1"][0] == "ok" and st["last_fp"] == "600519:1.5")

        # A3：同指纹再次扫描 → no_change（去重回环生效；修复前必重推）
        r3 = SB.run_scan("intraday")
        check("A3 同指纹去重", r3["s1"][0] == "no_change")

        # A4：cap 未被失败消耗（A1 失败不计次；A2 计 1；连续成功 2 次后仍可推）
        #（A3 未推送不计；再把指纹变掉应还能推——count=1 < 3）
        def _fake_builder2(sub, mode):
            fp = "600519:2.7"
            if mode == "intraday" and sub.get("_last_fp") == fp:
                return None
            sub["_fp_new"] = fp
            return ("📈 T2", "s", ["b2"], "purple")
        SB._BUILDERS = {"quote_move": _fake_builder2}
        r4 = SB.run_scan("intraday")
        check("A4 新指纹可推（cap 未误耗）", r4["s1"][0] == "ok")

        # ── Part B：jev_gate 死信升级 + dry 污染 ──────────────────
        sys.path.insert(0, os.path.join(WT, "jev-eval"))
        import jev_gate as G

        # 先存后还原（B1 依赖真实 _push_with_dlq / _escalate_dlq）
        saved_g = _snapshot(G, ("_escalate_dlq", "_append_dead_letter",
                                "push_feishu", "mark_seen", "_push_with_dlq"))
        G_ = G

        # B1：终态死信 → 触发 B 通道升级（monkeypatch 捕获，不真发）
        escalated = []
        G._escalate_dlq = lambda alert, reason: escalated.append((alert["alert_id"], reason))
        G._append_dead_letter = lambda *a, **k: None
        G.push_feishu = lambda a, v: False
        G._PUSH_FAILS.clear()
        alert = {"alert_id": "a1", "level": "WARN", "title": "t"}
        for i in range(G.MAX_PUSH_ATTEMPTS):
            ok = G._push_with_dlq(alert, {"urgent_p": 0.9})
        check("B1 第5轮终态", ok is True and len(escalated) == 1
              and escalated[0][0] == "a1")

        # B2：dry 不 mark_seen（修复前 dry 也落 seen → 真实告警被吞 7 天）
        seen_marks = []
        G.mark_seen = lambda aid: seen_marks.append(aid)
        G._push_with_dlq = lambda a, v: True
        G._handle_alert({"alert_id": "c1", "level": "CRITICAL", "title": "t"}, dry=True)
        check("B2 CRITICAL dry 不落 seen", seen_marks == [])
        G._handle_alert({"alert_id": "w1", "level": "WARN", "title": "t"}, dry=True)
        check("B2 WARN dry 不落 seen", seen_marks == [])
        # B2b：非 dry 正常落（回归保护）
        G._handle_alert({"alert_id": "c2", "level": "CRITICAL", "title": "t"}, dry=False)
        check("B2b 非 dry 正常落 seen", seen_marks == ["c2"])
    finally:
        os.chdir(old_cwd)
        if SB_ is not None:
            _restore(SB_, saved_sb)
        if G_ is not None:
            _restore(G_, saved_g)

    print("FAILURES: %d" % len(FAIL))
    return len(FAIL)


def test_jev_gate_dlq():
    """pytest 入口：脚本主体零失败才通过（收集期零执行）。"""
    assert _run_script() == 0, "K-STOCK-401 守卫存在 FAIL 项，见上方 PASS/FAIL 明细"


if __name__ == "__main__":
    sys.exit(1 if _run_script() else 0)
