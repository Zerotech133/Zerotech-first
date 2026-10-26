# 个人 OS 数字军团 · 席位镜像骨架
# 注意：镜像名/标签为占位，须替换为实际发布名；不得使用 latest tag。
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY roles/ ./roles/
COPY entrypoint.py ./

# cmd：实际启动按席位注入（如 python entrypoint.py --role zhuge_liang）
CMD ["python", "entrypoint.py", "--help"]
