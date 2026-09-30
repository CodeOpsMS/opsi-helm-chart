#!/usr/bin/env bash
# Invoked only by the gated workflow; never repackage the accepted chart.
set -euo pipefail
[[ "${GITHUB_REF:-}" == refs/heads/main && "${GITHUB_JOB:-}" == publish ]]
candidate_dir=${1:?candidate directory required}
python3 scripts/candidate.py verify "$candidate_dir" --source "$SOURCE_SHA" --sha256 "$LIVE_SHA256"
test "$(python3 scripts/candidate.py verify "$candidate_dir" --field run_id)" = "$CI_RUN_ID"
version=$(python3 scripts/candidate.py verify "$candidate_dir" --field version)
package_name=$(python3 scripts/candidate.py verify "$candidate_dir" --field package)
package="$(pwd)/$candidate_dir/$package_name"
tag="opsi-$version"
owner=$(printf '%s' "$GITHUB_REPOSITORY_OWNER" | tr '[:upper:]' '[:lower:]')
oci="oci://ghcr.io/$owner/helm-charts/opsi"
pages_url="https://$owner.github.io/opsi-helm-chart"
work=$(mktemp -d)
pages_dir="$work/pages"
cleanup() {
  git worktree remove --force "$pages_dir" >/dev/null 2>&1 || true
  rm -rf -- "$work"
}
trap cleanup EXIT
export HELM_REGISTRY_CONFIG="$work/registry-auth.json"
helm lint "$package" --strict -f tests/ci-values.yaml
test "$(gh api "repos/$GITHUB_REPOSITORY/immutable-releases" --jq .enabled)" = true
gh api "repos/$GITHUB_REPOSITORY/pages" --jq .html_url >/dev/null

prerelease=false
if [[ "$version" == *-* ]]; then prerelease=true; fi
if gh release view "$tag" --json isDraft,isPrerelease,targetCommitish > "$work/release.json" 2>/dev/null; then
  test "$(jq -r .isPrerelease "$work/release.json")" = "$prerelease"
  if [[ "$(jq -r .isDraft "$work/release.json")" == true ]]; then
    test "$(jq -r .targetCommitish "$work/release.json")" = "$SOURCE_SHA"
  else
    git fetch origin "refs/tags/$tag:refs/tags/$tag"
    test "$(git rev-parse "$tag^{commit}")" = "$SOURCE_SHA"
  fi
else
  if git ls-remote --exit-code --tags origin "refs/tags/$tag" >/dev/null 2>&1; then
    echo 'Refusing an existing tag without a matching release' >&2
    exit 1
  fi
  printf 'OPSI Helm chart %s. Tested source commit: %s.\n\nCI: https://github.com/%s/actions/runs/%s\n\nArchive SHA-256: %s\n\nLive acceptance reference: %s\n' \
    "$version" "$SOURCE_SHA" "$GITHUB_REPOSITORY" "$CI_RUN_ID" "$LIVE_SHA256" "$LIVE_REFERENCE" > "$work/notes.md"
  flags=()
  if [[ "$prerelease" == true ]]; then flags+=(--prerelease --latest=false); fi
  gh release create "$tag" --draft --target "$SOURCE_SHA" --title "$tag" \
    --notes-file "$work/notes.md" "${flags[@]}"
fi
mkdir "$work/release"
if gh release download "$tag" --pattern "$package_name" --dir "$work/release" >/dev/null 2>&1; then
  test "$(sha256sum "$work/release/$package_name" | awk '{print $1}')" = "$LIVE_SHA256"
else
  # Upload only to a draft. Published immutable assets are never replaced.
  test "$(gh release view "$tag" --json isDraft --jq .isDraft)" = true
  gh release upload "$tag" "$package"
fi

printf '%s' "$GH_TOKEN" | helm registry login ghcr.io --username "$GITHUB_ACTOR" --password-stdin
mkdir "$work/oci"
if helm pull "$oci" --version "$version" --destination "$work/oci" > "$work/pull.log" 2>&1; then
  test "$(sha256sum "$work/oci/$package_name" | awk '{print $1}')" = "$LIVE_SHA256"
else
  owner_type=$(gh api "users/$GITHUB_REPOSITORY_OWNER" --jq .type)
  if [[ "$owner_type" == Organization ]]; then base=orgs; else base=users; fi
  if gh api --paginate --slurp "$base/$GITHUB_REPOSITORY_OWNER/packages/container/helm-charts%2Fopsi/versions?per_page=100" > "$work/versions.json" 2> "$work/package-error"; then
    if jq -e --arg version "$version" 'any(.[][]; any(.metadata.container.tags[]?; . == $version))' "$work/versions.json" >/dev/null; then
      echo 'OCI version exists but could not be pulled; refusing to overwrite' >&2
      exit 1
    fi
  elif ! grep -q '(HTTP 404)' "$work/package-error"; then
    cat "$work/package-error" >&2
    exit 1
  fi
  helm push "$package" "oci://ghcr.io/$owner/helm-charts"
fi
helm registry logout ghcr.io >/dev/null
export HELM_REGISTRY_CONFIG="$work/registry-anonymous.json"
oci_verified=false
for _ in {1..18}; do
  rm -f "$work/oci/$package_name"
  if helm pull "$oci" --version "$version" --destination "$work/oci" >/dev/null 2>&1; then
    test "$(sha256sum "$work/oci/$package_name" | awk '{print $1}')" = "$LIVE_SHA256"
    oci_verified=true
    break
  fi
  sleep 5
done
if [[ "$oci_verified" != true ]]; then
  echo 'OCI package must be public. Set GHCR helm-charts/opsi visibility to public and rerun this release.' >&2
  exit 1
fi

git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
if git ls-remote --exit-code --heads origin gh-pages >/dev/null 2>&1; then
  git fetch origin gh-pages
  git worktree add --detach "$pages_dir" origin/gh-pages
else
  git worktree add --detach "$pages_dir" "$SOURCE_SHA"
  git -C "$pages_dir" switch --orphan gh-pages
  git -C "$pages_dir" rm -rf --ignore-unmatch .
fi
if [[ -e "$pages_dir/$package_name" ]]; then
  test "$(sha256sum "$pages_dir/$package_name" | awk '{print $1}')" = "$LIVE_SHA256"
fi
cp "$package" "$pages_dir/"
cp .github/pages/index.html "$pages_dir/index.html"
touch "$pages_dir/.nojekyll"
index_args=()
if [[ -f "$pages_dir/index.yaml" ]]; then index_args+=(--merge "$pages_dir/index.yaml"); fi
helm repo index "$pages_dir" --url "$pages_url" "${index_args[@]}"
git -C "$pages_dir" add .nojekyll index.html index.yaml "$package_name"
if ! git -C "$pages_dir" diff --cached --quiet; then
  git -C "$pages_dir" commit -m "Publish opsi chart $version"
  git -C "$pages_dir" push origin HEAD:gh-pages
fi
# GITHUB_TOKEN pushes do not trigger a branch-based Pages build. Explicitly
# request it on reruns too, in case a previous attempt stopped after the push.
gh api --method POST "repos/$GITHUB_REPOSITORY/pages/builds" --jq .status
pages_verified=false
for attempt in {1..30}; do
  if curl -fsSL --max-time 15 "$pages_url/$package_name?run=$GITHUB_RUN_ID-$attempt" -o "$work/pages.tgz"; then
    if [[ "$(sha256sum "$work/pages.tgz" | awk '{print $1}')" == "$LIVE_SHA256" ]]; then
      pages_verified=true
      break
    fi
  fi
  sleep 5
done
test "$pages_verified" = true
mkdir "$work/final"
gh release download "$tag" --pattern "$package_name" --dir "$work/final"
test "$(sha256sum "$work/final/$package_name" | awk '{print $1}')" = "$LIVE_SHA256"
if [[ "$(gh release view "$tag" --json isDraft --jq .isDraft)" == true ]]; then
  if [[ "$prerelease" == true ]]; then
    gh release edit "$tag" --draft=false --prerelease --latest=false
  else
    gh release edit "$tag" --draft=false --prerelease=false --latest
  fi
fi
test "$(gh release view "$tag" --json isImmutable --jq .isImmutable)" = true
git fetch origin "refs/tags/$tag:refs/tags/$tag"
test "$(git rev-parse "$tag^{commit}")" = "$SOURCE_SHA"
echo "Published $tag with verified archive SHA-256 $LIVE_SHA256"
