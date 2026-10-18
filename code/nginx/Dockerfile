# 使用本地预构建的 dist/（由 npm run build 在宿主机生成）
FROM nginx:alpine
COPY frontend/dist /usr/share/nginx/html
COPY nginx/nginx.conf /etc/nginx/conf.d/default.conf
