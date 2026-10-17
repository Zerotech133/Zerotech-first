# K-STOCK-416 · config.json db 端口漂移修复（5432→5446）

日期：2026-10-17 · 线：quant · 状态：已修复待 merge

## 漂移

`zerobound-kg/config.json` db 段 `port: 5432`，与 zhice-db 容器实际宿主映射不符。

## 证据链

1. 容器映射（决定性证据）：
   `docker port zhice-db` → `5432/tcp -> 0.0.0.0:5446`、`5432/tcp -> [::]:5446`
2. 宿主 5432 亦有监听（wslrelay + com.docker.backend），但非 zhice-db——config 写 5432 会连错对象。
3. 消费方（读 `config["db"]` 建连）：
   - `zerobound-kg/chanlun_data.py:31`（DB_CFG → psycopg2 connect / psql）
   - `zerobound-kg/data_loader.py:14`（同款）
4. 修复后连通验证：按 data_loader 同款加载方式直连 `127.0.0.1:5446/zhice_os_v2`，`information_schema.tables = 276`，OK。

## 修复

- `zerobound-kg/config.json`：db.port 5432 → 5446（仅此一处，最小手术）
- 嵌入仓提交：`92aa9b7`（zerobound-kg 独立仓，信息带工单号）

## 关联

- 数仓研究方案（管理/对接/治理/AI互补）中的既定后续项，用户 2026-10-17 批准执行。
- 同日 K-STOCK-415（datamart HTTP 门面）merge 被他线未提交 portal 重构阻塞，本单 merge 同因同候；巡检任务 4135a373 门绿后一并收口。
