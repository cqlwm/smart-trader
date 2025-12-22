#!/bin/bash

# 定义镜像名称和标签
IMAGE_NAME="smart-trader"
IMAGE_TAG="0.0.2"
CONTAINER_NAME="smart-trader"

# 指定Dockerfile的路径
DOCKERFILE_PATH="."

# 执行docker build命令来打包镜像
docker build -t ${IMAGE_NAME}:${IMAGE_TAG} ${DOCKERFILE_PATH}

docker stop "${CONTAINER_NAME}"
docker rm "${CONTAINER_NAME}"

# 部署
if [ ! -f .env ]; then
    echo "Error: .env file not found. Please create a .env file before running the container."
    exit 1
fi
docker run --name "${CONTAINER_NAME}" --restart=always \
  -v /etc/localtime:/etc/localtime \
  -v ./.env:/usr/local/app/.env \
  -v ./data:/usr/local/app/data \
  -d "$IMAGE_NAME:$IMAGE_TAG"
