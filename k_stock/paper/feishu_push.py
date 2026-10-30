# -*- coding: utf-8 -*-
"""飞书推送通道（WO-QT-20261020-022，缺口 E-2 汇报器**真实推送**侧，014 之 015）。

014（`report_builder`）只生成文本、零真实出口，把推送挂账到 015。本模块就是
015 的通道落地：**收件人分域 + 订阅闸门骨架 + 幂等/频控/静默窗 + 发送适配层**。

口径依据：`docs/plans/2026-10-20_飞书中台商业化_收件人分域与订阅闸门立项方案.md`
（三域收件人模型 + 订阅闸门强制 + entitlement_log 权益判定日志 + 凭据纪律）。

## 三域收件人模型（立项方案 §4）

| 域 | 常量 | 对象 | 模板白名单 | 频控 | 静默窗 critical 豁免 | 订阅闸门 |
|---|---|---|---|---|---|---|
| 本人 | `owner` | 老田（司马懿汇报线） | 全量 | 免额度 | **豁免** | 直通（免订阅） |
| 运维 | `ops` | 内部运维 | 工单/告警类 | 有 | 不豁免 | 直通（内部白名单免订阅） |
| 客户 | `customer` | 订阅用户 | 方案决定（白名单） | 有 | **不豁免** | **强制** |

域常量与 `portal/notify_contracts.DOMAINS`（owner/ops/customer）逐字对齐——本模块
**只镜像不 import**（保持 stdlib-only、零 portal 依赖，避免跨域耦合）。

## 本单范围（诚实边界）

**本单只打通本人域**（内部汇报口径：报告 → 老田本人域收件人）。客户域：

- `subscription_gate` 提供**判定接口 + 判定日志骨架**（`EntitlementProvider`
  抽象 + 默认 `UnwiredEntitlementProvider`）；
- **默认 provider 未接线（not_implemented）→ 判定结果一律拒绝**
  （`subscription_gate_not_implemented`，fail-closed，绝不「查不到就放行」）；
- **商业化计费/订阅账本/收银台/续费提醒全部未实现**（属 M2/M3，见契约文档
  「未接线路径」）。**不实现计费**不是缺陷而是本单边界：老田未拍板档位与价格
  （立项方案 §7 第 2/3 项），先落地判定骨架与审计口径。

## 真实发送默认关闭（本单铁律）

- **默认 `dry_run=True`**：产出投递回执（`pushed=false` + reason_code），**不发起
  任何网络出口**；
- 真实发送需**三重显式开关同时成立**：`dry_run=False` + env `FEISHU_PUSH_LIVE=1`
  + 目标域在 env `FEISHU_PUSH_LIVE_DOMAINS` 白名单内，缺一即**显式抛**
  （`live_switch_off` / `live_domain_not_allowed`），**严禁静默降级**；
- **凭据缺失显式失败**：`CredentialsMissing`（`reason_code=feishu_credentials_missing`），
  **严禁静默成功、严禁把 payload 打进日志冒充已送达**；
- HTTP 出口仅存在于 `_webhook_post()` 单一函数内，只被 live 分支触达（默认路径
  零 socket——单测以 socket 桩断言）。

## 凭据纪律（沿用 R-1~R-4）

- **不打印凭据/密钥明文**（`repr`/日志/回执/账本均只出 `secret_sha256` 前 8 位
  指纹与布尔态，指纹不可逆）；
- **不落盘明文凭据**：凭据配置文件**只登记环境变量名**（`webhook_env` /
  `sign_secret_env`），值只从环境变量读——配置文件本身无密可失；
- entitlement_log / 投递账本**只记判定结论与原因码**，不记金额、不记流水号、
  不记 open_id 明文（主体标识一律 `_mask(subject)` 哈希指纹）；
- 客户域股票类内容**必须带风险提示**（缺则拒 `risk_notice_missing`），不承诺收益。

## 幂等 / 频控 / 静默窗

- **幂等键** = `sha256(report_id | target_id | template)` 前 16 位；同报告 + 同目标
  + 同模板**重复推送零发送**（`duplicate_suppressed`，只读账本判定，不写新行）；
- **频控按域**：本人域免额度；运维/客户域按 (域, 目标, kind, 自然日) 计次封顶，
  超限 `rate_limited`；
- **静默窗** 22:00-07:30（跨零点）：窗内非 critical 拒发并**留痕**
  （`quiet_window_suppressed`）；**critical 豁免仅本人域**（立项方案 §7 第 5 项
  拍板口径：客户域 critical 不豁免）。

## 与 -014 接线

`deliver_report(report, target_id=...)` 消费 -014 `build_report()` 的产物 dict，
**只写 `report["outlet"]`（投递回执），不碰五段 `sections` 与任何口径字段**——
-014 的五段口径一个字不改，本模块也不 import -014（零依赖）。

未接线路径（如实挂账，见契约文档）：
  - 客户域商业化计费与订阅账本接线（M2/M3）；
  - 真实凭据下发（老田在主会话手动注入，本单不碰凭据配置文件）；
  - 推送排期/调度面挂载（automation 69b51985 恢复后另单）；
  - 出向闸挂 8097 出口内部（立项方案 M1，本单**不建第二出口**，只在库内提供
    分域判定接口，由中台侧接线）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from tools.safe_urlopen import safe_urlopen

PUSH_SCHEMA_VERSION = 1

# ---------- 域常量（镜像 portal/notify_contracts.DOMAINS，逐字对齐不 import） ----------

DOMAIN_OWNER = "owner"
DOMAIN_OPS = "ops"
DOMAIN_CUSTOMER = "customer"
DOMAINS = (DOMAIN_OWNER, DOMAIN_OPS, DOMAIN_CUSTOMER)
DOMAIN_LABELS = {DOMAIN_OWNER: "本人", DOMAIN_OPS: "运维", DOMAIN_CUSTOMER: "客户"}

# 订阅闸门强制的域（立项方案 §3/§4：客户域强制，运维域内部白名单免订阅）
DOMAINS_REQUIRE_SUBSCRIPTION = frozenset({DOMAIN_CUSTOMER})
# critical 豁免静默窗的域（立项方案 §7-5：豁免仅本人域）
DOMAINS_CRITICAL_EXEMPT = frozenset({DOMAIN_OWNER})

# 模板白名单（客户域由方案决定；本单未接计费，故用**最小集占位**并在
# targets.json 显式登记 not_implemented_commercial=true，避免「白名单即已接计费」
# 的误读）
TEMPLATE_REPORT = "sima_report"
TEMPLATE_ALERT = "alert"
TEMPLATE_TICKET = "ticket"
OWNER_TEMPLATES = frozenset({TEMPLATE_REPORT, TEMPLATE_ALERT, TEMPLATE_TICKET})
OPS_TEMPLATES = frozenset({TEMPLATE_ALERT, TEMPLATE_TICKET})
CUSTOMER_TEMPLATES = frozenset({TEMPLATE_ALERT})  # 最小集占位（未接计费）
CUSTOMER_TEMPLATES_COMMERCIAL = "not_implemented"

# 客户域频控（每日上限；plan 额度在 M2 由订阅账本下发，此处为保守默认封顶）
DEFAULT_RATE_LIMITS = {
    DOMAIN_OWNER: None,  # 本人域免额度（立项方案 §4）
    DOMAIN_OPS: 200,
    DOMAIN_CUSTOMER: 20,
}

# 静默窗（跨零点）：22:00-07:30
QUIET_HOURS = ("22:00", "07:30")

# 唯一豁免 critical 的窄口径定义：仅风险提示类（沿用中台 W34 拍板口径）
CRITICAL_KINDS = frozenset({"alert_critical"})

# ---------- 凭据来源（配置文件只登记 env 名，值不落盘） ----------

ENV_PUSH_LIVE = "FEISHU_PUSH_LIVE"  # live 总闸（"1" 才允许真实发送）
ENV_LIVE_DOMAINS = "FEISHU_PUSH_LIVE_DOMAINS"  # live 域白名单（逗号分隔）
ENV_CRED_FILE = "FEISHU_PUSH_CREDENTIALS"  # 凭据**登记表**路径（非密文）
DEFAULT_CRED_FILE_ENV_KEYS = {
    "webhook_env": "FEISHU_PUSH_WEBHOOK_URL",
    "sign_secret_env": "FEISHU_PUSH_SIGN_SECRET",
}

# 目标域注册表（可被 push_targets.json 覆盖；覆盖后仍走 load_targets 严格校验）
DEFAULT_TARGETS_PATH = Path(__file__).resolve().parent / "push_targets.json"
DEFAULT_TARGETS = {
    DOMAIN_OWNER: {
        "target_id": "owner.laotian",
        "name": "老田（本人域·司马懿汇报线）",
        "templates": sorted(OWNER_TEMPLATES),
        "daily_limit": None,
        "critical_exempt_silence": True,
        "subscription_required": False,
        "requires_risk_notice": False,
    },
    DOMAIN_OPS: {
        "target_id": "ops.oncall",
        "name": "内部运维值班",
        "templates": sorted(OPS_TEMPLATES),
        "daily_limit": DEFAULT_RATE_LIMITS[DOMAIN_OPS],
        "critical_exempt_silence": False,
        "subscription_required": False,
        "requires_risk_notice": False,
    },
    DOMAIN_CUSTOMER: {
        "target_id": "customer.subscribers",
        "name": "订阅用户（占位·未接计费）",
        "templates": sorted(CUSTOMER_TEMPLATES),
        "daily_limit": DEFAULT_RATE_LIMITS[DOMAIN_CUSTOMER],
        "critical_exempt_silence": False,
        "subscription_required": True,
        "requires_risk_notice": True,
    },
}

# ---------- 账本（append-only jsonl；默认落 k_stock/data/，测试注入 tmp） ----------

DEFAULT_ENTITLEMENT_LOG = (
    Path(__file__).resolve().parent.parent / "data" / "entitlement_log.jsonl"
)
DEFAULT_DELIVERY_LOG = (
    Path(__file__).resolve().parent.parent / "data" / "feishu_delivery.jsonl"
)

# 判定日志固定字段（其余一律不收，防夹带明文敏感值）
_ENTITLEMENT_FIELDS = (
    "ts",
    "log_id",
    "schema_version",
    "subject_ref",
    "domain",
    "target_id",
    "template",
    "kind",
    "provider",
    "implemented",
    "allowed",
    "reason_code",
    "checked",
    "entry_sha256",
)
_DELIVERY_FIELDS = (
    "ts",
    "receipt_id",
    "schema_version",
    "report_id",
    "report_summary",
    "idempotency_key",
    "domain",
    "target_id",
    "template",
    "kind",
    "mode",
    "pushed",
    "charged",
    "reason_code",
    "evidence",
    "critical",
    "quiet_window",
    "rate_used",
    "rate_limit",
    "gate",
    "credential_state",
    "message_chars",
    "payload_sha256",
    "entry_sha256",
)

_SUBJECT_PATTERN = r"[A-Za-z0-9_.:@\-]{1,64}"
_APPEND_LOCK = threading.Lock()


# ---------- 异常 ----------


class PushError(Exception):
    """推送通道契约违例（closed on failure）。携带 reason_code 供上游归因。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


class CredentialsMissing(PushError):
    """凭据缺失/不可用——真实发送显式失败，严禁静默成功。"""


class EntitlementNotImplemented(PushError):
    """订阅闸门判定链路未接线（not_implemented）；由 gate 转成拒绝回执。"""


# ---------- reason_code 单一事实源 ----------

RC_CREDENTIALS_MISSING = "feishu_credentials_missing"
RC_CREDENTIALS_FILE_INVALID = "feishu_credentials_file_invalid"
RC_CREDENTIALS_EMPTY = "feishu_credentials_empty"
RC_LIVE_SWITCH_OFF = "live_switch_off"
RC_LIVE_DOMAIN_NOT_ALLOWED = "live_domain_not_allowed"
RC_TARGET_UNKNOWN = "push_target_unknown"
RC_TARGET_DOMAIN_MISMATCH = "push_target_domain_mismatch"
RC_TEMPLATE_NOT_ALLOWED = "template_not_allowed"
RC_TEMPLATE_MISSING = "template_missing"
RC_PAYLOAD_INVALID = "push_payload_invalid"
RC_DUPLICATE = "duplicate_suppressed"
RC_RATE_LIMITED = "rate_limited"
RC_QUIET_WINDOW = "quiet_window_suppressed"
RC_RISK_NOTICE_MISSING = "risk_notice_missing"
RC_SUBSCRIPTION_REQUIRED = "subscription_required"
RC_SUBSCRIPTION_NOT_SUBSCRIBED = "subscription_not_subscribed"
RC_SUBSCRIPTION_EXPIRED = "subscription_expired"
RC_SUBSCRIPTION_QUOTA_EXHAUSTED = "subscription_quota_exhausted"
RC_SUBSCRIPTION_UNKNOWN = "subscription_status_unknown"
RC_GATE_NOT_IMPLEMENTED = "subscription_gate_not_implemented"
RC_DELIVERED = "delivered_api_accepted"
RC_DRYRUN_PREVIEW = "dryrun_preview_not_sent"

# 证据分级（口径引中台 EVIDENCE_DELIVERY 三档，不另立）
EVIDENCE_API_ACCEPTED = "api_accepted"
EVIDENCE_DRYRUN = "dryrun_preview"
EVIDENCE_NONE = "none"

MODE_DRYRUN = "dryrun"
MODE_LIVE = "live_sent"


# ---------- 通用小工具 ----------


def _sha256_16(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[
        :16
    ]


def _mask(subject) -> str:
    """主体标识掩码：只出不可逆指纹，**不出明文**（open_id/手机号一律哈希）。"""
    if subject in (None, ""):
        return "subj:anonymous"
    return "subj:" + hashlib.sha256(str(subject).encode("utf-8")).hexdigest()[:12]


def _digest(entry: dict) -> str:
    body = json.dumps(
        {k: v for k, v in entry.items() if k != "entry_sha256"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _append_jsonl(path, entry: dict, fields: tuple) -> dict:
    """append-only 追加一行 + 逐行自校验 sha256（改写→verify 时显式拒）。"""
    extra = set(entry) - set(fields)
    if extra:
        raise PushError(
            "log_field_forbidden", f"账本只收固定字段，夹带字段: {sorted(extra)}"
        )
    row = dict(entry)
    row["entry_sha256"] = _digest(row)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    with _APPEND_LOCK:
        with open(p, "ab") as f:  # 追加语义，禁 'w'/'r+'
            f.write(line)
    return row


def _iter_jsonl(path):
    p = Path(path)
    if not p.exists():
        return
    with open(p, "r", encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError as e:
                raise PushError(
                    "ledger_corrupt",
                    f"账本第 {ln} 行非法 JSON（closed on failure）: {e}",
                ) from e


def verify_integrity(path) -> dict:
    """逐行校验自校验 sha256；任一行被改写即显式拒（append-only 篡改检测）。"""
    checked = 0
    for row in _iter_jsonl(path):
        checked += 1
        if row.get("entry_sha256") != _digest(row):
            raise PushError(
                "ledger_tampered",
                f"账本行自校验失败（append-only 被破坏）: {path} "
                f"log_id={row.get('log_id') or row.get('receipt_id')}",
            )
    return {"path": str(path), "entries": checked, "tampered": False}


def _parse_hhmm(text) -> tuple[int, int]:
    m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", str(text).strip())
    if not m:
        raise PushError("quiet_hours_invalid", f"静默窗时刻须 HH:MM: {text!r}")
    return int(m.group(1)), int(m.group(2))


# ---------- 收件人分域：push_targets ----------


def _validate_target(domain: str, spec: dict) -> dict:
    if domain not in DOMAINS:
        raise PushError(
            "push_target_invalid", f"未知域: {domain!r}（白名单 {DOMAINS}）"
        )
    if not isinstance(spec, dict):
        raise PushError("push_target_invalid", f"{domain} 目标须为 dict")
    tid = spec.get("target_id")
    if not isinstance(tid, str) or not re.fullmatch(_SUBJECT_PATTERN, tid):
        raise PushError("push_target_invalid", f"{domain}.target_id 非法: {tid!r}")
    tpl = spec.get("templates")
    if not isinstance(tpl, (list, tuple)) or not tpl:
        raise PushError("push_target_invalid", f"{domain}.templates 须为非空 list")
    limit = spec.get("daily_limit", DEFAULT_RATE_LIMITS[domain])
    if limit is not None and (
        not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0
    ):
        raise PushError("push_target_invalid", f"{domain}.daily_limit 非法: {limit!r}")
    sub_required = bool(
        spec.get("subscription_required", domain in DOMAINS_REQUIRE_SUBSCRIPTION)
    )
    # 闸门强制的域不得被配置降级（客户域强降级=绕闸，直接拒）
    if domain in DOMAINS_REQUIRE_SUBSCRIPTION and not sub_required:
        raise PushError(
            "push_target_invalid",
            f"{domain} 域订阅闸门强制，不接受 subscription_required=false",
        )
    exempt = bool(
        spec.get("critical_exempt_silence", domain in DOMAINS_CRITICAL_EXEMPT)
    )
    if exempt and domain not in DOMAINS_CRITICAL_EXEMPT:
        raise PushError(
            "push_target_invalid",
            f"critical 豁免静默窗仅限本人域，{domain} 不得声明豁免",
        )
    return {
        "domain": domain,
        "target_id": tid,
        "name": str(spec.get("name") or tid),
        "templates": frozenset(str(t) for t in tpl),
        "daily_limit": limit,
        "critical_exempt_silence": exempt,
        "subscription_required": sub_required,
        "requires_risk_notice": bool(
            spec.get("requires_risk_notice", domain == DOMAIN_CUSTOMER)
        ),
    }


def load_targets(path=None) -> dict:
    """加载收件人分域注册表（域 → 目标规格）。

    默认用模块内 `DEFAULT_TARGETS`；给 `path` 时读 JSON（结构同上）并**严格校验**
    （未知域 / 非法 target_id / 客户域闸门降级 / 非本人域 critical 豁免 → 显式抛）。
    """
    if path is None:
        return {d: _validate_target(d, s) for d, s in DEFAULT_TARGETS.items()}
    p = Path(path)
    if not p.exists():
        raise PushError("push_targets_missing", f"收件人注册表不存在: {p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise PushError(
            "push_targets_corrupt", f"收件人注册表非法 JSON: {p} ({e})"
        ) from e
    targets = raw.get("targets") if isinstance(raw, dict) else None
    if not isinstance(targets, dict) or set(targets) != set(DOMAINS):
        raise PushError("push_targets_corrupt", f"注册表须含且仅含三域 {DOMAINS}: {p}")
    return {d: _validate_target(d, s) for d, s in targets.items()}


def resolve_target(
    target_id: str, targets: dict | None = None, *, domain: str | None = None
) -> dict:
    """按 target_id 解析目标；给了 domain 则强制配对（不匹配即拒，不猜）。"""
    tmap = targets if targets is not None else load_targets()
    hits = [t for t in tmap.values() if t["target_id"] == target_id]
    if not hits:
        raise PushError(
            RC_TARGET_UNKNOWN,
            f"收件人未登记（拒绝推送，不猜默认收件人）: {target_id!r}",
        )
    if len(hits) > 1:
        raise PushError(RC_TARGET_UNKNOWN, f"收件人重复登记: {target_id!r}")
    t = hits[0]
    if domain is not None and t["domain"] != domain:
        raise PushError(
            RC_TARGET_DOMAIN_MISMATCH,
            f"收件人域不匹配: 声明 {domain!r}，实际 {t['domain']!r}",
        )
    return t


# ---------- 订阅闸门（判定接口 + 判定日志骨架） ----------


class EntitlementProvider:
    """权益查询接口（**骨架**：M2 由订阅账本 sqlite 实现）。

    实现方须返回 dict：
      {"status": "active"|"expired"|"none"|"unwired"|其他,
       "plan_id": str|None, "period": (start_iso, end_iso)|None,
       "daily_quota": int|None, "used_today": int|None}
    判定口径（立项方案 §5）：`生效 = 状态=生效 AND now∈[起,止] AND 当日额度未耗尽`。
    """

    name = "abstract"

    def lookup(self, subject, *, kind: str, now: datetime) -> dict:  # pragma: no cover
        raise NotImplementedError


class UnwiredEntitlementProvider(EntitlementProvider):
    """默认 provider：**未接线**，判定恒为 not_implemented（fail-closed）。

    本单不实现商业化计费与订阅账本接线（M2/M3），故客户域默认判定「无法确认权益
    → 拒绝」。绝不「查不到就放行」。
    """

    name = "unwired"

    def lookup(self, subject, *, kind: str, now: datetime) -> dict:
        return {"status": "unwired"}


class StaticEntitlementProvider(EntitlementProvider):
    """测试/影子用的静态 provider（**不实现计费**：只按给定字典回判定结论）。

    存在的意义：让闸门判定链路（判定→日志→放行/拒绝）可离线验证，而不必先有
    订阅账本。它**不是**计费实现，也不产生任何真实权益。
    """

    name = "static"

    def __init__(self, table: dict | None = None):
        self.table = dict(table or {})

    def lookup(self, subject, *, kind: str, now: datetime) -> dict:
        row = self.table.get(subject) or {}
        base = {
            "status": "none",
            "plan_id": None,
            "period": None,
            "daily_quota": None,
            "used_today": None,
        }
        base.update(row)
        return base


def _decide_entitlement(
    rec: dict, *, kind: str, now: datetime
) -> tuple[bool, str, dict]:
    """判定口径（立项方案 §5）。返回 (allowed, reason_code, checked)。

    `checked` 只记**查了哪些字段与判定中间量**，不记金额/流水/密钥。
    """
    status = str(rec.get("status") or "unknown")
    checked = {
        "status": status,
        "plan_id": rec.get("plan_id"),
        "period": rec.get("period"),
        "daily_quota": rec.get("daily_quota"),
        "used_today": rec.get("used_today"),
        "kind": kind,
        "checked_at": now.isoformat(timespec="seconds"),
    }
    if status == "unwired":
        return False, RC_GATE_NOT_IMPLEMENTED, checked
    if status in ("none", "not_subscribed"):
        return False, RC_SUBSCRIPTION_NOT_SUBSCRIBED, checked
    if status == "expired":
        return False, RC_SUBSCRIPTION_EXPIRED, checked
    if status != "active":
        return False, RC_SUBSCRIPTION_UNKNOWN, checked
    period = rec.get("period")
    if period:
        try:
            start, end = period
            s = datetime.fromisoformat(str(start))
            e = datetime.fromisoformat(str(end))
        except (TypeError, ValueError):
            return False, RC_SUBSCRIPTION_UNKNOWN, checked
        if not (s <= now <= e):
            checked["in_period"] = False
            return False, RC_SUBSCRIPTION_EXPIRED, checked
        checked["in_period"] = True
    quota, used = rec.get("daily_quota"), rec.get("used_today")
    if isinstance(quota, int) and not isinstance(quota, bool) and isinstance(used, int):
        if used >= quota:
            checked["quota_exhausted"] = True
            return False, RC_SUBSCRIPTION_QUOTA_EXHAUSTED, checked
    return True, RC_DELIVERED, checked


def record_entitlement(
    path,
    *,
    subject,
    domain: str,
    target_id: str,
    template: str,
    kind: str,
    provider: str,
    implemented: bool,
    allowed: bool,
    reason_code: str,
    checked: dict,
    now: datetime,
) -> dict:
    """写一行权益判定日志（append-only）。**只记结论与原因码，不记明文敏感值**。"""
    row = {
        "ts": now.isoformat(timespec="seconds"),
        "log_id": _sha256_16(
            now.isoformat(), subject, domain, target_id, template, kind
        ),
        "schema_version": PUSH_SCHEMA_VERSION,
        "subject_ref": _mask(subject),  # 主体只出哈希指纹
        "domain": domain,
        "target_id": target_id,
        "template": template,
        "kind": kind,
        "provider": provider,
        "implemented": bool(implemented),
        "allowed": bool(allowed),
        "reason_code": reason_code,
        "checked": {
            k: checked[k]
            for k in sorted(checked)
            if k
            in (
                "status",
                "plan_id",
                "period",
                "daily_quota",
                "used_today",
                "kind",
                "checked_at",
                "in_period",
                "quota_exhausted",
            )
        },
    }
    return _append_jsonl(path, row, _ENTITLEMENT_FIELDS)


def subscription_gate(
    subject,
    *,
    target: dict,
    template: str,
    kind: str,
    now: datetime | None = None,
    provider: EntitlementProvider | None = None,
    entitlement_log=None,
) -> dict:
    """订阅闸门判定（**强制口径**：客户域未放行即不得分发）。

    - 本人域/运维域：直通（免订阅），判据写 `domain_direct_pass`；
    - 客户域：必走 provider 判定 + 写 entitlement_log；provider 未接线 →
      **拒绝**（`subscription_gate_not_implemented`）；
    - 判定失败一律 fail-closed（拒绝并留痕），不抛异常冒泡（由 send 转成回执）。

    返回 dict：`{allowed, reason_code, domain, provider, implemented, checked}`。
    """
    now = now or datetime.now()
    dom = target["domain"]
    if not target["subscription_required"]:
        checked = {
            "status": "domain_direct_pass",
            "domain": dom,
            "kind": kind,
            "checked_at": now.isoformat(timespec="seconds"),
        }
        gate = {
            "allowed": True,
            "reason_code": RC_DELIVERED,
            "domain": dom,
            "provider": "none",
            "implemented": True,
            "checked": checked,
        }
        if entitlement_log is not None:
            record_entitlement(
                entitlement_log,
                subject=subject,
                domain=dom,
                target_id=target["target_id"],
                template=template,
                kind=kind,
                provider="none",
                implemented=True,
                allowed=True,
                reason_code=RC_DELIVERED,
                checked=checked,
                now=now,
            )
        return gate
    if template not in target["templates"]:
        gate = {
            "allowed": False,
            "reason_code": RC_TEMPLATE_NOT_ALLOWED,
            "domain": dom,
            "provider": "none",
            "implemented": True,
            "checked": {
                "status": "template_not_allowed",
                "kind": kind,
                "checked_at": now.isoformat(timespec="seconds"),
            },
        }
        if entitlement_log is not None:
            record_entitlement(
                entitlement_log,
                subject=subject,
                domain=dom,
                target_id=target["target_id"],
                template=template,
                kind=kind,
                provider="none",
                implemented=False,
                allowed=False,
                reason_code=RC_TEMPLATE_NOT_ALLOWED,
                checked=gate["checked"],
                now=now,
            )
        return gate

    prov = provider or UnwiredEntitlementProvider()
    rec = prov.lookup(subject, kind=kind, now=now)
    allowed, reason, checked = _decide_entitlement(rec, kind=kind, now=now)
    implemented = rec.get("status") != "unwired"
    gate = {
        "allowed": allowed,
        "reason_code": reason,
        "domain": dom,
        "provider": getattr(prov, "name", "unknown"),
        "implemented": implemented,
        "checked": checked,
    }
    if entitlement_log is not None:
        record_entitlement(
            entitlement_log,
            subject=subject,
            domain=dom,
            target_id=target["target_id"],
            template=template,
            kind=kind,
            provider=gate["provider"],
            implemented=implemented,
            allowed=allowed,
            reason_code=reason,
            checked=checked,
            now=now,
        )
    return gate


# ---------- 频控 / 静默窗 ----------


def is_quiet(
    now: datetime, *, domain: str, critical: bool, quiet_hours: tuple = QUIET_HOURS
) -> bool:
    """静默窗内且**无 critical 豁免** → True（窗内拒发）。"""
    if critical and domain in DOMAINS_CRITICAL_EXEMPT:
        return False
    sh, sm = _parse_hhmm(quiet_hours[0])
    eh, em = _parse_hhmm(quiet_hours[1])
    now.time()
    start, end = sh * 60 + sm, eh * 60 + em
    cur = now.hour * 60 + now.minute
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end  # 跨零点 22:00-07:30


def count_charged(
    delivery_log, *, domain: str, target_id: str, kind: str, day: date
) -> int:
    """当日已计次发送数（只数 `charged=true` 的行：被闸门/静默窗/幂等挡下的不计次）。"""
    n = 0
    for row in _iter_jsonl(delivery_log):
        if (
            row.get("charged")
            and row.get("domain") == domain
            and row.get("target_id") == target_id
            and row.get("kind") == kind
            and str(row.get("ts", ""))[:10] == day.isoformat()
        ):
            n += 1
    return n


# ---------- 凭据（只判存在与过期，绝不打印明文） ----------


class FeishuCredentials:
    """凭据容器：**不可逆 repr**，只出指纹与布尔态。"""

    __slots__ = ("webhook_url", "sign_secret", "_webhook_fp", "_secret_fp", "source")

    def __init__(self, webhook_url: str, sign_secret: str | None, source: str):
        self.webhook_url = webhook_url
        self.sign_secret = sign_secret
        self._webhook_fp = hashlib.sha256(webhook_url.encode("utf-8")).hexdigest()[:8]
        self._secret_fp = (
            hashlib.sha256(sign_secret.encode("utf-8")).hexdigest()[:8]
            if sign_secret
            else None
        )
        self.source = source

    def fingerprint(self) -> dict:
        """凭据态（**无明文**）：来源、webhook 指纹、签名密钥是否存在、是否过期。"""
        return {
            "source": self.source,
            "webhook_sha256_8": self._webhook_fp,
            "sign_secret_present": bool(self.sign_secret),
            "sign_secret_sha256_8": self._secret_fp,
            "expired": bool(self._expired()),
        }

    def _expired(self) -> bool:
        exp = os.environ.get("FEISHU_PUSH_WEBHOOK_EXPIRES_AT")
        if not exp:
            return False
        try:
            return datetime.fromisoformat(exp) < datetime.now()
        except ValueError:
            raise PushError(
                RC_CREDENTIALS_FILE_INVALID,
                f"FEISHU_PUSH_WEBHOOK_EXPIRES_AT 非 ISO 时刻: {exp!r}",
            )

    def __repr__(self) -> str:
        return (
            f"FeishuCredentials(source={self.source!r}, "
            f"webhook_sha256_8={self._webhook_fp!r}, "
            f"sign_secret_present={bool(self.sign_secret)!r})"
        )

    __str__ = __repr__


def resolve_credentials(
    env=None, *, cred_file: Path | None = None
) -> FeishuCredentials:
    """从环境变量取凭据（`cred_file` 只登记**变量名**，不存值）。

    缺失 → `CredentialsMissing`（`feishu_credentials_missing`），**显式失败**。
    """
    env = os.environ if env is None else env
    if cred_file is None and env.get(ENV_CRED_FILE):
        cred_file = Path(env[ENV_CRED_FILE])
    keys = dict(DEFAULT_CRED_FILE_ENV_KEYS)
    if cred_file is not None:
        p = Path(cred_file)
        if not p.exists():
            raise CredentialsMissing(
                RC_CREDENTIALS_MISSING, f"凭据登记表不存在: {p}（显式失败，不静默成功）"
            )
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except ValueError as e:
            raise CredentialsMissing(
                RC_CREDENTIALS_FILE_INVALID, f"凭据登记表非法 JSON: {p} ({e})"
            ) from e
        if not isinstance(raw, dict):
            raise CredentialsMissing(
                RC_CREDENTIALS_FILE_INVALID, f"凭据登记表须为 dict: {p}"
            )
        keys = {
            k: str(raw.get(k) or DEFAULT_CRED_FILE_ENV_KEYS[k])
            for k in DEFAULT_CRED_FILE_ENV_KEYS
        }
    webhook = (env.get(keys["webhook_env"]) or "").strip()
    secret = (env.get(keys["sign_secret_env"]) or "").strip()
    if not webhook:
        raise CredentialsMissing(
            RC_CREDENTIALS_MISSING,
            f"缺少环境变量 {keys['webhook_env']}（飞书 webhook 未注入；"
            "显式失败，严禁静默成功或用 payload 冒充已送达）",
        )
    if not webhook.startswith("https://"):
        raise CredentialsMissing(
            RC_CREDENTIALS_FILE_INVALID,
            f"{keys['webhook_env']} 须为 https:// 飞书 webhook 地址",
        )
    return FeishuCredentials(webhook, secret or None, f"env:{keys['webhook_env']}")


def check_credentials(env=None, *, cred_file: Path | None = None) -> dict:
    """凭据预检（**只回布尔态与指纹，绝不回明文**）；缺失时 reason_code 原样带出。"""
    try:
        return {
            "present": True,
            "reason_code": RC_DELIVERED,
            **resolve_credentials(env, cred_file=cred_file).fingerprint(),
        }
    except PushError as e:
        return {"present": False, "reason_code": e.reason_code, "detail": str(e)}


# ---------- 幂等 ----------


def idempotency_key(report_id: str, target_id: str, template: str) -> str:
    """幂等键 = sha256(报告 id | 收件人 | 模板) 前 16 位。"""
    return _sha256_16(report_id, target_id, template)


def seen_before(delivery_log, key: str) -> bool:
    return any(row.get("idempotency_key") == key for row in _iter_jsonl(delivery_log))


# ---------- 唯一网络出口（只被 live 分支触达） ----------


def _webhook_post(
    creds: FeishuCredentials, body: dict, *, timeout: float = 8.0
) -> dict:
    """真实发送（**唯一 HTTP 出口**）。仅 live 分支可触达；失败不吞（显式抛）。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        creds.webhook_url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with safe_safe_urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise PushError(
            "feishu_send_failed",
            f"飞书 webhook 调用失败（显式失败，不静默）: {type(e).__name__}",
        ) from e
    try:
        parsed = json.loads(payload)
    except ValueError:
        parsed = {"raw": payload[:200]}
    if int(parsed.get("code") or parsed.get("StatusCode") or 0) not in (0,):
        raise PushError(
            "feishu_send_rejected",
            f"飞书返回非零（显式失败）: code={parsed.get('code')} "
            f"msg={str(parsed.get('msg'))[:120]}",
        )
    return parsed


def _live_allowed(env, domain: str) -> tuple[bool, str]:
    """live 三重门禁的后两重：env 总闸 + 域白名单。缺一即显式拒。"""
    if str(env.get(ENV_PUSH_LIVE, "")).strip() != "1":
        return False, RC_LIVE_SWITCH_OFF
    allowed = {
        x.strip() for x in str(env.get(ENV_LIVE_DOMAINS, "")).split(",") if x.strip()
    }
    if domain not in allowed:
        return False, RC_LIVE_DOMAIN_NOT_ALLOWED
    return True, RC_DELIVERED


# ---------- 发送适配层 ----------


def send(
    payload: dict,
    target: dict,
    *,
    template: str,
    kind: str = "daily",
    subject=None,
    dry_run: bool | None = None,
    now: datetime | None = None,
    report_id: str = "",
    targets: dict | None = None,
    entitlement_log=None,
    delivery_log=None,
    provider: EntitlementProvider | None = None,
    env=None,
    cred_file: Path | None = None,
) -> dict:
    """把 payload 投到 target，产出**投递回执**（默认 dry-run，零网络出口）。

    判定顺序（任一步不过即停，**不回退不静默**）：
      1. 参数校验（payload/template/subject 形状）；
      2. 模板白名单（域内）；
      3. 客户域风险提示必带；
      4. 幂等：同报告+同目标+同模板已发过 → **零发送**；
      5. 频控（按域 / 按 kind / 按自然日）；
      6. 静默窗（critical 豁免仅本人域）；
      7. 订阅闸门（客户域强制；未接线 → 拒）；
      8. dry-run → 出回执不发送；live → 凭据缺失显式抛 + HTTP 发送。

    回执字段：pushed / reason_code / mode / domain / target_id / template /
    idempotency_key / evidence / gate / credential_state / charged。
    """
    env = os.environ if env is None else env
    now = now or datetime.now()
    entitlement_log = (
        DEFAULT_ENTITLEMENT_LOG if entitlement_log is None else Path(entitlement_log)
    )
    delivery_log = DEFAULT_DELIVERY_LOG if delivery_log is None else Path(delivery_log)
    domain = target["domain"]
    target_id = target["target_id"]
    if not isinstance(payload, dict) or not payload:
        raise PushError(RC_PAYLOAD_INVALID, "payload 须为非空 dict")
    if not isinstance(template, str) or not template:
        raise PushError(RC_TEMPLATE_MISSING, "template 须为非空字符串")
    if template not in target["templates"]:
        raise PushError(
            RC_TEMPLATE_NOT_ALLOWED,
            f"{domain} 域模板白名单不含 {template!r}（"
            f"白名单 {sorted(target['templates'])}）",
        )
    if (
        target["requires_risk_notice"]
        and not str(payload.get("risk_notice") or "").strip()
    ):
        raise PushError(
            RC_RISK_NOTICE_MISSING,
            f"{domain} 域股票类内容必须带风险提示模板（合规红线）",
        )
    critical = kind in CRITICAL_KINDS
    quiet = is_quiet(now, domain=domain, critical=critical)
    subject = subject if subject is not None else target_id
    idem = idempotency_key(report_id or "-", target_id, template)
    payload_sha = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    rate_used, rate_limit = 0, target["daily_limit"]

    def receipt(
        pushed: bool,
        reason_code: str,
        *,
        charged: bool,
        evidence: str = EVIDENCE_NONE,
        gate: dict | None = None,
        credential_state: str = "not_required",
    ):
        row = {
            "ts": now.isoformat(timespec="seconds"),
            "receipt_id": _sha256_16(idem, now.isoformat(), reason_code),
            "schema_version": PUSH_SCHEMA_VERSION,
            "report_id": report_id or None,
            "report_summary": str(payload.get("title") or "")[:120],
            "idempotency_key": idem,
            "domain": domain,
            "target_id": target_id,
            "template": template,
            "kind": kind,
            "mode": MODE_DRYRUN if dry_run else MODE_LIVE,
            "pushed": bool(pushed),
            "charged": bool(charged),
            "reason_code": reason_code,
            "evidence": evidence,
            "critical": critical,
            "quiet_window": quiet,
            "rate_used": rate_used,
            "rate_limit": rate_limit,
            "gate": gate
            or {
                "allowed": None,
                "reason_code": None,
                "domain": domain,
                "provider": "none",
                "implemented": None,
                "checked": {"status": "gate_not_reached"},
            },
            "credential_state": credential_state,
            "message_chars": len(str(payload.get("text") or "")),
            "payload_sha256": payload_sha,
        }
        row["report_id"] = report_id or None
        _append_jsonl(delivery_log, row, _DELIVERY_FIELDS)
        return row

    # 4. 幂等
    if seen_before(delivery_log, idem):
        return receipt(False, RC_DUPLICATE, charged=False, evidence=EVIDENCE_NONE)
    # 5. 频控
    rate_used = count_charged(
        delivery_log, domain=domain, target_id=target_id, kind=kind, day=now.date()
    )
    if rate_limit is not None and rate_used >= rate_limit:
        return receipt(False, RC_RATE_LIMITED, charged=False)
    # 6. 静默窗
    if quiet:
        return receipt(False, RC_QUIET_WINDOW, charged=False)
    # 7. 订阅闸门
    gate = subscription_gate(
        subject,
        target=target,
        template=template,
        kind=kind,
        now=now,
        provider=provider,
        entitlement_log=entitlement_log,
    )
    if not gate["allowed"]:
        return receipt(False, gate["reason_code"], charged=False, gate=gate)

    # 8. 发送：默认 dry-run；live 需三重门禁 + 凭据
    if dry_run is None:
        dry_run = True  # 默认零真实出口（本单铁律）
    if dry_run:
        return receipt(
            False, RC_DRYRUN_PREVIEW, charged=True, evidence=EVIDENCE_DRYRUN, gate=gate
        )
    ok, why = _live_allowed(env, domain)
    if not ok:
        raise PushError(
            why,
            f"真实发送未授权（{why}）：需 env {ENV_PUSH_LIVE}=1 且 "
            f"{ENV_LIVE_DOMAINS} 含 {domain}",
        )
    creds = resolve_credentials(env, cred_file=cred_file)
    if creds._expired():
        raise CredentialsMissing(
            RC_CREDENTIALS_MISSING, "飞书 webhook 凭据已过期（显式失败）"
        )
    body = {
        "msg_type": "text",
        "content": {"text": str(payload.get("text") or payload.get("title") or "")},
    }
    _webhook_post(creds, body)
    # credential_state 只出态（present / secret_signed / expired_checked），不出明文
    cred_state = "present" + (":sign_secret" if creds.sign_secret else "")
    return receipt(
        True,
        RC_DELIVERED,
        charged=True,
        evidence=EVIDENCE_API_ACCEPTED,
        gate=gate,
        credential_state=cred_state,
    )


# ---------- 与 -014 接线：只写 outlet 段 ----------


def build_payload(
    report: dict, *, template: str = TEMPLATE_REPORT, risk_notice: str | None = None
) -> dict:
    """-014 报告 dict → 推送 payload（**只读消费**，不改报告任何字段）。"""
    if not isinstance(report, dict) or "sections" not in report:
        raise PushError(
            RC_PAYLOAD_INVALID, "须为 -014 build_report() 产物（含 sections）"
        )
    period = report.get("period") or {}
    title = (
        f"司马懿汇报 {period.get('month')}（{len(period.get('combo_ids') or [])} 组合）"
    )
    lines = [f"归月 {period.get('month')}｜生成 {report.get('generated_at')}"]
    sec = report["sections"]
    if isinstance(sec, dict):
        for key, title_txt in (
            ("research", "本期研究"),
            ("simulation", "模拟达到"),
            ("branches", "分支状态"),
            ("cost_budget", "成本预算"),
            ("alerts", "红黄绿告警"),
        ):
            s = sec.get(key) or {}
            if s.get("available"):
                lines.append(f"- {title_txt}：{s.get('status')}（详版见归档报告）")
            else:
                lines.append(f"- {title_txt}：{s.get('reason') or '未接入'}")
    return {
        "title": title,
        "text": "\n".join([title, *lines]),
        "template": template,
        "risk_notice": risk_notice
        or ("本内容为内部模拟汇报，不含实盘，不构成任何投资建议"),
    }


def report_fingerprint(report: dict) -> str:
    """报告幂等身份：生成时刻 + 归月 + 组合集的 sha256 前 16 位（**不改报告字段**）。"""
    period = report.get("period") or {}
    return _sha256_16(
        report.get("generated_at"),
        period.get("month"),
        ",".join(period.get("combo_ids") or []),
    )


def deliver_report(
    report: dict,
    *,
    target_id: str = "owner.laotian",
    template: str = TEMPLATE_REPORT,
    kind: str = "daily",
    dry_run: bool | None = None,
    now: datetime | None = None,
    targets: dict | None = None,
    entitlement_log=None,
    delivery_log=None,
    provider: EntitlementProvider | None = None,
    env=None,
    cred_file: Path | None = None,
    risk_notice: str | None = None,
) -> dict:
    """把 -014 报告投出去，**并把投递回执写进 `report["outlet"]`**。

    只写 outlet 的投递字段（pushed / reason_code / target 域 / 幂等键 / 证据 /
    闸门判据），**不碰五段 `sections` 与任何口径字段**——-014 五段口径零改动。
    返回投递回执（同写入 outlet 的那份）。
    """
    target = resolve_target(target_id, targets)
    payload = build_payload(report, template=template, risk_notice=risk_notice)
    receipt = send(
        payload,
        target,
        template=template,
        kind=kind,
        subject=report.get("report_id") or target_id,
        dry_run=dry_run,
        now=now,
        report_id=report_fingerprint(report),
        targets=targets,
        entitlement_log=entitlement_log,
        delivery_log=delivery_log,
        provider=provider,
        env=env,
        cred_file=cred_file,
    )
    outlet = report.setdefault("outlet", {})
    outlet.update(
        {
            "pushed": receipt["pushed"],
            "reason_code": receipt["reason_code"],
            "target": receipt["target_id"],
            "target_domain": receipt["domain"],
            "template": receipt["template"],
            "kind": receipt["kind"],
            "mode": receipt["mode"],
            "idempotency_key": receipt["idempotency_key"],
            "evidence": receipt["evidence"],
            "critical": receipt["critical"],
            "quiet_window": receipt["quiet_window"],
            "rate_used": receipt["rate_used"],
            "rate_limit": receipt["rate_limit"],
            "gate": receipt["gate"],
            "credential_state": receipt["credential_state"],
            "push_module": "feishu_push(WO-QT-20261020-022)",
            "push_ticket": "WO-QT-20261020-022",
            "domain_gate": "owner 直通 / customer 订阅闸门强制（未接线→拒）",
            "notice": (
                "DRYRUN — 未推送真实出口（015 通道已就位，待 live 三重门禁）"
                if not receipt["pushed"]
                else "已投递（证据分级见 evidence）"
            ),
        }
    )
    return receipt
