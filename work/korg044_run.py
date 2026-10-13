# -*- coding: utf-8 -*-
"""K-ORG-044 全自动链驱动：agent_coder.run() 闸门栈完整性断言。

背景（043 判例）：cloud 版 whole 模式整文件截水（4.5KB→845c，丢 main），
冒烟只测目标行为 → 残缺文件假 PASS。
要求：run() 在写盘前对 whole 模式产出做完整性断言——
  新增 _integrity_gate(content, orig_bytes, out_path) 返回 err(str) 或 None：
  1) 原文件存在且产出长度 < 原长度*0.8 → "完整性FAIL: 产出%d字 < 原文%d字*0.8（截水特征）"
  2) .py 且产出顶层 def/class 数 < 原文 → "完整性FAIL: 顶层定义%d个 < 原文%d个（函数丢失）"
  3) 其余（SR 模式/新文件/非py）→ None
  run() 中 lint 门 PASS 后、写盘前调用；err 非空时与语法错同路喂回重试。
只允许 SEARCH/REPLACE 块最小修改，禁止整文件重写、禁止改既有函数逻辑。

冒烟（行为级·子进程验新代码）：
  T1 正常重写（长度相近）→ None；T2 截水（0.3x）→ FAIL；T3 丢函数 → FAIL。
"""
import subprocess
import sys

sys.path.insert(0, "tools")
import agent_coder as ac  # noqa: E402

TARGET = "tools/agent_coder.py"


def smoke(_path):
    r = subprocess.run(
        [sys.executable, "work/korg044_gate_check.py"],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    print(r.stdout[-800:])
    assert r.returncode == 0, "gate_check FAIL: %s" % (r.stderr[-400:] or r.stdout[-400:])


def main():
    # anthropic 分支超时=timeout_cloud(240s)，大文件 SR 生成超时被降级 cloud
    # （044 首跑实录：3 次 read timeout → cloud whole 截水拦尽）。驱动侧提至 600s。
    _orig_auto = ac.chat_auto
    ac.chat_auto = lambda agent_id, task: _orig_auto(agent_id, task,
                                                     timeout_cloud=600)
    cur = open(TARGET, encoding="utf-8").read()
    prompt = (
        "给 tools/agent_coder.py 的 run() 验证门回路补一个完整性断言闸门"
        "（K-ORG-044，043 判例挂账：whole 模式整文件截水 4.5KB→845c 丢 main，"
        "冒烟只测目标行为导致残缺文件假 PASS）。\n"
        "两处最小修改：\n"
        "1) 新增模块级函数 _integrity_gate(content, orig_bytes, out_path)，返回错误串或 None：\n"
        "   - orig_bytes 为空(None)→None（新文件不查）；\n"
        "   - 产出字符数 < 原文字符数*0.8 → 返回\"完整性FAIL: 产出%d字 < 原文%d字*0.8(截水特征)\"；\n"
        "   - out_path 以 .py 结尾且产出顶层 def/class 定义数 < 原文 → 返回"
        "\"完整性FAIL: 顶层定义%d个 < 原文%d个(函数丢失)\"（定义计数建议 ast.parse 顶层节点，"
        "SyntaxError 时用正则 (?m)^(?:async\\s+def|def|class)\\s 兜底）；\n"
        "   - 其余返回 None。\n"
        "2) run() 非 json_out 分支：在 lint 门 PASS（err 为空）之后、写盘之前调用"
        " err = _integrity_gate(content, orig_bytes, out_path)，"
        "err 非空走既有的带错重试回路（与语法错同路，trace 记 integrity）。\n"
        "3) run() 冒烟回路的还原缺口：smoke 抛异常且已到最后一轮（attempt >= smoke_retries）时，"
        "当前直接 return False 未还原原文——坏产出残留工作区（044 首跑实录）。"
        "在该 return False 之前调用 _restore() 保证坏产出零残留。\n"
        "禁止改动其他既有逻辑，禁止整文件重写。\n\n"
        "【目标文件当前完整内容 tools/agent_coder.py】\n<<<<\n" + cur + "\n>>>>\n")
    ok, detail = ac.run(
        label="K-ORG-044 run()闸门栈完整性断言(whole截水防护)",
        agent_id="DEV2", out_path=TARGET, prompt=prompt,
        smoke=smoke, k_id="K-ORG-044")
    print("[run] ok=%s detail=%s" % (ok, detail))
    if not ok:
        sys.exit(1)
    res = ac.commit_work(TARGET, "K-ORG-044", "DEV2",
                         "run()完整性断言闸门", channel=ac.backend())
    print("[commit] %r" % (res,))


if __name__ == "__main__":
    main()
