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

## 五、踩过的坑（24 条，已固化进 skill）

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
12. `du -sm` 在 26 万小文件上会超时并给出**错误总量**（报 27.3GB，实为 26.4GB）→ 统计一律走 `tree_stat.py`。
13. 单流 paramiko/SFTP 只有 1.2MB/s（不是带宽问题，是小文件 tar 流 + 单 Transport 窗口）→ 多连接并发。
14. 大族不切会卡死单个 worker（`tushare/stk_mins` 3.2GB / 33,198 平铺文件）→ `--shard-mb` 按文件清单切片。
15. 族粒度要防"碎族"（`guba/` 每票一 jsonl）→ 纯文件堆目录整体算一族。
16. sshd 默认 `MaxStartups 10:30:100` + 145ms RTT → 10 路并发必被掐（WinError 10051/10060）→ 放开到 `40:50:120` + 客户端退避回队。
17. 传输中 `du` 会**倒退**（tar 覆盖先截断），不能当进度条。
18. 数据仓里的 `quant.db`/`tdx_info.db` 会把真源软链顶成实体文件 → 传完必须重跑 `09_layout.sh` 复原。
19. `publication_guard` 只支持 Windows → 数据仓到位后 `/api/data/freshness` 必 500（见第七节）。
20. root 解包的属主让 `zeroq` 读不了 `0o600` 锁文件 → 全量 `chown -R zeroq:zeroq`。
21. `pytest.ini` 的 `--import-mode=importlib` 让同目录 `from test_x import` 失败 → 跑单文件加 `-o addopts=""`。

## 六、本次未上线（有意留白，非遗漏）

| 上游 | 端口 | 原因 |
|---|---|---|
| Hermes WebUI | 8787 | 需要 Ollama/qwen 运行时；无模型服务的 Hermes 是半瘫，无意义 |
| push hub / 推送中台 | 8097 / 8095 | 依赖飞书凭据与外网回调域名 |
| CowAgent console | 9899 | 依赖其 301MB venv，且绑桌面端 |
| Ollama | 11434 | 服务器无 GPU，CPU 推理规格需单独评估 |
| QuantDinger | 8888 / 15000 | 仅提供 docker 镜像，本机无 docker 且镜像源不可达 |

对应 nginx 路由返回 502，验证记 N/A；补齐上游后即逐条转 200。

## 七、数据仓全量同步（已完成）

`quant_backtest/data` = **26,378.7 MB / 263,742 文件**，已全量落到服务器并与本地一致。

| 项 | 结果 |
|---|---|
| 传输 | 596 个单元 / 18,232 MB / **零失败** / 6384s（3 路并发 2.6–3.7MB/s） |
| 一致性 | 552/554 族字节一致；仅 2 个 0 字节文件（`quant.db-shm`、`.warehouse-publication.lock`）按设计不传 |
| 可读性 | 抽样 30 个 parquet **全部可读**（672 万行，RESULT PASS）；catalog 登记 230 表 |
| 属主 | `find ! -user zeroq` = 0 |

工具链（已固化进 skill）：`tree_stat.py`（族级统计）→ `diff_stat.py`（差异）→ `push_par.py`
（族级分片 + 大族按文件清单切片 + 连接退避与单元回队 + 断点续传）。

### 7.1 同步暴露的一个真实缺陷（已修复）

数据仓一到位，`/api/data/freshness` 从 200 变 500：

```
RuntimeError: 发布锁仅支持Windows LockFileEx        # quant_backtest/warehouse_commit.py:75
```

`publication_guard` 只实现了 Windows `LockFileEx`，非 Windows 平台**主动拒绝**（原作者意图是"宁可不锁，
也不能假装有锁"）。但数据仓消费者全部要走这把锁，等于 Linux 侧整个数据面不可用。

修复方式不是绕过锁，而是**补上语义等价的 POSIX 实现**：`fcntl.flock` 的 `LOCK_SH`（共享读）/
`LOCK_EX`（排他写）、读锁拿到后再 `_ensure_clear`（与 Windows 分支同构）、进程崩溃由内核释放。
并用 `lock_probe.py` 做了反向验证——写锁持有 8 秒期间，读锁实测 **waited 4.97s / VERDICT BLOCKED_AS_EXPECTED**，
证明拿到的是真锁。Windows 分支一行未改。

> 附带发现：root 经 tar 解包生成的 `0o600` 锁文件 `zeroq` 读不了（`Permission denied`），
> 必须对数据仓做一次全量 `chown -R zeroq:zeroq`。

### 7.2 最终验收（数据仓 + 锁修复之后复跑）

```
验证汇总: PASS=55  FAIL=0  N/A=7        RESULT: ALL GREEN
```


## 八、交付物

| 文件 | 说明 |
|---|---|
| `C:\Users\t\.workbuddy\skills\zeroq-server-deploy\SKILL.md` | **部署 skill**：11 段流程（00 预检 + 10 段主流程）+ 验收门 + **24 条坑** + 扩展位 + 回滚 + 断线收尾入口 |
| `…\zeroq-server-deploy\scripts\` | **30 个脚本**：16 个 shell（`00_preflight` 预检 + 10 段主流程 + 07b 日志修正 + 99_resume 断线收尾 + **`12_cowagent`** + `payload/preflight_remote.sh`）+ 13 个 Python（d.py / snap_dbs.py / make_*.py / tree_stat / diff_stat / push_par / verify_parquet / lock_probe + payload 的迁移器/nginx 补丁/`warehouse_commit.py` 修复版）+ 1 个 nginx 站点模板 |
| `…\zeroq-server-deploy\references\findings.md` | 首次部署实证记录（时间线、失败原文、修复、数据仓终态、加固实证） |
| `C:\项目\.workbuddy\deploy\build\cowagent_code.tgz` | **CowAgent 部署包**（2.34 MB / 699 文件，已剔除 141MB Windows venv；网络恢复即可直推） |
| `C:\项目\deliverables\zeroq-server-deploy-skill-20261017.tar.gz` | **skill 快照归档**（77 KB / 32 文件）——skill 位于用户级目录、不在 git 下，此档为防丢副本 |
| `C:\项目\老田的AI助手\quant_backtest\warehouse_commit.py` | **跨平台发布锁修复**（POSIX `fcntl.flock` 分支，本地与服务器同步） |
| `C:\项目\deliverables\zeroq-server-deploy-20261017.md` | 本报告 |
| 服务器 `/home/zeroq/deploy/` | 全部脚本副本（现场可复跑） |
| 服务器 `/home/zeroq/db_migration/` | `src_counts.json`、`manifest_p1.json`、`validation_p1.json` |

**下次部署**：直接说「用 zeroq-server-deploy 部署到 xxx」。**第一个动作永远是 `00_preflight.sh`**
（只读，1 分钟），它判定「全新 / 已部署 / 半成品」，分别路由到 01 起、`99_resume.sh`、或补段修复。
全流程实测约 2 小时（不含数据仓 1h46m）。

## 九、收尾状态与遗留

### 9.1 代码修复已入仓

| 项 | 值 |
|---|---|
| commit | `e3e5fc7` — publication_guard 补 POSIX 分支（fcntl.flock 替代仅 Windows 的 LockFileEx） |
| 变更 | 1 file changed, 28 insertions(+), 2 deletions(-) |
| 回退基线 | `git revert e3e5fc7`（仅影响 Linux 侧，Windows 分支零改动） |

### 9.2 终态快照（最后一次成功探测：21:40）

```
zeroq-postgresql  active/enabled   PG 15.19 @ 127.0.0.1:5432
zeroq-portal      active/enabled   portal_api @ 127.0.0.1:8091
nginx             active/enabled   :80 → 73 条 location 反代
/  →200   /api/health →200   /api/data/freshness →200   /api/unified/projection →200
验证汇总: PASS=55  FAIL=0  N/A=7   →  RESULT: ALL GREEN
数据仓: 26,433 MB 就位，软链三种库均指向 /home/zeroq/data/sqlite/
```

### 9.3 唯一未完成的动作（不影响功能）

`warehouse_commit.py` 在提交时顺带修了一句过期 docstring（原文写"本候选只支持
Windows，其他平台拒绝"，已改为"Windows 走 LockFileEx，POSIX 走 fcntl.flock，语义等价"）。
这句只改注释、不改逻辑，服务器上跑的是功能完全相同的上一版。

**原因**：同步这一刻本机网络从 `10.255.3.168` 切到了手机热点 `172.20.10.9`，
`10.205.0.118` 属原内网网段，热点无路由 → ping 100% 丢包 / TCP 22 超时（见 skill 判例 22）。

**回到原网段后一条命令补齐**（`99_resume.sh` 已把这套动作封装，含 md5 比对与复验）：

```bash
bash C:/Users/t/.workbuddy/skills/zeroq-server-deploy/scripts/99_resume.sh
```

### 9.4 离线加固（22:23–22:45，本机在 192.168.0.9 家用网，服务器不可达）

不可达期间做完三件不依赖服务器的事：

| 动作 | 结果 |
|---|---|
| 静态自检 | 14 个 shell `bash -n` 全通过；13 个 Python `py_compile` 全通过 |
| **新增 `00_preflight.sh`** | 部署前只读预检：可达性 → 远程事实 → 前提核对 → 判定全新/已部署/半成品，并直接告出下一步该跑什么。**已用真实不可达网络验证：exit 2、准确指出"只有默认网关 192.168.0.1、无 10.205 明细路由"** |
| 修过期文档 | `findings.md` 第 5 节原写"数据仓 27.3GB，已传 1.8GB 剩余续传"（早已全量完成）→ 重写为终态：596 单元 / 18,232 MB / 零失败 / 552-554 族一致 / 30 抽样可读；补第 6 节加固实证、第 7 节两次失联复盘 |
| skill 归档 | `zeroq-server-deploy-skill-20261017.tar.gz`（31 文件）——skill 不在 git 下，补一份快照 |

**新判例（已写入 SKILL.md 第 23/24 条）**：

- `cmd //c "route print <IP>"` 在受管沙箱里被拦截并**挂起**（整条命令 SIGTERM、零输出），
  看起来像"脚本坏了"而非"服务器不可达" → 诊断一律改 `netstat -rn | grep`。
  **排障工具本身必须是"不可能被环境吞掉"的实现**，否则"查不出问题"会被误读成"没有问题"。
- 判定"已部署"不能只看目录存在，要看 `unit=active/active` + 端口 `LISTEN` 双证据；
  而只要 `apps/zeroq` 与 `data/sqlite` 都在，**绝不能重跑 01-08**（会覆盖配置、重建 schema、按旧快照重灌 PG）。

### 9.5 CowAgent 9899 预置完成（22:50–23:05，stage 12）

把留白上游里成本最低的一个推进到"只差一个 LLM 端点"。

**推翻旧结论**：此前记录"CowAgent 依赖其 venv（301MB）、成本最低"。实测两点都需修正：

| 旧说法 | 实测 |
|---|---|
| 需带 301MB venv | 服务器用 `envs/app` 即可，**代码包仅 2.34 MB / 699 文件**（剔除 141MB Windows venv） |
| 依赖 click + requests | `requirements.txt` 有 20+ 包，但**渠道 SDK 全部惰性加载**；`channel_type="web"` 时闭包 = **12 个包**，全部命中 wheel、零编译 |

**本地实证链**（py3.11 干净 venv，非推断）：

```
pip install --only-binary=:all: 12 个包   → EXIT=0（32 个包落地，无编译）
import app                                → IMPORT_OK
python app.py                             → 端口 19899 LISTENING
GET /                                     → HTTP/1.1 200 OK
```

**产出**：`scripts/12_cowagent.sh`（stage 12）+ `cowagent_code.tgz`（2.34MB）。

脚本固化三条安全红线（源码默认值在生产上都不能用）：

| 项 | 源码默认 | 脚本 | 风险 |
|---|---|---|---|
| 监听 | `web_host=""` + 有密码 → **0.0.0.0** | `127.0.0.1` | 9899 直接对公网开放，仅一个弱口令 |
| Agent 权限 | `full-access` | `workspace-write` | 模型可写整机任意文件 |
| 自进化/工作区 | `~/cow` + `true` | `state/cow` + `false` | Agent 自动改自身代码 |

config 改写逻辑已离线验证：三遍幂等（密码不变）、无端点时 `knowledge=false` 避免刷错、
注入端点后 `model/base/bot_type/provider` 全部正确。

**唯一卡点**：LLM 端点。本机指向 `127.0.0.1:11434`（Ollama），服务器没有。三条路实测：

| 方案 | 判定 | 依据 |
|---|---|---|
| 云端 API key（DeepSeek 等） | ✅ 推荐 | 注入 `COW_LLM_API_*` 即可 |
| 本地小模型 llama-cpp-python | ❌ 否决 | PyPI 0.3.36 **只有 sdist（76.6MB），无 Linux wheel**；服务器无 gcc + glibc 2.17 编不了 |
| 反代回本机 Ollama | ❌ 否决 | 本机非常开服务端，且当前不同网段 |

`10_verify.sh` 的 E 段同步增强：9899 已在跑时 `/cowagent/` 转为**硬门**（502 记 FAIL 而非 N/A）。
