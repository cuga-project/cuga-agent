#!/usr/bin/env bash

echo "--------------------------------------------------------------"
echo 'Commencing image push to registry!'

chmod +x ./scripts/* 2>/dev/null || true

TARGET_IBM_CLOUD_URL=${1:-$TARGET_IBM_CLOUD_URL}
TARGET_IBM_CLOUD_REGION=${2:-$TARGET_IBM_CLOUD_REGION}
TARGET_IBM_CLOUD_GROUP=${3:-$TARGET_IBM_CLOUD_GROUP}
TARGET_IBM_REGISTRY_URL=${4:-$TARGET_IBM_REGISTRY_URL}
CR_NAMESPACE=${5:-$CR_NAMESPACE}
IMAGE_NAME=${6:-$IMAGE_NAME}
IMAGE_TAG=${7:-$IMAGE_TAG}

echo "Login to IBM Cloud using apikey"
ibmcloud login -a "$TARGET_IBM_CLOUD_URL" --apikey "$RIS_CLOUD_ACCOUNT_API_KEY" -r "$TARGET_IBM_CLOUD_REGION" -g "$TARGET_IBM_CLOUD_GROUP"
if [ $? -ne 0 ]; then
  echo "Failed to authenticate to IBM Cloud"
  exit 1
fi

echo "Logging into IBM Cloud container registry"
ibmcloud cr login
if [ $? -ne 0 ]; then
  echo "Failed to authenticate to IBM Cloud container registry"
  exit 1
fi

echo "Pushing image to registry"
docker push $TARGET_IBM_REGISTRY_URL/$CR_NAMESPACE/$IMAGE_NAME:$IMAGE_TAG
echo "Done pushing image to registry"
