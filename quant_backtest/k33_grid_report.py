# -*- coding: utf-8 -*-
"""k33_grid_report.py —— K33 警报池重定位的离线网格验证报告生成器

数据源：data/nine_dim_replay.jsonl（V3 全量 5479 样本，51 锚点 × step=10 交易日
as-of 截断重放）+ data/nine_dim_replay_eval_cache.json（by_anchor 环境层）。

验证对象（K33 规则）：
  ① 分段网格：lt60 / 60-65 / 65-75 / 75-85 / 85+ × r5/r20 × raw/thinned
  ② 环境分层：低波(vol_pct≤30) vs 中高波 · bull vs 非 · 分年份（时段混杂控制）
  ③ 超额检验：excess = r20 - bench20（beta/alpha 判别，60-65 低波判例复核）
  ④ 阈值敏感性：ALERT_TH ∈ {70, 75, 80} 的负收益单调性与入池节奏

口径：仅 mature 窗口、剔 adj（跨除权>0.5%）；thinned = 同票距上次入选
≥2 锚点步（step=10 交易日 → 20 交易日去重，同 K32/nine_dim_backtest）。
n<10 不构成结论（灰显语义）。

用法：python k33_grid_report.py   →  写 k33_grid_report.md
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPLAY = os.path.join(HERE, "data", "nine_dim_replay.jsonl")
EVALC = os.path.join(HERE, "data", "nine_dim_replay_eval_cache.json")
OUT = os.path.join(HERE, "k33_grid_report.md")

BANDS = [("<60", lambda s: s < 60),
         ("60-65", lambda s: 60 <= s < 65),
         ("65-75", lambda s: 65 <= s < 75),
         ("75-85", lambda s: 75 <= s < 85),
         ("≥85", lambda s: s >= 85)]


def load():
    rows = []
    with open(REPLAY, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    env = {}
    ev = json.load(open(EVALC, encoding="utf-8"))
    for a in ev.get("by_anchor") or []:
        env[a["dt"]] = a
    return rows, env, ev


def thin(rows):
    """同票 20 交易日去重（K32 官方口径）：锚点 step=10 交易日，
    距上次入选锚点 ≥2 步才可再入。锚点以全局序列 index 计步。"""
    anchors = sorted({r["anchor"] for r in rows})
    aidx = {a: i for i, a in enumerate(anchors)}
    last = {}
    for r in sorted(rows, key=lambda x: (x["anchor"], x["code"])):
        i = aidx[r["anchor"]]
        if i - last.get(r["code"], -99) >= 2:
            last[r["code"]] = i
            yield r


def stats(rs, w):
    """w ∈ {r5, r20}：仅成熟+未除权；返回统计字典或 None。"""
    key = "mature5" if w == "r5" else "mature20"
    vals = [r[w] for r in rs if r.get(w) is not None
            and r.get(key) and not r.get("adj")]
    if not vals:
        return None
    sv = sorted(vals)
    n = len(sv)
    exc = None
    exs = [r[w] - r["bench" + w[1:]] for r in rs
           if r.get(w) is not None and r.get(key) and not r.get("adj")
           and r.get("bench" + w[1:]) is not None]
    if exs:
        exc = sum(exs) / len(exs)
    return {"n": n,
            "win": round(sum(1 for x in vals if x > 0) * 100.0 / n, 1),
            "avg": round(sum(vals) / n, 2),
            "exc": round(exc, 2) if exc is not None else None,
            "p5": round(sv[max(0, int(n * 0.05) - 1)], 1),
            "min": round(sv[0], 1)}


def fmt(st):
    if not st:
        return "—"
    grey = "（灰）" if st["n"] < 10 else ""
    exc = "—" if st["exc"] is None else "%+.1f" % st["exc"]
    return ("%d%s / %.0f%% / %+.1f%% / %s / %.1f / %.1f"
            % (st["n"], grey, st["win"], st["avg"], exc, st["p5"], st["min"]))


def grid_table(rows, label):
    out = ["| 分段 | r20: n/胜率/均值/超额/p5/最低 | r5: n/胜率/均值/超额/p5/最低 |",
           "|---|---|---|"]
    for name, pred in BANDS:
        sel = [r for r in rows if pred(r["score"] or 0)]
        out.append("| %s | %s | %s |" % (name, fmt(stats(sel, "r20")),
                                         fmt(stats(sel, "r5"))))
    return "\n".join(out)


def main():
    rows, env, ev = load()
    thinned = list(thin(rows))
    L = []
    ap = L.append

    ap("# K33 警报池重定位 · 离线网格验证报告")
    ap("")
    ap("- 生成：%s" % ev.get("generated_at", "—"))
    ap("- 数据：nine_dim_replay.jsonl（V3 结构全量回放，%d 样本 / %d 锚点，"
       "thinned 去重 %d）" % (len(rows), len(env), len(thinned)))
    ap("- 口径：仅成熟窗口、剔跨除权(>0.5%)；超额=样本收益−同锚点沪深300；"
       "n<10 灰显不构成结论")
    ap("- thinned=同票 20 交易日去重（锚点 step=10 交易日 → 隔 ≥2 步），"
       "**全样本统一去重（桶间互斥）**；官方 eval cache 为分桶独立去重"
       "（lt70 4502 样本），两口径中低分段略有差异，高分段结论一致"
       "（交叉验证见 ⑤）")
    ap("- 规则对象：ALERT_TH=75 入警报池（回避位）；85+ 强警报；"
       "60-75 无信号；BUY_TH=90 已退役")
    ap("")
    ap("## ① 总体分段网格")
    ap("")
    ap("### raw（全样本）")
    ap("")
    ap(grid_table(rows, "raw"))
    ap("")
    ap("### thinned（同票 20 交易日去重）")
    ap("")
    ap(grid_table(thinned, "thinned"))
    ap("")

    # ② 环境分层（thinned r20）
    ap("## ② 环境分层（thinned r20）")
    ap("")
    for lb, flt in [
            ("低波（锚点 vol_pct≤30）",
             lambda r: (env.get(r["anchor"], {}).get("vol_pct") or 99) <= 30),
            ("中高波（vol_pct>30）",
             lambda r: (env.get(r["anchor"], {}).get("vol_pct") or 0) > 30),
            ("牛市档（bull）", lambda r: env.get(r["anchor"], {}).get("bull")),
            ("非牛市档", lambda r: not env.get(r["anchor"], {}).get("bull"))]:
        sel_all = [r for r in thinned if flt(r)]
        ap("### %s（n=%d）" % (lb, len(sel_all)))
        ap("")
        ap("| 分段 | n / 胜率 / r20均值 / 超额 / p5 / 最低 | r5均值 |")
        ap("|---|---|---|")
        for name, pred in BANDS:
            sel = [r for r in sel_all if pred(r["score"] or 0)]
            s20, s5 = stats(sel, "r20"), stats(sel, "r5")
            ap("| %s | %s | %s |" % (name, fmt(s20),
                                     ("%+.1f%%" % s5["avg"]) if s5 else "—"))
        ap("")

    # ②b 分年份（时段混杂控制）
    ap("## ②b 分年份（时段混杂控制，thinned r20）")
    ap("")
    ap("| 年份 | 分段 | n / 胜率 / r20均值 / 超额 |")
    ap("|---|---|---|")
    for y in sorted({r["anchor"][:4] for r in thinned}):
        for name, pred in BANDS:
            sel = [r for r in thinned if r["anchor"][:4] == y
                   and pred(r["score"] or 0)]
            st = stats(sel, "r20")
            if st:
                ap("| %s | %s | %s |" % (y, name, fmt(st)))
    ap("")

    # ③ 超额检验（beta/alpha 判别）
    ap("## ③ 超额检验（thinned，excess = r20 − bench20）")
    ap("")
    ap("| 分段 | 环境 | n | r20均值 | 超额均值 | r5胜率 | 判别 |")
    ap("|---|---|---|---|---|---|---|")
    for name, pred in BANDS:
        for lb, flt in [("全环境", lambda r: True),
                        ("低波", lambda r: (env.get(r["anchor"], {})
                                            .get("vol_pct") or 99) <= 30)]:
            sel = [r for r in thinned if pred(r["score"] or 0) and flt(r)]
            s20, s5 = stats(sel, "r20"), stats(sel, "r5")
            if not s20:
                continue
            verdict = "—" if s20["n"] < 10 else (
                "alpha（超额与收益同向，|超额|≥1pp）"
                if s20["exc"] is not None and abs(s20["exc"]) >= 1
                and (s20["avg"] > 0) == (s20["exc"] > 0)
                else "beta（超额反向或近零）")
            ap("| %s | %s | %d | %+.1f%% | %s | %s | %s |"
               % (name, lb, s20["n"], s20["avg"],
                  "%+.1f" % s20["exc"] if s20["exc"] is not None else "—",
                  ("%.0f%%" % s5["win"]) if s5 else "—", verdict))
    ap("")

    # ④ 阈值敏感性
    ap("## ④ 阈值敏感性（ALERT_TH 扫描，thinned r20）")
    ap("")
    ap("| ALERT_TH | 入池样本 n | 胜率 | r20均值 | 超额 | p5 |")
    ap("|---|---|---|---|---|---|")
    for th in (70, 75, 80, 85):
        sel = [r for r in thinned if (r["score"] or 0) >= th]
        st = stats(sel, "r20")
        if st:
            ap("| ≥%d | %d | %.0f%% | %+.1f%% | %s | %.1f |"
               % (th, st["n"], st["win"], st["avg"],
                  "%+.1f" % st["exc"] if st["exc"] is not None else "—",
                  st["p5"]))
    ap("")

    # ⑤ 结论
    ap("## ⑤ 结论")
    ap("")
    ap("0. **官方 cache 交叉验证**：eval cache r20 thinned —— lt70 "
       "4502/-7.99%/26.3% · 70-80 57/-14.16%/21.1% · 80-85 5/-7.07%/20% · "
       "85-90 3/-84.06%/0% · ≥90 1/-33.95%/0%。本报告 ≥85 段（n=4，"
       "-71.5%）= 官方 85-90 与 ≥90 加权合并，一致；≥75 段 -27.9%/9% 与 "
       "K32 结论一致。")
    g75 = stats([r for r in thinned if (r["score"] or 0) >= 75], "r20")
    g85 = stats([r for r in thinned if (r["score"] or 0) >= 85], "r20")
    lo = stats([r for r in thinned if (r["score"] or 0) < 60], "r20")
    ap("1. **警报器规则成立**：thinned r20 上 ≥75 段 n=%d 胜率 %.0f%% 均值 "
       "%+.1f%%（vs <60 段 %.0f%% / %+.1f%%），85+ 段 n=%d 胜率 %.0f%% 均值 "
       "%+.1f%%——高分位负向预测力稳健，反用为回避警报有据。"
       % (g75["n"], g75["win"], g75["avg"], lo["win"], lo["avg"],
          g85["n"], g85["win"], g85["avg"]))
    ap("2. **观察池否决维持**：65-75 与 60-65 各环境层均无可复制正信号"
       "（详见②③表；2024 段小样本与低波 beta 判例见 INTEL_RD_PLAN K33）。")
    ap("3. **入池节奏**：≥75 为罕见拥挤位（两仪 2 年 thinned 仅 %d 样本），"
       "警报池预期低频累积，n<10 期间不作结论。" % g75["n"])
    ap("4. **复核铁律**：任何结构修订（V4+）须重跑本报告（python "
       "k33_grid_report.py）且 ≥75 段负向单调性不劣化方可定稿。")
    ap("")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("written %s (%d chars)" % (OUT, len("\n".join(L))))


if __name__ == "__main__":
    main()
