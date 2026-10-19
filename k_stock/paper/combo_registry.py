# -*- coding: utf-8 -*-
"""E-1 组合四元组注册表（WO-QT-20261020-003，研发计划 v2 §2.1 缺口 E-1）。

把「组合」（虚拟组合）的定义从 account_id+strategy 的旧粒度升维为四元组：
    选股源(pick_source) × 投研 persona × 盯盘风格(watch_style) × 交易员(trader)
注册制实现：组合=注册表里的一条配置，引擎只认 combo_id 并按条目取参——
新增一个 persona/交易员/选股源只需新增/修改注册文件，既有代码 diff 为零
（松耦合是本单硬验收，tests/test_combo_registry.py 有动态加载实证）。

格式裁定：JSON（非 YAML/INI）。理由：paper 模块纯标准库（engine.py 自述
不依赖 pandas），且 hires.json/models.json/tiers.json 等既有配置全是 JSON，
零新依赖、风格一致。

版本化（X-6 纪律）：
  - registry_schema_version：注册表结构版本（本实现只认 1，向前拒绝）；
  - 四元组每个元素自带 id + version（如 persona_conservative@v1）；
  - persona.params 为 v1 参数结构：conf_floor / max_pos_pct / max_total_pct
    （risk_appetite 为描述性字段，不参与引擎计算）。

失败语义：注册表缺失/结构非法/combo_id 未注册 → 直接抛错（closed on
failure，禁静默降级到默认参数——错参数落单比不落单危害大）。

与 D-1 快照衔接：engine.exec_run(combo_id=...) 落单时，quad_stamp() 产物
写入快照 context_json["combo"]（加法演进，读侧容忍缺失），快照 combo_id
列从 account_id 替换为注册表组合 id（decision_snapshot.py 预留的批 2 口）。
"""
from __future__ import annotations

import json
from pathlib import Path

REGISTRY_SCHEMA_VERSION = 1

# 四元组元素（顺序即语义顺序：选股源×persona×盯盘×交易员）
QUAD_KEYS = ("pick_source", "persona", "watch_style", "trader")

# persona v1 必备参数（缺一即注册非法，closed on failure）
_PERSONA_V1_REQUIRED = ("conf_floor", "max_pos_pct", "max_total_pct")


class ComboRegistryError(Exception):
    """注册表加载/校验失败（结构非法、版本不支持、persona 参数缺失）。"""


class ComboNotFound(ComboRegistryError):
    """combo_id 未在注册表中登记。"""


def default_registry_path() -> Path:
    """默认注册文件（与模块同目录，随仓版本化）。"""
    return Path(__file__).resolve().parent / "combos.json"


def load_registry(path: Path | str | None = None) -> dict:
    """读入并校验注册表，返回整册 dict。任何不合法直接抛错（closed on failure）。

    动态加载任意路径的注册文件即完成「新增 persona/交易员/选股源」——
    这是松耦合验收的机制入口（见 test_combo_registry.py）。"""
    p = Path(path) if path is not None else default_registry_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ComboRegistryError(f"注册表文件不存在: {p}") from e
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ComboRegistryError(f"注册表文件不是合法 UTF-8 JSON: {p} ({e})") from e
    if not isinstance(raw, dict):
        raise ComboRegistryError(f"注册表根须为对象: {p}")
    ver = raw.get("registry_schema_version")
    if ver != REGISTRY_SCHEMA_VERSION:
        raise ComboRegistryError(
            f"注册表 schema 版本不支持: {ver!r}（本实现只认 {REGISTRY_SCHEMA_VERSION}）")
    combos = raw.get("combos")
    if not isinstance(combos, list) or not combos:
        raise ComboRegistryError(f"注册表 combos 须为非空数组: {p}")
    seen: set[str] = set()
    for entry in combos:
        _validate_entry(entry, p)
        cid = entry["combo_id"]
        if cid in seen:
            raise ComboRegistryError(f"combo_id 重复注册: {cid}（{p}）")
        seen.add(cid)
    return raw


def _validate_entry(entry, src) -> None:
    """单条组合注册校验：combo_id/name + 四元组齐全（各含 id+version）
    + persona.params v1 参数结构。"""
    where = f"{src}"
    if not isinstance(entry, dict):
        raise ComboRegistryError(f"组合条目须为对象: {where}")
    cid = entry.get("combo_id")
    if not isinstance(cid, str) or not cid.strip():
        raise ComboRegistryError(f"组合缺少非空 combo_id: {where}")
    if not isinstance(entry.get("name"), str) or not entry["name"].strip():
        raise ComboRegistryError(f"组合 {cid} 缺少非空 name: {where}")
    quad = entry.get("quad")
    if not isinstance(quad, dict):
        raise ComboRegistryError(f"组合 {cid} 缺少 quad 四元组: {where}")
    for key in QUAD_KEYS:
        el = quad.get(key)
        if not isinstance(el, dict):
            raise ComboRegistryError(f"组合 {cid} 四元组缺元素 {key}: {where}")
        if not isinstance(el.get("id"), str) or not el["id"].strip():
            raise ComboRegistryError(f"组合 {cid} 的 {key} 缺少非空 id: {where}")
        if not isinstance(el.get("version"), str) or not el["version"].strip():
            raise ComboRegistryError(f"组合 {cid} 的 {key} 缺少非空 version: {where}")
    params = quad["persona"].get("params")
    if not isinstance(params, dict):
        raise ComboRegistryError(f"组合 {cid} 的 persona 缺少 params: {where}")
    for k in _PERSONA_V1_REQUIRED:
        v = params.get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ComboRegistryError(
                f"组合 {cid} persona.params.{k} 须为数值（persona v1）: {where}")
    if not 0 <= float(params["conf_floor"]) <= 1:
        raise ComboRegistryError(f"组合 {cid} persona.conf_floor 须在 [0,1]: {where}")
    for k in ("max_pos_pct", "max_total_pct"):
        if not 0 < float(params[k]) <= 1:
            raise ComboRegistryError(f"组合 {cid} persona.{k} 须在 (0,1]: {where}")


def resolve(registry: dict, combo_id: str) -> dict:
    """按 combo_id 取注册条目。未注册 → ComboNotFound（closed on failure）。"""
    for entry in registry.get("combos", []):
        if entry.get("combo_id") == combo_id:
            return entry
    raise ComboNotFound(f"combo_id 未注册: {combo_id}")


def persona_params(entry: dict) -> dict:
    """persona v1 参数（引擎仓位/风控的唯一定义处）。"""
    return entry["quad"]["persona"]["params"]


def persona_conf_floor(entry: dict) -> float:
    """BUY 置信度门槛（映射 exec_run 的 min_conf；SELL 不受限——该卖还得卖）。"""
    return float(persona_params(entry)["conf_floor"])


def apply_engine_overrides(cfg: dict, entry: dict) -> dict:
    """把 persona 仓位/风控参数覆盖进引擎 cfg（原地），返回被覆盖键的旧值
    供调用方 finally 恢复——覆盖只作用于单次落单，不污染引擎实例。"""
    p = persona_params(entry)
    saved: dict = {}
    for key in ("max_pos_pct", "max_total_pct"):
        saved[key] = cfg.get(key)
        cfg[key] = float(p[key])
    return saved


def quad_stamp(entry: dict) -> dict:
    """快照用四元组印记：组合 id + 四元素(id@version) + 注册表 schema 版本。
    写入 decision_snapshots.context_json["combo"]（加法演进字段）。"""
    return {
        "combo_id": entry["combo_id"],
        "name": entry["name"],
        "quad": entry["quad"],
        "registry_schema_version": REGISTRY_SCHEMA_VERSION,
    }
