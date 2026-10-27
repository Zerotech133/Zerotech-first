# -*- coding: utf-8 -*-
"""tools/dev_route_audit.py 回归测试（工单 WO-DRILL1-ROUTEAUDIT）。

**为什么必须有这份测试**：路由审计是「只读观察 → --strict 才当门禁」的治理工具。
若它被合并回退或口径被改坏，`tools/dev_route_audit.py` 语法仍正常、退出码仍 0，
但研发单的 owner 越权与缺验收会**静默漏报**——没有任何报错。本文件把审计口径
（仅审 dev 单、两条违规原因、报告/门禁退出码分离）变成可回归的断言。

覆盖工单要求的五类场景：
  ① 全合规 → ``ok=True``、``checked`` 计数正确、退出码 0；
  ② owner 不在 ``dev_seats``（大小写不敏感）→ 记「owner 不在 dev_seats」；
  ③ ``acceptance_cmd`` 缺失/空串/纯空白 → 记「缺验收命令」；
  ④ 非 dev 单不审（不进 ``checked``、不产生违规）；
  ⑤ ``tasks.json`` 缺失 → 返回 ``{}``、不崩、``checked=0``。
另补：尾逗号自愈（且不破坏字符串内逗号）、坏 JSON 不崩、``--strict`` 退出码、
``--md`` 表格、路由配置缺失时 owner 校验 fail-safe 跳过。

纪律：**只读生产数据**——真实 ``.worktrees/tasks.json`` 与
``agent_platform/config/dev_routing_rules.json`` 严禁写入；夹具全部落 pytest
``tmp_path``。用 importlib 按文件路径加载工具（``tools`` 不是 package）。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "dev_route_audit.py"

_spec = importlib.util.spec_from_file_location("dev_route_audit_uut", str(TOOL))
DRA = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(DRA)

SCHEMA = "dev_route_audit/v1"


# ----------------------------------------------------------------------
# 夹具工具（全部落 tmp_path，零生产写入）
# ----------------------------------------------------------------------
def _write_json(path, data):
    """写 JSON 夹具并返回路径。"""
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _routing(tmp_path, seats=("ARCH1", "DEV1", "DEV5")):
    """写路由配置夹具并返回路径。"""
    return _write_json(tmp_path / "dev_routing_rules.json", {"dev_seats": list(seats)})


def _tasks(tmp_path, data):
    """写工单账本夹具并返回路径。"""
    return _write_json(tmp_path / "tasks.json", data)


# ----------------------------------------------------------------------
# ① 全合规
# ----------------------------------------------------------------------
def test_全合规报告ok(tmp_path):
    """dev 单 owner 均在席、验收命令齐全 → 零违规、ok=True。"""
    tasks = _tasks(
        tmp_path,
        {
            "WO-OK-1": {
                "dept": "dev",
                "owner": "dev1",  # 大小写不同也算在席
                "acceptance_cmd": "python -X utf8 -m pytest -q",
            },
            "WO-OK-2": {
                "dept": " DEV ",  # 部门代号空白/大小写归一
                "owner": "ARCH1",
                "acceptance_cmd": "  python -m pytest  ",
            },
        },
    )
    report = DRA.run(tasks, _routing(tmp_path, seats=("arch1", "DEV1", "ARCH1")))

    assert report == {
        "schema": SCHEMA,
        "checked": 2,
        "violations": [],
        "ok": True,
    }
    # 契约字段顺序固定（机读方按序展示）
    assert list(report) == ["schema", "checked", "violations", "ok"]


# ----------------------------------------------------------------------
# ② owner 不在 dev_seats
# ----------------------------------------------------------------------
@pytest.mark.parametrize("owner", ["A0", "a0", "未知席位"])
def test_owner不在dev_seats记违规(tmp_path, owner):
    """owner 不在 dev_seats（大小写不敏感）→ 记「owner 不在 dev_seats」。"""
    tasks = _tasks(
        tmp_path,
        {
            "WO-ROUTE": {
                "dept": "dev",
                "owner": owner,
                "acceptance_cmd": "pytest -q",
            }
        },
    )
    report = DRA.run(tasks, _routing(tmp_path))

    assert report["checked"] == 1
    assert report["ok"] is False
    assert report["violations"] == [
        {"tid": "WO-ROUTE", "owner": owner, "reasons": ["owner 不在 dev_seats"]}
    ]


# ----------------------------------------------------------------------
# ③ 缺验收命令
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "task_extra",
    [
        {},  # 字段缺失
        {"acceptance_cmd": None},  # 显式 null
        {"acceptance_cmd": ""},  # 空串
        {"acceptance_cmd": "   "},  # 纯空白
    ],
)
def test_缺验收命令记违规(tmp_path, task_extra):
    """acceptance_cmd 缺失/空串/纯空白/nulls → 记「缺验收命令」。"""
    task = {"dept": "dev", "owner": "DEV1"}
    task.update(task_extra)
    tasks = _tasks(tmp_path, {"WO-NOACC": task})
    report = DRA.run(tasks, _routing(tmp_path))

    assert report["checked"] == 1
    assert report["ok"] is False
    assert report["violations"] == [
        {"tid": "WO-NOACC", "owner": "DEV1", "reasons": ["缺验收命令"]}
    ]


def test_两条违规同时命中(tmp_path):
    """owner 越权 + 缺验收 → 两条原因同时给出（不互相掩盖）。"""
    tasks = _tasks(
        tmp_path,
        {"WO-BOTH": {"dept": "dev", "owner": "A0", "acceptance_cmd": "  "}},
    )
    report = DRA.run(tasks, _routing(tmp_path))

    assert report["violations"] == [
        {
            "tid": "WO-BOTH",
            "owner": "A0",
            "reasons": ["owner 不在 dev_seats", "缺验收命令"],
        }
    ]


# ----------------------------------------------------------------------
# ④ 非 dev 单不审
# ----------------------------------------------------------------------
def test_非dev单不审(tmp_path):
    """quant/ops/无 dept/devex 等非 dev 单即使 owner 非法、缺验收也不进报告。"""
    tasks = _tasks(
        tmp_path,
        {
            "WO-QUANT": {"dept": "quant", "owner": "A0"},
            "WO-OPS": {"dept": "ops", "owner": "A0"},
            "WO-NODEPT": {"owner": "A0"},
            "WO-DEVEX": {"dept": "devex", "owner": "A0"},
            "WO-DEV-OK": {
                "dept": "dev",
                "owner": "DEV1",
                "acceptance_cmd": "pytest -q",
            },
        },
    )
    report = DRA.run(tasks, _routing(tmp_path))

    assert report["checked"] == 1  # 只有 WO-DEV-OK 被审
    assert report["violations"] == []
    assert report["ok"] is True


# ----------------------------------------------------------------------
# ⑤ tasks 文件缺失不崩
# ----------------------------------------------------------------------
def test_缺tasks文件不崩(tmp_path):
    """tasks.json 缺失 → load_tasks 返回 {}，报告 checked=0/ok=True，CLI rc=0。"""
    missing = tmp_path / "nope-tasks.json"
    routing = _routing(tmp_path)

    assert DRA.load_tasks(missing) == {}
    report = DRA.run(missing, routing)
    assert report == {
        "schema": SCHEMA,
        "checked": 0,
        "violations": [],
        "ok": True,
    }
    assert DRA.main(["--tasks", str(missing), "--routing", str(routing), "--json"]) == 0


# ----------------------------------------------------------------------
# 尾逗号自愈 / 坏 JSON fail-safe
# ----------------------------------------------------------------------
def test_尾逗号自愈且不破坏字符串内逗号(tmp_path):
    """对象/数组尾逗号可解析；字符串里的「, }」不得被误删。"""
    tasks = tmp_path / "tasks.json"
    tasks.write_text(
        '{\n'
        '  "WO-TC": {"dept": "dev", "owner": "DEV1",\n'
        '            "acceptance_cmd": "pytest -q", "desc": "x, } y",},\n'
        '  "WO-LIST": {"dept": "dev", "owner": "DEV1", "acceptance_cmd": "pytest -q",\n'
        '              "tags": ["a", "b",],},\n'
        '}',
        encoding="utf-8",
    )
    loaded = DRA.load_tasks(tasks)
    assert set(loaded) == {"WO-TC", "WO-LIST"}
    assert loaded["WO-TC"]["desc"] == "x, } y"  # 字符串内容原样保留
    assert loaded["WO-LIST"]["tags"] == ["a", "b"]

    report = DRA.run(tasks, _routing(tmp_path))
    assert report["checked"] == 2
    assert report["ok"] is True


def test_坏JSON不崩返回空(tmp_path, capsys):
    """非尾逗号的坏 JSON → 返回 {}（告警落 stderr），不抛异常。"""
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")

    assert DRA.load_tasks(bad) == {}
    assert "JSON 解析失败" in capsys.readouterr().err


# ----------------------------------------------------------------------
# 路由配置缺失：owner 校验 fail-safe 跳过，验收照查
# ----------------------------------------------------------------------
def test_路由配置缺失时跳过owner校验(tmp_path):
    """dev_seats 不可判定 → 不误报 owner；缺验收命令仍照常记违规。"""
    tasks = _tasks(
        tmp_path,
        {
            "WO-OWNER-UNKNOWN": {
                "dept": "dev",
                "owner": "A0",
                "acceptance_cmd": "pytest -q",
            },
            "WO-NOACC": {"dept": "dev", "owner": "DEV1"},
        },
    )
    report = DRA.run(tasks, tmp_path / "no-routing.json")

    assert report["checked"] == 2
    assert report["violations"] == [
        {"tid": "WO-NOACC", "owner": "DEV1", "reasons": ["缺验收命令"]}
    ]


# ----------------------------------------------------------------------
# 退出码：默认报告模式 0（即使有违规）；--strict 有违规 1
# ----------------------------------------------------------------------
def _violating_fixture(tmp_path):
    """造一张确定性违规单（owner A0 + 缺验收）与路由配置，返回 CLI 参数。"""
    tasks = _tasks(tmp_path, {"WO-BAD": {"dept": "dev", "owner": "A0"}})
    routing = _routing(tmp_path)
    return ["--tasks", str(tasks), "--routing", str(routing), "--json"]


def test_默认报告模式有违规仍返回0(tmp_path, capsys):
    """报告模式：审计成功即 rc=0，违规只体现在报告里（不拦人）。"""
    assert DRA.main(_violating_fixture(tmp_path)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert len(payload["violations"]) == 1


def test_strict有违规返回1(tmp_path):
    """--strict：有违规 → rc=1（供 CI/巡检当门禁）。"""
    args = _violating_fixture(tmp_path)
    assert DRA.main(args) == 0
    assert DRA.main(args + ["--strict"]) == 1


def test_strict无违规返回0(tmp_path):
    """--strict：无违规 → rc=0。"""
    tasks = _tasks(
        tmp_path,
        {"WO-OK": {"dept": "dev", "owner": "DEV1", "acceptance_cmd": "pytest -q"}},
    )
    routing = _routing(tmp_path)
    assert DRA.main(
        ["--tasks", str(tasks), "--routing", str(routing), "--json", "--strict"]
    ) == 0


# ----------------------------------------------------------------------
# Markdown 报告
# ----------------------------------------------------------------------
def test_md输出违规表格(tmp_path, capsys):
    """--md 输出 Markdown 表，含表头、工单、owner 与两条违规原因。"""
    args = _violating_fixture(tmp_path)[:-1]  # 去掉 --json，换 --md
    assert DRA.main(args + ["--md"]) == 0
    out = capsys.readouterr().out

    assert "| 工单 | owner | 违规原因 |" in out
    assert "| WO-BAD | A0 | owner 不在 dev_seats；缺验收命令 |" in out
    assert "结论：FAIL" in out
    assert "审计工单：1（仅 dept=dev）" in out


def test_md无违规显示无违规(tmp_path, capsys):
    """--md 无违规时仍给表格，并以「无违规」行明示。"""
    tasks = _tasks(
        tmp_path,
        {"WO-OK": {"dept": "dev", "owner": "DEV1", "acceptance_cmd": "pytest -q"}},
    )
    routing = _routing(tmp_path)
    assert DRA.main(["--tasks", str(tasks), "--routing", str(routing), "--md"]) == 0
    out = capsys.readouterr().out

    assert "结论：OK" in out
    assert "| （无） | - | 无违规 |" in out
