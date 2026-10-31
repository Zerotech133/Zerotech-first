# run_shadow 进度 flush 与节点级 trace（WO-INTEL-SHADOWTRACE-20261031）

## 背景

A/B 预跑时 `run_shadow` 在隔离根 10 分钟无 ledger、`last_run.log` 0 字节；
py-spy 显示主线程等 `interp.run`，两个节点线程等 `llm.chat` HTTP 响应。
旧实现 stdout 被重定向到 `last_run.log` 后未 flush，无法判断卡在 T1/T2/N1/F1/D1。

## 改动

1. `agent_platform/engine/interpreter.py`
   - 新增 `_node_trace()`，仅 `SHADOW_NODE_TRACE=1` 输出并 flush；
   - `_single` 打 `start/done node=... model=... elapsed=...`；
   - `_debate` 打开场 T1/T2 的 start/done。
2. `agent_platform/run_shadow.py`
   - `main()` 设置 `SHADOW_NODE_TRACE=1`；
   - start/rehearsal/context/symbol/interp done 使用 `_log(..., flush=True)`。
3. `tools/shadow_node_probe.py`
   - 单节点 smoke：`--node T1`、`--all`、`--timeout`、`--json`；
   - 只调用 LLM，不读 news.db、不写 ledger、不抓行情；
   - 超时/异常 fail-visible，退出码 1。

## 使用顺序（A/B 前）

```powershell
# 1) 单节点
python tools/shadow_node_probe.py --node T1 --timeout 60 --json
python tools/shadow_node_probe.py --node D1 --timeout 60 --json

# 2) 逐步扩到 5 节点
python tools/shadow_node_probe.py --all --timeout 120 --json

# 3) 单臂 run_shadow（错峰）
$env:INTEL_TO_N1='0'; $env:INTEL_TO_D1='0'
python -u agent_platform/run_shadow.py --rehearsal --root C:\Temp\shadow_probe\off --codes 001380
```

观察 `shadow/last_run.log` 的 `[node-trace]`，定位具体节点后再跑 on 臂。

## 边界

- 不改变模型、不改变 prompt、不改变账本 schema；
- `SHADOW_NODE_TRACE` 默认仅 run_shadow 开；其他调用方保持静默；
- 单节点 probe 不能替代质量 A/B，只证明节点可响应；
- 两臂 ledger 成功前不做 `--compare`、不写 A/B 结论。

## 回滚

`git revert` 本单即可；trace 关闭只需 `SHADOW_NODE_TRACE=0`。
