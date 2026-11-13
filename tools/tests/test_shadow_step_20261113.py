# -*- coding: utf-8 -*-
"""盘前第 6 步（排序影子）的接线测试（2026-11-13）。

要固定的事：
1. 影子用哪份共识包 —— 必须取「本次运行用的那份」，取不到就跳过，不许拿最新的顶上。
2. 影子失败绝不影响主流程 —— 它返回的 rc 不进 out["rc"]，异常不外抛。
3. 超时按 rc=124 回来，不冒充崩溃。

不测「shadow_rank.py 本身算得对不对」—— 那是 zerobound-kg 侧 tests/ 的事。
这里只测接线，两边各管一段。
"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PREOPEN = os.path.dirname(HERE)          # tools/
sys.path.insert(0, os.path.join(PREOPEN, "preopen"))

import run_preopen as rp  # noqa: E402

# 日期字面量一律码点构造：本机传输通道会把数字串味，凭打印结果判断不可信。
PKG = "".join(chr(c) for c in (0x32, 0x30, 0x32, 0x36, 0x31, 0x31, 0x31, 0x30))  # 20261110
DAY = "".join(chr(c) for c in (0x32, 0x30, 0x32, 0x36, 0x31, 0x31, 0x31, 0x32))  # 20261112


def _pick_file(tmp_path, pkg_arg):
    p = tmp_path / "pick.json"
    payload = {"picker_run_at": "2026-11-13 08:12:00", "chain_policy_v": 4}
    if pkg_arg is not None:
        payload["picker_args"] = {"pkg": pkg_arg, "asof": DAY, "top_n": 15}
    io.open(str(p), "w", encoding="utf-8").write(json.dumps(payload))
    return str(p)


def test_pkg_from_pick_reads_artifact(tmp_path):
    """产物自述的包名要能取回 —— 这是「影子与实盘同输入」的唯一保证。"""
    f = _pick_file(tmp_path, os.path.join("X:", "kg", "strategy_center_inbox", PKG))
    assert rp._pkg_from_pick(f) == PKG


def test_pkg_from_pick_rejects_non_date_dirname(tmp_path):
    """目录名不是 8 位数字就不当包名用 —— 否则会把一条假告警伪装成包不存在。"""
    f = _pick_file(tmp_path, os.path.join("X:", "kg", "somewhere_else"))
    assert rp._pkg_from_pick(f) is None


def test_pkg_from_pick_absent_and_unreadable(tmp_path):
    assert rp._pkg_from_pick(str(tmp_path / "nope.json")) is None
    bad = tmp_path / "bad.json"
    io.open(str(bad), "w", encoding="utf-8").write("{not json")
    assert rp._pkg_from_pick(str(bad)) is None


def test_pkg_from_pick_missing_picker_args(tmp_path):
    """老产物没有 picker_args —— 返回 None，不许猜。"""
    f = _pick_file(tmp_path, None)
    assert rp._pkg_from_pick(f) is None


def test_shadow_step_passes_explicit_pkg_and_day(tmp_path):
    """本次运行已算出 pkg 时，命令里必须是它，且带 --asof <数据日>。"""
    seen = {}

    def fake(cmd, cwd=None, timeout=None):
        seen["cmd"], seen["cwd"], seen["timeout"] = cmd, cwd, timeout
        return 0, "asof=%s 池=82 候选=82\nlam=0.50 -> 1.19 x sd" % DAY

    r = rp.shadow_step(PKG, DAY, str(tmp_path / "unused.json"), runner=fake)
    assert r["rc"] == 0
    assert r["pkg"] == PKG and r["asof"] == DAY
    assert not r.get("skipped")
    assert "--pkg" in seen["cmd"] and PKG in seen["cmd"]
    assert "--asof" in seen["cmd"] and DAY in seen["cmd"]
    assert seen["cwd"] == rp.PICK_DIR
    # 没有上限＝一个观测步骤能拖死出票链路
    assert seen["timeout"] == rp.SHADOW_TIMEOUT
    assert rp.SHADOW_TIMEOUT and rp.SHADOW_TIMEOUT > 0


def test_shadow_step_falls_back_to_artifact_pkg(tmp_path):
    """复用路径（pkg=None）时，用产物自述的那份，而不是重推最新包。"""
    f = _pick_file(tmp_path, os.path.join("X:", "kg", "inbox", PKG))
    seen = {}

    def fake(cmd, cwd=None, timeout=None):
        seen["cmd"] = cmd
        return 0, "ok"

    r = rp.shadow_step(None, DAY, f, runner=fake)
    assert r["pkg"] == PKG
    assert PKG in seen["cmd"]


def test_shadow_step_skips_loudly_when_pkg_unknown(tmp_path):
    """取不到包名就跳过，并把原因写进记录 —— 不许静默用别的包。"""
    r = rp.shadow_step(None, DAY, str(tmp_path / "nope.json"), runner=None)
    assert r["skipped"] is True
    assert "reason" in r and r["reason"]
    assert "pkg" not in r


def test_shadow_step_nonzero_rc_is_recorded_not_raised(tmp_path):
    """影子失败只是「今天的对照没记上」，不是任务失败。"""
    r = rp.shadow_step(PKG, DAY, str(tmp_path / "x.json"),
                       runner=lambda cmd, cwd=None, timeout=None: (3, "包不存在"))
    assert r["rc"] == 3
    assert "包不存在" in r["out"]


def test_shadow_step_swallows_runner_exception(tmp_path):
    """runner 抛异常也不能冒泡 —— 否则第 6 步会毁掉一次已经出完票的盘前。"""
    def boom(cmd, cwd=None, timeout=None):
        raise OSError("disk gone")

    r = rp.shadow_step(PKG, DAY, str(tmp_path / "x.json"), runner=boom)
    assert r["rc"] == -1
    assert "OSError" in r["error"]


def test_shadow_step_output_is_truncated(tmp_path):
    """只留前 4 行：台账记录不是日志转储。"""
    noisy = "\n".join("line%d" % i for i in range(50))
    r = rp.shadow_step(PKG, DAY, str(tmp_path / "x.json"),
                       runner=lambda cmd, cwd=None, timeout=None: (0, noisy))
    assert len(r["out"].splitlines()) == 4


def test_step_numbering_is_six():
    """步数标记与真实步数一致 —— 曾经是 5 步，加了影子不改编号会读成「又跑了一遍 5」。"""
    src = io.open(os.path.join(PREOPEN, "preopen", "run_preopen.py"),
                  encoding="utf-8-sig").read()
    for i in (1, 2, 3, 4, 5):
        assert "[%d/6]" % i in src, "第 %d 步的标记没改成 /6" % i
    assert "[6/6]" in src
    assert "/5]" not in src, "还有残留的 /5 标记"


def test_policy_v_matches_pick15_artifact_value():
    """口径版本必须与 pick15 产出的 chain_policy_v 一致。

    11-13 的真实缺口：pick15 升到 v4（新增 ratio 硬否决），这边还是 v3，
    于是 --skip-pick 会被自己的版本号挡住（产物 v4 vs 期望 v3）。
    这个断言读的是**产出方源码**，不是抄一个数字 —— 抄的数字会一起过期。
    """
    src_path = os.path.join(rp.PICK_DIR, "_preopen_pick15_v2_adj.py")
    if not os.path.isfile(src_path):
        import pytest
        pytest.skip("找不到 pick15 源码，无法交叉核对")
    src = io.open(src_path, encoding="utf-8-sig").read()
    # 用正则而不是 find+切片：切片从 marker 之后开始，首字符是冒号后的空格或换行，
    # 手写的取数循环会在第一个非数字字符就 break，于是恒取不到值 —— 而且
    # 失败信息是「解析不出来」，看起来像源码有问题，实际是测试自己有 bug。
    m = re.search(r'"chain_policy_v"\s*:\s*(\d+)', src)
    assert m, "pick15 里没有 chain_policy_v 字段，口径核对失效"
    got = m.group(1)
    assert got, "解析不出 chain_policy_v 的值"
    assert int(got) == rp.POLICY_V, (
        "口径版本不一致：pick15 产出 v%s，run_preopen 期望 v%s" % (got, rp.POLICY_V))


def test_fg_daily_chain_policy_v_matches():
    """日链的 REFINE_POLICY_V 也必须同版本 —— 三处一起改是定死的规矩。"""
    src_path = os.path.join(rp.PICK_DIR, "fg_daily_chain.py")
    if not os.path.isfile(src_path):
        import pytest
        pytest.skip("找不到 fg_daily_chain.py")
    src = io.open(src_path, encoding="utf-8-sig").read()
    m = re.search(r'REFINE_POLICY_V\s*=\s*(\d+)', src)
    assert m, "日链里没有 REFINE_POLICY_V，核对失效"
    got = m.group(1)
    assert got, "解析不出 REFINE_POLICY_V 的值"
    assert int(got) == rp.POLICY_V, (
        "日链口径 v%s 与 run_preopen v%s 不一致" % (got, rp.POLICY_V))
