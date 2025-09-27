#!/bin/bash

# 定义镜像名称和标签
IMAGE_NAME="smart-trader-v2"
IMAGE_TAG="0.0.1"
CONTAINER_NAME="smart-trader-v2"

# 指定Dockerfile的路径
DOCKERFILE_PATH="."

# 执行docker build命令来打包镜像
docker build -t ${IMAGE_NAME}:${IMAGE_TAG} ${DOCKERFILE_PATH}

docker stop "${CONTAINER_NAME}"
docker rm "${CONTAINER_NAME}"

# 部署
docker run --name "${CONTAINER_NAME}" --restart=always \
  -v /etc/localtime:/etc/localtime \
  -v /root/projects/smart-trader:/usr/local/app \
  -d "$IMAGE_NAME:$IMAGE_TAG"

