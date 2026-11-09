# -*- coding: utf-8 -*-
"""WO-LEGION-DEVEXEC-20261027 devtask 研发试点门（dev_owner_gate）回归测试。

**为什么必须有这份测试**：试点门是「warn 只告警 → 改 block 才拒收」的渐进治理
开关。若它被并行 worktree 合并回退，`tools/devtask.py` 语法正常、既有测试仍全绿、
`cmd_new` 仍返回 0 —— 研发单会**静默绕过** owner/验收命令校验，试点门等于不存在，
且没有任何报错。本文件把「warn 告警不拒收 / block 前置拒收 / 非 dev 不受影响 /
配置缺失回默认」变成可回归的断言。

覆盖工单要求的四类场景：
  ① warn 模式缺 acceptance 只告警不拒收，且 tasks 记 ``dept``/``acceptance_cmd``、
     事件 detail 前缀 ``[dev-gate]``；
  ② block 模式缺 acceptance（或 owner 非法）拒收（``SystemExit``），且拒收发生在
     ``git worktree add`` **之前**（不留空 worktree、不落账、不发 task_start）；
  ③ 非 dev 单不受 gate 影响（block 模式下照样建单）；
  ④ ``_load_dev_gate`` 缺文件/坏 JSON 回默认 ``{"phase": "warn", "dept": "dev"}``。

纪律（沿用 test_devtask_owner_domain.py）：
  * **只读生产数据** —— 真实 ``.worktrees/tasks.json``、``org.json``、
    ``dev_owner_gate.json`` 严禁写入；夹具落系统临时目录，monkeypatch 模块级
    路径常量。
  * 不执行真实 git/checkpoint：``_git``/``_ckpt``/``_emit`` 全部打桩；「block 前置
    拒收」靠 git 调用记录断言（拒收时 git 调用数必须为 0）。
  * 不直跑 ``devtask.py new`` CLI（``main`` 入口同样委派 ``cmd_new``，已覆盖）。
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import devtask  # noqa: E402


# ----------------------------------------------------------------------
# 夹具（全部落系统临时目录，零生产写入）
# ----------------------------------------------------------------------
@pytest.fixture
def tmp_root():
    """系统临时目录夹具根 —— 所有写盘只落这里。"""
    d = tempfile.mkdtemp(prefix="wo_devexec_gate_")
    try:
        yield Path(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sandbox(tmp_root, monkeypatch):
    """把 devtask 的 IO 面全部指向临时目录，并桩掉 git/checkpoint/事件。

    返回 dict：``ledger``（临时 tasks.json 路径）、``git_calls``（git 参数记录）、
    ``events``（``_emit`` 捕获的 (kind, task, detail) 列表）、``gate_path``
    （临时门配置路径）、``tmp``（夹具根）。
    """
    wt = tmp_root / ".worktrees"
    wt.mkdir(parents=True, exist_ok=True)
    ledger = wt / "tasks.json"
    monkeypatch.setattr(devtask, "WT_DIR", str(wt))
    monkeypatch.setattr(devtask, "TASKS", str(ledger))

    git_calls = []

    def _fake_git(*args, **kwargs):
        git_calls.append(args)
        return ""

    monkeypatch.setattr(devtask, "_git", _fake_git)
    monkeypatch.setattr(devtask, "_ckpt", lambda *a, **k: None)
    events = []
    monkeypatch.setattr(
        devtask,
        "_emit",
        lambda kind, task, detail="": events.append((kind, task, detail)),
    )
    # org.json 夹具：最小合成组织图（不读真实 org.json —— 多 Agent 共享 worktree
    # 期间它可能被其他工单并发修改，试点门判定不应因此抖动）。
    org = tmp_root / "org.json"
    org.write_text(
        json.dumps(
            {
                "chief": {"agent_id": "A0"},
                "departments": [
                    {"dept": "dev", "lead": "DEV1", "agents": ["DEV1", "DEV2"]}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(devtask, "ORG_JSON", str(org))
    # owner_domain 夹具：external_tools 维持 pending_ruling，非法 owner 可复现
    domain = tmp_root / "owner_domain.json"
    domain.write_text(
        json.dumps(
            {
                "version": 1,
                "note": "试点门测试夹具",
                "external_tools": {
                    "status": "pending_ruling",
                    "observed": ["TraeCode"],
                },
                "unowned_tag": "[UNOWNED]",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(devtask, "OWNER_DOMAIN", str(domain))
    gate_path = tmp_root / "dev_owner_gate.json"
    monkeypatch.setattr(devtask, "DEV_GATE", str(gate_path))
    # 2026-10-27 WO-LEGION-DEVEXEC3：dev 单路由规则夹具（dev_seats 只含 DEV1/DEV2，
    # 便于复现「合法 owner=A0 但不在研发部」的路由告警；缺文件=不启用，向后兼容）。
    routing_path = tmp_root / "dev_routing_rules.json"
    routing_path.write_text(
        json.dumps(
            {
                "version": 1,
                "dev_dept": "dev",
                "dev_seats": ["DEV1", "DEV2"],
                "note": "测试夹具",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(devtask, "DEV_ROUTING", str(routing_path))
    monkeypatch.setenv("AGENT_NAME", "DEV1")  # org.json 内的合法成员
    # 并发闸隔离（2026-11-08 K-ORG-20261108-OVWSPLIT 复跑时踩到）：
    # ``cmd_new`` 第一步调 ``_enforce_worktree_cap()``，而 ``_active_worktree_count()``
    # 数的是**本机真实** ``.worktrees``（git worktree list），夹具的 WT_DIR/TASKS
    # 覆盖不到它。08:0x 本机恰好顶到 65/65（并行会话开了第 65 个 worktree），
    # 闸门 SystemExit → 本文件 23 项全部连带判红，而同文件 07:0x 跑是绿的：
    # 结论随机器状态漂移，不是功能回归。
    # 本文件只验 dev_owner_gate（warn/block/路由/向后兼容），并发闸的真实覆盖在
    # tools/tests/test_devtask_worktree_mech.py（它自己 patch 计数），故此处钉成 0。
    monkeypatch.setattr(devtask, "_active_worktree_count", lambda repo=None: 0)
    return {
        "ledger": ledger,
        "git_calls": git_calls,
        "events": events,
        "gate_path": gate_path,
        "routing_path": routing_path,
        "tmp": tmp_root,
    }


def _write_gate(path, phase="warn", dept="dev"):
    """写临时门配置（与仓内 dev_owner_gate.json 同结构）。"""
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "dept": dept,
                "phase": phase,
                "acceptance_required": True,
                "note": "测试夹具",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _task(sandbox, tid):
    """读临时账本里某工单的落账 dict。"""
    return json.loads(sandbox["ledger"].read_text(encoding="utf-8"))[tid]


def _worktree_adds(sandbox):
    """git 调用记录里 worktree add 的次数（= 是否真的建了 worktree）。"""
    return [a for a in sandbox["git_calls"] if a[:2] == ("worktree", "add")]


# ----------------------------------------------------------------------
# 判据④：配置加载 fail-safe
# ----------------------------------------------------------------------
class TestLoadDevGate:
    """``_load_dev_gate`` 缺文件/坏 JSON → 默认 warn/dev（不能因此全量拒单）。"""

    def test_缺文件回默认(self, sandbox):
        assert not sandbox["gate_path"].exists()
        assert devtask._load_dev_gate() == {"phase": "warn", "dept": "dev"}

    def test_坏JSON回默认(self, sandbox):
        sandbox["gate_path"].write_text("{ not json", encoding="utf-8")
        assert devtask._load_dev_gate() == {"phase": "warn", "dept": "dev"}

    @pytest.mark.parametrize(
        "raw,phase",
        [
            ('{"dept": "dev", "phase": "BLOCK "}', "block"),  # 大小写/空白归一
            ('{"dept": "dev", "phase": "enforce"}', "warn"),  # 未知字面量 → warn
            ('{"phase": "warn"}', "warn"),  # 缺 dept → dev
            ("[]", "warn"),  # 非 dict → 默认
            ('{"dept": "  ", "phase": "block"}', "block"),  # 空白 dept → dev
        ],
    )
    def test_字面量归一与结构容错(self, sandbox, raw, phase):
        sandbox["gate_path"].write_text(raw, encoding="utf-8")
        gate = devtask._load_dev_gate()
        assert gate["phase"] == phase
        assert gate["dept"] == "dev"

    def test_仓内真实配置为warn且dept_dev(self):
        """回归护栏：仓内 dev_owner_gate.json 当前 = warn/dev/acceptance_required。

        试点期必须是 warn（只告警），任何人擅自改 block 前应先过老田批准——本用例
        让「悄悄改 phase」在测试里显形（改 block 需同步改本用例 = 一次显式决策）。
        """
        real = json.loads(
            (ROOT / "agent_platform" / "config" / "dev_owner_gate.json").read_text(
                encoding="utf-8"
            )
        )
        assert real["version"] == 1
        assert real["dept"] == "dev"
        assert real["phase"] == "warn"
        assert real["acceptance_required"] is True
        gate = devtask._load_dev_gate()
        assert gate["phase"] == "warn"
        assert gate["dept"] == "dev"


# ----------------------------------------------------------------------
# 纯函数 _dev_gate_check 判定矩阵
# ----------------------------------------------------------------------
class TestDevGateCheckPure:
    """纯函数判定矩阵：无 IO、无副作用，``gate`` 显式传入。"""

    @pytest.mark.parametrize(
        "dept,owner_ok,acceptance,phase,expect_warnings,expect_blocked",
        [
            # warn：只告警不拒收
            ("dev", True, None, "warn", ["缺验收命令"], False),
            ("dev", False, None, "warn", ["owner 不在合法域", "缺验收命令"], False),
            ("dev", False, "pytest -q", "warn", ["owner 不在合法域"], False),
            ("dev", True, "pytest -q", "warn", [], False),
            # owner 不可判定（None）≠ 非法：只按缺验收命令告警
            ("dev", None, None, "warn", ["缺验收命令"], False),
            # block：缺合法 owner / 缺验收命令即拒收
            ("dev", True, None, "block", ["缺验收命令"], True),
            ("dev", False, "pytest -q", "block", ["owner 不在合法域"], True),
            ("dev", False, None, "block", ["owner 不在合法域", "缺验收命令"], True),
            ("dev", True, "pytest -q", "block", [], False),
            # 非 dev：完全不受 gate 影响
            ("quant", False, None, "block", [], False),
            ("ops", False, None, "warn", [], False),
            # 旧式未声明 dept（None）不进门
            (None, False, None, "block", [], False),
            # 部门代号大小写/空白归一
            (" Dev ", True, None, "warn", ["缺验收命令"], False),
        ],
    )
    def test_判定矩阵(
        self, dept, owner_ok, acceptance, phase, expect_warnings, expect_blocked
    ):
        warnings, blocked = devtask._dev_gate_check(
            dept, owner_ok, acceptance, gate={"dept": "dev", "phase": phase}
        )
        assert warnings == expect_warnings
        assert blocked is expect_blocked

    def test_acceptance空白视为缺验收命令(self):
        warnings, blocked = devtask._dev_gate_check(
            "dev", True, "   ", gate={"dept": "dev", "phase": "warn"}
        )
        assert warnings == ["缺验收命令"]
        assert blocked is False

    def test_不传gate时读配置(self, sandbox):
        _write_gate(sandbox["gate_path"], phase="block")
        warnings, blocked = devtask._dev_gate_check("dev", True, None)
        assert warnings == ["缺验收命令"]
        assert blocked is True


# ----------------------------------------------------------------------
# 判据①：warn 模式只告警不拒收
# ----------------------------------------------------------------------
class TestWarnPhase:
    """warn 模式：dev 单缺验收命令/owner 非法 → 告警 + 落账，绝不拒收。"""

    def test_warn缺验收命令只告警不拒收(self, sandbox, capsys):
        _write_gate(sandbox["gate_path"], phase="warn")
        rc = devtask.cmd_new("WO-GATE-WARN", "研发试点单", "--dept", "dev")
        assert rc == 0
        t = _task(sandbox, "WO-GATE-WARN")
        assert t["status"] == "doing"
        assert t["dept"] == "dev"
        assert t["acceptance_cmd"] is None
        # 告警落 stderr
        err = capsys.readouterr().err
        assert "[warn] dev-gate" in err
        assert "缺验收命令" in err
        # 事件 detail 前缀 [dev-gate] + 告警摘要（截断后仍可检出）
        kind, tid, detail = sandbox["events"][-1]
        assert (kind, tid) == ("task_start", "WO-GATE-WARN")
        assert detail.startswith("[dev-gate]")
        assert "缺验收命令" in detail
        # 不拒收 → worktree 照建
        assert len(_worktree_adds(sandbox)) == 1

    def test_warn有验收命令时无缺验收告警(self, sandbox, capsys):
        _write_gate(sandbox["gate_path"], phase="warn")
        rc = devtask.cmd_new(
            "WO-GATE-WARN-OK",
            "完整研发单",
            "--dept",
            "dev",
            "--acceptance",
            "python -m pytest -q",
        )
        assert rc == 0
        t = _task(sandbox, "WO-GATE-WARN-OK")
        assert t["acceptance_cmd"] == "python -m pytest -q"
        err = capsys.readouterr().err
        assert "缺验收命令" not in err
        assert "owner 不在合法域" not in err
        kind, _, detail = sandbox["events"][-1]
        assert kind == "task_start"
        assert detail.startswith("[dev-gate]")  # 研发单标记保留
        assert "缺验收命令" not in detail

    def test_warn非法owner仍降级不拒收(self, sandbox, monkeypatch, capsys):
        """非法 owner 在 warn 模式沿用 WO-TF-03 降级（打 [UNOWNED]）而非拒收。"""
        monkeypatch.setenv("AGENT_NAME", "TraeCode")
        _write_gate(sandbox["gate_path"], phase="warn")
        rc = devtask.cmd_new(
            "WO-GATE-WARN-BAD",
            "非法owner研发单",
            "--dept",
            "dev",
            "--acceptance",
            "pytest -q",
        )
        assert rc == 0
        t = _task(sandbox, "WO-GATE-WARN-BAD")
        assert "[UNOWNED]" in t["desc"]
        err = capsys.readouterr().err
        assert "owner 不在合法域" in err
        detail = sandbox["events"][-1][2]
        assert detail.startswith("[dev-gate] owner 不在合法域")

    def test_warn取值域不可判定不算非法(self, sandbox, monkeypatch):
        """org.json 读不到 → owner_ok=None，试点门不得把「不可判定」当非法告警。"""
        monkeypatch.setattr(devtask, "ORG_JSON", str(sandbox["tmp"] / "nope.json"))
        _write_gate(sandbox["gate_path"], phase="warn")
        rc = devtask.cmd_new(
            "WO-GATE-NOORG",
            "域不可判定研发单",
            "--dept",
            "dev",
            "--acceptance",
            "pytest -q",
        )
        assert rc == 0
        detail = sandbox["events"][-1][2]
        assert "owner 不在合法域" not in detail
        assert "owner_domain_unavailable=1" in detail


# ----------------------------------------------------------------------
# 判据②：block 模式前置拒收（不留空 worktree）
# ----------------------------------------------------------------------
class TestBlockPhase:
    """block 模式：缺验收命令/合法 owner → SystemExit，且在建 worktree 之前。"""

    def test_block缺验收命令拒收且不留worktree(self, sandbox):
        _write_gate(sandbox["gate_path"], phase="block")
        with pytest.raises(SystemExit) as ei:
            devtask.cmd_new("WO-GATE-BLOCK", "研发试点单", "--dept", "dev")
        msg = str(ei.value)
        assert "拒收" in msg
        assert "缺验收命令" in msg
        # 关键：拒收发生在 git worktree add 之前（不留空 worktree / 不落账 / 不发事件）
        assert sandbox["git_calls"] == []
        assert not sandbox["ledger"].exists()
        assert sandbox["events"] == []

    def test_block非法owner即使有验收命令也拒收(self, sandbox, monkeypatch):
        monkeypatch.setenv("AGENT_NAME", "TraeCode")
        _write_gate(sandbox["gate_path"], phase="block")
        with pytest.raises(SystemExit) as ei:
            devtask.cmd_new(
                "WO-GATE-BLOCK2",
                "非法owner研发单",
                "--dept",
                "dev",
                "--acceptance",
                "pytest -q",
            )
        assert "owner 不在合法域" in str(ei.value)
        assert sandbox["git_calls"] == []
        assert not sandbox["ledger"].exists()

    def test_block条件齐全则放行(self, sandbox):
        _write_gate(sandbox["gate_path"], phase="block")
        rc = devtask.cmd_new(
            "WO-GATE-PASS",
            "满足试点门研发单",
            "--dept",
            "dev",
            "--acceptance",
            "python -m pytest -q",
        )
        assert rc == 0
        t = _task(sandbox, "WO-GATE-PASS")
        assert t["status"] == "doing"
        assert t["dept"] == "dev"
        assert t["acceptance_cmd"] == "python -m pytest -q"
        assert len(_worktree_adds(sandbox)) == 1
        assert sandbox["events"][-1][2].startswith("[dev-gate]")

    def test_门配置缺失时fail_safe不误拒收(self, sandbox):
        """门配置缺失 → 降回 warn（fail-safe），不能把研发单全量误拒。"""
        assert not sandbox["gate_path"].exists()
        rc = devtask.cmd_new("WO-GATE-NOCFG", "无门配置研发单", "--dept", "dev")
        assert rc == 0
        t = _task(sandbox, "WO-GATE-NOCFG")
        assert t["acceptance_cmd"] is None
        assert len(_worktree_adds(sandbox)) == 1
        assert sandbox["events"][-1][2].startswith("[dev-gate]")


# ----------------------------------------------------------------------
# 判据③：非 dev 单不受 gate 影响
# ----------------------------------------------------------------------
class TestNonDevUnaffected:
    """非 dev 单即使 block 模式也不受试点门影响（不拒收、不加 [dev-gate] 标）。"""

    def test_block模式下非dev单不拒收(self, sandbox):
        _write_gate(sandbox["gate_path"], phase="block")
        rc = devtask.cmd_new("WO-GATE-QUANT", "量化线单", "--dept", "quant")
        assert rc == 0
        t = _task(sandbox, "WO-GATE-QUANT")
        assert t["status"] == "doing"
        assert t["dept"] == "quant"  # 声明值原样落账
        assert t["acceptance_cmd"] is None
        assert len(_worktree_adds(sandbox)) == 1
        # 非研发单不进试点门：事件 detail 不加 [dev-gate] 标
        assert not sandbox["events"][-1][2].startswith("[dev-gate]")

    def test_门配置dept非dev时dev单也不进门(self, sandbox):
        """门的 dept 可配置：配成 ops 后 dev 单不命中，同样不受影响。"""
        _write_gate(sandbox["gate_path"], phase="block", dept="ops")
        rc = devtask.cmd_new("WO-GATE-OPS", "ops门外的dev单", "--dept", "dev")
        assert rc == 0
        assert _task(sandbox, "WO-GATE-OPS")["acceptance_cmd"] is None
        assert not sandbox["events"][-1][2].startswith("[dev-gate]")


# ----------------------------------------------------------------------
# 判据②b：dev 单路由规则（2026-10-27 老田批，WO-LEGION-DEVEXEC3）
# ----------------------------------------------------------------------
class TestDevRouting:
    """新研发需求优先派 ARCH1/组长：owner 非研发席 → warn 告警；block 阶段拒收。"""

    def test_warn模式owner非研发部只告警不拒收(self, sandbox, monkeypatch, capsys):
        monkeypatch.setenv("AGENT_NAME", "A0")  # 合法成员，但不在 dev_seats
        _write_gate(sandbox["gate_path"], phase="warn")
        rc = devtask.cmd_new(
            "WO-ROUTE-WARN",
            "路由规则研发单",
            "--dept",
            "dev",
            "--acceptance",
            "python -m pytest -q",
        )
        assert rc == 0
        err = capsys.readouterr().err
        assert "owner 不在研发部" in err
        assert len(_worktree_adds(sandbox)) == 1
        detail = sandbox["events"][-1][2]
        assert detail.startswith("[dev-gate]")
        assert "owner 不在研发部" in detail

    def test_block模式owner非研发部拒收且不留worktree(self, sandbox, monkeypatch):
        monkeypatch.setenv("AGENT_NAME", "A0")
        _write_gate(sandbox["gate_path"], phase="block")
        with pytest.raises(SystemExit) as ei:
            devtask.cmd_new(
                "WO-ROUTE-BLOCK",
                "路由规则研发单",
                "--dept",
                "dev",
                "--acceptance",
                "python -m pytest -q",
            )
        assert "owner 不在研发部" in str(ei.value)
        assert sandbox["git_calls"] == []
        assert not sandbox["ledger"].exists()

    def test_研发部owner不告警(self, sandbox, capsys):
        _write_gate(sandbox["gate_path"], phase="warn")
        rc = devtask.cmd_new(
            "WO-ROUTE-OK",
            "研发部owner单",
            "--dept",
            "dev",
            "--acceptance",
            "python -m pytest -q",
        )
        assert rc == 0
        assert "owner 不在研发部" not in capsys.readouterr().err

    def test_非dev单不受路由规则影响(self, sandbox, monkeypatch, capsys):
        monkeypatch.setenv("AGENT_NAME", "A0")
        _write_gate(sandbox["gate_path"], phase="block")
        rc = devtask.cmd_new(
            "WO-ROUTE-QUANT", "量化单", "--dept", "quant", "--acceptance", "pytest -q"
        )
        assert rc == 0
        assert not sandbox["events"][-1][2].startswith("[dev-gate]")

    def test_路由配置缺失时fail_safe不启用(self, sandbox, monkeypatch):
        monkeypatch.setattr(
            devtask, "DEV_ROUTING", str(sandbox["tmp"] / "nope-routing.json")
        )
        assert devtask._load_dev_routing() == {"dev_dept": "dev", "dev_seats": []}
        warnings, blocked = devtask._dev_gate_check(
            "dev", True, "pytest -q", gate={"phase": "warn", "dept": "dev"}, owner="A0"
        )
        assert warnings == [] and blocked is False

    def test_例外标记放行且不告警(self, sandbox, monkeypatch, capsys):
        """规则文档 §三：例外单须显式写 ``路由例外=`` 标记（block 阶段放行）。"""
        monkeypatch.setenv("AGENT_NAME", "A0")
        _write_gate(sandbox["gate_path"], phase="block")
        rc = devtask.cmd_new(
            "WO-ROUTE-EXEMPT",
            "跨部门治理单 路由例外=跨部门治理",
            "--dept",
            "dev",
            "--acceptance",
            "pytest -q",
        )
        assert rc == 0
        err = capsys.readouterr().err
        assert "owner 不在研发部" not in err
        assert len(_worktree_adds(sandbox)) == 1
        assert sandbox["events"][-1][2].startswith("[dev-gate]")


# ----------------------------------------------------------------------
# 向后兼容：旧调用（不带 --dept）行为完全不变
# ----------------------------------------------------------------------
class TestBackwardCompat:
    """不带 ``--dept`` 的旧调用不进门、字段集不变（既有 77 条测试同款判据）。"""

    def test_不带dept不进门_即使block模式(self, sandbox, capsys):
        _write_gate(sandbox["gate_path"], phase="block")
        rc = devtask.cmd_new("WO-GATE-OLD", "旧式单（无dept）")
        assert rc == 0
        t = _task(sandbox, "WO-GATE-OLD")
        assert set(t.keys()) == {
            "desc",
            "branch",
            "worktree",
            "status",
            "created",
            "project",
            "owner",
        }
        assert "dev-gate" not in capsys.readouterr().err
        assert not sandbox["events"][-1][2].startswith("[dev-gate]")

    def test_acceptance不带dept仍按旧式单处理(self, sandbox):
        rc = devtask.cmd_new(
            "WO-GATE-OLD2", "旧式带验收单", "--acceptance", "pytest -q"
        )
        assert rc == 0
        t = _task(sandbox, "WO-GATE-OLD2")
        assert "dept" not in t
        assert "acceptance_cmd" not in t

    def test_dept缺值抛SystemExit(self, sandbox):
        with pytest.raises(SystemExit) as ei:
            devtask.cmd_new("WO-GATE-BADDEPT", "缺值单", "--dept")
        assert "--dept" in str(ei.value)
        assert sandbox["git_calls"] == []

    def test_acceptance缺值抛SystemExit(self, sandbox):
        with pytest.raises(SystemExit) as ei:
            devtask.cmd_new("WO-GATE-BADACC", "缺值单", "--acceptance")
        assert "--acceptance" in str(ei.value)

    def test_dept与acceptance可乱序解析且不污染desc(self, sandbox):
        rc = devtask.cmd_new(
            "WO-GATE-ORDER", "顺序单", "--acceptance", "pytest -q", "--dept", "dev"
        )
        assert rc == 0
        t = _task(sandbox, "WO-GATE-ORDER")
        assert t["desc"] == "顺序单"
        assert t["dept"] == "dev"
        assert t["acceptance_cmd"] == "pytest -q"


# ----------------------------------------------------------------------
# DEVTOOL_AGENT 归因强制（WO-LEGION-P0WRAP-20261027）
# ----------------------------------------------------------------------
class TestDevtoolAgentAttribution:
    """owner 取值链 AGENT_NAME > DEVTOOL_AGENT > unknown；两者皆缺留 attribution_missing=1。"""

    def test_AGENT_NAME优先(self, sandbox, monkeypatch):
        monkeypatch.setenv("AGENT_NAME", "DEV1")
        monkeypatch.setenv("DEVTOOL_AGENT", "DEV2")
        assert devtask.cmd_new("WO-ATTR-PRI", "归因优先单") == 0
        assert _task(sandbox, "WO-ATTR-PRI")["owner"] == "DEV1"

    def test_AGENT_NAME缺省回落DEVTOOL_AGENT(self, sandbox, monkeypatch):
        monkeypatch.delenv("AGENT_NAME", raising=False)
        monkeypatch.setenv("DEVTOOL_AGENT", "DEV2")
        assert devtask.cmd_new("WO-ATTR-FALL", "归因回落单") == 0
        assert _task(sandbox, "WO-ATTR-FALL")["owner"] == "DEV2"

    def test_两者皆缺标记attribution_missing(self, sandbox, monkeypatch):
        monkeypatch.delenv("AGENT_NAME", raising=False)
        monkeypatch.delenv("DEVTOOL_AGENT", raising=False)
        assert devtask.cmd_new("WO-ATTR-MISS", "无归因单") == 0
        assert _task(sandbox, "WO-ATTR-MISS")["owner"] == "unknown-agent"
        detail = sandbox["events"][-1][2]
        assert detail.startswith("attribution_missing=1")
