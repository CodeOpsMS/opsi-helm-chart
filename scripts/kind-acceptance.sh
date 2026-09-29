#!/usr/bin/env bash
# This script deliberately refuses existing/local clusters.
set -euo pipefail
[[ "${CI:-}" == true && "${GITHUB_JOB:-}" == runtime ]]
[[ "$(kubectl config current-context)" == kind-opsi-ci ]]
candidate_dir=${1:?candidate directory required}
python3 scripts/candidate.py verify "$candidate_dir" --source "$GITHUB_SHA"
package="$candidate_dir/$(python3 scripts/candidate.py verify "$candidate_dir" --field package)"
work=$(mktemp -d)
chmod 700 "$work"
forward_pid=''
cleanup() {
  if [[ -n "$forward_pid" ]]; then kill "$forward_pid" 2>/dev/null || true; fi
  rm -rf -- "$work"
}
trap cleanup EXIT
python3 - "$work" <<'PY'
from pathlib import Path
import secrets
import sys
path = Path(sys.argv[1])
keys = ['admin-password', 'mysql-password', 'mysql-root-password', 'redis-password']
passwords = {key: secrets.token_hex(24) for key in keys}
(path / 'credentials').write_text(''.join(f'{k}={v}\n' for k, v in passwords.items()))
(path / 'admin-password').write_text(passwords['admin-password'])
(path / 'marker').write_text(secrets.token_hex(24))
PY
kubectl --context kind-opsi-ci create namespace opsi-ci
kubectl --context kind-opsi-ci -n opsi-ci create secret generic opsi-ci-credentials --from-env-file="$work/credentials"
kubectl --context kind-opsi-ci -n opsi-ci apply -f tests/fixtures.yaml
kubectl --context kind-opsi-ci -n opsi-ci rollout status deployment/opsi-ci-mariadb --timeout=5m
kubectl --context kind-opsi-ci -n opsi-ci rollout status deployment/opsi-ci-redis --timeout=5m
# Expand REDIS_PASSWORD inside the fixture container, never in the CI runner.
# shellcheck disable=SC2016
kubectl --context kind-opsi-ci -n opsi-ci exec deployment/opsi-ci-redis -- sh -ec \
  'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli MODULE LIST | grep -qi timeseries'
helm --kube-context kind-opsi-ci install opsi "$package" -n opsi-ci -f tests/ci-values.yaml --wait --timeout 15m
helm --kube-context kind-opsi-ci test opsi -n opsi-ci --timeout 3m
kubectl --context kind-opsi-ci -n opsi-ci exec deployment/opsi -- cat /etc/opsi/ssl/opsi-ca-cert.pem > "$work/opsi-ca.pem"

start_forward() {
  if [[ -n "$forward_pid" ]]; then kill "$forward_pid" 2>/dev/null || true; wait "$forward_pid" 2>/dev/null || true; fi
  kubectl --context kind-opsi-ci -n opsi-ci port-forward service/opsi 14447:4447 >"$work/forward.log" 2>&1 &
  forward_pid=$!
  for _ in {1..30}; do
    if grep -q 'Forwarding from' "$work/forward.log"; then return; fi
    kill -0 "$forward_pid"
    sleep 1
  done
  echo 'Local port-forward did not become ready' >&2
  return 1
}
smoke() {
  python3 scripts/smoke.py --url https://opsi.example.invalid:14447 --connect-address 127.0.0.1 --ca-file "$work/opsi-ca.pem" \
    --password-file "$work/admin-password" --host-id opsi.example.invalid --marker-file "$work/marker" \
    --identity-fingerprint-file "$work/identity-fingerprint" "$@"
}
start_forward
smoke --write-marker
kubectl --context kind-opsi-ci -n opsi-ci rollout restart deployment/opsi
kubectl --context kind-opsi-ci -n opsi-ci rollout status deployment/opsi --timeout=10m
start_forward
smoke
helm --kube-context kind-opsi-ci upgrade opsi "$package" -n opsi-ci -f tests/ci-values.yaml \
  --set-string podAnnotations.acceptance-revision=2 --wait --timeout 10m
start_forward
smoke
helm --kube-context kind-opsi-ci test opsi -n opsi-ci --timeout 3m
echo 'Candidate passed API, authentication, WebDAV, restart and upgrade acceptance.'
