#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一条命令跑完档案侧的全部验收项，失败即非零退出。

为什么需要它：
    前几轮的验证都是我在会话里临时敲的——编译、构建幂等、渲染校验、判据自检，
    每次换个顺序、有时漏一项。漏掉的那次就会把缺陷放过去。
    固化成脚本后，改任何一处代码都能一键回到同一套验收口径。

覆盖：
    1) 语法：所有 .py 通过 py_compile
    2) 幂等：build_registry 连跑两次，除 generated_at 外输出完全一致
    3) 不变量：unique + alias == total；alias_of 必须指向存在的正本；别名不得指向别名
    4) 页面：build_html 后 render_check.cjs 真执行渲染，与 registry.json 比对
    5) 报告：check_titles --report-only 能从已有结果重出（不联网）
    6) 可选联网：check_titles --self-test（只发 2 个请求，验证判据有判别力）

用法：
    python verify_all.py               # 离线项（约 20 秒）
    python verify_all.py --network     # 另跑判据自检（联网，约 10 秒）
"""
from __future__ import annotations

import argparse
import json
import re
import py_compile
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
REGISTRY = BASE / "registry.json"
PY = sys.executable
NODE_FALLBACK = r"C:\Users\t\.workbuddy\binaries\node\versions\22.22.2-6\node.exe"

# 频率审计报告的分档表必须逐档齐全。少比任何一档，该档就能在报告里被随意改而无人报。
FREQ_MD_KINDS = (
    "频率低频但区间标实时",
    "档案与页面频率不一致",
    "档案未填可补齐",
    "抽取可疑需重抓",
    "未取到证据需重抓",
)

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def run(cmd: list[str], cwd: Path = BASE) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def load_registry() -> dict:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    payload["meta"].pop("generated_at", None)
    payload["meta"].pop("rendered_at", None)
    return payload


def check_compile() -> None:
    bad = []
    for path in sorted(BASE.glob("*.py")):
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            bad.append(f"{path.name}: {exc.msg}")
    record("语法编译", not bad, "; ".join(bad) if bad else f"{len(list(BASE.glob('*.py')))} 个脚本")


def check_idempotent() -> None:
    code, out = run([PY, "build_registry.py"])
    if code != 0:
        record("构建幂等", False, out.strip()[:200])
        return
    first = load_registry()
    code, out = run([PY, "build_registry.py"])
    if code != 0:
        record("构建幂等", False, out.strip()[:200])
        return
    second = load_registry()
    same = first == second
    record("构建幂等", same, "两次构建逐字段一致" if same else "两次构建结果不同（正本选择可能依赖了会变的内容）")


def check_invariants() -> None:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    meta, ds = payload["meta"], payload["datasets"]
    ids = {d["id"] for d in ds}
    aliases = [d for d in ds if d.get("alias_of")]
    problems = []

    if meta.get("unique_datasets", -1) + meta.get("alias_records", -1) != meta.get("total_records", -2):
        problems.append(f"unique({meta.get('unique_datasets')})+alias({meta.get('alias_records')})"
                        f" != total({meta.get('total_records')})")
    if len(aliases) != meta.get("alias_records"):
        problems.append(f"实际别名字段 {len(aliases)} 条 ≠ meta.alias_records {meta.get('alias_records')}")
    dangling = [d["id"] for d in aliases if d["alias_of"] not in ids]
    if dangling:
        problems.append(f"alias_of 指向不存在的 id: {dangling[:3]}")
    chained = [d["id"] for d in aliases if any(
        o["id"] == d["alias_of"] and o.get("alias_of") for o in aliases)]
    if chained:
        problems.append(f"别名指向了另一个别名（链式）: {chained[:3]}")
    self_ref = [d["id"] for d in aliases if d["alias_of"] == d["id"]]
    if self_ref:
        problems.append(f"自指: {self_ref[:3]}")
    # 同一正本下不应出现两套不同优先级（说明分诊不一致）
    by_canon: dict[str, set] = {}
    for d in ds:
        if d.get("alias_of"):
            by_canon.setdefault(d["alias_of"], set()).add(d.get("priority"))
    conflict = {k: v for k, v in by_canon.items() if len(v) > 1}
    if conflict:
        problems.append(f"正本与别名优先级不一致 {len(conflict)} 组（需人判）")

    # 新鲜度证据层：stale 必须有页面原文与滞后月数，且月数确实 >18
    flagged = [d for d in ds if d.get("freshness_flag")]
    ALLOWED = {"ok", "lag", "stale", "page_unlabeled", "conflict"}
    bad_flag = [d["id"] for d in flagged if d["freshness_flag"] not in ALLOWED]
    if bad_flag:
        problems.append(f"未知 freshness_flag: {bad_flag[:3]}")
    if meta.get("freshness_checked") != len(flagged):
        problems.append(f"meta.freshness_checked({meta.get('freshness_checked')}) != 实际 {len(flagged)}")
    if sum((meta.get("by_freshness") or {}).values()) != len(flagged):
        problems.append("by_freshness 合计与实际条数不符")
    stale = [d for d in flagged if d["freshness_flag"] == "stale"]
    for d in stale:
        if not str(d.get("coverage_page") or "").strip():
            problems.append(f"stale 缺页面原文: {d['id']}")
        if not isinstance(d.get("freshness_lag_months"), int) or d["freshness_lag_months"] <= 18:
            problems.append(f"stale 的滞后月数异常: {d['id']}={d.get('freshness_lag_months')}")
    # lag 必须落在 7~18：闸门只挡 stale>18 时，41 个月标成 lag 也能混过去
    for d in [x for x in flagged if x["freshness_flag"] == "lag"]:
        lag = d.get("freshness_lag_months")
        if not isinstance(lag, int) or not 7 <= lag <= 18:
            problems.append(f"lag 的滞后月数越界: {d['id']}={lag}（应 7~18）")

    # 证据源在就必须挂满：否则证据文件整个丢失时，所有 freshness 不变量会"真空成立"
    ev = BASE / "_coverage_audit.json"
    lock = BASE / "_coverage_audit.lock.json"
    if lock.is_file():
        # 锁是外部锚点：没有它，删掉证据文件就等于"没有记录就没有违规"，检查会假绿。
        import hashlib

        lk = json.loads(lock.read_text(encoding="utf-8"))
        if not ev.is_file():
            problems.append("证据文件 _coverage_audit.json 丢失（锁仍在），新鲜度标记已失效")
        else:
            digest = hashlib.sha256(ev.read_bytes()).hexdigest()
            if digest != lk.get("sha256"):
                problems.append("证据文件 sha256 与锁不符（结果被改过，需重跑审计并重新加锁）")
            if meta.get("freshness_checked") != lk.get("rows"):
                problems.append(f"挂载条数 {meta.get('freshness_checked')} != 锁记录 {lk.get('rows')}")
    elif ev.is_file():
        problems.append("证据文件存在但缺锁，无法防止其被静默删除/改写；请跑 coverage_audit.py --lock-only")

    if ev.is_file():
        ev_rows = json.loads(ev.read_text(encoding="utf-8")).get("rows", [])
        if meta.get("freshness_checked", 0) < len(ev_rows):
            problems.append(f"证据源有 {len(ev_rows)} 条，实际只挂 {meta.get('freshness_checked')} 条"
                            "（id 改名或匹配失败会让证据无声消失）")
        # 档案记载必须与审计抄录一致，防止两边各改各的
        by_id = {d["id"]: d for d in ds}
        drift = [r["id"] for r in ev_rows
                 if r.get("id") in by_id and str(by_id[r["id"]].get("coverage", "")).strip()
                 != str(r.get("record", "")).strip()]
        if drift:
            problems.append(f"档案 coverage 与审计抄录不一致 {len(drift)} 条: {drift[:3]}")

    # 频率证据层：档案 frequency 也必须与审计抄录一致。
    # 只查 coverage 不查 frequency 时，把分片里某条的 frequency 从「年度」改成「日」，
    # 档案会同时写着 frequency=日 和 freq_conflict=频率低频但区间标实时（严重自相矛盾），
    # 而审计里 freq_record 仍是「年度」——两处口径打架却全绿。
    fev_file = BASE / "_freq_audit.json"
    if fev_file.is_file():
        by_id_f = {d["id"]: d for d in ds}
        frows = json.loads(fev_file.read_text(encoding="utf-8")).get("rows", [])
        fdrift = [r["id"] for r in frows
                  if str(r.get("freq_record") or "").strip()
                  and r.get("id") in by_id_f
                  and str(by_id_f[r["id"]].get("frequency", "")).strip()
                  != str(r.get("freq_record") or "").strip()]
        if fdrift:
            problems.append(f"档案 frequency 与频率审计抄录不一致 {len(fdrift)} 条: {fdrift[:3]}")

    # 频率证据层：同样受哈希锁保护，且标记必须带页面原文
    freq_flagged = [d for d in ds if d.get("freq_conflict")]
    FREQ_KINDS = {"频率低频但区间标实时", "档案与页面频率不一致",
                  "档案未填可补齐", "抽取可疑需重抓"}
    bad_kind = [d["id"] for d in freq_flagged if d["freq_conflict"] not in FREQ_KINDS]
    if bad_kind:
        problems.append(f"未知 freq_conflict: {bad_kind[:3]}")
    if meta.get("freq_checked") != len(freq_flagged):
        problems.append(f"meta.freq_checked({meta.get('freq_checked')}) != 实际 {len(freq_flagged)}")
    for d in freq_flagged:
        if not str(d.get("freq_page") or "").strip():
            problems.append(f"频率标记缺页面原文: {d['id']}")
        if "频率低频但区间标实时" == d["freq_conflict"] and not str(d.get("freq_note") or "").strip():
            problems.append(f"频率矛盾未带时间区间证据: {d['id']}")
    fev = BASE / "_freq_audit.json"
    flock = BASE / "_freq_audit.lock.json"
    rows_ev: list[dict] = []
    if fev.is_file():
        rows_ev = json.loads(fev.read_text(encoding="utf-8")).get("rows", [])
        # 纯 JSON 内部不变量：**不依赖报告文件、也不依赖锁**。
        # `freq_page` 为空只有两类来源，性质完全不同，不能混算：
        #   未取到证据需重抓  页面抓到了但没抽到频率 —— 是结论，必须单独成档
        #   取页失败          页面根本没抓到       —— 没有结论，也不能算一致
        # 所以恒等式是「这两类相加 == freq_page 为空」。只写 n_noev == n_empty_page 时，
        # 联网审计一出现失败页就会天天假阳，跑几次之后人就学会忽略这条闸门了。
        # 为什么单列这条：把无证据那一档的 conflict 全部置空（伪装成「与档案一致」）
        # 再重锁，哈希锁自洽、分档计数照样能对上 registry，闸门会全绿——但那些记录
        # 已经被静默当成「有结论」。这条直接抓「没证据被当成有结论」，绕不过去。
        n_noev = sum(1 for r in rows_ev if r.get("conflict") == "未取到证据需重抓")
        n_failed = sum(1 for r in rows_ev if r.get("conflict") == "取页失败")
        n_empty_page = sum(1 for r in rows_ev if not str(r.get("freq_page") or "").strip())
        if n_noev + n_failed != n_empty_page:
            problems.append(f"未取到证据档 {n_noev} 条 + 取页失败 {n_failed} 条"
                            f" != freq_page 为空的 {n_empty_page} 条（该档可能被旧版脚本回退）")
    if flock.is_file():
        import hashlib

        flk = json.loads(flock.read_text(encoding="utf-8"))
        if not fev.is_file():
            problems.append("证据文件 _freq_audit.json 丢失（锁仍在），频率标记已失效")
        else:
            if hashlib.sha256(fev.read_bytes()).hexdigest() != flk.get("sha256"):
                problems.append("_freq_audit.json sha256 与锁不符（需重跑审计并重新加锁）")
            expect = sum(1 for r in rows_ev if r.get("conflict") in FREQ_KINDS)
            if len(freq_flagged) != expect:
                problems.append(f"频率标记 {len(freq_flagged)} 条 != 审计结论 {expect} 条（id 改名会静默丢证据）")
            # 冲突条数必须与审计一致，防止只标一部分还报全绿
            want = sum(1 for r in rows_ev if r.get("conflict") == "频率低频但区间标实时")
            got = sum(1 for d in freq_flagged if d["freq_conflict"] == "频率低频但区间标实时")
            if want != got:
                problems.append(f"频率待确认标记 {got} 条 != 审计 {want} 条")
            # 交叉校验：人读报告与机读结果必须同源。哈希锁只能防"改完没重新加锁"，
            # 防不了"改完重锁"；但改 JSON 不会同步改 md，两条路径对齐就露了。
            # 必须**逐档**比五个数字：只比「频率低频但区间标实时」一个数字时，
            # 另外四档（尤其 74 条「未取到证据需重抓」）在报告里怎么改都无人报。
            md = BASE / "频率口径审计.md"
            if md.is_file():
                md_text = md.read_text(encoding="utf-8")
                actual = {k: sum(1 for r in rows_ev if r.get("conflict") == k) for k in FREQ_MD_KINDS}
                for kind in FREQ_MD_KINDS:
                    # 只认「| 分档名 | 数字 |」这种带竖线的表格行：同一档名还会以
                    # 「## 未取到证据需重抓（74 条）」小节标题的形式出现，那不是表格行，
                    # 用不带锚点的裸匹配会先撞上标题、把小节条目数当成分档计数。
                    m = re.search(rf"^\|\s*{re.escape(kind)}\s*\|\s*(\d+)\s*\|", md_text, re.MULTILINE)
                    if not m:
                        problems.append(f"报告缺分档「{kind}」的计数行")
                    elif int(m.group(1)) != actual[kind]:
                        problems.append(f"报告分档「{kind}」写 {m.group(1)} 条，"
                                        f"JSON 里是 {actual[kind]} 条（两者必须一致）")
            else:
                # 文件被改名/删除时不能静默跳过：跳过等于"没有报告就没有违规"，交叉校验真空成立。
                problems.append(f"频率审计报告缺失（{md.name}），无法与 JSON 交叉校验")
    elif fev.is_file():
        problems.append("_freq_audit.json 存在但缺锁，无法防止被静默改写")

    # 唯一数据集口径：别让下游拿条目数当"有多少份数据停更"
    canon_flagged = [d for d in flagged if not d.get("alias_of")]
    if sum((meta.get("by_freshness_canonical") or {}).values()) != len(canon_flagged):
        problems.append("by_freshness_canonical 合计与正本条数不符")
    # 频率口径同样要给唯一数据集口径，否则别名会把"待确认有多少份数据"算大
    canon_freq = [d for d in freq_flagged if not d.get("alias_of")]
    if sum((meta.get("by_freq_conflict_canonical") or {}).values()) != len(canon_freq):
        problems.append(f"by_freq_conflict_canonical 合计({sum((meta.get('by_freq_conflict_canonical') or {}).values())})"
                        f" 与带 freq_conflict 的非别名记录数({len(canon_freq)})不符")
    if (meta.get("by_freshness_canonical") or {}).get("stale", 0) > (meta.get("by_freshness") or {}).get("stale", 0):
        problems.append("正本 stale 不应多于条目 stale")
    # 证据层不得改写原始记载：stale 记录仍应写着「至今」
    rewritten = [d["id"] for d in stale if "至今" not in str(d.get("coverage", ""))]
    if rewritten:
        problems.append(f"stale 记录的原 coverage 被改写: {rewritten[:3]}")

    record("档案不变量", not problems, "; ".join(problems) if problems else
           f"total {meta['total_records']} = unique {meta['unique_datasets']} + alias {meta['alias_records']}"
           f"，新鲜度已核 {len(flagged)} 条（stale {len(stale)}）")


def check_render() -> None:
    node = shutil.which("node") or (NODE_FALLBACK if Path(NODE_FALLBACK).is_file() else None)
    if node is None:
        record("页面渲染", False, "找不到 node，无法真执行渲染校验")
        return
    code, out = run([PY, "build_html.py"])
    if code != 0:
        record("页面渲染", False, out.strip()[:200])
        return
    code, out = run([node, "render_check.cjs", "档案总览.html"])
    record("页面渲染", code == 0, out.strip().splitlines()[-1] if out.strip() else "")


def check_report_only() -> None:
    if not (BASE / "_title_health.json").is_file():
        record("报告可重出", False, "缺 _title_health.json，先跑 check_titles.py --all")
        return
    code, out = run([PY, "check_titles.py", "--report-only"])
    ok = code == 0 and (BASE / "标题校验报告.md").is_file()
    record("报告可重出", ok, "无网络也能从已有结果重出报告" if ok else out.strip()[:200])


def check_self_test() -> None:
    code, out = run([PY, "check_titles.py", "--self-test"])
    tail = [ln for ln in out.strip().splitlines() if "自检" in ln or "错配" in ln]
    record("判据判别力自检", code == 0, " / ".join(tail[-2:]) if tail else "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", action="store_true", help="另跑联网的判据自检")
    args = ap.parse_args()

    print(f"验收目录：{BASE}\n")
    check_compile()
    check_idempotent()
    check_invariants()
    check_render()
    check_report_only()
    if args.network:
        check_self_test()

    failed = [r for r in results if not r[1]]
    print(f"\n{'=' * 60}\n{len(results) - len(failed)}/{len(results)} 项通过")
    if failed:
        print("未通过：")
        for name, _, detail in failed:
            print(f"  - {name}: {detail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
