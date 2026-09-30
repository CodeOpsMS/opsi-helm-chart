#!/usr/bin/env bash
# Publish the already-built and tested image; never replace a source-SHA tag.
set -euo pipefail
[[ "${GITHUB_EVENT_NAME:-}" == workflow_dispatch && "${GITHUB_REF:-}" == refs/heads/main && "${GITHUB_JOB:-}" == publish ]]
image=${1:?image tag required}
test "$image" = "ghcr.io/codeopsms/valkey-timeseries-module:sha-$GITHUB_SHA"
local_id=$(docker image inspect "$image" --format '{{.Id}}')
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
owner_type=$(gh api "users/$GITHUB_REPOSITORY_OWNER" --jq .type)
if [[ "$owner_type" == Organization ]]; then base=orgs; else base=users; fi
exists=false
if gh api --paginate --slurp "$base/$GITHUB_REPOSITORY_OWNER/packages/container/valkey-timeseries-module/versions?per_page=100" > "$work/versions.json" 2> "$work/error"; then
  if jq -e --arg tag "sha-$GITHUB_SHA" 'any(.[][]; any(.metadata.container.tags[]?; . == $tag))' "$work/versions.json" >/dev/null; then
    exists=true
  fi
elif ! grep -q '(HTTP 404)' "$work/error"; then
  cat "$work/error" >&2
  exit 1
fi
printf '%s' "$GH_TOKEN" | docker login ghcr.io --username "$GITHUB_ACTOR" --password-stdin
if [[ "$exists" == true ]]; then
  docker pull "$image"
  if [[ "$(docker image inspect "$image" --format '{{.Id}}')" != "$local_id" ]]; then
    echo 'Source-SHA tag already contains a different image. Refusing to overwrite; use a new source commit.' >&2
    exit 1
  fi
else
  docker push "$image"
fi
digest=$(docker image inspect "$image" --format '{{range .RepoDigests}}{{println .}}{{end}}' | sed -n 's|^ghcr.io/codeopsms/valkey-timeseries-module@||p' | head -1)
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]
printf 'digest=%s\n' "$digest" >> "$GITHUB_OUTPUT"
printf 'Image: %s@%s\n\nSource: %s\n' "${image%:*}" "$digest" "$GITHUB_SHA" >> "$GITHUB_STEP_SUMMARY"
