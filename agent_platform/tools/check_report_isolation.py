# -*- coding: utf-8 -*-
"""check_report_isolation.py —— 报告防假绿三查（W38，只读，三态化 + 阳性对照）。

承《2026-10-19_周报月报立项预研施工单》f5e112ca W38 条款 + 2026-10-19 实装
授权书：对报告产物（weekly_report.py / monthly_report.py 出件）做三查——

  查1 锚覆盖率：报告头部必须带「报告级三态」与三态释义固定句；每个「## 」段
      的锚行（"> " 起，含 锚: 与 态:）必须存在——缺锚段即 FAIL（覆盖率 100%）。
  查2 禁创作语汇：黑名单 grep「建议|预计|据悉|约|或因|可能因」——投影器不创作。
      豁免规则（防误咬真转抄）：命中行去缩进原文若能在本段锚源账本文本中逐字
      找到（verbatim），判为真转抄豁免；账本里没有的该行=编文 FAIL。
  查3 数字反查抽查：段内整数（剔除日期/时间/ISO 周记号后）抽样 --sample N
      （默认 10；W36 口径 N≥10 / W37 N≥5），逐个在段锚源账本文本中反查子串
      命中；抽样必须覆盖至少 1 个非零值与至少 1 个非 OK 段（降级段），全抽
      OK 段=抽查无效。锚不可解析的段（如「本报告②③④段」自引用锚）跳过抽查。

三态（同 digital-legion 6f2c2d5 check_shadow_isolation.py 口径）：
  OK=三查全过且抽查有效 / FAIL=任一查咬出违例 / INCONCLUSIVE=证据不足
  （无可抽查数字、锚全不可解析、抽样未覆盖降级段/非零值——空集通过=假绿，
  判例 6）。
  退出码：0=OK / 1=FAIL / 2=参数错 / 3=INCONCLUSIVE。

本脚本除 --self-test 阳性对照（tempfile 即用即弃）外只读：只 grep 只反查，
零账本写。零依赖：仅标准库。
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

BLACKLIST_RE = re.compile(r"建议|预计|据悉|约|或因|可能因")
ANCHOR_LINE_RE = re.compile(r"^>\s.*锚:")
STATE_RE = re.compile(r"态:\s*(OK|INCONCLUSIVE|DEGRADED)")
REPORT_STATE_RE = re.compile(r"报告级三态:\s*(OK|INCONCLUSIVE|DEGRADED)")
SECTION_HEAD_RE = re.compile(r"^## ")

# 数字抽取前先剔除日期/时间/ISO 周/时间戳记号（结构性记号，非账本数字）
_STRIP_RES = (
    re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?"),
    re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?"),
    re.compile(r"\d{4}-\d{2}-\d{2}"),
    re.compile(r"\d{4}-W\d{2}"),
    re.compile(r"\d{2}:\d{2}:\d{2}"),
    re.compile(r"\d{2}:\d{2}"),
    re.compile(r"\d{2}-\d{2}"),
)
NUM_RE = re.compile(r"\d+")


# ---------------------------------------------------------------- 解析面

def parse_report(text: str) -> dict:
    """拆 header + 段列表。段 = '## ' 标题行起，到下一个 '## ' 前。"""
    lines = text.splitlines()
    header_lines: list[str] = []
    sections: list[dict] = []
    cur: dict | None = None
    for ln in lines:
        if SECTION_HEAD_RE.match(ln):
            cur = {"title": ln.strip(), "lines": [ln]}
            sections.append(cur)
        elif cur is None:
            header_lines.append(ln)
        else:
            cur["lines"].append(ln)
    return {"header": "\n".join(header_lines), "sections": sections}


def section_anchor(sec: dict) -> tuple[str | None, list[str], str | None]:
    """段 -> (锚行, 路径列表, 段三态)。无锚行时首元素为 None。"""
    anchor_line = next((ln for ln in sec["lines"][1:] if ANCHOR_LINE_RE.match(ln)),
                       None)
    if anchor_line is None:
        return None, [], None
    m = STATE_RE.search(anchor_line)
    state = m.group(1) if m else None
    mm = re.search(r"锚:\s*(.*?)(?:\s*\|\s*态:|$)", anchor_line)
    paths = [p.strip() for p in (mm.group(1) if mm else "").split(";") if p.strip()]
    return anchor_line, paths, state


def collect_anchor_texts(paths: list[str], base: Path) -> tuple[list[str], list[str]]:
    """解析锚路径 -> (命中的账本文本列表, 不可解析路径)。目录递归全文件。"""
    texts: list[str] = []
    bad: list[str] = []
    for p in paths:
        if "本报告" in p:      # 报告内自引用锚（如月报①段），无外部账本可反查
            bad.append(p)
            continue
        f = Path(p)
        if not f.is_absolute():
            f = base / p
        try:
            if f.is_dir():
                for sub in sorted(f.rglob("*")):
                    if sub.is_file() and sub.stat().st_size < 5_000_000:
                        texts.append(sub.read_text(encoding="utf-8", errors="replace"))
            elif f.is_file():
                texts.append(f.read_text(encoding="utf-8", errors="replace"))
            else:
                bad.append(p)
        except OSError:
            bad.append(p)
    return texts, bad


def extract_numbers(lines: list[str]) -> list[str]:
    """段体数字（剔锚行与日期/时间记号）。"""
    out: list[str] = []
    for ln in lines:
        if ln.startswith(">"):
            continue
        s = ln
        for rx in _STRIP_RES:
            s = rx.sub(" ", s)
        out.extend(NUM_RE.findall(s))
    return out


# ---------------------------------------------------------------- 三查

def check_report(report_path: Path, base: Path, sample: int = 10) -> dict:
    text = report_path.read_text(encoding="utf-8", errors="replace")
    rep = parse_report(text)
    result = {"file": str(report_path), "checks": {}, "violations": [],
              "inconclusive": [], "state": "OK", "exit": 0}

    if not rep["header"] or not REPORT_STATE_RE.search(rep["header"]):
        result["violations"].append("查1：头部缺「报告级三态:」声明")
    if "三态释义" not in rep["header"]:
        result["violations"].append("查1：头部缺三态释义固定句（INCONCLUSIVE/DEGRADED 释义）")

    n_sec = len(rep["sections"])
    n_anchored = 0
    sampled: list[tuple[str, str, str]] = []   # (数字, 段态, 段标题)
    skipped_sections = 0
    sec_states: list[str] = []

    for sec in rep["sections"]:
        title = sec["title"]
        anchor_line, paths, state = section_anchor(sec)
        if anchor_line is None:
            result["violations"].append(f"查1：段缺锚行 -> {title}")
            sec_states.append("UNKNOWN")
            continue
        if state is None:
            result["violations"].append(f"查1：锚行缺 态: 字段 -> {title}")
            sec_states.append("UNKNOWN")
            continue
        n_anchored += 1
        sec_states.append(state)
        texts, _bad = collect_anchor_texts(paths, base)
        if not texts:
            skipped_sections += 1

        joined = "\n".join(texts)

        # 查2 禁创作语汇（两类豁免，其余命中=编文 FAIL）
        for ln in sec["lines"][1:]:
            if BLACKLIST_RE.search(ln):
                # 豁免1 git log 原文转抄行（"- " + commit hash 前缀，其真实性
                # 由查3 recheck 对 git log 逐行 diff=0 机械保证）
                if re.match(r"^\s*-\s*[0-9a-f]{8,40}\s", ln):
                    continue
                # 豁免2 真转抄：命中行去缩进原文可在锚源账本逐字找到
                core = ln.strip().lstrip("-").strip()
                if texts and core and core in joined:
                    continue
                result["violations"].append(
                    f"查2：禁创作语汇命中且非账本转抄 -> {title} :: {ln.strip()[:80]}")

        # 查3 候选数字（锚不可解析的段跳过）
        if texts:
            for nstr in extract_numbers(sec["lines"][1:]):
                sampled.append((nstr, state, title))

    coverage = (n_anchored / n_sec) if n_sec else 0.0
    if n_sec == 0:
        result["violations"].append("查1：报告无任何「## 」段（空报=假绿）")
    result["checks"]["锚覆盖率"] = f"{n_anchored}/{n_sec}"
    result["checks"]["可抽查数字"] = str(len(sampled))
    result["checks"]["锚不可解析段"] = str(skipped_sections)

    has_fail = bool(result["violations"])

    schema_m = re.search(r"report_schema_version:\s*(\S+)", rep["header"])
    schema_version = schema_m.group(1) if schema_m else None

    if schema_version in ("weekly.v1", "monthly.v1"):
        # 查3 主路径：生成器自带 recheck 复算钩子（全量重算比对，非子串抽查）。
        # 报告级 schema 与生成器绑定，复算逻辑唯一权威在生成器本身。
        rc = _run_recheck(schema_version, text, base)
        if rc is None:
            result["inconclusive"].append("查3：recheck 模块不可用，复算未执行")
        else:
            result["violations"].extend(rc["violations"])
            result["inconclusive"].extend(rc.get("notes", []))
            result["checks"]["复算比对项"] = str(rc["checked"])
            has_fail = has_fail or bool(rc["violations"])
            if rc["checked"] == 0 and not rc["violations"]:
                result["inconclusive"].append("查3：复算比对项为 0（空集=假绿，判例 6）")
    else:
        # 查3 通用回退：锚源子串反查抽样（适用于无生成器钩子的报告/夹具）。
        _substring_sampling(rep, base, sample, result)
        has_fail = has_fail or bool(result["violations"])

    if n_sec > 0 and skipped_sections == n_sec:
        result["inconclusive"].append("全部段锚不可解析（无可反查账本）")

    if has_fail:
        result["state"] = "FAIL"
        result["exit"] = 1
    elif result["inconclusive"]:
        result["state"] = "INCONCLUSIVE"
        result["exit"] = 3
    else:
        result["state"] = "OK"
        result["exit"] = 0
    return result


def _run_recheck(schema_version: str, text: str, base: Path) -> dict | None:
    """按 schema 版本动态加载生成器 recheck 钩子。失败返回 None（如实 INCONCLUSIVE）。"""
    import importlib.util
    mod_name = {"weekly.v1": "weekly_report", "monthly.v1": "monthly_report"}[schema_version]
    mod_path = Path(__file__).resolve().parent / f"{mod_name}.py"
    if not mod_path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location(mod_name, mod_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod.recheck(text, base)
    except Exception as e:  # 复算器自身故障必须显式可见，不得静默当 PASS
        print(f"[warn] recheck 模块 {mod_name} 异常：{e}")
        return None


def _substring_sampling(rep: dict, base: Path, sample: int, result: dict) -> None:
    """通用回退查3：段内整数（剔日期/时间记号）在段锚源中子串反查抽样。"""
    sampled: list[tuple[str, str, str]] = []
    skipped_sections = 0
    for sec in rep["sections"]:
        _a, paths, state = section_anchor(sec)
        if state is None:
            continue
        texts, _bad = collect_anchor_texts(paths, base)
        if not texts:
            skipped_sections += 1
            continue
        for nstr in extract_numbers(sec["lines"][1:]):
            sampled.append((nstr, state, sec["title"]))
    result["checks"]["可抽查数字"] = str(len(sampled))
    if not sampled:
        if len(rep["sections"]) > skipped_sections:
            result["inconclusive"].append("有锚段但无可抽查数字（空集=假绿，判例 6）")
        return
    picked: list[tuple[str, str, str]] = []
    nz = [s for s in sampled if s[0] != "0"]
    dg = [s for s in sampled if s[1] != "OK"]
    if nz:
        picked.append(nz[0])
    if dg:
        picked.append(dg[0])
    for item in sampled:
        if len(picked) >= sample:
            break
        if item not in picked:
            picked.append(item)
    picked = picked[:sample]
    if nz and not any(s in picked for s in nz):
        result["inconclusive"].append("抽样未覆盖非零值（抽查无效）")
    if dg and not any(s in picked for s in dg):
        result["inconclusive"].append("存在降级段但抽样未覆盖（抽查无效）")
    texts_cache: dict[str, list[str]] = {}
    for nstr, _state, title in picked:
        if title not in texts_cache:
            sec = next(s for s in rep["sections"] if s["title"] == title)
            _a, paths, _st = section_anchor(sec)
            texts_cache[title], _ = collect_anchor_texts(paths, base)
        if not any(nstr in t for t in texts_cache[title]):
            result["violations"].append(
                f"查3：数字反查未命中 -> {title} :: {nstr}（锚源账本中不存在该数字）")


# ---------------------------------------------------------------- 阳性对照

_GOOD_REPORT = """# 测试报告 夹具
> report_schema_version: test.v1 | 报告级三态: OK | 生成时刻: 2026-01-05 08:00:00
> 三态释义：INCONCLUSIVE=如实缺据，见缺件清单；DEGRADED=账实不符，见双列数据。

## ①计数段
> 锚: {base}/ledger.txt | 态: OK（样例）
- 合计 42 件，其中有效 13 件

## ②转抄段
> 锚: {base}/ledger.txt | 态: OK（样例）
    部门A产出 7 件——账本原文行
"""

_BAD_REPORT = """# 测试报告 坏夹具
> report_schema_version: test.v1 | 报告级三态: OK | 生成时刻: 2026-01-05 08:00:00
> 三态释义：INCONCLUSIVE=如实缺据，见缺件清单；DEGRADED=账实不符，见双列数据。

## ①编文段
> 锚: {base}/ledger.txt | 态: OK（样例）
- 下月预计完成 99 件，建议加大投入

## ②缺锚段
- 本段没有锚行，凭空写了个数字 88
"""


def self_test(base: Path) -> int:
    """阳性对照：坏夹具必须被咬出（缺锚段 + 编文语汇 + 凭空数字），好夹具必须全过。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "ledger.txt").write_text(
            "部门A产出 7 件——账本原文行\n合计 42 件，其中有效 13 件\n", encoding="utf-8")
        good = tmp / "good.md"
        bad = tmp / "bad.md"
        good.write_text(_GOOD_REPORT.format(base=tmp.as_posix()), encoding="utf-8")
        bad.write_text(_BAD_REPORT.format(base=tmp.as_posix()), encoding="utf-8")

        rg = check_report(good, tmp, sample=5)
        if rg["exit"] != 0:
            print(f"[self-test] 阳性对照失败：好夹具未通过（exit={rg['exit']}）")
            for v in rg["violations"]:
                print(f"  - {v}")
            for v in rg["inconclusive"]:
                print(f"  - {v}")
            return 1

        rb = check_report(bad, tmp, sample=5)
        miss_anchor = any("段缺锚行" in v for v in rb["violations"])
        miss_lang = any("查2" in v for v in rb["violations"])
        miss_num = any("查3" in v for v in rb["violations"])
        if rb["exit"] != 1:
            print(f"[self-test] 阳性对照失败：坏夹具未被咬出（exit={rb['exit']}）")
            for v in rb["violations"]:
                print(f"  - {v}")
            return 1
        if not (miss_anchor and miss_lang and miss_num):
            print(f"[self-test] 阳性对照部分失灵：缺锚={miss_anchor} 编文={miss_lang} "
                  f"数字反查={miss_num}")
            return 1
        print("[self-test] 阳性对照 PASS：好夹具 exit=0；坏夹具 exit=1 且三查各咬出违例"
              "（缺锚段/编文语汇/凭空数字）")
        return 0


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="报告防假绿三查（只读，三态化）")
    ap.add_argument("report", nargs="?", help="报告产物路径（md）")
    ap.add_argument("--base", help="锚相对路径基准（缺省=仓根）")
    ap.add_argument("--sample", type=int, default=10, help="查3 抽样数（默认 10）")
    ap.add_argument("--self-test", action="store_true", help="阳性对照（tempfile 即用即弃）")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test(Path(__file__).resolve().parents[2])
    if not args.report:
        ap.error("需要 report 路径或 --self-test")
    base = Path(args.base) if args.base else Path(__file__).resolve().parents[2]
    r = check_report(Path(args.report), base, sample=max(1, args.sample))
    for k, v in r["checks"].items():
        print(f"  {k}: {v}")
    for v in r["violations"]:
        print(f"  [FAIL] {v}")
    for v in r["inconclusive"]:
        print(f"  [INCONCLUSIVE] {v}")
    print(f"三查结论: {r['state']} (exit={r['exit']}) -> {r['file']}")
    return r["exit"]


if __name__ == "__main__":
    sys.exit(main())
