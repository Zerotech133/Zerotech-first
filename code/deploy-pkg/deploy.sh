#!/usr/bin/env bash
# zhice-os-v2 一键交互部署脚本。
# 用法: 解压部署包后 cd 进目录, 运行 ./deploy.sh, 按提示选市场/填配置/选是否采集。
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

G='\033[0;32m'; N='\033[0m'
say(){ echo -e "${G}==>${N} $*"; }
ask(){ local v; read -rp "$1 [$2]: " v; echo "${v:-$2}"; }

# ---------- [0] 环境检查 ----------
command -v docker >/dev/null || { echo "错误: 需要 docker"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "错误: 需要 docker compose v2"; exit 1; }

# ---------- [1] 加载核心镜像 ----------
say "[1/6] 加载后端镜像 (zhice-api)..."
docker load -i images/zhice-api-prod.tar

# ---------- [2] 选择市场 ----------
echo ""
echo "[2/6] 选择部署哪些市场:"
echo "  1) 四市场  (CN / US / HK / CRYPTO)"
echo "  2) US + CRYPTO"
echo "  3) 仅 CN"
echo "  4) 自定义 (手动输入)"
read -rp "选择 [1-4] (默认 1): " MK; MK=${MK:-1}
case "$MK" in
  1) MARKETS="CN,US,HK,CRYPTO"; NGINX_IMAGE="zhice-nginx:prod";       NGTAR="zhice-nginx-prod.tar";;
  2) MARKETS="US,CRYPTO";       NGINX_IMAGE="zhice-nginx:us-crypto";  NGTAR="zhice-nginx-us-crypto.tar";;
  3) MARKETS="CN";              NGINX_IMAGE="zhice-nginx:cn";         NGTAR="zhice-nginx-cn.tar";;
  4) read -rp "  输入市场(逗号分隔, 如 US,HK): " MARKETS
     NGINX_IMAGE="zhice-nginx:prod"; NGTAR="zhice-nginx-prod.tar"
     echo "  (自定义组合前端用四市场版; 未采集市场的 tab 为空)";;
  *) echo "无效选择"; exit 1;;
esac
say "加载前端镜像 $NGINX_IMAGE ..."
docker load -i "images/$NGTAR"

# ---------- [3] 配置 ----------
echo ""
echo "[3/6] 基础配置 (回车用默认值):"
DBPW=$(ask "  数据库密码" "zhice_pass")
ADMPW=$(ask "  管理员 admin 密码" "123456")
read -rp "  TUSHARE token (CN 日线数据源, 可留空): " TS
SECRET=$(openssl rand -hex 32 2>/dev/null || echo "changeme$(date +%s)$RANDOM")
cat > .env <<EOF
DB_USER=zhice
DB_PASSWORD=$DBPW
DB_NAME=zhice_os_v2
SECRET_KEY=$SECRET
ADMIN_PASSWORD=$ADMPW
TUSHARE_TOKEN=$TS
SCHEDULER_MARKETS=$MARKETS
NGINX_IMAGE=$NGINX_IMAGE
APP_ENV=production
EOF

# ---------- [4] HTTPS 自签名证书 (如无) ----------
if [ ! -f certs/privkey.pem ]; then
  say "[4/6] 生成自签名 HTTPS 证书..."
  mkdir -p certs
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout certs/privkey.pem -out certs/fullchain.pem -subj "/CN=zhice-os-v2" \
    >/dev/null 2>&1 || echo "  (无 openssl, 请自备 certs/fullchain.pem + certs/privkey.pem)"
else
  say "[4/6] 已存在证书, 跳过。"
fi

# ---------- [5] 启动 (含首启迁移) ----------
say "[5/6] 启动服务 (首次会迁移数据库, 请稍候)..."
docker compose --env-file .env up -d

say "等待数据库就绪 + 修复已知项 (market_calendar 索引 / nginx DNS)..."
sleep 12
docker exec zhice-os-v2-postgres-1 psql -U zhice -d zhice_os_v2 -c \
  "CREATE UNIQUE INDEX IF NOT EXISTS uq_v2_market_calendar_mt ON v2_market_calendar(market,trade_date)" \
  >/dev/null 2>&1 || true
docker restart zhice-os-v2-nginx-1 >/dev/null 2>&1 || true

# ---------- [6] 是否自动采集数据 ----------
echo ""
echo "[6/6] 是否立即让系统自动获取(采集)数据?"
read -rp "  y = 启动 scheduler 自动采集 / n = 仅展示不采集 [Y/n]: " AC; AC=${AC:-y}
if [[ "$AC" =~ ^[Yy] ]]; then
  docker compose --env-file .env up -d scheduler
  echo "  ✓ scheduler 已启动, 开始自动采集: $MARKETS"
else
  docker compose --env-file .env stop scheduler >/dev/null 2>&1 || true
  echo "  ✓ scheduler 已停 (仅展示)。以后运行 'docker compose --env-file .env up -d scheduler' 即可开启采集。"
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}'); IP=${IP:-<本机IP>}
echo ""
say "✅ 部署完成!"
echo "   访问:  https://$IP    (自签名证书, 浏览器首次需信任)"
echo "   账号:  admin / $ADMPW"
echo "   市场:  $MARKETS"
echo "   运维:  docker compose --env-file .env [ps|logs -f|restart|down]"
