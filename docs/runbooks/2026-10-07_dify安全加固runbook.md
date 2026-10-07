# Dify 安全加固 Runbook（P0-3）

**日期**：2026-10-07
**依据**：上线前全检报告 P0-3（安全官 F-02）
**现状核验**：本 runbook 所有「现状」均读自 `dify/docker/.env` 与 `dify/docker/docker-compose.yaml` 实际内容（2026-10-07）
**性质**：**只读文档**——本文不执行任何变更、不重启容器；由老田在维护窗口人工执行
**预计耗时**：15 分钟 + 验证 10 分钟

---

## 0. 现状（实测值，改前对照）

| 项 | 位置 | 现状 | 风险 |
|---|------|------|------|
| DB 口令 | `dify/docker/.env:77` `DB_PASSWORD=difyai123456` | 与 compose 内置缺省值等值（`docker-compose.yaml:439` 缺省 `${DB_PASSWORD:-difyai123456}` 同族默认），**等同厂商默认口令** | 局域网任何能触到 5432/容器者可直连 Dify 主库（含全部应用数据） |
| 初始化口令 | `dify/docker/.env:35` `INIT_PASSWORD=`（空） | 控制台初始化/入口无口令保护 | 容器重建后任何人可抢先初始化成为 Dify 管理员 |
| nginx 端口映射 | `.env:300` `EXPOSE_NGINX_PORT=80`；`docker-compose.yaml:834-836` 映射 `${EXPOSE_NGINX_PORT:-80}:${NGINX_PORT:-80}`（+SSL 443，`NGINX_HTTPS_ENABLED=false`） | **0.0.0.0:80** 全网卡监听；443 同样 0.0.0.0 | 局域网任何人可直接访问 Dify |
| 依赖方 | `portal/nginx.conf:237` `/probe/dify → http://host.docker.internal:80/` | 门户健康探测依赖宿主 80 端口 | **改端口/回环绑定会打断此探测**（见 §3.3） |
| DB 服务 | compose 服务名 `db_postgres`（postgres:15-alpine，profile 含 postgresql），`DB_USERNAME=postgres`、`DB_HOST=db_postgres`、库 `dify`，数据卷持久化 | — | 改口令只需 ALTER ROLE + .env 同步，**不动卷** |

---

## 1. DB_PASSWORD 轮换

### 1.1 生成新口令

任选其一（64 位十六进制为宜）：

```powershell
# PowerShell（宿主机）
-join ((48..57)+(97..122) | Get-Random -Count 32 | % {[char]$_})
```

记为 `<NEW_PWD>`（临时记在本地密码管理器，勿进聊天/文档）。

### 1.2 在库内 ALTER ROLE（先改库，容器暂不动）

```powershell
cd E:\老田的AI助手\dify\docker
docker compose exec db_postgres psql -U postgres -d dify -c "ALTER ROLE postgres PASSWORD '<NEW_PWD>';"
```

> 时点语义：ALTER 立即生效，但**在跑的 api/worker 等容器仍持有旧口令的连接池**，已建连接不断、新连接会失败——因此 1.3 必须紧跟着做（分钟级窗口）。

### 1.3 同步 .env 并重建依赖容器

```powershell
# 编辑 dify/docker/.env 第 77 行
#   DB_PASSWORD=difyai123456  →  DB_PASSWORD=<NEW_PWD>
notepad E:\老田的AI助手\dify\docker\.env

cd E:\老田的AI助手\dify\docker
docker compose up -d        # 按 env diff 自动重建 api/worker/plugin_daemon 等持有 DB 口令的容器
```

**重启顺序说明**：`docker compose up -d` 按依赖序自动完成（db_postgres 卷数据不受影响，POSTGRES_PASSWORD 仅首次初始化生效，无需动 db 容器；若想连 db 容器 env 一并对齐，可追加 `docker compose up -d --force-recreate db_postgres`，同样不触数据卷）。**严禁 `docker compose down -v`**（会删数据卷）。

### 1.4 谁消费了这个口令（核对清单）

- `db_postgres` 本体（ALTER 已覆盖）
- dify api / worker / plugin_daemon 等（由 compose 从 `.env` 注入，`up -d` 重建后生效）
- 项目侧：已核验 `portal/`、`quant_backtest/` 未直连 Dify 的 postgres（仅经 HTTP API），无需改代码

---

## 2. INIT_PASSWORD 设置

```powershell
# 编辑 dify/docker/.env 第 35 行
#   INIT_PASSWORD=  →  INIT_PASSWORD=<另生成一个强口令，这是 Dify 控制台初始化口令>
notepad E:\老田的AI助手\dify\docker\.env

cd E:\老田的AI助手\dify\docker
docker compose up -d        # 重建 web/api 使 env 生效
```

设置后：Dify 控制台首次初始化/安装管理员页需要该口令，防止容器重建后被局域网抢先注册管理员。
若管理员账号已存在，此口令作为安装页防护仍然建议保留。

---

## 3. nginx 端口映射改回环（127.0.0.1:8082:80）

### 3.1 现值确认（勿写错）

- `.env:300` `EXPOSE_NGINX_PORT=80`（宿主端口 80）；`.env:301` `EXPOSE_NGINX_SSL_PORT=443`
- `docker-compose.yaml:834-836`：`"${EXPOSE_NGINX_PORT:-80}:${NGINX_PORT:-80}"` 与 `"${EXPOSE_NGINX_SSL_PORT:-443}:${NGINX_SSL_PORT:-443}"`
- ⚠️ **仅改 `.env` 里的 `EXPOSE_NGINX_PORT=8082` 只会变成 `0.0.0.0:8082`，仍暴露局域网**——必须同时改 compose 映射串加 `127.0.0.1:` 前缀。

### 3.2 变更步骤

```powershell
# ① dify/docker/.env
#    EXPOSE_NGINX_PORT=80       →  EXPOSE_NGINX_PORT=8082
#    （SSL 未启用：NGINX_HTTPS_ENABLED=false，EXPOSE_NGINX_SSL_PORT 建议直接注释掉）

# ② dify/docker/docker-compose.yaml  第 834-836 行（nginx 服务 ports）
#    - "${EXPOSE_NGINX_PORT:-80}:${NGINX_PORT:-80}"          →  - "127.0.0.1:${EXPOSE_NGINX_PORT:-8082}:${NGINX_PORT:-80}"
#    - "${EXPOSE_NGINX_SSL_PORT:-443}:${NGINX_SSL_PORT:-443}" →  注释掉（HTTPS 未启用）或同样加 127.0.0.1: 前缀

cd E:\老田的AI助手\dify\docker
docker compose up -d
```

### 3.3 连带影响（必读）

1. **访问地址变化**：Dify 控制台从 `http://<主机>:80/` 变为 `http://127.0.0.1:8082/`（仅本机可访问）。浏览器书签、笔记里的链接需更新。
2. **门户健康探测会 502**：`portal/nginx.conf:237` `/probe/dify` 经 `host.docker.internal:80` 探测——回环绑定后**容器无法经宿主网关 IP 触达 127.0.0.1 端口**，该探测固定 502。三选一：
   - a.（推荐）把 Dify 健康探测挪到宿主侧：`ops/health_check.py` 的 `check_services()` 加一条 `Dify(8082): _http_ok("http://127.0.0.1:8082/")`，并删除/注释 nginx.conf 的 `/probe/dify` location 后 `nginx -s reload`；
   - b. 保留 nginx.conf 但接受门户健康页 Dify 恒 502（不推荐，制造假警报）;
   - c. 折衷：Dify 端口映射写宿主内网 IP（如 `192.168.0.3:8082:80`）并配防火墙限源——归入 P0-1 防火墙方案一起做。
3. 改动落在 `dify/docker/.env` 与 `dify/docker/docker-compose.yaml` 两个文件，**改前先 git 提交一份当前态**（.env 若在 .gitignore 中，先手工备份 `docker/.env.bak-20261007`）。

---

## 4. 验证（变更后 10 分钟内完成）

```powershell
cd E:\老田的AI助手\dify\docker

# 1) 容器齐活
docker compose ps          # 全部 Up / healthy

# 2) DB 新口令可用
$env:PGPASSWORD='<NEW_PWD>'; docker compose exec db_postgres psql -U postgres -d dify -c "select count(*) from accounts;"

# 3) 旧口令已失效（应认证失败）
$env:PGPASSWORD='difyai123456'; docker compose exec db_postgres psql -U postgres -d dify -c "select 1;"
# 期望：password authentication failed

# 4) Dify Web 仅本机可达
curl.exe -I http://127.0.0.1:8082          # 期望 200/307
curl.exe -I http://192.168.0.3:8082        # 从局域网另一台机器执行，期望连接拒绝

# 5) 控制台初始化口令生效
# 浏览器开 http://127.0.0.1:8082/install —— 应要求输入 INIT_PASSWORD

# 6) 门户面
# 按 §3.3 选择处理 /probe/dify 后，门户健康页无新增红灯
```

## 5. 回滚

| 变更 | 回滚动作 |
|---|---|
| DB 口令 | `docker compose exec db_postgres psql -U postgres -d dify -c "ALTER ROLE postgres PASSWORD 'difyai123456';"` + `.env` 还原旧值 + `docker compose up -d` |
| INIT_PASSWORD | `.env:35` 还原为空 + `docker compose up -d` |
| 端口映射 | 还原 `.env:300` 与 `docker-compose.yaml:834-836` 原文 + `docker compose up -d`（并按 §3.3 还原 probe） |

全程数据卷不动，回滚零数据风险。

---

## 6. 执行清单（打勾用）

- [ ] git/手工备份 `.env` 与 `docker-compose.yaml` 当前态
- [ ] §1 DB 口令轮换（ALTER → .env → up -d）
- [ ] §2 INIT_PASSWORD 设置
- [ ] §3 端口回环映射 + /probe/dify 连带处理
- [ ] §4 六步验证
- [ ] 变更记录追加到本文档末尾（执行人/时间/新口令存放位置说明——**不要写口令本身**）

> 执行人：＿＿＿＿　执行时间：＿＿＿＿　验证结果：＿＿＿＿
