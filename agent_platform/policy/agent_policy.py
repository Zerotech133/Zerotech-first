# -*- coding: utf-8 -*-
"""Agent 工具调用运行时策略引擎 v0（P0 治理，2026-11-01 · WO-AGENT-POLICY-V0-20261101）。

背景：平台已有三份「只读策略声明」——`agent_platform/rules/policy.json`
（schema agent_policy/v1，L0-L3 审批级 + 默认 deny）、`tool_matrix.json`
（岗×操作白名单）、`sandbox_network_policy.json`（网络默认 deny + 精确
host:port 白名单），但**没有可复用的「一次工具调用是否放行」判定函数**：
执行点（OH/LDE/serve）各自零散判断，口径容易漂。本模块把判定收敛成纯函数
决策引擎 v0，供后续执行点接入（本单不接线、不改 OH/LDE/serve/生产配置）。

默认拒绝、显式放行（与 policy.json / tool_matrix / 网络策略同口径）：
  决策优先级（从高到低）：
    1. 工具名缺失/非字符串、args 非 dict → DENY（invalid_tool / invalid_args）
    2. max_args 超限 → DENY（too_many_args）
    3. known_tools 已声明且工具不命中 → DENY（unknown_tool）
    4. denied_tools 命中 → DENY（deny 永远赢；rule_id=deny:<pattern>）
    5. 显式 requires_approval_tools 命中 → ALLOW + requires_approval=True
       （rule_id=approval:<pattern>；策略显式声明「此类工具可执行但须人工审批」）
    6. allowed_tools 命中 → ALLOW；若工具同时命中内置危险模式
       （rm/shutdown/publish/delete/deploy 等，DEFAULT_APPROVAL_TOOLS）则自动
       升级为 requires_approval=True（只提级、不放行：未放行的危险工具仍 DENY）
    7. 其余 → DENY（default_deny：未登记即拒绝，fail-visible）
  ALLOW（含待审批）后继续做参数级校验，任一不过即 DENY（更安全，不进入审批）：
    - 路径参数（默认 path/file/src/dst/cwd 等键，可由 path_arg_keys 覆盖）：
      只接受 worktree 内相对路径；拒绝绝对路径（`/`、`C:`、UNC）、`..` 上穿、
      NUL；反斜杠归一为 `/` 后必须落在 path_roots 内。未声明 path_roots 时
      路径参数一律 DENY（path_roots_missing）。
    - 网络工具（network_tools 命中，默认 http*/fetch*/web_*/...）：network_egress
      为 deny（缺省）时必须声明 allowed_hosts，且从 host/hostname/url/uri/endpoint
      提取的 host[:port] 必须**精确命中**（禁通配，与 sandbox_network_policy 同口径）；
      network_egress=allow 时跳过 host 校验。
  context 为可选归因上下文（dict：agent/session 等），只影响 reason 文案、不影响决策。

纯函数 / 无 IO：
  - `policy_from_dict(data)` 编译策略；`load_policy(path)` 仅读 YAML（lazy import
    PyYAML，未安装时提示改用 dict）；
  - `evaluate(policy, tool, args, context)` / `AgentPolicy.evaluate(tool, args, context)`
    只做内存计算：不读文件、不 resolve 真实路径、不访问网络——路径安全靠**词法**
    规范化 + 白名单根，不依赖真实文件系统状态。

数据结构：
  PolicyRule(kind, pattern, reason, source)：编译后的单条规则（deny/approval/allow）
  Decision(allowed, reason, requires_approval, rule_id)：frozen dataclass，
    `as_dict()` 可 JSON 序列化，`bool(decision) == decision.allowed`
  AgentPolicy：编译后的策略（rules/path_roots/network_egress/allowed_hosts/
    max_args/network_tools/path_arg_keys/known_tools）

YAML 策略示例（字段均可选；缺省 = 空策略 = 全部拒绝）：
  schema: agent_policy/v1
  allowed_tools: ["read_file", "git_*"]
  denied_tools: ["shell_exec"]
  requires_approval_tools: ["publish"]
  path_roots: ["agent_platform", "docs"]
  network_egress: deny
  allowed_hosts: ["pypi.org:443", "files.pythonhosted.org:443"]
  max_args: 8
  known_tools: ["read_file", "git_*", "rm", "shutdown", "publish", "http_get"]

后续接入建议（本单不接线，属另一单）：
  1. OH/LDE/serve 的工具执行入口：`decision = policy.evaluate(tool, args, ctx)`；
     `not decision.allowed` → 拒绝执行并回传 reason/rule_id；
     `decision.requires_approval` → 走审批通道（K-STOCK-416 五要素），批准后再执行；
  2. 策略文件放 `agent_platform/rules/agent_policy.yaml`（或 policy.json 迁移），
     按 execution point 拆分；启动时 load 一次缓存，配置热加载另立；
  3. 可观测：每次 DENY/审批落 dev_events（code_action/guard_fail），rule_id 作为
     归因键；observe 期只报不拦（沿 tool_matrix observe→enforce 路径）；
  4. 与 policy.json/tool_matrix 的一致性校验工具（本模块只消费 dict/YAML，不读
     多份配置合并——单源迁移期建议先由接线单做「策略编译」再传入）。
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit

SCHEMA = "agent_policy/v1"
POLICY_KINDS = ("deny", "approval", "allow")
EGRESS_MODES = ("deny", "allow")

# 内置危险工具：**不放行、只提级**——工具经 allowed_tools 放行后，若命中这些模式
# 自动升级为「需人工审批」（K-STOCK-416 L2 口径）；未放行者仍照常 DENY。
DEFAULT_APPROVAL_TOOLS = (
    "rm", "rm_*", "delete", "delete_*", "shutdown", "shutdown_*",
    "publish", "publish_*", "deploy", "deploy_*",
)
# 默认网络工具模式（policy 可用 network_tools 覆盖）。
DEFAULT_NETWORK_TOOLS = (
    "http*", "fetch*", "web_*", "request*", "download*", "upload*",
    "net_*", "url_*", "curl", "wget",
)
# 视为「路径参数」的键（policy 可用 path_arg_keys 覆盖）。
DEFAULT_PATH_ARG_KEYS = frozenset({
    "path", "paths", "file", "files", "filename", "src", "source",
    "dst", "dest", "target", "dir", "directory", "cwd", "root",
})
# 视为「网络目标」的键（提取 host[:port]）。
NETWORK_HOST_KEYS = ("host", "hostname", "url", "uri", "endpoint")

# 配置里允许出现的顶层元数据/字段（未知字段直接报错，防拼写漂移）。
_META_KEYS = {"schema", "_note", "description", "updated", "version"}
_FIELD_KEYS = {
    "allowed_tools", "denied_tools", "requires_approval_tools", "known_tools",
    "path_roots", "path_arg_keys", "network_egress", "allowed_hosts",
    "max_args", "network_tools",
}
_KNOWN_KEYS = _META_KEYS | _FIELD_KEYS
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_HOST_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.\-]{0,253})$")


class PolicyError(ValueError):
    """策略配置非法 / 读取失败（`code` 为机读错误码）。"""

    def __init__(self, message: str, code: str = "bad_policy"):
        super().__init__(message)
        self.code = code


# ----------------------------------------------------------------------
# 数据结构
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class PolicyRule:
    """编译后的单条策略规则（pattern 为 fnmatch 通配）。"""

    kind: str  # deny / approval / allow
    pattern: str
    reason: str = ""
    source: str = ""

    def matches(self, tool: str) -> bool:
        return bool(tool) and fnmatch.fnmatchcase(str(tool), self.pattern)


@dataclass(frozen=True)
class Decision:
    """一次工具调用的策略决策。"""

    allowed: bool
    reason: str
    requires_approval: bool = False
    rule_id: str = ""

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "requires_approval": self.requires_approval,
            "rule_id": self.rule_id,
        }

    def __bool__(self) -> bool:
        return bool(self.allowed)


@dataclass(frozen=True)
class AgentPolicy:
    """编译后的 Agent 工具调用策略（不可变；evaluate 为纯函数）。"""

    rules: tuple[PolicyRule, ...] = ()
    path_roots: tuple[str, ...] = ()
    network_egress: str = "deny"
    allowed_hosts: tuple[str, ...] = ()
    max_args: int | None = None
    network_tools: tuple[str, ...] = DEFAULT_NETWORK_TOOLS
    path_arg_keys: frozenset[str] = DEFAULT_PATH_ARG_KEYS
    known_tools: tuple[str, ...] = ()
    danger_tools: tuple[str, ...] = DEFAULT_APPROVAL_TOOLS
    schema: str = SCHEMA

    # -- 便捷属性 ------------------------------------------------------
    @property
    def allowed_tools(self) -> tuple[str, ...]:
        return tuple(rule.pattern for rule in self.rules if rule.kind == "allow")

    @property
    def denied_tools(self) -> tuple[str, ...]:
        return tuple(rule.pattern for rule in self.rules if rule.kind == "deny")

    @property
    def approval_tools(self) -> tuple[str, ...]:
        return tuple(rule.pattern for rule in self.rules if rule.kind == "approval")

    # -- 决策入口 ------------------------------------------------------
    def evaluate(self, tool, args=None, context=None) -> Decision:
        return evaluate(self, tool, args, context)

    def allows(self, tool, args=None, context=None) -> bool:
        return self.evaluate(tool, args, context).allowed

    def as_dict(self) -> dict:
        return {
            "schema": self.schema,
            "rules": [{"kind": rule.kind, "pattern": rule.pattern,
                       "reason": rule.reason, "source": rule.source}
                      for rule in self.rules],
            "path_roots": list(self.path_roots),
            "network_egress": self.network_egress,
            "allowed_hosts": list(self.allowed_hosts),
            "max_args": self.max_args,
            "network_tools": list(self.network_tools),
            "path_arg_keys": sorted(self.path_arg_keys),
            "known_tools": list(self.known_tools),
            "danger_tools": list(self.danger_tools),
        }


# ----------------------------------------------------------------------
# 配置解析（dict → AgentPolicy）
# ----------------------------------------------------------------------
def _pattern_list(value, field_name: str) -> tuple[str, ...]:
    """校验「模式列表」字段：list[str]，非空、去重保序（允许 "*"）。"""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise PolicyError("%s 必须是字符串数组，实际 %s"
                          % (field_name, type(value).__name__), "bad_field_type")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise PolicyError("%s 含非字符串项：%r" % (field_name, item), "bad_field_type")
        pattern = item.strip()
        if not pattern:
            raise PolicyError("%s 含空模式" % field_name, "bad_field_type")
        if pattern not in out:
            out.append(pattern)
    return tuple(out)


def _validate_path_root(item) -> str:
    if not isinstance(item, str):
        raise PolicyError("path_roots 含非字符串项：%r" % (item,), "bad_field_type")
    raw = item.strip().replace("\\", "/")
    if not raw:
        raise PolicyError("path_roots 含空项", "bad_field_type")
    if raw == "*":
        return "*"
    if "*" in raw or "?" in raw:
        raise PolicyError("path_roots 只支持精确根或 \"*\"（禁通配）：%r" % item,
                          "bad_path_roots")
    if raw.startswith("/") or raw.startswith("//") or _DRIVE_RE.match(raw):
        raise PolicyError("path_roots 必须是相对根（禁绝对路径）：%r" % item,
                          "bad_path_roots")
    normalized, code, detail = _normalize_rel_path(raw)
    if code:
        raise PolicyError("path_roots 非法：%s（%s）" % (item, detail), "bad_path_roots")
    return normalized


def _path_roots(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise PolicyError("path_roots 必须是字符串数组", "bad_field_type")
    out: list[str] = []
    for item in value:
        normalized = _validate_path_root(item)
        if normalized not in out:
            out.append(normalized)
    return tuple(out)


def _validate_allowed_host(item) -> str:
    """allowed_hosts 项：精确 host 或 host:port；禁协议/路径/通配（同沙箱网络策略）。"""
    if not isinstance(item, str):
        raise PolicyError("allowed_hosts 含非字符串项：%r" % (item,), "bad_field_type")
    text = item.strip().lower()
    if not text:
        raise PolicyError("allowed_hosts 含空项", "bad_allowed_hosts")
    if "://" in text or "/" in text or "*" in text or "?" in text:
        raise PolicyError("allowed_hosts 必须精确 host[:port]（禁协议/路径/通配）：%r" % item,
                          "bad_allowed_hosts")
    if text.count(":") > 1:
        raise PolicyError("allowed_hosts 暂不支持 IPv6（v0）：%r" % item, "bad_allowed_hosts")
    host, port_text = (text.rsplit(":", 1) if ":" in text else (text, ""))
    if ":" in text:
        if not port_text.isdigit():
            raise PolicyError("allowed_hosts 端口必须是整数：%r" % item, "bad_allowed_hosts")
        port = int(port_text)
        if not 1 <= port <= 65535:
            raise PolicyError("allowed_hosts 端口越界：%r" % item, "bad_allowed_hosts")
    if not _HOST_LABEL_RE.match(host):
        raise PolicyError("allowed_hosts host 非法：%r" % item, "bad_allowed_hosts")
    return text


def _allowed_hosts(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise PolicyError("allowed_hosts 必须是字符串数组", "bad_field_type")
    out: list[str] = []
    for item in value:
        host = _validate_allowed_host(item)
        if host not in out:
            out.append(host)
    return tuple(out)


def _path_arg_keys(value) -> frozenset[str]:
    if value is None:
        return DEFAULT_PATH_ARG_KEYS
    if not isinstance(value, (list, tuple)):
        raise PolicyError("path_arg_keys 必须是字符串数组", "bad_field_type")
    keys: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise PolicyError("path_arg_keys 含非法项：%r" % (item,), "bad_field_type")
        keys.append(item.strip())
    return frozenset(keys)


def _network_tools(value) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_NETWORK_TOOLS
    patterns = _pattern_list(value, "network_tools")
    if not patterns:
        raise PolicyError("network_tools 不能为空数组（如需禁用网络判定请删掉该字段）",
                          "bad_field_type")
    return patterns


def _max_args(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise PolicyError("max_args 必须是整数", "bad_field_type")
    if value < 0:
        raise PolicyError("max_args 不能为负", "bad_field_type")
    return int(value)


def policy_from_dict(data) -> AgentPolicy:
    """dict → AgentPolicy（空 dict/None = 空策略 = 全部拒绝）。

    未知字段直接 PolicyError（防配置拼写漂移）；各字段类型/取值严格校验。
    """
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise PolicyError("策略顶层必须是对象（dict），实际 %s" % type(data).__name__,
                          "bad_policy_root")
    unknown = sorted(str(key) for key in data if key not in _KNOWN_KEYS)
    if unknown:
        raise PolicyError("策略含未知字段（防拼写漂移）：%s" % ", ".join(unknown),
                          "unknown_field")
    schema = data.get("schema") or SCHEMA
    if schema != SCHEMA:
        raise PolicyError("schema 必须是 %r，实际 %r" % (SCHEMA, schema), "bad_schema")
    egress = str(data.get("network_egress") or "deny").strip().lower()
    if egress not in EGRESS_MODES:
        raise PolicyError("network_egress 只能是 deny/allow，实际 %r" % egress,
                          "bad_network_egress")

    denied = _pattern_list(data.get("denied_tools"), "denied_tools")
    approval = _pattern_list(data.get("requires_approval_tools"), "requires_approval_tools")
    allowed = _pattern_list(data.get("allowed_tools"), "allowed_tools")
    known = _pattern_list(data.get("known_tools"), "known_tools")

    rules: list[PolicyRule] = []
    for pattern in denied:
        rules.append(PolicyRule("deny", pattern, "denied_tools 命中（显式拒绝优先）",
                                "denied_tools"))
    for pattern in approval:
        rules.append(PolicyRule("approval", pattern,
                                "显式声明需人工审批（K-STOCK-416 L2）",
                                "requires_approval_tools"))
    for pattern in allowed:
        rules.append(PolicyRule("allow", pattern, "allowed_tools 命中", "allowed_tools"))
    return AgentPolicy(
        rules=tuple(rules),
        path_roots=_path_roots(data.get("path_roots")),
        network_egress=egress,
        allowed_hosts=_allowed_hosts(data.get("allowed_hosts")),
        max_args=_max_args(data.get("max_args")),
        network_tools=_network_tools(data.get("network_tools")),
        path_arg_keys=_path_arg_keys(data.get("path_arg_keys")),
        known_tools=known,
        danger_tools=DEFAULT_APPROVAL_TOOLS,
        schema=SCHEMA,
    )


# ----------------------------------------------------------------------
# YAML 加载（唯一 IO：只读）
# ----------------------------------------------------------------------
def load_policy(path) -> AgentPolicy:
    """读取 YAML 策略文件 → AgentPolicy（只读；PyYAML lazy import）。"""
    try:
        import yaml  # noqa: PLC0415 —— 可选依赖：纯函数用法不需要 PyYAML
    except ImportError:
        raise PolicyError("PyYAML 未安装，无法读取 YAML 策略；"
                          "可改用 policy_from_dict(dict)", "yaml_missing")
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise PolicyError("策略文件不可读：%s（%s）" % (file_path, exc), "policy_unreadable")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyError("策略 YAML 非法：%s（%s）" % (file_path, str(exc)[:300]), "bad_yaml")
    return policy_from_dict(data)


# ----------------------------------------------------------------------
# 路径安全（词法规范化，不碰文件系统）
# ----------------------------------------------------------------------
def _normalize_rel_path(value) -> tuple[str | None, str, str]:
    """相对路径词法规范化；返回 (normalized, error_code, detail)。"""
    if not isinstance(value, str):
        return None, "invalid_path_arg", "路径参数必须是字符串，实际 %s" % type(value).__name__
    text = value.strip()
    if not text:
        return None, "empty_path", "路径参数为空"
    if "\x00" in text:
        return None, "invalid_path_arg", "路径含 NUL 字符"
    text = text.replace("\\", "/")
    if text.startswith("/") or text.startswith("//") or _DRIVE_RE.match(text):
        return None, "path_absolute", "拒绝绝对路径：%r" % value
    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None, "path_escape", "路径 `..` 上穿根目录：%r" % value
            parts.pop()
            continue
        parts.append(part)
    if not parts:
        return None, "empty_path", "路径规范化后为空：%r" % value
    return "/".join(parts), "", ""


def normalize_rel_path(value) -> str:
    """公开辅助：返回词法规范化后的相对路径；非法抛 PolicyError（code 见上）。"""
    normalized, code, detail = _normalize_rel_path(value)
    if code:
        raise PolicyError(detail, code)
    return str(normalized)


def _under_roots(path: str, roots: Sequence[str]) -> bool:
    for root in roots:
        if root == "*":
            return True
        if path == root or path.startswith(root + "/"):
            return True
    return False


def _check_paths(policy: AgentPolicy, args: dict) -> Decision | None:
    """校验路径参数；无风险返回 None，否则返回 DENY Decision。"""
    found = [(key, args[key]) for key in sorted(args) if key in policy.path_arg_keys]
    if not found:
        return None
    if not policy.path_roots:
        return _deny("path_roots_missing",
                     "路径参数 %s 需要 path_roots 声明（默认 deny）" % found[0][0])
    for key, value in found:
        values = value if isinstance(value, (list, tuple)) else [value]
        for item in values:
            normalized, code, detail = _normalize_rel_path(item)
            if code:
                return _deny(code, "参数 %s：%s" % (key, detail))
            if not _under_roots(str(normalized), policy.path_roots):
                return _deny("path_outside_roots",
                             "参数 %s=%r 不在 path_roots 内（%s）"
                             % (key, item, ", ".join(policy.path_roots)))
    return None


# ----------------------------------------------------------------------
# 网络出口（URL 解析用 stdlib，无网络 IO）
# ----------------------------------------------------------------------
def _is_network_tool(tool: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(tool, pattern) for pattern in patterns)


def _parse_host(value) -> tuple[str, int | None]:
    """从 host / host:port / URL 提取 (host, port)；非法抛 ValueError。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("host 参数不是非空字符串")
    text = value.strip()
    if "://" not in text:
        text = "//" + text
    parsed = urlsplit(text)
    if parsed.username or parsed.password:
        raise ValueError("URL 不允许内嵌凭据")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("无法从 %r 解析 host" % value)
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("端口非法：%r" % value)
    scheme = (parsed.scheme or "").lower()
    if port is None:
        if scheme == "https":
            port = 443
        elif scheme == "http":
            port = 80
    return host, port


def _host_allowed(host: str, port: int | None, allowed_hosts: Sequence[str]) -> bool:
    host = host.lower()
    for entry in allowed_hosts:
        if ":" in entry:
            entry_host, entry_port = entry.rsplit(":", 1)
            if entry_host == host and port is not None and int(entry_port) == port:
                return True
        elif entry == host:
            return True
    return False


def _check_network(policy: AgentPolicy, args: dict) -> Decision | None:
    """校验网络出口（仅对网络工具）；无风险返回 None，否则返回 DENY Decision。"""
    if policy.network_egress == "allow":
        return None  # 策略显式放行出网（不校验 host）
    if not policy.allowed_hosts:
        return _deny("network_denied",
                     "network_egress=deny 且未声明 allowed_hosts（网络默认拒绝）")
    host_args = [(key, args[key]) for key in args if key in NETWORK_HOST_KEYS]
    if not host_args:
        return _deny("network_host_missing",
                     "网络工具缺少 host/url 参数，无法校验出口白名单")
    for key, value in host_args:
        values = value if isinstance(value, (list, tuple)) else [value]
        for item in values:
            try:
                host, port = _parse_host(item)
            except ValueError as exc:
                return _deny("network_url_invalid", "参数 %s：%s" % (key, exc))
            if not _host_allowed(host, port, policy.allowed_hosts):
                target = host if port is None else "%s:%d" % (host, port)
                return _deny("network_host_not_allowed",
                             "目标 %s 不在 allowed_hosts（%s）"
                             % (target, ", ".join(policy.allowed_hosts)))
    return None


# ----------------------------------------------------------------------
# 决策核心（纯函数）
# ----------------------------------------------------------------------
def _deny(rule_id: str, reason: str) -> Decision:
    return Decision(allowed=False, reason=reason, requires_approval=False, rule_id=rule_id)


def _first_match(policy: AgentPolicy, tool: str, kind: str) -> PolicyRule | None:
    for rule in policy.rules:
        if rule.kind == kind and rule.matches(tool):
            return rule
    return None


def _context_suffix(context: dict | None) -> str:
    if not isinstance(context, dict):
        return ""
    agent = str(context.get("agent") or "").strip()
    session = str(context.get("session") or "").strip()
    bits = []
    if agent:
        bits.append("agent=%s" % agent)
    if session:
        bits.append("session=%s" % session)
    return "（%s）" % "，".join(bits) if bits else ""


def evaluate(policy: AgentPolicy, tool, args=None, context=None) -> Decision:
    """纯函数决策：返回 Decision（不抛配置/参数异常，非法输入一律 DENY）。

    policy 必须是 AgentPolicy（API 误用抛 TypeError，属编程错误而非运行期输入）。
    """
    if not isinstance(policy, AgentPolicy):
        raise TypeError("policy 必须是 AgentPolicy，实际 %s" % type(policy).__name__)
    name = tool.strip() if isinstance(tool, str) else ""
    if not name:
        return _deny("invalid_tool", "工具名缺失或非字符串")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return _deny("invalid_args", "args 必须是 dict，实际 %s" % type(args).__name__)
    if context is not None and not isinstance(context, dict):
        return _deny("invalid_context", "context 必须是 dict 或 None")
    if policy.max_args is not None and len(args) > policy.max_args:
        return _deny("too_many_args",
                     "参数个数 %d 超过 max_args=%d" % (len(args), policy.max_args))
    if policy.known_tools and not any(
            fnmatch.fnmatchcase(name, pattern) for pattern in policy.known_tools):
        return _deny("unknown_tool", "未知工具（不在 known_tools）：%s" % name)

    suffix = _context_suffix(context)
    deny_rule = _first_match(policy, name, "deny")
    if deny_rule is not None:
        return _deny("deny:%s" % deny_rule.pattern,
                     "%s（rule=%s）%s" % (deny_rule.reason, deny_rule.pattern, suffix))
    approval_rule = _first_match(policy, name, "approval")
    if approval_rule is not None:
        # 策略显式声明的审批级工具：可执行，但必须人工审批后才执行。
        decision = Decision(
            allowed=True,
            reason="策略显式要求人工审批后方可执行：%s（rule=%s）%s"
                   % (approval_rule.reason, approval_rule.pattern, suffix),
            requires_approval=True,
            rule_id="approval:%s" % approval_rule.pattern,
        )
    else:
        allow_rule = _first_match(policy, name, "allow")
        if allow_rule is None:
            return _deny("default_deny",
                         "工具不在 allowed_tools 白名单（未登记即拒绝）：%s%s"
                         % (name, suffix))
        danger_pattern = next(
            (pattern for pattern in policy.danger_tools
             if fnmatch.fnmatchcase(name, pattern)), "")
        if danger_pattern:
            # 内置危险工具：只提级、不放行——先过白名单，再要求人工审批。
            decision = Decision(
                allowed=True,
                reason="危险工具需人工审批（内置提级）：%s（rule=%s）%s"
                       % (allow_rule.reason, danger_pattern, suffix),
                requires_approval=True,
                rule_id="approval:%s" % danger_pattern,
            )
        else:
            decision = Decision(
                allowed=True,
                reason="%s（rule=%s）%s" % (allow_rule.reason, allow_rule.pattern, suffix),
                requires_approval=False,
                rule_id="allow:%s" % allow_rule.pattern,
            )

    path_decision = _check_paths(policy, args)
    if path_decision is not None:
        return path_decision
    if _is_network_tool(name, policy.network_tools):
        network_decision = _check_network(policy, args)
        if network_decision is not None:
            return network_decision
    return decision


# 常用别名：便于执行点按 `policy.evaluate(...)` / `agent_policy.decide(...)` 调用。
decide = evaluate
