# -*- coding: utf-8 -*-
"""影子清单 p 值的行序无关性回归（只读）。

为什么需要这个脚本
------------------
`combo_shadow.null_test()` 用 `rng.choice(len(all_days), n, replace=False)` 抽样，
是**按索引**抽的，所以 `all_days` 的元素顺序决定抽到哪些日期。原先 `all_days`
由 `defaultdict` 的插入顺序产生，即跟随 `rows` 遍历顺序 —— 同一批数据只要行序
变一下，p 值就跟着漂。

实测（2026-11-08，1,265 笔成熟记录 / 75 个 pass 日 / universe 99）：
    · 8 次打乱行序 → p ∈ [0.015, 0.065]，极差 0.050，其中 1 次 0.065 不过 5% 闸门
    · 按 entry_date 排序后 → 4 次打乱全为 0.035，完全确定
当时的具体表现是：修复前清单（1,676 行）p=0.025、修复后（1,716 行）p=0.035，
日批均、进度、pass 日数三者完全相同 —— 差异纯来自行序，不是数据变化。

关键设计：本脚本**直接调用 combo_shadow.null_test**，不复制算法。
只读已落盘的 jsonl 不行：那份文件本身按 entry_date 排序写出，即使生产代码
退回不排序，读回来照样能过。必须拿生产函数喂打乱的行序才测得出来。

断言
----
A1 生产函数在打乱行序下 p 完全一致（极差 < 1e-9）—— 排序生效。
A2 生产函数的 p 与 portal/combo_shadow_stats.json 记录一致（统计文件未过期）。
A3 universe / pass 日数与统计文件一致。
A4 hold 口径同样通过 A1。
A5 抽样次数 ≥ MIN_BOOT 且蒙特卡洛标准误 ≤ MAX_P_SE。

A5 管的是第二条更隐蔽的坑
--------------------------
修掉行序依赖后，p 看起来稳定在 0.035，很容易以为已经"确定"了。其实那只是
**抽 200 次的噪声**。固定排序、只换随机种子实测：

    boot=200    p 均值 0.0442  标准差 0.0127  范围[0.025,0.075]  >0.05 的比例 24%
    boot=1000   p 均值 0.0438  标准差 0.0067                 >0.05 的比例  8%
    boot=5000   p 均值 0.0419  标准差 0.0027                 >0.05 的比例  0%
    boot=20000  p 均值 0.0423  标准差 0.0010                 >0.05 的比例  0%
    20 万次      p≈0.0425（近真值）

即真实 p≈0.042 而非 0.035，且 200 次抽样下每 4 次重跑就有 1 次不过闸。
一个会随抽样次数跨过判据阈值的 p，不是数据的性质，是抽样预算的性质。

任一条不满足即 exit 1。只读，不写任何数据。

用法:
    python tools/research/p_value_order_check.py
    python tools/research/p_value_order_check.py --shuffles 16
"""
import argparse
import importlib.util
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHADOW = os.path.join(ROOT, "quant_backtest", "data", "combo_shadow.jsonl")
STATS = os.path.join(ROOT, "portal", "combo_shadow_stats.json")
CS_PATH = os.path.join(ROOT, "tools", "research", "combo_shadow.py")

# 抽样次数下限与蒙特卡洛标准误上限。boot=200 时 p 的 1σ 约 0.0127，
# 实测 50 个种子里 24% 会跨过 0.05 —— 那种 p 只能说"抽了多少次"，
# 不能说"数据支持什么"。5000 次把 1σ 压到 ~0.0028。
MIN_BOOT = 5000
MAX_P_SE = 0.005


def load_combo_shadow(path=None):
    """按路径加载 combo_shadow，避免依赖 sys.path 与模块名冲突。"""
    path = path or CS_PATH
    spec = importlib.util.spec_from_file_location("combo_shadow_regress", path)
    m = importlib.util.module_from_spec(spec)
    sys.modules["combo_shadow_regress"] = m
    spec.loader.exec_module(m)
    return m


def load_rows(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                if r.get("mature"):
                    rows.append(r)
    return rows


def main():
    ap = argparse.ArgumentParser(description="影子 p 值行序无关性回归")
    ap.add_argument("--shuffles", type=int, default=8)
    ap.add_argument("--cs-path", default=None,
                    help="被测的 combo_shadow.py（默认仓库内那份；"
                         "传一份退回不排序的副本可自检本脚本能否报错）")
    a = ap.parse_args()

    fails = []
    for p in (SHADOW, STATS, a.cs_path or CS_PATH):
        if not os.path.exists(p):
            print("[FATAL] 找不到 %s" % p)
            return 1

    m = load_combo_shadow(a.cs_path)
    if not callable(getattr(m, "null_test", None)):
        print("[FATAL] combo_shadow 没有模块级 null_test —— 是否被改回 main() 内嵌？")
        return 1

    rows = load_rows(SHADOW)
    if len(rows) < 200:
        print("[FATAL] 成熟记录仅 %d 条，样本不足，无法检验" % len(rows))
        return 1
    st = json.load(open(STATS, encoding="utf-8"))

    def show(tag, d):
        if d is None:
            print("%-18s N/A" % tag)
            return
        print("%-18s n=%-3d universe=%-3d real=%+.4f p50=%+.4f p95=%+.4f p=%.4f"
              % (tag, d["days"], d["universe"], d["real"] / 100.0,
                 d["null_p50"] / 100.0, d["null_p95"] / 100.0, d["p_value"]))

    print("清单成熟记录 %d 笔 / pass 笔 %d / 开仓日 %d"
          % (len(rows), sum(1 for r in rows if r["pass"]),
             len({r["entry_date"] for r in rows})))
    print("-" * 92)

    for rule in ("trail", "hold"):
        ref = (st.get("null") or {}).get(rule) or {}
        base = m.null_test(rows, rule)
        if base is None:
            fails.append("%s: null_test 返回 None（pass 日数与 universe 冲突）" % rule)
            continue
        show("重算(%s)" % rule, base)
        show("统计文件(%s)" % rule, ref)

        # A1 / A4 —— 打乱行序 p 必须不变
        rs = np.random.default_rng(7)
        ps = []
        for _ in range(a.shuffles):
            idx = rs.permutation(len(rows))
            d = m.null_test([rows[j] for j in idx], rule)
            if d is not None:
                ps.append(d["p_value"])
        if len(ps) != a.shuffles:
            fails.append("%s: 打乱后有 %d 次返回 None（应为 %d 次全部有值）"
                         % (rule, a.shuffles - len(ps), a.shuffles))
        elif max(ps) - min(ps) > 1e-9:
            fails.append("%s: 打乱 %d 次行序 p 漂移 %.4f（∈[%.4f,%.4f]）—— 排序未生效"
                         % (rule, a.shuffles, max(ps) - min(ps), min(ps), max(ps)))
        else:
            print("[OK]   %s: 打乱 %d 次行序 p 恒为 %.4f（与行序无关）"
                  % (rule, a.shuffles, ps[0]))

        # A2 / A3 —— 与统计文件对齐
        if ref.get("p_value") is None:
            fails.append("%s: 统计文件缺 p_value" % rule)
        elif abs(base["p_value"] - float(ref["p_value"])) > 1e-4 + 1e-9:
            fails.append("%s: p 不一致 生产 %.4f vs 统计 %.4f（统计文件过期？）"
                         % (rule, base["p_value"], float(ref["p_value"])))
        if ref.get("universe") is not None and int(ref["universe"]) != base["universe"]:
            fails.append("%s: universe 不一致 生产 %d vs 统计 %d"
                         % (rule, base["universe"], int(ref["universe"])))
        elif ref.get("days") is not None and int(ref["days"]) != base["days"]:
            fails.append("%s: pass 日数不一致 生产 %d vs 统计 %d"
                         % (rule, base["days"], int(ref["days"])))

        # A5 —— 抽样次数与蒙特卡洛误差。这是第二条坑的防线：
        # boot=200 时 p 的标准差 0.0127，24% 的重跑会跨过 0.05 门槛，
        # 报告里的 p 就变成"抽了多少次的函数"而不是数据的性质。
        boot_used = base.get("boot")
        if not boot_used:
            fails.append("%s: 返回里没有 boot 字段，无法核对抽样次数" % rule)
        elif int(boot_used) < MIN_BOOT:
            fails.append("%s: 抽样仅 %d 次（要求 ≥%d）；p 的蒙特卡洛标准差约 %.4f，"
                         "足以跨越 0.05 门槛" % (rule, int(boot_used), MIN_BOOT,
                                                 (base["p_value"] * (1 - base["p_value"])
                                                  / int(boot_used)) ** 0.5))
        elif base.get("p_se") is None or float(base["p_se"]) > MAX_P_SE:
            fails.append("%s: 蒙特卡洛标准误 %s 超过 %.4f，p 值精度不足以判 0.05"
                         % (rule, base.get("p_se"), MAX_P_SE))
        else:
            print("[OK]   %s: 抽样 %d 次，p_se=%.4f（≤%.4f），p=%.4f±%.4f"
                  % (rule, int(boot_used), float(base["p_se"]), MAX_P_SE,
                     base["p_value"], float(base["p_se"])))

        if not any(x.startswith(rule) for x in fails):
            print("[OK]   %s: p / universe / pass 日数与统计文件一致" % rule)
        print("-" * 92)

    if fails:
        for msg in fails:
            print("[FAIL]", msg)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
