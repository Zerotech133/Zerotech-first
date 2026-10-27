# -*- coding: utf-8 -*-
"""K-STOCK-137g P2 · gaorta 模型目录解析与计价一致性（被测：tools/gaorta_models.py
+ k_stock/paper/models.json 数据一致性）。

覆盖：usd_price tiered/科学计数法/无空格/解析失败/idle≥peak/0·负值拒；
validate_row 合法行/非法模型名/coef 异常拒；models.json 聘请价与 coef 复算一致。
"""

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE / ".." / ".."
sys.path.insert(0, str(REPO / "tools"))

import gaorta_models as gm  # noqa: E402

MODELS_FP = REPO / "k_stock" / "paper" / "models.json"


def _flash_expr() -> str:
    """deepseek-v4-flash 的 tiered 表达式（与 models.json 快照一致：
    peak in 0.44 / idle in 0.22 / peak out 1.32 / idle out 0.66）。"""
    return "c*1.32 p*0.44 c*0.66 p*0.22"


def _pro_expr() -> str:
    """deepseek-v4-pro：peak in 0.6204 / idle in 0.3102 / peak out 1.8612 /
    idle out 0.9306。"""
    return "c*1.8612 p*0.6204 c*0.9306 p*0.3102"


def test_usd_price_tiered_real_exprs():
    # flash：peak 段在前、idle 段在后（ps[0]=peak, ps[1]=idle）
    pin, pout, peak = gm.usd_price(
        {
            "billing_mode": "tiered_expr",
            "billing_expr": _flash_expr(),
            "model_name": "deepseek-v4-flash",
        }
    )
    assert (pin, pout, peak) == (0.22, 0.66, 0.44)
    pin, pout, peak = gm.usd_price(
        {
            "billing_mode": "tiered_expr",
            "billing_expr": _pro_expr(),
            "model_name": "deepseek-v4-pro",
        }
    )
    assert (pin, pout, peak) == (0.3102, 0.9306, 0.6204)
    print("PASS test_usd_price_tiered_real_exprs")


def test_usd_price_scientific_and_no_spaces():
    pin, pout, peak = gm.usd_price(
        {
            "billing_mode": "tiered_expr",
            "billing_expr": "p*8.8e-1,c*2.64,p*4.4e-1,c*1.32",
            "model_name": "sci-test",
        }
    )
    assert (pin, pout, peak) == (0.44, 1.32, 0.88)
    pin, pout, peak = gm.usd_price(
        {
            "billing_mode": "tiered_expr",
            "billing_expr": "p*0.44,c*1.32,p*0.22,c*0.66",
            "model_name": "nospace-test",
        }
    )
    assert (pin, pout, peak) == (0.22, 0.66, 0.44)
    print("PASS test_usd_price_scientific_and_no_spaces")


def test_usd_price_parse_failure():
    for expr in (
        "p*0.44,c*1.32",  # 缺成对系数
        "peak 0.44 idle 0.22",  # 完全无 p/c
        "p*x,c*1,p*x,c*1",  # 数字解析失败
        "",
    ):
        try:
            gm.usd_price(
                {
                    "billing_mode": "tiered_expr",
                    "billing_expr": expr,
                    "model_name": "bad",
                }
            )
            raise AssertionError(f"expr={expr!r} 应 ValueError")
        except ValueError:
            pass
    print("PASS test_usd_price_parse_failure")


def test_usd_price_peak_idle_order():
    try:
        gm.usd_price(
            {
                "billing_mode": "tiered_expr",
                "billing_expr": "p*0.22,c*0.66,p*0.22,c*0.66",  # idle==peak
                "model_name": "swap",
            }
        )
        raise AssertionError("idle≥peak 应 ValueError")
    except ValueError:
        pass
    try:
        gm.usd_price(
            {
                "billing_mode": "tiered_expr",
                "billing_expr": "p*0.1,c*0.3,p*0.22,c*0.66",  # idle>peak
                "model_name": "swap2",
            }
        )
        raise AssertionError("idle>peak 应 ValueError")
    except ValueError:
        pass
    print("PASS test_usd_price_peak_idle_order")


def test_usd_price_zero_negative_nan():
    for ratio in (0, -1, "nan", None):
        try:
            gm.usd_price(
                {
                    "model_ratio": ratio,
                    "completion_ratio": 1.0,
                    "model_name": "bad-ratio",
                }
            )
            raise AssertionError(f"ratio={ratio!r} 应 ValueError")
        except ValueError:
            pass
    print("PASS test_usd_price_zero_negative_nan")


def test_validate_row_accepts_valid():
    row = {"model_name": "GLM-5.2", "model_ratio": 0.7, "completion_ratio": 1.0}
    assert gm.validate_row(row) == (1.4, 1.4, None)
    row = {
        "model_name": "deepseek-v4-flash",
        "billing_mode": "tiered_expr",
        "billing_expr": _flash_expr(),
    }
    assert gm.validate_row(row) == (0.22, 0.66, 0.44)
    print("PASS test_validate_row_accepts_valid")


def test_validate_row_rejects_bad_names(capsys):
    for bad in ("../evil", "a" * 65, "bad name", "", None):
        assert gm.validate_row({"model_name": bad, "model_ratio": 0.7}) is None, (
            f"非法模型名 {bad!r} 应跳过"
        )
    assert "非法模型名" in capsys.readouterr().err
    print("PASS test_validate_row_rejects_bad_names")


def test_validate_row_rejects_bad_coef_prices(capsys):
    # coef = pin/1.4：pin 非正 / NaN / 超帽（>10 USD → coef>7.14）均拒
    assert gm.validate_row({"model_name": "m0", "model_ratio": 0}) is None  # coef 0
    assert gm.validate_row({"model_name": "m-", "model_ratio": -0.7}) is None  # coef 负
    assert gm.validate_row({"model_name": "mn", "model_ratio": "nan"}) is None  # NaN
    assert gm.validate_row({"model_name": "mc", "model_ratio": 6.0}) is None  # 超帽
    err = capsys.readouterr().err
    assert err.count("价格非法") == 4
    print("PASS test_validate_row_rejects_bad_coef_prices")


def test_models_json_consistency():
    doc = json.loads(MODELS_FP.read_text(encoding="utf-8"))
    assert "gaorta" in doc["constraint"]
    models = doc["models"]
    assert len(models) == 5
    for m in models:
        # coef == round(usd_in / 1.4, 2)
        assert m["coef_vs_glm52"] == round(m["usd_in_per_m"] / 1.4, 2), (
            f"{m['model']} coef 不一致"
        )
        # 三档聘请价 == round(基价 × coef)
        for role, base in gm.ROLE_BASE_PRICE.items():
            want = round(base * m["coef_vs_glm52"])
            assert m["hire_price_cny_per_month"][role] == want, (
                f"{m['model']}.{role} 聘请价不一致"
            )
        assert m["base_url"] == "https://client.gaorta.com/v1"
        # tiered 模型的 usd_price 复算与快照一致
        if m["billing_mode"] == "tiered_expr":
            pin, pout, peak = gm.usd_price(
                {
                    "billing_mode": "tiered_expr",
                    "billing_expr": _flash_expr()
                    if "flash" in m["model"]
                    else _pro_expr(),
                    "model_name": m["model"],
                }
            )
            assert (pin, pout, peak) == (
                m["usd_in_per_m"],
                m["usd_out_per_m"],
                m["usd_in_per_m_peak"],
            )
        else:
            assert m["usd_in_per_m_peak"] is None
            assert not math.isnan(m["usd_in_per_m"]) and m["usd_in_per_m"] > 0
    print("PASS test_models_json_consistency")


def test_capability_tier_present_and_valid():
    """K-STOCK-137i 拍板项2：每模型含 capability_tier 且取值合法"""
    doc = json.loads(MODELS_FP.read_text(encoding="utf-8"))
    valid = set(gm.CAPABILITY_TIERS.values()) | {gm.DEFAULT_TIER}
    for m in doc["models"]:
        tier = m.get("capability_tier")
        assert tier in valid, f"{m['model']} capability_tier 非法: {tier!r}"
        expected = gm.CAPABILITY_TIERS.get(m["model"], gm.DEFAULT_TIER)
        assert tier == expected, f"{m['model']} capability_tier 应为 {expected}"
    print("PASS test_capability_tier_present_and_valid")


def test_capability_tier_glm53_no_markup():
    """K-STOCK-137i：GLM-5.3 标'新旗舰'但 coef==GLM-5.2（价格随上游，不人为加价回归锚）"""
    doc = json.loads(MODELS_FP.read_text(encoding="utf-8"))
    models = {m["model"]: m for m in doc["models"]}
    assert models["GLM-5.3"]["capability_tier"] == "新旗舰"
    assert models["GLM-5.2"]["capability_tier"] == "上一代旗舰"
    assert models["GLM-5.3"]["coef_vs_glm52"] == models["GLM-5.2"]["coef_vs_glm52"], (
        "GLM-5.3 与 5.2 coef 应一致（差异化走能力标签，不走价格）"
    )
    assert models["GLM-5.3"]["usd_in_per_m"] == models["GLM-5.2"]["usd_in_per_m"]
    print("PASS test_capability_tier_glm53_no_markup")


def test_hire_formula_mentions_profile_override():
    """K-STOCK-147 P2-10：hire_formula 注明「档案可覆盖统一基价」（蓝图拍板项 1）"""
    doc = json.loads(MODELS_FP.read_text(encoding="utf-8"))
    formula = doc["hire_formula"]
    assert "档案可覆盖" in formula, f"hire_formula 应注明档案可覆盖: {formula!r}"
    assert "hire_base_price_cny" in formula, (
        f"hire_formula 应指明档案字段 hire_base_price_cny: {formula!r}"
    )
    print("PASS test_hire_formula_mentions_profile_override")


if __name__ == "__main__":
    test_usd_price_tiered_real_exprs()
    test_usd_price_scientific_and_no_spaces()
    test_usd_price_parse_failure()
    test_usd_price_peak_idle_order()
    test_usd_price_zero_negative_nan()
    test_validate_row_accepts_valid()
    test_validate_row_rejects_bad_names()
    test_validate_row_rejects_bad_coef_prices()
    test_models_json_consistency()
    test_capability_tier_present_and_valid()
    test_capability_tier_glm53_no_markup()
    test_hire_formula_mentions_profile_override()
    print("12/12 PASS")
