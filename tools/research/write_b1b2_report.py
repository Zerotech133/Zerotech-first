"""Build the B1/B2 full-market IC / long-short report from two eval JSONs."""
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))
import factor_lib_tushare as ft  # noqa: E402

B1 = ROOT / "research" / "reports" / "factor_b1_full_eval_20261105.json"
B2 = ROOT / "research" / "reports" / "factor_b2_full_eval_20261105.json"
OUT = ROOT / "deliverables" / "2026-11-05_B1B2全市场20日CAR_IC与多空评测.md"
SUMMARY = ROOT / "research" / "reports" / "factor_b1b2_summary_20261105.json"

r1 = json.loads(B1.read_text(encoding="utf-8"))
r2 = json.loads(B2.read_text(encoding="utf-8"))
meta = r1["meta"]
eff = meta["effective"]
assert eff == r2["meta"]["effective"], (eff, r2["meta"]["effective"])


def ftype(name: str) -> str:
    return ft.TUSHARE_FACTOR_MAP.get(name, {}).get("factor_type", "-")


def fam(name: str) -> str:
    return r1["factors"].get(name, r2["factors"].get(name, {})).get("family", "-")


rows = []
for batch, r in (("B1", r1), ("B2", r2)):
    for name, e in r["factors"].items():
        ic = e.get("ic") or {}
        di = e.get("ic_directed") or {}
        q = (e.get("quintiles") or {}).get("top_minus_bottom") or {}
        boot = (e.get("bootstrap") or {}).get("ic_directed") or {}
        iso = e.get("is_oos") or {}
        rows.append(dict(
            name=name, batch=batch, family=fam(name), ftype=ftype(name),
            direction=e.get("direction"), days=e.get("n_days"),
            ic=ic.get("mean"), t=di.get("t"), ir=di.get("ir"),
            ann=q.get("directed_ann_pct"), p=boot.get("p_le_0"),
            is_ic=(iso.get("IS") or {}).get("mean"),
            oos_ic=(iso.get("OOS") or {}).get("mean"),
            obs=e.get("n_obs"),
        ))

valid = [x for x in rows if x["ic"] is not None]
nulls = [x for x in rows if x["ic"] is None]
valid.sort(key=lambda x: abs(x["ic"]), reverse=True)

families = ["Tushare-动量", "Tushare-流动性", "Tushare-风险", "Tushare-反转", "Tushare-规模", "Tushare-Alpha101"]
fam_stats = {}
for f in families:
    xs = [x for x in rows if x["family"] == f]
    vs = [x for x in xs if x["ic"] is not None]
    ics = [abs(x["ic"]) for x in vs]
    top = max(vs, key=lambda x: abs(x["ic"])) if vs else None
    fam_stats[f] = dict(
        n=len(xs), valid=len(vs),
        med=statistics.median(ics) if ics else None,
        min_days=min((x["days"] for x in xs), default=None),
        max_days=max((x["days"] for x in xs), default=None),
        top=top["name"] if top else "-",
        top_ic=top["ic"] if top else None,
    )

by_ann = sorted([x for x in valid if x["ann"] is not None], key=lambda x: x["ann"], reverse=True)
top_ann = by_ann[:10]
bottom_ann = by_ann[-5:]
weak = sorted(valid, key=lambda x: abs(x["ic"]))[:10]
short = sorted([x for x in valid if (x["days"] or 0) < eff["signal_days"] - 5], key=lambda x: x["days"] or 0)

all_med = statistics.median([abs(x["ic"]) for x in valid]) if valid else None
family_rank = sorted(fam_stats.items(), key=lambda kv: (kv[1]["med"] or -1), reverse=True)
best_family = family_rank[0][0]
best_top = valid[0] if valid else None
best_ann = by_ann[0] if by_ann else None


def fmt(x, nd=4, signed=True):
    if x is None:
        return "-"
    return f"{x:+.{nd}f}" if signed else f"{x:.{nd}f}"


def pct(x):
    return "-" if x is None else f"{x:+.2f}%"


def pval(x):
    return "-" if x is None else f"{x:.4f}"


def num(x, nd=0):
    return "-" if x is None else f"{x:.{nd}f}"


L = []
A = L.append
A("# B1/B2 全市场 20 日 CAR 横截面 IC / 多空评测")
A("")
A("- 日期：2026-11-05")
A("- 任务：m07333「先把 B1/B2 的全市场 IC/多空评测补完」")
A(f"- 样本：**B1 行情/估值 86 个 + B2 Alpha101 101 个 = 187 个因子**；B3 财务 84 个此前已单独评测，本报告不重复。")
if valid and best_top:
    b2_rows = [x for x in valid if x["batch"] == "B2"]
    b2_med = statistics.median([abs(x["ic"]) for x in b2_rows]) if b2_rows else None
    b2_top = max(b2_rows, key=lambda x: abs(x["ic"])) if b2_rows else None
    b2_txt = ""
    if b2_top:
        b2_txt = (f" B2 Alpha101 中位 |IC| 更低（{b2_med:.4f}），但最强 `{b2_top['name']}` "
                  f"IC {b2_top['ic']:+.4f}/多空 {pct(b2_top['ann'])}。")
    A(f"- 结论先看：**{len(valid)}/{len(rows)} 个因子得到有效 IC；全样本中位 |IC| {all_med:.4f}；"
      f"最强因子 `{best_top['name']}` IC {best_top['ic']:+.4f}/多空年化 {pct(best_top['ann'])}；"
      f"分族中位 |IC| 最高的是 {best_family}（{fam_stats[best_family]['med']:.4f}）。{b2_txt}"
      "价格量因子整体比 B3 财务因子更有区分度，但仍需中性化、可交易性和样本外验证后再谈策略化。**")
    A("- 方向提示：B1 的 `return_*` 动量类、`MACD`、`DIF`、`beta` 类等不少因子 IC 与 spec 的 direction 相反（动量整体呈反转、低 beta 占优）；本表保留原始 factor spec 方向，未按样本内结果调头，后续做策略前必须先按先验方向重定义，避免用全样本 IC 反推方向造成过拟合。")
A("")
A("## 1. 口径")
A("")
A("| 项 | 设置 |")
A("|---|---|")
A(f"| 信号窗口 | {eff['start']} ~ {eff['end']}（{eff['signal_days']} 个交易日） |")
A(f"| 面板 | {eff['panel_dates']} 个交易日 × {eff['codes']} 只（含 warmup） |")
A("| 股票池 | `tradable`：收盘≥2 元、成交额≥5000 万、上市≥250 天 |")
A("| 前瞻收益 | 20 日 CAR：T+1 收盘入场，T+21 收盘卖出；个股复权收益 − 等权市场 − 30bp 成本 |")
A("| IC | 每日横截面 Spearman(因子, 20 日 CAR)；`IC_t` 为时序 t 值 |")
A("| 多空 | 每日横截面五分组，`多空年化 = Q5−Q1 日收益均值 × 252/20`（方向调整后）；重叠窗口近似 |")
A("| bootstrap | 50 次 block bootstrap（B3 为 300 次；p 值只作筛选参考） |")
A(f"| IS/OOS | 自动取信号窗口中点（{meta['is_end']}），仅作稳定性参考 |")
A(f"| 基准 | 全市场合格股票等权 20 日 CAR 均值 **{meta['baseline']['mean_pct']:+.3f}%**（n={meta['baseline']['n_obs']:,}） |")
A("")
A("> 说明：B1/B2 用 2023-01 起的更长面板，是为了让 250/500/750 日回看的因子有足够历史；B3 之前用 2025-01 起面板，因此**不能直接用本表的 IC 与 B3 的 IC 做绝对比较**，只能看因子族内部的相对强弱。")
A("")
A("## 2. 分族统计")
A("")
A("| 因子族 | 数量 | 有效 IC | 中位 |IC| | 有效天数范围 | 族内最强因子 | 最强 IC |")
A("|---|---:|---:|---:|---:|---|---:|")
for f in families:
    s = fam_stats[f]
    rng = f"{s['min_days']}–{s['max_days']}" if s["min_days"] is not None else "-"
    A(f"| {f} | {s['n']} | {s['valid']} | {num(s['med'], 4)} | {rng} | `{s['top']}` | {fmt(s['top_ic'])} |")
A("")
A("读法：")
A("")
A("- **B1 的量价/流动性/风险因子**历史最长覆盖；但 1320 日回看类因子在 968 日面板上仍没有足够历史，IC 为空或只有短样本。")
A("- **B2 Alpha101** 大部分公式只回看 5–250 日，样本覆盖完整；复杂度高，稳定性要看 IS/OOS 是否一致。")
A("- 多空年化是重叠窗口近似值，不能当独立日频组合收益；显著性优先看 IC_t、bootstrap p 和 IS/OOS 一致性。")
A("")
A("## 3. Top 20（按 |IC|）")
A("")
A("| # | 因子 | 批次 | 类型 | 方向 | 有效日 | IC | IC_t | ICIR | 多空年化 | p(≤0) | IS IC | OOS IC |")
A("|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
for i, x in enumerate(valid[:20], 1):
    A(f"| {i} | `{x['name']}` | {x['batch']} | {x['ftype']} | {x['direction']:+d} | {x['days']} | "
      f"{fmt(x['ic'])} | {fmt(x['t'], 2)} | {fmt(x['ir'], 3)} | {pct(x['ann'])} | "
      f"{pval(x['p'])} | {fmt(x['is_ic'])} | {fmt(x['oos_ic'])} |")
A("")
A("## 4. 多空方向最强 / 最弱")
A("")
A("| 组 | 因子 | 类型 | 方向 | IC | 多空年化 | 有效日 |")
A("|---|---|---|---:|---:|---:|---:|")
for x in top_ann:
    A(f"| 强 | `{x['name']}` | {x['ftype']} | {x['direction']:+d} | {fmt(x['ic'])} | {pct(x['ann'])} | {x['days']} |")
for x in bottom_ann:
    A(f"| 弱 | `{x['name']}` | {x['ftype']} | {x['direction']:+d} | {fmt(x['ic'])} | {pct(x['ann'])} | {x['days']} |")
A("")
A("## 5. 弱因子 / 历史不足")
A("")
A("| 因子 | 类型 | IC | 多空年化 | 有效日 | 备注 |")
A("|---|---|---:|---:|---:|---|")
for x in weak:
    note = "接近 0，无区分度" if abs(x["ic"]) < 0.005 else "区分度弱"
    if x["days"] is not None and x["days"] < eff["signal_days"] - 5:
        note = "历史不足"
    A(f"| `{x['name']}` | {x['ftype']} | {fmt(x['ic'])} | {pct(x['ann'])} | {x['days']} | {note} |")
if nulls:
    A("")
    A("**无有效 IC（历史不足）**：")
    A("")
    for x in nulls:
        A(f"- `{x['name']}`（{x['ftype']}）：有效日 {x['days']}")
A("")
A("## 6. 全 187 因子明细（按 |IC| 降序）")
A("")
A("| # | 因子 | 批次 | 类型 | 方向 | 有效日 | IC | IC_t | ICIR | 多空年化 | p(≤0) | IS IC | OOS IC |")
A("|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
for i, x in enumerate(valid, 1):
    A(f"| {i} | `{x['name']}` | {x['batch']} | {x['ftype']} | {x['direction']:+d} | {x['days']} | "
      f"{fmt(x['ic'])} | {fmt(x['t'], 2)} | {fmt(x['ir'], 3)} | {pct(x['ann'])} | "
      f"{pval(x['p'])} | {fmt(x['is_ic'])} | {fmt(x['oos_ic'])} |")
A("")
A("## 7. 方法与限制")
A("")
A("- 因子全部为确定性公式计算，非机器学习训练；B1 为量价/换手/风险/规模，B2 为 Alpha101 表达式。")
A("- B2 首轮实现中 `Ts_Rank`/`Ts_ArgMax`/`Ts_ArgMin`/`Decay_Linear` 用 pandas `rolling.apply` 逐窗口 Python 调用，全市场运行超 50 分钟未完成；已改为 numpy 滑窗向量化，并用 pandas 参考实现对 2/5/20 日窗口（含 NaN 与部分窗口）验证 maxdiff 为 0，新增 3 个回归测试。B2 重跑 **745.7s**，B1 **277.8s**，两批合计约 17 分钟（不含早期被终止的慢跑）。")
A("- 未做行业/市值中性化、未做换手/涨跌停/停牌可交易性约束；B3/Value 线此前证明中性化会明显改变结论，B1/B2 后续应单独做中性化评测。")
A("- 20 日 CAR 重叠窗口，多空年化只作量级比较；bootstrap 50 次，p 值不如 B3 的 300 次稳定。")
A("- 2023 年 PIT 行业/股票池覆盖低于 2026 年，早期样本的横截面完整性较弱。")
A("- 本报告只做研究评测，**生产选股链路未接入 B1/B2 因子**。")
A("")
A("## 8. 产物与复现")
A("")
A("- 原始 JSON：[factor_b1_full_eval_20261105.json](research/reports/factor_b1_full_eval_20261105.json)、[factor_b2_full_eval_20261105.json](research/reports/factor_b2_full_eval_20261105.json)")
A("- 汇总 JSON：[factor_b1b2_summary_20261105.json](research/reports/factor_b1b2_summary_20261105.json)")
A("- 运行命令：")
A("")
A("```powershell")
A("cd C:\\项目\\老田的AI助手")
A("& C:\\项目\\py312\\python.exe -X utf8 tools/research/run_b1b2_full_eval.py b1")
A("& C:\\项目\\py312\\python.exe -X utf8 tools/research/run_b1b2_full_eval.py b2")
A("& C:\\项目\\py312\\python.exe -X utf8 tools/research/write_b1b2_report.py")
A("```")
A("")
A("- 说明：B1/B2 分开进程顺序跑，单进程耗时见 JSON `meta.runtime_sec`；总 wall time 约 17 分钟（B2 首轮慢跑已被终止，不计入）。")
A("")

OUT.write_text("\n".join(L), encoding="utf-8")
summary = dict(
    generated_at=meta["generated_at"],
    effective=eff,
    baseline=meta["baseline"],
    n_factors=len(rows),
    n_valid_ic=len(valid),
    median_abs_ic=all_med,
    families=fam_stats,
    top=valid[:20],
    top_long_short=top_ann,
    weak=weak,
    nulls=[x["name"] for x in nulls],
)
SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
print("wrote", OUT, "chars", OUT.stat().st_size)
print("summary", SUMMARY)
print("valid", len(valid), "nulls", len(nulls), "best", best_top["name"] if best_top else None)
