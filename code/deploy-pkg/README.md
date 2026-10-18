# zhice-os-v2 一键部署包

离线部署包（含全部镜像，目标机无需联网拉镜像）。适用 **Linux ARM64 / x86_64 + Docker + docker compose v2**。

## 快速开始

```bash
tar -xzf zhice-deploy-*.tar.gz
cd zhice-deploy
./deploy.sh
```

按提示三步走：
1. **选市场**：四市场 / US+CRYPTO / 仅CN / 自定义 —— 决定采集范围与前端展示的市场 tab。
2. **填配置**：数据库密码、管理员密码、TUSHARE token（回车用默认）。
3. **选是否自动采集**：`y` 立即启动 scheduler 自动获取数据；`n` 仅展示、以后再开。

完成后终端会打印访问地址（`https://<本机IP>`）和账号。

## 包内容

```
zhice-deploy/
├── deploy.sh                 一键交互部署脚本
├── docker-compose.yml        编排(nginx 镜像按市场选择/scheduler healthcheck 已关)
├── images/
│   ├── zhice-api-prod.tar        后端(api+scheduler 共用)
│   ├── zhice-nginx-prod.tar      前端·四市场
│   ├── zhice-nginx-us-crypto.tar 前端·US+CRYPTO
│   └── zhice-nginx-cn.tar        前端·仅CN
└── README.md
```

数据库首启由 `migrate` 容器自动迁移到最新 schema；**部署包不含历史数据**，勾选自动采集后由 scheduler 逐步获取。

## 常用运维

```bash
docker compose --env-file .env ps          # 状态
docker compose --env-file .env logs -f      # 日志
docker compose --env-file .env up -d scheduler   # 开启采集
docker compose --env-file .env stop scheduler    # 停止采集
docker compose --env-file .env down         # 停止全部(数据保留在 pgdata 卷)
```

## 说明
- HTTPS 用自签名证书（`certs/`，浏览器首次需信任）；有正式证书可替换 `certs/fullchain.pem` + `certs/privkey.pem`。
- 市场可后续调整：改 `.env` 的 `SCHEDULER_MARKETS` 后 `docker compose --env-file .env up -d` 重建即可（前端市场展示需换对应 `NGINX_IMAGE`）。
- 已内置修复：`market_calendar` 唯一索引、`scheduler` healthcheck 关闭（纯 job 进程无 HTTP 端点）、nginx 启动后刷新防 502。
