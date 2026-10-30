# Phase B B0：Pocket ID 部署记录

- 日期：2026-10-30
- 工单：`WO-LEGION-PHASEB-L2-20261030`
- 范围：**仅 B0（部署 + discovery 验证）**
- 状态：**容器 healthy；未切换登录入口；未改 auth-gateway 代码**

## 1. 部署结果

```text
container: zerobound-pocket-id
image:     ghcr.io/pocket-id/pocket-id:v2.19.1
status:    Up (healthy)
port:      127.0.0.1:1411 -> 1411
restart:   unless-stopped
```

数据/密钥（只记录路径，不回显内容）：

```text
C:\项目\_vault\pocket-id\data
C:\项目\_vault\pocket-id\secrets\encryption_key
```

`encryption_key` 已生成并挂载到容器
`/run/secrets/pocket_id_key`；丢失不可恢复，需备份 vault。

### 2.0 审计 P0 修正（2026-10-30）

最初使用 `C:\aios\_vault\pocket-id`，而 `C:\aios` 是仓库 junction，
密钥实际落进 repo 工作区，`git status` 出现 `?? _vault/pocket-id/`。

已处理：

- 停容器 → 迁移到 repo 外 `C:\项目\_vault\pocket-id`；
- 按新路径重建容器（healthy / discovery 200）；
- `.gitignore` 增加 `_vault/pocket-id/` 防再犯；
- `git ls-files _vault/pocket-id/*` 为空；
- `git log --all -- _vault/pocket-id/*` 无历史命中。

## 2. 与方案 v1 的差异

方案 v1 原计划直接拉 `ghcr.io/pocket-id/pocket-id:v2.19.1`。
实测 GHCR 在 Docker Desktop 下长时间无 layer 进度，已改用官方
Docker Hub 镜像 `pocketid/pocket-id:v2.19.1`（同版本号），镜像落盘后
容器运行正常。

WebAuthn RPID 不能是 IP：

```text
APP_URL=http://localhost:1411
RPID=localhost
```

浏览器初始化/ passkey 注册必须走：

```text
http://localhost:1411
```

不要用 `127.0.0.1` 作为 passkey 域。

## 2.1 首次管理员入口（重要）

Pocket ID v2.19.1 首次创建管理员**不走根路径**：

```text
http://localhost:1411/          → 登录页（没有注册入口）
http://localhost:1411/setup     → 302 /signup/setup → 注册表单
```

根因：

- 数据库 `users=0`
- `allowUserSignups=disabled` 是默认配置，不是故障
- `/setup` 只在“首个账号不存在”时可用
- `/api/users/me` 401、`/api/application-images/logo?default=false` 404 都是未登录/未设 logo 的正常响应

正确操作：

```text
http://localhost:1411/setup
→ 填用户名/邮箱/名/姓
→ 注册
→ Add Passkey（Windows Hello）
```

不要用 `http://127.0.0.1:1411/setup` 注册 passkey，RPID 是 `localhost`。

## 3. 验收

```text
GET http://localhost:1411/ → 200
GET http://localhost:1411/.well-known/openid-configuration → 200
issuer = http://localhost:1411
authorization_endpoint = http://localhost:1411/authorize
docker ps: Up (healthy)
```

## 4. 未做 / 下一步

未做（按 L2 文档边界）：

- 未创建 OIDC client（需老田在 Pocket ID UI 注册管理员 passkey）
- 未改 `portal/auth_gateway.py`
- 未切换 `AUTH_OIDC_MODE`
- 未改密码桥/只读 BFF/服务账号

下一步 B1/B2 需要：

1. 打开 `http://localhost:1411`，创建管理员 + passkey；
2. 创建 OIDC client：
   - callback `http://127.0.0.1:8734/api/auth/oidc/callback`
   - Authorization Code + PKCE
   - secret 写入 vault，不打印
3. 代码加 OIDC RP，默认 `AUTH_OIDC_MODE=off`；
4. 映射 `idp_sub` 后再 shadow/on。

## 5. 服务账号轮换基线

`zero_bff` 只读 smoke（2026-10-30）：

```text
login 200 sub=zero_bound:bff
me 200 sub=zero_bound:bff
logout 200 ok=True
```

首跑已排期：

```text
2026-11-30 09:00 Asia/Shanghai
runbook: docs/ops/2026-10-30_BFF智策服务账号月度轮换_v1.md
```

本次没有改凭据、没有重启 8091。
