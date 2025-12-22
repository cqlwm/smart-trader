FROM python:3.14.2-bookworm

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    bash \
    build-essential \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/local/app

# Install uv
RUN pip install --no-cache-dir uv

# Copy dependency files
COPY pyproject.toml uv.lock ./

# 验证到底复制了哪些文件
# Copy source code
COPY . .
RUN ls -la

# Create virtual environment and install dependencies
ENV UV_LINK_MODE=copy
RUN uv sync --frozen --no-install-project --no-cache 



CMD ["uv", "run", "python", "run.py"]
