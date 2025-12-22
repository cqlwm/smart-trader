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
docker run --name "${CONTAINER_NAME}" --restart=always \
  -v /etc/localtime:/etc/localtime \
  -v /root/projects/smart-trader/.env:/usr/local/app/.env \
  -d "$IMAGE_NAME:$IMAGE_TAG"

