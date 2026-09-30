#!/usr/bin/env bash
set -euo pipefail
image=${1:?image tag required}
source_sha=${2:?source commit required}
[[ "$source_sha" =~ ^[0-9a-f]{40}$ ]]
docker buildx build --platform linux/amd64 --load --provenance=false \
  --build-arg "SOURCE_SHA=$source_sha" --tag "$image" images/valkey-timeseries
test "$(docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')" = "$source_sha"
python3 scripts/check-valkey-timeseries.py "$image"
