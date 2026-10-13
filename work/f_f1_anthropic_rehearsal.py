# -*- coding: utf-8 -*-
"""K-ORG-038a: F1 判据工单——anthropic 通道(GLM-4.6 包年)全自动链。
判据(K-ORG-035 F1 原文): 该通道重写同级模块一个 py_compile+冒烟过。
真实需求: QA1 复核固化 tools/qa1_review.py（036g~i 每批收尾都在手动内联复核，应常备化）。
"""
import os
import subprocess as sp
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import agent_coder as ac  # noqa: E402

TARGET = os.path.join("tools", "qa1_review.py")
KID = "K-ORG-038a"

PROMPT = """【工单 %s】新建 tools/qa1_review.py：QA1 质检复核固化工具。

背景：编码生产线每批工单收尾都由 QA1 复核 git diff，目前是手动内联脚本，
须固化为常备工具。仓库根目录运行。

功能要求：
1. 命令行：python tools/qa1_review.py <短sha> [<sha>...]，至少一个 sha
2. 对每个 sha：subprocess 调 `git show <sha>` 取 diff（截前 3000 字）
3. 构造复核 prompt：要求逐笔回答 1)改动与提交消息是否相符 2)有无逻辑/边界隐患
   3)结论「通过」或「退回+理由」；然后调用 agent_coder.chat_auto("QA1", task)
   （sys.path 处理：把本文件所在目录加入 sys.path 后 import agent_coder，
   参考 tools/repo_map.py 被外部调用的方式）
4. 打印每笔复核结论；若结论含「退回」，最终 exit 1，否则 exit 0
5. 只依赖标准库 + tools/agent_coder.py；含 UTF-8 头注释

输出完整新文件内容（python 围栏内）。【禁止】pass/... 占位。""" % KID

def smoke(path):
    src = open(path, encoding="utf-8").read()
    for key in ("def main", "chat_auto", '"QA1"', "show"):
        assert key in src, "缺关键实现: %s" % key
    import re
    assert not re.search(r"^\s+pass\s*$", src, re.M), "含 pass 占位"
    r = sp.run([sys.executable, TARGET, "f702d87"], capture_output=True,
               text=True, encoding="utf-8", errors="replace", cwd=".")
    out = (r.stdout or "") + (r.stderr or "")
    assert "f702d87" in out, "未输出该 sha 复核: " + out[-300:]
    assert "退回" not in out, "已知好提交被判退回: " + out[-400:]
    print("[smoke] qa1_review 实跑 f702d87 复核输出 PASS")

ok, detail = ac.run(label="qa1_review复核工具", agent_id="DEV2", out_path=TARGET,
                    prompt=PROMPT, smoke=smoke, k_id=KID)
if not ok:
    import os.path
    if os.path.exists(TARGET):
        os.remove(TARGET)          # 新建类工单失败即删，不留半成品
    print("[REMOVED partial output]")
    sys.exit("FULL-CHAIN FAIL: " + detail)

cok, cdet = ac.commit_work(TARGET, KID, "DEV2", "QA1复核固化工具",
                           channel="anthropic")
print("[commit]", cok, cdet)
print("FULL-CHAIN", "PASS" if cok else "COMMIT-FAIL", "-", detail)
