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

    判读席位升级（K-ORG-130 · MASS「评估者弱成瓶颈」判例落地）：
    python a0_agent.py --live --model glm/glm-4.6 ...   # 走智谱包年通道，Ollama 本地默认不变

输出：data/shadow/a0_review_<date>.md
      data/shadow/a0_compare_<date>_<target>.json（成对判读，含质量自检结果）
纪律：不挂 automation（上线时机待老田拍板）；影子期间点评不含操作建议（人设铁律）。
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE / "engine"))

from llm import LLMError, NovaMaxLLM, OllamaLLM  # noqa: E402
from org_profiles import profile_index  # noqa: E402
import logging
# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

PERSONA = BASE / "prompts" / "a0_chief.md"
COMPARE_PERSONA = BASE / "prompts" / "a0_compare.md"  # K-ORG-120 成对判读人设
ORG = BASE / "registry" / "org.json"


def _glm_key():
    """智谱 key 优先级：audit_ai_config.json 的 zhipu_key > GLM_API_KEY。

    K-ORG-136 判例：旧 GLM_API_KEY 为 coding 计划 key 的 flash 档（5.3 系 429），
    项目内 audit_ai_config.json（不入库）的 zhipu_key 实测 coding 端点 5.3/5.3-flash
    全通——席位升级取项目内 key，环境变量兜底。"""
    try:
        import json as _json
        from pathlib import Path as _P

        _vault = _P(os.environ.get("AUDIT_AI_CONFIG_PATH")
                    or r"C:\项目\_vault\secrets\portal_audit_ai_config.json")
        for _p in (_vault,
                   _P(__file__).resolve().parent.parent / "portal" / "audit_ai_config.json"):
            if _p.exists():
                zk = _json.loads(_p.read_text(encoding="utf-8")).get("zhipu_key", "")
                if zk:
                    return zk
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/a0_agent.py", exc)
    return os.environ.get("GLM_API_KEY", "")


def glm_chat(persona, user, model="glm/glm-5.3", timeout=240):
    """K-ORG-130→136：判读席位 glm 通道——智谱 coding 计划端点（glm-5.3）。

    动机：K-ORG-127 round2 复核实录——9b 评估者在补证档复杂上下文下两处归因
    错误，印证 MASS 结论「评估者弱成瓶颈」，判读席位需可挂强模型。
    K-ORG-136 升级：anthropic 端点（原 4.6）对 coding 计划 key 报 429/404；
    实测 coding 端点（/api/coding/paas/v4，OpenAI 兼容）glm-5.3 / 5.3-flash 全通，
    席位模型升 5.3（思考型：content 为文本，reasoning_content 只算思考不拼正文）。
    返回 (text, "ok") 与 OllamaLLM().chat 同构。"""
    import urllib.error
    import urllib.request

    key = _glm_key()
    if not key:
        raise LLMError(
            "智谱 key 未配置（audit_ai_config.json zhipu_key / GLM_API_KEY 均空；"
            "不降档静默——席位模型是判读有效性的组成部分）"
        )
    payload = {
        "model": model.split("/", 1)[1] if "/" in model else model,
        "max_tokens": 16384,
        "messages": [
            {"role": "system", "content": persona},
            {"role": "user", "content": user},
        ],
        "stream": False,
    }
    req = urllib.request.Request(
        "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    try:
        with safe_urlopen(req, timeout=timeout) as resp:
            d = json.load(resp)
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        OSError,
        json.JSONDecodeError,
    ) as e:  # K-ORG-137：429/404/超时/非JSON一律转
        # LLMError——此前裸抛 urllib 异常，main() 的 except LLMError 捕不到，
        # 降档提示（sys.exit(2)）失效、直接裸 traceback
        raise LLMError("glm 通道调用失败：%s" % e) from e
    out = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    out = out.strip()
    if not out:
        raise LLMError(
            "glm 空回（思考型吞 content? finish=%s）" % d.get("finish_reason")
        )
    return out, "ok"


def route_chat(persona, user, model, metadata=None):
    """按完整 binding 路由；可选 metadata 记录实际模型，不改变 (text, status) 接口。"""
    if metadata is not None:
        metadata["actual"] = model
    if model.startswith("glm/"):
        try:
            return glm_chat(persona, user, model=model)
        except LLMError:
            # 云端不可用时显式记录兜底，不能把本地结果冒充云端裁决。
            text, st = OllamaLLM().chat(persona, user, model="qwen3.5:9b")
            if metadata is not None:
                metadata["actual"] = "ollama/qwen3.5:9b"
            return text, ("degraded" if st in ("normal", "ok") else st)
    if model.startswith("nova/"):
        try:
            port = model.split("/")[1]
            return NovaMaxLLM(port=port).chat(system=persona, user=user)
        except LLMError:
            text, st = OllamaLLM().chat(persona, user, model="qwen3.5:9b")
            if metadata is not None:
                metadata["actual"] = "ollama/qwen3.5:9b"
            return text, ("degraded" if st in ("normal", "ok") else st)
    if model.startswith("ollama/"):
        model = model.split("/", 1)[1]
    return OllamaLLM().chat(persona, user, model=model)


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
        {
            "side": "candidate",
            "point": "（mock）候选产出补齐了契约要求的数据来源",
            "evidence": "（mock）候选侧出现来源字段，冠军侧未见",
        },
        {
            "side": "champion",
            "point": "（mock）冠军产出推理链更完整",
            "evidence": "（mock）冠军侧含两步推理，候选侧一步",
        },
    ],
    "missing": "",
}


def chief_model():
    """按部门档案 > 顶层 chief > 团队档案取 A0 完整 binding；缺档兜底 9b。"""
    try:
        binding = profile_index(BASE).get("A0", {}).get("llm_binding")
        if isinstance(binding, str) and binding.strip():
            return binding.strip()
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/a0_agent.py", exc)
    return "ollama/qwen3.5:9b"


# K-STOCK-120：点评质量四判据自检——黑名单操作建议词（含变体，如"建议买入"）
BLACKLIST_WORDS = ("买入", "卖出", "加仓", "清仓", "止损位")

# K-ORG-133：链路/脚本缺陷类判读须含 [P0|P1|P2] 定级（K-ORG-127 promoted 输出契约）
# K-ORG-134：词表扩展——补 K-ORG-127 补证档/任务单缺陷表述（未闭环/未执行/阻断/报错/崩溃/出错/降级）
DEFECT_KEYWORDS = (
    "读取失败",
    "字段缺失",
    "超时",
    "失败",
    "异常",
    "无响应",
    "未修复",
    "不可见",
    "损坏",
    "卡死",
    "未闭环",
    "未执行",
    "阻断",
    "报错",
    "崩溃",
    "出错",
    "降级",
)
DEFECT_EXEMPT = (
    "已修复",
    "已闭环",
    "已处理",
    "修复闭环",
    "零降级",
)  # 闭环结论非待办缺陷；零降级为正面陈述（日报惯用「五票全 done 零降级」）
SEV_PAT = re.compile(r"\[P[012]\]")


def defect_check(text):
    """K-ORG-133：缺陷类判读定级检查——按行扫描，命中缺陷关键词的行若缺 [P0|P1|P2] 即违规。

    豁免（K-ORG-135 全量语料实证）：① **[回看]** 段（含 `- **[回看]**` 列表前缀，闭环追踪非判读）；
          ② **明日关注** 段（计划/追问，非缺陷判读）；③ 章节标题（`#` 开头）；
          ④ 机器日志引用（[warn]/[skip] 前缀）；⑤ 否定/条件语境——
          行内「无/零/若 + 间隔≤10字 + 关键词」（如「无 P0/P1 异常」「若未执行」）非缺陷陈述；
          ⑥ 行内含「已修复/已闭环」等闭环结论。
    返回 (ok, reasons)。"""
    reasons = []
    _kw = "|".join(re.escape(k) for k in DEFECT_KEYWORDS)
    for ln in text.splitlines():
        s = ln.lstrip()
        if s.startswith("- "):  # 剥列表前缀：- **[回看]** / - [warn] 同裸前缀处理
            s = s[2:].lstrip()
        if s.startswith(("#", "**[回看]**", "[warn]", "[skip]")):
            continue
        if "**明日关注**" in s:
            continue
        if any(k in ln for k in DEFECT_KEYWORDS):
            if SEV_PAT.search(ln):
                continue
            if any(w in ln for w in DEFECT_EXEMPT):
                continue
            if re.search(r"(?:无|零|若)[^，。；]{0,10}(?:%s)" % _kw, ln):
                continue  # 否定/条件语境（无 P0/P1 异常、若未执行…）非缺陷
            reasons.append("缺陷类判读缺定级:%s" % ln.strip()[:60])
    return (len(reasons) == 0, reasons)


def quality_check(text, source_report):
    """K-STOCK-120：对点评文本执行质量判据①②③④，返回 (ok, reasons)。

    ① 不含操作建议（黑名单词命中即违规）
    ② 引用了当日 ≥2 个真实数字（从输入日报中出现的数字算）
    ③ 正文长度 100-500 字（中文字符计）
    ④ 缺陷类判读须含 [P0|P1|P2] 定级（K-ORG-133；推理耗时判据仍在 main 记录，不在此自检）
    """
    reasons = []
    # ① 黑名单词检查（子串匹配即可覆盖"建议买入"等变体）
    for w in BLACKLIST_WORDS:
        if w in text:
            reasons.append("黑名单词命中:%s" % w)
    # ② 统计点评中出现的、且在输入日报里出现过的数字（≥2 个才算通过）
    src_nums = set(re.findall(r"\d+(?:\.\d+)?", source_report))
    cited = [n for n in re.findall(r"\d+(?:\.\d+)?", text) if n in src_nums]
    if len(set(cited)) < 2:
        reasons.append("真实数字引用<2个(仅%d个)" % len(set(cited)))
    # ③ 中文字符计数 100-500
    cn = len(re.findall(r"[\u4e00-\u9fff]", text))
    if not (100 <= cn <= 500):
        reasons.append("中文字符数%d不在100-500" % cn)
    # ④ 缺陷类判读须含 [P0|P1|P2] 定级（K-ORG-133）
    d_ok, d_reasons = defect_check(text)
    if not d_ok:
        reasons.extend(d_reasons)
    return (len(reasons) == 0, reasons)


def parse_json_block(text):
    """K-ORG-120：从模型输出中剥围栏/前后杂文，提取首个 { 到末尾 } 的 JSON。

    返回 (obj, err)：成功 err 为 None；失败 obj 为 None、err 为一句原因。"""
    import re as _re

    try:
        return json.loads(text.strip()), None
    except json.JSONDecodeError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/a0_agent.py", exc)
    m = _re.search(r"\{.*\}", text, _re.S)
    if not m:
        return None, "输出中找不到合法 JSON（无 {...} 块）"
    try:
        return json.loads(m.group(0)), None
    except json.JSONDecodeError as e:
        return None, "JSON 解析失败：%s" % e


def compare_quality_check(verdict, cand_text, champ_text, ev_text=""):
    """K-ORG-120：成对判读质量自检（对齐 prompts/a0_compare.md 契约），返回 (ok, reasons)。

    ① winner/confidence 枚举合法；reasons ≥2 条且每条 side/point/evidence 齐全
    ② 理由文本不含操作建议黑名单词（影子期红线，同日报点评线）
    ③ evidence 可追溯：理由中出现的数字必须能在两份产出文本里找到
       （K-ORG-127 重判起：附补证档时，补证档数字同为合法引用源）
    ④ tie 时 missing 必填非空；非 tie 时 missing 应为空
    """
    import re as _re

    reasons = []
    if not isinstance(verdict, dict):
        return False, ["裁决须为 JSON 对象"]
    if verdict.get("winner") not in ("candidate", "champion", "tie"):
        reasons.append("winner 非法:%r" % verdict.get("winner"))
    if verdict.get("confidence") not in ("high", "medium", "low"):
        reasons.append("confidence 非法:%r" % verdict.get("confidence"))
    rs = verdict.get("reasons") or []
    if not isinstance(rs, list) or len(rs) < 2:
        reasons.append("理由<2条(仅%s条)" % (len(rs) if isinstance(rs, list) else 0))
    else:
        src_nums = set(_re.findall(r"\d+(?:\.\d+)?", cand_text + champ_text + ev_text))
        for i, r in enumerate(rs):
            if not isinstance(r, dict):
                reasons.append("理由%d 须为对象" % (i + 1))
                continue
            if r.get("side") not in ("candidate", "champion", "both"):
                reasons.append("理由%d side 非法:%r" % (i + 1, r.get("side")))
            point, evidence = r.get("point"), r.get("evidence")
            if (
                not isinstance(point, str)
                or not point.strip()
                or not isinstance(evidence, str)
                or not evidence.strip()
            ):
                reasons.append("理由%d point/evidence 为空或非文本" % (i + 1))
            # ③ 数字可追溯（evidence 无数字则视为文字引用，放行）
            for n in set(
                _re.findall(
                    r"\d+(?:\.\d+)?",
                    (evidence if isinstance(evidence, str) else "")
                    + (point if isinstance(point, str) else ""),
                )
            ):
                if n not in src_nums:
                    reasons.append("理由%d 引用数字%s在两份产出中不存在" % (i + 1, n))
    # ② 黑名单（判读理由层同样不许出现操作建议）
    for r in rs if isinstance(rs, list) else []:
        if not isinstance(r, dict):
            continue
        blob = "".join(
            v for v in (r.get("point"), r.get("evidence")) if isinstance(v, str)
        )
        for w in BLACKLIST_WORDS:
            if w in blob:
                reasons.append("理由含黑名单词:%s" % w)
    # ④ tie 配套
    missing_value = verdict.get("missing")
    missing = missing_value.strip() if isinstance(missing_value, str) else ""
    if missing_value is not None and not isinstance(missing_value, str):
        reasons.append("missing 须为文本")
    if verdict.get("winner") == "tie" and not missing:
        reasons.append("winner=tie 但 missing 为空（须说明缺什么证据）")
    if verdict.get("winner") != "tie" and missing:
        reasons.append("winner!=tie 但 missing 非空")
    return (len(reasons) == 0, reasons)


def main():
    ap = argparse.ArgumentParser(description="A0 总管判读")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--live", action="store_true", help="真调 Ollama（默认 mock）")
    ap.add_argument(
        "--model", default=None, help="覆盖模型（默认 org.json chief binding）"
    )
    ap.add_argument(
        "--candidate", default=None, help="候选工作流产出的文件路径（成对判读模式）"
    )
    ap.add_argument(
        "--champion", default=None, help="冠军工作流产出的文件路径（成对判读模式）"
    )
    ap.add_argument(
        "--target", default="wf", help="成对判读的工作流标识（用于输出文件名）"
    )
    ap.add_argument(
        "--persona",
        default=None,
        help="覆盖人设文件（K-ORG-127 影子对比：候选跑，输出加 _cand 后缀防覆盖冠军档）",
    )
    ap.add_argument(
        "--with-prev",
        action="store_true",
        help="附昨日点评档入上下文（K-ORG-127 候选hop：首条[回看]段的依据）",
    )
    ap.add_argument(
        "--evidence",
        default=None,
        help="执行反馈补证档路径（K-ORG-127 重判：附入判读上下文补齐 missing 证据）",
    )
    args = ap.parse_args()

    if args.candidate or args.champion:
        return run_compare(args)

    report = BASE / "data" / "shadow" / ("daily_report_%s.md" % args.date)
    if not report.exists():
        print("[A0] 无日报：%s" % report)
        sys.exit(1)
    daily = report.read_text(encoding="utf-8")
    t0 = time.time()  # K-STOCK-120 判据④：推理耗时起点（含读输入后全过程）
    persona = (Path(args.persona) if args.persona else PERSONA).read_text(
        encoding="utf-8"
    )
    user = "以下是今日投研部日报，请给出判读：\n\n" + daily
    if args.with_prev:  # K-ORG-127 候选 hop：回看段需要昨日点评档作输入
        prev_date = (
            datetime.strptime(args.date, "%Y-%m-%d") - timedelta(days=1)
        ).strftime("%Y-%m-%d")
        prev = BASE / "data" / "shadow" / ("a0_review_%s.md" % prev_date)
        if prev.exists():
            user += (
                "\n\n以下是昨日点评档（首条[回看]请核对昨日「明日关注」是否在今日日报中闭环）：\n\n"
                + prev.read_text(encoding="utf-8")
            )
            print("[A0] 已附昨日点评档：%s" % prev.name)
        else:
            print("[A0] 无昨日点评档（%s），[回看]段按人设跳过" % prev.name)

    if args.live:
        model = args.model or chief_model()
        try:
            review, status = route_chat(
                persona, user, model=model
            )  # K-ORG-130：glm/* 走智谱通道
        except LLMError as e:
            print("[A0] LLM 失败：%s（可用 --model qwen3.5:9b 降档重试）" % e)
            sys.exit(2)
        print("[A0] live 模型=%s 状态=%s" % (model, status))
    else:
        review = MOCK_REVIEW
        print("[A0] mock 模式（结构验证）")

    suffix = (
        "_cand" if args.persona else ""
    )  # 候选跑不覆盖冠军档（成对判读要引用冠军原产出）
    out = BASE / "data" / "shadow" / ("a0_review_%s%s.md" % (args.date, suffix))
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
    ev_text = ""
    if (
        args.evidence
    ):  # K-ORG-127 重判：补证档附入（中立事实，供判读补齐此前 missing 的执行反馈）
        ev_p = Path(args.evidence)
        if not ev_p.exists():
            print("[A0·判读] 补证档不存在：%s" % ev_p)
            sys.exit(1)
        ev_text = ev_p.read_text(encoding="utf-8")
        user += "\n\n【执行反馈补证档（事后补齐的事实证据）】\n" + ev_text

    if args.live:
        model = args.model or chief_model()
        route_meta = {}
        verdict, err, raw = None, None, ""
        for attempt in range(
            1, 4
        ):  # K-ORG-127：小模型严格JSON不稳，解析失败重发至多3次（不放松契约）
            try:
                raw, status = route_chat(
                    persona, user, model=model, metadata=route_meta
                )
            except LLMError as e:
                print("[A0·判读] LLM 失败：%s（可用 --model qwen3.5:9b 降档重试）" % e)
                sys.exit(2)
            if attempt == 1:
                print("[A0·判读] live 模型=%s 状态=%s" % (model, status))
            verdict, err = parse_json_block(raw)
            if not err:
                break
            print("[A0·判读] 第%d次输出 JSON 无效：%s" % (attempt, err))
        if err:
            print("[A0·判读] 3 次仍未得合法 JSON；末次原文前200字：%s" % raw[:200])
            sys.exit(3)
    else:
        verdict, model, status, route_meta = (
            dict(MOCK_COMPARE),
            "mock",
            "mock",
            {"actual": "mock"},
        )
        print("[A0·判读] mock 模式（结构验证）")

    actual = route_meta.get("actual", model)
    degraded = actual != model or status == "degraded"
    elapsed = time.time() - t0
    ok, reasons = compare_quality_check(verdict, cand, champ, ev_text)
    accepted = ok and not degraded and status in ("normal", "ok")
    print(
        "[A0·判读] 推理耗时 %.1fs · 质量自检：%s"
        % (elapsed, "PASS" if ok else "FAIL（%s）" % ";".join(reasons))
    )

    out = (
        BASE / "data" / "shadow" / ("a0_compare_%s_%s.json" % (args.date, args.target))
    )
    out.write_text(
        json.dumps(
            {
                "ticket": "K-ORG-120",
                "date": args.date,
                "target": args.target,
                "model": model,
                "requested": model,
                "actual": actual,
                "degraded": degraded,
                "status": status,
                "accepted": accepted,
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
    print(
        "winner=%s confidence=%s accepted=%s"
        % (
            verdict.get("winner") if isinstance(verdict, dict) else None,
            verdict.get("confidence") if isinstance(verdict, dict) else None,
            accepted,
        )
    )


if __name__ == "__main__":
    main()
