# 零界平台服务器部署交付报告

- **交付时间**：2026-10-17 15:12 – 17:10（UTC+8）
- **目标主机**：`10.205.0.118`（root）
- **部署代号**：`zeroq`
- **结论**：**核心平台已上线，数据库迁移校验通过，部署后验证 55 项全绿（0 失败）**

---

## 一、环境事实与选型

| 项 | 实测值 |
|---|---|
| 系统 | CentOS Linux 7.4.1708（kernel 3.10，EOL） |
| 硬件 | 20 vCPU / 62 GB RAM / `/` 50 GB + `/home` **2.0 TB** |
| 预装 | 无 docker、无 python3、无 gcc；yum 官方源已失效 |
| 安全 | SELinux=Enforcing、firewalld=active |
| 出网 | aliyun / tuna / pypi ✓；**github / repo.postgresql.org / download.docker.com ✗** |
| glibc | **2.17**（决定了所有二进制包的最高版本） |

**选型结论**（被出网与 glibc 双重约束逼出来的）：

- Python 3.11 + PostgreSQL 15 → **Miniconda（conda-forge 二进制）**，不编译、不用 docker；Miniconda 必须用 **4.12 代**（新版要求 glibc ≥ 2.28）。
- nginx → **EPEL 7 RPM**（1.20.1），yum 源切到 **阿里云 centos-vault 7.9.2009**。
- 部署根 **`/home/zeroq`**（2TB 数据盘），系统盘 50GB 只放 OS。

## 二、上线架构

```
浏览器 ──:80──> nginx（/etc/nginx/conf.d/zeroq.conf，73 条 location）
                  ├── webroot = /home/zeroq/apps/zeroq/portal（静态页）
                  └── /api/ /bt /kg /gzh /chanlun-* /qd /ollama/ … → 127.0.0.1:8091
                              │
          zeroq-portal（systemd）= portal_api.py（纯标准库 http.server，23k 行巨石）
                              ├── SQLite  /home/zeroq/data/sqlite/*.db（应用侧软链）
                              └── PostgreSQL 15  /home/zeroq/data/pgdata（127.0.0.1:5432）
                                    zeroq 库 → schema: news / quant / tdx_info / platform
```

三个 unit 全部 `enabled`（重启自恢复）：

| unit | 端口 | 说明 |
|---|---|---|
| `zeroq-postgresql` | 127.0.0.1:5432 | PG 15.19，8GB shared_buffers，LC_COLLATE=C |
| `zeroq-portal` | 127.0.0.1:8091 | portal_api，日志 `logs/portal_api.log` |
| `nginx` | 0.0.0.0:80 | 反代 + 静态站点，`*.py/*.conf/*.md` 一律 404 防源码泄露 |

## 三、数据库迁移（核心交付）

**方式**：用 SQLite 在线 backup API 取一致性快照（不与定时任务抢锁），复用项目自带
`db_migration/sqlite_to_pg.py`（生成 Linux 版：路径与口令改环境变量），COPY 批量装载 → 深度校验。

| 源库 | 体积 | 目标 schema | 表数 | 行数 |
|---|---:|---|---:|---:|
| `news_collector/news.db` | 42.3 MB | news | 5 | 78,513 |
| `quant_backtest/data/quant.db` | 182.7 MB | quant | 6 | 24,064 |
| `quant_backtest/data/tdx/tdx_info.db` | 203.6 MB | tdx_info | 3 | 588,255 |
| **合计** | **428.6 MB** | | **14** | **690,832** |

- **校验结论**：`sqlite_to_pg.py validate` → **OVERALL: PASS**
  （14/14 表行数一致；每库两大表 PK 统计 + 抽样逐字段比对；无主键表全量有序流式比对 0 差异）
- **PG 侧体积**：338 MB
- **SQLite 真源保持可写未改动**——PG 是快照镜像。回滚只需 `DROP SCHEMA news,quant,tdx_info CASCADE`。
- 另有 7 个小账本库（checkpoints/evidence/approvals/artifacts/paper/portal/xueqiu）原样迁移到
  `/home/zeroq/data/sqlite/`，应用侧按原相对路径软链/复制，**代码零改动**。

## 四、部署后验证（`10_verify.sh`）

| 组 | 结果 | 内容 |
|---|---|---|
| A 基础设施 | 7/7 | 三服务 active + enabled；8091/5432/80 LISTEN；磁盘 2.0T/1% |
| B 数据库 | 3/3 | PG 15.19；**14/14 表行数一致（690,832 行）**；338MB |
| C 门户 API | 12/12 | health / home / overview / unified-projection / platform-replay / data-freshness / devops / brief / unified-{evidence,knowledge,skills,usage}；`unified/replay` 无参正确返回 400（参数守卫） |
| D 门户页面 | 28/28 | 26 个页面全 200（含 /quant /intel /dev /ops /audit /approvals /market /legion /academy /paper /screen /flow /sys-health /hall /stack /ide /sandbox /nl /premarket /afterhours /algo /strategies /fin /portal /col-audit /org）+ 反代 2 + 源码防护 2 |
| E 软门 | 1 pass / 6 N/A | 上游未部署的 /mission /console /hermes/ /push/ /cowagent/ /ollama/ 记 N/A |
| F 统一投影 | 2/2 | 16 个业务域（academy/gzh/invest/dev/pitch/personal/devtask/knowledge/skill/approvals/tasks/projects…），findings=0 |
| G 单元测试 | 1/1 | `pytest agent_platform/tests` → **275 passed** |
| H 日志体检 | — | portal 日志 0 Traceback |

```
验证汇总: PASS=55  FAIL=0  N/A=7        RESULT: ALL GREEN
```

> 验证过程中发现的真实缺口：首次跑 pytest 有 **4 个 `test_real_repo_*` 用例失败**
> （`by_domain >= 1` 断言不成立）。根因是打包时 `--exclude='*/data'` 把
> `agent_platform/data/*.jsonl` 台账一并剔掉。补传 198 个台账文件后 **275/275 全绿**。
> 这 4 例是部署验证真正的价值点——它证明了"代码搬过去不等于系统搬过去"。

## 五、踩过的坑（11 条，已固化进 skill）

1. Miniconda latest 要 glibc ≥2.28 → 只能用 `Miniconda3-py39_4.12.0`。
2. pandas 3.x 无 manylinux_2_17 wheel → pip 回落源码编译 → 无 gcc 必炸；解法 `--only-binary + 锁版本`。
3. conda 4.12 解算 conda-forge 数据栈 12 分钟不收敛 → 数据栈改 pip。
4. SFTP 1.0MB/s vs `tar | ssh` 6.6MB/s → 高延迟链路禁用 SFTP。
5. 后台任务沙箱读不到 `~/.ssh` → 传输走 paramiko 密码登录。
6. Git Bash 会把 `/home/...` 改写成 `C:/Program Files/...` → `MSYS_NO_PATHCONV=1`
   （曾把 159MB 数据误投到服务器 `/root/C:`，已清理）。
7. `tar czf C:/x.tgz` 被 tar 当成远端主机 → 输出路径写 `/c/...`。
8. CentOS 7 的 systemd 219 不支持 `StandardOutput=append:` → 日志用 bash 重定向。
9. `initdb` 后 `psql` 默认拿 OS 用户名当角色 → `-U postgres`；heredoc 会吃掉密码交互 → export PGPASSWORD。
10. `--exclude='*/data'` 对"数据即状态"的平台是危险排除项（连带剔掉台账）。
11. 验证脚本的路由期望必须对着 handler / nginx 逐条核（`/api/unified/replay` 无参 400 才正确，
    `/api/flow` 不存在，页面是 `/col-audit` 不是 `/bt-view`）。

## 六、本次未上线（有意留白，非遗漏）

| 上游 | 端口 | 原因 |
|---|---|---|
| Hermes WebUI | 8787 | 需要 Ollama/qwen 运行时；无模型服务的 Hermes 是半瘫，无意义 |
| push hub / 推送中台 | 8097 / 8095 | 依赖飞书凭据与外网回调域名 |
| CowAgent console | 9899 | 依赖其 301MB venv，且绑桌面端 |
| Ollama | 11434 | 服务器无 GPU，CPU 推理规格需单独评估 |
| QuantDinger | 8888 / 15000 | 仅提供 docker 镜像，本机无 docker 且镜像源不可达 |

对应 nginx 路由返回 502，验证记 N/A；补齐上游后即逐条转 200。

## 七、数据仓同步（可续跑）

`quant_backtest/data` = **27.3 GB / 26 万 parquet 文件**，本次未全量搬运：

- 已传：`catalog.json`、`stock_names.json`、`portfolio_config.json`、`labels`、`tdx/`、`tushare`（约 1.9GB+，后台续传中）
- 工具：`push_data.py` 按顶层条目分块 + 断点续传（已完成条目记 `C:/Temp/push_data_done.txt`）
- 续跑一条命令即可，中断后重跑自动 SKIP；全量参考耗时：paramiko ≈6h，原生 ssh 管道 ≈70–90 分钟

## 八、交付物

| 文件 | 说明 |
|---|---|
| `C:\Users\t\.workbuddy\skills\zeroq-server-deploy\SKILL.md` | **部署 skill**：10 段流程 + 验收门 + 11 条坑 + 扩展位 + 回滚 |
| `…\zeroq-server-deploy\scripts\` | 11 个可执行脚本 + 4 个工具（d.py / snap_dbs.py / push_data.py / make_*.py）+ payload |
| `…\zeroq-server-deploy\references\findings.md` | 首次部署实证记录（时间线、失败原文、修复） |
| `C:\项目\deliverables\zeroq-server-deploy-20261017.md` | 本报告 |
| 服务器 `/home/zeroq/deploy/` | 全部脚本副本（现场可复跑） |
| 服务器 `/home/zeroq/db_migration/` | `src_counts.json`、`manifest_p1.json`、`validation_p1.json` |

**下次部署**：直接说「用 zeroq-server-deploy 部署到 xxx」，按 skill 的 10 段顺序执行，
每段有明确验收门；全流程实测约 2 小时（不含数据仓）。
