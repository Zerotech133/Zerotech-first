# -*- coding: utf-8 -*-
"""知识图谱热度策略适配器（strategy_id = "kg_boost"）。

数据源：
1. 实体级打分 parquet：``data/kg_analysis/kg_scores_YYYY-MM-DD.parquet``
   （glob 取 max 日期；优先匹配 trade_date 对应文件，缺失则用最新一份）。
   列：label, name, pagerank, community_id, degree_kg。
2. Neo4j 图谱（bolt://localhost:7687，容器 neo4j-kg，只读查询）：
   - ``Security`` 节点带 ts_code / name / community_id；
   - 关系 ``Security-[:MAPS_TO]->Concept``（先经 ``CALL db.labels()``
     探明的实际 label / 关系名，已写入本实现）。

打分逻辑（K-STOCK-426 定稿）：
- 个股信号分 = 该股自身 pagerank 在 parquet 全部 Security 行中的
  百分位（0-100）。旧「社区峰值分位」已废弃：Louvain 社区划分对
  图谱增量极度敏感（19149→17103 社区漂移即可让信号从 50 条级
  暴涨到 5596 条级），自身 pagerank 分位对漂移免疫且分布均匀。
- action 映射：score >= 70 → buy（高热）；40-70 → watch（观察）；
  < 40 → hold（冷却）。
- confidence 由「高分关联概念」数量决定（高分 = 概念热度分位 >= 60，
  概念热度 = Concept pagerank 分位，K-STOCK-426 升级；旧 parquet
  无 Concept 行时回退 MAPS_TO 入度）：0 个 → 0.4；1 个 → 0.5；
  2 个 → 0.65；>=3 个 → 0.8。
- rationale：``图谱热度：pagerank分位{score}，关联概念 X/Y/Z``。

降级语义：parquet 缺失 / Neo4j 查询失败 / 无关联概念 → 打印日志并
返回 []（降级≠失败），绝不 raise 到调用方。所有网络调用 timeout=15。
"""
import glob
import os
import re
import subprocess

from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from ..signal import Signal

# 项目数据目录（strategy_layer/adapters/ → 上两级到 quant_backtest/）
_QB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KG_DIR = os.path.join(_QB_ROOT, "data", "kg_analysis")
KG_SCORE_GLOB = os.path.join(KG_DIR, "kg_scores_*.parquet")

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NET_TIMEOUT = 15  # 秒，所有网络调用统一超时

# 个股关联概念列表里参与 rationale 展示的最大条数
_TOP_CONCEPTS_IN_RATIONALE = 3


def _neo4j_password() -> Optional[str]:
    """从 neo4j-kg 容器环境变量读 NEO4J_AUTH 中的密码。

    绝不打印/记录密码；读取失败返回 None（由调用方降级）。
    """
    try:
        raw = subprocess.run(
            ["docker", "inspect", "neo4j-kg", "--format", "{{.Config.Env}}"],
            capture_output=True, text=True, timeout=NET_TIMEOUT,
        ).stdout
        m = re.search(r"NEO4J_AUTH=neo4j/([^\s'\"]+)", raw or "")
        return m.group(1) if m else None
    except Exception:
        return None


class KgBoostAdapter:
    """知识图谱热度策略：个股图谱社区热度 → 标准信号。

    Args:
        score_glob: 打分 parquet 的 glob 模式（测试可指向临时目录）。
        driver: 预置 neo4j Driver（测试注入 mock）；None 时惰性真实建连。
        password: 显式密码（测试用）；None 时经 :func:`_neo4j_password` 读取。
        read_parquet: parquet 读取函数（测试注入 mock，默认 pd.read_parquet）。
    """

    strategy_id = "kg_boost"

    def __init__(
        self,
        score_glob: str = KG_SCORE_GLOB,
        driver: Any = None,
        password: Optional[str] = None,
        read_parquet: Callable[[str], "pd.DataFrame"] = pd.read_parquet,
    ) -> None:
        self._score_glob = score_glob
        self._driver = driver
        self._password = password
        self._read_parquet = read_parquet
        self._stock_pctl: Optional[Dict[str, float]] = None  # 股票名 → 0-100 分位

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def score(self, trade_date: str) -> List[Signal]:
        """给定交易日（YYYYMMDD），返回图谱热度信号列表。

        任何一步失败均降级返回 [] 并打印日志。
        """
        try:
            df = self._load_scores(trade_date)
            if df is None or df.empty:
                return []
            if self._stock_pctl is None:
                self._stock_pctl = self._build_security_percentile(df)
            graph = self._fetch_graph()
            if not graph:
                return []
            concept_heat = self._fetch_concept_heat(scores_df=df)
            return self._compose(trade_date, graph, concept_heat)
        except Exception as exc:
            print(f"[kg_boost] 降级：打分失败，返回空信号列表: {exc}")
            return []

    # ------------------------------------------------------------------ #
    # 数据源 1：parquet 社区热度分位
    # ------------------------------------------------------------------ #
    def _pick_parquet_path(self, trade_date: str) -> Optional[str]:
        """优先 trade_date 对应文件，否则取 glob max 日期的最新一份。"""
        files = sorted(glob.glob(self._score_glob))
        if not files:
            return None
        dated = {m.group(1): p for p in files
                 for m in [re.search(r"kg_scores_(\d{4}-\d{2}-\d{2})\.parquet$", p)] if m}
        if trade_date and len(trade_date) == 8:
            iso = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
            if iso in dated:
                return dated[iso]
        return files[-1]  # glob 已排序，最后一份即最新

    def _load_scores(self, trade_date: str) -> Optional["pd.DataFrame"]:
        """读当日/最新打分 parquet；缺失降级返回 None。"""
        path = self._pick_parquet_path(trade_date)
        if path is None:
            print(f"[kg_boost] 降级：未找到打分 parquet（{self._score_glob}）")
            return None
        df = self._read_parquet(path)
        need = {"label", "name", "pagerank", "community_id", "degree_kg"}
        if df is None or df.empty or not need.issubset(set(df.columns)):
            print(f"[kg_boost] 降级：打分 parquet 为空或缺列: {path}")
            return None
        return df

    @staticmethod
    def _build_security_percentile(df: "pd.DataFrame") -> Dict[str, float]:
        """股票名 → 自身 pagerank 全网分位（0-100）。

        只取 label=Security 行做 rank(pct=True) × 100。对 Louvain
        社区漂移免疫（K-STOCK-426：旧社区峰值口径在社区数漂移时
        信号量 50 ↔ 5596 失控，已废弃）。
        """
        sec = df[df["label"].astype(str) == "Security"]
        if sec.empty or not sec["pagerank"].notna().any():
            return {}
        pctl = sec["pagerank"].rank(pct=True) * 100.0
        return {str(n): round(float(v), 2) for n, v in
                zip(sec["name"], pctl) if n}

    # ------------------------------------------------------------------ #
    # 数据源 2：Neo4j 个股-概念映射
    # ------------------------------------------------------------------ #
    def _get_driver(self) -> Any:
        """惰性获取 neo4j Driver（密码经容器环境读取，绝不打印）。"""
        if self._driver is None:
            from neo4j import GraphDatabase
            pwd = self._password if self._password is not None \
                else _neo4j_password()
            if not pwd:
                raise RuntimeError("无法从 neo4j-kg 容器读取 NEO4J_AUTH 密码")
            self._driver = GraphDatabase.driver(
                NEO4J_URI, auth=(NEO4J_USER, pwd),
                connection_timeout=NET_TIMEOUT)
        return self._driver

    def _fetch_graph(self) -> Dict[str, Dict[str, Any]]:
        """查询全部股票节点及其关联概念。

        返回 {ts_code: {"name": str, "community_id": int, "concepts": [str]}}；
        查询失败打印日志并返回 {}（降级）。
        """
        query = (
            "MATCH (s:Security) WHERE s.ts_code IS NOT NULL "
            "OPTIONAL MATCH (s)-[:MAPS_TO]->(c:Concept) "
            "RETURN s.ts_code AS ts, s.name AS name, "
            "s.community_id AS cid, collect(c.name) AS concepts"
        )
        try:
            drv = self._get_driver()
            with drv.session() as session:
                out: Dict[str, Dict[str, Any]] = {}
                for rec in session.run(query):
                    ts = rec["ts"]
                    if not ts:
                        continue
                    out[str(ts)] = {
                        "name": rec["name"] or "",
                        "community_id": rec["cid"],
                        "concepts": [c for c in rec["concepts"] if c],
                    }
                return out
        except Exception as exc:
            print(f"[kg_boost] 降级：Neo4j 查询失败，返回空映射: {exc}")
            return {}

    def _fetch_concept_heat(self, scores_df: Optional["pd.DataFrame"] = None
                            ) -> Dict[str, float]:
        """概念热度（K-STOCK-426 升级）。

        主路径：Concept 级 pagerank 百分位（0-100）——直接取打分 parquet
        中 label=Concept 行的 pagerank rank(pct=True)，与社区分位同源同
        批次（07:00 KGAnalysis 产出）；比旧 MAPS_TO 入度更优：入度只度量
        覆盖广度（ETF/指数类概念天然大），pagerank 度量图谱重要性。
        降级：parquet 无 Concept 行（旧版管线）→ 回退 Neo4j MAPS_TO 入度；
        查询失败降级返回 {}。
        """
        if scores_df is not None:
            con = scores_df[scores_df["label"].astype(str) == "Concept"]
            if len(con) and con["pagerank"].notna().any():
                pctl = con["pagerank"].rank(pct=True) * 100.0
                return {str(n): float(v) for n, v in
                        zip(con["name"], pctl) if n}
        # ── 降级：Neo4j MAPS_TO 入度（旧语义） ──
        query = (
            "MATCH (c:Concept)<-[:MAPS_TO]-(s:Security) "
            "RETURN c.name AS name, count(DISTINCT s) AS n"
        )
        try:
            drv = self._get_driver()
            with drv.session() as session:
                rows = {rec["name"]: int(rec["n"]) for rec in session.run(query)}
            if not rows:
                return {}
            import pandas as _pd
            ser = _pd.Series(rows)
            pctl = ser.rank(pct=True) * 100.0
            return {str(k): float(v) for k, v in pctl.items()}
        except Exception as exc:
            print(f"[kg_boost] 降级：概念热度查询失败: {exc}")
            return {}

    # ------------------------------------------------------------------ #
    # 合成信号
    # ------------------------------------------------------------------ #
    def _high_concept_count(self, concepts: List[str],
                            heat: Dict[str, float]) -> int:
        """高分概念数：概念热度分位 >= 60 记一个高分。"""
        return sum(1 for c in concepts if heat.get(c, 0.0) >= 60.0)

    @staticmethod
    def _confidence_for(n_high: int) -> float:
        """置信度规则：0 个高分概念 0.4；1 个 0.5；2 个 0.65；>=3 个 0.8。"""
        if n_high >= 3:
            return 0.8
        if n_high == 2:
            return 0.65
        if n_high == 1:
            return 0.5
        return 0.4

    @staticmethod
    def _action_for(score: float) -> str:
        """action 映射：>=70 buy（高热）；40-70 watch；<40 hold。"""
        if score >= 70.0:
            return "buy"
        if score >= 40.0:
            return "watch"
        return "hold"

    def _compose(self, trade_date: str,
                 graph: Dict[str, Dict[str, Any]],
                 heat: Dict[str, float]) -> List[Signal]:
        """图谱映射 + 个股 pagerank 分位 → 标准信号列表（按分位降序）。"""
        pctl = self._stock_pctl or {}
        signals: List[Signal] = []
        for ts, info in graph.items():
            sc = pctl.get(info.get("name") or "")
            if sc is None:
                continue  # parquet 无该股（新上市/名称变动）→ 跳过
            concepts = sorted(info.get("concepts", []),
                              key=lambda c: -heat.get(c, 0.0))
            if not concepts:
                continue  # 无关联概念不产信号
            n_high = self._high_concept_count(concepts, heat)
            shown = "/".join(concepts[:_TOP_CONCEPTS_IN_RATIONALE])
            rationale = (f"图谱热度：pagerank分位{sc:.0f}，"
                         f"关联概念 {shown}")
            signals.append(Signal(
                symbol=str(ts),
                action=self._action_for(sc),
                score=round(float(sc), 2),
                confidence=self._confidence_for(n_high),
                rationale=rationale,
                strategy=self.strategy_id,
                trade_date=trade_date,
            ))
        signals.sort(key=lambda s: -s.score)
        return signals


# ---------------------------------------------------------------------- #
# 真实连通性冒烟（不进 pytest；手动执行）
# ---------------------------------------------------------------------- #
if __name__ == "__main__":
    import datetime as _dt

    today = _dt.date.today().strftime("%Y%m%d")
    adapter = KgBoostAdapter()
    sigs = adapter.score(today)
    print(f"kg_boost smoke: {len(sigs)} signals @ {today}")
    for s in sigs[:5]:
        print(f"  {s.symbol} {s.action} score={s.score} "
              f"conf={s.confidence} | {s.rationale}")
