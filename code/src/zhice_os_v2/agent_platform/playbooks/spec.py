"""PlaybookSpec — 打法包规格：数据结构 + YAML/JSON 加载校验 + 默认包。

- load_playbook(path_or_dict)：加载并校验；method id 必须在已知集合——
  已实现（chan/volume/rsi_spec）参与投票；预留 id（event_window 等）声明合法
  但标注 unavailable=True 不参与投票（T07 实现插件后自然生效）。
- default_playbook(market)：与 v1 现行为完全等价的默认包（向后兼容锚点）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from zhice_os_v2.agent_platform.config import AgentPlatformConfig

logger = logging.getLogger(__name__)

# 旧默认包方法集合（保持 legacy default 行为与票数不变）。
LEGACY_METHOD_IDS = ("chan", "volume", "rsi_spec",
                     "sentiment_regime", "trend_break",
                     "event_window", "chan_structure")
# 已实现插件（registry 默认注册集合）
IMPLEMENTED_METHOD_IDS = LEGACY_METHOD_IDS
# 双周期回踩是额外的聚合打法插件：允许 Playbook 显式绑定，但不纳入
# default_playbook 的 v1 方法集合，避免改变未传 playbooks 时的旧行为。
PLAYBOOK_METHOD_IDS = ("dual_tf_pullback",)
# T07 前的预留 id（历史兼容：unavailable 判定已改为动态——见 _is_unavailable）
RESERVED_METHOD_IDS = ("event_window", "trend_break", "sentiment_regime", "chan_structure")
# 已知全集 = 加载校验白名单
KNOWN_METHOD_IDS = frozenset(IMPLEMENTED_METHOD_IDS + RESERVED_METHOD_IDS + PLAYBOOK_METHOD_IDS)

DEFAULT_PLAYBOOK_ID = "default"

VALID_BAR_FREQS = ("5m", "15m", "1d")


def _registered_method_ids() -> frozenset[str]:
    """当前 registry 已注册方法 id（含 CN 专用方法）。"""
    try:
        from zhice_os_v2.agent_platform.methods import default_registry

        return frozenset(default_registry().ids())
    except Exception:      # pragma: no cover - 循环导入兜底
        return frozenset()


def _is_unavailable(mid: str) -> bool:
    """T07 动态转正：id 在当前 registry 已注册 → 参与投票；否则 unavailable。

    向后兼容：老进程未注册新插件时（如测试用裸 MethodRegistry），预留 id
    声明仍合法但 unavailable=True 不投票——与 T06 行为一致。
    """
    if mid in IMPLEMENTED_METHOD_IDS and mid not in RESERVED_METHOD_IDS:
        return False                       # chan/volume/rsi_spec 恒可用
    if mid == "dual_tf_pullback":
        try:
            from zhice_os_v2.agent_platform.methods import create_cn_registry
            return mid not in create_cn_registry().ids()
        except Exception:
            return True
    return mid not in _registered_method_ids()


class PlaybookValidationError(ValueError):
    """PlaybookSpec 校验失败（未知方法/缺字段/非法值）。"""


@dataclass
class MethodBinding:
    """单方法绑定：id + 打法内权重 + 角色说明。

    unavailable=True：预留 id 未实现，加载合法但不参与投票/仲裁。
    """

    id: str
    weight: float = 1.0
    role: str = ""
    unavailable: bool = False

    def to_dict(self) -> dict:
        return {"id": self.id, "weight": self.weight, "role": self.role,
                "unavailable": self.unavailable}


@dataclass
class ArbitrationParams:
    """打法级仲裁参数（None 字段 = 回退 AgentPlatformConfig 既有值）。"""

    quorum_min_votes: int | None = None
    tie_margin: float | None = None
    regime_veto: bool | None = None

    def to_dict(self) -> dict:
        return {"quorum_min_votes": self.quorum_min_votes,
                "tie_margin": self.tie_margin,
                "regime_veto": self.regime_veto}


@dataclass
class PlaybookSpec:
    """打法包规格（设计文档第二节注册规格的直译）。"""

    playbook_id: str
    name: str
    market: str
    snapshot_hz: float                      # 周期秒（5s 包=5.0；60=CN 盘中 1 分钟档）
    bar_freq: str                           # "5m"/"15m"/"1d" 主决策周期
    methods: list[MethodBinding] = field(default_factory=list)
    arbitration: ArbitrationParams = field(default_factory=ArbitrationParams)
    graduation_tier: str = "CRYPTO"         # 毕业分层（min_samples 键）
    entry_style_binding: bool = False
    risk: dict = field(default_factory=dict)
    source_path: str = ""                   # 加载来源（内置/文件路径，溯源用）

    def __post_init__(self) -> None:
        if not self.playbook_id:
            raise PlaybookValidationError("playbook_id 不能为空")
        if self.bar_freq not in VALID_BAR_FREQS:
            raise PlaybookValidationError(
                f"bar_freq 非法: {self.bar_freq!r}（可选 {list(VALID_BAR_FREQS)}）")

    # ── 派生视图 ────────────────────────────────────────────────────────────
    def available_methods(self) -> list[MethodBinding]:
        """参与投票的方法绑定（排除 unavailable 预留位）。"""
        return [m for m in self.methods if not m.unavailable]

    def method_weights(self) -> dict[str, float]:
        """{method_id: weight}（仅可用方法；供 evaluate_subset 消费）。"""
        return {m.id: m.weight for m in self.available_methods()}

    def method_ids(self) -> list[str]:
        return [m.id for m in self.available_methods()]

    def to_dict(self) -> dict:
        return {
            "playbook_id": self.playbook_id,
            "name": self.name,
            "market": self.market,
            "snapshot_hz": self.snapshot_hz,
            "bar_freq": self.bar_freq,
            "methods": [m.to_dict() for m in self.methods],
            "arbitration": self.arbitration.to_dict(),
            "graduation_tier": self.graduation_tier,
            "entry_style_binding": self.entry_style_binding,
            "risk": dict(self.risk),
        }

    def describe(self) -> str:
        avail = ",".join(f"{m.id}×{m.weight}" for m in self.available_methods())
        unavail = ",".join(m.id for m in self.methods if m.unavailable) or "-"
        return (f"Playbook({self.playbook_id}, market={self.market}, "
                f"{self.snapshot_hz:g}s/{self.bar_freq}, methods=[{avail}], "
                f"unavailable=[{unavail}], tier={self.graduation_tier})")


# ── 加载与校验 ──────────────────────────────────────────────────────────────
def _parse_methods(raw: list) -> list[MethodBinding]:
    if not isinstance(raw, list) or not raw:
        raise PlaybookValidationError("methods 必须为非空列表")
    out: list[MethodBinding] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            mid = str(item.get("id", "")).strip()
            weight = float(item.get("weight", 1.0))
            role = str(item.get("role", "") or "")
        else:  # 紧凑字符串形式 "chan" / "chan:1.5"
            parts = str(item).split(":", 1)
            mid = parts[0].strip()
            weight = float(parts[1]) if len(parts) > 1 else 1.0
            role = ""
        if not mid:
            raise PlaybookValidationError(f"method id 为空: {item!r}")
        if mid not in KNOWN_METHOD_IDS:
            raise PlaybookValidationError(
                f"未知方法 id: {mid!r}（已知: {sorted(KNOWN_METHOD_IDS)}）")
        if mid in seen:
            raise PlaybookValidationError(f"方法重复声明: {mid}")
        if not (0.3 <= weight <= 3.0):
            raise PlaybookValidationError(
                f"方法 {mid} weight={weight} 超界 [0.3, 3.0]")
        seen.add(mid)
        unavailable = _is_unavailable(mid)
        if unavailable:
            logger.info("方法 %s 未在当前 registry 注册（预留 id）— 标注 unavailable 不参与投票", mid)
        out.append(MethodBinding(id=mid, weight=weight, role=role,
                                 unavailable=unavailable))
    return out


def _parse_arbitration(raw: dict | None) -> ArbitrationParams:
    raw = raw or {}
    return ArbitrationParams(
        quorum_min_votes=(int(raw["quorum_min_votes"])
                          if raw.get("quorum_min_votes") is not None else None),
        tie_margin=(float(raw["tie_margin"])
                    if raw.get("tie_margin") is not None else None),
        regime_veto=(bool(raw["regime_veto"])
                     if raw.get("regime_veto") is not None else None),
    )


def load_playbook(path_or_dict) -> PlaybookSpec:
    """从 YAML 文件路径 / JSON 字符串 / dict 加载并校验。

    - 未知 method id → PlaybookValidationError（拒绝加载）
    - 预留 id（event_window 等）→ 合法，unavailable=True 不参与投票
    - graduation 段：tier 取 graduation.tier（缺省 "CRYPTO"）
    """
    if isinstance(path_or_dict, (str, Path)) and Path(path_or_dict).exists():
        text = Path(path_or_dict).read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        source = str(path_or_dict)
    elif isinstance(path_or_dict, str):
        data = yaml.safe_load(path_or_dict)  # 也兼容内联 YAML/JSON 字符串
        source = "<inline>"
    elif isinstance(path_or_dict, dict):
        data = dict(path_or_dict)
        source = "<dict>"
    else:
        raise PlaybookValidationError(f"不支持的加载源类型: {type(path_or_dict)!r}")
    if not isinstance(data, dict):
        raise PlaybookValidationError("playbook 内容必须为映射结构")

    for fld in ("playbook_id", "name", "market"):
        if not data.get(fld):
            raise PlaybookValidationError(f"缺少必填字段: {fld}")

    grad = data.get("graduation") or {}
    spec = PlaybookSpec(
        playbook_id=str(data["playbook_id"]),
        name=str(data["name"]),
        market=str(data["market"]),
        snapshot_hz=float(data.get("snapshot_hz", 5)),
        bar_freq=str(data.get("bar_freq", "15m")),
        methods=_parse_methods(data.get("methods")),
        arbitration=_parse_arbitration(data.get("arbitration")),
        graduation_tier=str(grad.get("tier", "CRYPTO")),
        entry_style_binding=bool(data.get("entry_style_binding", False)),
        risk=dict(data.get("risk") or {}),
        source_path=source,
    )
    return spec


def default_playbook(market: str = "CRYPTO",
                     cfg: AgentPlatformConfig | None = None) -> PlaybookSpec:
    """v1 现行为等价默认包：chan/volume/rsi_spec 全 1.0，5s/15m，仲裁参数取 config。

    向后兼容锚点——不传 playbooks 的所有既有调用等价于此包。
    """
    cfg = cfg or AgentPlatformConfig()
    return PlaybookSpec(
        playbook_id=DEFAULT_PLAYBOOK_ID,
        name="默认通用包（v1 等价）",
        market=market,
        snapshot_hz={"5s": 5.0, "10s": 10.0, "3m": 180.0}.get(cfg.snapshot_hz, 5.0),
        bar_freq=cfg.backtest_freq,
                 methods=[MethodBinding(id=mid, weight=cfg.initial_method_weight)
                 for mid in LEGACY_METHOD_IDS],
        arbitration=ArbitrationParams(),          # 全 None → 回退 config
        graduation_tier=market,
    )
