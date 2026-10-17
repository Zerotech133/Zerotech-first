# A股量化情报工作站 · 端口台账

> **维护约定：新增任何服务（容器 / 宿主进程）占用端口前，必须先在本文件登记一行，再启动服务。**
>
> 本表全部条目于 2026-10-16 用 `netstat -ano` 逐项实测核验（LISTENING 状态 + PID 归属），
> Docker 侧经 `docker ps` 端口映射交叉确认。宿主 Docker 端口由 `com.docker.backend`
> (PID 28000) 与 `wslrelay` (PID 17936) 代为监听，属正常现象。

## 总览速查

| 端口 | 归属 | 业务 | 实测状态 |
|-----:|------|------|----------|
| 3307 | quant-cainiao-mysql (容器) | 菜鸟量化 MySQL（新机映射宿主 3307→容器 3306） | ✅ LISTENING |
| 5432 | docker-infinity-1 (容器) | **RAGFlow Infinity 向量库**（⚠️坑点：占用了 Postgres 默认端口，PgSQL 全家已被迫改用 544x 段） | ✅ LISTENING |
| 5442 | quant-pg (容器) | 量化主 Postgres（5432→5442 避让） | ✅ LISTENING |
| 5443 | quant-system-pg (容器) | 量化系统 Postgres | ✅ LISTENING（台账补充项） |
| 5444 | bettafish-db (容器) | BettaFish Postgres | ✅ LISTENING |
| 5446 | zhice-db (容器) | 知策 Postgres | ✅ LISTENING |
| 5455 | docker-mysql-1 (容器) | RAGFlow 栈 MySQL | ✅ LISTENING |
| 6333 | （无） | Qdrant —— **新机不存在，评审结论：不引入** | ❌ 未监听（预期） |
| 6379 | docker-redis-1 (容器) | RAGFlow 栈 Redis（valkey/valkey:8） | ✅ LISTENING |
| 6380 | zhice-redis (容器) | 知策 Redis（valkey/valkey:8） | ✅ LISTENING |
| 7474 | neo4j-kg (容器) | Neo4j HTTP 管理界面（仅绑定 127.0.0.1） | ✅ LISTENING |
| 7687 | neo4j-kg (容器) | Neo4j Bolt 协议（仅绑定 127.0.0.1） | ✅ LISTENING |
| 7501-7504 | bettafish (容器) | BettaFish 应用（8501-8503 Streamlit / 5000 后端，台账补充项） | ✅ LISTENING |
| 8080 | ai-portal (容器, nginx:alpine) | 门户 Portal nginx | ✅ LISTENING |
| 8091 | python (宿主进程, PID 45392) | Portal API（仅绑定 127.0.0.1） | ✅ LISTENING |
| 8098 | （无） | jev-gate —— 当前未运行 | ❌ 未监听 |
| 8388 | docker-ragflow-server-1 (容器) | RAGFlow Web（台账补充项） | ✅ LISTENING |
| 8888 | （无） | SearXNG —— 当前未运行 | ❌ 未监听 |
| 9010/9011 | docker-minio-1 (容器, pgsty/silo) | RAGFlow 栈 MinIO S3/控制台（台账补充项） | ✅ LISTENING |
| 11434 | ollama (宿主进程, PID 42536) | Ollama LLM 服务（仅绑定 127.0.0.1） | ✅ LISTENING |
| 7890 | （无） | **JEV geo-block 代理端口 —— 当前未运行**（需要科学上网时再启） | ❌ 未监听（预期） |
| 23817/23820 | docker-infinity-1 (容器) | Infinity 内部通信端口（台账补充项） | ✅ LISTENING |

## 坑点与备注

1. **5432 被 RAGFlow Infinity 占用**：Infinity 容器（docker-infinity-1）把宿主 5432 映射掉了。
   因此本机所有 PostgreSQL 一律走 544x 段（5442/5443/5444/5446），**不要**假设 5432 是 Postgres。
2. **3307 归属已落地**：quant-cainiao-mysql 已上线并监听 0.0.0.0:3307（并行会话所建），连接串
   走 `127.0.0.1:3307`。注意 0.0.0.0 暴露在局域网可达，若只本机用建议改为 127.0.0.1 绑定。
3. **仅本机回环的服务**：neo4j(7474/7687)、portal api(8091)、ollama(11434) 只绑 127.0.0.1，
   局域网不可达，属安全配置，勿改成 0.0.0.0。
4. **6333/Qdrant**：新机不存在该服务，评审结论为不引入（向量需求由 Infinity 承担）。
5. **7890/JEV 代理**：geo-block 场景代理端口，按需启动；未运行时依赖外网的采集任务会失败。
6. **8098 jev-gate / 8888 SearXNG**：登记在案但当前未运行；重启服务后请回填状态。
