# -*- coding: utf-8 -*-
"""P1-6 分层落地守卫（2026-11-08）。

锁住四件事，任何一件被回退，这里就红：

1. `portal/conftest.py` 用 `sys.path.append` 把 `portal/` 塞进搜索路径
   —— 删了这三行，4 个根级 import 型测试立刻退回「收集即错」，
   而且**不会有任何红灯提示**（它们本来就不在套件里），正是 P1-6 要防的静默。
   必须是 append 不是 insert(0)：insert(0) 会顶掉仓库根同名包，
   就是 P0-3 / P2-5 那类遮蔽病的成因。
2. 两个实测红的文件仍在 `skip(reason=...)` 挂起态，且 reason 带坑号
   —— 防止「把 skip 改成删文件」冒充修好。
3. 两个全绿文件仍在 `test_gate.ps1` 的 portal 清单里 —— 防止被静默移出。
4. 10 个脚本式自检在 `DEVELOPER_GUIDE.md` §7.6 的 manual 清单里
   —— 「不进套件」必须留一条可查的跑法，不能只活在别人的记忆里。
"""

import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PORTAL = REPO / "portal"
GUIDE = REPO / "DEVELOPER_GUIDE.md"
GATE = REPO / "tools" / "test_gate.ps1"

# 脚本式自检：`python portal/<file>` 直跑，module 体执行 + 自写 check()，
# pytest 收集只有 0 个用例（rc=5），不是收集错误。
MANUAL_FILES = (
    "test_feishu_event_guard.py",
    "test_inbound_gateway.py",
    "test_intel_bus.py",
    "test_legion_auth_p0.py",
    "test_legion_room_api_live.py",
    "test_legion_room_unit.py",
    "test_org_e2e.py",
    "test_org_guard_p1.py",
    "test_qd_multisource.py",
    "test_voice_ingest.py",
)

# 实测红、等 owner 定性的文件（整模块 skip）
STALE_FILES = (
    "test_alerts_level.py",
    "test_alert_source_health.py",
)

# 补路径后全绿、已纳入 fast 门
GATED_FILES = (
    "test_watch_tower_linkage.py",
    "test_watch_tower_snapshot.py",
)


def _norm(p):
    return os.path.normcase(str(Path(p).resolve()))


def test_portal_dir_on_sys_path_by_conftest():
    """conftest 的 append 必须真生效（本测试与 4 个根级文件共用同一次收集）。"""
    here = _norm(PORTAL)
    assert here in {_norm(p) for p in sys.path if p}, (
        "portal/ 不在 sys.path 里：portal/conftest.py 的 append 被删了？"
        "删掉后 4 个根级 import 型测试会静默退回「收集即错」")


def test_conftest_uses_append_not_insert():
    text = (PORTAL / "conftest.py").read_text(encoding="utf-8")
    assert "sys.path.append(_HERE)" in text, "conftest 的 sys.path.append 不见了"
    assert "sys.path.insert(0" not in text, (
        "不得改成 insert(0)：那会顶掉仓库根同名包，"
        "正是 P0-3 / P2-5 的导入遮蔽成因")


def test_stale_files_still_skip_with_reason():
    for name in STALE_FILES:
        text = (PORTAL / name).read_text(encoding="utf-8")
        m = re.search(r'pytestmark = pytest\.mark\.skip\(reason="([^"]+)"\)', text)
        assert m, (
            "%s 的模块级 skip 不见了：红了要先修，不允许把 skip 删掉冒充修好" % name)
        reason = m.group(1)
        # 只断言「文件里出现过 P1-6」会被注释蒙过去（本守卫首版就被自己的
        # 变异自测咬过：把 reason 里的坑号删掉仍然 PASS），所以必须解析进 reason。
        assert "P1-6" in reason, "%s 的 skip reason 必须带坑号 P1-6，实际：%s" % (name, reason)
        assert "stale" in reason, "%s 的 skip reason 必须标明 stale（等 owner 定性）" % name


def test_gated_files_still_in_fast_portal_suite():
    gate = GATE.read_text(encoding="utf-8-sig")
    for name in GATED_FILES:
        assert "portal/" + name in gate, (
            "%s 被移出 test_gate.ps1 的 portal 清单：这是 P1-6 里唯一"
            "确证可用的回归资产，移出等于白修" % name)


def test_manual_files_documented_in_guide():
    guide = GUIDE.read_text(encoding="utf-8")
    missing = [n for n in MANUAL_FILES if n not in guide]
    assert not missing, (
        "manual 清单（§7.6）漏了 %s —— 不进套件的测试必须留下跑法，"
        "否则下一个人只能靠猜" % missing)
