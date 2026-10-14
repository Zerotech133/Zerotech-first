#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jev_graduation_status — jev 毕业判据 v2 正式口径全域状态输出（W5c，2026-10-14，只读）。

背景：v2 于 2026-10-14 经老田批准正式采纳（建议方案 C 目标态 + A 过渡态），
判据原文见 docs/changes/20261014_jev_oos_grad_changes.md 第四节、
采纳留痕见 docs/changes/20261014_jev_v2_adoption_changes.md。

v2 判据（正式口径）：
  每市场域独立判定，两条同时满足该域达标：
    - n_eff ≥ 30：唯一 ts_code 计数（有效标的数，非名义条数）；
    - FOCUS 组 T+5 前向均值（标的聚类口径：先按标的求均值再等权）> +1pp；
    - 辅助指标（无硬门槛）：同域 FOCUS−PASS > 0。
  域级特殊规则：
    - CN：不设 FOCUS 判据，状态单列"入口缺位"（quality 从未判 low）；
    - CRYPTO：过渡期剔除出毕业判定（A），单独监控；quality 模型对 crypto
      有区分度后自动并入（C）。

数据源：与 jev_graduation_bydomain.py 完全一致（直接复用其 load_all /
domain_stats：triples.jsonl + replay 合并 2070 条，T+5 前向，
回填窗口 2026-08-13 ~ 10-08）。本脚本零写入产线数据、不碰 DB、不调 API。

输出：
  - review_data/graduation_status_v2.json（消费方：shadow_weekly_report.py jev 线）
  - stdout：默认人读摘要；--json 打印状态 JSON；--md 打印可直接嵌周报五线表的行

用法：
  "C:/Program Files/Python311/python.exe" jev_graduation_status.py [--json] [--md]
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from jev_graduation_bydomain import load_all, domain_stats, mean  # noqa: E402

OUT_JSON = HERE / "review_data" / "graduation_status_v2.json"

V2_N_MIN = 30     # n_eff 门槛（唯一 ts_code 计数）
V2_PP = 0.01      # 聚类均值门槛：> +1pp
# 域级特殊规则（正式口径）
DOMAIN_NO_FOCUS = {"CN": "FOCUS 入口缺位（quality 从未判 low），不设 FOCUS 判据；修 quality 在 CN 的触发是前置课题"}
DOMAIN_EXCLUDED = {"CRYPTO": "过渡期剔除出毕业判定（处置A），单独监控；quality 模型对 crypto 有区分度后自动并入（处置C）"}
# live 域 = 参与毕业判定的常规域（v2 下当前为 US / HK 及未来新增域）

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def judge_domain(n_eff, cluster_mean, gap, domain):
    """v2 判定核心（纯函数，可单测；输入均值/差为小数，如 0.01 = +1pp）。

    返回 {"pass": bool, "state": str, "note": str}。
    """
    if domain in DOMAIN_NO_FOCUS:
        return {"pass": False, "state": "入口缺位", "note": DOMAIN_NO_FOCUS[domain]}
    if domain in DOMAIN_EXCLUDED:
        return {"pass": False, "state": "过渡期剔除", "note": DOMAIN_EXCLUDED[domain]}
    # 常规 live 域：n_eff ≥ 30 且 聚类均值 > +1pp 两条同时满足
    ok_n = n_eff is not None and n_eff >= V2_N_MIN
    ok_mean = cluster_mean is not None and cluster_mean > V2_PP
    passed = ok_n and ok_mean
    if passed:
        state = "达标"
    elif not ok_n and not ok_mean:
        state = "n与均值均未达标"
    elif not ok_n:
        state = "样本不足"
    else:
        state = "均值未过线"
    aux = "辅助FOCUS−PASS>0 ✓" if (gap is not None and gap > 0) else "辅助FOCUS−PASS>0 ✗"
    return {"pass": passed, "state": state,
            "note": "n_eff≥%d %s；聚类均值>+%.0fpp %s；%s（辅助，无硬门槛）" % (
                V2_N_MIN, "✓" if ok_n else "✗", V2_PP * 100,
                "✓" if ok_mean else "✗", aux)}


def overall_light(domains: dict) -> str:
    """周报总灯色（简化规则）：任一 live 域达标→green；无达标但有 FOCUS−PASS>0 的域→yellow；全负→red。"""
    if any(v.get("pass") for v in domains.values()):
        return "green"
    if any((v.get("focus_pass_gap_pp") is not None) and v["focus_pass_gap_pp"] > 0
           for v in domains.values()):
        return "yellow"
    return "red"


def compute() -> dict:
    """读数据源，产出 v2 全域状态（只读）。"""
    recs = load_all()
    fo = [r for r in recs if r["decision"] == "FOCUS"]
    found = {r["market"] for r in fo}
    domain_list = sorted(found | set(DOMAIN_NO_FOCUS) | set(DOMAIN_EXCLUDED))

    domains = {}
    for m in domain_list:
        st = domain_stats(fo, recs, m)
        n_eff = st["n_unique_codes"]
        cluster = st["ret5_cluster"]
        gap = st["focus_minus_pass"]
        j = judge_domain(n_eff, cluster, gap, m)
        domains[m] = {
            "n_eff": n_eff,
            "cluster_mean_pp": round(cluster * 100, 2) if cluster is not None else None,
            "focus_pass_gap_pp": round(gap * 100, 2) if gap is not None else None,
            "pass": j["pass"],
            "state": j["state"],
            "note": j["note"],
            "n_nominal": st["n_nominal"],
            "cluster_mean_raw": cluster,
        }

    dates = sorted({r["pool_date"] for r in recs if r.get("pool_date")})
    return {
        "adopted": "v2",
        "computed_at": datetime.now().isoformat(timespec="seconds"),
        "criterion": {
            "per_domain": True,
            "n_eff_min": V2_N_MIN, "n_eff_by": "唯一 ts_code 计数",
            "cluster_mean_pp_min": V2_PP, "mean_basis": "标的聚类（先按标的求均值再等权）",
            "aux": "同域 FOCUS−PASS > 0（无硬门槛）",
            "special": {"CN": "入口缺位，不设 FOCUS 判据",
                        "CRYPTO": "过渡期剔除（A），单独监控，quality 修复后自动并入（C）"},
            "source": "docs/changes/20261014_jev_v2_adoption_changes.md",
        },
        "sample": {"total": len(recs), "focus_n_nominal": len(fo),
                   "window": "%s ~ %s（T+5 回填截止）" % (dates[0], dates[-1]) if dates else None},
        "domains": domains,
        "light": overall_light(domains),
    }


def md_line(status: dict) -> str:
    """输出可直接嵌周报五线表的一行 + 域级明细表。"""
    doms = status["domains"]
    mark = {"green": "🟢 绿灯", "yellow": "🟡 黄灯", "red": "🔴 红灯"}.get(status["light"], status["light"])
    parts = []
    for m in ("US", "HK", "CRYPTO", "CN"):
        d = doms.get(m)
        if d is None:
            continue
        if d["state"] == "入口缺位":
            parts.append("%s 入口缺位" % m)
        elif d["state"] == "过渡期剔除":
            parts.append("%s 过渡期监控 %spp" % (m, "%+.2f" % d["cluster_mean_pp"] if d["cluster_mean_pp"] is not None else "无"))
        else:
            parts.append("%s n_eff%d%s %spp%s" % (
                m, d["n_eff"], "✓" if d["pass"] or (d["n_eff"] or 0) >= V2_N_MIN else "✗",
                "%+.2f" % d["cluster_mean_pp"] if d["cluster_mean_pp"] is not None else "无",
                "✓" if d["pass"] else "✗"))
    gap_notes = [f"{m} FOCUS−PASS {d['focus_pass_gap_pp']:+.2f}pp" for m, d in doms.items()
                 if d.get("focus_pass_gap_pp") is not None and d["focus_pass_gap_pp"] > 0
                 and d["state"] not in ("入口缺位", "过渡期剔除")]
    note = "；".join(gap_notes) if gap_notes and status["light"] == "yellow" else "v2 口径（2026-10-14 采纳）"
    row = "| jev 毕业冲刺 | v2 全域状态 | 样本 %s | %s | %s |" % (
        status["sample"]["window"] or "-", mark, note)
    detail = ["", "**jev 毕业判据 v2 各域状态**（每域独立：n_eff≥30 且 聚类均值>+1pp；辅助 FOCUS−PASS>0）", "",
              "| 域 | n_eff | 聚类均值 | FOCUS−PASS | 判定 |", "|---|---|---|---|---|"]
    for m, d in doms.items():
        detail.append("| %s | %s | %s | %s | %s |" % (
            m, d["n_eff"] if d["n_eff"] is not None else "-",
            "%+.2fpp" % d["cluster_mean_pp"] if d["cluster_mean_pp"] is not None else "-",
            "%+.2fpp" % d["focus_pass_gap_pp"] if d["focus_pass_gap_pp"] is not None else "-",
            ("✅ 达标" if d["pass"] else d["state"])))
    detail.append("")
    detail.append("总灯色：%s（任一 live 域达标→绿；无达标但有 FOCUS−PASS>0 的域→黄；全负→红）" % mark)
    return row + "\n" + "\n".join(detail)


def main() -> int:
    status = compute()
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

    if "--json" in sys.argv:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    elif "--md" in sys.argv:
        print(md_line(status))
    else:
        print("jev 毕业判据 v2 全域状态（%s，窗口 %s）" % (
            status["computed_at"], status["sample"]["window"]))
        for m, d in status["domains"].items():
            print("  %-7s n_eff=%-4s 聚类均值=%spp FOCUS−PASS=%spp → %s" % (
                m, d["n_eff"],
                "%+.2f" % d["cluster_mean_pp"] if d["cluster_mean_pp"] is not None else "  - ",
                "%+.2f" % d["focus_pass_gap_pp"] if d["focus_pass_gap_pp"] is not None else "  - ",
                ("✅ 达标" if d["pass"] else d["state"])))
        print("  总灯色：%s" % status["light"])
        print("已写 %s" % OUT_JSON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
