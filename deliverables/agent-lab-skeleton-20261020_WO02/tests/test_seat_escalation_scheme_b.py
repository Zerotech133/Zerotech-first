"""WO-LEG-20261020-02 判据测试：方案B 白名单扩员 + 监督席永不直发本人。

四条判据（老田 2026-10-20 拍板方案B）：
①  越级直发被拦：监督席（zhuge_liang / zhao_yun）直发 principal 必被总线拦入死信；
②  归口席阻断类放行：tian_feng 仅阻断类可 level=principal 直发本人；
③  非阻断类改道：tian_feng 非阻断类升级/请示改走 command 层，不直发本人；
④  两类消息可区分可审计：阻断类直发带 remediation_ticket / escalation_request 类别前缀。

测试约定：bus 指向不可达地址（http://127.0.0.1:1）；report 相关仅构造与总线校验，
不触真实网络。escalate 的返回值与 Report 构造通过打桩捕获，验证路由面而不发网络。
TODO(领域实现): 真实送达链路（8096 总线在役）由 W24 执行插槽实装后接端到端测试。
"""
from __future__ import annotations

import pytest

from roles.base_agent import Report
from roles.message_bus import (PRINCIPAL_MESSAGE_CLASSES,
                                PRINCIPAL_WHITELIST, MessageBus)
from roles.rectification_line import (PREFIX_ESCALATION_REQUEST,
                                      PREFIX_ESCALATION_ROUTE,
                                      PREFIX_REMEDIATION_TICKET,
                                      SEVERITIES, SEVERITY_BLOCKING,
                                      SEVERITY_ESCALATION, SEVERITY_NORMAL,
                                      is_exception_class, normalize_severity,
                                      routes_for)
from roles.zhao_yun import ZhaoYun
from roles.zhuge_liang import ZhugeLiang

BUS_URL = "http://127.0.0.1:1"


class _Capture:
    """打桩 report_up：只捕获构造，不发送（零网络）。"""

    def __init__(self) -> None:
        self.reports = []

    def __call__(self, report: Report) -> None:
        self.reports.append(report)

    @property
    def last(self) -> Report:
        return self.reports[-1]


@pytest.fixture()
def seats():
    zl = ZhugeLiang(report_to="a0a", bus_url=BUS_URL, metrics_port=0)
    zy = ZhaoYun(report_to="a0a", bus_url=BUS_URL, metrics_port=0)
    cap = _Capture()
    zl.report_up = cap
    zy.report_up = cap
    return zl, zy, cap


# ---------------- 判据① 越级直发被拦 ----------------

def test_supervision_seats_not_in_principal_whitelist():
    assert "zhuge_liang" not in PRINCIPAL_WHITELIST
    assert "zhao_yun" not in PRINCIPAL_WHITELIST
    assert "tian_feng" in PRINCIPAL_WHITELIST


def test_supervision_seat_forged_principal_lands_in_dead_letter(tmp_path):
    """阳性对照：伪造监督席 principal 直发必被拦入死信（检出器未失效）。"""
    bus = MessageBus(tmp_path / "ledger.jsonl", tmp_path / "bus_dlq.jsonl")
    bus.register("zhuge_liang", "command")
    res = bus.route({"level": "principal", "source": "zhuge_liang",
                     "subject": "[升级请示] 监督席直发本人", "body": "越权"})
    assert not res.get("ok"), "监督席直发 principal 未被拦截——白名单校验失效"
    assert "principal" in res.get("reason", "") or "白名单" in res.get("reason", "")
    assert bus.dead_letters(), "越级消息未留死信——审计断链"


def test_escalate_never_builds_principal_report(seats):
    """判据①：escalate 无论声明何种严重度，构造出的 Report 恒为 command 层。"""
    zl, _zy, cap = seats
    for sev in SEVERITIES:
        out = zl.escalate(f"议题{sev}", "正文", {"e": 1}, severity=sev)
        assert cap.last.level == "command", f"severity={sev} 时仍构造 principal"
        assert out["direct_to_principal"] is False
        assert out["route"] == "tian_feng"
    assert cap.last.subject.startswith(PREFIX_ESCALATION_ROUTE)


def test_escalate_severity_is_declaration_not_verdict(seats):
    """阻断类判定权在归口席：severity 只是来源声明的线索。"""
    zl, _zy, _cap = seats
    out = zl.escalate("阻断议题", "正文", {"e": 1}, severity=SEVERITY_BLOCKING)
    assert out["severity_declared"] == SEVERITY_BLOCKING
    assert "阻断类判定权在归口席" in out["note"] or "归口席" in out["note"]


# ---------------- 判据③ 非阻断类改道 ----------------

def test_routes_for_non_exception_go_to_intake_only():
    assert routes_for("一般问题") == ("tian_feng",)


def test_routes_for_exception_go_dual_action():
    """例外通道双动作：待签 + 预警（老田 2026-10-20 校准）。"""
    assert routes_for("approval") == ("approval_gate", "risk_guard")
    assert routes_for("trade") == ("approval_gate", "risk_guard")
    assert routes_for("production_change") == ("approval_gate", "risk_guard")
    assert is_exception_class("approval") and is_exception_class("production_change")
    assert not is_exception_class("general")


def test_severity_normalize_keeps_three_bands():
    assert normalize_severity(SEVERITY_NORMAL) == SEVERITY_NORMAL
    assert normalize_severity(SEVERITY_ESCALATION) == SEVERITY_ESCALATION
    assert normalize_severity(SEVERITY_BLOCKING) == SEVERITY_BLOCKING
    assert normalize_severity(None) in SEVERITIES
    assert normalize_severity("乱七八糟") in SEVERITIES


# ---------------- 判据④ 两类可区分可审计 ----------------

def test_two_blocking_classes_are_distinguishable():
    """阻断类两类必须可用机器区分（裁定包指出的审计隐患消解项）。"""
    assert PREFIX_REMEDIATION_TICKET != PREFIX_ESCALATION_REQUEST
    t = f"{PREFIX_REMEDIATION_TICKET} 工单受阻"
    e = f"{PREFIX_ESCALATION_REQUEST} 归口不收敛"
    assert t.startswith(PREFIX_REMEDIATION_TICKET)
    assert e.startswith(PREFIX_ESCALATION_REQUEST)
    assert not e.startswith(PREFIX_REMEDIATION_TICKET)
    # 归口路由前缀不得与两类阻断前缀混淆
    assert not PREFIX_ESCALATION_ROUTE.startswith(PREFIX_REMEDIATION_TICKET)
    assert not PREFIX_ESCALATION_ROUTE.startswith(PREFIX_ESCALATION_REQUEST)


def test_ledger_records_subject_for_class_audit(tmp_path):
    """审计可回放：类别以主题前缀落总线账本，可按前缀筛出两类阻断直发。"""
    bus = MessageBus(tmp_path / "ledger.jsonl", tmp_path / "bus_dlq.jsonl")
    bus.register("tian_feng", "command")
    # 归口席直发本人：两类阻断消息各走一条
    for cls in ("remediation_ticket", "escalation_request"):
        prefix, seats_ok = PRINCIPAL_MESSAGE_CLASSES[cls]
        assert seats_ok == ("tian_feng",), f"{cls} 的允许发送席位口径不对"
        res = bus.route({"level": "principal", "source": "tian_feng",
                         "subject": f"{prefix} 样例", "body": "x"})
        assert res.get("ok"), f"归口席 {cls} 类直发本人被拦——类别校验异常"
    import json
    rows = [json.loads(l) for l in
            (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]
    # 总线派生 message_class 字段（可审计分类）+ 账本内嵌 subject 前缀双口径
    classes = [r.get("message_class") for r in rows]
    assert "remediation_ticket" in classes
    assert "escalation_request" in classes
    subs = [r.get("report", {}).get("subject", "") for r in rows]
    assert any(s.startswith(PREFIX_REMEDIATION_TICKET) for s in subs)
    assert any(s.startswith(PREFIX_ESCALATION_REQUEST) for s in subs)


# ---------------- S0 角色表口径 ----------------

def test_both_supervision_seats_are_command_manager(seats):
    """S0：诸葛/赵云均为常任管理席（command:manager），不再是专职监督位标签。"""
    zl, zy, _cap = seats
    assert zl.role == "command:manager"
    assert zy.role == "command:manager"


def test_s0_docstrings_state_manager_positioning():
    """诚实披露：模块 docstring 必须写明常任管理席，且监督整改只是分支。"""
    import roles.zhao_yun as zy_mod
    import roles.zhuge_liang as zl_mod

    for mod in (zl_mod, zy_mod):
        doc = mod.__doc__ or ""
        assert "常任管理席" in doc, f"{mod.__name__} 未声明常任管理席"
        assert "分支" in doc, f"{mod.__name__} 未写明监督整改只是分支"
        assert "永不直发本人" in doc