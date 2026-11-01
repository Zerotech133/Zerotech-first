# -*- coding: utf-8 -*-
"""指挥部五角色适配器：田丰/诸葛亮/赵云/黄忠/关羽（2026-11-01 K-ORG-20261101-HQROLE）。

原则（专项计划 §3.1/§4 W-C1）：

- **只出意见，不执行**：适配器只构造/校验/序列化 RoleOpinion v1，返回 dict；
  不调模型、不联网、不写生产、不派单、不签字（`READ_ONLY=True` / `EXECUTES=False`）；
- **复用真源身份，不新造身份**：身份 = ``agent_platform/registry/org.json``
  （``command_hq.members`` + departments ``agent_profiles``）与
  ``agent_platform/registry/profile_seats.json``（ZHAOYUN 治理席档案，不占编）；
- **复用 engine/router 绑定**：声明绑定取 org/profile 档案的 ``llm_binding``；
  生效绑定优先取 ``agent_platform/engine/router.py`` 的 ``EXOTIC_ASSIGNMENTS``
  （如 AI1=诸葛亮 → openai/gpt-6.0），与 SmartRouter 同口径；
- **prompt 绑定**：优先引用 ``registry/prompt_versions.json`` 已登记文件（如
  tianfeng → agent_platform/prompts/a0_chief.md）；无专属 prompt 的角色用
  ``compose_prompt()`` 现拼（不改 prompts 主体、不新建 persona）；
- 输出：``RoleOpinion v1``（schema ``hq_role_opinion/v1``，见 role_contract）。

角色→注册身份映射（不新造）：TIANFENG=A0、ZHUGE=AI1、ZHAOYUN=ZHAOYUN、
HUANGZHONG=HUANGZHONG、GUANYU=GUANYU。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
try:  # 包内导入（agent_platform.command_hq.roles）
    from . import role_contract as rc
except ImportError:  # 文件方式加载（测试/单文件运行）
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))
    import role_contract as rc  # type: ignore  # noqa: F401

REPO = _HERE.parents[1]
REGISTRY_DIR = REPO / "agent_platform" / "registry"
ENGINE_ROUTER = REPO / "agent_platform" / "engine" / "router.py"
PROMPT_VERSIONS = REGISTRY_DIR / "prompt_versions.json"

READ_ONLY = True
EXECUTES = False
ROLE_ORDER = ("TIANFENG", "ZHUGE", "ZHAOYUN", "HUANGZHONG", "GUANYU")

# 角色静态说明（仅职责/边界/默认工具，不构成新身份；身份字段优先取 registry）
ROLE_SPECS = {
    "TIANFENG": {
        "adapter": "tianfeng", "seat_id": "A0", "cn_name": "田丰",
        "duty": "目标拆解、任务 DAG、派单、汇总、推进、最终执行编排",
        "never": ("不代替四签", "不代替老田 L2"),
        "tools": ("devtask", "events", "delegation"),
        "prompt_ref": "agent_platform/prompts/a0_chief.md",
    },
    "ZHUGE": {
        "adapter": "zhuge", "seat_id": "AI1", "cn_name": "诸葛亮",
        "duty": "反向质询、pre-mortem、找反例、挑战前提",
        "never": ("不拍板", "不写生产代码"),
        "tools": ("events", "datamart_read", "datamart_latest",
                  "data_health", "recent_events", "signal_precedents"),
        "prompt_ref": "",
    },
    "ZHAOYUN": {
        "adapter": "zhaoyun", "seat_id": "ZHAOYUN", "cn_name": "赵云",
        "duty": "独立证据复核、审计、经验归档、进化建议",
        "never": ("不做业务决策", "不改生产"),
        "tools": ("events", "datamart_read", "recent_events"),
        "prompt_ref": "",
    },
    "HUANGZHONG": {
        "adapter": "huangzhong", "seat_id": "HUANGZHONG", "cn_name": "黄忠",
        "duty": "验收标准、抽样、质量终检、建议放行/拒收",
        "never": ("不代签", "不修代码"),
        "tools": ("quality_gate", "test_baseline", "zhuge_redteam",
                  "huangzhong_accept", "trace"),
        "prompt_ref": "",
    },
    "GUANYU": {
        "adapter": "guanyu", "seat_id": "GUANYU", "cn_name": "关羽",
        "duty": "代码审计、技术债、门禁、整改建议",
        "never": ("不自行合并", "不碰生产"),
        "tools": ("quality_gate", "devtask", "events"),
        "prompt_ref": "",
    },
}
ADAPTER_KEYS = tuple(ROLE_SPECS[role]["adapter"] for role in ROLE_ORDER)
# AC 不可读时的声明绑定兜底（仅用于展示；真源仍为 org/profile_seats）
FALLBACK_BINDINGS = {
    "TIANFENG": "glm/glm-5.3",
    "ZHUGE": "openai/gpt-6.0",
    "ZHAOYUN": "deepseek/deepseek-v4-pro",
    "HUANGZHONG": "glm/glm-5.3",
    "GUANYU": "deepseek/deepseek-flash",
}


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def _walk_agent_profiles(node, out):
    """递归收集 org.json 中带 agent_id 的档案节点（departments/chief/...）。"""
    if isinstance(node, dict):
        agent_id = node.get("agent_id")
        if isinstance(agent_id, str) and agent_id.strip():
            out.append((node, node.get("_dept") or node.get("dept") or ""))
        for value in node.values():
            _walk_agent_profiles(value, out)
    elif isinstance(node, list):
        for value in node:
            _walk_agent_profiles(value, out)


def _merge_profile(dst, src):
    for key, value in src.items():
        if key in ("agent_id",):
            continue
        if key not in dst or dst[key] in (None, "", [], {}):
            dst[key] = value


def load_identity(registry_dir=None):
    """解析五角色身份真源（只读）。→ {role: {seat_id, cn_name, llm_binding, ...}} + issues。

    返回 ``{"roles": {...}, "command_hq": [...], "issues": [...], "source": path}``。
    """
    registry = Path(registry_dir or REGISTRY_DIR)
    org = _read_json(registry / "org.json")
    seats_doc = _read_json(registry / "profile_seats.json")
    issues = []
    members = []
    if isinstance(org, dict):
        hq = org.get("command_hq") or {}
        members = [str(x).strip() for x in (hq.get("members") or []) if str(x).strip()]
    nodes = []
    _walk_agent_profiles(org, nodes)
    profiles = {}
    for node, dept in nodes:
        agent_id = str(node.get("agent_id") or "").strip()
        merged = profiles.setdefault(agent_id, {})
        _merge_profile(merged, dict(node))
        if dept and not merged.get("dept"):
            merged["dept"] = dept
    for seat in (seats_doc.get("seats") or []) if isinstance(seats_doc, dict) else []:
        if not isinstance(seat, dict):
            continue
        agent_id = str(seat.get("agent_id") or "").strip()
        if not agent_id:
            continue
        merged = profiles.setdefault(agent_id, {})
        _merge_profile(merged, dict(seat))
        merged.setdefault("source_file", "profile_seats.json")
    roles = {}
    for role in ROLE_ORDER:
        spec = ROLE_SPECS[role]
        seat = spec["seat_id"]
        profile = profiles.get(seat, {})
        declared = str(profile.get("llm_binding") or "").strip()
        cn_name = str(profile.get("cn_name") or "").strip() or spec["cn_name"]
        tools = profile.get("tools") or spec["tools"]
        if isinstance(tools, dict):
            tools = list(tools.keys())
        source = "fallback"
        if declared:
            source = str(profile.get("source_file") or "org.json")
        if not declared:
            declared = FALLBACK_BINDINGS[role]
            issues.append("%s(%s): 档案缺 llm_binding，使用兜底展示绑定" % (role, seat))
        if not profile:
            issues.append("%s(%s): registry 无档案（使用角色静态说明）" % (role, seat))
        if members and seat not in members:
            issues.append("%s(%s): 不在 org.json command_hq.members" % (role, seat))
        fallbacks = profile.get("llm_fallback") or []
        if isinstance(fallbacks, str):
            fallbacks = [fallbacks]
        roles[role] = {
            "role": role,
            "adapter": spec["adapter"],
            "seat_id": seat,
            "cn_name": cn_name,
            "duty": spec["duty"],
            "never": list(spec["never"]),
            "role_profile": str(profile.get("role_profile") or "").strip(),
            "dept": str(profile.get("dept") or "").strip(),
            "grade": str(profile.get("grade") or "").strip(),
            "llm_binding": declared,
            "llm_fallbacks": [str(x) for x in fallbacks if str(x).strip()],
            "tools": [str(x) for x in tools],
            "tools_allowed": profile.get("attributes", {}).get("tools_allowed")
            if isinstance(profile.get("attributes"), dict) else None,
            "binding_note": str(profile.get("binding_note") or "").strip(),
            "in_command_hq": (not members) or (seat in members),
            "source": source,
            "prompt_ref": spec["prompt_ref"],
        }
    return {"roles": roles, "command_hq": members, "issues": issues,
            "source": str(registry)}


def load_router_bindings(root=None):
    """读取 engine/router.py 的 EXOTIC_ASSIGNMENTS（生效覆盖绑定）；失败 → 空 + error。"""
    root = Path(root or REPO)
    path = root / "agent_platform" / "engine" / "router.py"
    if not path.is_file():
        return {}, "engine_router_missing:%s" % path
    try:
        sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location("hq_engine_router", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["hq_engine_router"] = module
        spec.loader.exec_module(module)
        overrides = getattr(module, "EXOTIC_ASSIGNMENTS", {}) or {}
        return {str(k).strip(): str(v).strip() for k, v in overrides.items()}, ""
    except Exception as exc:  # noqa: BLE001 - 只读绑定失败按 fallback 并显式记录
        return {}, "%s: %s" % (type(exc).__name__, str(exc)[:160])


def _prompt_registry_entry(registry_dir=None):
    doc = _read_json(Path(registry_dir or REGISTRY_DIR) / "prompt_versions.json")
    prompts = doc.get("prompts") if isinstance(doc, dict) else {}
    return prompts if isinstance(prompts, dict) else {}


def role_binding(role_key, *, root=None, registry_dir=None):
    """单个角色的完整绑定（身份 + 声明/生效模型 + 工具 + prompt，只读）。"""
    role = str(role_key or "").strip().upper()
    if role not in ROLE_SPECS:
        raise ValueError("未知角色 %r（可选 %s）" % (role_key, "/".join(ROLE_ORDER)))
    root = Path(root or REPO)
    identity = load_identity(registry_dir)
    info = dict(identity["roles"][role])
    overrides, router_error = load_router_bindings(root)
    declared = info["llm_binding"]
    effective = overrides.get(info["seat_id"]) or declared
    model_source = ("engine/router.EXOTIC_ASSIGNMENTS"
                    if overrides.get(info["seat_id"]) else info["source"])
    prompt_ref = info.get("prompt_ref") or ""
    prompt_meta = {}
    for key, meta in _prompt_registry_entry(registry_dir).items():
        if isinstance(meta, dict) and str(meta.get("file") or "") == prompt_ref:
            prompt_meta = {"registry_key": key, "version": meta.get("version"),
                           "last_date": meta.get("last_date")}
            break
    prompt_path = root / prompt_ref if prompt_ref else None
    info.update({
        "model": {"declared": declared, "effective": effective,
                  "source": model_source, "fallbacks": info["llm_fallbacks"],
                  "router_error": router_error},
        "prompt": {"ref": prompt_ref,
                   "exists": bool(prompt_path and prompt_path.is_file()),
                   "meta": prompt_meta},
        "read_only": READ_ONLY,
        "executes": EXECUTES,
    })
    return info


def all_role_bindings(*, root=None, registry_dir=None):
    """五角色绑定列表（顺序固定 TIANFENG..GUANYU）。"""
    return [role_binding(role, root=root, registry_dir=registry_dir)
            for role in ROLE_ORDER]


def compose_prompt(role_key, topic_id, topic="", *, context="", constraints=(),
                   round_no=1) -> str:
    """生成确定性角色提示（含职责/边界/契约/证据要求）；不调用模型、不写盘。"""
    binding = role_binding(role_key)
    lines = [
        "# 指挥部五角色意见征询（RoleOpinion v1 契约，只出意见不执行）",
        "角色：%s（%s / 注册身份 %s）" % (binding["cn_name"], binding["role"],
                                         binding["seat_id"]),
        "职责：%s" % binding["duty"],
        "边界：%s；只读意见，不执行、不签字、不改生产。" % "；".join(binding["never"]),
        "绑定：声明模型 %s；生效模型 %s（%s）" % (
            binding["model"]["declared"], binding["model"]["effective"],
            binding["model"]["source"]),
        "议题：%s" % (topic or topic_id),
        "topic_id：%s  |  round：%s" % (topic_id, round_no),
        "上下文：%s" % (context or "（无）"),
        "约束：%s" % ("；".join(constraints) if constraints else "（无）"),
        "",
        "输出必须是合法 %s JSON，字段：schema/topic_id/role(%s)/round/position"
        "(support|conditional|oppose|abstain)/recommendation/evidence[]/risks[]/"
        "conditions[]/escalate/escalate_reason/created_at。" % (rc.SCHEMA, binding["role"]),
        "证据要求：每条 evidence 必须有可复核 ref（kind=file/command/test/live/hash；"
        "hash 必带 sha256 digest）；无证据不得 verified=true。",
        "规则：oppose 必须给替代方案或不可接受原因；conditional 必须给可验收条件；"
        "escalate=true 必须给升级原因（由田丰出飞书卡，不内部消化）。",
    ]
    return "\n".join(lines)


class RoleAdapter:
    """单个角色的只读适配器：构造/校验 RoleOpinion，不调模型、不执行。"""

    def __init__(self, role_key, *, root=None, registry_dir=None):
        self.role = str(role_key or "").strip().upper()
        if self.role not in ROLE_SPECS:
            raise ValueError("未知角色 %r（可选 %s）" % (role_key, "/".join(ROLE_ORDER)))
        self.spec = dict(ROLE_SPECS[self.role])
        self.binding = role_binding(self.role, root=root, registry_dir=registry_dir)
        self.read_only = READ_ONLY
        self.executes = EXECUTES

    @property
    def adapter_key(self):
        return self.spec["adapter"]

    @property
    def seat_id(self):
        return self.spec["seat_id"]

    def compose_prompt(self, topic_id, topic="", **kwargs):
        return compose_prompt(self.role, topic_id, topic, **kwargs)

    def draft_opinion(self, topic_id, *, topic="", position="abstain",
                      recommendation="", evidence=(), risks=(), conditions=(),
                      escalate=False, escalate_reason="", round_no=1,
                      created_at=None):
        """构造并校验一份只读意见（无模型调用；证据不合规即 RoleOpinionError）。"""
        if not recommendation:
            recommendation = "（%s %s：待补充结论）" % (self.binding["cn_name"],
                                                       self.role)
        return rc.make_opinion(
            self.role, topic_id, position=position, recommendation=recommendation,
            evidence=evidence, risks=risks, conditions=conditions,
            escalate=escalate, escalate_reason=escalate_reason,
            round=round_no, created_at=created_at)


def adapters(*, root=None, registry_dir=None):
    """五角色适配器（按 ROLE_ORDER 顺序）。"""
    return {ROLE_SPECS[role]["adapter"]: RoleAdapter(role, root=root,
                                                     registry_dir=registry_dir)
            for role in ROLE_ORDER}


# 五角色便捷入口（与 ROLE_SPECS.adapter 同名）
def tianfeng(topic_id, **kwargs):
    return RoleAdapter("TIANFENG").draft_opinion(topic_id, **kwargs)


def zhuge(topic_id, **kwargs):
    return RoleAdapter("ZHUGE").draft_opinion(topic_id, **kwargs)


def zhaoyun(topic_id, **kwargs):
    return RoleAdapter("ZHAOYUN").draft_opinion(topic_id, **kwargs)


def huangzhong(topic_id, **kwargs):
    return RoleAdapter("HUANGZHONG").draft_opinion(topic_id, **kwargs)


def guanyu(topic_id, **kwargs):
    return RoleAdapter("GUANYU").draft_opinion(topic_id, **kwargs)
