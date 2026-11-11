# -*- coding: utf-8 -*-
"""2026-11-11 止损边界测试契约更新：缠论分支 11-10 接入硬止损后的期望重写。

事实链（有提交佐证，非测试臆断）：
  * 该测试 10-21 拍下时的契约：「chanlun 两符号 stop_pct 均被忽略」——
    当时 stop_pct 只在 double_knife 分支被读取，缠论侧传了等于空转
    （「参数看着传了没生效」第 4 例）。
  * 2026-11-10 引擎接入缠论硬止损（cfg.stop_pct < 0 触发、盯市收盘确认、
    默认 open_next 次日开盘成交）。行为变更经影子台账 A/B 验证，是有意
    的修复而非回归。
  * 因此：[-0.125] 用例必须改期望为「跌穿 -12.5% → stop_loss 次日开盘卖」；
    [+0.125] 用例保持「不触发」（引擎 guard 是 cfg.stop_pct < 0）。

只改两个过期用例的断言与注释，其余 double_knife 用例不动（它们本来就在
测双刀分支、且当前全绿）。锚点 count==1 + 读回断言；文件为 LF。
"""
from pathlib import Path

CRLF = chr(13) + chr(10)
TARGET = "tests/test_a3_stop_parameter_boundary_readonly.py"


def rep(old_lines, new_lines, label):
    p = Path(TARGET)
    b = p.read_bytes()
    nl = CRLF.encode("utf-8")
    old = nl.join(
        (x if isinstance(x, bytes) else x.encode("utf-8")) for x in old_lines
    )
    new = nl.join(
        (x if isinstance(x, bytes) else x.encode("utf-8")) for x in new_lines
    )
    if new in b:
        print(label, "SKIP (already applied)")
        return
    cnt = b.count(old)
    assert cnt == 1, label + " anchor count=" + str(cnt)
    p.write_bytes(b.replace(old, new, 1))
    assert new in p.read_bytes(), label + " verify failed"
    print(label, "OK")




# ① 「两符号都忽略止损」→ 按新契约拆成两分支断言
rep(
    [
        '@pytest.mark.parametrize("stop_pct", [-0.125, 0.125])',
        "def test_chanlun_both_signs_ignore_stop_without_other_exit(engine, monkeypatch, stop_pct):",
        '    closes = (100.0, 100.0, 87.5, 87.0, 85.0)',
        '    baseline = _run(engine, monkeypatch, "chanlun", 0.0, closes)',
        '    observed = _run(engine, monkeypatch, "chanlun", stop_pct, closes)',
        "",
        '    assert observed["meta"]["config"]["stop_pct"] == stop_pct',
        '    assert observed["trades"] == baseline["trades"] == [',
        '        {"day": DAYS[1], "stock": STOCK, "side": "BUY", "how": "chanlun", "note": "", "pnl": None}',
        "    ]",
    ],
    [
        '# 2026-11-10 契约变更：缠论分支接入硬止损（stop_pct<0 触发、收盘确认、',
        '# 次日开盘成交；引擎注释「参数看着传了没生效」第 4 例修复）。原',
        '# 「两符号都忽略止损」期望过期，2026-11-11 按新契约重写：',
        '#   负值 → 跌穿阈值 stop_loss 离场；非负（含 0=默认关）→ 不触发。',
        '@pytest.mark.parametrize("stop_pct", [-0.125, 0.125])',
        'def test_chanlun_stop_sign_gates_trigger(engine, monkeypatch, stop_pct):',
        '    closes = (100.0, 100.0, 87.5, 87.0, 85.0)',
        '    observed = _run(engine, monkeypatch, "chanlun", stop_pct, closes)',
        "",
        '    assert observed["meta"]["config"]["stop_pct"] == stop_pct',
        "    if stop_pct < 0:",
        '        # T+2 收盘 87.5 跌穿 -12.5% → 触发，T+3（DAYS[3]）开盘卖',
        '        assert [(t["day"], t["side"], t["how"]) for t in observed["trades"]] == [',
        '            (DAYS[1], "BUY", "chanlun"),',
        '            (DAYS[3], "SELL", "stop_loss"),',
        "        ]",
        "    else:",
        '        assert [(t["day"], t["side"], t["how"]) for t in observed["trades"]] == [',
        '            (DAYS[1], "BUY", "chanlun"),',
        "        ]",
    ],
    "stop-sign-contract-rewrite",
)

# ② 同文件的卖点对照用例：卖出优先级仍应保持卖点先于止损（DAYS[2] 卖点
#    挂 pending_sell 后止损 guard `if s in pending_sell ... continue` 会让位）。
#    该用例当前本来就绿（exit_open 早于止损日），不动，仅记录判断依据。
print("note: signal-exit-priority case left unchanged (already green)")
