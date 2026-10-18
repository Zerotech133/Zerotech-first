#!/usr/bin/env bash
# 生成 zhice-os-v2 离线一键部署包。
# 前置: 本机已 build zhice-api:prod + zhice-nginx:{prod,us-crypto,cn}。
# 产物: dist/zhice-deploy-<date>.tar.gz
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # 项目根

DIST="dist/zhice-deploy"
rm -rf "$DIST"; mkdir -p "$DIST/images"

echo "==> 复制部署文件"
cp deploy-pkg/deploy.sh deploy-pkg/docker-compose.yml deploy-pkg/README.md "$DIST/"
chmod +x "$DIST/deploy.sh"

echo "==> 导出镜像 (较大, 请稍候)"
docker save zhice-api:prod       -o "$DIST/images/zhice-api-prod.tar"
docker save zhice-nginx:prod      -o "$DIST/images/zhice-nginx-prod.tar"
docker save zhice-nginx:us-crypto -o "$DIST/images/zhice-nginx-us-crypto.tar"
docker save zhice-nginx:cn        -o "$DIST/images/zhice-nginx-cn.tar"

echo "==> 打包 tar.gz"
PKG="dist/zhice-deploy-$(date +%Y%m%d).tar.gz"
tar -czf "$PKG" -C dist zhice-deploy

echo "==> 完成:"
ls -lh "$PKG"
echo "    内容:"; tar -tzf "$PKG" | head -20
