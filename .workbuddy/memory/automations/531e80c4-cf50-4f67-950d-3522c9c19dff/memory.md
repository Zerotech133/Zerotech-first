# DB dump 每日自动化 — 执行记录

## 2026-10-12 02:30
- rc=0，must_fail=0，必保 4 库全 ok=true：cainiao_mysql 169MB / zhice_pg 415MB / quantdinger_pg 645KB / nqs2_pg 700KB
- Neo4j 两项按预期失败（community 停库窗口），不计失败；产物 backups/db/2026-10-12，轮转无清理项
- 状态：成功，manifest 已核验

## 2026-10-11 02:30
- rc=0，必保 4 库全 ok=true：cainiao_mysql 169MB / zhice_pg 413MB / quantdinger_pg 645KB / nqs2_pg 699KB
- Neo4j 两项按预期失败（community 停库窗口），不计失败；产物 backups/db/2026-10-11，轮转无清理项
- 状态：成功（manifest must_fail=0）

## 2026-10-10 02:30
- 命令：`python ops/db_dump_daily.py`（managed python 3.13.12），目录 E:\老田的AI助手
- 退出码 rc=0，耗时约 1m42s
- 结果：必保 4 库全部 ok=true
  - cainiao_mysql: ~169 MB (177,164,689 B)
  - zhice_pg: ~412 MB (431,620,496 B)
  - quantdinger_pg: ~645 KB
  - nqs2_pg: ~437 KB
- Neo4j 两项（kg_neo4j / zerobound_neo4j）按预期失败（community 版停库窗口限制），不计失败
- 轮转：无超 14 天旧目录；产物目录 backups/db/2026-10-10，将随 03:00 backup_daily 链收走
- 状态：成功
