# -*- coding: utf-8 -*-
"""JEV conf 校准重训（K-DATA-20261110-JEVCAL2）。

目的：JEV 自报的 `conf`（写在 news.db 的 jev JSON 里）经 jev_posterior_poc 验证
**未校准**——conf 桶 0.55→1.01 的方向准确率全在 0.48–0.54 平直。本工具决定：
  (A) conf 是否携带任何判别信号（AUC）？若有，单调校准（Temperature/Platt/Isotonic）
      能把"概率"修准；若没有（AUC≈0.5），单调校准器保序、AUC 不变 → 救不回来。
  (B) 若救不回来，给出"弃用 conf、改用分位/事件类型分层"的替代方案与可靠性查表。

口径：复用 jev_posterior_poc 的标注逻辑
  · 信号日 = 新闻 ts；持有 = 其后首个交易日 T+1 收盘
  · 标的收益 = 关联 A 股 T+1 pct_chg 等权均值；超额 = 标的 − 沪深300
  · right(1) = bull→excess>0 / bear→excess<0 / neutral→|excess|≤band
  · conf = jev JSON 的 conf（裁剪到 (1e-3, 1-1e-3)）
训练/测试：**按 ts 时间切分（前 70% 训练，后 30% 测试）**，避免未来信息泄漏。

只读 news.db 与 parquet；输出报告 + 拟合参数（供 calibrate_conf() 复用）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(r"C:\项目\老田的AI助手")
sys.path.insert(0, str(ROOT / "tools"))
import jev_posterior_poc as P  # noqa: E402  (复用其数据加载逻辑)

from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.isotonic import IsotonicRegression  # noqa: E402
from sklearn.metrics import roc_auc_score, brier_score_loss  # noqa: E402
from sklearn.calibration import calibration_curve  # noqa: E402
from scipy.optimize import minimize  # noqa: E402

REPORT_DIR = ROOT / "quant_backtest" / "reports"
EPS = 1e-3


def build_samples(days: int) -> list[dict]:
    """返回 [(conf, right, ts), ...]，right=1 表示该条新闻 bias 判读正确。"""
    judged = P.load_judged(days)
    opens = P.load_open_days()
    rows = []
    for r in judged:
        if not r["codes"]:
            continue
        td = P.next_trade_day(r["ts"][:10].replace("-", ""), opens)
        if not td:
            continue
        r["trade_day"] = td
        rows.append(r)
    dates = {r["trade_day"] for r in rows}
    codes = {c for r in rows for c in r["codes"]}
    stock_px, bench_px = P.load_returns(dates, codes)
    out = []
    for r in rows:
        day = r["trade_day"]
        if day not in bench_px:
            continue
        vals = [stock_px[(c, day)] for c in r["codes"] if (c, day) in stock_px]
        if not vals:
            continue
        ret = sum(vals) / len(vals)
        excess = ret - bench_px[day]
        if r["bias"] == "bull":
            right = 1 if excess > 0 else 0
        elif r["bias"] == "bear":
            right = 1 if excess < 0 else 0
        else:  # neutral
            right = 1 if abs(excess) <= P.NEUTRAL_BAND else 0
        conf = float(r["conf"] or 0.0)
        conf = min(max(conf, EPS), 1 - EPS)
        out.append({"conf": conf, "right": right, "ts": r["ts"]})
    return out


def ece(y: np.ndarray, p: np.ndarray, n_bins: int = 10) -> float:
    frac_pos, mean_pred = calibration_curve(y, p, n_bins=n_bins, strategy="uniform")
    # 重新算每 bin 的样本权重，避免 calibration_curve 仅返回中心点的偏差
    bins = np.linspace(0, 1, n_bins + 1)
    idx = np.clip(np.digitize(p, bins) - 1, 0, n_bins - 1)
    e = 0.0
    for b in range(n_bins):
        m = idx == b
        if m.sum() == 0:
            continue
        e += m.sum() * abs(y[m].mean() - p[m].mean())
    return float(e / len(y))


def temp_scaled(conf: np.ndarray, T: float) -> np.ndarray:
    logit = np.log(conf / (1 - conf))
    return 1.0 / (1.0 + np.exp(-logit / T))


def fit_temperature(train_conf: np.ndarray, train_right: np.ndarray) -> float:
    def neg_bce(T):
        p = temp_scaled(train_conf, T[0])
        return -np.mean(train_right * np.log(p) + (1 - train_right) * np.log(1 - p))

    res = minimize(neg_bce, x0=np.array([1.0]), bounds=[(0.05, 20.0)])
    return float(res.x[0])


def quantile_table(conf: np.ndarray, right: np.ndarray, q: int = 10) -> list[dict]:
    """分位可靠性查表：把 conf 按分位桶，输出经验 P(right)。"""
    edges = np.quantile(conf, np.linspace(0, 1, q + 1))
    edges[0], edges[-1] = 0.0, 1.0
    out = []
    for i in range(q):
        lo, hi = edges[i], edges[i + 1]
        m = (conf >= lo) & (conf < hi) if i < q - 1 else (conf >= lo)
        if m.sum() == 0:
            continue
        out.append({"bin": "[%.3f,%.3f)" % (lo, hi), "n": int(m.sum()),
                    "emp_p_right": round(float(right[m].mean()), 3),
                    "conf_center": round(float(conf[m].mean()), 3)})
    return out


def evaluate(name: str, raw_conf: np.ndarray, p: np.ndarray, y: np.ndarray) -> dict:
    auc = float(roc_auc_score(y, p)) if len(set(y.tolist())) > 1 else None
    return {"method": name, "auc": round(auc, 4) if auc is not None else None,
            "ece": round(ece(y, p), 4), "brier": round(float(brier_score_loss(y, p)), 4)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="JEV conf 校准重训")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--train-frac", type=float, default=0.7)
    args = ap.parse_args(argv)
    t0 = time.time()

    print("[1/4] 构造 (conf, right) 样本…", flush=True)
    samples = build_samples(args.days)
    samples.sort(key=lambda x: x["ts"])  # 时间切分
    n = len(samples)
    k = int(n * args.train_frac)
    train, test = samples[:k], samples[k:]
    ytr = np.array([s["right"] for s in train]); ctr = np.array([s["conf"] for s in train])
    yte = np.array([s["right"] for s in test]); cte = np.array([s["conf"] for s in test])
    print("  总 %d；训练 %d（右率 %.3f）/ 测试 %d（右率 %.3f）" % (
        n, len(train), ytr.mean(), len(test), yte.mean()), flush=True)

    print("[2/4] 原始指标…", flush=True)
    raw = evaluate("raw_conf", cte, cte, yte)
    print("  原始 AUC=%.3f ECE=%.3f Brier=%.3f" % (raw["auc"], raw["ece"], raw["brier"]), flush=True)

    print("[3/4] 拟合校准器（训练集）…", flush=True)
    # Temperature
    T = fit_temperature(ctr, ytr)
    p_temp = temp_scaled(cte, T)
    # Platt（logit(conf) 作特征）
    z = np.log(ctr / (1 - ctr)).reshape(-1, 1)
    platt = LogisticRegression(C=1e6, solver="lbfgs").fit(z, ytr)
    p_platt = platt.predict_proba(np.log(cte / (1 - cte)).reshape(-1, 1))[:, 1]
    # Isotonic
    iso = IsotonicRegression(out_of_bounds="clip").fit(ctr, ytr)
    p_iso = iso.predict(cte)
    calib = {
        "temperature_T": round(T, 4),
        "platt_coef": [round(float(x), 4) for x in platt.coef_[0]],
        "platt_intercept": round(float(platt.intercept_[0]), 4),
        "isotonic": None,  # 单调性保序，运行时 predict；参数不落盘（可用 iso 对象在线）
    }
    res_temp = evaluate("temperature(T=%.3f)" % T, cte, p_temp, yte)
    res_platt = evaluate("platt", cte, p_platt, yte)
    res_iso = evaluate("isotonic", cte, p_iso, yte)
    for r in (res_temp, res_platt, res_iso):
        print("  %-18s AUC=%s ECE=%.3f Brier=%.3f" % (
            r["method"], ("%.3f" % r["auc"]) if r["auc"] else "-", r["ece"], r["brier"]), flush=True)

    print("[4/4] 分位可靠性查表…", flush=True)
    qt = quantile_table(cte, yte, q=10)
    spread = max(x["emp_p_right"] for x in qt) - min(x["emp_p_right"] for x in qt)
    for row in qt:
        print("  %s n=%-5d emp_p=%.3f" % (row["bin"], row["n"], row["emp_p_right"]), flush=True)

    # 决策
    auc_raw = raw["auc"] if raw["auc"] is not None else 0.5
    calib_methods = [res_temp, res_platt, res_iso]
    best = min(calib_methods, key=lambda r: r["ece"])
    best_auc = max(r["auc"] for r in calib_methods if r["auc"] is not None) or auc_raw
    if auc_raw >= 0.55:
        sig_class, has_signal, sig_word = "has_signal", True, "有判别信号"
    elif auc_raw >= 0.45:
        sig_class, has_signal, sig_word = "no_signal", False, "无判别信号(≈随机)"
    else:
        sig_class, has_signal, sig_word = "anti_signal", False, "反相关信号(高conf更可能错)"
    verdict = (
        "conf 携带可校准信号（AUC>=0.55），用 %s 修概率后可用" % best["method"]
        if has_signal else
        "conf %s（AUC=%.3f）：单调校准器保序，AUC 不变，救不回来；即便反转 conf（Platt）"
        "最佳单调判别 AUC 也仅 %.3f。→ 弃用 conf，改用事件类型分层 + 组合层 sign"
        "（bull 平均 +0.20%%/bear −0.41%%）" % (sig_word, auc_raw, best_auc)
    )

    report = {
        "date": time.strftime("%Y%m%d"), "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "scope": {"days": args.days, "train_frac": args.train_frac,
                  "n_total": n, "n_train": len(train), "n_test": len(test),
                  "right_rate_train": round(float(ytr.mean()), 3),
                  "right_rate_test": round(float(yte.mean()), 3)},
        "raw": raw,
        "calibrated": {"temperature": res_temp, "platt": res_platt, "isotonic": res_iso,
                       "best_by_ece": best["method"]},
        "calib_params": calib,
        "quantile_table": qt, "quantile_spread": round(float(spread), 3),
        "decision": {"raw_auc": round(auc_raw, 4), "signal_class": sig_class,
                     "has_signal": has_signal, "best_monotonic_auc": round(best_auc, 4),
                     "verdict": verdict},
        "recommendation": (
            "保留 calibrate_conf() 供下游替换；但当前 AUC<0.55，校准只修绝对概率、不增判别力。"
            "落地建议：停用 api_common_stock 的 min_conf=0.75 过滤与按 conf 排序，"
            "改为事件类型分层（credit/trade/risk/policy/equity/earnings 有边，price/supply 无）。"
        ),
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_json = REPORT_DIR / ("jev_calibrate_%s.json" % report["date"])
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # 拟合参数单独落盘（供 calibrate_conf 复用）
    (REPORT_DIR / "jev_calib_params.json").write_text(
        json.dumps({"temperature_T": T,
                    "platt_coef": platt.coef_[0].tolist(),
                    "platt_intercept": float(platt.intercept_[0]),
                    "quantile_table": qt, "date": report["date"]},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    L = [
        "# JEV conf 校准重训（%s）" % report["generated_at"], "",
        "> K-DATA-20261110-JEVCAL2 ｜ 只读 ｜ 时间切分 70/30 ｜ 复用 jev_posterior 标注", "",
        "## 覆盖", "",
        "- 样本 %d（训练 %d / 测试 %d）；测试右率 %.3f" % (
            n, len(train), len(test), yte.mean()), "",
        "## 原始 vs 校准", "",
        "| 方法 | AUC | ECE | Brier |", "|---|---|---|---|",
        "| raw_conf | %s | %.3f | %.3f |" % (
            ("%.3f" % raw["auc"]) if raw["auc"] else "-", raw["ece"], raw["brier"]),
        "| temperature (T=%.3f) | %s | %.3f | %.3f |" % (
            T, ("%.3f" % res_temp["auc"]) if res_temp["auc"] else "-", res_temp["ece"], res_temp["brier"]),
        "| platt | %s | %.3f | %.3f |" % (
            ("%.3f" % res_platt["auc"]) if res_platt["auc"] else "-", res_platt["ece"], res_platt["brier"]),
        "| isotonic | %s | %.3f | %.3f |" % (
            ("%.3f" % res_iso["auc"]) if res_iso["auc"] else "-", res_iso["ece"], res_iso["brier"]),
        "", "## 分位可靠性查表（测试集，10 分位）", "",
        "| conf 分位 | n | 经验 P(right) |", "|---|---|---|",
    ]
    for row in qt:
        L.append("| %s | %d | %.3f |" % (row["bin"], row["n"], row["emp_p_right"]))
    L += ["", "分位跨度（max−min 经验右率）= **%.3f**" % spread, "",
          "## 结论", "",
          "- 原始 AUC = **%.3f**（%s）" % (auc_raw, sig_word),
          "- %s" % verdict, "",
          "## 落地建议", "", report["recommendation"], "",
          "报告：%s ｜ 拟合参数：jev_calib_params.json ｜ 耗时 %.1fs" % (out_json, time.time() - t0), ""]
    (REPORT_DIR / ("jev_calibrate_%s.md" % report["date"])).write_text(
        "\n".join(L), encoding="utf-8")

    print("\n== 结论 ==", flush=True)
    print("  原始 AUC=%.3f → %s" % (auc_raw, sig_word), flush=True)
    print("  分位跨度=%.3f（平=无结构）" % spread, flush=True)
    print("  %s" % verdict, flush=True)
    print("  报告:", out_json, "| 耗时 %.1fs" % (time.time() - t0), flush=True)


def calibrate_conf(raw_conf: float, params_path: str | None = None) -> float:
    """下游替换入口：raw_conf -> 校准后概率（Temperature 缩放，保序）。

    用法：from tools.jev_calibrate import calibrate_conf; p = calibrate_conf(0.82)
    注意：当前 JEV 的 conf 经验 AUC≈0.5、无判别信号 —— 校准只修绝对概率，
    不增加判别力。请勿把校准后的值当作"可靠度"再去做过滤/排序。
    """
    import json as _json
    pp = params_path or str(REPORT_DIR / "jev_calib_params.json")
    try:
        T = _json.load(open(pp, encoding="utf-8"))["temperature_T"]
    except Exception:
        T = 1.0
    c = min(max(float(raw_conf), EPS), 1 - EPS)
    return float(1.0 / (1.0 + np.exp(-np.log(c / (1 - c)) / T)))


if __name__ == "__main__":
    main()
