# -*- coding: utf-8 -*-
r"""Phase 1-A：Composition IR（组合中间表示）+ 校验/编译器（只读）。

把「一条组合流水线」表达为可校验、可编译的类型化 IR：谁（owner/tenant）、
在什么运行模式（mode）下、由哪些节点（universe/strategy/filter/rank/allocator/
risk/execution/agent_team）按什么端口连成 DAG、带什么风控策略（risk_policy）、
发布口径（publish）如何。**只读**：编译产物是计划（CompiledPipeline），
不执行、不下单、不写 ledger、不发布、不改策略卡/registry。

设计红线（WO-COMPOSITION-IR-20261031，Phase 1-A）
------------------------------------------------
- 只读复用 Phase 0-A 的 Strategy Card（``strategy_cards.load_cards()``）：
  strategy 节点的 ``strategy_id`` 必须能在卡索引里找到，否则拒绝（closed-on-failure）；
  本模块**不修改** ``strategy_cards.py/.json``、``strategy_registry.json``、
  ``aggregator.py`` 或任何交易链文件，不新增计划任务、不重启服务。
- 不执行：``compile_ir`` 只做「校验 + 拓扑排序 + 解析卡片 + 产出计划」；
  CompiledPipeline 显式带 ``readonly=True`` / ``side_effects == []`` /
  ``safety.{executes,places_orders,writes_ledger,publishes} == False``。
- 严格闭合 schema：未知顶层字段 / 未知节点类型 / 未知端口 / 未知策略 /
  未知参数 / 版本不匹配一律报错（IRValidationError），不做静默降级。

IR schema（``composition_ir/v1``）
---------------------------------
::

    {
      "schema": "composition_ir/v1",           # 可选，缺省即该值；不匹配拒绝
      "pipeline_id": "pc-cn-shadow-001",       # 必填，标识符
      "version": "1.0.0",                      # 必填，x.y.z
      "owner": "quant-classic",                # 必填，非空
      "tenant": "official",                    # 必填，标识符（多租户隔离键，Phase 0-C）
      "mode": "SHADOW",                        # 必填，BACKTEST/REPLAY/SHADOW/SIM_MARKET
      "description": "...",                    # 可选
      "nodes": [                               # 必填，非空
        {"id": "u_cn",   "type": "universe",  "params": {"markets": ["CN"]}},
        {"id": "s_chan", "type": "strategy",  "strategy_id": "chan_monitor",
         "params": {}},
        {"id": "r_top",  "type": "rank",      "params": {"top_n": 30}},
        {"id": "x_sim",  "type": "execution", "params": {"book": "official",
                                                          "dry_run": true}}
      ],
      "edges": [                               # 必填，非空
        {"from": "u_cn",  "to": "s_chan", "port": "universe"},
        {"from": "s_chan","to": "r_top",  "port": "signals"},
        {"from": "r_top", "to": "x_sim",  "port": "ranking"}
      ],
      "risk_policy": {                         # 必填
        "policy_id": "rp-default",             # 必填
        "max_position_pct": 10.0,              # 必填，(0, 100]
        "max_positions": 30,                   # 可选，正整数
        "max_gross_exposure_pct": 100.0,       # 可选，正数
        "stop_loss_pct": 8.0,                  # 可选，(0, 100]
        "kill_switch": true,                   # 可选 bool
        "notes": "..."                         # 可选
      },
      "publish": {                             # 必填
        "enabled": false,                      # 必填 bool；本阶段只登记计划不发布
        "channel": "paper",                    # enabled=true 时必填
        "visibility": "private",               # 可选 private/tenant/public
        "requires_approval": true,             # 可选 bool
        "notes": "..."                         # 可选
      }
    }

节点类型与端口契约（``PORT_TYPES``）
------------------------------------
- universe：无输入，输出 ``universe``；入边禁止、出边必须。
- strategy：输入 ``universe``，输出 ``signals``；必须带 ``strategy_id``（卡内存在）。
- filter：输入/输出 ``signals``。
- rank：输入 ``signals``，输出 ``ranking``。
- allocator：输入 ``signals`` / ``ranking`` / ``advice``，输出 ``allocations``。
- risk：输入/输出 ``allocations``。
- execution：输入 ``allocations``，输出 ``orders``；终止节点；``dry_run`` 不得为 false。
- agent_team：输入 universe/signals/ranking/allocations/orders，输出 ``advice``；
  终止节点（可挂在任意层做观察，也可给 allocator 建议）。

边 ``{"from", "to", "port"}`` 的 ``port`` 是**上游输出端口名**；校验时按上游端口
类型匹配下游可接受输入端口（编译产物里补出 ``in_port`` / ``port_type``）。

校验项（``validate_ir``）
-------------------------
1) 顶层必填字段 / 未知字段 / 版本（``schema``、``version``）与取值域；
2) nodes 非空、id 唯一合法、节点类型合法、strategy_id 必须来自
   ``strategy_cards.load_cards()``、strategy 节点参数键/类型/枚举对卡校验；
3) edges 非空、from/to 存在、端口名合法、端口类型兼容、无自环/重复边；
4) 图结构：至少 1 universe + 1 strategy；universe 无入边/有出边；非 universe
   有入边；除终止节点外有出边；
5) DAG 无环（Kahn 拓扑排序，确定性：同层按字典序）；
6) risk_policy / publish 必填与取值域。

编译（``compile_ir``）
----------------------
拓扑排序 + 按节点解析 Strategy Card（版本/owner/modes/data_deps/capacity/
signal kind/params_schema + 参数默认值合并节点 override）→ ``CompiledPipeline``。
产物只读计划：``readonly=True``、``side_effects=[]``、safety 四开关全 false、
``execution.enabled=False``、``publish_plan.effective=False``（would_publish 记录意图）。

用法（只读）::

    from strategy_layer import composition_ir as ci

    cir = ci.validate_ir(doc)                 # -> CompositionIR；非法抛 IRValidationError
    plan = ci.compile_ir(doc)                 # -> CompiledPipeline（不执行任何副作用）
    plan.to_dict()                            # JSON 友好（只读计划）

命令行（closed-on-failure：非法 IR 退出码 1，输入/用法错误 2）::

    python -X utf8 strategy_layer/composition_ir.py --file ir.json
    python -X utf8 strategy_layer/composition_ir.py --file ir.json --json
    python -X utf8 strategy_layer/composition_ir.py --file ir.json --validate-only --json
    python -X utf8 strategy_layer/composition_ir.py --file ir.json \
        --cards-file strategy_layer/strategy_cards.json --json
"""

from __future__ import annotations

import copy
import heapq
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:  # 只读复用 Phase 0-A Strategy Card；脚本直跑时补 sys.path（同 strategy_health 判例）
    from strategy_layer import strategy_cards as _strategy_cards
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from strategy_layer import strategy_cards as _strategy_cards

HERE = os.path.dirname(os.path.abspath(__file__))
QUANT_ROOT = os.path.dirname(HERE)
DEFAULT_CARDS_PATH = _strategy_cards.DEFAULT_CARDS_PATH
DEFAULT_MANIFESTS_DIR = _strategy_cards.DEFAULT_MANIFESTS_DIR

SCHEMA = "composition_ir/v1"
COMPILED_SCHEMA = "compiled_pipeline/v1"
SUPPORTED_SCHEMAS = (SCHEMA,)

# 运行模式与 Strategy Card 同域（strategy_cards.ALLOWED_MODES）
ALLOWED_MODES = tuple(_strategy_cards.ALLOWED_MODES)

# ── schema 常量 ───────────────────────────────────────────────────────
NODE_TYPES = (
    "universe",
    "strategy",
    "filter",
    "rank",
    "allocator",
    "risk",
    "execution",
    "agent_team",
)
PORT_TYPES = ("universe", "signals", "ranking", "allocations", "orders", "advice")
TERMINAL_NODE_TYPES = ("execution", "agent_team")

TOP_LEVEL_REQUIRED = (
    "pipeline_id",
    "version",
    "owner",
    "tenant",
    "mode",
    "nodes",
    "edges",
    "risk_policy",
    "publish",
)
TOP_LEVEL_OPTIONAL = ("schema", "description")
TOP_LEVEL_ALLOWED = TOP_LEVEL_REQUIRED + TOP_LEVEL_OPTIONAL

NODE_REQUIRED = ("id", "type")
NODE_ALLOWED = ("id", "type", "label", "strategy_id", "params")

EDGE_REQUIRED = ("from", "to", "port")
EDGE_ALLOWED = ("from", "to", "port", "label")

RISK_POLICY_REQUIRED = ("policy_id", "max_position_pct")
RISK_POLICY_ALLOWED = (
    "policy_id",
    "max_position_pct",
    "max_gross_exposure_pct",
    "max_positions",
    "stop_loss_pct",
    "kill_switch",
    "notes",
)

PUBLISH_REQUIRED = ("enabled",)
PUBLISH_ALLOWED = ("enabled", "channel", "visibility", "requires_approval", "notes")
PUBLISH_VISIBILITIES = ("private", "tenant", "public")

# execution 节点的只读护栏：本阶段不允许任何实盘/下单意图
EXECUTION_FORBIDDEN_TRUE_FLAGS = (
    "live",
    "live_trading",
    "allow_live_trading",
    "place_orders",
)

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,63}$")
_PIPELINE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,127}$")
_TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")

PARAM_SCHEMA_TYPES = ("int", "float", "bool", "str", "list", "dict", "enum")


@dataclass(frozen=True)
class _NodeSpec:
    """节点类型契约：输入端口名→端口类型、输出端口名→端口类型、是否终止节点。"""

    inputs: Mapping[str, str]
    outputs: Mapping[str, str]
    terminal: bool = False


NODE_SPECS: Dict[str, _NodeSpec] = {
    "universe": _NodeSpec(inputs={}, outputs={"universe": "universe"}),
    "strategy": _NodeSpec(
        inputs={"universe": "universe"}, outputs={"signals": "signals"}
    ),
    "filter": _NodeSpec(
        inputs={"signals": "signals"}, outputs={"signals": "signals"}
    ),
    "rank": _NodeSpec(inputs={"signals": "signals"}, outputs={"ranking": "ranking"}),
    "allocator": _NodeSpec(
        inputs={"signals": "signals", "ranking": "ranking", "advice": "advice"},
        outputs={"allocations": "allocations"},
    ),
    "risk": _NodeSpec(
        inputs={"allocations": "allocations"},
        outputs={"allocations": "allocations"},
    ),
    "execution": _NodeSpec(
        inputs={"allocations": "allocations"},
        outputs={"orders": "orders"},
        terminal=True,
    ),
    "agent_team": _NodeSpec(
        inputs={
            "universe": "universe",
            "signals": "signals",
            "ranking": "ranking",
            "allocations": "allocations",
            "orders": "orders",
        },
        outputs={"advice": "advice"},
        terminal=True,
    ),
}


# ── 异常 ──────────────────────────────────────────────────────────────
class IRValidationError(ValueError):
    """Composition IR 非法（closed-on-failure）：携带全部校验错误。

    继承 ``ValueError``，调用方可用 ``except IRValidationError`` 精确区分
    「IR 内容非法」与「IO/依赖输入错误」（后者为普通 ``ValueError``）。
    """

    def __init__(self, errors: Iterable[str], prefix: str = "非法 Composition IR"):
        self.errors: List[str] = [str(item) for item in errors]
        detail = "；".join(self.errors) if self.errors else "未知错误"
        super().__init__("%s: %s" % (prefix, detail))


# ── 数据结构 ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class IRNode:
    """IR 节点（已校验后的类型化形态）。"""

    id: str
    type: str
    label: str = ""
    strategy_id: Optional[str] = None
    params: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "params": copy.deepcopy(dict(self.params)),
        }
        if self.label:
            out["label"] = self.label
        if self.strategy_id is not None:
            out["strategy_id"] = self.strategy_id
        return out


@dataclass(frozen=True)
class IREdge:
    """IR 边：``from`` → ``to``，``port`` 为上游输出端口名。"""

    from_id: str
    to_id: str
    port: str
    label: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "from": self.from_id,
            "to": self.to_id,
            "port": self.port,
        }
        if self.label:
            out["label"] = self.label
        return out


@dataclass(frozen=True)
class CompositionIR:
    """校验通过的 Composition IR（不可变；``topological_order`` 为确定性拓扑序）。"""

    pipeline_id: str
    version: str
    owner: str
    tenant: str
    mode: str
    nodes: Tuple[IRNode, ...] = ()
    edges: Tuple[IREdge, ...] = ()
    risk_policy: Mapping[str, Any] = field(default_factory=dict)
    publish: Mapping[str, Any] = field(default_factory=dict)
    schema: str = SCHEMA
    description: str = ""
    topological_order: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "schema": self.schema,
            "pipeline_id": self.pipeline_id,
            "version": self.version,
            "owner": self.owner,
            "tenant": self.tenant,
            "mode": self.mode,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "risk_policy": copy.deepcopy(dict(self.risk_policy)),
            "publish": copy.deepcopy(dict(self.publish)),
        }
        if self.description:
            out["description"] = self.description
        return out


@dataclass(frozen=True)
class CompiledPipeline:
    """编译产物：只读计划（不执行/不下单/不写 ledger/不发布）。"""

    pipeline_id: str
    version: str
    owner: str
    tenant: str
    mode: str
    schema: str = COMPILED_SCHEMA
    description: str = ""
    topological_order: Tuple[str, ...] = ()
    nodes: Tuple[Mapping[str, Any], ...] = ()
    edges: Tuple[Mapping[str, Any], ...] = ()
    strategies: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    data_deps: Tuple[str, ...] = ()
    risk_policy: Mapping[str, Any] = field(default_factory=dict)
    publish_plan: Mapping[str, Any] = field(default_factory=dict)
    execution: Mapping[str, Any] = field(default_factory=dict)
    safety: Mapping[str, Any] = field(default_factory=dict)
    sources: Mapping[str, Any] = field(default_factory=dict)
    readonly: bool = True
    side_effects: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "pipeline_id": self.pipeline_id,
            "version": self.version,
            "owner": self.owner,
            "tenant": self.tenant,
            "mode": self.mode,
            "description": self.description,
            "readonly": self.readonly,
            "side_effects": list(self.side_effects),
            "safety": copy.deepcopy(dict(self.safety)),
            "topological_order": list(self.topological_order),
            "nodes": [copy.deepcopy(dict(node)) for node in self.nodes],
            "edges": [copy.deepcopy(dict(edge)) for edge in self.edges],
            "strategies": copy.deepcopy(
                {key: dict(value) for key, value in self.strategies.items()}
            ),
            "data_deps": list(self.data_deps),
            "risk_policy": copy.deepcopy(dict(self.risk_policy)),
            "publish_plan": copy.deepcopy(dict(self.publish_plan)),
            "execution": copy.deepcopy(dict(self.execution)),
            "sources": copy.deepcopy(dict(self.sources)),
        }


# ── 基础工具 ──────────────────────────────────────────────────────────
def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _json_copy(value: Any) -> Any:
    """JSON 友好的深拷贝（IR 输入均为 JSON 值；异常时退回原值不发明数据）。"""
    try:
        return copy.deepcopy(value)
    except (TypeError, ValueError):  # pragma: no cover - 仅防御非 JSON 输入
        return value


def _string_list(value: Any) -> Optional[List[str]]:
    """非空、元素为非空字符串的 list；不合法返回 None。"""
    if not isinstance(value, list) or not value:
        return None
    if any(not _is_nonempty_str(item) for item in value):
        return None
    return [str(item) for item in value]


def _accepting_in_port(spec: _NodeSpec, port_type: str) -> Optional[str]:
    """目标节点接受的输入端口名（按端口类型匹配）；不接受返回 None。"""
    for in_port, accepted in spec.inputs.items():
        if accepted == port_type:
            return in_port
    return None


def _load_card_index(
    cards: Optional[Sequence[Mapping[str, Any]]] = None,
    cards_path: Optional[str] = None,
    manifests_dir: Optional[str] = None,
) -> Tuple[Dict[str, Dict[str, Any]], str]:
    """加载 Strategy Card 索引（只读）。

    返回 ``(index, cards_source)``；``cards`` 为注入卡片时 ``cards_source``
    在无 ``cards_path`` 时记 ``injected``。卡片非法时抛普通 ``ValueError``
    （属于输入/依赖错误，不是 IR 内容非法）。
    """
    if cards is not None:
        index = _strategy_cards.load_card_index(cards=cards)
        source = os.path.abspath(cards_path) if cards_path else "injected"
        return index, source
    path = cards_path or DEFAULT_CARDS_PATH
    index = _strategy_cards.load_card_index(path=path, manifests_dir=manifests_dir)
    return index, os.path.abspath(path)


def _param_type_ok(type_name: Any, spec: Mapping[str, Any], value: Any) -> bool:
    """Strategy Card params_schema 取值类型校验（int/float/bool/str/list/dict/enum）。"""
    if type_name == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "float":
        return _is_number(value)
    if type_name == "bool":
        return isinstance(value, bool)
    if type_name == "str":
        return isinstance(value, str)
    if type_name == "list":
        return isinstance(value, list)
    if type_name == "dict":
        return isinstance(value, Mapping)
    if type_name == "enum":
        values = spec.get("values")
        return (
            isinstance(values, list)
            and bool(values)
            and isinstance(value, str)
            and value in values
        )
    return False


def _validate_strategy_params(
    node_id: str, params: Mapping[str, Any], card: Mapping[str, Any], errors: List[str]
) -> None:
    """strategy 节点参数：键必须属于卡片 params_schema，值类型/枚举必须匹配。"""
    schema = card.get("params_schema")
    if not isinstance(schema, Mapping):
        errors.append("策略卡 %s 的 params_schema 必须为 dict" % card.get("strategy_id"))
        return
    for key, value in params.items():
        spec = schema.get(key)
        if spec is None:
            errors.append(
                "节点 %s 的参数 %r 不在策略卡 %s 的 params_schema 内（合法: %s）"
                % (
                    node_id,
                    key,
                    card.get("strategy_id"),
                    ", ".join(sorted(str(item) for item in schema)) or "-",
                )
            )
            continue
        if not isinstance(spec, Mapping):
            errors.append(
                "策略卡 %s 的参数 %r 定义非法（必须为 dict）"
                % (card.get("strategy_id"), key)
            )
            continue
        type_name = spec.get("type")
        if type_name not in PARAM_SCHEMA_TYPES:
            errors.append(
                "策略卡 %s 的参数 %r 类型未知: %r"
                % (card.get("strategy_id"), key, type_name)
            )
            continue
        if not _param_type_ok(type_name, spec, value):
            errors.append(
                "节点 %s 的参数 %r 类型非法：期望 %s，收到 %r"
                % (node_id, key, type_name, value)
            )


def _validate_node_extra_params(node: IRNode, errors: List[str]) -> None:
    """非 strategy 节点的轻量参数校验（只校验有安全/结构语义的已知参数）。"""
    params = node.params
    if node.type == "universe":
        if "markets" in params and _string_list(params["markets"]) is None:
            errors.append(
                "节点 %s 的参数 markets 必须为非空无重复字符串 list" % node.id
            )
    elif node.type == "agent_team":
        if "team" in params and _string_list(params["team"]) is None:
            errors.append("节点 %s 的参数 team 必须为非空字符串 list" % node.id)
        if "role" in params and not _is_nonempty_str(params["role"]):
            errors.append("节点 %s 的参数 role 必须为非空字符串" % node.id)
    elif node.type == "execution":
        if "book" in params and not _is_nonempty_str(params["book"]):
            errors.append("节点 %s 的参数 book 必须为非空字符串" % node.id)
        if "dry_run" in params and params["dry_run"] is not True:
            errors.append(
                "节点 %s 为只读计划：execution.params.dry_run 不得为 false" % node.id
            )
        for flag in EXECUTION_FORBIDDEN_TRUE_FLAGS:
            if params.get(flag) is True:
                errors.append(
                    "节点 %s 为只读计划：不得声明实盘/下单参数 %s=true" % (node.id, flag)
                )


# ── 顶层/节点/边校验 ──────────────────────────────────────────────────
def _validate_header(doc: Mapping[str, Any], errors: List[str]) -> Dict[str, Any]:
    """顶层字段校验；返回 {schema, description} 归一值。"""
    unknown = sorted(str(key) for key in doc if key not in TOP_LEVEL_ALLOWED)
    if unknown:
        errors.append("IR 含未知顶层字段: %s" % ", ".join(unknown))
    missing = [key for key in TOP_LEVEL_REQUIRED if key not in doc]
    if missing:
        errors.append("IR 缺必填字段: %s" % ", ".join(missing))

    schema = doc.get("schema", SCHEMA)
    if not isinstance(schema, str) or schema not in SUPPORTED_SCHEMAS:
        errors.append(
            "不支持的 IR schema 版本: %r（支持: %s）"
            % (schema, ", ".join(SUPPORTED_SCHEMAS))
        )
        schema = SCHEMA

    pipeline_id = doc.get("pipeline_id")
    if not _is_nonempty_str(pipeline_id) or not _PIPELINE_ID_RE.match(str(pipeline_id)):
        errors.append("pipeline_id 必须为合法标识符（字母开头，允许 . _ -）: %r" % (pipeline_id,))

    version = doc.get("version")
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        errors.append(
            "pipeline version 必须为 x.y.z 形式: %r" % (version,)
        )

    owner = doc.get("owner")
    if not _is_nonempty_str(owner):
        errors.append("owner 必须为非空字符串")

    tenant = doc.get("tenant")
    if not _is_nonempty_str(tenant) or not _TENANT_RE.match(str(tenant)):
        errors.append("tenant 必须为合法标识符: %r" % (tenant,))

    mode = doc.get("mode")
    if mode not in ALLOWED_MODES:
        errors.append(
            "mode 非法: %r（允许: %s）" % (mode, ", ".join(ALLOWED_MODES))
        )

    description = doc.get("description", "")
    if not isinstance(description, str):
        errors.append("description 必须为字符串")

    nodes = doc.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        errors.append("IR.nodes 必须为非空 list")
    edges = doc.get("edges")
    if not isinstance(edges, list) or not edges:
        errors.append("IR.edges 必须为非空 list")
    if not isinstance(doc.get("risk_policy"), Mapping):
        errors.append("IR.risk_policy 必须为 dict")
    if not isinstance(doc.get("publish"), Mapping):
        errors.append("IR.publish 必须为 dict")
    return {"schema": schema, "description": description}


def _validate_nodes(
    doc: Mapping[str, Any],
    card_index: Mapping[str, Mapping[str, Any]],
    errors: List[str],
) -> Tuple[IRNode, ...]:
    """节点校验（id 唯一/类型合法/strategy_id 在卡内/参数对卡校验）。"""
    raw_nodes = doc.get("nodes")
    if not isinstance(raw_nodes, list):
        return ()
    nodes: List[IRNode] = []
    seen_ids: Dict[str, int] = {}
    seen_strategy_ids: Dict[str, str] = {}
    for position, raw in enumerate(raw_nodes):
        where = "nodes[%d]" % position
        if not isinstance(raw, Mapping):
            errors.append("%s 必须为 dict" % where)
            continue
        unknown = sorted(str(key) for key in raw if key not in NODE_ALLOWED)
        if unknown:
            errors.append("%s 含未知字段: %s" % (where, ", ".join(unknown)))

        node_id = raw.get("id")
        if not _is_nonempty_str(node_id):
            errors.append("%s 缺合法 id" % where)
            continue
        node_id = str(node_id)
        if not _ID_RE.match(node_id):
            errors.append("节点 id 非法: %r（字母开头，允许 . _ -）" % node_id)
        if node_id in seen_ids:
            errors.append("节点 id 重复: %s" % node_id)
        seen_ids[node_id] = position

        node_type = raw.get("type")
        if not _is_nonempty_str(node_type):
            errors.append("%s 缺合法 type" % where)
            continue
        node_type = str(node_type)
        if node_type not in NODE_TYPES:
            errors.append(
                "未知节点类型: %r（允许: %s）"
                % (node_type, ", ".join(NODE_TYPES))
            )
            continue

        label = raw.get("label", "")
        if not isinstance(label, str):
            errors.append("%s 的 label 必须为字符串" % where)
            label = ""

        params = raw.get("params", {})
        if not isinstance(params, Mapping):
            errors.append("%s 的 params 必须为 dict" % where)
            params = {}

        strategy_id = raw.get("strategy_id")
        node_strategy_id: Optional[str] = None
        if node_type == "strategy":
            if not _is_nonempty_str(strategy_id):
                errors.append("strategy 节点 %s 缺 strategy_id" % node_id)
            else:
                node_strategy_id = str(strategy_id)
                if node_strategy_id not in card_index:
                    errors.append(
                        "未知策略: %s（节点 %s；不在 Strategy Card 索引内: %s）"
                        % (
                            node_strategy_id,
                            node_id,
                            ", ".join(sorted(card_index)) or "-",
                        )
                    )
                elif node_strategy_id in seen_strategy_ids:
                    errors.append(
                        "重复 strategy_id 节点: %s（节点 %s 与 %s）"
                        % (
                            node_strategy_id,
                            seen_strategy_ids[node_strategy_id],
                            node_id,
                        )
                    )
                else:
                    seen_strategy_ids[node_strategy_id] = node_id
                    _validate_strategy_params(
                        node_id, params, card_index[node_strategy_id], errors
                    )
        elif "strategy_id" in raw and raw.get("strategy_id") is not None:
            errors.append("非 strategy 节点 %s 不得带 strategy_id" % node_id)

        node = IRNode(
            id=node_id,
            type=node_type,
            label=label,
            strategy_id=node_strategy_id,
            params=dict(params),
        )
        if node_type != "strategy":
            _validate_node_extra_params(node, errors)
        nodes.append(node)
    return tuple(nodes)


def _validate_edges(
    doc: Mapping[str, Any],
    nodes: Sequence[IRNode],
    errors: List[str],
) -> Tuple[IREdge, ...]:
    """边校验（from/to 存在、端口名合法、端口类型兼容、无自环/重复边）。"""
    raw_edges = doc.get("edges")
    if not isinstance(raw_edges, list):
        return ()
    node_map = {node.id: node for node in nodes}
    edges: List[IREdge] = []
    seen: Dict[Tuple[str, str, str], int] = {}
    for position, raw in enumerate(raw_edges):
        where = "edges[%d]" % position
        if not isinstance(raw, Mapping):
            errors.append("%s 必须为 dict" % where)
            continue
        unknown = sorted(str(key) for key in raw if key not in EDGE_ALLOWED)
        if unknown:
            errors.append("%s 含未知字段: %s" % (where, ", ".join(unknown)))

        missing = [key for key in EDGE_REQUIRED if key not in raw]
        if missing:
            errors.append("%s 缺必填字段: %s" % (where, ", ".join(missing)))
            continue
        from_id = raw.get("from")
        to_id = raw.get("to")
        port = raw.get("port")
        bad = [
            name
            for name, value in (("from", from_id), ("to", to_id), ("port", port))
            if not _is_nonempty_str(value)
        ]
        if bad:
            errors.append("%s 字段必须为非空字符串: %s" % (where, ", ".join(bad)))
            continue
        from_id, to_id, port = str(from_id), str(to_id), str(port)
        label = raw.get("label", "")
        if not isinstance(label, str):
            errors.append("%s 的 label 必须为字符串" % where)
            label = ""

        key = (from_id, to_id, port)
        if key in seen:
            errors.append(
                "重复边: %s --%s--> %s（与 edges[%d] 重复）"
                % (from_id, port, to_id, seen[key])
            )
            continue
        seen[key] = position

        source = node_map.get(from_id)
        target = node_map.get(to_id)
        if source is None:
            errors.append("%s 的 from 节点不存在: %s" % (where, from_id))
            continue
        if target is None:
            errors.append("%s 的 to 节点不存在: %s" % (where, to_id))
            continue
        if from_id == to_id:
            errors.append("自环边不允许: 节点 %s --%s--> 自身" % (from_id, port))
            continue
        source_spec = NODE_SPECS[source.type]
        target_spec = NODE_SPECS[target.type]
        if port not in source_spec.outputs:
            errors.append(
                "端口非法: 节点 %s(%s) 无输出端口 %r（合法: %s）"
                % (
                    from_id,
                    source.type,
                    port,
                    ", ".join(sorted(source_spec.outputs)) or "-",
                )
            )
            continue
        port_type = source_spec.outputs[port]
        if _accepting_in_port(target_spec, port_type) is None:
            errors.append(
                "端口类型不兼容: %s.%s(%s) → %s(%s) 不接受类型 %s（接受: %s）"
                % (
                    from_id,
                    port,
                    port_type,
                    to_id,
                    target.type,
                    port_type,
                    ", ".join(
                        "%s:%s" % (name, kind)
                        for name, kind in sorted(target_spec.inputs.items())
                    )
                    or "-",
                )
            )
            continue
        edges.append(
            IREdge(from_id=from_id, to_id=to_id, port=port, label=label)
        )
    return tuple(edges)


def _validate_risk_policy(doc: Mapping[str, Any], errors: List[str]) -> Dict[str, Any]:
    """risk_policy 校验；返回归一 dict（只保留声明字段，不发明默认值）。"""
    raw = doc.get("risk_policy")
    if not isinstance(raw, Mapping):
        return {}
    unknown = sorted(str(key) for key in raw if key not in RISK_POLICY_ALLOWED)
    if unknown:
        errors.append("risk_policy 含未知字段: %s" % ", ".join(unknown))
    missing = [key for key in RISK_POLICY_REQUIRED if key not in raw]
    if missing:
        errors.append("risk_policy 缺必填字段: %s" % ", ".join(missing))
    out: Dict[str, Any] = dict(raw)

    policy_id = raw.get("policy_id")
    if "policy_id" in raw and not _is_nonempty_str(policy_id):
        errors.append("risk_policy.policy_id 必须为非空字符串")
    max_position = raw.get("max_position_pct")
    if "max_position_pct" in raw:
        if not _is_number(max_position) or not (0 < float(max_position) <= 100):
            errors.append("risk_policy.max_position_pct 必须为 (0, 100] 内的数值")
    if "max_positions" in raw and not _is_positive_int(raw.get("max_positions")):
        errors.append("risk_policy.max_positions 必须为正整数")
    if "max_gross_exposure_pct" in raw:
        gross = raw.get("max_gross_exposure_pct")
        if not _is_number(gross) or float(gross) <= 0:
            errors.append("risk_policy.max_gross_exposure_pct 必须为正数")
    if "stop_loss_pct" in raw:
        stop = raw.get("stop_loss_pct")
        if not _is_number(stop) or not (0 < float(stop) <= 100):
            errors.append("risk_policy.stop_loss_pct 必须为 (0, 100] 内的数值")
    if "kill_switch" in raw and not isinstance(raw.get("kill_switch"), bool):
        errors.append("risk_policy.kill_switch 必须为 bool")
    if "notes" in raw and not isinstance(raw.get("notes"), str):
        errors.append("risk_policy.notes 必须为字符串")
    return out


def _validate_publish(doc: Mapping[str, Any], errors: List[str]) -> Dict[str, Any]:
    """publish 校验；返回归一 dict（本阶段发布仅登记计划，不生效）。"""
    raw = doc.get("publish")
    if not isinstance(raw, Mapping):
        return {}
    unknown = sorted(str(key) for key in raw if key not in PUBLISH_ALLOWED)
    if unknown:
        errors.append("publish 含未知字段: %s" % ", ".join(unknown))
    missing = [key for key in PUBLISH_REQUIRED if key not in raw]
    if missing:
        errors.append("publish 缺必填字段: %s" % ", ".join(missing))
    out: Dict[str, Any] = dict(raw)
    enabled = raw.get("enabled")
    if "enabled" in raw and not isinstance(enabled, bool):
        errors.append("publish.enabled 必须为 bool")
    visibility = raw.get("visibility")
    if "visibility" in raw and visibility not in PUBLISH_VISIBILITIES:
        errors.append(
            "publish.visibility 非法: %r（允许: %s）"
            % (visibility, ", ".join(PUBLISH_VISIBILITIES))
        )
    if enabled is True and not _is_nonempty_str(raw.get("channel")):
        errors.append("publish 启用时 channel 必须为非空字符串")
    if "channel" in raw and not isinstance(raw.get("channel"), str):
        errors.append("publish.channel 必须为字符串")
    if "requires_approval" in raw and not isinstance(
        raw.get("requires_approval"), bool
    ):
        errors.append("publish.requires_approval 必须为 bool")
    if "notes" in raw and not isinstance(raw.get("notes"), str):
        errors.append("publish.notes 必须为字符串")
    return out


def _validate_structure(
    nodes: Sequence[IRNode], edges: Sequence[IREdge], errors: List[str]
) -> None:
    """图结构校验：入口/连通/终止节点基本形态（DAG 无环另测）。"""
    if not nodes:
        return
    type_counts: Dict[str, int] = {}
    for node in nodes:
        type_counts[node.type] = type_counts.get(node.type, 0) + 1
    if type_counts.get("universe", 0) < 1:
        errors.append("图必须至少含 1 个 universe 节点")
    if type_counts.get("strategy", 0) < 1:
        errors.append("图必须至少含 1 个 strategy 节点")

    incoming: Dict[str, List[IREdge]] = {}
    outgoing: Dict[str, List[IREdge]] = {}
    for edge in edges:
        incoming.setdefault(edge.to_id, []).append(edge)
        outgoing.setdefault(edge.from_id, []).append(edge)
    for node in nodes:
        has_in = bool(incoming.get(node.id))
        has_out = bool(outgoing.get(node.id))
        if node.type == "universe":
            if has_in:
                errors.append("universe 节点 %s 不得有入边" % node.id)
            if not has_out:
                errors.append("universe 节点 %s 必须有出边" % node.id)
        else:
            if not has_in:
                errors.append("节点 %s(%s) 缺入边（非 universe 节点必须有上游）" % (node.id, node.type))
            if node.type not in TERMINAL_NODE_TYPES and not has_out:
                errors.append(
                    "节点 %s(%s) 缺出边（该类型不是终止节点）" % (node.id, node.type)
                )


def _topological_order(
    nodes: Sequence[IRNode], edges: Sequence[IREdge]
) -> Tuple[str, ...]:
    """Kahn 拓扑排序（确定性：同层按节点 id 字典序）；有环抛 IRValidationError。"""
    adjacency: Dict[str, set] = {node.id: set() for node in nodes}
    indegree: Dict[str, int] = {node.id: 0 for node in nodes}
    for edge in edges:
        if edge.to_id in adjacency[edge.from_id]:
            continue
        adjacency[edge.from_id].add(edge.to_id)
        indegree[edge.to_id] += 1
    ready: List[str] = [node_id for node_id in indegree if indegree[node_id] == 0]
    heapq.heapify(ready)
    order: List[str] = []
    while ready:
        current = heapq.heappop(ready)
        order.append(current)
        for child in sorted(adjacency.get(current, ())):
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(ready, child)
    if len(order) != len(nodes):
        remaining = sorted(node_id for node_id in indegree if indegree[node_id] > 0)
        raise IRValidationError(
            [
                "DAG 校验失败：图存在环（无法拓扑排序），涉及节点: %s"
                % ", ".join(remaining)
            ]
        )
    return tuple(order)


def _validate_ir_doc(
    doc: Mapping[str, Any], card_index: Mapping[str, Mapping[str, Any]]
) -> CompositionIR:
    """校验 IR 文档（内部入口：卡片索引已加载）。"""
    errors: List[str] = []
    header = _validate_header(doc, errors)
    nodes = _validate_nodes(doc, card_index, errors)
    edges = _validate_edges(doc, nodes, errors)
    risk_policy = _validate_risk_policy(doc, errors)
    publish = _validate_publish(doc, errors)
    if nodes:
        _validate_structure(nodes, edges, errors)
    if errors:
        raise IRValidationError(errors)
    order = _topological_order(nodes, edges)
    return CompositionIR(
        pipeline_id=str(doc["pipeline_id"]),
        version=str(doc["version"]),
        owner=str(doc["owner"]),
        tenant=str(doc["tenant"]),
        mode=str(doc["mode"]),
        nodes=nodes,
        edges=edges,
        risk_policy=risk_policy,
        publish=publish,
        schema=header["schema"],
        description=header["description"],
        topological_order=order,
    )


def validate_ir(
    ir: Any,
    cards: Optional[Sequence[Mapping[str, Any]]] = None,
    cards_path: Optional[str] = None,
    manifests_dir: Optional[str] = None,
) -> CompositionIR:
    """校验 Composition IR（JSON dict 或 CompositionIR）。

    Parameters
    ----------
    ir : dict | CompositionIR
        IR 文档；顶层必须为 dict。
    cards : sequence[dict] | None
        注入的 Strategy Card 列表（测试/多租户场景）；缺省从
        ``strategy_cards.load_cards()`` 只读加载。
    cards_path : str | None
        卡片 overlay 路径（缺省 ``strategy_layer/strategy_cards.json``）。
    manifests_dir : str | None
        manifest 目录（缺省 ``strategies/manifests/``）。

    Returns
    -------
    CompositionIR
        校验通过的不可变 IR（含 ``topological_order``）。

    Raises
    ------
    IRValidationError
        IR 内容非法（字段/类型/端口/策略/环/版本等；闭死失败）。
    ValueError
        卡片/依赖输入非法（非 IR 内容问题）。
    """
    card_index, _source = _load_card_index(cards, cards_path, manifests_dir)
    if isinstance(ir, CompositionIR):
        doc: Any = ir.to_dict()
    else:
        doc = ir
    if not isinstance(doc, Mapping):
        raise IRValidationError(
            ["IR 顶层必须为 dict，收到 %s" % type(doc).__name__]
        )
    return _validate_ir_doc(doc, card_index)


def topological_order(
    ir: Any,
    cards: Optional[Sequence[Mapping[str, Any]]] = None,
    cards_path: Optional[str] = None,
    manifests_dir: Optional[str] = None,
) -> Tuple[str, ...]:
    """返回确定性拓扑序（节点 id 元组）；IR 非法抛 IRValidationError。"""
    if isinstance(ir, CompositionIR):
        return tuple(ir.topological_order)
    return validate_ir(
        ir, cards=cards, cards_path=cards_path, manifests_dir=manifests_dir
    ).topological_order


# ── 编译（只读计划） ──────────────────────────────────────────────────
def _merge_strategy_params(
    node_params: Mapping[str, Any], card: Mapping[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """卡片默认参数 + 节点 override 合并；返回 (params, 每键来源)。"""
    schema = card.get("params_schema")
    if not isinstance(schema, Mapping):
        schema = {}
    merged: Dict[str, Any] = {}
    sources: Dict[str, str] = {}
    for key, spec in schema.items():
        default = spec.get("default") if isinstance(spec, Mapping) else None
        merged[key] = _json_copy(default)
        sources[key] = "default"
    for key, value in node_params.items():
        merged[key] = _json_copy(value)
        sources[key] = "override"
    return merged, sources


def _card_snapshot(card: Mapping[str, Any], mode: str) -> Dict[str, Any]:
    """解析 Strategy Card → 编译产物中的只读卡片快照（缺失字段显式 None）。"""
    signal_schema = card.get("signal_schema")
    signal_kind = (
        signal_schema.get("kind") if isinstance(signal_schema, Mapping) else None
    )
    universe = card.get("universe")
    regime_tags = card.get("regime_tags")
    data_deps = card.get("data_deps")
    modes = card.get("modes")
    capacity = card.get("capacity")
    return {
        "strategy_id": card.get("strategy_id"),
        "version": card.get("version"),
        "owner": card.get("owner"),
        "frequency": card.get("frequency"),
        "horizon_days": card.get("horizon_days"),
        "universe": [str(item) for item in universe] if isinstance(universe, list) else [],
        "regime_tags": [str(item) for item in regime_tags]
        if isinstance(regime_tags, list)
        else [],
        "data_deps": [str(item) for item in data_deps]
        if isinstance(data_deps, list)
        else [],
        "modes": [str(item) for item in modes] if isinstance(modes, list) else [],
        "capacity": _json_copy(capacity) if isinstance(capacity, Mapping) else {},
        "signal_kind": signal_kind,
        "params_schema": _json_copy(card.get("params_schema"))
        if isinstance(card.get("params_schema"), Mapping)
        else {},
        "mode_supported": (
            str(mode) in [str(item) for item in modes]
            if isinstance(modes, list)
            else None
        ),
        "health": _json_copy(card.get("health"))
        if isinstance(card.get("health"), Mapping)
        else {},
    }


def compile_ir(
    ir: Any,
    cards: Optional[Sequence[Mapping[str, Any]]] = None,
    cards_path: Optional[str] = None,
    manifests_dir: Optional[str] = None,
    ir_path: Optional[str] = None,
) -> CompiledPipeline:
    """校验 + 拓扑排序 + 解析 Strategy Card → ``CompiledPipeline``（只读计划）。

    产物仅描述「计划」：``readonly=True``、``side_effects=[]``、
    ``safety`` 四开关全 false、``execution.enabled=False``、
    ``publish_plan.effective=False``。不执行策略、不下单、不写 ledger、
    不发布、不写任何文件；IR/卡片非法一律 IRValidationError（闭死失败）。
    """
    card_index, cards_source = _load_card_index(cards, cards_path, manifests_dir)
    if isinstance(ir, CompositionIR):
        doc: Any = ir.to_dict()
    else:
        doc = ir
    if not isinstance(doc, Mapping):
        raise IRValidationError(
            ["IR 顶层必须为 dict，收到 %s" % type(doc).__name__]
        )
    validated = _validate_ir_doc(doc, card_index)

    node_map = {node.id: node for node in validated.nodes}
    ordered_nodes = [node_map[node_id] for node_id in validated.topological_order]
    incoming: Dict[str, List[IREdge]] = {}
    for edge in validated.edges:
        incoming.setdefault(edge.to_id, []).append(edge)

    strategies: Dict[str, Dict[str, Any]] = {}
    plan_nodes: List[Dict[str, Any]] = []
    for position, node in enumerate(ordered_nodes):
        spec = NODE_SPECS[node.type]
        in_ports: List[str] = []
        for edge in sorted(incoming.get(node.id, []), key=lambda item: (item.from_id, item.port)):
            source_type = node_map[edge.from_id].type
            port_type = NODE_SPECS[source_type].outputs[edge.port]
            in_port = _accepting_in_port(spec, port_type)
            if in_port is not None:
                in_ports.append(in_port)
        plan_node: Dict[str, Any] = {
            "id": node.id,
            "type": node.type,
            "label": node.label,
            "order": position,
            "inputs": sorted(set(in_ports)),
            "outputs": sorted(spec.outputs),
            "params": _json_copy(dict(node.params)),
        }
        if node.type == "strategy":
            strategy_id = str(node.strategy_id)
            card = card_index[strategy_id]
            params, param_sources = _merge_strategy_params(dict(node.params), card)
            card_view = _card_snapshot(card, validated.mode)
            plan_node["strategy_id"] = strategy_id
            plan_node["params"] = params
            plan_node["param_sources"] = param_sources
            plan_node["card"] = card_view
            strategies[strategy_id] = {
                "node_id": node.id,
                "order": position,
                "card": card_view,
                "params": params,
                "param_sources": param_sources,
            }
        plan_nodes.append(plan_node)

    plan_edges: List[Dict[str, Any]] = []
    for edge in validated.edges:
        source_type = node_map[edge.from_id].type
        target_type = node_map[edge.to_id].type
        port_type = NODE_SPECS[source_type].outputs[edge.port]
        in_port = _accepting_in_port(NODE_SPECS[target_type], port_type)
        plan_edge: Dict[str, Any] = {
            "from": edge.from_id,
            "to": edge.to_id,
            "port": edge.port,
            "in_port": in_port,
            "port_type": port_type,
        }
        if edge.label:
            plan_edge["label"] = edge.label
        plan_edges.append(plan_edge)

    data_deps: set = set()
    for item in strategies.values():
        for dep in item["card"].get("data_deps", []):
            data_deps.add(str(dep))

    execution_nodes = [
        node for node in validated.nodes if node.type == "execution"
    ]
    books = sorted(
        {
            str(node.params.get("book"))
            for node in execution_nodes
            if _is_nonempty_str(node.params.get("book"))
        }
    )
    execution = {
        "enabled": False,
        "dry_run": True,
        "places_orders": False,
        "writes_ledger": False,
        "node_ids": sorted(node.id for node in execution_nodes),
        "books": books,
        "reason": "compile_ir 只读：只产出执行计划，不执行、不下单、不写 ledger",
    }
    publish_plan = _json_copy(dict(validated.publish))
    publish_plan.setdefault("visibility", "private")
    publish_plan["effective"] = False
    publish_plan["would_publish"] = bool(validated.publish.get("enabled"))
    publish_plan["reason"] = "compile_ir 只读：发布仅登记计划，不执行发布"

    safety = {
        "executes": False,
        "places_orders": False,
        "writes_ledger": False,
        "publishes": False,
    }
    sources = {
        "ir": os.path.abspath(ir_path) if ir_path else "in-memory",
        "cards": cards_source,
        "manifests": os.path.abspath(manifests_dir or DEFAULT_MANIFESTS_DIR),
    }
    return CompiledPipeline(
        pipeline_id=validated.pipeline_id,
        version=validated.version,
        owner=validated.owner,
        tenant=validated.tenant,
        mode=validated.mode,
        description=validated.description,
        topological_order=tuple(validated.topological_order),
        nodes=tuple(plan_nodes),
        edges=tuple(plan_edges),
        strategies=strategies,
        data_deps=tuple(sorted(data_deps)),
        risk_policy=_json_copy(dict(validated.risk_policy)),
        publish_plan=publish_plan,
        execution=execution,
        safety=safety,
        sources=sources,
        readonly=True,
        side_effects=(),
    )


# ── 文件读取与 CLI（只读，closed-on-failure） ─────────────────────────
def load_ir_file(path: str) -> Dict[str, Any]:
    """只读加载 IR JSON 文件；顶层必须为 dict。失败抛 ValueError（非 IRValidationError）。"""
    if not path or not os.path.isfile(path):
        raise ValueError("IR 文件不存在: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValueError("IR JSON 读取失败 %s: %s" % (path, exc)) from exc
    if not isinstance(doc, Mapping):
        raise ValueError("IR JSON 顶层必须为 dict: %s" % path)
    return dict(doc)


USAGE = """用法:
  python -X utf8 composition_ir.py --file IR.json [--cards-file CARDS.json]
                                   [--manifests DIR] [--validate-only] [--json]

只读：校验/编译 Composition IR 并打印 JSON（--json）或人类可读摘要；
不执行策略、不下单、不写 ledger、不发布、不写任何文件、不新增计划任务。
退出码：0=合法；1=IR 非法（closed-on-failure）；2=用法/IO/依赖输入错误。
"""


def _parse_cli(argv: Sequence[str]) -> Tuple[Dict[str, Any], Optional[str]]:
    """手工解析（同 strategy_health 判例：未知参数返回 rc=2，不抛 SystemExit）。"""
    options: Dict[str, Any] = {
        "file": None,
        "cards_file": None,
        "manifests": None,
        "json": False,
        "validate_only": False,
        "help": False,
    }
    index = 0
    value_flags = {
        "--file": "file",
        "--cards-file": "cards_file",
        "--manifests": "manifests",
    }
    while index < len(argv):
        arg = str(argv[index])
        if arg in ("-h", "--help"):
            options["help"] = True
            return options, None
        if arg in value_flags:
            if index + 1 >= len(argv):
                return options, "参数 %s 缺值" % arg
            options[value_flags[arg]] = str(argv[index + 1])
            index += 2
            continue
        if arg == "--json":
            options["json"] = True
            index += 1
            continue
        if arg == "--validate-only":
            options["validate_only"] = True
            index += 1
            continue
        return options, "未知参数: %s" % arg
    return options, None


def _strategy_summary(plan: CompiledPipeline) -> str:
    parts = []
    for strategy_id in sorted(plan.strategies):
        item = plan.strategies[strategy_id]
        version = (item.get("card") or {}).get("version")
        parts.append("%s@%s" % (strategy_id, version or "-"))
    return ", ".join(parts) or "-"


def _human_summary(ir: CompositionIR, plan: Optional[CompiledPipeline]) -> str:
    lines = [
        "[composition_ir] pipeline=%s version=%s owner=%s tenant=%s mode=%s"
        % (ir.pipeline_id, ir.version, ir.owner, ir.tenant, ir.mode),
        "[composition_ir] schema=%s nodes=%d edges=%d strategies=%d"
        % (
            ir.schema,
            len(ir.nodes),
            len(ir.edges),
            sum(1 for node in ir.nodes if node.type == "strategy"),
        ),
        "[composition_ir] 拓扑顺序: %s" % " -> ".join(ir.topological_order),
    ]
    if plan is not None:
        lines.append("[composition_ir] 策略解析: %s" % _strategy_summary(plan))
        lines.append(
            "[composition_ir] data_deps: %s"
            % (", ".join(plan.data_deps) or "-")
        )
        lines.append(
            "[composition_ir] 只读计划：不执行 / 不下单 / 不写 ledger / 不发布"
            "（publish.effective=false, would_publish=%s）"
            % str(plan.publish_plan.get("would_publish")).lower()
        )
    else:
        lines.append("[composition_ir] 仅校验：未编译（--validate-only）")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 入口：--file / --cards-file / --json / --validate-only；只读 + 闭死失败。"""
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    options, error = _parse_cli(argv)
    if error:
        print("[composition_ir] %s" % error, file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2
    if options["help"]:
        print(USAGE)
        return 0
    if not options["file"]:
        print("[composition_ir] 缺 --file IR JSON 路径", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2

    try:
        doc = load_ir_file(str(options["file"]))
    except (OSError, ValueError) as exc:
        print("[composition_ir] 读取 IR 失败: %s" % exc, file=sys.stderr)
        return 2

    cards: Optional[List[Dict[str, Any]]] = None
    cards_path = options["cards_file"] or None
    if cards_path:
        try:
            cards = _strategy_cards.load_cards(
                path=cards_path,
                manifests_dir=options["manifests"] or None,
            )
        except (OSError, ValueError) as exc:
            print("[composition_ir] 读取策略卡失败: %s" % exc, file=sys.stderr)
            return 2

    try:
        validated = validate_ir(
            doc,
            cards=cards,
            cards_path=cards_path,
            manifests_dir=options["manifests"] or None,
        )
        plan = None
        if not options["validate_only"]:
            plan = compile_ir(
                doc,
                cards=cards,
                cards_path=cards_path,
                manifests_dir=options["manifests"] or None,
                ir_path=str(options["file"]),
            )
    except IRValidationError as exc:
        if options["json"]:
            print(
                json.dumps(
                    {"ok": False, "schema": SCHEMA, "errors": exc.errors},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        print("[composition_ir] 非法 IR: %s" % exc, file=sys.stderr)
        return 1
    except ValueError as exc:
        print("[composition_ir] 校验输入非法: %s" % exc, file=sys.stderr)
        return 2

    if options["json"]:
        payload = {
            "ok": True,
            "validation": validated.to_dict(),
            "compiled": plan.to_dict() if plan is not None else None,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(_human_summary(validated, plan))
    return 0


__all__ = [
    "ALLOWED_MODES",
    "COMPILED_SCHEMA",
    "CompositionIR",
    "DEFAULT_CARDS_PATH",
    "DEFAULT_MANIFESTS_DIR",
    "EDGE_ALLOWED",
    "IRValidationError",
    "IREdge",
    "IRNode",
    "NODE_SPECS",
    "NODE_TYPES",
    "PORT_TYPES",
    "PUBLISH_VISIBILITIES",
    "SCHEMA",
    "SUPPORTED_SCHEMAS",
    "TERMINAL_NODE_TYPES",
    "TOP_LEVEL_REQUIRED",
    "CompiledPipeline",
    "compile_ir",
    "load_ir_file",
    "main",
    "topological_order",
    "validate_ir",
]


if __name__ == "__main__":
    raise SystemExit(main())
