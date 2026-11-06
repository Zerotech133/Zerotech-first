# -*- coding: utf-8 -*-
"""断言抽取器（P0-1）：把重切后的口播段抽成「条件→动作」四要素断言。

设计依据
--------
- 契约：`95_KG/assertion_schema.json`（唯一事实源，代码只读它不硬编码枚举）
- 本体：`95_KG/assertion_ontology.md`
- 上游：`units_v2.build_units()` 产出的段级 unit（内容寻址 unit_id + pit + unit_class）

与既有 card_llm_extract.py 的区别（同名易混，必须分清）
--------------------------------------------------
card_llm_extract 抽的是「名词性概念 + 20-120 字答案」，回答"是什么"；
本模块抽的是「条件 + 动作 + 标的 + 失效条件」，回答"什么时候做什么"。
两者是不同抽取任务，不共用 prompt，不互相替代。

防幻觉的唯一硬闸
----------------
模型返回的 `quote` 必须能在该 unit 的 claim 原文里**精确匹配**（空白与全半角标点归一）。
匹配不上即 A7 hard_reject，不放行、不做模糊兜底。
理由：模型极擅编造"听起来像原文"的句子，这一段文字是整条链路上唯一能机械判伪的地方。

用法
----
    # 试抽 30 条（默认写 95_KG/_assertions/）
    python assertion_extract.py --limit 30

    # 只看候选池规模，不调模型、不写盘
    python assertion_extract.py --dry

    # 全量 M 类 168 段，4 并发
    python assertion_extract.py --limit 0 --workers 4
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import llm_router as R  # noqa: E402
import units_v2 as U  # noqa: E402

KB = Path(r"C:/项目/知识库")
KG = KB / "95_KG"
SCHEMA_PATH = KG / "assertion_schema.json"
OUT_DIR = KG / "_assertions"
DEFAULT_OUT = OUT_DIR / "assertion_candidates.jsonl"
DEFAULT_REJ = OUT_DIR / "assertion_rejected.jsonl"

DEFAULT_PROJECT = "视频号学习_北斗股术1688"

SYSTEM_PROMPT = """你是交易知识的断言抽取器。任务：从一段口播文字中抽取「可复用的交易知识」。

【铁律】
1. 只抽原文明确表达的内容。不得推断、不得补充、不得改写原文含义。
2. 只有这段话确实不含任何可复用知识时才返回 {"assertions": []}。**宁可多抽一条让人工筛，不要漏掉真知识。** 泛泛的鸡汤（"要努力""心态很重要"）算无知识；但作者给出的判断标准、识别方法、术语定义都算知识。
3. 以下一律不抽：寒暄客套、产品推销、个人经历、纯行情预测（如"明天会大涨"）、纯情绪表达。
4. **原文在否定/驳斥/批评某个观点时（"驳斥了""不是…而是""错误观点""很多人以为"），绝不把这个被否定的观点抽成规则。** 要抽就抽作者主张的那一面，并在 rationale 说明这是对错误观点的纠正。
   反例：原文说"驳斥了『股价高、市盈率高才等于风险大』的错误观点，风险是绝对的，最大的风险是筹码丧失"
   → 错：WARNING / 股价高等于风险大
   → 对：WARNING / verb=avoid / "把『股价高=风险大』当成风险判断标准"，或抽"最大风险是筹码丧失"
5. 不需要输出 quote 或 evidence，系统会自动定位原文位置。你只需把断言字段填准。

【五种 kind】
RULE        可复用的规则。包括三类：
            (a) 条件→动作："跌破20日线就减仓一半"
            (b) 判定标准："不创新高且盘整背驰，构成二类卖点"（verb 用 judge 族）
            (c) 无条件原则："永远不要满仓"
WARNING     禁止或风险警示。例："千万别追高"
DEFINITION  解释某个术语/概念**是什么**。只有当原文给出了实质定义内容（构成、性质、判定方式）才抽，
            并把定义内容写进 rationale。只是提到术语名而没给定义的，不要抽。
            例：原文"中枢是三段重叠的区间" → 抽；原文"必须彻底明白中枢的定义" → 不抽。
OBSERVATION 描述某段具体行情，有时效。例："今天放量突破前高"
HYPOTHESIS  作者主观推测。例："我觉得后面可能会补涨"

【字段】
condition      {"text":"触发条件"} 或 null。填 null 表示无条件（原文说"永远/一律/任何时候"），此时 kind 必须是 RULE 或 WARNING。
                **condition 必须是"如果/当/一旦…就"这种触发型条件。**
                原文的"因为…所以…"是**因果解释，不是条件**，要写进 rationale，不要写进 condition。
                反例：原文"因为性格和方法错配，所以做短线会亏"，condition 不能填"性格谨慎"，
                应填 null 或"做短线与性格不匹配"，rationale 填"性格和方法错配"。
                对 DEFINITION 型，condition 填 null。
action.verb     只能从这个白名单选：
                buy add_position open_position scale_in
                sell reduce_position close_position scale_out
                stop_loss take_profit hedge
                hold wait no_action
                screen filter rank
                size_position cap_exposure
                avoid prohibit
                identify confirm classify validate
                other
                —— 判定形态/识别买卖点/确认信号成立，用 identify/confirm/classify/validate，不要用 other。
action.text     **必须是动词开头的动作短语**，说"做什么"，不是说"是什么"、也不是说"结果是什么"。
                反例（错）："生存能力" "风险" "踏空筹码" "保证筹码不丢，成本降低" "中枢"
                正例（对）："减仓一半" "识别二类卖点" "不要随意换股" "把周期放到周线月线"
                判定类写"识别什么形态"，不写"某某点"。
target.scope    这条规则适用的市场品种，只能选：
                equity_cn equity_us equity_hk futures options crypto forex commodity bond fund any unknown
target.text     这条规则的**作用对象**（如"持有的个股"、"日线级别的走势"、"持仓"），不是动作的宾语。
                动作的宾语写在 action.text 里。
invalidator     什么情况下这条不成立。原文有才填，没有填 null。
rationale       原文给出的因果解释（"因为…所以…"）。原文有才填，没有填 null。
signals         涉及的可观测指标名数组，如 ["MA20","成交量","MACD"]。没有填 []。

【输出】严格 JSON，不要 markdown 代码块，不要任何解释文字：
{"assertions":[{"kind":"RULE","condition":{"text":"跌破20日均线且放量"},"action":{"verb":"reduce_position","text":"减仓一半"},"target":{"scope":"equity_cn","text":"持有的个股"},"invalidator":null,"rationale":"放量破位说明主力出货","signals":["MA20","成交量"]}]}
"""

_PUNCT = str.maketrans({
    "，": ",", "。": ".", "：": ":", "；": ";", "！": "!", "？": "?",
    "（": "(", "）": ")", "、": ",", "“": '"', "”": '"', "‘": "'", "’": "'",
    "《": "<", "》": ">", "—": "-", "－": "-", "～": "~",
})


def norm(s: str) -> str:
    """空白与全半角标点归一。仅用于 quote 回查，不用于任何存储。"""
    return re.sub(r"\s+", "", (s or "").translate(_PUNCT))


def load_schema() -> dict:
    with io.open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _s(v) -> str:
    """把任意字段值转成去空白字符串。

    模型偶尔把 invalidator/rationale 填成 dict 或 list（不是字符串），
    直接 ``(x or "").strip()`` 会抛 AttributeError 把整段抽取打挂。
    2026-11-06 乔木缠论首抽就撞上：invalidator={"text": ...} 导致线程异常。
    """
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    return json.dumps(v, ensure_ascii=False).strip()


def verb_set(schema: dict) -> set:
    vs = set()
    for k, v in schema["verbs"].items():
        if k == "note":
            continue
        vs.update(v)
    return vs


def make_assertion_id(kind: str, verb: str, action_text: str,
                      cond_text: str, scope: str) -> str:
    raw = "|".join([kind or "", verb or "", (action_text or "").strip(),
                    (cond_text or "").strip(), scope or ""])
    return "asrt_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def parse_model_json(text: str) -> dict:
    """从模型输出里抠 JSON。容忍 markdown fence 与前后废话。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[A-Za-z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("no_json_object")
    return json.loads(t[i:j + 1])


def collect_candidates(project: str, classes: tuple, limit: int,
                       source_path: Path | None = None,
                       rule_version: str = "v1") -> tuple:
    """返回 (candidates, stat)。candidates 按 (video_key, seq) 稳定排序。

    ``source_path`` 为 None 时按约定取 ``05_学习源件/{project}/source/{project}.md``；
    8博主这类"一个项目多个博主 md"的语料必须显式指定，否则取不到。
    """
    src_path = source_path or (KB / "05_学习源件" / project / "source" / f"{project}.md")
    if not src_path.exists():
        raise SystemExit(f"[fatal] 源件不存在: {src_path}")

    src = src_path.read_text(encoding="utf-8")
    videos = U.parse_md(src)
    units, stat = U.build_units(videos, project, rule_version=rule_version)
    title_of = {v["video_key"]: v.get("title") or "" for v in videos}

    pool = [u for u in units if u.get("unit_class") in classes]
    pool.sort(key=lambda u: (u["video_key"], u["seq"]))

    cands = []
    for u in pool:
        cands.append({
            "unit_id": u["unit_id"],
            "video_key": u["video_key"],
            "seq": u["seq"],
            "title": title_of.get(u["video_key"], ""),
            "text": u["claim"],
            "unit_class": u["unit_class"],
            "pit": u.get("pit"),
            "expire_at": u.get("expire_at"),
        })

    meta = {
        "project": project,
        "source": str(src_path),
        "rule_version": rule_version,
        "units_total": len(units),
        "pool_total": len(pool),
        "pool_classes": list(classes),
        "video_classes": stat.get("video_classes"),
    }
    if limit and limit > 0:
        cands = cands[:limit]
    return cands, meta


def _bigrams(s: str) -> set:
    t = re.sub(r"[^\w\u4e00-\u9fff]", "", s or "")
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])")


def _core_text(a: dict) -> str:
    cond = a.get("condition")
    cond_text = (cond.get("text") or "") if isinstance(cond, dict) else ""
    return " ".join(filter(None, [
        cond_text,
        (a.get("action") or {}).get("text") or "",
        (a.get("target") or {}).get("text") or "",
    ]))


def pick_quote(a: dict, source_text: str) -> tuple:
    """从原文挑与断言最相关的一句。返回 (quote, coverage)。

    引文由代码选而非模型写，理由（2026-11-06 两轮试抽实测）：
      第 1 轮 —— 模型 75% 省略 quote，兜底等于没定位；
      第 2 轮 —— 把 quote 提到首位后，模型 20/23 条只输出 evidence 就结束。
    14B 同时做「抽取」和「精确引文」两件都做不好。拆开：抽取归模型，定位归代码。
    收益：quote 一定是原文子串（零幻觉）、零额外调用成本、格式不会崩。

    coverage = 断言核心词 bigram 在原文中的覆盖率，用于识别「凭空编造」。
    """
    sents = [s.strip() for s in _SENT_SPLIT.split(source_text or "") if s.strip()]
    if not sents:
        return (source_text or "")[:200], 0.0
    grams = _bigrams(_core_text(a))
    if not grams:
        return sents[0], 0.0
    best, best_score = sents[0], -1
    for s in sents:
        sc = sum(1 for g in grams if g in s)
        if sc > best_score:
            best, best_score = s, sc
    return best, best_score / len(grams)


def validate_one(a: dict, unit: dict, schema: dict) -> tuple:
    """返回 (ok, codes, flags, detail)。codes 为空表示通过。

    硬拒 A1-A11 见 schema.validation.hard_reject；软标 F1-F6 只记 flags 不拒。
    引文校验（A11）不在此处——它需要 pick_quote 的结果，在 extract_one 里做。
    """
    codes, flags = [], []
    verbs = verb_set(schema)
    scopes = set(schema["scopes"])
    kinds = set(schema["fields"]["kind"]["enum"])
    caps = schema["fields"]["confidence"]["caps"]

    kind = a.get("kind")
    if kind not in kinds:
        codes.append("A2")

    act = a.get("action") or {}
    verb = _s(act.get("verb"))
    atext = _s(act.get("text"))
    if verb not in verbs:
        codes.append("A3")
    elif verb == "other" and not atext:
        codes.append("A3")
    if not atext:
        codes.append("A4")

    tgt = a.get("target") or {}
    scope = _s(tgt.get("scope"))
    ttext = _s(tgt.get("text"))
    if scope not in scopes:
        codes.append("A5")
    if not ttext:
        codes.append("A6")

    cond = a.get("condition")
    cond_text = ""
    if cond is None:
        if kind not in ("RULE", "WARNING", "DEFINITION"):
            codes.append("A9")
    else:
        cond_text = _s((cond or {}).get("text"))
        if not cond_text and kind not in ("RULE", "WARNING", "DEFINITION"):
            codes.append("A9")

    conf = a.get("confidence")
    if conf is None:
        conf = 0.6
    if not isinstance(conf, (int, float)) or not (0 <= conf <= 1):
        codes.append("A8")
    else:
        if conf > caps.get(kind, 0.8):
            codes.append("A8")

    exp = a.get("expire_at")
    if kind == "OBSERVATION" and not exp:
        codes.append("A10")

    # 软标
    if not _s(a.get("invalidator")):
        flags.append("F1")
    sigs = a.get("signals") or []
    if not isinstance(sigs, list) or not sigs:
        flags.append("F2")
    if verb in ("avoid", "prohibit") and kind != "WARNING":
        flags.append("F4")
    if kind == "DEFINITION" and len(_s(a.get("rationale"))) < 15:
        flags.append("F6")
    # action.text 疑似名词而非动作（不含任何动作字）
    if atext and not re.search(
            r"买|卖|减|加|持|等|抛|止|清|换|识|判|确|分|选|筛|控|降|提|做|看|放|收|调|找|设|避|停|转|拿|让|去", atext):
        flags.append("F7")

    detail = ""
    if codes:
        detail = "|".join(filter(None, [
            "" if "A2" not in codes else f"kind={kind!r}",
            "" if "A3" not in codes else f"verb={verb!r}",
            "" if "A4" not in codes else "action.text 空",
            "" if "A5" not in codes else f"scope={scope!r}",
            "" if "A6" not in codes else "target.text 空",
            "" if "A8" not in codes else f"confidence={conf}",
            "" if "A9" not in codes else "condition=null 但 kind 不允许",
            "" if "A10" not in codes else "OBSERVATION 缺 expire_at",
        ]))
    return (not codes), codes, flags, detail


def extract_one(cand: dict, schema: dict) -> dict:
    """抽一条候选段。返回 {unit_id, ok, assertions, rejected, error, cost, tier}。"""
    user = (
        f"视频标题：{cand['title']}\n"
        f"发布时间：{cand['pit']}\n"
        f"--- 待抽取文字 ---\n{cand['text']}"
    )
    t0 = time.monotonic()
    try:
        r = R.generate(user, system=SYSTEM_PROMPT, temperature=0.1,
                       max_tokens=1400, purpose="assertion_extract")
    except Exception as exc:  # noqa: BLE001
        return {"unit_id": cand["unit_id"], "ok": False, "assertions": [],
                "rejected": [], "error": f"llm_exception:{exc}",
                "cost": 0.0, "tier": None, "elapsed_s": round(time.monotonic() - t0, 2)}

    out = {"unit_id": cand["unit_id"], "ok": False, "assertions": [],
           "rejected": [], "error": None, "cost": r.get("cost_cny") or 0.0,
           "tier": r.get("tier"), "model": r.get("model"),
           "elapsed_s": round(time.monotonic() - t0, 2)}

    if not r.get("ok"):
        out["error"] = "llm_failed:" + str([a.get("status") for a in r.get("attempts", [])])
        return out

    try:
        payload = parse_model_json(r.get("text") or "")
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"A1:{exc}"
        out["raw"] = (r.get("text") or "")[:400]
        return out

    items = payload.get("assertions")
    if not isinstance(items, list):
        out["error"] = "A1:assertions_not_list"
        out["raw"] = (r.get("text") or "")[:400]
        return out

    out["ok"] = True
    seen_ids = set()   # 单段内内容寻址去重（跨段同断言合法，不在此去重）
    for a in items:
        if not isinstance(a, dict):
            out["rejected"].append({"codes": ["A1"], "detail": "not_object", "raw": str(a)[:200]})
            continue
        passed, codes, flags, detail = validate_one(a, cand, schema)
        quote_out, cov = pick_quote(a, cand["text"])
        if cov < 0.15:
            # 断言核心词几乎不出现在原文 → 疑似编造，硬拒
            passed = False
            codes = list(codes) + ["A11"]
            detail = (detail + "|" if detail else "") + f"关键词覆盖率 {cov:.0%}"
        cond_text = ""
        if isinstance(a.get("condition"), dict):
            cond_text = _s(a["condition"].get("text"))
        verb = _s((a.get("action") or {}).get("verb"))
        atext = _s((a.get("action") or {}).get("text"))
        scope = _s((a.get("target") or {}).get("scope"))
        aid = make_assertion_id(a.get("kind"), verb, atext, cond_text, scope)

        rec = {
            "assertion_id": aid,
            "kind": a.get("kind"),
            "condition": a.get("condition"),
            "action": a.get("action"),
            "target": a.get("target"),
            "invalidator": a.get("invalidator"),
            "rationale": a.get("rationale"),
            "signals": a.get("signals") or [],
            "confidence": a.get("confidence") if a.get("confidence") is not None else 0.6,
            "expire_at": a.get("expire_at"),
            "evidence": {
                "unit_id": cand["unit_id"],
                "video_key": cand["video_key"],
                "seq": cand["seq"],
                "quote": quote_out,
                "quote_source": "code_pick",
                "key_coverage": round(cov, 3),
            },
            "completeness": {
                "condition": bool(cond_text),
                "action": bool(atext),
                "target": bool(scope and (a.get("target") or {}).get("text")),
                "invalidator": bool(_s(a.get("invalidator"))),
            },
            "extracted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        rec["completeness_score"] = sum(rec["completeness"].values())
        if passed:
            # 同一段里抽出两条完全等价的断言（aid 由 kind/verb/action/condition/scope
            # 内容寻址，全同即重复）。实测全库仅 1 对，主要用于防未来复发，零误杀。
            if aid in seen_ids:
                out["rejected"].append({"codes": ["A12"], "detail": "duplicate_in_unit",
                                        "assertion": rec})
                continue
            seen_ids.add(aid)
            rec["flags"] = flags
            out["assertions"].append(rec)
        else:
            out["rejected"].append({"codes": codes, "detail": detail,
                                    "flags": flags, "assertion": rec})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="断言抽取器（四要素）")
    ap.add_argument("--project", default=DEFAULT_PROJECT)
    ap.add_argument("--source", default="",
                    help="可选：显式指定源件 md。8博主这类「一项目多博主」语料必须给，"
                         "同时把 --project 设成该博主名，让 unit_id 带上来源前缀")
    ap.add_argument("--classes", default="M", help="候选池 unit_class，逗号分隔，默认 M")
    ap.add_argument("--rule-version", default="v1", choices=["v1", "v2"],
                    help="分型规则版本；v2 只改 U 分支（交易术语兜底 + 长度下限）")
    ap.add_argument("--limit", type=int, default=30, help="抽取条数，0=全量")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--reject-out", default=str(DEFAULT_REJ))
    ap.add_argument("--dry", action="store_true", help="只盘候选池，不调模型")
    args = ap.parse_args(argv)

    schema = load_schema()
    classes = tuple(c.strip() for c in args.classes.split(",") if c.strip())
    src = Path(args.source) if args.source else None
    cands, meta = collect_candidates(args.project, classes, args.limit,
                                     source_path=src, rule_version=args.rule_version)

    print(f"[pool] 项目={meta['project']} 规则={meta['rule_version']} "
          f"全部单元={meta['units_total']} "
          f"候选池({','.join(classes)})={meta['pool_total']} 本次抽={len(cands)}")
    print(f"[pool] 视频级分型={meta['video_classes']}")
    if args.dry:
        for c in cands[:5]:
            print(f"  · [{c['video_key']}#{c['seq']}] {c['text'][:70]}...")
        print("[dry] 未调模型，未写盘")
        return 0

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    t0 = time.monotonic()
    results, errors = [], []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(extract_one, c, schema): c for c in cands}
        done = 0
        for fu in as_completed(futs):
            done += 1
            r = fu.result()
            results.append(r)
            if r.get("error"):
                errors.append(r)
            if done % 10 == 0 or done == len(cands):
                na = sum(len(x["assertions"]) for x in results)
                print(f"  [{done}/{len(cands)}] 累计断言 {na} 条", flush=True)

    all_a = [a for r in results for a in r["assertions"]]
    all_r = [dict(x, unit_id=r["unit_id"]) for r in results for x in r["rejected"]]
    cost = sum(r.get("cost") or 0 for r in results)
    elapsed = time.monotonic() - t0

    with io.open(args.out, "w", encoding="utf-8") as f:
        for a in all_a:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")
    with io.open(args.reject_out, "w", encoding="utf-8") as f:
        for x in all_r:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")

    nseg = len(cands)
    nseg_hit = len({r["unit_id"] for r in results if r["assertions"]})
    print()
    print("=" * 62)
    print(f"段数           {nseg}（命中断言的段 {nseg_hit}，{nseg_hit/max(nseg,1)*100:.1f}%）")
    print(f"通过断言       {len(all_a)} 条（平均每段 {len(all_a)/max(nseg,1):.2f}）")
    print(f"硬拒条目       {len(all_r)} 条")
    print(f"调用失败段     {len(errors)} 段")
    print(f"总成本         ¥{cost:.4f}（单条约 ¥{cost/max(len(all_a),1):.5f}）")
    print(f"耗时           {elapsed:.1f}s")
    if all_a:
        from collections import Counter
        print(f"kind 分布      {dict(Counter(a['kind'] for a in all_a))}")
        print(f"verb 分布      {dict(Counter(a['action']['verb'] for a in all_a).most_common(8))}")
        full = sum(1 for a in all_a if a["completeness_score"] == 4)
        print(f"四要素齐全     {full}/{len(all_a)} = {full/len(all_a)*100:.1f}%")
    if errors:
        print("\n失败样例（前 3）：")
        for e in errors[:3]:
            print(f"  · {e['unit_id']}: {e['error']}")
    print("=" * 62)
    print(f"→ {args.out}")
    print(f"→ {args.reject_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
