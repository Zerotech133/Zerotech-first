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
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE / "engine"))

from llm import LLMError, MockLLM, OllamaLLM  # noqa: E402

PERSONA = BASE / "prompts" / "a0_chief.md"
ORG = BASE / "registry" / "org.json"

MOCK_REVIEW = (
    "- **[链路]**（mock）五票全 done 零降级，链路健康。\n"
    "- **[风险]**（mock）全篮子 HOLD，J2 主判据依赖备用排序，影子后校准 D1。\n"
    "- **[观测]**（mock）601318/300750 出现 T1 倒戈，已入辩论健康观测。\n"
    "- **明日关注**：Day1 五票真推理负载与倒戈复现情况。\n"
)


def chief_model():
    """从 org.json 读 chief 的 llm_binding（缺省 ollama/qwen3.5:35b）"""
    try:
        org = json.loads(ORG.read_text(encoding="utf-8"))
        return org.get("chief", {}).get("llm_binding", "ollama/qwen3.5:35b").split("/")[-1]
    except Exception:  # noqa: BLE001
        return "qwen3.5:35b"


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
    header = "# A0 点评 %s\n\n> 输入：daily_report_%s.md · 影子期间点评不含操作建议\n\n" % (
        args.date, args.date)
    out.write_text(header + review + "\n", encoding="utf-8")
    print("[A0] 点评已生成：%s" % out)
    print(review)


if __name__ == "__main__":
    main()
