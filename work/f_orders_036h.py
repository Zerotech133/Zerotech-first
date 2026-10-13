# -*- coding: utf-8 -*-
"""K-ORG-036h/i: F4 判据「连续三工单全自动」第 2、3 单（F1 降级预案·纯 API 通道）。
  h: 模型分层修正——export_claude_agents.py 轻量档 glm-4-flash → glm-4.5-flash
     （判据实证：flash 档四连拦不能编码，12 个轻量岗导出配置须跟进）
  i: 冒烟轨迹去噪——f4_commit_smoke.py 复用 K-ORG-036d 号写 trace，污染 IDE 轨迹视图
     （实证：当日 bundle 轨迹块被 dry-run fail 噪音占据）→ 改用专属号 K-SMOKE-F4
"""
import json
import os
import shutil
import subprocess as sp
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import agent_coder as ac  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RESULTS = []

SR_FMT = """格式（必须严格遵守）：
<<<<<<< SEARCH
（目标文件中原样存在的文本，必须唯一命中、逐字符照抄）
=======
（替换后的文本）
>>>>>>> REPLACE

【禁止】整文件重写、pass/... 占位、遗漏任何一个要求的修改点。"""


def full_chain(kid, agent_id, target, prompt, smoke):
    bak = os.path.join(tempfile.gettempdir(),
                       os.path.basename(target) + ".bak_" + kid)
    shutil.copy2(target, bak)
    # Aider 范式：模型必须看到目标文件当前内容，SEARCH 锚点才抄得准（036h判例：盲猜锚=hit 0）
    cur = open(target, encoding="utf-8").read()
    full = prompt + ("\n\n【目标文件 %s 当前完整内容——SEARCH 原文必须从这里逐字符照抄】\n"
                     "===== FILE START =====\n%s\n===== FILE END =====\n" % (target, cur))
    ok, detail = ac.run(label=os.path.basename(target) + " 修订",
                        agent_id=agent_id, out_path=target,
                        prompt=full, smoke=smoke, k_id=kid)
    if not ok:
        shutil.copy2(bak, target)
        print("[%s RESTORED]" % kid)
        return False, detail
    return True, detail


# ---------- 工单 h：模型分层修正 ----------
T_H = os.path.join("tools", "export_claude_agents.py")

def smoke_h(path):
    src = open(path, encoding="utf-8").read()
    assert 'LITE = "glm-4.5-flash"' in src, "LITE 未改"
    assert "glm-4-flash\n" not in src.replace('glm-4.5-flash', ''), "残留旧档"
    r = sp.run([sys.executable, T_H], capture_output=True, text=True,
               encoding="utf-8", errors="replace", cwd=ROOT)
    assert r.returncode == 0, "导出脚本执行失败: " + r.stdout[-200:] + r.stderr[-200:]
    d = json.load(open(os.path.join(ROOT, "agent_platform/registry/claude_agents.json"),
                       encoding="utf-8"))
    assert d["QA1"]["model"] == "glm-4.6", d["QA1"]["model"]
    assert d["DOC1"]["model"] == "glm-4.6", "DOC1属dev应智能档: " + d["DOC1"]["model"]
    models = {a["model"] for a in d.values()}
    assert models <= {"glm-4.6", "glm-4.5-flash"}, "未知模型档: %s" % models
    lite = sum(1 for a in d.values() if a["model"] == "glm-4.5-flash")
    assert lite >= 10, "轻量档数量异常: %d" % lite
    print("[smoke-h] 导出产物分档 PASS（智能=%d 轻量=%d）" % (27 - lite, lite))

PROMPT_H = """【工单 K-ORG-036h】修订 tools/export_claude_agents.py：轻量档模型升级。

背景：2026-10-13 全自动链判据实证，glm-4-flash 四连拦（截水/桩体/改坏逻辑/吞编辑）
不能胜任编码，glm-4.5-flash 首发即中——导出配置里 12 个轻量岗仍指旧档，须跟进。

要求（SEARCH/REPLACE 两处）：
1. LITE 常量："glm-4-flash" 改为 "glm-4.5-flash"
2. 文件头注释里的「轻量产出岗 glm-4-flash」同步改为「轻量产出岗 glm-4.5-flash」
   （以【目标文件当前完整内容】里的实际原文为准，别编造不存在的注释行）
其余一字不动。只输出 SR 块。

""" + SR_FMT

ok, detail = full_chain("K-ORG-036h", "DEV2", T_H, PROMPT_H, smoke_h)
RESULTS.append(("K-ORG-036h", ok, detail))
if ok:
    for f in (T_H, os.path.join("agent_platform", "registry", "claude_agents.json")):
        cok, cdet = ac.commit_work(f, "K-ORG-036h", "DEV2", "模型分层修正:轻量档升4.5-flash",
                                   channel="cloud")
        print("[commit]", cok, cdet)
        RESULTS.append(("K-ORG-036h commit " + f, cok, cdet))

# ---------- 工单 i：冒烟轨迹去噪 ----------
T_I = os.path.join("work", "f4_commit_smoke.py")

def smoke_i(path):
    src = open(path, encoding="utf-8").read()
    assert '"K-SMOKE-F4"' in src, "专属号未改"
    assert '"K-ORG-036d"' not in src, "仍复用旧号"
    assert "time.strftime" in src, "probe 保鲜未做"
    r = sp.run([sys.executable, T_I], capture_output=True, text=True,
               encoding="utf-8", errors="replace", cwd=ROOT)
    assert r.returncode == 0, "F4 冒烟复跑失败: " + r.stdout[-300:] + r.stderr[-200:]
    day_dir = os.path.join(ROOT, "shadow", "coder_traces")
    days = sorted(os.listdir(day_dir))
    latest = os.path.join(day_dir, days[-1])
    assert "K-SMOKE-F4.jsonl" in os.listdir(latest), \
        "专属轨迹未落盘: %s" % os.listdir(latest)
    print("[smoke-i] 专属号轨迹落盘 PASS（%s）" % days[-1])

PROMPT_I = """【工单 K-ORG-036i】修订 work/f4_commit_smoke.py：冒烟轨迹去噪+probe保鲜。

背景1：该脚本 commit_work 冒烟复用了真实工单号 "K-ORG-036d"，每次复跑都往
shadow/coder_traces/ 追加同号轨迹，IDE 轨迹块被 dry-run 噪音占据。
背景2：probe 文件 f4_commit_probe.py 已入库（K-ORG-036d 提交），此后每次复跑
内容无变更 → git add 零暂存 → commit --dry-run 报 "Changes not staged"
返回非零 → 冒烟第2步必失败（真缺陷）。

要求（SEARCH/REPLACE 两处）：
1. commit_work 调用里的 "K-ORG-036d" 改为专属冒烟号 "K-SMOKE-F4"
2. 让 probe 每次复跑内容有变化：在文件顶部 import 区补 "import time"，
   并把写 probe 的 f.write(...) 内容末尾追加一行 run 戳，例如：
   f.write("# K-ORG-036d F4 smoke probe\\nVALUE = 42\\n# run " +
           time.strftime("%Y-%m-%d %H:%M:%S") + "\\n")
   （以【目标文件当前完整内容】里的实际原文为准做锚点）
其余一字不动。只输出 SR 块。

""" + SR_FMT

ok, detail = full_chain("K-ORG-036i", "DEV2", T_I, PROMPT_I, smoke_i)
RESULTS.append(("K-ORG-036i", ok, detail))
if ok:
    cok, cdet = ac.commit_work(T_I, "K-ORG-036i", "DEV2", "冒烟轨迹去噪:专属号K-SMOKE-F4",
                               channel="cloud")
    print("[commit]", cok, cdet)
    RESULTS.append(("K-ORG-036i commit", cok, cdet))

print("\n=== BATCH RESULT ===")
fails = [r for r in RESULTS if not r[1]]
for k, okk, d in RESULTS:
    print(" %-28s %s  %s" % (k, "OK " if okk else "FAIL", str(d)[:80]))
sys.exit(1 if fails else 0)
