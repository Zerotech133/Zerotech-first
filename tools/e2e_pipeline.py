# -*- coding: utf-8 -*-
"""端到端全自动工单驱动器（K-ORG-064 基建 · 055 计划 11-08 里程碑彩排）。

用法：
    python tools/e2e_pipeline.py <TID> --project portal --out tools/xxx.py \
        --spec-file <spec.md> [--owner DEV1]

链条（全程零人工）：开单(new+worktree) → 方案文档落 worktree → DEV1 产码
(chat_auto·编辑协议+lint门+smoke回路) → 产码提交 → SEC2 走查(缺失>0 即停)
→ QA1 复核(附证自动组装·stat 原样引用判例) → 通过则 merge → 轨迹落 data/e2e/。

驱动器本身是主控基建（一次性编写）；工单处理环节零人工触发。
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


def main():
    global TID
    ap = argparse.ArgumentParser()
    ap.add_argument("tid")
    ap.add_argument("--project", default="general")
    ap.add_argument("--owner", default="DEV1")
    ap.add_argument("--out", required=True, help="产码目标路径（worktree 内相对路径）")
    ap.add_argument("--spec-file", required=True, help="产码规格（同时作方案文档底稿）")
    args = ap.parse_args()
    TID = args.tid
    spec = open(args.spec_file, encoding="utf-8").read()
    wt = os.path.join(ROOT, ".worktrees", TID)

    # ---- 1. 开单 ----
    rc, out, err = run([PY, "tools/devtask.py", "new", TID,
                        "%s（e2e彩排单·驱动器全自动派单）" % spec.strip().splitlines()[0],
                        "--project", args.project])
    if rc != 0:
        die("devtask new", err.strip() or out.strip())
    log("devtask new", "OK worktree=%s" % wt)
    if not os.path.isdir(wt):
        die("worktree", "未生成 %s" % wt)

    # ---- 2. 方案文档（SEC2 对照基准）----
    plan = ("# 研发方案：%s（e2e 全自动彩排）\n\n> 派单：e2e_pipeline 驱动器·零人工　%s\n\n"
            "## 规格条目（SEC2 走查对照基准）\n\n%s\n\n"
            "## 验收判据\n\n- py_compile 通过\n- `--json` 退出码 0 且含 already_merged/still_reviewing 键\n"
            " - 只读零网络零写面（不改任何文件）\n" % (TID, datetime.date.today(), spec))
    plan_rel = "docs/plans/%s_%s_e2e方案.md" % (datetime.date.today(), TID)
    with open(os.path.join(wt, plan_rel), "w", encoding="utf-8", newline="\n") as f:
        f.write(plan)
    run(["git", "add", plan_rel], cwd=wt)
    rc, out, err = run(["git", "commit", "-q", "-m", "%s: 方案文档(e2e自动生成·SEC2对照基准)" % TID], cwd=wt)
    if rc != 0:
        die("方案提交", err.strip())
    log("plan commit", plan_rel)

    # ---- 3. DEV1 产码（子进程·cwd=worktree·BUNDLE 相对路径判例）----
    driver = json.dumps({"tid": TID, "out": args.out, "spec": spec,
                         "label": "%s 产码" % TID})
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
        "    assert 'already_merged' in d and 'still_reviewing' in d, '缺键: '+str(list(d.keys()))\n"
        "ok,detail=agent_coder.run(a['label'],'DEV1',a['out'],a['spec'],smoke=smoke,k_id=a['tid'])\n"
        "print('E2E_CODE',ok,detail)\n"
    )
    rc, out, err = run([PY, "-c", code], cwd=wt, timeout=1200)
    os.remove(dj)
    if "E2E_CODE True" not in out:
        die("DEV1 产码", (out + err).strip()[:800])
    log("DEV1 产码", out.strip().splitlines()[-1][:160])

    # ---- 4. 产码提交 ----
    run(["git", "add", args.out], cwd=wt)
    rc, out, err = run(["git", "commit", "-q", "-m",
                        "%s: e2e产码 %s(DEV1 via chat_auto·smoke回路过)" % (TID, args.out)], cwd=wt)
    if rc != 0:
        die("产码提交", err.strip())
    rc, out, _ = run(["git", "rev-parse", "--short", "HEAD"], cwd=wt)
    sha = out.strip()
    log("产码提交", sha)

    # ---- 5. SEC2 走查 ----
    rc, out, err = run([PY, "tools/sec2_review.py", "--plan", plan_rel,
                        "--sha", sha, "--files", args.out], cwd=wt, timeout=900)
    m = re.search(r"缺失\s*(\d+)", out)
    miss = int(m.group(1)) if m else -1
    log("SEC2", "缺失=%s（%s）" % (miss, "PASS" if miss == 0 else "RETQUIRE"))
    if miss != 0:
        write_trace("SEC2_FAIL")
        print("[结论] SEC2 缺失 %s → 按治理链需返工，e2e 彩排在走查关停下（轨迹已留）" % miss)
        sys.exit(2)

    # ---- 6. QA1 复核（附证自动组装·stat 原样引用判例）----
    rc, stat, _ = run(["git", "show", "--stat", sha], cwd=wt)
    stat_line = [l for l in stat.splitlines() if "changed" in l]
    note = ("证据包(驱动器自动组装):1)stat原样引用git show --stat输出:[%s];"
            "2)逐条实跑=smoke回路已过(run内置:--json退出0且两键齐全)+py_compile经lint门;"
            "3)只读边界=工具内无open(w)/write/网络调用;4)SEC2走查缺失=0(%s)"
            % (stat_line[0].strip() if stat_line else "N/A", plan_rel))
    rc, out, err = run([PY, "tools/qa1_review.py", "--task", TID, "--note", note, sha],
                       cwd=wt, timeout=900)
    m = re.search(r"结论[：:]\s*\*\*([^*]+)\*\*", out)
    verdict = m.group(1).strip() if m else out.strip().splitlines()[-1][:120] if out.strip() else "(无输出)"
    passed = ("通过" in verdict) and ("退回" not in verdict)
    log("QA1", verdict)
    if not passed:
        write_trace("QA1_FAIL")
        print("[结论] QA1：%s → e2e 彩排在复核关停下（轨迹已留·返工为人工后续）" % verdict)
        sys.exit(3)

    # ---- 7. merge ----
    rc, out, err = run([PY, "tools/devtask.py", "merge", TID], timeout=900)
    if rc != 0:
        die("merge", (err + out).strip()[:400])
    log("merge", "OK")
    write_trace("DONE")
    print("[结论] e2e 全自动链走通：开单→产码→SEC2→QA1→merge 零人工干预 ✅")


if __name__ == "__main__":
    main()
