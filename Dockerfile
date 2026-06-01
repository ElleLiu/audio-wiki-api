# 1. 使用极其轻量的 Python 3.10 官方基础镜像
FROM python:3.10-slim

# 2. 【核心魔法】在云端系统里静默安装 FFmpeg，绝不报错
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 3. 设置工作目录
WORKDIR /app

# 4. 把依赖清单复制进去，并安装 Python 包
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. 把你的主代码复制进去
COPY main.py .

# 6. 暴露 8000 端口，并启动 FastAPI 服务
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
