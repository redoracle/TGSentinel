#!/bin/bash
set -euo pipefail
VERSION="$1"
echo "Preparing release ${VERSION}"

# Update APP_VERSION in docker/app.Dockerfile
sed -i "s/^ARG APP_VERSION=.*/ARG APP_VERSION=${VERSION}/" docker/app.Dockerfile
echo "Updated docker/app.Dockerfile APP_VERSION to ${VERSION}"

echo "${VERSION}" > .release-prepared
