# W4 MANCHONG1 bundle 漂移根因更正 v1（2026-10-31 22:40）

> 原判断“bundle 过期，重烘即可”**不成立**。重烘成功但 org gate 仍 fail=6。

## 一、实测

```text
执行 ops/rebake_ide_bundle.ps1：
  rc=0
  portal/agent_ide_bundle.json 807,493B sha=1ae6742c…
  _vault/legion_ide/agent_ide_bundle.json 同 sha
  org_model_consistency：fail 仍=6，MANCHONG1 bundle_drift 仍在
```

## 二、根因：两份真源冲突

```text
agent_platform/registry/org.json（org gate 的单一真源）
  MANCHONG1.llm_binding = "ollama/qwen3.5:9b"

agent_platform/registry/team_intel.json（bundle 构建输入）
  MANCHONG1.llm_binding = "runtime/hermes"

portal/agent_ide_bundle.json 由 team_intel 构建
  MANCHONG1.llm = "runtime/hermes"

→ bundle 与 org 必然 drift；重烘只会把 runtime/hermes 再生成一遍。
```

## 三、这不是“机械修复”，是口径选择

```text
选项 A（runtime 席口径）：
  org 改 llm_binding=runtime/hermes（或 none + runtime 说明）
  → 承认 MANCHONG1 不是 LLM 分析席，而是 Hermes 运行时数据串联席
  → 影响：D1/回测不把它当模型席；心跳/模型统计口径变化

选项 B（LLM 席口径）：
  team_intel.llm_binding 改 ollama/qwen3.5:9b
  → 承认它实际是 9b LLM 席
  → 影响：runtime 是否真有 9b 调用需核实；bundle 重烘后 drift 消失

选项 C（展示例外）：
  org gate 给 MANCHONG1 加白名单（不推荐；掩盖真源冲突）
```

## 四、其他 fail 不受影响

```text
D1 / DEV2 / OPT1 / QS1 / WA1 仍需 W4 决策；
MANCHONG1 不能靠“重烘”清零。
```

## 五、已做/未做

```text
已做：重烘 bundle（两份 sha 一致，807,493B），未提交 git
未做：未改 org.json / team_intel.json / 模型配置；未切 strict
待：W4/ARCH1 在 A/B 中拍板，再改配置并重跑 org gate
```

## 六、结论

```text
MANCHONG1 的 6 条 fail 中唯一“看似机械”的一条，实际是双真源冲突；
先拍板再改，不能把重烘当修复。
```
