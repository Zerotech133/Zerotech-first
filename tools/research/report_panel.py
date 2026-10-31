# -*- coding: utf-8 -*-
"""多智能体研报评审面板 MVP（TradingAgents 思路）。

输入：本地已入库研报知识单元（C:/项目/PDF/_ingest_reports/<stem>/knowledge_units.jsonl）。
流程：
  1. 选研报：扫描入库目录，按「报告日期最新」优先、同日报「知识单元数 / 页数」排序取 N 篇；
  2. 每篇把知识单元摘要交给本地 Ollama（qwen3.5:9b）扮演 3 个角色：
       基本面分析师 / 量化研究员 / 风控官，
     各返回严格 JSON：
       {"role":..., "verdict":"pass|avoid|neutral", "reasons":[2-3条],
        "key_facts":[{"fact":..., "page":...}], "risks":[1-2条]}
  3. 聚合：角色票数统计；verdict 为 pass 且 pass 票 >= 2 → 进候选池；
  4. 输出 JSON + Markdown（research/reports/report_panel_<YYYYMMDD>.{json,md}）。

降级策略：单次 LLM 调用失败/超时/JSON 不可解析/超出整体 deadline 时，
该角色自动降级为确定性「规则式摘要」（source=rule_fallback，报告注明），
保证面板始终可产出，并在 fallback 段如实记录。

CLI：
  python tools/research/report_panel.py run --reports 5 --model qwen3.5:9b
  python tools/research/report_panel.py run --reports 3 --rule-only   # 不调 LLM，全规则式

本模块只读知识库，除输出文件外不落盘；调用方可用 chat_fn 注入 mock（测试不真调 LLM）。
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = Path(r"C:/项目/PDF/_ingest_reports")
OUT_DIR = REPO / "research" / "reports"
OLLAMA_ENDPOINT = "http://127.0.0.1:11434/api/chat"
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_TIMEOUT = 300
DEFAULT_DEADLINE = 600
DEFAULT_MAX_UNITS = 8
MAX_CLAIM_CHARS = 220
MAX_EVIDENCE_CHARS = 260

ROLE_PROFILES = {
    "基本面分析师": {
        "focus": "行业空间与景气度、竞争格局、公司盈利驱动、估值与业绩匹配度；判断该研报结论是否具备基本面支撑。",
        "pass_hint": "基本面逻辑成立、驱动清晰、与所给事实一致时倾向于 pass。",
        "avoid_hint": "逻辑不成立、事实与结论矛盾、驱动依赖未证实假设时倾向于 avoid。",
    },
    "量化研究员": {
        "focus": "结论是否可量化、可验证、可回测：是否给出可提取的指标/阈值/样本口径，是否具备历史数据支撑，信号是否可执行。",
        "pass_hint": "存在可量化指标、口径清晰、可回测验证时倾向于 pass。",
        "avoid_hint": "结论不可量化、样本/口径缺失、无法回测或信号不可执行时倾向于 avoid。",
    },
    "风控官": {
        "focus": "下行风险与结论边界：政策/流动性/信用/估值回撤风险，数据覆盖是否充分，研报是否过度乐观、是否缺少反面证据。",
        "pass_hint": "风险已被识别且可控、有反面证据与边界说明时倾向于 pass。",
        "avoid_hint": "风险未披露、数据覆盖不足、结论明显过度乐观时倾向于 avoid。",
    },
}
ROLES = ("基本面分析师", "量化研究员", "风控官")
VERDICTS = ("pass", "avoid", "neutral")

_POS_WORDS = ("增长", "提升", "改善", "受益", "推荐", "买入", "超预期", "回暖", "向好",
              "突破", "增持", "上调", "领先", "扩容", "利好", "加速", "景气", "修复")
_NEG_WORDS = ("风险", "下滑", "下降", "承压", "谨慎", "减持", "下调", "亏损", "波动",
              "回撤", "放缓", "不利", "警示", "泡沫", "高估", "不确定", "压力")
_QUANT_WORDS = ("同比", "环比", "增速", "占比", "百分", "%", "亿元", "万亿", "倍", "目标价",
                "预测", "测算", "复合", "样本", "回测", "分位", "估值", "PE", "PB", "ROE")


class PanelError(RuntimeError):
    """面板流程异常（非单角色降级类错误）。"""


class PanelParseError(PanelError):
    """LLM 输出无法解析为角色 JSON。"""


# ---------------------------------------------------------------------------
# 选研报
# ---------------------------------------------------------------------------
def parse_report_date(name: str) -> str | None:
    """从 rp_AP202610071830180005 提取报告日期 -> '2026-10-07'；失败返回 None。"""
    m = re.search(r"AP(20\d{6})", name or "")
    if not m:
        return None
    raw = m.group(1)
    try:
        return datetime.strptime(raw, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def load_report_catalog(root: Path | str = DEFAULT_ROOT) -> list[dict]:
    """扫描入库目录，返回研报元数据列表（不读单元正文）。"""
    root = Path(root)
    out: list[dict] = []
    if not root.exists():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        ku = d / "knowledge_units.jsonl"
        if not ku.exists():
            continue
        pages = 0
        st = d / "structure.json"
        if st.exists():
            try:
                pages = int(json.loads(st.read_text(encoding="utf-8")).get("pages") or 0)
            except (ValueError, TypeError, OSError, json.JSONDecodeError):
                pages = 0
        out.append({"report_id": d.name, "name": d.name, "path": str(d),
                    "date": parse_report_date(d.name), "pages": pages,
                    "units_total": count_jsonl(ku)})
    return out


def _date_key(rec: dict) -> int:
    date = rec.get("date") or ""
    return int(date.replace("-", "")) if len(date) == 10 else 0


def select_reports(catalog: list[dict], n: int = 5) -> list[dict]:
    """最新优先、同日报知识单元数与页数更丰富者优先；跳过零单元研报。"""
    n = max(1, int(n))
    usable = [r for r in catalog if int(r.get("units_total") or 0) > 0]
    usable.sort(key=lambda r: (-_date_key(r), -int(r.get("units_total") or 0),
                               -int(r.get("pages") or 0), r.get("report_id") or ""))
    return usable[:n]


def load_report_units(report_dir: Path | str, limit: int = DEFAULT_MAX_UNITS) -> list[dict]:
    """读取单篇研报知识单元，裁剪为面板所需字段；坏行跳过（缺数据容错）。"""
    path = Path(report_dir) / "knowledge_units.jsonl"
    units: list[dict] = []
    if not path.exists():
        return units
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return units
    for line in lines:
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        claim = str(raw.get("claim") or "").strip()
        if not claim:
            continue
        page = raw.get("page_start")
        try:
            page = int(page)
        except (TypeError, ValueError):
            page = None
        units.append({
            "chapter": str(raw.get("chapter") or "").strip(),
            "concept": str(raw.get("concept") or "").strip(),
            "claim": claim,
            "evidence": str(raw.get("evidence") or "").strip(),
            "page_start": page,
            "page_end": raw.get("page_end"),
        })
        if len(units) >= max(1, int(limit)):
            break
    return units


_BOILERPLATE_CHAPTER = re.compile(r"^(图表目录|目录|免责声明|声明|风险提示|分析师声明)$")
_DATE_CHAPTER = re.compile(r"^\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")


def _is_meaningful_title(text: str) -> bool:
    text = (text or "").strip()
    return bool(text) and len(text) >= 6 and not _BOILERPLATE_CHAPTER.match(text) and not _DATE_CHAPTER.match(text)


def report_title(report: dict, units: list[dict]) -> str:
    """取首个有信息量的章节名作标题；章节是「图表目录/日期」等空壳时退回概念名。"""
    for u in units:
        chapter = (u.get("chapter") or "").strip()
        if _is_meaningful_title(chapter):
            return chapter if len(chapter) <= 60 else chapter[:60] + "…"
    for u in units:
        concept = (u.get("concept") or "").strip()
        if concept:
            return _clip(concept, 60)
    for u in units:
        chapter = (u.get("chapter") or "").strip()
        if chapter:
            return _clip(chapter, 60)
    return report.get("report_id") or "未知研报"


# ---------------------------------------------------------------------------
# Prompt 构建
# ---------------------------------------------------------------------------
def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def format_units_digest(units: list[dict]) -> str:
    if not units:
        return "（无可用知识单元：入库提炼失败或页数不足，请据「数据不足」给出保守结论）"
    lines = []
    for i, u in enumerate(units, 1):
        page = u.get("page_start")
        page_s = f"p.{page}" if page is not None else "页码缺失"
        if u.get("page_end") and u.get("page_end") != page:
            page_s += f"-{u['page_end']}"
        lines.append(f"{i}. [{page_s}] 概念：{_clip(u.get('concept'), 40)}；"
                     f"主张：{_clip(u.get('claim'), MAX_CLAIM_CHARS)}；"
                     f"证据：{_clip(u.get('evidence'), MAX_EVIDENCE_CHARS) or '（无）'}")
    return "\n".join(lines)


def build_role_prompt(role: str, report: dict, units: list[dict]) -> str:
    """构建单角色评审 prompt（确定性，便于测试）。"""
    if role not in ROLE_PROFILES:
        raise PanelError(f"未知角色：{role}")
    profile = ROLE_PROFILES[role]
    meta = (f"研报ID：{report.get('report_id')}；报告日期：{report.get('date') or '未知'}；"
            f"页数：{report.get('pages') or '未知'}；知识单元数：{report.get('units_total', len(units))}")
    schema = ('{"role":"%s","verdict":"pass|avoid|neutral","reasons":["理由1","理由2"],'
              '"key_facts":[{"fact":"事实","page":1}],"risks":["风险1"]}' % role)
    return (
        f"你是{role}，正在参加一份中文卖方研报的多智能体评审。\n"
        f"【评审视角】{profile['focus']}\n"
        f"【倾向】{profile['pass_hint']}{profile['avoid_hint']}\n"
        f"【研报元数据】{meta}\n"
        f"【研报知识单元（含页码，唯一可用事实来源）】\n{format_units_digest(units)}\n"
        f"【任务】仅依据上述知识单元评审该研报是否值得进入候选池。"
        f"verdict 只能是 pass（可进入候选）/ avoid（规避）/ neutral（证据不足或中性）。"
        f"reasons 给 2-3 条，key_facts 给 1-3 条且 page 必须来自上述单元页码，risks 给 1-2 条。\n"
        f"【输出】只输出一个严格 JSON 对象，不要 markdown、不要解释、不要多余文字。格式：\n"
        f"{schema}\n"
    )


# ---------------------------------------------------------------------------
# Ollama 调用 + JSON 解析
# ---------------------------------------------------------------------------
def call_ollama(prompt: str, model: str = DEFAULT_MODEL, timeout: float = DEFAULT_TIMEOUT,
                endpoint: str = OLLAMA_ENDPOINT) -> str:
    """调用本地 Ollama /api/chat（think=false）；失败抛 PanelError。"""
    body = {"model": model, "stream": False, "think": False,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0.1, "num_predict": 800}}
    req = urllib.request.Request(endpoint, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise PanelError(f"Ollama 调用失败：{exc}") from exc
    content = ((payload.get("message") or {}).get("content") or "").strip()
    if not content:
        raise PanelError("Ollama 返回空内容")
    return content


def _strip_code_fence(text: str) -> str:
    text = (text or "").strip()
    m = re.search(r"```(?:json|JSON)?\s*(.*?)```", text, re.S)
    return m.group(1).strip() if m else text


def _extract_first_object(text: str) -> str | None:
    """从任意文本中截取第一个括号配平的 JSON 对象（忽略字符串内的花括号）。"""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _repair_json(text: str) -> str:
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = re.sub(r",\s*([}\]])", r"\1", text)  # 尾逗号
    return text


def _loads_loose(text: str):
    """尽力解析 JSON：标准 loads -> 宽松 loads -> 截取首个对象后重试。"""
    candidates = [text.strip()]
    fenced = _strip_code_fence(text)
    if fenced and fenced not in candidates:
        candidates.append(fenced)
    extracted = _extract_first_object(fenced or text)
    if extracted:
        candidates.append(extracted)
    for cand in candidates:
        for attempt in (cand, _repair_json(cand)):
            try:
                return json.loads(attempt, strict=False)
            except (json.JSONDecodeError, ValueError):
                continue
    raise PanelParseError(f"无法解析角色 JSON：{_clip(text, 200)!r}")


_VERDICT_MAP = {
    "pass": "pass", "通过": "pass", "推荐": "pass", "买入": "pass", "看好": "pass", "增持": "pass",
    "avoid": "avoid", "规避": "avoid", "回避": "avoid", "卖出": "avoid", "减持": "avoid", "回避/卖出": "avoid",
    "neutral": "neutral", "中性": "neutral", "观望": "neutral", "持有": "neutral", "数据不足": "neutral",
}


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        parts = [p.strip() for p in re.split(r"[；;、]\s*|\n+", value) if p.strip()]
        return parts or [value.strip()]
    return [value]


def _as_page(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    m = re.search(r"\d+", str(value or ""))
    return int(m.group()) if m else None


def normalize_role_payload(raw: dict, role: str) -> dict:
    """把 LLM 原始 dict 规整为面板角色结构；缺字段尽量修复并标记 repaired。"""
    if not isinstance(raw, dict):
        raise PanelParseError(f"角色输出不是 JSON 对象：{type(raw).__name__}")
    repaired = False
    verdict_raw = str(raw.get("verdict") or raw.get("观点") or raw.get("结论") or "").strip().lower()
    verdict = _VERDICT_MAP.get(verdict_raw)
    if verdict is None:
        for key, val in _VERDICT_MAP.items():
            if key and key in verdict_raw:
                verdict = val
                break
    if verdict is None:
        verdict = "neutral"
        repaired = True
    reasons = []
    for item in _as_list(raw.get("reasons") or raw.get("理由")):
        text = _clip(item.get("reason") if isinstance(item, dict) else item, 200)
        if text and text not in reasons:
            reasons.append(text)
        if len(reasons) >= 3:
            break
    if not reasons:
        reasons = ["模型未给出理由（已降级为中性/待复核）"]
        repaired = True
    else:
        repaired = repaired or len(reasons) < 2
    key_facts = []
    for item in _as_list(raw.get("key_facts") or raw.get("关键事实")):
        if isinstance(item, dict):
            fact = str(item.get("fact") or item.get("事实") or "").strip()
            page = _as_page(item.get("page") or item.get("页码"))
        else:
            fact, page = str(item or "").strip(), None
        if fact:
            key_facts.append({"fact": _clip(fact, 200), "page": page})
        if len(key_facts) >= 3:
            break
    risks = []
    for item in _as_list(raw.get("risks") or raw.get("风险")):
        text = _clip(item.get("risk") if isinstance(item, dict) else item, 200)
        if text and text not in risks:
            risks.append(text)
        if len(risks) >= 2:
            break
    if not risks:
        risks = ["未单列风险（模型输出缺失，需人工复核）"]
        repaired = True
    return {"role": role, "verdict": verdict, "reasons": reasons,
            "key_facts": key_facts, "risks": risks, "repaired": repaired}


def parse_role_json(text: str, role: str | None = None) -> dict:
    """容错解析 LLM 输出为角色 JSON。role=None 时沿用输出中的 role 字段。"""
    expected = role
    data = _loads_loose(text)
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        raise PanelParseError("角色输出顶层不是 JSON 对象")
    if expected is None:
        expected = str(data.get("role") or "").strip() or ROLES[0]
    return normalize_role_payload(data, expected)


# ---------------------------------------------------------------------------
# 规则式降级摘要
# ---------------------------------------------------------------------------
def _hit_score(text: str, words: tuple[str, ...]) -> int:
    return sum(text.count(w) for w in words)


def rule_based_role(role: str, report: dict, units: list[dict], reason: str = "llm_unavailable") -> dict:
    """确定性降级：关键词/数据覆盖启发式，source=rule_fallback。"""
    corpus = " ".join((u.get("claim") or "") + " " + (u.get("evidence") or "") for u in units)
    pos = _hit_score(corpus, _POS_WORDS)
    neg = _hit_score(corpus, _NEG_WORDS)
    quant = _hit_score(corpus, _QUANT_WORDS)
    coverage = len(units)
    if coverage < 2:
        verdict = "neutral"
    elif role == "风控官":
        verdict = "avoid" if neg >= 2 and neg >= pos else ("pass" if pos >= 2 and neg == 0 else "neutral")
    elif role == "量化研究员":
        if quant >= 2 and pos >= neg:
            verdict = "pass"
        elif neg >= 2 and neg > pos:
            verdict = "avoid"
        else:
            verdict = "neutral"
    else:  # 基本面分析师
        if pos >= 2 and pos > neg:
            verdict = "pass"
        elif neg >= 2 and neg >= pos:
            verdict = "avoid"
        else:
            verdict = "neutral"
    facts = []
    for u in units[:3]:
        page = u.get("page_start")
        if u.get("claim"):
            facts.append({"fact": _clip(u.get("claim"), 160), "page": page})
    reasons = [
        f"规则式降级（{reason}）：关键词正面 {pos} / 负面 {neg} / 量化信号 {quant}，"
        f"知识单元覆盖 {coverage} 条。",
        f"评审视角（{role}）：{'正面证据占优' if pos > neg else ('风险信号偏多' if neg > pos else '正负证据相当')}；"
        f"该结论未经 LLM 复核，仅供参考。",
    ]
    risks = ["降级路径未做语义论证，存在漏检/误判风险"]
    if neg:
        risks.append(f"文本中出现 {neg} 处风险类关键词，需人工复核")
    if coverage < 2:
        reasons.append("知识单元不足 2 条，判定为 neutral（证据不足）")
    return {"role": role, "verdict": verdict, "reasons": reasons, "key_facts": facts,
            "risks": risks[:2], "repaired": False, "source": "rule_fallback", "fallback_reason": reason}


# ---------------------------------------------------------------------------
# 聚合 + 渲染
# ---------------------------------------------------------------------------
def aggregate_roles(roles: list[dict]) -> dict:
    votes = {v: 0 for v in VERDICTS}
    for r in roles:
        if isinstance(r, dict) and r.get("verdict") in votes:
            votes[r["verdict"]] += 1
    if votes["pass"] >= 2:
        verdict = "pass"
    elif votes["avoid"] >= 2:
        verdict = "avoid"
    else:
        verdict = "neutral"
    candidate = verdict == "pass" and votes["pass"] >= 2
    majority = max(votes, key=lambda k: votes[k])
    dissent = [r.get("role") for r in roles if isinstance(r, dict) and r.get("verdict") != majority]
    return {"votes": votes, "verdict": verdict, "candidate": candidate,
            "pass_votes": votes["pass"], "dissent_roles": dissent,
            "rule": "verdict=pass 且 pass 票>=2 进候选池"}


def evaluate_report(report: dict, units: list[dict], chat_fn, model: str, timeout: float,
                    deadline_at: float, max_workers: int = 1) -> dict:
    """对单篇研报跑 3 角色评审（chat_fn 可注入，测试不真调 LLM）。"""
    title = report_title(report, units)
    errors: list[str] = []

    def _one(role: str) -> dict:
        remaining = deadline_at - time.monotonic()
        if remaining <= 1:
            return rule_based_role(role, report, units, reason="deadline_exceeded")
        prompt = build_role_prompt(role, report, units)
        try:
            raw = chat_fn(prompt, model=model, timeout=max(5.0, min(timeout, remaining)))
            parsed = parse_role_json(raw, role)
            parsed["source"] = "llm"
            parsed["fallback_reason"] = None
            return parsed
        except PanelParseError as exc:
            errors.append(f"{role} JSON 解析失败：{_clip(exc, 160)}")
            return rule_based_role(role, report, units, reason="parse_error")
        except (PanelError, TimeoutError, OSError, ValueError) as exc:
            errors.append(f"{role} LLM 调用失败：{_clip(exc, 160)}")
            return rule_based_role(role, report, units, reason="llm_error")

    if max_workers > 1 and len(ROLES) > 1:
        with cf.ThreadPoolExecutor(max_workers=min(max_workers, len(ROLES))) as pool:
            roles = list(pool.map(_one, ROLES))
    else:
        roles = [_one(role) for role in ROLES]
    aggregate = aggregate_roles(roles)
    return {"report_id": report.get("report_id"), "title": title,
            "date": report.get("date"), "pages": report.get("pages"),
            "units_total": report.get("units_total", len(units)),
            "units_used": len(units), "roles": roles, "aggregate": aggregate,
            "errors": errors}


def run_panel(root: Path | str = DEFAULT_ROOT, reports_n: int = 5, model: str = DEFAULT_MODEL,
              chat_fn=call_ollama, timeout: float = DEFAULT_TIMEOUT,
              deadline_seconds: float = DEFAULT_DEADLINE, max_workers: int = 3,
              max_units: int = DEFAULT_MAX_UNITS, rule_only: bool = False,
              date_tag: str | None = None) -> dict:
    """主流程：选研报 -> 3 角色评审 -> 聚合 -> 结构化 payload。"""
    started = time.monotonic()
    deadline_at = started + max(1.0, float(deadline_seconds))
    catalog = load_report_catalog(root)
    selected = select_reports(catalog, reports_n)
    panel = []
    reports_meta = []
    rule_fallback_roles = 0
    llm_roles = 0
    calls_attempted = 0
    notes: list[str] = []
    data_source = str(root)
    if not catalog:
        notes.append(f"未在 {data_source} 找到任何已入库研报目录（knowledge_units.jsonl），面板为空。")
    for rec in selected:
        units = load_report_units(rec["path"], limit=max_units)
        report = dict(rec)
        report["units_total"] = rec.get("units_total", len(units))
        title = report_title(report, units)
        reports_meta.append({**{k: rec.get(k) for k in
                                ("report_id", "date", "pages", "units_total", "path")},
                             "title": title, "units_used": len(units)})
        if rule_only:
            roles = [rule_based_role(role, report, units, reason="rule_only") for role in ROLES]
            item = {"report_id": rec["report_id"], "title": title, "date": rec.get("date"),
                    "pages": rec.get("pages"), "units_total": report["units_total"],
                    "units_used": len(units), "roles": roles,
                    "aggregate": aggregate_roles(roles), "errors": []}
        else:
            item = evaluate_report(report, units, chat_fn, model, timeout, deadline_at,
                                   max_workers=max_workers)
            calls_attempted += sum(1 for r in item["roles"] if r.get("source") != "rule_fallback")
        panel.append(item)
        for r in item["roles"]:
            if r.get("source") == "rule_fallback":
                rule_fallback_roles += 1
            else:
                llm_roles += 1
    candidates = [{"report_id": p["report_id"], "title": p["title"], "date": p.get("date"),
                   "pages": p.get("pages"), "pass_votes": p["aggregate"]["pass_votes"],
                   "votes": p["aggregate"]["votes"]}
                  for p in panel if p["aggregate"]["candidate"]]
    candidates.sort(key=lambda c: (-c["pass_votes"], -(c.get("pages") or 0), c["report_id"]))
    if rule_fallback_roles:
        notes.append(f"{rule_fallback_roles} 个角色评审降级为规则式摘要（LLM 调用失败/超时/JSON 不可解析/超 deadline），"
                     f"已在 panel.roles.source=rule_fallback 与 errors 中标注。")
    elapsed = round(time.monotonic() - started, 1)
    if elapsed > deadline_seconds:
        notes.append(f"本次运行耗时 {elapsed}s，超出 deadline {deadline_seconds}s：剩余角色已按规则式降级。")
    return {
        "schema_version": 1,
        "kind": "report_panel_mvp",
        "approach": "TradingAgents 多智能体评审（基本面分析师/量化研究员/风控官）",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "date_tag": date_tag or datetime.now().strftime("%Y%m%d"),
        "model": model,
        "llm": {"endpoint": OLLAMA_ENDPOINT, "think": False, "timeout_seconds": timeout,
                "deadline_seconds": deadline_seconds,
                "roles_total": len(panel) * len(ROLES),
                "llm_ok_roles": llm_roles, "rule_fallback_roles": rule_fallback_roles,
                "rule_only": bool(rule_only)},
        "selection": {"reports_requested": reports_n, "reports_selected": len(selected),
                      "rule": "报告日期最新优先，同日按知识单元数/页数降序；零知识单元研报跳过",
                      "catalog_size": len(catalog),
                      "skipped_no_units": sum(1 for c in catalog if int(c.get("units_total") or 0) == 0)},
        "reports": reports_meta,
        "panel": panel,
        "candidates": candidates,
        "fallback": {"used": bool(rule_fallback_roles or rule_only),
                     "llm_unavailable_roles": rule_fallback_roles,
                     "notes": notes},
        "elapsed_seconds": elapsed,
    }


def render_markdown(payload: dict) -> str:
    p = payload
    llm = p.get("llm") or {}
    sel = p.get("selection") or {}
    lines = [
        f"# 多智能体研报评审面板（{p.get('date_tag', '')}）", "",
        f"> TradingAgents 思路 MVP：每篇研报由 **{ ' / '.join(ROLES) }** 三个角色独立评审，"
        f"pass 票 ≥2 且综合 verdict=pass 进入候选池。", "",
        "## 运行概览", "",
        f"- 生成时间：{p.get('generated_at')}",
        f"- 模型：`{p.get('model')}`（本地 Ollama，think=false，timeout={llm.get('timeout_seconds')}s，"
        f"deadline={llm.get('deadline_seconds')}s）",
        f"- 选篇：请求 {sel.get('reports_requested')} 篇，实际 {sel.get('reports_selected')} 篇"
        f"（候选目录 {sel.get('catalog_size')} 篇，跳过零单元 {sel.get('skipped_no_units')} 篇）；"
        f"规则：{sel.get('rule')}",
        f"- 角色评审：{llm.get('llm_ok_roles')}/{llm.get('roles_total')} 走 LLM，"
        f"{llm.get('rule_fallback_roles')} 个降级为规则式（fallback.used={p.get('fallback', {}).get('used')}）",
        f"- 候选池：**{len(p.get('candidates') or [])} 篇**；耗时 {p.get('elapsed_seconds')}s", "",
    ]
    if p.get("candidates"):
        lines += ["## 候选池（pass 票 ≥ 2）", "",
                  "| # | 研报 | 日期 | 页数 | pass/avoid/neutral |",
                  "|---|---|---|---|---|"]
        for i, c in enumerate(p["candidates"], 1):
            v = c.get("votes") or {}
            lines.append(f"| {i} | `{c['report_id']}` {c.get('title') or ''} | {c.get('date')} | "
                         f"{c.get('pages')} | {v.get('pass', 0)}/{v.get('avoid', 0)}/{v.get('neutral', 0)} |")
        lines.append("")
    else:
        lines += ["## 候选池", "", "本次无研报获得 ≥2 个 pass 票，候选池为空（未发现可进入下游的研报）。", ""]
    lines += ["## 每篇角色观点", ""]
    for item in p.get("panel") or []:
        agg = item.get("aggregate") or {}
        v = agg.get("votes") or {}
        lines += [f"### `{item.get('report_id')}` {item.get('title') or ''}", "",
                  f"- 日期：{item.get('date')}；页数：{item.get('pages')}；"
                  f"知识单元：{item.get('units_used')}/{item.get('units_total')}",
                  f"- 聚合：verdict=**{agg.get('verdict')}**（pass {v.get('pass', 0)} / avoid {v.get('avoid', 0)} / "
                  f"neutral {v.get('neutral', 0)}），候选池：{'是' if agg.get('candidate') else '否'}；"
                  f"少数派：{', '.join(agg.get('dissent_roles') or []) or '无'}", ""]
        for r in item.get("roles") or []:
            source = "LLM" if r.get("source") == "llm" else f"规则降级({r.get('fallback_reason')})"
            lines.append(f"- **{r.get('role')}** · verdict=`{r.get('verdict')}` · 来源：{source}")
            for reason in r.get("reasons") or []:
                lines.append(f"  - 理由：{reason}")
            for fact in r.get("key_facts") or []:
                page = fact.get("page")
                lines.append(f"  - 事实：{fact.get('fact')}（p.{page if page is not None else '?'}）")
            for risk in r.get("risks") or []:
                lines.append(f"  - 风险：{risk}")
        if item.get("errors"):
            lines.append(f"- 降级/异常记录：{'；'.join(item['errors'])}")
        lines.append("")
    lines += ["## 局限与说明", ""]
    lines += [f"- {n}" for n in (p.get("fallback", {}).get("notes") or ["无 fallback，全部角色由 LLM 完成。"])]
    lines += [
        "- 每篇仅投喂最多若干条知识单元摘要（claim+evidence 截断），非全文；抽取质量受入库阶段 OCR/LLM 提炼影响。",
        "- 角色 prompt 未做多轮辩论/记忆，属 TradingAgents 的简化 MVP；LLM 存在幻觉与一致性风险，结论仅供研究参考。",
        "- 候选池仅为「研报值得进一步跟踪」的初筛，不构成投资建议，未经回测/实盘验证。",
        "- 本地 9b 模型在长文本上可能给出模板化理由；建议人工抽查 key_facts 页码与原文一致性。",
    ]
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict, json_path: Path | str, md_path: Path | str) -> tuple[Path, Path]:
    json_path = Path(json_path)
    md_path = Path(md_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    return json_path, md_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="多智能体研报评审面板 MVP（TradingAgents 思路）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="跑评审面板并输出 JSON+MD")
    run.add_argument("--reports", type=int, default=5, help="评审研报数量（默认 5）")
    run.add_argument("--model", default=DEFAULT_MODEL, help="Ollama 模型（默认 qwen3.5:9b）")
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="单次 LLM 超时秒数（默认 300）")
    run.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE, help="LLM 阶段整体 deadline 秒数（默认 600）")
    run.add_argument("--workers", type=int, default=3, help="并行 LLM 数（默认 3）")
    run.add_argument("--max-units", type=int, default=DEFAULT_MAX_UNITS, help="每篇最多投喂知识单元数（默认 8）")
    run.add_argument("--root", default=str(DEFAULT_ROOT), help="研报入库根目录")
    run.add_argument("--date", default=None, help="输出文件名日期标签（默认今天，如 20261101）")
    run.add_argument("--out-json", default=None, help="JSON 输出路径（默认 research/reports/report_panel_<date>.json）")
    run.add_argument("--out-md", default=None, help="Markdown 输出路径（默认同名 .md）")
    run.add_argument("--rule-only", action="store_true", help="不调 LLM，全部规则式（冒烟/无 Ollama 时）")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "run":  # pragma: no cover - argparse 已收口
        return 2
    date_tag = args.date or datetime.now().strftime("%Y%m%d")
    out_json = Path(args.out_json) if args.out_json else OUT_DIR / f"report_panel_{date_tag}.json"
    out_md = Path(args.out_md) if args.out_md else OUT_DIR / f"report_panel_{date_tag}.md"
    payload = run_panel(root=args.root, reports_n=args.reports, model=args.model,
                        timeout=args.timeout, deadline_seconds=args.deadline,
                        max_workers=args.workers, max_units=args.max_units,
                        rule_only=args.rule_only, date_tag=date_tag)
    jp, mp = write_outputs(payload, out_json, out_md)
    print(f"[report_panel] reports={len(payload['reports'])} candidates={len(payload['candidates'])} "
          f"llm_ok={payload['llm']['llm_ok_roles']} fallback={payload['llm']['rule_fallback_roles']} "
          f"elapsed={payload['elapsed_seconds']}s")
    print(f"[report_panel] json={jp}")
    print(f"[report_panel] md={mp}")
    return 0 if payload["reports"] else 1


if __name__ == "__main__":
    sys.exit(main())
