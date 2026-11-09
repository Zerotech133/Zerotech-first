# P0-03 回滚演练预检回执（v1）

- 日期：2026-11-07 16:22 +08:00
- 任务：`docs/plans/2026-11-06_鲁肃D1研发计划_v1.md:116-128`
- 结论：**PREFLIGHT PASS / REAL DRILL NOT PERFORMED**
- 验收状态：**不满足 P0-03**；`last_drilled` 仍为 pending，未把预检当真实回滚。

## 1. 本次做了什么

新增只读工具 `tools/d1_rollback_drill.py`：

- 默认 preflight，不改 nginx、不摘入口、不停止 8091、不写生产账本。
- 检查：manifest rollback steps、`last_drilled`、`/d1` 精确/前缀路由、登录门、页面、git HEAD/回滚目标 commit、容器 nginx 语法、manifest health_probe 端点、D1 台账/paper 基线。
- 生成 JSON、Markdown runbook，明确“批准窗口执行”的 8 步；真实摘除入口未执行。
- `--execute` 仅返回 `blocked=true` 并退出 3，不执行生产变更。

## 2. 预检结果

- git HEAD：`d165d6ff42d149be4eab6193ff138c13015b15d5`
- nginx sha256：`ddb065f24e1d4c7321c3bef9d47fc95904bc42bb00e9d2772b4c0e85ff78297f`
- 页面 sha256：`494e457a502c789539c7e5a7d56894f94da5efc34d5e039e338dfdf4bee28a78`
- rollback targets：`4788b1527`、`e15cc824b`、`b11cda7c2` 均存在
- 容器 `nginx -t`：通过
- HTTP：manifest 列出的 8 个 health_probe 端点全部 200
- 数据基线：decisions 4 行 sha256 `32b57ebe...`；handoffs 4 行 sha256 `f036704e...`
- paper：positions=0、nav=0.99464、sha256 `ff5195ea...`
- `--execute`：`EXIT_CODE=3`，未发生任何入口摘除。

## 3. 证据

`deliverables/d1_q2/20261107/`：

| 文件 | bytes | sha256 |
| --- | ---: | --- |
| `P0-03_rollback_preflight.json` | 7155 | `809f274dd4c0bdea059f122aef7c3d07f14669a7d6608d4dd546140431e6b91c` |
| `P0-03_rollback_preflight.md` | 3012 | `87b3bb168316e0453ef95a7db732308f5427ca52b8b4e877c76aaaa8719080ab` |
| `P0-03_execute_blocked.txt` | 260 | `485438b59619171114d7cb2b024717f7b3f9cb2db76868c157f893e5b4a6c8c6` |

## 4. 明确未做的事

- 未摘除 `/d1` 或 `/api/d1/*` 入口；未改 nginx；未重启 8091/8789/Watch。
- 未执行真实回滚，未开始 10 分钟计时，未伪造“恢复成功”记录。
- 未修改 `manifest.status.rollback.last_drilled`（保持 pending）。

## 5. 真实演练前置条件

1. Lead 指定维护窗口并明确授权“可摘除入口 10 分钟”。
2. 演练前保存 nginx/台账/paper 哈希和 git HEAD。
3. 按 runbook 摘除入口 → 验证 `/d1`、`/api/d1/*` 不可用且其他入口正常 → 10 分钟内恢复 → 复核基线哈希不变 → 回填 `last_drilled` 与恢复时间。
4. 任一验证失败立即恢复，不把 dry-run/preflight 记为演练通过。
