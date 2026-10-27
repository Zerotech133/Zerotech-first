# -*- coding: utf-8 -*-
"""1卖+趋✓ 飞书推送幂等逻辑验证脚本。

覆盖：
    1. 首次推送 N 条 → 全部推送 + pushed 文件记录 N 键
    2. 重发同样 N 条 → 全部跳过（幂等）
    3. 混合输入（已推 + 新）→ 只推新信号
    4. 非 1卖 或 tbe!=True → 不触发推送
    5. config 开关关闭 → 不推送
    6. 空输入 / None → 安全返回 0

用法（双模式）：
    python test_push_idempotency.py            # 直跑：逐项打印 + 汇总
    python -m pytest test_push_idempotency.py  # pytest：断言失败即 FAIL
    python test_push_idempotency.py --live     # 桩透传：真实发飞书（链路验证）

说明（2026-10-05）：
    - 用例断言由 return ok 改为 assert ok：消除 pytest
      PytestReturnNotNoneWarning，且 pytest 下失败真正判 FAIL
      （此前 return False 仅告警、测试仍记 passed）。
    - 直跑模式由 main() 的 _ok() 包装：断言失败捕获为 FAIL。
    - K187 桩化：默认 cs._push_feishu_text 替换为记录型桩（不真发
      飞书，pushed 文件逻辑照常走全链）；--live 才透传真推送。
      桩记录 _PUSH_CALLS 供交叉断言（桩调用数=飞书消息批次数，
      与推送信号条数是两个维度——多条信号合并一条消息）。

注意：默认模式零飞书副作用；跑完自动清理 pushed 文件，不留测试痕迹。
"""

import os
import sys
import json
import chanlun_scan as cs

_PUSH_CALLS = []
_LIVE = "--live" in sys.argv
_ORIG_PUSH = cs._push_feishu_text


def _stub_push(text):
    """K187 桩：默认记录调用不真发；--live 时透传 _ORIG_PUSH 真推送。"""
    if _LIVE:
        return _ORIG_PUSH(text)
    _PUSH_CALLS.append(text)
    return True


cs._push_feishu_text = _stub_push


def _section(title):
    print("\n" + "=" * 60)
    print("  %s" % title)
    print("=" * 60)


def _cleanup():
    """清理测试产生的 pushed 文件。"""
    if os.path.exists(cs.PUSHED_1SELL_TBE):
        os.remove(cs.PUSHED_1SELL_TBE)


def _restore_config():
    """恢复 config.json 的 push_1sell_tbe 开关为 True。"""
    cfg_path = os.path.join(cs.HERE, "config.json")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    if not (cfg.get("chanlun") or {}).get("push_1sell_tbe"):
        cfg.setdefault("chanlun", {})["push_1sell_tbe"] = True
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
    return cfg_path


def test_1_first_push():
    _section("测试 1: 首次推送 2 条新信号")
    _cleanup()
    batch = [
        {
            "code": "TEST_A.SZ",
            "name": "测试A",
            "type": "1卖",
            "dt": "2026-09-25",
            "price": 10,
            "detect_date": "2026-09-27",
            "lag": 3,
            "entry_px": 9.8,
            "tbe": True,
        },
        {
            "code": "TEST_B.SZ",
            "name": "测试B",
            "type": "1卖",
            "dt": "2026-09-26",
            "price": 20,
            "detect_date": "2026-09-27",
            "lag": 5,
            "entry_px": 19.5,
            "tbe": True,
        },
    ]
    n = cs.push_1sell_tbe_alert(batch)
    pushed = cs._load_pushed()
    # 桩调用数=飞书消息批次数：fresh 拼单条文本一次发送（K187 修正，
    # 原 erroneously 期望 2——2 条信号合 1 条消息）
    ok = (
        n == 2
        and len(pushed) == 2
        and len(_PUSH_CALLS) == 1
        and "TEST_A.SZ" in _PUSH_CALLS[0]
        and "TEST_B.SZ" in _PUSH_CALLS[0]
    )
    print("  推送数: %d (期望 2)" % n)
    print("  pushed 键: %s" % sorted(pushed))
    print("  桩调用数(飞书批次): %d (期望 1，2 条合 1 条消息)" % len(_PUSH_CALLS))
    print("  结果: %s" % ("PASS" if ok else "FAIL"))
    assert ok, "首次推送：应推 2 条且 pushed 记录 2 键"


def test_2_repush_same():
    _section("测试 2: 重发同样 2 条，应全部跳过（幂等核心）")
    batch = [
        {
            "code": "TEST_A.SZ",
            "name": "测试A",
            "type": "1卖",
            "dt": "2026-09-25",
            "price": 10,
            "detect_date": "2026-09-27",
            "lag": 3,
            "entry_px": 9.8,
            "tbe": True,
        },
        {
            "code": "TEST_B.SZ",
            "name": "测试B",
            "type": "1卖",
            "dt": "2026-09-26",
            "price": 20,
            "detect_date": "2026-09-27",
            "lag": 5,
            "entry_px": 19.5,
            "tbe": True,
        },
    ]
    n = cs.push_1sell_tbe_alert(batch)
    ok = n == 0
    print("  推送数: %d (期望 0)" % n)
    print("  结果: %s" % ("PASS" if ok else "FAIL"))
    assert ok, "重发同样批次：应全部跳过"


def test_3_mixed_old_new():
    _section("测试 3: 1 条已推送 + 1 条新信号 → 只推新的")
    batch = [
        {
            "code": "TEST_A.SZ",
            "name": "测试A",
            "type": "1卖",
            "dt": "2026-09-25",
            "price": 10,
            "detect_date": "2026-09-27",
            "lag": 3,
            "entry_px": 9.8,
            "tbe": True,
        },  # 已推送
        {
            "code": "TEST_C.SZ",
            "name": "测试C",
            "type": "1卖",
            "dt": "2026-09-24",
            "price": 30,
            "detect_date": "2026-09-27",
            "lag": 7,
            "entry_px": 29.0,
            "tbe": True,
        },  # 新
    ]
    n = cs.push_1sell_tbe_alert(batch)
    pushed = cs._load_pushed()
    ok = n == 1 and len(pushed) == 3
    print("  推送数: %d (期望 1)" % n)
    print("  pushed 键数: %d (期望 3)" % len(pushed))
    print("  结果: %s" % ("PASS" if ok else "FAIL"))
    assert ok, "混合输入：应只推 1 条新信号"


def test_4_wrong_type_or_tbe():
    _section("测试 4: 非 1卖 或 tbe!=True → 不触发推送")
    batch = [
        {
            "code": "TEST_D.SZ",
            "name": "测试D",
            "type": "2买",
            "dt": "2026-09-23",
            "tbe": True,
        },  # 非 1卖
        {
            "code": "TEST_E.SZ",
            "name": "测试E",
            "type": "1卖",
            "dt": "2026-09-22",
            "tbe": False,
        },  # tbe=False
        {
            "code": "TEST_F.SZ",
            "name": "测试F",
            "type": "1卖",
            "dt": "2026-09-21",
            "tbe": None,
        },  # tbe=None
    ]
    n = cs.push_1sell_tbe_alert(batch)
    pushed = cs._load_pushed()
    ok = n == 0 and len(pushed) == 3  # pushed 不应变
    print("  推送数: %d (期望 0)" % n)
    print("  pushed 键数: %d (期望仍 3)" % len(pushed))
    print("  结果: %s" % ("PASS" if ok else "FAIL"))
    assert ok, "非 1卖 / tbe!=True：不应推送"


def test_5_config_switch_off():
    _section("测试 5: 关闭 config 开关 → 不推送")
    cfg_path = os.path.join(cs.HERE, "config.json")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    orig = (cfg.get("chanlun") or {}).get("push_1sell_tbe")
    cfg.setdefault("chanlun", {})["push_1sell_tbe"] = False
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    try:
        batch = [
            {
                "code": "TEST_G.SZ",
                "name": "测试G",
                "type": "1卖",
                "dt": "2026-09-20",
                "tbe": True,
            }
        ]
        n = cs.push_1sell_tbe_alert(batch)
        ok = n == 0
        print("  推送数: %d (期望 0)" % n)
        print("  结果: %s" % ("PASS" if ok else "FAIL"))
    finally:
        # 恢复
        cfg["chanlun"]["push_1sell_tbe"] = orig if orig is not None else True
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
    assert ok, "开关关闭：不应推送"


def test_6_empty_input():
    _section("测试 6: 空输入 / None → 安全返回 0")
    n1 = cs.push_1sell_tbe_alert([])
    n2 = cs.push_1sell_tbe_alert(None)
    ok = n1 == 0 and n2 == 0
    print("  空列表推送数: %d (期望 0)" % n1)
    print("  None 输入推送数: %d (期望 0)" % n2)
    print("  结果: %s" % ("PASS" if ok else "FAIL"))
    assert ok, "空输入 / None：应安全返回 0"


def _ok(fn):
    """直跑模式汇总：断言失败转 FAIL（pytest 下无需此包装）。"""
    try:
        fn()
        return True
    except AssertionError:
        return False


def main():
    _restore_config()
    results = [
        ("首次推送 2 条", _ok(test_1_first_push)),
        ("重发幂等跳过", _ok(test_2_repush_same)),
        ("混合旧+新只推新", _ok(test_3_mixed_old_new)),
        ("非1卖/tbe!=True不推", _ok(test_4_wrong_type_or_tbe)),
        ("开关关闭不推", _ok(test_5_config_switch_off)),
        ("空输入安全", _ok(test_6_empty_input)),
    ]
    _cleanup()  # 最终清理

    print("\n" + "=" * 60)
    print("  汇总（模式：%s）" % ("LIVE 真推送" if _LIVE else "STUB 桩化零副作用"))
    print("=" * 60)
    for name, ok in results:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
    total = len(results)
    passed = sum(1 for _, ok in results if ok)
    print("\n  %d/%d PASS" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys_exit = __import__("sys").exit
    sys_exit(main())
