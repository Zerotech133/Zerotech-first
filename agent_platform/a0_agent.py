# -*- coding: utf-8 -*-
"""A0 总管 agent 实例化骨架 v0.1（K-ORG-006）

定位：A0 的 LLM 判读能力（org.json chief 仍是 planned，本模块为其第一个实体能力）。
与 a0_chief.py（规则模板聚合日报）正交：后者产日报，本模块读日报出「A0 点评」。

用法：
    python a0_agent.py                 # mock 模式（零 GPU，验证结构）
    python a0_agent.py --live          # 真调 Ollama（模型默认取 org.json chief binding）
    python a0_agent.py --live --model qwen3.5:9b   # 覆盖模型（35b 显存紧张时用 9b 先行）

    成对判读模式（K-ORG-120 · MASS 内环评估者席位）：
    python a0_agent.py --candidate c.md --champion p.md --target <workflow>
        [--live] [--model ...]        # 对比候选/冠军两份产出，裁定 winner

输出：data/shadow/a0_review_<date>.md
      data/shadow/a0_compare_<date>_<target>.json（成对判读，含质量自检结果）
纪律：不挂 automation（上线时机待老田拍板）；影子期间点评不含操作建议（人设铁律）。
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE / "engine"))

from llm import LLMError, OllamaLLM  # noqa: E402

PERSONA = BASE / "prompts" / "a0_chief.md"
COMPARE_PERSONA = BASE / "prompts" / "a0_compare.md"  # K-ORG-120 成对判读人设
ORG = BASE / "registry" / "org.json"

MOCK_REVIEW = (
    "- **[链路]**（mock）五票全 done 零降级，链路健康。\n"
    "- **[风险]**（mock）全篮子 HOLD，J2 主判据依赖备用排序，影子后校准 D1。\n"
    "- **[观测]**（mock）601318/300750 出现 T1 倒戈，已入辩论健康观测。\n"
    "- **明日关注**：Day1 五票真推理负载与倒戈复现情况。\n"
)

# K-ORG-120：成对判读 mock（结构与 prompts/a0_compare.md 严格 JSON 契约一致，零 GPU 验证用）
MOCK_COMPARE = {
    "winner": "candidate",
    "confidence": "medium",
    "reasons": [
        {"side": "candidate", "point": "（mock）候选产出补齐了契约要求的数据来源",
         "evidence": "（mock）候选侧出现来源字段，冠军侧未见"},
        {"side": "champion", "point": "（mock）冠军产出推理链更完整",
         "evidence": "（mock）冠军侧含两步推理，候选侧一步"},
    ],
    "missing": "",
}


def chief_model():
    """从 org.json 读 chief 的 llm_binding（缺省 ollama/qwen3.5:9b）。
    审计修正（K-STOCK-021b）：兜底原为 35b，但 35b 已实测证伪（本机显存不足必超时，
    K-ORG-008）——兜底必须指向已验证可用档。"""
    try:
        org = json.loads(ORG.read_text(encoding="utf-8"))
        return org.get("chief", {}).get("llm_binding", "ollama/qwen3.5:9b").split("/")[-1]
    except Exception:  # noqa: BLE001
        return "qwen3.5:9b"


# K-STOCK-120：点评质量四判据自检——黑名单操作建议词（含变体，如"建议买入"）
BLACKLIST_WORDS = ("买入", "卖出", "加仓", "清仓", "止损位")


def quality_check(text, source_report):
    """K-STOCK-120：对点评文本执行质量判据①②③，返回 (ok, reasons)。

    ① 不含操作建议（黑名单词命中即违规）
    ② 引用了当日 ≥2 个真实数字（从输入日报中出现的数字算）
    ③ 正文长度 100-500 字（中文字符计）
    判据④（推理耗时）在 main 里记录，不在此自检。
    """
    reasons = []
    # ① 黑名单词检查（子串匹配即可覆盖"建议买入"等变体）
    for w in BLACKLIST_WORDS:
        if w in text:
            reasons.append("黑名单词命中:%s" % w)
    # ② 统计点评中出现的、且在输入日报里出现过的数字（≥2 个才算通过）
    import re
    src_nums = set(re.findall(r"\d+(?:\.\d+)?", source_report))
    cited = [n for n in re.findall(r"\d+(?:\.\d+)?", text) if n in src_nums]
    if len(set(cited)) < 2:
        reasons.append("真实数字引用<2个(仅%d个)" % len(set(cited)))
    # ③ 中文字符计数 100-500
    cn = len(re.findall(r"[\u4e00-\u9fff]", text))
    if not (100 <= cn <= 500):
        reasons.append("中文字符数%d不在100-500" % cn)
    return (len(reasons) == 0, reasons)


def parse_json_block(text):
    """K-ORG-120：从模型输出中剥围栏/前后杂文，提取首个 { 到末尾 } 的 JSON。

    返回 (obj, err)：成功 err 为 None；失败 obj 为 None、err 为一句原因。"""
    import re as _re
    m = _re.search(r"\{.*\}", text, _re.S)
    if not m:
        return None, "输出中找不到 JSON 对象（无 {...} 块）"
    try:
        return json.loads(m.group(0)), None
    except json.JSONDecodeError as e:
        return None, "JSON 解析失败：%s" % e


def compare_quality_check(verdict, cand_text, champ_text):
    """K-ORG-120：成对判读质量自检（对齐 prompts/a0_compare.md 契约），返回 (ok, reasons)。

    ① winner/confidence 枚举合法；reasons ≥2 条且每条 side/point/evidence 齐全
    ② 理由文本不含操作建议黑名单词（影子期红线，同日报点评线）
    ③ evidence 可追溯：理由中出现的数字必须能在两份产出文本里找到
    ④ tie 时 missing 必填非空；非 tie 时 missing 应为空串
    """
    import re as _re
    reasons = []
    if verdict.get("winner") not in ("candidate", "champion", "tie"):
        reasons.append("winner 非法:%r" % verdict.get("winner"))
    if verdict.get("confidence") not in ("high", "medium", "low"):
        reasons.append("confidence 非法:%r" % verdict.get("confidence"))
    rs = verdict.get("reasons") or []
    if not isinstance(rs, list) or len(rs) < 2:
        reasons.append("理由<2条(仅%s条)" % (len(rs) if isinstance(rs, list) else 0))
    else:
        src_nums = set(_re.findall(r"\d+(?:\.\d+)?", cand_text + champ_text))
        for i, r in enumerate(rs):
            if r.get("side") not in ("candidate", "champion", "both"):
                reasons.append("理由%d side 非法:%r" % (i + 1, r.get("side")))
            if not (r.get("point") or "").strip() or not (r.get("evidence") or "").strip():
                reasons.append("理由%d point/evidence 为空" % (i + 1))
            # ③ 数字可追溯（evidence 无数字则视为文字引用，放行）
            for n in set(_re.findall(r"\d+(?:\.\d+)?", r.get("evidence", "") + r.get("point", ""))):
                if n not in src_nums:
                    reasons.append("理由%d 引用数字%s在两份产出中不存在" % (i + 1, n))
    # ② 黑名单（判读理由层同样不许出现操作建议）
    for r in rs if isinstance(rs, list) else []:
        blob = (r.get("point", "") or "") + (r.get("evidence", "") or "")
        for w in BLACKLIST_WORDS:
            if w in blob:
                reasons.append("理由含黑名单词:%s" % w)
    # ④ tie 配套
    missing = (verdict.get("missing") or "").strip()
    if verdict.get("winner") == "tie" and not missing:
        reasons.append("winner=tie 但 missing 为空（须说明缺什么证据）")
    if verdict.get("winner") != "tie" and missing:
        reasons.append("winner!=tie 但 missing 非空")
    return (len(reasons) == 0, reasons)


def main():
    ap = argparse.ArgumentParser(description="A0 总管判读")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--live", action="store_true", help="真调 Ollama（默认 mock）")
    ap.add_argument("--model", default=None, help="覆盖模型（默认 org.json chief binding）")
    ap.add_argument("--candidate", default=None, help="候选工作流产出的文件路径（成对判读模式）")
    ap.add_argument("--champion", default=None, help="冠军工作流产出的文件路径（成对判读模式）")
    ap.add_argument("--target", default="wf", help="成对判读的工作流标识（用于输出文件名）")
    args = ap.parse_args()

    if args.candidate or args.champion:
        return run_compare(args)

    report = BASE / "data" / "shadow" / ("daily_report_%s.md" % args.date)
    if not report.exists():
        print("[A0] 无日报：%s" % report)
        sys.exit(1)
    daily = report.read_text(encoding="utf-8")
    t0 = time.time()  # K-STOCK-120 判据④：推理耗时起点（含读输入后全过程）
    persona = PERSONA.read_text(encoding="utf-8")
    user = "以下是今日投研部日报，请给出判读：\n\n" + daily

    if args.live:
        model = args.model or chief_model()
        try:
            review, status = OllamaLLM().chat(persona, user, model=model)
        except LLMError as e:
            print("[A0] LLM 失败：%s（可用 --model qwen3.5:9b 降档重试）" % e)
            sys.exit(2)
        print("[A0] live 模型=%s 状态=%s" % (model, status))
    else:
        review = MOCK_REVIEW
        print("[A0] mock 模式（结构验证）")

    out = BASE / "data" / "shadow" / ("a0_review_%s.md" % args.date)
    elapsed = time.time() - t0  # K-STOCK-120 判据④：推理耗时（仅记录，不自检）
    print("[A0] 推理耗时 %.1fs" % elapsed)
    # K-STOCK-120：写文件前执行质量四判据自检（①②③），不过则头部加警告行但不阻塞（人工复核用）
    ok, reasons = quality_check(review, daily)
    print("[A0] 质量自检：%s" % ("PASS" if ok else "FAIL（%s）" % ";".join(reasons)))
    header = "# A0 点评 %s\n\n" % args.date
    if not ok:
        header += "> ⚠️ 质量判据未过: %s\n\n" % ";".join(reasons)
    header += "> 输入：daily_report_%s.md · 影子期间点评不含操作建议\n\n" % args.date
    out.write_text(header + review + "\n", encoding="utf-8")
    print("[A0] 点评已生成：%s" % out)
    print(review)


def run_compare(args):
    """K-ORG-120：成对判读模式（MASS 内环评估者席位）。

    读候选/冠军两份产出 → A0 以 a0_compare.md 人设裁定 → 质量自检 →
    落 data/shadow/a0_compare_<date>_<target>.json（含元数据与自检结果）。"""
    if not (args.candidate and args.champion):
        print("[A0·判读] --candidate 与 --champion 必须同时给出")
        sys.exit(1)
    cand_p, champ_p = Path(args.candidate), Path(args.champion)
    for p in (cand_p, champ_p):
        if not p.exists():
            print("[A0·判读] 输入不存在：%s" % p)
            sys.exit(1)
    cand = cand_p.read_text(encoding="utf-8")
    champ = champ_p.read_text(encoding="utf-8")

    t0 = time.time()
    persona = COMPARE_PERSONA.read_text(encoding="utf-8")
    user = (
        "以下是同一任务的两份产出，请按人设裁定并输出严格 JSON。\n\n"
        "【候选工作流产出】\n" + cand + "\n\n【冠军工作流产出】\n" + champ
    )

    if args.live:
        model = args.model or chief_model()
        try:
            raw, status = OllamaLLM().chat(persona, user, model=model)
        except LLMError as e:
            print("[A0·判读] LLM 失败：%s（可用 --model qwen3.5:9b 降档重试）" % e)
            sys.exit(2)
        print("[A0·判读] live 模型=%s 状态=%s" % (model, status))
        verdict, err = parse_json_block(raw)
        if err:
            print("[A0·判读] %s；原文前200字：%s" % (err, raw[:200]))
            sys.exit(3)
    else:
        verdict, model = dict(MOCK_COMPARE), "mock"
        print("[A0·判读] mock 模式（结构验证）")

    elapsed = time.time() - t0
    ok, reasons = compare_quality_check(verdict, cand, champ)
    print("[A0·判读] 推理耗时 %.1fs · 质量自检：%s" % (elapsed, "PASS" if ok else "FAIL（%s）" % ";".join(reasons)))

    out = BASE / "data" / "shadow" / ("a0_compare_%s_%s.json" % (args.date, args.target))
    out.write_text(
        json.dumps(
            {
                "ticket": "K-ORG-120",
                "date": args.date,
                "target": args.target,
                "model": model,
                "candidate_file": str(cand_p),
                "champion_file": str(champ_p),
                "elapsed_s": round(elapsed, 1),
                "quality": {"ok": ok, "reasons": reasons},
                "verdict": verdict,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("[A0·判读] 判读已生成：%s" % out)
    print("winner=%s confidence=%s" % (verdict.get("winner"), verdict.get("confidence")))


if __name__ == "__main__":
    main()
