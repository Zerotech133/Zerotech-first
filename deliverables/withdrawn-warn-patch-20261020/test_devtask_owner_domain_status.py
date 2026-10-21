# -*- coding: utf-8 -*-
"""WO-TF-03 第二处补丁：``external_tools.status`` 未知取值的告警（零生产写入）。

**为什么要有这份测试**：工单未明文规定「批准后该写什么字面量」，代码只认
``legal``。若有人把状态写成 ``approved`` 之类，判定**静默失效**——外部工具代号
仍全部判非法、改了登记表却不生效，且**不报错、不告警**，是极难自查的形态。
本补丁给未知取值加一条 stderr 告警（判定语义不变），本文件把「未知取值会被
告警、已知取值不被告警、同值不重复告警」变成可回归的断言。

覆盖四条：
  1. ``status="legal"`` ⇒ 外部代号**合法**、**无告警**（正常路径不被打扰）；
  2. ``status="pending_ruling"`` ⇒ 外部代号**非法**、**无告警**（当前状态，不打扰）；
  3. ``status="approved"``（及其它未知写法）⇒ 外部代号**仍判非法**（判定语义不变）、
     **有告警**、**同一取值第二次调用不再重复告警**（每进程每值只一次）；
  4. 未知取值**不改变**其余判定：合法成员仍合法、非法成员仍非法、建单仍不拒写。

纪律：
  * **只读生产数据** —— 真实 ``.worktrees/tasks.json`` 与真实
    ``agent_platform/registry/org.json`` / ``owner_domain.json`` 严禁写入。所有夹具
    落系统临时目录，用 monkeypatch 把模块级路径常量指过去。
  * 不执行会写生产账本的命令（不直跑 ``devtask.py new``）。
  * 本文件**不改实现**：发现实现缺陷写成失败用例并上报，不动
    ``tools/devtask.py`` 与 ``agent_platform/registry/owner_domain.json``。
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import devtask  # noqa: E402  —— 需先补 sys.path 再导入


# ----------------------------------------------------------------------
# 夹具
# ----------------------------------------------------------------------
@pytest.fixture
def tmp_root():
    """系统临时目录夹具根 —— 所有写盘只落这里。"""
    d = tempfile.mkdtemp(prefix="wotf03_status_test_")
    try:
        yield Path(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def org_fixture(tmp_root, monkeypatch):
    """把 devtask 的 org.json 路径常量指向临时夹具副本（monkeypatch 路径常量）。"""
    real = json.loads((ROOT / "agent_platform" / "registry" / "org.json")
                      .read_text(encoding="utf-8"))
    fx = tmp_root / "org.json"
    fx.write_text(json.dumps(real, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(devtask, "ORG_JSON", str(fx))
    return fx


@pytest.fixture
def reset_warned():
    """清空「已告警取值」去重集合 —— 保证每条用例独立计数。

    该集合是**模块级**的（每进程每种取值只告警一次），故跨用例会串味：
    不清则「第一次调用告警」在后续用例里可能已被去重掉。
    """
    devtask._WARNED_OWNER_STATUS.clear()
    try:
        yield
    finally:
        devtask._WARNED_OWNER_STATUS.clear()


@pytest.fixture
def domain(tmp_root, monkeypatch):
    """写一份 status 可指定的 owner_domain.json 夹具，返回写值函数。"""

    def _write(status, observed=("Codex·主控", "TraeCode")):
        fx = tmp_root / ("owner_domain_%s.json" % abs(hash(repr(status))))
        payload = {"version": 1, "unowned_tag": "[UNOWNED]"}
        if status is not _MISSING:
            payload["external_tools"] = {"status": status,
                                         "observed": list(observed)}
        fx.write_text(json.dumps(payload, ensure_ascii=False),
                      encoding="utf-8")
        monkeypatch.setattr(devtask, "OWNER_DOMAIN", str(fx))
        return fx

    return _write


_MISSING = object()   # 哨兵：表示「登记表里根本没有 external_tools 键」


# ----------------------------------------------------------------------
# 判据 1：legal —— 外部代号合法，且无告警
# ----------------------------------------------------------------------
class TestStatusLegal:
    """``status="legal"`` 是已裁定的合法写法，必须**完全正常**——不告警。"""

    def test_legal时代理代号合法且无告警(self, tmp_root, monkeypatch,
                                 org_fixture, domain, capsys, reset_warned):
        domain("legal")
        assert devtask._owner_check("Codex·主控")[0] is True
        assert devtask._owner_check("TraeCode")[0] is True
        err = capsys.readouterr().err
        assert "未知取值" not in err, "legal 是已知取值，不该告警：%r" % err


# ----------------------------------------------------------------------
# 判据 2：pending_ruling —— 外部代号非法，但无告警（当前状态，不打扰）
# ----------------------------------------------------------------------
class TestStatusPendingRuling:
    """``pending_ruling`` 是已知的「未裁定」状态，正常路径不被告警。"""

    def test_pending_ruling时判非法且无告警(self, tmp_root, monkeypatch,
                                    org_fixture, domain, capsys, reset_warned):
        domain("pending_ruling")
        assert devtask._owner_check("Codex·主控")[0] is False
        assert devtask._owner_check("TraeCode")[0] is False
        err = capsys.readouterr().err
        assert "未知取值" not in err, "pending_ruling 是已知取值，不该告警：%r" % err


# ----------------------------------------------------------------------
# 判据 3：未知取值 —— 判定不变 + 告警一次 + 同值不重复
# ----------------------------------------------------------------------
class TestStatusUnknownWarns:
    """未知取值：判定语义**不变**（仍判非法），但必须**显式告警**且不重复。"""

    @pytest.mark.parametrize("status", ["approved", "accepted", "true", "",
                                        "granted", "legal_pending", "ok"])
    def test_未知取值判非法且有告警(self, tmp_root, monkeypatch, org_fixture,
                             domain, capsys, reset_warned, status):
        domain(status)
        ok, _dom, _size = devtask._owner_check("Codex·主控")
        assert ok is False, "未知取值必须仍按「外部代号不合法」处理（判定不变）"
        err = capsys.readouterr().err
        assert "未知取值" in err, "未知取值必须显式告警：%r" % err
        assert repr(status) in err, "告警须写明 status 原值：%r" % err
        assert "legal" in err, "告警须提示仅 legal 生效：%r" % err

    def test_同一取值第二次调用不再重复告警(self, tmp_root, monkeypatch,
                                  org_fixture, domain, capsys, reset_warned):
        """每进程每种取值只告警一次 —— 避免每次建单刷屏。"""
        domain("approved")
        devtask._owner_check("Codex·主控")
        first = capsys.readouterr().err
        assert first.count("未知取值") == 1, "首次应告警一次：%r" % first
        devtask._owner_check("Codex·主控")
        devtask._owner_check("TraeCode")
        second = capsys.readouterr().err
        assert second.count("未知取值") == 0, \
            "同值重复调用不应再告警（每进程每值只一次）：%r" % second

    def test_不同未知取值各自告警一次(self, tmp_root, monkeypatch, org_fixture,
                              domain, capsys, reset_warned):
        """换了另一个未知取值 ⇒ 属新问题，应重新告警。"""
        domain("approved")
        devtask._owner_check("Codex·主控")
        capsys.readouterr()
        domain("accepted")
        devtask._owner_check("Codex·主控")
        err = capsys.readouterr().err
        assert err.count("未知取值") == 1, "新取值应重新告警：%r" % err

    def test_登记表无external_tools键时告警(self, tmp_root, monkeypatch,
                                  org_fixture, domain, capsys, reset_warned):
        """登记表整个缺 ``external_tools`` 段 ⇒ status 取不到值 ⇒ 属未知取值。"""
        domain(_MISSING)
        ok, _dom, _size = devtask._owner_check("Codex·主控")
        assert ok is False
        err = capsys.readouterr().err
        assert "未知取值" in err, "缺 status 取值应告警：%r" % err


# ----------------------------------------------------------------------
# 判据 4：未知取值不改变其余判定（不拒写、成员合法性不变）
# ----------------------------------------------------------------------
class TestUnknownStatusKeepsOtherSemantics:
    """未知取值只影响「可观测性」，**不得**改变任何其它判定。"""

    def test_合法成员仍合法非法成员仍非法(self, tmp_root, monkeypatch,
                                org_fixture, domain, capsys, reset_warned):
        domain("approved")
        assert devtask._owner_check("DEV1")[0] is True, "真实成员仍应合法"
        assert devtask._owner_check("NOT_REGISTERED_XYZ")[0] is False, \
            "未注册 ID 仍应判非法"
        capsys.readouterr()

    def test_建单不拒写且标签行为不变(self, tmp_root, monkeypatch, org_fixture,
                              domain, capsys, reset_warned):
        """未知取值下建单**仍不拒写**，非法 owner 仍打 ``[UNOWNED]``。"""
        wt = tmp_root / ".worktrees"
        wt.mkdir(parents=True, exist_ok=True)
        tasks = wt / "tasks.json"
        monkeypatch.setattr(devtask, "WT_DIR", str(wt))
        monkeypatch.setattr(devtask, "TASKS", str(tasks))
        monkeypatch.setattr(devtask, "_git", lambda *a, **k: "")
        monkeypatch.setattr(devtask, "_ckpt", lambda *a, **k: None)
        domain("approved")
        monkeypatch.setenv("AGENT_NAME", "TraeCode")
        devtask.cmd_new("K-UNK", "未知 status 建单")
        data = json.loads(tasks.read_text(encoding="utf-8"))
        assert data["K-UNK"]["status"] == "doing", "不得拒写"
        assert "[UNOWNED]" in data["K-UNK"]["desc"], "标签行为不得改变"
        err = capsys.readouterr().err
        assert "未知取值" in err, "未知取值仍须告警：%r" % err
