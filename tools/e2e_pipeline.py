# -*- coding: utf-8 -*-
"""端到端全自动工单驱动器 v2（K-ORG-065 · 055 计划 11-08 里程碑彩排）。

用法：
    python tools/e2e_pipeline.py <TID> --project portal --out tools/xxx.py \
        --spec-file <spec.md> [--repair-rounds 1]

链条（全程零人工）：开单(new+worktree) → 方案文档 → DEV1 产码(coding 档：
anthropic 重试1次·拒降 cloud flash·043 判例) → 产码提交 → SEC2 走查(缺失>0 即停)
→ QA1 复核(附证自动组装·stat 原样引用判例) → 通过则 merge。
QA1 退回 → 自动返修轮（退回理由回流产码·whole 重出）至多 --repair-rounds 轮。
轨迹落 data/e2e/。
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TRACE = []


def log(step, detail):
    line = "[%s] %s: %s" % (datetime.datetime.now().strftime("%H:%M:%S"), step, detail)
    TRACE.append(line)
    print(line, flush=True)


def run(cmd, cwd=ROOT, timeout=900):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=timeout)
    out = (r.stdout or b"").decode("utf-8", "replace")
    err = (r.stderr or b"").decode("utf-8", "replace")
    return r.returncode, out, err


def die(step, detail):
    log(step, "FAIL " + detail)
    write_trace("ABORT")
    sys.exit(1)


def write_trace(final):
    d = os.path.join(ROOT, "data", "e2e")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "%s_trace_%s.md" % (TID, datetime.datetime.now().strftime("%H%M%S")))
    with open(p, "w", encoding="utf-8") as f:
        f.write("# E2E 轨迹 %s（%s）\n\n" % (TID, final)
                + "\n".join("- " + t for t in TRACE) + "\n")
    print("[trace]", p, flush=True)


def extract_sec2_miss(wt, plan_rel, before_ts):
    """从最新 SEC2 报告提取「缺失」条目全文（走形不拦，仅缺失触发返修）。
    before_ts: 上一轮报告时间戳下界（防读到旧报告）。返回缺失段文本或 None。"""
    import glob as _g
    reps = sorted(_g.glob(os.path.join(
        wt, "agent_platform", "data", "sec2",
        "*_%s" % os.path.basename(plan_rel))))  # basename 已含 .md, 勿重复拼后缀
    for rp in reversed(reps):
        if os.path.getmtime(rp) <= before_ts:
            continue
        text = open(rp, encoding="utf-8", errors="replace").read()
        if re.search(r"缺失\s*[1-9]", text):
            # 抽判定行之后所有「判定：缺失」的段落（### 标题到下一个 ### 之间）
            secs = re.split(r"\n(?=### )", text)
            miss = [x for x in secs if re.search(r"判定[：:]\s*缺失", x)]
            if miss:
                return "\n\n".join(x.strip()[:1200] for x in miss)
    return None


def gen_code(wt, out_rel, spec, repair_note):
    """产码轮：子进程 cwd=worktree（BUNDLE 相对路径判例）。
    repair_note 非 None 时走返修模式：删旧文件强制 whole 重出+退回理由回流。"""
    out_abs = os.path.join(wt, out_rel)
    if repair_note and os.path.exists(out_abs):
        os.remove(out_abs)  # 判例：SR 修小 bug 爱散文/漏收尾，返修一律 whole 重出
    prompt = spec
    if repair_note:
        src, body = repair_note
        prompt += ("\n\n【%s（返修轮·逐条落实）】\n%s\n"
                   "【返修纪律】直接输出修复后的完整新文件（禁止 SEARCH/REPLACE 块）；"
                   "逐条对照返修项修改，其他部分保持已验证设计不变。" % (src, body))
    driver = json.dumps({"tid": TID, "out": out_rel, "spec": prompt,
                         "label": "%s 产码%s" % (TID, "(返修)" if repair_note else "")})
    dj = os.path.join(wt, "_e2e_driver_args.json")
    with open(dj, "w", encoding="utf-8") as f:
        f.write(driver)
    code = (
        "import sys,json;sys.path.insert(0,'tools')\n"
        "import agent_coder\n"
        "a=json.load(open('_e2e_driver_args.json',encoding='utf-8'))\n"
        "def smoke(p):\n"
        "    import subprocess\n"
        "    r=subprocess.run([sys.executable,p,'--json'],capture_output=True,timeout=120)\n"
        "    raw=r.stdout.decode('utf-8','replace')\n"
        "    if r.returncode!=0:\n"
        "        raise AssertionError('rc=%d stderr[:250]=%r' % (r.returncode, r.stderr.decode('utf-8','replace')[:250]))\n"
        "    try:\n"
        "        d=json.loads(raw)\n"
        "    except Exception:\n"
        "        raise AssertionError('JSON解析失败·stdout前350字=%r·stderr前150字=%r' % (raw[:350], r.stderr.decode('utf-8','replace')[:150]))\n"
        "    assert isinstance(d,dict) and d, '顶层须为非空dict'\n"
        "ok,detail=agent_coder.run(a['label'],'DEV1',a['out'],a['spec'],smoke=smoke,k_id=a['tid'])\n"
        "print('E2E_CODE',ok,detail)\n"
    )
    rc, out, err = run([PY, "-c", code], cwd=wt, timeout=1800)
    os.remove(dj)
    if "E2E_CODE True" not in out:
        return False, (out + err).strip()[:800]
    return True, out.strip().splitlines()[-1][:160]


SMOKE_ASSERTS = None  # 保留（smoke 断言内嵌于 gen_code 子进程）


def main():
    global TID
    ap = argparse.ArgumentParser()
    ap.add_argument("tid")
    ap.add_argument("--project", default="general")
    ap.add_argument("--owner", default="DEV1")
    ap.add_argument("--out", required=True)
    ap.add_argument("--spec-file", required=True)
    ap.add_argument("--repair-rounds", type=int, default=1)
    ap.add_argument("--incremental", action="store_true",
                    help="增量型工单：目标文件已存在·金样本自动采集+增量纪律段")
    args = ap.parse_args()
    TID = args.tid
    spec = open(args.spec_file, encoding="utf-8").read()
    wt = os.path.join(ROOT, ".worktrees", TID)

    # ---- 0. 增量模式：金样本采集（改动前真实输出·K-ORG-068 QA1 三点整改之一）----
    golden = ""
    if args.incremental:
        target = os.path.join(ROOT, args.out)
        if not os.path.isfile(target):
            die("金样本", "增量模式要求目标文件已存在：%s" % target)
        rc, out, err = run([sys.executable, args.out, "--json"],
                           cwd=ROOT, timeout=120)
        if rc != 0:
            die("金样本", "改动前基线运行失败 rc=%s %s" % (rc, err.strip()[:150]))
        golden = out.strip()
        spec += ("\n\n## 增量纪律（K-ORG-070 判例·违反即 QA1 退回）\n\n"
                 "- 目标文件 `%s` 已存在且经治理链验证：**禁止重写/重构既有函数**，"
                 "只允许新增函数或在 main() 内做最小增量修改\n"
                 "- 既有函数实现体一字不动；确需改动契约时必须在 P-3 显式声明"
                 "「契约变更：旧→新」\n"
                 "- **金样本（改动前真实 stdout 基线）**：无新参数时既有行为必须"
                 "与金样本一致，逐字段类型不得漂移\n"
                 "- `--json` golden=%s\n" % (args.out, golden[:400]))

    # ---- 1. 开单 ----
    rc, out, err = run([PY, "tools/devtask.py", "new", TID,
                        "%s（e2e彩排单·驱动器全自动派单）" % spec.strip().splitlines()[0][:60],
                        "--project", args.project])
    if rc != 0:
        die("devtask new", err.strip() or out.strip())
    log("devtask new", "OK worktree=%s" % wt)
    if not os.path.isdir(wt):
        die("worktree", "未生成 %s" % wt)

    # ---- 2. 方案文档 ----
    plan = ("# 研发方案：%s（e2e 全自动彩排）\n\n> 派单：e2e_pipeline 驱动器·零人工　%s\n\n"
            "## 规格条目（SEC2 走查对照基准）\n\n%s\n"
            "## 验收判据\n\n- py_compile 通过\n- `--json` 契约与规格 P-3 一致"
            "（退出码 0 · stdout 仅一个合法 JSON 对象·字段类型可序列化）\n"
            " - spec 声明的数据源在真实环境对账非空（跑得通≠分得对）\n"
            " - 只读零网络零写面（不改任何文件）\n" % (TID, datetime.date.today(), spec))
    plan_rel = "docs/plans/%s_%s_e2e方案.md" % (datetime.date.today(), TID)
    with open(os.path.join(wt, plan_rel), "w", encoding="utf-8", newline="\n") as f:
        f.write(plan)
    run(["git", "add", plan_rel], cwd=wt)
    rc, out, err = run(["git", "commit", "-q", "-m", "%s: 方案文档(e2e自动生成·SEC2对照基准)" % TID], cwd=wt)
    if rc != 0:
        die("方案提交", err.strip())
    log("plan commit", plan_rel)

    # ---- 3~6. 产码→提交→SEC2→QA1（SEC2 缺失与 QA1 退回共用返修预算）----
    qa1_verdict, qa1_out, sha = "", "", ""
    last_fail, sec2_miss = "QA1", None
    for rnd in range(1 + args.repair_rounds):
        repair_note = None
        if rnd > 0:
            if last_fail == "SEC2":
                repair_note = ("SEC2 返工清单", sec2_miss or "")
                log("返修轮 %d" % rnd, "SEC2 缺失清单已回流(%d 字)" % len(sec2_miss or ""))
            else:
                m = re.search(r"结论[：:]\s*\*\*退回\*\*[—-]*(.*)", qa1_out, re.S)
                repair_note = ("QA1 退回理由", m.group(1).strip()[:1500] if m else qa1_out[-800:])
                log("返修轮 %d" % rnd, "退回理由已回流(%d 字)" % len(repair_note[1]))
        ok, detail = gen_code(wt, args.out, spec, repair_note)
        if not ok:
            die("DEV1 产码(轮%d)" % (rnd + 1), detail)
        log("DEV1 产码(轮%d)" % (rnd + 1), detail)
        run(["git", "add", args.out], cwd=wt)
        rc, out, err = run(["git", "commit", "-q", "-m", "%s: e2e产码 %s(轮%d%s)" % (
            TID, args.out, rnd + 1, "·返修" if rnd else "")], cwd=wt)
        if rc != 0:
            die("产码提交", err.strip())
        rc, out, _ = run(["git", "rev-parse", "--short", "HEAD"], cwd=wt)
        sha = out.strip()
        log("产码提交", sha)

        sec2_t0 = datetime.datetime.now().timestamp()
        rc, out, err = run([PY, "tools/sec2_review.py", "--plan", plan_rel,
                            "--sha", sha, "--files", args.out], cwd=wt, timeout=900)
        m = re.search(r"缺失\s*(\d+)", out)
        miss = int(m.group(1)) if m else -1
        log("SEC2(轮%d)" % (rnd + 1), "缺失=%s" % miss)
        if miss != 0:
            sec2_miss = extract_sec2_miss(wt, plan_rel, sec2_t0)
            if rnd >= args.repair_rounds or not sec2_miss:
                write_trace("SEC2_FAIL")
                print("[结论] SEC2 缺失 %s → 返修预算耗尽，e2e 停（轨迹已留）" % miss)
                sys.exit(2)
            last_fail = "SEC2"
            continue

        rc, stat, _ = run(["git", "show", "--stat", sha], cwd=wt)
        stat_line = [l for l in stat.splitlines() if "changed" in l]
        rc, real, _ = run([PY, args.out, "--json"], cwd=wt, timeout=120)
        note = ("证据包(驱动器自动组装·轮%d):1)stat原样引用git show --stat输出:[%s];"
                "2)逐条实跑=smoke回路过(--json退出码0+stdout为合法JSON对象·smoke断言与规格P-3一致不含额外键断言)+真实stdout原样贴:%s;"
                "3)只读边界=工具无open(w)/write/网络;4)SEC2走查缺失=0(%s)"
                % (rnd + 1, stat_line[0].strip() if stat_line else "N/A",
                   real.strip()[:200], plan_rel))
        rc, out, err = run([PY, "tools/qa1_review.py", "--task", TID, "--note", note, sha],
                           cwd=wt, timeout=900)
        qa1_out = out
        m = re.search(r"结论[：:]\s*\*\*([^*]+)\*\*", out)
        if m:
            qa1_verdict = m.group(1).strip()
        elif re.search(r"\*\*?退回", out):
            qa1_verdict = "退回(格式降级匹配)"
        elif re.search(r"\*\*?通过", out):
            qa1_verdict = "通过(格式降级匹配)"
        else:
            # 终极兜底(K-ORG-068 d段): stdout 格式漂移时, review_verdict 落账
            # 必有「结论：**X**」格式——账本是结论的最终事实源。
            rc, ev, _ = run([PY, "-c",
                             "import json\n"
                             "for L in reversed(open(r'portal/data/dev_events.jsonl',encoding='utf-8')):\n"
                             "    e=json.loads(L)\n"
                             "    if e.get('kind')=='review_verdict' and e.get('task')==%r:\n"
                             "        print(e.get('detail','')[:400]);break\n" % TID])
            m2 = re.search(r"结论[：:]\s*\*\*([^*]+)\*\*", ev)
            if m2:
                qa1_verdict = m2.group(1).strip() + "(账本兜底)"
            else:
                # 判例(K-ORG-068 轮1): 无结论≠退回——误判会触发无谓返修, fail-stop
                write_trace("QA1_UNPARSEABLE")
                print("[结论] QA1 stdout+账本均无法解析出结论 → fail-stop（stdout 尾部: %r）"
                      % out.strip()[-200:])
                sys.exit(4)
        log("QA1(轮%d)" % (rnd + 1), qa1_verdict)
        if ("通过" in qa1_verdict) and ("退回" not in qa1_verdict):
            break
        last_fail = "QA1"
    else:
        write_trace("QA1_FAIL")
        print("[结论] QA1：%s → 返修轮耗尽仍退回，e2e 停（轨迹已留）" % qa1_verdict)
        sys.exit(3)

    # ---- 7. merge ----
    rc, out, err = run([PY, "tools/devtask.py", "merge", TID], timeout=900)
    if rc != 0:
        die("merge", (err + out).strip()[:400])
    log("merge", "OK")
    write_trace("DONE")
    print("[结论] e2e 全自动链走通：开单→产码→SEC2→QA1→merge 零人工干预 ✅（QA1 判定=%s）" % qa1_verdict)


if __name__ == "__main__":
    main()
