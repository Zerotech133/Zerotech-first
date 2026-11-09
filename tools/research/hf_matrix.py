# -*- coding: utf-8 -*-
"""缠论资产盘点：58 个老脚本 × 新版纳入状态 → portal/hf_matrix.json。

为什么单独成文件而不是写死在报告里
--------------------------------
报告是一次性的，代码是活的。下次有人加了新引擎或补了某个模块，只需要
重跑本脚本：

  · 代码级复用率是**扫 import 自动算的**（不靠人记）；
  · 状态判定是人写的，但每一行都带「新版对应物」字段，便于逐条核；
  · 四种状态口径固定：adopted 纳入 / degraded 降级 / missing 缺失 / na 不在本体。

状态口径
--------
adopted   新版有等价或更强的实现（可在 evidence 里指到文件/函数）
degraded  方法论语义只保住一部分（例如字段在但无报表、多周期只剩日线）
missing   方法论有、新版完全没有
na        不属于方法论本体：一次性研究、其他市场、工程治理、展示层
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
QB = os.path.join(ROOT, "quant_backtest")
OUT = os.path.join(ROOT, "portal", "hf_matrix.json")

# 新一代引擎清单（用于自动统计代码级复用）
NEW = [
    "tools/research/hf_book.py", "tools/research/hf_hist.py",
    "tools/research/chan_struct.py", "tools/research/chanlun_pool.py",
    "tools/research/chanlun_regime.py", "tools/research/trade_book.py",
    "tools/research/shadow_book.py", "tools/research/observe_board.py",
    "tools/research/three_layer_v3.py", "tools/research/hf_ab.py",
    "tools/research/hf_variants.py", "tools/research/hf_phase3.py",
    "tools/research/hf_phase4.py", "tools/research/hf_phase5.py",
    "tools/research/hf_phase6.py",
]

# (脚本, 层, 状态, 新版对应物/说明)
MATRIX = [
    # ---- L1 结构 ----
    ("chanlun_data.py", "L1结构", "degraded", "hf_book.load_bars：只日线，30m/1w/多周期丢失"),
    ("chanlun_fractal.py", "L1结构", "adopted", "chan_struct import find_fractals/_merge_inclusion"),
    ("chanlun_bi.py", "L1结构", "adopted", "chan_struct import find_bis/find_pivots（find_segments 未用）"),
    ("chanlun_pattern.py", "L1结构", "na", "15 种 K 线形态是补强，非缠论本体"),
    # ---- L2 买卖点 ----
    ("chanlun_signal.py", "L2买卖点", "adopted", "hf_hist 直接调 analyze，871 天 63,252 条"),
    ("chanlun_3buy_limit.py", "L2买卖点", "missing", "3买等回踩 vs 追突破，未纳入"),
    # ---- L3 影子九项 ----
    ("chanlun_macd.py", "L3影子", "degraded", "mbei 字段率 27.7%，只存不用，无分层报表"),
    ("chanlun_flow.py", "L3影子", "degraded", "vv 100% 保留；reg 大势标签 0% 整项丢失"),
    ("chanlun_trend.py", "L3影子", "degraded", "tbe 字段率 24.1%，只存不用"),
    ("chanlun_mlf.py", "L3影子", "missing", "mls 质量分（LightGBM 20 日上涨概率）未纳入"),
    ("chanlun_tbe_stats.py", "L3影子", "missing", "tbe/pbe 分层对照报表未纳入"),
    # ---- L4 富化 ----
    ("chanlun_gray.py", "L4富化", "missing", "中枢演化 + 背驰双口径 + 置信度融合，整层缺失"),
    # ---- L5 多级别 ----
    ("chanlun_mtf.py", "L5多级别", "missing", "日线 × 60 分钟共振，需分钟数据"),
    ("chanlun_li122.py", "L5多级别", "missing", "多级别联立纯函数层"),
    ("chanlun_market.py", "L7三层", "degraded",
     "大盘研判→build_regime（20 日动量档）；择势语义由 tools/research/"
     "chanlun_regime.py + hf_book.regime_gate 承接，结构档降级为动量档"),
    # ---- L6 复合与质量 ----
    ("chanlun_composite.py", "L6复合", "missing", "RULES/judge_all 多侧面叠加未纳入"),
    ("chanlun_quality_layering.py", "L6复合", "missing", "买点质量分层未纳入"),
    # ---- L7 三层分工 ----
    ("chanlun_position.py", "L7三层", "missing", "卖出 v3 状态机；新版只有 hold/tag/结构止损"),
    # ---- 交易规则 ----
    ("chanlun_scan.py", "交易规则", "adopted", "产线 07:10/15:35 仍在跑，trade_book 读台账"),
    ("chanlun_gate.py", "交易规则", "degraded", "G2成本/G3集中→limit_gate+gross_cap；G1池子未接"),
    ("chanlun_backtest.py", "交易规则", "adopted", "hf_book.run_once 取代"),
    ("chanlun_alpha.py", "交易规则", "adopted", "hf_phase4.beta_alpha"),
    ("chanlun_beta_tv.py", "交易规则", "missing", "beta 时变修正未纳入"),
    ("chanlun_cost_model.py", "交易规则", "adopted", "hf_phase5：滑点+√冲击+ADV20，更强"),
    ("chanlun_exit_policy.py", "交易规则", "adopted", "hf_variants tag_all/tag_red/tag_half"),
    ("chanlun_hold_sensitivity.py", "交易规则", "adopted", "hf_phase5 D + hf_phase6 C/D（带势）"),
    ("chanlun_daily_ledger.py", "交易规则", "degraded", "hf_book 有账本，但不写产线 ledger"),
    ("chanlun_ledger_maint.py", "交易规则", "missing", "账本维护（字段修复/重建/归档）"),
    ("chanlun_healthcheck.py", "交易规则", "degraded", "各 hf_*_selfcheck 取代，无统一入口"),
    ("chanlun_conformance.py", "交易规则", "missing", "双口径一致性审计"),
    ("chanlun_learn.py", "交易规则", "missing", "学习环 L1-L4"),
    # ---- 验证与样本 ----
    ("chan_anchor_backtest.py", "验证样本", "adopted", "hf_phase4 B 五窗口独立起算"),
    ("chan_oos_tushare.py", "验证样本", "adopted", "hf_phase4 B + hf_phase5 C walk-forward"),
    ("chanlun_pit_select.py", "验证样本", "adopted", "hf_hist --verify 逐候选截断重放"),
    ("chanlun_month_replay.py", "验证样本", "adopted", "hf_hist 871 天再生"),
    ("chanlun_month_replay_pit.py", "验证样本", "adopted", "hf_hist --verify（剔除 18% 前视）"),
    ("chanlun_ext_backfill.py", "验证样本", "adopted", "hf_hist 全市场 871 天扩样本"),
    ("chanlun_sample.py", "验证样本", "adopted", "hf_hist 取代"),
    # ---- 研究（不在本体）----
    ("chanlun_struct_features.py", "研究", "na", "结构特征矩阵，供 ML 用"),
    ("chanlun_struct_ml.py", "研究", "na", "训练 IC 0.24 → 测试 0.00，自证过拟合"),
    ("chanlun_struct_ml_diag.py", "研究", "na", "上者的诊断脚本"),
    ("chanlun_struct_ml_rank.py", "研究", "na", "横截面分位标签重做"),
    ("chanlun_tgraph.py", "研究", "na", "结构时序图谱指纹"),
    ("chanlun_rotation_backtest.py", "研究", "na", "交易视角轮动"),
    ("chanlun_rotation_v6.py", "研究", "na", "学院方案对齐"),
    ("chanlun_rotation_v6c.py", "研究", "na", "学院组合制"),
    ("chanlun_topk_backtest.py", "研究", "na", "Top-K 优选"),
    ("chanlun_q_verify.py", "研究", "na", "q_ 子集 IC 真伪"),
    ("chanlun_annotation_stability.py", "研究", "na", "标注可复现性度量"),
    # ---- 其他市场 ----
    ("chanlun_cross_market.py", "其他市场", "na", "A 股/美股/港股横截面 IC"),
    ("chanlun_gold_b3_bench.py", "其他市场", "na", "沪金 b3 剥离黄金 beta"),
    ("chanlun_gold_b3_verify.py", "其他市场", "na", "沪金前视偏差量化"),
    ("gold_chanlun_shadow.py", "其他市场", "na", "沪金缠论影子统计"),
    # ---- 展示层 ----
    ("chanlun_card.py", "展示层", "missing", "五面推演卡"),
    ("chanlun_plot.py", "展示层", "missing", "结构图出图"),
    ("chanlun_annotate.py", "展示层", "missing", "结构标注产物"),
    ("chanlun_copy_audit.py", "展示层", "na", "对外文案合规扫描"),
]

STATE_CN = {"adopted": "纳入", "degraded": "降级", "missing": "缺失", "na": "不在本体"}


def code_reuse(valid_modules):
    """扫 import 统计代码级复用。只认真实 import，且模块必须**是老脚本**。

    chan_struct / hf_* 都是新一代自己的模块，被 import 不算复用老代码——
    首版没排除，把 chan_struct 算进去，复用率虚高成 5/59。
    """
    pat = re.compile(r"^\s*(?:import\s+(\w+)|from\s+(\w+)\s+import)", re.M)
    used = {}
    for p in NEW:
        fp = os.path.join(ROOT, p)
        if not os.path.exists(fp):
            continue
        txt = open(fp, encoding="utf-8").read()
        for m in pat.finditer(txt):
            mod = m.group(1) or m.group(2)
            if mod and mod in valid_modules and mod != "chanlun_pool":
                used.setdefault(mod, []).append(os.path.basename(p))
    return used


def check_exist():
    """矩阵条目必须与实际文件对齐：多列=盘点点错，漏列=资产没入账。"""
    real = sorted(os.path.basename(x) for x in
                  __import__("glob").glob(os.path.join(QB, "chan*.py")))
    real.append("gold_chanlun_shadow.py")
    listed = {r[0] for r in MATRIX}
    extra = sorted(listed - set(real))
    miss = sorted(set(real) - listed)
    return real, extra, miss


def main():
    real, extra, miss = check_exist()
    if extra:
        print("! 矩阵里有 %d 个不存在的脚本：%s" % (len(extra), ", ".join(extra)))
    if miss:
        print("! 实际有 %d 个脚本没进矩阵：%s" % (len(miss), ", ".join(miss)))
    rows = []
    for name, layer, state, ev in MATRIX:
        rows.append({"script": name, "layer": layer, "state": state,
                     "state_cn": STATE_CN[state], "evidence": ev})
    tot = {}
    for r in rows:
        tot[r["state"]] = tot.get(r["state"], 0) + 1
    layers = {}
    for r in rows:
        layers.setdefault(r["layer"], []).append(r)
    for k, v in layers.items():
        c = {}
        for x in v:
            c[x["state"]] = c.get(x["state"], 0) + 1
        layers[k] = {"n": len(v), "states": c, "items": v}

    valid = {r["script"][:-3] for r in rows}
    reuse = code_reuse(valid)
    n_old = len(rows)
    out = {
        "generated_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "n_scripts": n_old,
        "n_lines": 22332,
        "totals": tot,
        "layers": layers,
        "align": {"extra": extra, "missing": miss,
                  "n_real": len(real), "ok": not extra and not miss},
        "code_reuse": {
            "n_reused": len(reuse),
            "n_total": n_old,
            "ratio": round(len(reuse) / n_old, 4),
            "modules": {k: sorted(set(v)) for k, v in sorted(reuse.items())},
            "note": "只统计新版引擎里真实 import 的**老**模块；"
                    "chan_struct/hf_* 属新一代，不算复用",
        },
        "rows": rows,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("%d 个脚本：%s" % (n_old, "  ".join(
        "%s %d" % (STATE_CN[k], v) for k, v in tot.items())))
    print("代码级复用 %d/%d = %.1f%%：%s"
          % (len(reuse), n_old, len(reuse) / n_old * 100,
             ", ".join(sorted(reuse))))
    print("→ %s (%.1f KB)" % (OUT, os.path.getsize(OUT) / 1e3))
    return 0


if __name__ == "__main__":
    sys.exit(main())
