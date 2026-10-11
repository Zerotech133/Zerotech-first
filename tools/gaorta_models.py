# -*- coding: utf-8 -*-
"""K-STOCK-137 S-a · gaorta 模型目录生成器

从 https://client.gaorta.com/api/pricing 拉取公开定价清单（无需 key），
生成 k_stock/paper/models.json（模型目录 + USD 换算 + 角色聘请价草案）。

用法：
    python tools/gaorta_models.py            # 拉取并生成
    python tools/gaorta_models.py --check    # 仅比对，无改动则退出 0

硬约束（老田 2026-10-11 拍板）：模拟交易模型目录仅限 gaorta 平台模型。
One-API 倍率换算：USD/1M tokens(输入) ≈ model_ratio × 2；输出 × completion_ratio。
tiered_expr 模型（deepseek 系）表达式内 p/c 系数已是 USD/1M，分高峰/空闲时段。
"""
import json
import sys
import urllib.request
from pathlib import Path

PRICING_URL = "https://client.gaorta.com/api/pricing"
BASE_URL = "https://client.gaorta.com"
OUT = Path(__file__).resolve().parent.parent / "k_stock" / "paper" / "models.json"

# 角色基价（人民币/月，模拟交易计价草案——收费动线后置，此处只读不收）
ROLE_BASE_PRICE = {"manager": 99, "trader": 39, "trainee": 19}

# 模型系数：以 GLM-5.2 为基准 1.0，按 USD 输入价相对比值取整档
# tiered_expr 的 deepseek 取空闲档为基准、高峰档另存 premium 字段
COEF_BASELINE_IN_USD = 1.4  # GLM-5.2: 0.7 * 2


def fetch_pricing():
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    data = json.loads(op.open(PRICING_URL, timeout=20).read())
    return data.get("data", [])


def usd_price(row):
    """返回 (输入USD/1M, 输出USD/1M, peak输入USD/1M or None)"""
    ratio = row.get("model_ratio") or 0
    comp = row.get("completion_ratio") or 1
    if row.get("billing_mode") == "tiered_expr":
        expr = row.get("billing_expr", "")
        # 从表达式抽数字：peak 段 p*x，idle 段 p*y（两处 p 的系数）
        import re
        ps = re.findall(r"p \* ([0-9.]+)", expr)
        cs = re.findall(r"c \* ([0-9.]+)", expr)
        if len(ps) >= 2 and len(cs) >= 2:
            return float(ps[1]), float(cs[1]), float(ps[0])  # idle 在后段
    return round(ratio * 2, 4), round(ratio * 2 * comp, 4), None


def build(rows):
    models = []
    for row in rows:
        name = row["model_name"]
        pin, pout, peak = usd_price(row)
        coef = round(pin / COEF_BASELINE_IN_USD, 2)
        models.append({
            "model": name,
            "vendor_id": row.get("vendor_id"),
            "endpoint": "openai",  # supported_endpoint_types 全为 openai
            "base_url": BASE_URL + "/v1",
            "usd_in_per_m": pin,
            "usd_out_per_m": pout,
            "usd_in_per_m_peak": peak,
            "billing_mode": row.get("billing_mode"),
            "coef_vs_glm52": coef,
            "hire_price_cny_per_month": {
                role: round(base * coef) for role, base in ROLE_BASE_PRICE.items()
            },
            "pricing_version": row.get("pricing_version"),
        })
    models.sort(key=lambda m: m["coef_vs_glm52"])
    return {
        "schema": "k-stock-137/models-v0.1",
        "source": PRICING_URL,
        "constraint": "模型目录仅限 gaorta 平台（老田 2026-10-11 拍板）",
        "coef_baseline": "GLM-5.2 输入价 = 1.0",
        "role_base_price_cny": ROLE_BASE_PRICE,
        "hire_formula": "聘请价 = 角色基价 × coef_vs_glm52（按月，收费动线后置）",
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "models": models,
    }


def main():
    rows = fetch_pricing()
    doc = build(rows)
    text = json.dumps(doc, ensure_ascii=False, indent=1)
    if "--check" in sys.argv and OUT.exists() and OUT.read_text(encoding="utf-8") == text:
        print("check: 无变化")
        return 0
    OUT.write_text(text + "\n", encoding="utf-8")
    print(f"生成 {OUT}：{len(doc['models'])} 个模型")
    for m in doc["models"]:
        peak = f"（高峰 in ${m['usd_in_per_m_peak']}/1M）" if m["usd_in_per_m_peak"] else ""
        print(f"  {m['model']:<20} coef={m['coef_vs_glm52']:<5} "
              f"in=${m['usd_in_per_m']}/1M out=${m['usd_out_per_m']}/1M {peak}"
              f" 经理价 ¥{m['hire_price_cny_per_month']['manager']}/月")
    return 0


if __name__ == "__main__":
    sys.exit(main())
