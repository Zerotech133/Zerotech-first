# -*- coding: utf-8 -*-
"""cowbridge provider 插件 v0.2 自测——用 Hermes 真实加载器全流程验证。

运行方式（须 hermes venv + hermes-agent 目录在 sys.path）：
  C:/Users/Administrator/.hermes/venv/Scripts/python.exe test_provider_plugin.py
（脚本自身已把 hermes-agent 插到 sys.path[0]，cwd 无要求）

覆盖：
  1 发现启发式识别  2 register 模式装载  3 discover 列表可见
  4 is_available    5 initialize+context 防线  6 system_prompt_block
  7 prefetch 往返   8 工具 schema+调用   9 影子留痕（memory_write/turn/session_end）
 10 register(ctx)  11 shutdown
 10 审计P1-1 翻转断言（raise+硬只读+零落盘）  11 审计P2-1 limit 边界  12 审计P2-2 工具描述
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

AGENT_DIR = Path(r"C:\Users\Administrator\.hermes\hermes-agent")
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

BRIDGE_DIR = Path(__file__).resolve().parent
SHADOW = BRIDGE_DIR / "shadow"
FX = SHADOW / "fixtures" / "cow-workspace"

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


def read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> int:
    print("== cowbridge provider v0.2 自测 ==")

    # 合成 CowAgent workspace（供读侧检索；真实 ~/cow 未部署属预期）
    if FX.parent.exists():
        shutil.rmtree(FX.parent)
    mem = FX / "memory" / "daily"
    mem.mkdir(parents=True)
    (mem.parent / "MEMORY.md").write_text(
        "# Cow Memory\n\n## 用户偏好\n- 偏好简体中文回复\n"
        "- 台账用「值@日期」格式\n\n## 项目约定\n- 提交前先跑自测\n", encoding="utf-8")
    (mem / "2026-10-08.md").write_text(
        "# Daily\n\n## 10-08 晨会\n- 拍板 v0.2 Provider 形态\n", encoding="utf-8")

    # 1) 发现启发式
    from plugins.memory import (_is_memory_provider_dir, discover_memory_providers,
                                load_memory_provider)
    user_plugin = Path.home() / ".hermes" / "plugins" / "cowbridge"
    check("1 启发式识别插件目录", _is_memory_provider_dir(user_plugin))

    # 2) register 模式装载（真实加载器）
    prov = load_memory_provider("cowbridge")
    check("2 load_memory_provider 返回实例", prov is not None,
          "返回 None——register 装载失败")
    if prov is None:
        print(f"== FAIL {len(FAIL)} 项，中止 ==")
        return 1

    # 3) discover 列表可见且 available
    disc = {n: (d, a) for n, d, a in discover_memory_providers()}
    check("3 discover 可见 cowbridge", "cowbridge" in disc, str(list(disc)))
    check("3b is_available=True", disc.get("cowbridge", ("", False))[1] is True)

    # 4) 生命周期：primary 上下文
    prov.initialize("test-session-001", hermes_home=str(user_plugin.parent),
                    agent_context="primary", platform="cli")
    check("4 桥本体已加载", prov._bridge is not None)
    check("4b 非只读上下文", prov._readonly_ctx is False)

    # 5) system_prompt_block
    spb = prov.system_prompt_block()
    check("5 提示词块含影子标注", "影子" in spb and "cow_memory_search" in spb)

    # 6) prefetch 往返（monkeypatch 桥读侧到 fixtures）
    prov._bridge.read_cow_memory = lambda cow_root=None: [
        {"side": "cow", "file": "MEMORY.md", "tier": "long", "header": "用户偏好",
         "body": "偏好简体中文回复\n台账用「值@日期」格式", "sha1": "a" * 12},
        {"side": "cow", "file": "daily/2026-10-08.md", "tier": "mid", "header": "10-08 晨会",
         "body": "拍板 v0.2 Provider 形态", "sha1": "b" * 12}]
    prov.queue_prefetch("简体中文 偏好")
    time.sleep(0.5)
    pf = prov.prefetch("简体中文 偏好")
    check("6 prefetch 注入召回", "简体中文" in pf and "用户偏好" in pf, repr(pf))
    prov.queue_prefetch("毫无命中关键词xyzq")
    time.sleep(0.5)
    check("6b 无命中返回空", prov.prefetch("x") == "")

    # 7) 工具
    schemas = prov.get_tool_schemas()
    check("7 暴露 1 个工具 cow_memory_search",
          len(schemas) == 1 and schemas[0]["name"] == "cow_memory_search")
    out = prov.handle_tool_call("cow_memory_search", {"query": "台账 格式"})
    o = json.loads(out)
    check("7b 工具返回 JSON 含命中", o["count"] >= 1 and "台账" in json.dumps(o, ensure_ascii=False), out)

    # 8) 影子留痕（primary + shadow_mode）
    mirror = SHADOW / "to-cowagent" / "memory_write_mirror.jsonl"
    n0 = len(read_jsonl(mirror))
    prov.on_memory_write("add", "memory", "测试：拍板 Provider 形态",
                         {"write_origin": "test"})
    rows = read_jsonl(mirror)
    check("8 on_memory_write 影子留痕", len(rows) == n0 + 1
          and rows[-1]["action"] == "add" and rows[-1]["shadow_mode"] is True)

    turn_f = SHADOW / "to-cowagent" / "turn_mirror.jsonl"
    n0 = len(read_jsonl(turn_f))
    prov.sync_turn("用户问 v0.2", "答：影子模式已激活", session_id="test-session-001")
    rows = read_jsonl(turn_f)
    check("8b sync_turn 影子留痕", len(rows) == n0 + 1 and rows[-1]["kind"] == "turn")

    se_f = SHADOW / "to-cowagent" / "session_end.jsonl"
    n0 = len(read_jsonl(se_f))
    prov.on_session_end([{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}])
    rows = read_jsonl(se_f)
    check("8c on_session_end 留痕", len(rows) == n0 + 1 and rows[-1]["messages"] == 2)

    # 9) cron/subagent 上下文防线：写钩子必须零落盘
    prov2 = load_memory_provider("cowbridge")
    prov2.initialize("cron-session", hermes_home=str(user_plugin.parent),
                     agent_context="cron")
    check("9 cron 上下文置只读", prov2._readonly_ctx is True)
    n0 = len(read_jsonl(mirror))
    prov2.on_memory_write("add", "memory", "cron 污染测试")
    prov2.sync_turn("cron user", "cron asst")
    check("9b cron 上下文零写入", len(read_jsonl(mirror)) == n0)

    # 10) 审计 P1-1 翻转断言：shadow_mode=false + allow_write_cow=true 但生产写
    #     未实现 → initialize 必须 raise，且实例硬只读（写路径零落盘）
    prov.shutdown()
    prov3 = load_memory_provider("cowbridge")
    prov3._cfg["shadow_mode"] = False
    prov3._cfg["allow_write_cow"] = True
    raised = False
    try:
        prov3.initialize("flip-session", hermes_home=str(user_plugin.parent),
                         agent_context="primary")
    except RuntimeError:
        raised = True
    check("10 翻转断言 raise(RuntimeError)", raised,
          "未 raise——防误翻转断言失效！")
    check("10b 断言触发后 _disabled=True",
          getattr(prov3, "_disabled", False) is True)
    applied_f = SHADOW / "to-cowagent" / "memory_write_applied.jsonl"
    n_m, n_a = len(read_jsonl(mirror)), len(read_jsonl(applied_f))
    prov3.on_memory_write("add", "memory", "翻转后污染测试")
    prov3.sync_turn("flip user", "flip asst")
    check("10c 翻转断言后写路径零落盘",
          len(read_jsonl(mirror)) == n_m and len(read_jsonl(applied_f)) == n_a)

    # 11) 审计 P2-1 limit 边界：0/负数/超大/非法 → 夹逼 [1,50]，0/非法回退 5
    big = [{"side": "cow", "file": f"daily/d{i}.md", "tier": "mid",
            "header": f"条目{i}", "body": "台账 关键词", "sha1": f"{i:012d}"}
           for i in range(60)]
    prov._bridge.read_cow_memory = lambda cow_root=None: big
    for raw, expect in [(0, 5), (-3, 1), (999, 50), (2, 2), ("abc", 5)]:
        o = json.loads(prov.handle_tool_call(
            "cow_memory_search", {"query": "台账", "limit": raw}))
        check(f"P2-1 limit={raw!r} → {expect} 条", o["count"] == expect,
              f"got {o['count']}")

    # 12) 审计 P2-2 工具描述对齐真实数据源（index.db / tier=dialog）
    desc = prov.get_tool_schemas()[0]["description"]
    check("P2-2 描述含 index.db+tier=dialog",
          "index.db" in desc and "tier=dialog" in desc, desc)

    print(f"== PASS {len(PASS)} / FAIL {len(FAIL)} ==")
    if FAIL:
        print("失败项:", FAIL)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
