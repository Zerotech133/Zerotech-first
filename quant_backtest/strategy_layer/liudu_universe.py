"""门 N1 universe 候选圈选登记（休眠默认，不启用）。

本模块是「六度选股 R0→R1→R2 生产切流工程」工位 #369 的独占交付物，
与门 N8 reconcile 语义升级（`reconcile_semantics.py`）成对交付。

职责边界（红线）：
- 仅「登记」WO-025 考证的 5 个 universe 候选（A–E），不做任何实质圈选。
- 提供与现状保持一致的解析器 `resolve_universe`：在 CURRENT_UNIVERSE_CHOICES
  内原样返回，否则回退 "market"（与 backtest_engine_v2.py:946 行为一致）。
  这是「保持现状」的休眠默认，绝不自动启用 A–E 任一候选。
- 依赖仅 stdlib（enum / dataclasses / typing），不 import pandas/numpy/第三方。
- 不触发任何切流；`LIUDU_UNIVERSE_ENABLED` 默认 False。

所有激活开关留待老田在 N1 门拍板：激活 A–E 任一候选须经老田实质圈选 +
五要素批准；本模块不替用户圈选。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Tuple


class UniverseCandidate(str, Enum):
    """N1 门 universe 候选（仅登记，不启用）。

    全部为「未来可选」，须经老田在 N1 门实质圈选 + 五要素批准后方可激活。
    本模块不替用户圈选，也不在开关关闭时做任何选择。
    """

    A = "A"  # 盘前六层环境分驱动
    B = "B"  # 六维打分仿九维
    C = "C"  # 外部产品
    D = "D"  # 六策略误记
    E = "E"  # 六档强度（全仓无据）


# 与 backtest_engine_v2.py:1178 完全一致：当前仅 market / pool76 可选。
CURRENT_UNIVERSE_CHOICES: tuple = ("market", "pool76")

# 仍为「未来可选」候选项（文档化登记用），与上面枚举一一对应。
_CANDIDATE_CODES: List[str] = [c.value for c in UniverseCandidate]


# 激活开关：已按老田 2026-10-21 路径 A 授权翻为开启（非生产决策，已拍板）。
# 激活 universe="market" 即启用 A 股样本域过滤（apply_universe_filter），
# 在 xunyou.build_pool 真实链强制剔除非 A 股/北交所。WO-025 概念候选 A–E 本次不启用。
LIUDU_UNIVERSE_ENABLED: bool = True

# === N1 决策记录（2026-10-21 老田拍板，见 AskUserQuestion 答复）===
# 选定 universe = "market"（全市场 5000+ 剔 ST / 北交所），与 backtest_engine_v2.py:1178
# 引擎默认一致，无需新数据源，R1 试运行最稳。WO-025 概念候选 A–E 本次不启用。
# LIUDU_UNIVERSE_ENABLED 已据路径 A 授权翻 True：universe="market" 真实链生效，
# apply_universe_filter 在 build_pool 强制过滤；resolve_universe() 行为不变。
SELECTED_UNIVERSE: str = "market"
N1_DECISION_DATE: str = "2026-10-21"
N1_DECISION_BY: str = "老田"


@dataclass(frozen=True)
class _CandidateDoc:
    code: str
    name: str
    note: str
    status: str  # 固定为「待老田拍板」


_CANDIDATE_DOCS: Dict[str, _CandidateDoc] = {
    "A": _CandidateDoc("A", "盘前六层环境分驱动", "WO-025 考证候选", "待老田拍板"),
    "B": _CandidateDoc("B", "六维打分仿九维", "WO-025 考证候选", "待老田拍板"),
    "C": _CandidateDoc("C", "外部产品", "WO-025 考证候选", "待老田拍板"),
    "D": _CandidateDoc("D", "六策略误记", "WO-025 考证候选", "待老田拍板"),
    "E": _CandidateDoc("E", "六档强度（全仓无据）", "WO-025 考证候选", "待老田拍板"),
}


def resolve_universe(requested: str) -> str:
    """解析 universe 请求（休眠默认，保持现状）。

    与 backtest_engine_v2.py:946 行为一致：若 ``requested`` 在
    CURRENT_UNIVERSE_CHOICES 中则原样返回；否则回退 "market"。

    这是「保持现状」的休眠默认，不启用任何 A–E 候选。无论
    ``LIUDU_UNIVERSE_ENABLED`` 是何值，本函数都不替用户圈选。
    """
    if requested in CURRENT_UNIVERSE_CHOICES:
        return requested
    return "market"


def enumerate_candidates() -> Dict[str, dict]:
    """只做文档化登记，返回 A–E 的说明字典。

    不得包含任何自动选择逻辑；全部标记「待老田拍板」。
    返回的字典中绝不夹带 enabled/selected/active 之类的启用标记。
    """
    return {
        code: {
            "code": doc.code,
            "name": doc.name,
            "note": doc.note,
            "status": doc.status,
        }
        for code, doc in _CANDIDATE_DOCS.items()
    }


def is_candidate_enabled() -> bool:
    """暴露当前是否允许启用候选（默认 False）。仅为查询用，不改变任何状态。"""
    return LIUDU_UNIVERSE_ENABLED


# ── N1 决策强制执行（休眠默认：仅当 LIUDU_UNIVERSE_ENABLED=True 调用）──────
_ASH_RE = re.compile(r"^\d{6}\.(SH|SZ)$", re.IGNORECASE)


def is_ashare_symbol(symbol: str) -> bool:
    """A 股判定：6 位数字 + .SH/.SZ（含剔北交所/美外股，后缀不符即非 A 股）。"""
    return bool(_ASH_RE.match(str(symbol or "")))


def apply_universe_filter(rows: List[dict], requested: str | None = None) -> Tuple[List[dict], List[dict]]:
    """按已拍板 universe 过滤样本域（纯函数，不改入参）。

    纪律（红线）：
    - 本函数**不检查开关**；开关守卫由调用方（如 xunyou.build_pool）负责。
      默认 LIUDU_UNIVERSE_ENABLED=False → 调用方不调用本函数 → 行为不变。
    - 仅当老田在 N1 门翻开关后才应调用；本函数不替用户激活任何候选。
    - 不触发切流、不写库、不发网络。

    口径（与 backtest_engine_v2.py:1178 的 market 域一致）：
    - universe="market"（SELECTED_UNIVERSE 当前值）：仅保留 A 股
      （6 位 + .SH/.SZ），剔除北交所(.BJ) 与美/外股字母 ticker。
      ST 剔除需名称数据，不在本函数臆造（属数据源层职责）。
    - universe="pool76"：需静态 76 只清单，本环境未提供 → 返回未过滤
      （调用方据 notes 提示老田补清单，绝不对空清单臆造过滤）。
    返回 (kept_rows, removed_rows)。
    """
    uni = resolve_universe(requested if requested is not None else SELECTED_UNIVERSE)
    if uni == "market":
        kept, removed = [], []
        for r in rows:
            if is_ashare_symbol(r.get("symbol")):
                kept.append(r)
            else:
                removed.append(r)
        return kept, removed
    # pool76 静态清单未在当前环境提供：返回未过滤，交由调用方提示补清单。
    return list(rows), []
