# K-STOCK-418：OM 补 Neo4j/MySQL 连接器施工记录

- **状态**：MySQL 段完成（两服务全绿）；Neo4j 段受阻（OM 2.0.5 无 Neo4j 连接器）
- **日期**：2026-10-17
- **施工方**：Trae 会话（用户直接指示「执行吧」，未走认领流程）

## 1. 结论摘要

| 目标 | 结果 | 说明 |
|---|---|---|
| `mysql_thunder_restore`（quant-mysql@3306 容器名） | ✅ 完成 | thunder_model 233 表入库 OM，列级 schema 齐全 |
| `mysql_cainiao_live`（quant-cainiao-mysql@3306 容器名） | ✅ 完成 | thunder_model 233 表入库 OM，列级 schema 齐全 |
| `neo4j_kg`（neo4j-kg:7687） | ⛔ 受阻 | 实证：OM 2.0.5 ingestion 镜像 **无 Neo4j 连接器**（无 `neo4jConnection` schema、无 neo4j source），无法创建服务 |

## 2. 网络施工（已完成）

三容器幂等接入 `openmetadata_app_net`：

```powershell
docker network connect openmetadata_app_net neo4j-kg
docker network connect openmetadata_app_net quant-mysql
docker network connect openmetadata_app_net quant-cainiao-mysql
```

接入后网络成员（新分配 IP）：

- `neo4j-kg` → 172.16.240.7
- `quant-mysql` → 172.16.240.8
- `quant-cainiao-mysql` → 172.16.240.9

ingestion 容器内验证（DNS + TCP 全通）：

```text
neo4j-kg 172.16.240.7  7687 OK
quant-mysql 172.16.240.8  3306 OK
quant-cainiao-mysql 172.16.240.9  3306 OK
```

不新增宿主端口、未重建容器。**Neo4j 网络接入已执行**——因 OM 不支持 Neo4j 连接器暂无用武之地；保留（OM 升级后可直接使用），如需回收可执行 `docker network disconnect openmetadata_app_net neo4j-kg`。

## 3. 只读账号（已完成）

两个 MySQL 实例分别创建只读账号 `om_ro@'%'`：

- 权限：`GRANT SELECT, SHOW VIEW ON thunder_model.*`
- 无任何写权限
- 实测验证：`SELECT` 可读（stock_basic 5480 行）；`INSERT` 被拒（`ERROR 1142 command denied`）
- 密码受控，未入库、未入施工单、未入事件总线；已随临时脚本一并清理
- 凭据管理备注：现为双实例同密码；后续如需长周期治理可轮换或按实例区分

## 4. OM 服务与 ingestion（已完成）

通过 `metadata ingest -c <yml>`（容器内执行）创建两个 DatabaseService 并完成 metadata-only ingestion：

| 服务名 | serviceType | ingestion 结果 | 说明 |
|---|---|---|---|
| `mysql_thunder_restore` | Mysql | Processed 236→240，errors 0 | 源 `quant-mysql:3306`，schema `thunder_model` |
| `mysql_cainiao_live` | Mysql | Processed 236→240，errors 0 | 源 `quant-cainiao-mysql:3306`，schema `thunder_model` |

- 仅 metadata ingestion（DatabaseMetadata），未开 profiler / usage / data insights
- 连接配置经 `authType.password`（OM 2.0.5 字段语义），首版直写 `password` 字段导致「Error loading metadata configuration」解析失败，修正后通过
- 既有 Datalake 服务 `kg_warehouse`（tushare 511 parquet）不受影响

## 5. 验收（已完成）

REST 资产树接口核验：

```text
== mysql_thunder_restore ==
  database: default | mysql_thunder_restore.default
    schema: thunder_model
    tables: 233  sample=[ai_api_key, ai_chat_conversation, ...]
    col check on ai_api_key: 12 columns, e.g. (id, BIGINT), (name, VARCHAR), (api_key, VARCHAR)
== mysql_cainiao_live ==
  database: default | mysql_cainiao_live.default
    schema: thunder_model
    tables: 233  sample=[ai_api_key, ai_chat_conversation, ...]
    col check on ai_api_key: 12 columns, e.g. (id, BIGINT), (name, VARCHAR), (api_key, VARCHAR)
```

- ✅ 两服务可 Test Connection（ingestion 零错误即连接+抓取成功）
- ✅ 均出现 `thunder_model` 及 233 表（与源实例 `information_schema` 表数 233 完全一致）
- ✅ 列级 schema、类型、基本元数据齐全

## 6. 受阻项与观察

### 6.1 Neo4j（K-STOCK-418 未完成项）

- 证据：ingestion 容器 `/home/airflow/.local/lib/python3.12/site-packages/metadata/generated/schema/entity/services/connections/database/` 目录中无 `neo4jConnection`；`metadata/ingestion/source/database/` 无 neo4j source；全库 grep `neo4j` 仅命中 amundsenConnection 的元数据引用
- 结论：OM 2.0.5 该镜像未内置 Neo4j 连接器，施工单第 5.1 节无法落地
- 可选路径（待用户拍板）：
  1. OM 升级至内置 Neo4j 连接器的版本后再补
  2. 保留 neo4j-kg 网络接入待 OM 升级（当前已接入）
  3. 若不接受网络预接入，`docker network disconnect openmetadata_app_net neo4j-kg` 回收

### 6.2 OM search API 500（已定案：调用参数过时，非故障）

- 现象：`GET /api/v1/search/query?index=table_search&...` 返回 500 `{"code":500,"message":"Failed to find index table_search"}`
- 排查：带 admin token 复现五种查询形态——`index=table_search` / `index=database_schema_search` 均 500；省略 index 的 plain search 返回 200 且命中 `_index: table_search_index`；改用正确索引名后全通
- 根因：**OM 2.0.5 的 search API 索引名已变更**，`table_search`/`database_schema_search` 是旧版本参数。正确值为 `table_search_index` / `database_schema_search_index`
- 实测验证：
  - `index=table_search_index&q=mysql_thunder_restore` → 200，命中 ai_api_key/ai_chat_conversation 等
  - `index=database_schema_search_index&q=mysql_thunder_restore` → 200，命中 thunder_model
- 结论：服务与 ES 均健康，**无需修复**；后续 OM search 调用须使用 `*_search_index` 形式的索引名

## 7. 回滚方案（备查，未触发）

- 停用/删除 `mysql_thunder_restore`、`mysql_cainiao_live` 服务：
  - OM UI/API 删除对应 DatabaseService 与元数据即可
- 撤销只读账号：`DROP USER 'om_ro'@'%'`（两实例）
- 解除网络接入：`docker network disconnect openmetadata_app_net <container>`
- 不影响既有 `kg_warehouse` Datalake 编目

## 8. 遗留与建议

- Neo4j 编目挂起（见 6.1），需用户决策
- `om_ro` 凭据长期管理：建议纳入既有凭据治理（env 外置，不入库）
- search API 500 观察项建议登记后续工单
- OM 后续如需对两个 MySQL 做 profiler/质量告警，属新单，另立
