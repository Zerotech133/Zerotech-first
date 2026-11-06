# -*- coding: utf-8 -*-
"""断言忠实度校验（verification pass）：模型级复核，专治机械规则抓不到的方向性错误。

为什么必须有这一层
------------------
assertion_extract 的硬闸（A1-A11）只能判**形式**：字段齐不齐、verb 在不在白名单、
引文能不能在原文里精确匹配。它判不了**意思对不对**。

2026-11-06 人工逐条审 77 条实测，11 条 bad 里 4 条是方向性错误（5.2%），三套机械检测全失败：
  · 否定词窗口 —— 抓 14 条全误报，4 条真反转全漏
  · bigram 覆盖率 —— 正常条也有 0.00，无法区分
  · 同句判据 —— 正常条 39/74 也不同句
根因：中文否定辖域是语义问题（"卖错了就别看" / "小级别卖点不用卖"），字符串规则判不了。

本轮把抓的范围从「方向性错误」扩到 5 类，因为 11 条 bad 的完整分布是：
  polarity 4 / condition_mismatch 0 独立（含在 polarity 里）/ vacuous 5 / ephemeral 1 / not_supported 1

判而不改
--------
模型只出结论（ok / fix / drop）+ 理由 + 可选修正建议，**不直接改数据**。
理由：14B 重写整条断言的失败率高于判断本身；改错比不改更危险。
修正建议进 _verify/fix_suggested.jsonl，由人或下游规则决定是否采纳。

用法
----
    # 全量校验（按 unit_id 分组，一个原文一次调用）
    python assertion_verify.py

    # 只校验指定的 4 条已知错误（prompt 冒烟，约 ¥0.01）
    python assertion_verify.py --only-ids asrt_c440dd58e129,asrt_87551a58c28c

    # 只校验人工已审的 77 条，用来算准确率
    python assertion_verify.py --only-reviewed
"""
from __future__ import annotations

import argparse
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

import difflib  # noqa: E402

KB = Path(r"C:/项目/知识库")
KG = KB / "95_KG"
OUT_DIR = KG / "_assertions"
DEF_IN = OUT_DIR / "assertion_all.jsonl"
DEF_UNITS = KB / "30_知识单元" / "视频号学习_北斗股术1688_units_effective_classified.jsonl"
VERIFY_DIR = OUT_DIR / "_verify"
DEFAULT_PROJECT = "视频号学习_北斗股术1688"

SYSTEM_PROMPT = """你是交易知识的断言审核员。给你一段口播原文，以及从这段原文里抽出来的若干条断言。
你的任务：逐条判断这条断言是否**忠实于原文的意思**。

【这不是润色任务】
不要评价写得好不好、格式对不对。只问一件事：**原文说的是这个意思吗？**

【五类问题，按严重度排序】
1. polarity —— 动作方向反了。最常见：原文有否定词（别/不要/不用/不参与/避免），抽取时丢了否定，变成正向动作。
   真实案例：原文"卖错了就别看这只股票" → 抽成"继续看这只股票"。这是最严重的错误，必须 drop。
   真实案例：原文"主升段还没走出来…小级别卖点不用卖" → 抽成"卖出小级别卖点"。同样 drop。
2. condition_mismatch —— 条件和动作不是原文的配对。条件张冠李戴，或者把"直到…才…"的终止条件抽成了触发条件。
   真实案例：原文"出现三买后，就满仓持股…直到中枢完成向上移动出现背驰，然后全部抛出"
   → 抽成"条件=出现三买后 / 动作=全部抛出"。错在把终止条件当成了触发条件。这是 fix。
3. not_supported —— 原文没有这个意思，是模型自己补充的。drop。
4. vacuous —— 空泛：空定义（只重复术语名，没给出构成/性质/判定方式）、同义反复（条件与动作说的是同一件事）、
   描述性陈述被抽成动作（"这是最大的风险"抽成"避免失去盈利准入证"）。drop。
5. ephemeral —— 具体历史事件或具体某天的行情，不可复用。drop。
   判据看**条件**：条件里出现具体日期、具体政策事件（如"530提高印花税""某年某月降准"）、
   或"某天某只票涨了多少"，就是 ephemeral。哪怕写得像一条规则，它也只对那一天有效。

【重要：不要误报】
- 条件里含否定词（"如果不满仓""不创新高"）是正常的，不是 polarity。只有当**动作方向**与原文相反时才是 polarity。
- 抽取比原文更概括、更书面，只要方向对，就算 ok。
- 原文用的是同义词、口语说法，抽取换成术语，算 ok。
- 但同义词豁免须能在 src 中指出对应词（见第 1.5 步）；仅凭「像是更概括」不算。
- 拿不准时判 ok，不要为了显本事硬挑错。

【最容易漏的一类，请重点查】
断言里出现**原文根本没有的概念或因果**，尤其是一个听起来很有道理的后果。
真实案例：原文"仓位配置不是根据预测的未来去加仓减仓，而是要根据市场现状做出合理的应对"
→ 抽成"避免用亏钱的交易模式做仓位配置"。"亏钱的交易模式"原文一个字都没提，是模型自己编的因果。
这条容易被判 ok，因为方向看着是负面的、像那么回事。**必须判 not_supported。**

【四步走，不要跳步】
第 1 步：在原文里找到与这条断言动作对应的那句话，逐字摘出来填进 src（10-30 字）。
　　　　摘不出来 = 原文没有这个意思 → not_supported。
第 1.5 步【动作必须有原文依据，主题相近不算】：把 action.text 去掉否定词
　　　　（不要/别/不用/避免/切勿/不参与）后的核心实词，与刚摘出的 src 逐字比对。
　　　　· src 里既没有这个词、也没有能明确指出的同义说法（OCR 错字如"嫁回来"≈"加回来"算同义），
　　　　  说明动作是凭空捏造的 → not_supported / drop。
　　　　· 即使 src 讲的是同一话题，只要动作概念在原文里一个字都没有，也不能判 ok。
　　　　  真实案例：原文"医药的标的有近300个…一定要严格按照标准筛选"
　　　　           → 抽成"不要随意换股"。"换股"原文完全没有 → not_supported，不是 ok。
　　　　· 同义词豁免只在你能在 src 中指出对应词时成立，不能凭"像是更概括的说法"放行。
第 2 步：看那句话有没有否定词（别/不要/不用/不参与/避免/切勿），再看 action.text 的方向是否与之一致。
第 3 步：看条件。原文是不是"…之后一直…直到…才…"这种持续到终止条件的句式？如果是，抽取只写了前半截条件就是 condition_mismatch。
第 4 步：下结论。

【输出】严格 JSON，不要 markdown 代码块，不要任何解释文字：
{"verdicts":[{"idx":1,"src":"卖错了就别看这只股票","verdict":"drop","issue":"polarity","reason":"原文『卖错了就别看』是否定，抽取成了『继续看』","fix":null}]}

字段说明：
  idx      断言的编号（从 1 开始，对应输入里的 [1] [2]）
  src      **必填**，原文中与这条断言对应的句子，逐字摘抄，30 字以内。摘不出就填 ""
  verdict  ok / fix / drop
  issue    polarity / condition_mismatch / not_supported / vacuous / ephemeral / none
  reason   一句话说清问题，40 字以内。verdict=ok 时填 "原文支持"。
  fix      verdict=fix 时给出修正后的动作短语（字符串），否则 null
必须为每一条断言都给一个 verdict，条数要一致。
**只输出一个 JSON 对象，一次给完所有条目。不要先给一版再补一版。**

【verdict 与 issue 必须一致，不许自相矛盾】
  issue=none        → verdict 只能是 ok
  issue=condition_mismatch → verdict 只能是 fix
  issue=别的任何一种 → verdict 只能是 drop
如果只是措辞不够漂亮、够不够书面，那不是问题，issue 填 none，verdict 填 ok。
"""

_ISSUES = {"polarity", "condition_mismatch", "not_supported", "vacuous", "ephemeral", "none"}
_VERDICTS = {"ok", "fix", "drop"}


def load_units(path: Path) -> dict:
    m = {}
    if not path.exists():
        return m
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            m[d["unit_id"]] = d
    return m


def build_units_map(project: str, source: str = "", rule_version: str = "v1") -> dict:
    """用 units_v2 现场重建单元表（与抽取器同一来源）。

    不能用 30_知识单元 下的 jsonl 顶替：那批文件的 unit_id 还是 legacy 编号
    （`learn_视频号学习_北斗股术1688_0001`），抽取器写的是内容寻址 ID
    （`learn_视频号学习_北斗股术1688_1d09668f18`），两套对不上，162 条会全部匹配失败。

    ``rule_version`` 必须与抽取时一致：v2 会改变视频分型，但**段文本与 unit_id
    不变**（内容寻址），所以只影响候选池归属，不影响这里的 lookup。
    """
    src = Path(source) if source else (
        KB / "05_学习源件" / project / "source" / f"{project}.md")
    if not src.exists():
        return {}
    videos = U.parse_md(src.read_text(encoding="utf-8"))
    units, _stat = U.build_units(videos, project)
    return {u["unit_id"]: u for u in units}


def load_jsonl(path: Path) -> list:
    rows = []
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _cond_text(a: dict) -> str:
    c = a.get("condition")
    if isinstance(c, dict):
        return (c.get("text") or "").strip()
    return ""


def build_user(unit_text: str, group: list) -> str:
    """一次调用处理同一段原文的全部断言。原文共享，模型有完整上下文。"""
    lines = [f"原文段落：\n{unit_text}\n", f"抽出的断言（共 {len(group)} 条）："]
    for i, a in enumerate(group, 1):
        cond = _cond_text(a) or "（无，无条件）"
        act = a.get("action") or {}
        tgt = a.get("target") or {}
        lines.append(
            f"[{i}] kind={a.get('kind')}\n"
            f"    条件：{cond}\n"
            f"    动作：{act.get('text')}（verb={act.get('verb')}）\n"
            f"    标的：{tgt.get('text')}（scope={tgt.get('scope')}）\n"
            f"    理由：{a.get('rationale') or '（无）'}"
        )
    lines.append("\n请逐条给出 verdict。")
    return "\n".join(lines)


_DEC = json.JSONDecoder()


def _iter_json_objects(t: str):
    """逐个抠出文本里的 JSON 对象，容忍模型输出多个对象/夹杂说明文字。"""
    i = 0
    while True:
        j = t.find("{", i)
        if j < 0:
            return
        try:
            obj, end = _DEC.raw_decode(t, j)
        except ValueError:
            i = j + 1
            continue
        yield obj
        i = end


def parse_verdicts(text: str, n: int) -> tuple:
    """抠 JSON 并做结构校验。返回 (verdicts_by_idx, error)。

    必须逐对象扫而不是 ``find("{")`` 到 ``rfind("}")`` 一把切：
    14B 有 38/87 段会输出两个 JSON 对象（先给一版、又补一版），
    一把切会拼成 ``{...}{...}`` → ``Extra data``，整段判失败。
    """
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[A-Za-z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)

    merged, seen_obj = {}, False
    for obj in _iter_json_objects(t):
        items = obj.get("verdicts") if isinstance(obj, dict) else None
        if not isinstance(items, list):
            continue
        seen_obj = True
        for it in items:
            if not isinstance(it, dict):
                continue
            try:
                idx = int(it.get("idx"))
            except (TypeError, ValueError):
                continue
            v = (it.get("verdict") or "").strip().lower()
            if v not in _VERDICTS:
                v = "ok"
            issue = (it.get("issue") or "none").strip().lower()
            if issue not in _ISSUES:
                issue = "none"
            # 后出现的覆盖先出现的：模型第二版通常是补丁
            merged[idx] = {
                "verdict": v,
                "issue": issue,
                "src": (it.get("src") or "").strip()[:120],
                "reason": (it.get("reason") or "").strip()[:200],
                "fix": it.get("fix"),
            }
    if not seen_obj:
        return {}, "no_verdicts_object"
    got = set(merged)
    if set(range(1, n + 1)) - got:
        return merged, f"missing_idx:{sorted(set(range(1, n + 1)) - got)}"
    out = {k: v for k, v in merged.items() if 1 <= k <= n}
    for v in out.values():
        _coerce(v)
    return out, None


# issue 非 none 却给 verdict=ok，是 14B 最常见的自相矛盾形态
# （实测 #59：issue 写 not_supported，verdict 却写 ok，理由栏还自己把矛盾说清了）。
# 不许信 verdict 那一栏，以 issue 为准。
_DROP_ISSUES = {"polarity", "not_supported", "vacuous", "ephemeral"}


def _coerce(v: dict) -> None:
    """按 issue 纠正 verdict，纠正就打 _coerced 标，报告里单独统计。"""
    issue, verdict = v.get("issue"), v.get("verdict")
    if issue == "none" or verdict != "ok":
        if issue == "none" and verdict == "drop":
            v["_issue_missing"] = True
        return
    v["_coerced"] = True
    v["verdict"] = "drop" if issue in _DROP_ISSUES else "fix"


def _norm_cn(s: str) -> str:
    return re.sub(r"[\s，。：；！？、,.:;!?（）()「」『』\"']", "", s or "")


def _apply_mech(a: dict) -> None:
    """机械可判的一类 vacuous：条件与动作**完全同一句话**。

    实测依据：asrt_1df2c4b138a7（条件"随意换股" / 动作"随意换股" / 理由"不要随意换股"）
    这种写法把条件抄进动作，模型判了 ok。这条机械判比 14B 准。

    **不要再加"空壳动作"机械规则**（2026-11-06 踩过）
    ---------------------------------------------
    第一版复用了 precheck 的 ``is_shell()``（"action.text 里有没有动作动词"），
    一次 drop 掉 57 条，占全量 35%。抽查全是误杀：
      · "把『股价高=风险大』当成风险判断标准" —— 合格的 avoid 动作，is_shell 不认识"当成"
      · "五分钟级别已完成转折" —— judge 族断言的正常宾语
      · "市场可能的演化路径" —— 这条确实空，但它和上面两类混在一起分不开
    根因：``is_shell`` 是给 precheck 的 R4 用的，服务的是"动作必须动词开头"这条
    形式铁律；verify 阶段的判断标准是"意思对不对"，judge 族本来就允许"识别X"这种
    非动词短语。**同一判据换个语境就失效，不能搬。** 空壳留给模型 + 人工。
    """
    if a["verify"].get("verdict") == "drop":
        return
    atext = ((a.get("action") or {}).get("text") or "").strip()
    ctext = _cond_text(a).strip()
    if not atext:
        return

    same = ctext and _norm_cn(ctext) == _norm_cn(atext)
    near = ctext and not same and difflib.SequenceMatcher(
        None, _norm_cn(ctext), _norm_cn(atext)).ratio() >= 0.9
    if not (same or near):
        return

    # 只对**禁止类**成立：条件"随意换股" + 动作"避免随意换股"，条件没有附加任何信息，
    # 等于"无条件禁止换股"，是真空转。
    # 买卖类不适用：条件"卖在高潮末期" + 动作"卖在高潮末期"读作"在高潮末期卖"，
    # 动作本身（卖）是有信息的，人工审对这类判 fix 不判 bad。实测误杀 2 条，据此收窄。
    verb = ((a.get("action") or {}).get("verb") or "").strip()
    if verb not in ("avoid", "prohibit"):
        return
    a["verify"].update(
        verdict="drop", issue="vacuous",
        _mech="condition_eq_action" if same else "condition_near_action",
        reason=f"条件与动作同为「{atext}」，禁止类断言的条件没提供额外信息")


def verify_group(unit_id: str, unit_text: str, group: list) -> dict:
    """校验一段原文下的全部断言。

    解析失败会重试一次（同一 prompt、再降一点温度也无空间，靠重新采样）。
    理由：2026-11-06 全量首跑 38/87 段解析失败，全是模型输出格式问题而非内容问题，
    直接判 unknown 会让 99 条断言没有结论，等于这一层白跑。重试成本可忽略。
    """
    user = build_user(unit_text, group)
    t0 = time.monotonic()
    out = {"unit_id": unit_id, "n": len(group), "verdicts": {}, "error": None,
           "cost": 0.0, "tier": None, "retries": 0, "elapsed_s": 0.0}
    last_raw = ""
    for attempt in range(2):
        try:
            r = R.generate(user, system=SYSTEM_PROMPT, temperature=0.0,
                           max_tokens=900 + 260 * len(group), purpose="assertion_verify")
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"llm_exception:{exc}"
            out["elapsed_s"] = round(time.monotonic() - t0, 2)
            return out

        out["cost"] += r.get("cost_cny") or 0.0
        out["tier"] = r.get("tier")
        if not r.get("ok"):
            out["error"] = "llm_failed:" + str([a.get("status") for a in r.get("attempts", [])])
            out["elapsed_s"] = round(time.monotonic() - t0, 2)
            return out

        last_raw = r.get("text") or ""
        vs, err = parse_verdicts(last_raw, len(group))
        out["verdicts"] = vs
        out["error"] = err
        if not err and len(vs) == len(group):
            out["error"] = None
            break
        out["retries"] = attempt + 1

    out["elapsed_s"] = round(time.monotonic() - t0, 2)
    if out["error"]:
        out["raw"] = last_raw[:600]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="断言忠实度校验")
    ap.add_argument("--in", dest="inp", default=str(DEF_IN))
    ap.add_argument("--project", default=DEFAULT_PROJECT)
    ap.add_argument("--source", default="",
                    help="可选：源件 md 路径。多博主语料（8博主）必须给")
    ap.add_argument("--units", default="",
                    help="可选：显式指定单元 jsonl。留空则用 units_v2 现场重建（推荐，ID 与抽取器一致）")
    ap.add_argument("--out-dir", default=str(VERIFY_DIR))
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0, help="最多校验多少个原文段，0=全部")
    ap.add_argument("--only-ids", default="", help="逗号分隔 assertion_id，只校验这些")
    ap.add_argument("--only-reviewed", action="store_true",
                    help="只校验 precheck_all/reviewed.jsonl 里人工已审的条目")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args(argv)

    rows = load_jsonl(Path(args.inp))
    units = load_units(Path(args.units)) if args.units else {}
    if not units:
        units = build_units_map(args.project, args.source)
    if not units:
        raise SystemExit(f"[fatal] 单元表为空：units_v2 重建失败，或 --units 指向无效文件（{args.project}）")

    if args.only_ids:
        keep = {x.strip() for x in args.only_ids.split(",") if x.strip()}
        rows = [r for r in rows if r["assertion_id"] in keep]
    if args.only_reviewed:
        rv_path = OUT_DIR / "precheck_all" / "reviewed.jsonl"
        keep = {r["assertion_id"] for r in load_jsonl(rv_path) if (r.get("review") or {}).get("verdict")}
        rows = [r for r in rows if r["assertion_id"] in keep]

    # 按 unit_id 分组，缺原文的单独记账
    groups, missing = {}, []
    for r in rows:
        uid = (r.get("evidence") or {}).get("unit_id")
        u = units.get(uid)
        if not u or not (u.get("claim") or "").strip():
            missing.append(r["assertion_id"])
            continue
        groups.setdefault(uid, []).append(r)

    print(f"[in] 断言 {len(rows)} 条 → 原文段 {len(groups)} 个，缺原文 {len(missing)} 条")
    if missing:
        print(f"     缺原文示例: {missing[:5]}")
    if args.dry:
        uid0 = next(iter(groups))
        print("--- 首组 prompt 预览 ---")
        print(build_user(units[uid0]["claim"], groups[uid0])[:1200])
        print("[dry] 未调模型")
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    tasks = list(groups.items())
    if args.limit and args.limit > 0:
        tasks = tasks[:args.limit]

    t0 = time.monotonic()
    results = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(verify_group, uid, units[uid]["claim"], grp): uid
                for uid, grp in tasks}
        done = 0
        for fu in as_completed(futs):
            done += 1
            r = fu.result()
            results[r["unit_id"]] = r
            if done % 10 == 0 or done == len(tasks):
                print(f"  [{done}/{len(tasks)}] 已完成", flush=True)

    # 合并回断言
    merged, errs = [], []
    for uid, grp in tasks:
        r = results.get(uid) or {}
        if r.get("error") and not r.get("verdicts"):
            errs.append({"unit_id": uid, "error": r.get("error")})
            for a in grp:
                merged.append(dict(a, verify={"verdict": "unknown", "issue": "none",
                                              "reason": r.get("error") or "", "fix": None}))
            continue
        for i, a in enumerate(grp, 1):
            v = (r.get("verdicts") or {}).get(i) or {
                "verdict": "unknown", "issue": "none", "reason": "模型未给该条结论", "fix": None}
            rec = dict(a, verify=v)
            _apply_mech(rec)
            merged.append(rec)
        if r.get("error"):
            errs.append({"unit_id": uid, "error": r["error"]})

    cost = sum((r.get("cost") or 0) for r in results.values())
    elapsed = time.monotonic() - t0

    keep_rows = [a for a in merged if a["verify"]["verdict"] in ("ok", "fix")]
    dropped = [a for a in merged if a["verify"]["verdict"] == "drop"]
    fixes = [a for a in merged if a["verify"]["verdict"] == "fix"]
    unknown = [a for a in merged if a["verify"]["verdict"] == "unknown"]

    def dump(name, rows):
        p = Path(args.out_dir) / name
        with io.open(p, "w", encoding="utf-8") as f:
            for a in rows:
                f.write(json.dumps(a, ensure_ascii=False) + "\n")
        return p

    p_all = dump("verified.jsonl", merged)
    p_drop = dump("dropped.jsonl", dropped)
    p_fix = dump("fix_suggested.jsonl", fixes)

    print()
    print("=" * 64)
    print(f"校验断言       {len(merged)} 条（{len(tasks)} 段原文）")
    print(f"ok             {len([a for a in merged if a['verify']['verdict']=='ok'])} 条")
    print(f"fix            {len(fixes)} 条（有修正建议，原样可用但更弱）")
    print(f"drop           {len(dropped)} 条（{len(dropped)/max(len(merged),1)*100:.1f}%）")
    print(f"unknown        {len(unknown)} 条（调用失败/解析失败）")
    print(f"成本           ¥{cost:.4f}　耗时 {elapsed:.1f}s")
    if merged:
        from collections import Counter
        print(f"issue 分布     {dict(Counter(a['verify']['issue'] for a in merged))}")
        nm = len([a for a in merged if a["verify"].get("_mech")])
        nc = len([a for a in merged if a["verify"].get("_coerced")])
        print(f"机械补判       {nm} 条（同义反复/空壳动作）　issue→verdict 纠正 {nc} 条")
    if errs:
        print(f"\n失败段 {len(errs)} 个，前 3：")
        for e in errs[:3]:
            print(f"  · {e['unit_id']}: {e['error']}")
    print("=" * 64)
    print(f"→ {p_all}")
    print(f"→ {p_drop}")
    print(f"→ {p_fix}")

    # 有黄金标注时顺手算准确率（不限于 --only-reviewed：跑全量时 reviewed 的子集也能对上）
    rv_path = OUT_DIR / "precheck_all" / "reviewed.jsonl"
    if rv_path.exists():
        rv = {r["assertion_id"]: (r.get("review") or {}).get("verdict")
              for r in load_jsonl(rv_path)}
        cm = {"ok": "ok", "fix": "fix", "bad": "drop"}
        tp = fp = fn = tn = 0
        n_asfix = 0
        hits = []
        for a in merged:
            gold = rv.get(a["assertion_id"])
            g = cm.get(gold)
            if g is None:
                continue
            pred = a["verify"]["verdict"]
            if g == "drop":
                # 判 fix 也算抓到：fix 同样"不许原样进图"，只是修正成本比 drop 低
                if pred in ("drop", "fix"):
                    tp += 1
                    if pred == "fix":
                        n_asfix += 1
                    else:
                        pass
                else:
                    fn += 1
                    hits.append(("漏", a, gold, pred))
            elif pred == "drop":
                fp += 1
                hits.append(("误杀", a, gold, pred))
            else:
                tn += 1
        if tp + fn + fp + tn:
            print()
            print("对照人工审（fix 并入 ok 作为 gold；判 fix/drop 都算抓到 bad）")
            print(f"  真 bad 抓到 {tp} 条（其中判 fix {n_asfix} 条），漏 {fn} 条"
                  f"　→ 召回 {tp/max(tp+fn,1)*100:.0f}%")
            print(f"  好条误杀 {fp} 条，放过 {tn} 条　→ 误杀率 {fp/max(fp+tn,1)*100:.0f}%")
            for tag, a, gold, pred in hits[:12]:
                print(f"    [{tag}] 人工={gold} verify={pred} {a['assertion_id']}"
                      f" | {a['action']['text']} | {(a['verify'].get('reason') or '')[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
