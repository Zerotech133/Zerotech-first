# -*- coding: utf-8 -*-
"""A0 总管 agent 实例化骨架 v0.1（K-ORG-006）

定位：A0 的 LLM 判读能力（org.json chief 仍是 planned，本模块为其第一个实体能力）。
与 a0_chief.py（规则模板聚合日报）正交：后者产日报，本模块读日报出「A0 点评」。

用法：
    python a0_agent.py                 # mock 模式（零 GPU，验证结构）
    python a0_agent.py --live          # 真调 Ollama（模型默认取 org.json chief binding）
    python a0_agent.py --live --model qwen3.5:9b   # 覆盖模型（35b 显存紧张时用 9b 先行）

输出：data/shadow/a0_review_<date>.md
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
ORG = BASE / "registry" / "org.json"

MOCK_REVIEW = (
    "- **[链路]**（mock）五票全 done 零降级，链路健康。\n"
    "- **[风险]**（mock）全篮子 HOLD，J2 主判据依赖备用排序，影子后校准 D1。\n"
    "- **[观测]**（mock）601318/300750 出现 T1 倒戈，已入辩论健康观测。\n"
    "- **明日关注**：Day1 五票真推理负载与倒戈复现情况。\n"
)


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


def main():
    ap = argparse.ArgumentParser(description="A0 总管判读")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--live", action="store_true", help="真调 Ollama（默认 mock）")
    ap.add_argument("--model", default=None, help="覆盖模型（默认 org.json chief binding）")
    args = ap.parse_args()

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


if __name__ == "__main__":
    main()
