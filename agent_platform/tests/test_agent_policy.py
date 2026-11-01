# -*- coding: utf-8 -*-
"""agent_policy 单测（P0 Agent 工具调用运行时策略引擎 v0 · WO-AGENT-POLICY-V0-20261101）。

覆盖：
- 默认拒绝：空策略/未知工具/未白名单工具全 DENY；allow 精确与 fnmatch 通配；
  deny 优先于 allow/审批；known_tools 未知工具拒绝；
- 审批：显式 requires_approval_tools 放行+审批；内置危险工具（rm/shutdown/publish）
  只提级不放行（未白名单仍 DENY）；deny 压过审批；普通工具不误标审批；
- 路径安全：path_roots 内放行；`..` 上穿/目录逃逸/绝对路径（/、C:、UNC）/NUL/
  非法类型全 DENY；反斜杠归一；列表参数；path_roots 缺失默认拒绝；"*" 根；
  path_arg_keys 自定义；normalize_rel_path 公开辅助；
- 网络：默认 deny 无 allowlist 拒绝；host:port / host-only 精确匹配；端口不匹配拒绝；
  egress=allow 跳过；缺 host 参数拒绝；URL 凭据/坏端口/非字符串拒绝；
  自定义 network_tools；allowlist 配置校验（禁通配/协议/路径）；
- max_args；YAML 加载（成功/坏语法/非 dict/缺失文件/空文件/PyYAML 缺失/未知字段）；
  配置坏（类型/取值/schema/根非 dict）；Decision/PolicyRule 数据结构；
  纯函数无 IO（evaluate 不触发 Path.read_text）；API 误用 TypeError。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]  # agent_platform
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from policy import agent_policy as P  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _policy(**over):
    data = {
        "allowed_tools": ["read_file", "git_*"],
        "denied_tools": ["shell_exec"],
        "requires_approval_tools": ["charge"],
        "path_roots": ["agent_platform", "docs"],
        "network_egress": "deny",
        "allowed_hosts": ["pypi.org:443"],
        "max_args": 8,
    }
    data.update(over)
    return P.policy_from_dict(data)


def _write_yaml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "agent_policy.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1) 默认拒绝 / 白名单
# ---------------------------------------------------------------------------
def test_empty_policy_denies_everything():
    policy = P.policy_from_dict({})
    for tool in ("read_file", "git_status", "rm", "shutdown", "publish", "http_get"):
        decision = policy.evaluate(tool)
        assert not decision.allowed, tool
        assert decision.rule_id == "default_deny", tool
        assert bool(decision) is False
    assert P.policy_from_dict(None).evaluate("anything").rule_id == "default_deny"


def test_allow_exact_and_pattern():
    policy = _policy()
    exact = policy.evaluate("read_file")
    assert exact.allowed and exact.rule_id == "allow:read_file"
    assert exact.requires_approval is False
    pattern = policy.evaluate("git_status")
    assert pattern.allowed and pattern.rule_id == "allow:git_*"


def test_unlisted_and_denied_tool_rejected():
    policy = _policy()
    denied = policy.evaluate("shell_exec")
    assert not denied.allowed and denied.rule_id == "deny:shell_exec"
    unlisted = policy.evaluate("teleport")
    assert not unlisted.allowed and unlisted.rule_id == "default_deny"


def test_deny_wins_over_allow():
    policy = _policy(allowed_tools=["*"], denied_tools=["rm"])
    assert policy.evaluate("read_file").allowed
    danger = policy.evaluate("rm")
    assert not danger.allowed and danger.rule_id == "deny:rm"


def test_known_tools_unknown_rejected():
    policy = _policy(known_tools=["read_file", "git_*", "shell_exec"])
    assert policy.evaluate("read_file").allowed
    unknown = policy.evaluate("teleport")
    assert not unknown.allowed and unknown.rule_id == "unknown_tool"
    # known 但被 deny 的工具走 deny 优先级
    assert policy.evaluate("shell_exec").rule_id == "deny:shell_exec"


def test_policy_rules_and_decision_dataclasses():
    policy = _policy()
    kinds = {(rule.kind, rule.pattern) for rule in policy.rules}
    assert ("deny", "shell_exec") in kinds
    assert ("approval", "charge") in kinds
    assert ("allow", "read_file") in kinds
    rule = next(rule for rule in policy.rules if rule.kind == "allow")
    assert rule.matches("read_file") and not rule.matches("write_file")
    decision = policy.evaluate("read_file")
    payload = decision.as_dict()
    assert payload["allowed"] is True and payload["requires_approval"] is False
    assert json.loads(json.dumps(payload))["rule_id"] == "allow:read_file"


# ---------------------------------------------------------------------------
# 2) 审批语义
# ---------------------------------------------------------------------------
def test_explicit_approval_tool_allowed_pending_approval():
    policy = _policy()
    decision = policy.evaluate("charge")
    assert decision.allowed is True
    assert decision.requires_approval is True
    assert decision.rule_id == "approval:charge"


def test_explicit_approval_pattern():
    policy = _policy(requires_approval_tools=["publish_*"])
    decision = policy.evaluate("publish_blog")
    assert decision.allowed and decision.requires_approval
    assert decision.rule_id == "approval:publish_*"


def test_builtin_danger_elevates_allowed_tool():
    """内置危险工具只提级：先被白名单放行后，才升级为「需审批」。"""
    policy = _policy(allowed_tools=["*"])
    for tool in ("rm", "shutdown", "publish", "delete_user", "deploy_web"):
        decision = policy.evaluate(tool)
        assert decision.allowed is True, tool
        assert decision.requires_approval is True, tool
        assert decision.rule_id.startswith("approval:"), tool


def test_builtin_danger_not_allowlisted_is_denied():
    policy = _policy()  # rm 不在 allowed_tools 里
    decision = policy.evaluate("rm")
    assert decision.allowed is False
    assert decision.requires_approval is False
    assert decision.rule_id == "default_deny"


def test_deny_overrides_approval():
    policy = _policy(denied_tools=["charge"], requires_approval_tools=["charge"])
    decision = policy.evaluate("charge")
    assert not decision.allowed and decision.rule_id == "deny:charge"


def test_normal_allow_not_marked_approval():
    policy = _policy(allowed_tools=["read_file"])
    assert policy.evaluate("read_file").requires_approval is False
    assert policy.evaluate("read_file").rule_id == "allow:read_file"


# ---------------------------------------------------------------------------
# 3) 路径安全
# ---------------------------------------------------------------------------
def test_path_inside_roots_allowed():
    policy = _policy()
    assert policy.evaluate("read_file", {"path": "agent_platform/policy/agent_policy.py"}).allowed
    assert policy.evaluate("read_file", {"path": "docs/README.md"}).allowed


def test_path_backslash_normalized():
    policy = _policy()
    decision = policy.evaluate("read_file", {"path": "agent_platform\\policy\\x.py"})
    assert decision.allowed, decision


def test_path_dotdot_escape_denied():
    policy = _policy()
    decision = policy.evaluate("read_file", {"path": "agent_platform/../.vault/x"})
    assert not decision.allowed
    assert decision.rule_id == "path_outside_roots"


def test_path_dotdot_above_root_denied():
    policy = _policy()
    decision = policy.evaluate("read_file", {"path": "../outside.txt"})
    assert not decision.allowed and decision.rule_id == "path_escape"


def test_path_absolute_denied():
    policy = _policy()
    for value in ("/etc/passwd", "C:\\vault\\secret.txt", "\\\\server\\share\\x",
                  "//server/share/x"):
        decision = policy.evaluate("read_file", {"path": value})
        assert not decision.allowed, value
        assert decision.rule_id == "path_absolute", value


def test_path_nul_rejected():
    policy = _policy()
    decision = policy.evaluate("read_file", {"path": "agent_platform/a\x00b"})
    assert not decision.allowed and decision.rule_id == "invalid_path_arg"


def test_path_roots_required_for_path_args():
    policy = _policy(path_roots=None, allowed_tools=["read_file"])
    with_path = policy.evaluate("read_file", {"path": "docs/x.md"})
    assert not with_path.allowed and with_path.rule_id == "path_roots_missing"
    # 无路径参数的工具不受影响
    assert policy.evaluate("read_file").allowed


def test_path_roots_star_allows_any_relative_path():
    policy = _policy(path_roots=["*"])
    assert policy.evaluate("read_file", {"path": "anything/else.txt"}).allowed
    escape = policy.evaluate("read_file", {"path": "../escape.txt"})
    assert not escape.allowed and escape.rule_id == "path_escape"


def test_path_list_args_validated():
    policy = _policy()
    assert policy.evaluate("read_file", {"paths": ["agent_platform/a.py", "docs/b.md"]}).allowed
    bad = policy.evaluate("read_file", {"paths": ["agent_platform/a.py", ".vault/b"]})
    assert not bad.allowed and bad.rule_id == "path_outside_roots"


def test_path_invalid_value_type_denied():
    policy = _policy()
    decision = policy.evaluate("read_file", {"path": 123})
    assert not decision.allowed and decision.rule_id == "invalid_path_arg"


def test_custom_path_arg_keys():
    policy = _policy(path_roots=["docs"], path_arg_keys=["dest"])
    assert policy.evaluate("read_file", {"dest": "docs/x.md"}).allowed
    # 自定义后其他键不再视为路径参数（口径由配置显式声明）
    assert policy.evaluate("read_file", {"path": "../outside"}).allowed


def test_normalize_rel_path_helper():
    assert P.normalize_rel_path("./agent_platform//a.py") == "agent_platform/a.py"
    assert P.normalize_rel_path("agent_platform\\policy\\x.py") == "agent_platform/policy/x.py"
    with pytest.raises(P.PolicyError) as exc_info:
        P.normalize_rel_path("../x")
    assert exc_info.value.code == "path_escape"
    with pytest.raises(P.PolicyError) as exc_info:
        P.normalize_rel_path("/abs/x")
    assert exc_info.value.code == "path_absolute"


# ---------------------------------------------------------------------------
# 4) 网络出口
# ---------------------------------------------------------------------------
def test_network_default_deny_without_allowlist():
    policy = _policy(allowed_tools=["*"], allowed_hosts=None)
    decision = policy.evaluate("http_get", {"url": "https://pypi.org/simple"})
    assert not decision.allowed and decision.rule_id == "network_denied"


def test_network_host_allowlist_match():
    policy = _policy(allowed_tools=["*"], allowed_hosts=["pypi.org:443"])
    ok = policy.evaluate("http_get", {"url": "https://pypi.org/simple/x"})
    assert ok.allowed, ok
    evil = policy.evaluate("http_get", {"url": "https://evil.example/x"})
    assert not evil.allowed and evil.rule_id == "network_host_not_allowed"
    host_only_entry = _policy(allowed_tools=["*"], allowed_hosts=["files.pythonhosted.org"])
    assert host_only_entry.evaluate(
        "http_get", {"host": "files.pythonhosted.org"}).allowed


def test_network_host_only_allowlist_ignores_port():
    policy = _policy(allowed_tools=["*"], allowed_hosts=["pypi.org"])
    # URL 默认端口 443 被 infer，但 host-only 条目不校验端口
    assert policy.evaluate("http_get", {"url": "https://pypi.org/x"}).allowed


def test_network_port_mismatch_denied():
    policy = _policy(allowed_tools=["*"], allowed_hosts=["pypi.org:443"])
    decision = policy.evaluate("http_get", {"url": "http://pypi.org/x"})  # port 80
    assert not decision.allowed and decision.rule_id == "network_host_not_allowed"
    explicit = policy.evaluate("http_get", {"host": "pypi.org:8443"})
    assert not explicit.allowed and explicit.rule_id == "network_host_not_allowed"


def test_network_egress_allow_skips_host_check():
    policy = _policy(allowed_tools=["*"], network_egress="allow", allowed_hosts=None)
    assert policy.evaluate("http_get", {"url": "https://any.example/x"}).allowed


def test_network_host_missing_denied():
    policy = _policy(allowed_tools=["*"])
    decision = policy.evaluate("http_get", {})
    assert not decision.allowed and decision.rule_id == "network_host_missing"


def test_network_invalid_target_denied():
    policy = _policy(allowed_tools=["*"])
    credentials = policy.evaluate(
        "http_get", {"url": "https://user:pass@pypi.org/x"})
    assert not credentials.allowed and credentials.rule_id == "network_url_invalid"
    not_string = policy.evaluate("http_get", {"host": 123})
    assert not not_string.allowed and not_string.rule_id == "network_url_invalid"


def test_custom_network_tools():
    policy = _policy(allowed_tools=["*"], network_tools=["fetch_*"])
    # http_get 不再被当作网络工具（自定义覆盖默认模式）
    assert policy.evaluate("http_get", {"url": "https://evil.example/x"}).allowed
    assert policy.evaluate("fetch_url", {"url": "https://evil.example/x"}).rule_id == \
        "network_host_not_allowed"


def test_allowed_hosts_config_validation():
    for hosts in (["*.pypi.org"], ["https://pypi.org"], ["pypi.org/path"],
                  ["pypi.org:abc"], ["pypi.org:70000"], [123]):
        with pytest.raises(P.PolicyError, match="allowed_hosts"):
            P.policy_from_dict({"allowed_hosts": hosts})


# ---------------------------------------------------------------------------
# 5) max_args / 参数形态
# ---------------------------------------------------------------------------
def test_max_args_exceeded():
    policy = _policy(max_args=2, allowed_tools=["read_file"], path_roots=None)
    assert policy.evaluate("read_file", {"a": 1, "b": 2}).allowed
    too_many = policy.evaluate("read_file", {"a": 1, "b": 2, "c": 3})
    assert not too_many.allowed and too_many.rule_id == "too_many_args"


def test_invalid_tool_and_args():
    policy = _policy()
    assert policy.evaluate("").rule_id == "invalid_tool"
    assert policy.evaluate(None).rule_id == "invalid_tool"
    assert policy.evaluate(123).rule_id == "invalid_tool"
    assert policy.evaluate("read_file", "not-dict").rule_id == "invalid_args"
    assert policy.evaluate("read_file", {}, 123).rule_id == "invalid_context"


def test_context_only_affects_reason():
    policy = _policy()
    plain = policy.evaluate("read_file")
    with_ctx = policy.evaluate("read_file", {}, {"agent": "DEV1", "session": "s1"})
    assert plain.allowed and with_ctx.allowed
    assert plain.rule_id == with_ctx.rule_id
    assert "agent=DEV1" in with_ctx.reason


def test_wrong_policy_type_raises_type_error():
    with pytest.raises(TypeError):
        P.evaluate({}, "read_file")


# ---------------------------------------------------------------------------
# 6) YAML 加载
# ---------------------------------------------------------------------------
def test_load_yaml_policy(tmp_path):
    path = _write_yaml(tmp_path, """
schema: agent_policy/v1
allowed_tools: ["read_file", "git_*", "http_*"]
denied_tools: ["shell_exec"]
requires_approval_tools: ["charge"]
path_roots: ["agent_platform", "docs"]
network_egress: deny
allowed_hosts: ["pypi.org:443"]
max_args: 4
""")
    policy = P.load_policy(path)
    assert policy.evaluate("read_file", {"path": "docs/a.md"}).allowed
    assert policy.evaluate("shell_exec").rule_id == "deny:shell_exec"
    assert policy.evaluate("charge").requires_approval is True
    assert policy.evaluate("http_get", {"url": "https://pypi.org/x"}).allowed
    assert policy.max_args == 4


def test_load_yaml_bad_syntax(tmp_path):
    path = _write_yaml(tmp_path, "allowed_tools: [unclosed\n")
    with pytest.raises(P.PolicyError) as exc_info:
        P.load_policy(path)
    assert exc_info.value.code == "bad_yaml"


def test_load_yaml_non_dict_root(tmp_path):
    path = _write_yaml(tmp_path, "- a\n- b\n")
    with pytest.raises(P.PolicyError) as exc_info:
        P.load_policy(path)
    assert exc_info.value.code == "bad_policy_root"


def test_load_yaml_missing_file(tmp_path):
    with pytest.raises(P.PolicyError) as exc_info:
        P.load_policy(tmp_path / "nope.yaml")
    assert exc_info.value.code == "policy_unreadable"


def test_load_empty_yaml_is_empty_policy(tmp_path):
    path = _write_yaml(tmp_path, "")
    policy = P.load_policy(path)
    assert not policy.evaluate("read_file").allowed


def test_load_yaml_unknown_field(tmp_path):
    path = _write_yaml(tmp_path, "allow_tools: [read_file]\n")
    with pytest.raises(P.PolicyError) as exc_info:
        P.load_policy(path)
    assert exc_info.value.code == "unknown_field"


def test_load_yaml_pyyaml_missing(monkeypatch, tmp_path):
    path = _write_yaml(tmp_path, "{}\n")
    monkeypatch.setitem(sys.modules, "yaml", None)  # 模拟未安装 PyYAML
    with pytest.raises(P.PolicyError) as exc_info:
        P.load_policy(path)
    assert exc_info.value.code == "yaml_missing"


# ---------------------------------------------------------------------------
# 7) 配置坏（类型/取值）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    {"allowed_tools": "read_file"},
    {"allowed_tools": [1]},
    {"allowed_tools": [""]},
    {"path_roots": "agent_platform"},
    {"path_roots": ["/abs"]},
    {"path_roots": ["../up"]},
    {"path_roots": ["agent_*"]},
    {"max_args": "8"},
    {"max_args": -1},
    {"max_args": True},
    {"network_egress": "maybe"},
    {"allowed_hosts": "pypi.org"},
    {"known_tools": [1]},
    {"path_arg_keys": "path"},
    {"network_tools": []},
    {"schema": "agent_policy/v2"},
    {"allow_tools": []},
])
def test_bad_config_rejected(bad):
    with pytest.raises(P.PolicyError):
        P.policy_from_dict(bad)


def test_non_dict_policy_rejected():
    for bad in ([], "x", 123):
        with pytest.raises(P.PolicyError) as exc_info:
            P.policy_from_dict(bad)
        assert exc_info.value.code == "bad_policy_root"


def test_metadata_keys_accepted():
    policy = P.policy_from_dict({
        "schema": P.SCHEMA,
        "_note": "备注", "description": "说明", "updated": "2026-11-01",
        "version": 1,
    })
    assert not policy.evaluate("read_file").allowed
    assert policy.schema == P.SCHEMA


# ---------------------------------------------------------------------------
# 8) 纯函数（evaluate 无 IO）
# ---------------------------------------------------------------------------
def test_evaluate_is_pure_no_filesystem_io(monkeypatch):
    policy = _policy()

    def _boom(*_args, **_kwargs):
        raise AssertionError("evaluate 不应访问文件系统")

    monkeypatch.setattr(Path, "read_text", _boom)
    monkeypatch.setattr(Path, "read_bytes", _boom)
    monkeypatch.setattr(Path, "resolve", _boom)
    monkeypatch.setattr(Path, "exists", _boom)
    monkeypatch.setattr(Path, "is_file", _boom)
    decision = policy.evaluate("read_file", {"path": "agent_platform/policy/x.py"})
    assert decision.allowed
    assert policy.evaluate("rm").allowed is False


def test_decide_alias_and_policy_allows():
    policy = _policy()
    assert P.decide(policy, "read_file").allowed
    assert policy.allows("read_file") is True
    assert policy.allows("teleport") is False
    dumped = policy.as_dict()
    assert dumped["schema"] == P.SCHEMA
    assert "rules" in dumped and "path_roots" in dumped
    assert dumped["network_egress"] == "deny"
    assert "rm" in dumped["danger_tools"]
    json.dumps(dumped)  # 可序列化
