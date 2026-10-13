# -*- coding: utf-8 -*-
"""
K-ORG-036 F6: 27 在编岗人设 → Claude Code --agents JSON 导出（子代理化）。
来源单一事实源 = portal/agent_ide_bundle.json（org.json+prompts 烘焙产物）。
产出 agent_platform/registry/claude_agents.json：
  { "<agent_id>": {"description", "prompt", "tools", "model"} }
映射规则：
  description  = 名字·职级·一句话职能
  prompt       = bundle 人设 + 追加【工作边界】段（由 grants_write 推导，模型文本不可绕→以
                 PreToolUse 钩子兜底，此处为提示层纪律）
  tools        = 质检/安全只读（Read,Grep）；研发全能（Read,Edit,Grep,Glob,Bash）；其余只读三件套
  model        = 决策/研发/质检岗 glm-4.6；轻量产出岗 glm-4.5-flash（成本分层）
"""
import json
import os
import sys

BUNDLE_PATH = "portal/agent_ide_bundle.json"
OUT_PATH = "agent_platform/registry/claude_agents.json"

FULL_TOOLS = "Read,Edit,Grep,Glob,Bash"
RO_TOOLS = "Read,Grep,Glob"
AUDIT_TOOLS = "Read,Grep"  # 质检/安全：只读边界（SEC1 判例）

SMART = "glm-4.6"
LITE = "glm-4.5-flash"
SMART_DEPTS = {"chief", "dev", "quality"}

TOOLS_BY_DEPT = {
    "quality": AUDIT_TOOLS,
    "dev": FULL_TOOLS,
}


def tools_for(agent):
    # K-ORG-053: 组织图动态注册岗可带 claude_tools 覆盖（架构即注册表）
    return agent.get("claude_tools") or TOOLS_BY_DEPT.get(agent["dept"], RO_TOOLS)


# K-ORG-052: 岗级模型特例——A0A(E1 轻量调度)走 Lite，不随 chief 部门吃 SMART
MODEL_OVERRIDES = {"A0A": LITE}


def model_for(agent):
    # K-ORG-053: 岗卡 claude_model > 岗级特例 > 部门默认
    if agent.get("claude_model"):
        return agent["claude_model"]
    if agent.get("id") in MODEL_OVERRIDES:
        return MODEL_OVERRIDES[agent["id"]]
    return SMART if agent["dept"] in SMART_DEPTS else LITE


def scope_block(agent):
    """【工作边界】段：写权限白名单推导。"""
    gw = agent.get("grants_write") or []
    gr = agent.get("grants_read") or []
    lines = ["", "## 工作边界（K-ORG-035 F6 纪律段）",
             "- 允许读取域：%s" % (", ".join(gr) or "无"),
             "- 允许写入域：%s" % (", ".join(gw) or "无（只读岗，禁止任何写操作）"),
             "- 禁改：org_baseline.json、哨文件、A0 日报链、tasks.json 登记区",
             "- 越界产出无效；QA1 复核以本边界为判据之一。"]
    return "\n".join(lines)


def main():
    bundle = json.load(open(BUNDLE_PATH, encoding="utf-8"))
    out = {}
    for aid, a in bundle["agents"].items():
        out[aid] = {
            "description": "%s·%s·%s" % (a.get("name", aid),
                                         a.get("grade", ""),
                                         a.get("team_desc") or a.get("grade_note", "")),
            "prompt": (a.get("prompt") or "").rstrip() + "\n" + scope_block(a),
            "tools": tools_for(a),
            "model": model_for(a),
        }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("exported %d agents -> %s" % (len(out), OUT_PATH))
    # 自检：岗数与 bundle 一致（K-ORG-053 去硬编码：组织图动态注册随时扩编）
    n_bundle = len(bundle["agents"])
    assert len(out) == n_bundle, (len(out), n_bundle)
    for aid, e in out.items():
        assert all(e.get(k) for k in ("description", "prompt", "tools", "model")), aid
        assert "工作边界" in e["prompt"], aid
    assert out["QA1"]["tools"] == AUDIT_TOOLS and out["SEC1"]["tools"] == AUDIT_TOOLS
    assert out["SEC2"]["tools"] == AUDIT_TOOLS  # K-ORG-052: 走形检查岗只读
    assert out["A0A"]["model"] == LITE and out["SEC2"]["model"] == SMART
    assert out["DEV2"]["tools"] == FULL_TOOLS
    n_smart = sum(1 for e in out.values() if e["model"] == SMART)
    print("selfcheck OK: %d agents (bundle %d), QA1/SEC1/SEC2 read-only, smart=%d lite=%d"
          % (len(out), n_bundle, n_smart, len(out) - n_smart))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
