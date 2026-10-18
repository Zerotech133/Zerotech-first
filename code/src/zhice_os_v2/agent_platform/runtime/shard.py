"""ShardPolicy — 分片策略：market 一级 + symbol 二级（架构稿 §3.4）。

method 不做分片轴（同标的所有方法投票须同进程）。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass
class ShardPolicy:
    """单个 WatchAgent 的管辖范围。"""

    market: str
    symbols: list[str] = field(default_factory=list)
    snapshot_hz: str = "5s"          # 5s / 10s / 3m
    method_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_config(cls, cfg, symbols: Sequence[str] | None = None) -> ShardPolicy:
        """cfg: AgentPlatformConfig；symbols 缺省 = 全池（由上层 discover 注入）。"""
        return cls(
            market=cfg.market,
            symbols=list(symbols) if symbols is not None else [],
            snapshot_hz=cfg.snapshot_hz,
        )

    def split_by_symbol(self, max_symbols: int) -> list[ShardPolicy]:
        """二级分片：按 symbol 均匀切（顺序切块保行情局部性）。"""
        if max_symbols <= 0 or len(self.symbols) <= max_symbols:
            return [self]
        out: list[ShardPolicy] = []
        for i in range(0, len(self.symbols), max_symbols):
            out.append(ShardPolicy(
                market=self.market,
                symbols=self.symbols[i:i + max_symbols],
                snapshot_hz=self.snapshot_hz,
                method_ids=list(self.method_ids),
            ))
        return out

    def describe(self) -> str:
        return (f"Shard(market={self.market}, hz={self.snapshot_hz}, "
                f"symbols={len(self.symbols)}, methods={self.method_ids})")
