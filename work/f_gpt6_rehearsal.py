# -*- coding: utf-8 -*-
"""K-ORG-039a: GPT-6.0 判据工单全自动链（老田 10-13 12:10 提供 gpt-6-sol 密钥）。
工单=真实缺口：qa1_review.py 复核结论不回写事件账（手工 emit review_verdict 常被漏）。
链路：run()（生成→SR→lint→冒烟反馈重试）→ commit_work → 轨迹。通道 CODER_BACKEND=gpt6。"""
import json
import os
import shutil
import subprocess as sp
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, "tools")
import agent_coder as ac  # noqa: E402

TARGET = "tools/qa1_review.py"
KID = "K-ORG-039a"
TARGET_CONTENT = open(TARGET, encoding="utf-8").read()

PROMPT = """【工单 K-ORG-039a】修订 tools/qa1_review.py：三处修复。

背景：本工具是 QA1 质检复核工具，存在两处缺陷：
A) 致命：review_one() 里 `agent_coder.chat_auto("QA1", task)` 传入的 task
   是字符串，但 chat_auto 只接受 dict（取 task["prompt"] 等）——任何通道
   都会抛 AttributeError，本工具自出生起无法完成一次真复核；
B) 复核结论只打印 stdout，编码生产线的统一事件账（tools/events.py，
   CLI：python tools/events.py emit --kind review_verdict --agent QA1
   --task <工单号> --detail "<结论摘要>"）需要每笔复核自动留痕。

要求（SEARCH/REPLACE，基于【目标文件当前完整内容】）：
1. 修 A：review_one() 改为把复核文本包进 dict 再传：
   agent_coder.chat_auto("QA1", {"label": "QA1复核 " + sha,
                                 "prompt": task, "json_out": False})
2. 修 B：main() 的 `for sha in argv:` 循环内，每笔拿到 verdict 并打印后，
   追加自动 emit——subprocess.run 调
   ["python", "tools/events.py", "emit", "--kind", "review_verdict",
    "--agent", "QA1", "--task", task_id,
    "--detail", "<sha>: <结论最后一行，截200字>"]，
   emit 失败只打印警告、不影响复核结论与退出码。
3. main() 开头解析可选参数：argv 第一项若为 "--task"，取下一项为
   task_id（默认 "QA1-REVIEW"），并从 argv 移除这两个元素。
4. 收紧退回判定（现 REJECT_KEYWORD in verdict 过宽：模型说「无需退回」
   也会误判）：rejected 只看【最后一个非空行】是否含「退回」。
5. review_one 的调用处异常捕获从 RuntimeError 放宽到 Exception
   （chat_auto 可能抛 AttributeError 等网络/格式异常，不能中断批量复核）。
其余一字不动。只输出 SR 块。

【目标文件当前完整内容】
<<<<<<< FILE
%s
>>>>>>> FILE
""" % TARGET_CONTENT


def smoke(path):
    """方向无关断言：3a2c59f 是带缺陷的历史提交，QA1 判「退回」是正确行为。
    本冒烟只验工具功能完好：跑得通、无崩溃、退出码与结论一致、事件入账。"""
    src = open(path, encoding="utf-8").read()
    assert "--task" in src and "review_verdict" in src, "关键实现缺失"
    assert '"prompt": task' in src, "str→dict 修复缺失"
    r = sp.run([sys.executable, TARGET, "--task", "K-SMOKE-039", "3a2c59f"],
               capture_output=True, text=True, encoding="utf-8",
               errors="replace", cwd=ROOT, timeout=420)
    out = r.stdout or ""
    assert "AttributeError" not in out and "Traceback" not in (r.stderr or ""), \
        "工具崩溃: " + out[-200:] + (r.stderr or "")[-200:]
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines, "无输出"
    last = lines[-1]
    expect_rc = 1 if "退回" in last else 0
    assert r.returncode == expect_rc, \
        "退出码与结论不一致: rc=%s last=%r" % (r.returncode, last)
    assert ("通过" in out) or ("退回" in out), "无明确结论: " + out[-300:]
    t = sp.run([sys.executable, "tools/events.py", "tail", "-n", "5"],
               capture_output=True, text=True, encoding="utf-8",
               errors="replace", cwd=ROOT)
    assert "K-SMOKE-039" in (t.stdout or ""), "事件未入账: " + (t.stdout or "")[-300:]
    print("[smoke] 工具功能完好(结论=%s rc=%d) + review_verdict 自动入账 PASS"
          % (last[:30], r.returncode))


bak = os.path.join(tempfile.gettempdir(), "qa1_review.bak_039a")
shutil.copy2(TARGET, bak)
ok, detail = ac.run(label="qa1_review 结论自动入事件账",
                    agent_id="DEV2", out_path=TARGET, prompt=PROMPT,
                    smoke=smoke, k_id=KID)
print("run:", ok, detail)
if not ok:
    print("[自动回滚] 还原原文")
    shutil.copy2(bak, TARGET)
    sys.exit(1)

r = ac.commit_work(TARGET, "K-ORG-039a", "DEV2", "qa1_review复核结论自动入事件账",
                   channel="gpt6")
print("commit:", r)
assert r[0], r[1]
print("FULL-CHAIN PASS")
