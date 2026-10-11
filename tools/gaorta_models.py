# -*- coding: utf-8 -*-
"""K-STOCK-137 S-a · gaorta 模型目录生成器

从 https://client.gaorta.com/api/pricing 拉取公开定价清单（无需 key），
生成 k_stock/paper/models.json（模型目录 + USD 换算 + 角色聘请价草案）。

用法：
    python tools/gaorta_models.py            # 拉取并生成
    python tools/gaorta_models.py --check    # 仅比对，无改动则退出 0
    python tools/gaorta_models.py --allow-new  # 放行新增模型（人工确认）

硬约束（老田 2026-10-11 拍板）：模拟交易模型目录仅限 gaorta 平台模型。
One-API 倍率换算：USD/1M tokens(输入) ≈ model_ratio × 2；输出 × completion_ratio。
tiered_expr 模型（deepseek 系）表达式内 p/c 系数已是 USD/1M，分高峰/空闲时段。

K-STOCK-137b（P0-1 信任链）：上游为公开无鉴权 API，零信任——
模型名格式白名单、价格系数区间校验、usd_price 拒绝静默回退、
新增模型须 --allow-new 人工确认。
"""
import argparse
import json
import math
import re
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

# K-STOCK-137b：模型名白名单 + 价格系数上限（USD/1M）
MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
PRICE_CAP_USD = 10.0


def fetch_pricing():
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    data = json.loads(op.open(PRICING_URL, timeout=20).read())
    return data.get("data", [])


def _valid_price(v, cap=None):
    """有限正数，拒绝 NaN/inf/0/负；cap 给定时同时要求 ≤ cap"""
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v) and v > 0
    return ok and (cap is None or v <= cap)


def usd_price(row):
    """返回 (输入USD/1M, 输出USD/1M, peak输入USD/1M or None)

    K-STOCK-137b：解析失败/0/负/NaN 一律抛 ValueError（拒绝静默回退
    到 ratio*2 产出 0 价）；tiered 断言 idle 价 < peak 价，防上游峰谷换序。
    """
    ratio = row.get("model_ratio") or 0
    comp = row.get("completion_ratio") or 1
    if row.get("billing_mode") == "tiered_expr":
        expr = row.get("billing_expr", "")
        # 从表达式抽数字：peak 段 p*x，idle 段 p*y（两处 p 的系数）
        # 兼容有无空格 + 科学计数法
        NUM = r"([0-9.]+(?:[eE][+-]?[0-9]+)?)"  # 含科学计数法，不吞运算符
        ps = re.findall(rf"p\s*\*\s*{NUM}", expr)
        cs = re.findall(rf"c\s*\*\s*{NUM}", expr)
        if len(ps) >= 2 and len(cs) >= 2:
            try:
                vals = [float(x) for x in (ps[0], ps[1], cs[0], cs[1])]
            except ValueError:
                raise ValueError(
                    f"tiered_expr 系数解析失败 model={row.get('model_name')!r} "
                    f"expr片段={expr[:120]!r}")
            peak_in, idle_in, peak_out, idle_out = vals
            # 断言 idle < peak：上游峰谷换序会导致空闲档被当高峰档
            if not idle_in < peak_in:
                raise ValueError(
                    f"tiered 峰谷序异常 model={row.get('model_name')!r} "
                    f"idle_in={idle_in} peak_in={peak_in} expr片段={expr[:120]!r}")
            return idle_in, idle_out, peak_in  # idle 在后段
        raise ValueError(
            f"tiered_expr 缺少成对 p/c 系数 model={row.get('model_name')!r} "
            f"expr片段={expr[:120]!r}")
    try:
        r = float(ratio)
    except (TypeError, ValueError):
        raise ValueError(
            f"model_ratio 非数值 model={row.get('model_name')!r} "
            f"ratio={ratio!r}")
    pin, pout = round(r * 2, 4), round(r * 2 * float(comp), 4)
    if not (_valid_price(pin) and _valid_price(pout)):
        raise ValueError(
            f"ratio 路径价格非法 model={row.get('model_name')!r} "
            f"ratio={ratio!r} comp={comp!r} → in={pin} out={pout}")
    return pin, pout, None


def validate_row(row):
    """K-STOCK-137b：校验单行 pricing 数据。

    返回 (pin, pout, peak)；校验失败打印 stderr 警告并返回 None
    （跳过该行、不中断整批，由调用方累计跳过数）。
    """
    name = row.get("model_name")
    if not isinstance(name, str) or not MODEL_NAME_RE.match(name):
        print(f"[gaorta_models] 警告: 非法模型名 {name!r}，跳过该行", file=sys.stderr)
        return None
    try:
        pin, pout, peak = usd_price(row)
    except ValueError as e:
        print(f"[gaorta_models] 警告: {e}，跳过该行", file=sys.stderr)
        return None
    prices = [(pin, PRICE_CAP_USD), (pout, None)]
    if peak is not None:
        prices.append((peak, PRICE_CAP_USD))
    for v, cap in prices:
        if not _valid_price(v, cap):
            print(f"[gaorta_models] 警告: 模型 {name!r} 价格非法 {v!r}"
                  f"（须有限正数且≤{PRICE_CAP_USD}），跳过该行", file=sys.stderr)
            return None
    return pin, pout, peak


def load_existing_models():
    """读现有 models.json 的模型集合（不存在则空集）"""
    if not OUT.exists():
        return set()
    try:
        doc = json.loads(OUT.read_text(encoding="utf-8"))
        return {m.get("model") for m in doc.get("models", [])}
    except (json.JSONDecodeError, OSError) as e:
        print(f"[gaorta_models] 警告: 现有 {OUT} 不可解析（{e}），视作空目录",
              file=sys.stderr)
        return set()


def build(rows, existing_models=None, allow_new=False):
    """生成目录。K-STOCK-137b：新增模型默认拒绝写入（须 --allow-new），
    返回 None 表示被拒。"""
    if existing_models is None:
        existing_models = set() if allow_new else load_existing_models()
    models = []
    skipped = 0
    for row in rows:
        got = validate_row(row)
        if got is None:
            skipped += 1
            continue
        pin, pout, peak = got
        name = row["model_name"]
        if not allow_new and name not in existing_models:
            print(f"[gaorta_models] 发现新模型 {name}，须 --allow-new 显式确认",
                  file=sys.stderr)
            return None
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
    if skipped:
        print(f"[gaorta_models] 共跳过 {skipped} 行校验失败数据", file=sys.stderr)
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
        "_skipped_rows": skipped,
    }


def _comparable_text(doc):
    """剔除 generated_at 等时间无关差异后的文本（供 --check 比对）"""
    d = dict(doc)
    d.pop("generated_at", None)
    return json.dumps(d, ensure_ascii=False, indent=1, sort_keys=False)


def _existing_comparable_text():
    if not OUT.exists():
        return None
    try:
        return _comparable_text(json.loads(OUT.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="gaorta 模型目录生成器")
    ap.add_argument("--check", action="store_true", help="仅比对，不写文件")
    ap.add_argument("--allow-new", action="store_true",
                    help="放行新增模型（人工确认开关）")
    args = ap.parse_args(argv)

    rows = fetch_pricing()
    doc = build(rows)
    if doc is None:
        print("拒绝写入：存在新增模型且未加 --allow-new", file=sys.stderr)
        return 1

    skipped = doc.pop("_skipped_rows", 0)
    text = json.dumps(doc, ensure_ascii=False, indent=1)

    if args.check:
        problems = []
        if skipped:
            problems.append(f"{skipped} 行数据校验失败（校验失败=差异）")
        old = _existing_comparable_text()
        if old != _comparable_text(doc):
            problems.append("models.json 内容与生成结果不一致")
        if problems:
            print("check: 存在差异 —— " + "；".join(problems), file=sys.stderr)
            return 1
        print("check: 无变化")
        return 0

    OUT.write_text(text + "\n", encoding="utf-8")
    print(f"生成 {OUT}：{len(doc['models'])} 个模型"
          + (f"（跳过 {skipped} 行）" if skipped else ""))
    for m in doc["models"]:
        peak = f"（高峰 in ${m['usd_in_per_m_peak']}/1M）" if m["usd_in_per_m_peak"] else ""
        print(f"  {m['model']:<20} coef={m['coef_vs_glm52']:<5} "
              f"in=${m['usd_in_per_m']}/1M out=${m['usd_out_per_m']}/1M {peak}"
              f" 经理价 ¥{m['hire_price_cny_per_month']['manager']}/月")
    return 0


if __name__ == "__main__":
    sys.exit(main())
