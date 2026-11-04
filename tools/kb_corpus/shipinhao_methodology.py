# -*- coding: utf-8 -*-
"""视频号 8 博主：方法论卡片 + 主题摘要/对比报告（基于清洗文本/知识单元/候选卡）。"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(r"C:\项目\老田的AI助手")
KB = Path(r"C:\项目\知识库")
PID = "视频号学习_8博主"
DATE = datetime.now().strftime("%Y%m%d")
CLEAN = KB / "20_清洗" / PID / "clean"
UNITS = KB / "30_知识单元" / f"{PID}_units_draft.jsonl"
PROP = REPO / "research/reports" / f"card_proposals_{PID}_{DATE}.json"

CATS = {
    "缠论/结构": ["缠论", "中枢", "背驰", "笔", "线段", "买卖点", "级别", "结构"],
    "技术指标/公式": ["指标", "公式", "均线", "MACD", "KDJ", "RSI", "布林", "金叉", "死叉", "背离", "走势"],
    "量化/AI": ["量化", "AI", "模型", "算法", "因子", "回测", "数据", "机器学习", "神经网络", "Python", "代码", "编程", "策略"],
    "趋势/形态": ["趋势", "突破", "支撑", "阻力", "形态", "平台", "缺口", "波浪", "箱体", "新高"],
    "仓位/风控": ["仓位", "止损", "止盈", "风控", "回撤", "杠杆", "资金管理", "复利", "风险"],
    "交易心理/纪律": ["心态", "纪律", "情绪", "贪婪", "恐惧", "执行", "复盘", "耐心", "认知"],
    "基本面/消息": ["业绩", "估值", "财报", "政策", "消息", "题材", "板块", "资金流"],
}
STYLE = {
    "缠论/结构": "缠论结构派", "技术指标/公式": "指标工具派", "量化/AI": "量化开发派",
    "趋势/形态": "趋势形态派", "仓位/风控": "风控执行派", "交易心理/纪律": "交易心理派",
    "基本面/消息": "基本面/消息派",
}


def blogger(name: str) -> str:
    n = name.replace("20261103_", "")
    for suffix in ("(1).md", ".md"):
        n = n.replace(suffix, "")
    return n


def load_units() -> list[dict]:
    out = []
    for line in UNITS.open(encoding="utf-8"):
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def load_props() -> dict:
    if PROP.exists():
        return json.loads(PROP.read_text(encoding="utf-8"))
    return {"cards": []}


def cat_counts(text: str) -> dict:
    return {c: sum(text.count(k) for k in kws) for c, kws in CATS.items()}


def main() -> int:
    units = load_units()
    props = load_props()
    cards = props.get("cards") or []
    files = sorted(CLEAN.glob("*.md"))
    data = []
    for f in files:
        b = blogger(f.name)
        text = f.read_text(encoding="utf-8", errors="replace")
        fus = [u for u in units if (u.get("evidence") or {}).get("file") == f.name]
        fpass = [c for c in cards if c.get("blogger") == b and c["review"]["checkboxes"]["pass"]]
        counts = cat_counts(text)
        cand_claims = [u["claim"] for u in fus if 20 <= len(u.get("claim") or "") <= 120 and any(
            k in u["claim"] for kws in CATS.values() for k in kws)]
        seen, claims = set(), []
        ranked = sorted(cand_claims, key=lambda c: -(sum(c.count(k) for kws in CATS.values() for k in kws) + len(c) / 40))
        for c in ranked:
            key = re.sub(r"\W", "", c)[:40]
            if key in seen:
                continue
            seen.add(key)
            claims.append(c)
            if len(claims) >= 8:
                break
        top_cat = sorted(counts.items(), key=lambda x: -x[1])[:2]
        style = " / ".join(STYLE[c] for c, v in top_cat if v > 0) or "未分类"
        data.append({
            "blogger": b, "file": f.name, "chars": len(text), "lines": text.count("\n") + 1,
            "units": len(fus), "candidates_pass": len(fpass),
            "categories": counts, "top_categories": top_cat,
            "style": style, "core_claims": claims,
            "top_cards": [{"concept": c["concept"], "a": c["a"][:120], "score": c["quality_score"]}
                          for c in sorted(fpass, key=lambda x: -x["quality_score"])[:5]],
        })

    out = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "project_id": PID, "files": len(data), "bloggers": data}
    (REPO / "research/reports" / f"shipinhao_methodology_cards_{DATE}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # ===== 方法论卡片 MD =====
    L = ["# 视频号 8 博主 · 方法论卡片（v1）", "",
         f"> 生成：{out['generated_at']}｜来源：8 份视频转写笔记（2.05MB / 3,813 知识单元）｜**学习资料，不构成投资建议**", "",
         "## 〇、总览", "", "| 博主 | 字数 | 单元 | 候选通过 | 风格判断 |", "|---|---|---|---|---|"]
    for d in data:
        L.append(f"| {d['blogger']} | {d['chars']:,} | {d['units']} | {d['candidates_pass']} | {d['style']} |")
    L += ["", "## 一、逐博主方法论卡片", ""]
    for d in data:
        L += [f"### {d['blogger']}（{d['style']}）", "",
              f"- 资料：{d['file']}｜{d['chars']:,} 字｜{d['lines']} 行｜单元 {d['units']}｜候选通过 {d['candidates_pass']}",
              f"- 主题画像：{ '、'.join(f'{c}×{v}' for c, v in d['top_categories']) }", "",
              "**核心观点（原文摘录）**："]
        for c in d["core_claims"][:6]:
            L.append(f"- {c}")
        if d["top_cards"]:
            L += ["", "**候选学习卡（预审通过，Top5）**："]
            for c in d["top_cards"]:
                L.append(f"- {c['concept']}：{c['a']}")
        L += ["", "---", ""]

    (REPO / "research/reports" / f"shipinhao_methodology_cards_{DATE}.md").write_text("\n".join(L), encoding="utf-8")

    # ===== 主题摘要/对比 MD =====
    C = ["# 视频号 8 份笔记 · 主题摘要与对比报告（v1）", "",
         f"> 生成：{out['generated_at']}｜数据：清洗文本 8 份 / 知识单元 3,813 条 / 候选卡 291 张（通过 81）", "",
         "## 一、规模与内容结构", "",
         "| 博主 | 字数 | 知识单元 | 通过候选 | 主导主题 |", "|---|---|---|---|---|"]
    for d in data:
        C.append(f"| {d['blogger']} | {d['chars']:,} | {d['units']} | {d['candidates_pass']} | {d['top_categories'][0][0]} |")
    C += ["", "## 二、主题矩阵（各博主 × 主题关键词密度）", "",
          "| 博主 | " + " | ".join(CATS) + " |",
          "|---" * (len(CATS) + 1) + "|"]
    for d in data:
        C.append("| " + d["blogger"] + " | " + " | ".join(str(d["categories"].get(c, 0)) for c in CATS) + " |")

    # 共性/差异
    shared = []
    for i, c in enumerate(CATS):
        vals = [d["categories"].get(c, 0) for d in data]
        if all(v > 0 for v in vals):
            shared.append(c)
    unique = {}
    for d in data:
        top = d["top_categories"][0][0]
        unique.setdefault(top, []).append(d["blogger"])
    C += ["", "## 三、共性主题", "", "所有 8 份笔记都涉及：**" + "、".join(shared) + "**。" if shared else "（无全量共性主题）", "",
          "## 四、各自专长（主导主题）", "", "| 主题 | 博主 |", "|---|---|"]
    for k, v in sorted(unique.items(), key=lambda x: -len(x[1])):
        C.append(f"| {k} | {'、'.join(v)} |")
    C += ["", "## 五、互补关系与使用建议", "",
          "1. **工具/指标派**（指标公式入门、AI量化工具手记）：适合作为「指标编写/工具落地」参考；",
          "2. **缠论结构派**（乔木缠论ROEE、一修论市）：适合作为「结构/级别/买卖点」框架参考；",
          "3. **量化开发派**（数据帝-量化开发、K哥真交易）：适合作为「数据/模型/回测工程」参考；",
          "4. **实战/心态派**（爱死磕-康康、cash猫4415）：适合作为「执行/纪律/复盘」参考；",
          "5. 8 份资料主题互补 > 重叠：可作为「从指标工具→结构判断→量化验证→纪律执行」的学习路径素材。", "",
          "## 六、边界", "",
          "- 全部为视频转写/二次整理，可能存在同音字错、断句与删减；",
          "- 内容是个人交易方法论分享，**未经四关验证，不构成投资建议**；",
          "- 候选卡通过仅为机器预审，需人工抽审后才可发布进学习卡库。"]
    (REPO / "research/reports" / f"shipinhao_theme_compare_{DATE}.md").write_text("\n".join(C), encoding="utf-8")
    (REPO / "research/reports" / f"shipinhao_theme_compare_{DATE}.json").write_text(
        json.dumps({"generated_at": out["generated_at"], "shared_themes": shared,
                    "style_map": {d["blogger"]: d["style"] for d in data},
                    "matrix": {d["blogger"]: d["categories"] for d in data}},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print("methodology cards:", f"shipinhao_methodology_cards_{DATE}.json")
    print("theme compare:", f"shipinhao_theme_compare_{DATE}.md")
    for d in data:
        print(f"{d['blogger']:20s} chars={d['chars']:>7,} units={d['units']:>4} pass={d['candidates_pass']:>3} style={d['style']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
