# Agent 沙箱 + 内部 benchmark v1（ARCH1 实施说明）

> 工单：`WO-AGENT-SANDBOX-20261027` ｜ 日期：2026-10-27 ｜ 状态：**MVP 已实现并实跑**
> 依据：开源调研报告 §4.2 P0-8（OpenHands Docker 沙箱；TheAgentCompany 任务 benchmark）；用户 L1 批准（只读挂载+网络出口白名单方向）。

## 1. 安全基线（默认全部满足）

| 维度 | 实现 |
|---|---|
| 隔离 | `docker run --rm` 一次性容器，**不碰生产容器、不重启服务** |
| 仓库挂载 | `/workspace:ro`（只读）；仅临时 scratch `/work:rw` |
| 网络 | 默认 `--network none`（**默认拒绝**；出口白名单的当前实现=拒绝；`--network bridge` 仅排障且显式告警） |
| 权限 | `--cap-drop ALL`、`--security-opt no-new-privileges` |
| 文件系统 | 容器 rootfs `--read-only` + `/tmp` tmpfs（64MB，noexec/nosuid） |
| 其他 | `PYTHONDONTWRITEBYTECODE=1`；每命令超时，超时 `docker rm -f` 兜底 |
| 镜像 | 本地已有 `docker.1ms.run/openmetadata/ingestion:2.0.5`（含 python3.12），**不拉取新镜像** |

## 2. 工具

`agent_platform/tools/agent_sandbox.py`：

```powershell
# 单命令沙箱
python -X utf8 agent_platform/tools/agent_sandbox.py run --cmd "python3 -c 'print(42)'"
python -X utf8 agent_platform/tools/agent_sandbox.py run --cmd "..." --dry-run   # 零容器

# 10 条内部 benchmark
python -X utf8 agent_platform/tools/agent_sandbox.py benchmark --out work/sandbox/bench.json
```

- `run`：返回 rc/耗时/stdout/stderr；dry-run 只输出 docker 命令计划；
- `benchmark`：逐条在沙箱执行，按类别汇总通过率，写 JSON。

## 3. 内部 benchmark（10 条，全 stdlib/零网络）

任务文件：`agent_platform/evals/sandbox_benchmark.json`

| 类别 | 任务 | 验证 |
|---|---|---|
| SWE | SWE-01 hello / SWE-02 AST parse / SWE-03 org JSON / SWE-04 doc read | 输出/解析/只读挂载 |
| research | RES-01 golden shape / RES-02 sha256 / RES-03 网络阻断 | 数据形状/哈希/`--network none` 实际生效 |
| data | DATA-01 sqlite 写 / DATA-02 csv / DATA-03 mount | scratch 可写/标准库/只读挂载 |

## 4. 实跑结果（2026-10-27 工作树）

- 单命令：`python3 -c 'print(42)'` → **ok, rc=0, 1.0s, stdout=42**；
- benchmark：**10/10 = 100%**，SWE 4/4、research 3/3、data 3/3，总耗时约 **11.0s**；
- 网络任务 RES-03 实测输出 `net_blocked`（证明 `--network none` 生效）；
- 报告：`work/sandbox/bench.json`（gitignored 运行态）。

## 5. 测试

`agent_platform/tests/test_agent_sandbox.py` **4 passed**：
- docker 命令安全 flags（network none / ro / cap-drop / no-new-privileges / read-only）；
- dry-run 零写；
- benchmark 任务形状（10 条 = SWE4/research3/data3）；
- benchmark dry-run 不落盘。

## 6. 边界与后续

- **未做**：按主机名/IP 的出口白名单（当前=默认拒绝；需要时走代理/allowlist，另立 L2 方案）；CPU/内存配额；镜像最小化（现用本地大镜像）；把沙箱强制接入 devtask/ARCH1 执行路径。
- **不改变**：生产容器、compose、计划任务、网络配置均未动；Docker daemon 访问本身仍是宿主级能力，沙箱只限制容器内。
- **后续**：① 给 devtask/ARCH1 加 `--sandbox` 开关；② 加 `--memory/--cpus`；③ egress allowlist 代理；④ benchmark 接入 ARCH1 seat eval（作为 eval 第 6 维的一部分）。

| 版本 | 日期 | 说明 |
|---|---|---|
| v1 | 2026-10-27 | MVP 实现 + 10 条 benchmark 10/10 + 4 测试 |
