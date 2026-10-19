# K-ORG-169 ｜ airflow_db 表清单归属厘清

> 2026-10-18 ｜ Trae-CN ｜ 会话延续任务（memory topics.md 2026-10-18 06:44:51）：「确认 airflow_db 的表清单归属，需厘清 200 条表清单中混入的 ACT_*/QRTZ_*/FLW_* 等 airflow 内部表的归属范围」
> 用户澄清口径：清单出处 = **OpenMetadata 编目结果**；airflow_db = **泛指调度/工作流引擎内部表**（QRTZ_*/ACT_*/FLW_* 类）

---

## 一、结论速览

| 对象 | 数量 | 归属判定 |
|---|---|---|
| QRTZ_*（Quartz 调度器内部表） | **22 张**（两服务各 11） | 框架/调度引擎域：thunder-model-admin 芋道底座自带，**非证券业务资产** |
| airflow_db（Airflow 调度器元数据表） | **70 张** | OM ingestion 栈内部库，**未入业务编目** |
| ACT_*（Activiti）、FLW_*（Flowable） | **编目零命中** | 任务概要中的假设前缀，未实证，无需处置 |

**总判定**：OM 业务编目（1004 张表）中仅 QRTZ_* 为引擎内部表，且归属框架域；airflow_db 为 OM 基础设施内部库，二者均不属于证券业务资产。

## 二、取证方法

1. **OM 部署编排**：`zerobound-kg/deploy/openmetadata/docker-compose.yml` —— ingestion 服务为 Airflow 调度器（`AIRFLOW__CORE__EXECUTOR: LocalExecutor`），元数据库 `AIRFLOW_DB: airflow_db`（用户 `airflow_user`），与 OM 共用 `openmetadata_mysql` 实例（MYSQL_ROOT_PASSWORD: <REDACTED:MySQL root口令>）。
2. **实例直查**（只读）：`docker exec openmetadata_mysql mysql -uroot -p<REDACTED:MySQL root口令>`：
   - `SHOW DATABASES` → 确认 `airflow_db` 存在
   - `SHOW TABLES IN airflow_db` → 70 张 Airflow 2.x 内部表
   - `openmetadata_db.table_entity` JSON 前缀扫描 → 引擎前缀表归属
3. **前缀精确匹配**（仅对 `$.name` 字段，REGEXP `^(QRTZ_|ACT_|FLW_)`）→ 22 张全为 QRTZ_*。

## 三、OM 编目分布（openmetadata_db.table_entity 全量 1004 表）

| DatabaseService | 表数 | 说明 |
|---|---|---|
| kg_warehouse | 538 | Postgres 数仓主服务 |
| mysql_cainiao_live | 233 | 菜鸟在线 MySQL（quant-cainiao-mysql） |
| mysql_thunder_restore | 233 | thunder_model 恢复实例 |
| **合计** | **1004** | 「200 条」为检索分页/概数口径，全量以此为准 |

## 四、引擎内部表归属明细

### 4.1 QRTZ_*：Quartz 调度器内部表（22 张）

- `mysql_cainiao_live.default.thunder_model.QRTZ_*` × 11
- `mysql_thunder_restore.default.thunder_model.QRTZ_*` × 11

11 张清单：QRTZ_BLOB_TRIGGERS / QRTZ_CALENDARS / QRTZ_CRON_TRIGGERS / QRTZ_FIRED_TRIGGERS / QRTZ_JOB_DETAILS / QRTZ_LOCKS / QRTZ_PAUSED_TRIGGER_GRPS / QRTZ_SCHEDULER_STATE / QRTZ_SIMPLE_TRIGGERS / QRTZ_SIMPROP_TRIGGERS / QRTZ_TRIGGERS

**归属**：Quartz 调度器内部表，thunder-model-admin（ruoyi-vue-pro 芋道底座）自带，与证券业务无关。对齐既有结论：`deliverables/2026-10-08_菜鸟数据库三资产联动分析.md` 已归「框架/低用域（~100 表）」，本次编目复扫一致。**不纳入业务资产，编目可保留（元数据无害），无需清理。**

### 4.2 airflow_db：Airflow 调度器元数据（70 张）

Airflow 2.x 内部表族（节选）：ab_group/ab_role/ab_user/ab_user_role（Flask-AppBuilder 鉴权族）、dag/dag_run/dag_version/dag_code/dag_tag（DAG 族）、task_instance/task_reschedule/task_map（任务执行族）、xcom/log/job/trigger/variable/session/slot_pool/connection（核心元数据族）、asset_* 资产族等。

**归属**：OM ingestion（Airflow）调度器的元数据库，位于 OM 栈内部 MySQL 实例，**未出现在任何业务 DatabaseService 编目中**。属 OM 基础设施内部库，不入业务资产清单。

### 4.3 ACT_*/FLW_*：零命中

全编目（1004 表）按 `$.name` 前缀精确扫描，**无 ACT_*/FLW_* 表**。任务概要中的假设前缀未在编目中实证，无需处置。

## 五、风险与注记

- **「200 条」口径**：编目全量 1004 表（538/233/233 三服务），无恰为 200 的独立集合；按用户确认的「OM 编目结果」口径，以 table_entity 全量作为归属厘清范围，结论覆盖所有分页口径（引擎前缀表在任何分页下均为这 22 张）。
- **后续复扫**：如 OM 新增 DatabaseService（如 Neo4j 连接器补齐、quant-mysql 恢复上线），需按同一 REGEXP 前缀规则复扫确认引擎内部表归属，防止业务清单再度混入框架表。
- 本任务全程只读取证，未对 OM 编目、MySQL 实例做任何写操作。

## 六、证据来源

- `zerobound-kg/deploy/openmetadata/docker-compose.yml`（airflow_db/airflow_user 定义）
- `openmetadata_mysql` 实例直查（SHOW DATABASES / SHOW TABLES / table_entity JSON）
- `deliverables/2026-10-08_菜鸟数据库三资产联动分析.md`（QRTZ_* 归属先例）
- `backups/ingestion/snapshot_20261017/thunder_model/tables_index.md`（thunder_model 234 表索引）
