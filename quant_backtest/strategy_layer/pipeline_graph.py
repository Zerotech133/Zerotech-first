# -*- coding: utf-8 -*-
r"""Phase 1-B：只读 Pipeline Graph 导出（WO-PIPELINE-GRAPH-20261031）。

把已合并的 ``strategy_cards``（10 张归一卡）+ ``strategy_registry.json``
（enabled/weights/top_n）+ 现有 ``pipeline_map.html`` 的层/节点静态定义，
导出为机器可读 Graph JSON，供 ``pipeline_map.html`` 优先 fetch 渲染
（fetch 失败保留原静态 fallback）。

设计红线（本单硬约束）
----------------------
- **只读**：只读 ``strategy_cards.json`` / ``strategy_registry.json`` /
  ``strategies/manifests/``；不写 registry、不改卡片、不注册 adapter、
  不改交易链；唯一允许的落盘 = 调用方显式 ``--out <path>``（缺省不写盘）。
- **不改运行语义**：默认不启用任何新增策略；节点只是把当前 registry
  enabled/shadow 口径可视化，不构成运行参数变更。
- 不新增计划任务、不重启服务、不引入第三方依赖（仅标准库）。
- 缺数据/口径漂移一律显式 ``ValueError``（fail-visible，不静默降级）。

Graph JSON 契约（``pipeline_graph/v1``）
---------------------------------------
::

    {
      "nodes": [
        {
          "id": "layer:L0" | "L0:tushare_sync" | "strategy:chan_monitor" | ...,
          "type": "layer" | "pipeline" | "strategy" | "slot",
          "label": "人类可读名",
          "status": "live" | "shadow" | "overlay" | "planned",
          "layer": "L0".."L5",
          "strategy_id": "chan_monitor",          # 仅 type=strategy 必填
          "card": true,                            # 仅 type=strategy：是否 10 卡之一
          "detail": "显示用补充说明（可选）",
          "schedule": "计划任务时段（可选）",
          "href": "/",                             # 可选跳转
          "metrics": {...}                         # 可选：权重/容量/版本等数值元数据
        }, ...
      ],
      "edges": [
        {"from": "<node id>", "to": "<node id>", "port": "...", "label": "...",
         "kind": "forward|feedback|shadow|overlay"}, ...
      ],
      "meta": {
        "schema_version": "1.0.0",
        "version": "1.0.0",
        "wo": "WO-PIPELINE-GRAPH-20261031",
        "phase": "Phase 1-B",
        "generated_at": "2026-10-31T...+08:00",
        "readonly": true,
        "generator": "quant_backtest/strategy_layer/pipeline_graph.py",
        "source": {"cards": ..., "registry": ..., "manifests": ...,
                   "cards_module": ..., "page": ...},
        "layers": [{"id","label","desc","color","order"}, ...],
        "node_contract": {...}, "edge_contract": {...},
        "status_values": ["live","shadow","overlay","planned"],
        "counts": {...}          # 与顶层 counts 同值（镜像，防消费方只读 meta）
      },
      "counts": {
        "nodes","edges","layers","layer_nodes","cards","enabled","shadow",
        "shadow_candidates","overlay","strategy_nodes","registry_only_enabled",
        "pipeline_nodes","slot_nodes","planned"
      }
    }

口径（当前基线：10 cards / 2 enabled / 8 shadow）
-------------------------------------------------
- ``counts.cards`` = 10 张 Strategy Card 节点数（``type=strategy & card=true``）。
- ``counts.enabled`` = 2 = registry ``enabled`` 条目数（kg_boost + chan_monitor）；
  其中 kg_boost 无 manifest 卡（adapter-only，节点 ``card=false``）。
- ``counts.shadow`` = 8 = strategy_cards ``shadow_candidates`` 口径
  （10 - 1 在册启用卡 chan_monitor - 1 非选股 overlay vol_target）。
- strategy 节点 status：registry enabled → ``live``；影子候选 → ``shadow``；
  非选股 overlay（vol_target）→ ``overlay``。因此
  ``counts.shadow`` 恰等于 status=shadow 的卡节点数（不含 overlay）。

用法（在 quant_backtest 目录下，或任意目录显式加 sys.path）::

    from strategy_layer.pipeline_graph import build_graph, validate_graph

    graph = build_graph()                 # 只读构建（不写盘）
    validate_graph(graph)                 # schema/引用/counts 校验，非法抛 ValueError
    graph["counts"]                       # {"cards": 10, "enabled": 2, "shadow": 8, ...}

命令行::

    python -X utf8 strategy_layer/pipeline_graph.py                 # 只读摘要（不写盘）
    python -X utf8 strategy_layer/pipeline_graph.py --json          # 打印 Graph JSON
    python -X utf8 strategy_layer/pipeline_graph.py --out strategy_layer/pipeline_graph.json
    python -X utf8 strategy_layer/pipeline_graph.py --check        # 已提交 JSON 与源同步校验
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
QUANT_ROOT = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(QUANT_ROOT)
if QUANT_ROOT not in sys.path:  # 直接脚本运行（python strategy_layer/pipeline_graph.py）兜底
    sys.path.insert(0, QUANT_ROOT)

from strategy_layer import strategy_cards  # noqa: E402  （只读复用卡片/registry 视图）

DEFAULT_GRAPH_PATH = os.path.join(HERE, "pipeline_graph.json")
DEFAULT_PAGE_PATH = os.path.join(HERE, "pipeline_map.html")

# ── 图契约版本 / 工单 ─────────────────────────────────────────────────
GRAPH_SCHEMA_VERSION = "1.0.0"
GRAPH_VERSION = "1.0.0"
WORK_ORDER = "WO-PIPELINE-GRAPH-20261031"
PHASE = "Phase 1-B"

NODE_TYPES = ("layer", "pipeline", "strategy", "slot")
STATUS_VALUES = ("live", "shadow", "overlay", "planned")
EDGE_KINDS = ("forward", "feedback", "shadow", "overlay")

EDGE_PORTS = (
    "rows",
    "scores_input",
    "signals",
    "strategy_scores",
    "verdicts",
    "feedback",
    "adapter",
    "score",
    "shadow",
    "overlay",
)

# 当前验收基线（只用于测试/文档；build_graph 不因基线漂移拒跑，
# 而是保证「节点 status 与 registry/cards 视图一致」——registry 变更后
# 重跑导出即可，测试基线显式失败提醒更新快照）。
BASELINE_COUNTS = {"cards": 10, "enabled": 2, "shadow": 8}

# ── 层定义（与 pipeline_map.html 现有层带一一对应）────────────────────
LAYERS: Tuple[Dict[str, str], ...] = (
    {
        "id": "L0",
        "label": "采集层",
        "desc": "原始数据进门 · 计划任务驱动 · 失败必告警",
        "color": "#1a73e8",
    },
    {
        "id": "L1",
        "label": "存储层",
        "desc": "数仓 210 表 · 数据池=数仓整体（7 子模块）",
        "color": "#188038",
    },
    {
        "id": "L2",
        "label": "情报服务层",
        "desc": "数据→信息→情报 · 逐跳置信度",
        "color": "#e8710a",
    },
    {
        "id": "L3",
        "label": "策略适配层",
        "desc": "可插拔 · 下方开关与权重可实时调整（写回 registry，07:30 生效）",
        "color": "#7b52d0",
    },
    {
        "id": "L4",
        "label": "组合裁决层",
        "desc": "加权融合 + 分歧标注 · upsert 双路径",
        "color": "#d93025",
    },
    {
        "id": "L5",
        "label": "前端反馈层",
        "desc": "8791 仪表盘 · 反馈闭环入口",
        "color": "#0b8043",
    },
)
LAYER_IDS = tuple(layer["id"] for layer in LAYERS)

# ── 非策略节点（内容取自现有 pipeline_map.html，只是搬成机器可读定义）──
PIPELINE_NODES: Dict[str, Tuple[Dict[str, Any], ...]] = {
    "L0": (
        {
            "id": "L0:tushare_sync",
            "label": "TushareSync",
            "schedule": "每日 ~18:10",
            "detail": "→ PG + Parquet（日线/基本面）",
        },
        {
            "id": "L0:tdx_sync",
            "label": "TdxSync",
            "schedule": "每日盘后",
            "detail": "→ 本地行情库镜像",
        },
        {
            "id": "L0:chanlun_scan",
            "label": "ChanlunScan",
            "schedule": "每日 05:50（约45min）",
            "detail": "→ chanlun_pool.jsonl（买卖点信号）",
        },
        {
            "id": "L0:xmap_sync",
            "label": "XmapSync",
            "schedule": "每日 06:00",
            "detail": "→ xmap 实体对齐（PG 5442）",
        },
        {
            "id": "L0:xueqiu_sync",
            "label": "XueqiuSync",
            "schedule": "多时段 · 三级token链",
            "detail": "→ 帖流/热帖/行情快照",
        },
        {
            "id": "L0:jev_feed",
            "label": "JevFeed / GoldShadow / AdjFactorSync",
            "schedule": "事件门禁 · 黄金影子 · 复权因子",
            "detail": "→ news.db / 数仓",
        },
    ),
    "L1": (
        {
            "id": "L1:pg5442",
            "label": "PG 5442",
            "detail": "quant_system 主库 · 血缘账本 · strategy schema",
        },
        {
            "id": "L1:neo4j",
            "label": "Neo4j",
            "detail": "KG 147万节点 · Security-MAPS_TO-Concept",
        },
        {
            "id": "L1:parquet",
            "label": "Parquet 湖",
            "detail": "data/ashare 单票后复权 · ts_daily · kg_scores",
        },
        {
            "id": "L1:mysql3307",
            "label": "MySQL 3307",
            "detail": "只读镜像（内网 VPN 依赖）",
        },
        {
            "id": "L1:duckdb",
            "label": "DuckDB",
            "detail": "分析湖",
        },
    ),
    "L2": (
        {
            "id": "L2:kg_analysis",
            "label": "KGAnalysis",
            "schedule": "每日 07:00",
            "detail": "Louvain 社区 + PageRank → kg_scores.parquet",
        },
        {
            "id": "L2:jev_gate",
            "label": "Jev 事件门禁",
            "detail": "事件分级 · rc≠0 告警",
        },
        {
            "id": "L2:mcp_server",
            "label": "MCP Server",
            "detail": "11 工具（含 pool_overview）",
        },
        {
            "id": "L2:confidence_engine",
            "label": "置信度传播引擎",
            "detail": "融合增益 ×1.15/冲突 ×0.7 · 半衰期 · 事故惩罚",
        },
    ),
    "L3": (),
    "L4": (
        {
            "id": "L4:signal_aggregator",
            "label": "SignalAggregator",
            "schedule": "StrategyDaily 每日 07:30",
            "detail": "组合分 = Σ wᵢ×scoreᵢ · 冲突出分歧标记不强扭",
        },
        {
            "id": "L4:signals_combined",
            "label": "signals_combined",
            "detail": "PG strategy schema · PK(trade_date,symbol) · contributions jsonb",
        },
        {
            "id": "L4:risk_guard",
            "label": "RiskGuard 共享位",
            "status": "planned",
            "detail": "规划中 · 四规则解耦后全策略前置",
        },
    ),
    "L5": (
        {
            "id": "L5:signal_tabs",
            "label": "多策略信号页签",
            "detail": "组合信号对比 · 置信徽章 · 分歧看板",
            "href": "/",
        },
        {
            "id": "L5:feedback_controls",
            "label": "反馈控件",
            "detail": "采纳/驳回/静默 → strategy.feedback",
        },
        {
            "id": "L5:weight_updater",
            "label": "WeightUpdater",
            "status": "shadow",
            "schedule": "每周日 20:00 · 影子期 dry-run",
            "detail": "双通道 + EWMA α=0.3 → 建议权重（--apply 待满窗）",
        },
    ),
}

# L3 预留插槽（非 10 卡，非 registry 条目；只读登记，status=planned）
SLOT_NODES: Tuple[Dict[str, Any], ...] = (
    {
        "id": "L3:dual_blade",
        "label": "dual_blade 量子双刀",
        "type": "slot",
        "status": "planned",
        "layer": "L3",
        "detail": "预留插槽 · Scanner→Screener→Signal 包装即接入",
    },
    {
        "id": "L3:llm_agent",
        "label": "llm_agent AI选股",
        "type": "slot",
        "status": "planned",
        "layer": "L3",
        "detail": "预留插槽 · Ollama qwen2.5:7b + MCP 工具调用",
    },
)

# 层间流动边（与页面 flow 通道一一对应；L5→L3 为反馈回流环）
LAYER_FLOWS: Tuple[Dict[str, str], ...] = (
    {
        "from": "L0",
        "to": "L1",
        "port": "rows",
        "label": "原始行 · parquet / PG 行 / jsonl",
        "kind": "forward",
    },
    {
        "from": "L1",
        "to": "L2",
        "port": "scores_input",
        "label": "打分输入 · 图查询 · 事件流",
        "kind": "forward",
    },
    {
        "from": "L2",
        "to": "L3",
        "port": "signals",
        "label": "标准化 Signal（score 0-100 · confidence 0-1 · rationale）",
        "kind": "forward",
    },
    {
        "from": "L3",
        "to": "L4",
        "port": "strategy_scores",
        "label": "启用策略并行 score（失败降级跳过）",
        "kind": "forward",
    },
    {
        "from": "L4",
        "to": "L5",
        "port": "verdicts",
        "label": "组合建议 + 各策略意见 + 分歧标记",
        "kind": "forward",
    },
    {
        "from": "L5",
        "to": "L3",
        "port": "feedback",
        "label": "权重进化回流：feedback + 后验收益 → wᵢ ← EWMA（回到 L3）",
        "kind": "feedback",
    },
)

# 策略节点展示名（machine id → 人类可读 label；未知 id 原样回落）
STRATEGY_LABELS: Dict[str, str] = {
    "kg_boost": "kg_boost 图谱社区打分",
    "chan_monitor": "chan_monitor 盯盘缠论",
    "trend_follow": "trend_follow 趋势跟随",
    "boll_breakout": "boll_breakout 布林突破",
    "rsi_reversal": "rsi_reversal RSI 反转",
    "ma_cross": "ma_cross 双均线交叉",
    "vol_target": "vol_target 波动率目标缩放",
    "chanlun_li122": "chanlun_li122 缠论多级别",
    "nine_dim": "nine_dim 九维拥挤预警",
    "qpzm": "qpzm 相对强弱",
    "rps_resonance": "rps_resonance RPS 共振",
}

NODE_CONTRACT = {
    "required": ["id", "type", "label", "status", "layer"],
    "optional": ["strategy_id", "card", "detail", "schedule", "href", "metrics"],
    "type_values": list(NODE_TYPES),
    "status_values": list(STATUS_VALUES),
    "layer_values": list(LAYER_IDS),
    "strategy_status_rule": (
        "type=strategy 时：registry enabled → live；影子候选 → shadow；"
        "非选股 overlay（signal_schema.kind=position_scale）→ overlay。"
    ),
}
EDGE_CONTRACT = {
    "required": ["from", "to", "port", "label"],
    "optional": ["kind"],
    "port_values": list(EDGE_PORTS),
    "kind_values": list(EDGE_KINDS),
}


# ── 基础校验工具 ──────────────────────────────────────────────────────
def _require(cond: Any, msg: str) -> None:
    """断言失败统一抛 ValueError（与 strategy_cards 同风格，单一异常类型）。"""
    if not cond:
        raise ValueError(msg)


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _now_iso(now: Any = None) -> str:
    """generated_at 口径：本地时区 ISO8601（秒精度）；可注入固定值便于测试。"""
    if now is None:
        return datetime.now().astimezone().replace(microsecond=0).isoformat()
    if isinstance(now, str):
        _require(bool(now.strip()), "generated_at 不能为空字符串")
        return now
    if isinstance(now, datetime):
        if now.tzinfo is None:
            now = now.astimezone()
        return now.replace(microsecond=0).isoformat()
    raise ValueError("now 仅接受 None / datetime / ISO8601 字符串，收到 %r" % (now,))


def _rel_source(path: str) -> str:
    """源文件路径统一转仓库根相对 POSIX 形式（跨 worktree 稳定，便于 diff）。"""
    try:
        rel = os.path.relpath(os.path.abspath(path), REPO_ROOT)
    except ValueError:  # Windows 跨盘符无相对路径
        rel = os.path.abspath(path)
    return rel.replace(os.sep, "/")


# ── 节点构建 ──────────────────────────────────────────────────────────
def _layer_node(layer: Mapping[str, str]) -> Dict[str, Any]:
    lid = str(layer["id"])
    return {
        "id": "layer:" + lid,
        "type": "layer",
        "label": "%s %s" % (lid, layer["label"]),
        "status": "live",
        "layer": lid,
        "metrics": {
            "title": layer["label"],
            "desc": layer["desc"],
            "color": layer["color"],
        },
    }


def _pipeline_node(layer_id: str, spec: Mapping[str, Any]) -> Dict[str, Any]:
    node: Dict[str, Any] = {
        "id": str(spec["id"]),
        "type": "pipeline",
        "label": str(spec["label"]),
        "status": str(spec.get("status") or "live"),
        "layer": layer_id,
    }
    for key in ("detail", "schedule", "href"):
        if spec.get(key):
            node[key] = str(spec[key])
    if spec.get("metrics"):
        node["metrics"] = dict(spec["metrics"])
    return node


def _ordered_strategy_ids(view: Mapping[str, Any]) -> List[str]:
    """策略节点顺序：registry enabled → shadow 候选 → 非选股 overlay。"""
    ordered: List[str] = []
    for sid in list(view["enabled_ids"]):
        ordered.append(str(sid))
    for sid in list(view["shadow_candidate_ids"]):
        ordered.append(str(sid))
    for sid in list(view["non_signal_overlay_ids"]):
        ordered.append(str(sid))
    deduped: List[str] = []
    seen = set()
    for sid in ordered:
        if sid in seen:
            continue
        seen.add(sid)
        deduped.append(sid)
    expected = set(str(sid) for sid in view["card_ids"]) | set(
        str(sid) for sid in view["registry_only_enabled_ids"]
    )
    _require(
        set(deduped) == expected,
        "策略归类覆盖异常：归类=%s 期望=%s" % (sorted(deduped), sorted(expected)),
    )
    return deduped


def _strategy_node(
    sid: str,
    card: Optional[Mapping[str, Any]],
    view: Mapping[str, Any],
    weights: Mapping[str, Any],
    top_n: Mapping[str, Any],
    enabled_set: set,
    shadow_set: set,
    overlay_set: set,
) -> Dict[str, Any]:
    if sid in enabled_set:
        status = "live"
    elif sid in overlay_set:
        status = "overlay"
    elif sid in shadow_set:
        status = "shadow"
    else:  # pragma: no cover - _ordered_strategy_ids 已保证覆盖
        raise ValueError("策略 %s 无法归类 live/shadow/overlay" % sid)

    metrics: Dict[str, Any] = {
        "enabled": sid in enabled_set,
        "shadow_candidate": sid in shadow_set,
        "overlay": sid in overlay_set,
        "weight": None,
        "top_n": None,
    }
    if sid in weights and weights[sid] is not None:
        metrics["weight"] = float(weights[sid])
    if sid in top_n and top_n[sid] is not None:
        metrics["top_n"] = top_n[sid]

    node: Dict[str, Any] = {
        "id": "strategy:" + sid,
        "type": "strategy",
        "label": STRATEGY_LABELS.get(sid, sid),
        "status": status,
        "layer": "L3",
        "strategy_id": sid,
        "card": card is not None,
    }
    if card is not None:
        metrics["version"] = str(card["version"])
        metrics["horizon_days"] = int(card["horizon_days"])
        metrics["modes"] = [str(mode) for mode in card["modes"]]
        metrics["capacity_timeout_s"] = float(card["capacity"]["timeout_s"])
        node["detail"] = "%s · %s · horizon %dd · v%s" % (
            "/".join(str(item) for item in card["universe"]),
            card["frequency"],
            int(card["horizon_days"]),
            card["version"],
        )
    else:
        metrics["registry_only"] = True
        top_n_hint = metrics.get("top_n")
        node["detail"] = "adapter-only（无 manifest 卡）· Neo4j 社区分位 → top %s" % (
            top_n_hint if top_n_hint is not None else "-"
        )
    node["metrics"] = metrics
    return node


# ── 边构建 ────────────────────────────────────────────────────────────
def _build_edges(nodes: Sequence[Mapping[str, Any]]) -> List[Dict[str, str]]:
    edges: List[Dict[str, str]] = []
    for flow in LAYER_FLOWS:
        edges.append(
            {
                "from": "layer:" + flow["from"],
                "to": "layer:" + flow["to"],
                "port": flow["port"],
                "label": flow["label"],
                "kind": flow["kind"],
            }
        )
    for node in nodes:
        if node.get("type") != "strategy":
            continue
        status = node.get("status")
        if status == "live":
            edges.append(
                {
                    "from": "layer:L3",
                    "to": str(node["id"]),
                    "port": "adapter",
                    "label": "registry 装配 · enabled",
                    "kind": "forward",
                }
            )
            edges.append(
                {
                    "from": str(node["id"]),
                    "to": "layer:L4",
                    "port": "score",
                    "label": "score × wᵢ → 加权融合",
                    "kind": "forward",
                }
            )
        elif status == "shadow":
            edges.append(
                {
                    "from": "layer:L3",
                    "to": str(node["id"]),
                    "port": "shadow",
                    "label": "影子候选 · 未启用（只读登记）",
                    "kind": "shadow",
                }
            )
        elif status == "overlay":
            edges.append(
                {
                    "from": "layer:L3",
                    "to": str(node["id"]),
                    "port": "overlay",
                    "label": "仓位缩放 overlay（只读登记）",
                    "kind": "overlay",
                }
            )
    return edges


# ── counts / meta ─────────────────────────────────────────────────────
def _build_counts(
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    view: Mapping[str, Any],
) -> Dict[str, int]:
    strategy_nodes = [node for node in nodes if node.get("type") == "strategy"]
    card_nodes = [node for node in strategy_nodes if node.get("card")]
    counts = {
        "nodes": len(nodes),
        "edges": len(edges),
        "layers": len(LAYERS),
        "layer_nodes": len([node for node in nodes if node.get("type") == "layer"]),
        "cards": len(card_nodes),
        "enabled": len([node for node in strategy_nodes if node.get("status") == "live"]),
        "shadow": len([node for node in card_nodes if node.get("status") == "shadow"]),
        "shadow_candidates": len(
            [node for node in strategy_nodes if node.get("status") == "shadow"]
        ),
        "overlay": len(
            [node for node in strategy_nodes if node.get("status") == "overlay"]
        ),
        "strategy_nodes": len(strategy_nodes),
        "registry_only_enabled": len(
            [
                node
                for node in strategy_nodes
                if (node.get("metrics") or {}).get("registry_only")
            ]
        ),
        "pipeline_nodes": len([node for node in nodes if node.get("type") == "pipeline"]),
        "slot_nodes": len([node for node in nodes if node.get("type") == "slot"]),
        "planned": len([node for node in nodes if node.get("status") == "planned"]),
    }
    # 节点 status 口径必须与 strategy_cards 只读视图严格一致（防漂移）
    _require(
        counts["cards"] == int(view["total_cards"]),
        "cards 口径漂移：graph=%d cards_view=%d"
        % (counts["cards"], int(view["total_cards"])),
    )
    _require(
        counts["enabled"] == int(view["enabled"]),
        "enabled 口径漂移：graph=%d registry_view=%d"
        % (counts["enabled"], int(view["enabled"])),
    )
    _require(
        counts["shadow"] == counts["shadow_candidates"] == int(view["shadow_candidates"]),
        "shadow 口径漂移：graph=%d/%d view=%d"
        % (counts["shadow"], counts["shadow_candidates"], int(view["shadow_candidates"])),
    )
    _require(
        counts["overlay"] == len(list(view["non_signal_overlay_ids"])),
        "overlay 口径漂移：graph=%d view=%d"
        % (counts["overlay"], len(list(view["non_signal_overlay_ids"]))),
    )
    return counts


def _build_meta(
    counts: Mapping[str, int],
    cards_path: str,
    registry_path: str,
    manifests_dir: str,
    generated_at: str,
) -> Dict[str, Any]:
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "version": GRAPH_VERSION,
        "wo": WORK_ORDER,
        "phase": PHASE,
        "generated_at": generated_at,
        "readonly": True,
        "generator": _rel_source(os.path.join(HERE, "pipeline_graph.py")),
        "source": {
            "cards": _rel_source(cards_path),
            "registry": _rel_source(registry_path),
            "manifests": _rel_source(manifests_dir),
            "cards_module": _rel_source(os.path.join(HERE, "strategy_cards.py")),
            "page": _rel_source(DEFAULT_PAGE_PATH),
        },
        "layers": [
            {
                "id": layer["id"],
                "label": layer["label"],
                "desc": layer["desc"],
                "color": layer["color"],
                "order": index,
            }
            for index, layer in enumerate(LAYERS)
        ],
        "node_contract": copy.deepcopy(NODE_CONTRACT),
        "edge_contract": copy.deepcopy(EDGE_CONTRACT),
        "status_values": list(STATUS_VALUES),
        "counts": dict(counts),  # 镜像顶层 counts，便于只读 meta 的消费方
    }


# ── 构建入口 ──────────────────────────────────────────────────────────
def build_graph(
    cards: Optional[Sequence[Mapping[str, Any]]] = None,
    registry_path: Optional[str] = None,
    cards_path: Optional[str] = None,
    manifests_dir: Optional[str] = None,
    now: Any = None,
) -> Dict[str, Any]:
    """只读构建 Graph JSON（不写任何文件；落盘由调用方显式 ``write_graph``）。

    Parameters
    ----------
    cards : list[dict] | None
        已加载的 Strategy Card；缺省经 ``strategy_cards.load_cards`` 只读加载。
    registry_path / cards_path / manifests_dir : str | None
        只读源路径覆盖（测试可注入临时文件）。
    now : None | datetime | str
        ``meta.generated_at`` 注入点（测试用固定值保证可复现）。

    Raises
    ------
    ValueError
        卡片/registry 不可用、策略归类异常、counts 口径漂移（fail-visible）。
    """
    items = (
        list(cards)
        if cards is not None
        else strategy_cards.load_cards(cards_path, manifests_dir)
    )
    view = strategy_cards.registry_view(
        cards=items,
        registry_path=registry_path,
        cards_path=cards_path,
        manifests_dir=manifests_dir,
    )
    card_index = strategy_cards.load_card_index(cards=items)
    weights = view.get("weights") if isinstance(view.get("weights"), Mapping) else {}
    top_n = view.get("top_n") if isinstance(view.get("top_n"), Mapping) else {}
    enabled_set = set(str(sid) for sid in view["enabled_ids"])
    shadow_set = set(str(sid) for sid in view["shadow_candidate_ids"])
    overlay_set = set(str(sid) for sid in view["non_signal_overlay_ids"])

    nodes: List[Dict[str, Any]] = []
    for layer in LAYERS:
        lid = str(layer["id"])
        nodes.append(_layer_node(layer))
        for spec in PIPELINE_NODES.get(lid, ()):
            nodes.append(_pipeline_node(lid, spec))
        if lid == "L3":
            for sid in _ordered_strategy_ids(view):
                nodes.append(
                    _strategy_node(
                        sid,
                        card_index.get(sid),
                        view,
                        weights=weights,
                        top_n=top_n,
                        enabled_set=enabled_set,
                        shadow_set=shadow_set,
                        overlay_set=overlay_set,
                    )
                )
            for slot in SLOT_NODES:
                nodes.append(copy.deepcopy(dict(slot)))

    edges = _build_edges(nodes)
    counts = _build_counts(nodes, edges, view)

    effective_cards_path = cards_path or strategy_cards.DEFAULT_CARDS_PATH
    effective_registry_path = registry_path or strategy_cards.DEFAULT_REGISTRY_PATH
    effective_manifests_dir = manifests_dir or strategy_cards.DEFAULT_MANIFESTS_DIR
    meta = _build_meta(
        counts,
        cards_path=effective_cards_path,
        registry_path=effective_registry_path,
        manifests_dir=effective_manifests_dir,
        generated_at=_now_iso(now),
    )
    graph = {"nodes": nodes, "edges": edges, "meta": meta, "counts": counts}
    validate_graph(graph)
    return graph


# ── 校验 ──────────────────────────────────────────────────────────────
def validate_graph(graph: Any) -> Dict[str, Any]:
    """校验 Graph JSON 契约，通过返回原 graph（浅拷贝）；非法抛 ValueError。

    校验项：顶层四键、node/edge 必填字段与取值域、node id 唯一、edges
    端点必须存在、strategy 节点 id/status 一致性、counts 与 nodes/edges 自洽、
    ``meta.counts`` 与顶层 counts 一致。
    """
    _require(isinstance(graph, Mapping), "Graph 顶层必须为 dict")
    for key in ("nodes", "edges", "meta", "counts"):
        _require(key in graph, "Graph 缺顶层键: %s" % key)

    nodes = graph["nodes"]
    edges = graph["edges"]
    meta = graph["meta"]
    counts = graph["counts"]
    _require(isinstance(nodes, list) and bool(nodes), "nodes 必须为非空 list")
    _require(isinstance(edges, list), "edges 必须为 list")
    _require(isinstance(meta, Mapping), "meta 必须为 dict")
    _require(isinstance(counts, Mapping), "counts 必须为 dict")

    # ── meta ──
    _require(_is_nonempty_str(meta.get("generated_at")), "meta.generated_at 必须为非空字符串")
    _require(_is_nonempty_str(meta.get("version")), "meta.version 必须为非空字符串")
    _require(meta.get("readonly") is True, "meta.readonly 必须为 true（本图只读）")
    _require(_is_nonempty_str(meta.get("generator")), "meta.generator 必须为非空字符串")
    source = meta.get("source")
    _require(isinstance(source, Mapping), "meta.source 必须为 dict")
    for key in ("cards", "registry", "manifests", "cards_module", "page"):
        _require(_is_nonempty_str(source.get(key)), "meta.source.%s 必须为非空字符串" % key)
    meta_counts = meta.get("counts")
    _require(meta_counts == counts, "meta.counts 必须与顶层 counts 一致")

    # ── nodes ──
    node_ids: List[str] = []
    layer_ids = list(LAYER_IDS)
    for node in nodes:
        _require(isinstance(node, Mapping), "nodes 存在非 dict 元素")
        for field in NODE_CONTRACT["required"]:
            _require(field in node, "node 缺字段 %s（id=%r）" % (field, node.get("id")))
            _require(_is_nonempty_str(node.get(field)), "node.%s 必须为非空字符串" % field)
        node_id = str(node["id"])
        _require(node_id not in node_ids, "node id 重复: %s" % node_id)
        node_ids.append(node_id)
        _require(node["type"] in NODE_TYPES, "node.type 非法: %r" % (node["type"],))
        _require(node["status"] in STATUS_VALUES, "node.status 非法: %r" % (node["status"],))
        _require(node["layer"] in LAYER_IDS, "node.layer 非法: %r" % (node["layer"],))
        if node["type"] == "layer":
            _require(
                node_id == "layer:" + str(node["layer"]),
                "layer 节点 id 必须为 layer:<layer>（实际 %s）" % node_id,
            )
        if node["type"] == "strategy":
            _require(
                _is_nonempty_str(node.get("strategy_id")),
                "strategy 节点缺 strategy_id（id=%s）" % node_id,
            )
            _require(
                node_id == "strategy:" + str(node["strategy_id"]),
                "strategy 节点 id 必须为 strategy:<strategy_id>（实际 %s）" % node_id,
            )
            _require(node["layer"] == "L3", "strategy 节点必须属 L3（实际 %s）" % node["layer"])
            if node.get("card") is True:
                _require(
                    node["status"] in ("live", "shadow", "overlay"),
                    "card 节点 status 必须为 live/shadow/overlay（实际 %r）"
                    % (node["status"],),
                )
        if "metrics" in node:
            _require(isinstance(node["metrics"], Mapping), "node.metrics 必须为 dict")
        for field in ("detail", "schedule", "href"):
            if field in node:
                _require(_is_nonempty_str(node[field]), "node.%s 必须为非空字符串" % field)
    strategy_ids = [str(node.get("strategy_id")) for node in nodes if node.get("type") == "strategy"]
    _require(
        len(strategy_ids) == len(set(strategy_ids)),
        "strategy 节点 strategy_id 重复",
    )
    for layer_id in layer_ids:
        _require(
            ("layer:" + layer_id) in node_ids,
            "缺 layer 节点: layer:%s" % layer_id,
        )

    # ── edges ──
    node_id_set = set(node_ids)
    for edge in edges:
        _require(isinstance(edge, Mapping), "edges 存在非 dict 元素")
        for field in EDGE_CONTRACT["required"]:
            _require(field in edge, "edge 缺字段 %s（%r）" % (field, edge.get("from")))
            _require(_is_nonempty_str(edge.get(field)), "edge.%s 必须为非空字符串" % field)
        _require(
            str(edge["from"]) in node_id_set,
            "edge.from 引用不存在的 node: %r" % (edge["from"],),
        )
        _require(
            str(edge["to"]) in node_id_set,
            "edge.to 引用不存在的 node: %r" % (edge["to"],),
        )
        _require(edge["port"] in EDGE_PORTS, "edge.port 非法: %r" % (edge["port"],))
        if "kind" in edge:
            _require(edge["kind"] in EDGE_KINDS, "edge.kind 非法: %r" % (edge["kind"],))

    # ── counts 自洽 ──
    strategy_nodes = [node for node in nodes if node.get("type") == "strategy"]
    card_nodes = [node for node in strategy_nodes if node.get("card")]
    derived = {
        "nodes": len(nodes),
        "edges": len(edges),
        "layers": len(LAYER_IDS),
        "layer_nodes": len([node for node in nodes if node.get("type") == "layer"]),
        "cards": len(card_nodes),
        "enabled": len([node for node in strategy_nodes if node.get("status") == "live"]),
        "shadow": len([node for node in card_nodes if node.get("status") == "shadow"]),
        "shadow_candidates": len(
            [node for node in strategy_nodes if node.get("status") == "shadow"]
        ),
        "overlay": len(
            [node for node in strategy_nodes if node.get("status") == "overlay"]
        ),
        "strategy_nodes": len(strategy_nodes),
        "registry_only_enabled": len(
            [
                node
                for node in strategy_nodes
                if (node.get("metrics") or {}).get("registry_only")
            ]
        ),
        "pipeline_nodes": len([node for node in nodes if node.get("type") == "pipeline"]),
        "slot_nodes": len([node for node in nodes if node.get("type") == "slot"]),
        "planned": len([node for node in nodes if node.get("status") == "planned"]),
    }
    for key, value in derived.items():
        _require(key in counts, "counts 缺字段: %s" % key)
        _require(
            counts[key] == value,
            "counts.%s 与 nodes/edges 不符: %r != %r" % (key, counts[key], value),
        )
    _require(
        counts["shadow"] == counts["shadow_candidates"],
        "counts.shadow 必须等于 counts.shadow_candidates",
    )
    return dict(graph)


# ── 序列化 / 落盘 ─────────────────────────────────────────────────────
def dumps_graph(graph: Mapping[str, Any], indent: int = 2) -> str:
    """Graph JSON 文本（UTF-8 友好、键序稳定；尾部补换行）。"""
    return json.dumps(dict(graph), ensure_ascii=False, indent=indent) + "\n"


def write_graph(graph: Mapping[str, Any], path: Optional[str] = None) -> str:
    """写 Graph JSON 到显式路径（缺省 ``pipeline_graph.json``）；返回绝对路径。

    本函数是模块唯一的写盘入口——只写调用方给的这一个 path，
    不碰 cards / registry / manifest / 交易链。
    """
    target = os.path.abspath(path or DEFAULT_GRAPH_PATH)
    payload = dumps_graph(validate_graph(graph))
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(payload)
    return target


def load_graph(path: Optional[str] = None) -> Dict[str, Any]:
    """读并校验 Graph JSON（缺省 ``pipeline_graph.json``）。"""
    target = path or DEFAULT_GRAPH_PATH
    if not os.path.isfile(target):
        raise ValueError("Graph JSON 文件不存在: %s" % target)
    try:
        with open(target, "r", encoding="utf-8") as fh:
            graph = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ValueError("Graph JSON 读取失败 %s: %s" % (target, exc)) from exc
    return validate_graph(graph)


# ── 已提交 JSON 与源同步比对 ──────────────────────────────────────────
def _stable_view(graph: Mapping[str, Any]) -> Dict[str, Any]:
    """去掉易变的 meta.generated_at，供「同源同步」比较。"""
    stable = copy.deepcopy(dict(graph))
    meta = dict(stable.get("meta") or {})
    meta.pop("generated_at", None)
    stable["meta"] = meta
    return stable


def compare_graph(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> List[str]:
    """比较两个 Graph 的结构差异（忽略 generated_at），返回差异顶层键列表。"""
    left = _stable_view(expected)
    right = _stable_view(actual)
    diffs: List[str] = []
    for key in sorted(set(left) | set(right)):
        if json.dumps(left.get(key), ensure_ascii=False, sort_keys=True) != json.dumps(
            right.get(key), ensure_ascii=False, sort_keys=True
        ):
            diffs.append(key)
    return diffs


# ── CLI（只读自检 / 显式导出 / 同步校验）──────────────────────────────
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 1-B 只读 Pipeline Graph 导出（10 卡 / 2 enabled / 8 shadow）"
    )
    parser.add_argument("--json", action="store_true", help="打印 Graph JSON（不写盘）")
    parser.add_argument(
        "--out",
        default="",
        help="写入路径（缺省不写盘；生成仓库基线用 --out strategy_layer/pipeline_graph.json）",
    )
    parser.add_argument(
        "--graph",
        default="",
        help="--check 比对的已提交 Graph JSON 路径（缺省 pipeline_graph.json）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="校验已提交 JSON 与当前 source 同步（忽略 generated_at）；0=同步 1=漂移",
    )
    parser.add_argument("--cards", default="", help="cards overlay 路径（缺省内置，只读）")
    parser.add_argument("--registry", default="", help="registry 路径（缺省内置，只读）")
    parser.add_argument("--manifests", default="", help="manifest 目录（缺省内置，只读）")
    args = parser.parse_args(argv)

    graph = build_graph(
        registry_path=args.registry or None,
        cards_path=args.cards or None,
        manifests_dir=args.manifests or None,
    )
    counts = graph["counts"]

    if args.check:
        committed = load_graph(args.graph or None)
        diffs = compare_graph(committed, graph)
        if diffs:
            print(
                "[pipeline_graph][FAIL] 已提交 JSON 与当前源漂移，差异顶层键: %s"
                "（重跑 --out 重新生成）" % ", ".join(diffs),
                file=sys.stderr,
            )
            return 1
        print(
            "[pipeline_graph][OK] 已提交 JSON 与 source 同步："
            "cards=%d enabled=%d shadow=%d nodes=%d edges=%d"
            % (
                counts["cards"],
                counts["enabled"],
                counts["shadow"],
                counts["nodes"],
                counts["edges"],
            )
        )
        return 0

    if args.out:
        target = write_graph(graph, args.out)
        print(
            "[pipeline_graph] wrote %s · cards=%d enabled=%d shadow=%d nodes=%d edges=%d"
            % (
                target,
                counts["cards"],
                counts["enabled"],
                counts["shadow"],
                counts["nodes"],
                counts["edges"],
            )
        )
        return 0

    if args.json:
        print(dumps_graph(graph), end="")
        return 0

    print(
        "[pipeline_graph] cards=%d enabled=%d shadow=%d nodes=%d edges=%d readonly=true"
        % (
            counts["cards"],
            counts["enabled"],
            counts["shadow"],
            counts["nodes"],
            counts["edges"],
        )
    )
    print(
        "[pipeline_graph] sources: cards=%s registry=%s manifests=%s"
        % (
            graph["meta"]["source"]["cards"],
            graph["meta"]["source"]["registry"],
            graph["meta"]["source"]["manifests"],
        )
    )
    print(
        "[pipeline_graph] 只读导出：不写 registry/cards/manifest，不改交易链；"
        "生成基线请显式 --out 或 devtask 单内重跑"
    )
    return 0


__all__ = [
    "BASELINE_COUNTS",
    "DEFAULT_GRAPH_PATH",
    "DEFAULT_PAGE_PATH",
    "EDGE_CONTRACT",
    "EDGE_KINDS",
    "EDGE_PORTS",
    "GRAPH_SCHEMA_VERSION",
    "GRAPH_VERSION",
    "LAYER_FLOWS",
    "LAYER_IDS",
    "LAYERS",
    "NODE_CONTRACT",
    "NODE_TYPES",
    "PHASE",
    "PIPELINE_NODES",
    "SLOT_NODES",
    "STATUS_VALUES",
    "STRATEGY_LABELS",
    "WORK_ORDER",
    "build_graph",
    "compare_graph",
    "dumps_graph",
    "load_graph",
    "main",
    "validate_graph",
    "write_graph",
]


if __name__ == "__main__":
    sys.exit(main())
