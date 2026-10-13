# -*- coding: utf-8 -*-
"""K-ORG-037: 验收线「轨迹回放 + 按单回滚」实证（K-ORG-035 验收线第 3 项）。
  1) 演练提交（真 commit·专属演练号 K-ORG-037-RR）
  2) 轨迹回放：解析 trace jsonl 重建动作序列、提取 commit sha
  3) 按单回滚：git revert 该 sha → 验证产物被还原（历史保留·可审计）
"""
import json
import os
import subprocess as sp
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import agent_coder as ac  # noqa: E402

KID = "K-ORG-037-RR"
PROBE = os.path.join("work", "f_rr_probe.py")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def git(*args):
    return sp.run(["git", *args], capture_output=True, text=True,
                  encoding="utf-8", errors="replace", cwd=ROOT)

# 1) 演练提交（真提交·轨迹自动入账；probe 带时间戳保鲜=036i 判例同款）
import time
with open(PROBE, "w", encoding="utf-8") as f:
    f.write("# K-ORG-037 按单回滚演练产物\nVALUE = 'K-ORG-037-RR'\n# run "
            + time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
ok, detail = ac.commit_work(PROBE, KID, "OPS1", "按单回滚演练", channel="cloud")
assert ok, "演练提交失败: " + detail
print("1 演练提交 PASS ->", detail)

# 2) 轨迹回放：按单 jsonl 重建动作序列
day_dir = os.path.join(ROOT, "shadow", "coder_traces")
days = sorted(os.listdir(day_dir))
fn = None
for d in reversed(days):
    cand = os.path.join(day_dir, d, KID + ".jsonl")
    if os.path.exists(cand):
        fn = cand
        break
assert fn, "轨迹未找到: " + KID
recs = [json.loads(l) for l in open(fn, encoding="utf-8")]
kinds = [r["kind"] for r in recs]
assert "add" in kinds and "commit" in kinds, kinds
commit_rec = [r for r in recs if r["kind"] == "commit"][-1]
trace_sha = commit_rec["detail"].split()[0]
assert trace_sha == detail, (trace_sha, detail)
print("2 轨迹回放 PASS: kinds=%s sha=%s" % (kinds, trace_sha))

# 3) 按单回滚：revert 该 sha（历史保留·反向提交可审计）
#    注：probe 在首轮演练(e8e20c9)已入库，本 sha 为修改型提交——
#    revert 后应回到父版（run 戳消失），而非文件消失（037 判例：按单回滚=撤该单改动）
st = git("show", "--stat", "--oneline", trace_sha)
assert "f_rr_probe.py" in st.stdout, st.stdout[:200]
r = git("revert", "--no-edit", trace_sha)
assert r.returncode == 0, "revert 失败: " + (r.stderr or r.stdout)[:200]
revert_sha = git("rev-parse", "--short", "HEAD").stdout.strip()
restored = open(os.path.join(ROOT, PROBE), encoding="utf-8").read()
assert "# run" not in restored, "回滚后仍含本单改动戳"
print("3 按单回滚 PASS: revert=%s 本单改动已撤销（历史保留 %s→%s）"
      % (revert_sha, trace_sha, revert_sha))
print("REPLAY-ROLLBACK ALL PASS")
