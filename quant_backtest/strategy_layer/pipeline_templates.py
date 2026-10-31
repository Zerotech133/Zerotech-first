# -*- coding: utf-8 -*-
r"""Phase 2-E：组合流水线模板库（Composition IR 模板，只读）。

把常用组合范式沉淀为**可直接复用**的模板：每个模板是一条合法的
``composition_ir/v1`` IR（由 Phase 1-A ``composition_ir.validate_ir`` /
``compile_ir`` 验证），并额外声明三个参数化入口（tenant / weights / max_single）
的默认值。模板只在内存中物化：``load_template`` 返回 IR 深拷贝，参数覆写
**不落盘、不写 strategy_registry.json、不注册 adapter、不新增计划任务**。

模板（``pipeline_templates/v1``）
---------------------------------
- ``trend_pullback``：趋势回踩 —— trend_follow + filter/allocator/risk/execution；
- ``trend_reversal``：趋势+反转 —— trend_follow + rsi_reversal + filter/rank/
  allocator/risk/execution；
- ``nine_dim_chan``：九维+缠论 —— nine_dim + chan_monitor + chanlun_li122 +
  filter/rank/allocator/risk/execution。

安全红线（WO-PIPELINE-TEMPLATES-20261031）
------------------------------------------
- **只读**：不修改 ``strategy_cards.py/.json``、``composition_ir.py``、
  ``strategy_registry.json``、manifest 或任何交易链文件；不写盘（CLI 仅 stdout）；
  不新增计划任务、不重启服务。
- 每个模板 ``execution.params.dry_run=true``、``publish.enabled=false``；
  ``compile_ir`` 产物 ``readonly=True`` / ``side_effects=[]`` /
  ``safety.{executes,places_orders,writes_ledger,publishes}==False`` /
  ``publish_plan.effective=false``。
- **参数化只改内存副本**：``load_template(name, tenant=..., weights=...,
  max_single=...)`` 在返回的 IR 深拷贝上覆写，原 ``pipeline_templates.json``
  字节不变；校验失败一律抛 ``PipelineTemplateError``（closed-on-failure）。

模板 JSON 结构（``pipeline_templates/v1``）
------------------------------------------
::

    {
      "_meta": {"schema": "pipeline_templates/v1", "ir_schema": "composition_ir/v1", ...},
      "templates": [
        {
          "template_id": "trend_pullback",     # 必填，唯一标识符
          "title": "趋势回踩",                  # 必填，非空
          "description": "...",                 # 必填，字符串
          "parameters": {                       # 必填，三个参数化入口
            "tenant":     {"type": "str",   "default": "official", "description": "..."},
            "weights":    {"type": "dict",  "default": {...},       "description": "..."},
            "max_single": {"type": "float", "default": 8.0,         "description": "..."}
          },
          "ir": { ...合法 composition_ir/v1 文档... }
        }
      ]
    }

参数语义
--------
- ``tenant``：IR 顶层多租户隔离键（Phase 0-C 口径），覆写写回 ``ir.tenant``；
- ``weights``：allocator 节点策略权重表；键必须是本模板 strategy 节点的
  ``strategy_id`` 子集，值为正数；覆写写回每个 allocator 的
  ``params.scheme="weighted"`` + ``params.weights``；
- ``max_single``：单票上限（percent，(0,100]），覆写写回
  ``ir.risk_policy.max_position_pct`` 及 allocator ``params.max_single_pct``。

用法（只读）::

    from strategy_layer import pipeline_templates as pt
    from strategy_layer import composition_ir as ci

    names = [t["template_id"] for t in pt.list_templates()]
    ir = pt.load_template("trend_reversal", tenant="tenant-a",
                          weights={"trend_follow": 0.7, "rsi_reversal": 0.3},
                          max_single=12.5)
    cir = ci.validate_ir(ir)      # 合法 Composition IR
    plan = ci.compile_ir(ir)      # 只读计划（does not execute）
    report = pt.validate_templates()   # 3 模板全量 validate+compile 报告

命令行（只读，退出码：0=合法 / 1=模板内容非法 / 2=用法或 IO 错误）::

    python -X utf8 strategy_layer/pipeline_templates.py --list
    python -X utf8 strategy_layer/pipeline_templates.py --list --json
    python -X utf8 strategy_layer/pipeline_templates.py --template trend_pullback --json
    python -X utf8 strategy_layer/pipeline_templates.py --template trend_reversal \
        --tenant tenant-a --weights trend_follow=0.7,rsi_reversal=0.3 --max-single 12.5
    python -X utf8 strategy_layer/pipeline_templates.py --json      # 全库校验报告
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence

try:  # 只读复用 Phase 1-A Composition IR；脚本直跑时补 sys.path（同 composition_ir 判例）
    from strategy_layer import composition_ir as _composition_ir
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from strategy_layer import composition_ir as _composition_ir

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TEMPLATES_PATH = os.path.join(HERE, "pipeline_templates.json")
IR_SCHEMA = _composition_ir.SCHEMA

TEMPLATE_SCHEMA = "pipeline_templates/v1"
LIBRARY_KEYS = ("_meta", "templates")
TEMPLATE_FIELDS = ("template_id", "title", "description", "parameters", "ir")
PARAMETER_NAMES = ("tenant", "weights", "max_single")
_TEMPLATE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,63}$")
_TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
MAX_SINGLE_RANGE = (0.0, 100.0)

USAGE = """用法:
  python -X utf8 pipeline_templates.py --list [--json]
  python -X utf8 pipeline_templates.py --template NAME [--json]
        [--tenant T] [--max-single X] [--weights "k=v,k=v"]
  python -X utf8 pipeline_templates.py --json            # 全库 validate+compile 报告

只读：模板库只做内存物化/校验/打印；不写任何文件、不写 registry、不执行/不下单。
退出码：0=合法；1=模板内容非法（closed-on-failure）；2=用法或 IO/读取错误。
"""


# ── 异常 ──────────────────────────────────────────────────────────────
class PipelineTemplateError(ValueError):
    """模板库内容非法（closed-on-failure）；错误信息携带全部原因。"""


class TemplateNotFoundError(PipelineTemplateError):
    """查询的模板不存在。"""


# ── 基础读写 ──────────────────────────────────────────────────────────
def _load_json(path: str) -> Any:
    """读 UTF-8 JSON；缺失/损坏抛 ValueError（IO/输入错误）。"""
    if not path or not os.path.isfile(path):
        raise ValueError("模板库文件不存在: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValueError("模板库 JSON 读取失败 %s: %s" % (path, exc)) from exc


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_str_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(_is_nonempty_str(item) for item in value)
    )


def _validate_tenant(value: Any) -> str:
    """tenant 参数校验（与 Composition IR 的 tenant 规则一致）。"""
    if not _is_nonempty_str(value) or not _TENANT_RE.match(str(value)):
        raise PipelineTemplateError(
            "tenant 必须为合法标识符（字母/数字开头，允许 . _ -）: %r" % (value,)
        )
    return str(value)


def _validate_max_single(value: Any) -> float:
    """单票上限校验：数值且落在 (0, 100]。"""
    if not _is_number(value) or not (
        MAX_SINGLE_RANGE[0] < float(value) <= MAX_SINGLE_RANGE[1]
    ):
        raise PipelineTemplateError(
            "max_single 必须为 (0, 100] 内数值（percent），收到 %r" % (value,)
        )
    return float(value)


def _normalize_weights(
    weights: Any, strategy_ids: Sequence[str]
) -> Dict[str, float]:
    """权重参数校验：非空 dict、键属于本模板 strategy 节点、值为正数。"""
    if not isinstance(weights, Mapping) or not weights:
        raise PipelineTemplateError("weights 必须为非空 dict: %r" % (weights,))
    known = set(str(item) for item in strategy_ids)
    out: Dict[str, float] = {}
    for key, value in weights.items():
        if not _is_nonempty_str(key):
            raise PipelineTemplateError("weights 键必须为非空字符串: %r" % (key,))
        if key not in known:
            raise PipelineTemplateError(
                "weights 键 %r 不是本模板 strategy 节点（合法: %s）"
                % (key, ", ".join(sorted(known)) or "-")
            )
        if not _is_number(value) or float(value) <= 0:
            raise PipelineTemplateError(
                "weights[%r] 必须为正数，收到 %r" % (key, value)
            )
        out[str(key)] = float(value)
    return out


# ── 模板库加载（结构级，不做 IR 校验） ────────────────────────────────
def _load_library(path: Optional[str] = None) -> Dict[str, Any]:
    """只读加载并做结构校验；内容非法抛 PipelineTemplateError。"""
    templates_path = path or DEFAULT_TEMPLATES_PATH
    doc = _load_json(templates_path)
    if not isinstance(doc, Mapping):
        raise PipelineTemplateError("模板库顶层必须为 dict: %s" % templates_path)
    unknown = sorted(str(key) for key in doc if key not in LIBRARY_KEYS)
    if unknown:
        raise PipelineTemplateError("模板库含未知顶层字段: %s" % ", ".join(unknown))
    meta = doc.get("_meta")
    if not isinstance(meta, Mapping):
        raise PipelineTemplateError("模板库 _meta 必须为 dict")
    if meta.get("schema") != TEMPLATE_SCHEMA:
        raise PipelineTemplateError(
            "模板库 _meta.schema 必须为 %s，收到 %r"
            % (TEMPLATE_SCHEMA, meta.get("schema"))
        )
    if meta.get("ir_schema") != IR_SCHEMA:
        raise PipelineTemplateError(
            "模板库 _meta.ir_schema 必须为 %s，收到 %r"
            % (IR_SCHEMA, meta.get("ir_schema"))
        )
    raw_templates = doc.get("templates")
    if not isinstance(raw_templates, list) or not raw_templates:
        raise PipelineTemplateError("模板库 templates 必须为非空 list")

    seen: set = set()
    for position, raw in enumerate(raw_templates):
        where = "templates[%d]" % position
        if not isinstance(raw, Mapping):
            raise PipelineTemplateError("%s 必须为 dict" % where)
        unknown_fields = sorted(
            str(key) for key in raw if key not in TEMPLATE_FIELDS
        )
        if unknown_fields:
            raise PipelineTemplateError(
                "%s 含未知字段: %s" % (where, ", ".join(unknown_fields))
            )
        missing = [key for key in TEMPLATE_FIELDS if key not in raw]
        if missing:
            raise PipelineTemplateError("%s 缺字段: %s" % (where, ", ".join(missing)))
        tid = raw.get("template_id")
        if not _is_nonempty_str(tid) or not _TEMPLATE_ID_RE.match(str(tid)):
            raise PipelineTemplateError("%s template_id 非法: %r" % (where, tid))
        if tid in seen:
            raise PipelineTemplateError("模板 template_id 重复: %s" % tid)
        seen.add(str(tid))
        if not _is_nonempty_str(raw.get("title")):
            raise PipelineTemplateError("模板 %s 的 title 必须为非空字符串" % tid)
        if not isinstance(raw.get("description"), str):
            raise PipelineTemplateError("模板 %s 的 description 必须为字符串" % tid)
        if not isinstance(raw.get("ir"), Mapping):
            raise PipelineTemplateError("模板 %s 的 ir 必须为 dict" % tid)
        parameters = raw.get("parameters")
        if not isinstance(parameters, Mapping):
            raise PipelineTemplateError("模板 %s 的 parameters 必须为 dict" % tid)
        lost = [name for name in PARAMETER_NAMES if name not in parameters]
        if lost:
            raise PipelineTemplateError(
                "模板 %s 的 parameters 缺入口: %s" % (tid, ", ".join(lost))
            )
        for name in PARAMETER_NAMES:
            spec = parameters[name]
            if not isinstance(spec, Mapping) or "default" not in spec:
                raise PipelineTemplateError(
                    "模板 %s 的 parameters.%s 必须为含 default 的 dict" % (tid, name)
                )
    return dict(doc)


def _template_map(path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """模板 id → 模板 dict（深拷贝，调用方修改不影响后续加载）。"""
    library = _load_library(path)
    return {
        str(raw["template_id"]): copy.deepcopy(dict(raw))
        for raw in library["templates"]
    }


def get_template(
    template_id: str, path: Optional[str] = None
) -> Dict[str, Any]:
    """按 id 取单个模板（深拷贝）；不存在抛 :class:`TemplateNotFoundError`。"""
    index = _template_map(path)
    if template_id not in index:
        raise TemplateNotFoundError(
            "未知模板: %r（可选: %s）" % (template_id, ", ".join(sorted(index)) or "-")
        )
    return index[template_id]


# ── IR 辅助 ───────────────────────────────────────────────────────────
def _strategy_ids(ir: Mapping[str, Any]) -> List[str]:
    """IR 中 strategy 节点的 strategy_id 列表（保持节点顺序，去重）。"""
    out: List[str] = []
    nodes = ir.get("nodes")
    if not isinstance(nodes, list):
        return out
    for node in nodes:
        if not isinstance(node, Mapping) or node.get("type") != "strategy":
            continue
        sid = node.get("strategy_id")
        if _is_nonempty_str(sid) and str(sid) not in out:
            out.append(str(sid))
    return out


def _allocator_nodes(ir: Mapping[str, Any]) -> List[Dict[str, Any]]:
    nodes = ir.get("nodes")
    if not isinstance(nodes, list):
        return []
    return [
        node
        for node in nodes
        if isinstance(node, dict) and node.get("type") == "allocator"
    ]


def _execution_nodes(ir: Mapping[str, Any]) -> List[Dict[str, Any]]:
    nodes = ir.get("nodes")
    if not isinstance(nodes, list):
        return []
    return [
        node
        for node in nodes
        if isinstance(node, dict) and node.get("type") == "execution"
    ]


def _apply_parameters(
    ir: Dict[str, Any],
    tenant: Optional[str] = None,
    weights: Optional[Mapping[str, Any]] = None,
    max_single: Optional[Any] = None,
) -> Dict[str, Any]:
    """在 IR 深拷贝上应用参数覆写（仅内存）；返回实际应用的参数。"""
    applied: Dict[str, Any] = {}
    strategy_ids = _strategy_ids(ir)
    if tenant is not None:
        ir["tenant"] = _validate_tenant(tenant)
        applied["tenant"] = ir["tenant"]
    if weights is not None:
        normalized = _normalize_weights(weights, strategy_ids)
        allocators = _allocator_nodes(ir)
        if not allocators:
            raise PipelineTemplateError("模板 IR 无 allocator 节点，无法应用 weights")
        for node in allocators:
            params = node.get("params")
            params = dict(params) if isinstance(params, Mapping) else {}
            params["scheme"] = "weighted"
            params["weights"] = copy.deepcopy(normalized)
            node["params"] = params
        applied["weights"] = copy.deepcopy(normalized)
    if max_single is not None:
        limit = _validate_max_single(max_single)
        risk_policy = ir.get("risk_policy")
        if not isinstance(risk_policy, dict):
            raise PipelineTemplateError("模板 IR 的 risk_policy 必须为 dict")
        risk_policy["max_position_pct"] = limit
        for node in _allocator_nodes(ir):
            params = node.get("params")
            params = dict(params) if isinstance(params, Mapping) else {}
            params["max_single_pct"] = limit
            node["params"] = params
        applied["max_single"] = limit
    return applied


# ── 对外 API ──────────────────────────────────────────────────────────
def list_templates(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """列出模板元数据（结构性字段；不校验 IR —— 校验用 validate_templates）。"""
    infos: List[Dict[str, Any]] = []
    for template in _template_map(path).values():
        ir = template["ir"]
        nodes = ir.get("nodes")
        edges = ir.get("edges")
        mode = ir.get("mode")
        execution_nodes = _execution_nodes(ir)
        infos.append(
            {
                "template_id": template["template_id"],
                "title": template["title"],
                "description": template["description"],
                "mode": mode,
                "tenant": ir.get("tenant"),
                "strategy_ids": _strategy_ids(ir),
                "node_count": len(nodes) if isinstance(nodes, list) else 0,
                "edge_count": len(edges) if isinstance(edges, list) else 0,
                "execution_dry_run": bool(execution_nodes)
                and all(
                    isinstance(node.get("params"), Mapping)
                    and node["params"].get("dry_run") is True
                    for node in execution_nodes
                ),
                "publish_effective": not (
                    isinstance(ir.get("publish"), Mapping)
                    and ir["publish"].get("enabled") is False
                ),
                "readonly": True,
                "parameters": copy.deepcopy(template["parameters"]),
            }
        )
    return infos


def load_template(
    template_id: str,
    tenant: Optional[str] = None,
    weights: Optional[Mapping[str, Any]] = None,
    max_single: Optional[Any] = None,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """取模板并可选应用内存参数覆写 → 已校验的 Composition IR dict。

    参数
    ----
    template_id : str
        模板 id（trend_pullback / trend_reversal / nine_dim_chan）。
    tenant / weights / max_single : 可选
        内存覆写；语义见模块 docstring。``None`` 表示保留模板默认值。
    path : str | None
        模板库路径（缺省内置 pipeline_templates.json）。

    返回
    ----
    dict
        composition_ir/v1 文档（深拷贝；可直接交给
        ``composition_ir.validate_ir`` / ``compile_ir``）。只读，不落盘。

    Raises
    ------
    TemplateNotFoundError / PipelineTemplateError
        模板不存在或覆写参数非法 / 模板 IR 非法。
    """
    template = get_template(template_id, path)
    ir = copy.deepcopy(dict(template["ir"]))
    _apply_parameters(ir, tenant=tenant, weights=weights, max_single=max_single)
    _composition_ir.validate_ir(ir)
    return ir


def validate_templates(
    path: Optional[str] = None, raise_on_error: bool = True
) -> Dict[str, Any]:
    """全量校验模板库：validate_ir + compile_ir + 只读/参数安全口径。

    Parameters
    ----------
    path : str | None
        模板库路径（缺省内置）。
    raise_on_error : bool
        True（默认）时任一模板非法即抛 :class:`PipelineTemplateError`
        （携带全部错误）；False 时只返回报告（供 CLI 输出全量问题）。

    Returns
    -------
    dict
        ``{schema, ir_schema, readonly, source, total, all_ok, templates: [...]}``。
        每个模板项含 ``ok / errors / node_count / edge_count / strategy_ids /
        mode / execution_dry_run / publish_effective / readonly / safety /
        parameters``。
    """
    templates_path = path or DEFAULT_TEMPLATES_PATH
    library = _load_library(templates_path)
    results: List[Dict[str, Any]] = []
    failures: List[str] = []
    for template in library["templates"]:
        tid = str(template["template_id"])
        item: Dict[str, Any] = {
            "template_id": tid,
            "ok": True,
            "errors": [],
            "parameters": copy.deepcopy(template["parameters"]),
        }
        errors: List[str] = []
        ir = copy.deepcopy(dict(template["ir"]))
        plan = None
        try:
            validated = _composition_ir.validate_ir(ir)
            plan = _composition_ir.compile_ir(ir)
        except _composition_ir.IRValidationError as exc:
            errors.extend(exc.errors)
        except (ValueError, OSError) as exc:
            errors.append("校验/编译异常: %s" % exc)
        if plan is not None and not errors:
            # 只读/安全口径（任何一项不满足即 invalid）
            safety = dict(plan.safety)
            execution = dict(plan.execution)
            publish_plan = dict(plan.publish_plan)
            if plan.readonly is not True or tuple(plan.side_effects) != ():
                errors.append("编译产物必须 readonly=True 且 side_effects=[]")
            if any(bool(value) for value in safety.values()):
                errors.append("编译产物 safety 四开关必须全 false: %r" % safety)
            if execution.get("dry_run") is not True or execution.get("enabled") is not False:
                errors.append("编译产物 execution 必须 dry_run=True/enabled=False")
            if publish_plan.get("effective") is not False:
                errors.append("编译产物 publish_plan.effective 必须为 False")
            if isinstance(ir.get("publish"), Mapping) and ir["publish"].get("enabled") is not False:
                errors.append("模板 IR publish.enabled 必须为 false")
            execution_nodes = _execution_nodes(ir)
            if not execution_nodes:
                errors.append("模板 IR 缺 execution 节点")
            for node in execution_nodes:
                params = node.get("params")
                if not isinstance(params, Mapping) or params.get("dry_run") is not True:
                    errors.append(
                        "execution 节点 %s 必须显式 params.dry_run=true" % node.get("id")
                    )
            # 参数默认值合法
            params_spec = template["parameters"]
            strategy_ids = _strategy_ids(ir)
            try:
                _validate_tenant(params_spec["tenant"].get("default"))
            except PipelineTemplateError as exc:
                errors.append("parameters.tenant.default 非法: %s" % exc)
            try:
                _validate_max_single(params_spec["max_single"].get("default"))
            except PipelineTemplateError as exc:
                errors.append("parameters.max_single.default 非法: %s" % exc)
            try:
                _normalize_weights(params_spec["weights"].get("default"), strategy_ids)
            except PipelineTemplateError as exc:
                errors.append("parameters.weights.default 非法: %s" % exc)
            item.update(
                {
                    "node_count": len(ir.get("nodes") or []),
                    "edge_count": len(ir.get("edges") or []),
                    "strategy_ids": strategy_ids,
                    "mode": validated.mode,
                    "tenant": validated.tenant,
                    "topological_order": list(validated.topological_order),
                    "execution_dry_run": True,
                    "publish_effective": False,
                    "readonly": True,
                    "safety": safety,
                }
            )
        if errors:
            item["ok"] = False
            item["errors"] = list(errors)
            failures.append("%s: %s" % (tid, "；".join(item["errors"])))
        results.append(item)

    report: Dict[str, Any] = {
        "schema": TEMPLATE_SCHEMA,
        "ir_schema": IR_SCHEMA,
        "readonly": True,
        "source": os.path.abspath(templates_path),
        "total": len(results),
        "all_ok": not failures,
        "templates": results,
    }
    if failures and raise_on_error:
        raise PipelineTemplateError(["模板库校验失败: " + " | ".join(failures)])
    return report


def readonly_sources(path: Optional[str] = None) -> List[str]:
    """本模块声明**永不写入**的只读源文件清单（供测试/审计做字节级不变断言）。"""
    cards_module = _composition_ir._strategy_cards  # composition_ir 只读复用的同一模块
    sources = [
        os.path.abspath(path or DEFAULT_TEMPLATES_PATH),
        os.path.abspath(cards_module.DEFAULT_CARDS_PATH),
        os.path.abspath(cards_module.__file__),
        os.path.abspath(_composition_ir.__file__),
        os.path.abspath(cards_module.DEFAULT_REGISTRY_PATH),
    ]
    manifests_dir = cards_module.DEFAULT_MANIFESTS_DIR
    if os.path.isdir(manifests_dir):
        for name in sorted(os.listdir(manifests_dir)):
            if name.endswith(".manifest.json"):
                sources.append(os.path.abspath(os.path.join(manifests_dir, name)))
    return [item for item in sources if os.path.exists(item)]


# ── CLI（只读自检；closed-on-failure） ────────────────────────────────
def _parse_weights_arg(text: str) -> Dict[str, float]:
    """解析 ``--weights "k=v,k=v"`` → {str: float}；格式错误抛 ValueError。"""
    out: Dict[str, float] = {}
    for chunk in str(text).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ValueError("--weights 片段缺 '=': %r（期望 key=value）" % chunk)
        key, _, raw_value = chunk.partition("=")
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            raise ValueError("--weights 键为空: %r" % chunk)
        try:
            value = float(raw_value)
        except ValueError as exc:
            raise ValueError("--weights 值非数值: %r" % chunk) from exc
        if value <= 0:
            raise ValueError("--weights 值必须为正数: %r" % chunk)
        out[key] = value
    if not out:
        raise ValueError("--weights 不得为空")
    return out


def _human_list(infos: Sequence[Mapping[str, Any]]) -> str:
    lines = ["[pipeline_templates] templates=%d（只读模板库）" % len(infos)]
    for info in infos:
        lines.append(
            "  - %-14s %s | strategies=%s nodes=%d edges=%d dry_run=%s publish_effective=%s"
            % (
                info["template_id"],
                info["title"],
                ",".join(info["strategy_ids"]) or "-",
                info["node_count"],
                info["edge_count"],
                str(info["execution_dry_run"]).lower(),
                str(info["publish_effective"]).lower(),
            )
        )
    return "\n".join(lines)


def _human_template(template_id: str, ir: Mapping[str, Any]) -> str:
    validated = _composition_ir.validate_ir(ir)
    strategy_ids = _strategy_ids(ir)
    lines = [
        "[pipeline_templates] template=%s pipeline_id=%s tenant=%s"
        % (template_id, validated.pipeline_id, validated.tenant),
        "[pipeline_templates] mode=%s strategies=%s nodes=%d edges=%d"
        % (
            validated.mode,
            ", ".join(strategy_ids) or "-",
            len(validated.nodes),
            len(validated.edges),
        ),
        "[pipeline_templates] 拓扑顺序: %s" % " -> ".join(validated.topological_order),
        "[pipeline_templates] 只读：execution dry_run=true / publish effective=false"
        "（不执行、不下单、不写 ledger、不写 registry）",
    ]
    return "\n".join(lines)


def _human_report(report: Mapping[str, Any]) -> str:
    lines = [
        "[pipeline_templates] validate_templates: total=%d all_ok=%s ir_schema=%s"
        % (report["total"], str(report["all_ok"]).lower(), report["ir_schema"])
    ]
    for item in report["templates"]:
        status = "OK" if item["ok"] else "FAIL"
        detail = (
            "strategies=%s nodes=%d edges=%d"
            % (
                ",".join(item.get("strategy_ids") or []) or "-",
                item.get("node_count", 0),
                item.get("edge_count", 0),
            )
            if item["ok"]
            else "；".join(item.get("errors") or [])
        )
        lines.append("  - [%s] %s %s" % (status, item["template_id"], detail))
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI：--list / --template / --json（+ 可选参数覆写）；只读 stdout。"""
    parser = argparse.ArgumentParser(
        description="Phase 2-E 只读组合流水线模板库（3 个 Composition IR 模板）"
    )
    parser.add_argument("--list", action="store_true", help="列出模板元数据")
    parser.add_argument("--template", default="", help="物化指定模板并打印摘要/IR")
    parser.add_argument("--json", action="store_true", help="输出 JSON（不写盘）")
    parser.add_argument("--path", default="", help="模板库 JSON 路径（缺省内置，只读）")
    parser.add_argument("--tenant", default="", help="覆写 tenant（仅 --template）")
    parser.add_argument(
        "--max-single", type=float, default=None, help="覆写单票上限 %%（仅 --template）"
    )
    parser.add_argument(
        "--weights", default="", help='覆写权重 "k=v,k=v"（仅 --template）'
    )
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:  # 测试直调 main 时不抛 SystemExit
        code = exc.code
        return 2 if code is None else (code if isinstance(code, int) else 2)

    library_path = args.path or None
    try:
        if args.template:
            weights = _parse_weights_arg(args.weights) if args.weights else None
            ir = load_template(
                args.template,
                tenant=args.tenant or None,
                weights=weights,
                max_single=args.max_single,
                path=library_path,
            )
            applied = {
                "tenant": args.tenant or None,
                "weights": copy.deepcopy(weights),
                "max_single": args.max_single,
            }
            if args.json:
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "schema": TEMPLATE_SCHEMA,
                            "ir_schema": IR_SCHEMA,
                            "template_id": args.template,
                            "applied_overrides": applied,
                            "readonly": True,
                            "ir": ir,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0
            print(_human_template(args.template, ir))
            return 0
        if args.list:
            infos = list_templates(library_path)
            if args.json:
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "schema": TEMPLATE_SCHEMA,
                            "ir_schema": IR_SCHEMA,
                            "readonly": True,
                            "templates": infos,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0
            print(_human_list(infos))
            return 0
        report = validate_templates(library_path, raise_on_error=False)
        if args.json:
            print(
                json.dumps({"ok": report["all_ok"], "report": report},
                           ensure_ascii=False, indent=2)
            )
            return 0 if report["all_ok"] else 1
        print(_human_report(report))
        return 0 if report["all_ok"] else 1
    except TemplateNotFoundError as exc:
        print("[pipeline_templates] %s" % exc, file=sys.stderr)
        return 2
    except PipelineTemplateError as exc:
        if args.json:
            print(
                json.dumps(
                    {"ok": False, "errors": [str(exc)]}, ensure_ascii=False, indent=2
                )
            )
        print("[pipeline_templates] 模板非法: %s" % exc, file=sys.stderr)
        return 1
    except _composition_ir.IRValidationError as exc:
        if args.json:
            print(
                json.dumps(
                    {"ok": False, "errors": list(exc.errors)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        print("[pipeline_templates] 模板 IR 非法: %s" % exc, file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print("[pipeline_templates] 用法/读取错误: %s" % exc, file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2


__all__ = [
    "DEFAULT_TEMPLATES_PATH",
    "IR_SCHEMA",
    "LIBRARY_KEYS",
    "MAX_SINGLE_RANGE",
    "PARAMETER_NAMES",
    "PipelineTemplateError",
    "TEMPLATE_FIELDS",
    "TEMPLATE_SCHEMA",
    "TemplateNotFoundError",
    "USAGE",
    "get_template",
    "list_templates",
    "load_template",
    "main",
    "readonly_sources",
    "validate_templates",
]


if __name__ == "__main__":
    raise SystemExit(main())
