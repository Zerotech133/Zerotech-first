# -*- coding: utf-8 -*-
"""WO-TF-03 owner 落账校验回归测试（零生产写入）。

**为什么必须有这份测试**：WO-TF-03 的校验逻辑此前**零测试覆盖**。若它被
并行 worktree 合并回退，`tools/devtask.py` 语法仍正常、既有测试仍全绿、
`cmd_new` 仍返回 0，而 owner 又会继续落 ``unknown-agent`` —— 校验静默消失，
且**没有任何报错**。这正是工单落账侧根因原地复发且更难发现的形态。
本文件把「校验生效」这件事变成可回归的断言。

覆盖五条：
  1. 合法 owner 零变化（不打标签、无降级留痕、字段与现状一致）
  2. 非法 owner 降级（建单不拒写 + 打 ``[UNOWNED]`` + 留痕 ``owner_degraded=1``），
     且**只打一次**（desc 预含标签时不得重复追加）
  3. 取值域动态生效（夹具 org.json 加成员/部长 → 立即合法，零代码改动）
  4. ``external_tools`` 仅 ``status == "legal"`` 才算合法（锁死静默失效点）
  5. 取值域不可判定时不误标（org.json 缺失 / 坏 JSON / 无 departments /
     departments 为空 / 部门层零成员 → ``_owner_check`` 返回 ``None`` 三态，
     不判非法、不打 ``[UNOWNED]``，仅留痕 ``owner_domain_unavailable=1``）

纪律：
  * **只读生产数据** —— 真实 ``.worktrees/tasks.json`` 与真实
    ``agent_platform/registry/org.json`` 严禁写入。所有夹具落系统临时目录，
    用 monkeypatch 把模块级路径常量指过去。
  * 不执行会写生产账本的命令（不直跑 ``devtask.py new``）。
  * 本文件**不改实现**：发现实现缺陷写成失败用例并上报，不动
    ``tools/devtask.py`` 与 ``agent_platform/registry/owner_domain.json``。
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

import devtask  # noqa: E402  —— 需先补 sys.path 再导入


# ----------------------------------------------------------------------
# 夹具
# ----------------------------------------------------------------------
@pytest.fixture
def tmp_root():
    """系统临时目录夹具根 —— 所有写盘只落这里。"""
    d = tempfile.mkdtemp(prefix="wotf03_test_")
    try:
        yield Path(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _expect_domain_size(org_path):
    """独立口径：自己按「部门 agents ∪ 部门 lead ∪ chief」数成员，不借用实现。

    为什么不能写死 55：这张表是活的——2026-10-22 把 A0/A0A 正式并入部门档案表后
    规模立刻变 60，四个用例整批红，而红的原因是「组织变好了」。
    """
    org = json.loads(Path(org_path).read_text(encoding="utf-8"))
    s = set()
    for d in org.get("departments") or []:
        for a in d.get("agents") or []:
            if isinstance(a, str) and a.strip():
                s.add(a.strip().upper())
        lead = d.get("lead")
        if isinstance(lead, str) and lead.strip():
            s.add(lead.strip().upper())
    chief = (org.get("chief") or {}).get("agent_id")
    if isinstance(chief, str) and chief.strip():
        s.add(chief.strip().upper())
    return len(s)


@pytest.fixture
def org_fixture(tmp_root, monkeypatch):
    """把 devtask 的 org.json 路径常量指向临时夹具副本。

    **monkeypatch 替换模块级路径常量**（``devtask.ORG_JSON``），不写真实
    ``agent_platform/registry/org.json``。
    """
    real = json.loads(
        (ROOT / "agent_platform" / "registry" / "org.json").read_text(encoding="utf-8")
    )
    fx = tmp_root / "org.json"
    fx.write_text(json.dumps(real, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(devtask, "ORG_JSON", str(fx))
    return fx


@pytest.fixture
def domain_fixture(tmp_root, monkeypatch):
    """把 devtask 的 owner_domain.json 路径常量指向临时夹具副本。"""
    fx = tmp_root / "owner_domain.json"
    fx.write_text(
        json.dumps(
            {
                "version": 1,
                "note": "测试夹具",
                "external_tools": {
                    "status": "pending_ruling",
                    "observed": ["unknown-agent", "Codex", "Trae"],
                },
                "unowned_tag": "[UNOWNED]",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(devtask, "OWNER_DOMAIN", str(fx))
    return fx


@pytest.fixture
def ledger(tmp_root, monkeypatch):
    """把 devtask 的账本路径常量指向临时夹具，并桩掉 git/worktree 与 checkpoint。

    ``cmd_new`` 内会 ``git worktree add``（写仓库）与挂 checkpoint（写db），
    二者都与 owner 校验无关，桩掉以保证**零生产写入**。
    """
    wt = tmp_root / ".worktrees"
    wt.mkdir(parents=True, exist_ok=True)
    tasks = wt / "tasks.json"
    monkeypatch.setattr(devtask, "WT_DIR", str(wt))
    monkeypatch.setattr(devtask, "TASKS", str(tasks))
    monkeypatch.setattr(devtask, "_git", lambda *a, **k: "")
    monkeypatch.setattr(devtask, "_ckpt", lambda *a, **k: None)
    return tasks


@pytest.fixture
def events(monkeypatch):
    """捕获 ``_emit`` 事件（kind, task, detail），用于断言留痕。"""
    captured = []
    monkeypatch.setattr(
        devtask,
        "_emit",
        lambda kind, task, detail="": captured.append((kind, task, detail)),
    )
    return captured


@pytest.fixture
def agent_name(monkeypatch):
    """设置/清除 AGENT_NAME，用完自动还原。"""

    def _set(value):
        if value is None:
            monkeypatch.delenv("AGENT_NAME", raising=False)
        else:
            monkeypatch.setenv("AGENT_NAME", value)

    return _set


def _new(agent_name, ledger, events, tid, desc, owner):
    """跑一次 cmd_new，返回落账后该工单的 dict 与该次事件的 detail。"""
    agent_name(owner)
    devtask.cmd_new(tid, desc)
    data = json.loads(ledger.read_text(encoding="utf-8"))
    detail = events[-1][2] if events else ""
    return data[tid], detail


# ----------------------------------------------------------------------
# 判据 1：合法 owner 零变化
# ----------------------------------------------------------------------
class TestLegalOwnerUnchanged:
    """合法 owner 的行为必须与落账校验引入前**完全一致**。"""

    @pytest.mark.parametrize("owner", ["DEV1", "QA1", "ARCH1", "A0"])
    def test_合法成员不打标签且无降级留痕(
        self, org_fixture, domain_fixture, ledger, events, agent_name, owner
    ):
        task, detail = _new(agent_name, ledger, events, "K-LEGAL", "合法归属单", owner)
        assert "[UNOWNED]" not in task["desc"]
        assert "owner_degraded" not in detail
        assert task["owner"] == owner

    def test_合法路径字段与现状一致(
        self, org_fixture, domain_fixture, ledger, events, agent_name
    ):
        """字段集与 WO-TF-03 之前完全相同（不多不少）。"""
        task, _ = _new(agent_name, ledger, events, "K-FIELDS", "字段核对单", "DEV1")
        assert set(task.keys()) == {
            "desc",
            "branch",
            "worktree",
            "status",
            "created",
            "project",
            "owner",
        }

    def test_合法路径仍发正常task_start事件(
        self, org_fixture, domain_fixture, ledger, events, agent_name
    ):
        _new(agent_name, ledger, events, "K-EVT", "事件核对单", "DEV1")
        assert len(events) == 1
        kind, task_id, detail = events[0]
        assert kind == "task_start"
        assert task_id == "K-EVT"
        assert "owner_degraded" not in detail


# ----------------------------------------------------------------------
# 判据 2：非法 owner 降级
# ----------------------------------------------------------------------
class TestIllegalOwnerDegraded:
    """非法 owner → 降级打标签 + 留痕，**且不拒写**。"""

    @pytest.mark.parametrize(
        "owner",
        [
            "Codex·主控",
            "TraeCode",
            "unknown-agent",
            "trae-main",
            "ST1 席位（缠论分析师）",
        ],
    )
    def test_非法owner建单成功且打标签(
        self, org_fixture, domain_fixture, ledger, events, agent_name, owner
    ):
        task, detail = _new(agent_name, ledger, events, "K-BAD", "非法归属单", owner)
        # 不拒写：工单确实落账了
        assert task["status"] == "doing"
        assert "[UNOWNED]" in task["desc"]
        # 留痕
        assert "owner_degraded=1" in detail
        # owner 原值保留，不被静默改写
        assert task["owner"] == owner

    def test_空owner判非法并降级(
        self, org_fixture, domain_fixture, ledger, events, agent_name
    ):
        """owner 为空（AGENT_NAME 未设）同样判非法。"""
        task, detail = _new(agent_name, ledger, events, "K-EMPTY", "空owner单", None)
        assert "[UNOWNED]" in task["desc"]
        assert "owner_degraded=1" in detail
        assert task["owner"] == "unknown-agent"

    def test_降级只打一次_预含标签不重复追加(
        self, org_fixture, domain_fixture, ledger, events, agent_name
    ):
        """desc 已含 [UNOWNED] 时不得重复追加。"""
        task, detail = _new(
            agent_name, ledger, events, "K-DUP", "预标注单 [UNOWNED]", "TraeCode"
        )
        assert task["desc"].count("[UNOWNED]") == 1
        # 仍属降级路径 → 仍须留痕
        assert "owner_degraded=1" in detail

    def test_降级标签追加在desc末尾且可读(
        self, org_fixture, domain_fixture, ledger, events, agent_name
    ):
        """降级标签以空格前置追加，原描述不被破坏。"""
        task, _ = _new(agent_name, ledger, events, "K-READ", "原始描述文本", "TraeCode")
        assert task["desc"] == "原始描述文本 [UNOWNED]"

    def test_降级留痕含owner与域规模(
        self, org_fixture, domain_fixture, ledger, events, agent_name
    ):
        """留痕须带owner 原值与合法域规模，便于事后核对。"""
        _, detail = _new(agent_name, ledger, events, "K-DT", "留痕核对单", "TraeCode")
        assert "owner_degraded=1" in detail
        assert "owner=TraeCode" in detail
        assert "domain_size=" in detail


# ----------------------------------------------------------------------
# 判据 3：取值域动态生效（不改代码）
# ----------------------------------------------------------------------
class TestDomainDynamic:
    """成员池必须**动态派生** —— org.json 加人即刻生效、零代码改动。"""

    def test_基线域规模(self, org_fixture):
        expect = _expect_domain_size(org_fixture)
        assert len(devtask._owner_domain_members()) == expect

    def test_新增成员与部长立即合法_零代码改动(
        self, org_fixture, monkeypatch, tmp_root
    ):
        """夹具 org.json 加一个部门（1 成员 + 1 部长）→ 两者立即合法。"""
        org = json.loads(org_fixture.read_text(encoding="utf-8"))
        org["departments"].append(
            {"dept": "probe_dept", "lead": "PROBELEAD1", "agents": ["PROBEAGENT1"]}
        )
        org_fixture.write_text(json.dumps(org, ensure_ascii=False), encoding="utf-8")
        assert len(devtask._owner_domain_members()) == _expect_domain_size(org_fixture)
        assert devtask._owner_check("PROBEAGENT1")[0] is True
        assert devtask._owner_check("PROBELEAD1")[0] is True

    def test_未注册ID仍判非法(self, org_fixture):
        assert devtask._owner_check("NOT_REGISTERED_XYZ")[0] is False

    def test_恢复后域规模回到基线(self, org_fixture, tmp_root, monkeypatch):
        """指回真实 org.json 后域规模回到基线 55（夹具未污染真实数据）。"""
        real = str(ROOT / "agent_platform" / "registry" / "org.json")
        monkeypatch.setattr(devtask, "ORG_JSON", real)
        assert len(devtask._owner_domain_members()) == _expect_domain_size(real)

    def test_真实org域规模与成员合法性回归护栏(self, tmp_root, monkeypatch):
        """**回归护栏**：真实 org.json 下域规模仍 55、真实成员 DEV1 仍合法。

        防的是「为修边界而误伤正常路径」——判据 5 的三态化与「部门层零成员」
        守卫都改过 ``_owner_domain_members`` 的返回语义（``set`` → ``None``），
        若改动过头，正常建单会**全量被判非法 → 全量误打 ``[UNOWNED]``**。

        本用例直接指向**真实** org.json（只读，不写），故能锁住真实组织图的
        权威取值：规模 55、成员 DEV1 合法、域可判定（非 None）。

        注：真实 org.json 里 chief=``A0`` **同时**是 ``chief_line`` 部门的
        lead，故 ``A0`` 在域内**不能**用来证明 ``chief.agent_id`` 那条分支
        生效（去掉它 A0 仍在）。故此处只断言规模与成员合法性，chief 分支的
        有效性由 ``TestDomainUnavailable`` 的零成员场景覆盖。
        """
        monkeypatch.setattr(
            devtask, "ORG_JSON", str(ROOT / "agent_platform" / "registry" / "org.json")
        )
        members = devtask._owner_domain_members()
        assert members is not None, "真实 org.json 必须可判定（不得返回 None）"
        expect = _expect_domain_size(
            str(ROOT / "agent_platform" / "registry" / "org.json")
        )
        assert len(members) == expect
        # D5-C：合法域规模 = org 成员数 + profile 席位（治理席不占编但占合法域）
        profile = devtask._profile_seat_members()
        owner_ok, _domain, size = devtask._owner_check("DEV1")
        assert owner_ok is True
        assert size == expect + len(profile)


# ----------------------------------------------------------------------
# 判据 4：external_tools 合法值判定（锁死静默失效点）
# ----------------------------------------------------------------------
class TestExternalToolsStatus:
    """只有 ``status == "legal"`` 时外部工具代号才算合法。

    这条专门锁死一个**静默失效点**：工单未明文规定「批准后该写什么字面量」，
    而代码只认 ``legal``。若有人把状态写成 ``approved`` 之类，代码不认、
    **不报错**，裁定永远不生效。
    """

    def _domain(self, tmp_root, monkeypatch, status):
        fx = tmp_root / ("owner_domain_%s.json" % status)
        fx.write_text(
            json.dumps(
                {
                    "version": 1,
                    "external_tools": {
                        "status": status,
                        "observed": ["Codex·主控", "TraeCode"],
                    },
                    "unowned_tag": "[UNOWNED]",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(devtask, "OWNER_DOMAIN", str(fx))
        return fx

    def test_status_legal时代理代号才算合法(self, tmp_root, monkeypatch, org_fixture):
        self._domain(tmp_root, monkeypatch, "legal")
        assert devtask._owner_check("Codex·主控")[0] is True
        assert devtask._owner_check("TraeCode")[0] is True

    def test_status_pending_ruling时判非法(self, tmp_root, monkeypatch, org_fixture):
        self._domain(tmp_root, monkeypatch, "pending_ruling")
        assert devtask._owner_check("Codex·主控")[0] is False
        assert devtask._owner_check("TraeCode")[0] is False

    @pytest.mark.parametrize(
        "status",
        [
            "approved",
            "legal_pending",
            "ok",
            "yes",
            "",
            "granted",
            "allow",
            "accept",
            "true",
            "enabled",
        ],
    )
    def test_其他字面量一律判非法(self, tmp_root, monkeypatch, org_fixture, status):
        """除 ``legal`` 外的任何字面量都**不得**被当作已批准。

        这条锁死的正是静默失效点：工单未明文规定「批准后该写什么字面量」，
        若有人写成 ``approved`` 之类，代码不认、**也不报错**，裁定永远不生效。
        """
        self._domain(tmp_root, monkeypatch, status)
        assert devtask._owner_check("Codex·主控")[0] is False
        assert devtask._owner_check("TraeCode")[0] is False

    @pytest.mark.parametrize("status", ["legal", "LEGAL", "Legal", " legal "])
    def test_legal的大小写与空白归一化后仍算合法(
        self, tmp_root, monkeypatch, org_fixture, status
    ):
        """实现按 ``.strip().lower() == "legal"`` 判定，故大小写/空白变体
        同样算已批准 —— 这是刻意的容错，不是漏判。

        与上面「其他字面量一律判非法」互为对照：只有 ``legal`` 的
        大小写/空白变体被接受，**任何语义不同的字面量都被拒**。
        """
        self._domain(tmp_root, monkeypatch, status)
        assert devtask._owner_check("Codex·主控")[0] is True
        assert devtask._owner_check("TraeCode")[0] is True

    def test_真实登记表当前为pending_ruling(self, org_fixture):
        """仓内真实登记表当前是 pending_ruling —— 外部代号一律不合法。"""
        real = json.loads(
            (ROOT / "agent_platform" / "registry" / "owner_domain.json").read_text(
                encoding="utf-8"
            )
        )
        assert real["external_tools"]["status"] == "pending_ruling"
        assert devtask._owner_check("Codex·主控")[0] is False


# ----------------------------------------------------------------------
# 判据 5：取值域不可判定时不误标
# ----------------------------------------------------------------------
class TestDomainUnavailable:
    """org.json 缺失/坏 JSON/无 departments → **不拒写、不打 [UNOWNED]**，
    仅留痕 ``owner_domain_unavailable=1``。

    「不可判定」≠「判定为非法」：取不到权威域时若一律判非法，真实成员会被
    误标 ``[UNOWNED]``，反而污染 desc 并制造大量假降级痕。

    实现以 ``_owner_check`` 的**三态**表达（ws-fix3 收尾改动已合入）：
      * ``True``  —— 域可判定且 owner 在域内 → 正常放行
      * ``False`` —— 域可判定且 owner ∉ 域 → 非法，降级打标签
      * ``None``  —— **域不可判定** → 不判非法、不打标签，仅留痕
    """

    def test_org文件缺失时不误标(
        self, tmp_root, monkeypatch, ledger, events, agent_name
    ):
        monkeypatch.setattr(devtask, "ORG_JSON", str(tmp_root / "nope.json"))
        task, detail = _new(
            agent_name, ledger, events, "K-NOORG", "域不可判定单", "DEV1"
        )
        assert task["status"] == "doing"  # 不拒写
        assert "[UNOWNED]" not in task["desc"]  # 不误标
        assert "owner_domain_unavailable=1" in detail
        assert "owner_degraded=1" not in detail  # 不是非法降级

    def test_org坏JSON时不误标(self, tmp_root, monkeypatch, ledger, events, agent_name):
        bad = tmp_root / "bad.json"
        bad.write_text("{ not json", encoding="utf-8")
        monkeypatch.setattr(devtask, "ORG_JSON", str(bad))
        task, detail = _new(
            agent_name, ledger, events, "K-BADORG", "域坏JSON单", "DEV1"
        )
        assert "[UNOWNED]" not in task["desc"]
        assert "owner_domain_unavailable=1" in detail

    def test_org无departments时不误标(
        self, tmp_root, monkeypatch, ledger, events, agent_name
    ):
        nodep = tmp_root / "nodep.json"
        nodep.write_text(json.dumps({"chief": {"agent_id": "A0"}}), encoding="utf-8")
        monkeypatch.setattr(devtask, "ORG_JSON", str(nodep))
        task, detail = _new(
            agent_name, ledger, events, "K-NODEP", "域无departments单", "DEV1"
        )
        assert "[UNOWNED]" not in task["desc"]
        assert "owner_domain_unavailable=1" in detail

    @pytest.mark.parametrize(
        "scenario",
        [
            "missing",
            "bad_json",
            "no_departments",
            "empty_departments",
            "dept_no_members",
            "dept_no_members_no_chief",
        ],
    )
    def test_不可判定时owner_check返回None三态(
        self, tmp_root, monkeypatch, org_fixture, scenario
    ):
        """``_owner_check`` 必须以 ``None`` 表达「不可判定」，
        而不是 False（False 会被调用方当成非法 → 误标）。

        覆盖五类不可判定形态：文件缺失 / 坏 JSON / 缺 ``departments`` 键 /
        ``departments`` 为空数组 / 部门层零成员（含、无 chief 两种）。

        **关键不变量**：``chief.agent_id`` 是 org.json 里的常量，不部门是否有人
        都恒在。故「池里只剩 chief」**恒等价于部门层一个人都没派出来** ——
        与「缺 ``departments`` 键」是同一观测特征，必须同判不可判定。
        """
        if scenario == "missing":
            monkeypatch.setattr(devtask, "ORG_JSON", str(tmp_root / "nope.json"))
        elif scenario == "bad_json":
            bad = tmp_root / "bad.json"
            bad.write_text("{ not json", encoding="utf-8")
            monkeypatch.setattr(devtask, "ORG_JSON", str(bad))
        elif scenario == "no_departments":
            nodep = tmp_root / "nodep.json"
            nodep.write_text(
                json.dumps({"chief": {"agent_id": "A0"}}), encoding="utf-8"
            )
            monkeypatch.setattr(devtask, "ORG_JSON", str(nodep))
        elif scenario == "empty_departments":
            # 最简形态：departments=[] + chief 常量在 —— 改前会落 {'A0'}
            # 并被判非法 → 全量误标；现应判不可判定。
            empty = tmp_root / "empty_deps.json"
            empty.write_text(
                json.dumps({"chief": {"agent_id": "A0"}, "departments": []}),
                encoding="utf-8",
            )
            monkeypatch.setattr(devtask, "ORG_JSON", str(empty))
        elif scenario == "dept_no_members":
            # 部门存在但 agents/lead 全空 + chief 在 —— 与上一条同观测特征
            nomem = tmp_root / "no_members.json"
            nomem.write_text(
                json.dumps(
                    {
                        "chief": {"agent_id": "A0"},
                        "departments": [{"dept": "d1", "lead": "", "agents": []}],
                    }
                ),
                encoding="utf-8",
            )
            monkeypatch.setattr(devtask, "ORG_JSON", str(nomem))
        else:
            # 部门层零成员 + 连 chief 都没有
            nomem2 = tmp_root / "no_members_no_chief.json"
            nomem2.write_text(
                json.dumps({"departments": [{"dept": "d1", "lead": "", "agents": []}]}),
                encoding="utf-8",
            )
            monkeypatch.setattr(devtask, "ORG_JSON", str(nomem2))
        owner_ok, _domain, size = devtask._owner_check("DEV1")
        assert owner_ok is None
        assert size == 0

    @pytest.mark.parametrize("scenario", ["empty_departments", "dept_no_members"])
    def test_部门层零成员时不误标(
        self, tmp_root, monkeypatch, ledger, events, agent_name, scenario
    ):
        """部门层零成员（``departments=[]`` 或各部 agents/lead 全空）→
        **不判非法、不打标签**，仅留痕 ``owner_domain_unavailable=1``。

        此前该形态会因「池里只剩 chief 常量」被判非法 → **全量新建单被批量误打**
        ``[UNOWNED]``。它与「缺 departments 键」是同一观测特征，须同判不可判定。
        """
        if scenario == "empty_departments":
            empty = tmp_root / "empty_deps.json"
            empty.write_text(
                json.dumps({"chief": {"agent_id": "A0"}, "departments": []}),
                encoding="utf-8",
            )
            monkeypatch.setattr(devtask, "ORG_JSON", str(empty))
        else:
            nomem = tmp_root / "no_members.json"
            nomem.write_text(
                json.dumps(
                    {
                        "chief": {"agent_id": "A0"},
                        "departments": [{"dept": "d1", "lead": "", "agents": []}],
                    }
                ),
                encoding="utf-8",
            )
            monkeypatch.setattr(devtask, "ORG_JSON", str(nomem))
        task, detail = _new(
            agent_name, ledger, events, "K-EMPTYDEPT", "部门层零成员单", "DEV1"
        )
        assert task["status"] == "doing"  # 不拒写
        assert "[UNOWNED]" not in task["desc"]  # 不误标
        assert "owner_domain_unavailable=1" in detail
        assert "owner_degraded=1" not in detail  # 不是非法降级

    def test_域不可判定时建单仍不抛异常(
        self, tmp_root, monkeypatch, ledger, events, agent_name
    ):
        """**建单本身绝不能因取值域问题崩掉**（老田裁定①：不拒写）。"""
        monkeypatch.setattr(devtask, "ORG_JSON", str(tmp_root / "nope.json"))
        task, _ = _new(agent_name, ledger, events, "K-NOCRASH", "不崩单", "DEV1")
        assert task["status"] == "doing"
        assert task["owner"] == "DEV1"

    def test_域不可判定时owner原样落账不被改写(
        self, tmp_root, monkeypatch, ledger, events, agent_name
    ):
        """不可判定时 owner 仍按原值落账 —— 不得因无法判定而清空或改写。"""
        monkeypatch.setattr(devtask, "ORG_JSON", str(tmp_root / "nope.json"))
        task, _ = _new(agent_name, ledger, events, "K-KEEP", "保留owner单", "TraeCode")
        assert task["owner"] == "TraeCode"


# ----------------------------------------------------------------------
# D5-C：profile 席位（治理席不占 org.json 编制，但必须是合法 owner）
# ----------------------------------------------------------------------
class TestProfileSeats:
    """``owner_domain.json.profile_seats`` 的合法性与边界。

    背景（2026-10-26 老田拍板 D5-C）：治理席以 profile 角色存在、不占 org.json
    编制；若不走 profile_seats，治理席每张工单都会被判非法并打 ``[UNOWNED]``。
    """

    def _domain(self, tmp_root, monkeypatch, seats):
        fx = tmp_root / "owner_domain_profile.json"
        fx.write_text(
            json.dumps(
                {
                    "version": 1,
                    "note": "D5-C 测试夹具",
                    "external_tools": {"status": "pending_ruling", "observed": []},
                    "profile_seats": {"seats": seats},
                    "unowned_tag": "[UNOWNED]",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(devtask, "OWNER_DOMAIN", str(fx))
        return fx

    def test_profile席位合法且不影响org口径(self, org_fixture, tmp_root, monkeypatch):
        self._domain(tmp_root, monkeypatch, ["ZHAOYUN"])
        assert devtask._owner_check("ZHAOYUN")[0] is True
        assert devtask._owner_check("R2")[0] is True  # org 风控席仍合法
        assert devtask._owner_check("NOT_A_SEAT")[0] is False
        # 纯 org 成员池口径不被污染（仍是 org 派生结果）
        assert "ZHAOYUN" not in (devtask._owner_domain_members() or set())

    def test_profile席位未登记时判非法(self, org_fixture, tmp_root, monkeypatch):
        self._domain(tmp_root, monkeypatch, [])
        assert devtask._owner_check("ZHAOYUN")[0] is False

    def test_profile席位大小写与空白归一(self, org_fixture, tmp_root, monkeypatch):
        self._domain(tmp_root, monkeypatch, [" zhaoyun "])
        assert devtask._owner_check("ZHAOYUN")[0] is True
        assert devtask._owner_check("zhaoyun")[0] is True

    def test_org不可判定时profile席位仍可判定合法(self, tmp_root, monkeypatch):
        """org 读不到（不可判定）时，本地 profile 席位登记仍是权威：命中即合法。"""
        monkeypatch.setattr(devtask, "ORG_JSON", str(tmp_root / "nope.json"))
        self._domain(tmp_root, monkeypatch, ["ZHAOYUN"])
        owner_ok, _domain, size = devtask._owner_check("ZHAOYUN")
        assert owner_ok is True
        assert size == 1
        # 非 profile 席位仍保持"不可判定"三态（不误判非法）
        assert devtask._owner_check("DEV1")[0] is None

    def test_真实登记表治理席已合法(self):
        """回归护栏：真实 owner_domain.json 已登记 ZHAOYUN 且判定合法（只读）。"""
        real_domain = json.loads(
            (ROOT / "agent_platform" / "registry" / "owner_domain.json").read_text(
                encoding="utf-8"
            )
        )
        seats = (real_domain.get("profile_seats") or {}).get("seats") or []
        assert "ZHAOYUN" in [str(s).strip().upper() for s in seats]
        assert devtask._owner_check("ZHAOYUN")[0] is True
