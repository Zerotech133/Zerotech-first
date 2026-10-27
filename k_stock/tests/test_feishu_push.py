# -*- coding: utf-8 -*-
"""WO-QT-20261020-022 验收测试：飞书推送通道（015 落地单）。

验收口径（工单 + 契约）：
  1. **零真实出口（铁律）**：默认路径不得发起任何 HTTP/socket 出网——用 socket
     桩（connect/send 全部抛）跑通全链路，证明默认 dry-run 不触网；真实发送还需
     live 三重门禁 + 凭据三重缺一即显式抛；
  2. **凭据纪律**：缺凭据 → `CredentialsMissing` 且 reason_code
     `feishu_credentials_missing`（**禁静默成功**）；凭据 repr/指纹/账本**无明文**；
  3. **收件人分域**：本人/运维/客户三域路由，未登记收件人显式拒（不猜默认）；
  4. **订阅闸门**：客户域未订阅 → 拒且写 entitlement_log；provider 未接线 →
     `subscription_gate_not_implemented` 拒（fail-closed，**绝不查不到就放行**）；
  5. **频控 / 静默窗 / critical 豁免**：按域计次封顶；22:00-07:30 窗内非 critical
     拒且留痕；**critical 豁免仅本人域**（客户/运维不豁免）；
  6. **幂等**：同报告+同目标+同模板重复推 → 零发送（duplicate_suppressed）；
  7. **-014 接线**：只写 outlet 段（pushed/reason/目标域/幂等键），**五段 sections
     与口径字段零改动**；append-only 账本自校验（改写 → 显式拒）。

全部路径注入 tmp 临时账本，绝不污染 k_stock/data/ 下的真实账本与凭据。
"""

import json
import socket
import sys
from datetime import datetime
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "paper"))

import feishu_push as fp  # noqa: E402
import report_builder as rb  # noqa: E402

CLOCK = datetime(2026, 10, 20, 16, 0, 0)
QUIET_NIGHT = datetime(2026, 10, 20, 23, 30, 0)  # 静默窗内
QUIET_DAWN = datetime(2026, 10, 20, 6, 0, 0)  # 静默窗内（跨零点另一侧）
LIVE_ENV = {
    fp.ENV_PUSH_LIVE: "1",
    fp.ENV_LIVE_DOMAINS: "owner",
    "FEISHU_PUSH_WEBHOOK_URL": "https://open.feishu.cn/hook/SECRET-TOKEN-XYZ",
}
FAKE_HOOK = "https://open.feishu.cn/hook/SECRET-TOKEN-XYZ"


# ---------- 铁律 0：零真实网络出口桩 ----------


@pytest.fixture
def no_socket(monkeypatch):
    """把 socket 出网全部钳死：任何 connect/send 都抛（证明默认路径零 HTTP）。"""

    def _blocked(*a, **k):
        raise AssertionError("零真实出口铁律：测试中禁止任何 socket 出网")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    return _blocked


@pytest.fixture
def logs(tmp_path):
    """账本锚到 tmp：entitlement_log / delivery_log 各自独立。"""
    return {
        "entitlement_log": tmp_path / "entitlement_log.jsonl",
        "delivery_log": tmp_path / "feishu_delivery.jsonl",
    }


def _payload(**kw):
    base = {
        "title": "司马懿汇报 2026-10",
        "text": "一、本期研究了什么 …",
        "risk_notice": "内部模拟汇报，不构成投资建议",
    }
    base.update(kw)
    return base


def _send(logs, target, **kw):
    kw.setdefault("template", fp.TEMPLATE_REPORT)
    kw.setdefault("report_id", "rpt-20261020-0001")
    kw.setdefault("now", CLOCK)
    kw.setdefault("dry_run", None)  # 走默认（应恒 dry-run）
    kw.setdefault("env", {})  # 无 live 开关、无凭据
    return fp.send(
        _payload(),
        target,
        entitlement_log=logs["entitlement_log"],
        delivery_log=logs["delivery_log"],
        **kw,
    )


# ---------- 1. 默认零真实出口 + dry-run 出回执不真发 ----------


def test_default_dryrun_zero_network(no_socket, logs):
    """默认（不传 dry_run、不给 live 开关、不给凭据）：出回执、零出网、未投递。"""
    target = fp.load_targets()[fp.DOMAIN_OWNER]
    r = _send(logs, target)
    assert r["pushed"] is False
    assert r["reason_code"] == fp.RC_DRYRUN_PREVIEW
    assert r["mode"] == fp.MODE_DRYRUN
    assert r["evidence"] == fp.EVIDENCE_DRYRUN
    assert r["charged"] is True  # 计次（防 dry-run 洗额度）


def test_dryrun_never_calls_webhook_post(no_socket, logs, monkeypatch):
    """干跑不得触达唯一 HTTP 出口函数（源级+运行级双保险）。"""
    called = []
    monkeypatch.setattr(fp, "_webhook_post", lambda *a, **k: called.append(1))
    target = fp.load_targets()[fp.DOMAIN_OWNER]
    r = _send(logs, target, dry_run=True)
    assert called == [] and r["pushed"] is False


def test_dryrun_receipt_does_not_claim_delivered(no_socket, logs):
    """严禁把 payload 打进日志冒充已送达：干跑回执 evidence 必为 dryrun_preview。"""
    target = fp.load_targets()[fp.DOMAIN_OWNER]
    r = _send(logs, target, dry_run=True)
    assert r["evidence"] in (fp.EVIDENCE_DRYRUN, fp.EVIDENCE_NONE)
    assert r["evidence"] != fp.EVIDENCE_API_ACCEPTED
    assert "SECRET-TOKEN-XYZ" not in json.dumps(r, ensure_ascii=False)
    rows = list(fp._iter_jsonl(logs["delivery_log"]))
    assert len(rows) == 1 and rows[0]["pushed"] is False


# ---------- 2. 凭据纪律：缺失显式抛 + 无明文 ----------


def test_missing_credentials_raises_explicitly(logs):
    """live 三重门禁齐备但**凭据缺失** → 显式抛 CredentialsMissing（含正确码）。"""
    target = fp.load_targets()[fp.DOMAIN_OWNER]
    with pytest.raises(fp.CredentialsMissing) as e:
        _send(
            logs,
            target,
            dry_run=False,
            env={fp.ENV_PUSH_LIVE: "1", fp.ENV_LIVE_DOMAINS: "owner"},
        )
    assert e.value.reason_code == fp.RC_CREDENTIALS_MISSING


def test_credentials_check_reports_missing_without_raising():
    """预检只回布尔态 + 原因码，**不抛也不回明文**。"""
    st = fp.check_credentials(env={})
    assert st["present"] is False and st["reason_code"] == fp.RC_CREDENTIALS_MISSING


def test_credentials_never_leak_plaintext(logs):
    """凭据 repr / 指纹 / 账本均无 webhook 明文与密钥明文。"""
    creds = fp.resolve_credentials(env=LIVE_ENV)
    blob = repr(creds) + json.dumps(creds.fingerprint(), ensure_ascii=False)
    assert "SECRET-TOKEN-XYZ" not in blob
    assert (
        creds.fingerprint()["webhook_sha256_8"]
        and len(creds.fingerprint()["webhook_sha256_8"]) == 8
    )
    assert "sign_secret_present" in creds.fingerprint()


def test_cred_file_registers_env_names_only_not_secrets(tmp_path):
    """凭据登记表**只登记变量名**：文件本身不含任何密文（不落盘明文凭据）。"""
    f = tmp_path / "cred.json"
    f.write_text(
        json.dumps({"webhook_env": "MY_HOOK_ENV", "sign_secret_env": "MY_SIGN_ENV"}),
        encoding="utf-8",
    )
    raw = f.read_text(encoding="utf-8")
    assert "SECRET-TOKEN-XYZ" not in raw  # 登记表无 webhook 明文
    assert set(json.loads(raw)) == {"webhook_env", "sign_secret_env"}  # 只登记变量名
    c = fp.resolve_credentials(env={"MY_HOOK_ENV": FAKE_HOOK}, cred_file=f)
    assert c.source == "env:MY_HOOK_ENV"
    with pytest.raises(fp.CredentialsMissing):
        fp.resolve_credentials(env={}, cred_file=f)  # 变量未注入 → 显式失败


def test_entitlement_log_has_no_plaintext_subject(logs):
    """权益判定日志：主体只出哈希指纹（open_id 明文一律不落账）。"""
    t = fp.load_targets()[fp.DOMAIN_CUSTOMER]
    _send(
        logs,
        t,
        subject="ou_user_ABCDEF123456",
        template=fp.TEMPLATE_ALERT,
        provider=fp.StaticEntitlementProvider({}),
    )
    raw = logs["entitlement_log"].read_text(encoding="utf-8")
    assert "ou_user_ABCDEF123456" not in raw
    assert "subj:" in raw


# ---------- 3. live 三重门禁（缺一即显式抛） ----------


def test_live_requires_switch(no_socket, logs):
    target = fp.load_targets()[fp.DOMAIN_OWNER]
    with pytest.raises(fp.PushError) as e:
        _send(logs, target, dry_run=False, env={fp.ENV_LIVE_DOMAINS: "owner"})
    assert e.value.reason_code == fp.RC_LIVE_SWITCH_OFF


def test_live_requires_domain_allowlist(no_socket, logs):
    """域白名单未含本人域 → 拒（即便总闸已开）。"""
    target = fp.load_targets()[fp.DOMAIN_OWNER]
    with pytest.raises(fp.PushError) as e:
        _send(
            logs,
            target,
            dry_run=False,
            env={fp.ENV_PUSH_LIVE: "1", fp.ENV_LIVE_DOMAINS: "ops"},
        )
    assert e.value.reason_code == fp.RC_LIVE_DOMAIN_NOT_ALLOWED


def test_live_all_gates_pass_sends_once(no_socket, logs, monkeypatch):
    """三重门禁 + 凭据齐备 → 唯一 HTTP 出口被调用恰一次（桩，非真发）。"""
    calls = []
    monkeypatch.setattr(
        fp,
        "_webhook_post",
        lambda creds, body, **k: (calls.append((creds, body)), {"code": 0})[1],
    )
    target = fp.load_targets()[fp.DOMAIN_OWNER]
    r = _send(logs, target, dry_run=False, env=LIVE_ENV)
    assert len(calls) == 1 and r["pushed"] is True
    assert r["evidence"] == fp.EVIDENCE_API_ACCEPTED


def test_send_failure_raises_not_swallowed(logs, monkeypatch):
    """真实发送失败**显式抛**，不吞、不静默成功。"""
    target = fp.load_targets()[fp.DOMAIN_OWNER]
    monkeypatch.setattr(
        fp,
        "_webhook_post",
        lambda *a, **k: (_ for _ in ()).throw(
            fp.PushError("feishu_send_failed", "boom")
        ),
    )
    with pytest.raises(fp.PushError) as e:
        _send(logs, target, dry_run=False, env=LIVE_ENV)
    assert e.value.reason_code == "feishu_send_failed"


# ---------- 4. 收件人分域路由 ----------


def test_three_domains_registered():
    tmap = fp.load_targets()
    assert set(tmap) == set(fp.DOMAINS) == {"owner", "ops", "customer"}
    assert tmap["owner"]["critical_exempt_silence"] is True
    assert tmap["customer"]["subscription_required"] is True
    assert tmap["ops"]["subscription_required"] is False


def test_template_whitelist_per_domain(logs):
    """模板白名单按域：本人域收汇报；客户域收不到汇报模板。"""
    tmap = fp.load_targets()
    _send(logs, tmap["owner"], template=fp.TEMPLATE_REPORT)
    with pytest.raises(fp.PushError) as e:
        _send(logs, tmap["customer"], template=fp.TEMPLATE_REPORT)
    assert e.value.reason_code == fp.RC_TEMPLATE_NOT_ALLOWED


def test_unknown_target_rejected_not_guessed():
    """未登记收件人 → 显式拒（绝不猜默认收件人）。"""
    with pytest.raises(fp.PushError) as e:
        fp.resolve_target("nobody@nowhere")
    assert e.value.reason_code == fp.RC_TARGET_UNKNOWN


def test_domain_mismatch_rejected():
    tmap = fp.load_targets()
    with pytest.raises(fp.PushError) as e:
        fp.resolve_target("owner.laotian", tmap, domain=fp.DOMAIN_CUSTOMER)
    assert e.value.reason_code == fp.RC_TARGET_DOMAIN_MISMATCH


def test_targets_file_rejects_customer_gate_downgrade(tmp_path):
    """**防绕闸**：客户域 subscription_required=false → 加载即显式拒。"""
    f = tmp_path / "targets.json"
    f.write_text(
        json.dumps(
            {
                "targets": {
                    "owner": {
                        "target_id": "owner.laotian",
                        "templates": ["sima_report"],
                    },
                    "ops": {"target_id": "ops.oncall", "templates": ["ticket"]},
                    "customer": {
                        "target_id": "customer.subscribers",
                        "templates": ["alert"],
                        "subscription_required": False,
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(fp.PushError) as e:
        fp.load_targets(f)
    assert e.value.reason_code == "push_target_invalid"


def test_targets_file_rejects_non_owner_critical_exemption(tmp_path):
    """豁免仅本人域：运维域声明 critical 豁免 → 显式拒。"""
    f = tmp_path / "targets.json"
    f.write_text(
        json.dumps(
            {
                "targets": {
                    "owner": {
                        "target_id": "owner.laotian",
                        "templates": ["sima_report"],
                    },
                    "ops": {
                        "target_id": "ops.oncall",
                        "templates": ["ticket"],
                        "critical_exempt_silence": True,
                    },
                    "customer": {
                        "target_id": "customer.subscribers",
                        "templates": ["alert"],
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(fp.PushError) as e:
        fp.load_targets(f)
    assert e.value.reason_code == "push_target_invalid"


def test_shipped_targets_file_loads():
    """随仓注册表 push_targets.json 可加载且三域齐备。"""
    tmap = fp.load_targets(fp.DEFAULT_TARGETS_PATH)
    assert set(tmap) == set(fp.DOMAINS)


# ---------- 5. 订阅闸门 ----------


def test_owner_domain_passes_without_subscription(logs):
    """本人域直通：判定放行且写 entitlement_log（审计可查）。"""
    tmap = fp.load_targets()
    g = fp.subscription_gate(
        "laotian",
        target=tmap["owner"],
        template=fp.TEMPLATE_REPORT,
        kind="daily",
        now=CLOCK,
        entitlement_log=logs["entitlement_log"],
    )
    assert g["allowed"] is True and g["reason_code"] == fp.RC_DELIVERED
    assert logs["entitlement_log"].exists()


def test_customer_domain_unwired_provider_fails_closed(logs):
    """客户域 provider 未接线 → **拒绝**（fail-closed，绝不查不到就放行）。"""
    tmap = fp.load_targets()
    g = fp.subscription_gate(
        "ou_x",
        target=tmap["customer"],
        template=fp.TEMPLATE_ALERT,
        kind="daily",
        now=CLOCK,
        entitlement_log=logs["entitlement_log"],
    )
    assert g["allowed"] is False
    assert g["reason_code"] == fp.RC_GATE_NOT_IMPLEMENTED
    assert g["implemented"] is False


def test_customer_not_subscribed_denied_and_logged(logs):
    """客户域未订阅 → 拒且写 entitlement_log（allowed=false + 原因码）。"""
    tmap = fp.load_targets()
    prov = fp.StaticEntitlementProvider({"ou_x": {"status": "none"}})
    g = fp.subscription_gate(
        "ou_x",
        target=tmap["customer"],
        template=fp.TEMPLATE_ALERT,
        kind="daily",
        now=CLOCK,
        provider=prov,
        entitlement_log=logs["entitlement_log"],
    )
    assert g["allowed"] is False
    assert g["reason_code"] == fp.RC_SUBSCRIPTION_NOT_SUBSCRIBED
    rows = list(fp._iter_jsonl(logs["entitlement_log"]))
    assert len(rows) == 1 and rows[0]["allowed"] is False
    assert rows[0]["domain"] == fp.DOMAIN_CUSTOMER


def test_customer_expired_and_quota_denied(logs):
    """到期 / 额度耗尽均拒（判定口径：状态=生效 AND now∈[起,止] AND 额度未耗尽）。"""
    tmap = fp.load_targets()
    tgt = tmap["customer"]
    expired = fp.StaticEntitlementProvider(
        {
            "ou_x": {
                "status": "active",
                "period": ("2026-09-01T00:00:00", "2026-10-01T00:00:00"),
            }
        }
    )
    g = fp.subscription_gate(
        "ou_x",
        target=tgt,
        template=fp.TEMPLATE_ALERT,
        kind="daily",
        now=CLOCK,
        provider=expired,
        entitlement_log=logs["entitlement_log"],
    )
    assert g["reason_code"] == fp.RC_SUBSCRIPTION_EXPIRED
    quota = fp.StaticEntitlementProvider(
        {
            "ou_x": {
                "status": "active",
                "period": ("2026-10-01T00:00:00", "2026-10-31T00:00:00"),
                "daily_quota": 3,
                "used_today": 3,
            }
        }
    )
    g2 = fp.subscription_gate(
        "ou_x",
        target=tgt,
        template=fp.TEMPLATE_ALERT,
        kind="daily",
        now=CLOCK,
        provider=quota,
        entitlement_log=logs["entitlement_log"],
    )
    assert g2["reason_code"] == fp.RC_SUBSCRIPTION_QUOTA_EXHAUSTED


def test_customer_active_passes_gate(logs):
    """判定链路打通：状态生效 + 在期内 + 有额度 → 放行（骨架可用，非计费实现）。"""
    tmap = fp.load_targets()
    prov = fp.StaticEntitlementProvider(
        {
            "ou_x": {
                "status": "active",
                "period": ("2026-10-01T00:00:00", "2026-10-31T00:00:00"),
                "daily_quota": 5,
                "used_today": 1,
            }
        }
    )
    g = fp.subscription_gate(
        "ou_x",
        target=tmap["customer"],
        template=fp.TEMPLATE_ALERT,
        kind="daily",
        now=CLOCK,
        provider=prov,
        entitlement_log=logs["entitlement_log"],
    )
    assert g["allowed"] is True and g["implemented"] is True


def test_customer_stock_content_requires_risk_notice(logs):
    """合规红线：客户域股票类内容缺风险提示 → 拒。"""
    tmap = fp.load_targets()
    with pytest.raises(fp.PushError) as e:
        fp.send(
            {"title": "预警", "text": "涨停"},
            tmap["customer"],
            template=fp.TEMPLATE_ALERT,
            kind="daily",
            now=CLOCK,
            env={},
            entitlement_log=logs["entitlement_log"],
            delivery_log=logs["delivery_log"],
        )
    assert e.value.reason_code == fp.RC_RISK_NOTICE_MISSING


def test_commercial_billing_not_implemented_is_declared():
    """诚实标注：客户域商业化模板未实现（M2/M3），不假装已接计费。"""
    raw = json.loads(fp.DEFAULT_TARGETS_PATH.read_text(encoding="utf-8"))
    assert raw["commercial_templates_status"] == "not_implemented"
    assert fp.CUSTOMER_TEMPLATES_COMMERCIAL == "not_implemented"


# ---------- 6. 频控 / 静默窗 / critical 豁免 ----------


def test_rate_limit_by_domain(logs):
    """频控按域：客户域封顶 2，第三次拒（dry-run 计次，防洗额度）。"""
    tmap = fp.load_targets()
    tgt = dict(tmap["customer"], daily_limit=2)
    prov = fp.StaticEntitlementProvider(
        {f"ou_{i}": {"status": "active"} for i in range(3)}
    )
    codes = []
    for i in range(3):
        r = _send(
            logs,
            tgt,
            subject=f"ou_{i}",
            template=fp.TEMPLATE_ALERT,
            provider=prov,
            report_id=f"rpt-{i}",
        )
        codes.append(r["reason_code"])
    assert codes[:2] == [fp.RC_DRYRUN_PREVIEW] * 2
    assert codes[2] == fp.RC_RATE_LIMITED


def test_owner_domain_exempt_from_rate_limit(logs):
    """本人域免额度（daily_limit=None）：连续多次不触发 rate_limited。"""
    tgt = fp.load_targets()[fp.DOMAIN_OWNER]
    codes = [_send(logs, tgt, report_id=f"rpt-{i}")["reason_code"] for i in range(5)]
    assert fp.RC_RATE_LIMITED not in codes


def test_quiet_window_blocks_non_critical(logs):
    """静默窗 22:00-07:30 内非 critical → 拒且留痕。"""
    tgt = fp.load_targets()[fp.DOMAIN_OPS]
    for night in (QUIET_NIGHT, QUIET_DAWN):
        r = _send(
            logs,
            tgt,
            template=fp.TEMPLATE_ALERT,
            now=night,
            report_id=f"rpt-{night.isoformat()}",
        )
        assert r["reason_code"] == fp.RC_QUIET_WINDOW
        assert r["quiet_window"] is True
    rows = list(fp._iter_jsonl(logs["delivery_log"]))
    assert len(rows) == 2 and all(x["quiet_window"] for x in rows)


def test_critical_exempt_only_for_owner(logs):
    """critical 豁免静默窗**仅本人域**（立项方案 §7-5）。"""
    tmap = fp.load_targets()
    r_owner = _send(
        logs,
        tmap["owner"],
        template=fp.TEMPLATE_ALERT,
        kind="alert_critical",
        now=QUIET_NIGHT,
    )
    assert r_owner["reason_code"] == fp.RC_DRYRUN_PREVIEW
    for dom in (fp.DOMAIN_OPS, fp.DOMAIN_CUSTOMER):
        tgt = tmap[dom]
        prov = (
            fp.StaticEntitlementProvider({"ou_x": {"status": "active"}})
            if dom == fp.DOMAIN_CUSTOMER
            else None
        )
        r = _send(
            logs,
            tgt,
            template=fp.TEMPLATE_ALERT,
            kind="alert_critical",
            now=QUIET_NIGHT,
            report_id=f"rpt-crit-{dom}",
            provider=prov,
        )
        assert r["reason_code"] == fp.RC_QUIET_WINDOW, dom


def test_quiet_hours_boundaries():
    """窗边界：22:00 起算、07:30 收口（闭开区间 [start, end)）。"""
    assert fp.is_quiet(datetime(2026, 10, 20, 22, 0), domain="ops", critical=False)
    assert not fp.is_quiet(datetime(2026, 10, 20, 7, 30), domain="ops", critical=False)
    assert not fp.is_quiet(datetime(2026, 10, 20, 12, 0), domain="ops", critical=False)


# ---------- 7. 幂等 ----------


def test_duplicate_report_target_suppressed(logs):
    """同报告 + 同目标 + 同模板重复推 → 零发送（duplicate_suppressed）。"""
    tgt = fp.load_targets()[fp.DOMAIN_OWNER]
    r1 = _send(logs, tgt, report_id="rpt-same")
    r2 = _send(logs, tgt, report_id="rpt-same")
    assert r1["reason_code"] == fp.RC_DRYRUN_PREVIEW
    assert r2["reason_code"] == fp.RC_DUPLICATE and r2["pushed"] is False
    assert r2["charged"] is False
    assert r1["idempotency_key"] == r2["idempotency_key"]
    assert len(list(fp._iter_jsonl(logs["delivery_log"]))) == 2  # 留痕


def test_idempotency_key_differs_by_target_and_template():
    a = fp.idempotency_key("rpt", "owner.laotian", fp.TEMPLATE_REPORT)
    b = fp.idempotency_key("rpt", "ops.oncall", fp.TEMPLATE_REPORT)
    c = fp.idempotency_key("rpt", "owner.laotian", fp.TEMPLATE_ALERT)
    assert len({a, b, c}) == 3 and len(a) == 16


def test_duplicate_never_calls_webhook(logs, monkeypatch):
    """重复推零发送：唯一 HTTP 出口不被触达。"""
    calls = []
    monkeypatch.setattr(
        fp, "_webhook_post", lambda *a, **k: (calls.append(1), {"code": 0})[1]
    )
    tgt = fp.load_targets()[fp.DOMAIN_OWNER]
    _send(logs, tgt, report_id="rpt-dup", dry_run=False, env=LIVE_ENV)
    r = _send(logs, tgt, report_id="rpt-dup", dry_run=False, env=LIVE_ENV)
    assert len(calls) == 1 and r["reason_code"] == fp.RC_DUPLICATE


# ---------- 8. 与 -014 接线（只写 outlet） ----------


def _report(**kw):
    return rb.build_report([], clock="2026-10-20T16:00:00", **kw)


def test_deliver_report_writes_outlet_only(logs):
    """接线：投递回执进 outlet 段；**五段 sections 与口径字段零改动**。"""
    report = _report()
    before_sections = json.dumps(report["sections"], ensure_ascii=False, sort_keys=True)
    before_scope = json.dumps(report["scope"], ensure_ascii=False, sort_keys=True)
    receipt = fp.deliver_report(
        report,
        target_id="owner.laotian",
        delivery_log=logs["delivery_log"],
        entitlement_log=logs["entitlement_log"],
        env={},
        now=CLOCK,
    )
    out = report["outlet"]
    assert out["pushed"] is False
    assert out["reason_code"] == fp.RC_DRYRUN_PREVIEW
    assert out["target_domain"] == fp.DOMAIN_OWNER
    assert out["idempotency_key"] == receipt["idempotency_key"]
    assert (
        json.dumps(report["sections"], ensure_ascii=False, sort_keys=True)
        == before_sections
    )
    assert (
        json.dumps(report["scope"], ensure_ascii=False, sort_keys=True) == before_scope
    )
    assert list(report["section_order"]) == list(rb.SECTIONS)


def test_deliver_report_keeps_no_live_fields(logs):
    """接线后仍满足 -014 实盘字段守卫（输出永远不含实盘字段）。"""
    report = _report()
    fp.deliver_report(
        report,
        delivery_log=logs["delivery_log"],
        entitlement_log=logs["entitlement_log"],
        env={},
        now=CLOCK,
    )
    rb._assert_no_live_fields(report)  # 命中即抛


def test_deliver_report_idempotent_across_calls(logs):
    """同报告重复接线 → 第二次幂等抑制（幂等键=报告指纹+目标+模板）。"""
    report = _report()
    r1 = fp.deliver_report(
        report,
        delivery_log=logs["delivery_log"],
        entitlement_log=logs["entitlement_log"],
        env={},
        now=CLOCK,
    )
    r2 = fp.deliver_report(
        report,
        delivery_log=logs["delivery_log"],
        entitlement_log=logs["entitlement_log"],
        env={},
        now=CLOCK,
    )
    assert r1["reason_code"] == fp.RC_DRYRUN_PREVIEW
    assert r2["reason_code"] == fp.RC_DUPLICATE


def test_report_fingerprint_stable_and_distinct():
    a = _report()
    b = _report()
    assert fp.report_fingerprint(a) == fp.report_fingerprint(b)
    c = rb.build_report([], clock="2026-10-21T16:00:00")
    assert fp.report_fingerprint(a) != fp.report_fingerprint(c)


def test_report_renders_after_wiring(logs):
    """接线后 -014 的 Markdown 渲染仍可用（未破坏其口径管线）。"""
    report = _report()
    fp.deliver_report(
        report,
        delivery_log=logs["delivery_log"],
        entitlement_log=logs["entitlement_log"],
        env={},
        now=CLOCK,
    )
    md = rb.render_markdown(report)
    assert "司马懿汇报" in md
    for t in rb.SECTION_TITLES.values():
        assert t in md


# ---------- 9. append-only 账本自校验 ----------


def test_ledger_tamper_detected(logs):
    """账本被改写 → verify_integrity 显式拒（append-only 篡改检测）。"""
    tgt = fp.load_targets()[fp.DOMAIN_OWNER]
    _send(logs, tgt, report_id="rpt-1")
    assert fp.verify_integrity(logs["delivery_log"])["entries"] == 1
    raw = (
        logs["delivery_log"]
        .read_text(encoding="utf-8")
        .replace('"pushed": false', '"pushed": true')
    )
    logs["delivery_log"].write_text(raw, encoding="utf-8")
    with pytest.raises(fp.PushError) as e:
        fp.verify_integrity(logs["delivery_log"])
    assert e.value.reason_code == "ledger_tampered"


def test_log_rejects_extra_fields(logs):
    """账本只收固定字段：夹带字段显式拒（防明文敏感值顺手入账）。"""
    with pytest.raises(fp.PushError) as e:
        fp._append_jsonl(
            logs["delivery_log"], {"ts": "x", "secret": "leak"}, fp._DELIVERY_FIELDS
        )
    assert e.value.reason_code == "log_field_forbidden"


# ---------- 10. 无网络库依赖（stdlib-only 源级断言） ----------


def test_module_is_stdlib_only():
    src = Path(fp.__file__).read_text(encoding="utf-8")
    for banned in (
        "import requests",
        "import httpx",
        "import aiohttp",
        "import socket",
        "from urllib.request import",
    ):
        assert banned not in src, f"禁引入网络库: {banned}"
