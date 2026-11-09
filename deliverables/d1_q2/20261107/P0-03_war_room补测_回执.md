# P0-03 war-room 单接口补测回执

- 日期：2026-11-07
- 批准窗口：**21:30–21:40 +08:00**
- 实际执行：**21:29:40–21:30:55 +08:00**
- 批准记录：[approval.md](P0-03_war_room_drill_20261107_2130/approval.md)
- 执行结果：`ok=true`、`restored=true`、`aborted=false`、耗时 **75.1s**

## 1. 范围

仅摘除 8734 门户上的：

- `GET /api/war-room/snapshot`
- 同路径带 query 的请求

未摘 `/api/d1/*`、`/d1` UI、`/api/health`；8091 直连 `/api/war-room/snapshot` 保持可读。未重启 8091/8789/Watch，未写 PG/paper/生产账本。

## 2. 结果矩阵

| 检查 | 摘除期间 | 恢复后 |
|---|---|---|
| 8734 `/api/war-room/snapshot` | **503** | **200** |
| 8734 `/api/war-room/snapshot?probe=1` | **503** | **200** |
| 8091 直连 war-room | **200** | **200** |
| 8734 `/api/d1/decisions` | 200 | 200 |
| 8734 `/api/health` | 200 | 200 |
| 8734 `/d1` | 302 | 302 |

- 摘除期间 host/container nginx hash：`1c44bc9a5fb7d4dae91bc68a7f8bbc09a9737827ca3b33ae75b7763bc831794e`
- 恢复后 host/container hash：`5f80ffa665d06b474e9acac651312dbcb0e53ba8e7d8f35fd33d4a879c1378ac`
- 数据哈希前后不变：
  - decisions `32b57ebef63524b6baac11b976cb99e0c4c938d3636a76c313a08c80a404762c`
  - handoffs `f036704e7716779caeb8c740ec1fe854ef53838d50128ad29b59d20a819fb2a6`
  - paper `ff5195ea0df580513e1f27d47b986eccaa016cd0bba017941368bc529327f9cb`

## 3. 产物与哈希

| 文件 | 大小 | SHA-256 |
|---|---:|---|
| `approval.md` | 1913 | `9985e7c08ca227797ec472abc76a7a67ee63a752574fe472e40ac6e896c07de8` |
| `approval.md.sha256` | 79 | `84aa6843ca28c297d44cffec8093fb754260c7aff928593820c533de571cc314` |
| `nginx.conf.before` | 64015 | `5f80ffa665d06b474e9acac651312dbcb0e53ba8e7d8f35fd33d4a879c1378ac` |
| `baseline_before.json` | 3267 | `47910cd08684421280aa40098b8fc382df415c26ca28f1c7cd8131ef3c9bd2bd` |
| `drill_result.json` | 5260 | `381ef01d30e55b6d43c611db9c8950ed4b6a9c3af3c9025533c5b56e4488131c` |
| `timeline.log` | 1109 | `3633a59ff667c5760ed4fbf050268a49a9d7bdd39fd86762184b93f0363ecc1e` |
| `run_war_room_drill.py` | 11726 | `4b8e92faefdd9305b7b70a80a0a522acce115194e16714cafd64411818b3c1ef` |

## 4. 边界

- 这是 **8734 入口层**的临时摘除/恢复演练，不是停 8091 的进程级故障演练。
- 只验证 GET 路径及 query 变体；未发 POST。
- 至此 P0-03 的 9 个 active D1 接口在 8734 的暴露矩阵已补齐；此前 8 条由 20:30 完整演练覆盖，本条覆盖第 9 条 `/api/war-room/snapshot`。
- 不改写完整 P0-03 的 `last_drilled`；以 `status.rollback.war_room_drill` 单独登记。
