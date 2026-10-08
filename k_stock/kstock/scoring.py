# -*- coding: utf-8 -*-
"""K-STOCK S1 PoC · 六维评分（S2 阶段接入；S1 仅落因子表加载器）

数据源：菜鸟 DB 快照 stock_weight_factor（6 维 × 24 因子，WEIGHT/PENALTY）。
本文件当前只做：因子表解析与配置加载；打分引擎 S2 按计算公式自研（TDX 因子库做算子）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# 从 thunder_model.sql 抽取的 24 因子静态快照（2025-12-25 生产值）
# 结构：category_id → [(factor_code, name, WEIGHT/PENALTY, default_weight)]
CATEGORIES = {
    1: "基本面（财务质量）",
    2: "业绩预测（EPS/超预期）",
    3: "资金面",
    4: "技术面",
    5: "情绪面（龙虎榜等）",
    6: "行业相对强度",
}

SAMPLE_FACTORS = [
    (1, "revenue_growth_weight", "营收增速≥25%", "WEIGHT", 6.00),
    (1, "gross_margin_weight", "毛利率≥行业TOP30%", "WEIGHT", 4.00),
    (1, "cash_flow_weight", "经营现金流为正", "WEIGHT", 2.00),
    (1, "roe_weight", "ROE环比提升", "WEIGHT", 3.00),
    (1, "deteriorate_penalty", "任一指标恶化超20%扣分值", "PENALTY", -2.00),
    (2, "eps_up_weight", "30日内EPS上调≥2次", "WEIGHT", 6.00),
    (2, "beat_expect_weight", "Q业绩超预期概率≥80%", "WEIGHT", 4.00),
    (2, "year_forecast_weight", "年度预测增速上修", "WEIGHT", 2.00),
]


def load_factors() -> dict[int, list[dict]]:
    """S1：返回已确认的样本因子（1-2 维）；S2：解析 thunder_model.sql 全 24 因子。"""
    out: dict[int, list[dict]] = {}
    for cid, code, name, ftype, w in SAMPLE_FACTORS:
        out.setdefault(cid, []).append({
            "factor_code": code, "factor_name": name,
            "factor_type": ftype, "default_weight": w,
        })
    return out


def parse_full_factors(sql_path: str | Path) -> dict[int, list[dict]]:
    """S2：直接从菜鸟快照 thunder_model.sql 解析全部因子行。"""
    text = Path(sql_path).read_text(encoding="utf-8", errors="replace")
    out: dict[int, list[dict]] = {}
    for m in re.finditer(
            r"INSERT INTO `stock_weight_factor` VALUES \((\d+), (\d+), '([^']+)', '([^']*)', "
            r"'(WEIGHT|PENALTY)', '[^']*', (-?[\d.]+),", text):
        cid = int(m.group(2))
        out.setdefault(cid, []).append({
            "id": int(m.group(1)), "factor_code": m.group(3),
            "factor_name": m.group(4).replace("\\r\\n", "").strip(),
            "factor_type": m.group(5), "default_weight": float(m.group(6)),
        })
    return out


if __name__ == "__main__":
    factors = parse_full_factors(r"E:\老田的AI助手\菜鸟\thunder_model\thunder_model.sql")
    total = sum(len(v) for v in factors.values())
    print(json.dumps({CATEGORIES.get(k, k): len(v) for k, v in sorted(factors.items())},
                     ensure_ascii=False, indent=2))
    print("因子总数:", total)
