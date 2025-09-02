#!/bin/bash

START_STRATEGY=$1

# 定义镜像名称和标签
IMAGE_NAME="smart-trader-v2"
IMAGE_TAG="0.0.1"
CONTAINER_NAME=$1

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
  -e START_STRATEGY="${START_STRATEGY}" \
  -e BINANCE_API_KEY='crem6s2RAVCeD3VqmVrpbTduNYpPy8SY346Tg3DhzBJmdBxjdK4snk3jjRQL789M' \
  -e BINANCE_API_SECRET='6m1H8d4wfetfm6ddZGFD5vWpEIyDIut50BXSaddfoYTd2gzpynaTSy7ZKrEB9FWJ' \
  -e BINANCE_IS_TEST='False' \
  -d "$IMAGE_NAME:$IMAGE_TAG"

