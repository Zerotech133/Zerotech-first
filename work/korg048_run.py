# -*- coding: utf-8 -*-
"""K-ORG-048 全自动链驱动：chat_auto 通道元数据改造（047 设计稿方案 B）。

设计稿：docs/plans/2026-10-13_174000_chat_auto通道元数据改造设计_K-ORG-047.md
失真根因（043/045 两判例）：chat_auto 降级后只回 str（实际通道丢弃）、
run() trace 用 backend()（请求档）、commit_work 取调用方传参（请求档）。

改造四处（SR 最小修改）：
  1) import 区补 `import time` 与 `from dataclasses import dataclass`
  2) ChatResult dataclass + _chat_auto_impl（原 chat_auto 主体迁入，返回
     ChatResult(content/channel/requested/attempts/latency_s)；入口重置
     _LAST_META；每级失败 attempts 追加 (通道, err)；最终 return 前写 _LAST_META）
  3) 公开薄壳：chat_auto(...)->str 返回 .content（零调用方破坏）；
     新增 chat_auto_meta(...) 返回 ChatResult；新增 last_channel() 取实际通道
  4) run() 改调 chat_auto_meta（raw=meta.content），generate trace 行追加
     actual 通道；commit_work 的 channel 参数 None/"auto" → last_channel()

冒烟（T1-T6 子进程验新代码，见 work/korg048_gate_check.py）。
驱动侧注意：run() 改调 chat_auto_meta 后，monkey-patch 必须同时覆盖两个入口。
"""
import subprocess
import sys

sys.path.insert(0, "tools")
import agent_coder as ac  # noqa: E402

TARGET = "tools/agent_coder.py"


def smoke(_path):
    r = subprocess.run(
        [sys.executable, "work/korg048_gate_check.py"],
        capture_output=True, text=True, encoding="utf-8", timeout=120)
    print(r.stdout[-1200:])
    assert r.returncode == 0, "gate_check FAIL: %s" % (r.stderr[-500:] or r.stdout[-500:])


def main():
    # anthropic 大文件 SR 生成 >240s 默认超时必降级（044 判例）→ 两个入口都提 600s。
    # run() 重构后调 chat_auto_meta，patch 必须双覆盖——本驱动自身即判例。
    def _wrap(fn):
        def inner(agent_id, task, **kw):
            kw.setdefault("timeout_cloud", 600)
            return fn(agent_id, task, **kw)
        return inner
    ac.chat_auto = _wrap(ac.chat_auto)
    ac.chat_auto_meta = _wrap(getattr(ac, "chat_auto_meta", ac.chat_auto))

    cur = open(TARGET, encoding="utf-8").read()
    prompt = (
        "按设计稿（K-ORG-047 已拍板方案 B）给 tools/agent_coder.py 做"
        " chat_auto 通道元数据改造（K-ORG-048，根因：降级链后实际生成通道"
        "信息丢失，commit/trace 只记请求档——043/045 两判例）。\n"
        "四处最小修改（全部 SEARCH/REPLACE 块，禁止整文件重写、禁止改其他逻辑）：\n"
        "1) import 区（import ast/json/os/shutil/subprocess 附近）补两行："
        "`import time` 与 `from dataclasses import dataclass`。\n"
        "2) 把现有 def chat_auto(...) 整个函数改造为 _chat_auto_impl：\n"
        "   - 签名同原 chat_auto，返回 ChatResult；函数体开头："
        "`_t0 = time.time()`、`attempts = []`、"
        "模块级 `_LAST_META = None  # 最近一次 chat_auto_meta 结果（非线程安全）`"
        "（此行放模块级，不在函数内）；\n"
        "   - 每处 `except Exception as e:` 分支在 print 后追加 "
        "`attempts.append((\"<该级通道名>\", str(e)[:120]))`（gpt6 级记 \"gpt6\"，"
        "anthropic 级记 \"anthropic\"，claude_glm 级记 \"claude_glm\"，cloud 级记 \"cloud\"）；\n"
        "   - 每个 return 点改为构造 ChatResult：成功级通道名作为 channel，"
        "requested=bk（原 backend() 结果），attempts=attempts，"
        "latency_s=round(time.time()-_t0, 1)；并在 return 前赋值 `_LAST_META`（gpt6 分支"
        "成功通道 \"gpt6\"；anthropic 分支成功 \"anthropic\"；claude_glm 分支成功 "
        "\"claude_glm\"；cloud 分支成功 \"cloud\"；函数末尾兜底 ollama 分支通道 "
        "\"ollama\" 也同样构造并写 _LAST_META）。\n"
        "3) 在 _chat_auto_impl 之后新增三个公开函数：\n"
        "   - `def chat_auto(agent_id, task, timeout_cloud=240, timeout_local=420, "
        "timeout_claude=600):` 薄壳 `return _chat_auto_impl(...).content`（转发全部参数）；\n"
        "   - `def chat_auto_meta(agent_id, task, timeout_cloud=240, timeout_local=420, "
        "timeout_claude=600):` `return _chat_auto_impl(...)`；\n"
        "   - `def last_channel():` 返回 `_LAST_META.channel if _LAST_META else backend()`。\n"
        "   并在 import 区之后、_chat_auto_impl 之前定义 dataclass：\n"
        "   ```\n"
        "   @dataclass\n"
        "   class ChatResult:\n"
        "       content: str\n"
        "       channel: str      # 实际生成通道（降级后最终档）\n"
        "       requested: str    # backend() 请求档\n"
        "       attempts: list    # [(通道, err)] 失败链\n"
        "       latency_s: float\n"
        "   ```\n"
        "4) run() 接线：`raw = chat_auto(agent_id, task)` 改为 "
        "`meta = chat_auto_meta(agent_id, task)` + `raw = meta.content`；"
        "generate 那行 trace_log 改为 "
        "`\"%s by %s via %s(requested=%s actual=%s)\" % (label, agent_id, backend(), meta.requested, meta.channel)`；"
        "commit_work 的 `channel=None` 参数语义升级：函数体开头加 "
        "`if channel in (None, \"auto\"): channel = last_channel()`（显式传值仍覆盖）。\n\n"
        "【目标文件当前完整内容 tools/agent_coder.py】\n<<<<\n" + cur + "\n>>>>\n")
    ok, detail = ac.run(
        label="K-ORG-048 chat_auto通道元数据改造(ChatResult+薄壳+last_channel)",
        agent_id="DEV2", out_path=TARGET, prompt=prompt,
        smoke=smoke, k_id="K-ORG-048")
    print("[run] ok=%s detail=%s" % (ok, detail))
    if not ok:
        sys.exit(1)
    # channel="auto" = 新语义首次实战：commit 消息自动取实际生成通道
    res = ac.commit_work(TARGET, "K-ORG-048", "DEV2",
                         "chat_auto通道元数据改造(047设计稿方案B)", channel="auto")
    print("[commit] %r" % (res,))


if __name__ == "__main__":
    main()
