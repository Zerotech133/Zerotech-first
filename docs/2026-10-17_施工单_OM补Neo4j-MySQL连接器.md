# K-STOCK-418：OM 补 Neo4j/MySQL 连接器施工单

- **状态**：待认领
- **建议执行方**：Hermes / 其他 AI
- **项目**：quant
- **日期**：2026-10-17
- **范围**：OpenMetadata 2.0.5 metadata-only 编目

## 1. 目标

将以下三项本地数据资产接入 OpenMetadata，完成服务注册、元数据抓取和验收：

1. `neo4j-kg`：Neo4j 知识图谱
2. `quant-mysql`：量化模型 MySQL
3. `quant-cainiao-mysql`：菜鸟线上 MySQL

本单只做元数据编目，不做数据写入、数据迁移或业务逻辑改造。

## 2. 资产基线

| 资产 | 容器 | 当前宿主映射 | OM 侧推荐访问方式 | OM 服务名 | 数据库/目录 |
|---|---|---:|---|---|---|
| Neo4j 图谱 | `neo4j-kg` | `127.0.0.1:7474`、Bolt `127.0.0.1:7687` | `neo4j-kg:7687` | `neo4j_kg` | Neo4j 默认数据库或实际发现的数据库 |
| 量化模型 MySQL | `quant-mysql` | `127.0.0.1:3308` | `quant-mysql:3306` | `mysql_thunder_restore` | `thunder_model` |
| 菜鸟 MySQL | `quant-cainiao-mysql` | `127.0.0.1:3307` | `quant-cainiao-mysql:3306` | `mysql_cainiao_live` | `thunder_model` |

### 禁止误接资产

- `5432` 的 RAGFlow Infinity 不属于本单，禁止接入或修改。
- 不得触碰 RAGFlow MySQL、RAGFlow Infinity 或其他未列出的数据库。
- Neo4j 只读，不执行 Cypher 写入、索引变更或数据清理。

## 3. 网络施工方案

OpenMetadata ingestion 容器：

- 容器：`openmetadata_ingestion`
- 网络：`openmetadata_app_net`
- OM API/UI：`127.0.0.1:8585`
- ingestion/Airflow：`127.0.0.1:18080`

目标容器目前位于默认 `bridge` 网络，且 Neo4j 宿主端口绑定为 `127.0.0.1`，因此不要依赖 `host.docker.internal:7687`。

将目标容器幂等接入 OM 网络：

```powershell
docker network connect openmetadata_app_net neo4j-kg
docker network connect openmetadata_app_net quant-mysql
docker network connect openmetadata_app_net quant-cainiao-mysql
```

施工要求：

- 执行前先检查 `docker network inspect openmetadata_app_net` 和目标容器当前网络。
- 已接入时不得重复报错，需按幂等方式处理。
- 不新增宿主端口，不修改现有端口绑定，不重建容器。
- 容器重建后需能按同样命令恢复网络接入。
- 完成后从 `openmetadata_ingestion` 容器内验证 DNS 和 TCP 连通性。

## 4. 账号与权限

### MySQL

在两个目标 MySQL 实例分别创建或确认只读账号，例如：

```sql
CREATE USER 'om_ro'@'%' IDENTIFIED BY '<受控凭据>';
GRANT SELECT, SHOW VIEW ON `thunder_model`.* TO 'om_ro'@'%';
FLUSH PRIVILEGES;
```

实际执行前必须：

- 先确认账号是否已存在，避免覆盖现有凭据。
- 记录授权范围和执行结果。
- 不授予 `INSERT`、`UPDATE`、`DELETE`、`DROP`、`ALTER`、`CREATE` 等写权限。
- 凭据不得提交到 Git、施工单或事件总线。

### Neo4j

- 使用已存在的只读账号，或按本地安全规范创建只读账号。
- 不在仓库、任务单或日志中明文记录密码。
- 连接测试只允许执行只读元数据探测。
- 不执行写入 Cypher。

## 5. OpenMetadata 配置

在 OM UI/API 中创建三个 Database Service：

### 5.1 Neo4j

- 服务名：`neo4j_kg`
- 连接器：Neo4j
- 主机：`neo4j-kg`
- 端口：`7687`
- 协议：Bolt
- 数据库：按实际发现结果填写
- 凭据：只读账号

### 5.2 量化模型 MySQL

- 服务名：`mysql_thunder_restore`
- 连接器：MySQL
- 主机：`quant-mysql`
- 端口：`3306`
- 数据库：`thunder_model`
- 凭据：`om_ro`

### 5.3 菜鸟 MySQL

- 服务名：`mysql_cainiao_live`
- 连接器：MySQL
- 主机：`quant-cainiao-mysql`
- 端口：`3306`
- 数据库：`thunder_model`
- 凭据：`om_ro`

每个服务必须先执行 **Test Connection**，测试失败不得直接启动全量 ingestion。

## 6. Ingestion 配置

首轮只开启 metadata ingestion：

- 开启：数据库、schema、表、列、类型、描述等元数据抓取。
- 关闭：Profiler。
- 关闭：Usage。
- 关闭：Data Insights。
- 关闭：任何会写入目标数据源的同步或质量任务。

执行顺序：

1. 网络和 TCP 连通性验证。
2. 只读账号登录验证。
3. OM Test Connection。
4. 小范围或单服务 metadata ingestion。
5. 查看 ingestion 日志并处理失败项。
6. 再执行其余服务。
7. 记录服务 ID、ingestion ID、开始/结束时间和结果。

## 7. 验收标准

### 网络与权限

- [ ] `openmetadata_ingestion` 可解析 `neo4j-kg`、`quant-mysql`、`quant-cainiao-mysql`。
- [ ] 三个目标服务均可从 OM ingestion 容器访问。
- [ ] MySQL 账号仅具备目标库只读权限。
- [ ] Neo4j 连接未执行任何写操作。
- [ ] 宿主端口和容器未被额外暴露或重建。

### OpenMetadata

- [ ] `neo4j_kg` 服务创建成功并可 Test Connection。
- [ ] `mysql_thunder_restore` 服务创建成功并可 Test Connection。
- [ ] `mysql_cainiao_live` 服务创建成功并可 Test Connection。
- [ ] Neo4j 节点标签和关系类型可在 OM 中检索。
- [ ] 两个 MySQL 服务均出现 `thunder_model` 及其表。
- [ ] MySQL 表具备列级 schema、类型和基本元数据。
- [ ] 三个服务的 metadata ingestion 均成功完成或有明确失败清单。
- [ ] 通过 OM search 查询服务名、数据库名和代表性表/节点实体。

OM search API 注意（2.0.5 实测，2026-10-17 定案）：`index` 参数必须使用 `*_search_index` 形式索引名——表级 `table_search_index`、schema 级 `database_schema_search_index`；旧参数名 `table_search` / `database_schema_search` 会返回 500 `Failed to find index ...`（服务健康，参数过时所致）。验收示例：

```text
GET /api/v1/search/query?index=table_search_index&q=mysql_thunder_restore&limit=5&deleted=false
GET /api/v1/search/query?index=database_schema_search_index&q=mysql_thunder_restore&limit=5&deleted=false
```

或省略 `index` 走默认聚合搜索（亦返回 200）。

### 审计留痕

施工完成后必须记录：

- 资产连接基线。
- 网络接入命令及结果。
- 账号授权范围，不记录密码。
- OM 服务配置摘要。
- ingestion 任务 ID、结果和日志位置。
- 验收查询与返回实体数量。
- 失败项、重试项和回滚结果。

## 8. 回滚方案

如 Test Connection 或 ingestion 异常：

1. 停止对应 ingestion，不继续扩大范围。
2. 保留日志和错误信息。
3. 删除本单创建的 OM 服务或禁用其 ingestion。
4. 如本单创建了临时只读账号，按审批结果撤销该账号。
5. 如需解除网络接入：

```powershell
docker network disconnect openmetadata_app_net neo4j-kg
docker network disconnect openmetadata_app_net quant-mysql
docker network disconnect openmetadata_app_net quant-cainiao-mysql
```

不得回滚或影响既有 OM Datalake 编目服务 `kg_warehouse.tushare`。

## 9. 认领与合流要求

认领后先广播 `accept` 事件，再登记任务：

```powershell
python C:\项目\老田的AI助手\tools\events.py emit `
  --kind accept `
  --task K-STOCK-418 `
  --agent <认领者> `
  --detail "认领 OM Neo4j/MySQL metadata-only 连接器施工"

python C:\项目\老田的AI助手\tools\devtask.py new K-STOCK-418 `
  "OM 补 Neo4j/MySQL 连接器：neo4j-kg 图谱 + quant-mysql/quant-cainiao-mysql 元数据编目，只读" `
  --project quant
```

施工必须在独立 worktree 完成，提交审计留痕后再走 `devtask.py merge K-STOCK-418`。

不得：

- 越过认领直接施工。
- 修改主仓其他会话未提交改动。
- 擅自重建 Mixin baseline。
- 将密码、连接串或临时 token 提交到仓库。
- 将 RAGFlow 资产混入本单。
